"""Edge noise must not sink a correctly recognised sentence.

Confirmed from a simultaneous capture of the game and the Narrator panel:

    on screen : "Trzeba to szybko wrzucić do netu."
    OCR       : "SS trzeba to szybkd wrzucić do netu EE"

OCR read almost the whole line, but the scraps "SS" and "EE" at the edges
dragged the phrase confidence down and the entire dialogue was dropped. Only the
edges are trimmed; the misread "szybkd" in the middle is deliberately kept.
"""

from __future__ import annotations

import pytest

from game_optimization_linux.services.narrator_ocr import TesseractOcrProvider
from game_optimization_linux.services.narrator_ocr_worker import TSV_HEADER
from game_optimization_linux.services.narrator_pipeline import SubtitleTextGate

_CORE = "trzeba to szybkd wrzucić do netu"


def _row(word: str, confidence: float, left: int, index: int) -> str:
    return (
        f"5\t1\t1\t1\t1\t{index}\t{left}\t40\t{max(12, len(word) * 14)}\t26\t"
        f"{confidence}\t{word}\n"
    )


def _payload(words: list[tuple[str, float]]) -> str:
    body = ""
    left = 100
    for index, (word, confidence) in enumerate(words, start=1):
        body += _row(word, confidence, left, index)
        left += max(30, len(word) * 16) + 14
    return TSV_HEADER + body


_REPORTED = [
    ("SS", 12.0),
    ("trzeba", 94.0),
    ("to", 95.0),
    ("szybkd", 74.0),
    ("wrzucić", 96.0),
    ("do", 95.0),
    ("netu", 93.0),
    ("EE", 9.0),
]


# ---------------------------------------------------------------------------
# Mandatory regression case
# ---------------------------------------------------------------------------


def test_edge_garbage_is_trimmed_and_core_is_kept() -> None:
    raw, filtered, confidence, raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(_payload(_REPORTED))
    )

    assert "SS" in raw and "EE" in raw, "raw text stays literal"
    assert filtered == _CORE
    # The misread word inside the sentence is preserved, not silently corrected.
    assert "szybkd" in filtered

    trimmed = [
        str(token["text"])
        for token in tokens
        if token.get("filter_reason") == "edge_garbage"
    ]
    assert trimmed == ["SS", "EE"]

    # Confidence is recomputed from the surviving core, not from the whole line.
    assert raw_confidence is not None and confidence is not None
    assert confidence > raw_confidence
    core_confidence = TesseractOcrProvider._token_confidence(
        [token for token in tokens if bool(token["included"])]
    )
    assert confidence == pytest.approx(core_confidence)
    assert confidence > 0.62, "the core must clear the existing gate"


def test_core_reaches_acceptance_through_the_existing_two_of_two() -> None:
    """No single-frame acceptance: the core still needs two observations."""

    _raw, filtered, confidence, _raw_conf, _tokens = (
        TesseractOcrProvider._analyze_tsv(_payload(_REPORTED))
    )
    gate = SubtitleTextGate()

    first = gate.observe(filtered, confidence, now=1.0)
    assert first.accepted_text == ""
    assert first.candidate_observation_count == 1
    assert first.required_observations == 2

    second = gate.observe(filtered, confidence, now=1.2)
    assert second.accepted_text == _CORE
    assert second.candidate_observation_count == 2


def test_exactly_one_tts_submission_for_the_repeated_observation() -> None:
    from game_optimization_linux.services.narrator_pipeline import (
        PhraseDeduplicator,
    )

    _raw, filtered, confidence, _raw_conf, _tokens = (
        TesseractOcrProvider._analyze_tsv(_payload(_REPORTED))
    )
    gate = SubtitleTextGate()
    deduplicator = PhraseDeduplicator()
    submitted: list[str] = []

    for index, now in enumerate((1.0, 1.2, 1.4)):
        observation = gate.observe(filtered, confidence, now=now)
        if not observation.accepted_text:
            continue
        phrase = deduplicator.accept(
            observation.accepted_text, now=now, cooldown_seconds=4.5
        )
        if phrase:
            submitted.append(phrase)
            deduplicator.mark_spoken(phrase, now=now)

    assert submitted == [_CORE]


