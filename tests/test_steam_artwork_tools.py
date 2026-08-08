from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl
import pytest

import game_optimization_linux.controllers.app_controller as app_controller_module
from game_optimization_linux.controllers import AppController
from game_optimization_linux.controllers.presenters import game_to_qml
from game_optimization_linux.models import (
    CapabilityStatus,
    FilesystemInfo,
    FilesystemType,
    Game,
    Launcher,
    SessionType,
    SystemInfo,
)
from game_optimization_linux.providers import (
    SteamGameProvider,
    find_local_steam_artwork,
    find_local_steam_cover,
    is_steam_tool_name,
)
from game_optimization_linux.services import LibraryCache, SettingsStore


_QT_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _Filesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(path, FilesystemType.EXT4, False, filesystem_name="ext4")


class _Provider:
    def __init__(self, games: tuple[Game, ...]) -> None:
        self._games = {game.id: game for game in games}

    def list_games(self) -> tuple[Game, ...]:
        return tuple(self._games.values())

    def get_game(self, game_id: str) -> Game | None:
        return self._games.get(game_id)

    def refresh(self) -> tuple[Game, ...]:
        return self.list_games()

    def add_game(self, game: Game) -> Game:
        self._games[game.id] = game
        return game


class _System:
    def collect(self) -> SystemInfo:
        return SystemInfo(
            distribution="Test Linux",
            kernel="test",
            desktop_environment="Test Desktop",
            session_type=SessionType.UNKNOWN,
            cpu="Test CPU",
            gpu="Test GPU",
            ram_gb=8.0,
            vram_gb=0.0,
            capabilities={"OptiScaler": CapabilityStatus.GAME_DEPENDENT},
            demo=False,
        )


def _manifest(root: Path, app_id: str, name: str) -> None:
    game_path = root / "steamapps" / "common" / name
    game_path.mkdir(parents=True)
    (root / "steamapps" / f"appmanifest_{app_id}.acf").write_text(
        f'"AppState" {{ "appid" "{app_id}" "name" "{name}" '
        f'"installdir" "{name}" "SizeOnDisk" "1" }}',
        encoding="utf-8",
    )


def _game(path: Path, game_id: str, name: str, *, tool: bool) -> Game:
    return Game(
        id=game_id,
        name=name,
        launcher=Launcher.STEAM,
        install_path=path,
        logical_size_gb=1.0,
        physical_size_gb=1.0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        steam_app_id=game_id.removeprefix("steam-"),
        data_source="Steam",
        is_steam_tool=tool,
    )


def test_local_cover_prefers_portrait_and_has_fallback(tmp_path: Path) -> None:
    cache = tmp_path / "Steam" / "appcache" / "librarycache"
    cache.mkdir(parents=True)
    header = cache / "123_header.jpg"
    portrait = cache / "123_library_600x900.png"
    header_only = cache / "456_header.png"
    header.write_bytes(b"header")
    portrait.write_bytes(b"portrait")
    header_only.write_bytes(b"header fallback")

    assert find_local_steam_cover("123", [tmp_path / "Steam"]) == portrait.resolve()
    assert find_local_steam_cover("456", [tmp_path / "Steam"]) == header_only.resolve()
    assert find_local_steam_cover("999", [tmp_path / "Steam"]) is None


