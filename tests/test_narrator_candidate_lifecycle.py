"""Candidate lifecycle correlation. Diagnostics only, no decision changes.

Hypothesis under test: a correctly recognised full subtitle appears as candidate
1/2 but is never accepted, because the next observation is not similar enough, so
the candidate is replaced or expires.

Most of this lifecycle already existed in OcrDecisionObservation (bounded to
OCR_DECISION_HISTORY_LIMIT = 20): raw/filtered/normalized text, confidence,
candidate_observation_count / candidate_required_observations (the 1/2 counter),
candidate_similarity, candidate_match_kind, the decision vocabulary covering
created / retained / accepted / replaced / expired / reset, and rejection_reason.

What was missing was an identity: candidate_identity is the normalized text and
changes on replacement, so observations could not be grouped and a replacement
could not name what it displaced. These tests cover only that addition.
"""

from __future__ import annotations

import pytest

from game_optimization_linux.services.narrator_pipeline import SubtitleTextGate

_GOOD = "W obliczu śmierci nie ma się już czego bać."


def _gate() -> SubtitleTextGate:
    return SubtitleTextGate()


def test_first_observation_creates_candidate_one_with_an_id() -> None:
    observation = _gate().observe(_GOOD, 0.94, now=1.0)

    assert observation.decision == "candidate_started"
    assert observation.candidate_id == 1
    assert observation.candidate_observation_count == 1
    assert observation.required_observations == 2
    # Nothing was displaced by the very first candidate.
    assert observation.replaced_candidate_id == 0
    assert observation.replaced_candidate_text == ""


def test_confirming_observation_keeps_the_same_candidate_id() -> None:
    """All observations of one subtitle attempt share an identity."""

    gate = _gate()
    first = gate.observe(_GOOD, 0.94, now=1.0)
    second = gate.observe(_GOOD, 0.95, now=1.2)

    assert second.decision == "accepted_consensus"
    assert second.candidate_id == first.candidate_id == 1
    assert second.accepted_text == _GOOD


def test_dissimilar_observation_names_the_candidate_it_replaced() -> None:
    """The hypothesis' mechanism: a good candidate displaced before 2/2."""

    gate = _gate()
    good = gate.observe(_GOOD, 0.94, now=1.0)
    replacement = gate.observe("Zupelnie inne zdanie o czyms innym.", 0.93, now=1.2)

    assert replacement.decision == "candidate_replaced_dissimilar"
    assert replacement.candidate_replaced is True
    # The new candidate has its own id, and reports the abandoned one.
    assert replacement.candidate_id == 2
    assert replacement.replaced_candidate_id == good.candidate_id == 1
    assert replacement.replaced_candidate_text == _GOOD
    # The abandoned candidate never reached the required count.
    assert good.candidate_observation_count < good.required_observations


def test_expired_candidate_is_reported_with_its_text() -> None:
    """Same subtitle, but after the stability window: expiry, not confirmation."""

    gate = _gate()
    good = gate.observe(_GOOD, 0.94, now=1.0)
    late = gate.observe(_GOOD, 0.94, now=1.0 + 99.0)

    assert late.decision == "candidate_window_expired"
    assert late.replaced_candidate_id == good.candidate_id
    assert late.replaced_candidate_text == _GOOD
    assert late.candidate_id == 2


def test_reset_reports_the_abandoned_candidate() -> None:
    """An empty observation clears a candidate; the log must still name it."""

    gate = _gate()
    good = gate.observe(_GOOD, 0.94, now=1.0)
    gate.observe("", None, now=1.3)  # retained on the first gap
    reset = gate.observe("", None, now=1.4)

    assert reset.decision.startswith("candidate_reset_")
    assert reset.replaced_candidate_id == good.candidate_id
    assert reset.replaced_candidate_text == _GOOD


def test_retained_candidate_keeps_its_id() -> None:
    gate = _gate()
    good = gate.observe(_GOOD, 0.94, now=1.0)
    retained = gate.observe("", None, now=1.3)

    assert retained.decision.startswith("candidate_retained_after_")
    assert retained.candidate_id == good.candidate_id
    # Retention is not abandonment, so nothing is reported as replaced.
    assert retained.replaced_candidate_id == 0


def test_candidate_ids_are_monotonic_across_a_session() -> None:
    gate = _gate()
    ids = []
    for index, text in enumerate(
        ("pierwsze zdanie tutaj", "drugie zdanie tutaj", "trzecie zdanie tutaj")
    ):
        ids.append(gate.observe(text, 0.94, now=1.0 + index * 0.2).candidate_id)

    assert ids == [1, 2, 3]


def test_lifecycle_reaches_the_decision_history() -> None:
    """The fields must survive into the bounded history the panel reads."""

    from game_optimization_linux.models.narrator import OcrDecisionObservation

    payload = OcrDecisionObservation(
        observation_id=7,
        observed_at_monotonic=1.0,
        raw_text="W obliczu smierci",
        filtered_text=_GOOD,
        normalized_text=_GOOD,
        confidence=0.94,
        decision="candidate_replaced_dissimilar",
        candidate_id=2,
        replaced_candidate_id=1,
        replaced_candidate_text=_GOOD,
        candidate_observation_count=1,
        candidate_required_observations=2,
        candidate_similarity=0.41,
    ).to_dict(now=1.0)

    assert payload["candidateId"] == 2
    assert payload["replacedCandidateId"] == 1
    assert payload["replacedCandidateText"] == _GOOD
    # The pre-existing lifecycle fields are still present.
    assert payload["candidateObservationCount"] == 1
    assert payload["candidateRequiredObservations"] == 2
    assert payload["candidateSimilarity"] == pytest.approx(0.41)


def test_no_decision_changed_by_the_added_identity() -> None:
    """Guard: the counter must not alter acceptance or rejection outcomes."""

    gate = _gate()
    assert gate.observe(_GOOD, 0.94, now=1.0).accepted_text == ""
    assert gate.observe(_GOOD, 0.95, now=1.2).accepted_text == _GOOD

    # Low confidence still rejected, similarity still gates replacement.
    other = _gate()
    assert other.observe(_GOOD, 0.10, now=1.0).rejection_reason == "low_confidence"
