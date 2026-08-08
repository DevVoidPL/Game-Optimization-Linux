"""Abstract application service contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from gameforge.models.backup import Backup
from gameforge.models.enums import CompressionProfile
from gameforge.models.game import Game
from gameforge.models.task import Task


class TaskService(ABC):
    """Queue contract designed to be advanced by an event-loop timer."""

    @abstractmethod
    def enqueue(self, task: Task) -> Task:
        raise NotImplementedError

    @abstractmethod
    def enqueue_analysis(self, game: Game) -> Task:
        raise NotImplementedError

    @abstractmethod
    def enqueue_compression(
        self, game: Game, profile: CompressionProfile = CompressionProfile.AUTO
    ) -> Task:
        raise NotImplementedError

    @abstractmethod
    def list_tasks(self) -> Sequence[Task]:
        raise NotImplementedError

    @abstractmethod
    def get_task(self, task_id: str) -> Task | None:
        raise NotImplementedError

    @abstractmethod
    def tick(self, step: float = 10.0) -> Sequence[Task]:
        raise NotImplementedError

    @abstractmethod
    def pause(self, task_id: str) -> Task:
        raise NotImplementedError

    @abstractmethod
    def resume(self, task_id: str) -> Task:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, task_id: str) -> Task:
        raise NotImplementedError


class BackupService(ABC):
    """Backup record contract, independent of any storage implementation."""

    @abstractmethod
    def list_backups(self, game_id: str | None = None) -> Sequence[Backup]:
        raise NotImplementedError

    @abstractmethod
    def get_backup(self, backup_id: str) -> Backup | None:
        raise NotImplementedError

    @abstractmethod
    def create_backup(self, game: Game, operation_type: str = "Manual") -> Backup:
        raise NotImplementedError

    @abstractmethod
    def restore_backup(self, backup_id: str) -> Backup:
        raise NotImplementedError

    @abstractmethod
    def delete_backup(self, backup_id: str) -> None:
        raise NotImplementedError
