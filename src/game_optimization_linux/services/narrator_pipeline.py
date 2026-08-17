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
    NarratorSessionSnapshot,
    NarratorSessionStatus,
    NarratorSourceMode,
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
    """Normalize case and punctuation for temporal OCR comparisons."""
    normalized = normalize_subtitle(text).casefold()
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
    ) -> OcrGateObservation:
        raw = str(text)
        filtered = normalize_subtitle(raw)
        reason = self._validate(filtered, confidence)
        if reason:
            self._clear(no_subtitle=True)
            return OcrGateObservation(raw, filtered, confidence, reason)

        identity = subtitle_identity(filtered)
        if self._accepted_identity and self._similarity(
            identity, self._accepted_identity
        ) >= self.similarity_threshold:
            self._reset_candidate()
            return OcrGateObservation(raw, filtered, confidence, "duplicate")

        within_window = (
            self._candidate_identity
            and now - self._candidate_since <= self.stability_window_seconds
        )
        similar = within_window and self._similarity(
            identity, self._candidate_identity
        ) >= self.similarity_threshold
        if not similar:
            self._candidate_text = filtered
            self._candidate_identity = identity
            self._candidate_confidence = confidence if confidence is not None else -1.0
            self._candidate_count = 1
            self._candidate_since = now
            self._needs_confirmation = True
            return OcrGateObservation(
                raw, filtered, confidence, "unstable", needs_confirmation=True
            )

        self._candidate_count += 1
        if confidence is not None and confidence > self._candidate_confidence:
            self._candidate_text = filtered
            self._candidate_identity = identity
            self._candidate_confidence = confidence
        if self._candidate_count < self.required_observations:
            self._needs_confirmation = True
            return OcrGateObservation(
                raw, filtered, confidence, "unstable", needs_confirmation=True
            )

        accepted = self._candidate_text
        self._accepted_identity = self._candidate_identity
        self._reset_candidate()
        return OcrGateObservation(raw, filtered, confidence, "", accepted)

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
        self._needs_confirmation = False

    @staticmethod
    def _similarity(first: str, second: str) -> float:
        if not first or not second:
            return 0.0
        if first == second:
            return 1.0
        return SequenceMatcher(None, first, second, autojunk=False).ratio()

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
        self._latest_audio_request_id = 0
        self._stabilizer = SubtitleRegionStabilizer()
        self._text_gate = SubtitleTextGate()
        self._deduplicator = PhraseDeduplicator()
        self._ocr_rejection_counts: dict[str, int] = {}
        self._last_ocr_diagnostic: tuple[str, str, float | None] | None = None
        self._ocr_future: Future[OcrResult] | None = None
        self._request_active = False
        self._pending_frame: CaptureFrame | None = None
        self._last_sampled_at: float | None = None
        self._inactive_since: float | None = None
        self._stage_futures: set[Future[object]] = set()
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

    def missing_requirements(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.capture.capabilities().available:
            missing.append("capture")
        if not self.ocr.available:
            missing.append("ocr")
        if not self.translator.available:
            missing.append("translation")
        if not self.tts.available:
            missing.append("tts")
        if not self.audio.available:
            missing.append("audio")
        return tuple(missing)

    def full_narration_available(self) -> bool:
        return bool(
            self.translator.available and self.tts.available and self.audio.available
        )

    def start(self, settings: NarratorGameSettings) -> NarratorSessionSnapshot:
        with self._lock:
            if self.active:
                raise RuntimeError("A narrator session is already active")
            if not settings.enabled:
                raise RuntimeError("Narrator is disabled for this game")
            if settings.source_mode is NarratorSourceMode.ADAPTER:
                raise RuntimeError("No game subtitle adapter is available")
            settings = self._validated_settings(settings)
            missing = self.missing_requirements()
            if missing:
                raise RuntimeError(
                    "Narrator components are unavailable: " + ", ".join(missing)
                )
            activity = self.activity.is_active(settings.game_key)
            if activity is False:
                raise RuntimeError("The selected game is not running")
            self._generation += 1
            self._request_id = 0
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
            self._request_active = False
            self._pending_frame = None
            self._last_sampled_at = None
            self._inactive_since = None
            self._snapshot = NarratorSessionSnapshot(
                session_id=self._session_id,
                game_key=settings.game_key,
                status=NarratorSessionStatus.STARTING,
                generation=self._generation,
                capture_state=CaptureState.STARTING.value,
                ocr_status="ready",
                translation_status=(
                    "ready" if self.translator.available else "component_missing"
                ),
                tts_status="ready" if self.tts.available else "component_missing",
                audio_status="ready" if self.audio.available else "unavailable",
            )
            self._emit(NarratorSessionStatus.STARTING)
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
        selected_providers = (
            ("OCR", settings.ocr_provider_id, self.ocr.provider_id),
            (
                "translation",
                settings.translation_provider_id,
                self.translator.provider_id,
            ),
            ("speech", settings.tts_provider_id, self.tts.provider_id),
        )
        for label, selected, active in selected_providers:
            if selected and selected != active:
                raise RuntimeError(
                    f"The selected {label} provider is not available: {selected}"
                )

        profiles = tuple(
            str(value) for value in getattr(self.translator, "profile_ids", ())
        )
        profile_id = settings.translation_profile_id or str(
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
                settings.translation_provider_id or self.translator.provider_id
            ),
            translation_profile_id=profile_id,
            tts_provider_id=settings.tts_provider_id or self.tts.provider_id,
            voice_id=voice_id,
        )

    def stop(self, message: str = "") -> NarratorSessionSnapshot:
        with self._lock:
            if not self.active:
                return self._snapshot
            self._generation += 1
            self._request_id += 1
            self._latest_audio_request_id = 0
            self._request_active = False
            self._pending_frame = None
            if self._ocr_future is not None:
                self._ocr_future.cancel()
            for future in tuple(self._stage_futures):
                future.cancel()
            self._stage_futures.clear()
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
                ocr_status="ready" if self.ocr.available else "component_missing",
                translation_status=(
                    "ready" if self.translator.available else "component_missing"
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
            settings = self._settings
            if (
                settings is None
                or frame.session_id != self._session_id
                or frame.generation != self._generation
            ):
                return
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
                )
                return
            self._last_sampled_at = frame.timestamp_monotonic
            started = self._clock()
            try:
                cropped = crop_frame(frame, settings.subtitle_region)
            except Exception as error:
                self._recoverable_error(f"Could not crop the subtitle region: {error}")
                return
            capture_ms = max(0.0, (self._clock() - started) * 1000.0)
            if self._text_gate.needs_confirmation:
                stable = cropped
            else:
                stable = self._stabilizer.consider(
                    cropped,
                    threshold=settings.visual_change_threshold,
                    stabilization_seconds=settings.stabilization_ms / 1000.0,
                )
            if stable is None:
                self._snapshot = replace(
                    self._snapshot,
                    capture_width=frame.width,
                    capture_height=frame.height,
                )
                return
            self._snapshot = replace(
                self._snapshot,
                capture_ms=capture_ms,
                capture_width=frame.width,
                capture_height=frame.height,
            )
            if self._request_active:
                if self._pending_frame is not None:
                    self._snapshot = replace(
                        self._snapshot,
                        dropped_frames=self._snapshot.dropped_frames + 1,
                    )
                self._pending_frame = stable
                return
            self._start_ocr(stable)

    def drain_events(self) -> list[NarratorEvent]:
        events: list[NarratorEvent] = []
        while not self._events.empty():
            events.append(self._events.get())
        return events

    def shutdown(self) -> None:
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

    def _start_ocr(self, frame: CaptureFrame) -> None:
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
        )
        self._emit(NarratorSessionStatus.OCR)
        future = self._executor.submit(self.ocr.recognize, frame, language="en")
        self._ocr_future = future
        future.add_done_callback(
            lambda completed: self._ocr_finished(
                completed,
                generation=generation,
                request_id=request_id,
                frame_timestamp=frame.timestamp_monotonic,
            )
        )

    def _ocr_finished(
        self,
        future: Future[OcrResult],
        *,
        generation: int,
        request_id: int,
        frame_timestamp: float,
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
                    self._finish_request()
            return
        with self._lock:
            settings = self._settings
            if settings is None or generation != self._generation:
                return
            now = self._clock()
            observation = self._text_gate.observe(
                result.text,
                result.confidence,
                now=now,
            )
            rejection_reason = observation.rejection_reason
            if rejection_reason:
                self._ocr_rejection_counts[rejection_reason] = (
                    self._ocr_rejection_counts.get(rejection_reason, 0) + 1
                )
            diagnostic = (
                observation.filtered_text,
                rejection_reason,
                observation.confidence,
            )
            if diagnostic != self._last_ocr_diagnostic:
                logger.debug(
                    "Narrator OCR raw=%r filtered=%r confidence=%s rejection=%s accepted=%r",
                    observation.raw_text,
                    observation.filtered_text,
                    (
                        f"{observation.confidence:.3f}"
                        if observation.confidence is not None
                        else "none"
                    ),
                    rejection_reason or "none",
                    observation.accepted_text,
                )
                self._last_ocr_diagnostic = diagnostic

            if rejection_reason not in {"unstable", "duplicate"}:
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
            self._snapshot = replace(
                self._snapshot,
                last_detected_text=(
                    phrase or self._snapshot.last_detected_text
                ),
                ocr_ms=result.elapsed_ms,
                total_capture_to_text_ms=max(
                    0.0, (self._clock() - frame_timestamp) * 1000.0
                ),
                ocr_confidence=result.confidence,
                last_raw_ocr_text=observation.raw_text,
                last_filtered_ocr_text=observation.filtered_text,
                last_ocr_rejection_reason=rejection_reason,
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
                self._emit(NarratorSessionStatus.LISTENING)
                self._finish_request()
                return
            if not self.full_narration_available():
                self._emit(NarratorSessionStatus.LISTENING, detected_text=phrase)
                self._finish_request()
                return
            self._emit(NarratorSessionStatus.TRANSLATING, detected_text=phrase)
            self._snapshot = replace(
                self._snapshot,
                last_translation="",
                translation_ms=None,
                tts_ms=None,
                audio_start_ms=None,
                total_capture_to_audio_start_ms=None,
                translation_status="processing",
                tts_status="ready",
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
                    request_id=request_id,
                    frame_timestamp=frame_timestamp,
                )
                return
            translation_future = self._executor.submit(
                self.translator.translate,
                phrase,
                source_language="en",
                target_language="pl",
                profile_id=settings.translation_profile_id,
            )
            self._stage_futures.add(translation_future)
            translation_future.add_done_callback(
                lambda completed: self._translation_finished(
                    completed,
                    generation=generation,
                    request_id=request_id,
                    frame_timestamp=frame_timestamp,
                )
            )

    def _translation_finished(
        self,
        future: Future[TranslationResult],
        *,
        generation: int,
        request_id: int,
        frame_timestamp: float,
    ) -> None:
        with self._lock:
            self._stage_futures.discard(future)
        try:
            result = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and request_id == self._request_id
                ):
                    self._recoverable_error(f"Translation failed: {error}")
                    self._finish_request()
            return
        with self._lock:
            if generation != self._generation or request_id != self._request_id:
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
                request_id=request_id,
                frame_timestamp=frame_timestamp,
            )

    def _translation_finished_value(
        self,
        result: TranslationResult,
        *,
        generation: int,
        request_id: int,
        frame_timestamp: float,
    ) -> None:
        settings = self._settings
        if (
            settings is None
            or generation != self._generation
            or request_id != self._request_id
        ):
            return
        translated = normalize_subtitle(result.translated_text)
        if not translated:
            self._recoverable_error("Translation returned empty text")
            self._finish_request()
            return
        self._snapshot = replace(
            self._snapshot,
            last_translation=translated,
            translation_ms=result.elapsed_ms,
            translation_status="ready",
            tts_status="processing",
        )
        tts_future = self._executor.submit(
            self.tts.synthesize,
            translated,
            language="pl",
            voice_id=settings.voice_id,
            speech_rate=settings.speech_rate,
        )
        self._stage_futures.add(tts_future)
        tts_future.add_done_callback(
            lambda completed: self._tts_finished(
                completed,
                translated=translated,
                source_text=result.source_text,
                generation=generation,
                request_id=request_id,
                frame_timestamp=frame_timestamp,
            )
        )

    def _tts_finished(
        self,
        future: Future[PcmAudio],
        *,
        translated: str,
        source_text: str,
        generation: int,
        request_id: int,
        frame_timestamp: float,
    ) -> None:
        with self._lock:
            self._stage_futures.discard(future)
        try:
            audio = future.result()
        except Exception as error:
            with self._lock:
                if (
                    generation == self._generation
                    and request_id == self._request_id
                ):
                    self._recoverable_error(f"Speech synthesis failed: {error}")
                    self._finish_request()
            return
        with self._lock:
            settings = self._settings
            if (
                settings is None
                or generation != self._generation
                or request_id != self._request_id
            ):
                return
            self._snapshot = replace(
                self._snapshot,
                tts_ms=audio.elapsed_ms,
                tts_status="ready",
                audio_status="waiting",
            )
            try:
                self._latest_audio_request_id = request_id
                self.audio.play(
                    audio,
                    volume=settings.volume,
                    request_id=request_id,
                    started_callback=lambda elapsed: self._audio_started(
                        elapsed,
                        source_text=source_text,
                        translated=translated,
                        frame_timestamp=frame_timestamp,
                        generation=generation,
                        request_id=request_id,
                    ),
                    completed_callback=lambda: self._audio_completed(
                        generation=generation,
                        request_id=request_id,
                    ),
                    error_callback=lambda message: self._audio_failed(
                        message,
                        generation=generation,
                        request_id=request_id,
                    ),
                )
                self._finish_request()
            except Exception as error:
                self._recoverable_error(f"Audio playback failed: {error}")
                self._finish_request()

    def _audio_started(
        self,
        elapsed_ms: float,
        *,
        source_text: str,
        translated: str,
        frame_timestamp: float,
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
            self._snapshot = replace(
                self._snapshot,
                last_spoken_text=translated,
                audio_start_ms=elapsed_ms,
                total_capture_to_audio_start_ms=max(
                    0.0, (self._clock() - frame_timestamp) * 1000.0
                ),
                audio_status="speaking",
            )
            self._emit(
                NarratorSessionStatus.SPEAKING,
                translated_text=translated,
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

    def _finish_request(self) -> None:
        self._request_active = False
        if self._pending_frame is None:
            if (
                self._snapshot.status is NarratorSessionStatus.SPEAKING
                and self._snapshot.audio_status == "ready"
            ):
                self._emit(NarratorSessionStatus.LISTENING)
            return
        frame = self._pending_frame
        self._pending_frame = None
        self._start_ocr(frame)

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
        )
        timings = {
            name: value
            for name, value in {
                "capture": self._snapshot.capture_ms,
                "ocr": self._snapshot.ocr_ms,
                "translation": self._snapshot.translation_ms,
                "tts": self._snapshot.tts_ms,
                "audioStart": self._snapshot.audio_start_ms,
                "captureToAudioStart": (
                    self._snapshot.total_capture_to_audio_start_ms
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
        self._latest_audio_request_id = 0
        if self._ocr_future is not None:
            self._ocr_future.cancel()
        for future in tuple(self._stage_futures):
            future.cancel()
        self._stage_futures.clear()
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
