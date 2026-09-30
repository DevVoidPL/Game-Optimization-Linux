"""Regression contract for OCR consensus, episodes, cleanup and ROI delivery."""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path

import pytest

from game_optimization_linux.models.narrator import (
    CaptureFrame,
    NarratorSubtitleLanguageMode,
    NormalizedRect,
    OcrResult,
)
from game_optimization_linux.services.narrator_persistence import (
    NarratorSettingsRepository,
)
from test_narrator_foundation import (
    _AlwaysStable,
    _ManualExecutor,
    _Ocr,
    _frame,
    _pipeline,
    _settings,
)


class ContractReplay:
    """Drive recorded observations through the same public API as capture."""

    def __init__(self, tmp_path: Path) -> None:
        self.executor = _ManualExecutor()
        (
            self.pipeline,
            _capture,
            _activity,
            _ocr,
            _translator,
            self.tts,
            _audio,
        ) = _pipeline(tmp_path, self.executor)
        self.pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
        self.now = 10.0
        self.pipeline._clock = lambda: self.now
        self.current = OcrResult("")

        def recognize(frame: CaptureFrame, *, language: str) -> OcrResult:
            del frame
            assert language == "pl"
            return self.current

        self.pipeline.ocr.recognize = recognize  # type: ignore[method-assign]
        settings = replace(
            _settings(),
            subtitle_language_mode=NarratorSubtitleLanguageMode.POLISH,
        )
        self.snapshot = self.pipeline.start(settings)
        self.index = 0

    def observe(
        self,
        text: str,
        *,
        confidence: float | None = 0.95,
        raw_text: str | None = None,
        quality: float | None = 0.95,
        line_confidence: float | None = 0.95,
        leading: tuple[str, ...] = (),
        trailing: tuple[str, ...] = (),
        frame_value: int | None = None,
    ):
        self.current = OcrResult(
            text=text,
            confidence=confidence,
            provider_id="test-ocr",
            raw_text=text if raw_text is None else raw_text,
            filtered_text=text,
            quality_score=quality,
            selected_line_confidence=line_confidence,
            leading_low_quality_tokens=leading,
            trailing_low_quality_tokens=trailing,
        )
        self.index += 1
        self.now += 0.2
        self.pipeline.submit_frame(
            _frame(
                session_id=self.snapshot.session_id,
                generation=self.snapshot.generation,
                value=(
                    frame_value
                    if frame_value is not None
                    else (self.index * 61) % 256
                ),
                timestamp=self.now,
            )
        )
        self.executor.run_next()  # OCR
        while self.executor.jobs:
            self.executor.run_next()
        return self.pipeline.snapshot.ocr_decision_history[-1]

    def close(self) -> None:
        self.pipeline.shutdown()


