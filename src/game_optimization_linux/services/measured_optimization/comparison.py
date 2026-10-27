"""Comparability checks and transparent, goal-specific scoring.

Every threshold is a named constant. Each recommendation lists the measured
values before and after, the absolute and percentage differences, the rules
that decided it and the costs. Missing evidence makes the result Inconclusive.
"""

from __future__ import annotations

from typing import Any, Mapping

from .metrics import METRIC_KEYS, SessionMetrics

# Fields that must match between baseline and candidate (unless they are the
# candidate's intended variable). "Unknown" on either side cannot be verified.
CONTROLLED_FIELDS = (
    ("build", "Game build"),
    ("executable", "Executable"),
    ("displayResolution", "Display resolution"),
    ("renderResolution", "Render resolution"),
    ("api", "Renderer/API"),
    ("runtime", "Proton/runtime"),
    ("graphicsSettings", "In-game graphics settings"),
    ("rayTracing", "Ray tracing"),
    ("upscaler", "Upscaler"),
    ("frameGeneration", "Frame generation"),
    ("gpu", "GPU"),
    ("driver", "Driver"),
)
LEVELS = ("controlled", "approximate", "not_comparable")
DURATION_RATIO_LIMIT = 2.0

# Changes smaller than these are treated as noise of a single A/B pair.
FPS_NOISE = 0.03
LOW_NOISE = 0.05
P99_NOISE = 0.05
SEVERE_P99 = 0.15
STUTTER_RELATIVE = 0.25
STUTTER_ABSOLUTE = 1.0  # per minute
POWER_MIN_SAVING = 0.05
POWER_MAX_COST = 0.10
BALANCED_MAX_FPS_LOSS = 0.05
STABLE_MAX_FPS_LOSS = 0.10
FLOOR_LOW_FACTOR = 0.75

HIGHER_IS_BETTER = {"averageFps", "onePercentLowFps"}
LOWER_IS_BETTER = {"p50FrametimeMs", "p95FrametimeMs", "p99FrametimeMs", "stuttersPerMinute", "gpuPowerW", "cpuPowerW"}
UNKNOWN = "Unknown"


def check_comparability(before: Mapping[str, Any], after: Mapping[str, Any], *, intended: tuple[str, ...] = (),
                        before_metrics: SessionMetrics | None, after_metrics: SessionMetrics | None,
                        config_diff_ok: bool = True) -> dict[str, Any]:
    """Automatic ceiling of the comparability level and the reasons for it."""

    violations: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for name, label in CONTROLLED_FIELDS:
        old, new = before.get(name, UNKNOWN), after.get(name, UNKNOWN)
        row = {"field": name, "label": label, "before": old, "after": new}
        if name in intended:
            continue
        if old in (None, "", UNKNOWN) or new in (None, "", UNKNOWN):
            unverified.append(row)
        elif old != new:
            violations.append(row)
    issues: list[str] = []
    if not config_diff_ok:
        issues.append("more_than_one_variable")
    for label, metrics in (("baseline", before_metrics), ("candidate", after_metrics)):
        if metrics is None:
            issues.append(f"{label}_missing")
        elif not metrics.sufficient:
            issues.extend(f"{label}_{issue}" for issue in (metrics.issues or ("insufficient",)))
    duration_mismatch = False
    if before_metrics and after_metrics and before_metrics.gameplay_seconds and after_metrics.gameplay_seconds:
        ratio = max(before_metrics.gameplay_seconds, after_metrics.gameplay_seconds) / min(
            before_metrics.gameplay_seconds, after_metrics.gameplay_seconds)
        duration_mismatch = ratio > DURATION_RATIO_LIMIT
    base = "not_comparable" if violations or issues else "approximate" if duration_mismatch else "controlled"
    ceiling = "approximate" if base == "controlled" and unverified else base
    return {"ceiling": ceiling, "baseCeiling": base, "violations": violations, "unverified": unverified,
            "issues": list(dict.fromkeys(issues)), "durationMismatch": duration_mismatch}


