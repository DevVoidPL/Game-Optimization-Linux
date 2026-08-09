"""Non-blocking task service shared by analysis and real compression work."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_for_futures
from copy import deepcopy
from datetime import UTC, datetime
from threading import Event, RLock
import logging
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

from game_optimization_linux.models.enums import CompressionProfile, TaskStatus, TaskType
from game_optimization_linux.models.compression import CompressionPlan, CompressionResult
from game_optimization_linux.models.game import Game
from game_optimization_linux.models.task import TERMINAL_TASK_STATUSES, Task

from .analysis_cache import AnalysisCache, AnalysisCacheError
from .btrfs_analysis import (
    AnalysisCancelled,
    AnalysisProgress,
    BtrfsAnalysisReport,
    BtrfsCompressionAnalyzer,
)
from .compression import CompressionService
from .privileged_measurement import PrivilegedMeasurementError
from .task_history import MAX_FINISHED_TASKS, TaskHistoryStore
from .tasks import InvalidTaskTransitionError, TaskNotFoundError, TaskServiceError
from .unavailable import FeatureUnavailableError


logger = logging.getLogger(__name__)


class BtrfsAnalysisTaskService:
    """Run at most one analysis worker and expose thread-safe snapshots."""

    def __init__(
        self,
        analyzer: BtrfsCompressionAnalyzer | None = None,
        cache: AnalysisCache | None = None,
        compression_service: CompressionService | None = None,
        history_store: TaskHistoryStore | None = None,
        *,
        max_workers: int = 1,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise TypeError("max_workers must be a positive integer")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._analyzer = analyzer or BtrfsCompressionAnalyzer()
        self._cache = cache
        self._compression_service = compression_service
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="game-optimization-analysis",
        )
        self._history_store = history_store
        restored = history_store.load() if history_store is not None else ()
        self._tasks: dict[str, Task] = {task.id: task for task in restored}
        self._games: dict[str, Game] = {}
        self._cancel_events: dict[str, Event] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = RLock()
        self._closed = False

    def enqueue(self, task: Task) -> Task:
        """Store a queued task; analysis work is started by ``enqueue_analysis``."""

        with self._lock:
            self._ensure_open()
            if task.id in self._tasks:
                raise TaskServiceError(f"task already exists: {task.id}")
            if task.status is not TaskStatus.QUEUED:
                raise InvalidTaskTransitionError("a new task must be queued")
            self._tasks[task.id] = deepcopy(task)
            self._persist_locked()
            return deepcopy(task)

    def enqueue_task(self, task: Task) -> Task:
        return self.enqueue(task)

    def enqueue_analysis(self, game: Game) -> Task:
        with self._lock:
            self._ensure_open()
            duplicate = next(
                (
                    task
                    for task in self._tasks.values()
                    if task.game_id == game.id
                    and task.task_type is TaskType.ANALYSIS
                    and task.status not in TERMINAL_TASK_STATUSES
                ),
                None,
            )
            if duplicate is not None:
                raise TaskServiceError("an analysis for this game is already active")
            task = Task(
                id=f"analysis-{uuid4().hex}",
                game_id=game.id,
                game_name=game.name,
                task_type=TaskType.ANALYSIS,
                title=f"Analyze {game.name}",
                remaining_data_gb=max(0.0, game.logical_size_gb),
                metadata={
                    "stage": "Queued",
                    "scanned_files": 0,
                    "analyzed_bytes": 0,
                    "elapsed_seconds": 0.0,
                    "total_size_gb": max(0.0, game.logical_size_gb),
                    "cancellable": True,
                    "pausable": False,
                    "read_only": True,
                    "cache_hit": False,
                },
            )
            cancel_event = Event()
            self._tasks[task.id] = task
            self._games[task.id] = game
            self._cancel_events[task.id] = cancel_event
            future = self._executor.submit(
                self._run_analysis,
                task.id,
                game,
                cancel_event,
            )
            self._futures[task.id] = future
            self._persist_locked()
            return deepcopy(task)

    def enqueue_compression(
        self,
        game: Game,
        profile: CompressionProfile = CompressionProfile.AUTO,
    ) -> Task:
        del game, profile
        raise FeatureUnavailableError(
            "Prepare and confirm a compression plan before enqueueing it"
        )

    def enqueue_verification(self, game: Game) -> Task:
        service = self._compression_service
        if service is None:
            raise FeatureUnavailableError("The compression service is unavailable")
        with self._lock:
            self._ensure_open()
            duplicate = next(
                (
                    task
                    for task in self._tasks.values()
                    if task.game_id == game.id
                    and task.task_type is TaskType.VERIFICATION
                    and task.status not in TERMINAL_TASK_STATUSES
                ),
                None,
            )
            if duplicate is not None:
                raise TaskServiceError(
                    "a compression verification for this game is already active"
                )
            task = Task(
                id=f"verification-{uuid4().hex}",
                game_id=game.id,
                game_name=game.name,
                task_type=TaskType.VERIFICATION,
                title=f"Verify compression for {game.name}",
                metadata={
                    "stage": "Waiting for authorization",
                    "cancellable": True,
                    "pausable": False,
                    "read_only": True,
                },
            )
            self._tasks[task.id] = task
            self._games[task.id] = game
            cancel_event = Event()
            self._cancel_events[task.id] = cancel_event
            future = self._executor.submit(
                self._run_verification,
                task.id,
                game,
                cancel_event,
            )
            self._futures[task.id] = future
            self._persist_locked()
            return deepcopy(task)

    def enqueue_compression_plan(
        self,
        game: Game,
        plan: CompressionPlan,
        *,
        confirmed: bool,
        automatic_authorized: bool = False,
    ) -> Task:
        """Run one already reviewed plan through the same queue as analysis."""

        service = self._compression_service
        if service is None:
            raise FeatureUnavailableError("The compression service is unavailable")
        if plan.game_id != game.id:
            raise TaskServiceError("the compression plan belongs to another game")
        with self._lock:
            self._ensure_open()
            duplicate = next(
                (
                    task
                    for task in self._tasks.values()
                    if task.game_id == game.id
                    and task.task_type is TaskType.COMPRESSION
                    and task.status not in TERMINAL_TASK_STATUSES
                ),
                None,
            )
            if duplicate is not None:
                raise TaskServiceError(
                    "a compression task for this game is already active"
                )
            task = Task(
                id=f"compression-{uuid4().hex}",
                game_id=game.id,
                game_name=game.name,
                task_type=TaskType.COMPRESSION,
                title=f"Compress {game.name}",
                remaining_data_gb=max(0.0, plan.total_bytes / (1024**3)),
                metadata={
                    "stage": "Queued",
                    "processed_files": 0,
                    "total_files": plan.total_files,
                    "processed_bytes": 0,
                    "total_bytes": plan.total_bytes,
                    "current_file": "",
                    "elapsed_seconds": 0.0,
                    "estimated_remaining_seconds": None,
                    "before_bytes": plan.before.physical_bytes,
                    "after_bytes": None,
                    "saved_bytes": None,
                    "cancellable": True,
                    "pausable": False,
                    "read_only": False,
                    "plan_id": plan.id,
                    "profile": plan.profile.value,
                    "full_compression": plan.full_compression,
                    "after_update": plan.after_update,
                    "automatic": bool(automatic_authorized),
                    "confirmation_recorded": bool(
                        confirmed or automatic_authorized
                    ),
                },
            )
            cancel_event = Event()
            self._tasks[task.id] = task
            self._games[task.id] = game
            self._cancel_events[task.id] = cancel_event
            future = self._executor.submit(
                self._run_compression,
                task.id,
                game,
                plan,
                bool(confirmed),
                bool(automatic_authorized),
                cancel_event,
            )
            self._futures[task.id] = future
            self._persist_locked()
            return deepcopy(task)

    def list_tasks(self) -> Sequence[Task]:
        with self._lock:
            return tuple(deepcopy(task) for task in self._tasks.values())

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task is not None else None

    def clear_finished(self) -> int:
        with self._lock:
            task_ids = [
                task_id
                for task_id, task in self._tasks.items()
                if task.status in TERMINAL_TASK_STATUSES
            ]
            for task_id in task_ids:
                self._drop_task_locked(task_id)
            self._persist_locked()
            return len(task_ids)

    def remove_finished(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status not in TERMINAL_TASK_STATUSES:
                raise InvalidTaskTransitionError("an active task cannot be removed")
            self._drop_task_locked(task_id)
            self._persist_locked()
            return True

    def tick(self, step: float = 10.0) -> Sequence[Task]:
        """Compatibility no-op: workers update progress independently."""

        del step
        return self.list_tasks()

    def pause(self, task_id: str) -> Task:
        self._require_task_copy(task_id)
        raise InvalidTaskTransitionError(
            "A read-only analysis cannot be paused; it can be cancelled"
        )

    def resume(self, task_id: str) -> Task:
        self._require_task_copy(task_id)
        raise InvalidTaskTransitionError("A read-only analysis cannot be resumed")

    def cancel(self, task_id: str) -> Task:
        cancel_provider = False
        with self._lock:
            task = self._require_task(task_id)
            if task.status in TERMINAL_TASK_STATUSES:
                raise InvalidTaskTransitionError(
                    f"cannot cancel a task in {task.status.value} state"
                )
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()
            future = self._futures.get(task_id)
            if future is not None:
                cancelled_before_start = future.cancel()
            else:
                cancelled_before_start = True
            cancel_provider = bool(
                task.task_type in {TaskType.COMPRESSION, TaskType.VERIFICATION}
                and not cancelled_before_start
            )
            if task.task_type is TaskType.COMPRESSION and not cancelled_before_start:
                task.metadata["stage"] = "Cancelling"
                task.metadata["cancellation_requested"] = True
                task.speed_mbps = 0.0
                task.updated_at = datetime.now(UTC)
                result = deepcopy(task)
            else:
                task.status = TaskStatus.CANCELLED
                task.speed_mbps = 0.0
                task.remaining_data_gb = 0.0
                task.result = None
                task.error = None
                task.metadata["stage"] = "Cancelled"
                task.metadata["temporary_data_removed"] = True
                task.updated_at = datetime.now(UTC)
                result = deepcopy(task)
            self._persist_locked()
        if cancel_provider and self._compression_service is not None:
            self._compression_service.cancel_all()
        return result

    def wait_for(self, task_id: str, timeout: float = 10.0) -> Task:
        """Testing/CLI helper; the Qt controller never blocks on this method."""

        with self._lock:
            future = self._futures.get(task_id)
        if future is None:
            return self._require_task_copy(task_id)
        future.result(timeout=timeout)
        return self._require_task_copy(task_id)

    def shutdown(self, *, wait: bool = True, timeout: float = 2.0) -> bool:
        """Cancel active helpers and wait no longer than ``timeout`` seconds."""

        cancel_compression = False
        with self._lock:
            if self._closed:
                return all(future.done() for future in self._futures.values())
            self._closed = True
            for task_id, event in self._cancel_events.items():
                task = self._tasks.get(task_id)
                if task is not None and task.status not in TERMINAL_TASK_STATUSES:
                    event.set()
                    task.speed_mbps = 0.0
                    if task.task_type in {
                        TaskType.COMPRESSION,
                        TaskType.VERIFICATION,
                    }:
                        task.metadata["stage"] = "Cancelling"
                        task.metadata["cancellation_requested"] = True
                        cancel_compression = True
                    else:
                        task.status = TaskStatus.CANCELLED
                        task.remaining_data_gb = 0.0
                        task.result = None
                        task.error = None
                        task.metadata["stage"] = "Cancelled"
                        task.metadata["temporary_data_removed"] = True
                    task.updated_at = datetime.now(UTC)
            futures = tuple(self._futures.values())
            for future in futures:
                future.cancel()
            self._persist_locked()

        if cancel_compression and self._compression_service is not None:
            self._compression_service.cancel_all()
        unfinished = tuple(future for future in futures if not future.done())
        if wait and unfinished:
            _, not_done = wait_for_futures(
                unfinished,
                timeout=max(0.0, float(timeout)),
            )
            unfinished = tuple(not_done)
        self._executor.shutdown(wait=not unfinished, cancel_futures=True)
        if unfinished:
            logger.warning(
                "%d analysis worker(s) did not stop within %.2f seconds",
                len(unfinished),
                max(0.0, float(timeout)),
            )
        return not unfinished

    def _run_compression(
        self,
        task_id: str,
        game: Game,
        plan: CompressionPlan,
        confirmed: bool,
        automatic_authorized: bool,
        cancelled: Event,
    ) -> None:
        service = self._compression_service
        if service is None:
            self._fail(task_id, "The compression service is unavailable", time.monotonic())
            return
        started = time.monotonic()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status is TaskStatus.CANCELLED:
                return
            task.status = TaskStatus.RUNNING
            task.metadata["stage"] = "Preparing"
            task.updated_at = datetime.now(UTC)
            self._persist_locked()
        result: CompressionResult | None = None
        failure = ""
        try:
            result = service.execute(
                plan.id,
                game,
                confirmed=confirmed,
                automatic_authorized=automatic_authorized,
                cancel_event=cancelled,
                progress_callback=lambda values: self._update_compression_progress(
                    task_id, values
                ),
            )
        except Exception as error:
            failure = str(error) or type(error).__name__
            logger.exception("Compression parent task %s failed", task_id)
        finally:
            if result is not None:
                self._complete_compression(task_id, result, started)
            else:
                self._fail(
                    task_id,
                    failure or "Compression did not return a result",
                    started,
                )

    def _run_verification(
        self,
        task_id: str,
        game: Game,
        cancelled: Event,
    ) -> None:
        started = time.monotonic()
        service = self._compression_service
        if cancelled.is_set():
            self._mark_cancelled(task_id)
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.RUNNING
            task.metadata["stage"] = "Measuring compression"
            task.updated_at = datetime.now(UTC)
            self._persist_locked()
        if service is None:
            self._fail(task_id, "The compression service is unavailable", started)
            return
        try:
            measurement = service.verify_measurement(game)
        except Exception as error:
            if cancelled.is_set():
                self._mark_cancelled(task_id)
                return
            if isinstance(error, PrivilegedMeasurementError):
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task.metadata.update(
                            {
                                "helper_exit_code": error.exit_code,
                                "helper_stdout": error.stdout,
                                "helper_stderr": error.stderr,
                            }
                        )
                        logger.error(
                            "Verification task %s helper failed "
                            "exit_code=%r stdout=%r stderr=%r",
                            task_id,
                            error.exit_code,
                            error.stdout,
                            error.stderr,
                        )
            self._fail(task_id, str(error) or type(error).__name__, started)
            return
        if cancelled.is_set():
            self._mark_cancelled(task_id)
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.COMPLETED
            task.progress = 100.0
            task.result = measurement.to_dict()
            task.error = None
            task.metadata["stage"] = "Completed"
            task.metadata["elapsed_seconds"] = max(
                0.0, time.monotonic() - started
            )
            task.updated_at = datetime.now(UTC)
            self._persist_locked()

    def _update_compression_progress(
        self, task_id: str, values: Mapping[str, Any]
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                return
            task.status = TaskStatus.RUNNING
            processed = self._safe_int(values.get("processed_bytes"))
            total = self._safe_int(values.get("total_bytes"))
            processed_files = self._safe_int(values.get("processed_files"))
            total_files = self._safe_int(values.get("total_files"))
            elapsed = self._safe_float(values.get("elapsed_seconds"))
            task.metadata.update(
                {
                    "stage": str(values.get("stage", "Compressing")),
                    "processed_files": processed_files,
                    "total_files": total_files,
                    "processed_bytes": processed,
                    "total_bytes": total,
                    "current_file": self._short_file(
                        str(values.get("current_file", ""))
                    ),
                    "elapsed_seconds": elapsed,
                    "progress_determinate": total > 0,
                }
            )
            if total > 0:
                task.progress = min(99.9, max(task.progress, processed / total * 100.0))
                remaining = max(0, total - processed)
                task.remaining_data_gb = remaining / (1024**3)
                if elapsed > 0 and processed > 0:
                    bytes_per_second = processed / elapsed
                    task.speed_mbps = bytes_per_second / (1024 * 1024)
                    if processed_files >= 2:
                        task.metadata["estimated_remaining_seconds"] = (
                            remaining / bytes_per_second
                        )
            task.updated_at = datetime.now(UTC)

    def _complete_compression(
        self,
        task_id: str,
        result: CompressionResult,
        started: float,
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if result.status == "cancelled":
                task.status = TaskStatus.CANCELLED
                stage = "Cancelled"
            elif result.status in {"completed", "completed_with_warning"}:
                task.status = TaskStatus.COMPLETED
                stage = "Completed"
            else:
                task.status = TaskStatus.FAILED
                stage = (
                    "Verification required"
                    if result.status == "verification_required"
                    else "Failed"
                )
            task.progress = (
                100.0 if task.status is TaskStatus.COMPLETED else task.progress
            )
            task.speed_mbps = 0.0
            task.remaining_data_gb = 0.0
            task.result = result.to_dict()
            task.error = result.error
            task.metadata.update(
                {
                    "stage": stage,
                    "elapsed_seconds": max(0.0, time.monotonic() - started),
                    "processed_files": result.processed_files,
                    "processed_bytes": result.processed_bytes,
                    "after_bytes": (
                        result.after.physical_bytes
                        if result.after is not None
                        else None
                    ),
                    "saved_bytes": result.actual_saved_bytes,
                    "verification_state": result.verification_state,
                    "warnings": list(result.warnings),
                }
            )
            task.updated_at = datetime.now(UTC)
            self._persist_locked()

    def _run_analysis(self, task_id: str, game: Game, cancelled: Event) -> None:
        started = time.monotonic()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status is TaskStatus.CANCELLED:
                return
            task.status = TaskStatus.ANALYZING
            task.metadata["stage"] = "Checking analysis cache"
            task.updated_at = datetime.now(UTC)

        try:
            report = self._load_cached(game, cancelled)
            if report is not None:
                report = self._analyzer.refresh_cached_report(
                    game,
                    report,
                    cancel_event=cancelled,
                )
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task.metadata["cache_hit"] = True
                        task.metadata["stage"] = "Loaded cached report"
                self._complete(task_id, report, started)
                return

            report = self._analyzer.analyze(
                game,
                cancel_event=cancelled,
                progress_callback=lambda progress: self._update_progress(
                    task_id, progress
                ),
            )
            if cancelled.is_set():
                raise AnalysisCancelled("Compression analysis was cancelled")
            if not self._complete(task_id, report, started):
                return
            cache_warning = self._save_cached(game, report)
            if cache_warning:
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task.metadata["cache_warning"] = cache_warning
        except AnalysisCancelled:
            self._mark_cancelled(task_id)
        except Exception as error:
            self._fail(task_id, str(error) or type(error).__name__, started)

    def _update_progress(self, task_id: str, progress: AnalysisProgress) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                return
            task.status = TaskStatus.ANALYZING
            task.progress = min(99.9, max(task.progress, progress.progress * 100.0))
            task.metadata.update(progress.to_dict())
            total = float(task.metadata.get("total_size_gb", 0.0))
            task.remaining_data_gb = max(0.0, total * (1.0 - task.progress / 100.0))
            if progress.elapsed_seconds > 0 and progress.analyzed_bytes > 0:
                task.speed_mbps = (
                    progress.analyzed_bytes / progress.elapsed_seconds / (1024 * 1024)
                )
            task.updated_at = datetime.now(UTC)

    def _complete(
        self,
        task_id: str,
        report: BtrfsAnalysisReport,
        started: float,
    ) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status is TaskStatus.CANCELLED:
                return False
            task.status = TaskStatus.COMPLETED
            task.progress = 100.0
            task.speed_mbps = 0.0
            task.remaining_data_gb = 0.0
            task.result = report.to_dict()
            task.error = None
            task.metadata["stage"] = "Completed"
            task.metadata["outcome"] = (
                "completed_warning" if report.warnings else "completed_success"
            )
            task.metadata["elapsed_seconds"] = max(0.0, time.monotonic() - started)
            task.metadata["scanned_files"] = report.file_count
            task.metadata["analyzed_bytes"] = report.sampled_bytes
            task.updated_at = datetime.now(UTC)
            self._persist_locked()
            return True

    def _mark_cancelled(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.CANCELLED
            task.speed_mbps = 0.0
            task.remaining_data_gb = 0.0
            task.result = None
            task.error = None
            task.metadata["stage"] = "Cancelled"
            task.metadata["temporary_data_removed"] = True
            task.updated_at = datetime.now(UTC)
            self._persist_locked()

    def _fail(self, task_id: str, error: str, started: float) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status is TaskStatus.CANCELLED:
                return
            task.status = TaskStatus.FAILED
            task.speed_mbps = 0.0
            task.remaining_data_gb = 0.0
            task.result = None
            task.error = error
            task.metadata["stage"] = "Failed"
            task.metadata["elapsed_seconds"] = max(0.0, time.monotonic() - started)
            task.updated_at = datetime.now(UTC)
            self._persist_locked()

    def _load_cached(
        self,
        game: Game,
        cancelled: Event,
    ) -> BtrfsAnalysisReport | None:
        if self._cache is None:
            return None
        return self._cache.load(game, cancel_event=cancelled)

    def _save_cached(self, game: Game, report: BtrfsAnalysisReport) -> str:
        if self._cache is None or not report.scan_complete:
            return ""
        try:
            self._cache.save(game, report)
        except AnalysisCacheError as error:
            return str(error)
        return ""

    @staticmethod
    def _safe_int(value: Any) -> int:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
        return 0

    @staticmethod
    def _safe_float(value: Any) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        return 0.0

    @staticmethod
    def _short_file(value: str, limit: int = 96) -> str:
        if len(value) <= limit:
            return value
        return f"…{value[-(limit - 1):]}"

    def _drop_task_locked(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._games.pop(task_id, None)
        self._cancel_events.pop(task_id, None)
        self._futures.pop(task_id, None)

    def _persist_locked(self) -> None:
        store = self._history_store
        if store is None:
            return
        tasks = list(self._tasks.values())
        active = [task for task in tasks if task.status not in TERMINAL_TASK_STATUSES]
        finished = sorted(
            (task for task in tasks if task.status in TERMINAL_TASK_STATUSES),
            key=lambda task: task.updated_at,
            reverse=True,
        )
        kept = [*active, *finished[:MAX_FINISHED_TASKS]]
        kept_ids = {task.id for task in kept}
        for task_id in tuple(self._tasks):
            if task_id not in kept_ids:
                self._drop_task_locked(task_id)
        try:
            store.save(kept)
        except (OSError, TypeError, ValueError) as error:
            logger.warning("Could not persist task history: %s", error)

    def _require_task(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFoundError(f"unknown task: {task_id}") from error

    def _require_task_copy(self, task_id: str) -> Task:
        with self._lock:
            return deepcopy(self._require_task(task_id))

    def _ensure_open(self) -> None:
        if self._closed:
            raise TaskServiceError("analysis task service has been shut down")


__all__ = ["BtrfsAnalysisTaskService"]
