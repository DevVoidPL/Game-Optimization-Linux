from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
import time
from typing import Any, Sequence

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import (
    AppSettings,
    AutomaticCompressionMode,
    CompressionFile,
    CompressionMeasurement,
    CompressionPlan,
    CompressionProfile,
    CompressionToolCapabilities,
    FilesystemInfo,
    FilesystemType,
    Game,
    GameStatus,
    Launcher,
    SessionType,
    SystemInfo,
    Task,
    TaskStatus,
    TaskType,
)
from game_optimization_linux.services import (
    GameChangeSet,
    GameUpdateRecord,
    GameUpdateStatus,
    SettingsStore,
)


_QT_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _GameProvider:
    def __init__(self, games: Sequence[Game]) -> None:
        self._games = {game.id: game for game in games}

    def list_games(self) -> tuple[Game, ...]:
        return tuple(self._games.values())

    def get_game(self, game_id: str) -> Game | None:
        return self._games.get(game_id)

    def add_game(self, game: Game) -> Game:
        self._games[game.id] = game
        return game

    def refresh(self) -> tuple[Game, ...]:
        return self.list_games()


class _FilesystemProvider:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            device="/dev/test",
            mount_options=("rw",),
            writable=True,
            filesystem_name="btrfs",
            size_bytes=16 * 1024**3,
            used_bytes=4 * 1024**3,
            available_bytes=12 * 1024**3,
        )

    def list_filesystems(self, **_: object) -> tuple[FilesystemInfo, ...]:
        return ()


class _SystemProvider:
    def collect(self) -> SystemInfo:
        return SystemInfo(
            distribution="Controller Test Linux",
            kernel="test",
            desktop_environment="test",
            session_type=SessionType.UNKNOWN,
            cpu="Test CPU",
            gpu="Test GPU",
            ram_gb=8.0,
            vram_gb=4.0,
        )


class _TaskService:
    def __init__(self) -> None:
        self.tasks: list[Task] = []
        self.analysis_calls: list[str] = []
        self.compression_calls: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.shutdown_calls: list[tuple[bool, float | None]] = []

    def enqueue_analysis(self, game: Game) -> Task:
        self.analysis_calls.append(game.id)
        task = Task(
            id=f"analysis-{len(self.analysis_calls)}",
            game_id=game.id,
            game_name=game.name,
            task_type=TaskType.ANALYSIS,
            title=f"Analyze {game.name}",
        )
        self.tasks.append(task)
        return task

    def enqueue_compression(
        self,
        game: Game,
        profile: CompressionProfile,
    ) -> Task:
        del profile
        return self.enqueue_compression_plan(
            game,
            object(),
            confirmed=True,
        )

    def enqueue_compression_plan(
        self,
        game: Game,
        plan: Any,
        *,
        confirmed: bool,
        automatic_authorized: bool = False,
    ) -> Task:
        self.compression_calls.append(
            {
                "game": game,
                "plan": plan,
                "confirmed": confirmed,
                "automatic_authorized": automatic_authorized,
            }
        )
        task = Task(
            id=f"compression-{len(self.compression_calls)}",
            game_id=game.id,
            game_name=game.name,
            task_type=TaskType.COMPRESSION,
            title=f"Compress {game.name}",
        )
        self.tasks.append(task)
        return task

    def list_tasks(self) -> tuple[Task, ...]:
        return tuple(self.tasks)

    def tick(self, step: float = 10.0) -> None:
        del step

    def pause(self, task_id: str) -> Task:
        task = self._task(task_id)
        task.status = TaskStatus.PAUSED
        return task

    def resume(self, task_id: str) -> Task:
        task = self._task(task_id)
        task.status = TaskStatus.RUNNING
        return task

    def cancel(self, task_id: str) -> Task:
        task = self._task(task_id)
        task.status = TaskStatus.CANCELLED
        self.cancelled.append(task_id)
        return task

    def shutdown(self, *, wait: bool = True, timeout: float | None = None) -> None:
        self.shutdown_calls.append((wait, timeout))

    def _task(self, task_id: str) -> Task:
        return next(task for task in self.tasks if task.id == task_id)