def final_level(automatic: Mapping[str, Any], user: str, *, unverified_confirmed: bool = False) -> str:
    """The user's confirmation can only lower the automatic ceiling. Fields
    GameOpti cannot read (for example ray tracing) keep the result at
    "approximate" unless the user explicitly confirms they did not change."""

    if user not in LEVELS:
        return "pending"
    order = {name: index for index, name in enumerate(LEVELS)}
    ceiling = str((automatic.get("baseCeiling") if unverified_confirmed else automatic.get("ceiling")) or "not_comparable")
    return max(user, ceiling, key=lambda item: order[item])


def metric_table(before: SessionMetrics, after: SessionMetrics) -> list[dict[str, Any]]:
    rows = []
    for name in METRIC_KEYS:
        old, new = before.value(name), after.value(name)
        delta = (new - old) if old is not None and new is not None else None
        percent = (delta / old * 100.0) if delta is not None and old else None
        rows.append({"metric": name, "before": _r(old), "after": _r(new), "absolute": _r(delta),
                     "percent": _r(percent, 1),
                     "better": "higher" if name in HIGHER_IS_BETTER else "lower" if name in LOWER_IS_BETTER else "",
                     "available": old is not None and new is not None})
    return rows


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _change(before: SessionMetrics, after: SessionMetrics, name: str) -> float | None:
    old, new = before.value(name), after.value(name)
    if old is None or new is None or old == 0:
        return None
    return (new - old) / old


