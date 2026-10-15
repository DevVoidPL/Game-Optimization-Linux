"""One subtitle episode is read exactly once.

Confirmed bug: the same line was sometimes spoken two or three times, including
in Batman where OCR reads it almost perfectly, so garbage on the edges was not
the only cause. The remaining cause was the cooldown: once it elapsed, a subtitle
still on screen passed again.

An episode starts when a phrase is handed onwards for synthesis and ends only on
a real disappearance or a clearly different accepted phrase.
"""

from __future__ import annotations

import pytest

from game_optimization_linux.services.narrator_pipeline import (
    _DEDUP_EPISODE_ABSENT_FRAMES,
    PhraseDeduplicator,
)

_COOLDOWN = 4.5
_LINE = "Jedź do salonu gier"


class _Session:
    """Drives the deduplicator the way the pipeline does and counts playbacks."""

    def __init__(self) -> None:
        self._deduplicator = PhraseDeduplicator()
        self._now = 1.0
        self.played: list[str] = []

    def _tick(self) -> float:
        self._now += 0.4
        return self._now

    def accepted(self, text: str, quality: float | None = None) -> str | None:
        """An accepted phrase reaching the deduplication step before TTS."""

        now = self._tick()
        phrase = self._deduplicator.accept(
            text, now=now, cooldown_seconds=_COOLDOWN, quality=quality
        )
        if phrase:
            # Stands in for pipeline submission -> audio.play()
            self.played.append(phrase)
            self._deduplicator.mark_spoken(phrase, now=now)
        return phrase

    def rejected_with_text(self, text: str) -> None:
        """A frame that still showed the subtitle but was not accepted."""

        self._deduplicator.accept(
            "",
            now=self._tick(),
            cooldown_seconds=_COOLDOWN,
            frame_had_text=bool(text.strip()),
        )

    def empty_frame(self) -> None:
        self._deduplicator.accept(
            "", now=self._tick(), cooldown_seconds=_COOLDOWN, frame_had_text=False
        )

    def stable_disappearance(self) -> None:
        for _ in range(_DEDUP_EPISODE_ABSENT_FRAMES):
            self.empty_frame()

    def wait_out_cooldown(self) -> None:
        self._now += _COOLDOWN + 1.0

    @property
    def canonical_text(self) -> str:
        return self._deduplicator.canonical_text

    @property
    def episode_id(self) -> int:
        return self._deduplicator.episode_id

    @property
    def last_rejection_reason(self) -> str:
        return self._deduplicator.last_rejection_reason


# ---------------------------------------------------------------------------
# A. the same exact phrase three times, cooldown expiring in between
# ---------------------------------------------------------------------------


def test_same_phrase_three_times_is_spoken_once() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.wait_out_cooldown()
    session.accepted(_LINE)
    session.wait_out_cooldown()
    session.accepted(_LINE)

    assert session.played == [_LINE]


# ---------------------------------------------------------------------------
# B. transient events between two sightings
# ---------------------------------------------------------------------------


def test_low_confidence_then_one_empty_frame_does_not_re_arm() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.rejected_with_text("Jedz do salonu gler")  # rejected_low_confidence
    session.empty_frame()  # a single dropped frame
    session.wait_out_cooldown()
    session.accepted(_LINE)

    assert session.played == [_LINE]


def test_a_run_of_rejections_with_text_never_ends_the_episode() -> None:
    session = _Session()
    session.accepted(_LINE)

    for _ in range(10):
        session.rejected_with_text("JEDZ do salonu gier")

    assert session.accepted(_LINE) is None
    assert session.played == [_LINE]


# ---------------------------------------------------------------------------
# C. garbage variants within one episode
# ---------------------------------------------------------------------------


def test_garbage_variants_within_one_episode_are_spoken_once() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.accepted("JASOIVC Jedź do salonu gier")
    session.accepted("xsasJEDZ do salonu gier")

    assert session.played == [_LINE]


def test_garbage_variants_after_cooldown_are_still_one_episode() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.wait_out_cooldown()
    session.accepted("JASOIVC Jedź do salonu gier")
    session.wait_out_cooldown()
    session.accepted("xsasJEDZ do salonu gier")

    assert session.played == [_LINE]


# ---------------------------------------------------------------------------
# D. a real disappearance ends the episode but preserves phrase cooldown
# ---------------------------------------------------------------------------


