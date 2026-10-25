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
        # The 600-byte stderr tail often keeps only PipeWire's own stream error.
        "Internal data stream error.\nstreaming stopped, reason error (-5)\n"
        "stream error: Format negotiation failed",
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


def test_format_change_after_frames_flowed_retries_the_same_variant() -> None:
    """The 22.10 incident: GTA V capture on runtime 25.08 (pipewiresrc 1.4.9).

    system-memory negotiated 1920x1050 and delivered frames for 15 s, then the
    window size changed and pipewiresrc 1.4.9 died with "unhandled format" /
    "Format negotiation failed". That is not a DMA-BUF signature: the tail had
    already proven it accepts what the compositor offers. Switching to GL made
    things worse (Failed to upload buffer) and retired both variants.
    """

    transport = _transport(dmabuf=True)
    transport._negotiated_variant = _SYSTEM_VARIANT
    transport._tried_variants = [_SYSTEM_VARIANT]
    transport._ready = True          # frames flowed on this attempt
    transport._frames_received = 90
    delays = _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "GStreamer PipeWire capture failed: stream error: unhandled format\n"
        "stream error: Format negotiation failed",
    )

    # Same variant, same bounded restart path as any transient error.
    assert transport.failed_variants == ()
    assert transport._dmabuf_suspected is False
    assert transport._select_variant() == _SYSTEM_VARIANT
    assert delays == [0.5]
    assert states == [(CaptureState.STARTING, "Reconnecting the capture stream")]


def test_format_signature_without_any_frame_still_means_dmabuf() -> None:
    """Negotiation that never produced a frame keeps the GL fallback."""

    transport = _transport(dmabuf=True)
    transport._negotiated_variant = _SYSTEM_VARIANT
    transport._tried_variants = [_SYSTEM_VARIANT]
    transport._ready = False
    delays = _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "stream error: unhandled format",
    )

    assert transport.failed_variants == (_SYSTEM_VARIANT,)
    assert transport._dmabuf_suspected is True
    assert delays == [0.2]
    assert states[0][0] is CaptureState.STARTING
    assert _DMABUF_VARIANT in states[0][1]


def test_gl_failure_after_frames_flowed_still_retires_the_gl_variant() -> None:
    """A GL runtime failure is never reinterpreted as a format change."""

    transport = _transport(dmabuf=True)
    transport._negotiated_variant = _DMABUF_VARIANT
    transport._tried_variants = [_SYSTEM_VARIANT, _DMABUF_VARIANT]
    transport._dmabuf_suspected = True
    transport._failed_variants.add(_SYSTEM_VARIANT)
    transport._ready = True
    delays = _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "stream error: unhandled format\n"
        "GstGLUploadElement:gluploadelement0: Failed to upload buffer",
    )

    assert transport.failed_variants == (_SYSTEM_VARIANT, _DMABUF_VARIANT)
    assert delays == []
    assert states[-1][0] is CaptureState.SOURCE_LOST


def test_repeated_format_changes_stay_bounded_without_frames() -> None:
    """Restarts that never deliver a frame still exhaust the budget visibly."""

    transport = _transport(dmabuf=True)
    transport._negotiated_variant = _SYSTEM_VARIANT
    transport._ready = True
    transport._restarts = _MAX_STREAM_RESTARTS
    _failing(transport)
    states: list[tuple[CaptureState, str]] = []

    transport._stream_failed(
        1,
        lambda state, message: states.append((state, message)),
        "stream error: unhandled format",
    )

    assert states[-1][0] is CaptureState.SOURCE_LOST
    assert "did not recover" in states[-1][1]


def test_first_frame_after_a_restart_resets_the_restart_budget() -> None:
    """Budget counts consecutive failures: a long session with many window
    resizes must not end with a false "did not recover" after the fifth."""

    import io
    import time

    class _Process:
        def __init__(self) -> None:
            width, height = 2, 1
            self.stdout = io.BytesIO(
                f"P6\n{width} {height}\n255\n".encode() + b"\x00" * (width * height * 3)
            )
            self.stderr = None

        def poll(self) -> int:
            return 0

    transport = _transport(dmabuf=True)
    transport._restarts = 3
    transport._attempt = 1
    process = _Process()
    transport._process = process  # type: ignore[assignment]
    transport._restart_after = lambda *args: None  # type: ignore[assignment]
    transport._last_start = {"target_object": "42"}
    frames: list[object] = []
    states: list[tuple[CaptureState, str]] = []
    transport._stopping.clear()

    transport._read_frames(
        process,  # type: ignore[arg-type]
        1,
        "session",
        1,
        "42",
        frames.append,
        lambda: None,
        lambda state, message: states.append((state, message)),
    )
    time.sleep(0)

    assert len(frames) == 1
    assert transport.frames_received == 1
    assert transport.restarts == 0


