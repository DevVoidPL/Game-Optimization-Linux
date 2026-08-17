from __future__ import annotations

from dataclasses import replace
import hashlib
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event
import time

import pytest
from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureSourceType,
    CaptureState,
    NarratorComponentKind,
    NarratorGameSettings,
    OcrResult,
)
from game_optimization_linux.services.narrator_capture import (
    CaptureCapabilities,
    CaptureRequest,
    PortalScreenCaptureProvider,
)
from game_optimization_linux.services.narrator_components import (
    NarratorComponentDefinition,
    NarratorComponentManager,
)
from game_optimization_linux.services.narrator_gstreamer import (
    GStreamerPipeWireTransport,
    PnmStreamDecoder,
)
from game_optimization_linux.services.narrator_ocr import (
    TESSERACT_COMPONENT_ID,
    TESSERACT_MODEL_RELATIVE_PATH,
    TesseractOcrProvider,
)
from game_optimization_linux.services.narrator_persistence import (
    CaptureGrantRepository,
    TranslationCache,
)
from game_optimization_linux.services.narrator_pipeline import (
    NarratorPipeline,
    UnavailableAudioOutput,
    UnavailableTranslationProvider,
    UnavailableTtsProvider,
)
from game_optimization_linux.services.narrator_portal import (
    PortalStream,
    QtPortalScreenCastBackend,
)


def _frame(
    *,
    session_id: str = "session",
    generation: int = 1,
    timestamp: float = 1.0,
    value: int = 32,
) -> CaptureFrame:
    width, height = 40, 20
    return CaptureFrame(
        session_id=session_id,
        generation=generation,
        timestamp_monotonic=timestamp,
        width=width,
        height=height,
        stride=width * 3,
        pixel_format="rgb888",
        pixels=bytes([value]) * width * height * 3,
    )


class _Portal:
    def __init__(self) -> None:
        self.create_callbacks: list[tuple[object, object]] = []
        self.select_error = None
        self.closed = None
        self.close_count = 0
        self.shutdown_count = 0

    def capabilities(self, transport_available: bool, transport_message: str):
        del transport_message
        return CaptureCapabilities(
            transport_available,
            portal_version=6,
            source_types=frozenset(
                {CaptureSourceType.WINDOW, CaptureSourceType.MONITOR}
            ),
            persistence_supported=True,
        )

    def create_session(self, callback, error_callback) -> None:
        self.create_callbacks.append((callback, error_callback))

    def select_sources(self, session_handle: str, **values) -> None:
        del session_handle
        if self.select_error is not None:
            values["error_callback"](*self.select_error)
        else:
            values["callback"]()

    def start_session(self, session_handle: str, **values) -> None:
        del session_handle
        values["callback"](PortalStream(44, "9123"), "new-token")

    def open_remote(self, session_handle: str, callback, error_callback) -> None:
        del session_handle, error_callback
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        callback(read_fd)

    def watch_session_closed(self, session_handle: str, callback) -> None:
        del session_handle
        self.closed = callback

    def close_session(self, session_handle: str) -> None:
        del session_handle
        self.close_count += 1

    def shutdown(self) -> None:
        self.shutdown_count += 1


class _Transport:
    available = True
    message = "ready"

    def __init__(self) -> None:
        self.values = None
        self.stop_count = 0

    def start(self, **values) -> None:
        self.values = values
        values["ready_callback"]()

    def stop(self) -> None:
        self.stop_count += 1


def test_portal_backend_runs_standard_flow_and_releases_stream() -> None:
    portal = _Portal()
    transport = _Transport()
    backend = QtPortalScreenCastBackend(portal=portal, transport=transport)
    states: list[CaptureState] = []
    started = []
    frames = []
    request = CaptureRequest(
        "session",
        "208650",
        3,
        CaptureSourceType.WINDOW,
        sampling_hz=8.0,
    )

    backend.start(
        request,
        frame_callback=frames.append,
        started_callback=started.append,
        state_callback=lambda state, _message: states.append(state),
    )
    portal.create_callbacks[-1][0]("/portal/session/1")

    assert states == [CaptureState.STARTING, CaptureState.SELECTING_SOURCE]
    assert started[0].stream_id == "9123"
    assert started[0].restore_token == "new-token"
    assert transport.values["sampling_hz"] == 8.0
    transport.values["frame_callback"](_frame(generation=3))
    assert len(frames) == 1

    backend.stop()
    assert transport.stop_count >= 1
    assert portal.close_count == 1
    assert portal.shutdown_count == 1


