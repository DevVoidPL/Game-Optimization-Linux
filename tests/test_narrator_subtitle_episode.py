"""One subtitle episode is read exactly once.

Confirmed bug: the same line was sometimes spoken two or three times, including
in Batman where OCR reads it almost perfectly, so garbage on the edges was not
the only cause. The remaining cause was the cooldown: once it elapsed, a subtitle
still on screen passed again.

An episode starts when a phrase is handed onwards for synthesis and ends only on
a real disappearance or a clearly different accepted phrase.
"""

from __future__ import annotations

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

    def accepted(self, text: str) -> str | None:
        """An accepted phrase reaching the deduplication step before TTS."""

        now = self._tick()
        phrase = self._deduplicator.accept(
            text, now=now, cooldown_seconds=_COOLDOWN
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
# D. a real disappearance permits a genuine second reading
# ---------------------------------------------------------------------------


def test_same_phrase_after_stable_disappearance_is_spoken_again() -> None:
    session = _Session()

    session.accepted(_LINE)
    session.stable_disappearance()
    session.accepted(_LINE)

    assert session.played == [_LINE, _LINE]


def test_a_real_disappearance_outranks_the_cooldown() -> None:
    """The episode is the authority, not the clock.

    Once the subtitle has really gone, a later appearance is a new subtitle and
    must be readable even if the cooldown has not elapsed. The cooldown only
    suppresses flicker inside an episode, which the latch now covers.
    """

    session = _Session()
    session.accepted(_LINE)
    session.stable_disappearance()

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


def test_required_number_of_absent_frames_clears_the_latch() -> None:
    """Probing in between would itself count as a sighting, so do not."""

    session = _Session()
    session.accepted(_LINE)

    for _ in range(_DEDUP_EPISODE_ABSENT_FRAMES):
        session.empty_frame()

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