def test_same_phrase_after_stable_disappearance_stays_in_cooldown() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.stable_disappearance()

    assert session.episode_id == 0
    assert session.canonical_text == _LINE
    assert session.accepted(_LINE) is None
    assert session.last_rejection_reason == "cooldown_exact"
    assert session.played == [_LINE]


def test_same_phrase_after_disappearance_can_play_after_cooldown() -> None:
    session = _Session()
    session.accepted(_LINE)
    session.stable_disappearance()
    session.wait_out_cooldown()

    assert session.accepted(_LINE) is not None
    assert session.played == [_LINE, _LINE]


# ---------------------------------------------------------------------------
# E. a clearly different phrase is its own episode
# ---------------------------------------------------------------------------


def test_a_different_phrase_is_spoken_and_starts_a_new_episode() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.accepted("Wróć do warsztatu")

    assert session.played == [_LINE, "Wróć do warsztatu"]


def test_the_previous_phrase_stays_blocked_by_its_cooldown() -> None:
    """A new episode must not resurrect the phrase just spoken."""

    session = _Session()
    session.accepted(_LINE)
    session.accepted("Wróć do warsztatu")

    assert session.accepted(_LINE) is None
    assert session.played == [_LINE, "Wróć do warsztatu"]


# ---------------------------------------------------------------------------
# F. exactly how many absent frames are required
# ---------------------------------------------------------------------------


def test_one_empty_frame_does_not_clear_the_latch() -> None:
    session = _Session()
    session.accepted(_LINE)

    session.empty_frame()
    session.wait_out_cooldown()

    assert session.accepted(_LINE) is None
    assert session.played == [_LINE]


def test_absent_streak_is_broken_by_a_frame_containing_text() -> None:
    """Interleaved misreads must not accumulate into a disappearance."""

    session = _Session()
    session.accepted(_LINE)

    for _ in range(6):
        session.empty_frame()
        session.empty_frame()
        session.rejected_with_text("Jedz do salonu gier")

    session.wait_out_cooldown()
    assert session.accepted(_LINE) is None
    assert session.played == [_LINE]


def test_one_frame_short_of_a_disappearance_stays_blocked() -> None:
    session = _Session()
    session.accepted(_LINE)

    for _ in range(_DEDUP_EPISODE_ABSENT_FRAMES - 1):
        session.empty_frame()
    session.wait_out_cooldown()

    assert session.accepted(_LINE) is None
    assert session.played == [_LINE]


def test_required_number_of_absent_frames_ends_episode_but_keeps_cooldown() -> None:
    """Three empty observations end visibility, not phrase cooldown."""

    session = _Session()
    session.accepted(_LINE)

    for _ in range(_DEDUP_EPISODE_ABSENT_FRAMES):
        session.empty_frame()

    assert session.episode_id == 0
    assert session.canonical_text == _LINE
    assert session.accepted(_LINE) is None
    assert session.last_rejection_reason == "cooldown_exact"
    assert session.played == [_LINE]

    session.wait_out_cooldown()
    assert session.accepted(_LINE) is not None
    assert session.played == [_LINE, _LINE]


def test_probing_between_empty_frames_resets_the_streak() -> None:
    """A text-bearing observation means the subtitle is back on screen."""

    session = _Session()
    session.accepted(_LINE)

    session.empty_frame()
    session.empty_frame()
    session.rejected_with_text(_LINE)  # sighting: streak restarts
    session.empty_frame()
    session.empty_frame()

    assert session.accepted(_LINE) is None
    assert session.played == [_LINE]


# ---------------------------------------------------------------------------
# session reset
# ---------------------------------------------------------------------------


def test_a_fresh_session_clears_the_latch() -> None:
    """The pipeline builds a new deduplicator when a session starts."""

    first = _Session()
    first.accepted(_LINE)
    assert first.accepted(_LINE) is None

    second = _Session()
    assert second.accepted(_LINE) is not None


# ---------------------------------------------------------------------------
# F. a cut-off piece of a long subtitle that is still on screen
# ---------------------------------------------------------------------------

