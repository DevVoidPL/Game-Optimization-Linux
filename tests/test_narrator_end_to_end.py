from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureSourceType,
    CaptureState,
    NarratorGameSettings,
    NarratorSessionStatus,
    NormalizedRect,
    OcrResult,
    PcmAudio,
    TranslationResult,
)
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.narrator_capture import (
    CaptureCapabilities,
    CaptureRequest,
)
from game_optimization_linux.services.narrator_components import (
    NarratorComponentManager,
)
from game_optimization_linux.services.narrator_persistence import (
    NarratorSettingsRepository,
    TranslationCache,
)
from game_optimization_linux.services.narrator_pipeline import NarratorPipeline


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _ManualExecutor:
    def __init__(self) -> None:
        self.jobs: list[
            tuple[Future[Any], Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = []

    def submit(
        self, function: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Future[Any]:
        future: Future[Any] = Future()
        self.jobs.append((future, function, args, kwargs))
        return future

    def mark_first_running(self) -> None:
        assert self.jobs
        assert self.jobs[0][0].set_running_or_notify_cancel()

    def run_next(self) -> None:
        future, function, args, kwargs = self.jobs.pop(0)
        if future.cancelled():
            return
        if not future.running() and not future.set_running_or_notify_cancel():
            return
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)


class _Capture:
    provider_id = "test-capture"

    def __init__(self) -> None:
        self.requests: list[CaptureRequest] = []
        self.frame_callback: Callable[[CaptureFrame], None] | None = None
        self.stop_calls = 0

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            available=True,
            portal_version=6,
            source_types=frozenset({CaptureSourceType.WINDOW}),
            persistence_supported=True,
        )

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: Callable[[CaptureFrame], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        self.requests.append(request)
        self.frame_callback = frame_callback
        state_callback(CaptureState.ACTIVE, "")

    def stop(self) -> None:
        self.stop_calls += 1


class _Activity:
    def __init__(self) -> None:
        self.active = True

    def is_active(self, game_key: str) -> bool:
        del game_key
        return self.active


class _Ocr:
    provider_id = "test-ocr"
    available = True

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
        assert language == "en"
        return OcrResult(
            text=f"phrase {frame.pixels[0]}",
            confidence=0.95,
            provider_id=self.provider_id,
            elapsed_ms=4.0,
        )


class _Translator:
    provider_id = "test-translator-v1"
    available = True
    profile_ids = ("small", "fast")
    default_profile_id = "small"

    def __init__(self, *, fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.values: list[tuple[str, str, str, str]] = []
        self.cancel_calls = 0

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult:
        self.values.append((text, source_language, target_language, profile_id))
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("temporary translator failure")
        return TranslationResult(
            source_text=text,
            translated_text=f"polski {text}",
            source_language=source_language,
            target_language=target_language,
            provider_id=self.provider_id,
            elapsed_ms=8.0,
        )

    def cancel(self) -> None:
        self.cancel_calls += 1


class _UnavailableTranslator(_Translator):
    available = False


class _Tts:
    provider_id = "test-tts-v1"
    available = True
    available_voice_ids = ("voice-pl", "voice-alt")
    default_voice_id = "voice-pl"

    def __init__(self, *, fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.values: list[tuple[str, str, str, float]] = []
        self.cancel_calls = 0

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio:
        self.values.append((text, language, voice_id, speech_rate))
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("temporary TTS failure")
        return PcmAudio(
            samples=b"\0\0",
            sample_rate=22050,
            channels=1,
            provider_id=self.provider_id,
            elapsed_ms=12.0,
        )

    def cancel(self) -> None:
        self.cancel_calls += 1


class _Audio:
    provider_id = "test-audio"
    available = True

    def __init__(self) -> None:
        self.played: list[int] = []
        self.stop_calls = 0

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
        del audio, volume, error_callback
        self.played.append(request_id)
        started_callback(1.5)
        completed_callback()

    def stop(self) -> None:
        self.stop_calls += 1


class _AlwaysStable:
    def consider(
        self,
        frame: CaptureFrame,
        *,
        threshold: float,
        stabilization_seconds: float,
    ) -> CaptureFrame:
        del threshold, stabilization_seconds
        return frame


def _settings(**changes: object) -> NarratorGameSettings:
    values = NarratorGameSettings(
        game_key="292030",
        enabled=True,
        ocr_provider_id="test-ocr",
        translation_provider_id="test-translator-v1",
        translation_profile_id="small",
        tts_provider_id="test-tts-v1",
        voice_id="voice-pl",
        subtitle_region=NormalizedRect(x=0, y=0, width=1, height=1),
    )
    return replace(values, **changes)


def _frame(snapshot: object, value: int, timestamp: float) -> CaptureFrame:
    return CaptureFrame(
        session_id=str(getattr(snapshot, "session_id")),
        generation=int(getattr(snapshot, "generation")),
        timestamp_monotonic=timestamp,
        width=2,
        height=2,
        stride=2,
        pixel_format="gray8",
        pixels=bytes([value] * 4),
    )


def _pipeline(
    tmp_path: Path,
    executor: _ManualExecutor,
    *,
    translator: _Translator | None = None,
    tts: _Tts | None = None,
) -> tuple[NarratorPipeline, _Capture, _Activity, _Translator, _Tts, _Audio]:
    capture = _Capture()
    activity = _Activity()
    selected_translator = translator or _Translator()
    selected_tts = tts or _Tts()
    audio = _Audio()
    pipeline = NarratorPipeline(
        capture,
        _Ocr(),
        selected_translator,
        selected_tts,
        audio,
        activity,
        TranslationCache(tmp_path / "translations.sqlite3"),
        executor=executor,  # type: ignore[arg-type]
    )
    return pipeline, capture, activity, selected_translator, selected_tts, audio


def _confirm_phrase(
    pipeline: NarratorPipeline,
    executor: _ManualExecutor,
    snapshot: object,
    value: int,
    timestamp: float,
) -> None:
    pipeline.submit_frame(_frame(snapshot, value, timestamp))
    executor.run_next()
    pipeline.submit_frame(_frame(snapshot, value, timestamp + 0.2))
    executor.run_next()


def test_pipeline_cache_hit_skips_translation_and_uses_language_namespace(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    pipeline.cache.put(
        "phrase 7",
        "fraza z pamięci",
        provider_id=translator.provider_id,
        profile_id="small",
        source_language="en",
        target_language="pl",
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    _confirm_phrase(pipeline, executor, snapshot, 7, 1.0)
    executor.run_next()  # TTS.

    assert translator.values == []
    assert tts.values == [("fraza z pamięci", "pl", "voice-pl", 1.0)]
    assert audio.played == [2]
    assert pipeline.snapshot.last_translation == "fraza z pamięci"

    assert (
        pipeline.cache.get(
            "phrase 7",
            provider_id="test-translator-v2",
            profile_id="small",
            source_language="en",
            target_language="pl",
        )
        is None
    )
    assert (
        pipeline.cache.get(
            "phrase 7",
            provider_id=translator.provider_id,
            profile_id="small",
            source_language="pl",
            target_language="en",
        )
        is None
    )


def test_translation_failure_is_recoverable_for_a_later_subtitle(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    translator = _Translator(fail_count=1)
    pipeline, _capture, _activity, _translator, tts, audio = _pipeline(
        tmp_path, executor, translator=translator
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    _confirm_phrase(pipeline, executor, snapshot, 1, 1.0)
    executor.run_next()

    assert pipeline.snapshot.status is NarratorSessionStatus.LISTENING
    assert pipeline.snapshot.translation_status == "error"
    assert "Translation failed" in pipeline.snapshot.message

    _confirm_phrase(pipeline, executor, snapshot, 2, 2.0)
    executor.run_next()
    executor.run_next()

    assert pipeline.snapshot.status is NarratorSessionStatus.LISTENING
    assert pipeline.snapshot.translation_status == "ready"
    assert pipeline.snapshot.last_translation == "polski phrase 2"
    assert tts.values[-1][0] == "polski phrase 2"
    assert audio.played == [4]


def test_tts_failure_is_recoverable_for_a_later_subtitle(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    tts = _Tts(fail_count=1)
    pipeline, _capture, _activity, translator, _tts, audio = _pipeline(
        tmp_path, executor, tts=tts
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    _confirm_phrase(pipeline, executor, snapshot, 3, 1.0)
    executor.run_next()
    executor.run_next()

    assert pipeline.snapshot.status is NarratorSessionStatus.LISTENING
    assert pipeline.snapshot.tts_status == "error"
    assert "Speech synthesis failed" in pipeline.snapshot.message

    _confirm_phrase(pipeline, executor, snapshot, 4, 2.0)
    executor.run_next()
    executor.run_next()

    assert pipeline.snapshot.status is NarratorSessionStatus.LISTENING
    assert pipeline.snapshot.tts_status == "ready"
    assert translator.values[-1][0] == "phrase 4"
    assert audio.played == [4]


def test_disable_cancels_in_flight_translation_and_rejects_stale_result(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    _confirm_phrase(pipeline, executor, snapshot, 5, 1.0)
    executor.mark_first_running()

    pipeline.stop("Narrator disabled")
    executor.run_next()

    assert pipeline.snapshot.status is NarratorSessionStatus.STOPPED
    assert translator.cancel_calls == 1
    assert tts.cancel_calls == 1
    assert capture.stop_calls == 1
    assert audio.stop_calls == 1
    assert tts.values == []
    assert audio.played == []


def test_game_exit_cancels_in_flight_tts_and_rejects_stale_audio(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, activity, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    now = [10.0]
    pipeline._clock = lambda: now[0]
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    _confirm_phrase(pipeline, executor, snapshot, 6, 1.0)
    executor.run_next()  # Translation queues TTS.
    executor.mark_first_running()

    activity.active = False
    pipeline.poll_game_activity()
    now[0] = 12.1
    pipeline.poll_game_activity()
    executor.run_next()

    assert pipeline.snapshot.status is NarratorSessionStatus.STOPPED
    assert pipeline.snapshot.message == "The game exited"
    assert translator.cancel_calls == 1
    assert tts.cancel_calls == 1
    assert capture.stop_calls == 1
    assert audio.stop_calls == 1
    assert audio.played == []


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"ocr_provider_id": "different-ocr"}, "selected OCR provider"),
        (
            {"translation_provider_id": "different-translator"},
            "selected translation provider",
        ),
        ({"translation_profile_id": "unknown"}, "translation profile"),
        ({"tts_provider_id": "different-tts"}, "selected speech provider"),
        ({"voice_id": "unknown"}, "Polish voice"),
    ),
)
def test_pipeline_rejects_unavailable_selected_provider_profile_or_voice(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, _translator, _tts, _audio = _pipeline(
        tmp_path, executor
    )

    with pytest.raises(RuntimeError, match=message):
        pipeline.start(_settings(**changes))

    assert capture.requests == []


def test_pipeline_resolves_empty_profile_and_voice_to_provider_defaults(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(
        _settings(
            translation_profile_id="",
            voice_id="",
        )
    )
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    _confirm_phrase(pipeline, executor, snapshot, 8, 1.0)
    executor.run_next()
    executor.run_next()

    assert translator.values == [("phrase 8", "en", "pl", "small")]
    assert tts.values == [("polski phrase 8", "pl", "voice-pl", 1.0)]
    assert audio.played == [2]


def test_controller_blocks_start_when_a_required_runtime_component_is_missing(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, _translator, _tts, _audio = _pipeline(
        tmp_path, executor, translator=_UnavailableTranslator()
    )
    repository = NarratorSettingsRepository(tmp_path / "games")
    controller = AppController(
        game_provider=DemoGameProvider(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=repository,
        narrator_component_manager=NarratorComponentManager(tmp_path / "components"),
        narrator_pipeline=pipeline,
        auto_refresh=False,
    )
    try:
        game_id = controller.games[0]["id"]
        game = controller._resolve_game(game_id, show_error=False)
        assert game is not None
        game_key = controller._narrator_controller._game_key(game)
        repository.save(replace(NarratorGameSettings.default(game_key), enabled=True))

        state = controller.getNarratorSessionState(game_id)
        assert state["canStart"] is False
        assert "translation" in state["missingRequirements"]
        assert controller.startNarrator(game_id) is False
        assert capture.requests == []
    finally:
        controller.shutdown()
