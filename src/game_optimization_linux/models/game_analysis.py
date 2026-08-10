from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DetectionEvidence:
    source: str
    detail: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "detail": self.detail, "weight": self.weight}


@dataclass(frozen=True, slots=True)
class DetectedValue:
    value: str
    confidence: float
    source: str
    evidence: tuple[DetectionEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence": min(1.0, max(0.0, self.confidence)),
            "source": self.source,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    cpu: str = "Unknown"
    gpu: str = "Unknown"
    vram_gb: float | None = None
    ram_gb: float | None = None
    display_name: str = ""
    resolution_width: int | None = None
    resolution_height: int | None = None
    refresh_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu,
            "gpu": self.gpu,
            "vramGb": self.vram_gb,
            "ramGb": self.ram_gb,
            "displayName": self.display_name,
            "resolutionWidth": self.resolution_width,
            "resolutionHeight": self.resolution_height,
            "refreshRate": self.refresh_rate,
        }


@dataclass(frozen=True, slots=True)
class GameFingerprint:
    game_id: str
    app_id: str
    title: str
    provider: str
    game_root: str
    main_executable: str
    executable_directory: str
    runtime: DetectedValue
    architecture: DetectedValue
    engine: DetectedValue
    engine_version: str
    graphics_api: DetectedValue
    available_graphics_apis: tuple[DetectedValue, ...]
    category: DetectedValue
    manual_category_override: bool
    config_locations: tuple[str, ...]
    launcher: str
    system: SystemSnapshot
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gameId": self.game_id,
            "appId": self.app_id,
            "title": self.title,
            "provider": self.provider,
            "gameRoot": self.game_root,
            "mainExecutable": self.main_executable,
            "executableDirectory": self.executable_directory,
            "runtime": self.runtime.to_dict(),
            "architecture": self.architecture.to_dict(),
            "engine": self.engine.to_dict(),
            "engineVersion": self.engine_version,
            "graphicsApi": self.graphics_api.to_dict(),
            "availableGraphicsApis": [
                value.to_dict() for value in self.available_graphics_apis
            ],
            "category": self.category.to_dict(),
            "manualCategoryOverride": self.manual_category_override,
            "configLocations": list(self.config_locations),
            "launcher": self.launcher,
            "system": self.system.to_dict(),
            "analyzedAt": self.analyzed_at.astimezone(UTC).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class PerformanceMeasurement:
    source_path: str
    samples: int
    duration_seconds: float | None
    average_fps: float | None
    minimum_fps: float | None
    one_percent_low_fps: float | None
    average_frametime_ms: float | None
    p95_frametime_ms: float | None
    p99_frametime_ms: float | None
    cpu_usage_percent: float | None
    gpu_usage_percent: float | None
    ram_used_mb: float | None
    vram_used_mb: float | None
    gpu_temperature_c: float | None
    measured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    quality: str = "medium"
    limitations: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.samples > 0 and (
            self.average_fps is not None or self.average_frametime_ms is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourcePath": self.source_path,
            "samples": self.samples,
            "durationSeconds": self.duration_seconds,
            "averageFps": self.average_fps,
            "minimumFps": self.minimum_fps,
            "onePercentLowFps": self.one_percent_low_fps,
            "averageFrametimeMs": self.average_frametime_ms,
            "p95FrametimeMs": self.p95_frametime_ms,
            "p99FrametimeMs": self.p99_frametime_ms,
            "cpuUsagePercent": self.cpu_usage_percent,
            "gpuUsagePercent": self.gpu_usage_percent,
            "ramUsedMb": self.ram_used_mb,
            "vramUsedMb": self.vram_used_mb,
            "gpuTemperatureC": self.gpu_temperature_c,
            "measuredAt": self.measured_at.astimezone(UTC).isoformat(),
            "quality": self.quality,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PerformanceMeasurement:
        measured = datetime.fromisoformat(str(value.get("measuredAt") or datetime.now(UTC).isoformat()))
        if measured.tzinfo is None:
            measured = measured.replace(tzinfo=UTC)
        numeric = (
            "durationSeconds", "averageFps", "minimumFps", "onePercentLowFps",
            "averageFrametimeMs", "p95FrametimeMs", "p99FrametimeMs",
            "cpuUsagePercent", "gpuUsagePercent", "ramUsedMb", "vramUsedMb",
            "gpuTemperatureC",
        )
        parsed = {
            name: (float(value[name]) if value.get(name) is not None else None)
            for name in numeric
        }
        limitations = value.get("limitations")
        return cls(
            str(value.get("sourcePath") or ""),
            int(value.get("samples") or 0),
            parsed["durationSeconds"],
            parsed["averageFps"],
            parsed["minimumFps"],
            parsed["onePercentLowFps"],
            parsed["averageFrametimeMs"],
            parsed["p95FrametimeMs"],
            parsed["p99FrametimeMs"],
            parsed["cpuUsagePercent"],
            parsed["gpuUsagePercent"],
            parsed["ramUsedMb"],
            parsed["vramUsedMb"],
            parsed["gpuTemperatureC"],
            measured,
            str(value.get("quality") or "low"),
            tuple(str(item) for item in limitations) if isinstance(limitations, list) else (),
        )


@dataclass(frozen=True, slots=True)
class BottleneckAnalysis:
    conclusion: str
    confidence: float
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "confidence": min(1.0, max(0.0, self.confidence)),
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class OptimizationCandidate:
    id: str
    target: str
    mechanism: str
    source: str
    evidence: tuple[str, ...]
    current_value: str
    proposed_value: str
    expected_effect: str
    quality_impact: str
    risk: str
    reversible: bool
    requires_measurement: bool
    engine_support: str
    api_support: str
    files_to_modify: tuple[str, ...] = ()
    env_changes: Mapping[str, str] = field(default_factory=dict)
    automatically_selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "mechanism": self.mechanism,
            "source": self.source,
            "evidence": list(self.evidence),
            "currentValue": self.current_value,
            "proposedValue": self.proposed_value,
            "expectedEffect": self.expected_effect,
            "qualityImpact": self.quality_impact,
            "risk": self.risk,
            "reversible": self.reversible,
            "requiresMeasurement": self.requires_measurement,
            "engineSupport": self.engine_support,
            "apiSupport": self.api_support,
            "filesToModify": list(self.files_to_modify),
            "envChanges": dict(self.env_changes),
            "automaticallySelected": self.automatically_selected,
        }