# Hogwarts Legacy, real OCR log: line 22, then only its tail (23), then the
# full line again with other garbage (24).  23 and 24 were read again.
_LONG_22 = (
    "„ODIIN DaNKIeET. dELKI. ZODACcZzyCIiE ICN CaTKIEM SPOTO W QTOQZE GO "
    "SKTYLUKI NUumeT dwanaście. Znajdujemy się obecnie pod głównym holem. "
    "Skrytki, które właśnie mijamy, należą do najnowszych."
)
_TAIL_23 = (
    "dwanaście. Znajdujemy się obecnie pod głównym holem. Skrytki, które "
    "właśnie mijamy, należą do najnowszych."
)
_LONG_24 = (
    "„ODIIN DaNKIeT. >ELKI. ZODACZzYyCIE ICN CaTKIEM SPOTO W QTOQZE GO "
    "SKTYLKI NUumeT dwanaście. Znajdujemy się obecnie pod głównym holem. "
    "Skrytki, które właśnie mijamy, należą do najnowszych. Me WA"
)


def test_tail_fragment_and_full_line_again_are_read_once() -> None:
    session = _Session()

    session.accepted(_LONG_22)
    assert session.accepted(_TAIL_23) is None
    assert session.last_rejection_reason == "active_episode_fragment"
    assert session.accepted(_LONG_24) is None

    assert session.played == [_LONG_22]


def test_fragment_after_a_short_empty_frame_is_still_the_same_line() -> None:
    session = _Session()

    session.accepted(_LONG_22)
    session.empty_frame()
    assert session.accepted(_TAIL_23) is None
    assert session.played == [_LONG_22]


def test_fragment_that_drops_a_negation_is_a_new_line() -> None:
    session = _Session()

    session.accepted("Nie jedź teraz do salonu gier")
    session.accepted("jedź teraz do salonu gier")

    assert session.played == [
        "Nie jedź teraz do salonu gier",
        "jedź teraz do salonu gier",
    ]


def test_short_common_words_do_not_count_as_a_fragment() -> None:
    session = _Session()

    session.accepted("Idź do domu, szybko, zanim zrobi się ciemno i zimno.")
    session.accepted("do domu")

    assert session.played == [
        "Idź do domu, szybko, zanim zrobi się ciemno i zimno.",
        "do domu",
    ]


def test_number_change_inside_a_fragment_is_a_new_line() -> None:
    session = _Session()

    session.accepted("Skrytka numer 101 znajduje się na samym dole banku.")
    session.accepted("numer 102 znajduje się na samym dole")

    assert len(session.played) == 2


# ---------------------------------------------------------------------------
# G. real double reads from the 2026-10-02 log (Batman, Hogwarts Legacy)
# ---------------------------------------------------------------------------

_GORDON = (
    "Gordon: Ostatni autokar opuścił miasto godzinę temu. Cieszę się, że moja "
    "córka wyjechała. Teraz zacznie się prawdziwa robota..."
)


def test_word_smeared_into_a_glyph_stays_in_the_episode() -> None:
    # Log 20:35:53-20:36:00: one line, three episodes (42, 43, 44), three reads.
    session = _Session()

    session.accepted(_GORDON)
    session.accepted(_GORDON.replace("prawdziwa robota...", '"M robota... ;'))
    session.accepted(_GORDON)

    assert session.played == [_GORDON]


def test_word_read_twice_stays_in_the_episode() -> None:
    # Log 20:04:01-20:04:02: episodes 50 and 51.
    session = _Session()

    session.accepted("Profesor.Fig Fig: Kiedy ostatni raz otwierano tę skrytkę?")
    session.accepted("Profesor Fig: Kiedy ostatni raz otwierano tę skrytkę?")

    assert len(session.played) == 1


def test_glued_and_split_words_stay_in_the_episode() -> None:
    # Log 20:28:16-20:28:18: episodes 8 and 9.
    session = _Session()

    session.accepted(
        "Dziewięć miesięcy temu Joker został skremowany. Sam nacisnąłem "
        "przycisk i spaliłem tegofpodłego skufczybyka."
    )
    session.accepted(
        "la Dziewięć miesięcy temu Joker został sktemowany. Sam nacisnąłem "
        "przyciski spaliłem tego podłego skurczybyka. , „"
    )

    assert len(session.played) == 1


def test_a_real_word_swapped_in_a_long_line_is_a_new_line() -> None:
    session = _Session()

    session.accepted(_GORDON)
    session.accepted(_GORDON.replace("prawdziwa robota", "nocna zmiana"))

    assert len(session.played) == 2


