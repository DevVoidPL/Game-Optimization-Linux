from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
import re
from statistics import fmean, median
from typing import Iterable

from game_optimization_linux.models import (
    BottleneckAnalysis,
    FrameRateAnalysis,
    PerformanceComparison,
    PerformanceMeasurement,
    SystemSnapshot,
)


_ALIASES = {
    "fps": ("fps", "framerate"),
    "frametime": ("frametime", "frametimems", "frametime_ms"),
    "cpu": ("cpuload", "cpuusage", "cpu_load", "cpu"),
    "gpu": ("gpuload", "gpuusage", "gpu_load", "gpu"),
    "ram": ("ram", "ramused", "ram_used", "ramusage", "procmem"),
    "vram": (
        "vram", "vramused", "vramusage", "gpu_vram_used", "procvram", "proc_vram"
    ),
    "gpu_temp": ("gputemp", "gpu_temp", "gputemperature"),
    "gpu_clock": ("gpuclock", "gpu_clock", "gpu_core_clock"),
    "time": ("time", "elapsed", "elapsedseconds", "timestamp"),
}

_WINDOW_SECONDS = 10.0
_MIN_WINDOW_SAMPLES = 30
_MIN_SEGMENT_SAMPLES = 100
_MIN_SEGMENT_SECONDS = 10.0
_MIN_REPRESENTATIVE_SHARE = 0.20


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
        samples: list[dict[str, float]] = []
        for raw in reader:
            fps_value = _number(raw.get(headings["fps"] or "", ""))
            frametime_value = _number(raw.get(headings["frametime"] or "", ""))
            if fps_value is None and frametime_value is None:
                continue
            sample: dict[str, float] = {}
            for key, column in headings.items():
                if column:
                    value = _number(raw.get(column, ""))
                    if value is not None:
                        normalized_column = _heading(column)
                        if normalized_column in {"ram_used", "gpu_vram_used"}:
                            value *= 1024
                        elif key == "time" and normalized_column == "elapsed":
                            value /= 1_000_000_000
                        sample[key] = value
            samples.append(sample)
        if not samples:
            raise ValueError("MangoHud log contains no performance samples")
        selected, representative, selection_reasons = self._representative_samples(
            samples
        )
        paired = [
            sample
            for sample in selected
            if sample.get("fps", 0) > 0 and sample.get("frametime", 0) > 0
        ]
        rows = {
            key: [sample[key] for sample in paired if key in sample]
            for key in headings
        }
        all_times = [sample["time"] for sample in samples if "time" in sample]
        fps = rows["fps"]
        frametimes = rows["frametime"]
        times = rows["time"]
        total_samples = len(samples)
        used_samples = len(paired)
        duration = max(all_times) - min(all_times) if len(all_times) >= 2 else None
        selected_duration = max(times) - min(times) if len(times) >= 2 else None
        limitations: list[str] = []
        if duration is None or duration < 10:
            limitations.append("The recorded session was shorter than 10 seconds")
        if total_samples < 100:
            limitations.append("Fewer than 100 performance samples were recorded")
        for key, label in (("cpu", "CPU"), ("gpu", "GPU"), ("ram", "RAM"), ("vram", "VRAM")):
            if not rows[key]:
                limitations.append(f"{label} utilization was not present in the MangoHud log")
        core_complete = bool(fps and frametimes and rows["cpu"] and rows["gpu"])
        if not representative:
            limitations.extend(selection_reasons)
        quality = (
            "high"
            if representative and used_samples >= 300 and selected_duration is not None
            and selected_duration >= 30 and core_complete
            else "medium"
            if representative and used_samples >= 100 and selected_duration is not None
            and selected_duration >= 10 and fps and frametimes
            else "low"
        )
        return PerformanceMeasurement(
            source_path=str(source),
            samples=used_samples,
            duration_seconds=duration,
            average_fps=(1000.0 / fmean(frametimes)) if frametimes else None,
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
            total_samples=total_samples,
            excluded_samples=total_samples - used_samples,
            selected_duration_seconds=selected_duration,
            representative=representative,
            selection_reasons=selection_reasons,
            median_fps=median(fps) if fps else None,
            p10_fps=_percentile(fps, 0.10),
            p90_fps=_percentile(fps, 0.90),
            p95_fps=_percentile(fps, 0.95),
            p99_fps=_percentile(fps, 0.99),
            median_frametime_ms=median(frametimes) if frametimes else None,
        )

    def _representative_samples(
        self, samples: list[dict[str, float]]
    ) -> tuple[list[dict[str, float]], bool, tuple[str, ...]]:
        times = [sample.get("time") for sample in samples]
        if len(samples) >= _MIN_SEGMENT_SAMPLES and not all(
            value is not None for value in times
        ):
            return samples, False, (
                "A stable segment could not be selected because sample timestamps are missing",
            )
        if len(samples) >= _MIN_SEGMENT_SAMPLES and any(
            float(current) < float(previous)
            for previous, current in zip(times, times[1:])
            if previous is not None and current is not None
        ):
            return samples, False, (
                "A stable segment could not be selected because sample timestamps are not continuous",
            )
        duration = (
            float(times[-1]) - float(times[0])
            if len(times) >= 2 and all(value is not None for value in times)
            else None
        )
        if len(samples) < _MIN_SEGMENT_SAMPLES or duration is None or duration < 10:
            return samples, True, ()

        windows: list[list[dict[str, float]]] = []
        started = float(times[0])
        for sample in samples:
            index = max(0, int((float(sample["time"]) - started) / _WINDOW_SECONDS))
            while len(windows) <= index:
                windows.append([])
            windows[index].append(sample)

        stable: list[tuple[int, list[dict[str, float]], float]] = []
        for index, window in enumerate(windows):
            frametimes = [item["frametime"] for item in window if item.get("frametime", 0) > 0]
            minimum_samples = (
                10 if index in {0, len(windows) - 1} else _MIN_WINDOW_SAMPLES
            )
            if len(window) < minimum_samples or len(frametimes) < minimum_samples:
                continue
            low = _percentile(frametimes, 0.10)
            high = _percentile(frametimes, 0.90)
            if low is None or high is None or low <= 0 or high / low > 1.8:
                continue
            stable.append((index, window, median(frametimes)))

        segments: list[list[tuple[int, list[dict[str, float]], float]]] = []
        for item in stable:
            if not segments:
                segments.append([item])
                continue
            previous = segments[-1][-1]
            ratio = max(previous[2], item[2]) / min(previous[2], item[2])
            if item[0] == previous[0] + 1 and ratio <= 1.6:
                segments[-1].append(item)
            else:
                segments.append([item])

        candidates: list[tuple[float, list[dict[str, float]], float]] = []
        activity_keys = tuple(
            key for key in ("gpu", "cpu", "gpu_clock")
            if any(key in sample for sample in samples)
        )
        activity_maxima = {
            key: max(sample.get(key, 0.0) for sample in samples)
            for key in activity_keys
        }
        for segment in segments:
            selected = [sample for _index, window, _median in segment for sample in window]
            segment_times = [sample["time"] for sample in selected if "time" in sample]
            segment_duration = (
                max(segment_times) - min(segment_times)
                if len(segment_times) >= 2 else 0.0
            )
            if len(selected) < _MIN_SEGMENT_SAMPLES or segment_duration < _MIN_SEGMENT_SECONDS:
                continue
            scores: list[float] = []
            for key in activity_keys:
                maximum = activity_maxima[key]
                values = [sample[key] for sample in selected if key in sample]
                if maximum > 0 and values:
                    scores.append(median(values) / maximum)
            activity = fmean(scores) if scores else 1.0
            candidates.append((activity, selected, median(item[2] for item in segment)))

        if not candidates:
            return [], False, (
                "No stable contiguous measurement window was found",
            )
        candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        activity, selected, selected_frametime = candidates[0]
        reasons: list[str] = [
            "Selected the most active stable contiguous frametime segment"
        ]
        if len(candidates) > 1:
            other_activity, _other_samples, other_frametime = candidates[1]
            activity_gap = activity - other_activity
            regime_ratio = max(selected_frametime, other_frametime) / min(
                selected_frametime, other_frametime
            )
            if activity_gap < 0.08 and regime_ratio > 1.6:
                reasons.append(
                    "Multiple equally active rendering regimes had incompatible frametimes"
                )
                return selected, False, tuple(reasons)
        used_share = len(selected) / len(samples)
        if used_share < _MIN_REPRESENTATIVE_SHARE:
            reasons.append(
                f"Only {used_share * 100:.1f}% of samples belonged to the selected stable segment"
            )
            return selected, False, tuple(reasons)
        if len(selected) != len(samples):
            reasons.append(
                f"Excluded {len(samples) - len(selected)} samples from other or unstable rendering regimes"
            )
        return selected, True, tuple(reasons)

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