def test_local_artwork_keeps_portrait_header_and_generic_fallback_separate(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "Steam" / "appcache" / "librarycache"
    cache.mkdir(parents=True)
    portrait = cache / "123_library_600x900.jpg"
    header = cache / "123_header.png"
    fallback = cache / "123_logo.webp"
    for path in (portrait, header, fallback):
        path.write_bytes(path.name.encode())

    artwork = find_local_steam_artwork("123", [tmp_path / "Steam"])

    assert artwork.portrait_artwork_path == portrait.resolve()
    assert artwork.header_artwork_path == header.resolve()
    assert artwork.fallback_artwork_path == fallback.resolve()
    assert artwork.preferred_path == portrait.resolve()


def test_local_artwork_rejects_invalid_app_id_and_missing_files(tmp_path: Path) -> None:
    assert find_local_steam_artwork("../123", [tmp_path]).preferred_path is None
    assert find_local_steam_artwork("123", [tmp_path]).preferred_path is None


def test_steam_provider_caches_local_cover_path_in_game(tmp_path: Path) -> None:
    root = tmp_path / "Steam"
    _manifest(root, "4242", "Artwork Game")
    cover = root / "appcache" / "librarycache" / "4242" / "library_600x900.jpg"
    header = root / "appcache" / "librarycache" / "4242" / "header.png"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"local portrait")
    header.write_bytes(b"local header")

    game = SteamGameProvider(_Filesystem(), roots=[root]).refresh()[0]  # type: ignore[arg-type]

    assert game.cover_asset == str(cover.resolve())
    assert game.portrait_artwork_path == cover.resolve()
    assert game.header_artwork_path == header.resolve()
    assert game.fallback_artwork_path is None


def test_artwork_paths_are_presented_as_independent_local_urls(tmp_path: Path) -> None:
    portrait = tmp_path / "portrait cover.jpg"
    header = tmp_path / "header.png"
    fallback = tmp_path / "fallback.webp"
    game = Game(
        id="steam-123",
        name="Artwork Game",
        launcher=Launcher.STEAM,
        install_path=tmp_path / "game",
        logical_size_gb=1.0,
        physical_size_gb=1.0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        portrait_artwork_path=portrait,
        header_artwork_path=header,
        fallback_artwork_path=fallback,
    )

    presented = game_to_qml(game)

    assert presented["portraitArtwork"] == portrait.as_uri()
    assert presented["headerArtwork"] == header.as_uri()
    assert presented["fallbackArtwork"] == fallback.as_uri()
    assert presented["cover"] == portrait.as_uri()
    assert presented["effectiveArtworkUrl"] == QUrl.fromLocalFile(
        str(portrait)
    ).toString(QUrl.ComponentFormattingOption.FullyEncoded)
    assert presented["effectiveArtworkUrl"].startswith("file:///")
    assert presented["portraitArtworkPath"] == str(portrait)

    header_only = game_to_qml(replace(game, portrait_artwork_path=None))
    assert header_only["effectiveArtworkUrl"] == header.as_uri()

    fallback_only = game_to_qml(
        replace(
            game,
            portrait_artwork_path=None,
            header_artwork_path=None,
        )
    )
    assert fallback_only["effectiveArtworkUrl"] == fallback.as_uri()