def test_a_short_real_word_swap_is_a_new_line() -> None:
    session = _Session()

    session.accepted("Idź do sklepu i kup chleb, mleko oraz masło na jutro.")
    session.accepted("Idź do sklepu i kup chleb, mleko oraz masło na dziś.")

    assert len(session.played) == 2


# ---------------------------------------------------------------------------
# H. episode memory while the subtitle never disappeared (report B1, B3, B5)
# Real readings from the 2026-10-02/03 logs; no confirmed disappearance between.
# ---------------------------------------------------------------------------

_FIG_FULL = (
    "rofesor Fig: Wygląda na to, że ci, którzy pozostawili myślodsiewnię, "
    "medalion Irogę do obu, chcieli, aby ktoś z twoimi zdolnościami dotarł "
    "aż tutaj."
)
_FIG_HOLES = (
    "Wygląda nato, że ci, pozostawili myślodsiewnię, medalion chcieli, z "
    "twoimi zdolnościami dotarł aż tutaj."
)
_FIG_FULL_AGAIN = (
    "rofesor Wygląda nato, że ci, którzy pozostawili myślodsiewnię, medalion "
    "i Irogę chcieli, aby ktoś z twoimi zdolnościami dotarł aż tutaj."
)
_CHINATOWN_FULL = (
    "Gordon: Śledziliśmy nieoznakowany pojazd v wojskowy spieszący przez "
    "Chinatown."
)


def test_full_then_fragment_with_holes_then_full_is_read_once() -> None:
    # Hogwarts 08:14:05 -> 08:14:08 -> 08:14:09 (episodes 131, 132, 133).
    session = _Session()

    session.accepted(_FIG_FULL, quality=0.82)
    assert session.accepted(_FIG_HOLES, quality=0.93) is None
    assert session.last_rejection_reason == "active_episode_fragment"
    assert session.accepted(_FIG_FULL_AGAIN, quality=0.89) is None

    assert session.played == [_FIG_FULL]


def test_tail_then_head_of_the_same_line_is_read_once() -> None:
    # Batman 23:07:38 -> 23:07:41 -> 23:07:42 (episodes 53, 54, 55): the head
    # is compared with the full reading, not only with the tail seen last.
    session = _Session()

    session.accepted(_CHINATOWN_FULL, quality=0.86)
    assert session.accepted(
        "pojazd wojskowy spieszący przez Chinatown.", quality=0.86
    ) is None
    assert session.accepted(
        'Gordon: Śledziliśmy nieoznakowany pojazd wojskowy spieszący "',
        quality=0.88,
    ) is None

    assert session.played == [_CHINATOWN_FULL]


def test_full_then_worse_ocr_then_full_keeps_the_best_canonical() -> None:
    session = _Session()

    session.accepted(_FIG_FULL, quality=0.90)
    session.accepted(_FIG_HOLES, quality=0.95)
    # A fragment never replaces the fuller canonical, even with higher quality.
    assert session.canonical_text == _FIG_FULL
    session.accepted(_FIG_FULL_AGAIN, quality=0.70)

    assert session.played == [_FIG_FULL]
    assert session.canonical_text == _FIG_FULL


def test_a_fuller_reading_becomes_the_canonical() -> None:
    session = _Session()

    session.accepted("Bluszcz: Coś nie tak?", quality=0.85)
    session.accepted("Trujący Bluszcz: Coś nie tak?", quality=0.92)

    assert session.canonical_text == "Trujący Bluszcz: Coś nie tak?"


def test_fragment_then_fuller_line_is_read_once() -> None:
    # Batman 23:19:23 -> 23:19:25 (episodes 179, 180).
    session = _Session()

    session.accepted("Bluszcz: Coś nie tak?", quality=0.83)
    assert session.accepted("Trujący Bluszcz: Coś nie tak?", quality=0.92) is None
    assert session.last_rejection_reason == "active_episode_extension"

    assert session.played == ["Bluszcz: Coś nie tak?"]


def test_fragment_then_fuller_line_with_a_lost_last_word() -> None:
    # GTA V 07:19:38 (episodes 60, 61): canonical lost "pilates".
    session = _Session()

    session.accepted("ale Dave ma dziś.", quality=0.90)
    assert session.accepted("ale Dave ma dziś pilates.", quality=0.88) is None

    assert len(session.played) == 1


def test_a_short_piece_does_not_swallow_a_longer_new_line() -> None:
    session = _Session()

    session.accepted("Idź do domu teraz.")
    session.accepted("Idź do domu teraz, zanim zrobi się ciemno i bardzo zimno na dworze.")

    assert len(session.played) == 2


