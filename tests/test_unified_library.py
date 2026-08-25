from __future__ import annotations

import json
from pathlib import Path
from threading import Barrier, Event
import time

from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers.games_model import GamesListModel
from game_optimization_linux.controllers.app_controller import AppController
from game_optimization_linux.controllers.library_scanner import LibraryScanner
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.providers.local import ConfiguredGameProvider
from game_optimization_linux.services.library_cache import LibraryCache
from game_optimization_linux.providers import DemoSystemProvider
from game_optimization_linux.services import MockTaskService, SettingsStore


_APP = QCoreApplication.instance() or QCoreApplication([])


def _game(game_id: str, launcher: Launcher, path: Path, name: str = "Same") -> Game:
    path.mkdir(parents=True, exist_ok=True)
    return Game(
        id=game_id,
        name=name,
        launcher=launcher,
        install_path=path,
        logical_size_gb=0,
        physical_size_gb=0,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        launcher_game_id=game_id.rsplit("-", 1)[-1],
    )


class _Provider:
    def __init__(self, games: tuple[Game, ...], error: Exception | None = None) -> None:
        self.games = games
        self.error = error

    def refresh(self, *, cancel_event: Event | None = None) -> tuple[Game, ...]:
        if self.error:
            raise self.error
        return self.games

    def list_games(self) -> tuple[Game, ...]:
        return self.games

    def get_game(self, game_id: str) -> Game | None:
        return next((game for game in self.games if game.id == game_id), None)

    def add_game(self, game: Game) -> Game:
        raise ValueError


def test_unified_provider_keeps_same_title_games_and_isolates_failure(tmp_path: Path) -> None:
    steam_game = _game("steam-1", Launcher.STEAM, tmp_path / "steam")
    heroic_game = _game("heroic-gog-1", Launcher.HEROIC, tmp_path / "heroic")
    lutris_game = _game("lutris-1", Launcher.LUTRIS, tmp_path / "lutris")
    aggregate = ConfiguredGameProvider(
        _Provider((steam_game,)),  # type: ignore[arg-type]
        _Provider(()),  # type: ignore[arg-type]
        heroic=_Provider((heroic_game,)),
        lutris=_Provider((lutris_game,)),
    )
    assert {game.id for game in aggregate.refresh()} == {
        "steam-1", "heroic-gog-1", "lutris-1"
    }

    aggregate.heroic = _Provider((), RuntimeError("broken Heroic metadata"))
    refreshed = aggregate.refresh()
    assert {game.id for game in refreshed} == {"steam-1", "lutris-1"}
    assert aggregate.provider_errors["Heroic"] == "broken Heroic metadata"


def test_unified_provider_starts_launcher_scans_concurrently(tmp_path: Path) -> None:
    barrier = Barrier(4, timeout=2.0)

    class Coordinated(_Provider):
        def refresh(self, *, cancel_event: Event | None = None) -> tuple[Game, ...]:
            barrier.wait()
            return self.games

    aggregate = ConfiguredGameProvider(
        Coordinated((_game("steam-1", Launcher.STEAM, tmp_path / "steam"),)),  # type: ignore[arg-type]
        Coordinated((_game("local-1", Launcher.MANUAL, tmp_path / "local"),)),  # type: ignore[arg-type]
        heroic=Coordinated(
            (_game("heroic-1", Launcher.HEROIC, tmp_path / "heroic"),)
        ),
        lutris=Coordinated(
            (_game("lutris-1", Launcher.LUTRIS, tmp_path / "lutris"),)
        ),
    )

    assert len(aggregate.refresh()) == 4


