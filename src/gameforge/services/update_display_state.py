"""Persistent, presentation-only dismissals for the Updates page."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Mapping
from uuid import uuid4


UPDATE_DISPLAY_STATE_FORMAT_VERSION = 2
MAX_DISMISSED_UPDATE_ROWS = 500


class UpdateDisplayStateStore:
    """Keep hidden row versions without deleting update/compression history."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> dict[str, str]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
                return {}
        if not isinstance(payload, Mapping) or payload.get("version") not in {1, 2}:
            return {}
        raw = (
            payload.get("tombstones")
            if payload.get("version") == 2
            else payload.get("dismissed")
        )
        if not isinstance(raw, Mapping):
            return {}
        return {
            str(row_id): str(version)
            for row_id, version in raw.items()
            if str(row_id) and str(version)
        }

    def save(self, dismissed: Mapping[str, str]) -> None:
        entries = list(dismissed.items())[-MAX_DISMISSED_UPDATE_ROWS:]
        payload = {
            "version": UPDATE_DISPLAY_STATE_FORMAT_VERSION,
            "tombstones": {
                str(row_id): str(version)
                for row_id, version in entries
                if str(row_id) and str(version)
            },
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        with self._lock:
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


__all__ = [
    "MAX_DISMISSED_UPDATE_ROWS",
    "UPDATE_DISPLAY_STATE_FORMAT_VERSION",
    "UpdateDisplayStateStore",
]
