from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from gameforge.controllers import AppController
from gameforge.models import FilesystemInfo, FilesystemType, SessionType, SystemInfo
from gameforge.services import SettingsStore


class _EmptyGameProvider:
    def list_games(self) -> Sequence[Any]:
        return ()

    def get_game(self, game_id: str) -> None:
        del game_id
        return None

    def add_game(self, game: Any) -> Any:
        return game

    def refresh(self) -> Sequence[Any]:
        return ()


class _SystemProvider:
    def collect(self) -> SystemInfo:
        return SystemInfo(
            distribution="Test Linux",
            kernel="1.0",
            desktop_environment="Test",
            session_type=SessionType.WAYLAND,
            cpu="Test CPU",
            gpu="Test GPU",
            ram_gb=8.0,
            vram_gb=4.0,
            demo=False,
        )


class _FilesystemProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Path, ...], bool]] = []

    def inspect(self, path: Path) -> FilesystemInfo:
        return self._info(path)

    def list_filesystems(
        self,
        *,
        game_paths: Sequence[Path] = (),
        show_system_mounts: bool = False,
    ) -> Sequence[FilesystemInfo]:
        self.calls.append((tuple(game_paths), show_system_mounts))
        return (self._info(Path("/games")),)

    @staticmethod
    def _info(path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            label="Games",
            device="/dev/games",
            mount_options=("rw", "compress=zstd:3"),
            writable=True,
            filesystem_name="btrfs",
            size_bytes=10_000,
            used_bytes=4_000,
            available_bytes=6_000,
        )


def test_controller_merges_real_filesystems_and_toggles_system_mounts(
    tmp_path: Path,
) -> None:
    filesystems = _FilesystemProvider()
    controller = AppController(
        game_provider=_EmptyGameProvider(),  # type: ignore[arg-type]
        filesystem_provider=filesystems,
        system_provider=_SystemProvider(),  # type: ignore[arg-type]
        settings_store=SettingsStore(tmp_path / "settings.json"),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        row = controller.systemInfo["filesystems"][0]
        assert row["mountPoint"] == "/games"
        assert row["filesystem"] == "Btrfs"
        assert row["compressionSupported"] is True
        assert row["device"] == "/dev/games"
        assert row["mountOptions"] == ["rw", "compress=zstd:3"]
        assert row["writable"] is True
        assert row["filesystemName"] == "btrfs"
        assert row["sizeBytes"] == 10_000
        assert row["usedBytes"] == 4_000
        assert row["availableBytes"] == 6_000
        assert Path("/games") in filesystems.calls[0][0]
        assert filesystems.calls[0][1] is False

        controller.setShowSystemMounts(True)

        assert controller.showSystemMounts is True
        assert filesystems.calls[-1][1] is True
    finally:
        controller.shutdown()
