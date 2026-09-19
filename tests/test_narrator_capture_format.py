"""PipeWire capture format negotiation, restart policy and diagnostics.

Background: pipewiresrc advertises ANY caps and derives what it offers PipeWire
from what is downstream of it. handle_format_change() intersects the format
PipeWire chose with those downstream caps and, when the intersection is empty,
kills the stream with pw_stream_set_error(-EINVAL, "unhandled format"). A
system-memory-only tail therefore cannot negotiate a DMA-BUF stream at all.
"""

from __future__ import annotations

import subprocess

import pytest

from game_optimization_linux.models.narrator import CaptureState
from game_optimization_linux.services.narrator_gstreamer import (
    _DMABUF_VARIANT,
    _MAX_STREAM_RESTARTS,
    _SYSTEM_VARIANT,
    GStreamerPipeWireTransport,
)


def _transport(
    *, dmabuf: bool = True, dmabuf_suspected: bool = False
) -> GStreamerPipeWireTransport:
    transport = GStreamerPipeWireTransport(
        "/fixture/gst-launch-1.0",
        inspect_executable="/fixture/gst-inspect-1.0",
        probe_plugins=False,
    )
    # GL elements installed. This must NOT be enough to select the GL variant.
    transport._gl_elements_present = dmabuf
    transport._dmabuf_suspected = dmabuf_suspected
    return transport


def _argv(transport: GStreamerPipeWireTransport) -> list[str]:
    return transport._build_argv(
        inherited_fd=7,
        target_property="target-object",
        target_object="42",
        numerator=6,
        variant=transport._select_variant(),
    )


# ---------------------------------------------------------------------------
# Format negotiation surface
# ---------------------------------------------------------------------------


def test_default_variant_is_system_memory_with_no_gl_elements() -> None:
    """Regression guard: GL must never be the default.

    Capture worked on the system-memory path for weeks. Preferring GL because
    its elements happen to exist broke the common case with "Failed to upload
    buffer": the elements can be installed and still fail at runtime.
    """

    transport = _transport(dmabuf=True)

    assert transport._select_variant() == _SYSTEM_VARIANT
    pipeline = " ".join(_argv(transport))
    assert "glupload" not in pipeline
    assert "glcolorconvert" not in pipeline
    assert "gldownload" not in pipeline
    assert "videoconvert" in pipeline


def test_gl_is_not_selected_merely_because_its_elements_exist() -> None:
    assert _transport(dmabuf=True)._select_variant() == _SYSTEM_VARIANT
    assert _transport(dmabuf=False)._select_variant() == _SYSTEM_VARIANT


def test_gl_variant_is_only_reachable_after_a_dmabuf_signature_failure() -> None:
    transport = _transport(dmabuf=True)
    assert transport._select_variant() == _SYSTEM_VARIANT

    transport._dmabuf_suspected = True
    transport._failed_variants.add(_SYSTEM_VARIANT)
    assert transport._select_variant() == _DMABUF_VARIANT

    pipeline = " ".join(
        transport._build_argv(
            inherited_fd=7,
            target_property="target-object",
            target_object="42",
            numerator=6,
            variant=_DMABUF_VARIANT,
        )
    )
    assert "glupload" in pipeline
    assert pipeline.index("glupload") < pipeline.index("videoconvert")


def test_gl_variant_is_never_selected_when_its_elements_are_absent() -> None:
    transport = _transport(dmabuf=False, dmabuf_suspected=True)
    transport._failed_variants.add(_SYSTEM_VARIANT)

    assert transport._select_variant() == ""


@pytest.mark.parametrize("variant", (_SYSTEM_VARIANT, _DMABUF_VARIANT))
def test_both_variants_still_deliver_system_memory_rgb_to_the_ocr_path(
    variant: str,
) -> None:
    """The OCR path consumes rgb888; neither variant may change that."""

    pipeline = " ".join(
        _transport(dmabuf=True)._build_argv(
            inherited_fd=7,
            target_property="target-object",
            target_object="42",
            numerator=6,
            variant=variant,
        )
    )

    assert "video/x-raw,format=RGB,framerate=6/1" in pipeline
    assert pipeline.rstrip().endswith("fdsink fd=1 sync=false")
    assert "pnmenc" in pipeline
    # The RGB capsfilter must remain downstream of every converter.
    assert pipeline.index("videoconvert") < pipeline.index(
        "video/x-raw,format=RGB"
    )
    assert pipeline.index("video/x-raw,format=RGB") < pipeline.index("pnmenc")


