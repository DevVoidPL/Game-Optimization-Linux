"""Domain values used by the local game narrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Any, Mapping

from .mangohud import validate_game_key


NARRATOR_SETTINGS_SCHEMA_VERSION = 1
NARRATOR_COMPONENT_SCHEMA_VERSION = 1


class NarratorSourceMode(StrEnum):
    AUTO = "auto"
    OCR = "ocr"
    ADAPTER = "adapter"


class CaptureSourceType(StrEnum):
    MONITOR = "monitor"
    WINDOW = "window"


class NarratorComponentKind(StrEnum):
    CAPTURE = "capture"
    OCR = "ocr"
    TRANSLATION = "translation"
    TTS = "tts"
    AUDIO = "audio"


class NarratorComponentState(StrEnum):
    AVAILABLE = "available"
    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    UPDATE_AVAILABLE = "update_available"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


class NarratorSessionStatus(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    SELECTING_SOURCE = "selecting_source"
    LISTENING = "listening"
    OCR = "ocr"
    TRANSLATING = "translating"
    SPEAKING = "speaking"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class CaptureState(StrEnum):
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"
    SELECTING_SOURCE = "selecting_source"
    STARTING = "starting"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"
    SOURCE_LOST = "source_lost"
    RESTORE_FAILED = "restore_failed"
    STOPPED = "stopped"
    ERROR = "error"


def _finite_number(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return normalized


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(value).strip() not in {str(normalized), f"{normalized}.0"}:
        raise ValueError(f"{name} must be an integer")
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return normalized


@dataclass(frozen=True, slots=True)
class NormalizedRect:
    x: float = 0.05
    y: float = 0.62
    width: float = 0.90
    height: float = 0.30

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            object.__setattr__(
                self,
                name,
                _finite_number(getattr(self, name), name, 0.0, 1.0),
            )
        if self.width <= 0 or self.height <= 0:
            raise ValueError("subtitle region width and height must be positive")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("subtitle region must fit inside the captured frame")

    @classmethod
    def from_dict(cls, values: Mapping[str, Any] | None) -> NormalizedRect:
        data = dict(values or {})
        default = cls()
        return cls(
            x=data.get("x", default.x),
            y=data.get("y", default.y),
            width=data.get("width", default.width),
            height=data.get("height", default.height),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class NarratorGameSettings:
    game_key: str
    schema_version: int = NARRATOR_SETTINGS_SCHEMA_VERSION
    enabled: bool = False
    source_mode: NarratorSourceMode = NarratorSourceMode.AUTO
    capture_source: CaptureSourceType = CaptureSourceType.WINDOW
    subtitle_adapter_id: str = ""
    ocr_provider_id: str = ""
    translation_provider_id: str = ""
    translation_profile_id: str = ""
    tts_provider_id: str = ""
    voice_id: str = ""
    volume: float = 0.85
    speech_rate: float = 1.0
    subtitle_region: NormalizedRect = field(default_factory=NormalizedRect)
    capture_sampling_hz: float = 6.0
    visual_change_threshold: float = 0.08
    stabilization_ms: int = 240
    ocr_min_confidence: float = 0.62
    duplicate_cooldown_ms: int = 4500
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "game_key", validate_game_key(self.game_key))
        if self.schema_version != NARRATOR_SETTINGS_SCHEMA_VERSION:
            raise ValueError("unsupported narrator settings schema version")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        object.__setattr__(self, "source_mode", NarratorSourceMode(self.source_mode))
        object.__setattr__(self, "capture_source", CaptureSourceType(self.capture_source))
        for name in (
            "subtitle_adapter_id",
            "ocr_provider_id",
            "translation_provider_id",
            "translation_profile_id",
            "tts_provider_id",
            "voice_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or any(char in value for char in "\r\n\0"):
                raise ValueError(f"{name} must be a single-line string")
        object.__setattr__(
            self, "volume", _finite_number(self.volume, "volume", 0.0, 1.0)
        )
        object.__setattr__(
            self,
            "speech_rate",
            _finite_number(self.speech_rate, "speech_rate", 0.5, 2.0),
        )
        object.__setattr__(
            self,
            "capture_sampling_hz",
            _finite_number(self.capture_sampling_hz, "capture_sampling_hz", 1.0, 10.0),
        )
        object.__setattr__(
            self,
            "visual_change_threshold",
            _finite_number(
                self.visual_change_threshold,
                "visual_change_threshold",
                0.001,
                1.0,
            ),
        )
        object.__setattr__(
            self,
            "stabilization_ms",
            _integer(self.stabilization_ms, "stabilization_ms", 50, 3000),
        )
        object.__setattr__(
            self,
            "ocr_min_confidence",
            _finite_number(
                self.ocr_min_confidence,
                "ocr_min_confidence",
                0.0,
                1.0,
            ),
        )
        object.__setattr__(
            self,
            "duplicate_cooldown_ms",
            _integer(
                self.duplicate_cooldown_ms,
                "duplicate_cooldown_ms",
                250,
                60000,
            ),
        )

    @classmethod
    def default(cls, game_key: object) -> NarratorGameSettings:
        return cls(game_key=validate_game_key(game_key))

    @classmethod
    def from_dict(
        cls,
        values: Mapping[str, Any],
        *,
        expected_game_key: object,
    ) -> NarratorGameSettings:
        expected = validate_game_key(expected_game_key)
        stored_key = validate_game_key(values.get("game_key", expected))
        if stored_key != expected:
            raise ValueError("narrator settings belong to another game")
        updated_at = values.get("updated_at")
        if isinstance(updated_at, str) and updated_at.strip():
            try:
                parsed_updated_at = datetime.fromisoformat(updated_at)
            except ValueError as error:
                raise ValueError("updated_at must be an ISO-8601 timestamp") from error
        else:
            parsed_updated_at = datetime.now(UTC)
        return cls(
            game_key=stored_key,
            schema_version=_integer(
                values.get("schema_version", 1), "schema_version", 1, 1
            ),
            enabled=values.get("enabled", False),
            source_mode=NarratorSourceMode(values.get("source_mode", "auto")),
            capture_source=CaptureSourceType(
                values.get("capture_source", "window")
            ),
            subtitle_adapter_id=str(values.get("subtitle_adapter_id", "")),
            ocr_provider_id=str(values.get("ocr_provider_id", "")),
            translation_provider_id=str(
                values.get("translation_provider_id", "")
            ),
            translation_profile_id=str(values.get("translation_profile_id", "")),
            tts_provider_id=str(values.get("tts_provider_id", "")),
            voice_id=str(values.get("voice_id", "")),
            volume=values.get("volume", 0.85),
            speech_rate=values.get("speech_rate", 1.0),
            subtitle_region=NormalizedRect.from_dict(
                values.get("subtitle_region")
                if isinstance(values.get("subtitle_region"), Mapping)
                else None
            ),
            capture_sampling_hz=values.get("capture_sampling_hz", 6.0),
            visual_change_threshold=values.get("visual_change_threshold", 0.08),
            stabilization_ms=values.get("stabilization_ms", 240),
            ocr_min_confidence=values.get("ocr_min_confidence", 0.62),
            duplicate_cooldown_ms=values.get("duplicate_cooldown_ms", 4500),
            updated_at=parsed_updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "game_key": self.game_key,
            "enabled": self.enabled,
            "source_mode": self.source_mode.value,
            "capture_source": self.capture_source.value,
            "subtitle_adapter_id": self.subtitle_adapter_id,
            "ocr_provider_id": self.ocr_provider_id,
            "translation_provider_id": self.translation_provider_id,
            "translation_profile_id": self.translation_profile_id,
            "tts_provider_id": self.tts_provider_id,
            "voice_id": self.voice_id,
            "volume": self.volume,
            "speech_rate": self.speech_rate,
            "subtitle_region": self.subtitle_region.to_dict(),
            "capture_sampling_hz": self.capture_sampling_hz,
            "visual_change_threshold": self.visual_change_threshold,
            "stabilization_ms": self.stabilization_ms,
            "ocr_min_confidence": self.ocr_min_confidence,
            "duplicate_cooldown_ms": self.duplicate_cooldown_ms,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class NarratorComponent:
    component_id: str
    kind: NarratorComponentKind
    name: str
    state: NarratorComponentState
    version: str = ""
    installed_size_bytes: int | None = None
    download_size_bytes: int | None = None
    license_id: str = ""
    runtime_license_id: str = ""
    artifact_license_id: str = ""
    attribution: str = ""
    message: str = ""
    managed: bool = False
    update_version: str = ""

    def __post_init__(self) -> None:
        if not self.component_id.strip() or not self.name.strip():
            raise ValueError("narrator component id and name are required")
        object.__setattr__(self, "kind", NarratorComponentKind(self.kind))
        object.__setattr__(self, "state", NarratorComponentState(self.state))
        for name in ("installed_size_bytes", "download_size_bytes"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "componentId": self.component_id,
            "kind": self.kind.value,
            "name": self.name,
            "state": self.state.value,
            "version": self.version,
            "installedSizeBytes": self.installed_size_bytes,
            "downloadSizeBytes": self.download_size_bytes,
            "licenseId": self.license_id,
            "runtimeLicenseId": self.runtime_license_id,
            "artifactLicenseId": self.artifact_license_id,
            "attribution": self.attribution,
            "message": self.message,
            "managed": self.managed,
            "updateVersion": self.update_version,
        }


@dataclass(frozen=True, slots=True)
class CaptureFrame:
    session_id: str
    generation: int
    timestamp_monotonic: float
    width: int
    height: int
    stride: int
    pixel_format: str
    pixels: bytes
    source_id: str = ""

    def __post_init__(self) -> None:
        if not self.session_id or self.generation < 0:
            raise ValueError("capture frame needs a session and generation")
        if self.width <= 0 or self.height <= 0 or self.stride <= 0:
            raise ValueError("capture frame dimensions must be positive")
        if len(self.pixels) < self.stride * self.height:
            raise ValueError("capture frame buffer is shorter than its dimensions")


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    confidence: float | None = None
    provider_id: str = ""
    elapsed_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class TranslationResult:
    source_text: str
    translated_text: str
    source_language: str = "en"
    target_language: str = "pl"
    provider_id: str = ""
    elapsed_ms: float = 0.0
    cached: bool = False


@dataclass(frozen=True, slots=True)
class PcmAudio:
    samples: bytes
    sample_rate: int
    channels: int
    sample_format: str = "s16le"
    provider_id: str = ""
    elapsed_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.channels <= 0:
            raise ValueError("PCM audio format must be valid")


@dataclass(frozen=True, slots=True)
class NarratorSessionSnapshot:
    session_id: str = ""
    game_key: str = ""
    status: NarratorSessionStatus = NarratorSessionStatus.IDLE
    message: str = ""
    last_detected_text: str = ""
    last_translation: str = ""
    last_spoken_text: str = ""
    capture_ms: float | None = None
    ocr_ms: float | None = None
    translation_ms: float | None = None
    tts_ms: float | None = None
    audio_start_ms: float | None = None
    total_capture_to_text_ms: float | None = None
    total_capture_to_audio_start_ms: float | None = None
    capture_width: int = 0
    capture_height: int = 0
    capture_state: str = "stopped"
    ocr_status: str = "component_missing"
    translation_status: str = "component_missing"
    tts_status: str = "component_missing"
    audio_status: str = "unavailable"
    ocr_confidence: float | None = None
    last_raw_ocr_text: str = ""
    last_filtered_ocr_text: str = ""
    last_ocr_rejection_reason: str = ""
    last_accepted_ocr_text: str = ""
    ocr_rejection_counts: Mapping[str, int] = field(default_factory=dict)
    last_detected_at_monotonic: float | None = None
    dropped_frames: int = 0
    ocr_execution_count: int = 0
    generation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "gameKey": self.game_key,
            "status": self.status.value,
            "message": self.message,
            "lastDetectedText": self.last_detected_text,
            "lastTranslation": self.last_translation,
            "lastSpokenText": self.last_spoken_text,
            "captureMs": self.capture_ms,
            "ocrMs": self.ocr_ms,
            "translationMs": self.translation_ms,
            "ttsMs": self.tts_ms,
            "audioStartMs": self.audio_start_ms,
            "totalCaptureToTextMs": self.total_capture_to_text_ms,
            "totalCaptureToAudioStartMs": self.total_capture_to_audio_start_ms,
            "captureWidth": self.capture_width,
            "captureHeight": self.capture_height,
            "captureState": self.capture_state,
            "ocrStatus": self.ocr_status,
            "translationStatus": self.translation_status,
            "ttsStatus": self.tts_status,
            "audioStatus": self.audio_status,
            "ocrConfidence": self.ocr_confidence,
            "lastRawOcrText": self.last_raw_ocr_text,
            "lastFilteredOcrText": self.last_filtered_ocr_text,
            "lastOcrRejectionReason": self.last_ocr_rejection_reason,
            "lastAcceptedOcrText": self.last_accepted_ocr_text,
            "ocrRejectionCounts": dict(self.ocr_rejection_counts),
            "lastDetectedAtMonotonic": self.last_detected_at_monotonic,
            "droppedFrames": self.dropped_frames,
            "ocrExecutionCount": self.ocr_execution_count,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class NarratorEvent:
    session_id: str
    game_key: str
    generation: int
    status: NarratorSessionStatus
    message: str = ""
    detected_text: str = ""
    translated_text: str = ""
    spoken_text: str = ""
    timings: Mapping[str, float] = field(default_factory=dict)


__all__ = [
    "CaptureFrame",
    "CaptureSourceType",
    "CaptureState",
    "NARRATOR_COMPONENT_SCHEMA_VERSION",
    "NARRATOR_SETTINGS_SCHEMA_VERSION",
    "NarratorComponent",
    "NarratorComponentKind",
    "NarratorComponentState",
    "NarratorEvent",
    "NarratorGameSettings",
    "NarratorSessionSnapshot",
    "NarratorSessionStatus",
    "NarratorSourceMode",
    "NormalizedRect",
    "OcrResult",
    "PcmAudio",
    "TranslationResult",
]