def _reason(code: str, text: str, *values: Any) -> dict[str, Any]:
    return {"code": code, "text": text, "values": [str(value) for value in values]}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def score(goal: str, before: SessionMetrics | None, after: SessionMetrics | None, candidate: Mapping[str, Any], *,
          level: str, activation: Mapping[str, Any], observations: Mapping[str, str] | None = None,
          fps_floor: int = 0, after_exit_code: int | None = None,
          frame_generation: tuple[bool, bool] = (False, False)) -> dict[str, Any]:
    """Keep / Revert / Inconclusive with every rule that produced it."""

    observations = dict(observations or {})
    reasons: list[dict[str, Any]] = []
    costs: list[dict[str, Any]] = []
    quality = str(candidate.get("imageQuality") or "none")
    if quality == "lossy_upscaling":
        costs.append(_reason("cost_lossy_upscaling", "Lower render resolution with lossy upscaling"))
    elif quality == "presentation":
        costs.append(_reason("cost_presentation", "Presentation goes through another compositor"))
    if observations.get("visual") == "worse":
        costs.append(_reason("cost_visual_reported", "You reported worse image quality"))
    if not after or not before:
        return _result("inconclusive", [_reason("missing_session", "A measured session is missing")], costs, level, before, after)
    table = metric_table(before, after)

    def done(decision: str) -> dict[str, Any]:
        return _result(decision, reasons, costs, level, before, after, table,
                       quality=quality, visual=str(observations.get("visual") or "not_checked"), goal=goal)

    # Conditions that make any judgement impossible or that always reject.
    if observations.get("stability") == "crash":
        reasons.append(_reason("candidate_crash", "The candidate session crashed"))
        return done("revert")
    if level in ("pending", "not_comparable"):
        reasons.append(_reason("not_comparable", "The sessions are not comparable"))
        return done("inconclusive")
    if not activation.get("verified"):
        reasons.append(_reason("activation_unverified", "The change was not verified in the measured launch"))
        return done("inconclusive")
    if after_exit_code not in (None, 0) and observations.get("stability") not in ("same", "better"):
        reasons.append(_reason("abnormal_exit", "The candidate session ended with exit code %1", after_exit_code))
        return done("inconclusive")
    if observations.get("stability") == "worse":
        reasons.append(_reason("instability_reported", "You reported less stable gameplay"))
        return done("revert")
    if any(frame_generation):
        if goal in ("lowest_latency", "stable_image") and frame_generation[1]:
            reasons.append(_reason("frame_generation_penalty", "Frame generation adds latency and generated frames"))
            return done("revert")
        reasons.append(_reason("rendered_fps_unavailable", "Frame generation is active, so rendered FPS is not measured"))
        return done("inconclusive")

    fps = _change(before, after, "averageFps")
    low = _change(before, after, "onePercentLowFps")
    p99 = _change(before, after, "p99FrametimeMs")
    old_stutter, new_stutter = before.value("stuttersPerMinute"), after.value("stuttersPerMinute")
    stutter_delta = (new_stutter - old_stutter) if old_stutter is not None and new_stutter is not None else None
    pacing_better = bool((p99 is not None and p99 <= -P99_NOISE) or (
        stutter_delta is not None and stutter_delta <= -STUTTER_ABSOLUTE
        and old_stutter and -stutter_delta / old_stutter >= STUTTER_RELATIVE))
    pacing_worse = bool((p99 is not None and p99 >= P99_NOISE) or (
        stutter_delta is not None and stutter_delta >= STUTTER_ABSOLUTE
        and (not old_stutter or stutter_delta / old_stutter >= STUTTER_RELATIVE)))
    power_before = _power(before)
    power_after = _power(after)
    power = (power_after - power_before) / power_before if power_before and power_after is not None else None

    if fps is None:
        reasons.append(_reason("fps_missing", "Average FPS is missing"))
        return done("inconclusive")

    if goal == "maximum_performance":
        if p99 is not None and p99 >= SEVERE_P99:
            reasons.append(_reason("severe_p99", "99th percentile frame time worsened by %1", _pct(p99)))
            return done("revert")
        if fps <= -FPS_NOISE or (low is not None and low <= -LOW_NOISE):
            reasons.append(_reason("performance_worse", "Average FPS %1, 1% low %2", _pct(fps), _pct(low)))
            return done("revert")
        if (fps >= FPS_NOISE and (low is None or low > -LOW_NOISE)) or (low is not None and low >= LOW_NOISE and fps > -FPS_NOISE):
            reasons.append(_reason("performance_better", "Average FPS %1, 1% low %2", _pct(fps), _pct(low)))
            return done("keep")
        reasons.append(_reason("within_noise", "Changes are within %1 (FPS) and %2 (1% low)", _pct(FPS_NOISE), _pct(LOW_NOISE)))
        return done("inconclusive")

    if goal == "balanced":
        if fps <= -BALANCED_MAX_FPS_LOSS:
            reasons.append(_reason("fps_cost", "Average FPS fell by %1", _pct(fps)))
            return done("revert")
        if power is not None and power >= POWER_MAX_COST:
            reasons.append(_reason("power_cost", "Measured power rose by %1", _pct(power)))
            return done("revert")
        if quality != "none" and observations.get("visual") == "worse":
            reasons.append(_reason("quality_cost", "Image quality was reported worse"))
            return done("revert")
        if pacing_worse:
            reasons.append(_reason("pacing_worse", "Frame pacing worsened (p99 %1)", _pct(p99)))
            return done("revert")
        if pacing_better:
            reasons.append(_reason("pacing_better", "Frame pacing improved (p99 %1)", _pct(p99)))
            if power is None:
                costs.append(_reason("power_not_measured", "Power was not measured in both sessions"))
            return done("keep")
        reasons.append(_reason("within_noise_pacing", "Frame pacing changed less than %1", _pct(P99_NOISE)))
        return done("inconclusive")

    if goal == "low_power":
        if fps_floor <= 0:
            reasons.append(_reason("floor_missing", "Select an FPS floor for Low Power"))
            return done("inconclusive")
        if power is None:
            reasons.append(_reason("power_not_measured", "Power was not measured in both sessions"))
            return done("inconclusive")
        average, one_low = after.value("averageFps"), after.value("onePercentLowFps")
        if average is None or average < fps_floor or (one_low is not None and one_low < fps_floor * FLOOR_LOW_FACTOR):
            reasons.append(_reason("floor_missed", "The candidate fell below the %1 FPS floor", fps_floor))
            return done("revert")
        if power <= -POWER_MIN_SAVING:
            reasons.append(_reason("power_saved", "Measured power changed by %1 while staying above %2 FPS", _pct(power), fps_floor))
            return done("keep")
        if power > 0:
            reasons.append(_reason("power_not_saved", "Measured power changed by %1", _pct(power)))
            return done("revert")
        reasons.append(_reason("within_noise_power", "Power changed less than %1", _pct(POWER_MIN_SAVING)))
        return done("inconclusive")

    if goal == "lowest_latency":
        costs.append(_reason("latency_not_measured", "No click-to-photon latency was measured; FPS and frame time are proxies"))
        if fps <= -FPS_NOISE or (p99 is not None and p99 >= P99_NOISE):
            reasons.append(_reason("latency_proxy_worse", "Average FPS %1, p99 frame time %2", _pct(fps), _pct(p99)))
            return done("revert")
        if (fps >= FPS_NOISE and (p99 is None or p99 < P99_NOISE)) or (p99 is not None and p99 <= -P99_NOISE and fps > -0.02):
            reasons.append(_reason("latency_proxy_better", "Average FPS %1, p99 frame time %2", _pct(fps), _pct(p99)))
            return done("keep")
        reasons.append(_reason("within_noise", "Changes are within %1 (FPS) and %2 (1% low)", _pct(FPS_NOISE), _pct(LOW_NOISE)))
        return done("inconclusive")

    visual_evaluated = observations.get("visual") in ("same", "better", "worse")
    if goal in ("stable_image", "best_quality") and quality != "none" and not visual_evaluated:
        reasons.append(_reason("quality_not_evaluated", "Image quality was not evaluated; this goal needs your image-quality decision"))
        return done("inconclusive")

    if goal == "stable_image":
        if quality == "lossy_upscaling":
            reasons.append(_reason("image_changed", "The candidate lowers the render resolution"))
            return done("revert")
        if observations.get("visual") == "worse":
            reasons.append(_reason("quality_cost", "Image quality was reported worse"))
            return done("revert")
        if fps <= -STABLE_MAX_FPS_LOSS:
            reasons.append(_reason("fps_cost", "Average FPS fell by %1", _pct(fps)))
            return done("revert")
        if pacing_worse:
            reasons.append(_reason("pacing_worse", "Frame pacing worsened (p99 %1)", _pct(p99)))
            return done("revert")
        if pacing_better:
            reasons.append(_reason("pacing_better", "Frame pacing improved (p99 %1)", _pct(p99)))
            if observations.get("visual") in (None, "", "not_checked"):
                costs.append(_reason("visual_not_checked", "Image quality was not checked by you"))
            return done("keep")
        reasons.append(_reason("within_noise_pacing", "Frame pacing changed less than %1", _pct(P99_NOISE)))
        return done("inconclusive")

    if goal == "best_quality":
        if fps_floor <= 0:
            reasons.append(_reason("minimum_missing", "Select a minimum FPS for Best Quality"))
            return done("inconclusive")
        if quality == "lossy_upscaling" or observations.get("visual") == "worse":
            reasons.append(_reason("image_changed", "The candidate lowers the render resolution") if quality == "lossy_upscaling"
                           else _reason("quality_cost", "Image quality was reported worse"))
            return done("revert")
        average, one_low = after.value("averageFps"), after.value("onePercentLowFps")
        if average is None or average < fps_floor or (one_low is not None and one_low < fps_floor * FLOOR_LOW_FACTOR):
            reasons.append(_reason("floor_missed", "The candidate fell below the %1 FPS floor", fps_floor))
            return done("revert")
        if fps >= FPS_NOISE or (low is not None and low >= LOW_NOISE):
            reasons.append(_reason("headroom_better", "More performance headroom at the same image quality (FPS %1)", _pct(fps)))
            return done("keep")
        reasons.append(_reason("no_benefit", "No measurable benefit at the same image quality"))
        return done("inconclusive")

    reasons.append(_reason("unknown_goal", "Unknown optimization goal"))
    return done("inconclusive")