def test_real_process_resize_restart_delivers_new_frame_size(tmp_path) -> None:
    """End to end through start()/_read_frames/_stream_failed/_restart_after.

    A stand-in gst-launch delivers one 256x90 frame and then dies exactly like
    pipewiresrc 1.4.9 does on a mid-stream size change. The restarted process
    delivers 256x100. The transport must stay on system-memory, report
    STARTING once and then keep streaming, with no SOURCE_LOST. The restarted
    stand-in streams frames continuously, like the real pipeline, because the
    64 KiB chunked reader only returns a partial chunk at EOF.
    """

    import os
    import sys
    import threading

    script = tmp_path / "fake-gst-launch"
    counter = tmp_path / "launches"
    counter.write_text("0")
    script.write_text(
        "import sys, time, pathlib\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "n = int(p.read_text()) + 1\n"
        "p.write_text(str(n))\n"
        "w, h = (256, 90) if n == 1 else (256, 100)\n"
        "frame = f'P6\\n{w} {h}\\n255\\n'.encode() + b'\\x11' * (w*h*3)\n"
        "sys.stdout.buffer.write(frame)\n"
        "sys.stdout.flush()\n"
        "if n == 1:\n"
        "    time.sleep(0.2)\n"
        "    sys.stderr.write('BLAD: od elementu pipewiresrc0: stream error: unhandled format\\n'\n"
        "                     'stream error: Format negotiation failed\\n')\n"
        "    sys.stderr.flush()\n"
        "    sys.exit(1)\n"
        "for _ in range(100):\n"
        "    time.sleep(0.05)\n"
        "    sys.stdout.buffer.write(frame)\n"
        "    sys.stdout.flush()\n"
    )

    def factory(argv, **kwargs):
        return subprocess.Popen([sys.executable, str(script)], **kwargs)

    transport = GStreamerPipeWireTransport(
        "/fixture/gst-launch-1.0",
        inspect_executable="/fixture/gst-inspect-1.0",
        probe_plugins=False,
        process_factory=factory,
    )
    transport._gl_elements_present = True
    frames: list[tuple[int, int]] = []
    states: list[tuple[CaptureState, str]] = []
    second_frame = threading.Event()

    def on_frame(frame) -> None:
        frames.append((frame.width, frame.height))
        if len(frames) >= 2:
            second_frame.set()

    read_fd, write_fd = os.pipe()
    try:
        transport.start(
            remote_fd=read_fd,
            target_object="42",
            session_id="s",
            generation=1,
            sampling_hz=6.0,
            frame_callback=on_frame,
            ready_callback=lambda: None,
            state_callback=lambda state, message: states.append((state, message)),
        )
        assert second_frame.wait(5.0), (frames, states)
    finally:
        transport.stop()
        os.close(read_fd)
        os.close(write_fd)

    assert frames[:2] == [(256, 90), (256, 100)]
    assert transport.negotiated_variant == _SYSTEM_VARIANT
    assert transport.failed_variants == ()
    assert [state for state, _ in states] == [CaptureState.STARTING]
    assert transport.stream_errors == 1
    assert counter.read_text() == "2"


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


# ---------------------------------------------------------------------------
# Regression 2026-10-16: variants exhausted in one session were carried over,
# so every "Retry" failed at once with "no untried pipeline variant left".
# ---------------------------------------------------------------------------


class _FailingStderr:
    """Yields the error once; the next read tells stdout it may close."""

    def __init__(self, message: bytes, drained) -> None:
        self._message = message
        self._drained = drained

    def read(self, _size: int = -1) -> bytes:
        if self._message:
            message, self._message = self._message, b""
            return message
        self._drained.set()
        return b""

    def close(self) -> None:
        pass


class _ClosingStdout:
    def __init__(self, drained) -> None:
        self._drained = drained

    def read(self, _size: int = -1) -> bytes:
        self._drained.wait(2.0)
        return b""

    def close(self) -> None:
        pass


class _FailedGstProcess:
    def __init__(self, stderr_message: bytes) -> None:
        from threading import Event

        drained = Event()
        self.stdout = _ClosingStdout(drained)
        self.stderr = _FailingStderr(stderr_message, drained)
        self.returncode = 1

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        pass

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        pass


def _variant_of(argv: list[str]) -> str:
    return _DMABUF_VARIANT if "glupload" in " ".join(argv) else _SYSTEM_VARIANT