def test_failed_provider_does_not_erase_cached_games_or_other_sources(tmp_path: Path) -> None:
    cached_heroic = _game("heroic-gog-1", Launcher.HEROIC, tmp_path / "heroic")
    lutris_game = _game("lutris-1", Launcher.LUTRIS, tmp_path / "lutris")
    aggregate = ConfiguredGameProvider(
        _Provider(()),  # type: ignore[arg-type]
        _Provider(()),  # type: ignore[arg-type]
        heroic=_Provider((), RuntimeError("Heroic JSON denied")),
        lutris=_Provider((lutris_game,)),
    )
    controller = AppController(
        game_provider=aggregate,
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=DemoSystemProvider(),
        initial_games=(cached_heroic,),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.refreshGames()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and controller.isScanning:
            _APP.processEvents()
            time.sleep(0.002)
        rows = {row["id"]: row for row in controller.games}
        assert set(rows) == {"heroic-gog-1", "lutris-1"}
        assert rows["heroic-gog-1"]["libraryAvailable"] is False
        assert controller.libraryProviderDiagnostics
    finally:
        controller.shutdown()


def test_launcher_filter_is_in_memory_and_exact() -> None:
    model = GamesListModel()
    rows = [
        {"id": "steam-1", "name": "One", "launcher": "Steam"},
        {"id": "heroic-1", "name": "Two", "launcher": "Heroic"},
        {"id": "lutris-1", "name": "Three", "launcher": "Lutris"},
        {"id": "local-1", "name": "Four", "launcher": "Manual"},
    ]
    model.apply_snapshot(rows, reason="test")
    model.setFilters("", "Heroic", "", 0)
    assert model.count == 1
    assert model.data(model.index(0, 0))["id"] == "heroic-1"
    model.setFilters("", "Lutris", "", 0)
    assert model.count == 1
    assert model.data(model.index(0, 0))["id"] == "lutris-1"
    model.setFilters("", "", "", 0)
    assert model.count == 4


def test_games_page_exposes_all_launcher_filters_without_rescanning() -> None:
    qml = (
        Path(__file__).parents[1]
        / "src"
        / "game_optimization_linux"
        / "qml"
        / "pages"
        / "GamesPage.qml"
    ).read_text(encoding="utf-8")

    assert 'qsTr("All"), "Steam", "Heroic", "Lutris", qsTr("Manual/Custom")' in qml
    assert 'return "Manual"' in qml
    assert "incrementalGamesModel.setFilters(" in qml
    filter_function = qml[
        qml.index("function syncIncrementalFilter()") : qml.index("WheelHandler {")
    ]
    assert "refreshGames" not in filter_function


def test_flatpak_manifest_grants_only_launcher_metadata_roots() -> None:
    manifest = (
        Path(__file__).parents[1]
        / "flatpak"
        / "io.github.DevVoidPL.GameOptimizationLinux.yml"
    ).read_text(encoding="utf-8")
    for permission in (
        "--filesystem=~/.var/app/com.heroicgameslauncher.hgl/config/heroic:ro",
        "--filesystem=~/.var/app/com.heroicgameslauncher.hgl/config/legendary:ro",
        "--filesystem=~/.var/app/net.lutris.Lutris/config/lutris:ro",
        "--filesystem=~/.var/app/net.lutris.Lutris/data/lutris:ro",
        "--filesystem=~/.var/app/net.lutris.Lutris/cache/lutris:ro",
    ):
        assert permission in manifest
    assert "--filesystem=host\n" not in manifest


def test_existing_version_two_steam_cache_loads_with_new_defaults(tmp_path: Path) -> None:
    game = _game("steam-1", Launcher.STEAM, tmp_path / "steam")
    raw = game.to_dict()
    for key in (
        "launcher_game_id", "store", "launch_uri", "launcher_variant", "runner",
        "wine_prefix", "working_directory", "launch_available",
        "launch_unavailable_reason",
    ):
        raw.pop(key, None)
    path = tmp_path / "library.json"
    path.write_text(json.dumps({"version": 2, "games": [raw]}), encoding="utf-8")
    restored = LibraryCache(path).load()[0]
    assert restored.launcher is Launcher.STEAM
    assert restored.launcher_game_id is None
    assert restored.store == "unknown"
    assert restored.launch_available is True


def test_library_scanner_rejects_stale_generation_result(tmp_path: Path) -> None:
    release = Event()
    stale_game = _game("heroic-1", Launcher.HEROIC, tmp_path / "stale")
    current_game = _game("lutris-1", Launcher.LUTRIS, tmp_path / "current")

    class Slow(_Provider):
        def refresh(self, *, cancel_event: Event | None = None) -> tuple[Game, ...]:
            release.wait(1.0)
            return self.games

    scanner = LibraryScanner(max_threads=2)
    published: list[tuple[int, tuple[str, ...]]] = []
    scanner.libraryReady.connect(
        lambda generation, games: published.append(
            (generation, tuple(game.id for game in games))
        )
    )
    try:
        scanner.start(Slow((stale_game,)))
        current_generation = scanner.start(_Provider((current_game,)))
        release.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not published:
            _APP.processEvents()
            time.sleep(0.002)
        assert published == [(current_generation, ("lutris-1",))]
    finally:
        scanner.shutdown()
