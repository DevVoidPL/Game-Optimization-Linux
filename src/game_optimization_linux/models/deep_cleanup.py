"""Persistent, versioned journal of one deep-cleanup quarantine transaction.

Paths are always relative (to the game root or to the transaction's
quarantine directory).  The two absolute roots are private, stay in the
application data directory, and are never part of the sanitized history.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

CLEANUP_SCHEMA_VERSION = 1
TRANSACTION_STATES = (
    "preparing", "quarantining", "quarantined", "restoring", "restored",
    "finalizing", "finalized", "rolling_back", "failed", "partial",
)
# States in which no operation runs and the journal is final or waiting.
SETTLED_STATES = frozenset({"quarantined", "restored", "finalized", "failed", "partial"})
ENTRY_STATES = (
    "pending", "quarantined", "restored", "conflict", "deleted", "missing", "error",
)
FINALIZE_CONFIRMATION_WORDS = frozenset({"USUŃ", "DELETE", "ELIMINAR"})


def validate_relative_path(value: str) -> str:
    """A safe POSIX path inside a root: no absolute, empty, ``.``/``..`` or NUL parts."""

    text = str(value or "")
    if not text or "\0" in text or "\\" in text or text.startswith("/"):
        raise ValueError("unsafe relative path")
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("unsafe relative path")
    if PurePosixPath(*parts).as_posix() != text:
        raise ValueError("relative path is not normalized")
    return text


@dataclass(frozen=True, slots=True)
class CleanupEntry:
    relative_path: str
    size: int
    mtime_ns: int
    device: int
    inode: int
    category: str
    key: str
    language: str = ""
    safety: str = "review"
    reason: str = ""
    status: str = "pending"
    error: str = ""
    conflict: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", validate_relative_path(self.relative_path))
        if self.status not in ENTRY_STATES:
            raise ValueError(f"unsupported entry status {self.status!r}")
        if self.safety not in {"safe", "review"}:
            raise ValueError("only safe or review items can be part of a transaction")
        if self.size < 0:
            raise ValueError("size must not be negative")

    @property
    def quarantine_path(self) -> str:
        """Relative path of the entry inside the transaction directory."""

        return f"files/{self.relative_path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "relativePath": self.relative_path, "size": self.size, "mtimeNs": self.mtime_ns,
            "device": self.device, "inode": self.inode, "category": self.category,
            "key": self.key, "language": self.language, "safety": self.safety,
            "reason": self.reason, "status": self.status, "error": self.error,
            "conflict": self.conflict,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CleanupEntry:
        return cls(
            relative_path=str(raw["relativePath"]), size=int(raw["size"]),
            mtime_ns=int(raw["mtimeNs"]), device=int(raw["device"]), inode=int(raw["inode"]),
            category=str(raw["category"]), key=str(raw["key"]),
            language=str(raw.get("language", "")), safety=str(raw.get("safety", "review")),
            reason=str(raw.get("reason", "")), status=str(raw.get("status", "pending")),
            error=str(raw.get("error", "")), conflict=str(raw.get("conflict", "")),
        )


@dataclass(frozen=True, slots=True)
class CleanupTransaction:
    transaction_id: str
    game_id: str
    app_id: str
    game_name: str
    platform: str
    game_root: str
    quarantine_root: str
    plan_fingerprint: str
    target_language: str
    state: str
    entries: tuple[CleanupEntry, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    game_root_device: int = 0
    game_root_inode: int = 0
    recovered: bool = False
    note: str = ""
    schema_version: int = CLEANUP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CLEANUP_SCHEMA_VERSION:
            raise ValueError("unsupported cleanup transaction schema")
        if self.state not in TRANSACTION_STATES:
            raise ValueError(f"unsupported transaction state {self.state!r}")
        if not self.transaction_id.isalnum():
            raise ValueError("invalid transaction id")
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("a file may appear only once in a transaction")

    def with_state(self, state: str, **changes: Any) -> CleanupTransaction:
        return replace(self, state=state, updated_at=datetime.now(UTC), **changes)

    def with_entry(self, index: int, **changes: Any) -> CleanupTransaction:
        entries = list(self.entries)
        entries[index] = replace(entries[index], **changes)
        return replace(self, entries=tuple(entries), updated_at=datetime.now(UTC))

    def counts(self) -> dict[str, int]:
        result = {status: 0 for status in ENTRY_STATES}
        for entry in self.entries:
            result[entry.status] += 1
        return result

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def to_dict(self) -> dict[str, Any]:
        """Private journal (application data directory only)."""

        return {
            "schemaVersion": self.schema_version,
            "transactionId": self.transaction_id,
            "gameId": self.game_id, "appId": self.app_id, "gameName": self.game_name,
            "platform": self.platform, "gameRoot": self.game_root,
            "gameRootDevice": self.game_root_device, "gameRootInode": self.game_root_inode,
            "quarantineRoot": self.quarantine_root, "planFingerprint": self.plan_fingerprint,
            "targetLanguage": self.target_language, "state": self.state,
            "recovered": self.recovered, "note": self.note,
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
            "updatedAt": self.updated_at.astimezone(UTC).isoformat(),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CleanupTransaction:
        return cls(
            schema_version=int(raw.get("schemaVersion", 0)),
            transaction_id=str(raw["transactionId"]), game_id=str(raw["gameId"]),
            app_id=str(raw.get("appId", "")), game_name=str(raw.get("gameName", "")),
            platform=str(raw.get("platform", "")), game_root=str(raw["gameRoot"]),
            game_root_device=int(raw.get("gameRootDevice", 0)),
            game_root_inode=int(raw.get("gameRootInode", 0)),
            quarantine_root=str(raw["quarantineRoot"]),
            plan_fingerprint=str(raw.get("planFingerprint", "")),
            target_language=str(raw.get("targetLanguage", "")), state=str(raw["state"]),
            recovered=bool(raw.get("recovered", False)), note=str(raw.get("note", "")),
            created_at=datetime.fromisoformat(str(raw["createdAt"])),
            updated_at=datetime.fromisoformat(str(raw["updatedAt"])),
            entries=tuple(CleanupEntry.from_dict(item) for item in raw.get("entries", ())),
        )

    def history_entry(self) -> dict[str, Any]:
        """Sanitized summary for the UI: no absolute paths, no file contents."""

        counts = self.counts()
        categories = sorted({entry.category for entry in self.entries})
        languages = sorted({entry.language for entry in self.entries if entry.language})
        return {
            "transactionId": self.transaction_id, "state": self.state,
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
            "updatedAt": self.updated_at.astimezone(UTC).isoformat(),
            "categories": categories, "languages": languages,
            "fileCount": len(self.entries), "bytes": self.total_bytes,
            "quarantinedFiles": counts["quarantined"], "restoredFiles": counts["restored"],
            "deletedFiles": counts["deleted"], "conflicts": counts["conflict"],
            "errors": counts["error"] + counts["missing"],
            "recovered": self.recovered, "note": self.note,
            "gameRoot": "$GAME_ROOT",
            "conflictPaths": [entry.relative_path for entry in self.entries if entry.status == "conflict"][:20],
        }


__all__ = [
    "CLEANUP_SCHEMA_VERSION",
    "CleanupEntry",
    "CleanupTransaction",
    "ENTRY_STATES",
    "FINALIZE_CONFIRMATION_WORDS",
    "SETTLED_STATES",
    "TRANSACTION_STATES",
    "validate_relative_path",
]