def test_missing_gl_elements_do_not_make_the_transport_unavailable() -> None:
    """A runtime without GL must still capture, just without DMA-BUF."""

    calls: list[str] = []

    def runner(argv, **_values):
        element = argv[1]
        calls.append(element)
        # Required elements succeed; the GL stack is absent.
        code = 1 if element in {"glupload", "glcolorconvert", "gldownload"} else 0
        return subprocess.CompletedProcess(argv, code)

    transport = GStreamerPipeWireTransport(
        "/fixture/gst-launch-1.0",
        inspect_executable="/fixture/gst-inspect-1.0",
        probe_plugins=False,
    )
    original = subprocess.run
    try:
        subprocess.run = runner  # type: ignore[assignment]
        transport._probe_runtime()
    finally:
        subprocess.run = original  # type: ignore[assignment]

    assert transport.available is True
    assert transport.dmabuf_supported is False
    assert "glupload" in calls


# ---------------------------------------------------------------------------
# Restart policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    (
        "GStreamer PipeWire capture failed: stream error: unhandled format",
        "No supported formats found",
    ),
)
def test_dmabuf_signature_is_detected(message: str) -> None:
    assert GStreamerPipeWireTransport._indicates_dmabuf(message) is True
    # Must NOT be a same-variant retry: the tail itself is wrong.
    assert GStreamerPipeWireTransport._is_recoverable(message) is False


@pytest.mark.parametrize(
    "message",
    (
        "ERROR from GstGLUploadElement:gluploadelement0: Failed to upload buffer",
        "gst_video_frame_map_id: assertion 'info->finfo->format == meta->format' failed",
        "gst_value_get_fraction_denominator: assertion 'GST_VALUE_HOLDS_FRACTION (value)' failed",
    ),
)
def test_gl_runtime_failures_are_detected(message: str) -> None:
    assert GStreamerPipeWireTransport._indicates_gl_failure(message) is True
    assert GStreamerPipeWireTransport._is_recoverable(message) is False


@pytest.mark.parametrize(
    "message",
    (
        "streaming stopped, reason not-negotiated",
        "The PipeWire capture stream closed",
    ),
)
def test_transient_errors_are_same_variant_retries(message: str) -> None:
    assert GStreamerPipeWireTransport._is_recoverable(message) is True
    assert GStreamerPipeWireTransport._indicates_gl_failure(message) is False


@pytest.mark.parametrize(
    "message",
    (
        "GStreamer element pipewiresrc is unavailable",
        "PipeWire capture needs a remote descriptor and target",
        "Permission denied",
    ),
)
def test_unrelated_errors_are_not_retried(message: str) -> None:
    assert GStreamerPipeWireTransport._is_recoverable(message) is False
    assert GStreamerPipeWireTransport._indicates_gl_failure(message) is False
    assert GStreamerPipeWireTransport._indicates_dmabuf(message) is False


def _failing(transport: GStreamerPipeWireTransport) -> list[float]:
    delays: list[float] = []

    def fake_restart(delay, parameters, message, cb):
        delays.append(delay)

    transport._restart_after = fake_restart  # type: ignore[assignment]
    transport._last_start = {"target_object": "42"}
    transport._attempt = 1
    return delays


def test_gl_failure_falls_back_to_system_memory_variant() -> None:
    """The real-world regression: GL fails at runtime, so switch tails."""

    transport = _transport(dmabuf=True)
    transport._negotiated_variant = _DMABUF_VARIANT
    transport._tried_variants = [_SYSTEM_VARIANT, _DMABUF_VARIANT]
    transport._dmabuf_suspected = True
    transport._failed_variants.add(_SYSTEM_VARIANT)
    delays = _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "Failed to upload buffer",
    )

    # Both variants are now retired, so this must be permanent and visible.
    assert transport.failed_variants == (_SYSTEM_VARIANT, _DMABUF_VARIANT)
    assert delays == []
    assert states[-1][0] is CaptureState.SOURCE_LOST
    assert "no working capture pipeline" in states[-1][1]


