from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage

from gameforge.controllers import AppController
from gameforge.models import (
    FilesystemType,
    Game,
    GameStatus,
    Launcher,
    Task,
    TaskStatus,
    TaskType,
)
from gameforge.services import (
    GameUpdateRecord,
    GameUpdateStateStore,
    GameUpdateStatus,
    GameUpdateTracker,
    ManifestObservation,
    MockTaskService,
    SettingsStore,
    TaskHistoryStore,
    UpdateDisplayStateStore,
    UpdateStateDatabase,
)


_QT_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _Provider:
    def __init__(
        self,
        games: tuple[Game, ...],
        configured_roots: tuple[Path, ...] = (),
    ) -> None:
        self._games = {game.id: game for game in games}
        self.configured_roots = configured_roots

    def list_games(self) -> tuple[Game, ...]:
        return tuple(self._games.values())

    def get_game(self, game_id: str) -> Game | None:
        return self._games.get(game_id)

    def refresh(self) -> tuple[Game, ...]:
        return self.list_games()


def _game(tmp_path: Path) -> Game:
    portrait = tmp_path / "library_600x900.jpg"
    header = tmp_path / "header.jpg"
    portrait.write_bytes(b"portrait")
    header.write_bytes(b"header")
    return Game(
        id="steam-4242",
        steam_app_id="4242",
        name="A very long game name retained from the local Steam cache",
        launcher=Launcher.STEAM,
        install_path=tmp_path / "SteamLibrary" / "steamapps" / "common" / "Game",
        library_path=tmp_path / "SteamLibrary",
        logical_size_gb=1.0,
        physical_size_gb=1.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        portrait_artwork_path=portrait,
        header_artwork_path=header,
    )


def _controller(
    tmp_path: Path,
    game: Game,
    *,
    task_service: object | None = None,
    tracker: GameUpdateTracker | None = None,
    display_store: UpdateDisplayStateStore | None = None,
    artwork_roots: tuple[Path, ...] = (),
) -> AppController:
    return AppController(
        game_provider=_Provider((game,), artwork_roots),
        task_service=task_service or MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        update_tracker=tracker,
        update_display_store=display_store,
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )


