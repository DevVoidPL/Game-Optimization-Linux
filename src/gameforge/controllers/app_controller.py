"""The single, deliberately thin QObject bridge consumed by QML."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_for_futures
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import shlex
from threading import Event
import time
from typing import Any, Protocol, cast
from uuid import uuid4

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    Property,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ..config import (
    ANALYSIS_CACHE_FILE,
    APP_ICON,
    APP_NAME,
    APP_VERSION,
    COMPRESSION_HISTORY_FILE,
    COMPRESSION_BENCHMARK_REPORTS_DIR,
    LIBRARY_CACHE_FILE,
    SETTINGS_FILE,
    TASK_HISTORY_FILE,
    UPDATE_DISPLAY_STATE_FILE,
    UPDATE_STATE_FILE,
)
from ..models import (
    AppSettings,
    AutomaticCompressionMode,
    Backup,
    BackupStatus,
    CompressionProfile,
    ControllerMode,
    FilesystemType,
    Game,
    GameOptimizationProfile,
    GameStatus,
    Launcher,
    MangoHudProfile,
    OptiScalerProfile,
    OptimizationOptions,
    OptimizationProfile,
    PostLaunchBehavior,
    SizeScanStatus,
    SystemInfo,
    Task,
    TaskStatus,
    TaskType,
    TextureCompatibility,
)
from ..providers import (
    BtrfsCompressionProvider,
    DemoGameProvider,
    DemoOptimizationProvider,
    DemoSystemProvider,
    LinuxSystemProvider,
    PreviewOptimizationProvider,
)
from ..services import (
    AnalysisCache,
    BtrfsAnalysisTaskService,
    BtrfsAnalysisReport,
    BtrfsCompressionAnalyzer,
    BenchmarkEstimateCatalog,
    CompressionHistoryStore,
    CompressionService,
    DemoBackupService,
    DemoTaskService,
    GamepadService,
    HostServiceClient,
    GameUpdateRecord,
    GameArtworkResolver,
    GameOptimizationProfileRepository,
    DisplayDetector,
    OptimizationAdvisor,
    OptimizationLaunchPlanner,
    OptiScalerCancelled,
    OptiScalerError,
    OptiScalerProfileRepository,
    OptiScalerService,
    RuntimeToolDetector,
    RunnerIntegration,
    GameUpdateStateStore,
    GameUpdateStatus,
    GameUpdateTracker,
    MangoHudConfigWriter,
    MangoHudDetector,
    MangoHudLaunchIntegration,
    MangoHudProfileRepository,
    METRIC_CONFIG_KEYS,
    SettingsStore,
    PrivilegedMeasurementClient,
    TaskHistoryStore,
    SteamLaunchError,
    SteamLauncher,
    UiSoundService,
    UnavailableBackupService,
    UpdateDisplayStateStore,
    aggregate_library_compression,
    classify_compression_effect,
    normalized_benchmark_projection,
    uses_flatpak_steam,
)
from ..services.library_cache import LibraryCache
from .couch_navigation import CouchNavigationController
from .games_model import GamesListModel
from .library_scanner import LibraryScanner
from .presenters import (
    backup_to_qml,
    game_to_qml,
    qml_value,
    settings_to_qml,
    system_info_to_qml,
    task_to_qml,
)


logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.INTERRUPTED.value,
}
_VALID_PAGES = {"games", "updates", "tasks", "system", "settings", "gameDetails"}
_MEASUREMENT_AUTH_TOAST = "Waiting for authorization to measure compression"


class GameProviderLike(Protocol):
    def list_games(self) -> Sequence[Game]: ...

    def get_game(self, game_id: str) -> Game | None: ...

    def add_game(self, game: Game) -> Game: ...

    def refresh(self) -> Sequence[Game] | None: ...


class FilesystemProviderLike(Protocol):
    def inspect(self, path: Path) -> Any: ...

    def list_filesystems(
        self,
        *,
        game_paths: Sequence[Path] = (),
        show_system_mounts: bool = False,
    ) -> Sequence[Any]: ...


class DirectorySizeScannerLike(Protocol):
    def scan(self, path: Path, *args: Any, **kwargs: Any) -> Any: ...


class LibraryCacheLike(Protocol):
    def load(self) -> Sequence[Game]: ...

    def save(self, games: list[Game] | tuple[Game, ...]) -> Any: ...


class TaskServiceLike(Protocol):
    def enqueue_analysis(self, game: Game) -> Task: ...

    def enqueue_verification(self, game: Game) -> Task: ...

    def enqueue_compression(
        self, game: Game, profile: CompressionProfile
    ) -> Task: ...

    def enqueue_compression_plan(
        self,
        game: Game,
        plan: Any,
        *,
        confirmed: bool,
        automatic_authorized: bool = False,
    ) -> Task: ...

    def list_tasks(self) -> Sequence[Task]: ...

    def tick(self, step: float = 10.0) -> Any: ...

    def pause(self, task_id: str) -> Task: ...

    def resume(self, task_id: str) -> Task: ...

    def cancel(self, task_id: str) -> Task: ...

    def clear_finished(self) -> int: ...

    def remove_finished(self, task_id: str) -> bool: ...


class BackupServiceLike(Protocol):
    def list_backups(self, game_id: str | None = None) -> Sequence[Backup]: ...

    def restore_backup(self, backup_id: str) -> Backup: ...

    def delete_backup(self, backup_id: str) -> bool | None: ...


class SettingsStoreLike(Protocol):
    def load(self) -> AppSettings: ...

    def save(self, settings: AppSettings) -> Any: ...


class SystemProviderLike(Protocol):
    """Loose protocol used while real system detection remains replaceable."""


class OptimizationProviderLike(Protocol):
    def defaults_for(self, profile: OptimizationProfile) -> OptimizationOptions: ...

    def preview_command(self, game: Game, options: OptimizationOptions) -> str: ...


class GameLauncherLike(Protocol):
    def launch(self, game: Game) -> Sequence[str]: ...


class AppController(QObject):
    """Expose provider data and safe simulated services to QML.

    Steam discovery and exact directory-size calculation are dispatched by a
    Qt thread-pool coordinator.  This QObject alone owns the QML-facing
    snapshot, so worker threads never mutate GUI state.
    """

    gamesChanged = Signal()
    gamesModelRefreshed = Signal(int, str, int)
    tasksChanged = Signal()
    updatesChanged = Signal()
    updatesSummaryChanged = Signal()
    compressionHistoryChanged = Signal()
    systemInfoChanged = Signal()
    showSystemMountsChanged = Signal()
    backupsChanged = Signal()
    selectedGameChanged = Signal()
    currentPageChanged = Signal()
    themeModeChanged = Signal()
    settingsChanged = Signal()
    updateStatusChanged = Signal()
    libraryScanStatusChanged = Signal()
    libraryScanMessageChanged = Signal()
    steamFoundChanged = Signal()
    isScanningChanged = Signal()
    gamepadAvailableChanged = Signal()
    controllersChanged = Signal()
    activeControllerChanged = Signal()
    interfaceModeChanged = Signal()
    mangoHudProfileChanged = Signal(str)
    optiScalerChanged = Signal(str)

    toastRequested = Signal(str, str)
    toastDismissRequested = Signal(str)
    taskFinished = Signal(str, str)
    gamepadAction = Signal(str)
    windowActionRequested = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        game_provider: GameProviderLike | None = None,
        task_service: TaskServiceLike | None = None,
        backup_service: BackupServiceLike | None = None,
        settings_store: SettingsStoreLike | None = None,
        system_provider: SystemProviderLike | None = None,
        optimization_provider: OptimizationProviderLike | None = None,
        game_launcher: GameLauncherLike | None = None,
        filesystem_provider: FilesystemProviderLike | None = None,
        directory_size_scanner: DirectorySizeScannerLike | None = None,
        library_cache: LibraryCacheLike | None = None,
        gamepad_service: GamepadService | None = None,
        compression_service: CompressionService | None = None,
        update_tracker: GameUpdateTracker | None = None,
        update_display_store: UpdateDisplayStateStore | None = None,
        benchmark_estimates: BenchmarkEstimateCatalog | None = None,
        mangohud_repository: MangoHudProfileRepository | None = None,
        mangohud_detector: MangoHudDetector | None = None,
        mangohud_launch_integration: MangoHudLaunchIntegration | None = None,
        optimization_profile_repository: GameOptimizationProfileRepository | None = None,
        runtime_tool_detector: RuntimeToolDetector | None = None,
        display_detector: DisplayDetector | None = None,
        optimization_advisor: OptimizationAdvisor | None = None,
        runner_integration: RunnerIntegration | None = None,
        optiscaler_service: OptiScalerService | None = None,
        initial_games: Sequence[Game] | None = None,
        demo_mode: bool | None = None,
        auto_refresh: bool = True,
        initial_interface_mode: str | None = None,
        reset_ui_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._deferred_toasts: list[tuple[str, str]] = []
        self._settings_store = settings_store or cast(
            SettingsStoreLike, SettingsStore(SETTINGS_FILE)
        )
        self._settings_model = self._load_settings()
        if reset_ui_mode and self._settings_model.controller_mode is not ControllerMode.AUTOMATIC:
            self._settings_model = replace(
                self._settings_model,
                controller_mode=ControllerMode.AUTOMATIC,
            )
            try:
                self._settings_store.save(self._settings_model)
            except Exception as error:
                logger.warning("Could not persist the UI mode reset: %s", error)
                self._deferred_toasts.append(
                    ("UI mode was reset for this session but could not be saved", "warning")
                )
        self._settings = settings_to_qml(self._settings_model)
        self._theme_mode = self._extract_theme_mode(self._settings)
        self._apply_runtime_log_level(str(self._settings.get("logLevel", "INFO")))

        explicit_provider = game_provider is not None
        if demo_mode is None:
            if explicit_provider:
                demo_mode = isinstance(game_provider, DemoGameProvider)
            else:
                demo_mode = os.environ.get("GAMEFORGE_DEMO", "").strip() == "1"
        self._demo_mode = bool(demo_mode)
        self._show_system_mounts = False

        self._filesystem_provider = filesystem_provider
        self._directory_size_scanner = directory_size_scanner
        if game_provider is None:
            if self._demo_mode:
                game_provider = cast(GameProviderLike, DemoGameProvider())
            else:
                game_provider = self._create_steam_provider()
        self._game_provider = game_provider
        self._artwork_resolver = GameArtworkResolver()
        self._effective_artwork_urls: dict[str, str] = {}
        self._games_model_generation = 0

        if library_cache is None and not explicit_provider and not self._demo_mode:
            library_cache = LibraryCache(LIBRARY_CACHE_FILE)
        self._library_cache = library_cache
        host_service: HostServiceClient | None = None
        if not self._demo_mode and os.environ.get("FLATPAK_ID", "").strip():
            host_service = HostServiceClient()
        self._host_service = host_service
        analyzer: BtrfsCompressionAnalyzer | None = None
        if not self._demo_mode and task_service is None:
            analyzer = BtrfsCompressionAnalyzer(
                filesystem_provider=self._filesystem_provider,
                host_service=host_service,
            )
            if update_tracker is None and not explicit_provider:
                update_tracker = GameUpdateTracker(
                    GameUpdateStateStore(UPDATE_STATE_FILE),
                    stability_delay_seconds=float(
                        self._settings_model.automatic_compression_delay_seconds
                    ),
                )
            if compression_service is None:
                compression_service = CompressionService(
                    BtrfsCompressionProvider(
                        analyzer=analyzer,
                        measurement_provider=(
                            host_service
                            if host_service is not None
                            else PrivilegedMeasurementClient()
                        ),
                    ),
                    CompressionHistoryStore(COMPRESSION_HISTORY_FILE),
                    fingerprint_loader=lambda game: self._compression_fingerprint(
                        game
                    ),
                    verified_callback=lambda game, result: (
                        self._mark_compression_verified(game)
                    ),
                )
        if task_service is None:
            task_service = (
                DemoTaskService()
                if self._demo_mode
                else BtrfsAnalysisTaskService(
                    analyzer=analyzer,
                    cache=AnalysisCache(ANALYSIS_CACHE_FILE),
                    compression_service=compression_service,
                    history_store=TaskHistoryStore(TASK_HISTORY_FILE),
                )
            )
        if backup_service is None:
            backup_service = (
                DemoBackupService() if self._demo_mode else UnavailableBackupService()
            )
        if system_provider is None:
            system_provider = (
                DemoSystemProvider()
                if self._demo_mode
                else LinuxSystemProvider(host_service=host_service)
            )
        if optimization_provider is None:
            optimization_provider = (
                DemoOptimizationProvider()
                if self._demo_mode
                else PreviewOptimizationProvider()
            )
        self._task_service = cast(TaskServiceLike, task_service)
        self._benchmark_estimates = benchmark_estimates or BenchmarkEstimateCatalog(
            COMPRESSION_BENCHMARK_REPORTS_DIR
        )
        self._compression_service = compression_service
        self._update_tracker = update_tracker
        if update_display_store is None and not explicit_provider and not self._demo_mode:
            update_display_store = UpdateDisplayStateStore(UPDATE_DISPLAY_STATE_FILE)
        self._update_display_store = update_display_store
        try:
            self._dismissed_updates = (
                update_display_store.load() if update_display_store is not None else {}
            )
        except Exception as error:
            logger.warning("Could not load Updates display state: %s", error)
            self._dismissed_updates = {}
        self._backup_service = cast(BackupServiceLike, backup_service)
        self._system_provider = cast(SystemProviderLike, system_provider)
        self._optimization_provider = cast(
            OptimizationProviderLike, optimization_provider
        )
        self._game_launcher = game_launcher or SteamLauncher(
            host_service=host_service,
            environment=os.environ,
        )
        self._mangohud_repository = (
            mangohud_repository or MangoHudProfileRepository()
        )
        self._mangohud_detector = mangohud_detector or MangoHudDetector(
            host_service=host_service
        )
        self._mangohud_launch_integration = (
            mangohud_launch_integration
            or MangoHudLaunchIntegration(
                self._mangohud_repository, self._mangohud_detector
            )
        )
        self._optimization_profile_repository = (
            optimization_profile_repository or GameOptimizationProfileRepository()
        )
        self._runtime_tool_detector = runtime_tool_detector or RuntimeToolDetector(
            host_service=host_service
        )
        self._display_detector = display_detector or DisplayDetector()
        self._optimization_advisor = optimization_advisor or OptimizationAdvisor()
        self._runner_integration = runner_integration or RunnerIntegration()
        self._optimization_launch_planner = OptimizationLaunchPlanner()
        self._optiscaler_service = optiscaler_service or OptiScalerService(
            executable_resolver=self._mangohud_launch_integration.executable_resolver
        )
        self._ui_sound_service = UiSoundService(parent=self)
        self._ui_sound_service.set_enabled(self._settings_model.interface_sounds)
        self._gamepad_service = gamepad_service or GamepadService(parent=self)
        self._couch_navigation = CouchNavigationController(self)
        self._gamepad_service.availabilityChanged.connect(
            self._on_gamepad_availability_changed
        )
        self._gamepad_service.controllersChanged.connect(
            self._on_gamepad_controllers_changed
        )
        self._gamepad_service.activeControllerChanged.connect(
            self._on_active_controller_changed
        )
        self._gamepad_service.controllerConnected.connect(
            self._on_controller_connected
        )
        self._gamepad_service.controllerDisconnected.connect(
            self._on_controller_disconnected
        )
        self._gamepad_service.actionTriggered.connect(self._on_gamepad_action)
        self._gamepad_service.inputActivity.connect(self._on_gamepad_activity)
        self._configure_gamepad_service()

        self._games: list[dict[str, Any]] = []
        self._games_model = GamesListModel(self)
        self._compression_library_summaries: list[dict[str, Any]] = []
        self._tasks: list[dict[str, Any]] = []
        self._updates: list[dict[str, Any]] = []
        self._updates_summary: dict[str, Any] = {}
        self._application_update_info = self._build_application_update_info()
        self._compression_plans: dict[str, Any] = {}
        self._selected_game_history: list[dict[str, Any]] = []
        self._backups: list[dict[str, Any]] = []
        self._system_info: dict[str, Any] = {}
        self._selected_game: dict[str, Any] = {}
        self._selected_game_id = ""
        self._current_page = "games"
        self._update_status = (
            "Demo mode · update checks disabled"
            if self._demo_mode
            else "Local Steam manifests · monitoring enabled"
        )
        self._analysis_reports: dict[str, dict[str, Any]] = {}
        self._operational_tasks: dict[str, dict[str, Any]] = {}
        self._reported_terminal_tasks: set[str] = set()
        self._timer_error_reported = False
        self._manual_game_number = 1
        self._shutdown_requested = False
        self._consume_gamepad_action = ""
        self._last_launch_request: dict[str, float] = {}
        requested_interface_mode = str(initial_interface_mode or "").strip().casefold()
        self._interface_mode = (
            requested_interface_mode
            if requested_interface_mode in {"desktop", "couch"}
            else "couch"
            if self._settings_model.controller_mode is ControllerMode.COUCH_ONLY
            else "desktop"
        )
        self._update_executor: ThreadPoolExecutor | None = (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="gameforge-updates",
            )
            if self._update_tracker is not None
            else None
        )
        self._update_jobs: dict[str, tuple[Future[GameUpdateRecord], Event]] = {}
        self._optiscaler_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="gameforge-optiscaler",
        )
        self._optiscaler_jobs: dict[
            str, tuple[Future[OptiScalerProfile], Event, str]
        ] = {}
        self._optiscaler_events: SimpleQueue[tuple[str, str, float]] = SimpleQueue()
        self._inventory_completion_pending = bool(
            self._update_tracker is not None
            and not self._update_tracker.initial_inventory_complete
        )
        self._inventory_scan_started = False
        self._pending_automatic_games: set[str] = set()
        self._updates_dirty = False
        self._last_periodic_rescan = datetime.now(UTC)
        self._scan_worker_active = False
        self._scan_retry_pending = False
        self._scan_request_groups: dict[str, dict[str, Any]] = {}

        self._gamepad_service.start()
        self._couch_navigation.setControllerConnected(
            self._gamepad_service.controllerCount > 0
        )
        if self._interface_mode == "couch" and not self._gamepad_service.available:
            logger.warning(
                "Couch Mode was requested but the controller backend is unavailable; "
                "falling back to Desktop Mode"
            )
            self._interface_mode = "desktop"

        domain_games = [
            self._resolve_game_artwork(game)
            for game in self._initial_games(initial_games)
            if not self._game_is_in_ignored_library(game)
        ]
        self._domain_games: dict[str, Game] = {game.id: game for game in domain_games}
        self._steam_found = bool(domain_games) and not self._demo_mode
        self._is_scanning = False
        self._library_scan_status = (
            "demo"
            if self._demo_mode
            else "cached" if domain_games else "idle"
        )
        self._library_scan_message = (
            "Using safe demonstration data"
            if self._demo_mode
            else (
                f"Showing {len(domain_games)} cached games while Steam is scanned"
                if domain_games
                else "Waiting to scan local Steam libraries"
            )
        )
        self._active_scan_generation = 0

        self._library_scanner = LibraryScanner(self)
        self._library_scanner.scanStarted.connect(self._on_library_scan_started)
        self._library_scanner.libraryReady.connect(self._on_library_ready)
        self._library_scanner.libraryFailed.connect(self._on_library_failed)
        self._library_scanner.gameSizeStarted.connect(self._on_game_size_started)
        self._library_scanner.gameSizeReady.connect(self._on_game_size_ready)
        self._library_scanner.gameSizeFailed.connect(self._on_game_size_failed)
        self._library_scanner.scanFinished.connect(self._on_library_scan_finished)

        self._scan_debounce_timer = QTimer(self)
        self._scan_debounce_timer.setSingleShot(True)
        self._scan_debounce_timer.setInterval(300)
        self._scan_debounce_timer.timeout.connect(self._start_requested_library_scan)
        self._ignored_scan_event_timer = QTimer(self)
        self._ignored_scan_event_timer.setSingleShot(True)
        self._ignored_scan_event_timer.setInterval(500)
        self._ignored_scan_event_timer.timeout.connect(
            self._flush_ignored_scan_events
        )

        self._reload_games(emit_signal=False, reason="startup")
        self._reload_tasks(emit_signal=False)
        # Tasks already present when the controller is constructed came from
        # persisted history. They belong in the history model, but the first
        # timer poll must not announce their old terminal state as a new event.
        self._reported_terminal_tasks.update(
            task.id
            for task in self._task_service.list_tasks()
            if task.status.value in _TERMINAL_STATUSES
        )
        self._reload_backups(emit_signal=False)
        if self._compression_service is not None:
            try:
                recovered = self._compression_service.recover_interrupted()
            except Exception as error:
                logger.warning("Could not recover compression history: %s", error)
                recovered = ()
            if recovered:
                self._deferred_toasts.append(
                    (
                        "Compression state requires verification after an interrupted operation",
                        "warning",
                    )
                )
        self._reload_updates(emit_signal=False)
        self._reload_selected_history(emit_signal=False)
        self._reload_system_info(emit_signal=False)

        self._task_timer = QTimer(self)
        self._task_timer.setInterval(150)
        self._task_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._task_timer.timeout.connect(self._poll_tasks)
        self._task_timer.start()
        for message, level in self._deferred_toasts:
            QTimer.singleShot(
                0,
                lambda message=message, level=level: self._emit_toast(message, level),
            )
        if auto_refresh and not self._demo_mode:
            QTimer.singleShot(
                0,
                lambda: self.requestLibraryScan("startup", "", "startup"),
            )
        elif self._update_tracker is not None:
            QTimer.singleShot(0, self._schedule_update_observations)

    @Property("QVariantList", notify=gamesChanged)
    def games(self) -> list[dict[str, Any]]:
        return self._games

    @Property(QObject, constant=True)
    def gamesModel(self) -> GamesListModel:
        return self._games_model

    @Property(QObject, constant=True)
    def couchNavigation(self) -> CouchNavigationController:
        return self._couch_navigation

    @Property("QVariantList", notify=gamesChanged)
    def compressionLibrarySummaries(self) -> list[dict[str, Any]]:
        """Existing authoritative compsize totals grouped by Steam library."""

        return self._compression_library_summaries

    @Property("QVariantList", notify=tasksChanged)
    def tasks(self) -> list[dict[str, Any]]:
        return self._tasks

    @Property("QVariantList", notify=tasksChanged)
    def activeTasks(self) -> list[dict[str, Any]]:
        return [
            task
            for task in self._tasks
            if str(task.get("status", "")).lower() not in _TERMINAL_STATUSES
        ]

    @Property("QVariantList", notify=tasksChanged)
    def taskHistory(self) -> list[dict[str, Any]]:
        finished = [
            task
            for task in self._tasks
            if str(task.get("status", "")).lower() in _TERMINAL_STATUSES
        ]
        return sorted(
            finished,
            key=lambda task: str(task.get("updatedAt", "")),
            reverse=True,
        )[:100]

    @Property(bool, notify=tasksChanged)
    def hasActiveCompressionTasks(self) -> bool:
        """Whether closing the window would interrupt a real write task."""

        try:
            return any(
                task.task_type is TaskType.COMPRESSION
                and task.status.value not in _TERMINAL_STATUSES
                for task in self._task_service.list_tasks()
            )
        except Exception:
            service = self._compression_service
            return bool(service is not None and service.active_game_ids())

    @Property("QVariantList", notify=updatesChanged)
    def updates(self) -> list[dict[str, Any]]:
        return self._updates

    @Property("QVariantMap", notify=updatesSummaryChanged)
    def updatesSummary(self) -> dict[str, Any]:
        return self._updates_summary

    @Property("QVariantMap", constant=True)
    def applicationUpdateInfo(self) -> dict[str, Any]:
        return self._application_update_info

    @Property("QVariantList", notify=compressionHistoryChanged)
    def selectedGameCompressionHistory(self) -> list[dict[str, Any]]:
        return self._selected_game_history

    @Property("QVariantMap", notify=systemInfoChanged)
    def systemInfo(self) -> dict[str, Any]:
        return self._system_info

    @Property(bool, notify=showSystemMountsChanged)
    def showSystemMounts(self) -> bool:
        return self._show_system_mounts

    @Slot(bool)
    def setShowSystemMounts(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if normalized == self._show_system_mounts:
            return
        self._show_system_mounts = normalized
        self.showSystemMountsChanged.emit()
        self._reload_system_info()

    @Property("QVariantList", notify=backupsChanged)
    def backups(self) -> list[dict[str, Any]]:
        return self._backups

    @Property("QVariantMap", notify=selectedGameChanged)
    def selectedGame(self) -> dict[str, Any]:
        return self._selected_game

    @Property(str, notify=currentPageChanged)
    def currentPage(self) -> str:
        return self._current_page

    @Property(str, notify=themeModeChanged)
    def themeMode(self) -> str:
        return self._theme_mode

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, Any]:
        return self._settings

    @Property(str, constant=True)
    def appName(self) -> str:
        return APP_NAME

    @Property(str, constant=True)
    def appVersion(self) -> str:
        return APP_VERSION

    @Property(str, constant=True)
    def appLogoUrl(self) -> str:
        try:
            return APP_ICON.as_uri() if APP_ICON.is_file() else ""
        except (OSError, ValueError):
            return ""

    @Property(str, notify=updateStatusChanged)
    def updateStatus(self) -> str:
        return self._update_status

    @Property(str, notify=libraryScanStatusChanged)
    def libraryScanStatus(self) -> str:
        return self._library_scan_status

    @Property(str, notify=libraryScanMessageChanged)
    def libraryScanMessage(self) -> str:
        return self._library_scan_message

    @Property(bool, constant=True)
    def demoMode(self) -> bool:
        return self._demo_mode

    @Property(bool, notify=steamFoundChanged)
    def steamFound(self) -> bool:
        return self._steam_found

    @Property(bool, notify=isScanningChanged)
    def isScanning(self) -> bool:
        return self._is_scanning

    @Property(bool, notify=gamepadAvailableChanged)
    def gamepadAvailable(self) -> bool:
        return bool(self._gamepad_service.available)

    @Property(str, notify=gamepadAvailableChanged)
    def gamepadStatus(self) -> str:
        return str(self._gamepad_service.status)

    @Property("QVariantList", notify=controllersChanged)
    def controllers(self) -> list[dict[str, Any]]:
        return list(self._gamepad_service.controllers)

    @Property(int, notify=controllersChanged)
    def controllerCount(self) -> int:
        return int(self._gamepad_service.controllerCount)

    @Property("QVariantMap", notify=activeControllerChanged)
    def activeController(self) -> dict[str, Any]:
        return dict(self._gamepad_service.activeController)

    @Property("QVariantMap", notify=activeControllerChanged)
    def gamepadButtonHints(self) -> dict[str, Any]:
        return dict(self._gamepad_service.buttonHints)

    @Property(str, notify=interfaceModeChanged)
    def interfaceMode(self) -> str:
        return self._interface_mode

    @Slot(result=bool)
    def toggleInterfaceMode(self) -> bool:
        self._set_interface_mode(
            "desktop" if self._interface_mode == "couch" else "couch"
        )
        return True

    @Slot(str, result=bool)
    def setInterfaceMode(self, mode: str) -> bool:
        normalized = str(mode).strip().casefold()
        if normalized not in {"desktop", "couch"}:
            return False
        self._set_interface_mode(normalized)
        return True

    @Slot(str, result=bool)
    def requestWindowAction(self, action: str) -> bool:
        """Expose only the safe window actions needed by both QML shells."""

        normalized = str(action).strip().casefold()
        if normalized not in {"close", "minimize", "stay"}:
            return False
        self.windowActionRequested.emit(normalized)
        return True

    @Slot(result=bool)
    def refreshGames(self) -> bool:
        """Queue a debounced manual library refresh."""

        accepted = self.requestLibraryScan("legacy_refresh_slot", "", "manual")
        # Preserve the public slot's historical non-blocking/immediate start
        # contract. Named UI/watcher requests use requestLibraryScan directly
        # and receive the debounce window.
        if accepted and self._scan_debounce_timer.isActive():
            self._scan_debounce_timer.stop()
            self._start_requested_library_scan()
        return accepted

    @Slot(str, str, str, result=bool)
    def requestLibraryScan(
        self,
        trigger: str,
        event_path: str = "",
        event_kind: str = "manual",
    ) -> bool:
        """Coalesce high-level Steam inventory events into bounded scans."""

        if self._shutdown_requested:
            return False
        normalized_trigger = str(trigger).strip() or "unknown"
        normalized_path = str(event_path).strip()
        classified = self._classify_library_event(normalized_path, event_kind)
        active_task = self._has_active_gameforge_task()
        group = self._scan_request_groups.setdefault(
            classified,
            {"count": 0, "triggers": set(), "paths": set(), "active_task": False},
        )
        group["count"] += 1
        group["triggers"].add(normalized_trigger)
        if normalized_path and len(group["paths"]) < 5:
            group["paths"].add(normalized_path)
        group["active_task"] = bool(group["active_task"] or active_task)

        if classified == "game_file":
            self._ignored_scan_event_timer.start()
            return False
        if self._scan_worker_active:
            self._scan_retry_pending = True
            decision = "coalesced_retry"
        elif self._scan_debounce_timer.isActive():
            decision = "coalesced_debounce"
        else:
            self._set_scan_state(
                status="scan-queued",
                message="Steam library refresh queued",
                is_scanning=True,
            )
            self._scan_debounce_timer.start()
            decision = "scheduled"
        logger.info(
            "Steam scan request: trigger=%s path=%s time=%s activeTask=%s "
            "source=%s decision=%s",
            normalized_trigger,
            normalized_path or "-",
            datetime.now(UTC).isoformat(),
            str(active_task).lower(),
            classified,
            decision,
        )
        return True

    @Slot()
    def _start_requested_library_scan(self) -> None:
        if self._shutdown_requested or self._scan_worker_active:
            return
        groups = self._scan_request_groups
        self._scan_request_groups = {}
        logger.info(
            "Steam scan batch starting: decision=executed groups=%s "
            "currentGeneration=%d games=%d",
            self._format_scan_request_groups(groups),
            self._games_model_generation,
            len(self._games),
        )
        try:
            self._active_scan_generation = self._library_scanner.start(
                self._game_provider,
                directory_scanner=(
                    None if self._demo_mode else self._directory_size_scanner
                ),
            )
        except Exception as error:
            self._scan_worker_active = False
            self._set_scan_state(is_scanning=False)
            self._report_error("starting the library scan", error)
            return
        self._scan_worker_active = True

    @Slot()
    def _flush_ignored_scan_events(self) -> None:
        group = self._scan_request_groups.pop("game_file", None)
        if not group:
            return
        logger.info(
            "Steam scan events grouped: time=%s source=game_file count=%d "
            "triggers=%s paths=%s activeTask=%s decision=ignored",
            datetime.now(UTC).isoformat(),
            int(group["count"]),
            sorted(group["triggers"]),
            sorted(group["paths"]),
            str(bool(group["active_task"])).lower(),
        )

    @staticmethod
    def _classify_library_event(event_path: str, event_kind: str) -> str:
        normalized_kind = str(event_kind).strip().casefold().replace("-", "_")
        path = Path(str(event_path)) if str(event_path).strip() else None
        name = path.name.casefold() if path is not None else ""
        if name == "libraryfolders.vdf":
            return "libraryfolders.vdf"
        if name.startswith("appmanifest_") and name.endswith(".acf"):
            return "appmanifest"
        parts = tuple(part.casefold() for part in path.parts) if path is not None else ()
        if any(
            parts[index : index + 2] == ("steamapps", "common")
            for index in range(max(0, len(parts) - 1))
        ):
            return "game_file"
        if normalized_kind in {
            "library_added",
            "library_removed",
            "game_added",
            "game_removed",
        }:
            return normalized_kind
        return normalized_kind or "manual"

    def _has_active_gameforge_task(self) -> bool:
        active = {"queued", "running", "paused", "cancelling"}
        return any(
            str(row.get("status") or "").casefold() in active
            for row in (*self._tasks, *self._operational_tasks.values())
        )

    @staticmethod
    def _format_scan_request_groups(groups: Mapping[str, Mapping[str, Any]]) -> str:
        formatted: list[str] = []
        for source in sorted(groups):
            group = groups[source]
            formatted.append(
                f"{source}:count={int(group.get('count', 0))},"
                f"triggers={sorted(group.get('triggers', ()))},"
                f"paths={sorted(group.get('paths', ()))},"
                f"activeTask={bool(group.get('active_task', False))}"
            )
        return "; ".join(formatted) or "none"

    @Slot(str, result=bool)
    def forgetLibrary(self, library_path: str) -> bool:
        """Forget app cache for a library only after current Steam config drops it."""

        raw_path = str(library_path).strip()
        if not raw_path or self._demo_mode:
            return False
        candidate = Path(raw_path).expanduser()
        try:
            normalized = Path(os.path.abspath(os.fspath(candidate)))
        except (OSError, TypeError, ValueError):
            return False
        configured = self._provider_configured_library_paths()
        if any(self._same_path(normalized, path) for path in configured):
            self._emit_toast(
                "Remove this library from Steam before forgetting its cache",
                "warning",
            )
            return False
        try:
            if normalized.exists():
                self._emit_toast(
                    "The library path still exists and cannot be forgotten safely",
                    "warning",
                )
                return False
        except OSError:
            return False
        game_ids = {
            game.id
            for game in self._domain_games.values()
            if self._path_is_within_library(game, normalized)
        }
        if not game_ids:
            return False
        if any(self._active_task_for_game(game_id) is not None for game_id in game_ids):
            self._emit_toast(
                "A task for this library is still active",
                "warning",
            )
            return False

        for row in self._updates:
            if str(row.get("gameId", "")) not in game_ids:
                continue
            row_id = str(row.get("rowId", ""))
            if row_id:
                self._dismissed_updates[row_id] = str(
                    row.get("displayVersion") or row_id
                )
        for game_id in game_ids:
            self._domain_games.pop(game_id, None)
            self._analysis_reports.pop(game_id, None)
            self._pending_automatic_games.discard(game_id)
        tracker = self._update_tracker
        forget = getattr(tracker, "forget_games", None)
        if callable(forget):
            try:
                forget(tuple(game_ids))
            except Exception as error:
                logger.warning("Could not forget update records: %s", error)
        self._save_update_display_state()
        self._save_library_cache()
        self._reload_games()
        self._reload_tasks()
        self._reload_updates()
        self._reload_system_info()
        self._emit_toast("Library cache was forgotten", "success")
        return True

    @Slot(str, result=bool)
    def ignoreLibrary(self, library_path: str) -> bool:
        """Hide one Steam library locally without editing Steam configuration."""

        normalized = self._canonical_library_path(library_path)
        if normalized is None or self._demo_mode:
            return False
        game_ids = {
            game.id
            for game in self._domain_games.values()
            if self._path_is_within_library(game, normalized)
        }
        configured = any(
            self._same_path(normalized, path)
            for path in self._provider_configured_library_paths()
        )
        if not game_ids and not configured:
            return False
        if any(self._active_task_for_game(game_id) is not None for game_id in game_ids):
            self._emit_toast("A task for this library is still active", "warning")
            return False

        ignored = list(self._settings_model.ignored_steam_libraries)
        if not any(self._same_path(normalized, path) for path in ignored):
            ignored.append(normalized)
        updated_settings = replace(
            self._settings_model,
            ignored_steam_libraries=tuple(ignored),
        )
        try:
            self._settings_store.save(updated_settings)
        except Exception as error:
            self._report_error("saving the ignored Steam library", error)
            return False
        self._settings_model = updated_settings
        self._settings = settings_to_qml(updated_settings)
        self.settingsChanged.emit()

        for row in self._updates:
            if str(row.get("gameId", "")) not in game_ids:
                continue
            row_id = str(row.get("rowId", ""))
            if row_id:
                self._dismissed_updates[row_id] = str(
                    row.get("displayVersion") or row_id
                )
        for game_id in game_ids:
            self._domain_games.pop(game_id, None)
            self._analysis_reports.pop(game_id, None)
            self._pending_automatic_games.discard(game_id)
        tracker = self._update_tracker
        forget = getattr(tracker, "forget_games", None)
        if callable(forget) and game_ids:
            try:
                forget(tuple(game_ids))
            except Exception as error:
                logger.warning("Could not forget ignored-library updates: %s", error)
        self._save_update_display_state()
        self._save_library_cache()
        self._reload_games()
        self._reload_tasks()
        self._reload_updates()
        self._reload_system_info()
        self._emit_toast("Library was forgotten in GameForge", "success")
        return True

    @Slot(str, result=bool)
    def restoreIgnoredLibrary(self, library_path: str) -> bool:
        """Remove a local ignored-library record and schedule a safe refresh."""

        normalized = self._canonical_library_path(library_path)
        if normalized is None:
            return False
        retained = tuple(
            path
            for path in self._settings_model.ignored_steam_libraries
            if not self._same_path(path, normalized)
        )
        if len(retained) == len(self._settings_model.ignored_steam_libraries):
            return False
        updated_settings = replace(
            self._settings_model,
            ignored_steam_libraries=retained,
        )
        try:
            self._settings_store.save(updated_settings)
        except Exception as error:
            self._report_error("restoring the ignored Steam library", error)
            return False
        self._settings_model = updated_settings
        self._settings = settings_to_qml(updated_settings)
        self.settingsChanged.emit()
        self._emit_toast("Library was restored in GameForge", "success")
        if not self._is_scanning:
            self.requestLibraryScan(
                "restore_ignored_library",
                str(normalized),
                "library_added",
            )
        return True

    @Slot(result=bool)
    def addManualGame(self) -> bool:
        """Add a descriptive in-memory entry; no path is touched or scanned."""

        if not self._demo_mode:
            self._emit_toast(
                "Manual game entries are available only in Demo mode",
                "info",
            )
            return False

        try:
            game = self._new_manual_game()
            self._game_provider.add_game(game)
            self._set_domain_games(
                self._game_provider.list_games(),
                reason="manual_game_added",
            )
        except Exception as error:
            self._report_error("adding a manual demo game", error)
            return False

        logger.info("Added in-memory manual demo game %s", game.id)
        self._emit_toast(f"Added {game.name} to the demo library", "success")
        return True

    @Slot(str, result=bool)
    def openGame(self, game_id: str) -> bool:
        game = self._find_game(game_id)
        if game is None:
            self._emit_toast("The selected game could not be found", "error")
            return False

        self._selected_game_id = game.id
        compression_result = self._latest_compression_results().get(game.id)
        verification_result = self._latest_verification_results().get(game.id)
        self._selected_game = self._present_game(
            game,
            analysis_report=self._analysis_reports.get(game.id),
            compression_result=compression_result,
            verification_result=verification_result,
            benchmark_estimate=self._benchmark_estimates.estimate_for(game),
        )
        self._reload_selected_history()
        self.selectedGameChanged.emit()
        self._set_current_page("gameDetails")
        return True

    @Slot(str, result=bool)
    def navigate(self, page: str) -> bool:
        normalized = self._normalize_page(page)
        if normalized is None:
            logger.warning("QML requested an unknown page: %r", page)
            self._emit_toast(f"Unknown page: {page}", "warning")
            return False
        if normalized == "gameDetails" and not self._selected_game_id:
            self._emit_toast("Select a game before opening its details", "warning")
            return False

        self._set_current_page(normalized)
        return True

    @Slot()
    def backToGames(self) -> None:
        self._set_current_page("games")

    @Slot(result=bool)
    @Slot(str, result=bool)
    def analyzeGame(self, game_id: str = "") -> bool:
        game = self._resolve_game(game_id)
        if game is None:
            return False
        if not self._game_actions_allowed(game):
            self._emit_toast(f"Library unavailable for {game.name}", "warning")
            return False
        try:
            task = self._task_service.enqueue_analysis(game)
            self._reload_tasks()
        except Exception as error:
            self._report_error(f"queuing analysis for {game.name}", error)
            return False

        logger.info("Queued read-only analysis task %s for %s", task.id, game.name)
        self._emit_toast(f"Analysis queued for {game.name}", "success")
        self._set_current_page("tasks")
        return True

    @Slot(str, result=bool)
    @Slot(str, str, result=bool)
    def requestCompression(self, game_id: str, mode: str = "Balanced") -> bool:
        game = self._resolve_game(game_id)
        if game is None:
            return False
        if self._demo_mode and not game.compression_available:
            logger.warning("Rejected demo compression for incompatible game %s", game.id)
            self._emit_toast(
                f"Compression is unavailable for {game.name} on {game.filesystem.value}",
                "warning",
            )
            return False

        if not self._demo_mode:
            plan = self.prepareCompression(game.id, mode, False)
            if not bool(plan.get("valid", False)):
                return False
            self._emit_toast(
                "Review and confirm the compression plan before starting",
                "info",
            )
            return True

        try:
            profile = self._coerce_enum(CompressionProfile, mode)
            task = self._task_service.enqueue_compression(game, profile)
            self._reload_tasks()
        except Exception as error:
            self._report_error(f"queuing demo compression for {game.name}", error)
            return False

        logger.info(
            "Queued simulated compression task %s for %s (%s)",
            task.id,
            game.name,
            profile.value,
        )
        self._emit_toast(
            f"{profile.value} compression simulation queued for {game.name}",
            "success",
        )
        self._set_current_page("tasks")
        return True

    @Slot(str, result=bool)
    def analyzeChanges(self, game_id: str) -> bool:
        return self.analyzeGame(game_id)

    @Slot(str, result=bool)
    def verifyCompression(self, game_id: str) -> bool:
        """Queue one authenticated read-only measurement, never recompression."""

        game = self._resolve_game(game_id)
        if game is None or self._demo_mode:
            return False
        if not self._game_actions_allowed(game):
            self._emit_toast("The Steam library is unavailable", "warning")
            return False
        if game.filesystem is not FilesystemType.BTRFS and (
            game.filesystem_name.casefold() != "btrfs"
        ):
            self._emit_toast(
                "Compression verification requires Btrfs",
                "warning",
            )
            return False
        method = getattr(self._task_service, "enqueue_verification", None)
        if not callable(method):
            self._emit_toast(
                "Privileged compression measurement is unavailable",
                "error",
            )
            return False
        try:
            task = method(game)
        except Exception as error:
            self._report_error(f"verifying compression for {game.name}", error)
            return False
        self._reload_tasks()
        self._emit_toast(
            _MEASUREMENT_AUTH_TOAST,
            "info",
        )
        logger.info(
            "Queued read-only compression verification %s for %s",
            task.id,
            game.id,
        )
        return True

    @Slot(str, str, bool, result="QVariantMap")
    def prepareCompression(
        self,
        game_id: str,
        mode: str = "Auto",
        changed_only: bool = False,
    ) -> dict[str, Any]:
        """Create a read-only plan; no Btrfs property or file is changed."""

        game = self._resolve_game(game_id)
        service = self._compression_service
        if game is None or service is None or self._demo_mode:
            return self._invalid_plan(
                "Real Btrfs compression is unavailable in this mode"
            )
        if not self._game_actions_allowed(game):
            return self._invalid_plan("The Steam library is unavailable")
        raw_report = self._analysis_reports.get(game.id)
        if not isinstance(raw_report, Mapping):
            self._emit_toast("Analyze the game before creating a plan", "warning")
            return self._invalid_plan("A completed analysis is required")
        try:
            report = BtrfsAnalysisReport.from_dict(raw_report)
            profile = self._coerce_enum(CompressionProfile, mode)
            plan = service.prepare(
                game,
                report,
                profile,
                changed_only=bool(changed_only),
                after_update=bool(changed_only),
                confirmation_required=True,
                minimum_free_bytes=int(
                    float(
                        self._settings_model.automatic_compression_min_free_gb
                    )
                    * (1024**3)
                ),
            )
        except Exception as error:
            self._report_error(f"preparing compression for {game.name}", error)
            return self._invalid_plan(str(error) or type(error).__name__)
        self._compression_plans[plan.id] = plan
        current_measurement = self._current_authoritative_compsize(game.id)
        profitability = normalized_benchmark_projection(
            self._benchmark_estimates.estimate_for(game),
            level=int(plan.one_time_recompression_level),
            current_uncompressed_bytes=current_measurement.get(
                "compsize_uncompressed_bytes"
            ),
            current_disk_usage_bytes=current_measurement.get(
                "compsize_disk_bytes"
            ),
            app_id=str(game.steam_app_id or ""),
            build_id=str(game.steam_build_id or ""),
        )
        presented = self._plan_to_qml(plan, profitability=profitability)
        if not plan.eligible:
            self._emit_toast(
                plan.blockers[0] if plan.blockers else "Compression is blocked",
                "warning",
            )
        return presented

    @Slot(str, result=bool)
    def startCompression(self, plan_id: str) -> bool:
        """Queue a plan after the QML confirmation dialog was accepted."""

        return self._start_compression_plan(
            str(plan_id),
            confirmed=True,
            automatic_authorized=False,
        )

    @Slot(str, result=bool)
    def ignoreUpdate(self, game_id: str) -> bool:
        tracker = self._update_tracker
        if tracker is None:
            return False
        try:
            tracker.ignore(game_id)
        except Exception as error:
            self._report_error(f"ignoring update for {game_id}", error)
            return False
        self._reload_updates()
        self._emit_toast("This game update was ignored", "info")
        return True

    @Slot(str, result=bool)
    def dismissUpdate(self, row_id: str) -> bool:
        """Hide one finished/inactive Updates row without deleting history."""

        normalized = str(row_id).strip()
        row = next(
            (item for item in self._updates if item.get("rowId") == normalized),
            None,
        )
        if row is None or row.get("canDismiss") is not True:
            return False
        game_id = str(row.get("gameId", ""))
        if game_id and self._active_task_for_game(game_id) is not None:
            return False
        self._dismissed_updates[normalized] = str(
            row.get("displayVersion") or normalized
        )
        self._save_update_display_state()
        self._reload_updates()
        return True

    @Slot(result=int)
    def clearFinishedUpdates(self) -> int:
        """Hide completed/error rows while preserving actionable active work."""

        removable_statuses = {
            GameUpdateStatus.INVENTORY.value,
            GameUpdateStatus.UP_TO_DATE.value,
            GameUpdateStatus.IGNORED.value,
            GameUpdateStatus.ERROR.value,
        }
        rows = [
            row
            for row in self._updates
            if row.get("canDismiss") is True
            and (
                row.get("sectionKey") == "recently_optimized"
                or str(row.get("updateStatus", "")) in removable_statuses
            )
        ]
        return self._dismiss_update_rows(rows)

    @Slot(result=int)
    def clearUnavailableUpdates(self) -> int:
        """Hide rows belonging to disconnected or already forgotten games."""

        rows = [
            row
            for row in self._updates
            if row.get("canDismiss") is True
            and (
                row.get("libraryAvailable") is False
                or str(row.get("gameId", "")) not in self._domain_games
            )
        ]
        return self._dismiss_update_rows(rows)

    @Slot(result=int)
    def clearHiddenUpdatesHistory(self) -> int:
        """Forget presentation tombstones so hidden events may be shown again."""

        removed = len(self._dismissed_updates)
        if not removed:
            return 0
        self._dismissed_updates.clear()
        self._save_update_display_state()
        self._reload_updates()
        self._emit_toast("Hidden Updates history was cleared", "success")
        return removed

    def _dismiss_update_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        removed = 0
        for row in rows:
            row_id = str(row.get("rowId", ""))
            game_id = str(row.get("gameId", ""))
            if (
                not row_id
                or (game_id and self._active_task_for_game(game_id) is not None)
            ):
                continue
            self._dismissed_updates[row_id] = str(
                row.get("displayVersion") or row_id
            )
            removed += 1
        if removed:
            self._save_update_display_state()
            self._reload_updates()
        return removed

    def _save_update_display_state(self) -> None:
        store = self._update_display_store
        if store is None:
            return
        try:
            store.save(self._dismissed_updates)
        except Exception as error:
            logger.warning("Could not save Updates display state: %s", error)

    @staticmethod
    def _invalid_plan(message: str) -> dict[str, Any]:
        readable = str(message).strip() or "Compression cannot be started"
        return {
            "id": "",
            "planId": "",
            "valid": False,
            "eligible": False,
            "canStart": False,
            "confirmationRequired": True,
            "blockers": [readable],
            "warnings": [],
            "error": readable,
            "message": readable,
        }

    @staticmethod
    def _plan_to_qml(
        plan: Any,
        *,
        profitability: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Expose a plan with explicit booleans and both UI-critical aliases."""

        before = plan.before.to_dict()
        blockers = [str(value) for value in plan.blockers]
        warnings = [str(value) for value in plan.warnings]
        eligible = bool(plan.eligible and not blockers)
        projection = dict(profitability or {})
        low_benefit = bool(
            projection.get("available") is True
            and projection.get("lowBenefit") is True
        )
        return {
            "id": str(plan.id),
            "planId": str(plan.id),
            "gameId": str(plan.game_id),
            "appId": str(plan.app_id),
            "gameName": str(plan.game_name),
            "gamePath": str(plan.game_path),
            "path": str(plan.game_path),
            "profile": str(plan.profile.value),
            "persistentCompressionAlgorithm": str(
                plan.persistent_compression_algorithm
            ),
            "oneTimeRecompressionLevel": int(plan.one_time_recompression_level),
            "plannedFileCount": int(plan.total_files),
            "totalFiles": int(plan.total_files),
            "plannedBytes": int(plan.total_bytes),
            "totalBytes": int(plan.total_bytes),
            "skippedFileCount": len(plan.skipped_files),
            "fullCompression": bool(plan.full_compression),
            "afterUpdate": bool(plan.after_update),
            "buildId": str(plan.build_id or ""),
            "estimatedSavingsLowBytes": plan.estimated_savings_low_bytes,
            "estimatedSavingsHighBytes": plan.estimated_savings_high_bytes,
            "estimatedSharedGrowthBytes": plan.estimated_shared_growth_bytes,
            "availableBytes": plan.available_bytes,
            "requiredFreeBytes": int(plan.required_free_bytes),
            "currentLogicalBytes": int(before.get("logical_bytes", 0)),
            "currentPhysicalBytes": int(before.get("physical_bytes", 0)),
            "currentExclusiveBytes": before.get("exclusive_bytes"),
            "currentSharedBytes": before.get("shared_bytes"),
            "sharedExtentState": str(
                before.get("shared_extent_state", "unknown")
            ),
            "before": qml_value(before),
            "valid": eligible,
            "eligible": eligible,
            "canStart": eligible,
            "confirmationRequired": bool(plan.confirmation_required),
            "profitability": qml_value(projection),
            "profitabilityAvailable": projection.get("available") is True,
            "estimatedTotalPotentialBytes": projection.get(
                "estimatedTotalPotentialBytes"
            ),
            "estimatedAdditionalSavingBytes": projection.get(
                "estimatedAdditionalSavingBytes"
            ),
            "estimatedPhysicalBytes": projection.get("estimatedPhysicalBytes"),
            "lowBenefit": low_benefit,
            "additionalConfirmationRequired": low_benefit,
            "blockers": blockers,
            "warnings": warnings,
            "error": "" if eligible else (blockers[0] if blockers else ""),
            "message": "" if eligible else (blockers[0] if blockers else ""),
            "createdAt": plan.created_at.isoformat(),
        }

    def _start_compression_plan(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        automatic_authorized: bool,
    ) -> bool:
        service = self._compression_service
        normalized_id = str(plan_id).strip()
        plan = self._compression_plans.get(normalized_id)
        if plan is None and service is not None:
            plan = service.get_plan(normalized_id)
        if service is None or plan is None:
            self._emit_toast("The compression plan is no longer available", "error")
            return False
        game = self._find_game(plan.game_id)
        if game is None or not self._game_actions_allowed(game):
            self._emit_toast("The game library is unavailable", "error")
            return False
        if not bool(plan.eligible):
            self._emit_toast(
                plan.blockers[0] if plan.blockers else "Compression is blocked",
                "error",
            )
            return False
        try:
            task = self._task_service.enqueue_compression_plan(
                game,
                plan,
                confirmed=bool(confirmed),
                automatic_authorized=bool(automatic_authorized),
            )
        except Exception as error:
            self._report_error(f"queuing compression for {game.name}", error)
            return False
        self._compression_plans.pop(normalized_id, None)
        self._pending_automatic_games.discard(game.id)
        self._reload_tasks()
        self._reload_updates()
        self._reload_system_info()
        if automatic_authorized:
            if self._settings_model.automatic_compression_notify:
                self._emit_toast(
                    f"Automatic compression queued for {game.name}",
                    "success",
                )
        else:
            self._emit_toast(f"Compression queued for {game.name}", "success")
            self._set_current_page("tasks")
        logger.info(
            "Queued compression task %s for %s from plan %s",
            task.id,
            game.id,
            plan.id,
        )
        return True

    @Slot(str, result=bool)
    def pauseTask(self, task_id: str) -> bool:
        return self._change_task_state("pause", task_id)

    @Slot(str, result=bool)
    def resumeTask(self, task_id: str) -> bool:
        return self._change_task_state("resume", task_id)

    @Slot(str, result=bool)
    def cancelTask(self, task_id: str) -> bool:
        return self._change_task_state("cancel", task_id)

    @Slot(result=int)
    def clearFinishedTasks(self) -> int:
        method = getattr(self._task_service, "clear_finished", None)
        removed = 0
        if callable(method):
            try:
                removed = int(method())
            except Exception as error:
                self._report_error("clearing finished tasks", error)
                return 0
        operational_ids = [
            task_id
            for task_id, task in self._operational_tasks.items()
            if str(task.get("status", "")).lower() in _TERMINAL_STATUSES
        ]
        for task_id in operational_ids:
            self._operational_tasks.pop(task_id, None)
        removed += len(operational_ids)
        self._reported_terminal_tasks.clear()
        self._reload_tasks()
        return removed

    @Slot(str, result=bool)
    def removeFinishedTask(self, task_id: str) -> bool:
        operational = self._operational_tasks.get(str(task_id))
        if (
            operational is not None
            and str(operational.get("status", "")).lower() in _TERMINAL_STATUSES
        ):
            self._operational_tasks.pop(str(task_id), None)
            self._reported_terminal_tasks.discard(str(task_id))
            self._reload_tasks()
            return True
        method = getattr(self._task_service, "remove_finished", None)
        if not callable(method):
            return False
        try:
            removed = bool(method(str(task_id)))
        except Exception as error:
            self._report_error("removing a finished task", error)
            return False
        if removed:
            self._reported_terminal_tasks.discard(str(task_id))
            self._reload_tasks()
        return removed

    @Slot(result=bool)
    def cancelActiveCompressionTasks(self) -> bool:
        """Request cancellation for every active compression task.

        The task service owns a cooperative event for each worker.  The
        provider polls that event and terminates its matching child process;
        a global provider cancellation is reserved for final shutdown so one
        task cannot poison future work.
        """

        requested = False
        try:
            for task in self._task_service.list_tasks():
                if (
                    task.task_type is TaskType.COMPRESSION
                    and task.status.value not in _TERMINAL_STATUSES
                ):
                    try:
                        self._task_service.cancel(task.id)
                    except Exception as error:
                        logger.warning(
                            "Could not cancel compression task %s: %s",
                            task.id,
                            error,
                        )
                    else:
                        requested = True
        except Exception as error:
            logger.warning("Could not enumerate compression tasks: %s", error)
        self._reload_tasks()
        return requested

    @Slot(str, "QVariant", result=bool)
    def saveSetting(self, key: str, value: Any) -> bool:
        field_name = self._setting_field_name(key)
        if field_name is None:
            logger.warning("Refused to save unknown setting key %r", key)
            self._emit_toast(f"Unknown setting: {key}", "warning")
            return False

        try:
            current_value = getattr(self._settings_model, field_name)
            converted_value = self._coerce_setting_value(
                current_value,
                value,
                field_name=field_name,
            )
            updated_model = replace(
                self._settings_model, **{field_name: converted_value}
            )
            self._settings_store.save(updated_model)
        except Exception as error:
            self._report_error(f"saving setting {field_name}", error)
            return False

        self._settings_model = updated_model
        self._settings = settings_to_qml(updated_model)
        self.settingsChanged.emit()
        toast_message = "Setting saved locally"
        toast_level = "success"
        new_theme_mode = self._extract_theme_mode(self._settings)
        if new_theme_mode != self._theme_mode:
            self._theme_mode = new_theme_mode
            self.themeModeChanged.emit()
        if field_name == "log_level":
            self._apply_runtime_log_level(str(qml_value(converted_value)))
        elif field_name == "steam_installation_directories" and not self._demo_mode:
            setter = getattr(self._game_provider, "set_additional_roots", None)
            try:
                if callable(setter):
                    setter(tuple(converted_value))
                self.requestLibraryScan(
                    "settings_steam_paths",
                    "",
                    "settings",
                )
            except Exception as error:
                logger.warning(
                    "Saved Steam locations but could not apply them immediately: %s",
                    error,
                )
                toast_message = "Steam locations were saved and will apply after restart"
                toast_level = "warning"
        elif field_name == "show_steam_tools_and_runtimes":
            self._reload_games()
        elif field_name in {
            "swap_accept_back",
            "analog_deadzone",
            "navigation_repeat_delay_ms",
            "navigation_repeat_rate_ms",
        }:
            self._configure_gamepad_service()
        elif field_name == "controller_mode":
            if converted_value is ControllerMode.DESKTOP_ONLY:
                self._set_interface_mode("desktop")
            elif converted_value is ControllerMode.COUCH_ONLY:
                self._set_interface_mode("couch")
        elif field_name == "interface_sounds":
            self._ui_sound_service.set_enabled(bool(converted_value))
        elif field_name.startswith("automatic_compression_"):
            self._reload_updates()
            if (
                field_name == "automatic_compression_mode"
                and converted_value is not AutomaticCompressionMode.OFF
            ):
                self._queue_eligible_automatic_compression()
        logger.info("Saved local setting %s", field_name)
        self._emit_toast(toast_message, toast_level)
        return True

    @Slot(str, result=bool)
    def restoreBackup(self, backup_id: str) -> bool:
        if not self._demo_mode:
            self._emit_toast("Backup restore is not implemented yet", "info")
            return False
        try:
            backup = self._backup_service.restore_backup(backup_id)
            self._reload_backups()
        except Exception as error:
            self._report_error(f"restoring demo backup {backup_id}", error)
            return False

        logger.info("Marked demo backup %s as restored", backup_id)
        self._emit_toast(
            f"Demo backup for {backup.game_name} marked as restored", "success"
        )
        return True

    @Slot(str, result=bool)
    def deleteBackup(self, backup_id: str) -> bool:
        if not self._demo_mode:
            self._emit_toast("Backup deletion is not implemented yet", "info")
            return False
        try:
            self._backup_service.delete_backup(backup_id)
            self._reload_backups()
        except Exception as error:
            self._report_error(f"deleting demo backup {backup_id}", error)
            return False

        logger.info("Deleted in-memory demo backup %s", backup_id)
        self._emit_toast("Demo backup removed from the in-memory list", "success")
        return True

    @Slot(str, result=bool)
    def launchGame(self, game_id: str) -> bool:
        game = self._resolve_game(game_id)
        if game is None:
            return False
        if not self._game_actions_allowed(game):
            self._emit_toast(f"Library unavailable for {game.name}", "warning")
            return False
        now = time.monotonic()
        if now - self._last_launch_request.get(game.id, -1e9) < 1.25:
            logger.info("Ignored duplicate launch request for %s", game.id)
            return False
        self._last_launch_request[game.id] = now
        if self._demo_mode:
            logger.info("Demo launch requested for %s; no process was started", game.id)
            self._emit_toast(f"Demo launch requested for {game.name}", "info")
            return True
        try:
            activation = None
            profile = self._mangohud_profile_for_game(game)
            if profile is not None and profile.enabled:
                activation = self._mangohud_launch_integration.prepare(game, profile)
                if not activation.available:
                    raise SteamLaunchError(activation.message)
            command = (
                self._game_launcher.launch(game, activation)
                if activation is not None
                else self._game_launcher.launch(game)
            )
        except SteamLaunchError as error:
            logger.warning("Could not launch %s: %s", game.id, error)
            self._emit_toast(str(error), "error")
            return False
        except Exception as error:
            logger.exception("Unexpected launch error for %s", game.id)
            self._emit_toast(f"Could not start Steam: {error}", "error")
            return False
        logger.info(
            "Started Steam launch for %s using %s MangoHud=%s config=%s",
            game.id,
            command[0],
            bool(profile is not None and profile.enabled),
            activation.config_path if activation is not None else "",
        )
        self._emit_toast(f"Starting {game.name}", "success")
        behavior = self._settings_model.post_launch_behavior
        if behavior is PostLaunchBehavior.MINIMIZE:
            self.windowActionRequested.emit("minimize")
        elif behavior is PostLaunchBehavior.CLOSE:
            self.windowActionRequested.emit("close")
        else:
            self.windowActionRequested.emit("stay")
        return True

    @Slot(str, result="QVariantMap")
    def getMangoHudProfile(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return self._mangohud_error("Select an available Steam game first")
        app_id = str(game.steam_app_id or "").strip()
        if game.launcher is not Launcher.STEAM or not app_id:
            return self._mangohud_error("MangoHud profiles require a Steam AppID")
        try:
            profile = self._mangohud_repository.load(app_id)
            return self._mangohud_profile_to_qml(game, profile)
        except Exception as error:
            logger.warning("Could not load MangoHud profile for %s: %s", game.id, error)
            return self._mangohud_error(str(error), app_id=app_id)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def previewMangoHudProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return self._mangohud_error("MangoHud profiles require a Steam AppID")
        try:
            profile = self._mangohud_profile_from_payload(
                str(game.steam_app_id), values
            )
            result = self._mangohud_profile_to_qml(game, profile)
            result["success"] = True
            return result
        except Exception as error:
            return self._mangohud_error(str(error), app_id=str(game.steam_app_id))

    @Slot(str, "QVariantMap", result="QVariantMap")
    def saveMangoHudProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or game.launcher is not Launcher.STEAM or not game.steam_app_id:
            result = self._mangohud_error("MangoHud profiles require a Steam AppID")
            self._emit_toast(result["error"], "error")
            return result
        app_id = str(game.steam_app_id)
        try:
            previous_profile = self._mangohud_repository.load(app_id)
            profile = self._mangohud_profile_from_payload(app_id, values)
            optimization_profile = self._optimization_profile_repository.load(app_id)
            if self._gamescope_owns_fps_limit(optimization_profile):
                profile = replace(
                    profile,
                    fps_limit=None,
                    fps_limit_method="",
                    updated_at=datetime.now(UTC),
                )
            resolution = self._mangohud_launch_integration.executable_resolver.resolve(
                game, profile.executable_path
            )
            if not profile.executable_path and resolution.reliable and resolution.selected:
                profile = replace(
                    profile, executable_path=resolution.selected.relative_path
                )
            steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
            availability = self._mangohud_detector.detect(steam_type)
            if profile.enabled and not availability.available:
                raise ValueError(availability.message)
            profile_path = self._mangohud_repository.save(profile)
            config_path = self._mangohud_repository.config_path(app_id)
            MangoHudConfigWriter(availability.supported_keys).write(
                profile, config_path
            )
            self._mangohud_launch_integration.synchronize(
                game, profile, previous_profile=previous_profile
            )
        except Exception as error:
            logger.warning("Could not save MangoHud profile for %s: %s", game.id, error)
            result = self._mangohud_error(str(error), app_id=app_id)
            self._emit_toast(f"Could not save MangoHud profile: {error}", "error")
            return result
        logger.info(
            "Saved MangoHud profile appId=%s profile=%s config=%s enabled=%s",
            app_id,
            profile_path,
            config_path,
            profile.enabled,
        )
        self.mangoHudProfileChanged.emit(app_id)
        self._emit_toast("MangoHud profile saved", "success")
        result = self._mangohud_profile_to_qml(game, profile)
        result["success"] = True
        return result

    @Slot(str, result="QVariantMap")
    def resetMangoHudProfile(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return self._mangohud_error("MangoHud profiles require a Steam AppID")
        try:
            self._mangohud_launch_integration.reset(game)
            profile = self._mangohud_repository.reset(game.steam_app_id)
        except Exception as error:
            result = self._mangohud_error(str(error), app_id=str(game.steam_app_id))
            self._emit_toast(f"Could not reset MangoHud profile: {error}", "error")
            return result
        self.mangoHudProfileChanged.emit(profile.app_id)
        self._emit_toast("GameForge MangoHud settings restored", "success")
        result = self._mangohud_profile_to_qml(game, profile)
        result["success"] = True
        return result

    @Slot(str, result=bool)
    def openMangoHudDirectory(self, game_id: str) -> bool:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return False
        directory = self._mangohud_repository.game_directory(game.steam_app_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._emit_toast(f"Could not open MangoHud directory: {error}", "error")
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    @Slot(str, result="QVariantMap")
    def mangoHudLaunchPlan(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return self._mangohud_error("Select an available Steam game first")
        try:
            profile = self._mangohud_profile_for_game(game)
            activation = (
                self._mangohud_launch_integration.prepare(game, profile)
                if profile is not None
                else None
            )
            build_plan = getattr(self._game_launcher, "build_plan", None)
            if not callable(build_plan):
                raise ValueError("The configured Steam launcher cannot build a launch plan")
            plan = build_plan(game, activation)
            result = plan.to_dict()
            result["success"] = True
            return result
        except Exception as error:
            return self._mangohud_error(str(error), app_id=str(game.steam_app_id or ""))

    @staticmethod
    def _local_file_argument(value: str) -> Path:
        text = str(value or "").strip()
        url = QUrl(text)
        if url.scheme():
            if not url.isLocalFile():
                raise ValueError("only local files are supported")
            text = url.toLocalFile()
        if not text:
            raise ValueError("select a local OptiScaler archive")
        return Path(text).expanduser()

    @Slot(str, result="QVariantMap")
    def getOptiScalerStatus(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            return self._optiscaler_service.status(game)
        except Exception as error:
            logger.warning("Could not inspect OptiScaler for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    @Slot(str, str, str, str, result="QVariantMap")
    def inspectOptiScalerArchive(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
    ) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            archive = self._local_file_argument(archive_value)
            return self._optiscaler_service.plan(
                game,
                archive,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
            ).to_dict()
        except Exception as error:
            logger.warning("OptiScaler plan rejected for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def _start_optiscaler_operation(
        self,
        game: Game,
        action: str,
        operation: Callable[
            [Event, Callable[[str, float], None]], OptiScalerProfile
        ],
    ) -> bool:
        if any(
            stored_game_id == game.id and not future.done()
            for future, _cancel, stored_game_id in self._optiscaler_jobs.values()
        ):
            self._emit_toast("An OptiScaler task for this game is already active", "warning")
            return False
        task_id = f"optiscaler-{action.casefold()}-{uuid4().hex}"
        cancel_event = Event()
        timestamp = datetime.now(UTC).isoformat()
        self._operational_tasks[task_id] = self._operational_task(
            task_id=task_id,
            title=f"OptiScaler: {action} - {game.name}",
            operation="OptiScaler",
            status="queued",
            progress=0.0,
            game_id=game.id,
            game_name=game.name,
            created_at=timestamp,
        )
        self._operational_tasks[task_id]["cancellable"] = True
        self._operational_tasks[task_id]["stage"] = "Queued"

        def report(stage: str, value: float) -> None:
            self._optiscaler_events.put((task_id, str(stage), float(value)))

        future = self._optiscaler_executor.submit(operation, cancel_event, report)
        self._optiscaler_jobs[task_id] = (future, cancel_event, game.id)
        self._reload_tasks()
        self._emit_toast(f"OptiScaler {action.casefold()} started", "info")
        return True

    @Slot(str, str, str, str, bool, result=bool)
    def installOptiScaler(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
        allow_replace_conflicts: bool,
    ) -> bool:
        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        try:
            archive = self._local_file_argument(archive_value)
        except ValueError as error:
            self._emit_toast(str(error), "error")
            return False
        return self._start_optiscaler_operation(
            game,
            "Install",
            lambda cancelled, progress: self._optiscaler_service.install(
                game,
                archive,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                allow_replace_conflicts=bool(allow_replace_conflicts),
                cancel_event=cancelled,
                progress=progress,
            ),
        )

    @Slot(str, result=bool)
    def removeOptiScaler(self, game_id: str) -> bool:
        game = self._resolve_game(game_id, show_error=False)
        return bool(
            game is not None
            and self._start_optiscaler_operation(
                game,
                "Remove",
                lambda cancelled, progress: self._optiscaler_service.remove(
                    game, cancel_event=cancelled, progress=progress
                ),
            )
        )

    @Slot(str, result=bool)
    def restoreOptiScalerFiles(self, game_id: str) -> bool:
        game = self._resolve_game(game_id, show_error=False)
        return bool(
            game is not None
            and self._start_optiscaler_operation(
                game,
                "Restore",
                lambda cancelled, progress: self._optiscaler_service.restore(
                    game, cancel_event=cancelled, progress=progress
                ),
            )
        )

    @Slot(str, result="QVariantMap")
    def verifyOptiScaler(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            profile = self._optiscaler_service.verify(game)
            result = self._optiscaler_service.status(game)
            self.optiScalerChanged.emit(profile.app_id)
            return result
        except Exception as error:
            return {"success": False, "error": str(error)}

    @Slot(str, result=bool)
    def openOptiScalerDirectory(self, game_id: str) -> bool:
        status = self.getOptiScalerStatus(game_id)
        directory = Path(str(status.get("installDirectory", "")))
        return bool(
            status.get("success")
            and directory.is_dir()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        )

    @Slot(str, result=bool)
    def openOptiScalerManifest(self, game_id: str) -> bool:
        status = self.getOptiScalerStatus(game_id)
        manifest = Path(str(status.get("manifestPath", "")))
        return bool(
            status.get("success")
            and manifest.is_file()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(manifest)))
        )

    @Slot(str, "QVariantMap", result=str)
    def buildLaunchPreview(self, game_id: str, options: Mapping[str, Any]) -> str:
        """Build display-only Steam launch text through the demo provider."""

        game = self._resolve_game(game_id, show_error=False)
        if game is None:
            return "%command%"
        try:
            normalized_options = self._optimization_options(options)
            preview = self._provider_launch_preview(game, normalized_options)
        except Exception as error:
            self._report_error(f"building launch preview for {game.name}", error)
            return "%command%"

        logger.debug("Generated display-only launch preview for %s", game.id)
        return preview

    @Slot(str, result=bool)
    def playUiSound(self, kind: str) -> bool:
        if self._interface_mode == "couch":
            return False
        return self._ui_sound_service.play(kind)

    @Slot(str, result="QVariantMap")
    def optimizationDefaults(self, profile: str) -> dict[str, Any]:
        """Return provider-owned defaults for an optimization profile."""

        try:
            normalized_profile = self._coerce_enum(OptimizationProfile, profile)
            options = self._optimization_provider.defaults_for(normalized_profile)
        except Exception as error:
            self._report_error(f"loading optimization profile {profile}", error)
            return {}

        return {
            "gamemode": options.gamemode,
            "gamescope": options.gamescope,
            "mangohud": options.mangohud,
            "fpsLimit": options.fps_limit,
            "adaptiveSync": options.adaptive_sync,
            "cursorGrab": options.cursor_grab,
            "cpuPerformanceProfile": options.cpu_performance_profile,
            "memoryMonitoring": options.memory_monitoring,
            "optiscaler": options.optiscaler,
        }

    @Slot(str, result="QVariantMap")
    def getOptimizationProfile(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or game.launcher is not Launcher.STEAM or not game.steam_app_id:
            return {"success": False, "error": "Optimization profiles require a Steam AppID"}
        try:
            profile = self._optimization_profile_repository.load(game.steam_app_id)
            return self._optimization_profile_to_qml(profile)
        except Exception as error:
            logger.warning("Could not load optimization profile for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    @Slot(str, "QVariantMap", result="QVariantMap")
    def previewOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return {"success": False, "error": "Optimization profiles require a Steam AppID"}
        try:
            profile = self._optimization_profile_from_payload(str(game.steam_app_id), values)
            return self._optimization_profile_to_qml(profile)
        except Exception as error:
            return {"success": False, "error": str(error)}

    @Slot(str, "QVariantMap", result="QVariantMap")
    def saveOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or game.launcher is not Launcher.STEAM or not game.steam_app_id:
            return {"success": False, "error": "Optimization profiles require a Steam AppID"}
        try:
            previous_profile = self._optimization_profile_repository.load(
                str(game.steam_app_id)
            )
            profile = self._optimization_profile_from_payload(str(game.steam_app_id), values)
            display = self._optimization_display_for(profile.target_display_id)
            recommendation = self._optimization_advisor.recommend(profile, display)
            profile = replace(
                profile,
                target_fps=(
                    recommendation.target_fps
                    if profile.target_fps_mode == "automatic"
                    else profile.target_fps
                ),
                last_recommendation=recommendation.to_dict(),
                updated_at=datetime.now(UTC),
            )
            gamemode, gamescope = self._runtime_tool_detector.detect()
            if profile.gamemode_enabled and not gamemode.available:
                raise ValueError(gamemode.message)
            if profile.gamescope_enabled and not gamescope.available:
                raise ValueError(gamescope.message)
            path = self._optimization_profile_repository.save(profile)
            try:
                if self._gamescope_owns_fps_limit(profile):
                    self._clear_mangohud_fps_limit(game)
            except Exception:
                self._optimization_profile_repository.save(previous_profile)
                raise
            result = self._optimization_profile_to_qml(profile)
            result.update({"success": True, "profilePath": str(path)})
            self._emit_toast("Optimization profile saved", "success")
            return result
        except Exception as error:
            self._emit_toast(f"Could not save optimization profile: {error}", "error")
            return {"success": False, "error": str(error)}

    @Slot(str, result="QVariantMap")
    def testGameForgeRunner(self, game_id: str) -> dict[str, Any]:
        game = self._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return {"success": False, "message": "Optimization profiles require a Steam AppID"}
        result = self._runner_integration.test(game.steam_app_id)
        self._emit_toast(
            str(result.get("message", "Runner test completed")),
            "success" if result.get("success") else "warning",
        )
        return result

    @Slot(str)
    @Slot(str, str)
    def showToast(self, message: str, level: str = "info") -> None:
        self._emit_toast(message, level)

    @Slot()
    def shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        if hasattr(self, "_task_timer") and self._task_timer.isActive():
            self._task_timer.stop()
        for timer_name in ("_scan_debounce_timer", "_ignored_scan_event_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None and timer.isActive():
                timer.stop()
        if hasattr(self, "_gamepad_service"):
            self._gamepad_service.stop()
        if hasattr(self, "_ui_sound_service"):
            self._ui_sound_service.stop()
        if hasattr(self, "_library_scanner"):
            try:
                self._library_scanner.shutdown(timeout_ms=2000)
            except Exception:
                logger.exception("Could not stop library workers cleanly")
        update_jobs = getattr(self, "_update_jobs", {})
        for future, cancel_event in tuple(update_jobs.values()):
            cancel_event.set()
            future.cancel()
        update_executor = getattr(self, "_update_executor", None)
        if update_jobs:
            try:
                wait_for_futures(
                    tuple(future for future, _event in update_jobs.values()),
                    timeout=2.0,
                )
            except Exception:
                logger.exception("Could not wait for update scanners cleanly")
        if update_executor is not None:
            try:
                update_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.exception("Could not stop the update scanner executor")
        self._update_jobs = {}
        optiscaler_jobs = getattr(self, "_optiscaler_jobs", {})
        for future, cancel_event, _game_id in tuple(optiscaler_jobs.values()):
            cancel_event.set()
            future.cancel()
        if optiscaler_jobs:
            try:
                wait_for_futures(
                    tuple(future for future, _event, _game_id in optiscaler_jobs.values()),
                    timeout=2.0,
                )
            except Exception:
                logger.exception("Could not wait for OptiScaler tasks cleanly")
        optiscaler_executor = getattr(self, "_optiscaler_executor", None)
        if optiscaler_executor is not None:
            try:
                optiscaler_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.exception("Could not stop the OptiScaler executor")
        self._optiscaler_jobs = {}
        shutdown_tasks = getattr(self._task_service, "shutdown", None)
        if callable(shutdown_tasks):
            try:
                shutdown_tasks(wait=True, timeout=2.0)
            except TypeError:
                try:
                    shutdown_tasks(wait=False)
                except TypeError:
                    try:
                        shutdown_tasks()
                    except Exception:
                        logger.exception("Could not stop task workers cleanly")
                except Exception:
                    logger.exception("Could not stop task workers cleanly")
            except Exception:
                logger.exception("Could not stop task workers cleanly")
        compression_service = self._compression_service
        if compression_service is not None:
            try:
                compression_service.shutdown()
            except Exception:
                logger.exception("Could not stop the compression provider cleanly")
        self._save_library_cache()
        logger.info("Stopped GameForge controller")

    def _configure_gamepad_service(self) -> None:
        self._gamepad_service.configure(
            deadzone=float(self._settings_model.analog_deadzone),
            repeat_delay_ms=int(self._settings_model.navigation_repeat_delay_ms),
            repeat_rate_ms=int(self._settings_model.navigation_repeat_rate_ms),
            swap_accept_back=bool(self._settings_model.swap_accept_back),
        )

    def _add_gamepad_system_info(self) -> None:
        active = dict(self._gamepad_service.activeController)
        self._system_info.update(
            {
                "sdl3Status": str(self._gamepad_service.status),
                "gamepadAvailable": bool(self._gamepad_service.available),
                "controllerCount": int(self._gamepad_service.controllerCount),
                "activeController": active,
                "activeControllerName": str(active.get("name", "")),
                "activeControllerType": str(active.get("type", "")),
                "activeControllerMapping": str(active.get("mappingStatus", "")),
            }
        )
        capabilities = self._system_info.get("capabilities")
        if isinstance(capabilities, Mapping):
            updated_capabilities = dict(capabilities)
        else:
            updated_capabilities = {}
        updated_capabilities["SDL3"] = str(self._gamepad_service.status)
        self._system_info["capabilities"] = updated_capabilities

    @Slot()
    def _on_gamepad_availability_changed(self) -> None:
        if not self._gamepad_service.available and self._interface_mode == "couch":
            self._set_interface_mode("desktop")
            self._emit_toast(
                "Controller support is unavailable; Desktop Mode is active",
                "warning",
            )
        self.gamepadAvailableChanged.emit()
        self._add_gamepad_system_info()
        self.systemInfoChanged.emit()

    @Slot()
    def _on_gamepad_controllers_changed(self) -> None:
        self.controllersChanged.emit()
        self._add_gamepad_system_info()
        self.systemInfoChanged.emit()

    @Slot()
    def _on_active_controller_changed(self) -> None:
        self.activeControllerChanged.emit()
        self._add_gamepad_system_info()
        self.systemInfoChanged.emit()

    @Slot(object)
    def _on_controller_connected(self, device: object) -> None:
        self._couch_navigation.setControllerConnected(True)
        if isinstance(device, Mapping):
            name = str(device.get("name") or "Controller")
        else:
            name = "Controller"
        self._emit_toast(f"Controller connected: {name}", "info")

    @Slot(str)
    def _on_controller_disconnected(self, name: str) -> None:
        self._couch_navigation.setControllerConnected(
            self._gamepad_service.controllerCount > 0
        )
        self._emit_toast(f"Controller disconnected: {name}", "warning")

    @Slot(str)
    def _on_gamepad_activity(self, action: str) -> None:
        if (
            self._settings_model.controller_mode is ControllerMode.AUTOMATIC
            and self._interface_mode == "desktop"
        ):
            self._set_interface_mode("couch")
            self._consume_gamepad_action = str(action)

    @Slot(str)
    def _on_gamepad_action(self, action: str) -> None:
        if self._consume_gamepad_action and action == self._consume_gamepad_action:
            self._consume_gamepad_action = ""
            return
        if action in {"ToggleMode", "ToggleDesktopCouch"}:
            self.toggleInterfaceMode()
            return
        if self._interface_mode == "desktop":
            return
        self._couch_navigation.dispatch(str(action))
        self.gamepadAction.emit(str(action))

    def _set_interface_mode(self, mode: str) -> None:
        normalized = "couch" if mode == "couch" else "desktop"
        if normalized == self._interface_mode:
            return
        self._interface_mode = normalized
        if normalized == "couch":
            self._ui_sound_service.stop()
        self.interfaceModeChanged.emit()
        logger.info("Interface mode changed to %s", normalized)

    def _create_steam_provider(self) -> GameProviderLike:
        """Build Linux read-only integrations lazily for normal operation."""

        # Imported only outside Demo mode so GUI fixtures have no host coupling.
        from ..providers.linux_filesystem import LinuxFilesystemProvider
        from ..providers.steam import SteamGameProvider
        from ..services.directory_size import DirectorySizeScanner

        if self._filesystem_provider is None:
            self._filesystem_provider = cast(
                FilesystemProviderLike,
                LinuxFilesystemProvider(),
            )
        if self._directory_size_scanner is None:
            self._directory_size_scanner = cast(
                DirectorySizeScannerLike,
                DirectorySizeScanner(),
            )
        return cast(
            GameProviderLike,
            SteamGameProvider(
                self._filesystem_provider,
                additional_roots=self._settings_model.steam_installation_directories,
            ),
        )

    def _initial_games(self, initial_games: Sequence[Game] | None) -> list[Game]:
        if initial_games is not None:
            return [game for game in initial_games if isinstance(game, Game)]
        if self._library_cache is not None and not self._demo_mode:
            try:
                cached_games = list(self._library_cache.load())
            except Exception as error:
                logger.warning("Could not load the Steam library cache: %s", error)
            else:
                if cached_games:
                    logger.info(
                        "Loaded %d games from the library cache",
                        len(cached_games),
                    )
                    return cached_games
        try:
            return list(self._game_provider.list_games())
        except Exception as error:
            logger.warning("Could not read initial provider games: %s", error)
            return []

    @Slot(int)
    def _on_library_scan_started(self, generation: int) -> None:
        self._active_scan_generation = generation
        if not self._demo_mode:
            now = datetime.now(UTC).isoformat()
            self._operational_tasks = {
                self._scan_task_id(generation): self._operational_task(
                    task_id=self._scan_task_id(generation),
                    title="Scan Steam libraries",
                    operation="Library scan",
                    status="running",
                    progress=0.0,
                    created_at=now,
                )
            }
            self._reload_tasks()
        self._set_scan_state(
            status="scanning",
            message=(
                "Refreshing demonstration library"
                if self._demo_mode
                else "Scanning local Steam libraries…"
            ),
            is_scanning=True,
        )

    @Slot(int, object)
    def _on_library_ready(self, generation: int, raw_games: object) -> None:
        if generation != self._active_scan_generation or self._shutdown_requested:
            return
        games = [
            game
            for game in (raw_games if isinstance(raw_games, Sequence) else ())
            if isinstance(game, Game)
        ]
        steam_found = False if self._demo_mode else self._provider_steam_found(games)

        if not self._demo_mode:
            self._update_operational_task(
                self._scan_task_id(generation),
                progress=0.35 if games and self._directory_size_scanner is not None else 1.0,
                status=(
                    "running"
                    if games and self._directory_size_scanner is not None
                    else "completed"
                ),
            )

        if self._directory_size_scanner is not None and not self._demo_mode:
            games = [
                replace(
                    game,
                    size_scan_status=SizeScanStatus.CALCULATING,
                    size_scan_error=None,
                )
                if game.status is not GameStatus.MISSING_FILES
                else game
                for game in games
            ]

        if not self._demo_mode:
            games = self._merge_unavailable_cached_games(games)

        self._set_domain_games(
            games,
            reason="library_scan_inventory",
            publish=False,
        )
        self._reload_system_info()
        self._set_scan_state(
            message=(
                f"Found {len(games)} games; calculating exact disk usage…"
                if games and self._directory_size_scanner is not None
                else f"Found {len(games)} games"
            ),
            steam_found=steam_found,
        )
        self._add_steam_system_info()
        self.systemInfoChanged.emit()
        logger.info("Library scan returned %d games", len(games))

    @Slot(int, str)
    def _on_library_failed(self, generation: int, message: str) -> None:
        if generation != self._active_scan_generation or self._shutdown_requested:
            return
        logger.error("Library scan generation %d failed: %s", generation, message)
        readable = str(message).strip() or "unknown provider error"
        if not self._demo_mode:
            self._update_operational_task(
                self._scan_task_id(generation),
                progress=1.0,
                status="failed",
                error=readable,
            )
        self._set_scan_state(
            status="error",
            message=f"Steam library scan failed: {readable}",
        )
        self._emit_toast(
            "Steam library scan failed; cached games remain available",
            "error",
        )

    @Slot(int, str)
    def _on_game_size_started(self, generation: int, game_id: str) -> None:
        if generation != self._active_scan_generation or self._shutdown_requested:
            return
        game = self._domain_games.get(game_id)
        if game is None or self._demo_mode:
            return
        task_id = self._size_task_id(generation, game_id)
        self._operational_tasks[task_id] = self._operational_task(
            task_id=task_id,
            title=f"Calculate size: {game.name}",
            operation="Size calculation",
            status="running",
            progress=0.0,
            game_id=game.id,
            game_name=game.name,
        )
        self._reload_tasks()

    @Slot(int, str, object)
    def _on_game_size_ready(
        self,
        generation: int,
        game_id: str,
        result: object,
    ) -> None:
        if generation != self._active_scan_generation or self._shutdown_requested:
            return
        game = self._domain_games.get(game_id)
        if game is None:
            return
        try:
            logical_size_gb, physical_size_gb = self._size_result_gb(result)
            if isinstance(result, Mapping):
                complete = result.get("complete", True)
                errors = result.get("errors", ())
            else:
                complete = getattr(result, "complete", True)
                errors = getattr(result, "errors", ())
            error_values = [str(error) for error in errors if str(error)]
            error_text = "; ".join(error_values[:3])
            if len(error_values) > 3:
                error_text += f"; and {len(error_values) - 3} more errors"
            if not bool(complete) and not error_text:
                error_text = "directory changed or could not be read completely"
            if error_text:
                logger.warning(
                    "Exact size scan for %s was incomplete: %s",
                    game_id,
                    error_text,
                )
            updater = getattr(self._game_provider, "update_game_sizes", None)
            provider_game = None
            if callable(updater):
                provider_game = updater(
                    game_id,
                    logical_size_gb,
                    physical_size_gb,
                    error=error_text or None,
                )
            updated = (
                provider_game
                if isinstance(provider_game, Game)
                else replace(
                    game,
                    logical_size_gb=logical_size_gb,
                    physical_size_gb=physical_size_gb,
                    size_scan_status=(
                        SizeScanStatus.COMPLETED
                        if bool(complete) and not error_text
                        else SizeScanStatus.FAILED
                    ),
                    size_scan_error=error_text or None,
                )
            )
        except Exception as error:
            self._on_game_size_failed(generation, game_id, str(error))
            return
        self._domain_games[game_id] = updated
        if not self._demo_mode:
            self._update_operational_task(
                self._size_task_id(generation, game_id),
                progress=1.0,
                status=(
                    "completed"
                    if updated.size_scan_status is SizeScanStatus.COMPLETED
                    else "failed"
                ),
                error=updated.size_scan_error or "",
            )
        # Publishing one QVariant-list snapshot per completed game destroys
        # every QML Repeater delegate. Keep the authoritative domain update,
        # but publish the complete snapshot once in _on_library_scan_finished.

    @Slot(int, str, str)
    def _on_game_size_failed(
        self,
        generation: int,
        game_id: str,
        message: str,
    ) -> None:
        if generation != self._active_scan_generation or self._shutdown_requested:
            return
        game = self._domain_games.get(game_id)
        if game is None:
            return
        readable = str(message).strip() or "directory could not be read"
        logger.warning("Exact size scan failed for %s: %s", game_id, readable)
        self._domain_games[game_id] = replace(
            game,
            size_scan_status=SizeScanStatus.FAILED,
            size_scan_error=readable,
        )
        if not self._demo_mode:
            self._update_operational_task(
                self._size_task_id(generation, game_id),
                progress=1.0,
                status="failed",
                error=readable,
            )
        # See _on_game_size_ready: a failed size result is included in the
        # single final library snapshot instead of rebuilding all delegates.

    @Slot(int)
    def _on_library_scan_finished(self, generation: int) -> None:
        if generation != self._active_scan_generation or self._shutdown_requested:
            return
        self._scan_worker_active = False
        if self._library_scan_status == "error":
            self._set_scan_state(is_scanning=False)
            self._schedule_coalesced_scan_retry()
            return

        if not self._demo_mode:
            self._update_operational_task(
                self._scan_task_id(generation),
                progress=1.0,
                status="completed",
            )

        failed_sizes = sum(
            game.size_scan_status is SizeScanStatus.FAILED
            for game in self._domain_games.values()
        )
        if self._demo_mode:
            status = "demo"
            message = f"Demo library ready · {len(self._domain_games)} games"
        elif not self._steam_found:
            status = "steam-not-found"
            message = "Steam was not found in standard or configured locations"
        elif not self._domain_games:
            status = "empty"
            message = "Steam was found, but no installed games were detected"
        else:
            status = "ready"
            message = f"Steam library ready · {len(self._domain_games)} games"
            if failed_sizes:
                message += f" · {failed_sizes} size scans unavailable"
        self._set_scan_state(
            status=status,
            message=message,
            is_scanning=False,
        )
        self._reload_games(reason="library_scan_finished")
        self._save_library_cache()
        self._reload_updates()
        self._schedule_update_observations()
        self._schedule_coalesced_scan_retry()

    def _schedule_coalesced_scan_retry(self) -> None:
        if not self._scan_retry_pending or self._shutdown_requested:
            return
        self._scan_retry_pending = False
        self._set_scan_state(
            status="scan-queued",
            message="A coalesced Steam library refresh is queued",
            is_scanning=True,
        )
        if not self._scan_debounce_timer.isActive():
            self._scan_debounce_timer.start()

    def _set_scan_state(
        self,
        *,
        status: str | None = None,
        message: str | None = None,
        steam_found: bool | None = None,
        is_scanning: bool | None = None,
    ) -> None:
        if status is not None and status != self._library_scan_status:
            self._library_scan_status = status
            self.libraryScanStatusChanged.emit()
        if message is not None and message != self._library_scan_message:
            self._library_scan_message = message
            self.libraryScanMessageChanged.emit()
        if steam_found is not None and steam_found != self._steam_found:
            self._steam_found = steam_found
            self.steamFoundChanged.emit()
        if is_scanning is not None and is_scanning != self._is_scanning:
            self._is_scanning = is_scanning
            self.isScanningChanged.emit()

    def _set_domain_games(
        self,
        games: Sequence[Game],
        *,
        reason: str = "domain_games_replaced",
        publish: bool = True,
    ) -> None:
        self._artwork_resolver.invalidate()
        self._domain_games = {
            game.id: self._resolve_game_artwork(game)
            for game in games
            if not self._game_is_in_ignored_library(game)
        }
        if publish:
            self._reload_games(reason=reason)
        else:
            logger.info(
                "Games snapshot prepared without model commit: reason=%s games=%d",
                reason,
                len(self._domain_games),
            )

    def _artwork_roots(self) -> tuple[Path, ...]:
        """Return local Steam roots without depending on a game install path."""

        values: list[Path] = []
        configured = getattr(self._game_provider, "configured_roots", ())
        if callable(configured):
            try:
                configured = configured()
            except Exception:
                configured = ()
        if isinstance(configured, Sequence) and not isinstance(
            configured, (str, bytes, bytearray)
        ):
            values.extend(Path(value) for value in configured)
        report = getattr(self._game_provider, "last_report", None)
        raw_report_roots = (
            report.get("steam_roots", ())
            if isinstance(report, Mapping)
            else getattr(report, "steam_roots", ())
            if report is not None
            else ()
        )
        if isinstance(raw_report_roots, Sequence) and not isinstance(
            raw_report_roots, (str, bytes, bytearray)
        ):
            values.extend(Path(value) for value in raw_report_roots)
        values.extend(Path(value) for value in self._settings_model.steam_installation_directories)
        unique: dict[str, Path] = {}
        for value in values:
            try:
                key = os.path.normcase(os.path.abspath(os.fspath(value)))
            except (OSError, TypeError, ValueError):
                continue
            unique.setdefault(key, value)
        return tuple(unique.values())

    def _resolve_game_artwork(self, game: Game) -> Game:
        return self._artwork_resolver.resolve(game, self._artwork_roots()).game

    def _present_game(self, game: Game, **kwargs: Any) -> dict[str, Any]:
        """Use the exact same artwork resolution for Games, Tasks and Updates."""

        resolved = self._resolve_game_artwork(game)
        if hasattr(self, "_domain_games") and self._domain_games.get(game.id) != resolved:
            self._domain_games[game.id] = resolved
        presented = game_to_qml(resolved, **kwargs)
        effective_url = str(presented.get("effectiveArtworkUrl") or "")
        if self._effective_artwork_urls.get(game.id) != effective_url:
            self._effective_artwork_urls[game.id] = effective_url
            logger.info(
                "Artwork presentation change: gameId=%s effectiveArtworkUrl=%s "
                "reason=%s",
                game.id,
                effective_url,
                "local_image" if effective_url else "placeholder_no_effective_url",
            )
        return presented

    def _merge_unavailable_cached_games(
        self,
        discovered_games: Sequence[Game],
    ) -> list[Game]:
        """Retain only cached games whose Steam library is known unavailable.

        A normal refresh remains authoritative for accessible libraries, so an
        uninstalled game is not resurrected from cache.  The exception is a
        library path explicitly reported as inaccessible by the provider.
        """

        merged = {game.id: game for game in discovered_games}
        inaccessible = self._provider_inaccessible_paths()
        configured = self._provider_configured_library_paths()
        retained = 0
        for game in self._domain_games.values():
            if game.launcher is not Launcher.STEAM:
                continue
            if game.id in merged:
                merged[game.id] = self._preserve_cached_artwork(
                    merged[game.id], game
                )
                continue
            belongs_to_inaccessible = any(
                self._path_is_within_library(game, root) for root in inaccessible
            )
            still_configured = any(
                self._path_is_within_library(game, root) for root in configured
            )
            if not belongs_to_inaccessible or not still_configured:
                continue
            merged[game.id] = replace(
                game,
                status=GameStatus.DRIVE_DISCONNECTED,
                compression_available=False,
                library_available=False,
                is_writable=False,
                size_scan_status=SizeScanStatus.NOT_REQUESTED,
                size_scan_error=None,
            )
            retained += 1
        if retained:
            logger.info(
                "Retained %d cached games from unavailable Steam libraries",
                retained,
            )
        self._log_library_decisions(discovered_games)
        return list(merged.values())

    @staticmethod
    def _preserve_cached_artwork(discovered: Game, cached: Game) -> Game:
        """Keep valid local artwork when a refresh temporarily finds none."""

        def cached_file(current: Path | None, previous: Path | None) -> Path | None:
            if current is not None:
                return current
            if previous is None:
                return None
            try:
                return previous if previous.is_file() else None
            except OSError:
                return None

        portrait = cached_file(
            discovered.portrait_artwork_path, cached.portrait_artwork_path
        )
        header = cached_file(
            discovered.header_artwork_path, cached.header_artwork_path
        )
        fallback = cached_file(
            discovered.fallback_artwork_path, cached.fallback_artwork_path
        )
        legacy = discovered.cover_asset
        if not legacy and cached.cover_asset:
            try:
                if Path(cached.cover_asset).is_file():
                    legacy = cached.cover_asset
            except OSError:
                pass
        if (
            portrait == discovered.portrait_artwork_path
            and header == discovered.header_artwork_path
            and fallback == discovered.fallback_artwork_path
            and legacy == discovered.cover_asset
        ):
            return discovered
        return replace(
            discovered,
            portrait_artwork_path=portrait,
            header_artwork_path=header,
            fallback_artwork_path=fallback,
            cover_asset=legacy,
        )

    def _provider_accessible_library_paths(self) -> tuple[Path, ...]:
        report = getattr(self._game_provider, "last_report", None)
        if report is None:
            return ()
        raw_paths = (
            report.get("libraries", ())
            if isinstance(report, Mapping)
            else getattr(report, "libraries", ())
        )
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return ()
        result: list[Path] = []
        for raw_path in raw_paths:
            try:
                result.append(Path(os.fspath(raw_path)))
            except TypeError:
                continue
        return tuple(result)

    def _log_library_decisions(self, discovered_games: Sequence[Game]) -> None:
        """Explain which Steam library evidence controlled the active model."""

        configured = self._provider_configured_library_paths()
        accessible = self._provider_accessible_library_paths()
        cached_games = tuple(
            game
            for game in self._domain_games.values()
            if game.launcher is Launcher.STEAM and game.library_path is not None
        )
        current_games = tuple(
            game
            for game in discovered_games
            if game.launcher is Launcher.STEAM and game.library_path is not None
        )
        libraries: list[Path] = []
        for path in (
            *configured,
            *accessible,
            *(game.library_path for game in cached_games),
            *(game.library_path for game in current_games),
        ):
            if path is None or any(self._same_path(path, known) for known in libraries):
                continue
            libraries.append(path)

        for library in libraries:
            is_configured = any(
                self._same_path(library, path) for path in configured
            )
            is_available = any(
                self._same_path(library, path) for path in accessible
            )
            matching_games = [
                game
                for game in (*current_games, *cached_games)
                if game.library_path is not None
                and self._same_path(game.library_path, library)
            ]
            filesystem = next(
                (
                    game.filesystem_name or game.filesystem.value
                    for game in matching_games
                    if game.filesystem_name or game.filesystem.value
                ),
                "unknown",
            )
            if is_configured and is_available:
                decision = "active"
            elif is_configured:
                decision = "disconnected"
            else:
                try:
                    exists = library.exists()
                except OSError:
                    exists = False
                decision = "orphaned" if exists else "removed"
            logger.info(
                "Steam library diagnostic: path=%s source=%s available=%s "
                "filesystem=%s decision=%s",
                library,
                "libraryfolders.vdf" if is_configured else "cache",
                str(is_available).lower(),
                filesystem,
                decision,
            )

    def _provider_inaccessible_paths(self) -> tuple[Path, ...]:
        report = getattr(self._game_provider, "last_report", None)
        if report is None:
            return ()
        raw_paths = (
            report.get("inaccessible_paths", ())
            if isinstance(report, Mapping)
            else getattr(report, "inaccessible_paths", ())
        )
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return ()
        paths: list[Path] = []
        for raw_path in raw_paths:
            try:
                paths.append(Path(os.fspath(raw_path)))
            except TypeError:
                continue
        return tuple(paths)

    def _provider_configured_library_paths(self) -> tuple[Path, ...]:
        report = getattr(self._game_provider, "last_report", None)
        if report is None:
            return ()
        missing = object()
        raw_paths = (
            report.get("configured_libraries", missing)
            if isinstance(report, Mapping)
            else getattr(report, "configured_libraries", missing)
        )
        # Compatibility for injected providers which predate this diagnostic:
        # their explicit inaccessible paths are the only available evidence.
        if raw_paths is missing:
            return self._provider_inaccessible_paths()
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return ()
        paths: list[Path] = []
        for raw_path in raw_paths:
            try:
                paths.append(Path(os.fspath(raw_path)))
            except TypeError:
                continue
        return tuple(paths)

    @staticmethod
    def _same_path(first: Path, second: Path) -> bool:
        try:
            return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
                os.path.abspath(second)
            )
        except (OSError, TypeError, ValueError):
            return False

    @staticmethod
    def _canonical_library_path(value: str | Path) -> Path | None:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            return Path(os.path.realpath(os.path.abspath(os.fspath(Path(raw).expanduser()))))
        except (OSError, TypeError, ValueError):
            return None

    def _library_is_ignored(self, value: str | Path) -> bool:
        normalized = self._canonical_library_path(value)
        return bool(
            normalized is not None
            and any(
                self._same_path(normalized, ignored)
                for ignored in self._settings_model.ignored_steam_libraries
            )
        )

    def _game_is_in_ignored_library(self, game: Game) -> bool:
        candidate = game.library_path
        return bool(candidate is not None and self._library_is_ignored(candidate))

    def _update_record_is_in_ignored_library(
        self,
        record: GameUpdateRecord,
    ) -> bool:
        game = self._domain_games.get(record.game_id)
        if game is not None:
            return self._game_is_in_ignored_library(game)
        for observation in (
            record.pending_observation,
            record.current_observation,
            record.compression_observation,
        ):
            if observation is not None and observation.library_path:
                return self._library_is_ignored(observation.library_path)
        return False

    @staticmethod
    def _path_is_within_library(game: Game, library: Path) -> bool:
        candidate = game.library_path or game.install_path
        return AppController._path_is_within_library_path(candidate, library)

    @staticmethod
    def _path_is_within_library_path(candidate: str | Path, library: Path) -> bool:
        try:
            candidate_text = os.path.normcase(os.path.abspath(os.fspath(candidate)))
            library_text = os.path.normcase(os.path.abspath(os.fspath(library)))
            return os.path.commonpath((candidate_text, library_text)) == library_text
        except (OSError, TypeError, ValueError):
            return False

    @staticmethod
    def _game_actions_allowed(game: Game) -> bool:
        return bool(
            game.library_available
            and game.status
            not in {GameStatus.DRIVE_DISCONNECTED, GameStatus.MISSING_FILES}
        )

    def _save_library_cache(self) -> None:
        if self._library_cache is None or self._demo_mode:
            return
        try:
            self._library_cache.save(tuple(self._domain_games.values()))
        except Exception as error:
            logger.warning("Could not save the Steam library cache: %s", error)

    def _provider_steam_found(self, games: Sequence[Game]) -> bool:
        if games:
            return True
        direct = getattr(self._game_provider, "steam_found", None)
        if isinstance(direct, bool):
            return direct
        report = getattr(self._game_provider, "last_report", None)
        if report is None:
            return False
        if isinstance(report, Mapping):
            getter = report.get
        else:
            getter = lambda name, default=None: getattr(report, name, default)
        for name in ("steam_found", "installation_found", "found"):
            value = getter(name, None)
            if isinstance(value, bool):
                return value
        for name in (
            "installations",
            "steam_roots",
            "installation_roots",
            "detected_roots",
        ):
            value = getter(name, None)
            if value:
                return True
        for name in ("installation_count", "installations_found"):
            value = getter(name, None)
            if isinstance(value, int):
                return value > 0
        return False

    @staticmethod
    def _size_result_gb(result: object) -> tuple[float, float]:
        if isinstance(result, Mapping):
            getter = result.get
        else:
            getter = lambda name, default=None: getattr(result, name, default)

        logical_gb = getter("logical_size_gb", None)
        physical_gb = getter("physical_size_gb", None)
        if logical_gb is not None and physical_gb is not None:
            return max(0.0, float(logical_gb)), max(0.0, float(physical_gb))

        def first(names: Sequence[str]) -> Any:
            for name in names:
                value = getter(name, None)
                if value is not None:
                    return value
            return None

        logical_bytes = first(
            (
                "logical_bytes",
                "logical_size_bytes",
                "total_logical_bytes",
                "apparent_size_bytes",
            )
        )
        physical_bytes = first(
            (
                "physical_bytes",
                "physical_size_bytes",
                "allocated_bytes",
                "disk_usage_bytes",
            )
        )
        if logical_bytes is None or physical_bytes is None:
            if isinstance(result, Sequence) and len(result) >= 2:
                logical_bytes, physical_bytes = result[0], result[1]
            else:
                raise ValueError("directory scanner returned no size values")
        divisor = float(1024**3)
        return (
            max(0.0, float(logical_bytes) / divisor),
            max(0.0, float(physical_bytes) / divisor),
        )

    @staticmethod
    def _build_application_update_info() -> dict[str, Any]:
        """Describe who owns application updates without attempting self-update."""

        if os.environ.get("FLATPAK_ID", "").strip():
            installation_type = "flatpak"
            message_key = "flatpak"
            message = "Application updates are delivered through Flatpak."
        elif os.environ.get("SNAP", "").strip():
            installation_type = "system package"
            message_key = "system"
            message = "Application updates are delivered through the package manager."
        else:
            repository_root = Path(__file__).resolve().parents[3]
            if (repository_root / "pyproject.toml").is_file():
                installation_type = "development"
                message_key = "development"
                message = (
                    "This development checkout does not update itself; "
                    "use the repository workflow."
                )
            else:
                installation_type = "system package"
                message_key = "system"
                message = "Application updates are delivered through the package manager."
        return {
            "version": APP_VERSION,
            "installationType": installation_type,
            "installation_type": installation_type,
            "selfUpdateAvailable": False,
            "self_update_available": False,
            "messageKey": message_key,
            "message": message,
        }

    def _compression_fingerprint(
        self,
        game: Game,
    ) -> Mapping[str, Mapping[str, Any]] | None:
        tracker = self._update_tracker
        if tracker is None:
            return None
        record = tracker.get(game.id)
        snapshot = record.compression_snapshot if record is not None else None
        if snapshot is None or not snapshot.complete:
            return None
        expected_path = os.path.abspath(os.fspath(game.install_path))
        if os.path.abspath(snapshot.root_path) != expected_path:
            return None
        return {
            item.relative_path: {
                "size": int(item.size),
                "mtime_ns": int(item.mtime_ns),
                "ctime_ns": int(item.ctime_ns),
            }
            for item in snapshot.files
        }

    def _mark_compression_verified(self, game: Game) -> None:
        tracker = self._update_tracker
        if tracker is None:
            return
        tracker.record_verified_compression(game)
        # The callback executes in a worker.  QML state is refreshed only by
        # the controller's GUI-thread timer.
        self._updates_dirty = True

    def _reload_selected_history(self, *, emit_signal: bool = True) -> None:
        service = self._compression_service
        if service is None or not self._selected_game_id:
            history: tuple[dict[str, Any], ...] = ()
        else:
            try:
                history = service.history(self._selected_game_id)
            except Exception as error:
                logger.warning("Could not read compression history: %s", error)
                history = ()
        self._selected_game_history = [
            {
                **cast(dict[str, Any], qml_value(dict(entry))),
                "historyId": str(entry.get("id", "")),
            }
            for entry in history
            if isinstance(entry, Mapping)
        ]
        if emit_signal:
            self.compressionHistoryChanged.emit()

    @staticmethod
    def _record_status_label(status: GameUpdateStatus) -> str:
        return {
            GameUpdateStatus.INVENTORY: "Up to date",
            GameUpdateStatus.UP_TO_DATE: "Up to date",
            GameUpdateStatus.WAITING_FOR_LAUNCHER: "Waiting for launcher",
            GameUpdateStatus.WAITING_FOR_STABILITY: "Update detected",
            GameUpdateStatus.ANALYSIS_REQUIRED: "Analysis required",
            GameUpdateStatus.IGNORED: "Up to date",
            GameUpdateStatus.LIBRARY_UNAVAILABLE: "Drive disconnected",
            GameUpdateStatus.ERROR: "Error",
        }.get(status, "Unknown")

    def _active_task_for_game(self, game_id: str) -> Task | None:
        try:
            tasks = self._task_service.list_tasks()
        except Exception:
            return None
        active = [
            task
            for task in tasks
            if task.game_id == game_id
            and task.status.value not in _TERMINAL_STATUSES
        ]
        return active[-1] if active else None

    @staticmethod
    def _update_event_identity(
        record: GameUpdateRecord,
        game: Game | None,
    ) -> str:
        """Build a restart-stable identity for one observable update event."""

        observation = record.pending_observation or record.current_observation
        raw_library = (
            observation.library_path
            if observation is not None and observation.library_path
            else os.fspath(game.library_path)
            if game is not None and game.library_path is not None
            else ""
        )
        try:
            library_identity = (
                os.path.normcase(os.path.realpath(os.path.abspath(raw_library)))
                if raw_library
                else "unknown-library"
            )
        except (OSError, TypeError, ValueError):
            library_identity = str(raw_library) or "unknown-library"

        build_id = str(
            (observation.build_id if observation is not None else None)
            or (game.steam_build_id if game is not None else None)
            or ""
        ).strip()
        change_signature = ""
        if not build_id:
            change_signature = record.pending_signature or record.current_signature
        if not change_signature:
            change_payload = {
                "new": sorted(record.changes.new_files),
                "modified": sorted(record.changes.modified_files),
                "deleted": sorted(record.changes.deleted_files),
                "changed_bytes": int(record.changes.changed_bytes),
                "error": str(record.last_error),
            }
            change_signature = hashlib.blake2b(
                json.dumps(
                    change_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                digest_size=20,
            ).hexdigest()
        payload = {
            "app_id": str(record.app_id or (game.steam_app_id if game else "") or record.game_id),
            "library": library_identity,
            "build_or_change": build_id or change_signature,
            "entry_type": record.status.value,
        }
        return hashlib.blake2b(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            digest_size=20,
        ).hexdigest()

    def _update_record_to_qml(self, record: GameUpdateRecord) -> dict[str, Any]:
        game = self._domain_games.get(record.game_id)
        if game is None and record.app_id:
            game = next(
                (
                    candidate
                    for candidate in self._domain_games.values()
                    if str(candidate.steam_app_id or "") == record.app_id
                ),
                None,
            )
        presented_game = (
            self._present_game(game, analysis_report=self._analysis_reports.get(game.id))
            if game is not None
            else {}
        )
        available = bool(
            game is not None
            and game.library_available
            and record.status is not GameUpdateStatus.LIBRARY_UNAVAILABLE
        )
        status_label = self._record_status_label(record.status)
        task = self._active_task_for_game(record.game_id)
        if task is not None:
            if task.task_type is TaskType.COMPRESSION:
                status_label = (
                    "Queued"
                    if task.status is TaskStatus.QUEUED
                    else "Compressing"
                )
            elif task.task_type is TaskType.ANALYSIS:
                status_label = (
                    "Queued"
                    if task.status is TaskStatus.QUEUED
                    else "Analyzing"
                )
        report = self._analysis_reports.get(record.game_id)
        du = report.get("btrfs_du", {}) if isinstance(report, Mapping) else {}
        report_ready = bool(
            isinstance(report, Mapping)
            and report.get("game_id") == record.game_id
            and report.get("scan_complete") is True
            and report.get("is_btrfs") is True
            and report.get("compression_eligible") is True
            and isinstance(du, Mapping)
            and du.get("available") is True
            and du.get("state") == "not_detected"
        )
        attention = record.status in {
            GameUpdateStatus.WAITING_FOR_STABILITY,
            GameUpdateStatus.ANALYSIS_REQUIRED,
        }
        can_analyze = bool(
            available
            and record.status is GameUpdateStatus.ANALYSIS_REQUIRED
            and task is None
        )
        can_compress = bool(
            available
            and record.status is GameUpdateStatus.ANALYSIS_REQUIRED
            and task is None
            and report_ready
        )
        observation = record.current_observation or record.pending_observation
        section = (
            "compression_pending"
            if record.status is GameUpdateStatus.ANALYSIS_REQUIRED or task is not None
            else "game_updates"
        )
        update_identity = self._update_event_identity(record, game)
        return {
            **presented_game,
            "rowId": f"update:{record.app_id or record.game_id}:{update_identity}",
            "displayVersion": update_identity,
            "updateIdentity": update_identity,
            "sectionKey": section,
            "gameId": record.game_id,
            "appId": record.app_id,
            "gameKnown": game is not None,
            "name": game.name if game is not None else f"Steam {record.app_id}",
            "gameName": game.name if game is not None else f"Steam {record.app_id}",
            "compressionState": status_label,
            "status": status_label,
            "updateStatus": record.status.value,
            "libraryAvailable": available,
            "canAnalyze": can_analyze,
            "canCompress": can_compress,
            "canIgnore": bool(attention and available and task is None),
            "requiresFullAnalysis": bool(record.requires_full_analysis),
            "installationDetected": bool(record.installation_detected),
            "newFiles": list(record.changes.new_files),
            "modifiedFiles": list(record.changes.modified_files),
            "deletedFiles": list(record.changes.deleted_files),
            "changedFileCount": (
                len(record.changes.new_files)
                + len(record.changes.modified_files)
                + len(record.changes.deleted_files)
            ),
            "changedBytes": int(record.changes.changed_bytes),
            "changesReliable": bool(record.changes.reliable),
            "detectedAt": (
                record.detected_at.isoformat()
                if record.detected_at is not None
                else ""
            ),
            "updatedAt": record.updated_at.isoformat(),
            "buildId": str(observation.build_id or "") if observation else "",
            "recommendedProfile": (
                self._settings_model.automatic_compression_profile.value
            ),
            "error": str(record.last_error),
            "ignored": record.status is GameUpdateStatus.IGNORED,
            "canDismiss": task is None,
        }

    def _history_update_rows(self) -> list[dict[str, Any]]:
        service = self._compression_service
        if service is None:
            return []
        try:
            history = service.history()
        except Exception as error:
            logger.warning("Could not read recent compression history: %s", error)
            return []
        rows: list[dict[str, Any]] = []
        for entry in history[:20]:
            history_path = str(
                entry.get("library_path")
                or entry.get("libraryPath")
                or entry.get("game_path")
                or entry.get("path")
                or ""
            )
            if history_path and any(
                self._path_is_within_library_path(history_path, ignored)
                for ignored in self._settings_model.ignored_steam_libraries
            ):
                continue
            game_id = str(entry.get("game_id", ""))
            game = self._domain_games.get(game_id)
            app_id = str(entry.get("app_id") or game_id.removeprefix("steam-"))
            if game is None and app_id:
                game = next(
                    (
                        candidate
                        for candidate in self._domain_games.values()
                        if str(candidate.steam_app_id or "") == app_id
                    ),
                    None,
                )
            presented_game = self._present_game(game) if game is not None else {}
            status = str(entry.get("status", ""))
            state = (
                "Optimized"
                if status in {"completed", "completed_with_warning"}
                else "Verification required"
                if status == "verification_required"
                else "Failed"
                if status == "failed"
                else "Unknown"
            )
            rows.append(
                {
                    **presented_game,
                    **cast(dict[str, Any], qml_value(dict(entry))),
                    "rowId": f"history:{entry.get('id', '')}",
                    "displayVersion": str(
                        entry.get("completed_at")
                        or entry.get("updated_at")
                        or entry.get("id", "")
                    ),
                    "historyId": str(entry.get("id", "")),
                    "sectionKey": "recently_optimized",
                    "gameId": game_id,
                    "name": str(
                        entry.get("game_name")
                        or (game.name if game is not None else "Unknown game")
                    ),
                    "compressionState": state,
                    "libraryAvailable": bool(
                        game is not None and game.library_available
                    ),
                    "canAnalyze": False,
                    "canCompress": False,
                    "canIgnore": False,
                    "canDismiss": self._active_task_for_game(game_id) is None,
                    "error": str(entry.get("error") or ""),
                }
            )
        return rows

    def _update_row_is_dismissed(self, row: Mapping[str, Any]) -> bool:
        row_id = str(row.get("rowId", ""))
        if not row_id:
            return False
        return self._dismissed_updates.get(row_id) == str(
            row.get("displayVersion") or row_id
        )

    @staticmethod
    def _update_row_is_very_old(row: Mapping[str, Any]) -> bool:
        """Auto-hide old informational rows, never current actionable work."""

        status = str(row.get("updateStatus", ""))
        section = str(row.get("sectionKey", ""))
        if section == "recently_optimized":
            if str(row.get("compressionState", "")) == "Verification required":
                return False
        elif status not in {
            GameUpdateStatus.INVENTORY.value,
            GameUpdateStatus.UP_TO_DATE.value,
            GameUpdateStatus.IGNORED.value,
        }:
            return False
        raw_date = (
            row.get("completed_at")
            or row.get("completedAt")
            or row.get("updatedAt")
            or row.get("detectedAt")
        )
        try:
            parsed = datetime.fromisoformat(str(raw_date))
        except (TypeError, ValueError):
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return datetime.now(UTC) - parsed > timedelta(days=30)

    def _reload_updates(self, *, emit_signal: bool = True) -> None:
        tracker = self._update_tracker
        records = tracker.list_records() if tracker is not None else ()
        record_rows = [
            self._update_record_to_qml(record)
            for record in records
            if not self._update_record_is_in_ignored_library(record)
        ]
        history_rows = self._history_update_rows()
        record_rows = [
            row
            for row in record_rows
            if not (
                self._library_scan_status == "ready"
                and row.get("gameKnown") is not True
                and self._active_task_for_game(str(row.get("gameId", ""))) is None
            )
            if not self._update_row_is_dismissed(row)
            and not self._update_row_is_very_old(row)
        ]
        history_rows = [
            row
            for row in history_rows
            if not self._update_row_is_dismissed(row)
            and not self._update_row_is_very_old(row)
        ]
        self._updates = record_rows + history_rows
        attention_statuses = {
            GameUpdateStatus.WAITING_FOR_STABILITY.value,
            GameUpdateStatus.ANALYSIS_REQUIRED.value,
            GameUpdateStatus.ERROR.value,
            GameUpdateStatus.LIBRARY_UNAVAILABLE.value,
        }
        needs_attention = sum(
            str(row.get("updateStatus", "")) in attention_statuses
            for row in record_rows
        )
        pending_count = sum(
            row.get("compressionState")
            in {"Queued", "Analyzing", "Compressing", "Analysis required"}
            for row in record_rows
        )
        recovered_bytes = sum(
            max(0, int(entry.get("actual_saved_bytes") or 0))
            for entry in history_rows
        )
        self._updates_summary = {
            "needsCheckCount": int(needs_attention),
            "updateCount": int(needs_attention),
            "pendingCount": int(pending_count),
            "queuedCount": int(pending_count),
            "recentlyOptimizedCount": len(history_rows),
            "recentRecoveredBytes": int(recovered_bytes),
        }
        self._updates_dirty = False
        if emit_signal:
            self.updatesChanged.emit()
            self.updatesSummaryChanged.emit()

    def _schedule_update_observations(self) -> None:
        if self._shutdown_requested:
            return
        tracker = self._update_tracker
        executor = self._update_executor
        if tracker is None or executor is None:
            return
        scheduled = False
        for game in tuple(self._domain_games.values()):
            if game.is_steam_tool or game.id in self._update_jobs:
                continue
            cancel_event = Event()
            try:
                future = executor.submit(
                    tracker.observe,
                    game,
                    cancel_event=cancel_event,
                )
            except RuntimeError as error:
                if not self._shutdown_requested:
                    logger.warning(
                        "Could not schedule update scan for %s: %s",
                        game.id,
                        error,
                    )
                continue
            self._update_jobs[game.id] = (future, cancel_event)
            scheduled = True
        if scheduled:
            self._inventory_scan_started = True
            self._last_periodic_rescan = datetime.now(UTC)

    def _poll_update_jobs(self) -> None:
        if not self._update_jobs:
            return
        changed = False
        for game_id, (future, _event) in tuple(self._update_jobs.items()):
            if not future.done():
                continue
            self._update_jobs.pop(game_id, None)
            try:
                record = future.result()
            except Exception as error:
                if not self._shutdown_requested:
                    logger.warning(
                        "Steam update scan for %s failed: %s",
                        game_id,
                        error,
                    )
            else:
                changed = True
                logger.debug(
                    "Update state for %s is %s",
                    game_id,
                    record.status.value,
                )
        if (
            self._inventory_completion_pending
            and self._inventory_scan_started
            and not self._update_jobs
            and self._update_tracker is not None
        ):
            try:
                self._update_tracker.complete_initial_inventory()
            except Exception as error:
                logger.warning("Could not finalize update inventory: %s", error)
            else:
                self._inventory_completion_pending = False
                changed = True
        if changed:
            self._reload_updates()
            self._queue_eligible_automatic_compression()

    def _automatic_mode_allows(self, record: GameUpdateRecord) -> bool:
        mode = self._settings_model.automatic_compression_mode
        if mode is AutomaticCompressionMode.OFF:
            return False
        if record.installation_detected:
            return bool(mode.allows_installation)
        return bool(mode.allows_update)

    def _automatic_library_allowed(self, game: Game) -> bool:
        configured = self._settings_model.automatic_compression_libraries
        if not configured:
            return True
        candidate = game.library_path or game.install_path
        try:
            candidate_path = os.path.abspath(os.fspath(candidate))
        except (OSError, TypeError, ValueError):
            return False
        for library in configured:
            try:
                allowed_path = os.path.abspath(os.fspath(library))
                if os.path.commonpath((candidate_path, allowed_path)) == allowed_path:
                    return True
            except (OSError, TypeError, ValueError):
                continue
        return False

    def _automatic_task_active(self, game_id: str = "") -> bool:
        try:
            tasks = self._task_service.list_tasks()
        except Exception:
            return False
        return any(
            task.status.value not in _TERMINAL_STATUSES
            and task.task_type in {TaskType.ANALYSIS, TaskType.COMPRESSION}
            and (not game_id or task.game_id == game_id)
            for task in tasks
        )

    def _queue_eligible_automatic_compression(self) -> None:
        tracker = self._update_tracker
        if (
            tracker is None
            or self._settings_model.automatic_compression_mode
            is AutomaticCompressionMode.OFF
            or self._shutdown_requested
        ):
            return
        try:
            active_compression = sum(
                task.task_type is TaskType.COMPRESSION
                and task.status.value not in _TERMINAL_STATUSES
                for task in self._task_service.list_tasks()
            )
        except Exception:
            active_compression = 0
        available_slots = max(
            0,
            self._settings_model.automatic_compression_max_jobs
            - active_compression,
        )
        if available_slots <= 0:
            return
        for record in tracker.list_records():
            if available_slots <= 0:
                break
            if (
                record.status is not GameUpdateStatus.ANALYSIS_REQUIRED
                or not self._automatic_mode_allows(record)
                or record.app_id
                in self._settings_model.automatic_compression_skipped_app_ids
                or record.game_id in self._pending_automatic_games
            ):
                continue
            game = self._domain_games.get(record.game_id)
            if (
                game is None
                or not self._game_actions_allowed(game)
                or game.update_in_progress
                or game.filesystem is not FilesystemType.BTRFS
                or not self._automatic_library_allowed(game)
                or self._automatic_task_active(game.id)
            ):
                continue
            self._pending_automatic_games.add(game.id)
            report = self._analysis_reports.get(game.id)
            if isinstance(report, Mapping):
                if self._start_automatic_compression(record):
                    available_slots -= 1
                    continue
            try:
                task = self._task_service.enqueue_analysis(game)
            except Exception as error:
                self._pending_automatic_games.discard(game.id)
                logger.warning(
                    "Could not queue automatic analysis for %s: %s",
                    game.id,
                    error,
                )
                continue
            self._reload_tasks()
            if self._settings_model.automatic_compression_notify:
                self._emit_toast(
                    f"Checking {game.name} before automatic compression",
                    "info",
                )
            logger.info(
                "Queued automatic safety analysis %s for %s",
                task.id,
                game.id,
            )
            # Analysis and compression share one bounded queue.  Waiting for
            # its result prevents a second game from being staged as a writer.
            available_slots -= 1

    def _start_automatic_compression(self, record: GameUpdateRecord) -> bool:
        game = self._domain_games.get(record.game_id)
        if game is None or not self._automatic_mode_allows(record):
            self._pending_automatic_games.discard(record.game_id)
            return False
        report = self._analysis_reports.get(game.id)
        btrfs_du = (
            report.get("btrfs_du", {})
            if isinstance(report, Mapping)
            else {}
        )
        if (
            not isinstance(btrfs_du, Mapping)
            or btrfs_du.get("available") is not True
            or btrfs_du.get("state") != "not_detected"
        ):
            # Breaking snapshot/reflink sharing requires a human to review the
            # measured growth warning.  Generic automatic opt-in is not enough.
            self._pending_automatic_games.discard(game.id)
            if self._settings_model.automatic_compression_notify:
                self._emit_toast(
                    f"Automatic compression skipped for {game.name}: "
                    "shared extents require manual confirmation",
                    "warning",
                )
            return False
        current_measurement = self._current_authoritative_compsize(game.id)
        current_classification = classify_compression_effect(
            current_measurement.get("compsize_uncompressed_bytes"),
            current_measurement.get("compsize_disk_bytes"),
        )
        if current_classification.get("key") == "no_compression":
            # A zero-effect installation may be a special/snapshot-backed
            # layout.  It remains available manually, but is never selected by
            # the unattended policy without a human reviewing the plan.
            self._pending_automatic_games.discard(game.id)
            if self._settings_model.automatic_compression_notify:
                self._emit_toast(
                    f"Automatic compression skipped for {game.name}: "
                    "the current measurement requires manual review",
                    "warning",
                )
            return False
        presented = self.prepareCompression(
            game.id,
            self._settings_model.automatic_compression_profile.value,
            not record.requires_full_analysis,
        )
        plan_id = str(presented.get("planId", ""))
        if not bool(presented.get("valid", False)) or not plan_id:
            self._pending_automatic_games.discard(game.id)
            return False
        if presented.get("additionalConfirmationRequired") is True:
            self._pending_automatic_games.discard(game.id)
            if self._settings_model.automatic_compression_notify:
                self._emit_toast(
                    f"Automatic compression skipped for {game.name}: "
                    "the estimated additional benefit requires manual confirmation",
                    "warning",
                )
            return False
        return self._start_compression_plan(
            plan_id,
            confirmed=False,
            automatic_authorized=True,
        )

    def _load_settings(self) -> AppSettings:
        try:
            settings = self._settings_store.load()
        except Exception as error:
            logger.exception(
                "Could not load settings from %s; using in-memory defaults: %s",
                SETTINGS_FILE,
                error,
            )
            self._deferred_toasts.append(
                ("Local settings could not be loaded; safe defaults are active", "warning")
            )
            return AppSettings()
        return settings

    def _reload_games(
        self,
        *,
        emit_signal: bool = True,
        reason: str = "state_update",
    ) -> None:
        all_games = list(self._domain_games.values())
        compression_results = self._latest_compression_results()
        verification_results = self._latest_verification_results()
        show_tools = self._demo_mode or bool(
            getattr(self._settings_model, "show_steam_tools_and_runtimes", False)
        )
        games = [
            game for game in all_games if show_tools or not game.is_steam_tool
        ]
        next_games = [
            self._present_game(
                game,
                analysis_report=self._analysis_reports.get(game.id),
                compression_result=compression_results.get(game.id),
                verification_result=verification_results.get(game.id),
                benchmark_estimate=self._benchmark_estimates.estimate_for(game),
            )
            for game in games
        ]
        # Build the entire presentation snapshot off to the side.  The legacy
        # QVariantList remains available to non-delegate consumers, but is not
        # replaced unless the incremental model found a visible change.
        previous_games = self._games
        summaries = aggregate_library_compression(next_games)
        configured_libraries = self._provider_configured_library_paths()
        for summary in summaries:
            library_path = Path(str(summary.get("libraryPath", "")))
            library_games = [
                game
                for game in games
                if game.library_path is not None
                and self._same_path(game.library_path, library_path)
            ]
            configured = any(
                self._same_path(library_path, configured_path)
                for configured_path in configured_libraries
            )
            try:
                path_exists = library_path.exists()
            except OSError:
                path_exists = False
            summary.update(
                {
                    "libraryAvailable": any(
                        game.library_available for game in library_games
                    ),
                    "libraryConfigured": configured,
                    "canForgetLibrary": bool(
                        self._library_scan_status == "ready"
                        and not configured
                        and not path_exists
                        and library_games
                        and not any(
                            self._active_task_for_game(game.id) is not None
                            for game in library_games
                        )
                    ),
                    "canIgnoreLibrary": bool(
                        not self._demo_mode
                        and library_games
                        and not any(game.library_available for game in library_games)
                        and not any(
                            self._active_task_for_game(game.id) is not None
                            for game in library_games
                        )
                    ),
                }
            )
        self._compression_library_summaries = summaries
        if self._selected_game_id:
            selected = next(
                (game for game in all_games if game.id == self._selected_game_id), None
            )
            if selected is None:
                self._selected_game_id = ""
                self._selected_game = {}
            else:
                self._selected_game = self._present_game(
                    selected,
                    analysis_report=self._analysis_reports.get(selected.id),
                    compression_result=compression_results.get(selected.id),
                    verification_result=verification_results.get(selected.id),
                    benchmark_estimate=self._benchmark_estimates.estimate_for(selected),
                )
            if emit_signal:
                self.selectedGameChanged.emit()
        mutation = self._games_model.apply_snapshot(next_games, reason=str(reason))
        if mutation["changed"] is not True:
            self._games = previous_games
            logger.info(
                "Games model unchanged, refresh skipped: generation=%d "
                "reason=%s games=%d modelReset=%d",
                self._games_model_generation,
                str(reason),
                len(next_games),
                self._games_model.modelResetCount,
            )
            return
        self._games = next_games
        self._games_model_generation += 1
        logger.info(
            "Games model incremental commit: generation=%d reason=%s games=%d "
            "inserted=%d removed=%d updated=%d moved=%d modelReset=%d",
            self._games_model_generation,
            str(reason),
            len(self._games),
            mutation["inserted"],
            mutation["removed"],
            mutation["updated"],
            mutation["moved"],
            mutation["resets"],
        )
        if emit_signal:
            self.gamesModelRefreshed.emit(
                self._games_model_generation,
                str(reason),
                len(self._games),
            )
            self.gamesChanged.emit()

    def _latest_compression_results(self) -> dict[str, Mapping[str, Any]]:
        service = self._compression_service
        if service is None:
            return {}
        try:
            history = service.history()
        except Exception as error:
            logger.warning("Could not read compression history for games: %s", error)
            return {}
        latest: dict[str, Mapping[str, Any]] = {}
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            game_id = str(entry.get("game_id", "")).strip()
            if game_id and game_id not in latest:
                latest[game_id] = entry
        return latest

    def _latest_verification_results(self) -> dict[str, Mapping[str, Any]]:
        """Return only the newest terminal verification for each game."""

        try:
            tasks = self._task_service.list_tasks()
        except Exception as error:
            logger.warning("Could not read verification task history: %s", error)
            return {}
        latest_tasks: dict[str, Task] = {}
        for task in tasks:
            if (
                task.task_type is not TaskType.VERIFICATION
                or task.status.value not in _TERMINAL_STATUSES
                or not task.game_id
            ):
                continue
            previous = latest_tasks.get(task.game_id)
            if previous is None or task.updated_at > previous.updated_at:
                latest_tasks[task.game_id] = task
        return {
            game_id: {
                "task_id": task.id,
                "game_id": task.game_id,
                "status": task.status.value,
                "error": str(task.error or ""),
                "result": qml_value(task.result or {}),
                "updated_at": task.updated_at.isoformat(),
            }
            for game_id, task in latest_tasks.items()
        }

    def _current_authoritative_compsize(self, game_id: str) -> dict[str, Any]:
        """Return the same newest complete compsize result used by the game header."""

        verification = self._latest_verification_results().get(game_id)
        if verification:
            result = verification.get("result")
            measurement = dict(result) if isinstance(result, Mapping) else {}
            if (
                str(verification.get("status") or "").casefold() == "completed"
                and self._complete_privileged_compsize(measurement)
            ):
                return measurement
            return {}
        compression = self._latest_compression_results().get(game_id)
        if not compression or compression.get("measurement_authoritative") is not True:
            return {}
        after = compression.get("after")
        measurement = dict(after) if isinstance(after, Mapping) else {}
        return (
            measurement
            if self._complete_privileged_compsize(measurement)
            else {}
        )

    @staticmethod
    def _complete_privileged_compsize(measurement: Mapping[str, Any]) -> bool:
        if (
            str(measurement.get("measurement_source") or "").casefold()
            != "polkit_helper"
        ):
            return False
        for key in (
            "compsize_disk_bytes",
            "compsize_uncompressed_bytes",
            "compsize_referenced_bytes",
        ):
            value = measurement.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
            ):
                return False
        return True

    def _reload_tasks(self, *, emit_signal: bool = True) -> None:
        rows = [
            task_to_qml(task) for task in self._task_service.list_tasks()
        ] + [dict(task) for task in self._operational_tasks.values()]
        for row in rows:
            game = self._domain_games.get(str(row.get("gameId", "")))
            if game is not None:
                presented_game = self._present_game(game)
                row.update(
                    {
                        "steamAppId": presented_game.get("steamAppId", ""),
                        "launcher": presented_game.get("launcher", ""),
                        "effectiveArtworkUrl": presented_game.get(
                            "effectiveArtworkUrl", ""
                        ),
                        "portraitArtwork": presented_game.get(
                            "portraitArtwork", ""
                        ),
                        "headerArtwork": presented_game.get(
                            "headerArtwork", ""
                        ),
                        "fallbackArtwork": presented_game.get(
                            "fallbackArtwork", ""
                        ),
                    }
                )
            elif str(row.get("gameId", "")).startswith("steam-"):
                row["steamAppId"] = str(row["gameId"]).removeprefix("steam-")
                row["launcher"] = "Steam"
        self._tasks = self._bounded_task_rows(
            rows
        )
        if emit_signal:
            self.tasksChanged.emit()

    @staticmethod
    def _bounded_task_rows(tasks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        active = [
            task
            for task in tasks
            if str(task.get("status", "")).lower() not in _TERMINAL_STATUSES
        ]
        finished = sorted(
            (
                task
                for task in tasks
                if str(task.get("status", "")).lower() in _TERMINAL_STATUSES
            ),
            key=lambda task: str(task.get("updatedAt", "")),
            reverse=True,
        )[:100]
        return [*active, *finished]

    def _reload_backups(self, *, emit_signal: bool = True) -> None:
        self._backups = [
            backup_to_qml(backup) for backup in self._backup_service.list_backups()
        ]
        if emit_signal:
            self.backupsChanged.emit()

    def _reload_system_info(self, *, emit_signal: bool = True) -> None:
        try:
            system_info = self._read_system_info()
            if not self._demo_mode:
                try:
                    detected_filesystems = self._read_filesystems()
                except Exception as error:
                    logger.warning("Could not enumerate mounted filesystems: %s", error)
                    detected_filesystems = ()
                if detected_filesystems:
                    system_info = replace(
                        system_info,
                        filesystems=tuple(detected_filesystems),
                    )
            self._system_info = system_info_to_qml(system_info)
        except Exception as error:
            logger.exception("Could not load system information: %s", error)
            self._system_info = {
                "distribution": "Unknown",
                "kernel": "Unknown",
                "desktopEnvironment": "Unknown",
                "sessionType": "Unknown",
                "cpu": "Unknown",
                "gpu": "Unknown",
                "gpuDriver": "Unknown",
                "capabilities": {},
                "demo": self._demo_mode,
                "error": "System information is temporarily unavailable",
            }
            self._deferred_toasts.append(
                ("System information is temporarily unavailable", "warning")
            )
        self._add_compression_system_info()
        self._add_steam_system_info()
        self._add_gamepad_system_info()
        if emit_signal:
            self.systemInfoChanged.emit()

    def _add_compression_system_info(self) -> None:
        service = self._compression_service
        if service is None:
            capabilities = {
                "btrfsAvailable": False,
                "compsizeAvailable": False,
                "propertySupported": False,
                "recompressionSupported": False,
                "levelSupported": False,
                "compressionAvailable": False,
                "activeJobs": 0,
                "lastError": "",
                "message": "Real compression is unavailable in this mode",
            }
        else:
            try:
                raw = service.capabilities().to_dict()
            except Exception as error:
                logger.warning("Could not inspect Btrfs capabilities: %s", error)
                raw = {
                    "btrfs_available": False,
                    "compsize_available": False,
                    "property_supported": False,
                    "recompression_supported": False,
                    "level_supported": False,
                    "compression_available": False,
                    "message": str(error),
                }
            capabilities = {
                **cast(dict[str, Any], qml_value(raw)),
                "btrfsAvailable": raw.get("btrfs_available") is True,
                "btrfsVersion": str(raw.get("btrfs_version", "")),
                "compsizeAvailable": raw.get("compsize_available") is True,
                "compsizeVersion": str(raw.get("compsize_version", "")),
                "propertySupported": raw.get("property_supported") is True,
                "recompressionSupported": raw.get("recompression_supported") is True,
                "levelSupported": raw.get("level_supported") is True,
                "compressionAvailable": raw.get("compression_available") is True,
                "activeJobs": len(service.active_game_ids()),
                "lastError": str(service.last_error),
                "message": str(raw.get("message", "")),
            }
        host_details = self._system_info.get("capabilityDetails")
        if isinstance(host_details, Mapping):
            host_btrfs = host_details.get("Btrfs tools")
            host_compsize = host_details.get("compsize")
            if isinstance(host_btrfs, Mapping):
                capabilities["hostBtrfsAvailable"] = host_btrfs.get("available") is True
                capabilities["hostBtrfsVersion"] = str(host_btrfs.get("version") or "")
            if isinstance(host_compsize, Mapping):
                capabilities["hostCompsizeAvailable"] = (
                    host_compsize.get("available") is True
                )
                if (
                    self._host_service is not None
                    and self._host_service.measurement_available
                    and host_compsize.get("available") is True
                ):
                    capabilities["compsizeAvailable"] = True
                    capabilities["compsizeVersion"] = str(
                        host_compsize.get("version") or ""
                    )
            if self._host_service is not None:
                capabilities["measurementSource"] = (
                    "optional_host_component"
                    if self._host_service.measurement_available
                    else "unavailable"
                )
                capabilities["hostComponentInstalled"] = self._host_service.installed
        self._system_info["compressionCapabilities"] = capabilities
        self._system_info["compression_capabilities"] = capabilities

    def _add_steam_system_info(self) -> None:
        details = self._system_info.get("capabilityDetails")
        steam = details.get("Steam") if isinstance(details, Mapping) else None
        steam_map = dict(steam) if isinstance(steam, Mapping) else {}
        executable_detected = steam_map.get("available") is True
        steam_type = str(steam_map.get("steam_type") or "unavailable")
        host_launch = steam_map.get("host_launch_available") is True
        self._system_info.update(
            {
                "steamLibraryDetected": bool(self._steam_found),
                "steam_library_detected": bool(self._steam_found),
                "steamExecutableDetected": executable_detected,
                "steam_executable_detected": executable_detected,
                "steamType": steam_type,
                "steam_type": steam_type,
                "hostLaunchAvailable": host_launch,
                "host_launch_available": host_launch,
            }
        )

    def _read_system_info(self) -> SystemInfo:
        for method_name in ("get_system_info", "inspect", "detect", "collect"):
            method = getattr(self._system_provider, method_name, None)
            if callable(method):
                return cast(SystemInfo, method())
        raise AttributeError("system provider does not expose an inspection method")

    def _read_filesystems(self) -> Sequence[Any]:
        provider = self._filesystem_provider
        method = getattr(provider, "list_filesystems", None)
        if not callable(method):
            return ()

        probe_paths: dict[str, Path] = {}
        for path in (
            *self._settings_model.library_directories,
            *self._settings_model.steam_installation_directories,
            *(game.install_path for game in self._domain_games.values()),
            *(
                game.library_path
                for game in self._domain_games.values()
                if game.library_path is not None
            ),
        ):
            normalized_path = os.path.normpath(os.path.abspath(os.fspath(path)))
            probe_paths[normalized_path] = Path(path)

        try:
            return cast(
                Sequence[Any],
                method(
                    game_paths=tuple(probe_paths.values()),
                    show_system_mounts=self._show_system_mounts,
                ),
            )
        except TypeError:
            # Preserve compatibility with third-party read-only providers that
            # implemented the original no-argument protocol.
            return cast(Sequence[Any], method())

    def _poll_tasks(self) -> None:
        if self._shutdown_requested:
            return
        try:
            self._poll_update_jobs()
            self._poll_optiscaler_jobs()
            now = datetime.now(UTC)
            if (
                self._update_tracker is not None
                and not self._update_jobs
                and (now - self._last_periodic_rescan).total_seconds() >= 60.0
            ):
                self._schedule_update_observations()
            if self._demo_mode:
                self._task_service.tick(step=1.5)
            domain_tasks = list(self._task_service.list_tasks())
            service_tasks = [task_to_qml(task) for task in domain_tasks]
            updated = self._bounded_task_rows(
                service_tasks + list(self._operational_tasks.values())
            )
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received while polling tasks")
            self.shutdown()
            application = QCoreApplication.instance()
            if application is not None:
                application.quit()
            return
        except Exception as error:
            logger.exception("Task status timer failed: %s", error)
            if not self._timer_error_reported:
                self._emit_toast("The task list could not be updated", "error")
                self._timer_error_reported = True
            return

        self._timer_error_reported = False
        if updated != self._tasks:
            self._tasks = updated
            self.tasksChanged.emit()

        for task, presented in zip(domain_tasks, service_tasks, strict=True):
            status = presented["status"]
            task_id = presented["id"]
            if status not in _TERMINAL_STATUSES or task_id in self._reported_terminal_tasks:
                continue
            self._reported_terminal_tasks.add(task_id)
            if task.task_type is TaskType.VERIFICATION:
                self.toastDismissRequested.emit(_MEASUREMENT_AUTH_TOAST)
            if status == TaskStatus.COMPLETED.value:
                self._remember_analysis_report(task)
                if task.task_type is TaskType.COMPRESSION:
                    self._reload_games()
                    self._reload_selected_history()
                    self._reload_updates()
                    self._reload_system_info()
                elif task.task_type is TaskType.VERIFICATION:
                    self._reload_games()
                    self._reload_selected_history()
                elif (
                    task.task_type is TaskType.ANALYSIS
                    and task.game_id in self._pending_automatic_games
                ):
                    tracker = self._update_tracker
                    record = tracker.get(task.game_id) if tracker is not None else None
                    if record is None or not self._start_automatic_compression(record):
                        self._pending_automatic_games.discard(task.game_id)
                self._emit_toast(f"{presented['name']} completed", "success")
            elif status == TaskStatus.FAILED.value:
                self._pending_automatic_games.discard(task.game_id)
                logger.error(
                    "Task %s failed: %s", task_id, presented.get("error") or "unknown"
                )
                self._emit_toast(f"{presented['name']} failed", "error")
            elif status == TaskStatus.CANCELLED.value:
                self._pending_automatic_games.discard(task.game_id)
            self.taskFinished.emit(task_id, status)
        if self._updates_dirty:
            self._reload_selected_history()
            self._reload_updates()
            self._reload_system_info()

    def _remember_analysis_report(self, task: Task) -> None:
        if task.task_type is not TaskType.ANALYSIS or not task.result:
            return
        report = qml_value(task.result)
        if isinstance(report, Mapping):
            self._analysis_reports[task.game_id] = dict(report)
            self._reload_games()

    def _poll_optiscaler_jobs(self) -> None:
        while True:
            try:
                task_id, stage, progress = self._optiscaler_events.get_nowait()
            except Empty:
                break
            task = self._operational_tasks.get(task_id)
            if task is None:
                continue
            task["status"] = "running"
            task["stage"] = stage
            task["progress"] = min(1.0, max(0.0, progress))
            task["progressPercent"] = task["progress"] * 100.0
            task["updatedAt"] = datetime.now(UTC).isoformat()

        for task_id, (future, cancelled, game_id) in tuple(
            self._optiscaler_jobs.items()
        ):
            if not future.done():
                continue
            self._optiscaler_jobs.pop(task_id, None)
            task = self._operational_tasks.get(task_id)
            if task is None:
                continue
            status = "completed"
            error_text = ""
            try:
                profile = future.result()
                game = self._domain_games.get(game_id)
                task["result"] = (
                    self._optiscaler_service.status(game)
                    if game is not None
                    else profile.to_dict()
                )
                self.optiScalerChanged.emit(profile.app_id)
            except OptiScalerCancelled as error:
                status = "cancelled"
                error_text = str(error)
            except Exception as error:
                status = "cancelled" if cancelled.is_set() else "failed"
                error_text = str(error)
                logger.warning("OptiScaler task %s failed: %s", task_id, error)
            task["status"] = status
            task["stage"] = (
                "Completed" if status == "completed"
                else "Cancelled" if status == "cancelled"
                else "Failed"
            )
            task["progress"] = 1.0
            task["progressPercent"] = 100.0
            task["error"] = error_text
            task["cancellable"] = False
            task["updatedAt"] = datetime.now(UTC).isoformat()
            self.taskFinished.emit(task_id, status)
            self._emit_toast(
                "OptiScaler operation completed"
                if status == "completed"
                else "OptiScaler operation cancelled"
                if status == "cancelled"
                else f"OptiScaler operation failed: {error_text}",
                "success" if status == "completed" else "warning"
                if status == "cancelled" else "error",
            )

    def _change_task_state(self, action: str, task_id: str) -> bool:
        optiscaler_job = self._optiscaler_jobs.get(str(task_id))
        if optiscaler_job is not None:
            if action != "cancel":
                return False
            future, cancelled, _game_id = optiscaler_job
            cancelled.set()
            future.cancel()
            task = self._operational_tasks.get(str(task_id))
            if task is not None:
                task["stage"] = "Cancelling"
                task["updatedAt"] = datetime.now(UTC).isoformat()
            self._reload_tasks()
            return True
        operation = getattr(self._task_service, action)
        try:
            task = operation(task_id)
            self._reload_tasks()
        except Exception as error:
            self._report_error(f"trying to {action} task {task_id}", error)
            return False

        logger.info("Task %s changed via %s to %s", task.id, action, task.status.value)
        self._emit_toast(f"Task {task.status.value}", "info")
        return True

    def _find_game(self, game_id: str) -> Game | None:
        normalized_id = str(game_id).strip()
        if not normalized_id:
            return None
        cached = self._domain_games.get(normalized_id)
        if cached is not None:
            return cached
        try:
            resolved = self._game_provider.get_game(normalized_id)
            if resolved is not None and self._game_is_in_ignored_library(resolved):
                return None
            return resolved
        except (KeyError, LookupError):
            return None

    def _resolve_game(self, game_id: str, *, show_error: bool = True) -> Game | None:
        resolved_id = str(game_id).strip() or self._selected_game_id
        game = self._find_game(resolved_id)
        if game is None and show_error:
            logger.warning("Action requested for unknown game id %r", resolved_id)
            self._emit_toast("Select an available game first", "warning")
        return game

    def _mangohud_profile_for_game(self, game: Game) -> MangoHudProfile | None:
        if game.launcher is not Launcher.STEAM or not game.steam_app_id:
            return None
        try:
            return self._mangohud_repository.load(game.steam_app_id)
        except Exception as error:
            raise SteamLaunchError(f"Could not load MangoHud profile: {error}") from error

    def _mangohud_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> MangoHudProfile:
        base = self._mangohud_repository.load(app_id)
        aliases = {
            "fontSize": "font_size",
            "backgroundAlpha": "background_alpha",
            "roundCorners": "round_corners",
            "tableColumns": "table_columns",
            "fpsLimit": "fps_limit",
            "fpsLimitMethod": "fps_limit_method",
            "vulkanPresentMode": "vulkan_present_mode",
            "toggleHudKey": "toggle_hud_key",
            "loggingEnabled": "logging_enabled",
            "logDuration": "log_duration",
            "logInterval": "log_interval",
            "outputFolder": "output_folder",
            "toggleLoggingKey": "toggle_logging_key",
            "selectedExecutable": "executable_path",
            "executablePath": "executable_path",
        }
        normalized = {aliases.get(str(key), str(key)): value for key, value in values.items()}
        preset = str(normalized.get("preset", base.preset)).strip().lower()
        prepared = base.apply_preset(preset) if preset != base.preset else base
        data = prepared.to_dict()
        allowed = {
            "enabled",
            "preset",
            "position",
            "font_size",
            "background_alpha",
            "round_corners",
            "compact",
            "horizontal",
            "table_columns",
            "fps_limit",
            "fps_limit_method",
            "vulkan_present_mode",
            "vsync",
            "toggle_hud_key",
            "metrics",
            "logging_enabled",
            "log_duration",
            "log_interval",
            "output_folder",
            "toggle_logging_key",
            "executable_path",
        }
        for key, value in normalized.items():
            if key in allowed:
                data[key] = value
        data["schema_version"] = base.schema_version
        data["app_id"] = app_id
        data["preset"] = preset
        if preset == "disabled":
            data["enabled"] = False
            data["metrics"] = []
        elif preset != "custom":
            data["enabled"] = bool(normalized.get("enabled", True))
            data["metrics"] = list(prepared.apply_preset(preset).metrics)
        data["updated_at"] = datetime.now(UTC)
        return MangoHudProfile.from_dict(
            data,
            expected_app_id=app_id,
            default_output_folder=self._mangohud_repository.log_root / app_id,
        )

    def _mangohud_profile_to_qml(
        self, game: Game, profile: MangoHudProfile
    ) -> dict[str, Any]:
        optimization_profile = self._optimization_profile_repository.load(profile.app_id)
        gamescope_owns_limit = self._gamescope_owns_fps_limit(optimization_profile)
        effective_profile = (
            replace(profile, fps_limit=None, fps_limit_method="")
            if gamescope_owns_limit else profile
        )
        steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
        availability = self._mangohud_detector.detect(steam_type)
        strategy = self._mangohud_launch_integration.status(game, effective_profile)
        data = effective_profile.to_dict()
        data.update(
            {
                "success": True,
                "schemaVersion": profile.schema_version,
                "appId": profile.app_id,
                "fontSize": profile.font_size,
                "backgroundAlpha": profile.background_alpha,
                "roundCorners": profile.round_corners,
                "tableColumns": profile.table_columns,
                "fpsLimit": effective_profile.fps_limit or 0,
                "fpsLimitMethod": effective_profile.fps_limit_method,
                "fpsLimitOwner": (
                    "gamescope" if gamescope_owns_limit
                    else "mangohud" if effective_profile.fps_limit is not None
                    else "none"
                ),
                "vulkanPresentMode": profile.vulkan_present_mode,
                "toggleHudKey": profile.toggle_hud_key,
                "loggingEnabled": profile.logging_enabled,
                "logDuration": profile.log_duration,
                "logInterval": profile.log_interval,
                "outputFolder": profile.output_folder,
                "toggleLoggingKey": profile.toggle_logging_key,
                "executablePath": profile.executable_path,
                "updatedAt": profile.updated_at.astimezone(UTC).isoformat(),
                "profilePath": str(self._mangohud_repository.profile_path(profile.app_id)),
                "configPath": str(self._mangohud_repository.config_path(profile.app_id)),
                "available": availability.available,
                "activationEnabled": bool(profile.enabled and availability.available),
                "availabilityMessage": availability.message,
                "steamType": steam_type,
                "version": availability.version,
                "supportedMetrics": [
                    metric
                    for metric, key in METRIC_CONFIG_KEYS.items()
                    if key in availability.supported_keys
                ],
            }
        )
        data.update(strategy.to_dict())
        writer = MangoHudConfigWriter(availability.supported_keys)
        data["configPreview"] = writer.render(effective_profile)
        return data

    @staticmethod
    def _gamescope_owns_fps_limit(profile: GameOptimizationProfile) -> bool:
        return bool(profile.gamescope_enabled and profile.gamescope_mode != "disabled")

    def _clear_mangohud_fps_limit(self, game: Game) -> None:
        app_id = str(game.steam_app_id or "")
        profile = self._mangohud_repository.load(app_id)
        if profile.fps_limit is None and not profile.fps_limit_method:
            return
        effective = replace(
            profile,
            fps_limit=None,
            fps_limit_method="",
            updated_at=datetime.now(UTC),
        )
        availability = self._mangohud_detector.detect(
            "flatpak" if uses_flatpak_steam(game) else "native"
        )
        self._mangohud_repository.save(effective)
        MangoHudConfigWriter(availability.supported_keys).write(
            effective, self._mangohud_repository.config_path(app_id)
        )
        self._mangohud_launch_integration.synchronize(
            game, effective, previous_profile=profile
        )
        self.mangoHudProfileChanged.emit(app_id)

    @staticmethod
    def _mangohud_error(message: str, *, app_id: str = "") -> dict[str, Any]:
        return {
            "success": False,
            "error": str(message),
            "appId": str(app_id),
            "enabled": False,
            "available": False,
            "activationEnabled": False,
            "metrics": [],
            "configPreview": "",
            "activationStrategy": "steam_environment",
            "strategyStatus": "executable_missing",
            "strategyMessage": str(message),
            "applicationConfigPath": "",
            "conflictPath": "",
            "requiresSteamRestart": True,
            "selectedExecutable": "",
            "executableCandidates": [],
        }

    def _new_manual_game(self) -> Game:
        known_ids = {game["id"] for game in self._games}
        while f"manual-demo-{self._manual_game_number}" in known_ids:
            self._manual_game_number += 1
        number = self._manual_game_number
        self._manual_game_number += 1
        return Game(
            id=f"manual-demo-{number}",
            name=f"Manual Demo Game {number}",
            launcher=Launcher.MANUAL,
            install_path=Path("/demo/manual") / f"Manual Demo Game {number}",
            logical_size_gb=12.0 + number,
            physical_size_gb=11.2 + number,
            filesystem=FilesystemType.BTRFS,
            compression_available=True,
            saved_space_gb=0.0,
            status=GameStatus.READY,
            active_optimization_profile=OptimizationProfile.BALANCED,
            backup_status=BackupStatus.AVAILABLE,
            texture_compatibility=TextureCompatibility.PARTIAL_SUPPORT,
        )

    def _setting_field_name(self, key: str) -> str | None:
        aliases = {
            "appearance": "theme",
            "themeMode": "theme",
            "automaticUpdates": "automatic_updates",
            "defaultCompressionProfile": "default_compression_profile",
            "automaticCompressionMode": "automatic_compression_mode",
            "automaticCompressionProfile": "automatic_compression_profile",
            "automaticCompressionDelaySeconds": "automatic_compression_delay_seconds",
            "automaticCompressionMaxJobs": "automatic_compression_max_jobs",
            "automaticCompressionMinFreeGb": "automatic_compression_min_free_gb",
            "automaticCompressionNotify": "automatic_compression_notify",
            "automaticCompressionSkippedAppIds": "automatic_compression_skipped_app_ids",
            "automaticCompressionLibraries": "automatic_compression_libraries",
            "cpuLimit": "cpu_limit_percent",
            "cpuUsageLimit": "cpu_limit_percent",
            "gpuLimit": "gpu_limit_percent",
            "gpuUsageLimit": "gpu_limit_percent",
            "backupDirectory": "backup_directory",
            "quarantineDirectory": "quarantine_directory",
            "libraryDirectories": "library_directories",
            "steamInstallationDirectories": "steam_installation_directories",
            "ignoredSteamLibraries": "ignored_steam_libraries",
            "experimentalFeatures": "experimental_features",
            "logLevel": "log_level",
            "showSteamToolsAndRuntimes": "show_steam_tools_and_runtimes",
            "controllerMode": "controller_mode",
            "swapAcceptBack": "swap_accept_back",
            "analogDeadzone": "analog_deadzone",
            "navigationRepeatDelayMs": "navigation_repeat_delay_ms",
            "navigationRepeatRateMs": "navigation_repeat_rate_ms",
            "hideCursorInCouchMode": "hide_cursor_in_couch_mode",
            "startCouchModeFullscreen": "start_couch_mode_fullscreen",
            "postLaunchBehavior": "post_launch_behavior",
            "interfaceSounds": "interface_sounds",
        }
        requested = aliases.get(key, key)
        if not is_dataclass(self._settings_model):
            return None
        valid_fields = {field.name for field in fields(self._settings_model)}
        return requested if requested in valid_fields else None

    def _coerce_setting_value(
        self,
        current_value: Any,
        value: Any,
        *,
        field_name: str = "",
    ) -> Any:
        if isinstance(current_value, Enum):
            return self._coerce_enum(type(current_value), str(value))
        if isinstance(current_value, Path):
            path_text = str(value)
            if not path_text.strip():
                raise ValueError("directory path cannot be empty")
            return Path(path_text).expanduser()
        if isinstance(current_value, bool):
            return self._coerce_bool(value)
        if isinstance(current_value, int) and not isinstance(current_value, bool):
            return int(value)
        if isinstance(current_value, float):
            return float(value)
        if isinstance(current_value, tuple):
            if isinstance(value, (str, bytes)):
                items = (str(value),)
            else:
                items = tuple(value)
            if field_name == "automatic_compression_skipped_app_ids":
                normalized = tuple(str(item).strip() for item in items)
                if any(
                    not item.isascii()
                    or not item.isdecimal()
                    or int(item) <= 0
                    for item in normalized
                ):
                    raise ValueError(
                        "skipped AppIDs must contain positive decimal Steam AppIDs"
                    )
                return tuple(dict.fromkeys(normalized))
            path_tuple = field_name in {
                "library_directories",
                "steam_installation_directories",
                "automatic_compression_libraries",
                "ignored_steam_libraries",
            } or bool(current_value and isinstance(current_value[0], Path))
            if path_tuple:
                path_values = tuple(str(item) for item in items)
                if any(not item.strip() for item in path_values):
                    raise ValueError("library directory paths cannot be empty")
                return tuple(Path(item).expanduser() for item in path_values)
            return items
        if isinstance(current_value, list):
            if isinstance(value, (str, bytes)):
                return [str(value)]
            return list(value)
        return value

    @staticmethod
    def _coerce_enum(enum_type: type[Enum], value: str) -> Any:
        normalized = str(value).strip().casefold().replace("_", " ").replace("-", " ")
        for member in enum_type:
            candidates = {
                str(member.value).casefold().replace("_", " ").replace("-", " "),
                member.name.casefold().replace("_", " "),
            }
            if normalized in candidates:
                return member
        valid_values = ", ".join(str(member.value) for member in enum_type)
        raise ValueError(f"expected one of: {valid_values}")

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in {0, 1}:
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise ValueError("expected a boolean value")

    @staticmethod
    def _extract_theme_mode(settings: Mapping[str, Any]) -> str:
        mode = str(settings.get("themeMode", settings.get("theme_mode", "system"))).lower()
        return mode if mode in {"system", "dark", "light"} else "system"

    @staticmethod
    def _normalize_page(page: str) -> str | None:
        compact = str(page).strip().replace("_", "").replace("-", "").lower()
        aliases = {
            "games": "games",
            "updates": "updates",
            "tasks": "tasks",
            "system": "system",
            "settings": "settings",
            "gamedetails": "gameDetails",
            "details": "gameDetails",
        }
        normalized = aliases.get(compact)
        return normalized if normalized in _VALID_PAGES else None

    def _set_current_page(self, page: str) -> None:
        if page == self._current_page:
            return
        self._current_page = page
        self.currentPageChanged.emit()

    def _optimization_options(self, options: Mapping[str, Any]) -> OptimizationOptions:
        def option(*names: str, default: Any = None) -> Any:
            for name in names:
                if name in options:
                    return options[name]
            return default

        fps_value = option("fpsLimit", "fps_limit")
        fps_limit = int(fps_value) if fps_value not in (None, "", 0, "0") else None
        return OptimizationOptions(
            profile=self._coerce_enum(
                OptimizationProfile, str(option("profile", default="Balanced"))
            ),
            gamemode=self._coerce_bool(
                option("gamemode", "gameMode", default=True)
            ),
            gamescope=self._coerce_bool(option("gamescope", default=False)),
            mangohud=self._coerce_bool(
                option("mangohud", "mangoHud", default=False)
            ),
            fps_limit=fps_limit,
            adaptive_sync=self._coerce_bool(
                option("adaptiveSync", "adaptive_sync", default=True)
            ),
            cursor_grab=self._coerce_bool(
                option("cursorGrab", "cursor_grab", default=False)
            ),
            cpu_performance_profile=self._coerce_bool(
                option("cpuPerformanceProfile", "cpu_performance_profile", default=False)
            ),
            memory_monitoring=self._coerce_bool(
                option("memoryMonitoring", "memory_monitoring", default=True)
            ),
            optiscaler=self._coerce_bool(
                option("optiscaler", "optiScaler", default=False)
            ),
        )

    def _optimization_displays(self) -> list[Any]:
        application = QGuiApplication.instance()
        if application is None:
            return []
        try:
            return list(self._display_detector.from_application(application))
        except Exception as error:
            logger.warning("Could not detect displays: %s", error)
            return []

    def _optimization_display_for(self, display_id: str) -> Any | None:
        displays = self._optimization_displays()
        return next(
            (display for display in displays if display.display_id == display_id),
            next((display for display in displays if display.primary), displays[0] if displays else None),
        )

    def _optimization_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> GameOptimizationProfile:
        base = self._optimization_profile_repository.load(app_id)
        aliases = {
            "gameCategory": "game_category", "userGoal": "user_goal",
            "targetDisplayId": "target_display_id", "targetFpsMode": "target_fps_mode",
            "targetFps": "target_fps", "gamemodeEnabled": "gamemode_enabled",
            "gamescopeEnabled": "gamescope_enabled", "gamescopeMode": "gamescope_mode",
            "gamescopeInputWidth": "gamescope_input_width",
            "gamescopeInputHeight": "gamescope_input_height",
            "gamescopeOutputWidth": "gamescope_output_width",
            "gamescopeOutputHeight": "gamescope_output_height",
            "gamescopeRefreshRate": "gamescope_refresh_rate",
            "gamescopeFullscreen": "gamescope_fullscreen",
            "gamescopeScaler": "gamescope_scaler", "gamescopeFilter": "gamescope_filter",
            "manualOverrides": "manual_overrides", "lastRecommendation": "last_recommendation",
        }
        data = base.to_dict()
        for key, value in values.items():
            normalized = aliases.get(str(key), str(key))
            if normalized in data and normalized not in {"schema_version", "app_id", "updated_at"}:
                data[normalized] = value
        data.update({"schema_version": base.schema_version, "app_id": app_id, "updated_at": datetime.now(UTC)})
        return GameOptimizationProfile.from_dict(data, expected_app_id=app_id)

    def _optimization_profile_to_qml(
        self, profile: GameOptimizationProfile
    ) -> dict[str, Any]:
        displays = self._optimization_displays()
        display = self._optimization_display_for(profile.target_display_id)
        recommendation = self._optimization_advisor.recommend(profile, display)
        gamemode, gamescope = self._runtime_tool_detector.detect()
        try:
            mangohud_fps_limit = self._mangohud_repository.load(
                profile.app_id
            ).fps_limit
        except ValueError:
            mangohud_fps_limit = None
        try:
            optiscaler_profile = self._optiscaler_service.profile_repository.load(
                profile.app_id
            )
            optiscaler_override = (
                optiscaler_profile.proton_override
                if optiscaler_profile.enabled
                and optiscaler_profile.installation_state == "installed"
                else ""
            )
        except (OSError, ValueError, OptiScalerError):
            optiscaler_override = ""
        plan = self._optimization_launch_planner.build(
            profile, ["%command%"], gamemode=gamemode, gamescope=gamescope,
            mangohud_fps_limit=mangohud_fps_limit,
            optiscaler_override=optiscaler_override,
            existing_wine_overrides=os.environ.get("WINEDLLOVERRIDES", ""),
            allow_placeholder=True,
        )
        runner = self._runner_integration.status()
        data = profile.to_dict()
        data.update({
            "success": True, "schemaVersion": profile.schema_version,
            "appId": profile.app_id, "gameCategory": profile.game_category,
            "userGoal": profile.user_goal, "targetDisplayId": profile.target_display_id,
            "targetFpsMode": profile.target_fps_mode, "targetFps": profile.target_fps,
            "gamemodeEnabled": profile.gamemode_enabled,
            "gamescopeEnabled": profile.gamescope_enabled,
            "gamescopeMode": profile.gamescope_mode,
            "gamescopeInputWidth": profile.gamescope_input_width,
            "gamescopeInputHeight": profile.gamescope_input_height,
            "gamescopeOutputWidth": profile.gamescope_output_width,
            "gamescopeOutputHeight": profile.gamescope_output_height,
            "gamescopeRefreshRate": profile.gamescope_refresh_rate,
            "gamescopeFullscreen": profile.gamescope_fullscreen,
            "gamescopeScaler": profile.gamescope_scaler,
            "gamescopeFilter": profile.gamescope_filter,
            "manualOverrides": dict(profile.manual_overrides),
            "lastRecommendation": dict(profile.last_recommendation),
            "updatedAt": profile.updated_at.astimezone(UTC).isoformat(),
            "displays": [item.to_dict() for item in displays],
            "recommendation": recommendation.to_dict(),
            "gamemode": gamemode.to_dict(), "gamescope": gamescope.to_dict(),
            "launchPlan": plan.to_dict(),
            "launchPlanText": shlex.join(plan.command),
            "fpsLimitOwner": plan.fps_limit_owner,
            "steamLaunchCommand": self._runner_integration.steam_command(profile.app_id),
            "runner": runner.to_dict(),
            "profilePath": str(self._optimization_profile_repository.path(profile.app_id)),
            "renderingSummary": (
                f"The game will render at {profile.gamescope_input_width}×{profile.gamescope_input_height} "
                f"and display at {profile.gamescope_output_width}×{profile.gamescope_output_height}"
            ),
            "protonOverrides": (
                [plan.environment["WINEDLLOVERRIDES"]]
                if "WINEDLLOVERRIDES" in plan.environment else []
            ),
        })
        return data

    def _provider_launch_preview(
        self, game: Game, options: OptimizationOptions
    ) -> str:
        for method_name in (
            "preview_command",
            "generate_command_preview",
            "build_launch_preview",
            "generate_launch_preview",
            "build_launch_command",
            "generate_launch_command",
        ):
            method = getattr(self._optimization_provider, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(game, options)
            except TypeError:
                result = method(options)
            if isinstance(result, str):
                return result
            for attribute in ("preview", "command", "command_preview"):
                value = getattr(result, attribute, None)
                if isinstance(value, str):
                    return value
        raise AttributeError("optimization provider cannot build a launch preview")

    @staticmethod
    def _apply_runtime_log_level(level_name: str) -> None:
        normalized = level_name.upper()
        level = logging.getLevelNamesMapping().get(normalized, logging.INFO)
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)

    def _emit_toast(self, message: str, level: str) -> None:
        normalized_level = level.lower()
        if normalized_level not in {"info", "success", "warning", "error"}:
            normalized_level = "info"
        self.toastRequested.emit(str(message), normalized_level)

    @staticmethod
    def _scan_task_id(generation: int) -> str:
        return f"library-scan-{generation}"

    @staticmethod
    def _size_task_id(generation: int, game_id: str) -> str:
        return f"size-scan-{generation}-{game_id}"

    @staticmethod
    def _operational_task(
        *,
        task_id: str,
        title: str,
        operation: str,
        status: str,
        progress: float,
        game_id: str = "",
        game_name: str = "Steam library",
        created_at: str | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        timestamp = created_at or datetime.now(UTC).isoformat()
        return {
            "id": task_id,
            "gameId": game_id,
            "gameName": game_name,
            "name": title,
            "title": title,
            "operation": operation,
            "type": operation,
            "progress": min(1.0, max(0.0, float(progress))),
            "progressPercent": min(100.0, max(0.0, float(progress) * 100.0)),
            "speed": "-",
            "remaining": "-",
            "status": status,
            "result": {},
            "error": error,
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "cancellable": False,
        }

    def _update_operational_task(
        self,
        task_id: str,
        *,
        progress: float,
        status: str,
        error: str = "",
    ) -> None:
        task = self._operational_tasks.get(task_id)
        if task is None:
            return
        task["progress"] = min(1.0, max(0.0, float(progress)))
        task["progressPercent"] = task["progress"] * 100.0
        task["status"] = status
        task["error"] = error
        task["updatedAt"] = datetime.now(UTC).isoformat()
        self._reload_tasks()

    def _report_error(self, action: str, error: Exception) -> None:
        logger.exception("Error while %s: %s", action, error)
        self._emit_toast(f"Could not finish {action}. See the log for details.", "error")


__all__ = ["AppController"]