class _CompressionService:
    def __init__(self, history: Sequence[dict[str, Any]] = ()) -> None:
        self.plans: dict[str, CompressionPlan] = {}
        self.prepare_calls: list[dict[str, Any]] = []
        self.cancel_all_calls = 0
        self.shutdown_calls = 0
        self.last_error = ""
        self._history = tuple(dict(entry) for entry in history)

    def recover_interrupted(self) -> tuple[object, ...]:
        return ()

    def capabilities(self) -> CompressionToolCapabilities:
        return CompressionToolCapabilities(
            btrfs_available=True,
            btrfs_version="test",
            compsize_available=False,
            property_supported=True,
            recompression_supported=True,
            level_supported=True,
            message="Test capability",
        )

    def active_game_ids(self) -> tuple[str, ...]:
        return ()

    def prepare(
        self,
        game: Game,
        report: Any,
        profile: CompressionProfile,
        *,
        changed_only: bool,
        after_update: bool,
        confirmation_required: bool,
        minimum_free_bytes: int,
    ) -> CompressionPlan:
        self.prepare_calls.append(
            {
                "game": game,
                "report": report,
                "profile": profile,
                "changed_only": changed_only,
                "after_update": after_update,
                "confirmation_required": confirmation_required,
                "minimum_free_bytes": minimum_free_bytes,
            }
        )
        plan = _compression_plan(
            game,
            profile=profile,
            number=len(self.prepare_calls),
            changed_only=changed_only,
            confirmation_required=confirmation_required,
        )
        self.plans[plan.id] = plan
        return plan

    def get_plan(self, plan_id: str) -> CompressionPlan | None:
        return self.plans.get(plan_id)

    def history(self, game_id: str | None = None) -> tuple[dict[str, Any], ...]:
        if not game_id:
            return self._history
        return tuple(
            entry for entry in self._history if entry.get("game_id") == game_id
        )

    def cancel_all(self) -> None:
        self.cancel_all_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _UpdateTracker:
    def __init__(
        self,
        records: Sequence[GameUpdateRecord] = (),
        *,
        block_observation: bool = False,
    ) -> None:
        self._records = {record.game_id: record for record in records}
        self.initial_inventory_complete = True
        self.block_observation = block_observation
        self.observation_started = Event()
        self.observation_cancelled = Event()
        self.inventory_completed = False

    def list_records(self) -> tuple[GameUpdateRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def get(self, game_id: str) -> GameUpdateRecord | None:
        return self._records.get(game_id)

    def observe(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
    ) -> GameUpdateRecord:
        self.observation_started.set()
        if self.block_observation:
            assert cancel_event is not None
            if cancel_event.wait(2.0):
                self.observation_cancelled.set()
        return self._records.get(game.id) or GameUpdateRecord(
            game_id=game.id,
            app_id=str(game.steam_app_id or game.id),
            status=GameUpdateStatus.UP_TO_DATE,
        )

    def complete_initial_inventory(self) -> None:
        self.inventory_completed = True
        self.initial_inventory_complete = True

    def ignore(self, game_id: str) -> GameUpdateRecord:
        record = self._records[game_id]
        return record

    def record_verified_compression(self, game: Game) -> GameUpdateRecord:
        return self._records[game.id]


@dataclass(slots=True)
class _Harness:
    controller: AppController
    game: Game
    tasks: _TaskService
    compression: _CompressionService
    tracker: _UpdateTracker | None


def _game(tmp_path: Path, app_id: str = "42") -> Game:
    library = tmp_path / f"library-{app_id}"
    install_path = library / "steamapps" / "common" / f"Fixture-{app_id}"
    install_path.mkdir(parents=True)
    (install_path / "payload.bin").write_bytes(b"A" * 4096)
    return Game(
        id=f"steam-{app_id}",
        name=f"Fixture {app_id}",
        launcher=Launcher.STEAM,
        install_path=install_path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        status=GameStatus.READY,
        steam_app_id=app_id,
        library_path=library,
        steam_build_id="100",
        library_available=True,
        is_writable=True,
    )


def _compression_plan(
    game: Game,
    *,
    profile: CompressionProfile = CompressionProfile.BALANCED,
    number: int = 1,
    changed_only: bool = False,
    confirmation_required: bool = True,
) -> CompressionPlan:
    payload = game.install_path / "payload.bin"
    stat_result = payload.stat()
    before = CompressionMeasurement(
        logical_bytes=stat_result.st_size,
        physical_bytes=stat_result.st_blocks * 512,
        exclusive_bytes=stat_result.st_blocks * 512,
        shared_bytes=0,
        compsize_disk_bytes=None,
        compsize_uncompressed_bytes=None,
        compsize_referenced_bytes=None,
        scan_complete=True,
        shared_extent_state="not_detected",
    )
    return CompressionPlan(
        id=f"plan-{number}",
        game_id=game.id,
        app_id=str(game.steam_app_id),
        game_name=game.name,
        game_path=str(game.install_path),
        profile=profile,
        persistent_compression_algorithm="zstd",
        one_time_recompression_level={
            CompressionProfile.FAST: 1,
            CompressionProfile.BALANCED: 3,
            CompressionProfile.MAXIMUM: 9,
            CompressionProfile.AUTO: 6,
        }[profile],
        files=(
            CompressionFile(
                relative_path=payload.name,
                size_bytes=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
                ctime_ns=stat_result.st_ctime_ns,
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
            ),
        ),
        skipped_files=(),
        full_compression=not changed_only,
        after_update=changed_only,
        build_id=game.steam_build_id,
        estimated_savings_low_bytes=512,
        estimated_savings_high_bytes=1024,
        estimated_shared_growth_bytes=0,
        available_bytes=12 * 1024**3,
        required_free_bytes=1024,
        before=before,
        eligible=True,
        confirmation_required=confirmation_required,
    )


def _analysis_report(game: Game) -> dict[str, Any]:
    return {
        "analyzer_version": 2,
        "game_id": game.id,
        "app_id": str(game.steam_app_id),
        "game_name": game.name,
        "path": str(game.install_path),
        "path_exists": True,
        "path_is_directory": True,
        "filesystem": "Btrfs",
        "is_btrfs": True,
        "writable": True,
        "scan_complete": True,
        "profiles_unlocked": True,
        "compression_eligible": True,
        "game_running": False,
        "btrfs_du": {
            "available": True,
            "state": "not_detected",
            "total_bytes": 4096,
            "exclusive_bytes": 4096,
            "set_shared_bytes": 0,
            "estimated_growth_bytes": 0,
            "message": "No shared extents",
        },
    }


def _record(
    game: Game,
    status: GameUpdateStatus,
    *,
    installation: bool = False,
) -> GameUpdateRecord:
    return GameUpdateRecord(
        game_id=game.id,
        app_id=str(game.steam_app_id),
        status=status,
        changes=GameChangeSet(
            new_files=("new.bin",),
            modified_files=("payload.bin",),
            changed_bytes=8192,
            reliable=True,
        ),
        requires_full_analysis=installation,
        installation_detected=installation,
        last_error="fixture error" if status is GameUpdateStatus.ERROR else "",
        detected_at=datetime.now(UTC),
    )


def _harness(
    tmp_path: Path,
    *,
    settings: AppSettings | None = None,
    records: Sequence[GameUpdateRecord] = (),
    history: Sequence[dict[str, Any]] = (),
    tracker: _UpdateTracker | None = None,
) -> _Harness:
    game = _game(tmp_path)
    tasks = _TaskService()
    compression = _CompressionService(history)
    actual_tracker = tracker
    if actual_tracker is None and records:
        actual_tracker = _UpdateTracker(records)
    store = SettingsStore(
        tmp_path / "settings.json",
        default_factory=lambda: settings or AppSettings(),
    )
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=store,
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=actual_tracker,  # type: ignore[arg-type]
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    return _Harness(controller, game, tasks, compression, actual_tracker)


def test_invalid_plan_has_explicit_boolean_defaults(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        plan = harness.controller.prepareCompression(
            harness.game.id,
            "Balanced",
            False,
        )

        assert plan["id"] == ""
        assert plan["planId"] == ""
        assert plan["blockers"] == ["A completed analysis is required"]
        assert plan["warnings"] == []
        for key in ("valid", "eligible", "canStart", "confirmationRequired"):
            assert type(plan[key]) is bool
        assert plan["valid"] is False
        assert plan["eligible"] is False
        assert plan["canStart"] is False
        assert plan["confirmationRequired"] is True
    finally:
        harness.controller.shutdown()


@pytest.mark.parametrize(
    "terminal_status",
    (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    ),
)
def test_verification_terminal_state_dismisses_authorization_toast(
    tmp_path: Path,
    terminal_status: TaskStatus,
) -> None:
    harness = _harness(tmp_path)
    dismissed: list[str] = []
    harness.controller.toastDismissRequested.connect(dismissed.append)
    task = Task(
        id=f"verification-{terminal_status.value}",
        game_id=harness.game.id,
        game_name=harness.game.name,
        task_type=TaskType.VERIFICATION,
        title=f"Verify compression for {harness.game.name}",
    )
    task.status = terminal_status
    if terminal_status is TaskStatus.FAILED:
        task.error = "compsize exited with status 1: No files."
    harness.tasks.tasks.append(task)

    try:
        harness.controller._poll_tasks()
        assert dismissed == [
            "Waiting for authorization to measure compression"
        ]
    finally:
        harness.controller.shutdown()


def test_newest_verification_replaces_old_error_and_updates_game_header(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    now = datetime.now(UTC)
    old_failure = Task(
        id="verification-old-failure",
        game_id=harness.game.id,
        game_name=harness.game.name,
        task_type=TaskType.VERIFICATION,
        title=f"Verify compression for {harness.game.name}",
        status=TaskStatus.FAILED,
        created_at=now - timedelta(minutes=2),
        updated_at=now - timedelta(minutes=2),
        error="compsize exited with status 1: No files.",
    )
    current_success = Task(
        id="verification-current-success",
        game_id=harness.game.id,
        game_name=harness.game.name,
        task_type=TaskType.VERIFICATION,
        title=f"Verify compression for {harness.game.name}",
        status=TaskStatus.COMPLETED,
        progress=100.0,
        created_at=now,
        updated_at=now,
        result={
            "logical_bytes": 4096,
            "compsize_disk_bytes": 3072,
            "compsize_uncompressed_bytes": 4096,
            "compsize_referenced_bytes": 4096,
            "measurement_source": "polkit_helper",
        },
    )
    # Deliberately store the old error after the success: timestamps, not list
    # order, decide which terminal result belongs on screen.
    harness.tasks.tasks.extend((current_success, old_failure))

    try:
        assert harness.controller.openGame(harness.game.id)
        selected = harness.controller.selectedGame
        assert selected["verificationTaskId"] == current_success.id
        assert selected["verificationError"] == ""
        assert selected["physicalSizeBytes"] == 3072
        assert selected["savedBytes"] == 1024
        assert selected["physicalSize"] != "Measurement unavailable"
        assert selected["savedSpace"] != "Measurement unavailable"
    finally:
        harness.controller.shutdown()


def test_low_additional_benefit_warns_without_blocking_manual_plan(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    gib = 1024**3
    verification = Task(
        id="verification-low-benefit",
        game_id=harness.game.id,
        game_name=harness.game.name,
        task_type=TaskType.VERIFICATION,
        title=f"Verify compression for {harness.game.name}",
        status=TaskStatus.COMPLETED,
        progress=100.0,
        result={
            "logical_bytes": 10 * gib,
            "compsize_disk_bytes": 9 * gib,
            "compsize_uncompressed_bytes": 10 * gib,
            "compsize_referenced_bytes": 10 * gib,
            "measurement_source": "polkit_helper",
        },
    )
    harness.tasks.tasks.append(verification)

    class LowBenefitCatalog:
        @staticmethod
        def estimate_for(game: Game) -> dict[str, Any]:
            return {
                "available": True,
                "appId": str(game.steam_app_id),
                "buildId": str(game.steam_build_id),
                "baselineBytes": 100 * gib,
                "levels": {
                    "3": {
                        "level": 3,
                        "estimatedPhysicalBytes": 94 * gib,
                        "potentialFromBenchmarkBaselineBytes": 6 * gib,
                    }
                },
            }

    harness.controller._benchmark_estimates = LowBenefitCatalog()  # type: ignore[assignment]
    harness.controller._analysis_reports[harness.game.id] = _analysis_report(
        harness.game
    )

    try:
        plan = harness.controller.prepareCompression(
            harness.game.id,
            "Balanced",
            False,
        )

        assert plan["valid"] is True
        assert plan["canStart"] is True
        assert plan["blockers"] == []
        assert plan["lowBenefit"] is True
        assert plan["additionalConfirmationRequired"] is True
        assert plan["estimatedAdditionalSavingBytes"] == 0
    finally:
        harness.controller.shutdown()


def test_prepare_and_confirm_start_use_services_without_btrfs_writes(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    try:
        harness.controller._analysis_reports[harness.game.id] = _analysis_report(
            harness.game
        )

        presented = harness.controller.prepareCompression(
            harness.game.id,
            "Maximum",
            True,
        )

        assert presented["valid"] is True
        assert presented["eligible"] is True
        assert presented["canStart"] is True
        assert presented["fullCompression"] is False
        assert presented["afterUpdate"] is True
        assert presented["oneTimeRecompressionLevel"] == 9
        assert harness.compression.prepare_calls[0]["profile"] is CompressionProfile.MAXIMUM
        assert harness.compression.prepare_calls[0]["changed_only"] is True
        assert harness.tasks.compression_calls == []

        assert harness.controller.startCompression(presented["planId"]) is True

        queued = harness.tasks.compression_calls[0]
        assert queued["game"] is harness.game
        assert queued["plan"].id == presented["planId"]
        assert queued["confirmed"] is True
        assert queued["automatic_authorized"] is False
        assert harness.controller.currentPage == "tasks"
        assert harness.controller.hasActiveCompressionTasks is True
    finally:
        harness.controller.shutdown()


def test_active_compression_property_and_cancel_slot_are_fail_closed(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    analysis = harness.tasks.enqueue_analysis(harness.game)
    active = harness.tasks.enqueue_compression_plan(
        harness.game,
        _compression_plan(harness.game),
        confirmed=True,
    )
    completed = harness.tasks.enqueue_compression_plan(
        harness.game,
        _compression_plan(harness.game, number=2),
        confirmed=True,
    )
    completed.status = TaskStatus.COMPLETED
    try:
        assert harness.controller.hasActiveCompressionTasks is True
        assert harness.controller.cancelActiveCompressionTasks() is True

        assert active.id in harness.tasks.cancelled
        assert completed.id not in harness.tasks.cancelled
        assert analysis.id not in harness.tasks.cancelled
        # A normal per-task cancellation must not set the provider's global
        # shutdown latch and poison a later task.
        assert harness.compression.cancel_all_calls == 0
        assert harness.controller.hasActiveCompressionTasks is False
    finally:
        harness.controller.shutdown()


def test_updates_mapping_and_summary_have_stable_types(tmp_path: Path) -> None:
    game = _game(tmp_path)
    records = (
        _record(game, GameUpdateStatus.ANALYSIS_REQUIRED),
        GameUpdateRecord(
            game_id="steam-99",
            app_id="99",
            status=GameUpdateStatus.ERROR,
            last_error="scan failed",
        ),
        GameUpdateRecord(
            game_id="steam-100",
            app_id="100",
            status=GameUpdateStatus.UP_TO_DATE,
        ),
    )
    history = (
        {
            "id": "history-1",
            "game_id": game.id,
            "game_name": game.name,
            "status": "completed",
            "actual_saved_bytes": 2048,
        },
    )
    tracker = _UpdateTracker(records)
    tasks = _TaskService()
    compression = _CompressionService(history)
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=tracker,  # type: ignore[arg-type]
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        update_rows = [
            row for row in controller.updates if row["sectionKey"] != "recently_optimized"
        ]
        assert len(update_rows) == 3
        for row in update_rows:
            for key in (
                "libraryAvailable",
                "canAnalyze",
                "canCompress",
                "canIgnore",
                "requiresFullAnalysis",
                "installationDetected",
                "changesReliable",
                "ignored",
            ):
                assert type(row[key]) is bool

        row = next(item for item in update_rows if item["gameId"] == game.id)
        assert row["compressionState"] == "Analysis required"
        assert row["canAnalyze"] is True
        assert row["canCompress"] is False
        assert row["changedFileCount"] == 2

        assert controller.updatesSummary == {
            "needsCheckCount": 2,
            "updateCount": 2,
            "pendingCount": 1,
            "queuedCount": 1,
            "recentlyOptimizedCount": 1,
            "recentRecoveredBytes": 2048,
        }
    finally:
        controller.shutdown()


def test_automatic_compression_defaults_to_off(tmp_path: Path) -> None:
    game = _game(tmp_path)
    harness = _harness(
        tmp_path / "controller",
        records=(_record(game, GameUpdateStatus.ANALYSIS_REQUIRED),),
    )
    try:
        assert AppSettings().automatic_compression_mode is AutomaticCompressionMode.OFF
        assert harness.controller.settings["automaticCompressionMode"] == "Off"

        harness.controller._queue_eligible_automatic_compression()

        assert harness.tasks.analysis_calls == []
        assert harness.tasks.compression_calls == []
    finally:
        harness.controller.shutdown()


def test_automatic_compression_does_not_duplicate_or_run_during_steam_write(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
        automatic_compression_notify=False,
    )
    harness = _harness(
        tmp_path,
        settings=settings,
        records=(),
    )
    assert harness.tracker is None
    tracker = _UpdateTracker(
        (_record(harness.game, GameUpdateStatus.ANALYSIS_REQUIRED),)
    )
    harness.controller._update_tracker = tracker
    try:
        busy = replace(harness.game, update_in_progress=True)
        harness.controller._domain_games[busy.id] = busy
        harness.controller._queue_eligible_automatic_compression()
        assert harness.tasks.analysis_calls == []

        harness.controller._domain_games[harness.game.id] = harness.game
        harness.controller._queue_eligible_automatic_compression()
        harness.controller._queue_eligible_automatic_compression()
        assert harness.tasks.analysis_calls == [harness.game.id]
        assert harness.tasks.compression_calls == []
    finally:
        harness.controller.shutdown()


@pytest.mark.parametrize("blocked_state", ("running", "ext4"))
def test_automatic_compression_blocks_running_game_and_non_btrfs(
    tmp_path: Path,
    blocked_state: str,
) -> None:
    settings = AppSettings(
        automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
        automatic_compression_notify=False,
    )
    harness = _harness(tmp_path, settings=settings)
    tracker = _UpdateTracker(
        (_record(harness.game, GameUpdateStatus.ANALYSIS_REQUIRED),)
    )
    harness.controller._update_tracker = tracker
    report = _analysis_report(harness.game)
    if blocked_state == "running":
        report["game_running"] = True
        harness.controller._analysis_reports[harness.game.id] = report
        harness.controller._pending_automatic_games.add(harness.game.id)
        harness.controller._start_automatic_compression(tracker.get(harness.game.id))
    else:
        harness.controller._domain_games[harness.game.id] = replace(
            harness.game,
            filesystem=FilesystemType.EXT4,
            filesystem_name="ext4",
            compression_available=False,
        )
        harness.controller._queue_eligible_automatic_compression()
    try:
        assert harness.tasks.analysis_calls == []
        assert harness.tasks.compression_calls == []
    finally:
        harness.controller.shutdown()


def test_automatic_settings_round_trip_including_empty_filters(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    settings_path = tmp_path / "settings.json"
    try:
        assert harness.controller.saveSetting(
            "automaticCompressionMode",
            "After installation and update",
        )
        assert harness.controller.saveSetting(
            "automaticCompressionProfile",
            "Balanced",
        )
        assert harness.controller.saveSetting(
            "automaticCompressionDelaySeconds",
            45,
        )
        assert harness.controller.saveSetting("automaticCompressionMaxJobs", 2)
        assert harness.controller.saveSetting("automaticCompressionMinFreeGb", 3.5)
        assert harness.controller.saveSetting("automaticCompressionNotify", False)
        assert harness.controller.saveSetting(
            "automaticCompressionSkippedAppIds",
            ["42", "99"],
        )
        assert harness.controller.saveSetting(
            "automaticCompressionLibraries",
            [str(tmp_path / "one"), str(tmp_path / "two")],
        )
        assert harness.controller.saveSetting(
            "automaticCompressionSkippedAppIds",
            [],
        )
        assert harness.controller.saveSetting("automaticCompressionLibraries", [])
    finally:
        harness.controller.shutdown()

    restored = SettingsStore(settings_path).load()
    assert (
        restored.automatic_compression_mode
        is AutomaticCompressionMode.AFTER_INSTALLATION_AND_UPDATE
    )
    assert restored.automatic_compression_profile is CompressionProfile.BALANCED
    assert restored.automatic_compression_delay_seconds == 45
    assert restored.automatic_compression_max_jobs == 2
    assert restored.automatic_compression_min_free_gb == 3.5
    assert restored.automatic_compression_notify is False
    assert restored.automatic_compression_skipped_app_ids == ()
    assert restored.automatic_compression_libraries == ()


@pytest.mark.parametrize(
    ("mode", "installation"),
    [
        (AutomaticCompressionMode.AFTER_UPDATE, False),
        (AutomaticCompressionMode.AFTER_INSTALLATION, True),
    ],
)
def test_automatic_queue_stages_safety_analysis_for_update_or_install(
    tmp_path: Path,
    mode: AutomaticCompressionMode,
    installation: bool,
) -> None:
    game = _game(tmp_path)
    tracker = _UpdateTracker(
        (_record(game, GameUpdateStatus.ANALYSIS_REQUIRED, installation=installation),)
    )
    tasks = _TaskService()
    compression = _CompressionService()
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=SettingsStore(
            tmp_path / "settings.json",
            default_factory=lambda: AppSettings(
                automatic_compression_mode=mode,
                automatic_compression_notify=False,
            ),
        ),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=tracker,  # type: ignore[arg-type]
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        controller._queue_eligible_automatic_compression()

        assert tasks.analysis_calls == [game.id]
        assert tasks.compression_calls == []
        assert game.id in controller._pending_automatic_games
    finally:
        controller.shutdown()


def test_completed_update_observation_event_enqueues_automatic_analysis(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    tracker = _UpdateTracker(
        (_record(game, GameUpdateStatus.ANALYSIS_REQUIRED),)
    )
    tasks = _TaskService()
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=SettingsStore(
            tmp_path / "settings.json",
            default_factory=lambda: AppSettings(
                automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
                automatic_compression_notify=False,
            ),
        ),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=_CompressionService(),  # type: ignore[arg-type]
        update_tracker=tracker,  # type: ignore[arg-type]
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        controller._schedule_update_observations()
        deadline = time.monotonic() + 2.0
        while controller._update_jobs and time.monotonic() < deadline:
            if all(future.done() for future, _event in controller._update_jobs.values()):
                break
            time.sleep(0.01)
        controller._poll_update_jobs()
        assert tasks.analysis_calls == [game.id]
    finally:
        controller.shutdown()


def test_automatic_queue_starts_confirmed_service_plan_after_analysis(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    tracker = _UpdateTracker(
        (_record(game, GameUpdateStatus.ANALYSIS_REQUIRED),)
    )
    tasks = _TaskService()
    compression = _CompressionService()
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=SettingsStore(
            tmp_path / "settings.json",
            default_factory=lambda: AppSettings(
                automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
                automatic_compression_profile=CompressionProfile.AUTO,
                automatic_compression_notify=False,
            ),
        ),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=tracker,  # type: ignore[arg-type]
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        controller._analysis_reports[game.id] = _analysis_report(game)

        controller._queue_eligible_automatic_compression()

        assert tasks.analysis_calls == []
        assert len(tasks.compression_calls) == 1
        queued = tasks.compression_calls[0]
        assert queued["confirmed"] is False
        assert queued["automatic_authorized"] is True
        assert queued["plan"].profile is CompressionProfile.AUTO
    finally:
        controller.shutdown()


def test_shutdown_cancels_update_worker_and_closes_task_and_compression_services(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    tracker = _UpdateTracker(
        (_record(game, GameUpdateStatus.UP_TO_DATE),),
        block_observation=True,
    )
    tasks = _TaskService()
    compression = _CompressionService()
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=tracker,  # type: ignore[arg-type]
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    controller._schedule_update_observations()
    assert tracker.observation_started.wait(1.0)

    controller.shutdown()

    assert tracker.observation_cancelled.wait(1.0)
    assert tasks.shutdown_calls == [(True, 2.0)]
    assert compression.shutdown_calls == 1
    assert controller._update_jobs == {}
    assert controller._shutdown_requested is True
    # Shutdown is deliberately idempotent; workers and providers close once.
    controller.shutdown()
    assert tasks.shutdown_calls == [(True, 2.0)]
    assert compression.shutdown_calls == 1
