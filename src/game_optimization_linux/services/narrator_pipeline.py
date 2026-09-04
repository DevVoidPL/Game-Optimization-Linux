"""Bounded asynchronous pipeline for local subtitle narration."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import logging
from queue import SimpleQueue
import re
from threading import RLock
import time
from typing import Protocol
import unicodedata
from uuid import uuid4

from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureState,
    NarratorEvent,
    NarratorGameSettings,
    OcrDecisionObservation,
    NarratorSessionSnapshot,
    NarratorSessionStatus,
    NarratorSourceMode,
    NarratorSubtitleLanguageMode,
    OcrResult,
    PcmAudio,
    TranslationResult,
)

from .narrator_capture import CaptureRequest, ScreenCaptureProvider
from .narrator_persistence import TranslationCache


logger = logging.getLogger(__name__)

OCR_STABLE_OBSERVATIONS = 2
OCR_SIMILARITY_THRESHOLD = 0.88
OCR_STABILITY_WINDOW_SECONDS = 1.25
OCR_DECISION_HISTORY_LIMIT = 20


class SubtitleSource(Protocol):
    source_id: str

    def available_for(self, game_key: str) -> bool: ...

    def start(
        self,
        game_key: str,
        callback: Callable[[str, float], None],
    ) -> None: ...

    def stop(self) -> None: ...


class OcrProvider(Protocol):
    provider_id: str
    available: bool

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult: ...


class TranslationProvider(Protocol):
    provider_id: str
    available: bool

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult: ...

    def cancel(self) -> None: ...


class TtsProvider(Protocol):
    provider_id: str
    available: bool

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio: ...

    def cancel(self) -> None: ...


class NarratorAudioOutput(Protocol):
    provider_id: str
    available: bool

    def play(
        self,
        audio: PcmAudio,
        *,
        volume: float,
        request_id: int,
        started_callback: Callable[[float], None],
        completed_callback: Callable[[], None],
        error_callback: Callable[[str], None],
    ) -> None: ...

    def stop(self) -> None: ...


class GameActivityProvider(Protocol):
    def is_active(self, game_key: str) -> bool | None: ...


def normalize_subtitle(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text))
    printable = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    return " ".join(printable.split())


def subtitle_identity(text: str) -> str:
    """Normalize case, punctuation and OCR-lost accents for comparisons."""
    normalized = normalize_subtitle(text).casefold().translate(
        str.maketrans({"ł": "l", "đ": "d"})
    )
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.category(character).startswith("M")
    )
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in normalized
        ).split()
    )


@dataclass(frozen=True, slots=True)
class OcrGateObservation:
    raw_text: str
    filtered_text: str
    confidence: float | None
    rejection_reason: str
    accepted_text: str = ""
    needs_confirmation: bool = False
    candidate_started: bool = False
    credible: bool = False
    candidate_text: str = ""
    candidate_observation_count: int = 0
    required_observations: int = OCR_STABLE_OBSERVATIONS
    candidate_similarity: float | None = None
    candidate_match_kind: str = ""
    candidate_replaced: bool = False
    decision: str = ""


@dataclass(frozen=True, slots=True)
class _AcceptedSubtitleTiming:
    first_visible_frame_timestamp: float
    frame_timestamp: float
    accepted_at: float
    frame_acquisition_ms: float
    roi_preparation_ms: float
    ocr_preprocessing_ms: float
    ocr_ms: float
    stabilization_dedup_ms: float


@dataclass(frozen=True, slots=True)
class _TtsStageResult:
    audio: PcmAudio
    started_at: float
    finished_at: float


class SubtitleTextGate:
    """Reject implausible OCR and require short temporal text consensus."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.62,
        required_observations: int = OCR_STABLE_OBSERVATIONS,
        similarity_threshold: float = OCR_SIMILARITY_THRESHOLD,
        stability_window_seconds: float = OCR_STABILITY_WINDOW_SECONDS,
    ) -> None:
        self.min_confidence = min(1.0, max(0.0, float(min_confidence)))
        self.required_observations = max(2, int(required_observations))
        self.similarity_threshold = min(1.0, max(0.0, similarity_threshold))
        self.stability_window_seconds = max(0.1, stability_window_seconds)
        self._candidate_text = ""
        self._candidate_identity = ""
        self._candidate_confidence = -1.0
        self._candidate_count = 0
        self._candidate_since = 0.0
        self._candidate_gap_count = 0
        self._accepted_identity = ""
        self._needs_confirmation = False

    @property
    def needs_confirmation(self) -> bool:
        return self._needs_confirmation

    def observe(
        self,
        text: str,
        confidence: float | None,
        *,
        now: float,
        raw_text: str | None = None,
        strong_short_phrase_evidence: bool = False,
    ) -> OcrGateObservation:
        raw = str(text if raw_text is None else raw_text)
        filtered = normalize_subtitle(text)
        previous_candidate = self._candidate_text
        previous_identity = self._candidate_identity
        previous_since = self._candidate_since
        reason = self._validate(filtered, confidence)
        if reason:
            if (
                previous_candidate
                and now - previous_since <= self.stability_window_seconds
                and self._candidate_gap_count == 0
            ):
                self._candidate_gap_count = 1
                self._needs_confirmation = True
                return OcrGateObservation(
                    raw,
                    filtered,
                    confidence,
                    reason,
                    needs_confirmation=True,
                    credible=False,
                    candidate_text=previous_candidate,
                    candidate_observation_count=self._candidate_count,
                    required_observations=self.required_observations,
                    decision=f"candidate_retained_after_{reason}",
                )
            self._clear(no_subtitle=True)
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                reason,
                required_observations=self.required_observations,
                decision=(
                    f"candidate_reset_{reason}"
                    if previous_candidate
                    else f"rejected_{reason}"
                ),
            )

        identity = subtitle_identity(filtered)
        accepted_similarity = self._similarity(identity, self._accepted_identity)
        if self._accepted_identity and self._strict_identities_match(
            identity,
            self._accepted_identity,
            similarity=accepted_similarity,
        ):
            self._reset_candidate()
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "duplicate",
                credible=True,
                required_observations=self.required_observations,
                candidate_similarity=accepted_similarity,
                candidate_match_kind=(
                    "normalized_exact"
                    if identity == self._accepted_identity
                    else "similarity"
                ),
                decision="duplicate_accepted_phrase",
            )

        # Normal subtitle acceptance remains a two-observation consensus. A
        # short subtitle can be visible for only one sampled OCR frame, though.
        # Permit one observation only when the OCR provider proved that it is a
        # clean, very-high-confidence, single-line cluster and the caller also
        # observed an actual subtitle-region image change. This evidence is not
        # inferred from text length alone and is never available to plain OCR
        # strings or noisy/filtered observations.
        if strong_short_phrase_evidence and not self._candidate_identity:
            self._accepted_identity = identity
            self._reset_candidate()
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "",
                filtered,
                credible=True,
                candidate_text=filtered,
                candidate_observation_count=1,
                required_observations=1,
                candidate_match_kind="strong_short_evidence",
                decision="accepted_strong_short_evidence",
            )

        within_window = (
            self._candidate_identity
            and now - self._candidate_since <= self.stability_window_seconds
        )
        candidate_similarity = self._similarity(identity, self._candidate_identity)
        match_kind = self._identity_match_kind(
            identity,
            self._candidate_identity,
            similarity=candidate_similarity,
        )
        similar = within_window and bool(match_kind)
        if not similar:
            self._candidate_text = filtered
            self._candidate_identity = identity
            self._candidate_confidence = confidence if confidence is not None else -1.0
            self._candidate_count = 1
            self._candidate_since = now
            self._candidate_gap_count = 0
            self._needs_confirmation = True
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "unstable",
                needs_confirmation=True,
                candidate_started=True,
                credible=True,
                candidate_text=filtered,
                candidate_observation_count=1,
                required_observations=self.required_observations,
                candidate_similarity=(
                    candidate_similarity if previous_identity else None
                ),
                candidate_replaced=bool(previous_identity),
                decision=(
                    "candidate_window_expired"
                    if previous_identity
                    and now - previous_since > self.stability_window_seconds
                    else (
                        "candidate_replaced_dissimilar"
                        if previous_identity
                        else "candidate_started"
                    )
                ),
            )

        self._candidate_count += 1
        self._candidate_gap_count = 0
        if confidence is not None and confidence > self._candidate_confidence:
            self._candidate_text = filtered
            self._candidate_identity = identity
            self._candidate_confidence = confidence
        if self._candidate_count < self.required_observations:
            self._needs_confirmation = True
            return OcrGateObservation(
                raw,
                filtered,
                confidence,
                "unstable",
                needs_confirmation=True,
                credible=True,
                candidate_text=self._candidate_text,
                candidate_observation_count=self._candidate_count,
                required_observations=self.required_observations,
                candidate_similarity=candidate_similarity,
                candidate_match_kind=match_kind,
                decision="candidate_confirming",
            )

        accepted = self._candidate_text
        self._accepted_identity = self._candidate_identity
        accepted_count = self._candidate_count
        self._reset_candidate()
        return OcrGateObservation(
            raw,
            filtered,
            confidence,
            "",
            accepted,
            credible=True,
            candidate_text=accepted,
            candidate_observation_count=accepted_count,
            required_observations=self.required_observations,
            candidate_similarity=candidate_similarity,
            candidate_match_kind=match_kind,
            decision="accepted_consensus",
        )

    def _clear(self, *, no_subtitle: bool) -> None:
        self._reset_candidate()
        if no_subtitle:
            self._accepted_identity = ""

    def _reset_candidate(self) -> None:
        self._candidate_text = ""
        self._candidate_identity = ""
        self._candidate_confidence = -1.0
        self._candidate_count = 0
        self._candidate_since = 0.0
        self._candidate_gap_count = 0
        self._needs_confirmation = False

    @staticmethod
    def _similarity(first: str, second: str) -> float:
        if not first or not second:
            return 0.0
        if first == second:
            return 1.0
        return SequenceMatcher(None, first, second, autojunk=False).ratio()

    def _identity_match_kind(
        self,
        first: str,
        second: str,
        *,
        similarity: float,
    ) -> str:
        if not self._numbers_match(first, second):
            return ""
        if similarity >= self.similarity_threshold:
            return "normalized_exact" if first == second else "similarity"
        # One OCR substitution/insertion/deletion in a real word must not keep
        # restarting a two-frame consensus. Keep short dialogue conservative:
        # e.g. "Nie" and "Nic" are not interchangeable. Numeric changes are
        # semantic ("Room 101" vs "Room 102"), so never relax those.
        if (
            min(len(first), len(second)) >= 5
            and self._edit_distance_at_most_one(first, second)
        ):
            return "single_edit"
        return ""

    def _strict_identities_match(
        self,
        first: str,
        second: str,
        *,
        similarity: float,
    ) -> bool:
        return (
            self._numbers_match(first, second)
            and similarity >= self.similarity_threshold
        )

    @staticmethod
    def _numbers_match(first: str, second: str) -> bool:
        return re.findall(r"\d+", first) == re.findall(r"\d+", second)

    @staticmethod
    def _edit_distance_at_most_one(first: str, second: str) -> bool:
        if abs(len(first) - len(second)) > 1:
            return False
        if first == second:
            return True
        if len(first) == len(second):
            return sum(left != right for left, right in zip(first, second)) == 1
        shorter, longer = (first, second) if len(first) < len(second) else (second, first)
        short_index = long_index = differences = 0
        while short_index < len(shorter) and long_index < len(longer):
            if shorter[short_index] == longer[long_index]:
                short_index += 1
                long_index += 1
                continue
            differences += 1
            if differences > 1:
                return False
            long_index += 1
        return True

    def _validate(self, text: str, confidence: float | None) -> str:
        if not text:
            return "empty"
        if confidence is None or confidence < self.min_confidence:
            return "low_confidence"

        visible = [character for character in text if not character.isspace()]
        alpha_count = sum(character.isalpha() for character in visible)
        digit_count = sum(character.isdigit() for character in visible)
        alphanumeric_count = alpha_count + digit_count
        if alpha_count < 2:
            return "min_alphabetic"
        if alphanumeric_count and digit_count / alphanumeric_count > 0.50:
            return "digit_ratio"
        noise_count = self._noise_units("".join(visible))
        visible_units = alphanumeric_count + noise_count
        if visible_units and noise_count / visible_units > 0.35:
            return "symbol_ratio"

        fragments = re.findall(
            r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}][^\W_]+)?",
            text,
        )
        isolated = sum(
            len(fragment.replace("'", "").replace("’", "")) == 1
            for fragment in fragments
        )
        if (
            len(fragments) >= 3
            and isolated >= 3
            and isolated / len(fragments) >= 0.60
        ):
            return "isolated_fragments"

        for fragment in fragments:
            compact = fragment.replace("'", "").replace("’", "")
            letters = sum(character.isalpha() for character in compact)
            digits = sum(character.isdigit() for character in compact)
            if (
                len(compact) >= 8
                and letters
                and digits
                and digits / len(compact) >= 0.15
            ):
                return "alphanumeric_noise"
            alphabetic_runs = re.findall(r"[^\W\d_]+", compact)
            for run in alphabetic_runs:
                if len(run) < 8:
                    continue
                longest_consonant_run = max(
                    (len(value) for value in re.split(r"[aeiouyAEIOUY]+", run)),
                    default=0,
                )
                if longest_consonant_run >= 7:
                    return "alphabetic_noise"
        return ""

    @staticmethod
    def _noise_units(text: str) -> int:
        """Count OCR noise while tolerating contractions and punctuation runs."""
        units = 0
        previous_terminal = False
        for index, character in enumerate(text):
            if character.isalnum():
                previous_terminal = False
                continue
            if (
                character in {"'", "’"}
                and index > 0
                and index + 1 < len(text)
                and text[index - 1].isalpha()
                and text[index + 1].isalpha()
            ):
                previous_terminal = False
                continue
            is_terminal = character in {".", "!", "?", "…"}
            if is_terminal and previous_terminal:
                continue
            units += 1
            previous_terminal = is_terminal
        return units


