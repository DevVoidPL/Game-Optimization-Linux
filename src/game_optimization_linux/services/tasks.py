"""A deterministic task queue suitable for driving from ``QTimer``."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite
from threading import RLock
from typing import Sequence
from uuid import uuid4

from game_optimization_linux.models.enums import CompressionProfile, TaskStatus, TaskType
from game_optimization_linux.models.game import Game
from game_optimization_linux.models.task import TERMINAL_TASK_STATUSES, Task
from game_optimization_linux.providers.base import CompressionProvider
from game_optimization_linux.providers.demo import DemoCompressionProvider

from .contracts import TaskService


class TaskServiceError(RuntimeError):
    """Base error raised by the in-memory task service."""


class TaskNotFoundError(TaskServiceError):
    pass


class InvalidTaskTransitionError(TaskServiceError):
    pass


class DemoTaskService(TaskService):
    """An in-memory FIFO queue that performs no real work.

    Calling :meth:`tick` advances at most one task. A Qt controller can invoke
    it from a short timer callback, keeping the GUI responsive without worker
    threads in demonstration mode.
    """

    def __init__(self, compression_provider: CompressionProvider | None = None) -> None:
        self._compression_provider = compression_provider or DemoCompressionProvider()
        self._tasks: dict[str, Task] = {}
        self._games_by_task: dict[str, Game] = {}
        self._profiles_by_task: dict[str, CompressionProfile] = {}
        self._resume_status: dict[str, TaskStatus] = {}
        self._lock = RLock()

    def enqueue(self, task: Task) -> Task:
        with self._lock:
            if task.id in self._tasks:
                raise TaskServiceError(f"task already exists: {task.id}")
            if task.status is not TaskStatus.QUEUED:
                raise InvalidTaskTransitionError("a new task must be queued")
            stored = deepcopy(task)
            self._tasks[stored.id] = stored
            return self._copy(stored)

    def enqueue_task(self, task: Task) -> Task:
        """Compatibility alias for callers that prefer an explicit name."""

        return self.enqueue(task)

    def enqueue_analysis(self, game: Game) -> Task:
        task = Task(
            id=f"analysis-{uuid4().hex}",
            game_id=game.id,
            game_name=game.name,
            task_type=TaskType.ANALYSIS,
            title=f"Analyze {game.name}",
            remaining_data_gb=game.logical_size_gb,
            metadata={"total_size_gb": game.logical_size_gb},
        )
        with self._lock:
            queued = self.enqueue(task)
            self._games_by_task[task.id] = game
        return queued

    def enqueue_compression(
        self, game: Game, profile: CompressionProfile = CompressionProfile.AUTO
    ) -> Task:
        estimate = self._compression_provider.estimate(game, profile)
        if not estimate.compatible:
            raise TaskServiceError(estimate.reason or "compression is unavailable")
        task = Task(
            id=f"compression-{uuid4().hex}",
            game_id=game.id,
            game_name=game.name,
            task_type=TaskType.COMPRESSION,
            title=f"Compress {game.name} ({profile.value})",
            remaining_data_gb=game.physical_size_gb,
            metadata={
                "total_size_gb": game.physical_size_gb,
                "compression_profile": profile.value,
            },
        )
        with self._lock:
            queued = self.enqueue(task)
            self._games_by_task[task.id] = game
            self._profiles_by_task[task.id] = profile
        return queued

    def list_tasks(self) -> Sequence[Task]:
        with self._lock:
            return tuple(self._copy(task) for task in self._tasks.values())

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return self._copy(task) if task is not None else None

    def tick(self, step: float = 10.0) -> Sequence[Task]:
        if (
            isinstance(step, bool)
            or not isinstance(step, (int, float))
            or not isfinite(step)
            or step <= 0
        ):
            raise ValueError("step must be a finite positive number")
        with self._lock:
            task = self._active_task()
            if task is None:
                task = next(
                    (
                        candidate
                        for candidate in self._tasks.values()
                        if candidate.status is TaskStatus.QUEUED
                    ),
                    None,
                )
            if task is None:
                return tuple(self._copy(item) for item in self._tasks.values())

            if task.status is TaskStatus.QUEUED:
                task.status = self._running_status(task)

            task.progress = min(100.0, round(task.progress + step, 2))
            total_size = self._total_size(task)
            task.remaining_data_gb = round(
                max(0.0, total_size * (1.0 - task.progress / 100.0)), 2
            )
            task.speed_mbps = self._demo_speed(task)
            task.updated_at = datetime.now(UTC)

            if task.progress >= 100.0:
                self._complete(task)

            return tuple(self._copy(item) for item in self._tasks.values())

    def pause(self, task_id: str) -> Task:
        with self._lock:
            task = self._require_task(task_id)
            if task.status not in {
                TaskStatus.QUEUED,
                TaskStatus.ANALYZING,
                TaskStatus.RUNNING,
            }:
                raise InvalidTaskTransitionError(
                    f"cannot pause a task in {task.status.value} state"
                )
            self._resume_status[task.id] = task.status
            task.status = TaskStatus.PAUSED
            task.speed_mbps = 0.0
            task.updated_at = datetime.now(UTC)
            return self._copy(task)

    def resume(self, task_id: str) -> Task:
        with self._lock:
            task = self._require_task(task_id)
            if task.status is not TaskStatus.PAUSED:
                raise InvalidTaskTransitionError(
                    f"cannot resume a task in {task.status.value} state"
                )
            prior_status = self._resume_status.pop(task.id, TaskStatus.QUEUED)
            if prior_status is TaskStatus.QUEUED or self._active_task() is not None:
                task.status = TaskStatus.QUEUED
            else:
                task.status = self._running_status(task)
            task.updated_at = datetime.now(UTC)
            return self._copy(task)

    def cancel(self, task_id: str) -> Task:
        with self._lock:
            task = self._require_task(task_id)
            if task.status in TERMINAL_TASK_STATUSES:
                raise InvalidTaskTransitionError(
                    f"cannot cancel a task in {task.status.value} state"
                )
            task.status = TaskStatus.CANCELLED
            task.speed_mbps = 0.0
            task.updated_at = datetime.now(UTC)
            self._resume_status.pop(task.id, None)
            return self._copy(task)

    def fail(self, task_id: str, error: str) -> Task:
        """Expose the failed state for future adapters and UI demonstrations."""

        if not error.strip():
            raise ValueError("error cannot be empty")
        with self._lock:
            task = self._require_task(task_id)
            if task.status in TERMINAL_TASK_STATUSES:
                raise InvalidTaskTransitionError(
                    f"cannot fail a task in {task.status.value} state"
                )
            task.status = TaskStatus.FAILED
            task.speed_mbps = 0.0
            task.error = error
            task.updated_at = datetime.now(UTC)
            self._resume_status.pop(task.id, None)
            return self._copy(task)

    @staticmethod
    def _copy(task: Task) -> Task:
        return deepcopy(task)

    def _require_task(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFoundError(f"unknown task: {task_id}") from error

    def _active_task(self) -> Task | None:
        return next(
            (
                task
                for task in self._tasks.values()
                if task.status in {TaskStatus.ANALYZING, TaskStatus.RUNNING}
            ),
            None,
        )

    @staticmethod
    def _running_status(task: Task) -> TaskStatus:
        if task.task_type is TaskType.ANALYSIS:
            return TaskStatus.ANALYZING
        return TaskStatus.RUNNING

    @staticmethod
    def _total_size(task: Task) -> float:
        raw_total = task.metadata.get("total_size_gb", task.remaining_data_gb)
        if isinstance(raw_total, (int, float)) and not isinstance(raw_total, bool):
            return max(0.0, float(raw_total))
        return task.remaining_data_gb

    @staticmethod
    def _demo_speed(task: Task) -> float:
        return 780.0 if task.task_type is TaskType.ANALYSIS else 420.0

    def _complete(self, task: Task) -> None:
        game = self._games_by_task.get(task.id)
        try:
            if task.task_type is TaskType.ANALYSIS and game is not None:
                result = self._compression_provider.analyze(game).to_dict()
            elif task.task_type is TaskType.COMPRESSION and game is not None:
                profile = self._profiles_by_task.get(task.id, CompressionProfile.AUTO)
                estimate = self._compression_provider.estimate(game, profile)
                result = {
                    "game_id": game.id,
                    "profile": profile.value,
                    "estimated_size_gb": estimate.estimated_size_gb,
                    "estimated_savings_gb": estimate.estimated_savings_gb,
                    "message": "Simulated compression completed; no files were changed.",
                }
            else:
                result = {
                    "message": "Simulated task completed; no system changes were made."
                }
        except Exception as error:
            # A provider failure is a task failure, not a timer failure. Keeping
            # it inside the queue lets the controller expose the documented
            # ``failed`` state and continue advancing later queued tasks.
            task.status = TaskStatus.FAILED
            task.speed_mbps = 0.0
            task.result = None
            task.error = str(error) or type(error).__name__
            task.updated_at = datetime.now(UTC)
            return

        task.status = TaskStatus.COMPLETED
        task.progress = 100.0
        task.speed_mbps = 0.0
        task.remaining_data_gb = 0.0
        task.result = result
        task.error = None
        task.updated_at = datetime.now(UTC)


MockTaskService = DemoTaskService
