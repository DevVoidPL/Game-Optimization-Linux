"""Side-effect-free backup records for demonstration mode."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Iterable, Sequence
from uuid import uuid4

from gameforge.models.backup import Backup
from gameforge.models.enums import BackupStatus
from gameforge.models.game import Game

from .contracts import BackupService


class BackupServiceError(RuntimeError):
    """Base error for demo backup record operations."""


class BackupNotFoundError(BackupServiceError):
    pass


def demo_backups() -> tuple[Backup, ...]:
    return (
        Backup(
            id="backup-batman-001",
            game_id="batman-arkham-knight",
            game_name="Batman: Arkham Knight",
            created_at=datetime(2026, 7, 16, 19, 30, tzinfo=UTC),
            operation_type="Before compression",
            size_gb=4.2,
        ),
        Backup(
            id="backup-dying-light-001",
            game_id="dying-light",
            game_name="Dying Light",
            created_at=datetime(2026, 7, 12, 21, 5, tzinfo=UTC),
            operation_type="Automatic safety copy",
            size_gb=2.8,
        ),
        Backup(
            id="backup-cyberpunk-001",
            game_id="cyberpunk-2077",
            game_name="Cyberpunk 2077",
            created_at=datetime(2026, 7, 8, 18, 15, tzinfo=UTC),
            operation_type="Before optimization",
            size_gb=5.6,
        ),
        Backup(
            id="backup-minecraft-001",
            game_id="minecraft",
            game_name="Minecraft",
            created_at=datetime(2026, 7, 2, 16, 45, tzinfo=UTC),
            operation_type="Manual",
            size_gb=0.9,
        ),
    )


class DemoBackupService(BackupService):
    """Manipulates immutable backup metadata only, never actual files."""

    def __init__(self, backups: Iterable[Backup] | None = None) -> None:
        initial_backups = tuple(backups) if backups is not None else demo_backups()
        self._backups: dict[str, Backup] = {}
        for backup in initial_backups:
            if backup.id in self._backups:
                raise ValueError(f"duplicate backup id: {backup.id}")
            self._backups[backup.id] = backup

    def list_backups(self, game_id: str | None = None) -> Sequence[Backup]:
        backups = tuple(self._backups.values())
        if game_id is None:
            return backups
        return tuple(backup for backup in backups if backup.game_id == game_id)

    def get_backup(self, backup_id: str) -> Backup | None:
        return self._backups.get(backup_id)

    def create_backup(self, game: Game, operation_type: str = "Manual") -> Backup:
        if not operation_type.strip():
            raise ValueError("operation_type cannot be empty")
        backup = Backup(
            id=f"backup-{uuid4().hex}",
            game_id=game.id,
            game_name=game.name,
            created_at=datetime.now(UTC),
            operation_type=operation_type,
            size_gb=round(max(0.1, game.physical_size_gb * 0.08), 1),
            status=BackupStatus.AVAILABLE,
        )
        self._backups[backup.id] = backup
        return backup

    def restore_backup(self, backup_id: str) -> Backup:
        backup = self._require_backup(backup_id)
        restored = replace(backup, status=BackupStatus.RESTORED)
        self._backups[backup_id] = restored
        return restored

    def delete_backup(self, backup_id: str) -> None:
        self._require_backup(backup_id)
        del self._backups[backup_id]

    def _require_backup(self, backup_id: str) -> Backup:
        try:
            return self._backups[backup_id]
        except KeyError as error:
            raise BackupNotFoundError(f"unknown backup: {backup_id}") from error


MockBackupService = DemoBackupService