def _power(metrics: SessionMetrics) -> float | None:
    gpu = metrics.value("gpuPowerW")
    if gpu is None:
        return None
    cpu = metrics.value("cpuPowerW")
    return gpu + (cpu or 0.0)


def quality_status(image_quality: str, visual: str) -> str:
    """not_affected | evaluated_same | evaluated_better | evaluated_worse | not_evaluated"""
    if visual in ("same", "better", "worse"):
        return "evaluated_" + visual
    return "not_affected" if image_quality == "none" else "not_evaluated"


def confidence_parts(decision: str, level: str, image_quality: str, visual: str,
                     performance: str = "") -> dict[str, Any]:
    """Separate answers: is the comparison sound, is the performance effect
    measured, was image quality judged, and is the whole result accepted."""
    comparable = level == "controlled"
    quality = quality_status(image_quality, visual)
    quality_ok = quality != "not_evaluated" or image_quality != "lossy_upscaling"
    measured = comparable and decision != "inconclusive"
    overall = ("verified" if measured and quality_ok
               else "performance_only" if measured else "unverified")
    return {"comparabilityVerified": comparable, "performanceVerified": measured,
            "performance": performance, "imageQuality": quality,
            "overall": overall, "fullyVerified": overall == "verified"}


def _performance(table: list[dict[str, Any]] | None) -> str:
    row = next((item for item in table or () if item["metric"] == "averageFps"), None)
    if not row or row.get("percent") is None:
        return "not_measured"
    return "improved" if row["percent"] >= FPS_NOISE * 100 else "worse" if row["percent"] <= -FPS_NOISE * 100 else "unchanged"