def test_task_rows_join_stable_game_id_to_shared_artwork_after_restore(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    task = Task(
        id="analysis-restored",
        game_id=game.id,
        game_name=game.name,
        task_type=TaskType.ANALYSIS,
        title=f"Analyze {game.name}",
    )
    history = TaskHistoryStore(tmp_path / "tasks.json")
    history.save((task,))

    class _RestoredTasks:
        def __init__(self) -> None:
            self.rows = history.load()

        def list_tasks(self) -> tuple[Task, ...]:
            return self.rows

        def tick(self, _step: float = 0.0) -> tuple[Task, ...]:
            return self.rows

        def shutdown(self, **_kwargs: object) -> None:
            return None

    tasks = _RestoredTasks()
    controller = _controller(tmp_path, game, task_service=tasks)
    try:
        row = controller.tasks[0]
        assert row["gameId"] == game.id
        assert row["steamAppId"] == "4242"
        assert row["portraitArtwork"] == game.portrait_artwork_path.as_uri()
        assert row["headerArtwork"] == game.header_artwork_path.as_uri()
    finally:
        controller.shutdown()


def test_loading_failed_task_history_emits_no_fresh_error_or_notification(
    tmp_path: Path,
    caplog: object,
) -> None:
    game = _game(tmp_path)
    now = datetime.now(UTC)
    failed = tuple(
        Task(
            id=f"historical-failure-{index}",
            game_id=game.id,
            game_name=game.name,
            task_type=TaskType.ANALYSIS,
            title=f"Historical failure {index}",
            status=TaskStatus.FAILED,
            error=message,
            created_at=now - timedelta(days=index + 2),
            updated_at=now - timedelta(days=index + 1),
        )
        for index, message in enumerate(
            (
                "Bad file descriptor",
                "Not authorized",
                "directory changed during privileged baseline measurement",
                "No files",
                "SEARCH_V2: Operation not permitted",
            )
        )
    )
    history = TaskHistoryStore(tmp_path / "tasks.json")
    history.save(failed)

    class _HistoricalTasks:
        def __init__(self) -> None:
            self.rows = history.load()

        def list_tasks(self) -> tuple[Task, ...]:
            return self.rows

        def tick(self, _step: float = 0.0) -> tuple[Task, ...]:
            return self.rows

        def shutdown(self, **_kwargs: object) -> None:
            return None

    caplog.set_level(logging.ERROR)
    task_service = _HistoricalTasks()
    controller = _controller(tmp_path, game, task_service=task_service)
    notifications: list[tuple[str, str]] = []
    finished: list[tuple[str, str]] = []
    controller.toastRequested.connect(
        lambda message, level: notifications.append((message, level))
    )
    controller.taskFinished.connect(
        lambda task_id, status: finished.append((task_id, status))
    )
    try:
        controller._poll_tasks()
        _QT_APPLICATION.processEvents()

        assert len(controller.taskHistory) == 5
        assert {row["error"] for row in controller.taskHistory} == {
            task.error for task in failed
        }
        assert notifications == []
        assert finished == []
        assert not any(
            task.error and task.error in record.getMessage()
            for task in failed
            for record in caplog.records
        )

        fresh_error = "fresh failure from current session"
        task_service.rows = (
            *task_service.rows,
            replace(
                failed[0],
                id="current-session-failure",
                error=fresh_error,
                updated_at=datetime.now(UTC),
            ),
        )
        caplog.clear()
        controller._poll_tasks()
        _QT_APPLICATION.processEvents()
        assert finished == [("current-session-failure", "failed")]
        assert notifications and notifications[-1][1] == "error"
        assert any(fresh_error in record.getMessage() for record in caplog.records)
    finally:
        controller.shutdown()


def test_updates_use_cached_game_name_and_same_artwork_as_games(tmp_path: Path) -> None:
    game = _game(tmp_path)
    state_path = tmp_path / "updates.json"
    GameUpdateStateStore(state_path).save(
        UpdateStateDatabase(
            records={
                game.id: GameUpdateRecord(
                    game_id=game.id,
                    app_id="4242",
                    status=GameUpdateStatus.ERROR,
                    last_error="fixture error",
                )
            },
            initial_inventory_complete=True,
        )
    )
    tracker = GameUpdateTracker(GameUpdateStateStore(state_path))
    controller = _controller(tmp_path, game, tracker=tracker)
    try:
        row = controller.updates[0]
        presented_game = controller.games[0]
        assert row["name"] == game.name
        assert row["portraitArtwork"] == presented_game["portraitArtwork"]
        assert row["headerArtwork"] == presented_game["headerArtwork"]
    finally:
        controller.shutdown()


def test_disconnected_update_resolves_real_artwork_from_independent_steam_cache(
    tmp_path: Path,
) -> None:
    steam_root = tmp_path / "Steam"
    portrait = steam_root / "appcache" / "librarycache" / "4242_library_600x900.png"
    portrait.parent.mkdir(parents=True)
    image = QImage(40, 60, QImage.Format_ARGB32)
    image.fill(QColor("#33cc99"))
    assert image.save(str(portrait), "PNG")
    (tmp_path / "cached").mkdir()
    game = replace(
        _game(tmp_path / "cached"),
        library_available=False,
        status=GameStatus.DRIVE_DISCONNECTED,
        portrait_artwork_path=None,
        header_artwork_path=None,
        fallback_artwork_path=None,
        cover_asset="",
    )
    state = GameUpdateStateStore(tmp_path / "disconnected-updates.json")
    state.save(
        UpdateStateDatabase(
            records={
                game.id: GameUpdateRecord(
                    game_id=game.id,
                    app_id="4242",
                    status=GameUpdateStatus.LIBRARY_UNAVAILABLE,
                )
            },
            initial_inventory_complete=True,
        )
    )
    controller = _controller(
        tmp_path,
        game,
        tracker=GameUpdateTracker(state),
        artwork_roots=(steam_root,),
    )
    try:
        row = controller.updates[0]
        assert row["portraitArtwork"] == portrait.resolve().as_uri()
        assert row["portraitArtwork"]
        assert controller.games[0]["portraitArtwork"] == row["portraitArtwork"]
        assert QImage(row["portraitArtwork"].removeprefix("file://")).isNull() is False
    finally:
        controller.shutdown()


def test_ignored_configured_library_stays_out_of_games_filters_and_updates_after_restart(
    tmp_path: Path,
) -> None:
    ignored_library = tmp_path / "Disconnected NTFS Library"
    game_root = tmp_path / "ignored-game"
    game_root.mkdir()
    disconnected = replace(
        _game(game_root),
        id="steam-242550",
        steam_app_id="242550",
        name="Rayman Legends",
        install_path=ignored_library / "steamapps" / "common" / "Rayman Legends",
        library_path=ignored_library,
        filesystem=FilesystemType.NTFS,
        filesystem_name="ntfs3",
        library_available=False,
        status=GameStatus.DRIVE_DISCONNECTED,
    )
    observation = ManifestObservation.from_game(disconnected)
    update_state_path = tmp_path / "ignored-library-updates.json"

    def save_source_record() -> None:
        GameUpdateStateStore(update_state_path).save(
            UpdateStateDatabase(
                records={
                    disconnected.id: GameUpdateRecord(
                        game_id=disconnected.id,
                        app_id="242550",
                        status=GameUpdateStatus.LIBRARY_UNAVAILABLE,
                        current_observation=observation,
                    )
                },
                initial_inventory_complete=True,
            )
        )

    save_source_record()
    first = _controller(
        tmp_path,
        disconnected,
        tracker=GameUpdateTracker(GameUpdateStateStore(update_state_path)),
    )
    try:
        assert first.games[0]["filesystem"] == "ntfs3"
        assert first.updates
        assert first.ignoreLibrary(str(ignored_library)) is True
        assert first.games == []
        assert first.compressionLibrarySummaries == []
        assert first.updates == []
    finally:
        first.shutdown()

    # Simulate Steam still reporting the same configured, disconnected source.
    save_source_record()
    restarted = _controller(
        tmp_path,
        disconnected,
        tracker=GameUpdateTracker(GameUpdateStateStore(update_state_path)),
    )
    try:
        assert restarted.refreshGames() is True
        for _ in range(100):
            _QT_APPLICATION.processEvents()
            if not restarted.isScanning:
                break
        assert restarted.games == []
        assert restarted.compressionLibrarySummaries == []
        assert restarted.updates == []
        assert all(row.get("filesystem") != "ntfs3" for row in restarted.games)
        ignored = restarted.settings["ignoredSteamLibraries"]
        assert ignored == [str(ignored_library.resolve())]

        assert restarted.restoreIgnoredLibrary(str(ignored_library)) is True
        assert restarted.settings["ignoredSteamLibraries"] == []
    finally:
        restarted.shutdown()


def test_dismissed_and_cleared_updates_stay_hidden_but_new_version_returns(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    update_state = GameUpdateStateStore(tmp_path / "updates.json")
    display_state = UpdateDisplayStateStore(tmp_path / "update-display.json")
    first_time = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    first = GameUpdateRecord(
        game_id=game.id,
        app_id="4242",
        status=GameUpdateStatus.ERROR,
        last_error="old error",
        updated_at=first_time,
    )
    update_state.save(
        UpdateStateDatabase(
            records={game.id: first},
            initial_inventory_complete=True,
        )
    )
    controller = _controller(
        tmp_path,
        game,
        tracker=GameUpdateTracker(update_state),
        display_store=display_state,
    )
    try:
        assert controller.clearFinishedUpdates() == 1
        assert controller.updates == []
    finally:
        controller.shutdown()

    restored = _controller(
        tmp_path,
        game,
        tracker=GameUpdateTracker(update_state),
        display_store=UpdateDisplayStateStore(display_state.path),
    )
    try:
        assert restored.updates == []
    finally:
        restored.shutdown()

    # Merely observing the same event later must not generate a fresh key.
    update_state.save(
        UpdateStateDatabase(
            records={game.id: replace(first, updated_at=datetime(2026, 8, 1, tzinfo=UTC))},
            initial_inventory_complete=True,
        )
    )
    observed_again = _controller(
        tmp_path,
        game,
        tracker=GameUpdateTracker(update_state),
        display_store=UpdateDisplayStateStore(display_state.path),
    )
    try:
        assert observed_again.updates == []
    finally:
        observed_again.shutdown()

    newer = GameUpdateRecord(
        game_id=game.id,
        app_id="4242",
        status=GameUpdateStatus.ERROR,
        last_error="new error",
        updated_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )
    update_state.save(
        UpdateStateDatabase(
            records={game.id: newer},
            initial_inventory_complete=True,
        )
    )
    latest = _controller(
        tmp_path,
        game,
        tracker=GameUpdateTracker(update_state),
        display_store=UpdateDisplayStateStore(display_state.path),
    )
    try:
        assert len(latest.updates) == 1
        assert latest.updates[0]["error"] == "new error"
    finally:
        latest.shutdown()


def test_active_update_cannot_be_dismissed(tmp_path: Path) -> None:
    game = _game(tmp_path)
    tasks = MockTaskService()
    tasks.enqueue(
        Task(
            id="active-analysis",
            game_id=game.id,
            game_name=game.name,
            task_type=TaskType.ANALYSIS,
            title="Analyze",
        )
    )
    update_state = GameUpdateStateStore(tmp_path / "updates.json")
    update_state.save(
        UpdateStateDatabase(
            records={
                game.id: GameUpdateRecord(
                    game_id=game.id,
                    app_id="4242",
                    status=GameUpdateStatus.ANALYSIS_REQUIRED,
                )
            },
            initial_inventory_complete=True,
        )
    )
    controller = _controller(
        tmp_path,
        game,
        task_service=tasks,
        tracker=GameUpdateTracker(update_state),
        display_store=UpdateDisplayStateStore(tmp_path / "display.json"),
    )
    try:
        row = controller.updates[0]
        assert row["canDismiss"] is False
        assert controller.dismissUpdate(row["rowId"]) is False
        assert len(controller.updates) == 1
    finally:
        controller.shutdown()


def test_single_and_unavailable_update_entries_can_be_cleared(tmp_path: Path) -> None:
    available_game = _game(tmp_path)
    update_state = GameUpdateStateStore(tmp_path / "updates.json")
    update_state.save(
        UpdateStateDatabase(
            records={
                available_game.id: GameUpdateRecord(
                    game_id=available_game.id,
                    app_id="4242",
                    status=GameUpdateStatus.ERROR,
                )
            },
            initial_inventory_complete=True,
        )
    )
    controller = _controller(
        tmp_path,
        available_game,
        tracker=GameUpdateTracker(update_state),
        display_store=UpdateDisplayStateStore(tmp_path / "display.json"),
    )
    try:
        row_id = controller.updates[0]["rowId"]
        assert controller.dismissUpdate(row_id) is True
        assert controller.updates == []
    finally:
        controller.shutdown()

    disconnected_game = replace(
        available_game,
        library_available=False,
        status=GameStatus.DRIVE_DISCONNECTED,
    )
    unavailable_state = GameUpdateStateStore(tmp_path / "unavailable.json")
    unavailable_state.save(
        UpdateStateDatabase(
            records={
                disconnected_game.id: GameUpdateRecord(
                    game_id=disconnected_game.id,
                    app_id="4242",
                    status=GameUpdateStatus.LIBRARY_UNAVAILABLE,
                )
            },
            initial_inventory_complete=True,
        )
    )
    unavailable = _controller(
        tmp_path,
        disconnected_game,
        tracker=GameUpdateTracker(unavailable_state),
        display_store=UpdateDisplayStateStore(tmp_path / "unavailable-display.json"),
    )
    try:
        assert unavailable.clearUnavailableUpdates() == 1
        assert unavailable.updates == []
    finally:
        unavailable.shutdown()

    # Reconstruct both stores and the controller. The same disconnected event
    # has the same stable identity and remains tombstoned after restart/rescan.
    restored = _controller(
        tmp_path,
        disconnected_game,
        tracker=GameUpdateTracker(GameUpdateStateStore(unavailable_state.path)),
        display_store=UpdateDisplayStateStore(
            tmp_path / "unavailable-display.json"
        ),
    )
    try:
        restored._reload_updates()
        assert restored.updates == []
        assert restored.clearHiddenUpdatesHistory() == 1
        assert len(restored.updates) == 1
    finally:
        restored.shutdown()


def test_very_old_informational_update_is_hidden_automatically(tmp_path: Path) -> None:
    game = _game(tmp_path)
    update_state = GameUpdateStateStore(tmp_path / "old-updates.json")
    update_state.save(
        UpdateStateDatabase(
            records={
                game.id: GameUpdateRecord(
                    game_id=game.id,
                    app_id="4242",
                    status=GameUpdateStatus.UP_TO_DATE,
                    updated_at=datetime.now(UTC) - timedelta(days=31),
                )
            },
            initial_inventory_complete=True,
        )
    )
    controller = _controller(
        tmp_path,
        game,
        tracker=GameUpdateTracker(update_state),
        display_store=UpdateDisplayStateStore(tmp_path / "old-display.json"),
    )
    try:
        assert controller.updates == []
        assert controller.updatesSummary["needsCheckCount"] == 0
    finally:
        controller.shutdown()