def test_portal_session_closed_releases_transport_and_connection() -> None:
    portal = _Portal()
    transport = _Transport()
    backend = QtPortalScreenCastBackend(portal=portal, transport=transport)
    states: list[CaptureState] = []
    backend.start(
        CaptureRequest("session", "208650", 1, CaptureSourceType.WINDOW),
        frame_callback=lambda _frame: None,
        started_callback=lambda _info: None,
        state_callback=lambda state, _message: states.append(state),
    )
    portal.create_callbacks[-1][0]("/portal/session/1")
    portal.closed()

    assert states[-1] is CaptureState.SOURCE_LOST
    assert portal.shutdown_count == 1
    assert transport.stop_count >= 2


def test_portal_permission_cancellation_is_not_generic_failure() -> None:
    portal = _Portal()
    portal.select_error = (1, "cancelled")
    backend = QtPortalScreenCastBackend(portal=portal, transport=_Transport())
    states: list[tuple[CaptureState, str]] = []
    backend.start(
        CaptureRequest("session", "208650", 1, CaptureSourceType.WINDOW),
        frame_callback=lambda _frame: None,
        started_callback=lambda _info: None,
        state_callback=lambda state, message: states.append((state, message)),
    )
    portal.create_callbacks[-1][0]("/portal/session/1")
    assert states[-1][0] is CaptureState.CANCELLED
    assert portal.shutdown_count == 1


def test_pipewire_connection_failure_is_not_reported_as_permission_denial() -> None:
    class BrokenPortal(_Portal):
        def open_remote(self, session_handle, callback, error_callback) -> None:
            del session_handle, callback
            error_callback("PipeWire remote could not be opened")

    portal = BrokenPortal()
    backend = QtPortalScreenCastBackend(portal=portal, transport=_Transport())
    states: list[tuple[CaptureState, str]] = []
    backend.start(
        CaptureRequest("session", "208650", 1, CaptureSourceType.WINDOW),
        frame_callback=lambda _frame: None,
        started_callback=lambda _info: None,
        state_callback=lambda state, message: states.append((state, message)),
    )
    portal.create_callbacks[-1][0]("/portal/session/1")

    assert states[-1] == (
        CaptureState.ERROR,
        "PipeWire remote could not be opened",
    )
    assert portal.shutdown_count == 1


def test_portal_backend_rejects_callbacks_from_previous_attempt() -> None:
    portal = _Portal()
    transport = _Transport()
    backend = QtPortalScreenCastBackend(portal=portal, transport=transport)
    started: list[str] = []
    first = CaptureRequest("old", "1", 1, CaptureSourceType.WINDOW)
    second = CaptureRequest("new", "2", 2, CaptureSourceType.WINDOW)
    backend.start(
        first,
        frame_callback=lambda _frame: None,
        started_callback=lambda info: started.append(info.session_id),
        state_callback=lambda _state, _message: None,
    )
    old_callback = portal.create_callbacks[-1][0]
    backend.start(
        second,
        frame_callback=lambda _frame: None,
        started_callback=lambda info: started.append(info.session_id),
        state_callback=lambda _state, _message: None,
    )
    old_callback("/portal/session/old")
    portal.create_callbacks[-1][0]("/portal/session/new")
    assert started == ["new"]


def test_restore_failure_retries_without_saved_token(tmp_path: Path) -> None:
    class Backend:
        def __init__(self) -> None:
            self.requests = []

        def capabilities(self):
            return CaptureCapabilities(
                True,
                portal_version=6,
                source_types=frozenset({CaptureSourceType.WINDOW}),
                persistence_supported=True,
            )

        def start(self, request, **values):
            self.requests.append((request, values))

        def stop(self):
            return

    grants = CaptureGrantRepository(tmp_path / "grants.json")
    grants.save_token("208650", "saved-token")
    backend = Backend()
    provider = PortalScreenCaptureProvider(backend, grants)
    provider.start(
        CaptureRequest("s", "208650", 1, CaptureSourceType.WINDOW),
        frame_callback=lambda _frame: None,
        state_callback=lambda _state, _message: None,
    )
    backend.requests[0][1]["state_callback"](CaptureState.RESTORE_FAILED, "failed")
    assert [item[0].restore_token for item in backend.requests] == [
        "saved-token",
        "",
    ]