def test_sequence_a_car_instruction_is_submitted_once(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    line = "Wsiądź do swojego samochodu."
    try:
        replay.observe(line, quality=0.98)
        replay.observe(line, quality=0.97)
        replay.observe("", confidence=None, quality=None, line_confidence=None)
        replay.observe(
            "UT des Wsiądź do swojego samochodu. |",
            quality=0.73,
            leading=("UT", "des"),
        )
        replay.observe(
            "Wsiądź do swojego sam ochodu. R A",
            quality=0.70,
            trailing=("R", "A"),
        )

        assert replay.tts.values == [line]
    finally:
        replay.close()


def test_sequence_b_damaged_long_line_is_submitted_once(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    line = "Wiesz, myślałem o tobie, Trevor. O twoim stylu życia."
    try:
        replay.observe(line, quality=0.98)
        replay.observe(line, quality=0.97)
        replay.observe(
            "Wiesz, myślałem o tobie,lrevor. O twoim stylii życia. 4",
            quality=0.74,
            trailing=("4",),
        )
        replay.observe(
            "AE myślałem o tobie, Trevor. O twoim stylużycje",
            quality=0.69,
            leading=("AE",),
        )

        assert replay.tts.values == [line]
    finally:
        replay.close()


def test_sequence_c_two_empty_frames_retain_candidate_without_votes(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    line = "Dobra, kumam."
    try:
        started = replay.observe(line)
        first_gap = replay.observe(
            "", confidence=None, quality=None, line_confidence=None
        )
        second_gap = replay.observe(
            "", confidence=None, quality=None, line_confidence=None
        )
        confirmed = replay.observe(
            "dA Dobra, kumam. a a",
            quality=0.78,
            leading=("dA",),
            trailing=("a", "a"),
        )

        assert first_gap.candidate_id == second_gap.candidate_id == started.candidate_id
        assert first_gap.candidate_observation_count == 1
        assert second_gap.candidate_observation_count == 1
        assert first_gap.decision == "candidate_retained_after_empty"
        assert second_gap.decision == "candidate_retained_after_empty"
        assert confirmed.accepted is True
        assert replay.tts.values == [line]
    finally:
        replay.close()


def test_sequence_d_cleanup_uses_edge_quality_and_preserves_language(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    noisy = "| _ UT des Pokój 101, a R2D2. a a + │"
    expected = "Pokój 101, a R2D2."
    try:
        for _ in range(2):
            replay.observe(
                noisy,
                quality=0.82,
                leading=("UT", "des"),
                trailing=("a", "a"),
            )

        assert replay.tts.values == [expected]
        assert expected.startswith(tuple("|_│¦=+")) is False
        assert expected.endswith(tuple("|_│¦=+")) is False
        assert "Pokój 101, a R2D2." in expected
    finally:
        replay.close()


def test_single_low_quality_polish_a_is_not_deleted(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    line = "a jednak 7."
    try:
        replay.observe(line, leading=("a",))
        replay.observe(line, leading=("a",))
        assert replay.tts.values == [line]
    finally:
        replay.close()


def test_best_quality_candidate_variant_is_not_automatically_the_last(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        replay.observe("Pomyśl, gdzie mieszkasz.", quality=0.98)
        replay.observe("Pomysl, gdzie mieszkasz.", quality=0.76)
        assert replay.tts.values == ["Pomyśl, gdzie mieszkasz."]
    finally:
        replay.close()


_SEMANTIC_PAIRS = (
    ("Pokój 101", "Pokój 102"),
    ("Jedź do salonu", "Nie jedź do salonu"),
    ("Please lock the door", "Please unlock the door"),
)


@pytest.mark.parametrize(("first", "second"), _SEMANTIC_PAIRS)
def test_sequence_e_distinct_dialogue_without_empty_frame_submits_twice(
    tmp_path: Path, first: str, second: str
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        for text in (first, first, second, second):
            replay.observe(text)
        assert replay.tts.values == [first, second]
    finally:
        replay.close()


@pytest.mark.parametrize(("first", "second"), _SEMANTIC_PAIRS)
def test_sequence_e_distinct_dialogue_after_disappearance_submits_twice(
    tmp_path: Path, first: str, second: str
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        replay.observe(first)
        replay.observe(first)
        for _ in range(3):
            replay.observe("", confidence=None, quality=None, line_confidence=None)
        replay.observe(second)
        replay.observe(second)
        assert replay.tts.values == [first, second]
    finally:
        replay.close()


def test_sequence_f_provider_receives_only_roi_pixels_and_roi_log_is_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(
        logging.INFO, logger="game_optimization_linux.services.narrator_pipeline"
    )
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, _translator, _tts, _audio = _pipeline(
        tmp_path, executor
    )

    class CapturingOcr(_Ocr):
        def __init__(self) -> None:
            super().__init__()
            self.frames: list[CaptureFrame] = []

        def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
            assert language == "pl"
            self.frames.append(frame)
            return OcrResult(
                text="",
                confidence=None,
                provider_id=self.provider_id,
                input_width=frame.width,
                input_height=frame.height,
                preprocessed_width=frame.width * 2,
                preprocessed_height=frame.height * 2,
            )

    provider = CapturingOcr()
    pipeline.ocr = provider
    settings = replace(
        _settings(),
        subtitle_language_mode=NarratorSubtitleLanguageMode.POLISH,
        subtitle_region=NormalizedRect(x=0.25, y=0.25, width=0.5, height=0.5),
        subtitle_region_source="saved",
    )
    snapshot = pipeline.start(settings)

    def full_frame(value: int, timestamp: float) -> CaptureFrame:
        pixels = bytearray([9] * 16)
        for row in (1, 2):
            for column in (1, 2):
                pixels[row * 4 + column] = value
        return CaptureFrame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            timestamp_monotonic=timestamp,
            width=4,
            height=4,
            stride=4,
            pixel_format="gray8",
            pixels=bytes(pixels),
        )

    try:
        pipeline.submit_frame(full_frame(200, 1.0))
        executor.run_next()
        pipeline.submit_frame(full_frame(250, 1.2))
        executor.run_next()

        assert (provider.frames[0].width, provider.frames[0].height) == (2, 2)
        assert provider.frames[0].pixels == bytes([200] * 4)
        assert 9 not in provider.frames[0].pixels
        roi_logs = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("Narrator OCR ROI:")
        ]
        assert len(roi_logs) == 1
        assert "frame=4x4" in roi_logs[0]
        assert "source=saved" in roi_logs[0]
        assert "rect=(1,1,2,2)" in roi_logs[0]
        assert "crop=2x2" in roi_logs[0]
        assert "tesseract=4x4" in roi_logs[0]
    finally:
        pipeline.shutdown()


def test_sequence_g_stable_ui_inside_roi_is_not_content_blacklisted(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    text = "PRZEGLĄDAJ [Q] KONTO ROCKSTAR GAMES"
    try:
        replay.observe(text)
        replay.observe(text)
        assert replay.tts.values == [text]
    finally:
        replay.close()


def test_roi_source_distinguishes_default_from_saved_region(tmp_path: Path) -> None:
    repository = NarratorSettingsRepository(tmp_path / "narrator-games")
    repository.save_defaults(
        replace(
            _settings(),
            subtitle_region=NormalizedRect(x=0.1, y=0.6, width=0.8, height=0.2),
        )
    )

    default_region = repository.load("480")
    assert default_region.subtitle_region == NormalizedRect()
    assert default_region.subtitle_region_source == "default"

    repository.save_overrides(
        "480",
        {
            "subtitle_region": {
                "x": 0.2,
                "y": 0.7,
                "width": 0.6,
                "height": 0.2,
            }
        },
    )
    saved_region = repository.load("480")
    assert saved_region.subtitle_region_source == "saved"


def test_candidate_expires_on_confirmed_third_empty_observation(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        started = replay.observe("Zostań tutaj.")
        replay.observe("", confidence=None, quality=None, line_confidence=None)
        replay.observe("", confidence=None, quality=None, line_confidence=None)
        disappeared = replay.observe(
            "", confidence=None, quality=None, line_confidence=None
        )
        restarted = replay.observe("Zostań tutaj.")

        assert disappeared.decision == "candidate_reset_empty"
        assert disappeared.reason_code == "confirmed_disappearance"
        assert disappeared.replaced_candidate_id == started.candidate_id
        assert restarted.decision == "candidate_started"
        assert restarted.candidate_id > started.candidate_id
        assert replay.tts.values == []
    finally:
        replay.close()


def test_static_empty_crop_reaches_confirmed_disappearance(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    line = "Wracaj tutaj."
    try:
        replay.observe(line, frame_value=180)
        replay.observe(line, frame_value=180)
        for _ in range(3):
            replay.observe(
                "",
                confidence=None,
                quality=None,
                line_confidence=None,
                frame_value=0,
            )
        replay.observe(line, frame_value=180)
        replay.observe(line, frame_value=180)
        assert replay.tts.values == [line]
        blocked = replay.pipeline.snapshot.ocr_decision_history[-1]
        assert blocked.decision == "rejected_duplicate"
        assert blocked.reason_code == "cooldown_exact"
        assert blocked.canonical_text == line

        replay.now += 5.0
        for _ in range(3):
            replay.observe(
                "",
                confidence=None,
                quality=None,
                line_confidence=None,
                frame_value=0,
            )
        replay.observe(line, frame_value=180)
        replay.observe(line, frame_value=180)

        assert replay.tts.values == [line, line]
    finally:
        replay.close()
