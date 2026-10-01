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
# "game": files of the analyzed game/AppID.  "global": confirmed leftovers of
# other, uninstalled games; never counted or acted on through a game's plan.
DEEP_SCOPES = ("game", "global")
LANGUAGE_ROLES = ("", "target", "fallback", "other", "shared")
MEASUREMENT_STATES = ("exact", "estimated", "unavailable")
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
    scope: str = "game"
    role: str = ""
    owner: str = ""

    def __post_init__(self) -> None:
        if self.scope not in DEEP_SCOPES:
            raise ValueError(f"unsupported scope {self.scope!r}")
        if self.role not in LANGUAGE_ROLES:
            raise ValueError(f"unsupported language role {self.role!r}")
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

    @property
    def is_savings(self) -> bool:
        return self.action in {"remove_candidate", "compress_candidate"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "key": self.key,
            "scope": self.scope,
            "role": self.role,
            "owner": self.owner,
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
    shared_extents: bool | None = None
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
            "sharedExtents": self.shared_extents,
            "hardlinkedFiles": self.hardlinked_files,
            "source": self.source,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DeepSizes:
    """Single source of truth for every size shown about the game.

    Each value has a state: ``exact`` (measured), ``estimated`` (derived, e.g.
    allocated blocks) or ``unavailable`` (no trustworthy measurement).
    """

    logical_size: int
    allocated_size: int
    physical_size: int | None = None
    physical_exclusive_size: int | None = None
    shared_size: int | None = None
    measured_savings: int | None = None
    measurement_source: str = "none"
    states: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        defaults = {
            "logical": "exact",
            "allocated": "estimated",
            "physical": "exact" if self.physical_size is not None else "unavailable",
            "exclusive": "exact" if self.physical_exclusive_size is not None else "unavailable",
            "shared": "exact" if self.shared_size is not None else "unavailable",
            "savings": "exact" if self.measured_savings is not None else "unavailable",
        }
        merged = {**defaults, **dict(self.states)}
        if any(value not in MEASUREMENT_STATES for value in merged.values()):
            raise ValueError("unsupported measurement state")
        object.__setattr__(self, "states", merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logicalSize": self.logical_size,
            "allocatedSize": self.allocated_size,
            "physicalSize": self.physical_size,
            "physicalExclusiveSize": self.physical_exclusive_size,
            "sharedSize": self.shared_size,
            "measuredSavings": self.measured_savings,
            "measurementSource": self.measurement_source,
            "states": dict(self.states),
        }


def savings_paths_disjoint(groups: tuple[DeepGroup, ...] | list[DeepGroup]) -> bool:
    """No listed path may appear in two savings groups (removal/compression)."""

    seen: set[str] = set()
    for group in groups:
        if not group.is_savings:
            continue
        for path in group.sample_paths:
            if path in seen:
                return False
            seen.add(path)
    return True


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
    sizes: DeepSizes
    file_count: int
    groups: tuple[DeepGroup, ...]
    languages: Mapping[str, int] = field(default_factory=dict)
    target_language: str = ""
    target_language_source: str = ""
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    complete: bool = True
    engine_version: str = ""
    notes: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = DEEP_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"completed", "cancelled", "failed"}:
            raise ValueError("unsupported plan status")
        if self.status != "completed" and self.complete:
            # A cancelled or failed scan must never look complete.
            object.__setattr__(self, "complete", False)

    @property
    def logical_bytes(self) -> int:
        return self.sizes.logical_size

    @property
    def allocated_bytes(self) -> int:
        return self.sizes.allocated_size

    def actionable_groups(self) -> tuple[DeepGroup, ...]:
        """Groups a future executor may act on through this game's plan.

        Global leftovers of other games are excluded by construction.
        """

        return tuple(
            group for group in self.groups
            if group.scope == "game" and group.is_savings and group.safety != "unsupported"
        )

    def savings_summary(self) -> dict[str, Any]:
        game = [group for group in self.groups if group.scope == "game"]
        removable = [group for group in game if group.action == "remove_candidate"]
        compression = [group for group in game if group.action == "compress_candidate"]
        languages: dict[str, dict[str, Any]] = {}
        for group in game:
            if group.language:
                entry = languages.setdefault(group.language, {"code": group.language, "bytes": 0, "role": group.role})
                entry["bytes"] += group.bytes
        return {
            "safeNow": sum(group.bytes for group in removable if group.safety == "safe"),
            "needsConfirmation": sum(group.bytes for group in removable if group.safety == "review"),
            "global": sum(group.bytes for group in self.groups if group.scope == "global" and group.is_savings),
            "btrfsEligible": sum(group.bytes for group in compression),
            "btrfsMeasuredSavings": self.sizes.measured_savings,
            "protected": sum(
                group.bytes for group in game
                if group.category != "btrfs" and group.action in {"keep", "info"}
            ),
            "languages": sorted(languages.values(), key=lambda item: -item["bytes"]),
            "otherLanguages": sum(item["bytes"] for item in languages.values() if item["role"] == "other"),
            "disjoint": savings_paths_disjoint(self.groups),
        }

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
            "sizes": self.sizes.to_dict(),
            "fileCount": self.file_count,
            "languages": dict(sorted(self.languages.items())),
            "targetLanguage": self.target_language,
            "targetLanguageSource": self.target_language_source,
            "groups": [group.to_dict() for group in self.groups],
            "savings": self.savings_summary(),
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
        }


__all__ = [
    "DEEP_ACTIONS",
    "DEEP_CATEGORIES",
    "DEEP_PLAN_SCHEMA_VERSION",
    "DEEP_SCOPES",
    "LANGUAGE_ROLES",
    "DeepBtrfsInfo",
    "DeepSizes",
    "savings_paths_disjoint",
    "DeepEvidence",
    "DeepGroup",
    "DeepOptimizationPlan",
    "GAME_ROOT_PLACEHOLDER",
    "MAX_SAMPLE_PATHS",
    "SAFETY_LEVELS",
    "STEAM_LIBRARY_PLACEHOLDER",
]