def test_pnm_decoder_handles_multiple_frames_and_pixel_whitespace() -> None:
    first = bytes([10, 32, 13, 1, 2, 3])
    second = bytes([4, 5, 6, 7, 8, 9])
    payload = b"P6\n# frame\n2 1\n255\n" + first + b"P6\n2 1\n255\n" + second
    decoder = PnmStreamDecoder()
    assert decoder.feed(payload[:17]) == ()
    frames = decoder.feed(payload[17:])
    assert frames == ((2, 1, first), (2, 1, second))


class _Process:
    def __init__(self, payload: bytes) -> None:
        self.stdout = BytesIO(payload)
        self.stderr = BytesIO()
        self.returncode = 0
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_gstreamer_transport_delivers_negotiated_frame_and_stops() -> None:
    pixels = bytes(range(18))
    process = _Process(b"P6\n3 2\n255\n" + pixels)
    ready = Event()
    frames = []
    transport = GStreamerPipeWireTransport(
        executable="/app/bin/gst-launch-1.0",
        inspect_executable="/app/bin/gst-inspect-1.0",
        probe_plugins=False,
        process_factory=lambda *_args, **_kwargs: process,
    )
    read_fd, write_fd = os.pipe()
    try:
        transport.start(
            remote_fd=read_fd,
            target_object="99",
            session_id="s",
            generation=4,
            sampling_hz=6.0,
            frame_callback=frames.append,
            ready_callback=ready.set,
            state_callback=lambda _state, _message: None,
        )
        assert ready.wait(2.0)
        assert (frames[0].width, frames[0].height, frames[0].stride) == (3, 2, 9)
        assert frames[0].pixels == pixels
    finally:
        transport.stop()
        os.close(read_fd)
        os.close(write_fd)


def _component_model(root: Path) -> Path:
    path = root / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    return path


def _bitmap_text_frame(text: str) -> CaptureFrame:
    patterns = {
        "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
        "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
        "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
        "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
        "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
        "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
        "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    }
    scale = 10
    width = (6 * len(text) + 2) * scale
    height = 11 * scale
    pixels = bytearray([255]) * (width * height)
    for index, character in enumerate(text):
        if character == " ":
            continue
        for row_index, row in enumerate(patterns[character]):
            for column_index, enabled in enumerate(row):
                if enabled != "1":
                    continue
                for y in range((row_index + 2) * scale, (row_index + 3) * scale):
                    start = y * width + (index * 6 + column_index + 1) * scale
                    pixels[start : start + scale] = b"\0" * scale
    return CaptureFrame(
        session_id="real",
        generation=1,
        timestamp_monotonic=time.monotonic(),
        width=width,
        height=height,
        stride=width,
        pixel_format="gray8",
        pixels=bytes(pixels),
    )


def test_ocr_provider_reports_missing_component(tmp_path: Path) -> None:
    provider = TesseractOcrProvider(tmp_path, executable="/usr/bin/tesseract")
    assert provider.available is False
    with pytest.raises(RuntimeError, match="not installed"):
        provider.recognize(_frame(), language="en")


def test_verified_ocr_component_download_install_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"verified-english-model"
    definition = NarratorComponentDefinition(
        component_id=TESSERACT_COMPONENT_ID,
        kind=NarratorComponentKind.OCR,
        name="English OCR",
        license_id="Apache-2.0",
        runtime_license_id="Apache-2.0",
        artifact_license_id="Apache-2.0",
        version="test",
        download_size_bytes=len(payload),
        source_url="https://raw.githubusercontent.com/example/model/eng.traineddata",
        sha256=hashlib.sha256(payload).hexdigest(),
        target_relative_path=TESSERACT_MODEL_RELATIVE_PATH.as_posix(),
        install_ready=True,
    )

    class Response(BytesIO):
        def geturl(self):
            return definition.source_url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        "game_optimization_linux.services.narrator_components.urlopen",
        lambda *_args, **_kwargs: Response(payload),
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))
    manager.install(TESSERACT_COMPONENT_ID)
    installed = tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    assert installed.read_bytes() == payload
    component = manager.status(TESSERACT_COMPONENT_ID)
    assert component.managed is True
    assert component.runtime_license_id == "Apache-2.0"
    assert component.artifact_license_id == "Apache-2.0"
    assert manager.remove(TESSERACT_COMPONENT_ID) is True
    assert not installed.exists()


