"""Deep-cleanup stage 2B: per-file Btrfs recompression plan and job journal.

Recompression rewrites extents; it is never presented as reversible and has
its own journal, separate from the quarantine transactions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Mapping

from .deep_cleanup import validate_relative_path

RECOMPRESSION_SCHEMA_VERSION = 1
CANDIDATE_STATUSES = ("candidate", "already_compressed", "skipped")
JOB_STATES = ("planned", "running", "cancel_requested", "cancelled", "completed", "failed", "partial")
SETTLED_JOB_STATES = frozenset({"cancelled", "completed", "failed", "partial"})
FILE_STATES = ("pending", "running", "compressed", "no_gain", "skipped", "failed", "interrupted")
# Reason codes shown (translated) in the UI and kept in the plan/journal.
SKIP_REASONS = (
    "format_skipped", "core_archive", "required_texture", "selected_for_removal",
    "hardlink", "sparse", "too_small", "low_compressibility", "shared_extents",
    "shared_unknown", "not_sampled", "unreadable", "mount_point",
)


@dataclass(frozen=True, slots=True)
class RecompressionCapabilities:
    """What the local btrfs-progs documents; nothing here is guessed."""

    btrfs_version: str = ""
    algorithms: tuple[str, ...] = ()
    level_flag: str = ""
    zstd_level_range: tuple[int, int] | None = None
    flush_flag: bool = False
    du_raw: bool = False
    property_get: bool = False
    property_set: bool = False
    property_reset_documented: bool = False
    compsize_available: bool = False
    exact_measurement_available: bool = False
    help_digest: str = ""

    @property
    def recompression_available(self) -> bool:
        # Without a per-file shared-extent check, recompression is blocked.
        return "zstd" in self.algorithms and self.du_raw

    @property
    def levels(self) -> tuple[int, ...]:
        if not self.level_flag or self.zstd_level_range is None:
            return ()
        low, high = self.zstd_level_range
        return tuple(level for level in (1, 3, 6) if low <= level <= high)

    @property
    def future_property_available(self) -> bool:
        """Only when reading and restoring the previous value is documented."""

        return self.property_get and self.property_set and self.property_reset_documented

    def to_dict(self) -> dict[str, Any]:
        return {
            "btrfsVersion": self.btrfs_version, "algorithms": list(self.algorithms),
            "levelFlag": self.level_flag,
            "zstdLevelRange": list(self.zstd_level_range) if self.zstd_level_range else [],
            "levels": list(self.levels), "flushFlag": self.flush_flag, "duRaw": self.du_raw,
            "propertyGet": self.property_get, "propertySet": self.property_set,
            "propertyResetDocumented": self.property_reset_documented,
            "futurePropertyAvailable": self.future_property_available,
            "compsizeAvailable": self.compsize_available,
            "exactMeasurementAvailable": self.exact_measurement_available,
            "recompressionAvailable": self.recompression_available,
            "helpDigest": self.help_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RecompressionCapabilities:
        levels = list(raw.get("zstdLevelRange") or [])
        return cls(
            btrfs_version=str(raw.get("btrfsVersion", "")),
            algorithms=tuple(str(item) for item in raw.get("algorithms", ())),
            level_flag=str(raw.get("levelFlag", "")),
            zstd_level_range=(int(levels[0]), int(levels[1])) if len(levels) == 2 else None,
            flush_flag=bool(raw.get("flushFlag", False)), du_raw=bool(raw.get("duRaw", False)),
            property_get=bool(raw.get("propertyGet", False)), property_set=bool(raw.get("propertySet", False)),
            property_reset_documented=bool(raw.get("propertyResetDocumented", False)),
            compsize_available=bool(raw.get("compsizeAvailable", False)),
            exact_measurement_available=bool(raw.get("exactMeasurementAvailable", False)),
            help_digest=str(raw.get("helpDigest", "")),
        )


@dataclass(frozen=True, slots=True)
class RecompressionCandidate:
    relative_path: str
    size: int
    allocated: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    nlink: int
    extension: str
    status: str
    reason: str = ""
    shared_bytes: int | None = None
    exclusive_bytes: int | None = None
    sample_bytes: int = 0
    estimated_gain: float | None = None  # zlib approximation, not a Zstd result
    estimated_savings_low: int = 0
    estimated_savings_high: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", validate_relative_path(self.relative_path))
        if self.status not in CANDIDATE_STATUSES:
            raise ValueError(f"unsupported candidate status {self.status!r}")
        if self.status == "skipped" and self.reason not in SKIP_REASONS:
            raise ValueError(f"unsupported skip reason {self.reason!r}")
        if self.status == "candidate" and (self.nlink != 1 or self.shared_bytes != 0):
            raise ValueError("candidates must be single-link files without shared extents")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relativePath": self.relative_path, "size": self.size, "allocated": self.allocated,
            "device": self.device, "inode": self.inode, "mtimeNs": self.mtime_ns,
            "ctimeNs": self.ctime_ns, "nlink": self.nlink, "extension": self.extension,
            "status": self.status, "reason": self.reason, "sharedBytes": self.shared_bytes,
            "exclusiveBytes": self.exclusive_bytes, "sampleBytes": self.sample_bytes,
            "estimatedGain": self.estimated_gain,
            "estimatedSavingsLow": self.estimated_savings_low,
            "estimatedSavingsHigh": self.estimated_savings_high,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RecompressionCandidate:
        return cls(
            relative_path=str(raw["relativePath"]), size=int(raw["size"]),
            allocated=int(raw.get("allocated", 0)), device=int(raw["device"]), inode=int(raw["inode"]),
            mtime_ns=int(raw["mtimeNs"]), ctime_ns=int(raw.get("ctimeNs", 0)), nlink=int(raw.get("nlink", 1)),
            extension=str(raw.get("extension", "")), status=str(raw["status"]), reason=str(raw.get("reason", "")),
            shared_bytes=raw.get("sharedBytes"), exclusive_bytes=raw.get("exclusiveBytes"),
            sample_bytes=int(raw.get("sampleBytes", 0)), estimated_gain=raw.get("estimatedGain"),
            estimated_savings_low=int(raw.get("estimatedSavingsLow", 0)),
            estimated_savings_high=int(raw.get("estimatedSavingsHigh", 0)),
        )


@dataclass(frozen=True, slots=True)
class BtrfsCompressionPlan:
    plan_id: str
    game_id: str
    app_id: str
    game_root: str
    root_device: int
    root_inode: int
    deep_plan_fingerprint: str
    capabilities: RecompressionCapabilities
    measurement_source: str
    profile_level: int | None
    algorithm: str
    candidates: tuple[RecompressionCandidate, ...]
    complete: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = RECOMPRESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RECOMPRESSION_SCHEMA_VERSION:
            raise ValueError("unsupported recompression plan schema")
        if self.algorithm not in self.capabilities.algorithms:
            raise ValueError("the algorithm is not supported by the local btrfs-progs")
        if self.profile_level is not None and self.profile_level not in self.capabilities.levels:
            raise ValueError("the compression level is not supported by the local btrfs-progs")

    def counts(self) -> dict[str, int]:
        result = {"candidate": 0, "already_compressed": 0, "format_skipped": 0,
                  "shared": 0, "not_worth": 0, "other_skipped": 0}
        for item in self.candidates:
            if item.status in {"candidate", "already_compressed"}:
                result[item.status] += 1
            elif item.reason in {"format_skipped", "core_archive", "required_texture"}:
                result["format_skipped"] += 1
            elif item.reason in {"shared_extents", "shared_unknown", "hardlink"}:
                result["shared"] += 1
            elif item.reason in {"low_compressibility", "too_small", "sparse"}:
                result["not_worth"] += 1
            else:
                result["other_skipped"] += 1
        return result

    def type_groups(self) -> list[dict[str, Any]]:
        """Candidates grouped by file type for the manual selection."""

        groups: dict[str, dict[str, Any]] = {}
        for item in self.candidates:
            if item.status != "candidate":
                continue
            entry = groups.setdefault(item.extension or "(none)", {
                "type": item.extension or "(none)", "files": 0, "bytes": 0,
                "savingsLow": 0, "savingsHigh": 0, "minGain": 1.0, "samples": [],
            })
            entry["files"] += 1
            entry["bytes"] += item.size
            entry["savingsLow"] += item.estimated_savings_low
            entry["savingsHigh"] += item.estimated_savings_high
            entry["minGain"] = min(entry["minGain"], float(item.estimated_gain or 0.0))
            if len(entry["samples"]) < 10:
                entry["samples"].append(item.relative_path)
        for entry in groups.values():
            # "Recommended" only for clearly compressible, verified-unshared types.
            entry["recommended"] = entry["minGain"] >= 0.2
        return sorted(groups.values(), key=lambda item: -item["bytes"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version, "planId": self.plan_id, "gameId": self.game_id,
            "appId": self.app_id, "gameRoot": self.game_root, "rootDevice": self.root_device,
            "rootInode": self.root_inode, "deepPlanFingerprint": self.deep_plan_fingerprint,
            "capabilities": self.capabilities.to_dict(), "measurementSource": self.measurement_source,
            "profileLevel": self.profile_level, "algorithm": self.algorithm, "complete": self.complete,
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
            "candidates": [item.to_dict() for item in self.candidates],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BtrfsCompressionPlan:
        return cls(
            schema_version=int(raw.get("schemaVersion", 0)), plan_id=str(raw["planId"]),
            game_id=str(raw["gameId"]), app_id=str(raw.get("appId", "")), game_root=str(raw["gameRoot"]),
            root_device=int(raw["rootDevice"]), root_inode=int(raw["rootInode"]),
            deep_plan_fingerprint=str(raw["deepPlanFingerprint"]),
            capabilities=RecompressionCapabilities.from_dict(raw.get("capabilities") or {}),
            measurement_source=str(raw.get("measurementSource", "none")),
            profile_level=raw.get("profileLevel"), algorithm=str(raw.get("algorithm", "zstd")),
            complete=bool(raw.get("complete", False)),
            created_at=datetime.fromisoformat(str(raw["createdAt"])),
            candidates=tuple(RecompressionCandidate.from_dict(item) for item in raw.get("candidates", ())),
        )


@dataclass(frozen=True, slots=True)
class CompressionJobEntry:
    relative_path: str
    extension: str
    size: int
    device: int
    inode: int
    mtime_ns: int
    status: str = "pending"
    reason: str = ""
    encoded_before: float | None = None
    encoded_after: float | None = None
    seconds: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", validate_relative_path(self.relative_path))
        if self.status not in FILE_STATES:
            raise ValueError(f"unsupported file state {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relativePath": self.relative_path, "extension": self.extension, "size": self.size,
            "device": self.device, "inode": self.inode, "mtimeNs": self.mtime_ns,
            "status": self.status, "reason": self.reason, "encodedBefore": self.encoded_before,
            "encodedAfter": self.encoded_after, "seconds": self.seconds,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CompressionJobEntry:
        return cls(
            relative_path=str(raw["relativePath"]), extension=str(raw.get("extension", "")),
            size=int(raw["size"]), device=int(raw["device"]), inode=int(raw["inode"]),
            mtime_ns=int(raw["mtimeNs"]), status=str(raw.get("status", "pending")),
            reason=str(raw.get("reason", "")), encoded_before=raw.get("encodedBefore"),
            encoded_after=raw.get("encodedAfter"), seconds=float(raw.get("seconds", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class CompressionJob:
    job_id: str
    game_id: str
    plan_id: str
    deep_plan_fingerprint: str
    game_root: str
    algorithm: str
    profile_level: int | None
    capabilities: RecompressionCapabilities
    state: str
    entries: tuple[CompressionJobEntry, ...]
    baseline: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)
    measured_saved_bytes: int | None = None
    errors: tuple[str, ...] = ()
    note: str = ""
    recovered: bool = False
    root_device: int = 0
    root_inode: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = RECOMPRESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RECOMPRESSION_SCHEMA_VERSION:
            raise ValueError("unsupported compression job schema")
        if self.state not in JOB_STATES:
            raise ValueError(f"unsupported job state {self.state!r}")
        if not self.job_id.isalnum():
            raise ValueError("invalid job id")

    def with_state(self, state: str, **changes: Any) -> CompressionJob:
        return replace(self, state=state, updated_at=datetime.now(UTC), **changes)

    def with_entry(self, index: int, **changes: Any) -> CompressionJob:
        entries = list(self.entries)
        entries[index] = replace(entries[index], **changes)
        return replace(self, entries=tuple(entries), updated_at=datetime.now(UTC))

    def counts(self) -> dict[str, int]:
        result = {status: 0 for status in FILE_STATES}
        for entry in self.entries:
            result[entry.status] += 1
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version, "jobId": self.job_id, "gameId": self.game_id,
            "planId": self.plan_id, "deepPlanFingerprint": self.deep_plan_fingerprint,
            "gameRoot": self.game_root, "rootDevice": self.root_device, "rootInode": self.root_inode,
            "algorithm": self.algorithm, "profileLevel": self.profile_level,
            "capabilities": self.capabilities.to_dict(), "state": self.state,
            "baseline": dict(self.baseline), "after": dict(self.after),
            "measuredSavedBytes": self.measured_saved_bytes, "errors": list(self.errors),
            "note": self.note, "recovered": self.recovered,
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
            "updatedAt": self.updated_at.astimezone(UTC).isoformat(),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CompressionJob:
        return cls(
            schema_version=int(raw.get("schemaVersion", 0)), job_id=str(raw["jobId"]),
            game_id=str(raw["gameId"]), plan_id=str(raw.get("planId", "")),
            deep_plan_fingerprint=str(raw.get("deepPlanFingerprint", "")),
            game_root=str(raw["gameRoot"]), root_device=int(raw.get("rootDevice", 0)),
            root_inode=int(raw.get("rootInode", 0)), algorithm=str(raw.get("algorithm", "zstd")),
            profile_level=raw.get("profileLevel"),
            capabilities=RecompressionCapabilities.from_dict(raw.get("capabilities") or {}),
            state=str(raw["state"]), baseline=dict(raw.get("baseline") or {}), after=dict(raw.get("after") or {}),
            measured_saved_bytes=raw.get("measuredSavedBytes"), errors=tuple(raw.get("errors", ())),
            note=str(raw.get("note", "")), recovered=bool(raw.get("recovered", False)),
            created_at=datetime.fromisoformat(str(raw["createdAt"])),
            updated_at=datetime.fromisoformat(str(raw["updatedAt"])),
            entries=tuple(CompressionJobEntry.from_dict(item) for item in raw.get("entries", ())),
        )

    def history_entry(self) -> dict[str, Any]:
        counts = self.counts()
        duration = (self.updated_at - self.created_at).total_seconds()
        return {
            "jobId": self.job_id, "state": self.state, "note": self.note, "recovered": self.recovered,
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
            "algorithm": self.algorithm, "level": self.profile_level,
            "files": len(self.entries), "bytes": sum(entry.size for entry in self.entries),
            "compressed": counts["compressed"], "noGain": counts["no_gain"],
            "skipped": counts["skipped"], "failed": counts["failed"] + counts["interrupted"],
            "measuredSavedBytes": self.measured_saved_bytes,
            "before": dict(self.baseline), "after": dict(self.after),
            "seconds": max(0.0, round(duration, 1)), "gameRoot": "$GAME_ROOT",
        }


__all__ = [
    "BtrfsCompressionPlan",
    "CANDIDATE_STATUSES",
    "CompressionJob",
    "CompressionJobEntry",
    "FILE_STATES",
    "JOB_STATES",
    "RECOMPRESSION_SCHEMA_VERSION",
    "RecompressionCandidate",
    "RecompressionCapabilities",
    "SETTLED_JOB_STATES",
    "SKIP_REASONS",
]