def test_dmabuf_signature_switches_variant_without_using_restart_budget() -> None:
    transport = _transport(dmabuf=True)
    transport._negotiated_variant = _SYSTEM_VARIANT
    transport._tried_variants = [_SYSTEM_VARIANT]
    delays = _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "stream error: unhandled format",
    )

    assert transport.failed_variants == (_SYSTEM_VARIANT,)
    assert transport._select_variant() == _DMABUF_VARIANT
    # A fallback happens promptly and does not consume a same-variant restart.
    assert delays == [0.2]
    assert transport.restarts == 0
    assert states[0][0] is CaptureState.STARTING
    assert _DMABUF_VARIANT in states[0][1]


def test_variants_cannot_ping_pong() -> None:
    """Each variant gets one fair attempt, then the failure is permanent."""

    transport = _transport(dmabuf=True)
    transport._dmabuf_suspected = True
    transport._failed_variants.update({_SYSTEM_VARIANT, _DMABUF_VARIANT})

    assert transport._select_variant() == ""


def test_transient_error_restarts_same_variant_with_backoff() -> None:
    transport = _transport(dmabuf=False)
    transport._negotiated_variant = _SYSTEM_VARIANT
    delays = _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "The PipeWire capture stream closed",
    )

    assert transport.stream_errors == 1
    assert states[0][0] is CaptureState.STARTING
    assert delays == [0.5]
    # The variant is still usable, so it must not be retired.
    assert transport.failed_variants == ()


def test_restart_budget_is_bounded_and_persistent_failure_stays_visible() -> None:
    transport = _transport(dmabuf=False)
    transport._negotiated_variant = _SYSTEM_VARIANT
    states: list[tuple[CaptureState, str]] = []
    _failing(transport)

    transport._restarts = _MAX_STREAM_RESTARTS
    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "The PipeWire capture stream closed",
    )

    assert states[-1][0] is CaptureState.SOURCE_LOST
    assert "did not recover" in states[-1][1]
    assert str(_MAX_STREAM_RESTARTS) in states[-1][1]


def test_non_recoverable_error_is_reported_immediately() -> None:
    transport = _transport(dmabuf=False)
    states: list[tuple[CaptureState, str]] = []
    transport._last_start = {"target_object": "42"}
    transport._attempt = 1

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "GStreamer element pipewiresrc is unavailable",
    )

    assert states == [
        (
            CaptureState.SOURCE_LOST,
            "GStreamer element pipewiresrc is unavailable",
        )
    ]
    assert transport.restarts == 0


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_transport_exposes_capture_counters() -> None:
    transport = _transport(dmabuf=True)

    assert transport.frames_received == 0
    assert transport.stream_errors == 0
    assert transport.restarts == 0
    assert transport.dmabuf_supported is True
    assert transport.negotiated_variant == ""


def test_snapshot_exposes_capture_diagnostics() -> None:
    from game_optimization_linux.models.narrator import NarratorSessionSnapshot

    payload = NarratorSessionSnapshot(
        session_id="s1",
        game_key="292030",
        capture_format="dmabuf",
        capture_dmabuf=True,
        capture_frames_received=0,
        capture_stream_errors=3,
        capture_restarts=2,
    ).to_dict()

    # Zero frames with non-zero errors is the signature of "no frames arrived",
    # as opposed to "no subtitles were recognised".
    assert payload["captureFramesReceived"] == 0
    assert payload["captureStreamErrors"] == 3
    assert payload["captureRestarts"] == 2
    assert payload["captureFormat"] == "dmabuf"
    assert payload["captureDmabuf"] is True


def test_narrator_page_shows_capture_diagnostics() -> None:
    from pathlib import Path

    source = Path(
        "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    ).read_text(encoding="utf-8")

    assert 'qsTr("Frames received: %1")' in source
    assert '["captureFramesReceived"]' in source
    assert 'qsTr("Capture stream errors: %1")' in source
    assert '["captureStreamErrors"]' in source
    assert 'qsTr("Capture restarts: %1")' in source
    assert '["captureRestarts"]' in source
    assert "function formatCaptureFormat(session)" in source
    assert '["captureDmabuf"]' in source
