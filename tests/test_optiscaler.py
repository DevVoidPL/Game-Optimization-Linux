from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event
import time
from zipfile import ZipFile

import py7zr
import pytest

from gameforge.models import FilesystemType, Game, Launcher
from gameforge.controllers import AppController
from gameforge.providers import DemoGameProvider
from gameforge.services import (
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
        "src/gameforge/qml/pages/details/OptiScalerSection.qml"
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
    assert merge_wine_dll_overrides("dxgi=b;foo=n", "dxgi=n,b") == "dxgi=b,n;foo=n"
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
