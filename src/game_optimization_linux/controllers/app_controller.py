"""QObject facade exposed to the QML interface."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_for_futures
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import SimpleQueue
from threading import Event
import time
from typing import Any, Protocol, cast

from PySide6.QtCore import (
    QObject,
    Property,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)

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
    Backup,
    CompressionProfile,
    ControllerMode,
    Game,
    GameOptimizationProfile,
    GameStatus,
    Launcher,
    MangoHudProfile,
    OptiScalerProfile,
    OptimizationOptions,
    OptimizationProfile,
    PostLaunchBehavior,
    SystemInfo,
    Task,
    TaskStatus,
    TaskType,
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
    OptiScalerService,
    RuntimeToolDetector,
    RunnerIntegration,
    GameUpdateStateStore,
    GameUpdateStatus,
    GameUpdateTracker,
    MangoHudDetector,
    MangoHudLaunchIntegration,
    MangoHudProfileRepository,
    SettingsStore,
    PrivilegedMeasurementClient,
    ProtonTweaksRepository,
    TaskHistoryStore,
    SteamLaunchError,
    SteamLauncher,
    UiSoundService,
    UnavailableBackupService,
    UpdateDisplayStateStore,
)
from ..services.library_cache import LibraryCache
from ..services.optiscaler_online import (
    CachedOptiScalerArchive,
    OptiScalerRelease,
    OptiScalerReleaseClient,
)
from .compression_controller import CompressionController
from .couch_navigation import CouchNavigationController
from .games_model import GamesListModel
from .library_controller import LibraryController
from .library_scanner import LibraryScanner
from .mangohud_controller import MangoHudController
from .optimization_controller import OptimizationController
from .optiscaler_controller import OptiScalerController
from .settings_controller import SettingsController
from .system_controller import SystemController
from .updates_controller import UpdatesController
from .presenters import (
    backup_to_qml,
    qml_value,
    settings_to_qml,
)


logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.INTERRUPTED.value,
}
_VALID_PAGES = {"games", "updates", "tasks", "system", "settings", "gameDetails"}


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
    """Protocol for system information providers."""


class OptimizationProviderLike(Protocol):
    def defaults_for(self, profile: OptimizationProfile) -> OptimizationOptions: ...

    def preview_command(self, game: Game, options: OptimizationOptions) -> str: ...


class GameLauncherLike(Protocol):
    def launch(self, game: Game) -> Sequence[str]: ...


class AppController(QObject):
    """Expose application state and operations to QML."""

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
    protonTweaksChanged = Signal(str)

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
        optiscaler_release_client: OptiScalerReleaseClient | None = None,
        proton_tweaks_repository: ProtonTweaksRepository | None = None,
        initial_games: Sequence[Game] | None = None,
        demo_mode: bool | None = None,
        auto_refresh: bool = True,
        initial_interface_mode: str | None = None,
        reset_ui_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._library_controller = LibraryController(self)
        self._compression_controller = CompressionController(self)
        self._updates_controller = UpdatesController(self)
        self._mangohud_controller = MangoHudController(self)
        self._optiscaler_controller = OptiScalerController(self)
        self._optimization_controller = OptimizationController(self)
        self._settings_controller = SettingsController(self)
        self._system_controller = SystemController(self)
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
                demo_mode = os.environ.get("GAME_OPTIMIZATION_DEMO", "").strip() == "1"
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
        self._optiscaler_release_client = (
            optiscaler_release_client or OptiScalerReleaseClient()
        )
        self._optiscaler_online_errors: dict[str, str] = {}
        self._proton_tweaks_repository = (
            proton_tweaks_repository or ProtonTweaksRepository()
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
                thread_name_prefix="game-optimization-updates",
            )
            if self._update_tracker is not None
            else None
        )
        self._update_jobs: dict[str, tuple[Future[GameUpdateRecord], Event]] = {}
        self._optiscaler_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="game-optimization-optiscaler",
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
        self._steam_found = bool(
            not self._demo_mode
            and any(game.launcher is Launcher.STEAM for game in domain_games)
        )
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
        return self._library_controller.refreshGames()

    @Slot(str, str, str, result=bool)
    def requestLibraryScan(
        self,
        trigger: str,
        event_path: str = "",
        event_kind: str = "manual",
    ) -> bool:
        return self._library_controller.requestLibraryScan(trigger, event_path, event_kind)

    @Slot()
    def _start_requested_library_scan(self) -> None:
        return self._library_controller._start_requested_library_scan()

    @Slot()
    def _flush_ignored_scan_events(self) -> None:
        return self._library_controller._flush_ignored_scan_events()

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

    def _has_active_game_optimization_task(self) -> bool:
        return self._library_controller._has_active_game_optimization_task()

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
        return self._library_controller.forgetLibrary(library_path)

    @Slot(str, result=bool)
    def ignoreLibrary(self, library_path: str) -> bool:
        return self._library_controller.ignoreLibrary(library_path)

    @Slot(str, result=bool)
    def restoreIgnoredLibrary(self, library_path: str) -> bool:
        return self._library_controller.restoreIgnoredLibrary(library_path)

    @Slot(str, result="QVariantMap")
    def localExecutableInfo(self, game_id: str) -> dict[str, Any]:
        return self._library_controller.localExecutableInfo(game_id)

    @Slot(str, str, result=bool)
    def selectLocalExecutable(self, game_id: str, executable: str) -> bool:
        return self._library_controller.selectLocalExecutable(game_id, executable)

    @Slot(result=bool)
    def addManualGame(self) -> bool:
        return self._library_controller.addManualGame()

    @Slot(str, result=bool)
    def openGame(self, game_id: str) -> bool:
        return self._library_controller.openGame(game_id)

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
        return self._compression_controller.analyzeGame(game_id)

    @Slot(str, result=bool)
    @Slot(str, str, result=bool)
    def requestCompression(self, game_id: str, mode: str = "Balanced") -> bool:
        return self._compression_controller.requestCompression(game_id, mode)

    @Slot(str, result=bool)
    def analyzeChanges(self, game_id: str) -> bool:
        return self._compression_controller.analyzeChanges(game_id)

    @Slot(str, result=bool)
    def verifyCompression(self, game_id: str) -> bool:
        return self._compression_controller.verifyCompression(game_id)

    @Slot(str, str, bool, result="QVariantMap")
    def prepareCompression(
        self,
        game_id: str,
        mode: str = "Auto",
        changed_only: bool = False,
    ) -> dict[str, Any]:
        return self._compression_controller.prepareCompression(game_id, mode, changed_only)

    @Slot(str, result=bool)
    def startCompression(self, plan_id: str) -> bool:
        return self._compression_controller.startCompression(plan_id)

    @Slot(str, result=bool)
    def ignoreUpdate(self, game_id: str) -> bool:
        return self._updates_controller.ignoreUpdate(game_id)

    @Slot(str, result=bool)
    def dismissUpdate(self, row_id: str) -> bool:
        return self._updates_controller.dismissUpdate(row_id)

    @Slot(result=int)
    def clearFinishedUpdates(self) -> int:
        return self._updates_controller.clearFinishedUpdates()

    @Slot(result=int)
    def clearUnavailableUpdates(self) -> int:
        return self._updates_controller.clearUnavailableUpdates()

    @Slot(result=int)
    def clearHiddenUpdatesHistory(self) -> int:
        return self._updates_controller.clearHiddenUpdatesHistory()

    def _dismiss_update_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self._updates_controller._dismiss_update_rows(rows)

    def _save_update_display_state(self) -> None:
        return self._updates_controller._save_update_display_state()

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
        return self._compression_controller._start_compression_plan(plan_id, confirmed=confirmed, automatic_authorized=automatic_authorized)

    @Slot(str, result=bool)
    def pauseTask(self, task_id: str) -> bool:
        return self._compression_controller.pauseTask(task_id)

    @Slot(str, result=bool)
    def resumeTask(self, task_id: str) -> bool:
        return self._compression_controller.resumeTask(task_id)

    @Slot(str, result=bool)
    def cancelTask(self, task_id: str) -> bool:
        return self._compression_controller.cancelTask(task_id)

    @Slot(result=int)
    def clearFinishedTasks(self) -> int:
        return self._compression_controller.clearFinishedTasks()

    @Slot(str, result=bool)
    def removeFinishedTask(self, task_id: str) -> bool:
        return self._compression_controller.removeFinishedTask(task_id)

    @Slot(result=bool)
    def cancelActiveCompressionTasks(self) -> bool:
        return self._compression_controller.cancelActiveCompressionTasks()

    @Slot(str, "QVariant", result=bool)
    def saveSetting(self, key: str, value: Any) -> bool:
        return self._settings_controller.saveSetting(key, value)

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
        if game.launcher is Launcher.MANUAL and game.data_source.casefold() == "local":
            try:
                command = self._runner_integration.launch_local(game)
            except Exception as error:
                logger.warning("Could not launch local game %s: %s", game.id, error)
                self._emit_toast(str(error), "error")
                return False
            logger.info("Started local game %s through Game Optimization Runner", game.id)
            self._emit_toast(f"Starting {game.name}", "success")
            self.windowActionRequested.emit("stay")
            return bool(command)
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
        return self._mangohud_controller.getMangoHudProfile(game_id)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def previewMangoHudProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._mangohud_controller.previewMangoHudProfile(game_id, values)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def saveMangoHudProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._mangohud_controller.saveMangoHudProfile(game_id, values)

    @Slot(str, result="QVariantMap")
    def resetMangoHudProfile(self, game_id: str) -> dict[str, Any]:
        return self._mangohud_controller.resetMangoHudProfile(game_id)

    @Slot(str, result=bool)
    def openMangoHudDirectory(self, game_id: str) -> bool:
        return self._mangohud_controller.openMangoHudDirectory(game_id)

    @Slot(str, result="QVariantMap")
    def mangoHudLaunchPlan(self, game_id: str) -> dict[str, Any]:
        return self._mangohud_controller.mangoHudLaunchPlan(game_id)

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
        return self._optiscaler_controller.getOptiScalerStatus(game_id)

    @staticmethod
    def _normalized_release_version(value: str) -> str:
        return str(value or "").strip().casefold().removeprefix("v")

    def _cached_optiscaler_release(self) -> OptiScalerRelease | None:
        return self._optiscaler_controller._cached_optiscaler_release()

    def _cached_optiscaler_archive(
        self, release: OptiScalerRelease
    ) -> CachedOptiScalerArchive | None:
        return self._optiscaler_controller._cached_optiscaler_archive(release)

    @Slot(str, str, result="QVariantMap")
    def rememberOptiScalerExecutable(
        self, game_id: str, executable_value: str
    ) -> dict[str, Any]:
        return self._optiscaler_controller.rememberOptiScalerExecutable(game_id, executable_value)

    @Slot(str, bool, result=bool)
    def refreshOptiScalerRelease(self, game_id: str, force_refresh: bool) -> bool:
        return self._optiscaler_controller.refreshOptiScalerRelease(game_id, force_refresh)

    @Slot(str, str, str, bool, result="QVariantMap")
    def inspectOnlineOptiScaler(
        self,
        game_id: str,
        executable: str,
        injection_dll: str,
        allow_anticheat_risk: bool,
    ) -> dict[str, Any]:
        return self._optiscaler_controller.inspectOnlineOptiScaler(game_id, executable, injection_dll, allow_anticheat_risk)

    @Slot(str, str, str, str, bool, bool, result=bool)
    def installOnlineOptiScaler(
        self,
        game_id: str,
        executable: str,
        injection_dll: str,
        operation_name: str,
        allow_replace_conflicts: bool,
        allow_anticheat_risk: bool,
    ) -> bool:
        return self._optiscaler_controller.installOnlineOptiScaler(game_id, executable, injection_dll, operation_name, allow_replace_conflicts, allow_anticheat_risk)

    @Slot(str, str, str, str, result="QVariantMap")
    def inspectOptiScalerArchive(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
    ) -> dict[str, Any]:
        return self._optiscaler_controller.inspectOptiScalerArchive(game_id, archive_value, executable, injection_dll)

    def _start_optiscaler_operation(
        self,
        game: Game,
        action: str,
        operation: Callable[
            [Event, Callable[[str, float], None]], OptiScalerProfile
        ],
    ) -> bool:
        return self._optiscaler_controller._start_optiscaler_operation(game, action, operation)

    @Slot(str, str, str, str, bool, result=bool)
    def installOptiScaler(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
        allow_replace_conflicts: bool,
    ) -> bool:
        return self._optiscaler_controller.installOptiScaler(game_id, archive_value, executable, injection_dll, allow_replace_conflicts)

    @Slot(str, result=bool)
    def removeOptiScaler(self, game_id: str) -> bool:
        return self._optiscaler_controller.removeOptiScaler(game_id)

    @Slot(str, result=bool)
    def restoreOptiScalerFiles(self, game_id: str) -> bool:
        return self._optiscaler_controller.restoreOptiScalerFiles(game_id)

    @Slot(str, result="QVariantMap")
    def verifyOptiScaler(self, game_id: str) -> dict[str, Any]:
        return self._optiscaler_controller.verifyOptiScaler(game_id)

    @Slot(str, result=bool)
    def openOptiScalerDirectory(self, game_id: str) -> bool:
        return self._optiscaler_controller.openOptiScalerDirectory(game_id)

    @Slot(str, result=bool)
    def openOptiScalerManifest(self, game_id: str) -> bool:
        return self._optiscaler_controller.openOptiScalerManifest(game_id)

    @Slot(str, "QVariantMap", result=str)
    def buildLaunchPreview(self, game_id: str, options: Mapping[str, Any]) -> str:
        return self._optimization_controller.buildLaunchPreview(game_id, options)

    @Slot(str, result=bool)
    def playUiSound(self, kind: str) -> bool:
        return self._optimization_controller.playUiSound(kind)

    @Slot(str, result="QVariantMap")
    def optimizationDefaults(self, profile: str) -> dict[str, Any]:
        return self._optimization_controller.optimizationDefaults(profile)

    def _proton_tweaks_to_qml(self, app_id: str) -> dict[str, Any]:
        return self._optimization_controller._proton_tweaks_to_qml(app_id)

    @Slot(str, result="QVariantMap")
    def getProtonTweaks(self, game_id: str) -> dict[str, Any]:
        return self._optimization_controller.getProtonTweaks(game_id)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def saveProtonTweaks(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._optimization_controller.saveProtonTweaks(game_id, values)

    @Slot(str, result="QVariantMap")
    def getOptimizationProfile(self, game_id: str) -> dict[str, Any]:
        return self._optimization_controller.getOptimizationProfile(game_id)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def previewOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._optimization_controller.previewOptimizationProfile(game_id, values)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def saveOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._optimization_controller.saveOptimizationProfile(game_id, values)

    @Slot(str, result="QVariantMap")
    def testGameOptimizationRunner(self, game_id: str) -> dict[str, Any]:
        return self._optimization_controller.testGameOptimizationRunner(game_id)

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
        logger.info("Stopped Game Optimization controller")

    def _configure_gamepad_service(self) -> None:
        self._gamepad_service.configure(
            deadzone=float(self._settings_model.analog_deadzone),
            repeat_delay_ms=int(self._settings_model.navigation_repeat_delay_ms),
            repeat_rate_ms=int(self._settings_model.navigation_repeat_rate_ms),
            swap_accept_back=bool(self._settings_model.swap_accept_back),
        )

    def _add_gamepad_system_info(self) -> None:
        return self._system_controller._add_gamepad_system_info()

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
        return self._library_controller._create_steam_provider()

    def _initial_games(self, initial_games: Sequence[Game] | None) -> list[Game]:
        return self._library_controller._initial_games(initial_games)

    @Slot(int)
    def _on_library_scan_started(self, generation: int) -> None:
        return self._library_controller._on_library_scan_started(generation)

    @Slot(int, object)
    def _on_library_ready(self, generation: int, raw_games: object) -> None:
        return self._library_controller._on_library_ready(generation, raw_games)

    @Slot(int, str)
    def _on_library_failed(self, generation: int, message: str) -> None:
        return self._library_controller._on_library_failed(generation, message)

    @Slot(int, str)
    def _on_game_size_started(self, generation: int, game_id: str) -> None:
        return self._library_controller._on_game_size_started(generation, game_id)

    @Slot(int, str, object)
    def _on_game_size_ready(
        self,
        generation: int,
        game_id: str,
        result: object,
    ) -> None:
        return self._library_controller._on_game_size_ready(generation, game_id, result)
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
        return self._library_controller._on_game_size_failed(generation, game_id, message)
        # See _on_game_size_ready: a failed size result is included in the
        # single final library snapshot instead of rebuilding all delegates.

    @Slot(int)
    def _on_library_scan_finished(self, generation: int) -> None:
        return self._library_controller._on_library_scan_finished(generation)

    def _schedule_coalesced_scan_retry(self) -> None:
        return self._library_controller._schedule_coalesced_scan_retry()

    def _set_scan_state(
        self,
        *,
        status: str | None = None,
        message: str | None = None,
        steam_found: bool | None = None,
        is_scanning: bool | None = None,
    ) -> None:
        return self._library_controller._set_scan_state(status=status, message=message, steam_found=steam_found, is_scanning=is_scanning)

    def _set_domain_games(
        self,
        games: Sequence[Game],
        *,
        reason: str = "domain_games_replaced",
        publish: bool = True,
    ) -> None:
        return self._library_controller._set_domain_games(games, reason=reason, publish=publish)

    def _artwork_roots(self) -> tuple[Path, ...]:
        return self._library_controller._artwork_roots()

    def _resolve_game_artwork(self, game: Game) -> Game:
        return self._library_controller._resolve_game_artwork(game)

    def _present_game(self, game: Game, **kwargs: Any) -> dict[str, Any]:
        return self._library_controller._present_game(game, **kwargs)

    def _merge_unavailable_cached_games(
        self,
        discovered_games: Sequence[Game],
    ) -> list[Game]:
        return self._library_controller._merge_unavailable_cached_games(discovered_games)

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
        return self._library_controller._provider_accessible_library_paths()

    def _log_library_decisions(self, discovered_games: Sequence[Game]) -> None:
        return self._library_controller._log_library_decisions(discovered_games)

    def _provider_inaccessible_paths(self) -> tuple[Path, ...]:
        return self._library_controller._provider_inaccessible_paths()

    def _provider_configured_library_paths(self) -> tuple[Path, ...]:
        return self._library_controller._provider_configured_library_paths()

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
        return self._library_controller._library_is_ignored(value)

    def _game_is_in_ignored_library(self, game: Game) -> bool:
        return self._library_controller._game_is_in_ignored_library(game)

    def _update_record_is_in_ignored_library(
        self,
        record: GameUpdateRecord,
    ) -> bool:
        return self._library_controller._update_record_is_in_ignored_library(record)

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
        return self._library_controller._save_library_cache()

    def _provider_steam_found(self, games: Sequence[Game]) -> bool:
        return self._library_controller._provider_steam_found(games)

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
        return self._compression_controller._compression_fingerprint(game)

    def _mark_compression_verified(self, game: Game) -> None:
        return self._compression_controller._mark_compression_verified(game)

    def _reload_selected_history(self, *, emit_signal: bool = True) -> None:
        return self._compression_controller._reload_selected_history(emit_signal=emit_signal)

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
        return self._updates_controller._active_task_for_game(game_id)

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
        return self._updates_controller._update_record_to_qml(record)

    def _history_update_rows(self) -> list[dict[str, Any]]:
        return self._updates_controller._history_update_rows()

    def _update_row_is_dismissed(self, row: Mapping[str, Any]) -> bool:
        return self._updates_controller._update_row_is_dismissed(row)

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
        return self._updates_controller._reload_updates(emit_signal=emit_signal)

    def _schedule_update_observations(self) -> None:
        return self._updates_controller._schedule_update_observations()

    def _poll_update_jobs(self) -> None:
        return self._updates_controller._poll_update_jobs()

    def _automatic_mode_allows(self, record: GameUpdateRecord) -> bool:
        return self._updates_controller._automatic_mode_allows(record)

    def _automatic_library_allowed(self, game: Game) -> bool:
        return self._updates_controller._automatic_library_allowed(game)

    def _automatic_task_active(self, game_id: str = "") -> bool:
        return self._updates_controller._automatic_task_active(game_id)

    def _queue_eligible_automatic_compression(self) -> None:
        return self._updates_controller._queue_eligible_automatic_compression()

    def _start_automatic_compression(self, record: GameUpdateRecord) -> bool:
        return self._updates_controller._start_automatic_compression(record)

    def _load_settings(self) -> AppSettings:
        return self._settings_controller._load_settings()

    def _reload_games(
        self,
        *,
        emit_signal: bool = True,
        reason: str = "state_update",
    ) -> None:
        return self._library_controller._reload_games(emit_signal=emit_signal, reason=reason)

    def _latest_compression_results(self) -> dict[str, Mapping[str, Any]]:
        return self._compression_controller._latest_compression_results()

    def _latest_verification_results(self) -> dict[str, Mapping[str, Any]]:
        return self._compression_controller._latest_verification_results()

    def _current_authoritative_compsize(self, game_id: str) -> dict[str, Any]:
        return self._compression_controller._current_authoritative_compsize(game_id)

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
        return self._compression_controller._reload_tasks(emit_signal=emit_signal)

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
        return self._system_controller._reload_system_info(emit_signal=emit_signal)

    def _add_compression_system_info(self) -> None:
        return self._system_controller._add_compression_system_info()

    def _add_steam_system_info(self) -> None:
        return self._system_controller._add_steam_system_info()

    def _read_system_info(self) -> SystemInfo:
        return self._system_controller._read_system_info()

    def _read_filesystems(self) -> Sequence[Any]:
        return self._system_controller._read_filesystems()

    def _poll_tasks(self) -> None:
        return self._compression_controller._poll_tasks()

    def _remember_analysis_report(self, task: Task) -> None:
        return self._compression_controller._remember_analysis_report(task)

    def _poll_optiscaler_jobs(self) -> None:
        return self._optiscaler_controller._poll_optiscaler_jobs()

    def _change_task_state(self, action: str, task_id: str) -> bool:
        return self._compression_controller._change_task_state(action, task_id)

    def _find_game(self, game_id: str) -> Game | None:
        return self._library_controller._find_game(game_id)

    def _resolve_game(self, game_id: str, *, show_error: bool = True) -> Game | None:
        return self._library_controller._resolve_game(game_id, show_error=show_error)

    def _mangohud_profile_for_game(self, game: Game) -> MangoHudProfile | None:
        return self._mangohud_controller._mangohud_profile_for_game(game)

    def _mangohud_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> MangoHudProfile:
        return self._mangohud_controller._mangohud_profile_from_payload(app_id, values)

    def _mangohud_profile_to_qml(
        self, game: Game, profile: MangoHudProfile
    ) -> dict[str, Any]:
        return self._mangohud_controller._mangohud_profile_to_qml(game, profile)

    @staticmethod
    def _gamescope_owns_fps_limit(profile: GameOptimizationProfile) -> bool:
        return bool(profile.gamescope_enabled and profile.gamescope_mode != "disabled")

    def _clear_mangohud_fps_limit(self, game: Game) -> None:
        return self._mangohud_controller._clear_mangohud_fps_limit(game)

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
        return self._library_controller._new_manual_game()

    def _setting_field_name(self, key: str) -> str | None:
        return self._settings_controller._setting_field_name(key)

    def _coerce_setting_value(
        self,
        current_value: Any,
        value: Any,
        *,
        field_name: str = "",
    ) -> Any:
        return self._settings_controller._coerce_setting_value(current_value, value, field_name=field_name)

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
        return self._optimization_controller._optimization_options(options)

    def _optimization_displays(self) -> list[Any]:
        return self._optimization_controller._optimization_displays()

    def _optimization_display_for(self, display_id: str) -> Any | None:
        return self._optimization_controller._optimization_display_for(display_id)

    def _optimization_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> GameOptimizationProfile:
        return self._optimization_controller._optimization_profile_from_payload(app_id, values)

    def _optimization_profile_to_qml(
        self, profile: GameOptimizationProfile
    ) -> dict[str, Any]:
        return self._optimization_controller._optimization_profile_to_qml(profile)

    def _provider_launch_preview(
        self, game: Game, options: OptimizationOptions
    ) -> str:
        return self._optimization_controller._provider_launch_preview(game, options)

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