def test_ocr_initialization_failure_is_recoverable(tmp_path: Path) -> None:
    _component_model(tmp_path)
    provider = TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, b"", b"model initialization failed"
        ),
    )
    with pytest.raises(RuntimeError, match="initialization failed"):
        provider.recognize(_frame(), language="en")


def test_ocr_provider_serializes_frame_and_parses_confidence(tmp_path: Path) -> None:
    _component_model(tmp_path)
    calls = []
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t8\t92\tHello\n"
        "5\t1\t1\t1\t1\t2\t11\t0\t12\t8\t88\tthere\n"
    )

    def runner(argv, **values):
        calls.append((argv, values))
        assert values["input"].startswith(b"\x89PNG")
        return subprocess.CompletedProcess(argv, 0, tsv.encode(), b"")

    provider = TesseractOcrProvider(
        tmp_path, executable="/usr/bin/tesseract", runner=runner
    )
    result = provider.recognize(_frame(), language="en")
    assert result.text == "Hello there"
    assert result.confidence == pytest.approx(0.9)
    assert "--tessdata-dir" in calls[0][0]


def test_real_tesseract_provider_boundary_when_runtime_and_model_are_available(
    tmp_path: Path,
) -> None:
    executable = shutil.which("tesseract")
    model_candidates = [
        Path("/tmp/eng.traineddata"),
        Path("/usr/share/tessdata/eng.traineddata"),
        Path("/usr/share/tesseract-ocr/5/tessdata/eng.traineddata"),
    ]
    model = next((path for path in model_candidates if path.is_file()), None)
    if executable is None or model is None:
        pytest.skip("real Tesseract runtime/model are not available")
    target = _component_model(tmp_path)
    shutil.copyfile(model, target)
    frame = _bitmap_text_frame("HELLO GAME")
    result = TesseractOcrProvider(tmp_path, executable=executable).recognize(
        frame, language="en"
    )
    assert result.text.upper() == "HELLO GAME"
    assert result.confidence is not None


class _Capture:
    provider_id = "capture"

    def __init__(self) -> None:
        self.frame_callback = None
        self.state_callback = None
        self.stop_count = 0

    def capabilities(self):
        return CaptureCapabilities(
            True,
            source_types=frozenset({CaptureSourceType.WINDOW}),
        )

    def start(self, request, *, frame_callback, state_callback):
        self.request = request
        self.frame_callback = frame_callback
        self.state_callback = state_callback
        state_callback(CaptureState.ACTIVE, "")

    def stop(self):
        self.stop_count += 1


class _Activity:
    active = True

    def is_active(self, game_key: str):
        del game_key
        return self.active


class _Ocr:
    provider_id = "ocr"
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, frame, *, language):
        del frame, language
        self.calls += 1
        return OcrResult("An English subtitle", 0.95, "ocr", 12.0)


def test_full_pipeline_cannot_start_with_only_capture_and_ocr(
    tmp_path: Path,
) -> None:
    capture = _Capture()
    activity = _Activity()
    ocr = _Ocr()
    clock = [1.0]
    pipeline = NarratorPipeline(
        capture,
        ocr,
        UnavailableTranslationProvider(),
        UnavailableTtsProvider(),
        UnavailableAudioOutput(),
        activity,
        TranslationCache(tmp_path / "translations.sqlite3"),
        clock=lambda: clock[0],
    )
    settings = replace(
        NarratorGameSettings.default("208650"),
        enabled=True,
        subtitle_region=replace(
            NarratorGameSettings.default("208650").subtitle_region,
            x=0,
            y=0,
            width=1,
            height=1,
        ),
        stabilization_ms=50,
    )
    with pytest.raises(RuntimeError, match="translation, tts, audio"):
        pipeline.start(settings)

    assert capture.frame_callback is None
    pipeline.shutdown()
