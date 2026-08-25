from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event
import time
from zipfile import ZipFile

import py7zr
import pytest
import game_optimization_linux.services.archive_reader as archive_reader_module

from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import (
    ArchiveReadError,
    GameExecutableResolver,
    OptiScalerCancelled,
    OptiScalerConflictError,
    OptiScalerError,
    OptiScalerProfileRepository,
    OptiScalerService,
    open_archive,
    merge_wine_dll_overrides,
    MockTaskService,
    SettingsStore,
)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _game(root: Path, app_id: str = "224760") -> Game:
    executable = root / "Binaries" / "Win64" / "TestGame-Win64-Shipping.exe"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"synthetic executable")
    return Game(
        id=f"steam-{app_id}",
        steam_app_id=app_id,
        name="Test Game",
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=0.01,
        physical_size_gb=0.01,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
    )


def _archive(path: Path, *, traversal: bool = False) -> Path:
    members = {
        "OptiScaler_0.7.7/OptiScaler.dll": b"optiscaler proxy",
        "OptiScaler_0.7.7/OptiScaler.ini": b"[OptiScaler]\nEnabled=true\n",
        "OptiScaler_0.7.7/plugins/helper.dll": b"helper",
    }
    if traversal:
        members["../outside.dll"] = b"escape"
    if path.suffix == ".7z":
        with py7zr.SevenZipFile(path, "w") as archive:
            for name, data in members.items():
                if name == "../outside.dll":
                    source = path.parent / "traversal-source.dll"
                    source.write_bytes(data)
                    archive.write(source, arcname=name)
                else:
                    archive.writestr(data, name)
    else:
        with ZipFile(path, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
    return path


def _versioned_archive(
    path: Path,
    *,
    proxy: bytes,
    ini: bytes,
    include_helper: bool = True,
) -> Path:
    """Create a synthetic release whose payload differs between versions."""

    root = f"OptiScaler_{path.stem}/"
    with ZipFile(path, "w") as archive:
        archive.writestr(root + "OptiScaler.dll", proxy)
        archive.writestr(root + "OptiScaler.ini", ini)
        if include_helper:
            archive.writestr(root + "plugins/helper.dll", b"helper-" + proxy)
    return path


@pytest.fixture
def setup_service(tmp_path: Path) -> tuple[OptiScalerService, Game, Path, Path]:
    game_root = tmp_path / "game"
    game = _game(game_root)
    archive = _archive(tmp_path / "OptiScaler_v0.7.7.7z")
    repository = OptiScalerProfileRepository(tmp_path / "config" / "games")
    service = OptiScalerService(
        profile_repository=repository,
        data_root=tmp_path / "data" / "games",
        executable_resolver=GameExecutableResolver(),
        process_detector=lambda _path: (),
    )
    return service, game, archive, game_root


def test_uses_existing_resolver_and_installs_next_to_unreal_executable(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    assert isinstance(service.executable_resolver, GameExecutableResolver)
    plan = service.plan(game, archive)
    assert plan.executable == "Binaries/Win64/TestGame-Win64-Shipping.exe"
    assert Path(plan.install_directory) == root / "Binaries" / "Win64"
    assert plan.executable_confidence == "confident"


def test_verified_online_archive_is_bound_to_expected_sha256(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    expected = _hash(archive)
    archive.write_bytes(b"replaced after cache validation")

    with pytest.raises(OptiScalerError, match="changed before installation"):
        service.install(game, archive, expected_archive_sha256=expected)

    assert not (root / "Binaries" / "Win64" / "dxgi.dll").exists()


def test_verified_online_archive_records_original_provenance(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, _root = setup_service
    expected = _hash(archive)

    profile = service.install(
        game,
        archive,
        expected_archive_sha256=expected,
    )
    manifest = service._load_manifest(profile)

    assert manifest["archive_path"] == str(archive.resolve())
    assert manifest["archive_sha256"] == expected


def test_install_rechecks_running_game_immediately_before_copy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "game"
    game = _game(root)
    archive = _archive(tmp_path / "OptiScaler.zip")
    calls = 0

    def detector(_path: Path) -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return () if calls == 1 else (4242,)

    service = OptiScalerService(
        profile_repository=OptiScalerProfileRepository(tmp_path / "config"),
        data_root=tmp_path / "data",
        process_detector=detector,
    )

    with pytest.raises(OptiScalerError, match="currently running"):
        service.install(game, archive)

    assert not (root / "Binaries" / "Win64" / "dxgi.dll").exists()


def test_install_rejects_replaced_game_directory_before_copy(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, root = setup_service
    original = tmp_path / "original-game"
    swapped = False

    def progress(stage: str, _value: float) -> None:
        nonlocal swapped
        if stage == "Validating extracted files" and not swapped:
            root.rename(original)
            replacement = root / "Binaries" / "Win64"
            replacement.mkdir(parents=True)
            (replacement / "TestGame-Win64-Shipping.exe").write_bytes(b"replacement")
            swapped = True

    with pytest.raises(OptiScalerError, match="game directory changed"):
        service.install(game, archive, progress=progress)

    assert not (root / "Binaries" / "Win64" / "dxgi.dll").exists()


def test_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    service = OptiScalerService(
        profile_repository=OptiScalerProfileRepository(tmp_path / "config"),
        data_root=tmp_path / "data",
        process_detector=lambda _path: (),
    )
    archive = _archive(tmp_path / "OptiScaler_v1.0.zip", traversal=True)
    with pytest.raises(OptiScalerError, match="unsafe archive path"):
        service.plan(game, archive)
    assert not (tmp_path / "outside.dll").exists()


def test_real_7z_is_detected_listed_and_extracted(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "OptiScaler_v0.7.7.7z")
    reader = open_archive(archive)
    assert reader.format_name == "7Z"
    assert {
        entry.relative_path for entry in reader.entries if not entry.is_directory
    } >= {
        "OptiScaler_0.7.7/OptiScaler.dll",
        "OptiScaler_0.7.7/OptiScaler.ini",
    }
    destination = tmp_path / "extracted"
    destination.mkdir()
    reader.extract_to(destination)
    assert (destination / "OptiScaler_0.7.7" / "OptiScaler.dll").read_bytes() == b"optiscaler proxy"


def test_unsupported_py7zr_method_uses_validated_bundled_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = _archive(tmp_path / "OptiScaler_BCJ2.7z")
    reader = open_archive(archive_path)
    original_extractall = py7zr.SevenZipFile.extractall
    fallback_calls = 0

    def unsupported(*_args, **_kwargs) -> None:
        raise py7zr.exceptions.UnsupportedCompressionMethodError(
            b"\x03\x03\x01\x1b", "synthetic BCJ2"
        )

    def fallback(_self, destination: Path) -> None:
        nonlocal fallback_calls
        fallback_calls += 1
        with py7zr.SevenZipFile(archive_path, "r") as handle:
            original_extractall(handle, path=destination)

    monkeypatch.setattr(py7zr.SevenZipFile, "extractall", unsupported)
    monkeypatch.setattr(
        archive_reader_module.SevenZipArchiveReader,
        "_extract_with_bundled_helper",
        fallback,
    )
    destination = tmp_path / "fallback-output"
    destination.mkdir()

    reader.extract_to(destination)

    assert fallback_calls == 1
    assert (destination / "OptiScaler_0.7.7" / "OptiScaler.dll").is_file()


def test_bundled_7z_fallback_uses_fixed_argv_without_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = _archive(tmp_path / "OptiScaler With Space.7z")
    reader = open_archive(archive_path)
    helper = tmp_path / "game-optimization-7zz"
    helper.write_bytes(b"fixed test helper")
    helper.chmod(0o700)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed.update({"argv": list(argv), **kwargs})
        return archive_reader_module.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(archive_reader_module, "BUNDLED_7ZIP_HELPER", helper)
    monkeypatch.setattr(archive_reader_module.subprocess, "run", fake_run)
    destination = tmp_path / "helper output"
    destination.mkdir()

    reader._extract_with_bundled_helper(destination)

    argv = observed["argv"]
    assert argv[0] == str(helper)
    assert argv[-1] == str(archive_path.resolve())
    assert f"-o{destination}" in argv
    assert observed["shell"] is False


def test_real_7z_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "unsafe.7z", traversal=True)
    with pytest.raises(OptiScalerError, match="unsafe archive path"):
        OptiScalerService(
            profile_repository=OptiScalerProfileRepository(tmp_path / "config"),
            data_root=tmp_path / "data",
            process_detector=lambda _path: (),
        ).plan(_game(tmp_path / "game"), archive)
    assert not (tmp_path / "outside.dll").exists()


def test_real_7z_symlink_is_rejected_before_extraction(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    outside = tmp_path / "outside.dll"
    outside.write_bytes(b"outside")
    (payload / "OptiScaler.dll").symlink_to(outside)
    archive = tmp_path / "symlink.7z"
    with py7zr.SevenZipFile(archive, "w") as handle:
        handle.writeall(payload, arcname="OptiScaler")
    with pytest.raises(ArchiveReadError, match="symbolic links"):
        open_archive(archive)


def test_duplicate_archive_paths_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    with ZipFile(archive, "w") as handle:
        handle.writestr("OptiScaler/OptiScaler.dll", b"first")
        handle.writestr("optiscaler/optiscaler.dll", b"second")
    with pytest.raises(ArchiveReadError, match="duplicate archive path"):
        open_archive(archive)


@pytest.mark.parametrize("suffix", (".7z", ".zip"))
def test_invalid_real_archive_is_rejected(tmp_path: Path, suffix: str) -> None:
    archive = tmp_path / f"broken{suffix}"
    archive.write_bytes(b"not an archive")
    with pytest.raises(OptiScalerError, match="format"):
        OptiScalerService(
            profile_repository=OptiScalerProfileRepository(tmp_path / "config"),
            data_root=tmp_path / "data",
            process_detector=lambda _path: (),
        ).plan(_game(tmp_path / "game"), archive)


def test_zip_and_7z_create_identical_installation_plans(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    service = OptiScalerService(
        profile_repository=OptiScalerProfileRepository(tmp_path / "config"),
        data_root=tmp_path / "data",
        process_detector=lambda _path: (),
    )
    seven = service.plan(game, _archive(tmp_path / "OptiScaler_v0.7.7.7z"))
    zipped = service.plan(game, _archive(tmp_path / "OptiScaler_v0.7.7.zip"))
    assert seven.archive_format == "7Z"
    assert zipped.archive_format == "ZIP"
    assert seven.version == zipped.version == "0.7.7"
    assert seven.executable == zipped.executable
    assert seven.injection_dll == zipped.injection_dll
    assert seven.proton_override == zipped.proton_override
    assert [item.to_dict() for item in seven.files] == [
        item.to_dict() for item in zipped.files
    ]


def test_file_picker_prefers_7z_and_keeps_zip_compatibility() -> None:
    qml = Path(
        "src/game_optimization_linux/qml/pages/details/OptiScalerSection.qml"
    ).read_text(encoding="utf-8")
    assert 'qsTr("Choose an OptiScaler archive")' in qml
    assert 'qsTr("OptiScaler archives (*.7z *.zip)")' in qml


@pytest.mark.parametrize(
    "selected,expected",
    (("auto", "dxgi.dll"), ("d3d12.dll", "d3d12.dll"), ("winhttp.dll", "winhttp.dll")),
)
def test_proxy_dll_selection(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    selected: str,
    expected: str,
) -> None:
    service, game, archive, _root = setup_service
    plan = service.plan(game, archive, injection_dll=selected)
    assert plan.injection_dll == expected
    assert plan.proton_override == f"{Path(expected).stem}=n,b"
    assert any(item.target_relative_path == expected for item in plan.files)
    assert all(item.target_relative_path != "OptiScaler.dll" for item in plan.files)


def test_wine_overrides_preserve_user_values_and_do_not_duplicate() -> None:
    assert merge_wine_dll_overrides("d3d11=b;foo=n", "dxgi=n,b") == "d3d11=b;foo=n;dxgi=n,b"
    assert merge_wine_dll_overrides("dxgi=b;foo=n", "dxgi=n,b") == "dxgi=n,b;foo=n"
    assert merge_wine_dll_overrides("DXGI=n,b;foo=n", "dxgi=n,b") == "DXGI=n,b;foo=n"


def test_conflict_backup_install_remove_and_full_restore(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    original = install_dir / "dxgi.dll"
    original.write_bytes(b"original proxy")
    original_hash = _hash(original)
    plan = service.plan(game, archive)
    assert plan.archive_format == "7Z"
    assert any(item.relative_path == "dxgi.dll" for item in plan.conflicts)
    with pytest.raises(OptiScalerConflictError):
        service.install(game, archive)

    profile = service.install(game, archive, allow_replace_conflicts=True)
    assert profile.installation_state == "installed"
    assert original.read_bytes() == b"optiscaler proxy"
    manifest_path = service.manifest_path(profile.app_id, profile.manifest_id)
    assert manifest_path.is_file()
    backup = service.backup_root(profile.app_id, profile.manifest_id) / "dxgi.dll"
    assert _hash(backup) == original_hash
    assert (install_dir / "OptiScaler.ini").is_file()

    removed = service.remove(game)
    assert removed.installation_state == "removed"
    assert not (install_dir / "OptiScaler.ini").exists()
    assert original.read_bytes() == b"original proxy"
    assert _hash(original) == original_hash


def test_install_without_existing_targets_removes_only_managed_files(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    profile = service.install(game, archive)
    install_dir = root / "Binaries" / "Win64"
    unknown = install_dir / "user-file.dll"
    unknown.write_bytes(b"user")
    service.remove(game)
    assert not (install_dir / "dxgi.dll").exists()
    assert not (install_dir / "OptiScaler.ini").exists()
    assert not (install_dir / "plugins" / "helper.dll").exists()
    assert unknown.read_bytes() == b"user"
    assert service.profile_repository.load(profile.app_id).installation_state == "removed"


def test_changed_managed_file_is_never_deleted(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    proxy = root / "Binaries" / "Win64" / "dxgi.dll"
    proxy.write_bytes(b"changed by user")
    with pytest.raises(
        OptiScalerConflictError,
        match="modified immutable managed file blocks removal: dxgi.dll",
    ):
        service.remove(game)
    assert proxy.read_bytes() == b"changed by user"
    assert service.profile_repository.load("224760").installation_state == "installed"


def test_replacement_already_matching_verified_original_completes_remove(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    """Model the real libxess state left by an earlier partial removal."""

    service, game, archive, root = setup_service
    proxy = root / "Binaries" / "Win64" / "dxgi.dll"
    original = b"original game proxy"
    proxy.write_bytes(original)
    installed = service.install(game, archive, allow_replace_conflicts=True)
    backup = service.backup_root(installed.app_id, installed.manifest_id) / "dxgi.dll"

    # An earlier lifecycle restored the verified original but left the active
    # manifest pointing at the otherwise still-installed OptiScaler payload.
    proxy.write_bytes(backup.read_bytes())
    verified = service.verify(game)
    assert verified.installation_state == "partial"
    assert any(
        issue["kind"] == "original_file_already_restored"
        and issue["path"] == "dxgi.dll"
        for issue in verified.issues
    )

    removed = service.remove(game)
    assert removed.installation_state == "removed"
    assert proxy.read_bytes() == original
    assert not (proxy.parent / "OptiScaler.ini").exists()
    assert not (proxy.parent / "plugins" / "helper.dll").exists()


def test_changed_mutable_ini_is_preserved_and_does_not_block_remove(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    installed = service.install(game, archive)
    install_dir = root / "Binaries" / "Win64"
    ini = install_dir / "OptiScaler.ini"
    changed = b"[OptiScaler]\nRuntimeChanged=true\n"
    ini.write_bytes(changed)

    verified = service.verify(game)
    assert verified.installation_state == "installed"
    assert verified.state == "configuration_changed"
    removed = service.remove(game)

    assert removed.installation_state == "removed"
    assert not ini.exists()
    assert not (install_dir / "dxgi.dll").exists()
    assert not (install_dir / "plugins" / "helper.dll").exists()
    manifest = service._load_manifest(removed)
    preserved = manifest["last_removal"]["preserved_configurations"]
    assert len(preserved) == 1
    preserved_path = Path(preserved[0]["preserved_path"])
    assert preserved_path.read_bytes() == changed
    assert preserved_path.is_relative_to(
        service.backup_root(installed.app_id, installed.manifest_id)
    )


def test_changed_preexisting_ini_is_preserved_then_original_is_restored(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    original = b"[OriginalGameConfig]\nKeep=true\n"
    ini.write_bytes(original)
    installed = service.install(game, archive, allow_replace_conflicts=True)
    changed = b"[OptiScaler]\nRuntimeChanged=true\n"
    ini.write_bytes(changed)

    removed = service.remove(game)
    manifest = service._load_manifest(removed)

    assert removed.installation_state == "removed"
    assert ini.read_bytes() == original
    assert manifest["last_removal"]["preserved_configurations"]
    preserved_path = Path(
        manifest["last_removal"]["preserved_configurations"][0][
            "preserved_path"
        ]
    )
    assert preserved_path.read_bytes() == changed
    original_backup = service.backup_root(
        installed.app_id, installed.manifest_id
    ) / "OptiScaler.ini"
    assert original_backup.read_bytes() == original


def test_remove_preflight_conflict_leaves_all_files_and_manifest_unchanged(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    installed = service.install(game, archive)
    install_dir = root / "Binaries" / "Win64"
    proxy = install_dir / "dxgi.dll"
    ini = install_dir / "OptiScaler.ini"
    helper = install_dir / "plugins" / "helper.dll"
    proxy.write_bytes(b"foreign modified DLL")
    manifest_path = service.manifest_path(installed.app_id, installed.manifest_id)
    manifest_before = manifest_path.read_bytes()

    with pytest.raises(
        OptiScalerConflictError,
        match="modified immutable managed file blocks removal: dxgi.dll",
    ):
        service.remove(game)

    assert proxy.read_bytes() == b"foreign modified DLL"
    assert ini.is_file()
    assert helper.is_file()
    assert manifest_path.read_bytes() == manifest_before
    current = service.profile_repository.load(installed.app_id)
    assert current.enabled is True
    assert current.installation_state == "installed"


def test_remove_mid_operation_failure_rolls_back_all_game_files(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, game, archive, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    proxy = install_dir / "dxgi.dll"
    proxy.write_bytes(b"original proxy")
    installed = service.install(game, archive, allow_replace_conflicts=True)
    before = {
        relative: (install_dir / relative).read_bytes()
        for relative in ("dxgi.dll", "OptiScaler.ini", "plugins/helper.dll")
    }
    manifest_path = service.manifest_path(installed.app_id, installed.manifest_id)
    manifest_before = manifest_path.read_bytes()
    original_copy = service._copy_atomic
    failed = False

    def fail_first_original_restore(source: object, target: Path) -> None:
        nonlocal failed
        if (
            not failed
            and isinstance(source, Path)
            and source.name == "dxgi.dll"
            and target == proxy
            and ".remove-rollback-" not in str(source)
        ):
            failed = True
            raise OSError("synthetic restore write failure")
        original_copy(source, target)

    monkeypatch.setattr(service, "_copy_atomic", fail_first_original_restore)

    with pytest.raises(OSError, match="synthetic restore write failure"):
        service.remove(game)

    assert failed is True
    for relative, content in before.items():
        assert (install_dir / relative).read_bytes() == content
    assert manifest_path.read_bytes() == manifest_before
    assert service.profile_repository.load(installed.app_id).installation_state == (
        "installed"
    )


def test_remove_reports_partial_when_mid_operation_rollback_fails(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, game, archive, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    proxy = install_dir / "dxgi.dll"
    proxy.write_bytes(b"original proxy")
    installed = service.install(game, archive, allow_replace_conflicts=True)
    original_copy = service._copy_atomic
    failed_restore = False

    def fail_operation_and_one_rollback(source: object, target: Path) -> None:
        nonlocal failed_restore
        source_text = str(source)
        if (
            not failed_restore
            and isinstance(source, Path)
            and source.name == "dxgi.dll"
            and target == proxy
            and ".remove-rollback-" not in source_text
        ):
            failed_restore = True
            raise OSError("synthetic operation failure")
        if ".remove-rollback-" in source_text and target.name == "OptiScaler.ini":
            raise OSError("synthetic rollback failure")
        original_copy(source, target)

    monkeypatch.setattr(service, "_copy_atomic", fail_operation_and_one_rollback)

    with pytest.raises(
        OptiScalerError, match="removal failed and rollback was incomplete"
    ):
        service.remove(game)

    current = service.profile_repository.load(installed.app_id)
    status = service.status(game)
    assert current.installation_state == "partial"
    assert current.enabled is True
    assert status["installationState"] == "partial"
    manifest = service._load_manifest(current)
    assert manifest["last_removal"]["state"] == "partial"
    assert any(
        "OptiScaler.ini" in item
        for item in manifest["last_removal"]["rollback_errors"]
    )


def test_pre_cancelled_install_leaves_game_unchanged(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    cancelled = Event()
    cancelled.set()
    with pytest.raises(OptiScalerCancelled):
        service.install(game, archive, cancel_event=cancelled)
    assert not (root / "Binaries" / "Win64" / "dxgi.dll").exists()


def test_hash_failure_rolls_back_existing_file(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    base, game, archive, root = setup_service

    class CorruptingService(OptiScalerService):
        def _copy_atomic(self, source: object, target: Path) -> None:
            super()._copy_atomic(source, target)  # type: ignore[arg-type]
            if (
                target == root / "Binaries" / "Win64" / "dxgi.dll"
                and not getattr(self, "_test_corrupted", False)
            ):
                self._test_corrupted = True
                target.write_bytes(target.read_bytes() + b"corrupt")

    service = CorruptingService(
        profile_repository=base.profile_repository,
        data_root=base.data_root,
        executable_resolver=base.executable_resolver,
        process_detector=lambda _path: (),
    )
    original = root / "Binaries" / "Win64" / "dxgi.dll"
    original.write_bytes(b"original")
    with pytest.raises(OptiScalerError, match="hash mismatch"):
        service.install(game, archive, allow_replace_conflicts=True)
    assert original.read_bytes() == b"original"
    assert not (original.parent / "OptiScaler.ini").exists()


def test_profiles_are_isolated_per_appid(tmp_path: Path) -> None:
    repository = OptiScalerProfileRepository(tmp_path / "config")
    first = repository.load("100")
    repository.save(replace(first, executable="Game.exe", installation_state="planned"))
    assert repository.load("100").executable == "Game.exe"
    assert repository.load("200").executable == ""


def test_remember_executable_persists_only_a_validated_game_path(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive_path, root = setup_service
    selected = root / "Binaries" / "Win64" / "TestGame-Win64-Shipping.exe"
    profile = service.remember_executable(game, str(selected))
    assert profile.executable == "Binaries/Win64/TestGame-Win64-Shipping.exe"
    assert service.profile_repository.load("224760").executable == profile.executable

    outside = tmp_path / "Outside.exe"
    outside.write_bytes(b"not part of the game")
    with pytest.raises(OptiScalerError, match="inside the game directory"):
        service.remember_executable(game, str(outside))
    assert service.profile_repository.load("224760").executable == profile.executable


def test_update_preserves_first_install_backup_lineage(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, first_archive, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    proxy = install_dir / "dxgi.dll"
    proxy.write_bytes(b"user proxy before Game Optimization")
    original_hash = _hash(proxy)
    first = service.install(game, first_archive, allow_replace_conflicts=True)

    update_archive = _versioned_archive(
        tmp_path / "OptiScaler_v0.8.0.zip",
        proxy=b"new release proxy",
        ini=b"[OptiScaler]\nEnabled=false\n",
    )
    plan = service.plan(game, update_archive)
    assert plan.requires_conflict_confirmation is False
    updated = service.install(game, update_archive, operation="update")
    assert updated.manifest_id != first.manifest_id
    assert proxy.read_bytes() == b"new release proxy"

    manifest = service._load_manifest(updated)
    assert manifest["format_version"] == 2
    replacement = next(
        item for item in manifest["replaced_files"]
        if item["relative_path"] == "dxgi.dll"
    )
    assert replacement["before_sha256"] == original_hash
    assert manifest["previous_manifest_id"] == first.manifest_id
    assert manifest["operation"] == "update"
    carried_backup = service.backup_root(updated.app_id, updated.manifest_id) / "dxgi.dll"
    assert carried_backup.read_bytes() == b"user proxy before Game Optimization"

    assert service.remove(game).installation_state == "removed"
    assert proxy.read_bytes() == b"user proxy before Game Optimization"
    assert _hash(proxy) == original_hash


def test_update_keeps_created_file_provenance_and_removes_obsolete_payload(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, first_archive, root = setup_service
    first = service.install(game, first_archive)
    install_dir = root / "Binaries" / "Win64"
    helper = install_dir / "plugins" / "helper.dll"
    assert helper.is_file()

    update_archive = _versioned_archive(
        tmp_path / "OptiScaler_v0.9.0.zip",
        proxy=b"updated proxy",
        ini=b"[OptiScaler]\nUpdated=true\n",
        include_helper=False,
    )
    updated = service.install(game, update_archive, operation="update")
    assert updated.manifest_id != first.manifest_id
    assert not helper.exists()
    manifest = service._load_manifest(updated)
    assert {
        item["relative_path"] for item in manifest["created_files"]
    } == {"dxgi.dll", "OptiScaler.ini"}
    assert {
        item["relative_path"] for item in manifest["reconciled_files"]
        if item["action"] == "removed_obsolete_managed_file"
    } == {"plugins/helper.dll"}

    removed = service.remove(game)
    assert removed.installation_state == "removed"
    assert not (install_dir / "dxgi.dll").exists()
    assert not (install_dir / "OptiScaler.ini").exists()


def test_repair_preserves_original_lineage_and_archives_intervening_change(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    proxy = root / "Binaries" / "Win64" / "dxgi.dll"
    proxy.write_bytes(b"original user proxy")
    original_hash = _hash(proxy)
    service.install(game, archive, allow_replace_conflicts=True)
    proxy.write_bytes(b"third-party changed proxy")
    intervening_hash = _hash(proxy)

    with pytest.raises(OptiScalerConflictError):
        service.install(game, archive, operation="repair")
    repaired = service.install(
        game,
        archive,
        operation="repair",
        allow_replace_conflicts=True,
    )
    manifest = service._load_manifest(repaired)
    assert manifest["operation"] == "repair"
    assert manifest["replaced_files"][0]["before_sha256"] == original_hash
    displaced = next(
        item for item in manifest["displaced_files"]
        if item["relative_path"] == "dxgi.dll"
    )
    assert displaced["sha256"] == intervening_hash
    displaced_path = service.backup_root(repaired.app_id, repaired.manifest_id) / str(
        displaced["backup_relative_path"]
    )
    assert displaced_path.read_bytes() == b"third-party changed proxy"

    service.remove(game)
    assert proxy.read_bytes() == b"original user proxy"


def test_reinstall_does_not_remove_unknown_mod_files(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    unknown = root / "Binaries" / "Win64" / "mods" / "unrelated.dll"
    unknown.parent.mkdir()
    unknown.write_bytes(b"foreign mod")
    service.install(game, archive, operation="reinstall")
    assert unknown.read_bytes() == b"foreign mod"
    service.remove(game)
    assert unknown.read_bytes() == b"foreign mod"


def test_same_version_reinstall_keeps_first_install_backup_lineage(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    proxy = install_dir / "dxgi.dll"
    ini = install_dir / "OptiScaler.ini"
    original_proxy = b"original game proxy"
    original_ini = b"[Original]\nGameSetting=true\n"
    proxy.write_bytes(original_proxy)
    ini.write_bytes(original_ini)
    first = service.install(game, archive, allow_replace_conflicts=True)
    service.configure_fsr4_update(game, True)
    ini.write_text(
        ini.read_text(encoding="utf-8") + "RuntimeChanged=true\n",
        encoding="utf-8",
    )

    second = service.install(game, archive)
    active = service.profile_repository.load(first.app_id)
    second_manifest = service._load_manifest(second)

    assert second.manifest_id != first.manifest_id
    assert active.manifest_id == second.manifest_id
    assert second_manifest["previous_manifest_id"] == first.manifest_id
    assert second_manifest["operation"] == "repair"
    replacement_by_name = {
        item["relative_path"]: item
        for item in second_manifest["replaced_files"]
    }
    assert replacement_by_name["dxgi.dll"]["before_sha256"] == sha256(
        original_proxy
    ).hexdigest()
    assert replacement_by_name["OptiScaler.ini"]["before_sha256"] == sha256(
        original_ini
    ).hexdigest()
    assert (
        service.backup_root(second.app_id, second.manifest_id) / "dxgi.dll"
    ).read_bytes() == original_proxy
    assert (
        service.backup_root(second.app_id, second.manifest_id) / "OptiScaler.ini"
    ).read_bytes() == original_ini

    removed = service.remove(game)
    assert removed.installation_state == "removed"
    assert proxy.read_bytes() == original_proxy
    assert ini.read_bytes() == original_ini


def test_configure_verify_reinstall_remove_leaves_no_created_payload(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    service.configure_fsr4_update(game, True)
    assert service.verify(game).state == "verified"

    reinstalled = service.install(game, archive)
    assert reinstalled.installation_state == "installed"
    assert service.verify(game).state == "verified"
    removed = service.remove(game)

    install_dir = root / "Binaries" / "Win64"
    assert removed.installation_state == "removed"
    assert not (install_dir / "dxgi.dll").exists()
    assert not (install_dir / "OptiScaler.ini").exists()
    assert not (install_dir / "plugins" / "helper.dll").exists()


def test_failed_update_restores_previous_release_and_profile(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    base, game, first_archive, root = setup_service

    class CorruptingUpdateService(OptiScalerService):
        corrupt_update = False
        corrupted_once = False

        def _copy_atomic(self, source: object, target: Path) -> None:
            super()._copy_atomic(source, target)  # type: ignore[arg-type]
            if (
                self.corrupt_update
                and not self.corrupted_once
                and target == root / "Binaries" / "Win64" / "dxgi.dll"
            ):
                self.corrupted_once = True
                target.write_bytes(target.read_bytes() + b"broken update")

    service = CorruptingUpdateService(
        profile_repository=base.profile_repository,
        data_root=base.data_root,
        executable_resolver=base.executable_resolver,
        process_detector=lambda _path: (),
    )
    first = service.install(game, first_archive)
    proxy = root / "Binaries" / "Win64" / "dxgi.dll"
    first_bytes = proxy.read_bytes()
    update_archive = _versioned_archive(
        tmp_path / "OptiScaler_v1.0.0.zip",
        proxy=b"version one proxy",
        ini=b"[OptiScaler]\nVersion=1\n",
    )
    service.corrupt_update = True
    with pytest.raises(OptiScalerError, match="installed file hash mismatch"):
        service.install(game, update_archive, operation="update")

    assert proxy.read_bytes() == first_bytes
    assert service.profile_repository.load(game.steam_app_id).manifest_id == first.manifest_id
    manifests = list(
        (service.data_root / first.app_id / "optiscaler" / "manifests").glob("*.json")
    )
    assert manifests == [service.manifest_path(first.app_id, first.manifest_id)]


def test_anticheat_and_running_game_block_installation(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    base, game, archive, root = setup_service
    (root / "EasyAntiCheat").mkdir()
    service = OptiScalerService(
        profile_repository=base.profile_repository,
        data_root=base.data_root,
        executable_resolver=base.executable_resolver,
        process_detector=lambda _path: (123,),
    )
    plan = service.plan(game, archive)
    assert any("Anti-cheat" in item for item in plan.blockers)
    assert any("currently running" in item for item in plan.blockers)
    assert plan.can_install is False


def test_anticheat_requires_explicit_confirmation_but_can_be_manually_allowed(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    (root / "EasyAntiCheat").mkdir()
    blocked = service.plan(game, archive)
    assert any("Anti-cheat" in item for item in blocked.blockers)

    confirmed = service.plan(game, archive, allow_anticheat_risk=True)
    assert not any("Anti-cheat" in item for item in confirmed.blockers)
    assert any("explicit risk confirmation" in item for item in confirmed.warnings)
    installed = service.install(
        game,
        archive,
        allow_anticheat_risk=True,
    )
    assert installed.installation_state == "installed"


def test_managed_fsr4_setting_updates_ini_and_manifest_hash(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    installed = service.install(game, archive)
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"

    updated = service.configure_fsr4_update(game, True)
    manifest = service._load_manifest(updated)
    ini_entry = next(
        item
        for item in manifest["installed_files"]
        if item["relative_path"] == "OptiScaler.ini"
    )

    assert "Fsr4Update=true" in ini.read_text(encoding="utf-8")
    assert ini_entry["after_sha256"] == _hash(ini)
    assert manifest["managed_settings"]["Fsr4Update"] is True
    assert service.verify(game).installation_state == "installed"
    assert updated.manifest_id == installed.manifest_id


def test_verify_keeps_installation_valid_when_runtime_changes_ini(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    ini.write_text(
        ini.read_text(encoding="utf-8") + "RuntimePersistedSetting=true\n",
        encoding="utf-8",
    )

    verified = service.verify(game)
    status = service.status(game)

    assert verified.installation_state == "installed"
    assert status["installed"] is True
    assert status["installationVerificationState"] == "configuration_changed"
    assert status["installationPayloadValid"] is True
    assert status["configurationDrift"] is True
    assert status["managedConfigurationDrift"] is False
    assert "user or runtime" in status["installationVerificationSummary"]


def test_verify_reports_managed_ini_drift_without_corrupting_payload(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    service.configure_fsr4_update(game, True)
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    ini.write_text(
        ini.read_text(encoding="utf-8").replace(
            "Fsr4Update=true", "Fsr4Update=false"
        ),
        encoding="utf-8",
    )

    verified = service.verify(game)
    status = service.status(game)

    assert verified.installation_state == "installed"
    assert status["installationVerificationState"] == "managed_configuration_drift"
    assert status["installationPayloadValid"] is True
    assert status["managedConfigurationDrift"] is True
    assert any(
        issue["kind"] == "managed_configuration_drift"
        and issue["key"] == "Fsr4Update"
        and issue["actual"] == "false"
        for issue in status["verificationIssues"]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_state", "expected_kind"),
    (
        ("missing", "missing_files", "managed_file_missing"),
        ("corrupt", "corrupt_files", "managed_file_hash_mismatch"),
    ),
)
def test_verify_reports_missing_or_corrupt_managed_dll(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    mutation: str,
    expected_state: str,
    expected_kind: str,
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    proxy = root / "Binaries" / "Win64" / "dxgi.dll"
    if mutation == "missing":
        proxy.unlink()
    else:
        proxy.write_bytes(b"third-party or damaged proxy")

    verified = service.verify(game)
    status = service.status(game)

    assert verified.installation_state == "corrupt"
    assert status["installed"] is False
    assert status["installationVerificationState"] == expected_state
    assert status["installationPayloadValid"] is False
    assert any(
        issue["kind"] == expected_kind and issue["path"] == "dxgi.dll"
        for issue in status["verificationIssues"]
    )


def test_verify_reports_unmanaged_proxy_without_invalidating_payload(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    (root / "Binaries" / "Win64" / "version.dll").write_bytes(
        b"unmanaged proxy"
    )

    verified = service.verify(game)
    status = service.status(game)

    assert verified.installation_state == "installed"
    assert status["installationVerificationState"] == "unmanaged_conflict"
    assert status["installationPayloadValid"] is True
    assert any(
        issue["kind"] == "unmanaged_conflict"
        and issue["path"] == "version.dll"
        for issue in status["verificationIssues"]
    )


def test_manifest_io_failure_preserves_last_known_installation_status(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, _root = setup_service
    installed = service.install(game, archive)
    service.manifest_path(installed.app_id, installed.manifest_id).write_text(
        "not-json", encoding="utf-8"
    )

    with pytest.raises(OptiScalerError, match="could not read OptiScaler manifest"):
        service.verify(game)
    status = service.status(game)

    assert status["success"] is True
    assert status["installed"] is True
    assert status["installedVersion"] == installed.installed_version
    assert status["installationVerificationState"] == "verification_error"
    assert "could not read OptiScaler manifest" in status["manifestError"]


def test_fsr4_setting_preserves_unknown_user_modified_ini(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    ini.write_text(
        "[OptiScaler]\nCustomSetting=user value\n",
        encoding="utf-8",
    )

    updated = service.configure_fsr4_update(game, True)
    text = ini.read_text(encoding="utf-8")
    manifest = service._load_manifest(updated)

    assert "CustomSetting=user value" in text
    assert "[FSR]\nFsr4Update=true" in text
    assert manifest["managed_ini_history"][-1]["preserved_external_settings"] is True


def test_old_optiscaler_and_nvidia_bridge_files_require_confirmation(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    (install_dir / "nvapi64.dll").write_bytes(b"old nvapi bridge")
    (install_dir / "nvngx.dll").write_bytes(b"old nvngx bridge")
    (install_dir / "OptiScaler.dll").write_bytes(b"old OptiScaler")
    plan = service.plan(game, archive)
    names = {item.relative_path.casefold() for item in plan.conflicts}
    assert {"nvapi64.dll", "nvngx.dll", "optiscaler.dll"} <= names
    assert plan.to_dict()["requiresConflictConfirmation"] is True


def test_fsr4_sdk_dll_uses_manifest_backup_and_restore(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive_path, root = setup_service
    install_dir = root / "Binaries" / "Win64"
    fidelityfx = install_dir / "amd_fidelityfx_upscaler_dx12.dll"
    fidelityfx.write_bytes(b"original game FSR DLL")
    archive = tmp_path / "OptiScaler_v0.9.4.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("release/OptiScaler.dll", b"proxy")
        package.writestr(
            "release/OptiScaler.ini",
            "[FSR]\nFsr4Update=auto\nFsr4ForceEnableInt8=auto\n"
            "FsrAgilitySDKUpgrade=auto\nFsr4EnableWatermark=auto\n",
        )
        package.writestr(
            "release/amd_fidelityfx_upscaler_dx12.dll", b"FSR 4.1.1 SDK"
        )

    plan = service.plan(game, archive)
    assert plan.requires_conflict_confirmation is True
    with pytest.raises(OptiScalerConflictError):
        service.install(game, archive)

    installed = service.install(
        game,
        archive,
        allow_replace_conflicts=True,
        source_identity="official_optiscaler",
        fidelityfx_upscaler_version="4.1.1",
    )
    manifest = service._load_manifest(installed)
    replacement = next(
        item
        for item in manifest["replaced_files"]
        if item["relative_path"] == "amd_fidelityfx_upscaler_dx12.dll"
    )
    assert replacement["before_sha256"] == sha256(
        b"original game FSR DLL"
    ).hexdigest()
    assert fidelityfx.read_bytes() == b"FSR 4.1.1 SDK"
    assert service.status(game)["fsr4AssetsInstalled"] is True

    assert service.remove(game).installation_state == "removed"
    assert fidelityfx.read_bytes() == b"original game FSR DLL"


def test_managed_fsr4_configuration_uses_supported_keys_and_is_idempotent(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive_path, root = setup_service
    archive = tmp_path / "OptiScaler_v0.9.4.zip"
    ini_payload = (
        "[FSR]\n"
        "Fsr4Update=auto\n"
        "Fsr4ForceEnableInt8=auto\n"
        "FsrAgilitySDKUpgrade=auto\n"
        "Fsr4EnableWatermark=auto\n"
        "UserFsrSetting=keep\n"
        "[Upscalers]\n"
        "# Available values: auto, fsr31, dlss\n"
        "Dx11Upscaler=auto\n"
        "# Available values: auto, fsr31, xess, dlss\n"
        "Dx12Upscaler=auto\n"
        "# Available values: auto, fsr31_12, dlss\n"
        "VulkanUpscaler=auto\n"
        "Dx12Upscaler=fsr31\n"
        "[User]\nUnknownKey=preserved\n"
    )
    with ZipFile(archive, "w") as package:
        package.writestr("release/OptiScaler.dll", b"proxy")
        package.writestr("release/OptiScaler.ini", ini_payload)
        package.writestr(
            "release/amd_fidelityfx_upscaler_dx12.dll", b"FSR 4.1.1 SDK"
        )
    service.install(game, archive)

    first = service.configure_upscaling(
        game,
        fsr4_mode="force_int8",
        fsr_agility_sdk_upgrade=False,
        fsr4_watermark=True,
        dx11_upscaler="fsr31",
        dx12_upscaler="fsr31",
        vulkan_upscaler="fsr31_12",
    )
    second = service.configure_upscaling(
        game,
        fsr4_mode="force_int8",
        fsr_agility_sdk_upgrade=False,
        fsr4_watermark=True,
        dx11_upscaler="fsr31",
        dx12_upscaler="fsr31",
        vulkan_upscaler="fsr31_12",
    )
    text = (root / "Binaries" / "Win64" / "OptiScaler.ini").read_text(
        encoding="utf-8"
    )

    assert "Fsr4Update=true" in text
    assert "Fsr4ForceEnableInt8=true" in text
    assert "Fsr4EnableWatermark=true" in text
    assert "UnknownKey=preserved" in text
    assert text.casefold().count("dx12upscaler=") == 1
    assert first.fsr4_mode == "force_int8"
    assert second.runtime_verification_status == "not_verified"
    assert service.verify(game).installation_state == "installed"


def test_watermark_enable_disable_apply_reload_uses_effective_game_ini(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive_path, root = setup_service
    archive = tmp_path / "OptiScaler_v0.9.4.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("release/OptiScaler.dll", b"proxy")
        package.writestr(
            "release/OptiScaler.ini",
            "[FSR]\nFsr4Update=auto\nFsr4ForceEnableInt8=auto\n"
            "FsrAgilitySDKUpgrade=auto\nFsr4EnableWatermark=auto\n"
            "[Upscalers]\nDx11Upscaler=auto\nDx12Upscaler=auto\n"
            "VulkanUpscaler=auto\n",
        )
    service.install(game, archive)

    common = {
        "fsr4_mode": "force_int8",
        "fsr_agility_sdk_upgrade": False,
        "dx11_upscaler": "auto",
        "dx12_upscaler": "auto",
        "vulkan_upscaler": "auto",
    }
    service.configure_upscaling(game, fsr4_watermark=True, **common)
    assert service.status(game)["fsr4Watermark"] is True
    service.configure_upscaling(game, fsr4_watermark=False, **common)

    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    assert "Fsr4EnableWatermark=auto" in ini.read_text(encoding="utf-8")
    reloaded = OptiScalerService(
        profile_repository=service.profile_repository,
        data_root=service.data_root,
        executable_resolver=GameExecutableResolver(),
        process_detector=lambda _path: (),
    )
    assert reloaded.status(game)["fsr4Watermark"] is False
    assert reloaded.status(game)["fsr4WatermarkState"] == "auto"
    assert reloaded.status(game)["requestedFsr4Mode"] == "force_int8"
    assert reloaded.status(game)["effectiveConfiguredFsr4Mode"] == "force_int8"
    assert reloaded.status(game)["configurationApplied"] is True
    assert reloaded.status(game)["runtimeVerified"] is False

    # Reproduce the real-game drift: OptiScaler's runtime UI rewrites the INI.
    ini.write_text(
        ini.read_text(encoding="utf-8").replace(
            "Fsr4EnableWatermark=auto", "Fsr4EnableWatermark=true"
        ),
        encoding="utf-8",
    )
    effective = reloaded.status(game)
    assert effective["fsr4Watermark"] is True
    assert effective["fsr4WatermarkState"] == "true"


def test_controller_automatic_mode_records_explainable_rdna2_int8_choice(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive_path, root = setup_service
    archive = tmp_path / "OptiScaler_v0.9.4.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("release/OptiScaler.dll", b"proxy")
        package.writestr(
            "release/OptiScaler.ini",
            "[FSR]\nFsr4Update=auto\nFsr4ForceEnableInt8=auto\n"
            "FsrAgilitySDKUpgrade=auto\nFsr4EnableWatermark=auto\n"
            "[Upscalers]\n"
            "# Available values: auto, fsr31_12\nDx11Upscaler=auto\n"
            "# Available values: auto, fsr31\nDx12Upscaler=auto\n"
            "# Available values: auto, fsr31_12\nVulkanUpscaler=auto\n",
        )
        package.writestr(
            "release/amd_fidelityfx_upscaler_dx12.dll", b"FSR 4.1.1 SDK"
        )
    service.install(
        game,
        archive,
        release_version="0.9.4-final",
        fidelityfx_upscaler_version="4.1.1",
        source_identity="official_optiscaler",
    )
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-auto.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    controller._optiscaler_controller._detected_game_context = lambda _app_id: {
        "gpu": "AMD Radeon RX 6600 XT",
        "graphicsApi": "Direct3D 12",
        "graphicsApiConfidence": 0.95,
        "gameUpscaler": "FSR 3.1",
        "runtime": "Proton",
    }
    try:
        result = controller.configureOptiScalerUpscaling(
            game.id,
            {
                "fsr4Mode": "automatic",
                "fsrAgilitySdkUpgrade": False,
                "fsr4Watermark": False,
                "dx11Upscaler": "auto",
                "dx12Upscaler": "auto",
                "vulkanUpscaler": "auto",
            },
        )
        text = (root / "Binaries" / "Win64" / "OptiScaler.ini").read_text(
            encoding="utf-8"
        )

        assert result["success"] is True
        assert result["fsr4Mode"] == "automatic"
        assert result["effectiveFsr4Mode"] == "force_int8"
        assert "native FP8" in result["automaticReason"]
        assert "Fsr4ForceEnableInt8=true" in text
        assert "Fsr4EnableWatermark=auto" in text
        assert result["runtimeVerified"] is False
        status = controller.getOptiScalerStatus(game.id)
        assert status["watermarkRequestedLabel"] == "Disabled"
        assert status["runtimeOverlayStatus"] == "unknown"
        assert status["watermarkEffectiveIniLabel"] == "Disabled (upstream default)"
        assert "forcedInt8SdkOverlayLimitation" not in status
    finally:
        controller.shutdown()


def test_optiscaler_status_request_never_blocks_qml_on_detection(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive, _root = setup_service
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-status.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    original = controller._optiscaler_controller._build_optiscaler_status

    def slow_status(game_id: str) -> dict[str, object]:
        time.sleep(0.2)
        return original(game_id)

    controller._optiscaler_controller._build_optiscaler_status = slow_status
    try:
        started = time.perf_counter()
        pending = controller.requestOptiScalerStatus(game.id, False)
        elapsed = time.perf_counter() - started

        assert elapsed < 0.05
        assert pending["loading"] is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_controller._status_jobs:
                break
            time.sleep(0.01)
        cached = controller.requestOptiScalerStatus(game.id, False)
        assert cached["success"] is True
        assert cached["loading"] is False
    finally:
        controller.shutdown()


def test_failed_async_status_refresh_preserves_installed_snapshot_and_error(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, _root = setup_service
    installed = service.install(game, archive)
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-refresh-error.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    published: list[dict[str, object]] = []
    controller.optiScalerStatusChanged.connect(
        lambda _game_id, result: published.append(dict(result))
    )

    def fail_status(_game_id: str) -> dict[str, object]:
        raise OSError("synthetic manifest read failure")

    try:
        previous = controller.getOptiScalerStatus(game.id)
        controller._optiscaler_controller._build_optiscaler_status = fail_status
        controller.requestOptiScalerStatus(game.id, True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_controller._status_jobs:
                break
            time.sleep(0.01)

        failed = published[-1]
        assert failed["success"] is True
        assert failed["snapshotState"] == "installed"
        assert failed["installed"] is True
        assert failed["installedVersion"] == installed.installed_version
        assert failed["installDirectory"] == previous["installDirectory"]
        assert failed["manifestId"] == previous["manifestId"]
        assert "synthetic manifest read failure" in str(failed["refreshError"])
        assert failed["snapshotFromCache"] is True
    finally:
        controller.shutdown()


def test_status_exception_without_message_keeps_exception_type_diagnostic(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, _archive, _root = setup_service
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-silent-error.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )

    class SilentStatusError(RuntimeError):
        def __str__(self) -> str:
            return ""

    def fail_silently(_game_id: str) -> dict[str, object]:
        raise SilentStatusError()

    try:
        controller._optiscaler_controller._build_optiscaler_status = fail_silently
        controller.requestOptiScalerStatus(game.id, True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if not controller._optiscaler_controller._status_jobs:
                break
            time.sleep(0.01)
        status = controller.requestOptiScalerStatus(game.id, False)
        assert status["snapshotState"] == "unknown"
        assert "SilentStatusError" in status["refreshError"]
        assert "Unknown error" not in status["refreshError"]
    finally:
        controller.shutdown()


def test_stale_failed_refresh_cannot_replace_newer_successful_snapshot(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, _root = setup_service
    service.install(game, archive)
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-generation.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    original = controller._optiscaler_controller._build_optiscaler_status
    release_first = Event()
    calls = 0
    published: list[dict[str, object]] = []
    controller.optiScalerStatusChanged.connect(
        lambda _game_id, result: published.append(dict(result))
    )

    def stale_then_current(game_id: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            release_first.wait(timeout=2.0)
            raise OSError("stale refresh failure")
        return original(game_id)

    try:
        controller.getOptiScalerStatus(game.id)
        controller._optiscaler_controller._build_optiscaler_status = (
            stale_then_current
        )
        controller.requestOptiScalerStatus(game.id, True)
        controller._optiscaler_controller._invalidate_status("224760")
        release_first.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if calls >= 2 and not controller._optiscaler_controller._status_jobs:
                break
            time.sleep(0.01)

        assert calls == 2
        assert len(published) == 1
        assert published[0]["snapshotState"] == "installed"
        assert published[0]["refreshError"] == ""
        assert "stale refresh failure" not in str(published[0])
    finally:
        controller.shutdown()


def test_controller_exposes_real_installation_as_one_tasks_entry(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, _root = setup_service
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    try:
        assert controller.installOptiScaler(
            game.id,
            str(archive),
            "Binaries/Win64/TestGame-Win64-Shipping.exe",
            "dxgi.dll",
            False,
        ) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            rows = [
                row for row in controller.tasks
                if str(row.get("operation", "")) == "OptiScaler"
            ]
            if rows and rows[0]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        assert len(rows) == 1
        assert rows[0]["status"] == "completed"
        assert rows[0]["progressPercent"] == 100.0
        assert service.profile_repository.load("224760").installation_state == "installed"
    finally:
        controller.shutdown()


def test_controller_verify_reports_runtime_ini_drift_without_losing_install(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    ini.write_text(
        ini.read_text(encoding="utf-8") + "RuntimePersistedSetting=true\n",
        encoding="utf-8",
    )
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-verify.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    try:
        before = controller.getOptiScalerStatus(game.id)
        assert before["installed"] is True
        assert controller.verifyOptiScaler(game.id) is True

        deadline = time.monotonic() + 3.0
        rows: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            controller._poll_tasks()
            rows = [
                row
                for row in controller.tasks
                if str(row.get("operation", "")) == "OptiScaler"
            ]
            if rows and rows[0]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.01)

        assert rows[0]["status"] == "completed"
        assert rows[0]["result"]["installationVerificationState"] == (
            "configuration_changed"
        )
        assert "user or runtime" in rows[0]["result"][
            "installationVerificationSummary"
        ]
        after = controller.getOptiScalerStatus(game.id)
        assert after["installed"] is True
        assert after["installedVersion"] == before["installedVersion"]
        assert after["installationVerificationState"] == "configuration_changed"
    finally:
        controller.shutdown()


def test_failed_remove_publishes_precise_operation_diagnostic(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    (root / "Binaries" / "Win64" / "dxgi.dll").write_bytes(
        b"modified immutable DLL"
    )
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-remove-error.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    published: list[dict[str, object]] = []
    controller.optiScalerStatusChanged.connect(
        lambda _game_id, result: published.append(dict(result))
    )
    try:
        controller.getOptiScalerStatus(game.id)
        assert controller.removeOptiScaler(game.id) is True
        deadline = time.monotonic() + 3.0
        rows: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            controller._poll_tasks()
            rows = [
                row
                for row in controller.tasks
                if str(row.get("operation", "")) == "OptiScaler"
            ]
            if (
                rows
                and rows[0]["status"] == "failed"
                and not controller._optiscaler_controller._status_jobs
            ):
                break
            time.sleep(0.01)

        assert rows[0]["status"] == "failed"
        assert "modified immutable managed file blocks removal: dxgi.dll" in str(
            rows[0]["error"]
        )
        assert "Unknown error" not in str(rows[0]["error"])
        assert published[-1]["snapshotState"] == "installed"
        assert "dxgi.dll" in str(published[-1]["operationError"])
        assert published[-1]["operationConflict"] == {
            "kind": "managed_file_conflict",
            "path": "dxgi.dll",
            "message": "modified immutable managed file blocks removal: dxgi.dll",
        }
    finally:
        controller.shutdown()


def test_successful_remove_publishes_status_to_visible_qml_observer(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-remove-signal.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    published: list[dict[str, object]] = []
    controller.optiScalerStatusChanged.connect(
        lambda _game_id, result: published.append(dict(result))
    )
    try:
        assert controller.getOptiScalerStatus(game.id)["snapshotState"] == (
            "installed"
        )
        assert controller.removeOptiScaler(game.id) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if (
                published
                and published[-1].get("snapshotState") == "not_installed"
                and not controller._optiscaler_controller._status_jobs
            ):
                break
            time.sleep(0.01)

        assert published[-1]["snapshotState"] == "not_installed"
        assert published[-1]["installed"] is False
        assert published[-1]["installationState"] == "removed"
        assert not (root / "Binaries" / "Win64" / "dxgi.dll").exists()
    finally:
        controller.shutdown()


def test_successful_remove_schedules_one_authoritative_status_refresh(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, game, archive, _root = setup_service
    service.install(game, archive)
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings-single-refresh.json"),
        optiscaler_service=service,
        initial_games=(game,),
        demo_mode=True,
        auto_refresh=False,
    )
    original_status = service.status
    status_calls = 0

    def counted_status(target_game: Game) -> dict[str, object]:
        nonlocal status_calls
        status_calls += 1
        return original_status(target_game)

    monkeypatch.setattr(service, "status", counted_status)
    try:
        assert controller.removeOptiScaler(game.id) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            controller._poll_tasks()
            if (
                not controller._optiscaler_jobs
                and not controller._optiscaler_controller._status_jobs
            ):
                break
            time.sleep(0.01)

        assert status_calls == 1
        assert controller.requestOptiScalerStatus(game.id, False)[
            "snapshotState"
        ] == "not_installed"
    finally:
        controller.shutdown()
