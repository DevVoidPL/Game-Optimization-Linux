"""Consensus text and long active-episode regressions from a real GTA V session."""

from __future__ import annotations

from pathlib import Path

import pytest

from game_optimization_linux.services.narrator_pipeline import (
    _cluster_representative,
    _long_episode_variant,
    _meaningful_lines,
)
from test_narrator_behavior_contract import ContractReplay


def _consensus(*observations: tuple[str, float]) -> str:
    variants = [
        (text, quality, tuple(_meaningful_lines(text)))
        for text, quality in observations
    ]
    return _cluster_representative(variants)[0]


@pytest.mark.parametrize(
    ("whole", "clipped", "expected_end"),
    [
        (
            "Kurwa, ja ich serio nienawidzę.",
            "Kurwa, ja ich serio nienawid",
            "nienawidzę.",
        ),
        (
            "Chcesz się trzymać z dala od Bean Machine i bankierów?",
            "Chcesz się trzymać z dala od Bean Machine i",
            "i bankierów?",
        ),
        (
            "ale jesteś tym ideałem, do którego aspirują.",
            "ale jesteś tym ideałem, do którego",
            "do którego aspirują.",
        ),
    ],
)
def test_a_consensus_never_cuts_a_word_seen_whole(
    whole: str, clipped: str, expected_end: str
) -> None:
    # The clipped read has the better OCR quality, as in the real log.
    result = _consensus((whole, 0.80), (clipped, 0.92))
    assert result.endswith(expected_end)
    observed = set(whole.split()) | set(clipped.split())
    assert all(token in observed for token in result.split())


@pytest.mark.parametrize(
    ("clean", "inserted", "expected"),
    [
        (
            "O czym my tu, kurwa, gadamy?",
            "O czym my. tu, kurwa, gadamy?",
            "O czym my tu, kurwa, gadamy?",
        ),
        (
            "Powinniśmy się uwinąć.",
            "Powinniśmy. się uwinąć.",
            "Powinniśmy się uwinąć.",
        ),
    ],
)
def test_b_consensus_does_not_insert_mid_sentence_stops(
    clean: str, inserted: str, expected: str
) -> None:
    assert _consensus((clean, 0.80), (inserted, 0.93)) == expected


def test_token_vote_restores_polish_diacritics() -> None:
    assert (
        _consensus(
            ("Oni sa odrazający.", 0.95),
            ("Oni są odrażający.", 0.80),
            ("Oni sa odrazający.", 0.90),
        )
        == "Oni są odrażający."
    )


_SEQUENCES = (
    (
        "Najpierw kawa, później odtłuszczone latte, a w końcu bankierzy.\n"
        "A ty będziesz już gdzie indziej,",
        "źniej odtłuszczone latte, a w końcu bankierzy.\n"
        "A ty będziesz już gdzie indziej, 4",
        "Najpierw kawa, później odtłuszczone latte, a w końcu bankierzy.\n"
        "A ty będziesz już gdzie",
        "iej odtłuszczone latte, a w końcu bankierzy.\n"
        "A ty będziesz już gdzie indziej,",
    ),
    (
        "Może nie jesteś klasycznym ogrodniczym rodzajem hipstera,\n"
        "ale jesteś tym ideałem, do którego aspirują.",
        "oże nie jesteś klasycznym ogrodniczym rodzajem hipstera,\n"
        "ale jesteś tym ideałem, do którego",
        "Może nie jesteś klasycznym ogrodniczym rodzajem hipstera,\n"
        "ale jesteś tym idealem, do którego aspirują. |",
        "oże nie jesteś klasycznym ogrodniczym rodzajem hipstera,\n"
        "ale jesteś tym ideałem, do którego aspir",
    ),
    (
        "Podjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany są prawdziwe, alarm to jakiś antyk.",
        "odjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany sa prawdziwe, alarm to jakiś ant",
        "Podjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany są prawdziwe, alarm to jakiś",
        "4 Podjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany są prawdziwe, alarm to jakiś antyk.",
    ),
)


@pytest.mark.parametrize("sequence", _SEQUENCES)
def test_c_long_multiline_variants_are_one_episode(
    tmp_path: Path, sequence: tuple[str, ...]
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        episodes: set[int] = set()
        for text in (sequence[0], *sequence):
            decision = replay.observe(text)
            if decision.episode_id:
                episodes.add(decision.episode_id)
        assert len(episodes) == 1
        assert len(replay.tts.values) == 1
    finally:
        replay.close()


@pytest.mark.parametrize(
    "changed",
    [
        "Podjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany nie są prawdziwe, alarm to jakiś antyk.",
        "Podjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Potem uciekamy łodzią do portu i czekamy na Lestera.",
        "Podjedziemy pod bank od tyłu. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany są prawdziwe, alarm to jakiś antyk.",
    ],
)
def test_long_episode_match_keeps_meaning_changes_distinct(changed: str) -> None:
    original = _SEQUENCES[2][0]
    assert (
        _long_episode_variant(
            original,
            _meaningful_lines(original),
            changed,
            _meaningful_lines(changed),
        )
        is False
    )


def test_changed_long_subtitle_without_empty_frame_is_read(tmp_path: Path) -> None:
    original = _SEQUENCES[2][0]
    changed = (
        "Podjedziemy pod bank od przodu. Wysadzimy drzwi wejściowe.\n"
        "Potem uciekamy łodzią do portu i czekamy na Lestera."
    )
    replay = ContractReplay(tmp_path)
    try:
        for text in (original, original, changed, changed):
            replay.observe(text)
        assert len(replay.tts.values) == 2
    finally:
        replay.close()


def test_long_episode_numbers_stay_protected() -> None:
    first = (
        "Podjedziemy pod bank o 5. Wysadzimy drzwi wejściowe.\n"
        "Jeśli plany są prawdziwe, alarm to jakiś antyk."
    )
    second = first.replace("o 5.", "o 6.")
    assert (
        _long_episode_variant(
            first, _meaningful_lines(first), second, _meaningful_lines(second)
        )
        is False
    )
