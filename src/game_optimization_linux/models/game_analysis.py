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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DetectionEvidence:
        return cls(
            str(value.get("source") or ""),
            str(value.get("detail") or ""),
            float(value.get("weight") or 0.0),
        )


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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DetectedValue:
        evidence = value.get("evidence")
        return cls(
            str(value.get("value") or "Unknown"),
            float(value.get("confidence") or 0.0),
            str(value.get("source") or "not detected"),
            tuple(
                DetectionEvidence.from_dict(item)
                for item in evidence
                if isinstance(item, Mapping)
            ) if isinstance(evidence, list) else (),
        )


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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SystemSnapshot:
        def optional_float(name: str) -> float | None:
            return float(value[name]) if value.get(name) is not None else None

        def optional_int(name: str) -> int | None:
            return int(value[name]) if value.get(name) is not None else None

        return cls(
            str(value.get("cpu") or "Unknown"),
            str(value.get("gpu") or "Unknown"),
            optional_float("vramGb"),
            optional_float("ramGb"),
            str(value.get("displayName") or ""),
            optional_int("resolutionWidth"),
            optional_int("resolutionHeight"),
            optional_float("refreshRate"),
        )


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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GameFingerprint:
        analyzed = datetime.fromisoformat(
            str(value.get("analyzedAt") or datetime.now(UTC).isoformat())
        )
        if analyzed.tzinfo is None:
            analyzed = analyzed.replace(tzinfo=UTC)
        available = value.get("availableGraphicsApis")
        locations = value.get("configLocations")
        return cls(
            str(value.get("gameId") or ""),
            str(value.get("appId") or ""),
            str(value.get("title") or ""),
            str(value.get("provider") or ""),
            str(value.get("gameRoot") or ""),
            str(value.get("mainExecutable") or ""),
            str(value.get("executableDirectory") or ""),
            DetectedValue.from_dict(value.get("runtime", {})),
            DetectedValue.from_dict(value.get("architecture", {})),
            DetectedValue.from_dict(value.get("engine", {})),
            str(value.get("engineVersion") or ""),
            DetectedValue.from_dict(value.get("graphicsApi", {})),
            tuple(
                DetectedValue.from_dict(item)
                for item in available
                if isinstance(item, Mapping)
            ) if isinstance(available, list) else (),
            DetectedValue.from_dict(value.get("category", {})),
            bool(value.get("manualCategoryOverride", False)),
            tuple(str(item) for item in locations)
            if isinstance(locations, list) else (),
            str(value.get("launcher") or ""),
            SystemSnapshot.from_dict(value.get("system", {})),
            analyzed,
        )


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
    total_samples: int = 0
    excluded_samples: int = 0
    selected_duration_seconds: float | None = None
    representative: bool = True
    selection_reasons: tuple[str, ...] = ()
    median_fps: float | None = None
    p10_fps: float | None = None
    p90_fps: float | None = None
    p95_fps: float | None = None
    p99_fps: float | None = None
    median_frametime_ms: float | None = None

    @property
    def available(self) -> bool:
        return self.representative and self.quality != "low" and self.samples > 0 and (
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
            "totalSamples": self.total_samples or self.samples,
            "excludedSamples": self.excluded_samples,
            "usedPercentage": (
                self.samples / (self.total_samples or self.samples) * 100
                if (self.total_samples or self.samples) else 0.0
            ),
            "selectedDurationSeconds": self.selected_duration_seconds,
            "representative": self.representative,
            "selectionReasons": list(self.selection_reasons),
            "medianFps": self.median_fps,
            "p10Fps": self.p10_fps,
            "p90Fps": self.p90_fps,
            "p95Fps": self.p95_fps,
            "p99Fps": self.p99_fps,
            "medianFrametimeMs": self.median_frametime_ms,
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
            int(value.get("totalSamples") or value.get("samples") or 0),
            int(value.get("excludedSamples") or 0),
            (
                float(value["selectedDurationSeconds"])
                if value.get("selectedDurationSeconds") is not None
                else parsed["durationSeconds"]
            ),
            bool(value.get("representative", True)),
            tuple(
                str(item) for item in value.get("selectionReasons", ())
                if isinstance(item, str)
            ) if isinstance(value.get("selectionReasons", ()), list) else (),
            (
                float(value["medianFps"])
                if value.get("medianFps") is not None else None
            ),
            float(value["p10Fps"]) if value.get("p10Fps") is not None else None,
            float(value["p90Fps"]) if value.get("p90Fps") is not None else None,
            float(value["p95Fps"]) if value.get("p95Fps") is not None else None,
            float(value["p99Fps"]) if value.get("p99Fps") is not None else None,
            (
                float(value["medianFrametimeMs"])
                if value.get("medianFrametimeMs") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class FrameRateAnalysis:
    state: str
    estimated_ceiling_fps: float | None
    confidence: float
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]

    @classmethod
    def unknown(cls, reason: str = "A representative baseline is required") -> FrameRateAnalysis:
        return cls("unknown", None, 0.0, (), (reason,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "estimatedCeilingFps": self.estimated_ceiling_fps,
            "confidence": min(1.0, max(0.0, self.confidence)),
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FrameRateAnalysis:
        return cls(
            str(value.get("state") or "unknown"),
            (
                float(value["estimatedCeilingFps"])
                if value.get("estimatedCeilingFps") is not None else None
            ),
            float(value.get("confidence") or 0.0),
            tuple(str(item) for item in value.get("evidence", ())),
            tuple(str(item) for item in value.get("limitations", ())),
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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BottleneckAnalysis:
        return cls(
            str(value.get("conclusion") or "insufficient_data"),
            float(value.get("confidence") or 0.0),
            tuple(str(item) for item in value.get("evidence", ())),
            tuple(str(item) for item in value.get("limitations", ())),
        )


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
    setting_id: str = ""
    setting_label: str = ""
    setting_category: str = ""
    performance_impact: str = "unknown"
    confidence_label: str = "unknown"
    config_sha256: str = ""
    config_section: str = ""
    config_key: str = ""
    config_adapter: str = ""

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
            "settingId": self.setting_id,
            "settingLabel": self.setting_label,
            "settingCategory": self.setting_category,
            "performanceImpact": self.performance_impact,
            "confidenceLabel": self.confidence_label,
            "configSha256": self.config_sha256,
            "configSection": self.config_section,
            "configKey": self.config_key,
            "configAdapter": self.config_adapter,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OptimizationCandidate:
        environment = value.get("envChanges")
        files = value.get("filesToModify")
        return cls(
            str(value.get("id") or ""),
            str(value.get("target") or ""),
            str(value.get("mechanism") or ""),
            str(value.get("source") or ""),
            tuple(str(item) for item in value.get("evidence", ())),
            str(value.get("currentValue") or ""),
            str(value.get("proposedValue") or ""),
            str(value.get("expectedEffect") or ""),
            str(value.get("qualityImpact") or "unknown"),
            str(value.get("risk") or "unknown"),
            bool(value.get("reversible", False)),
            bool(value.get("requiresMeasurement", False)),
            str(value.get("engineSupport") or ""),
            str(value.get("apiSupport") or ""),
            tuple(str(item) for item in files) if isinstance(files, list) else (),
            {
                str(key): str(item)
                for key, item in environment.items()
            } if isinstance(environment, Mapping) else {},
            bool(value.get("automaticallySelected", False)),
            str(value.get("settingId") or ""),
            str(value.get("settingLabel") or ""),
            str(value.get("settingCategory") or ""),
            str(value.get("performanceImpact") or "unknown"),
            str(value.get("confidenceLabel") or "unknown"),
            str(value.get("configSha256") or ""),
            str(value.get("configSection") or ""),
            str(value.get("configKey") or ""),
            str(value.get("configAdapter") or ""),
        )


@dataclass(frozen=True, slots=True)
class DetectedGameSetting:
    id: str
    label: str
    category: str
    file: str
    section: str
    key: str
    value: str
    adapter: str
    modifiable: bool
    instance_id: str = ""
    available_values: tuple[str, ...] = ()
    alternative_values: tuple[str, ...] = ()
    suggested_value: str = ""
    performance_impact: str = "unknown"
    quality_impact: str = "unknown"
    confidence_label: str = "unknown"
    automatically_recommended: bool = False
    automatic_reason: str = ""
    config_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "file": self.file,
            "section": self.section,
            "key": self.key,
            "value": self.value,
            "adapter": self.adapter,
            "modifiable": self.modifiable,
            "instanceId": self.instance_id,
            "availableValues": list(self.available_values),
            "alternativeValues": list(self.alternative_values),
            "suggestedValue": self.suggested_value,
            "performanceImpact": self.performance_impact,
            "qualityImpact": self.quality_impact,
            "confidenceLabel": self.confidence_label,
            "automaticallyRecommended": self.automatically_recommended,
            "automaticReason": self.automatic_reason,
            "configSha256": self.config_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DetectedGameSetting:
        return cls(
            str(value.get("id") or ""),
            str(value.get("label") or value.get("key") or ""),
            str(value.get("category") or ""),
            str(value.get("file") or ""),
            str(value.get("section") or ""),
            str(value.get("key") or ""),
            str(value.get("value") or ""),
            str(value.get("adapter") or ""),
            bool(value.get("modifiable", False)),
            str(value.get("instanceId") or ""),
            tuple(str(item) for item in value.get("availableValues", ())),
            tuple(str(item) for item in value.get("alternativeValues", ())),
            str(value.get("suggestedValue") or ""),
            str(value.get("performanceImpact") or "unknown"),
            str(value.get("qualityImpact") or "unknown"),
            str(value.get("confidenceLabel") or "unknown"),
            bool(value.get("automaticallyRecommended", False)),
            str(value.get("automaticReason") or ""),
            str(value.get("configSha256") or ""),
        )


@dataclass(frozen=True, slots=True)
class GameSettingsAnalysis:
    status: str
    engine: str
    config_files: tuple[str, ...]
    detected: tuple[DetectedGameSetting, ...]
    message: str
    recommendation_state: str = "not_evaluated"
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def unavailable(cls, message: str = "Settings analysis has not run") -> GameSettingsAnalysis:
        return cls("unavailable", "", (), (), message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "engine": self.engine,
            "configFiles": list(self.config_files),
            "detected": [item.to_dict() for item in self.detected],
            "message": self.message,
            "recommendationState": self.recommendation_state,
            "analyzedAt": self.analyzed_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GameSettingsAnalysis:
        detected = value.get("detected")
        analyzed = datetime.fromisoformat(
            str(value.get("analyzedAt") or datetime.now(UTC).isoformat())
        )
        if analyzed.tzinfo is None:
            analyzed = analyzed.replace(tzinfo=UTC)
        return cls(
            str(value.get("status") or "unavailable"),
            str(value.get("engine") or ""),
            tuple(str(item) for item in value.get("configFiles", ())),
            tuple(
                DetectedGameSetting.from_dict(item)
                for item in detected
                if isinstance(item, Mapping)
            ) if isinstance(detected, list) else (),
            str(value.get("message") or "Settings analysis is unavailable"),
            str(value.get("recommendationState") or "not_evaluated"),
            analyzed,
        )


@dataclass(frozen=True, slots=True)
class OptimizationAnalysis:
    fingerprint: GameFingerprint
    measurement: PerformanceMeasurement | None
    bottleneck: BottleneckAnalysis
    candidates: tuple[OptimizationCandidate, ...]
    frame_rate: FrameRateAnalysis = field(default_factory=FrameRateAnalysis.unknown)
    settings: GameSettingsAnalysis = field(default_factory=GameSettingsAnalysis.unavailable)
    baseline_stale: bool = False
    stale_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "measurement": self.measurement.to_dict() if self.measurement else {},
            "baselineAvailable": bool(self.measurement and self.measurement.available),
            "bottleneck": self.bottleneck.to_dict(),
            "frameRate": self.frame_rate.to_dict(),
            "settingsAnalysis": self.settings.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "noSafeRecommendations": not self.candidates,
            "baselineStale": self.baseline_stale,
            "staleReasons": list(self.stale_reasons),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OptimizationAnalysis:
        measurement = value.get("measurement")
        candidates = value.get("candidates")
        return cls(
            GameFingerprint.from_dict(value.get("fingerprint", {})),
            (
                PerformanceMeasurement.from_dict(measurement)
                if isinstance(measurement, Mapping) and measurement else None
            ),
            BottleneckAnalysis.from_dict(value.get("bottleneck", {})),
            tuple(
                OptimizationCandidate.from_dict(item)
                for item in candidates
                if isinstance(item, Mapping)
            ) if isinstance(candidates, list) else (),
            FrameRateAnalysis.from_dict(value.get("frameRate", {})),
            GameSettingsAnalysis.from_dict(value.get("settingsAnalysis", {})),
            bool(value.get("baselineStale", False)),
            tuple(str(item) for item in value.get("staleReasons", ())),
        )


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
    last_heartbeat_at: datetime | None = None
    runner_invocation_count: int = 0
    runner_rejection: str = ""
    steam_launch_result: str = ""
    expected_runner_path: str = ""
    expected_runner_hash: str = ""
    handshake_timeout_seconds: int = 120

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
            "lastHeartbeatAt": (
                self.last_heartbeat_at.astimezone(UTC).isoformat()
                if self.last_heartbeat_at else ""
            ),
            "runnerInvocationCount": self.runner_invocation_count,
            "runnerRejection": self.runner_rejection,
            "steamLaunchResult": self.steam_launch_result,
            "expectedRunnerPath": self.expected_runner_path,
            "expectedRunnerHash": self.expected_runner_hash,
            "handshakeTimeoutSeconds": self.handshake_timeout_seconds,
        }


__all__ = [
    "BottleneckAnalysis",
    "BaselineSession",
    "DetectedValue",
    "DetectionEvidence",
    "GameFingerprint",
    "GameSettingsAnalysis",
    "DetectedGameSetting",
    "FrameRateAnalysis",
    "OptimizationAnalysis",
    "OptimizationCandidate",
    "PerformanceComparison",
    "PerformanceMeasurement",
    "SystemSnapshot",
]
