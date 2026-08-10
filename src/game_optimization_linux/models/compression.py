"""Domain values used by the real, guarded Btrfs compression workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .enums import CompressionProfile


EXACT_COMPSIZE_MEASUREMENT_SOURCES = frozenset(
    {"polkit_helper", "polkit_compsize"}
)


def is_exact_compsize_measurement_source(value: object) -> bool:
    return str(value or "").strip().casefold() in EXACT_COMPSIZE_MEASUREMENT_SOURCES


class CompressionProviderError(RuntimeError):
    """Base error surfaced as a failed or blocked compression task."""


class CompressionCancelled(CompressionProviderError):
    """Raised after the active child process has been terminated and reaped."""


class CompressionPlanRejected(CompressionProviderError):
    """Raised when a stale or unsafe plan reaches the executor."""


def _non_negative(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class CompressionToolCapabilities:
    """Availability detected without changing a game or its filesystem."""

    btrfs_available: bool
    btrfs_version: str = ""
    compsize_available: bool = False
    compsize_version: str = ""
    property_supported: bool = False
    recompression_supported: bool = False
    level_supported: bool = False
    message: str = ""

    @property
    def compression_available(self) -> bool:
        return bool(
            self.btrfs_available
            and self.property_supported
            and self.recompression_supported
            and self.level_supported
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "btrfs_available": bool(self.btrfs_available),
            "btrfs_version": self.btrfs_version,
            "compsize_available": bool(self.compsize_available),
            "compsize_version": self.compsize_version,
            "property_supported": bool(self.property_supported),
            "recompression_supported": bool(self.recompression_supported),
            "level_supported": bool(self.level_supported),
            "compression_available": self.compression_available,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class CompressionFile:
    """One immutable plan entry, always relative to the verified game root."""

    relative_path: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
        ):
            raise ValueError("relative_path must stay below the game root")
        for value, name in (
            (self.size_bytes, "size_bytes"),
            (self.mtime_ns, "mtime_ns"),
            (self.ctime_ns, "ctime_ns"),
            (self.device, "device"),
            (self.inode, "inode"),
        ):
            _non_negative(value, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
            "device": self.device,
            "inode": self.inode,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CompressionFile":
        return cls(
            relative_path=str(raw.get("relative_path", "")),
            size_bytes=max(0, int(raw.get("size_bytes", 0))),
            mtime_ns=max(0, int(raw.get("mtime_ns", 0))),
            ctime_ns=max(0, int(raw.get("ctime_ns", 0))),
            device=max(0, int(raw.get("device", 0))),
            inode=max(0, int(raw.get("inode", 0))),
        )


@dataclass(frozen=True, slots=True)
class CompressionMeasurement:
    """Comparable before/after values gathered through read-only APIs."""

    logical_bytes: int
    physical_bytes: int
    exclusive_bytes: int | None
    shared_bytes: int | None
    compsize_disk_bytes: int | None
    compsize_uncompressed_bytes: int | None
    compsize_referenced_bytes: int | None
    scan_complete: bool
    shared_extent_state: str
    filesystem_available_bytes: int | None = None
    filesystem_free_bytes: int | None = None
    filesystem_used_bytes: int | None = None
    filesystem_total_bytes: int | None = None
    measurement_source: str = "unprivileged"
    measurement_error: str | None = None
    measured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        _non_negative(self.logical_bytes, "logical_bytes")
        _non_negative(self.physical_bytes, "physical_bytes")
        for value, name in (
            (self.exclusive_bytes, "exclusive_bytes"),
            (self.shared_bytes, "shared_bytes"),
            (self.compsize_disk_bytes, "compsize_disk_bytes"),
            (self.compsize_uncompressed_bytes, "compsize_uncompressed_bytes"),
            (self.compsize_referenced_bytes, "compsize_referenced_bytes"),
            (self.filesystem_available_bytes, "filesystem_available_bytes"),
            (self.filesystem_free_bytes, "filesystem_free_bytes"),
            (self.filesystem_used_bytes, "filesystem_used_bytes"),
            (self.filesystem_total_bytes, "filesystem_total_bytes"),
        ):
            if value is not None:
                _non_negative(value, name)
        if self.shared_extent_state not in {"detected", "not_detected", "unknown"}:
            raise ValueError("shared_extent_state has an unsupported value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_bytes": self.logical_bytes,
            "physical_bytes": self.physical_bytes,
            "exclusive_bytes": self.exclusive_bytes,
            "shared_bytes": self.shared_bytes,
            "compsize_disk_bytes": self.compsize_disk_bytes,
            "compsize_uncompressed_bytes": self.compsize_uncompressed_bytes,
            "compsize_referenced_bytes": self.compsize_referenced_bytes,
            "scan_complete": bool(self.scan_complete),
            "shared_extent_state": self.shared_extent_state,
            "filesystem_available_bytes": self.filesystem_available_bytes,
            "filesystem_free_bytes": self.filesystem_free_bytes,
            "filesystem_used_bytes": self.filesystem_used_bytes,
            "filesystem_total_bytes": self.filesystem_total_bytes,
            "measurement_source": self.measurement_source,
            "measurement_error": self.measurement_error,
            "measured_at": self.measured_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CompressionMeasurement":
        measured_raw = raw.get("measured_at")
        try:
            measured_at = datetime.fromisoformat(str(measured_raw))
        except (TypeError, ValueError):
            measured_at = datetime.now(UTC)
        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=UTC)

        def optional_int(name: str) -> int | None:
            value = raw.get(name)
            return (
                max(0, int(value))
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None
            )

        state = str(raw.get("shared_extent_state", "unknown"))
        if state not in {"detected", "not_detected", "unknown"}:
            state = "unknown"
        return cls(
            logical_bytes=max(0, int(raw.get("logical_bytes", 0))),
            physical_bytes=max(0, int(raw.get("physical_bytes", 0))),
            exclusive_bytes=optional_int("exclusive_bytes"),
            shared_bytes=optional_int("shared_bytes"),
            compsize_disk_bytes=optional_int("compsize_disk_bytes"),
            compsize_uncompressed_bytes=optional_int(
                "compsize_uncompressed_bytes"
            ),
            compsize_referenced_bytes=optional_int("compsize_referenced_bytes"),
            scan_complete=bool(raw.get("scan_complete", False)),
            shared_extent_state=state,
            filesystem_available_bytes=optional_int(
                "filesystem_available_bytes"
            ),
            filesystem_free_bytes=optional_int("filesystem_free_bytes"),
            filesystem_used_bytes=optional_int("filesystem_used_bytes"),
            filesystem_total_bytes=optional_int("filesystem_total_bytes"),
            measurement_source=str(
                raw.get("measurement_source") or "unprivileged"
            ),
            measurement_error=(
                str(raw["measurement_error"])
                if raw.get("measurement_error")
                else None
            ),
            measured_at=measured_at,
        )


@dataclass(frozen=True, slots=True)
class CompressionPlan:
    """A complete, reviewable plan. Creating it never mutates game data."""

    id: str
    game_id: str
    app_id: str
    game_name: str
    game_path: str
    profile: CompressionProfile
    persistent_compression_algorithm: str
    one_time_recompression_level: int
    files: tuple[CompressionFile, ...]
    skipped_files: tuple[str, ...]
    full_compression: bool
    after_update: bool
    build_id: str | None
    estimated_savings_low_bytes: int | None
    estimated_savings_high_bytes: int | None
    estimated_shared_growth_bytes: int | None
    available_bytes: int | None
    required_free_bytes: int
    before: CompressionMeasurement
    eligible: bool
    confirmation_required: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for value, name in (
            (self.id, "id"),
            (self.game_id, "game_id"),
            (self.game_name, "game_name"),
            (self.game_path, "game_path"),
            (self.persistent_compression_algorithm, "persistent_compression_algorithm"),
        ):
            if not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if self.persistent_compression_algorithm != "zstd":
            raise ValueError("only the zstd persistent algorithm is supported")
        if self.one_time_recompression_level not in {1, 3, 6, 9}:
            raise ValueError("unsupported one-time recompression level")
        _non_negative(self.required_free_bytes, "required_free_bytes")
        for value, name in (
            (self.estimated_savings_low_bytes, "estimated_savings_low_bytes"),
            (self.estimated_savings_high_bytes, "estimated_savings_high_bytes"),
            (self.estimated_shared_growth_bytes, "estimated_shared_growth_bytes"),
            (self.available_bytes, "available_bytes"),
        ):
            if value is not None:
                _non_negative(value, name)
        if self.eligible and self.blockers:
            raise ValueError("an eligible plan cannot contain blockers")

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    def to_dict(self, *, include_files: bool = True) -> dict[str, Any]:
        return {
            "id": self.id,
            "game_id": self.game_id,
            "app_id": self.app_id,
            "game_name": self.game_name,
            "game_path": self.game_path,
            "profile": self.profile.value,
            "persistent_compression_algorithm": self.persistent_compression_algorithm,
            "one_time_recompression_level": self.one_time_recompression_level,
            "files": [item.to_dict() for item in self.files] if include_files else [],
            "skipped_files": list(self.skipped_files),
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "full_compression": bool(self.full_compression),
            "after_update": bool(self.after_update),
            "build_id": self.build_id,
            "estimated_savings_low_bytes": self.estimated_savings_low_bytes,
            "estimated_savings_high_bytes": self.estimated_savings_high_bytes,
            "estimated_shared_growth_bytes": self.estimated_shared_growth_bytes,
            "available_bytes": self.available_bytes,
            "required_free_bytes": self.required_free_bytes,
            "before": self.before.to_dict(),
            "eligible": bool(self.eligible),
            "confirmation_required": bool(self.confirmation_required),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CompressionResult:
    """Verified outcome; a zero helper exit code alone never creates success."""

    plan_id: str
    game_id: str
    profile: CompressionProfile
    status: str
    started_at: datetime
    completed_at: datetime
    processed_files: int
    processed_bytes: int
    before: CompressionMeasurement
    after: CompressionMeasurement | None
    actual_saved_bytes: int | None
    verification_state: str
    full_compression: bool
    after_update: bool
    build_id: str | None
    command_exit_codes: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def measurement_authoritative(self) -> bool:
        return bool(
            self.after is not None
            and is_exact_compsize_measurement_source(
                self.before.measurement_source
            )
            and is_exact_compsize_measurement_source(
                self.after.measurement_source
            )
            and self.before.compsize_disk_bytes is not None
            and self.after.compsize_disk_bytes is not None
        )

    @property
    def active_files_compression_effect_bytes(self) -> int | None:
        after = self.after
        if (
            not self.measurement_authoritative
            or after is None
            or after.compsize_uncompressed_bytes is None
            or after.compsize_disk_bytes is None
        ):
            return None
        return max(
            0,
            after.compsize_uncompressed_bytes - after.compsize_disk_bytes,
        )

    @property
    def filesystem_used_delta_bytes(self) -> int | None:
        after = self.after
        if (
            not self.measurement_authoritative
            or after is None
            or self.before.filesystem_used_bytes is None
            or after.filesystem_used_bytes is None
        ):
            return None
        return self.before.filesystem_used_bytes - after.filesystem_used_bytes

    @property
    def filesystem_free_delta_bytes(self) -> int | None:
        after = self.after
        if (
            not self.measurement_authoritative
            or after is None
            or self.before.filesystem_free_bytes is None
            or after.filesystem_free_bytes is None
        ):
            return None
        return after.filesystem_free_bytes - self.before.filesystem_free_bytes

    def __post_init__(self) -> None:
        if self.status not in {
            "completed",
            "completed_with_warning",
            "cancelled",
            "failed",
            "verification_required",
        }:
            raise ValueError("unsupported compression result status")
        _non_negative(self.processed_files, "processed_files")
        _non_negative(self.processed_bytes, "processed_bytes")
        if self.actual_saved_bytes is not None and not isinstance(
            self.actual_saved_bytes, int
        ):
            raise ValueError("actual_saved_bytes must be an integer or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "game_id": self.game_id,
            "profile": self.profile.value,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "processed_files": self.processed_files,
            "processed_bytes": self.processed_bytes,
            "before": self.before.to_dict(),
            "after": self.after.to_dict() if self.after is not None else None,
            "actual_saved_bytes": self.actual_saved_bytes,
            "measurement_authoritative": self.measurement_authoritative,
            "active_files_compression_effect_bytes": (
                self.active_files_compression_effect_bytes
            ),
            "filesystem_used_delta_bytes": self.filesystem_used_delta_bytes,
            "filesystem_free_delta_bytes": self.filesystem_free_delta_bytes,
            "verification_state": self.verification_state,
            "full_compression": bool(self.full_compression),
            "after_update": bool(self.after_update),
            "build_id": self.build_id,
            "command_exit_codes": list(self.command_exit_codes),
            "warnings": list(self.warnings),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CompressionHistoryEntry:
    """Compact persistent history stored in XDG state, never in a game."""

    id: str
    game_id: str
    game_name: str
    game_path: str
    result: CompressionResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "game_path": self.game_path,
            **self.result.to_dict(),
        }


def files_from_dicts(values: Sequence[Mapping[str, Any]]) -> tuple[CompressionFile, ...]:
    return tuple(CompressionFile.from_dict(value) for value in values)


__all__ = [
    "CompressionCancelled",
    "CompressionFile",
    "CompressionHistoryEntry",
    "CompressionMeasurement",
    "CompressionPlan",
    "CompressionPlanRejected",
    "CompressionProviderError",
    "CompressionResult",
    "CompressionToolCapabilities",
    "files_from_dicts",
]
