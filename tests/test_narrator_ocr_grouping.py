"""Real-game OCR grouping, episode and final-text regressions (A-I)."""

from __future__ import annotations

from pathlib import Path

import pytest

from game_optimization_linux.services.narrator_pipeline import _phrase_metrics
from test_narrator_behavior_contract import ContractReplay


def _empty(replay: ContractReplay):
    return replay.observe("", confidence=None, quality=None, line_confidence=None)


_MOGE = (
    "Mogę na siebie zafożyć Cokolwiek, CO wisi w sklepie. JeZu, O CO CI\nchodzi?",
    "Mogę na sieDle zafożyć Cokolwiek, Co wisi w sklepie. JEZU, O CO CI\nchodzi?",
    "Mogę na siebie zafożyć Cokolwiek, Co wisi w sklepie. JeZu, O CO CI\np chodzi?",
    "Mogę na siebie zafożyć cokolwiek, Co wisi w sklepie. JeZu, O CO CI\n5 chodzi?",
)


def test_a_high_similarity_variants_share_one_candidate(tmp_path: Path) -> None:
    metrics = _phrase_metrics(_MOGE[0], _MOGE[2])
    assert metrics.char_similarity >= 0.96
    assert metrics.token_similarity >= 0.90
    replay = ContractReplay(tmp_path)
    try:
        first = replay.observe(_MOGE[0], confidence=0.76, quality=0.77)
        second = replay.observe(_MOGE[1], confidence=0.73, quality=0.70)
        assert second.accepted is True
        assert second.candidate_id == first.candidate_id
        for text in _MOGE[2:]:
            assert replay.observe(text, confidence=0.78, quality=0.74).accepted is False
        assert len(replay.tts.values) == 1
    finally:
        replay.close()


