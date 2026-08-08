"""Empty normal-mode services for features that are not implemented yet."""

from __future__ import annotations

from collections.abc import Sequence

from game_optimization_linux.models import Backup, CompressionProfile, Game, Task


class FeatureUnavailableError(RuntimeError):
    pass


class UnavailableTaskService:
    """Expose no simulated work in normal mode."""

    def list_tasks(self) -> Sequence[Task]:
        return ()

    def enqueue_analysis(self, game: Game) -> Task:
        raise FeatureUnavailableError("Game analysis is not implemented yet")

    def enqueue_compression(
        self, game: Game, profile: CompressionProfile
    ) -> Task:
        raise FeatureUnavailableError("Btrfs compression is not implemented yet")

    def tick(self, step: float = 10.0) -> Sequence[Task]:
        return ()

    def pause(self, task_id: str) -> Task:
        raise FeatureUnavailableError("This real task cannot be paused")

    def resume(self, task_id: str) -> Task:
        raise FeatureUnavailableError("This real task cannot be resumed")

    def cancel(self, task_id: str) -> Task:
        raise FeatureUnavailableError("This real task cannot be cancelled")


class UnavailableBackupService:
    """Expose no fictional backup records in normal mode."""

    def list_backups(self, game_id: str | None = None) -> Sequence[Backup]:
        return ()

    def restore_backup(self, backup_id: str) -> Backup:
        raise FeatureUnavailableError("Backup restore is not implemented yet")

    def delete_backup(self, backup_id: str) -> None:
        raise FeatureUnavailableError("Backup deletion is not implemented yet")


__all__ = [
    "FeatureUnavailableError",
    "UnavailableBackupService",
    "UnavailableTaskService",
]