class FrameRateAnalyzer:
    def analyze(
        self,
        measurement: PerformanceMeasurement | None,
        system: SystemSnapshot,
    ) -> FrameRateAnalysis:
        if measurement is None or not measurement.available:
            return FrameRateAnalysis.unknown()

        median_fps = measurement.median_fps
        p10_fps = measurement.p10_fps
        p90_fps = measurement.p90_fps
        p95_fps = measurement.p95_fps
        p99_fps = measurement.p99_fps
        median_frametime = measurement.median_frametime_ms
        p99_frametime = measurement.p99_frametime_ms
        if not all(
            value is not None and value > 0
            for value in (
                median_fps,
                p10_fps,
                p90_fps,
                p95_fps,
                p99_fps,
                median_frametime,
                p99_frametime,
            )
        ):
            return FrameRateAnalysis.unknown(
                "FPS and frametime distribution data is incomplete"
            )

        assert median_fps is not None
        assert p10_fps is not None
        assert p90_fps is not None
        assert p95_fps is not None
        assert p99_fps is not None
        assert median_frametime is not None
        assert p99_frametime is not None

        central_spread = (p90_fps - p10_fps) / median_fps
        upper_body_spread = (p95_fps - median_fps) / median_fps
        mean_gap = (
            abs(measurement.average_fps - median_fps) / median_fps
            if measurement.average_fps is not None else 1.0
        )
        expected_frametime = 1000.0 / median_fps
        interval_gap = abs(median_frametime - expected_frametime) / expected_frametime
        frametime_tail = p99_frametime / median_frametime
        duration = measurement.selected_duration_seconds or 0.0

        evidence: list[str] = []
        limitations: list[str] = []
        confidence = 0.0
        if central_spread <= 0.10:
            confidence += 0.24
            evidence.append("FPS remained tightly clustered around one stable ceiling")
        if upper_body_spread <= 0.08:
            confidence += 0.18
            evidence.append("The upper FPS distribution rarely exceeded that ceiling")
        if mean_gap <= 0.05:
            confidence += 0.10
        if interval_gap <= 0.08 and frametime_tail <= 1.15:
            confidence += 0.20
            evidence.append(
                "Frametime remained clustered around the matching frame interval"
            )
        if measurement.samples >= 300 and duration >= 30:
            confidence += 0.10
            evidence.append(f"The stable regime continued for {duration:.1f} seconds")

        gpu = measurement.gpu_usage_percent
        cpu = measurement.cpu_usage_percent
        if gpu is not None and gpu < 92:
            confidence += 0.14
            evidence.append(f"GPU load averaged {gpu:.1f}%, leaving measured headroom")
        elif gpu is not None:
            limitations.append(
                "GPU saturation can explain the observed ceiling without a frame limiter"
            )
        else:
            limitations.append("GPU utilization is unavailable")
        if cpu is not None and cpu < 92:
            confidence += 0.04
            evidence.append(f"Total CPU load averaged {cpu:.1f}%")
            limitations.append(
                "Total CPU utilization cannot exclude a single-thread bottleneck"
            )
        elif cpu is None:
            limitations.append("CPU utilization is unavailable")

        refresh_rate = system.refresh_rate
        ceiling = median_fps
        if (
            refresh_rate is not None
            and refresh_rate > 0
            and abs(ceiling - refresh_rate) / refresh_rate <= 0.03
        ):
            confidence += 0.03
            evidence.append(
                "The measured ceiling is close to the selected display refresh rate"
            )

        required_shape = (
            central_spread <= 0.10
            and upper_body_spread <= 0.08
            and mean_gap <= 0.05
            and interval_gap <= 0.08
            and frametime_tail <= 1.15
        )
        has_headroom = gpu is not None and gpu < 92 and (cpu is None or cpu < 92)
        if required_shape and has_headroom and confidence >= 0.72:
            return FrameRateAnalysis(
                "likely_capped",
                float(round(ceiling)),
                min(confidence, 0.97),
                tuple(evidence),
                tuple(limitations),
            )

        if not required_shape:
            limitations.append(
                "FPS and frametime did not form a sufficiently tight stable ceiling"
            )
        return FrameRateAnalysis(
            "not_detected",
            None,
            min(0.75, max(0.35, 1.0 - confidence)),
            tuple(evidence),
            tuple(limitations),
        )