def test_game_presenter_exposes_stable_action_and_analysis_booleans(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game", "steam-123", "Stable State", tool=False)
    empty = game_to_qml(game)
    boolean_fields = (
        "libraryAvailable",
        "launchAllowed",
        "analysisAllowed",
        "analysisReportAvailable",
        "analysisPathAvailable",
        "analysisIsBtrfs",
        "analysisScanComplete",
        "analysisProfilesUnlocked",
        "analysisGameRunning",
        "analysisHasWarnings",
    )
    assert all(type(empty[field]) is bool for field in boolean_fields)
    assert empty["analysisReport"] == {}
    assert empty["analysisReportAvailable"] is False

    disconnected = game_to_qml(replace(game, library_available=False))
    assert disconnected["status"] == "Drive disconnected"
    assert disconnected["libraryAvailable"] is False
    assert disconnected["launchAllowed"] is False
    assert disconnected["analysisAllowed"] is False
    assert disconnected["compressionAvailable"] is False

    report = {
        "game_id": game.id,
        "created_at": "2026-07-22T10:00:00+00:00",
        "path_exists": True,
        "path_is_directory": True,
        "is_btrfs": True,
        "scan_complete": True,
        "profiles_unlocked": True,
        "game_running": False,
        "warnings": [],
    }
    analyzed = game_to_qml(game, analysis_report=report)
    assert all(type(analyzed[field]) is bool for field in boolean_fields)
    assert analyzed["analysisReportAvailable"] is True
    assert analyzed["analysisPathAvailable"] is True
    assert analyzed["analysisIsBtrfs"] is True
    assert analyzed["analysisScanComplete"] is True
    assert analyzed["analysisProfilesUnlocked"] is True


def test_game_presenter_never_turns_failed_savings_measurement_into_zero(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game", "steam-123", "Measured Game", tool=False)

    unavailable = game_to_qml(
        game,
        compression_result={
            "game_id": game.id,
            "status": "completed_with_warning",
            "actual_saved_bytes": None,
        },
    )
    measured = game_to_qml(
        game,
        compression_result={
            "game_id": game.id,
            "status": "completed",
            "actual_saved_bytes": 800_000_000,
            "measurement_authoritative": True,
        },
    )

    assert unavailable["savedBytes"] is None
    assert unavailable["savingsMeasured"] is False
    assert unavailable["savedSpace"] == "Measurement unavailable"
    assert measured["savedBytes"] == 800_000_000
    assert measured["savingsMeasured"] is True
    assert measured["savedSpace"] == "0.8 GB"


def test_verification_presenter_keeps_known_logical_size_and_updates_header(
    tmp_path: Path,
) -> None:
    game = replace(
        _game(tmp_path / "game", "steam-123", "Measured Game", tool=False),
        filesystem=FilesystemType.BTRFS,
        filesystem_name="btrfs",
        compression_available=True,
    )
    mib = 1024 * 1024

    presented = game_to_qml(
        game,
        verification_result={
            "task_id": "verification-current",
            "status": "completed",
            "error": "",
            "result": {
                # Older helpers did not include logical_bytes. The presenter
                # must retain the already-known game size instead of inventing 0 B.
                "compsize_disk_bytes": 312 * mib,
                "compsize_uncompressed_bytes": 315 * mib,
                "compsize_referenced_bytes": 315 * mib,
                "measurement_source": "polkit_helper",
            },
        },
    )

    assert presented["sizeBytes"] == 1_000_000_000
    assert presented["logicalSize"] == "1.0 GB"
    assert presented["physicalSizeBytes"] == 312 * mib
    assert presented["physicalSize"] == "312 MiB"
    assert presented["savedBytes"] == 3 * mib
    assert presented["savedSpace"] == "3.00 MiB"
    assert presented["savingsMeasured"] is True


def test_verification_presenter_rejects_zero_required_measurement(
    tmp_path: Path,
) -> None:
    game = replace(
        _game(tmp_path / "game", "steam-123", "Measured Game", tool=False),
        filesystem=FilesystemType.BTRFS,
        filesystem_name="btrfs",
        compression_available=True,
    )

    presented = game_to_qml(
        game,
        verification_result={
            "task_id": "verification-incomplete",
            "status": "completed",
            "result": {
                "compsize_disk_bytes": 0,
                "compsize_uncompressed_bytes": 1_000_000_000,
                "compsize_referenced_bytes": 1_000_000_000,
                "measurement_source": "polkit_helper",
            },
        },
    )

    assert presented["physicalSizeBytes"] is None
    assert presented["physicalSize"] == "Measurement unavailable"
    assert presented["savedBytes"] is None
    assert presented["savingsMeasured"] is False


def test_library_cache_round_trips_shape_specific_artwork(tmp_path: Path) -> None:
    game = _game(tmp_path / "game", "steam-123", "Artwork Game", tool=False)
    game = replace(
        game,
        portrait_artwork_path=tmp_path / "portrait.jpg",
        header_artwork_path=tmp_path / "header.jpg",
        fallback_artwork_path=tmp_path / "fallback.png",
    )
    cache = LibraryCache(tmp_path / "library.json")

    cache.save((game,))
    restored = cache.load()[0]

    assert restored.portrait_artwork_path == tmp_path / "portrait.jpg"
    assert restored.header_artwork_path == tmp_path / "header.jpg"
    assert restored.fallback_artwork_path == tmp_path / "fallback.png"


def test_qml_artwork_components_clip_and_keep_fallbacks() -> None:
    qml_root = Path(__file__).parents[1] / "src" / "game_optimization_linux" / "qml"
    cover_qml = (qml_root / "components" / "GameArtwork.qml").read_text(encoding="utf-8")
    grid_qml = (qml_root / "components" / "GameGridCard.qml").read_text(encoding="utf-8")
    list_qml = (qml_root / "components" / "GameListRow.qml").read_text(encoding="utf-8")
    sidebar_qml = (qml_root / "components" / "Sidebar.qml").read_text(encoding="utf-8")

    assert "clip: true" in cover_qml
    assert "id: artworkLayer" in cover_qml
    assert "visible: true" in cover_qml
    assert "id: currentImage" in cover_qml
    assert "id: pendingImage" in cover_qml
    assert "Image.PreserveAspectFit" in cover_qml
    assert "Image.Loading" in cover_qml
    assert "Image.Error" in cover_qml
    assert "artworkGeneration" in cover_qml
    assert "pendingGameId" in cover_qml
    assert "committedArtworkSource" in cover_qml
    assert "artworkLoader.active = false" not in cover_qml
    assert '"effectiveArtworkUrl"' in grid_qml
    assert '"effectiveArtworkUrl"' in list_qml
    assert "portraitCoverHeight" in grid_qml
    assert "Image.PreserveAspectCrop" in grid_qml
    assert grid_qml.index('"portraitArtwork"') < grid_qml.index('"headerArtwork"')
    assert list_qml.index('"headerArtwork"') < list_qml.index('"portraitArtwork"')
    assert '"cover"' in grid_qml and '"cover"' in list_qml
    assert "collapsed ? 42 : 54" in sidebar_qml
    assert "sourceClipRect" not in sidebar_qml
    assert 'text: "GF"' in sidebar_qml
    assert "Image.PreserveAspectFit" in sidebar_qml


def test_classifier_hides_known_runtimes_but_keeps_ambiguous_games() -> None:
    assert is_steam_tool_name("Proton Experimental")
    assert is_steam_tool_name("Proton 9.0")
    assert is_steam_tool_name("Steam Linux Runtime 3.0 (sniper)")
    assert is_steam_tool_name("Steamworks Common Redistributables")
    assert is_steam_tool_name("Example Dedicated Server")
    assert not is_steam_tool_name("Proton Bus Simulator")
    assert not is_steam_tool_name("Server Room")


def test_show_steam_tools_setting_defaults_off_and_can_be_enabled(tmp_path: Path) -> None:
    user_game = _game(tmp_path / "user", "steam-10", "User Game", tool=False)
    runtime = _game(tmp_path / "runtime", "steam-20", "Proton 9.0", tool=True)
    controller = AppController(
        game_provider=_Provider((user_game, runtime)),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=_System(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.settings["showSteamToolsAndRuntimes"] is False
        assert [game["name"] for game in controller.games] == ["User Game"]

        assert controller.saveSetting("showSteamToolsAndRuntimes", True)

        assert {game["name"] for game in controller.games} == {"User Game", "Proton 9.0"}
    finally:
        controller.shutdown()

    restored = AppController(
        game_provider=_Provider((user_game, runtime)),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=_System(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert restored.settings["showSteamToolsAndRuntimes"] is True
        assert {game["name"] for game in restored.games} == {"User Game", "Proton 9.0"}
    finally:
        restored.shutdown()


def test_normal_mode_has_no_demo_services_or_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_controller_module,
        "TASK_HISTORY_FILE",
        tmp_path / "task-history.json",
    )
    controller = AppController(
        game_provider=_Provider(()),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=_System(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.demoMode is False
        assert controller.games == []
        assert controller.tasks == []
        assert controller.backups == []
        assert controller.systemInfo["demo"] is False
        payload = str(
            {
                "games": controller.games,
                "tasks": controller.tasks,
                "backups": controller.backups,
                "system": controller.systemInfo,
            }
        )
        assert "7800X3D" not in payload
        assert "/demo/" not in payload
    finally:
        controller.shutdown()


def test_demo_environment_keeps_explicit_demo_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GAME_OPTIMIZATION_DEMO", "1")
    controller = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        auto_refresh=False,
    )
    try:
        assert controller.demoMode is True
        assert len(controller.games) == 4
        assert len(controller.backups) == 4
        assert controller.systemInfo["demo"] is True
        assert "7800X3D" in controller.systemInfo["cpu"]
    finally:
        controller.shutdown()
