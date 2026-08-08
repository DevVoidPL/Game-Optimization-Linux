"""Read canonical, build-specific compression estimates without benchmarking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from gameforge.models.game import Game


LOW_ADDITIONAL_BENEFIT_BYTES = 1024**3
LOW_ADDITIONAL_BENEFIT_FRACTION = 0.05


def _non_negative_int(value: Any) -> int | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ):
        return int(value)
    return None


def current_compression_saving(
    uncompressed_bytes: Any,
    disk_usage_bytes: Any,
) -> int | None:
    """Return the measured active-file saving using compsize values only."""

    uncompressed = _non_negative_int(uncompressed_bytes)
    disk_usage = _non_negative_int(disk_usage_bytes)
    if uncompressed is None or disk_usage is None:
        return None
    return max(0, uncompressed - disk_usage)


def normalized_benchmark_projection(
    benchmark: Mapping[str, Any] | None,
    *,
    level: int,
    current_uncompressed_bytes: Any,
    current_disk_usage_bytes: Any,
    app_id: str,
    build_id: str,
) -> dict[str, Any]:
    """Normalize one build-specific benchmark ratio to the current compsize base."""

    unavailable = {
        "available": False,
        "reason": "No current estimate",
        "level": int(level),
        "source": "unavailable",
        "currentSavingBytes": current_compression_saving(
            current_uncompressed_bytes,
            current_disk_usage_bytes,
        ),
        "estimatedTotalPotentialBytes": None,
        "estimatedAdditionalSavingBytes": None,
        "estimatedPhysicalBytes": None,
        "predictedPhysicalRatio": None,
        "lowBenefit": False,
        "additionalConfirmationRequired": False,
        "automaticSkipRecommended": False,
    }
    source = benchmark if isinstance(benchmark, Mapping) else {}
    if (
        source.get("available") is not True
        or str(source.get("appId") or "") != str(app_id)
        or str(source.get("buildId") or "") != str(build_id)
    ):
        return unavailable

    baseline = _non_negative_int(source.get("baselineBytes"))
    levels = source.get("levels")
    level_data = (
        levels.get(str(level))
        if isinstance(levels, Mapping)
        and isinstance(levels.get(str(level)), Mapping)
        else {}
    )
    predicted_at_baseline = _non_negative_int(
        level_data.get("estimatedPhysicalBytes")
    )
    current_uncompressed = _non_negative_int(current_uncompressed_bytes)
    current_disk_usage = _non_negative_int(current_disk_usage_bytes)
    if (
        baseline is None
        or baseline <= 0
        or predicted_at_baseline is None
        or current_uncompressed is None
        or current_uncompressed <= 0
        or current_disk_usage is None
        or current_disk_usage <= 0
    ):
        return unavailable

    predicted_ratio = predicted_at_baseline / baseline
    predicted_physical = max(
        0,
        (
            current_uncompressed * predicted_at_baseline
            + baseline // 2
        )
        // baseline,
    )
    total_potential = max(0, current_uncompressed - predicted_physical)
    additional_saving = max(0, current_disk_usage - predicted_physical)
    low_benefit = bool(
        additional_saving < LOW_ADDITIONAL_BENEFIT_BYTES
        or additional_saving
        < current_disk_usage * LOW_ADDITIONAL_BENEFIT_FRACTION
    )
    return {
        "available": True,
        "reason": "",
        "level": int(level),
        "source": "benchmark_estimate",
        "currentSavingBytes": current_compression_saving(
            current_uncompressed,
            current_disk_usage,
        ),
        "estimatedTotalPotentialBytes": total_potential,
        "estimatedAdditionalSavingBytes": additional_saving,
        "estimatedPhysicalBytes": predicted_physical,
        "predictedPhysicalRatio": predicted_ratio,
        "lowBenefit": low_benefit,
        "additionalConfirmationRequired": low_benefit,
        "automaticSkipRecommended": low_benefit,
    }


class BenchmarkEstimateCatalog:
    """Index accepted top-level reports by Steam AppID and exact build ID."""

    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = Path(reports_dir)
        self._loaded = False
        self._reports: dict[tuple[str, str], dict[str, Any]] = {}
        self._app_ids: set[str] = set()

    def estimate_for(self, game: Game) -> dict[str, Any]:
        self._ensure_loaded()
        app_id = str(game.steam_app_id or "").strip()
        build_id = str(game.steam_build_id or "").strip()
        unavailable = {
            "available": False,
            "reason": "No current estimate",
            "appId": app_id,
            "buildId": build_id,
        }
        if not app_id or not build_id:
            return unavailable
        report = self._reports.get((app_id, build_id))
        if report is None:
            return unavailable
        algorithms = report.get("algorithms")
        if not isinstance(algorithms, list):
            return unavailable
        by_level = {
            int(item["level"]): item
            for item in algorithms
            if isinstance(item, Mapping)
            and item.get("family") == "zstd"
            and item.get("btrfs_compatible", True) is True
            and item.get("available") is True
            and isinstance(item.get("level"), int)
            and int(item["level"]) in {1, 3, 6, 9}
        }
        level3 = by_level.get(3)
        level9 = by_level.get(9)
        if level3 is None or level9 is None:
            return unavailable
        game_data = report.get("game")
        if not isinstance(game_data, Mapping):
            return unavailable
        try:
            baseline = max(0, int(game_data["logical_bytes"]))
            zstd3_reduction = max(
                0,
                int(
                    level3[
                        "estimated_total_payload_reduction_from_uncompressed_baseline_bytes"
                    ]
                ),
            )
            zstd9_reduction = max(
                0,
                int(
                    level9[
                        "estimated_total_payload_reduction_from_uncompressed_baseline_bytes"
                    ]
                ),
            )
            zstd3_size = max(0, int(level3["estimated_game_compressed_bytes"]))
            zstd9_size = max(0, int(level9["estimated_game_compressed_bytes"]))
        except (KeyError, TypeError, ValueError):
            return unavailable
        level_rows: dict[str, dict[str, Any]] = {}
        try:
            for level, item in by_level.items():
                physical = max(0, int(item["estimated_game_compressed_bytes"]))
                reduction = max(
                    0,
                    int(
                        item[
                            "estimated_total_payload_reduction_from_uncompressed_baseline_bytes"
                        ]
                    ),
                )
                level_rows[str(level)] = {
                    "level": level,
                    "estimatedPhysicalBytes": physical,
                    "potentialFromBenchmarkBaselineBytes": reduction,
                    "predictedPhysicalRatio": (
                        physical / baseline if baseline > 0 else None
                    ),
                }
        except (KeyError, TypeError, ValueError):
            return unavailable
        return {
            "available": True,
            "reason": "",
            "appId": app_id,
            "buildId": build_id,
            "baselineBytes": baseline,
            "zstd3PotentialBytes": zstd3_reduction,
            "zstd9PotentialBytes": zstd9_reduction,
            "zstd3EstimatedSizeBytes": zstd3_size,
            "zstd9EstimatedSizeBytes": zstd9_size,
            "levels": level_rows,
            "baselineKind": "uncompressed_payload",
            "createdAt": str(report.get("created_at") or ""),
        }

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._reports_dir.is_dir():
            return
        candidates: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        for path in self._reports_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            if (
                raw.get("report_type") != "game-compression-benchmark"
                or raw.get("cancelled") is True
            ):
                continue
            game = raw.get("game")
            if not isinstance(game, Mapping):
                continue
            if (
                game.get("steam_manifest_stable") is not True
                or game.get("inventory_stable") is not True
            ):
                continue
            app_id = str(game.get("steam_app_id") or "").strip()
            build_id = str(game.get("steam_build_id") or "").strip()
            if not app_id or not build_id:
                continue
            self._app_ids.add(app_id)
            created = str(raw.get("created_at") or "")
            key = (app_id, build_id)
            previous = candidates.get(key)
            if previous is None or created > previous[0]:
                candidates[key] = (created, raw)
        self._reports = {key: raw for key, (_, raw) in candidates.items()}


__all__ = [
    "BenchmarkEstimateCatalog",
    "LOW_ADDITIONAL_BENEFIT_BYTES",
    "LOW_ADDITIONAL_BENEFIT_FRACTION",
    "current_compression_saving",
    "normalized_benchmark_projection",
]