class BottleneckAnalyzer:
    def analyze(
        self,
        measurement: PerformanceMeasurement | None,
        system: SystemSnapshot,
        *,
        target_fps: int | None = None,
    ) -> BottleneckAnalysis:
        if (
            measurement is None
            or not measurement.available
            or measurement.quality == "low"
        ):
            limitations = (
                measurement.limitations
                if measurement is not None and measurement.limitations
                else ("A MangoHud baseline with FPS, frametime, CPU and GPU metrics is required",)
            )
            return BottleneckAnalysis(
                "insufficient_data",
                0.0,
                (),
                limitations,
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
        average_frametime = measurement.average_frametime_ms
        p95_frametime = measurement.p95_frametime_ms
        p99_frametime = measurement.p99_frametime_ms
        p95_ratio = (
            p95_frametime / average_frametime
            if p95_frametime is not None and average_frametime else None
        )
        p99_ratio = (
            p99_frametime / average_frametime
            if p99_frametime is not None and average_frametime else None
        )
        low_ratio = (
            measurement.one_percent_low_fps / avg
            if measurement.one_percent_low_fps is not None and avg else None
        )
        pacing = bool(
            p99_ratio is not None
            and (
                (low_ratio is not None and low_ratio < 0.70 and p99_ratio > 1.45)
                or (
                    p95_ratio is not None
                    and p95_ratio > 1.35
                    and p99_ratio > 1.70
                )
            )
        )
        if vram_fraction is not None and vram_fraction >= 0.90:
            evidence.append(f"VRAM use reached {vram_fraction * 100:.0f}% of detected capacity")
            if gpu is not None:
                evidence.append(f"GPU load averaged {gpu:.1f}%")
            return BottleneckAnalysis("vram_pressure", 0.88 * quality_factor, tuple(evidence), tuple(limitations))
        if ram_fraction is not None and ram_fraction >= 0.88:
            evidence.append(f"RAM use reached {ram_fraction * 100:.0f}% of detected capacity")
            return BottleneckAnalysis("ram_pressure", 0.82 * quality_factor, tuple(evidence), tuple(limitations))
        if gpu is not None and gpu >= 95 and (cpu is None or cpu <= 85):
            evidence.append(f"GPU load averaged {gpu:.1f}% across the representative segment")
            if below_target:
                evidence.append("The measured FPS also missed the configured target")
            if cpu is not None:
                evidence.append(f"CPU load averaged {cpu:.1f}%")
            return BottleneckAnalysis("gpu_bottleneck", 0.88 * quality_factor, tuple(evidence), tuple(limitations))
        if cpu is not None and cpu >= 90 and (gpu is None or gpu < 85) and below_target:
            evidence.append(f"Total CPU load averaged {cpu:.1f}% while GPU load was not saturated")
            if gpu is not None:
                evidence.append(f"GPU load averaged {gpu:.1f}%")
            limitations.append(
                "Per-thread CPU utilization is unavailable, so CPU-bound confidence is limited"
            )
            return BottleneckAnalysis("cpu_bottleneck", 0.55 * quality_factor, tuple(evidence), tuple(limitations))
        if pacing:
            if p99_frametime is not None and average_frametime is not None:
                evidence.append(
                    f"99th percentile frametime was {p99_frametime:.1f} ms versus a {average_frametime:.1f} ms average"
                )
            if low_ratio is not None and measurement.one_percent_low_fps is not None:
                evidence.append(
                    f"1% low was {measurement.one_percent_low_fps:.1f} FPS versus a {avg:.1f} FPS average"
                )
            return BottleneckAnalysis("frame_pacing_problem", 0.82 * quality_factor, tuple(evidence), tuple(limitations))
        if gpu is None or cpu is None:
            limitations.append("CPU or GPU utilization is missing from the log")
        if vram_fraction is None:
            limitations.append("VRAM pressure could not be calculated")
        evidence.append("The available metrics do not show one dominant saturated resource")
        return BottleneckAnalysis("balanced", 0.72 * quality_factor, tuple(evidence), tuple(limitations))


def compare_measurements(
    before: PerformanceMeasurement,
    after: PerformanceMeasurement,
    *,
    before_frame_rate: FrameRateAnalysis | None = None,
    after_frame_rate: FrameRateAnalysis | None = None,
) -> PerformanceComparison:
    evidence: list[str] = []
    if not before.available or not after.available:
        return PerformanceComparison(
            "insufficient_data",
            ("Both sessions must contain representative comparable measurements",),
            False,
        )

    def change(old: float | None, new: float | None) -> float | None:
        return (new - old) / old if old and new is not None else None

    fps_change = change(before.average_fps, after.average_fps)
    low_change = change(before.one_percent_low_fps, after.one_percent_low_fps)
    p95_change = change(before.p95_frametime_ms, after.p95_frametime_ms)
    p99_change = change(before.p99_frametime_ms, after.p99_frametime_ms)
    gpu_change = change(before.gpu_usage_percent, after.gpu_usage_percent)
    vram_change = change(before.vram_used_mb, after.vram_used_mb)
    for label, value in (
        ("Average FPS", fps_change),
        ("1% low", low_change),
        ("95th percentile frametime", p95_change),
        ("99th percentile frametime", p99_change),
    ):
        if value is not None:
            evidence.append(f"{label} changed by {value * 100:+.1f}%")
    if gpu_change is not None:
        evidence.append(f"GPU usage changed by {gpu_change * 100:+.1f}%")
    if vram_change is not None:
        evidence.append(f"VRAM usage changed by {vram_change * 100:+.1f}%")
    regression = bool(
        (fps_change is not None and fps_change <= -0.05)
        or (low_change is not None and low_change <= -0.08)
        or (p95_change is not None and p95_change >= 0.10)
        or (p99_change is not None and p99_change >= 0.10)
    )
    improvement = bool(
        not regression
        and (
            (
                fps_change is not None
                and fps_change >= 0.04
                and (low_change is None or low_change >= -0.02)
            )
            or (low_change is not None and low_change >= 0.08)
            or (p95_change is not None and p95_change <= -0.08)
        )
        and (p99_change is None or p99_change <= 0.05)
    )
    capped_before_and_after = bool(
        before_frame_rate is not None
        and after_frame_rate is not None
        and before_frame_rate.state == "likely_capped"
        and after_frame_rate.state == "likely_capped"
    )
    fps_stable = fps_change is not None and abs(fps_change) <= 0.03
    gpu_headroom_improved = bool(
        gpu_change is not None
        and gpu_change <= -0.10
        and before.gpu_usage_percent is not None
        and after.gpu_usage_percent is not None
        and before.gpu_usage_percent - after.gpu_usage_percent >= 8.0
    )
    headroom_improvement = bool(
        not regression
        and capped_before_and_after
        and fps_stable
        and gpu_headroom_improved
        and (low_change is None or low_change >= -0.03)
        and (p95_change is None or p95_change <= 0.05)
        and (p99_change is None or p99_change <= 0.05)
    )
    if headroom_improvement:
        evidence.append(
            "GPU headroom improved while both representative sessions remained frame-limited"
        )
    outcome = (
        "regression"
        if regression
        else "headroom_improved"
        if headroom_improvement
        else "improvement"
        if improvement
        else "no_meaningful_change"
    )
    return PerformanceComparison(outcome, tuple(evidence), regression)


__all__ = [
    "BottleneckAnalyzer",
    "FrameRateAnalyzer",
    "MangoHudLogParser",
    "compare_measurements",
]