def test_negation_added_in_a_fuller_line_is_a_new_line() -> None:
    session = _Session()

    session.accepted("jedź teraz do salonu gier")
    session.accepted("Nie jedź teraz do salonu gier")

    assert session.played == [
        "jedź teraz do salonu gier",
        "Nie jedź teraz do salonu gier",
    ]


def test_negation_dropped_from_a_remembered_variant_is_a_new_line() -> None:
    session = _Session()

    session.accepted("Nie jedź teraz do salonu gier, Franklin.")
    session.accepted("Nie jedź teraz do salonu gier")  # fragment, same episode
    session.accepted("jedź teraz do salonu gier, Franklin.")

    assert session.played == [
        "Nie jedź teraz do salonu gier, Franklin.",
        "jedź teraz do salonu gier, Franklin.",
    ]


def test_room_number_change_against_any_variant_is_a_new_line() -> None:
    session = _Session()

    session.accepted("Pokój 101 jest na samym końcu korytarza.")
    session.accepted("Pokój 101 jest na samym końcu")  # fragment, same episode
    session.accepted("Pokój 102 jest na samym końcu korytarza.")
    session.accepted("Pokój 102 jest na samym końcu")  # fragment of the 102 line

    assert session.played == [
        "Pokój 101 jest na samym końcu korytarza.",
        "Pokój 102 jest na samym końcu korytarza.",
    ]


def test_episode_memory_is_bounded_and_cleared_on_disappearance() -> None:
    from game_optimization_linux.services.narrator_pipeline import (
        _EPISODE_MAX_VARIANTS,
    )

    session = _Session()
    session.accepted(_FIG_FULL, quality=0.90)
    words = _FIG_FULL.split()
    for cut in range(1, 12):
        session.accepted(" ".join(words[cut:]), quality=0.80)
    variants = session._deduplicator._episode_variants
    assert len(variants) <= _EPISODE_MAX_VARIANTS
    assert variants[0].text == _FIG_FULL

    session.stable_disappearance()
    assert session._deduplicator._episode_variants == []
    assert session.episode_id == 0



# ---------------------------------------------------------------------------
# I. still-visible line re-read with OCR damage, similarity >= 0.80
# Real pairs from the 2026-10-03 log: both readings got new_episode_consensus
# with no confirmed disappearance between them.
# ---------------------------------------------------------------------------

_BATMAN_BAD = (
    "na Wróble powiedział, że ma plan. Ze razem pr y się ciebie i Gotham "
    "będzie na z /"
)
_BATMAN_GOOD = (
    "na Wróble powiedział, że ma plan. Ze razem pozbędziemy się ciebie i "
    "Gotham będzie na"
)
_SARA_GOOD = (
    "Sara Wiliams: Dziękuję, pani profesor. Ale... co oznacza, że będzie "
    "„śledzić” moje postępy w nauce?"
)
_SARA_BAD = (
    "Sara Wiliams: Dziękuję, pani profesor. Ale... co oznacza, że będzie "
    "„śledzić” moje postępy w \"ŚWI N /"
)
_AMIT_FIRST = (
    "mit Thakkar: Zakochasz.się wiwieży: Poza tym dobry teleskop pokazuje "
    "rzeczy, « akich nawet nie"
)
_AMIT_SECOND = (
    "imit Thakkar: Zakochasz/sięiwiwieży. Poza tym dobry teleskop pokazuje "
    "rzeczy, « akich nawet niesśniłaś. **% \" — AA, l. aaa « Sn \"/raSP\"E"
)


def test_batman_damaged_word_then_clean_reading_is_read_once() -> None:
    # Batman 14:12:34 -> 14:12:39, logged similarity 0.915.
    session = _Session()

    session.accepted(_BATMAN_BAD, quality=0.914)
    assert session.accepted(_BATMAN_GOOD, quality=0.943) is None
    assert session.last_rejection_reason == "active_episode_ocr_variant"

    assert session.played == [_BATMAN_BAD]
    # The higher-quality reading becomes the canonical.
    assert session.canonical_text == _BATMAN_GOOD


