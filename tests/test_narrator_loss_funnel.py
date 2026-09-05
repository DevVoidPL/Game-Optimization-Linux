"""Capture diagnostics plumbing and the per-session subtitle loss funnel.

Two real defects motivated these tests:

* the panel reported "Frames received: 0" and "Not negotiated" while the capture
  log proved frames were flowing, because the counters live on the GStreamer
  transport and the pipeline only holds the capture provider;
* "Dropped spoken lines" reported 0 while most dialogue was being lost, because
  it only counts audio-queue supersessions - losses happen much earlier.
"""

from __future__ import annotations

from pathlib import Path

from game_optimization_linux.models.narrator import NarratorSessionSnapshot


class _Transport:
    """Reports what the real transport reports on a healthy session."""

    negotiated_variant = "system-memory"
    dmabuf_supported = False
    frames_received = 2398
    stream_errors = 0
    restarts = 0
    tried_variants = ("system-memory",)
    failed_variants = ()


def test_backend_forwards_transport_capture_diagnostics() -> None:
    from game_optimization_linux.services.narrator_portal import (
        QtPortalScreenCastBackend,
    )

    backend = QtPortalScreenCastBackend.__new__(QtPortalScreenCastBackend)
    backend._transport = _Transport()  # type: ignore[attr-defined]

    assert backend.negotiated_variant == "system-memory"
    assert backend.frames_received == 2398
    assert backend.tried_variants == ("system-memory",)
    assert backend.dmabuf_supported is False


def test_provider_forwards_backend_capture_diagnostics() -> None:
    from game_optimization_linux.services.narrator_capture import (
        PortalScreenCaptureProvider,
    )

    provider = PortalScreenCaptureProvider.__new__(PortalScreenCaptureProvider)
    provider._backend = _Transport()  # type: ignore[attr-defined]

    assert provider.negotiated_variant == "system-memory"
    assert provider.frames_received == 2398
    assert provider.stream_errors == 0
    assert provider.tried_variants == ("system-memory",)


def test_flowing_frames_never_surface_placeholder_text() -> None:
    """The exact regression: real values must replace the placeholders."""

    payload = NarratorSessionSnapshot(
        session_id="s1",
        game_key="292030",
        capture_format="system-memory",
        capture_dmabuf=False,
        capture_frames_received=2398,
        capture_variants_tried="system-memory",
    ).to_dict()

    assert payload["captureFramesReceived"] == 2398
    assert payload["captureFormat"] == "system-memory"
    assert payload["captureVariantsTried"] == "system-memory"
    # The QML placeholders must not be reachable with these values.
    assert payload["captureFormat"] != ""
    assert payload["captureVariantsTried"] != ""


def test_missing_capture_attributes_fall_back_without_raising() -> None:
    from game_optimization_linux.services.narrator_capture import (
        PortalScreenCaptureProvider,
    )

    provider = PortalScreenCaptureProvider.__new__(PortalScreenCaptureProvider)
    provider._backend = object()  # type: ignore[attr-defined]

    assert provider.frames_received == 0
    assert provider.negotiated_variant == ""
    assert provider.tried_variants == ()


def _snapshot(funnel: dict[str, int], **values: object) -> dict[str, int]:
    return NarratorSessionSnapshot(
        session_id="s1",
        game_key="292030",
        narration_funnel=funnel,
        **values,  # type: ignore[arg-type]
    ).narration_funnel_summary()


def test_each_rejection_reason_counts_only_on_its_own_path() -> None:
    summary = _snapshot(
        {
            "observations": 10,
            "rejected_empty": 4,
            "rejected_low_confidence": 3,
            "rejected_duplicate": 2,
            "accepted": 1,
        }
    )

    assert summary["rejectedEmpty"] == 4
    assert summary["rejectedLowConfidence"] == 3
    assert summary["rejectedDuplicate"] == 2
    assert summary["accepted"] == 1
    assert summary["candidateAbandoned"] == 0


def test_candidate_abandoned_is_derived_from_the_three_discard_decisions() -> None:
    """No single event marks abandonment, so it is summed from the discards."""

    summary = _snapshot(
        {
            "observations": 20,
            "candidate_started": 9,
            "candidate_replaced_dissimilar": 3,
            "candidate_window_expired": 2,
            "candidate_reset_low_confidence": 1,
            "candidate_reset_empty": 1,
            "accepted": 2,
        }
    )

    # 3 replaced + 2 expired + 2 reset = 7 candidates reached 1/2 and were lost.
    assert summary["candidateAbandoned"] == 7
    assert summary["candidateReplacedDissimilar"] == 3
    assert summary["candidateStarted"] == 9


def test_retained_candidates_are_not_counted_as_abandoned() -> None:
    """candidate_retained_after_* keeps the candidate, so it is not a loss."""

    summary = _snapshot(
        {
            "observations": 5,
            "candidate_started": 2,
            "candidate_retained_after_empty": 3,
            "accepted": 1,
        }
    )

    assert summary["candidateAbandoned"] == 0


def test_funnel_is_internally_consistent() -> None:
    summary = _snapshot(
        {
            "observations": 100,
            "candidate_started": 30,
            "accepted": 12,
            "tts_submitted": 10,
            "played_to_completion": 8,
        },
        audio_supersessions=2,
    )

    assert summary["accepted"] <= summary["observations"]
    assert summary["candidateStarted"] <= summary["observations"]
    assert summary["ttsSubmitted"] <= summary["accepted"]
    assert summary["playedToCompletion"] <= summary["ttsSubmitted"]
    assert summary["supersededInAudioQueue"] == 2


def test_superseded_count_comes_from_the_audio_queue_not_the_funnel() -> None:
    """The old counter was correct but far too narrow; keep it as one row."""

    summary = _snapshot({"observations": 4}, audio_supersessions=3)

    assert summary["supersededInAudioQueue"] == 3
    assert summary["observations"] == 4


def test_empty_funnel_reports_zeros_not_missing_keys() -> None:
    summary = _snapshot({})

    for key in (
        "observations",
        "rejectedEmpty",
        "rejectedLowConfidence",
        "rejectedDuplicate",
        "candidateStarted",
        "candidateAbandoned",
        "accepted",
        "ttsSubmitted",
        "playedToCompletion",
        "supersededInAudioQueue",
    ):
        assert summary[key] == 0, key


def test_funnel_is_bounded_to_known_decision_names() -> None:
    """Bounded memory: the summary never grows with observation count."""

    small = _snapshot({"rejected_low_confidence": 1, "observations": 1})
    large = _snapshot({"rejected_low_confidence": 5000, "observations": 5000})

    # The number of reported rows is fixed; only the values grow.
    assert set(small) == set(large)
    assert large["rejectedLowConfidence"] == 5000


def test_narrator_page_shows_the_funnel_and_renamed_counter() -> None:
    source = Path(
        "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    ).read_text(encoding="utf-8")

    assert "function formatNarrationFunnel(session)" in source
    assert "function formatNarrationLosses(session)" in source
    assert '["narrationFunnel"]' in source
    # The uninterpretable label is gone and named for what it counts.
    assert 'qsTr("Dropped work: %1")' not in source
    assert 'qsTr("Frames skipped by rate limit: %1")' in source
    # The narrow audio counter is no longer presented as total loss.
    assert 'qsTr("Dropped spoken lines: %1")' not in source
    assert 'qsTr("Superseded in audio queue: %1")' in source
