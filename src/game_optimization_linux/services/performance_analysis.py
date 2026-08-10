from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
import re
from statistics import fmean
from typing import Iterable

from game_optimization_linux.models import (
    BottleneckAnalysis,
    PerformanceComparison,
    PerformanceMeasurement,
    SystemSnapshot,
)


_ALIASES = {
    "fps": ("fps", "framerate"),
    "frametime": ("frametime", "frametimems", "frametime_ms"),
    "cpu": ("cpuload", "cpuusage", "cpu_load", "cpu"),
    "gpu": ("gpuload", "gpuusage", "gpu_load", "gpu"),
    "ram": ("ram", "ramused", "ramusage", "procmem"),
    "vram": ("vram", "vramused", "vramusage", "procvram", "proc_vram"),
    "gpu_temp": ("gputemp", "gpu_temp", "gputemperature"),
    "time": ("time", "elapsed", "elapsedseconds", "timestamp"),
}


def _heading(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", value.casefold().replace("%", ""))


def _number(value: str) -> float | None:
    match = re.search(r"[-+]?[0-9]+(?:[.,][0-9]+)?", str(value).strip())
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class MangoHudLogParser:
    def parse(self, path: Path) -> PerformanceMeasurement:
        source = path.resolve(strict=True)
        if not source.is_file() or source.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("MangoHud log is not a supported file")
        lines = source.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        header_index, delimiter = self._header(lines)
        if header_index is None:
            raise ValueError("MangoHud log header was not found")
        reader = csv.DictReader(lines[header_index:], delimiter=delimiter)
        headings = {
            key: self._column(reader.fieldnames or (), aliases)
            for key, aliases in _ALIASES.items()
        }
        rows: dict[str, list[float]] = {key: [] for key in headings}
        samples = 0
        for raw in reader:
            fps_value = _number(raw.get(headings["fps"] or "", ""))
            frametime_value = _number(raw.get(headings["frametime"] or "", ""))
            if fps_value is None and frametime_value is None:
                continue
            samples += 1
            for key, column in headings.items():
                if column:
                    value = _number(raw.get(column, ""))
                    if value is not None:
                        rows[key].append(value)
        if samples == 0:
            raise ValueError("MangoHud log contains no performance samples")
        fps = rows["fps"]
        frametimes = rows["frametime"]
        times = rows["time"]
        duration = max(times) - min(times) if len(times) >= 2 else None
        limitations: list[str] = []
        if duration is None or duration < 10:
            limitations.append("The recorded session was shorter than 10 seconds")
        if samples < 100:
            limitations.append("Fewer than 100 performance samples were recorded")
        for key, label in (("cpu", "CPU"), ("gpu", "GPU"), ("ram", "RAM"), ("vram", "VRAM")):
            if not rows[key]:
                limitations.append(f"{label} utilization was not present in the MangoHud log")
        core_complete = bool(fps and frametimes and rows["cpu"] and rows["gpu"])
        quality = (
            "high"
            if samples >= 300 and duration is not None and duration >= 30 and core_complete
            else "medium"
            if samples >= 100 and duration is not None and duration >= 10 and fps and frametimes
            else "low"
        )
        return PerformanceMeasurement(
            source_path=str(source),
            samples=samples,
            duration_seconds=duration,
            average_fps=fmean(fps) if fps else None,
            minimum_fps=min(fps) if fps else None,
            one_percent_low_fps=_percentile(fps, 0.01) if len(fps) >= 100 else None,
            average_frametime_ms=fmean(frametimes) if frametimes else None,
            p95_frametime_ms=_percentile(frametimes, 0.95),
            p99_frametime_ms=_percentile(frametimes, 0.99),
            cpu_usage_percent=fmean(rows["cpu"]) if rows["cpu"] else None,
            gpu_usage_percent=fmean(rows["gpu"]) if rows["gpu"] else None,
            ram_used_mb=fmean(rows["ram"]) if rows["ram"] else None,
            vram_used_mb=fmean(rows["vram"]) if rows["vram"] else None,
            gpu_temperature_c=fmean(rows["gpu_temp"]) if rows["gpu_temp"] else None,
            measured_at=datetime.fromtimestamp(source.stat().st_mtime, tz=UTC),
            quality=quality,
            limitations=tuple(limitations),
        )

    @staticmethod
    def _header(lines: list[str]) -> tuple[int | None, str]:
        for index, line in enumerate(lines):
            for delimiter in (",", ";"):
                values = [_heading(value) for value in next(csv.reader([line], delimiter=delimiter))]
                if any(value in _ALIASES["fps"] for value in values) and any(
                    value in _ALIASES["frametime"] for value in values
                ):
                    return index, delimiter
        return None, ","

    @staticmethod
    def _column(fieldnames: Iterable[str], aliases: tuple[str, ...]) -> str | None:
        for field in fieldnames:
            if _heading(field) in aliases:
                return field
        return None


class BottleneckAnalyzer:
    def analyze(
        self,
        measurement: PerformanceMeasurement | None,
        system: SystemSnapshot,
        *,
        target_fps: int | None = None,
    ) -> BottleneckAnalysis:
        if measurement is None or not measurement.available:
            return BottleneckAnalysis(
                "insufficient_data",
                0.0,
                (),
                ("A MangoHud baseline with FPS, frametime, CPU and GPU metrics is required",),
            )
        evidence: list[str] = []
        limitations: list[str] = list(measurement.limitations)
        quality_factor = {"high": 1.0, "medium": 0.95, "low": 0.55}.get(
            measurement.quality, 0.55
        )
        gpu = measurement.gpu_usage_percent
        cpu = measurement.cpu_usage_percent
        avg = measurement.average_fps
        below_target = bool(target_fps and avg is not None and avg < target_fps * 0.92)
        vram_fraction = (
            measurement.vram_used_mb / (system.vram_gb * 1024)
            if measurement.vram_used_mb is not None and system.vram_gb
            else None
        )
        ram_fraction = (
            measurement.ram_used_mb / (system.ram_gb * 1024)
            if measurement.ram_used_mb is not None and system.ram_gb
            else None
        )
        pacing = bool(
            measurement.average_frametime_ms
            and measurement.p99_frametime_ms
            and measurement.p99_frametime_ms
            > measurement.average_frametime_ms * 1.8
        )
        if vram_fraction is not None and vram_fraction >= 0.90:
            evidence.append(f"VRAM use reached {vram_fraction * 100:.0f}% of detected capacity")
            if gpu is not None:
                evidence.append(f"GPU load averaged {gpu:.1f}%")
            return BottleneckAnalysis("vram_pressure", 0.88 * quality_factor, tuple(evidence), tuple(limitations))
        if ram_fraction is not None and ram_fraction >= 0.88:
            evidence.append(f"RAM use reached {ram_fraction * 100:.0f}% of detected capacity")
            return BottleneckAnalysis("ram_pressure", 0.82 * quality_factor, tuple(evidence), tuple(limitations))
        if gpu is not None and gpu >= 92 and (cpu is None or cpu <= 78) and below_target:
            evidence.append(f"GPU load averaged {gpu:.1f}% while the measured FPS missed the target")
            if cpu is not None:
                evidence.append(f"CPU load averaged {cpu:.1f}%")
            return BottleneckAnalysis("gpu_bottleneck", 0.90 * quality_factor, tuple(evidence), tuple(limitations))
        if cpu is not None and cpu >= 85 and (gpu is None or gpu < 88) and below_target:
            evidence.append(f"CPU load averaged {cpu:.1f}% while GPU load was not saturated")
            if gpu is not None:
                evidence.append(f"GPU load averaged {gpu:.1f}%")
            return BottleneckAnalysis("cpu_bottleneck", 0.84 * quality_factor, tuple(evidence), tuple(limitations))
        if pacing:
            evidence.append(
                f"99th percentile frametime was {measurement.p99_frametime_ms:.1f} ms versus a {measurement.average_frametime_ms:.1f} ms average"
            )
            return BottleneckAnalysis("frame_pacing_problem", 0.82 * quality_factor, tuple(evidence), tuple(limitations))
        if gpu is None or cpu is None:
            limitations.append("CPU or GPU utilization is missing from the log")
        if vram_fraction is None:
            limitations.append("VRAM pressure could not be calculated")
        evidence.append("The available metrics do not show one dominant saturated resource")
        return BottleneckAnalysis("balanced", 0.62 * quality_factor, tuple(evidence), tuple(limitations))


def compare_measurements(
    before: PerformanceMeasurement, after: PerformanceMeasurement
) -> PerformanceComparison:
    evidence: list[str] = []
    if before.quality == "low" or after.quality == "low":
        return PerformanceComparison(
            "insufficient_data",
            ("One of the sessions has low measurement quality",),
            False,
        )
    fps_change: float | None = None
    if before.average_fps and after.average_fps:
        fps_change = (after.average_fps - before.average_fps) / before.average_fps
        evidence.append(f"Average FPS changed by {fps_change * 100:+.1f}%")
    p95_change: float | None = None
    if before.p95_frametime_ms and after.p95_frametime_ms:
        p95_change = (after.p95_frametime_ms - before.p95_frametime_ms) / before.p95_frametime_ms
        evidence.append(f"95th percentile frametime changed by {p95_change * 100:+.1f}%")
    regression = bool(
        (fps_change is not None and fps_change <= -0.05)
        or (p95_change is not None and p95_change >= 0.10)
    )
    improvement = bool(
        not regression
        and (
            (fps_change is not None and fps_change >= 0.05)
            or (p95_change is not None and p95_change <= -0.10)
        )
    )
    outcome = "regression" if regression else "improvement" if improvement else "no_meaningful_change"
    return PerformanceComparison(outcome, tuple(evidence), regression)


__all__ = ["BottleneckAnalyzer", "MangoHudLogParser", "compare_measurements"]