@dataclass(frozen=True, slots=True)
class OptimizationAnalysis:
    fingerprint: GameFingerprint
    measurement: PerformanceMeasurement | None
    bottleneck: BottleneckAnalysis
    candidates: tuple[OptimizationCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "measurement": self.measurement.to_dict() if self.measurement else {},
            "baselineAvailable": bool(self.measurement and self.measurement.available),
            "bottleneck": self.bottleneck.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "noSafeRecommendations": not self.candidates,
        }


@dataclass(frozen=True, slots=True)
class PerformanceComparison:
    outcome: str
    evidence: tuple[str, ...]
    recommend_revert: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "evidence": list(self.evidence),
            "recommendRevert": self.recommend_revert,
        }


@dataclass(frozen=True, slots=True)
class BaselineSession:
    id: str
    app_id: str
    game_id: str
    status: str
    directory: Path
    config_path: Path
    log_directory: Path
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error: str = ""
    kind: str = "baseline"
    handshake_at: datetime | None = None
    runner_pid: int | None = None
    spawned_pid: int | None = None
    process_group: int | None = None
    runner_token: str = ""
    runner_completed_at: datetime | None = None
    observed_processes: tuple[str, ...] = ()
    lifecycle_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "appId": self.app_id,
            "gameId": self.game_id,
            "status": self.status,
            "directory": str(self.directory),
            "configPath": str(self.config_path),
            "logDirectory": str(self.log_directory),
            "createdAt": self.created_at.astimezone(UTC).isoformat(),
            "startedAt": (
                self.started_at.astimezone(UTC).isoformat()
                if self.started_at else ""
            ),
            "finishedAt": (
                self.finished_at.astimezone(UTC).isoformat()
                if self.finished_at else ""
            ),
            "exitCode": self.exit_code,
            "error": self.error,
            "kind": self.kind,
            "handshakeAt": (
                self.handshake_at.astimezone(UTC).isoformat()
                if self.handshake_at else ""
            ),
            "runnerPid": self.runner_pid,
            "spawnedPid": self.spawned_pid,
            "processGroup": self.process_group,
            "runnerCompletedAt": (
                self.runner_completed_at.astimezone(UTC).isoformat()
                if self.runner_completed_at else ""
            ),
            "observedProcesses": list(self.observed_processes),
            "lifecycleReason": self.lifecycle_reason,
        }


__all__ = [
    "BottleneckAnalysis",
    "BaselineSession",
    "DetectedValue",
    "DetectionEvidence",
    "GameFingerprint",
    "OptimizationAnalysis",
    "OptimizationCandidate",
    "PerformanceComparison",
    "PerformanceMeasurement",
    "SystemSnapshot",
]
