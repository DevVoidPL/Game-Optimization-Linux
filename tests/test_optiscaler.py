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
    assert removed.installation_state == "restore_required"
    assert not (install_dir / "OptiScaler.ini").exists()
    assert original.read_bytes() == b"optiscaler proxy"

    restored = service.restore(game)
    assert restored.installation_state == "removed"
    assert _hash(original) == original_hash
    assert original.read_bytes() == b"original proxy"


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
    with pytest.raises(OptiScalerConflictError, match="was not removed"):
        service.remove(game)
    assert proxy.read_bytes() == b"changed by user"


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

    assert service.remove(game).installation_state == "restore_required"
    service.restore(game)
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
    service.restore(game)
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


def test_fsr4_setting_refuses_user_modified_ini(
    setup_service: tuple[OptiScalerService, Game, Path, Path],
) -> None:
    service, game, archive, root = setup_service
    service.install(game, archive)
    ini = root / "Binaries" / "Win64" / "OptiScaler.ini"
    ini.write_text("user modification\n", encoding="utf-8")

    with pytest.raises(OptiScalerConflictError, match="changed outside"):
        service.configure_fsr4_update(game, True)

    assert ini.read_text(encoding="utf-8") == "user modification\n"


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
