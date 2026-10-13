"""Speech-only text filter and speaker-label handling before TTS."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from game_optimization_linux.models.narrator import NarratorGameSettings
from game_optimization_linux.services.narrator_pipeline import (
    sanitize_tts_text,
    strip_speaker_label,
)
from test_narrator_behavior_contract import ContractReplay


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("||| Dzięki.", "Dzięki."),
        ("Dzięki. | ", "Dzięki."),
        ("Zniżka 50%", "Zniżka 50%"),
        ("biało-czerwony", "biało-czerwony"),
        ("Idź - szybko", "Idź, szybko"),
        ("Zażółć GĘŚLĄ jaźń, Łódź!", "Zażółć GĘŚLĄ jaźń, Łódź!"),
        ("Tak,, nie!!! Czekaj....", "Tak, nie! Czekaj..."),
        (". , Dobra", "Dobra"),
        ("— Chodź tu —", "Chodź tu"),
        ("a <b> [c] {d} ~e^ #f @g ©h", "a b c d e f g h"),
    ],
)
def test_tts_filter_expected_output(raw: str, expected: str) -> None:
    assert sanitize_tts_text(raw) == expected


def test_percent_is_kept_only_after_a_digit() -> None:
    value = sanitize_tts_text("Pomyśl, gdzie mieszkasz... u%")
    assert "%" not in value
    assert value.startswith("Pomyśl, gdzie mieszkasz...")


@pytest.mark.parametrize("raw", ["___ = |", "|||", "12 34 %", "- — –"])
def test_text_without_letters_is_not_synthesized(raw: str) -> None:
    assert sanitize_tts_text(raw) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Policjant Owens: Dzięki.", "Dzięki."),
        ("Kelnerka: Nie ma sprawy.", "Nie ma sprawy."),
        ("O 12:30 w barze", "O 12:30 w barze"),
        ("Powiedział: nie", "Powiedział: nie"),
        ("Jeden dwa trzy cztery: Tak.", "Jeden dwa trzy cztery: Tak."),
        ("Policjant: .", "Policjant: ."),
    ],
)
def test_speaker_label_is_removed_only_when_safe(raw: str, expected: str) -> None:
    assert strip_speaker_label(raw) == expected


def test_read_speaker_names_defaults_off_and_round_trips() -> None:
    settings = NarratorGameSettings.default("480")
    assert settings.read_speaker_names is False
    legacy = NarratorGameSettings.from_dict(
        {"schema_version": 1, "game_key": "480"}, expected_game_key="480"
    )
    assert legacy.read_speaker_names is False
    enabled = replace(settings, read_speaker_names=True)
    restored = NarratorGameSettings.from_dict(
        enabled.to_dict(), expected_game_key="480"
    )
    assert restored.read_speaker_names is True


def test_pipeline_speaks_filtered_text_without_speaker_label(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        for _ in range(2):
            replay.observe("Policjant Owens: Dzięki.")
        assert replay.tts.values == ["Dzięki."]
        # Display text keeps the full recognised line.
        assert replay.pipeline.snapshot.last_accepted_ocr_text == (
            "Policjant Owens: Dzięki."
        )
    finally:
        replay.close()


def test_pipeline_reads_speaker_label_when_enabled(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        replay.pipeline._settings = replace(  # type: ignore[attr-defined]
            replay.pipeline._settings, read_speaker_names=True
        )
        for _ in range(2):
            replay.observe("Policjant Owens: Dzięki.")
        assert replay.tts.values == ["Policjant Owens: Dzięki."]
    finally:
        replay.close()


def test_pipeline_skips_synthesis_when_filter_leaves_no_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import game_optimization_linux.services.narrator_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "sanitize_tts_text", lambda _text: "")
    replay = ContractReplay(tmp_path)
    try:
        for _ in range(2):
            replay.observe("Dobra, jedziemy dalej.")
        assert replay.tts.values == []
        history = replay.pipeline.snapshot.ocr_decision_history
        assert history[-1].decision == "accepted_tts_empty_after_filter"
        assert history[-1].tts_submitted is False
    finally:
        replay.close()
