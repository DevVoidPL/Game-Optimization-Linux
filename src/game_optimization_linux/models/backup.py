"""Backup records used by the in-memory demo backup service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from .enums import BackupStatus


@dataclass(frozen=True, slots=True)
class Backup:
    id: str
    game_id: str
    game_name: str
    created_at: datetime
    operation_type: str
    size_gb: float
    status: BackupStatus = BackupStatus.AVAILABLE

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("backup id cannot be empty")
        if (
            isinstance(self.size_gb, bool)
            or not isinstance(self.size_gb, (int, float))
            or not isfinite(self.size_gb)
            or self.size_gb < 0
        ):
            raise ValueError("backup size must be a finite non-negative number")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "created_at": self.created_at.isoformat(),
            "created_label": self.created_at.strftime("%Y-%m-%d %H:%M"),
            "operation_type": self.operation_type,
            "size_gb": self.size_gb,
            "size_label": f"{self.size_gb:.1f} GB",
            "status": self.status.value,
        }
