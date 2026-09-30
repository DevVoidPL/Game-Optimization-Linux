"""Read-only plan of the deep cleaning and compression analysis.

The plan only describes what a later, separate stage could do.  It never
contains commands; every file is identified by a path relative to the game
root (``$GAME_ROOT``) or to its Steam library (``$STEAM_LIBRARY``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

DEEP_PLAN_SCHEMA_VERSION = 1
SAFETY_LEVELS = ("safe", "review", "unsupported")
DEEP_CATEGORIES = ("languages", "audio", "cache", "residues", "textures", "btrfs")
# What a later stage may do with a group; "keep" and "info" are never acted on.
DEEP_ACTIONS = ("remove_candidate", "compress_candidate", "keep", "info")
MAX_SAMPLE_PATHS = 100
GAME_ROOT_PLACEHOLDER = "$GAME_ROOT"
STEAM_LIBRARY_PLACEHOLDER = "$STEAM_LIBRARY"


@dataclass(frozen=True, slots=True)
class DeepEvidence:
    source: str
    detail: str
    weight: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "detail": self.detail, "weight": round(self.weight, 3)}


@dataclass(frozen=True, slots=True)
class DeepGroup:
    """One classified set of files with the same decision and reason."""

    category: str
    key: str
    safety: str
    action: str
    confidence: float
    reversible: bool
    reason: str
    file_count: int
    bytes: int
    sample_paths: tuple[str, ...] = ()
    language: str = ""
    size_exact: bool = True
    potential_savings_bytes: int | None = None
    savings_estimated: bool = False
    evidence: tuple[DeepEvidence, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.category not in DEEP_CATEGORIES:
            raise ValueError(f"unsupported deep category {self.category!r}")
        if self.safety not in SAFETY_LEVELS:
            raise ValueError(f"unsupported safety level {self.safety!r}")
        if self.action not in DEEP_ACTIONS:
            raise ValueError(f"unsupported action {self.action!r}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.file_count < 0 or self.bytes < 0:
            raise ValueError("counts must not be negative")
        if len(self.sample_paths) > MAX_SAMPLE_PATHS:
            raise ValueError("too many sample paths")
        for path in self.sample_paths:
            if path.startswith("/") or ".." in path.split("/"):
                raise ValueError("sample paths must be relative")

    @property
    def remaining_count(self) -> int:
        return max(0, self.file_count - len(self.sample_paths))

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "key": self.key,
            "language": self.language,
            "safety": self.safety,
            "action": self.action,
            "confidence": round(float(self.confidence), 3),
            "reversible": self.reversible,
            "reason": self.reason,
            "fileCount": self.file_count,
            "bytes": self.bytes,
            "sizeExact": self.size_exact,
            "potentialSavingsBytes": self.potential_savings_bytes,
            "savingsEstimated": self.savings_estimated,
            "samplePaths": list(self.sample_paths),
            "remainingCount": self.remaining_count,
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class DeepBtrfsInfo:
    is_btrfs: bool
    filesystem: str
    mount_compression: str = ""
    persistent_compression: str = ""
    compsize_available: bool = False
    exact_physical_bytes: int | None = None
    shared_extents: bool | None = None
    shared_bytes: int | None = None
    hardlinked_files: int = 0
    source: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "isBtrfs": self.is_btrfs,
            "filesystem": self.filesystem,
            "mountCompression": self.mount_compression,
            "persistentCompression": self.persistent_compression,
            "compsizeAvailable": self.compsize_available,
            "exactPhysicalBytes": self.exact_physical_bytes,
            "sharedExtents": self.shared_extents,
            "sharedBytes": self.shared_bytes,
            "hardlinkedFiles": self.hardlinked_files,
            "source": self.source,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DeepOptimizationPlan:
    game_id: str
    game_name: str
    launcher: str
    status: str
    engine: str
    engine_confidence: float
    engine_evidence: tuple[DeepEvidence, ...]
    filesystem: str
    btrfs: DeepBtrfsInfo
    logical_bytes: int
    allocated_bytes: int
    file_count: int
    groups: tuple[DeepGroup, ...]
    languages: Mapping[str, int] = field(default_factory=dict)
    target_language: str = ""
    target_language_source: str = ""
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    complete: bool = True
    engine_version: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = DEEP_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"completed", "cancelled", "failed"}:
            raise ValueError("unsupported plan status")
        if self.status != "completed" and self.complete:
            # A cancelled or failed scan must never look complete.
            object.__setattr__(self, "complete", False)

    def to_dict(self) -> dict[str, Any]:
        """Private-safe representation: no absolute paths, no file contents."""

        return {
            "schemaVersion": self.schema_version,
            "kind": "game-optimization-deep-analysis",
            "readOnly": True,
            "gameId": self.game_id,
            "gameName": self.game_name,
            "launcher": self.launcher,
            "installRoot": GAME_ROOT_PLACEHOLDER,
            "status": self.status,
            "complete": self.complete,
            "engine": {
                "value": self.engine,
                "version": self.engine_version,
                "confidence": round(float(self.engine_confidence), 3),
                "evidence": [item.to_dict() for item in self.engine_evidence],
            },
            "filesystem": self.filesystem,
            "isBtrfs": self.btrfs.is_btrfs,
            "btrfs": self.btrfs.to_dict(),
            "logicalBytes": self.logical_bytes,
            "allocatedBytes": self.allocated_bytes,
            "fileCount": self.file_count,
            "languages": dict(sorted(self.languages.items())),
            "targetLanguage": self.target_language,
            "targetLanguageSource": self.target_language_source,
            "groups": [group.to_dict() for group in self.groups],
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
        }


__all__ = [
    "DEEP_ACTIONS",
    "DEEP_CATEGORIES",
    "DEEP_PLAN_SCHEMA_VERSION",
    "DeepBtrfsInfo",
    "DeepEvidence",
    "DeepGroup",
    "DeepOptimizationPlan",
    "GAME_ROOT_PLACEHOLDER",
    "MAX_SAMPLE_PATHS",
    "SAFETY_LEVELS",
    "STEAM_LIBRARY_PLACEHOLDER",
]