# ---------------------------------------------------------------------------
# Mandatory safety cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    (
        ["Nie", "jedź", "do", "salonu", "gier"],
        ["Agent", "Steve", "Haines"],
        ["Trevor", "jedzie", "do", "Vinewood"],
        ["Oddział", "FIB"],
    ),
)
def test_real_words_at_the_edges_are_never_trimmed(words: list[str]) -> None:
    """Even at low confidence, these are words and must survive."""

    payload = _payload([(word, 45.0) for word in words])
    _raw, filtered, _confidence, _raw_conf, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert not any(
        token.get("filter_reason") == "edge_garbage" for token in tokens
    )
    for word in words:
        assert word in filtered


def test_q_v_x_alone_does_not_mark_a_token_as_noise() -> None:
    """Letters absent from Polish are not by themselves evidence of garbage."""

    for word in ("Vinewood", "Xavier", "quad"):
        token = {
            "text": word,
            "confidence": 30.0,
            "valid_confidence": True,
            "punctuation_only": False,
            "alphanumeric_characters": len(word),
        }
        assert TesseractOcrProvider._is_edge_garbage(token) is False


def test_random_string_without_a_core_is_still_rejected() -> None:
    """Trimming must not rescue a line that has no sentence in it."""

    payload = _payload(
        [("SS", 11.0), ("xz", 9.0), ("EE", 8.0), ("##", 7.0)]
    )
    _raw, filtered, confidence, _raw_conf, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    # Fewer than four words would remain, so nothing is trimmed as edge noise.
    assert not any(
        token.get("filter_reason") == "edge_garbage" for token in tokens
    )
    assert confidence is None or confidence < 0.62
    assert filtered.strip() != ""


def test_internal_bad_token_is_not_silently_removed() -> None:
    payload = _payload(
        [
            ("trzeba", 94.0),
            ("to", 95.0),
            ("QQ", 8.0),
            ("wrzucić", 96.0),
            ("do", 95.0),
            ("netu", 93.0),
        ]
    )
    _raw, filtered, _confidence, _raw_conf, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert "QQ" in filtered, "a token inside the sentence must remain visible"
    assert not any(
        token.get("filter_reason") == "edge_garbage" for token in tokens
    )


def test_one_low_confidence_observation_still_cannot_be_accepted() -> None:
    """The global confidence gate is untouched."""

    payload = _payload(
        [
            ("SS", 12.0),
            ("trzeba", 40.0),
            ("to", 38.0),
            ("szybkd", 35.0),
            ("wrzucić", 41.0),
            ("do", 39.0),
            ("netu", 37.0),
            ("EE", 9.0),
        ]
    )
    _raw, filtered, confidence, _raw_conf, _tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert confidence is not None and confidence < 0.62
    observation = SubtitleTextGate().observe(filtered, confidence, now=1.0)
    assert observation.accepted_text == ""
    assert observation.rejection_reason == "low_confidence"


def test_trimming_requires_a_core_of_at_least_four_words() -> None:
    """Three words plus noise must not be shortened."""

    payload = _payload(
        [("SS", 10.0), ("trzeba", 94.0), ("to", 95.0), ("netu", 93.0), ("EE", 9.0)]
    )
    _raw, _filtered, _confidence, _raw_conf, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert not any(
        token.get("filter_reason") == "edge_garbage" for token in tokens
    )


def test_high_confidence_short_token_at_the_edge_is_kept() -> None:
    """Low token confidence is required, so a confident scrap is left alone."""

    payload = _payload(
        [
            ("SS", 92.0),
            ("trzeba", 94.0),
            ("to", 95.0),
            ("wrzucić", 96.0),
            ("do", 95.0),
            ("netu", 93.0),
        ]
    )
    _raw, filtered, _confidence, _raw_conf, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert not any(
        token.get("filter_reason") == "edge_garbage" for token in tokens
    )
    assert filtered.startswith("SS")
