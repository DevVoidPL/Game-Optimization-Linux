"""Measurement contract: metrics of one recorded session from its MangoHud log.

MangoHud writes one row per ``log_interval`` (100 ms in GameOpti recordings),
so frame statistics are computed from periodic samples, not from every frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, median
from typing import Any, Mapping

from ..performance_analysis import MangoHudLogParser, _percentile

WARMUP_SECONDS = 20.0
TAIL_SECONDS = 5.0
MIN_GAMEPLAY_SECONDS = 60.0
MIN_SAMPLES = 300
# A stutter is a sample whose frame time is at least twice the session median
# and at least 8 ms longer than it (both conditions, stated in every result).
STUTTER_FACTOR = 2.0
STUTTER_MIN_EXTRA_MS = 8.0

METRIC_KEYS = (
    "averageFps", "onePercentLowFps", "p50FrametimeMs", "p95FrametimeMs", "p99FrametimeMs",
    "stuttersPerMinute", "vramMb", "gttMb", "ramMb", "gpuLoad", "cpuLoad", "gpuClockMhz",
    "cpuClockMhz", "gpuTempC", "cpuTempC", "gpuPowerW", "cpuPowerW",
)


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


@dataclass(frozen=True, slots=True)
class SessionMetrics:
    source: str
    total_samples: int
    samples: int
    warmup_seconds: float
    gameplay_seconds: float | None
    sample_interval_ms: float | None
    fps_kind: str  # "rendered" or "output" (frame generation active)
    values: Mapping[str, float | None]
    stutter_count: int
    stutter_threshold_ms: float | None
    issues: tuple[str, ...] = ()
    gtt_source: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def sufficient(self) -> bool:
        return (self.samples >= MIN_SAMPLES and self.gameplay_seconds is not None
                and self.gameplay_seconds >= MIN_GAMEPLAY_SECONDS and not self.issues)

    def value(self, name: str) -> float | None:
        return self.values.get(name)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "totalSamples": self.total_samples, "samples": self.samples,
                "warmupSeconds": self.warmup_seconds, "gameplaySeconds": _round(self.gameplay_seconds, 1),
                "sampleIntervalMs": _round(self.sample_interval_ms, 1), "fpsKind": self.fps_kind,
                "values": {key: _round(self.values.get(key)) for key in METRIC_KEYS},
                "stutterCount": self.stutter_count, "stutterThresholdMs": _round(self.stutter_threshold_ms),
                "stutterRule": f"frame time >= {STUTTER_FACTOR:g}x median and >= median + {STUTTER_MIN_EXTRA_MS:g} ms",
                "issues": list(self.issues), "sufficient": self.sufficient, "gttSource": self.gtt_source,
                "minimums": {"gameplaySeconds": MIN_GAMEPLAY_SECONDS, "samples": MIN_SAMPLES},
                **dict(self.extra)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionMetrics":
        values = value.get("values") if isinstance(value.get("values"), Mapping) else {}
        return cls(str(value.get("source") or ""), int(value.get("totalSamples") or 0),
                   int(value.get("samples") or 0), float(value.get("warmupSeconds") or 0),
                   float(value["gameplaySeconds"]) if value.get("gameplaySeconds") is not None else None,
                   float(value["sampleIntervalMs"]) if value.get("sampleIntervalMs") is not None else None,
                   str(value.get("fpsKind") or "rendered"),
                   {key: (float(values[key]) if values.get(key) is not None else None) for key in METRIC_KEYS},
                   int(value.get("stutterCount") or 0),
                   float(value["stutterThresholdMs"]) if value.get("stutterThresholdMs") is not None else None,
                   tuple(str(item) for item in value.get("issues", ()) or ()),
                   str(value.get("gttSource") or ""))


def session_metrics(path: Path, *, frame_generation_active: bool = False,
                    gpu_memory_summary: Mapping[str, Any] | None = None,
                    warmup_seconds: float = WARMUP_SECONDS) -> SessionMetrics:
    """Metrics after the warm-up and without the last seconds (exit to menu)."""

    source, rows = MangoHudLogParser().read_samples(Path(path))
    issues: list[str] = []
    times = [row.get("time") for row in rows]
    if not rows or any(value is None for value in times):
        issues.append("timestamps_missing")
        usable = [row for row in rows if row.get("frametime", 0) > 0]
    else:
        start, end = float(times[0]), float(times[-1])
        usable = [row for row in rows if row.get("frametime", 0) > 0
                  and start + warmup_seconds <= float(row["time"]) <= end - TAIL_SECONDS]
    frametimes = [row["frametime"] for row in usable]
    fps = [row["fps"] for row in usable if row.get("fps", 0) > 0]
    usable_times = [float(row["time"]) for row in usable if row.get("time") is not None]
    gameplay = usable_times[-1] - usable_times[0] if len(usable_times) >= 2 else None
    interval = (gameplay / (len(usable_times) - 1) * 1000) if gameplay and len(usable_times) > 1 else None
    middle = median(frametimes) if frametimes else None
    threshold = max(middle * STUTTER_FACTOR, middle + STUTTER_MIN_EXTRA_MS) if middle else None
    stutters = sum(1 for value in frametimes if threshold is not None and value >= threshold)

    def column(name: str) -> float | None:
        return _mean([row[name] for row in usable if name in row])

    values: dict[str, float | None] = {
        "averageFps": 1000.0 / fmean(frametimes) if frametimes else None,
        "onePercentLowFps": _percentile(fps, 0.01) if len(fps) >= 100 else None,
        "p50FrametimeMs": middle,
        "p95FrametimeMs": _percentile(frametimes, 0.95),
        "p99FrametimeMs": _percentile(frametimes, 0.99),
        "stuttersPerMinute": stutters / (gameplay / 60.0) if gameplay else None,
        "vramMb": column("vram"), "ramMb": column("ram"), "gpuLoad": column("gpu"),
        "cpuLoad": column("cpu"), "gpuClockMhz": column("gpu_clock"), "cpuClockMhz": column("cpu_clock"),
        "gpuTempC": column("gpu_temp"), "cpuTempC": column("cpu_temp"),
        "gpuPowerW": column("gpu_power"), "cpuPowerW": column("cpu_power"),
        "gttMb": None,
    }
    # MangoHud logs 0 W when the sensor is unavailable: treat it as missing.
    for name in ("gpuPowerW", "cpuPowerW", "gpuClockMhz", "cpuClockMhz", "gpuTempC", "cpuTempC"):
        if values[name] is not None and values[name] <= 0:
            values[name] = None
    gtt_source = ""
    summary = dict(gpu_memory_summary or {})
    if isinstance(summary.get("peakGttDevice"), (int, float)) and summary["peakGttDevice"] > 0:
        values["gttMb"] = float(summary["peakGttDevice"]) / (1024 * 1024)
        gtt_source = "peak device GTT from the GPU-memory wrapper (includes other applications)"
    if len(usable) < MIN_SAMPLES:
        issues.append("too_few_samples")
    if gameplay is None or gameplay < MIN_GAMEPLAY_SECONDS:
        issues.append("too_short")
    return SessionMetrics(str(source), len(rows), len(usable), warmup_seconds, gameplay, interval,
                          "output" if frame_generation_active else "rendered", values, stutters,
                          threshold, tuple(dict.fromkeys(issues)), gtt_source)


def find_session_log(log_directory: Path) -> Path | None:
    """Largest MangoHud CSV of a session, excluding the summary file."""

    try:
        logs = [path for path in Path(log_directory).glob("*.csv")
                if path.is_file() and not path.is_symlink() and not path.name.endswith("_summary.csv")]
    except OSError:
        return None
    return max(logs, key=lambda item: item.stat().st_size, default=None)


__all__ = [
    "METRIC_KEYS", "MIN_GAMEPLAY_SECONDS", "MIN_SAMPLES", "STUTTER_FACTOR", "STUTTER_MIN_EXTRA_MS",
    "SessionMetrics", "WARMUP_SECONDS", "find_session_log", "session_metrics",
]
