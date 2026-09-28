"""Replay OCR observations through the real crop/gate/translation/TTS pipeline."""
from dataclasses import replace
from pathlib import Path

import pytest

from game_optimization_linux.models.narrator import OcrResult, NarratorSubtitleLanguageMode
from game_optimization_linux.services.narrator_pipeline import PhraseDeduplicator
from test_narrator_foundation import _ManualExecutor, _pipeline, _settings, _frame

LINE = "Dobrze. Spróbujmy znaleźć drogę, nieważne, jak trudno ją dostr..."
VARIANT = "Dobrze. spróbujmy znaleźćdrogę, nie ważne, jak trudno ją dostr."


class Replay:
    def __init__(self, tmp_path: Path, polish: bool = False):
        self.executor = _ManualExecutor()
        self.pipeline, self.capture, _, self.ocr, self.translator, self.tts, self.audio = _pipeline(
            tmp_path, self.executor
        )
        self.now = 10.0
        self.pipeline._clock = lambda: self.now
        self.text = ""
        self.confidence = 0.95
        self.raw_text = ""
        self.ocr.recognize = lambda frame, language: OcrResult(
            text=self.text, raw_text=self.raw_text, confidence=self.confidence,
            provider_id=self.ocr.provider_id,
        )
        settings = _settings()
        if polish:
            settings = replace(settings, subtitle_language_mode=NarratorSubtitleLanguageMode.POLISH)
        self.snapshot = self.pipeline.start(settings)
        self.index = 0

    def observe(
        self, text: str, *, drain: bool = True, confidence: float = 0.95,
        raw_text: str | None = None,
    ):
        self.text, self.confidence = text, confidence
        self.raw_text = text if raw_text is None else raw_text
        self.index += 1
        self.now += 0.3
        # Changing the image exercises the real visual gate as a game's moving
        # background would. The OCR adapter returns the recorded observation.
        self.pipeline.submit_frame(_frame(
            session_id=self.snapshot.session_id, generation=self.snapshot.generation,
            value=(self.index * 73) % 256, timestamp=self.now,
        ))
        self.executor.run_at(len(self.executor.jobs) - 1)
        if drain:
            while self.executor.jobs:
                self.executor.run_next()
        return self.pipeline.snapshot.ocr_decision_history[-1]


@pytest.mark.parametrize("polish", [False, True])
def test_combined_token_merge_and_split_after_gate_reset(tmp_path: Path, polish: bool):
    replay = Replay(tmp_path, polish)
    try:
        replay.observe(LINE)
        replay.observe(LINE)
        replay.audio.completed_callbacks[-1]()
        # The gate forgets its accepted identity after invalid observations;
        # the episode latch must still protect the already submitted dialogue.
        replay.observe("", confidence=0)
        replay.now += 6
        replay.observe(VARIANT)
        decision = replay.observe(VARIANT)
        assert len(replay.tts.values) == 1
        assert len(replay.audio.played) == 1
        assert decision.rejection_reason == "duplicate"
        assert decision.tts_submitted is False
    finally:
        replay.pipeline.shutdown()


def test_nonconsecutive_empty_observations_do_not_end_episode(tmp_path: Path):
    replay = Replay(tmp_path)
    try:
        replay.observe(LINE)
        replay.observe(LINE)
        replay.audio.completed_callbacks[-1]()
        replay.now += 6
        for _ in range(5):
            replay.observe("")
            replay.observe("")
            replay.observe(LINE)
        replay.observe(LINE)
        assert len(replay.tts.values) == 1
        assert len(replay.audio.played) == 1
        # In contrast, three genuinely consecutive blanks end the episode.
        for _ in range(3):
            replay.observe("")
        replay.observe(LINE)
        replay.observe(LINE)
        assert len(replay.tts.values) == 2
    finally:
        replay.pipeline.shutdown()


