"""Domain values used by the local game narrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
import time
from typing import Any, Mapping

from .mangohud import validate_game_key


NARRATOR_SETTINGS_SCHEMA_VERSION = 1
NARRATOR_COMPONENT_SCHEMA_VERSION = 1


class NarratorSourceMode(StrEnum):
    AUTO = "auto"
    OCR = "ocr"
    ADAPTER = "adapter"


class NarratorSubtitleLanguageMode(StrEnum):
    ENGLISH_TO_POLISH = "english_to_polish"
    POLISH = "polish"


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


def _optional_finite_number(
    value: object, name: str, minimum: float, maximum: float
) -> float | None:
    """Validate an optional override where None means "use the default"."""

    if value is None:
        return None
    return _finite_number(value, name, minimum, maximum)


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
    subtitle_language_mode: NarratorSubtitleLanguageMode = (
        NarratorSubtitleLanguageMode.ENGLISH_TO_POLISH
    )
    subtitle_adapter_id: str = ""
    ocr_provider_id: str = ""
    translation_provider_id: str = ""
    translation_profile_id: str = ""
    tts_provider_id: str = ""
    voice_id: str = ""
    volume: float = 0.85
    speech_rate: float = 1.0
    # Advanced Piper inference overrides. None means "use the voice's own
    # configured value", which is the default for every installed voice.
    # Field names follow piper-tts 1.7.0: noise_scale and noise_w_scale.
    noise_scale: float | None = None
    noise_w_scale: float | None = None
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
        object.__setattr__(
            self,
            "subtitle_language_mode",
            NarratorSubtitleLanguageMode(self.subtitle_language_mode),
        )
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
            "noise_scale",
            _optional_finite_number(self.noise_scale, "noise_scale", 0.0, 2.0),
        )
        object.__setattr__(
            self,
            "noise_w_scale",
            _optional_finite_number(
                self.noise_w_scale, "noise_w_scale", 0.0, 2.0
            ),
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
            subtitle_language_mode=NarratorSubtitleLanguageMode(
                values.get("subtitle_language_mode", "english_to_polish")
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
            noise_scale=values.get("noise_scale"),
            noise_w_scale=values.get("noise_w_scale"),
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
            "subtitle_language_mode": self.subtitle_language_mode.value,
            "subtitle_adapter_id": self.subtitle_adapter_id,
            "ocr_provider_id": self.ocr_provider_id,
            "translation_provider_id": self.translation_provider_id,
            "translation_profile_id": self.translation_profile_id,
            "tts_provider_id": self.tts_provider_id,
            "voice_id": self.voice_id,
            "volume": self.volume,
            "speech_rate": self.speech_rate,
            "noise_scale": self.noise_scale,
            "noise_w_scale": self.noise_w_scale,
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
    preprocessing_ms: float = 0.0
    image_preprocessing_ms: float = 0.0
    png_encoding_ms: float = 0.0
    backend: str = ""
    recognition_ms: float | None = None
    worker_execution_ms: float | None = None
    worker_decode_ms: float | None = None
    worker_roundtrip_ms: float | None = None
    worker_lock_wait_ms: float | None = None
    client_overhead_ms: float | None = None
    worker_startup_ms: float | None = None
    worker_restarted: bool = False
    fallback_reason: str = ""
    debug_capture_path: str = ""
    raw_text: str = ""
    filtered_text: str = ""
    raw_confidence: float | None = None
    token_count: int = 0
    included_token_count: int = 0
    line_count: int = 0
    dropped_token_count: int = 0
    minimum_token_confidence: float | None = None
    geometry_coherent: bool = False
    clean_short_phrase_evidence: bool = False
    filter_summary: str = ""


@dataclass(frozen=True, slots=True)
class OcrDecisionObservation:
    """One bounded, in-memory explanation of an OCR pipeline decision."""

    observation_id: int
    observed_at_monotonic: float
    raw_text: str
    filtered_text: str
    normalized_text: str
    confidence: float | None
    decision: str
    rejection_reason: str = ""
    candidate_text: str = ""
    candidate_observation_count: int = 0
    candidate_required_observations: int = 2
    candidate_similarity: float | None = None
    candidate_match_kind: str = ""
    candidate_replaced: bool = False
    accepted_text: str = ""
    accepted: bool = False
    tts_submitted: bool = False
    roi_width: int = 0
    roi_height: int = 0
    backend: str = ""
    recognition_ms: float | None = None
    token_count: int = 0
    included_token_count: int = 0
    line_count: int = 0
    dropped_token_count: int = 0
    minimum_token_confidence: float | None = None
    geometry_coherent: bool = False
    clean_short_phrase_evidence: bool = False
    visual_change_decision: str = ""
    filter_summary: str = ""

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.monotonic() if now is None else float(now)
        return {
            "observationId": self.observation_id,
            "observedAtMonotonic": self.observed_at_monotonic,
            "ageSeconds": max(0.0, current - self.observed_at_monotonic),
            "rawText": self.raw_text,
            "filteredText": self.filtered_text,
            "normalizedText": self.normalized_text,
            "confidence": self.confidence,
            "decision": self.decision,
            "rejectionReason": self.rejection_reason,
            "candidateText": self.candidate_text,
            "candidateObservationCount": self.candidate_observation_count,
            "candidateRequiredObservations": self.candidate_required_observations,
            "candidateSimilarity": self.candidate_similarity,
            "candidateMatchKind": self.candidate_match_kind,
            "candidateReplaced": self.candidate_replaced,
            "acceptedText": self.accepted_text,
            "accepted": self.accepted,
            "ttsSubmitted": self.tts_submitted,
            "roiWidth": self.roi_width,
            "roiHeight": self.roi_height,
            "backend": self.backend,
            "recognitionMs": self.recognition_ms,
            "tokenCount": self.token_count,
            "includedTokenCount": self.included_token_count,
            "lineCount": self.line_count,
            "droppedTokenCount": self.dropped_token_count,
            "minimumTokenConfidence": self.minimum_token_confidence,
            "geometryCoherent": self.geometry_coherent,
            "cleanShortPhraseEvidence": self.clean_short_phrase_evidence,
            "visualChangeDecision": self.visual_change_decision,
            "filterSummary": self.filter_summary,
        }


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
    queue_wait_ms: float = 0.0
    worker_roundtrip_ms: float | None = None
    inference_ms: float | None = None
    serialization_ms: float | None = None
    worker_startup_ms: float | None = None
    worker_reused: bool = True
    audio_duration_ms: float | None = None

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
    frame_acquisition_ms: float | None = None
    capture_ms: float | None = None
    ocr_preprocessing_ms: float | None = None
    ocr_image_preprocessing_ms: float | None = None
    ocr_png_encoding_ms: float | None = None
    ocr_ms: float | None = None
    ocr_backend: str = ""
    ocr_recognition_ms: float | None = None
    ocr_worker_execution_ms: float | None = None
    ocr_worker_decode_ms: float | None = None
    ocr_worker_roundtrip_ms: float | None = None
    ocr_worker_lock_wait_ms: float | None = None
    ocr_client_overhead_ms: float | None = None
    ocr_worker_startup_ms: float | None = None
    ocr_worker_restart_count: int = 0
    ocr_cli_fallback_count: int = 0
    ocr_fallback_reason: str = ""
    ocr_debug_capture_path: str = ""
    stabilization_dedup_ms: float | None = None
    translation_ms: float | None = None
    tts_ms: float | None = None
    tts_queue_wait_ms: float | None = None
    tts_worker_roundtrip_ms: float | None = None
    tts_inference_ms: float | None = None
    tts_serialization_ms: float | None = None
    tts_worker_startup_ms: float | None = None
    tts_worker_reused: bool | None = None
    tts_audio_duration_ms: float | None = None
    audio_start_ms: float | None = None
    accepted_to_audio_start_ms: float | None = None
    confirming_frame_to_audio_start_ms: float | None = None
    first_visible_frame_to_audio_start_ms: float | None = None
    total_capture_to_text_ms: float | None = None
    total_capture_to_audio_start_ms: float | None = None
    first_visible_frame_at_monotonic: float | None = None
    confirming_frame_at_monotonic: float | None = None
    accepted_at_monotonic: float | None = None
    tts_started_at_monotonic: float | None = None
    tts_finished_at_monotonic: float | None = None
    playback_started_at_monotonic: float | None = None
    capture_width: int = 0
    capture_height: int = 0
    ocr_roi_width: int = 0
    ocr_roi_height: int = 0
    last_visual_change_decision: str = ""
    last_visual_change_score: float | None = None
    capture_state: str = "stopped"
    # Capture transport diagnostics. capture_frames_received separates "no
    # subtitles were recognised" from "no frames ever arrived".
    capture_format: str = ""
    capture_dmabuf: bool = False
    # Which pipeline variants were attempted and which were retired, so a
    # silent fallback becomes visible.
    capture_variants_tried: str = ""
    capture_variants_failed: str = ""
    # Per-session loss funnel, keyed by the pipeline's own decision vocabulary.
    # Bounded: one integer per known decision name.
    narration_funnel: Mapping[str, int] = field(default_factory=dict)
    # Per-session loss funnel, keyed by the pipeline's own decision vocabulary.
    # Bounded: one integer per known decision name.
    narration_funnel: Mapping[str, int] = field(default_factory=dict)
    capture_frames_received: int = 0
    capture_stream_errors: int = 0
    capture_restarts: int = 0
    ocr_status: str = "component_missing"
    translation_status: str = "component_missing"
    tts_status: str = "component_missing"
    audio_status: str = "unavailable"
    ocr_confidence: float | None = None
    last_raw_ocr_text: str = ""
    last_filtered_ocr_text: str = ""
    last_normalized_ocr_text: str = ""
    last_ocr_rejection_reason: str = ""
    last_ocr_observation_credible: bool = False
    last_ocr_gate_decision: str = ""
    ocr_candidate_text: str = ""
    ocr_candidate_observation_count: int = 0
    ocr_candidate_required_observations: int = 2
    ocr_candidate_similarity: float | None = None
    ocr_candidate_match_kind: str = ""
    last_accepted_ocr_text: str = ""
    ocr_decision_history: tuple[OcrDecisionObservation, ...] = ()
    ocr_rejection_counts: Mapping[str, int] = field(default_factory=dict)
    last_detected_at_monotonic: float | None = None
    dropped_frames: int = 0
    dropped_capture_sampling: int = 0
    dropped_capture_coalesced: int = 0
    unstable_ocr_observations: int = 0
    stale_queued_translation: int = 0
    stale_running_translation_results: int = 0
    stale_queued_tts: int = 0
    stale_running_tts_results: int = 0
    audio_supersessions: int = 0
    ocr_execution_count: int = 0
    generation: int = 0

    def narration_funnel_summary(self) -> dict[str, int]:
        """Aggregate the raw decision counts into an interpretable funnel.

        Every stage where a subtitle can be lost is reported, using the
        pipeline's existing decision names.

        ``candidate_abandoned`` is derived, because no single event marks it. A
        candidate that has been seen once and never confirmed disappears through
        one of three existing decisions: the subtitle changed to something
        dissimilar (``candidate_replaced_dissimilar``), the stability window
        elapsed (``candidate_window_expired``), or a later observation reset it
        (``candidate_reset_*``). Summing those gives the number of candidates
        that reached 1/2 and were silently dropped, which no other counter shows.
        """

        counts = dict(self.narration_funnel)

        def total(*names: str) -> int:
            return sum(int(counts.get(name, 0)) for name in names)

        def prefixed(prefix: str) -> int:
            return sum(
                int(value)
                for name, value in counts.items()
                if name.startswith(prefix)
            )

        abandoned = (
            total("candidate_replaced_dissimilar", "candidate_window_expired")
            + prefixed("candidate_reset_")
        )
        return {
            "observations": int(counts.get("observations", 0)),
            "rejectedEmpty": prefixed("rejected_empty"),
            "rejectedLowConfidence": prefixed("rejected_low_confidence"),
            "rejectedDuplicate": prefixed("rejected_duplicate"),
            "candidateStarted": int(counts.get("candidate_started", 0)),
            "candidateAbandoned": abandoned,
            "candidateReplacedDissimilar": total("candidate_replaced_dissimilar"),
            "accepted": int(counts.get("accepted", 0)),
            "ttsSubmitted": int(counts.get("tts_submitted", 0)),
            "playedToCompletion": int(counts.get("played_to_completion", 0)),
            "supersededInAudioQueue": int(self.audio_supersessions),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "gameKey": self.game_key,
            "status": self.status.value,
            "message": self.message,
            "lastDetectedText": self.last_detected_text,
            "lastTranslation": self.last_translation,
            "lastSpokenText": self.last_spoken_text,
            "frameAcquisitionMs": self.frame_acquisition_ms,
            "captureMs": self.capture_ms,
            "ocrPreprocessingMs": self.ocr_preprocessing_ms,
            "ocrImagePreprocessingMs": self.ocr_image_preprocessing_ms,
            "ocrPngEncodingMs": self.ocr_png_encoding_ms,
            "ocrMs": self.ocr_ms,
            "ocrBackend": self.ocr_backend,
            "ocrRecognitionMs": self.ocr_recognition_ms,
            "ocrWorkerExecutionMs": self.ocr_worker_execution_ms,
            "ocrWorkerDecodeMs": self.ocr_worker_decode_ms,
            "ocrWorkerRoundtripMs": self.ocr_worker_roundtrip_ms,
            "ocrWorkerLockWaitMs": self.ocr_worker_lock_wait_ms,
            "ocrClientOverheadMs": self.ocr_client_overhead_ms,
            "ocrWorkerStartupMs": self.ocr_worker_startup_ms,
            "ocrWorkerRestartCount": self.ocr_worker_restart_count,
            "ocrCliFallbackCount": self.ocr_cli_fallback_count,
            "ocrFallbackReason": self.ocr_fallback_reason,
            "ocrDebugCapturePath": self.ocr_debug_capture_path,
            "stabilizationDedupMs": self.stabilization_dedup_ms,
            "translationMs": self.translation_ms,
            "ttsMs": self.tts_ms,
            "ttsQueueWaitMs": self.tts_queue_wait_ms,
            "ttsWorkerRoundtripMs": self.tts_worker_roundtrip_ms,
            "ttsInferenceMs": self.tts_inference_ms,
            "ttsSerializationMs": self.tts_serialization_ms,
            "ttsWorkerStartupMs": self.tts_worker_startup_ms,
            "ttsWorkerReused": self.tts_worker_reused,
            "ttsAudioDurationMs": self.tts_audio_duration_ms,
            "audioStartMs": self.audio_start_ms,
            "acceptedToAudioStartMs": self.accepted_to_audio_start_ms,
            "confirmingFrameToAudioStartMs": (
                self.confirming_frame_to_audio_start_ms
            ),
            "firstVisibleFrameToAudioStartMs": (
                self.first_visible_frame_to_audio_start_ms
            ),
            "totalCaptureToTextMs": self.total_capture_to_text_ms,
            "totalCaptureToAudioStartMs": self.total_capture_to_audio_start_ms,
            "firstVisibleFrameAtMonotonic": (
                self.first_visible_frame_at_monotonic
            ),
            "confirmingFrameAtMonotonic": self.confirming_frame_at_monotonic,
            "acceptedAtMonotonic": self.accepted_at_monotonic,
            "ttsStartedAtMonotonic": self.tts_started_at_monotonic,
            "ttsFinishedAtMonotonic": self.tts_finished_at_monotonic,
            "playbackStartedAtMonotonic": self.playback_started_at_monotonic,
            "captureWidth": self.capture_width,
            "captureHeight": self.capture_height,
            "ocrRoiWidth": self.ocr_roi_width,
            "ocrRoiHeight": self.ocr_roi_height,
            "lastVisualChangeDecision": self.last_visual_change_decision,
            "lastVisualChangeScore": self.last_visual_change_score,
            "captureState": self.capture_state,
            "captureFormat": self.capture_format,
            "captureDmabuf": self.capture_dmabuf,
            "captureVariantsTried": self.capture_variants_tried,
            "captureVariantsFailed": self.capture_variants_failed,
            "narrationFunnel": self.narration_funnel_summary(),
            "captureFramesReceived": self.capture_frames_received,
            "captureStreamErrors": self.capture_stream_errors,
            "captureRestarts": self.capture_restarts,
            "ocrStatus": self.ocr_status,
            "translationStatus": self.translation_status,
            "ttsStatus": self.tts_status,
            "audioStatus": self.audio_status,
            "ocrConfidence": self.ocr_confidence,
            "lastRawOcrText": self.last_raw_ocr_text,
            "lastFilteredOcrText": self.last_filtered_ocr_text,
            "lastNormalizedOcrText": self.last_normalized_ocr_text,
            "lastOcrRejectionReason": self.last_ocr_rejection_reason,
            "lastOcrObservationCredible": self.last_ocr_observation_credible,
            "lastOcrGateDecision": self.last_ocr_gate_decision,
            "ocrCandidateText": self.ocr_candidate_text,
            "ocrCandidateObservationCount": self.ocr_candidate_observation_count,
            "ocrCandidateRequiredObservations": (
                self.ocr_candidate_required_observations
            ),
            "ocrCandidateSimilarity": self.ocr_candidate_similarity,
            "ocrCandidateMatchKind": self.ocr_candidate_match_kind,
            "lastAcceptedOcrText": self.last_accepted_ocr_text,
            "ocrDecisionHistory": [
                observation.to_dict()
                for observation in reversed(self.ocr_decision_history)
            ],
            "ocrRejectionCounts": dict(self.ocr_rejection_counts),
            "lastDetectedAtMonotonic": self.last_detected_at_monotonic,
            "droppedFrames": self.dropped_frames,
            "droppedCaptureSampling": self.dropped_capture_sampling,
            "droppedCaptureCoalesced": self.dropped_capture_coalesced,
            "unstableOcrObservations": self.unstable_ocr_observations,
            "staleQueuedTranslation": self.stale_queued_translation,
            "staleRunningTranslationResults": (
                self.stale_running_translation_results
            ),
            "staleQueuedTts": self.stale_queued_tts,
            "staleRunningTtsResults": self.stale_running_tts_results,
            "audioSupersessions": self.audio_supersessions,
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
    "OcrDecisionObservation",
    "OcrResult",
    "PcmAudio",
    "TranslationResult",
]
