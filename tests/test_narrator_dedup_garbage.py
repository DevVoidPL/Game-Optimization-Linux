"""Deduplication of a line re-recognised with OCR garbage at its edges.

Confirmed user-visible bug: the same subtitle was spoken twice, because the
second OCR result carried a garbage prefix or suffix, produced a different
normalized identity, and slipped past the exact-match deduplicator.

Only edge noise is forgiven. A changed or extra word inside the sentence must
keep the phrases distinct, so negation and different verbs still narrate.
"""

from __future__ import annotations

import pytest

from game_optimization_linux.services.narrator_pipeline import (
    PhraseDeduplicator,
    _dedup_is_garbage_variant,
)

_COOLDOWN = 4.5


def _spoken_then(first: str, second: str) -> str | None:
    """Speak ``first``, then offer ``second`` inside the cooldown window."""

    deduplicator = PhraseDeduplicator()
    accepted = deduplicator.accept(first, now=1.0, cooldown_seconds=_COOLDOWN)
    assert accepted, "the first phrase must be accepted"
    deduplicator.mark_spoken(first, now=1.0)
    return deduplicator.accept(second, now=2.0, cooldown_seconds=_COOLDOWN)


# ---------------------------------------------------------------------------
# Mandatory positive cases: all four pairs are duplicates
# ---------------------------------------------------------------------------

_GARBAGE_PAIRS = (
    ("Jedź do salonu gier", "JASOIVC Jedź do salonu gier"),
    ("Jedź do salonu gier", "xsasJEDZ do salonu gier"),
    (
        "Spokojnie, dzióbki wy moje.",
        '>kojnie, dzióbki wy moje. © « „a =',
    ),
    (
        "Agent Steve Haines, FIB, oddział w Los Santos.",
        "gent Steve Haines, -IB, oddział w Los Santos.",
    ),
)


@pytest.mark.parametrize(("clean", "noisy"), _GARBAGE_PAIRS)
def test_garbage_variant_is_recognised_as_the_same_line(
    clean: str, noisy: str
) -> None:
    assert _dedup_is_garbage_variant(noisy.casefold(), clean.casefold()) is True
    assert _dedup_is_garbage_variant(clean.casefold(), noisy.casefold()) is True


@pytest.mark.parametrize(("clean", "noisy"), _GARBAGE_PAIRS)
def test_garbage_variant_is_not_accepted_again(clean: str, noisy: str) -> None:
    assert _spoken_then(clean, noisy) is None


@pytest.mark.parametrize(("clean", "noisy"), _GARBAGE_PAIRS)
def test_clean_variant_after_noisy_one_is_also_suppressed(
    clean: str, noisy: str
) -> None:
    """Order must not matter: the noisy version may arrive first."""

    assert _spoken_then(noisy, clean) is None


def test_garbage_variant_creates_no_second_audio_request() -> None:
    """The whole point: no second TTS submission, so nothing is spoken twice."""

    played: list[str] = []
    deduplicator = PhraseDeduplicator()

    for index, text in enumerate(
        ("Jedź do salonu gier", "JASOIVC Jedź do salonu gier")
    ):
        phrase = deduplicator.accept(
            text, now=1.0 + index, cooldown_seconds=_COOLDOWN
        )
        if phrase:
            # Stands in for pipeline submission -> audio.play()
            played.append(phrase)
            deduplicator.mark_spoken(phrase, now=1.0 + index)

    assert played == ["Jedź do salonu gier"]
    assert len(played) == 1


# ---------------------------------------------------------------------------
# Mandatory negative cases: these remain separate lines
# ---------------------------------------------------------------------------


def test_different_verb_stays_a_new_line() -> None:
    """"Wróć" is a real word, not truncated noise."""

    assert (
        _dedup_is_garbage_variant(
            "wróć do salonu gier", "jedź do salonu gier"
        )
        is False
    )
    assert _spoken_then("Jedź do salonu gier", "Wróć do salonu gier") is not None


def test_negation_stays_a_new_line() -> None:
    """Dropping "nie" would invert the meaning, so it must never be merged."""

    assert (
        _dedup_is_garbage_variant(
            "nie jedź do salonu gier", "jedź do salonu gier"
        )
        is False
    )
    assert (
        _spoken_then("Jedź do salonu gier", "Nie jedź do salonu gier") is not None
    )


def test_extended_sentence_is_not_unconditionally_discarded() -> None:
    """A genuine continuation carries real words, so it is not garbage."""

    assert (
        _dedup_is_garbage_variant(
            "jedź do salonu gier i odbierz nagrodę", "jedź do salonu gier"
        )
        is False
    )
    assert (
        _spoken_then(
            "Jedź do salonu gier", "Jedź do salonu gier i odbierz nagrodę"
        )
        is not None
    )


def test_two_unrelated_lines_stay_separate() -> None:
    assert (
        _dedup_is_garbage_variant(
            "machniemy szybką scenkę jak z filmu przyrodniczego",
            "jestem strasznie śpiąca",
        )
        is False
    )
    assert (
        _spoken_then("Jestem strasznie śpiąca.", "Machniemy szybką scenkę?")
        is not None
    )


def test_short_phrases_never_collapse() -> None:
    """Fewer than three shared words can never be treated as one line."""

    assert _dedup_is_garbage_variant("tak czynimy", "tak jest") is False
    assert _dedup_is_garbage_variant("chodź", "chodź tu") is False


# ---------------------------------------------------------------------------
# The existing behaviour must be untouched
# ---------------------------------------------------------------------------


def test_exact_repeat_is_still_suppressed() -> None:
    assert _spoken_then("Jedź do salonu gier", "Jedź do salonu gier") is None


def test_cooldown_expiry_alone_does_not_re_arm_the_same_phrase() -> None:
    """Replaces an earlier test that asserted the opposite.

    That test encoded the bug: a subtitle still on screen was allowed through
    again merely because the cooldown had elapsed, so it was read twice. The
    episode has to end first.
    """

    deduplicator = PhraseDeduplicator()
    assert deduplicator.accept(
        "Jedź do salonu gier", now=1.0, cooldown_seconds=_COOLDOWN
    )
    deduplicator.mark_spoken("Jedź do salonu gier", now=1.0)

    later = deduplicator.accept(
        "JASOIVC Jedź do salonu gier",
        now=1.0 + _COOLDOWN + 0.1,
        cooldown_seconds=_COOLDOWN,
    )
    assert later is None, "the episode has not ended, so nothing is re-read"


def test_unrelated_phrase_between_repeats_still_suppresses() -> None:
    """Suppression looks at everything spoken in the window, not just the last."""

    deduplicator = PhraseDeduplicator()
    deduplicator.accept("Jedź do salonu gier", now=1.0, cooldown_seconds=_COOLDOWN)
    deduplicator.mark_spoken("Jedź do salonu gier", now=1.0)
    deduplicator.accept("Jestem strasznie śpiąca.", now=1.5, cooldown_seconds=_COOLDOWN)
    deduplicator.mark_spoken("Jestem strasznie śpiąca.", now=1.5)

    assert (
        deduplicator.accept(
            "JASOIVC Jedź do salonu gier", now=2.0, cooldown_seconds=_COOLDOWN
        )
        is None
    )


def test_empty_text_behaviour_unchanged() -> None:
    deduplicator = PhraseDeduplicator()
    assert deduplicator.accept("", now=1.0, cooldown_seconds=_COOLDOWN) is None