def _result(decision: str, reasons: list[dict[str, Any]], costs: list[dict[str, Any]], level: str,
            before: SessionMetrics | None, after: SessionMetrics | None,
            table: list[dict[str, Any]] | None = None, *, quality: str = "none", visual: str = "not_checked",
            goal: str = "") -> dict[str, Any]:
    table = table if table is not None else (metric_table(before, after) if before and after else [])
    parts = confidence_parts(decision, level, quality, visual, _performance(table))
    if parts["overall"] == "performance_only":
        costs = [*costs, _reason("quality_not_evaluated_cost", "Performance improvement measured; image quality not evaluated")
                 if decision == "keep" else _reason("quality_not_evaluated_cost", "Image quality was not evaluated")]
    verified = parts["fullyVerified"]
    return {"decision": decision, "verified": verified, **parts, "imageQualityImpact": quality,
            "confidence": "controlled" if verified else "approximate" if level == "approximate" else "insufficient",
            "comparability": level, "reasons": reasons, "costs": costs,
            "metrics": table,
            "thresholds": {"fpsNoise": FPS_NOISE, "onePercentLowNoise": LOW_NOISE, "p99Noise": P99_NOISE,
                           "severeP99": SEVERE_P99, "stutterRelative": STUTTER_RELATIVE,
                           "stutterAbsolutePerMinute": STUTTER_ABSOLUTE, "powerMinSaving": POWER_MIN_SAVING,
                           "powerMaxCost": POWER_MAX_COST, "floorOnePercentFactor": FLOOR_LOW_FACTOR}}


__all__ = ["CONTROLLED_FIELDS", "LEVELS", "check_comparability", "confidence_parts", "final_level", "metric_table",
           "quality_status", "score"]
