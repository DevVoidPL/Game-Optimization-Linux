"""Models used by the non-blocking, simulated task queue."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from .enums import TaskStatus, TaskType


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    }
)


@dataclass(slots=True)
class Task:
    """A unit of demo work.

    The service advances progress explicitly, so this model never starts a
    thread or performs filesystem work on its own.
    """

    id: str
    game_id: str
    game_name: str
    task_type: TaskType
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    speed_mbps: float = 0.0
    remaining_data_gb: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("task id cannot be empty")
        if not self.game_id.strip():
            raise ValueError("game id cannot be empty")
        if (
            isinstance(self.progress, bool)
            or not isinstance(self.progress, (int, float))
            or not isfinite(self.progress)
            or not 0.0 <= self.progress <= 100.0
        ):
            raise ValueError("progress must be a finite number between 0 and 100")
        for value, field_name in (
            (self.speed_mbps, "speed_mbps"),
            (self.remaining_data_gb, "remaining_data_gb"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"{field_name} must be a finite non-negative number"
                )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    @property
    def speed_label(self) -> str:
        return "-" if self.speed_mbps == 0 else f"{self.speed_mbps:.0f} MB/s"

    @property
    def remaining_label(self) -> str:
        return "-" if self.remaining_data_gb == 0 else f"{self.remaining_data_gb:.1f} GB"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "task_type": self.task_type.value,
            "title": self.title,
            "status": self.status.value,
            "progress": self.progress,
            "speed_mbps": self.speed_mbps,
            "speed_label": self.speed_label,
            "remaining_data_gb": self.remaining_data_gb,
            "remaining_label": self.remaining_label,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "result": self.result,
            "error": self.error,
            "metadata": dict(self.metadata),
        }