def test_a_interleaved_misread_does_not_erase_candidate_votes(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        first = replay.observe(_MOGE[2], confidence=0.76)
        replay.observe("Zupełnie inny tekst tła się pojawił tutaj.", confidence=0.70)
        restored = replay.observe(_MOGE[3], confidence=0.78)
        assert restored.accepted is True
        assert restored.candidate_id == first.candidate_id
        assert len(replay.tts.values) == 1
    finally:
        replay.close()


def test_b_edge_numbers_do_not_reset_candidate(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        first = replay.observe("4 To nie brak gustu, Trev, to jego przeciwieństwo.")
        second = replay.observe("74 To nie brak gustu, Trev, to jego przeciwieństwo.")
        assert second.candidate_id == first.candidate_id
        assert second.accepted is True
        replay.observe("7 4 To nie brak qustu, Trev, to jego przeciwieństwo.")
        assert replay.tts.values == [
            "To nie brak gustu, Trev, to jego przeciwieństwo."
        ]
    finally:
        replay.close()


def test_c_short_line_with_edge_garbage_groups_and_is_clean(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        first = replay.observe(
            "4 i Policjant Owens: Dzięki. | 4", confidence=0.70, quality=0.674
        )
        second = replay.observe(
            "E - Policjant Owens: Dzięki |", confidence=0.69, quality=0.621
        )
        assert second.candidate_id == first.candidate_id
        assert replay.pipeline.snapshot.last_accepted_ocr_text == (
            "Policjant Owens: Dzięki."
        )
        # Speaker labels are not spoken by default.
        assert replay.tts.values == ["Dzięki."]
    finally:
        replay.close()


def test_d_polish_vowels_are_not_alphabetic_noise(tmp_path: Path) -> None:
    line = "byłem szczęśliwym emerytem, zbijałem bąki przy basenie,"
    replay = ContractReplay(tmp_path)
    try:
        for confidence in (0.94, 0.88, 0.95, 0.85):
            decision = replay.observe(line, confidence=confidence)
            assert decision.rejection_reason != "alphabetic_noise"
        assert replay.tts.values == [line]
    finally:
        replay.close()


_LINE_1 = (
    "A później czekaliśmy... Gotham szykowało się na nieuniknioną walkę "
    "o władzę. Ale"
)
_LINE_2 = (
    "nic takiego nie nastąpiło. Przestępczość faktycznie spadła. W głębi duszy"
)


def test_e_growing_and_shrinking_multiline_block_is_one_episode(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        sequence = (
            f"{_LINE_1}\n{_LINE_2}",
            f"{_LINE_1}\n{_LINE_2}",
            f"{_LINE_1}\n{_LINE_2}\nwiedziałam 70 nadriaca wolna",
            f"{_LINE_1}\n{_LINE_2}\nwiedziałam 70 nadriaca wolna",
            f"{_LINE_1}\n{_LINE_2}\nwiedziałam 3a nadriana woina",
            f"{_LINE_1}\n{_LINE_2}\nwiedziałam 3a nadriana woina",
            f"{_LINE_1}\n{_LINE_2}",
            f"{_LINE_1.replace('o władzę', 'owładzę')}\n{_LINE_2}",
            f"{_LINE_1}\n{_LINE_2}\nwiedziałam 70 nadciana wolna",
            f"{_LINE_1}\n{_LINE_2}\nwiedziałam 70 nadciana wolna",
        )
        for text in sequence:
            replay.observe(text)
        assert len(replay.tts.values) == 1
    finally:
        replay.close()


def test_f_new_dialogue_is_read_without_and_after_disappearance(
    tmp_path: Path,
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        for text in (f"{_LINE_1}\n{_LINE_2}", f"{_LINE_1}\n{_LINE_2}"):
            replay.observe(text)
        for _ in range(2):
            replay.observe("Tylko czekałem, aż ktoś pociągnie za spust.")
        for _ in range(3):
            _empty(replay)
        for _ in range(2):
            replay.observe("Kelnerka: Sałatka z kurczakiem, bez sosu.")
        assert len(replay.tts.values) == 3
        assert replay.tts.values[1:] == [
            "Tylko czekałem, aż ktoś pociągnie za spust.",
            "Sałatka z kurczakiem, bez sosu.",
        ]
    finally:
        replay.close()


def test_g_final_text_has_no_cluster_unstable_artifacts(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        replay.observe("Pomyśl, gdzie mieszkasz... a A u", confidence=0.664, quality=0.626)
        replay.observe("SZYB Pomyśl, gdzie mieszkasz...", confidence=0.482, quality=0.41)
        replay.observe('"__ Pomyśl, gdzie mieszkasz... u%', confidence=0.766, quality=0.704)
        assert len(replay.tts.values) == 1
        final = replay.tts.values[0]
        assert final == "Pomyśl, gdzie mieszkasz..."
        for artifact in ('"__', "u%", "SZYB", "|", "_"):
            assert artifact not in final
    finally:
        replay.close()


def test_h_tiny_fragments_never_reach_consensus(tmp_path: Path) -> None:
    replay = ContractReplay(tmp_path)
    try:
        for _ in range(2):
            decision = replay.observe("SĄ | w", confidence=0.64, quality=0.60)
            assert decision.accepted is False
        assert replay.tts.values == []
    finally:
        replay.close()


_SAFETY_PAIRS = (
    ("Pokój 101", "Pokój 102"),
    ("Idź do pokoju 101", "Idź do pokoju 102"),
    ("Zadzwoń do mnie o 5", "Zadzwoń do mnie o 6"),
    ("Jedź do salonu", "Nie jedź do salonu"),
    ("Jedź teraz do salonu", "Nie jedź teraz do salonu"),
    ("Please lock the door", "Please unlock the door"),
    (
        "Musimy dziś wieczorem pojechać do banku po pieniądze.",
        "Musimy dziś wieczorem pojechać do banku i porozmawiać z Lesterem.",
    ),
)


@pytest.mark.parametrize(("first", "second"), _SAFETY_PAIRS)
def test_i_distinct_dialogue_without_empty_frame(
    tmp_path: Path, first: str, second: str
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        for text in (first, first, second, second):
            replay.observe(text)
        assert replay.tts.values == [first, second]
    finally:
        replay.close()


@pytest.mark.parametrize(("first", "second"), _SAFETY_PAIRS)
def test_i_distinct_dialogue_after_disappearance(
    tmp_path: Path, first: str, second: str
) -> None:
    replay = ContractReplay(tmp_path)
    try:
        replay.observe(first)
        replay.observe(first)
        for _ in range(3):
            _empty(replay)
        replay.observe(second)
        replay.observe(second)
        assert replay.tts.values == [first, second]
    finally:
        replay.close()


def test_stable_edge_numbers_remain_semantic_conflicts() -> None:
    from game_optimization_linux.services.narrator_pipeline import (
        _same_episode_variant,
    )

    for first, second in (
        ("Idź do pokoju 101", "Idź do pokoju 102"),
        ("Zadzwoń do mnie o 5", "Zadzwoń do mnie o 6"),
        ("Idź do pokoju 12.", "Idź do pokoju 13."),
    ):
        assert _phrase_metrics(first, second).semantic_conflict is True
        assert _same_episode_variant(first, second) is False
    # Only a number detached from the sentence is treated as OCR debris.
    assert (
        _phrase_metrics("4 To nie brak gustu.", "74 To nie brak gustu.")
        .semantic_conflict
        is False
    )


def test_low_quality_marker_confirmed_by_cluster_is_kept() -> None:
    from game_optimization_linux.services.narrator_pipeline import (
        _strip_low_quality_edges,
    )

    assert (
        _strip_low_quality_edges(
            "Dobra teraz jedziemy",
            leading=("Dobra",),
            trailing=(),
            reference_text="Dobra teraz jedziemy do domu",
        )
        == "Dobra teraz jedziemy"
    )
    assert (
        _strip_low_quality_edges(
            "UT teraz jedziemy",
            leading=("UT",),
            trailing=(),
            reference_text="teraz jedziemy do domu",
        )
        == "teraz jedziemy"
    )