def test_hogwarts_clean_reading_then_garbage_tail_is_read_once() -> None:
    # Hogwarts 11:43:36 -> 11:43:40, logged similarity 0.953.
    session = _Session()

    session.accepted(_SARA_GOOD, quality=0.886)
    assert session.accepted(_SARA_BAD, quality=0.874) is None

    assert session.played == [_SARA_GOOD]
    # A lower-quality reading never replaces the canonical.
    assert session.canonical_text == _SARA_GOOD


def test_hogwarts_negation_glued_to_the_next_word_is_read_once() -> None:
    # Hogwarts 11:37:53 -> 11:37:55, logged similarity 0.926: "nie" cut at the
    # end of the first reading, "niesśniłaś" whole in the second.
    session = _Session()

    session.accepted(_AMIT_FIRST, quality=0.793)
    assert session.accepted(_AMIT_SECOND, quality=0.596) is None

    assert session.played == [_AMIT_FIRST]
    assert session.canonical_text == _AMIT_FIRST


def test_damaged_reading_compares_against_every_remembered_variant() -> None:
    session = _Session()

    session.accepted(_SARA_GOOD, quality=0.886)
    session.accepted(_SARA_BAD, quality=0.874)
    session.accepted(_SARA_GOOD.replace("Dziękuję", "Dziekuje"), quality=0.80)

    assert session.played == [_SARA_GOOD]


def test_a_real_disappearance_still_reads_the_line_again() -> None:
    session = _Session()

    session.accepted(_SARA_GOOD, quality=0.886)
    session.stable_disappearance()
    session.wait_out_cooldown()
    session.accepted(_SARA_BAD, quality=0.874)

    assert session.played == [_SARA_GOOD, _SARA_BAD]


def test_visible_ocr_variant_keeps_a_negating_prefix_apart() -> None:
    # "możliwe"/"niemożliwe" is already merged by the older
    # _same_episode_variant path (unchanged by this rule); the new rule
    # itself must never merge a negating prefix.
    from game_optimization_linux.services.narrator_pipeline import (
        _visible_ocr_variant,
    )

    assert not _visible_ocr_variant(
        "To jest niemożliwe, wiem to na pewno.",
        "To jest możliwe, wiem to na pewno.",
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # New dialogue from the same log (logged similarity <= 0.69).
        (
            _SARA_GOOD,
            "Profesor Weasley: Może zobaczenie tego w akcji odpowie na twoje "
            "pytania.",
        ),
        (
            _BATMAN_GOOD,
            "„W całym mieście rozlokowanych jest wiele czołgów wroga. Miasto "
            "jest atakowane!”",
        ),
        (
            "Profesor Weasley: Może zobaczenie tego w akcji odpowie na twoje "
            "pytania.",
            "Profesor Weasley: Chodź ze mną om się o tym.",
        ),
        # High similarity, but a different line.
        (
            "Jedź do salonu gier i odbierz nagrodę.",
            "Nie jedź do salonu gier i odbierz nagrodę.",
        ),
        (
            "Nie jedź do salonu gier i odbierz nagrodę.",
            "Jedź do salonu gier i odbierz nagrodę.",
        ),
        (
            "Pokój 101 jest na samym końcu korytarza.",
            "Pokój 102 jest na samym końcu korytarza.",
        ),
        (
            "Idź do sklepu i kup chleb, mleko oraz masło na jutro.",
            "Idź do sklepu i kup chleb, mleko oraz masło na dziś.",
        ),
    ],
)
def test_a_different_line_without_disappearance_is_read(
    first: str, second: str
) -> None:
    session = _Session()

    session.accepted(first, quality=0.90)
    session.accepted(second, quality=0.90)

    assert session.played == [first, second]



# ---------------------------------------------------------------------------
# J. Hogwarts 15:26:03 -> 15:26:07 (0.938) and 15:28:41 -> 15:28:43 (0.950):
# both readings reached TTS with no confirmed disappearance in between.
# ---------------------------------------------------------------------------

_WEASLEY_BAD = (
    "Profesor Weasley: Tego, czego się spodziewasz. czarowi. uroków. "
    "Wszystkie przydatne. AP."
)
_WEASLEY_GOOD = (
    "Profesor Weasley: Tego, czego się spodziewasz. Czarów, zaklęć, uroków. "
    "Wszystkie przydatne."
)
_WEASLEY_CUT_FIRST = 'Profesor Weasley: Dla mnie również, bo będę mogła "M twoje osie'
_WEASLEY_CUT_SECOND = 'Profesor Weasley: Dla mnie również, bo będę mogła "R. twoje postę'


