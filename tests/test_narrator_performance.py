"""Deterministic concurrency and telemetry regressions for Narrator TTS."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import logging
from pathlib import Path
from threading import Event

from game_optimization_linux.models.narrator import (
    NarratorSubtitleLanguageMode,
    OcrResult,
)
from game_optimization_linux.services.narrator_pipeline import (
    PhraseDeduplicator,
    SubtitleTextGate,
)
from test_narrator_foundation import (
    _AlwaysStable,
    _ManualExecutor,
    _Tts,
    _frame,
    _pipeline,
    _settings,
)


class _PolishOcr:
    provider_id = "test-ocr"
    available = True

    def __init__(self, target_calls: int = 0) -> None:
        self.values: list[int] = []
        self.reached = Event()
        self.target_calls = target_calls

    def recognize(self, frame, *, language: str) -> OcrResult:
        assert language == "pl"
        value = frame.pixels[0]
        self.values.append(value)
        if self.target_calls and len(self.values) >= self.target_calls:
            self.reached.set()
        return OcrResult(
            text=f"phrase {value}",
            confidence=0.95,
            provider_id=self.provider_id,
        )


class _BlockingTts(_Tts):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.finished = Event()

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ):
        self.entered.set()
        if not self.release.wait(timeout=5.0):
            raise RuntimeError("test did not release blocking TTS")
        result = super().synthesize(
            text,
            language=language,
            voice_id=voice_id,
            speech_rate=speech_rate,
        )
        self.finished.set()
        return result


def _polish_settings():
    return replace(
        _settings(),
        subtitle_language_mode=NarratorSubtitleLanguageMode.POLISH,
    )


def test_blocking_tts_does_not_block_submit_frame(tmp_path: Path) -> None:
    shared = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-narrator-ocr")
    tts_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-narrator-tts")
    pipeline, *_providers = _pipeline(tmp_path, shared)  # type: ignore[arg-type]
    ocr = _PolishOcr()
    tts = _BlockingTts()
    pipeline.ocr = ocr
    pipeline.tts = tts
    pipeline._tts_executor = tts_executor
    snapshot = pipeline.start(_polish_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    try:
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=1,
                timestamp=1.0,
            )
        )
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=1,
                timestamp=1.2,
            )
        )
        assert tts.entered.wait(timeout=5.0)
        assert tts.release.is_set() is False

        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=2,
                timestamp=1.4,
            )
        )
        assert tts.release.is_set() is False
        assert pipeline.active is True
    finally:
        tts.release.set()
        assert tts.finished.wait(timeout=5.0)
        pipeline.shutdown()
        tts_executor.shutdown(wait=True, cancel_futures=True)
        shared.shutdown(wait=True, cancel_futures=True)


def test_ocr_continues_while_tts_worker_is_blocked(tmp_path: Path) -> None:
    shared = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-narrator-ocr")
    tts_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-narrator-tts")
    pipeline, *_providers = _pipeline(tmp_path, shared)  # type: ignore[arg-type]
    ocr = _PolishOcr(target_calls=4)
    tts = _BlockingTts()
    pipeline.ocr = ocr
    pipeline.tts = tts
    pipeline._tts_executor = tts_executor
    snapshot = pipeline.start(_polish_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    try:
        for value, timestamp in ((1, 1.0), (1, 1.2)):
            pipeline.submit_frame(
                _frame(
                    session_id=snapshot.session_id,
                    generation=snapshot.generation,
                    value=value,
                    timestamp=timestamp,
                )
            )
        assert tts.entered.wait(timeout=5.0)

        for value, timestamp in ((2, 1.4), (2, 1.6)):
            pipeline.submit_frame(
                _frame(
                    session_id=snapshot.session_id,
                    generation=snapshot.generation,
                    value=value,
                    timestamp=timestamp,
                )
            )
        assert ocr.reached.wait(timeout=5.0)
        assert ocr.values == [1, 1, 2, 2]
        assert tts.release.is_set() is False
    finally:
        tts.release.set()
        pipeline.shutdown()
        tts_executor.shutdown(wait=True, cancel_futures=True)
        shared.shutdown(wait=True, cancel_futures=True)


def test_superseded_and_duplicate_phrases_do_not_run_extra_tts(
    tmp_path: Path,
) -> None:
    shared = _ManualExecutor()
    tts_executor = _ManualExecutor()
    pipeline, *_providers, tts, audio = _pipeline(tmp_path, shared)
    pipeline.ocr = _PolishOcr()
    pipeline._tts_executor = tts_executor
    snapshot = pipeline.start(_polish_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    try:
        for value, timestamp in ((1, 1.0), (1, 1.2)):
            pipeline.submit_frame(
                _frame(
                    session_id=snapshot.session_id,
                    generation=snapshot.generation,
                    value=value,
                    timestamp=timestamp,
                )
            )
            shared.run_next()
        assert len(tts_executor.jobs) == 1

        for value, timestamp in ((1, 1.4), (1, 1.6)):
            pipeline.submit_frame(
                _frame(
                    session_id=snapshot.session_id,
                    generation=snapshot.generation,
                    value=value,
                    timestamp=timestamp,
                )
            )
            shared.run_next()
        assert len(tts_executor.jobs) == 1

        for value, timestamp in ((2, 1.8), (2, 2.0)):
            pipeline.submit_frame(
                _frame(
                    session_id=snapshot.session_id,
                    generation=snapshot.generation,
                    value=value,
                    timestamp=timestamp,
                )
            )
            shared.run_next()
        assert pipeline.snapshot.stale_queued_tts == 1

        while tts_executor.jobs:
            tts_executor.run_next()
        assert tts.values == ["phrase 2"]
        assert len(audio.played) == 1
    finally:
        pipeline.shutdown()


def test_stage_telemetry_is_correlated_and_contains_no_phrase_text(
    tmp_path: Path, caplog
) -> None:
    shared = _ManualExecutor()
    tts_executor = _ManualExecutor()
    pipeline, *_providers, _tts, audio = _pipeline(tmp_path, shared)
    pipeline.ocr = _PolishOcr()
    pipeline._tts_executor = tts_executor
    caplog.set_level(
        logging.INFO, logger="game_optimization_linux.services.narrator_pipeline"
    )
    snapshot = pipeline.start(_polish_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    try:
        for timestamp in (1.0, 1.2):
            pipeline.submit_frame(
                _frame(
                    session_id=snapshot.session_id,
                    generation=snapshot.generation,
                    value=7,
                    timestamp=timestamp,
                )
            )
            shared.run_next()
        tts_executor.run_next()
        audio.completed_callbacks[-1]()

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("Narrator stage event=")
        ]
        events = [message.split("event=", 1)[1].split(" ", 1)[0] for message in messages]
        assert events == [
            "tts_queued",
            "tts_started",
            "tts_model_ready",
            "tts_inference_finished",
            "audio_queued",
            "audio_started",
            "audio_finished",
        ]
        assert all(f"session={snapshot.session_id}" in message for message in messages)
        assert all("phrase_id=2" in message for message in messages)
        assert all("duration_ms=" in message for message in messages)
        assert all("queue_depth=" in message for message in messages)
        assert all("pid=" in message and "thread=" in message for message in messages)
        assert all("phrase 7" not in message for message in messages)
    finally:
        pipeline.shutdown()


def test_consensus_and_episode_dedup_contract_is_unchanged() -> None:
    gate = SubtitleTextGate()
    first = gate.observe("Wracaj tutaj.", 0.95, now=1.0)
    second = gate.observe("Wracaj tutaj.", 0.95, now=1.2)
    assert first.accepted_text == ""
    assert first.required_observations == 2
    assert second.accepted_text == "Wracaj tutaj."

    deduplicator = PhraseDeduplicator()
    assert (
        deduplicator.accept("Wracaj tutaj.", now=1.2, cooldown_seconds=4.5)
        == "Wracaj tutaj."
    )
    assert (
        deduplicator.accept("Wracaj tutaj.", now=1.4, cooldown_seconds=4.5)
        is None
    )
