"""Small atomic journal for GUI-level tasks."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from gameforge.models.enums import TaskStatus, TaskType
from gameforge.models.task import TERMINAL_TASK_STATUSES, Task


TASK_HISTORY_FORMAT_VERSION = 1
MAX_FINISHED_TASKS = 100
_RESTORED_ACTIVE = frozenset(
    {
        TaskStatus.QUEUED,
        TaskStatus.ANALYZING,
        TaskStatus.RUNNING,
        TaskStatus.PAUSED,
    }
)


class TaskHistoryStore:
    """Persist parent tasks only and recover stale active states safely."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[Task, ...]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return ()
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            return ()
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            return ()
        restored: list[Task] = []
        changed = False
        now = datetime.now(UTC)
        for raw in raw_tasks:
            task = self._task_from_dict(raw)
            if task is None:
                continue
            if task.status in _RESTORED_ACTIVE:
                task.status = TaskStatus.INTERRUPTED
                task.speed_mbps = 0.0
                task.remaining_data_gb = 0.0
                task.error = "GameForge was closed before this task finished"
                task.metadata["stage"] = "Interrupted"
                task.metadata["cancellable"] = False
                task.updated_at = now
                changed = True
            restored.append(task)
        kept = self._bounded(restored)
        if changed or len(kept) != len(restored):
            self.save(kept)
        return tuple(kept)

    def save(self, tasks: Sequence[Task]) -> None:
        kept = self._bounded(tasks)
        payload = {
            "version": TASK_HISTORY_FORMAT_VERSION,
            "tasks": [task.to_dict() for task in kept],
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _bounded(tasks: Sequence[Task]) -> list[Task]:
        active = [task for task in tasks if task.status not in TERMINAL_TASK_STATUSES]
        finished = sorted(
            (task for task in tasks if task.status in TERMINAL_TASK_STATUSES),
            key=lambda task: task.updated_at,
            reverse=True,
        )[:MAX_FINISHED_TASKS]
        return [*active, *finished]

    @staticmethod
    def _task_from_dict(raw: Any) -> Task | None:
        if not isinstance(raw, Mapping):
            return None
        try:
            created = datetime.fromisoformat(str(raw.get("created_at", "")))
            updated = datetime.fromisoformat(str(raw.get("updated_at", "")))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            metadata = raw.get("metadata")
            return Task(
                id=str(raw["id"]),
                game_id=str(raw["game_id"]),
                game_name=str(raw.get("game_name", "")),
                task_type=TaskType(str(raw["task_type"])),
                title=str(raw.get("title", "")),
                status=TaskStatus(str(raw.get("status", "queued"))),
                progress=float(raw.get("progress", 0.0)),
                speed_mbps=float(raw.get("speed_mbps", 0.0)),
                remaining_data_gb=float(raw.get("remaining_data_gb", 0.0)),
                created_at=created,
                updated_at=updated,
                result=(
                    dict(raw["result"])
                    if isinstance(raw.get("result"), Mapping)
                    else None
                ),
                error=str(raw["error"]) if raw.get("error") else None,
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        except (KeyError, TypeError, ValueError):
            return None


__all__ = [
    "MAX_FINISHED_TASKS",
    "TASK_HISTORY_FORMAT_VERSION",
    "TaskHistoryStore",
]
