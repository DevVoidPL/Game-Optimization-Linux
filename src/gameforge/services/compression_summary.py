"""Pure presentation calculations for existing compression measurements.

This module never scans files and never executes external commands.  It only
combines already persisted, authoritative compsize measurements.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any


STRONG_COMPRESSION_EFFECT_FRACTION = 0.15
MODERATE_COMPRESSION_EFFECT_FRACTION = 0.05
LOW_COMPRESSION_EFFECT_FRACTION = 0.01

# A large shared set with virtually no exclusive data is unsafe for unattended
# defragmentation/recompression.  These values are deliberately named constants
# so the policy can be reviewed and tested without touching presentation code.
LARGE_SHARED_EXTENT_BYTES = 1024**3
ALMOST_NO_EXCLUSIVE_FRACTION = 0.01


def _non_negative_int(value: Any) -> int | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ):
        return int(value)
    return None


def _positive_int(value: Any) -> int | None:
    parsed = _non_negative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def compression_effect(
    uncompressed_bytes: Any,
    disk_usage_bytes: Any,
) -> dict[str, Any]:
    """Calculate the current effect exclusively from matching compsize values."""

    uncompressed = _positive_int(uncompressed_bytes)
    disk_usage = _positive_int(disk_usage_bytes)
    if uncompressed is None or disk_usage is None:
        return {
            "available": False,
            "savingBytes": None,
            "savingFraction": None,
            "savingPercent": None,
        }
    saving = max(0, uncompressed - disk_usage)
    fraction = saving / uncompressed
    return {
        "available": True,
        "savingBytes": saving,
        "savingFraction": fraction,
        "savingPercent": fraction * 100.0,
    }


def has_high_shared_extent_risk(
    *,
    state: Any,
    total_bytes: Any,
    exclusive_bytes: Any,
    set_shared_bytes: Any,
    estimated_growth_bytes: Any = None,
) -> bool:
    """Identify the high-risk shared/snapshot shape reported by btrfs du."""

    if str(state or "").casefold() != "detected":
        return False
    total = _positive_int(total_bytes)
    exclusive = _non_negative_int(exclusive_bytes)
    set_shared = _non_negative_int(set_shared_bytes)
    growth = _non_negative_int(estimated_growth_bytes)
    if total is None or exclusive is None:
        return False
    shared_exposure = max(set_shared or 0, growth or 0)
    return bool(
        shared_exposure >= LARGE_SHARED_EXTENT_BYTES
        and exclusive <= total * ALMOST_NO_EXCLUSIVE_FRACTION
    )


def classify_compression_effect(
    uncompressed_bytes: Any,
    disk_usage_bytes: Any,
    *,
    shared_extent_state: Any = "unknown",
    shared_total_bytes: Any = None,
    exclusive_bytes: Any = None,
    set_shared_bytes: Any = None,
    estimated_shared_growth_bytes: Any = None,
) -> dict[str, Any]:
    """Classify a game's measured current effect and unattended safety state."""

    effect = compression_effect(uncompressed_bytes, disk_usage_bytes)
    shared_risk = has_high_shared_extent_risk(
        state=shared_extent_state,
        total_bytes=shared_total_bytes,
        exclusive_bytes=exclusive_bytes,
        set_shared_bytes=set_shared_bytes,
        estimated_growth_bytes=estimated_shared_growth_bytes,
    )
    if shared_risk:
        key = "shared_extents_blocked"
    elif effect["available"] is not True:
        key = "measurement_unavailable"
    else:
        fraction = float(effect["savingFraction"])
        if fraction >= STRONG_COMPRESSION_EFFECT_FRACTION:
            key = "strongly_compressed"
        elif fraction >= MODERATE_COMPRESSION_EFFECT_FRACTION:
            key = "moderately_compressed"
        elif fraction >= LOW_COMPRESSION_EFFECT_FRACTION:
            key = "low_effect"
        else:
            key = "no_compression"
    return {
        **effect,
        "key": key,
        "sharedExtentRisk": shared_risk,
        "automaticOperationBlocked": shared_risk or key == "no_compression",
        "estimatedSharedGrowthBytes": (
            _non_negative_int(estimated_shared_growth_bytes)
            if shared_risk
            else None
        ),
    }