def test_lost_word_variant_is_read_once_and_better_reading_kept() -> None:
    session = _Session()

    session.accepted(_WEASLEY_BAD, quality=0.693)
    # 4 s later in the log: the cooldown has expired, the subtitle has not.
    session.wait_out_cooldown()
    assert session.accepted(_WEASLEY_GOOD, quality=0.917) is None
    assert session.last_rejection_reason == "active_episode_ocr_variant"

    assert session.played == [_WEASLEY_BAD]
    assert session.canonical_text == _WEASLEY_GOOD


def test_line_cut_by_the_area_with_a_differently_cut_tail_is_read_once() -> None:
    session = _Session()

    session.accepted(_WEASLEY_CUT_FIRST, quality=0.809)
    assert session.accepted(_WEASLEY_CUT_SECOND, quality=0.808) is None

    assert session.played == [_WEASLEY_CUT_FIRST]
    # Lower quality never replaces the canonical.
    assert session.canonical_text == _WEASLEY_CUT_FIRST


def test_the_whole_episode_is_compared_not_only_the_last_canonical() -> None:
    session = _Session()

    session.accepted(_WEASLEY_BAD, quality=0.693)
    session.accepted(_WEASLEY_GOOD, quality=0.917)
    # Back to a reading close only to the first, worse variant.
    session.accepted(_WEASLEY_BAD.replace(" AP.", ""), quality=0.70)

    assert session.played == [_WEASLEY_BAD]


def test_same_line_after_confirmed_disappearance_is_read_again() -> None:
    session = _Session()

    session.accepted(_WEASLEY_BAD, quality=0.693)
    session.stable_disappearance()
    session.wait_out_cooldown()
    session.accepted(_WEASLEY_GOOD, quality=0.917)

    assert session.played == [_WEASLEY_BAD, _WEASLEY_GOOD]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Words appended at the end of a finished sentence are a continuation.
        (
            "Profesor Weasley: Tego, czego się spodziewasz. Czarów, uroków.",
            "Profesor Weasley: Tego, czego się spodziewasz. Czarów, uroków. "
            "Idź dalej.",
        ),
        # A real word inserted inside the line that is not an OCR slip.
        (
            "Profesor Weasley: Dla mnie również, bo będę mogła zobaczyć twoje",
            "Profesor Weasley: Dla mnie również, bo nie będę mogła zobaczyć twoje",
        ),
        # A different last word of a finished sentence.
        (
            "Profesor Weasley: Pamiętaj, by korzystać z Przewodnika dzisiaj.",
            "Profesor Weasley: Pamiętaj, by korzystać z Przewodnika jutro.",
        ),
        (
            "Pokój 101 jest na samym końcu korytarza.",
            "Pokój 102 jest na samym końcu korytarza.",
        ),
        (
            "Jedź do salonu gier i odbierz nagrodę.",
            "Nie jedź do salonu gier i odbierz nagrodę.",
        ),
    ],
)
def test_new_cut_tail_and_lost_word_rules_keep_real_changes_apart(
    first: str, second: str
) -> None:
    session = _Session()

    session.accepted(first, quality=0.90)
    session.accepted(second, quality=0.90)

    assert session.played == [first, second]


@pytest.mark.parametrize(
    ("text", "reading"),
    [
        # One word appended after a finished sentence: not a cut tail.
        (
            "Profesor Weasley: Tego, czego się spodziewasz. Czarów, uroków. Idź.",
            "Profesor Weasley: Tego, czego się spodziewasz. Czarów, uroków.",
        ),
        # Different last word of a finished sentence: not a cut tail.
        (
            "Profesor Weasley: Pamiętaj, by korzystać z Przewodnika jutro.",
            "Profesor Weasley: Pamiętaj, by korzystać z Przewodnika dzisiaj.",
        ),
        # A word lost inside the line, but the neighbours are different words.
        (
            "Profesor Weasley: Tego, czego się spodziewasz. Mioteł, eliksirów, "
            "uroków.",
            "Profesor Weasley: Tego, czego się spodziewasz. Czarów, uroków.",
        ),
    ],
)
def test_visible_ocr_variant_rejects_real_word_changes(text: str, reading: str) -> None:
    from game_optimization_linux.services.narrator_pipeline import (
        _visible_ocr_variant,
    )

    assert not _visible_ocr_variant(text, reading)