class PhraseDeduplicator:
    def __init__(self) -> None:
        self._visible_phrase = ""
        self._spoken_at: dict[str, float] = {}

    def accept(self, text: str, *, now: float, cooldown_seconds: float) -> str | None:
        normalized = normalize_subtitle(text)
        identity = normalized.casefold()
        if not identity:
            self._visible_phrase = ""
            return None
        if identity == self._visible_phrase:
            return None
        self._visible_phrase = identity
        previous = self._spoken_at.get(identity)
        if previous is not None and now - previous < cooldown_seconds:
            return None
        return normalized

    def mark_spoken(self, text: str, *, now: float) -> None:
        identity = normalize_subtitle(text).casefold()
        if identity:
            self._spoken_at[identity] = now


def crop_frame(frame: CaptureFrame, region: object) -> CaptureFrame:
    channels_by_format = {
        "rgba8888": 4,
        "bgra8888": 4,
        "rgb888": 3,
        "bgr888": 3,
        "gray8": 1,
    }
    channels = channels_by_format.get(frame.pixel_format.casefold())
    if channels is None:
        raise ValueError(f"unsupported capture pixel format: {frame.pixel_format}")
    x = max(0, min(frame.width - 1, round(float(getattr(region, "x")) * frame.width)))
    y = max(0, min(frame.height - 1, round(float(getattr(region, "y")) * frame.height)))
    width = max(
        1,
        min(frame.width - x, round(float(getattr(region, "width")) * frame.width)),
    )
    height = max(
        1,
        min(frame.height - y, round(float(getattr(region, "height")) * frame.height)),
    )
    output_stride = width * channels
    output = bytearray(output_stride * height)
    source = memoryview(frame.pixels)
    for row in range(height):
        start = (y + row) * frame.stride + x * channels
        target = row * output_stride
        output[target : target + output_stride] = source[start : start + output_stride]
    return CaptureFrame(
        session_id=frame.session_id,
        generation=frame.generation,
        timestamp_monotonic=frame.timestamp_monotonic,
        width=width,
        height=height,
        stride=output_stride,
        pixel_format=frame.pixel_format,
        pixels=bytes(output),
        source_id=frame.source_id,
    )


