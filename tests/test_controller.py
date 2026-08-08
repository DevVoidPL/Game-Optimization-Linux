from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import SettingsStore


_QT_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


def _controller(settings_path: Path) -> AppController:
    return AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(settings_path),
    )


def test_controller_exposes_demo_library_and_navigation(tmp_path: Path) -> None:
    controller = _controller(tmp_path / "settings.json")
    try:
        assert controller.currentPage == "games"
        assert [game["name"] for game in controller.games] == [
            "Batman: Arkham Knight",
            "Dying Light",
            "Cyberpunk 2077",
            "Minecraft",
        ]

        assert controller.openGame("dying-light") is True
        assert controller.currentPage == "gameDetails"
        assert controller.selectedGame["filesystem"] == "Btrfs"
        controller.backToGames()
        assert controller.currentPage == "games"
    finally:
        controller.shutdown()


def test_controller_persists_settings_and_only_mutates_demo_backups(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.json"
    controller = _controller(settings_path)
    try:
        assert controller.saveSetting("themeMode", "dark") is True
        assert controller.saveSetting("cpuUsageLimit", 55) is True
        assert controller.themeMode == "dark"

        backup_id = controller.backups[0]["id"]
        assert controller.deleteBackup(backup_id) is True
        assert all(backup["id"] != backup_id for backup in controller.backups)
    finally:
        controller.shutdown()

    restored = _controller(settings_path)
    try:
        assert restored.themeMode == "dark"
        assert restored.settings["cpuUsageLimit"] == 55
        # Backup records are deliberately in-memory and return on a new session.
        assert len(restored.backups) == 4
    finally:
        restored.shutdown()


def test_controller_builds_text_preview_and_rejects_incompatible_compression(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path / "settings.json")
    try:
        preview = controller.buildLaunchPreview(
            "batman-arkham-knight",
            {
                "gamemode": True,
                "gamescope": True,
                "mangohud": True,
                "fpsLimit": 60,
            },
        )
        assert preview.endswith("%command%")
        assert "gamemoderun" in preview
        assert "gamescope" in preview

        non_steam_preview = controller.buildLaunchPreview(
            "cyberpunk-2077", {"profile": "Balanced"}
        )
        assert "Steam launch options are unavailable" in non_steam_preview
        assert "%command%" not in non_steam_preview

        assert controller.requestCompression("cyberpunk-2077", "Balanced") is False
        assert controller.tasks == []
        assert controller.requestCompression("batman-arkham-knight", "Balanced") is True
        assert controller.tasks[0]["status"] == "queued"
    finally:
        controller.shutdown()


def test_controller_exposes_provider_optimization_defaults(tmp_path: Path) -> None:
    controller = _controller(tmp_path / "settings.json")
    try:
        maximum = controller.optimizationDefaults("Maximum Performance")
        quiet = controller.optimizationDefaults("Quiet")

        assert maximum["gamemode"] is True
        assert maximum["gamescope"] is True
        assert maximum["cpuPerformanceProfile"] is True
        assert quiet["gamemode"] is False
        assert quiet["fpsLimit"] == 60
        assert controller.optimizationDefaults("unknown") == {}
    finally:
        controller.shutdown()