def reclaimed_by_last_operation(
    compression_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a per-operation compsize delta only for a valid before/after pair."""

    result = compression_result if isinstance(compression_result, Mapping) else {}
    before = result.get("before")
    after = result.get("after")
    before_map = before if isinstance(before, Mapping) else {}
    after_map = after if isinstance(after, Mapping) else {}
    before_disk = _positive_int(before_map.get("compsize_disk_bytes"))
    after_disk = _positive_int(after_map.get("compsize_disk_bytes"))

    def complete_compsize(measurement: Mapping[str, Any]) -> bool:
        return all(
            _positive_int(measurement.get(name)) is not None
            for name in (
                "compsize_disk_bytes",
                "compsize_uncompressed_bytes",
                "compsize_referenced_bytes",
            )
        )

    complete = bool(
        result.get("measurement_authoritative") is True
        and str(before_map.get("measurement_source") or "").casefold()
        == "polkit_helper"
        and str(after_map.get("measurement_source") or "").casefold()
        == "polkit_helper"
        and before_disk is not None
        and after_disk is not None
        and complete_compsize(before_map)
        and complete_compsize(after_map)
    )
    return {
        "available": complete,
        "bytes": before_disk - after_disk if complete else None,
        "measuredAt": str(result.get("completed_at") or "") if complete else "",
        "source": "compsize_before_after" if complete else "unavailable",
    }


def _measurement_timestamp(game: Mapping[str, Any]) -> str:
    measurement = game.get("currentCompressionMeasurement")
    current = measurement if isinstance(measurement, Mapping) else {}
    return str(
        game.get("currentCompressionMeasuredAt")
        or current.get("measured_at")
        or current.get("measuredAt")
        or ""
    )


def _timestamp_sort_key(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return float("-inf")


def aggregate_library_compression(
    games: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate current compsize measurements by Steam library path."""

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for game in games:
        library_path = str(game.get("libraryPath") or "").strip()
        if not library_path:
            continue
        grouped.setdefault(library_path, []).append(game)

    summaries: list[dict[str, Any]] = []
    for library_path, library_games in sorted(grouped.items()):
        uncompressed_total = 0
        disk_total = 0
        measured_count = 0
        timestamps: list[str] = []
        for game in library_games:
            uncompressed = _positive_int(game.get("compsizeUncompressedBytes"))
            disk = _positive_int(game.get("physicalSizeBytes"))
            if uncompressed is None or disk is None:
                continue
            uncompressed_total += uncompressed
            disk_total += disk
            measured_count += 1
            timestamp = _measurement_timestamp(game)
            if timestamp:
                timestamps.append(timestamp)

        saving = max(0, uncompressed_total - disk_total)
        effect_fraction = (
            saving / uncompressed_total if uncompressed_total > 0 else None
        )
        fully_measured = measured_count == len(library_games) and measured_count > 0
        summaries.append(
            {
                "libraryPath": library_path,
                "gameCount": len(library_games),
                "measuredGameCount": measured_count,
                "uncompressedBytes": uncompressed_total if measured_count else None,
                "diskUsageBytes": disk_total if measured_count else None,
                "currentSavingBytes": saving if measured_count else None,
                "savingFraction": effect_fraction,
                "savingPercent": (
                    effect_fraction * 100.0
                    if effect_fraction is not None
                    else None
                ),
                "fullyMeasured": fully_measured,
                "lastFullMeasurementAt": (
                    max(timestamps, key=_timestamp_sort_key)
                    if fully_measured and len(timestamps) == measured_count
                    else ""
                ),
                "source": "compsize",
            }
        )
    return summaries


__all__ = [
    "ALMOST_NO_EXCLUSIVE_FRACTION",
    "LARGE_SHARED_EXTENT_BYTES",
    "LOW_COMPRESSION_EFFECT_FRACTION",
    "MODERATE_COMPRESSION_EFFECT_FRACTION",
    "STRONG_COMPRESSION_EFFECT_FRACTION",
    "aggregate_library_compression",
    "classify_compression_effect",
    "compression_effect",
    "has_high_shared_extent_risk",
    "reclaimed_by_last_operation",
]