@pytest.mark.parametrize(("first", "second"), [
    ("We can go.", "We cannot go."),
    ("Please lock the door.", "Please unlock the door."),
    ("Idź do pokoju 12.", "Idź do pokoju 13."),
])
def test_meaningful_interior_word_and_number_changes_are_new_dialogue(
    tmp_path: Path, first: str, second: str,
):
    replay = Replay(tmp_path)
    try:
        for text in (first, first, second, second):
            replay.observe(text)
        assert replay.translator.values == [first, second]
        assert len(replay.tts.values) == 2
    finally:
        replay.pipeline.shutdown()


def test_filtered_out_raw_text_does_not_count_as_subtitle_disappearance(tmp_path: Path):
    replay = Replay(tmp_path)
    try:
        replay.observe(LINE)
        replay.observe(LINE)
        replay.audio.completed_callbacks[-1]()
        replay.now += 6
        for _ in range(4):
            replay.observe("", raw_text=LINE, confidence=0.2)
        replay.observe(LINE)
        replay.observe(LINE)
        assert len(replay.tts.values) == 1
    finally:
        replay.pipeline.shutdown()


@pytest.mark.parametrize("second", [
    "Dobrze. Spróbujmy zamknąć drogę, nieważne, jak trudno ją dostr...",
    "Dobrze. Nie spróbujmy znaleźć drogę, nieważne, jak trudno ją dostr...",
    "Dobrze. Spróbujmy znaleźć drogę, nieważne, jak trudno ją dostr... Idź dalej.",
])
def test_similar_but_new_dialogue_reaches_tts(tmp_path: Path, second: str):
    replay = Replay(tmp_path)
    try:
        for text in (LINE, LINE, second, second):
            replay.observe(text)
        assert replay.translator.values == [LINE, second]
        assert len(replay.tts.values) == 2
    finally:
        replay.pipeline.shutdown()


@pytest.mark.parametrize("stage", ["translation", "tts", "playback"])
def test_pending_work_keeps_same_dialogue_latched(tmp_path: Path, stage: str):
    replay = Replay(tmp_path)
    replay.audio.auto_start = False
    try:
        replay.observe(LINE)
        replay.observe(LINE, drain=False)
        if stage in {"tts", "playback"}:
            replay.executor.run_next()
        if stage == "playback":
            replay.executor.run_next()
        replay.observe("", drain=False)
        replay.observe(VARIANT, drain=False)
        replay.observe(VARIANT, drain=False)
        while replay.executor.jobs:
            replay.executor.run_next()
        assert replay.translator.values == [LINE]
        assert len(replay.tts.values) == 1
        assert len(replay.audio.played) == 1
        assert sum(
            decision.tts_submitted
            for decision in replay.pipeline.snapshot.ocr_decision_history
        ) == 1
    finally:
        replay.pipeline.shutdown()


def test_long_lived_subtitle_survives_playback_completion_and_cooldown(tmp_path: Path):
    replay = Replay(tmp_path)
    try:
        replay.observe(LINE)
        replay.observe(LINE)
        replay.audio.completed_callbacks[-1]()
        for _ in range(40):
            replay.observe(LINE)
        assert len(replay.tts.values) == 1
        assert len(replay.audio.played) == 1
    finally:
        replay.pipeline.shutdown()


def test_combined_spacing_variant_is_blocked_before_playback_starts():
    dedup = PhraseDeduplicator()
    assert dedup.accept(LINE, now=1, cooldown_seconds=4.5) == LINE
    assert dedup.accept(VARIANT, now=2, cooldown_seconds=4.5) is None


def test_split_merge_does_not_hide_changed_numbers():
    dedup = PhraseDeduplicator()
    first, second = "Idź do pokoju 1 23", "Idź do pokoju 12 3"
    assert dedup.accept(first, now=1, cooldown_seconds=4.5) == first
    assert dedup.accept(second, now=2, cooldown_seconds=4.5) == second