def test_retry_after_both_variants_failed_starts_again_from_system_memory(caplog) -> None:
    import logging
    import os
    from threading import Event

    attempts: list[str] = []
    failures = {
        # The real incident: KWin/PipeWire "Format negotiation failed" on the
        # system-memory tail, then a GL runtime failure on gl-dmabuf.
        _SYSTEM_VARIANT: b"stream error: Format negotiation failed: unhandled format\n",
        _DMABUF_VARIANT: b"Failed to upload buffer\n",
    }

    def factory(argv, **_kwargs):
        variant = _variant_of(argv)
        attempts.append(variant)
        return _FailedGstProcess(failures[variant])

    transport = GStreamerPipeWireTransport(
        "/fixture/gst-launch-1.0",
        inspect_executable="/fixture/gst-inspect-1.0",
        probe_plugins=False,
        process_factory=factory,
    )
    transport._gl_elements_present = True
    states: list[tuple[CaptureState, str]] = []
    lost = Event()

    def on_state(state: CaptureState, message: str) -> None:
        states.append((state, message))
        if state is CaptureState.SOURCE_LOST:
            lost.set()

    # The portal keeps the PipeWire remote open for the whole session; the
    # automatic variant fallback reuses it.
    descriptors: list[int] = []

    def start() -> None:
        read_fd, write_fd = os.pipe()
        descriptors.extend((read_fd, write_fd))
        transport.start(
            remote_fd=read_fd,
            target_object="42",
            session_id="s",
            generation=1,
            sampling_hz=6.0,
            frame_callback=lambda _frame: None,
            ready_callback=lambda: None,
            state_callback=on_state,
        )

    caplog.set_level(logging.INFO, logger="game_optimization_linux.services.narrator_gstreamer")
    try:
        start()
        assert lost.wait(5.0)
        # First session: system-memory, then the gl-dmabuf fallback; both fail.
        assert attempts == [_SYSTEM_VARIANT, _DMABUF_VARIANT]
        assert transport.failed_variants == (_SYSTEM_VARIANT, _DMABUF_VARIANT)
        final = states[-1][1]
        # The original negotiation error is not hidden: it is logged with the
        # failed variant, and the final state reports the exhausted pipelines.
        assert "no working capture pipeline" in final
        assert any(
            "variant=system-memory FAILED" in record.getMessage()
            and "Format negotiation failed" in record.getMessage()
            for record in caplog.records
        )

        # "Retry" = a fresh session on the same transport: no immediate
        # "no untried pipeline variant left", and system-memory goes first.
        lost.clear()
        start()
        assert attempts[2] == _SYSTEM_VARIANT
        assert lost.wait(5.0)
        assert attempts == [_SYSTEM_VARIANT, _DMABUF_VARIANT] * 2
        assert not any(
            "no untried pipeline variant left" in message for _s, message in states
        )
        assert any(
            "clearing variants that failed in the previous session "
            "(failed=system-memory,gl-dmabuf)" in record.getMessage()
            for record in caplog.records
        )
    finally:
        transport.stop()
        for descriptor in descriptors:
            os.close(descriptor)


def test_fresh_start_resets_variants_before_selection_but_restart_does_not() -> None:
    import os

    attempts: list[str] = []

    class _Idle:
        stdout = None
        stderr = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def factory(argv, **_kwargs):
        attempts.append(_variant_of(argv))
        return _Idle()

    transport = GStreamerPipeWireTransport(
        "/fixture/gst-launch-1.0",
        inspect_executable="/fixture/gst-inspect-1.0",
        probe_plugins=False,
        process_factory=factory,
    )
    transport._gl_elements_present = True
    transport._dmabuf_suspected = True
    transport._failed_variants.update({_SYSTEM_VARIANT, _DMABUF_VARIANT})
    parameters = dict(
        target_object="42",
        session_id="s",
        generation=1,
        sampling_hz=6.0,
        frame_callback=lambda _frame: None,
        ready_callback=lambda: None,
        state_callback=lambda _state, _message: None,
    )
    read_fd, write_fd = os.pipe()
    try:
        # An automatic restart inside the same session keeps the retired
        # variants (no ping-pong) and still reports the exhaustion.
        transport._restart_pending = True
        with pytest.raises(RuntimeError, match="no untried pipeline variant left"):
            transport.start(remote_fd=read_fd, **parameters)
        assert attempts == []
        transport._restart_pending = False
        # A fresh session clears them before choosing: system-memory again.
        transport.start(remote_fd=read_fd, **parameters)
        assert attempts == [_SYSTEM_VARIANT]
        assert transport.failed_variants == ()
        assert transport.tried_variants == (_SYSTEM_VARIANT,)
    finally:
        transport.stop()
        os.close(read_fd)
        os.close(write_fd)
