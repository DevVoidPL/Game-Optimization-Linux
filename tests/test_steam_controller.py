from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, get_ident
import time
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers.app_controller import AppController
from game_optimization_linux.models import (
    FilesystemType,
    FilesystemInfo,
    Game,
    GameStatus,
    Launcher,
    SizeScanStatus,
)
from game_optimization_linux.providers import DemoSystemProvider, SteamGameProvider
from game_optimization_linux.providers.linux_filesystem import LinuxFilesystemProvider
from game_optimization_linux.providers.local import ConfiguredGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.directory_size import DirectorySizeResult, DirectorySizeScanner
from game_optimization_linux.services.library_cache import LibraryCache


_QT_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


def test_normal_startup_constructs_configured_library_provider(tmp_path: Path) -> None:
    controller = AppController(
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=DemoSystemProvider(),
        library_cache=LibraryCache(tmp_path / "library.json"),
        initial_games=(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert isinstance(controller._game_provider, ConfiguredGameProvider)
        assert isinstance(controller._filesystem_provider, LinuxFilesystemProvider)
        assert isinstance(controller._directory_size_scanner, DirectorySizeScanner)
        assert controller.demoMode is False
    finally:
        controller.shutdown()


def _wait_until(predicate: object, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _QT_APPLICATION.processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.002)
    _QT_APPLICATION.processEvents()
    assert callable(predicate) and predicate(), "Qt worker result timed out"


def _game(path: Path, game_id: str = "steam-10") -> Game:
    return Game(
        id=game_id,
        steam_app_id="10",
        name="Read-only Test Game",
        launcher=Launcher.STEAM,
        install_path=path,
        library_path=path.parent,
        logical_size_gb=0.5,
        physical_size_gb=0.5,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
        status=GameStatus.READY,
        data_source="Steam",
    )


class _FakeSteamProvider:
    def __init__(
        self,
        games: tuple[Game, ...],
        *,
        release_refresh: Event | None = None,
        error: Exception | None = None,
        steam_found: bool = True,
    ) -> None:
        self._games = {game.id: game for game in games}
        self.release_refresh = release_refresh
        self.error = error
        self.refresh_started = Event()
        self.refresh_thread: int | None = None
        self.refresh_calls = 0
        self.last_report = SimpleNamespace(steam_found=steam_found)

    def list_games(self) -> tuple[Game, ...]:
        return tuple(self._games.values())

    def get_game(self, game_id: str) -> Game | None:
        return self._games.get(game_id)

    def add_game(self, game: Game) -> Game:
        self._games[game.id] = game
        return game

    def refresh(self) -> tuple[Game, ...]:
        self.refresh_calls += 1
        self.refresh_thread = get_ident()
        self.refresh_started.set()
        if self.release_refresh is not None:
            self.release_refresh.wait(1.5)
        if self.error is not None:
            raise self.error
        return self.list_games()

    def set_additional_roots(self, roots: tuple[Path, ...]) -> None:
        self.additional_roots = roots

    def update_game_sizes(
        self,
        game_id: str,
        logical_size_gb: float,
        physical_size_gb: float,
        *,
        error: str | None = None,
    ) -> Game | None:
        game = self._games.get(game_id)
        if game is None:
            return None
        updated = replace(
            game,
            logical_size_gb=logical_size_gb,
            physical_size_gb=physical_size_gb,
            size_scan_status=(
                SizeScanStatus.FAILED if error else SizeScanStatus.COMPLETED
            ),
            size_scan_error=error,
        )
        self._games[game_id] = updated
        return updated


class _FakeDirectoryScanner:
    def __init__(self, result: DirectorySizeResult) -> None:
        self.result = result
        self.scan_thread: int | None = None

    def scan(
        self,
        path: Path,
        cancel_event: Event | None = None,
    ) -> DirectorySizeResult:
        self.scan_thread = get_ident()
        return self.result


def _controller(
    tmp_path: Path,
    provider: _FakeSteamProvider,
    *,
    scanner: object | None = None,
    initial_games: tuple[Game, ...] | None = None,
) -> AppController:
    return AppController(
        game_provider=provider,
        directory_size_scanner=scanner,
        library_cache=LibraryCache(tmp_path / "library.json"),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        task_service=MockTaskService(),
        initial_games=initial_games,
        demo_mode=False,
        auto_refresh=False,
    )


def test_refresh_is_non_blocking_and_applies_worker_results_on_qt_thread(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "Steam Library" / "steamapps" / "common" / "Game")
    release = Event()
    provider = _FakeSteamProvider((game,), release_refresh=release)
    scanner = _FakeDirectoryScanner(
        DirectorySizeResult(
            logical_bytes=2 * 1024**3,
            physical_bytes=1024**3,
            errors=(),
            complete=True,
        )
    )
    controller = _controller(tmp_path, provider, scanner=scanner)
    callback_threads: list[int] = []
    model_refreshes: list[tuple[int, str, int]] = []
    controller.gamesChanged.connect(lambda: callback_threads.append(get_ident()))
    controller.gamesModelRefreshed.connect(
        lambda generation, reason, count: model_refreshes.append(
            (generation, reason, count)
        )
    )
    main_thread = get_ident()
    try:
        started_at = time.monotonic()
        assert controller.refreshGames() is True
        assert time.monotonic() - started_at < 0.2
        assert controller.isScanning is True
        assert provider.refresh_started.wait(0.5)
        assert provider.refresh_thread != main_thread

        release.set()
        _wait_until(lambda: controller.libraryScanStatus == "ready")

        assert controller.games[0]["logicalSizeGb"] == 2.0
        assert controller.games[0]["physicalSizeGb"] == 1.0
        assert controller.games[0]["sizeScanStatus"] == "completed"
        assert scanner.scan_thread != main_thread
        assert callback_threads and set(callback_threads) == {main_thread}
        assert len(callback_threads) == 1
        assert [event[1] for event in model_refreshes] == [
            "library_scan_finished",
        ]
        assert all(event[2] == 1 for event in model_refreshes)
        assert LibraryCache(tmp_path / "library.json").load()[0].id == game.id
        assert {task["operation"] for task in controller.tasks} == {
            "Library scan",
            "Size calculation",
        }
        assert all(task["status"] == "completed" for task in controller.tasks)
        assert all(task["cancellable"] is False for task in controller.tasks)
    finally:
        release.set()
        controller.shutdown()


def test_failed_refresh_keeps_initial_snapshot_and_reports_error(tmp_path: Path) -> None:
    cached = _game(tmp_path / "cached")
    provider = _FakeSteamProvider((), error=RuntimeError("fixture scan failed"))
    controller = _controller(tmp_path, provider, initial_games=(cached,))
    try:
        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        assert controller.libraryScanStatus == "error"
        assert "fixture scan failed" in controller.libraryScanMessage
        assert [game["id"] for game in controller.games] == [cached.id]
    finally:
        controller.shutdown()


def test_cached_games_are_visible_before_background_refresh(tmp_path: Path) -> None:
    cached = _game(tmp_path / "cached", "steam-cached")
    current = _game(tmp_path / "current", "steam-current")
    LibraryCache(tmp_path / "library.json").save((cached,))
    provider = _FakeSteamProvider((current,))
    controller = _controller(tmp_path, provider)
    try:
        assert [game["id"] for game in controller.games] == [cached.id]
        assert controller.libraryScanStatus == "cached"

        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        assert [game["id"] for game in controller.games] == [current.id]
        assert LibraryCache(tmp_path / "library.json").load()[0].id == current.id
    finally:
        controller.shutdown()


def test_disconnected_library_games_remain_cached_and_actions_are_blocked(
    tmp_path: Path,
) -> None:
    external_library = tmp_path / "external-drive" / "SteamLibrary"
    cached = replace(
        _game(
            external_library / "steamapps" / "common" / "Cached Game",
            "steam-cached",
        ),
        library_path=external_library,
    )
    current_path = tmp_path / "local" / "steamapps" / "common" / "Current Game"
    current_path.mkdir(parents=True)
    current = _game(current_path, "steam-current")
    provider = _FakeSteamProvider((current,))
    provider.last_report.inaccessible_paths = (external_library,)
    controller = _controller(tmp_path, provider, initial_games=(cached,))
    toasts: list[tuple[str, str]] = []
    controller.toastRequested.connect(
        lambda message, tone: toasts.append((message, tone))
    )
    try:
        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        games = {game["id"]: game for game in controller.games}
        assert set(games) == {cached.id, current.id}
        disconnected = games[cached.id]
        assert disconnected["status"] == "Drive disconnected"
        assert disconnected["availabilityStatus"] == "Library unavailable"
        assert disconnected["libraryAvailable"] is False
        assert disconnected["launchAllowed"] is False
        assert disconnected["analysisAllowed"] is False
        assert disconnected["path"] == str(cached.install_path)
        assert controller.analyzeGame(cached.id) is False
        assert controller.launchGame(cached.id) is False
        assert any("Library unavailable" in message for message, _ in toasts)

        cached_snapshot = {
            game.id: game for game in LibraryCache(tmp_path / "library.json").load()
        }
        assert cached_snapshot[cached.id].library_available is False
        assert cached_snapshot[cached.id].status is GameStatus.DRIVE_DISCONNECTED

        cached.install_path.mkdir(parents=True)
        provider._games[cached.id] = replace(
            cached,
            status=GameStatus.READY,
            library_available=True,
        )
        provider.last_report.inaccessible_paths = ()
        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        reconnected = {
            game["id"]: game for game in controller.games
        }[cached.id]
        assert reconnected["status"] == "Ready"
        assert reconnected["libraryAvailable"] is True
        assert reconnected["launchAllowed"] is True
        assert reconnected["analysisAllowed"] is True
    finally:
        controller.shutdown()


def test_unconfigured_missing_library_is_removed_from_cache_and_filesystem_rows(
    tmp_path: Path,
    caplog: object,
) -> None:
    old_library = tmp_path / "old-ntfs" / "SteamLibrary"
    cached = replace(
        _game(old_library / "steamapps" / "common" / "Old Game", "steam-old"),
        library_path=old_library,
        filesystem=FilesystemType.NTFS,
        filesystem_name="ntfs3",
    )
    current_library = tmp_path / "new-btrfs" / "SteamLibrary"
    current_path = current_library / "steamapps" / "common" / "Current Game"
    current_path.mkdir(parents=True)
    current = replace(
        _game(current_path, "steam-current"),
        library_path=current_library,
        filesystem=FilesystemType.BTRFS,
        filesystem_name="btrfs",
    )
    provider = _FakeSteamProvider((current,))
    provider.last_report.inaccessible_paths = (old_library,)
    provider.last_report.configured_libraries = (current_library,)
    provider.last_report.libraries = (current_library,)
    controller = _controller(tmp_path, provider, initial_games=(cached,))
    try:
        caplog.set_level("INFO")
        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        assert [game["id"] for game in controller.games] == [current.id]
        assert {game["filesystem"] for game in controller.games} == {"btrfs"}
        assert [game.id for game in LibraryCache(tmp_path / "library.json").load()] == [
            current.id
        ]
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            f"path={old_library}" in message
            and "source=cache" in message
            and "filesystem=ntfs3" in message
            and "decision=removed" in message
            for message in messages
        )
        assert any(
            f"path={current_library}" in message
            and "source=libraryfolders.vdf" in message
            and "available=true" in message
            and "decision=active" in message
            for message in messages
        )
    finally:
        controller.shutdown()

    restarted = _controller(tmp_path, provider)
    try:
        assert [game["id"] for game in restarted.games] == [current.id]
        assert all(game["filesystem"] != "ntfs3" for game in restarted.games)
    finally:
        restarted.shutdown()


def test_refresh_keeps_valid_cached_artwork_when_fresh_scan_has_no_artwork(
    tmp_path: Path,
) -> None:
    library = tmp_path / "SteamLibrary"
    install_path = library / "steamapps" / "common" / "Stable Artwork"
    install_path.mkdir(parents=True)
    portrait = tmp_path / "4242_library_600x900.jpg"
    portrait.write_bytes(b"valid-local-artwork")
    cached = replace(
        _game(install_path, "steam-4242"),
        library_path=library,
        portrait_artwork_path=portrait,
        cover_asset=str(portrait),
    )
    discovered = replace(
        cached,
        portrait_artwork_path=None,
        header_artwork_path=None,
        fallback_artwork_path=None,
        cover_asset="",
    )
    provider = _FakeSteamProvider((discovered,))
    provider.last_report.configured_libraries = (library,)
    provider.last_report.libraries = (library,)
    provider.last_report.inaccessible_paths = ()
    controller = _controller(tmp_path, provider, initial_games=(cached,))
    try:
        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        row = controller.games[0]
        assert row["id"] == cached.id
        assert row["portraitArtwork"] == portrait.as_uri()
        restored = LibraryCache(tmp_path / "library.json").load()[0]
        assert restored.portrait_artwork_path == portrait
    finally:
        controller.shutdown()


def test_forget_library_only_removes_app_cache_after_config_is_gone(
    tmp_path: Path,
) -> None:
    old_library = tmp_path / "missing-library"
    cached = replace(
        _game(old_library / "steamapps" / "common" / "Old Game", "steam-old"),
        library_path=old_library,
    )
    provider = _FakeSteamProvider(())
    provider.last_report.configured_libraries = ()
    controller = _controller(tmp_path, provider, initial_games=(cached,))
    try:
        assert controller.forgetLibrary(str(old_library)) is True
        assert controller.games == []
        assert LibraryCache(tmp_path / "library.json").load() == []
    finally:
        controller.shutdown()

    configured_provider = _FakeSteamProvider(())
    configured_provider.last_report.configured_libraries = (old_library,)
    configured = _controller(tmp_path, configured_provider, initial_games=(cached,))
    try:
        assert configured.forgetLibrary(str(old_library)) is False
        assert [game["id"] for game in configured.games] == [cached.id]
    finally:
        configured.shutdown()


def test_empty_fake_provider_distinguishes_missing_steam(tmp_path: Path) -> None:
    provider = _FakeSteamProvider((), steam_found=False)
    controller = _controller(tmp_path, provider)
    try:
        assert controller.refreshGames() is True
        _wait_until(lambda: not controller.isScanning)

        assert controller.steamFound is False
        assert controller.libraryScanStatus == "steam-not-found"
        assert controller.games == []
    finally:
        controller.shutdown()


def test_custom_steam_locations_are_forwarded_then_refreshed(tmp_path: Path) -> None:
    provider = _FakeSteamProvider((), steam_found=True)
    controller = _controller(tmp_path, provider)
    custom_root = tmp_path / "Steam on another disk"
    try:
        assert controller.saveSetting(
            "steamInstallationDirectories",
            [str(custom_root)],
        ) is True
        _wait_until(lambda: not controller.isScanning)

        assert provider.additional_roots == (custom_root,)
        assert controller.settings["steamInstallationDirectories"] == [
            str(custom_root)
        ]
        assert controller.libraryScanStatus == "empty"
    finally:
        controller.shutdown()


def test_ten_identical_scans_do_not_advance_games_model_generation(
    tmp_path: Path,
) -> None:
    artwork = tmp_path / "library_600x900.png"
    artwork.write_bytes(b"stable artwork fixture")
    game = replace(
        _game(tmp_path / "SteamLibrary" / "steamapps" / "common" / "Game"),
        portrait_artwork_path=artwork,
    )
    provider = _FakeSteamProvider((game,))
    controller = _controller(tmp_path, provider, initial_games=(game,))
    model_events: list[tuple[int, str, int]] = []
    controller.gamesModelRefreshed.connect(
        lambda generation, reason, count: model_events.append(
            (generation, reason, count)
        )
    )
    try:
        initial_generation = controller._games_model_generation
        initial_url = controller.games[0]["effectiveArtworkUrl"]
        for _ in range(10):
            assert controller.refreshGames() is True
            _wait_until(lambda: not controller.isScanning)

        assert provider.refresh_calls == 10
        assert controller._games_model_generation == initial_generation
        assert controller.gamesModel.modelResetCount == 0
        assert model_events == []
        assert controller.games[0]["effectiveArtworkUrl"] == initial_url
    finally:
        controller.shutdown()


def test_one_game_change_emits_one_data_changed_without_model_reset(
    tmp_path: Path,
) -> None:
    first = _game(tmp_path / "library" / "first", "steam-first")
    second = _game(tmp_path / "library" / "second", "steam-second")
    provider = _FakeSteamProvider((first, second))
    controller = _controller(tmp_path, provider, initial_games=(first, second))
    changed_rows: list[tuple[int, int]] = []
    resets: list[bool] = []
    controller.gamesModel.dataChanged.connect(
        lambda top, bottom, _roles: changed_rows.append(
            (top.row(), bottom.row())
        )
    )
    controller.gamesModel.modelReset.connect(lambda: resets.append(True))
    try:
        controller._domain_games[first.id] = replace(
            first,
            status=GameStatus.DRIVE_DISCONNECTED,
            library_available=False,
        )
        controller._reload_games(reason="single_game_status")

        assert changed_rows == [(0, 0)]
        assert resets == []
        assert controller.gamesModel.modelResetCount == 0
        assert controller.games[0]["status"] == "Drive disconnected"
        assert controller.games[1]["status"] == "Ready"
    finally:
        controller.shutdown()


def test_adding_one_manifest_inserts_one_row_without_destroying_existing_rows(
    tmp_path: Path,
) -> None:
    first = _game(tmp_path / "library" / "first", "steam-first")
    second = _game(tmp_path / "library" / "second", "steam-second")
    provider = _FakeSteamProvider((first,))
    controller = _controller(tmp_path, provider, initial_games=(first,))
    inserted: list[tuple[int, int]] = []
    resets: list[bool] = []
    controller.gamesModel.rowsInserted.connect(
        lambda _parent, start, end: inserted.append((start, end))
    )
    controller.gamesModel.modelReset.connect(lambda: resets.append(True))
    try:
        provider.add_game(second)
        controller._set_domain_games(
            provider.list_games(),
            reason="appmanifest_added",
        )

        assert inserted == [(1, 1)]
        assert resets == []
        assert controller.gamesModel.modelResetCount == 0
        assert {game["id"] for game in controller.games} == {
            "steam-first",
            "steam-second",
        }
    finally:
        controller.shutdown()


def test_game_file_noise_is_grouped_and_does_not_request_library_scans(
    tmp_path: Path,
    caplog: object,
) -> None:
    game_path = tmp_path / "SteamLibrary" / "steamapps" / "common" / "Game"
    game = _game(game_path)
    provider = _FakeSteamProvider((game,))
    controller = _controller(tmp_path, provider, initial_games=(game,))
    caplog.set_level("INFO")  # type: ignore[attr-defined]
    try:
        initial_generation = controller._games_model_generation
        for index in range(500):
            assert controller.requestLibraryScan(
                "compression_activity",
                str(game_path / f"chunk-{index}.bin"),
                "file_changed",
            ) is False
        _wait_until(
            lambda: any(
                "source=game_file count=500" in record.getMessage()
                for record in caplog.records  # type: ignore[attr-defined]
            )
        )

        assert provider.refresh_calls == 0
        assert controller._games_model_generation == initial_generation
        assert controller.gamesModel.modelResetCount == 0
        grouped = [
            record.getMessage()
            for record in caplog.records  # type: ignore[attr-defined]
            if "source=game_file count=500" in record.getMessage()
        ]
        assert len(grouped) == 1
        assert "decision=ignored" in grouped[0]
    finally:
        controller.shutdown()


def test_manifest_event_burst_is_coalesced_into_one_scan(tmp_path: Path) -> None:
    game = _game(tmp_path / "SteamLibrary" / "steamapps" / "common" / "Game")
    provider = _FakeSteamProvider((game,))
    controller = _controller(tmp_path, provider, initial_games=(game,))
    manifest = tmp_path / "SteamLibrary" / "steamapps" / "appmanifest_10.acf"
    try:
        for _ in range(20):
            assert controller.requestLibraryScan(
                "steam_metadata_watcher",
                str(manifest),
                "file_changed",
            ) is True
        _wait_until(lambda: not controller.isScanning)

        assert provider.refresh_calls == 1
        assert controller.gamesModel.modelResetCount == 0
    finally:
        controller.shutdown()


def test_events_during_active_scan_schedule_at_most_one_retry(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "SteamLibrary" / "steamapps" / "common" / "Game")
    release = Event()
    provider = _FakeSteamProvider((game,), release_refresh=release)
    controller = _controller(tmp_path, provider, initial_games=(game,))
    manifest = tmp_path / "SteamLibrary" / "steamapps" / "appmanifest_10.acf"
    try:
        assert controller.refreshGames() is True
        assert provider.refresh_started.wait(0.5)
        for _ in range(30):
            assert controller.requestLibraryScan(
                "steam_metadata_watcher",
                str(manifest),
                "file_changed",
            ) is True

        release.set()
        _wait_until(
            lambda: provider.refresh_calls == 2 and not controller.isScanning,
        )

        assert provider.refresh_calls == 2
        assert controller.gamesModel.modelResetCount == 0
    finally:
        release.set()
        controller.shutdown()


def test_demo_environment_keeps_in_memory_provider(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setenv("GAME_OPTIMIZATION_DEMO", "1")
    controller = AppController(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        auto_refresh=False,
    )
    try:
        assert controller.demoMode is True
        assert controller.libraryScanStatus == "demo"
        assert len(controller.games) == 4
        assert controller.addManualGame() is True
        assert len(controller.games) == 5
    finally:
        controller.shutdown()


class _CancellableScanner:
    def __init__(self) -> None:
        self.started = Event()
        self.cancelled = Event()

    def scan(self, path: Path, cancel_event: Event | None = None) -> DirectorySizeResult:
        self.started.set()
        assert cancel_event is not None
        if cancel_event.wait(1.5):
            self.cancelled.set()
        return DirectorySizeResult(0, 0, (), complete=not cancel_event.is_set())


def test_shutdown_cancels_running_directory_scan(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    provider = _FakeSteamProvider((game,))
    scanner = _CancellableScanner()
    controller = _controller(tmp_path, provider, scanner=scanner)
    assert controller.refreshGames() is True
    _wait_until(scanner.started.is_set)

    started_at = time.monotonic()
    controller.shutdown()

    assert scanner.cancelled.wait(0.2)
    assert time.monotonic() - started_at < 1.0


class _CancellableRefreshProvider(_FakeSteamProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.cancelled = Event()

    def refresh(self, *, cancel_event: Event | None = None) -> tuple[Game, ...]:
        self.refresh_started.set()
        assert cancel_event is not None
        if cancel_event.wait(1.5):
            self.cancelled.set()
        return ()


def test_shutdown_cancels_running_provider_refresh(tmp_path: Path) -> None:
    provider = _CancellableRefreshProvider()
    controller = _controller(tmp_path, provider)
    assert controller.refreshGames() is True
    assert provider.refresh_started.wait(0.5)

    started_at = time.monotonic()
    controller.shutdown()

    assert provider.cancelled.wait(0.2)
    assert time.monotonic() - started_at < 1.0


class _StaticFilesystemProvider:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path.anchor and Path(path.anchor) or path,
            filesystem=FilesystemType.EXT4,
            compression_supported=False,
            filesystem_name="ext4",
            writable=True,
        )


def test_controller_integrates_real_provider_with_temporary_steam_library(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Steam Library"
    game_path = root / "steamapps" / "common" / "Temporary Game"
    game_path.mkdir(parents=True)
    payload = game_path / "payload.bin"
    payload.write_bytes(b"temporary fixture data")
    (root / "steamapps" / "appmanifest_4242.acf").write_text(
        '"AppState" {\n'
        '  "appid" "4242"\n'
        '  "name" "Temporary Game"\n'
        '  "installdir" "Temporary Game"\n'
        '  "SizeOnDisk" "1"\n'
        '  "StateFlags" "4"\n'
        '}\n',
        encoding="utf-8",
    )
    provider = SteamGameProvider(_StaticFilesystemProvider(), roots=[root])  # type: ignore[arg-type]
    controller = AppController(
        game_provider=provider,
        directory_size_scanner=DirectorySizeScanner(),
        library_cache=LibraryCache(tmp_path / "library.json"),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.refreshGames() is True
        _wait_until(lambda: controller.libraryScanStatus == "ready")

        assert len(controller.games) == 1
        presented = controller.games[0]
        assert presented["steamAppId"] == "4242"
        assert presented["dataSource"] == "Steam"
        assert presented["filesystem"] == "ext4"
        assert presented["sizeScanStatus"] == "completed"
        assert presented["logicalSizeGb"] == len(payload.read_bytes()) / 1024**3
    finally:
        controller.shutdown()