class SubtitleRegionStabilizer:
    def __init__(self) -> None:
        self._accepted_signature = b""
        self._pending_signature = b""
        self._pending_since = 0.0
        self._pending_frame: CaptureFrame | None = None
        self.last_localized_difference: float | None = None
        self.last_decision = "not_observed"

    @staticmethod
    def _signature(frame: CaptureFrame) -> bytes:
        pixels = frame.pixels
        if not pixels:
            return b""
        target = 2048
        step = max(1, len(pixels) // target)
        return bytes(pixels[::step][:target])

    @staticmethod
    def _difference(first: bytes, second: bytes) -> float:
        if not first or not second or len(first) != len(second):
            return 1.0
        return sum(abs(left - right) for left, right in zip(first, second)) / (
            255.0 * len(first)
        )

    @staticmethod
    def _localized_difference(first: bytes, second: bytes) -> float:
        """Measure localized changes without diluting subtitle pixels in a large ROI."""
        if not first or not second or len(first) != len(second):
            return 1.0
        differences = sorted(
            (abs(left - right) for left, right in zip(first, second)),
            reverse=True,
        )
        sample_count = max(1, len(differences) // 10)
        return sum(differences[:sample_count]) / (255.0 * sample_count)

    def consider_for_ocr(
        self,
        frame: CaptureFrame,
        *,
        threshold: float,
    ) -> CaptureFrame | None:
        """Promptly sample localized subtitle changes; text consensus adds safety."""
        signature = self._signature(frame)
        if self._accepted_signature:
            difference = self._localized_difference(
                signature, self._accepted_signature
            )
            self.last_localized_difference = difference
            if difference < threshold:
                self.last_decision = "unchanged"
                return None
            self.last_decision = "localized_change"
        else:
            self.last_localized_difference = None
            self.last_decision = "initial_probe"
        self._accepted_signature = signature
        self._pending_signature = b""
        self._pending_frame = None
        self._pending_since = 0.0
        return frame

    def consider(
        self,
        frame: CaptureFrame,
        *,
        threshold: float,
        stabilization_seconds: float,
    ) -> CaptureFrame | None:
        signature = self._signature(frame)
        now = frame.timestamp_monotonic
        if self._accepted_signature and self._difference(
            signature, self._accepted_signature
        ) < threshold:
            self._pending_signature = b""
            self._pending_frame = None
            self._pending_since = 0.0
            return None
        if not self._pending_signature or self._difference(
            signature, self._pending_signature
        ) >= threshold:
            self._pending_signature = signature
            self._pending_since = now
            self._pending_frame = frame
            return None
        self._pending_frame = frame
        if now - self._pending_since < stabilization_seconds:
            return None
        accepted = self._pending_frame
        self._accepted_signature = signature
        self._pending_signature = b""
        self._pending_frame = None
        return accepted


class UnavailableOcrProvider:
    provider_id = "unavailable"
    available = False

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
        del frame, language
        raise RuntimeError("No local OCR provider is installed")


class UnavailableTranslationProvider:
    provider_id = "unavailable"
    available = False

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult:
        del text, source_language, target_language, profile_id
        raise RuntimeError("No local translation provider is installed")

    def cancel(self) -> None:
        return


class UnavailableTtsProvider:
    provider_id = "unavailable"
    available = False

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio:
        del text, language, voice_id, speech_rate
        raise RuntimeError("No Polish TTS provider is installed")

    def cancel(self) -> None:
        return


class UnavailableAudioOutput:
    provider_id = "unavailable"
    available = False

    def play(
        self,
        audio: PcmAudio,
        *,
        volume: float,
        request_id: int,
        started_callback: Callable[[float], None],
        completed_callback: Callable[[], None],
        error_callback: Callable[[str], None],
    ) -> None:
        del (
            audio,
            volume,
            request_id,
            started_callback,
            completed_callback,
            error_callback,
        )
        raise RuntimeError("Narrator audio output is unavailable")

    def stop(self) -> None:
        return


class NarratorPipeline:
    """Run capture and inference without blocking or calling Qt from workers."""

    def __init__(
        self,
        capture: ScreenCaptureProvider,
        ocr: OcrProvider,
        translator: TranslationProvider,
        tts: TtsProvider,
        audio: NarratorAudioOutput,
        activity: GameActivityProvider,
        translation_cache: TranslationCache | None = None,
        *,
        executor: ThreadPoolExecutor | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capture = capture
        self.ocr = ocr
        self.translator = translator
        self.tts = tts
        self.audio = audio
        self.activity = activity
        self.cache = translation_cache or TranslationCache()
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="game-optimization-narrator"
        )
        self._owns_executor = executor is None
        self._clock = clock
        self._events: SimpleQueue[NarratorEvent] = SimpleQueue()
        self._lock = RLock()
        self._settings: NarratorGameSettings | None = None
        self._session_id = ""
        self._generation = 0
        self._request_id = 0
        self._work_id = 0
        self._latest_audio_request_id = 0
        self._stabilizer = SubtitleRegionStabilizer()
        self._text_gate = SubtitleTextGate()
        self._deduplicator = PhraseDeduplicator()
        self._ocr_rejection_counts: dict[str, int] = {}
        self._last_ocr_diagnostic: tuple[str, str, float | None] | None = None
        self._last_ocr_backend_diagnostic: tuple[str, str] | None = None
        self._ocr_future: Future[OcrResult] | None = None
        self._ocr_prepare_future: Future[object] | None = None
        self._tts_prepare_future: Future[object] | None = None
        self._request_active = False
        self._pending_frame: CaptureFrame | None = None
        self._pending_frame_stabilization_ms = 0.0
        self._pending_frame_visual_decision = ""
        self._last_sampled_at: float | None = None
        self._first_visible_frame_timestamp: float | None = None
        self._inactive_since: float | None = None
        self._stage_futures: set[Future[object]] = set()
        self._stage_kinds: dict[Future[object], str] = {}
        self._audio_supersession_baseline = 0
        self._latest_capture_frame: CaptureFrame | None = None
        self._latest_capture_game_key = ""
        self._preview_generation = 0
        self._preview_session_id = ""
        self._preview_game_key = ""
        self._preview_temporary = False
        self._preview_delivered = False
        self._preview_frame_callback: Callable[[CaptureFrame], None] | None = None
        self._preview_state_callback: Callable[[CaptureState, str], None] | None = None
        self._snapshot = NarratorSessionSnapshot()

    @property
    def snapshot(self) -> NarratorSessionSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def active(self) -> bool:
        return self.snapshot.status not in {
            NarratorSessionStatus.IDLE,
            NarratorSessionStatus.STOPPED,
            NarratorSessionStatus.ERROR,
        }

    def latest_preview_frame(self, game_key: str) -> CaptureFrame | None:
        """Return the last in-memory full capture frame for one game."""

        with self._lock:
            if self._latest_capture_game_key != str(game_key):
                return None
            return self._latest_capture_frame

    def request_preview_frame(
        self,
        settings: NarratorGameSettings,
        *,
        frame_callback: Callable[[CaptureFrame], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        """Reuse an active stream or request one portal frame for ROI setup."""

        self.cancel_preview_frame()
        with self._lock:
            if self.active:
                if self._snapshot.game_key != settings.game_key:
                    raise RuntimeError(
                        "Stop the active Narrator session for the other game first"
                    )
                frame = (
                    self._latest_capture_frame
                    if self._latest_capture_game_key == settings.game_key
                    else None
                )
                if frame is None:
                    self._preview_game_key = settings.game_key
                    self._preview_temporary = False
                    self._preview_frame_callback = frame_callback
                    self._preview_state_callback = state_callback
                else:
                    frame_callback(frame)
                state_callback(CaptureState.ACTIVE, "")
                return
            capabilities = self.capture.capabilities()
            if not capabilities.available:
                raise RuntimeError(
                    capabilities.message or "Screen capture is unavailable"
                )
            self._preview_generation += 1
            generation = self._preview_generation
            session_id = f"region-{uuid4().hex}"
            self._preview_session_id = session_id
            self._preview_game_key = settings.game_key
            self._preview_temporary = True
            self._preview_delivered = False
            self._preview_frame_callback = frame_callback
            self._preview_state_callback = state_callback
            request = CaptureRequest(
                session_id=session_id,
                game_key=settings.game_key,
                generation=generation,
                source_type=settings.capture_source,
                sampling_hz=1.0,
            )
        self.capture.start(
            request,
            frame_callback=self._preview_frame_received,
            state_callback=self._preview_state_changed,
        )

    def cancel_preview_frame(self) -> None:
        """Cancel only a temporary selector capture, never active narration."""

        with self._lock:
            temporary = self._preview_temporary
            self._preview_generation += 1
            self._preview_session_id = ""
            self._preview_game_key = ""
            self._preview_temporary = False
            self._preview_delivered = False
            self._preview_frame_callback = None
            self._preview_state_callback = None
        if temporary:
            self.capture.stop()

    def _preview_frame_received(self, frame: CaptureFrame) -> None:
        with self._lock:
            if (
                not self._preview_temporary
                or self._preview_delivered
                or frame.session_id != self._preview_session_id
                or frame.generation != self._preview_generation
            ):
                return
            callback = self._preview_frame_callback
            game_key = self._preview_game_key
            self._preview_delivered = True
            self._latest_capture_frame = frame
            self._latest_capture_game_key = game_key
        if callback is not None:
            callback(frame)

    def _preview_state_changed(self, state: CaptureState, message: str) -> None:
        with self._lock:
            callback = self._preview_state_callback
            temporary = self._preview_temporary
        if temporary and callback is not None:
            callback(state, message)

    def missing_requirements(
        self, settings: NarratorGameSettings | None = None
    ) -> tuple[str, ...]:
        selected = settings or self._settings
        polish_mode = bool(
            selected is not None
            and selected.subtitle_language_mode
            is NarratorSubtitleLanguageMode.POLISH
        )
        missing: list[str] = []
        if not self.capture.capabilities().available:
            missing.append("capture")
        if not self._ocr_language_available("pl" if polish_mode else "en"):
            missing.append("ocr")
        if not polish_mode and not self.translator.available:
            missing.append("translation")
        if not self.tts.available:
            missing.append("tts")
        if not self.audio.available:
            missing.append("audio")
        return tuple(missing)

    def full_narration_available(
        self, settings: NarratorGameSettings | None = None
    ) -> bool:
        selected = settings or self._settings
        translator_ready = bool(
            selected is not None
            and selected.subtitle_language_mode
            is NarratorSubtitleLanguageMode.POLISH
        ) or self.translator.available
        return bool(
            translator_ready and self.tts.available and self.audio.available
        )

    def _ocr_language_available(self, language: str) -> bool:
        language_available = getattr(self.ocr, "language_available", None)
        if callable(language_available):
            return bool(language_available(language))
        return bool(self.ocr.available)

    def start(self, settings: NarratorGameSettings) -> NarratorSessionSnapshot:
        self.cancel_preview_frame()
        with self._lock:
            if self.active:
                raise RuntimeError("A narrator session is already active")
            if not settings.enabled:
                raise RuntimeError("Narrator is disabled for this game")
            if settings.source_mode is NarratorSourceMode.ADAPTER:
                raise RuntimeError("No game subtitle adapter is available")
            settings = self._validated_settings(settings)
            missing = self.missing_requirements(settings)
            if missing:
                raise RuntimeError(
                    "Narrator components are unavailable: " + ", ".join(missing)
                )
            activity = self.activity.is_active(settings.game_key)
            if activity is False:
                raise RuntimeError("The selected game is not running")
            self._generation += 1
            self._request_id = 0
            self._work_id = 0
            self._latest_audio_request_id = 0
            self._session_id = uuid4().hex
            self._settings = settings
            self._stabilizer = SubtitleRegionStabilizer()
            self._text_gate = SubtitleTextGate(
                min_confidence=settings.ocr_min_confidence
            )
            self._deduplicator = PhraseDeduplicator()
            self._ocr_rejection_counts = {}
            self._last_ocr_diagnostic = None
            self._last_ocr_backend_diagnostic = None
            self._request_active = False
            self._pending_frame = None
            self._pending_frame_stabilization_ms = 0.0
            self._pending_frame_visual_decision = ""
            self._last_sampled_at = None
            self._first_visible_frame_timestamp = None
            self._inactive_since = None
            self._audio_supersession_baseline = int(
                getattr(self.audio, "superseded_count", 0)
            )
            self._snapshot = NarratorSessionSnapshot(
                session_id=self._session_id,
                game_key=settings.game_key,
                status=NarratorSessionStatus.STARTING,
                generation=self._generation,
                capture_state=CaptureState.STARTING.value,
                ocr_status="ready",
                translation_status=(
                    "bypassed"
                    if settings.subtitle_language_mode
                    is NarratorSubtitleLanguageMode.POLISH
                    else (
                        "ready"
                        if self.translator.available
                        else "component_missing"
                    )
                ),
                tts_status="ready" if self.tts.available else "component_missing",
                audio_status="ready" if self.audio.available else "unavailable",
            )
            self._emit(NarratorSessionStatus.STARTING)
            ocr_language = (
                "pl"
                if settings.subtitle_language_mode
                is NarratorSubtitleLanguageMode.POLISH
                else "en"
            )
            prepare_ocr = getattr(self.ocr, "prepare", None)
            if callable(prepare_ocr):
                self._ocr_prepare_future = self._executor.submit(
                    prepare_ocr, ocr_language
                )
                self._ocr_prepare_future.add_done_callback(
                    self._ocr_preparation_finished
                )
            prepare_tts = getattr(self.tts, "prepare", None)
            if callable(prepare_tts):
                self._tts_prepare_future = self._executor.submit(
                    prepare_tts, settings.voice_id
                )
                self._tts_prepare_future.add_done_callback(
                    self._tts_preparation_finished
                )
            request = CaptureRequest(
                session_id=self._session_id,
                game_key=settings.game_key,
                generation=self._generation,
                source_type=settings.capture_source,
                sampling_hz=settings.capture_sampling_hz,
            )
        self.capture.start(
            request,
            frame_callback=self.submit_frame,
            state_callback=self._capture_state_changed,
        )
        return self.snapshot

    def _validated_settings(
        self, settings: NarratorGameSettings
    ) -> NarratorGameSettings:
        polish_mode = (
            settings.subtitle_language_mode is NarratorSubtitleLanguageMode.POLISH
        )
        selected_providers = [
            ("OCR", settings.ocr_provider_id, self.ocr.provider_id),
            ("speech", settings.tts_provider_id, self.tts.provider_id),
        ]
        if not polish_mode:
            selected_providers.insert(
                1,
                (
                    "translation",
                    settings.translation_provider_id,
                    self.translator.provider_id,
                ),
            )
        for label, selected, active in selected_providers:
            if selected and selected != active:
                raise RuntimeError(
                    f"The selected {label} provider is not available: {selected}"
                )

        profile_id = settings.translation_profile_id
        if not polish_mode:
            profiles = tuple(
                str(value) for value in getattr(self.translator, "profile_ids", ())
            )
            profile_id = profile_id or str(
                getattr(self.translator, "default_profile_id", "")
            )
            if not profile_id and profiles:
                profile_id = profiles[0]
            if profiles and profile_id not in profiles:
                raise RuntimeError(
                    f"The selected translation profile is not available: {profile_id}"
                )

        voices = tuple(
            str(value) for value in getattr(self.tts, "available_voice_ids", ())
        )
        voice_id = settings.voice_id or str(
            getattr(self.tts, "default_voice_id", "")
        )
        if not voice_id and voices:
            voice_id = voices[0]
        if voices and voice_id not in voices:
            raise RuntimeError(f"The selected Polish voice is not available: {voice_id}")

        return replace(
            settings,
            ocr_provider_id=settings.ocr_provider_id or self.ocr.provider_id,
            translation_provider_id=(
                settings.translation_provider_id
                if polish_mode
                else settings.translation_provider_id or self.translator.provider_id
            ),
            translation_profile_id=profile_id,
            tts_provider_id=settings.tts_provider_id or self.tts.provider_id,
            voice_id=voice_id,
        )

    def stop(self, message: str = "") -> NarratorSessionSnapshot:
        with self._lock:
            if not self.active:
                return self._snapshot
            logger.debug(
                "Narrator work counters capture_sampling=%d "
                "capture_coalesced=%d unstable_ocr=%d "
                "translation_queued=%d translation_running=%d "
                "tts_queued=%d tts_running=%d audio_supersessions=%d",
                self._snapshot.dropped_capture_sampling,
                self._snapshot.dropped_capture_coalesced,
                self._snapshot.unstable_ocr_observations,
                self._snapshot.stale_queued_translation,
                self._snapshot.stale_running_translation_results,
                self._snapshot.stale_queued_tts,
                self._snapshot.stale_running_tts_results,
                self._snapshot.audio_supersessions,
            )
            settings = self._settings
            self._generation += 1
            self._request_id += 1
            self._work_id += 1
            self._latest_audio_request_id = 0
            self._request_active = False
            self._pending_frame = None
            self._pending_frame_stabilization_ms = 0.0
            self._pending_frame_visual_decision = ""
            self._first_visible_frame_timestamp = None
            if self._ocr_future is not None:
                self._ocr_future.cancel()
            if self._ocr_prepare_future is not None:
                self._ocr_prepare_future.cancel()
                self._ocr_prepare_future = None
            if self._tts_prepare_future is not None:
                self._tts_prepare_future.cancel()
                self._tts_prepare_future = None
            for future in tuple(self._stage_futures):
                future.cancel()
            self._stage_futures.clear()
            self._stage_kinds.clear()
            self._cancel_provider_work()
            game_key = self._snapshot.game_key
            session_id = self._snapshot.session_id
        self.capture.stop()
        self.audio.stop()
        with self._lock:
            self._settings = None
            self._snapshot = NarratorSessionSnapshot(
                session_id=session_id,
                game_key=game_key,
                status=NarratorSessionStatus.STOPPED,
                message=message,
                generation=self._generation,
                capture_state=CaptureState.STOPPED.value,
                ocr_status=(
                    "ready"
                    if self._ocr_language_available(
                        "pl"
                        if settings is not None
                        and settings.subtitle_language_mode
                        is NarratorSubtitleLanguageMode.POLISH
                        else "en"
                    )
                    else "component_missing"
                ),
                translation_status=(
                    "bypassed"
                    if settings is not None
                    and settings.subtitle_language_mode
                    is NarratorSubtitleLanguageMode.POLISH
                    else (
                        "ready"
                        if self.translator.available
                        else "component_missing"
                    )
                ),
                tts_status="ready" if self.tts.available else "component_missing",
                audio_status="stopped" if self.audio.available else "unavailable",
            )
            self._events.put(
                NarratorEvent(
                    session_id=session_id,
                    game_key=game_key,
                    generation=self._generation,
                    status=NarratorSessionStatus.STOPPED,
                    message=message,
                )
            )
            return self._snapshot

    def poll_game_activity(self) -> None:
        snapshot = self.snapshot
        if not self.active or not snapshot.game_key:
            return
        active = self.activity.is_active(snapshot.game_key)
        with self._lock:
            if active is not False:
                self._inactive_since = None
                return
            now = self._clock()
            if self._inactive_since is None:
                self._inactive_since = now
                return
            if now - self._inactive_since < 2.0:
                return
        self.stop("The game exited")

    def submit_frame(self, frame: CaptureFrame) -> None:
        with self._lock:
            received_at = self._clock()
            settings = self._settings
            if (
                settings is None
                or frame.session_id != self._session_id
                or frame.generation != self._generation
            ):
                return
            self._latest_capture_frame = frame
            self._latest_capture_game_key = settings.game_key
            if (
                not self._preview_temporary
                and self._preview_game_key == settings.game_key
                and self._preview_frame_callback is not None
            ):
                preview_callback = self._preview_frame_callback
                self._preview_frame_callback = None
                self._preview_state_callback = None
                self._preview_game_key = ""
                preview_callback(frame)
            sampling_interval = 1.0 / settings.capture_sampling_hz
            if (
                self._last_sampled_at is not None
                and frame.timestamp_monotonic >= self._last_sampled_at
                and frame.timestamp_monotonic - self._last_sampled_at
                < sampling_interval
            ):
                self._snapshot = replace(
                    self._snapshot,
                    dropped_frames=self._snapshot.dropped_frames + 1,
                    dropped_capture_sampling=(
                        self._snapshot.dropped_capture_sampling + 1
                    ),
                )
                return
            self._last_sampled_at = frame.timestamp_monotonic
            frame_acquisition_ms = max(
                0.0, (received_at - frame.timestamp_monotonic) * 1000.0
            )
            started = self._clock()
            try:
                cropped = crop_frame(frame, settings.subtitle_region)
            except Exception as error:
                self._recoverable_error(f"Could not crop the subtitle region: {error}")
                return
            capture_ms = max(0.0, (self._clock() - started) * 1000.0)
            stabilization_started = self._clock()
            if self._text_gate.needs_confirmation:
                stable = cropped
                visual_decision = "text_confirmation"
                visual_difference = None
            else:
                consider_for_ocr = getattr(
                    self._stabilizer, "consider_for_ocr", None
                )
                if callable(consider_for_ocr):
                    stable = consider_for_ocr(
                        cropped,
                        threshold=settings.visual_change_threshold,
                    )
                    visual_decision = str(
                        getattr(self._stabilizer, "last_decision", "unknown")
                    )
                    visual_difference = getattr(
                        self._stabilizer, "last_localized_difference", None
                    )
                else:
                    # Compatibility for lightweight test/provider stabilizers.
                    stable = self._stabilizer.consider(
                        cropped,
                        threshold=settings.visual_change_threshold,
                        stabilization_seconds=settings.stabilization_ms / 1000.0,
                    )
                    visual_decision = "legacy_stabilizer"
                    visual_difference = None
            stabilization_ms = max(
                0.0, (self._clock() - stabilization_started) * 1000.0
            )
            if stable is None:
                self._snapshot = replace(
                    self._snapshot,
                    frame_acquisition_ms=frame_acquisition_ms,
                    capture_ms=capture_ms,
                    stabilization_dedup_ms=stabilization_ms,
                    capture_width=frame.width,
                    capture_height=frame.height,
                    last_visual_change_decision=visual_decision,
                    last_visual_change_score=visual_difference,
                )
                return
            logger.debug(
                "Narrator subtitle ROI observation source=%s size=%dx%d "
                "decision=%s localized_difference=%s sent_to_ocr=true",
                stable.source_id or "unknown",
                stable.width,
                stable.height,
                visual_decision,
                (
                    f"{visual_difference:.4f}"
                    if visual_difference is not None
                    else "none"
                ),
            )
            self._snapshot = replace(
                self._snapshot,
                frame_acquisition_ms=frame_acquisition_ms,
                capture_ms=capture_ms,
                stabilization_dedup_ms=stabilization_ms,
                capture_width=frame.width,
                capture_height=frame.height,
                last_visual_change_decision=visual_decision,
                last_visual_change_score=visual_difference,
            )
            if self._request_active:
                if self._pending_frame is not None:
                    self._snapshot = replace(
                        self._snapshot,
                        dropped_frames=self._snapshot.dropped_frames + 1,
                        dropped_capture_coalesced=(
                            self._snapshot.dropped_capture_coalesced + 1
                        ),
                )
                self._pending_frame = stable
                self._pending_frame_stabilization_ms = stabilization_ms
                self._pending_frame_visual_decision = visual_decision
                return
            self._start_ocr(
                stable,
                stabilization_ms=stabilization_ms,
                visual_decision=visual_decision,
            )

    def drain_events(self) -> list[NarratorEvent]:
        events: list[NarratorEvent] = []
        while not self._events.empty():
            events.append(self._events.get())
        return events

    def shutdown(self) -> None:
        self.cancel_preview_frame()
        self.stop()
        close_capture = getattr(self.capture, "close", None)
        if callable(close_capture):
            close_capture()
        for provider in (self.ocr, self.translator, self.tts):
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        self.cache.close()
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _ocr_preparation_finished(self, future: Future[object]) -> None:
        with self._lock:
            if future is not self._ocr_prepare_future:
                return
            self._ocr_prepare_future = None
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as error:
            logger.debug(
                "Persistent OCR warm-up did not complete; recognition will "
                "retry and retain the CLI fallback: %s",
                error,
            )

    def _tts_preparation_finished(self, future: Future[object]) -> None:
        with self._lock:
            if future is not self._tts_prepare_future:
                return
            self._tts_prepare_future = None
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as error:
            logger.debug(
                "Persistent Piper warm-up did not complete; synthesis will "
                "retry on demand: %s",
                error,
            )

    def _start_ocr(
        self,
        frame: CaptureFrame,
        *,
        stabilization_ms: float = 0.0,
        visual_decision: str = "",
    ) -> None:
        settings = self._settings
        if settings is None:
            return
        generation = self._generation
        self._request_id += 1
        request_id = self._request_id
        self._request_active = True
        self._snapshot = replace(
            self._snapshot,
            ocr_status="processing",
            ocr_execution_count=self._snapshot.ocr_execution_count + 1,
            ocr_roi_width=frame.width,
            ocr_roi_height=frame.height,
        )
        self._emit(NarratorSessionStatus.OCR)
        language = (
            "pl"
            if settings.subtitle_language_mode is NarratorSubtitleLanguageMode.POLISH
            else "en"
        )
        logger.debug(
            "Narrator OCR ROI received request=%d generation=%d source=%s "
            "language=%s size=%dx%d frame_timestamp=%.6f",
            request_id,
            generation,
            frame.source_id or "unknown",
            language,
            frame.width,
            frame.height,
            frame.timestamp_monotonic,
        )
        future = self._executor.submit(
            self.ocr.recognize, frame, language=language
        )
        self._ocr_future = future
        future.add_done_callback(
            lambda completed: self._ocr_finished(
                completed,
                generation=generation,
                request_id=request_id,
                frame_timestamp=frame.timestamp_monotonic,
                stabilization_ms=stabilization_ms,
                visual_decision=visual_decision,
            )
        )

    def _ocr_finished(
        self,
        future: Future[OcrResult],
        *,
        generation: int,
        request_id: int,
        frame_timestamp: float,
        stabilization_ms: float,
        visual_decision: str,
    ) -> None:
        try:
            result = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and request_id == self._request_id
                ):
                    self._recoverable_error(f"OCR failed: {error}")
                    self._finish_ocr_request()
            return
        with self._lock:
            settings = self._settings
            if settings is None or generation != self._generation:
                return
            gate_started = self._clock()
            now = gate_started
            observation = self._text_gate.observe(
                result.filtered_text or result.text,
                result.confidence,
                now=now,
                raw_text=result.raw_text or result.text,
                strong_short_phrase_evidence=(
                    result.clean_short_phrase_evidence
                    and visual_decision in {"initial_probe", "localized_change"}
                ),
            )
            backend_diagnostic = (result.backend, result.fallback_reason)
            if backend_diagnostic != self._last_ocr_backend_diagnostic:
                logger.debug(
                    "Narrator OCR backend=%s complete=%.1fms image=%.1fms "
                    "png=%.1fms lock_wait=%.1fms roundtrip=%.1fms "
                    "worker=%.1fms decode=%.1fms recognition=%.1fms "
                    "client_overhead=%.1fms startup=%.1fms restarted=%s "
                    "fallback_reason=%s debug_capture=%s",
                    result.backend or "unknown",
                    result.elapsed_ms,
                    result.image_preprocessing_ms,
                    result.png_encoding_ms,
                    result.worker_lock_wait_ms or 0.0,
                    result.worker_roundtrip_ms or 0.0,
                    result.worker_execution_ms or 0.0,
                    result.worker_decode_ms or 0.0,
                    result.recognition_ms or 0.0,
                    result.client_overhead_ms or 0.0,
                    result.worker_startup_ms or 0.0,
                    result.worker_restarted,
                    result.fallback_reason or "none",
                    result.debug_capture_path or "none",
                )
                self._last_ocr_backend_diagnostic = backend_diagnostic
            if observation.candidate_started:
                self._first_visible_frame_timestamp = frame_timestamp
            rejection_reason = observation.rejection_reason
            if rejection_reason:
                self._ocr_rejection_counts[rejection_reason] = (
                    self._ocr_rejection_counts.get(rejection_reason, 0) + 1
                )
            if rejection_reason == "unstable":
                self._snapshot = replace(
                    self._snapshot,
                    unstable_ocr_observations=(
                        self._snapshot.unstable_ocr_observations + 1
                    ),
                )
            logger.debug(
                "Narrator OCR observation request=%d backend=%s fallback=%s "
                "raw=%r normalized=%r confidence=%s credible=%s decision=%s "
                "rejection=%s candidate=%r observations=%d/%d similarity=%s "
                "match=%s accepted=%r recognition=%.1fms complete=%.1fms "
                "debug_capture=%s",
                request_id,
                result.backend or "unknown",
                result.fallback_reason or "none",
                observation.raw_text,
                observation.filtered_text,
                (
                    f"{observation.confidence:.3f}"
                    if observation.confidence is not None
                    else "none"
                ),
                observation.credible,
                observation.decision or "none",
                rejection_reason or "none",
                observation.candidate_text,
                observation.candidate_observation_count,
                observation.required_observations,
                (
                    f"{observation.candidate_similarity:.3f}"
                    if observation.candidate_similarity is not None
                    else "none"
                ),
                observation.candidate_match_kind or "none",
                observation.accepted_text,
                result.recognition_ms or 0.0,
                result.elapsed_ms,
                result.debug_capture_path or "none",
            )

            if (
                rejection_reason not in {"unstable", "duplicate"}
                and not observation.needs_confirmation
            ):
                self._deduplicator.accept(
                    "",
                    now=now,
                    cooldown_seconds=settings.duplicate_cooldown_ms / 1000.0,
                )
            phrase = self._deduplicator.accept(
                observation.accepted_text,
                now=self._clock(),
                cooldown_seconds=settings.duplicate_cooldown_ms / 1000.0,
            ) if observation.accepted_text else None
            final_decision = observation.decision or "rejected_unknown"
            if observation.accepted_text and phrase is None:
                final_decision = "rejected_duplicate"
                rejection_reason = "duplicate"
                self._ocr_rejection_counts["duplicate"] = (
                    self._ocr_rejection_counts.get("duplicate", 0) + 1
                )
            elif phrase:
                final_decision = "accepted"
            self._append_ocr_decision(
                OcrDecisionObservation(
                    observation_id=request_id,
                    observed_at_monotonic=now,
                    raw_text=observation.raw_text,
                    filtered_text=result.filtered_text or result.text,
                    normalized_text=observation.filtered_text,
                    confidence=observation.confidence,
                    decision=final_decision,
                    rejection_reason=rejection_reason,
                    candidate_text=observation.candidate_text,
                    candidate_observation_count=(
                        observation.candidate_observation_count
                    ),
                    candidate_required_observations=(
                        observation.required_observations
                    ),
                    candidate_similarity=observation.candidate_similarity,
                    candidate_match_kind=observation.candidate_match_kind,
                    candidate_replaced=observation.candidate_replaced,
                    accepted_text=phrase or "",
                    accepted=bool(phrase),
                    roi_width=self._snapshot.ocr_roi_width,
                    roi_height=self._snapshot.ocr_roi_height,
                    backend=result.backend,
                    recognition_ms=result.recognition_ms,
                    token_count=result.token_count,
                    included_token_count=result.included_token_count,
                    line_count=result.line_count,
                    dropped_token_count=result.dropped_token_count,
                    minimum_token_confidence=result.minimum_token_confidence,
                    geometry_coherent=result.geometry_coherent,
                    clean_short_phrase_evidence=(
                        result.clean_short_phrase_evidence
                    ),
                    visual_change_decision=visual_decision,
                    filter_summary=result.filter_summary,
                )
            )
            stabilization_dedup_ms = stabilization_ms + max(
                0.0, (self._clock() - gate_started) * 1000.0
            )
            self._snapshot = replace(
                self._snapshot,
                last_detected_text=(
                    phrase or self._snapshot.last_detected_text
                ),
                ocr_preprocessing_ms=result.preprocessing_ms,
                ocr_image_preprocessing_ms=result.image_preprocessing_ms,
                ocr_png_encoding_ms=result.png_encoding_ms,
                ocr_ms=result.elapsed_ms,
                ocr_backend=result.backend,
                ocr_recognition_ms=result.recognition_ms,
                ocr_worker_execution_ms=result.worker_execution_ms,
                ocr_worker_decode_ms=result.worker_decode_ms,
                ocr_worker_roundtrip_ms=result.worker_roundtrip_ms,
                ocr_worker_lock_wait_ms=result.worker_lock_wait_ms,
                ocr_client_overhead_ms=result.client_overhead_ms,
                ocr_worker_startup_ms=result.worker_startup_ms,
                ocr_worker_restart_count=int(
                    getattr(self.ocr, "worker_restart_count", 0)
                ),
                ocr_cli_fallback_count=int(
                    getattr(self.ocr, "cli_fallback_count", 0)
                ),
                ocr_fallback_reason=result.fallback_reason,
                ocr_debug_capture_path=result.debug_capture_path,
                stabilization_dedup_ms=stabilization_dedup_ms,
                total_capture_to_text_ms=max(
                    0.0, (self._clock() - frame_timestamp) * 1000.0
                ),
                ocr_confidence=result.confidence,
                last_raw_ocr_text=observation.raw_text,
                last_filtered_ocr_text=result.filtered_text or result.text,
                last_normalized_ocr_text=observation.filtered_text,
                last_ocr_rejection_reason=rejection_reason,
                last_ocr_observation_credible=observation.credible,
                last_ocr_gate_decision=final_decision,
                ocr_candidate_text=observation.candidate_text,
                ocr_candidate_observation_count=(
                    observation.candidate_observation_count
                ),
                ocr_candidate_required_observations=(
                    observation.required_observations
                ),
                ocr_candidate_similarity=observation.candidate_similarity,
                ocr_candidate_match_kind=observation.candidate_match_kind,
                last_accepted_ocr_text=(
                    observation.accepted_text
                    or self._snapshot.last_accepted_ocr_text
                ),
                ocr_rejection_counts=dict(self._ocr_rejection_counts),
                last_detected_at_monotonic=(
                    now if phrase else self._snapshot.last_detected_at_monotonic
                ),
                ocr_status="ready",
            )
            if phrase is None:
                if not observation.needs_confirmation:
                    self._first_visible_frame_timestamp = None
                self._emit(NarratorSessionStatus.LISTENING)
                self._finish_ocr_request()
                return
            if not self.full_narration_available(settings):
                self._first_visible_frame_timestamp = None
                self._update_ocr_decision(
                    request_id,
                    decision="accepted_tts_unavailable",
                )
                self._emit(NarratorSessionStatus.LISTENING, detected_text=phrase)
                self._finish_ocr_request()
                return
            accepted_at = self._clock()
            first_visible_frame_timestamp = (
                self._first_visible_frame_timestamp
                if self._first_visible_frame_timestamp is not None
                else frame_timestamp
            )
            self._first_visible_frame_timestamp = None
            timing = _AcceptedSubtitleTiming(
                first_visible_frame_timestamp=first_visible_frame_timestamp,
                frame_timestamp=frame_timestamp,
                accepted_at=accepted_at,
                frame_acquisition_ms=self._snapshot.frame_acquisition_ms or 0.0,
                roi_preparation_ms=self._snapshot.capture_ms or 0.0,
                ocr_preprocessing_ms=result.preprocessing_ms,
                ocr_ms=result.elapsed_ms,
                stabilization_dedup_ms=stabilization_dedup_ms,
            )
            work_id = self._begin_work(request_id)
            self._snapshot = replace(
                self._snapshot,
                last_translation="",
                translation_ms=None,
                tts_ms=None,
                tts_queue_wait_ms=None,
                tts_worker_roundtrip_ms=None,
                tts_inference_ms=None,
                tts_serialization_ms=None,
                tts_worker_startup_ms=None,
                tts_worker_reused=None,
                tts_audio_duration_ms=None,
                audio_start_ms=None,
                accepted_to_audio_start_ms=None,
                confirming_frame_to_audio_start_ms=None,
                first_visible_frame_to_audio_start_ms=None,
                total_capture_to_audio_start_ms=None,
                first_visible_frame_at_monotonic=first_visible_frame_timestamp,
                confirming_frame_at_monotonic=frame_timestamp,
                accepted_at_monotonic=accepted_at,
                tts_started_at_monotonic=None,
                tts_finished_at_monotonic=None,
                playback_started_at_monotonic=None,
                tts_status="ready",
            )
            if (
                settings.subtitle_language_mode
                is NarratorSubtitleLanguageMode.POLISH
            ):
                self._emit(NarratorSessionStatus.LISTENING, detected_text=phrase)
                self._start_tts(
                    phrase,
                    source_text=phrase,
                    generation=generation,
                    work_id=work_id,
                    timing=timing,
                    translation_status="bypassed",
                    translation_ms=None,
                    last_translation="",
                )
                self._finish_ocr_request()
                return
            self._update_ocr_decision(
                request_id,
                decision="accepted_pending_translation",
            )
            self._emit(NarratorSessionStatus.TRANSLATING, detected_text=phrase)
            self._snapshot = replace(
                self._snapshot,
                translation_status="processing",
            )
            cached = self.cache.get(
                phrase,
                provider_id=self.translator.provider_id,
                profile_id=settings.translation_profile_id,
            )
            if cached is not None:
                translation = TranslationResult(
                    source_text=phrase,
                    translated_text=cached,
                    provider_id=self.translator.provider_id,
                    cached=True,
                )
                self._translation_finished_value(
                    translation,
                    generation=generation,
                    work_id=work_id,
                    timing=timing,
                )
                self._finish_ocr_request()
                return
            translation_future = self._executor.submit(
                self.translator.translate,
                phrase,
                source_language="en",
                target_language="pl",
                profile_id=settings.translation_profile_id,
            )
            self._stage_futures.add(translation_future)
            self._stage_kinds[translation_future] = "translation"
            translation_future.add_done_callback(
                lambda completed: self._translation_finished(
                    completed,
                    generation=generation,
                    work_id=work_id,
                    timing=timing,
                )
            )
            self._finish_ocr_request()

    def _translation_finished(
        self,
        future: Future[TranslationResult],
        *,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
    ) -> None:
        with self._lock:
            self._stage_futures.discard(future)
            self._stage_kinds.pop(future, None)
        try:
            result = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and work_id == self._work_id
                ):
                    self._recoverable_error(f"Translation failed: {error}")
            return
        with self._lock:
            if generation != self._generation or work_id != self._work_id:
                return
            settings = self._settings
            if settings is None:
                return
            self.cache.put(
                result.source_text,
                result.translated_text,
                provider_id=self.translator.provider_id,
                profile_id=settings.translation_profile_id,
            )
            self._translation_finished_value(
                result,
                generation=generation,
                work_id=work_id,
                timing=timing,
            )

    def _translation_finished_value(
        self,
        result: TranslationResult,
        *,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
    ) -> None:
        settings = self._settings
        if (
            settings is None
            or generation != self._generation
            or work_id != self._work_id
        ):
            return
        translated = normalize_subtitle(result.translated_text)
        if not translated:
            self._recoverable_error("Translation returned empty text")
            return
        self._start_tts(
            translated,
            source_text=result.source_text,
            generation=generation,
            work_id=work_id,
            timing=timing,
            translation_status="ready",
            translation_ms=result.elapsed_ms,
            last_translation=translated,
        )

    def _start_tts(
        self,
        spoken_text: str,
        *,
        source_text: str,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
        translation_status: str,
        translation_ms: float | None,
        last_translation: str,
    ) -> None:
        settings = self._settings
        if (
            settings is None
            or generation != self._generation
            or work_id != self._work_id
        ):
            return
        self._snapshot = replace(
            self._snapshot,
            last_translation=last_translation,
            translation_ms=translation_ms,
            translation_status=translation_status,
            tts_status="processing",
        )
        self._update_ocr_decision(
            work_id,
            decision="accepted_tts_submitted",
            tts_submitted=True,
        )
        tts_future = self._executor.submit(
            self._synthesize_timed,
            spoken_text,
            settings.voice_id,
            settings.speech_rate,
        )
        self._stage_futures.add(tts_future)
        self._stage_kinds[tts_future] = "tts"
        tts_future.add_done_callback(
            lambda completed: self._tts_finished(
                completed,
                translated=spoken_text,
                source_text=source_text,
                translation_bypassed=translation_status == "bypassed",
                generation=generation,
                work_id=work_id,
                timing=timing,
            )
        )

    def _synthesize_timed(
        self, text: str, voice_id: str, speech_rate: float
    ) -> _TtsStageResult:
        started_at = self._clock()
        audio = self.tts.synthesize(
            text,
            language="pl",
            voice_id=voice_id,
            speech_rate=speech_rate,
        )
        return _TtsStageResult(
            audio=audio,
            started_at=started_at,
            finished_at=self._clock(),
        )

    def _tts_finished(
        self,
        future: Future[_TtsStageResult],
        *,
        translated: str,
        source_text: str,
        translation_bypassed: bool,
        generation: int,
        work_id: int,
        timing: _AcceptedSubtitleTiming,
    ) -> None:
        with self._lock:
            self._stage_futures.discard(future)
            self._stage_kinds.pop(future, None)
        try:
            stage = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and work_id == self._work_id
                ):
                    self._recoverable_error(f"Speech synthesis failed: {error}")
            return
        with self._lock:
            settings = self._settings
            if (
                settings is None
                or generation != self._generation
                or work_id != self._work_id
            ):
                return
            audio = stage.audio
            self._snapshot = replace(
                self._snapshot,
                tts_ms=audio.elapsed_ms,
                tts_queue_wait_ms=audio.queue_wait_ms,
                tts_worker_roundtrip_ms=audio.worker_roundtrip_ms,
                tts_inference_ms=audio.inference_ms,
                tts_serialization_ms=audio.serialization_ms,
                tts_worker_startup_ms=audio.worker_startup_ms,
                tts_worker_reused=audio.worker_reused,
                tts_audio_duration_ms=audio.audio_duration_ms,
                tts_started_at_monotonic=stage.started_at,
                tts_finished_at_monotonic=stage.finished_at,
                tts_status="ready",
                audio_status="waiting",
            )
            logger.debug(
                "Narrator TTS total=%.1fms queue=%.1fms worker=%.1fms "
                "inference=%.1fms serialization=%.1fms startup=%.1fms "
                "reused=%s audio_duration=%.1fms chars=%d words=%d",
                audio.elapsed_ms,
                audio.queue_wait_ms,
                audio.worker_roundtrip_ms or 0.0,
                audio.inference_ms or 0.0,
                audio.serialization_ms or 0.0,
                audio.worker_startup_ms or 0.0,
                audio.worker_reused,
                audio.audio_duration_ms or 0.0,
                len(translated),
                len(translated.split()),
            )
            try:
                self._latest_audio_request_id = work_id
                self.audio.play(
                    audio,
                    volume=settings.volume,
                    request_id=work_id,
                    started_callback=lambda elapsed: self._audio_started(
                        elapsed,
                        source_text=source_text,
                        translated=translated,
                        translation_bypassed=translation_bypassed,
                        timing=timing,
                        generation=generation,
                        request_id=work_id,
                    ),
                    completed_callback=lambda: self._audio_completed(
                        generation=generation,
                        request_id=work_id,
                    ),
                    error_callback=lambda message: self._audio_failed(
                        message,
                        generation=generation,
                        request_id=work_id,
                    ),
                )
            except Exception as error:
                self._recoverable_error(f"Audio playback failed: {error}")

    def _audio_started(
        self,
        elapsed_ms: float,
        *,
        source_text: str,
        translated: str,
        translation_bypassed: bool,
        timing: _AcceptedSubtitleTiming,
        generation: int,
        request_id: int,
    ) -> None:
        with self._lock:
            if (
                generation != self._generation
                or request_id != self._latest_audio_request_id
                or self._settings is None
            ):
                return
            self._deduplicator.mark_spoken(source_text, now=self._clock())
            now = self._clock()
            self._snapshot = replace(
                self._snapshot,
                last_spoken_text=translated,
                frame_acquisition_ms=timing.frame_acquisition_ms,
                capture_ms=timing.roi_preparation_ms,
                ocr_preprocessing_ms=timing.ocr_preprocessing_ms,
                ocr_ms=timing.ocr_ms,
                stabilization_dedup_ms=timing.stabilization_dedup_ms,
                audio_start_ms=elapsed_ms,
                accepted_to_audio_start_ms=max(
                    0.0, (now - timing.accepted_at) * 1000.0
                ),
                confirming_frame_to_audio_start_ms=max(
                    0.0, (now - timing.frame_timestamp) * 1000.0
                ),
                first_visible_frame_to_audio_start_ms=max(
                    0.0,
                    (now - timing.first_visible_frame_timestamp) * 1000.0,
                ),
                total_capture_to_audio_start_ms=max(
                    0.0, (now - timing.frame_timestamp) * 1000.0
                ),
                playback_started_at_monotonic=now,
                audio_status="speaking",
            )
            logger.debug(
                "Narrator latency accepted=%r frame_acquisition=%.1fms "
                "roi=%.1fms ocr_preprocess=%.1fms ocr=%.1fms "
                "stabilization_dedup=%.1fms translation=%s tts=%s "
                "audio_queue=%.1fms accepted_to_playback=%.1fms "
                "confirming_frame_to_playback=%.1fms "
                "first_visible_frame_to_playback=%.1fms",
                source_text,
                self._snapshot.frame_acquisition_ms or 0.0,
                self._snapshot.capture_ms or 0.0,
                self._snapshot.ocr_preprocessing_ms or 0.0,
                self._snapshot.ocr_ms or 0.0,
                self._snapshot.stabilization_dedup_ms or 0.0,
                (
                    f"{self._snapshot.translation_ms:.1f}ms"
                    if self._snapshot.translation_ms is not None
                    else "bypassed"
                ),
                (
                    f"{self._snapshot.tts_ms:.1f}ms"
                    if self._snapshot.tts_ms is not None
                    else "unknown"
                ),
                elapsed_ms,
                self._snapshot.accepted_to_audio_start_ms or 0.0,
                self._snapshot.confirming_frame_to_audio_start_ms or 0.0,
                self._snapshot.first_visible_frame_to_audio_start_ms or 0.0,
            )
            self._emit(
                NarratorSessionStatus.SPEAKING,
                translated_text="" if translation_bypassed else translated,
                spoken_text=translated,
            )

    def _audio_completed(self, *, generation: int, request_id: int) -> None:
        with self._lock:
            if (
                generation != self._generation
                or request_id != self._latest_audio_request_id
                or self._settings is None
            ):
                return
            self._snapshot = replace(self._snapshot, audio_status="ready")
            if not self._request_active:
                self._emit(NarratorSessionStatus.LISTENING)

    def _audio_failed(
        self, message: str, *, generation: int, request_id: int
    ) -> None:
        with self._lock:
            if (
                generation != self._generation
                or request_id != self._latest_audio_request_id
                or self._settings is None
            ):
                return
            self._snapshot = replace(self._snapshot, audio_status="error")
            self._recoverable_error(message)

    def _begin_work(self, accepted_request_id: int) -> int:
        """Supersede only downstream work for a newly accepted subtitle.

        Raw OCR observations never reach this method, so background noise cannot
        cancel a valid narration job. Futures which have not started are removed;
        an already-running provider call is allowed to finish and its result is
        ignored. This preserves persistent model workers and never stops audio
        which has already reached the output device.
        """

        previous_work_id = self._work_id
        work_id = accepted_request_id
        stale_futures = tuple(self._stage_futures)
        self._stage_futures.clear()
        cancelled = {"translation": 0, "tts": 0}
        running = {"translation": 0, "tts": 0}
        for future in stale_futures:
            kind = self._stage_kinds.pop(future, "translation")
            if future.cancel():
                cancelled[kind] += 1
            else:
                running[kind] += 1
        if any(cancelled.values()) or any(running.values()):
            if previous_work_id:
                self._update_ocr_decision(
                    previous_work_id,
                    decision="accepted_but_tts_stale",
                )
            self._snapshot = replace(
                self._snapshot,
                stale_queued_translation=(
                    self._snapshot.stale_queued_translation
                    + cancelled["translation"]
                ),
                stale_running_translation_results=(
                    self._snapshot.stale_running_translation_results
                    + running["translation"]
                ),
                stale_queued_tts=(
                    self._snapshot.stale_queued_tts + cancelled["tts"]
                ),
                stale_running_tts_results=(
                    self._snapshot.stale_running_tts_results + running["tts"]
                ),
            )
            logger.debug(
                "Narrator accepted newer subtitle; translation queued=%d "
                "running=%d; TTS queued=%d running=%d",
                cancelled["translation"],
                running["translation"],
                cancelled["tts"],
                running["tts"],
            )
        self._work_id = accepted_request_id
        return work_id

    def _append_ocr_decision(self, observation: OcrDecisionObservation) -> None:
        history = (*self._snapshot.ocr_decision_history, observation)
        self._snapshot = replace(
            self._snapshot,
            ocr_decision_history=history[-OCR_DECISION_HISTORY_LIMIT:],
        )

    def _update_ocr_decision(
        self,
        observation_id: int,
        **changes: object,
    ) -> None:
        history = list(self._snapshot.ocr_decision_history)
        for index in range(len(history) - 1, -1, -1):
            if history[index].observation_id != observation_id:
                continue
            history[index] = replace(history[index], **changes)
            latest = bool(history and history[-1].observation_id == observation_id)
            self._snapshot = replace(
                self._snapshot,
                ocr_decision_history=tuple(history),
                last_ocr_gate_decision=(
                    str(changes["decision"])
                    if latest and "decision" in changes
                    else self._snapshot.last_ocr_gate_decision
                ),
            )
            return

    def _finish_ocr_request(self) -> None:
        self._request_active = False
        if self._pending_frame is None:
            if (
                self._snapshot.status is NarratorSessionStatus.SPEAKING
                and self._snapshot.audio_status == "ready"
            ):
                self._emit(NarratorSessionStatus.LISTENING)
            return
        frame = self._pending_frame
        stabilization_ms = self._pending_frame_stabilization_ms
        visual_decision = self._pending_frame_visual_decision
        self._pending_frame = None
        self._pending_frame_stabilization_ms = 0.0
        self._pending_frame_visual_decision = ""
        self._start_ocr(
            frame,
            stabilization_ms=stabilization_ms,
            visual_decision=visual_decision,
        )

    def _capture_state_changed(self, state: CaptureState, message: str) -> None:
        with self._lock:
            if self._settings is None:
                return
            self._snapshot = replace(self._snapshot, capture_state=state.value)
            mapped = {
                CaptureState.PERMISSION_REQUIRED: NarratorSessionStatus.SELECTING_SOURCE,
                CaptureState.SELECTING_SOURCE: NarratorSessionStatus.SELECTING_SOURCE,
                CaptureState.STARTING: NarratorSessionStatus.STARTING,
                CaptureState.ACTIVE: NarratorSessionStatus.LISTENING,
            }.get(state)
            if mapped is not None:
                self._emit(mapped, message=message)
                return
            if state in {CaptureState.CANCELLED, CaptureState.PERMISSION_DENIED}:
                self._fail(message or "Screen capture permission was cancelled")
            elif state in {
                CaptureState.UNAVAILABLE,
                CaptureState.SOURCE_LOST,
                CaptureState.ERROR,
            }:
                self._fail(message or "Screen capture stopped")

    def _emit(
        self,
        status: NarratorSessionStatus,
        message: str = "",
        *,
        detected_text: str = "",
        translated_text: str = "",
        spoken_text: str = "",
    ) -> None:
        self._snapshot = replace(
            self._snapshot,
            status=status,
            message=message,
            last_detected_text=(
                detected_text or self._snapshot.last_detected_text
            ),
            last_translation=(
                translated_text or self._snapshot.last_translation
            ),
            last_spoken_text=spoken_text or self._snapshot.last_spoken_text,
            audio_supersessions=max(
                0,
                int(getattr(self.audio, "superseded_count", 0))
                - self._audio_supersession_baseline,
            ),
        )
        timings = {
            name: value
            for name, value in {
                "frameAcquisition": self._snapshot.frame_acquisition_ms,
                "capture": self._snapshot.capture_ms,
                "ocrPreprocessing": self._snapshot.ocr_preprocessing_ms,
                "ocr": self._snapshot.ocr_ms,
                "stabilizationDedup": self._snapshot.stabilization_dedup_ms,
                "translation": self._snapshot.translation_ms,
                "tts": self._snapshot.tts_ms,
                "audioStart": self._snapshot.audio_start_ms,
                "acceptedToAudioStart": (
                    self._snapshot.accepted_to_audio_start_ms
                ),
                "confirmingFrameToAudioStart": (
                    self._snapshot.confirming_frame_to_audio_start_ms
                ),
                "firstVisibleFrameToAudioStart": (
                    self._snapshot.first_visible_frame_to_audio_start_ms
                ),
                "captureToAudioStart": (
                    self._snapshot.total_capture_to_audio_start_ms
                ),
                "firstVisibleFrameAt": (
                    self._snapshot.first_visible_frame_at_monotonic
                ),
                "confirmingFrameAt": (
                    self._snapshot.confirming_frame_at_monotonic
                ),
                "acceptedAt": self._snapshot.accepted_at_monotonic,
                "ttsStartedAt": self._snapshot.tts_started_at_monotonic,
                "ttsFinishedAt": self._snapshot.tts_finished_at_monotonic,
                "playbackStartedAt": (
                    self._snapshot.playback_started_at_monotonic
                ),
            }.items()
            if value is not None
        }
        self._events.put(
            NarratorEvent(
                session_id=self._session_id,
                game_key=self._snapshot.game_key,
                generation=self._generation,
                status=status,
                message=message,
                detected_text=detected_text,
                translated_text=translated_text,
                spoken_text=spoken_text,
                timings=timings,
            )
        )

    def _recoverable_error(self, message: str) -> None:
        logger.warning(
            "Narrator recoverable failure session=%s game=%s: %s",
            self._session_id,
            self._snapshot.game_key,
            message,
        )
        if message.startswith("OCR failed"):
            self._snapshot = replace(self._snapshot, ocr_status="error")
        elif message.startswith("Translation"):
            self._snapshot = replace(self._snapshot, translation_status="error")
        elif message.startswith("Speech synthesis"):
            self._snapshot = replace(self._snapshot, tts_status="error")
        elif message.startswith("Audio") or message.startswith("Narrator audio"):
            self._snapshot = replace(self._snapshot, audio_status="error")
        self._emit(NarratorSessionStatus.LISTENING, message=message)

    def _fail(self, message: str) -> None:
        self._generation += 1
        self._request_id += 1
        self._work_id += 1
        self._latest_audio_request_id = 0
        if self._ocr_future is not None:
            self._ocr_future.cancel()
        if self._ocr_prepare_future is not None:
            self._ocr_prepare_future.cancel()
            self._ocr_prepare_future = None
        if self._tts_prepare_future is not None:
            self._tts_prepare_future.cancel()
            self._tts_prepare_future = None
        for future in tuple(self._stage_futures):
            future.cancel()
        self._stage_futures.clear()
        self._stage_kinds.clear()
        self._cancel_provider_work()
        self._snapshot = replace(
            self._snapshot,
            status=NarratorSessionStatus.ERROR,
            message=message,
            generation=self._generation,
        )
        self._events.put(
            NarratorEvent(
                session_id=self._session_id,
                game_key=self._snapshot.game_key,
                generation=self._generation,
                status=NarratorSessionStatus.ERROR,
                message=message,
            )
        )
        self.capture.stop()
        self.audio.stop()
        self._request_active = False
        self._pending_frame = None
        self._pending_frame_stabilization_ms = 0.0
        self._pending_frame_visual_decision = ""
        self._first_visible_frame_timestamp = None
        self._settings = None

    def _cancel_provider_work(self) -> None:
        for provider in (self.ocr, self.translator, self.tts):
            cancel = getattr(provider, "cancel", None)
            if callable(cancel):
                cancel()


__all__ = [
    "GameActivityProvider",
    "NarratorAudioOutput",
    "NarratorPipeline",
    "OcrProvider",
    "PhraseDeduplicator",
    "OcrGateObservation",
    "SubtitleRegionStabilizer",
    "SubtitleTextGate",
    "SubtitleSource",
    "TranslationProvider",
    "TtsProvider",
    "UnavailableAudioOutput",
    "UnavailableOcrProvider",
    "UnavailableTranslationProvider",
    "UnavailableTtsProvider",
    "crop_frame",
    "normalize_subtitle",
    "subtitle_identity",
]
