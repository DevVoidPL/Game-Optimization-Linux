"""GStreamer transport for portal-provided PipeWire streams."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event, Lock, Thread, current_thread
import time

from game_optimization_linux.models.narrator import CaptureFrame, CaptureState


logger = logging.getLogger(__name__)

_PNM_MAGIC = b"P6"
_MAX_FRAME_BYTES = 128 * 1024 * 1024

# pipewiresrc advertises ANY caps and derives what it offers PipeWire from what
# is *downstream* of it. handle_format_change() in src/gst/gstpipewiresrc.c
# intersects the format PipeWire actually chose with those downstream caps, and
# when the intersection is empty it aborts the stream with
# pw_stream_set_error(-EINVAL, "unhandled format").
#
# A system-memory-only tail (queue ! videoconvert ! ...) therefore cannot
# negotiate at all when the compositor hands out DMA-BUF, which is common for
# windowed capture. These variants are tried in order so the DMA-BUF case can
# negotiate, while both still end in the system-memory RGB the OCR path expects.
_DMABUF_VARIANT = "gl-dmabuf"
_SYSTEM_VARIANT = "system-memory"

# System memory FIRST, and it is the default. That path has worked in practice;
# "unhandled format" was seen once, and making GL the default to cover that edge
# case broke the common case with "Failed to upload buffer". GL is a fallback,
# never a default, and element presence alone must never select it: the elements
# can be installed and still fail at runtime on a given compositor.
_VARIANT_ORDER = (_SYSTEM_VARIANT, _DMABUF_VARIANT)

# Elements used only by the GL fallback. Presence is a precondition for trying
# it, not a reason to prefer it.
_GL_ELEMENTS = ("glupload", "glcolorconvert", "gldownload")

# Restart policy for recoverable stream errors. Renegotiation caused by a window
# resize, alt-tab or a menu transition can kill the stream even though a fresh
# one would succeed, and no downstream counter reveals it because no frames
# arrive at all. Bounded, so a permanent failure stays visible.
_MAX_STREAM_RESTARTS = 4
_RESTART_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 4.0)

# Negotiation failed because PipeWire offered a format the current tail cannot
# accept. On the system-memory tail this is the DMA-BUF signature, and the only
# thing that justifies trying the GL fallback.
_DMABUF_SIGNATURE_MARKERS = (
    "unhandled format",
    "no supported formats found",
)

# GL failed at runtime despite the elements being installed. The GstVideoMeta
# versus caps format mismatch is typical of DMA-BUF with modifiers. These must
# cause a switch back to system memory, not another attempt at the same tail.
_GL_FAILURE_MARKERS = (
    "failed to upload buffer",
    "gstgluploadelement",
    "gluploadelement",
    "gst_video_frame_map",
    "gst_value_get_fraction_denominator",
    "info->finfo->format == meta->format",
    "gst_value_holds_fraction",
)

# Transient failures worth retrying on the SAME variant.
_RECOVERABLE_ERROR_MARKERS = (
    "not-negotiated",
    "streaming stopped, reason not-negotiated",
    "the pipewire capture stream closed",
)


class PnmStreamDecoder:
    """Split a byte stream containing consecutive binary PPM images."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> tuple[tuple[int, int, bytes], ...]:
        self._buffer.extend(data)
        images: list[tuple[int, int, bytes]] = []
        while True:
            start = self._buffer.find(_PNM_MAGIC)
            if start < 0:
                if len(self._buffer) > len(_PNM_MAGIC):
                    del self._buffer[: -len(_PNM_MAGIC)]
                break
            if start:
                del self._buffer[:start]
            header = self._header()
            if header is None:
                break
            width, height, data_start = header
            frame_size = width * height * 3
            if frame_size <= 0 or frame_size > _MAX_FRAME_BYTES:
                del self._buffer[: len(_PNM_MAGIC)]
                continue
            end = data_start + frame_size
            if len(self._buffer) < end:
                break
            images.append((width, height, bytes(self._buffer[data_start:end])))
            del self._buffer[:end]
        return tuple(images)

    def _header(self) -> tuple[int, int, int] | None:
        tokens: list[bytes] = []
        position = 0
        length = len(self._buffer)
        while len(tokens) < 4:
            while position < length and self._buffer[position] in b" \t\r\n":
                position += 1
            if position >= length:
                return None
            if self._buffer[position] == ord("#"):
                newline = self._buffer.find(b"\n", position)
                if newline < 0:
                    return None
                position = newline + 1
                continue
            end = position
            while end < length and self._buffer[end] not in b" \t\r\n":
                end += 1
            if end >= length:
                return None
            tokens.append(bytes(self._buffer[position:end]))
            position = end
        if tokens[0] != _PNM_MAGIC or tokens[3] != b"255":
            del self._buffer[: len(_PNM_MAGIC)]
            return None
        if position >= length or self._buffer[position] not in b" \t\r\n":
            return None
        if (
            self._buffer[position] == ord("\r")
            and position + 1 < length
            and self._buffer[position + 1] == ord("\n")
        ):
            position += 2
        else:
            position += 1
        try:
            return int(tokens[1]), int(tokens[2]), position
        except ValueError:
            del self._buffer[: len(_PNM_MAGIC)]
            return None


class GStreamerPipeWireTransport:
    """Convert a portal PipeWire stream to bounded RGB capture frames."""

    def __init__(
        self,
        executable: str | Path | None = None,
        *,
        inspect_executable: str | Path | None = None,
        probe_plugins: bool = True,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executable = str(executable or shutil.which("gst-launch-1.0") or "")
        self._inspect_executable = str(
            inspect_executable or shutil.which("gst-inspect-1.0") or ""
        )
        self._process_factory = process_factory
        self._clock = clock
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: Thread | None = None
        self._stderr_reader: Thread | None = None
        self._stopping = Event()
        self._stderr_tail = bytearray()
        self._ready = False
        self._attempt = 0
        self._unavailable_reason = ""
        # Capture diagnostics. frames_received matters most: it separates "no
        # subtitles were recognised" from "no frames ever arrived".
        self._gl_elements_present = False
        self._dmabuf_suspected = False
        self._failed_variants: set[str] = set()
        self._tried_variants: list[str] = []
        self._negotiated_variant = ""
        self._frames_received = 0
        self._stream_errors = 0
        self._restarts = 0
        self._restart_pending = False
        self._last_start: dict[str, object] | None = None
        if probe_plugins:
            self._probe_runtime()

    @property
    def dmabuf_supported(self) -> bool:
        """Whether the GL fallback could be attempted at all.

        Informational only. This must never decide which variant runs: the GL
        elements can be installed and still fail at runtime.
        """

        return self._gl_elements_present

    @property
    def tried_variants(self) -> tuple[str, ...]:
        return tuple(self._tried_variants)

    @property
    def failed_variants(self) -> tuple[str, ...]:
        return tuple(
            variant for variant in _VARIANT_ORDER if variant in self._failed_variants
        )

    @property
    def negotiated_variant(self) -> str:
        """Which pipeline variant the running stream negotiated."""

        return self._negotiated_variant

    @property
    def frames_received(self) -> int:
        return self._frames_received

    @property
    def stream_errors(self) -> int:
        return self._stream_errors

    @property
    def restarts(self) -> int:
        return self._restarts

    @property
    def available(self) -> bool:
        return bool(self._executable and not self._unavailable_reason)

    @property
    def message(self) -> str:
        if self.available:
            return "GStreamer PipeWire capture is available"
        return self._unavailable_reason or "GStreamer is unavailable in the application runtime"

    def _probe_runtime(self) -> None:
        if not self._executable or not self._inspect_executable:
            self._unavailable_reason = "GStreamer is unavailable in the application runtime"
            return
        for element in ("pipewiresrc", "queue", "videoconvert", "videorate", "pnmenc"):
            try:
                completed = subprocess.run(
                    [self._inspect_executable, element],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                self._unavailable_reason = f"GStreamer capability check failed: {error}"
                return
            if completed.returncode != 0:
                self._unavailable_reason = f"GStreamer element {element} is unavailable"
                return
        # The GL elements are optional: without them DMA-BUF streams cannot be
        # negotiated, but system-memory capture still works, so a missing GL
        # stack must not mark the whole transport unavailable.
        self._gl_elements_present = self._probe_optional_elements(_GL_ELEMENTS)

    def _probe_optional_elements(self, elements: tuple[str, ...]) -> bool:
        for element in elements:
            try:
                completed = subprocess.run(
                    [self._inspect_executable, element],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            if completed.returncode != 0:
                logger.info(
                    "Narrator capture: %s is unavailable, DMA-BUF screen capture "
                    "cannot be negotiated on this runtime",
                    element,
                )
                return False
        return True

    def _select_variant(self) -> str:
        """Pick the pipeline tail to attempt next.

        System memory is the default and is always tried first. The GL tail is
        only eligible once a negotiation failure has actually shown the DMA-BUF
        signature *and* the GL elements exist. Each variant gets one fair
        attempt; once a variant has failed it is never selected again this
        session, so the two cannot ping-pong.
        """

        for variant in _VARIANT_ORDER:
            if variant in self._failed_variants:
                continue
            if variant == _DMABUF_VARIANT and not (
                self._dmabuf_suspected and self._gl_elements_present
            ):
                continue
            return variant
        return ""

    def _build_argv(
        self,
        *,
        inherited_fd: int,
        target_property: str,
        target_object: str,
        numerator: int,
        variant: str,
    ) -> list[str]:
        """Build the capture pipeline.

        Both variants end in system-memory ``video/x-raw,format=RGB`` followed by
        pnmenc, so the frames handed to the OCR path stay byte-identical rgb888.
        Only the negotiation surface offered to pipewiresrc differs.
        """

        source = [
            "pipewiresrc",
            f"fd={inherited_fd}",
            f"{target_property}={target_object}",
            "do-timestamp=true",
            "!",
            "queue",
            "leaky=downstream",
            "max-size-buffers=1",
            "!",
        ]
        if variant == _DMABUF_VARIANT:
            # glupload accepts both video/x-raw(memory:DMABuf) and plain
            # video/x-raw, so pipewiresrc can negotiate either. gldownload brings
            # the frame back to system memory for pnmenc.
            convert = [
                "glupload",
                "!",
                "glcolorconvert",
                "!",
                "gldownload",
                "!",
                "videoconvert",
                "!",
            ]
        else:
            convert = ["videoconvert", "!"]
        tail = [
            "videorate",
            "drop-only=true",
            "!",
            f"video/x-raw,format=RGB,framerate={numerator}/1",
            "!",
            "pnmenc",
            "!",
            "fdsink",
            "fd=1",
            "sync=false",
        ]
        return [self._executable, "-q", *source, *convert, *tail]

    def start(
        self,
        *,
        remote_fd: int,
        target_object: str,
        target_is_serial: bool = True,
        session_id: str,
        generation: int,
        sampling_hz: float,
        frame_callback: Callable[[CaptureFrame], None],
        ready_callback: Callable[[], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        self.stop()
        if not self.available:
            raise RuntimeError(self.message)
        if remote_fd < 0 or not target_object:
            raise ValueError("PipeWire capture needs a remote descriptor and target")
        numerator = max(1, min(10, round(float(sampling_hz))))
        inherited_fd = os.dup(remote_fd)
        target_property = "target-object" if target_is_serial else "path"
        variant = self._select_variant()
        if not variant:
            os.close(inherited_fd)
            raise RuntimeError(
                "PipeWire capture has no untried pipeline variant left "
                f"(failed: {', '.join(self.failed_variants) or 'none'})"
            )
        argv = self._build_argv(
            inherited_fd=inherited_fd,
            target_property=target_property,
            target_object=target_object,
            numerator=numerator,
            variant=variant,
        )
        # Logged on every negotiation attempt: which tail, exactly what caps were
        # requested, and whether the GL fallback is even possible. The negotiated
        # caps and DMA-BUF presence are reported by _read_stderr/_read_frames once
        # the stream answers, so a real run shows requested versus actual.
        logger.info(
            "Narrator PipeWire capture attempt variant=%s requested_caps=%r "
            "gl_elements_present=%s dmabuf_suspected=%s tried=%s failed=%s "
            "target=%s sampling=%d/1 pipeline=%s",
            variant,
            f"video/x-raw,format=RGB,framerate={numerator}/1",
            self._gl_elements_present,
            self._dmabuf_suspected,
            ",".join(self._tried_variants) or "none",
            ",".join(self.failed_variants) or "none",
            target_object,
            numerator,
            " ".join(argv[2:]),
        )
        try:
            process = self._process_factory(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(inherited_fd,),
                close_fds=True,
            )
        except Exception:
            os.close(inherited_fd)
            raise
        os.close(inherited_fd)
        with self._lock:
            self._attempt += 1
            attempt = self._attempt
            self._process = process
            self._ready = False
            self._stopping.clear()
            self._stderr_tail.clear()
            self._negotiated_variant = variant
            if not self._restart_pending:
                # A fresh session, not an automatic restart: reset the counters
                # and give every variant a clean attempt again.
                self._frames_received = 0
                self._stream_errors = 0
                self._restarts = 0
                self._failed_variants.clear()
                self._tried_variants.clear()
                self._dmabuf_suspected = False
            self._restart_pending = False
            if variant not in self._tried_variants:
                self._tried_variants.append(variant)
            # Remembered so a recoverable stream error can be retried with the
            # same target and callbacks.
            self._last_start = {
                "remote_fd": remote_fd,
                "target_object": target_object,
                "target_is_serial": target_is_serial,
                "session_id": session_id,
                "generation": generation,
                "sampling_hz": sampling_hz,
                "frame_callback": frame_callback,
                "ready_callback": ready_callback,
                "state_callback": state_callback,
            }
        self._reader = Thread(
            target=self._read_frames,
            args=(
                process,
                attempt,
                session_id,
                generation,
                target_object,
                frame_callback,
                ready_callback,
                state_callback,
            ),
            name="narrator-pipewire-frames",
            daemon=True,
        )
        self._stderr_reader = Thread(
            target=self._read_stderr,
            args=(process, attempt),
            name="narrator-pipewire-errors",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()

    def stop(self) -> None:
        with self._lock:
            self._attempt += 1
            process = self._process
            self._process = None
            self._stopping.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for stream in (
            process.stdout if process is not None else None,
            process.stderr if process is not None else None,
        ):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in (self._reader, self._stderr_reader):
            if (
                thread is not None
                and thread is not current_thread()
                and thread.is_alive()
            ):
                thread.join(timeout=1.0)
        self._reader = None
        self._stderr_reader = None

    def _read_frames(
        self,
        process: subprocess.Popen[bytes],
        attempt: int,
        session_id: str,
        generation: int,
        source_id: str,
        frame_callback: Callable[[CaptureFrame], None],
        ready_callback: Callable[[], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        decoder = PnmStreamDecoder()
        stdout = process.stdout
        if stdout is None:
            self._stream_failed(attempt, state_callback, "GStreamer did not expose frame output")
            return
        try:
            while not self._stopping.is_set():
                chunk = stdout.read(64 * 1024)
                if not chunk:
                    break
                for width, height, pixels in decoder.feed(chunk):
                    frame = CaptureFrame(
                        session_id=session_id,
                        generation=generation,
                        timestamp_monotonic=self._clock(),
                        width=width,
                        height=height,
                        stride=width * 3,
                        pixel_format="rgb888",
                        pixels=pixels,
                        source_id=source_id,
                    )
                    with self._lock:
                        if attempt != self._attempt or process is not self._process:
                            return
                        first_frame = not self._ready
                        self._ready = True
                        self._frames_received += 1
                    if first_frame:
                        detail = bytes(self._stderr_tail).decode(
                            "utf-8", errors="replace"
                        )
                        logger.info(
                            "Narrator PipeWire capture NEGOTIATED variant=%s "
                            "outcome=frames_flowing negotiated_frame=%dx%d "
                            "negotiated_format=rgb888 dmabuf_in_stream=%s "
                            "gl_elements_present=%s",
                            self._negotiated_variant,
                            width,
                            height,
                            "memory:DMABuf" in detail,
                            self._gl_elements_present,
                        )
                    if first_frame:
                        ready_callback()
                    frame_callback(frame)
        except Exception as error:
            logger.warning("PipeWire frame transport failed: %s", error)
            self._stream_failed(attempt, state_callback, f"PipeWire capture failed: {error}")
            return
        if self._stopping.is_set():
            return
        code = process.poll()
        detail = bytes(self._stderr_tail).decode("utf-8", errors="replace").strip()
        message = "The PipeWire capture stream closed"
        if code not in {None, 0} and detail:
            message = f"GStreamer PipeWire capture failed: {detail[-600:]}"
        self._stream_failed(attempt, state_callback, message)

    def _read_stderr(self, process: subprocess.Popen[bytes], attempt: int) -> None:
        stderr = process.stderr
        if stderr is None:
            return
        while not self._stopping.is_set():
            chunk = stderr.read(4096)
            if not chunk:
                return
            with self._lock:
                if attempt != self._attempt:
                    return
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 8192:
                    del self._stderr_tail[:-8192]

    @staticmethod
    def _indicates_dmabuf(message: str) -> bool:
        """The system-memory tail could not accept what PipeWire offered."""

        lowered = message.casefold()
        return any(marker in lowered for marker in _DMABUF_SIGNATURE_MARKERS)

    @staticmethod
    def _indicates_gl_failure(message: str) -> bool:
        """GL is installed but cannot handle this buffer at runtime."""

        lowered = message.casefold()
        return any(marker in lowered for marker in _GL_FAILURE_MARKERS)

    @classmethod
    def _is_recoverable(cls, message: str) -> bool:
        """Whether the SAME variant is worth retrying.

        Format and GL failures are handled by switching variant instead, so they
        are deliberately not in this set: retrying an identical broken pipeline
        just burns the restart budget.
        """

        lowered = message.casefold()
        return any(marker in lowered for marker in _RECOVERABLE_ERROR_MARKERS)

    def _stream_failed(
        self,
        attempt: int,
        callback: Callable[[CaptureState, str], None],
        message: str,
    ) -> None:
        with self._lock:
            if attempt != self._attempt or self._stopping.is_set():
                return
            self._stream_errors += 1
            errors = self._stream_errors
            restarts = self._restarts
            parameters = dict(self._last_start or {})
            variant = self._negotiated_variant
        gl_failed = self._indicates_gl_failure(message)
        dmabuf_signature = self._indicates_dmabuf(message)
        if (gl_failed or dmabuf_signature) and parameters:
            # The variant itself is wrong for this compositor. Retire it and try
            # the other one; do not spend the restart budget on the same tail.
            with self._lock:
                if variant:
                    self._failed_variants.add(variant)
                if dmabuf_signature:
                    self._dmabuf_suspected = True
                next_variant = self._select_variant()
            logger.info(
                "Narrator PipeWire capture variant=%s FAILED "
                "(gl_failure=%s dmabuf_signature=%s stream_errors=%d): %s",
                variant or "unknown",
                gl_failed,
                dmabuf_signature,
                errors,
                message,
            )
            if next_variant:
                logger.info(
                    "Narrator PipeWire capture falling back to variant=%s",
                    next_variant,
                )
                callback(
                    CaptureState.STARTING,
                    f"Retrying capture using the {next_variant} pipeline",
                )
                Thread(
                    target=self._restart_after,
                    args=(0.2, parameters, message, callback),
                    name="narrator-pipewire-variant-fallback",
                    daemon=True,
                ).start()
                return
            callback(
                CaptureState.SOURCE_LOST,
                f"{message} (no working capture pipeline: tried "
                f"{', '.join(self.tried_variants) or 'none'})",
            )
            return
        recoverable = self._is_recoverable(message) and bool(parameters)
        if recoverable and restarts < _MAX_STREAM_RESTARTS:
            delay = _RESTART_BACKOFF_SECONDS[
                min(restarts, len(_RESTART_BACKOFF_SECONDS) - 1)
            ]
            logger.info(
                "Narrator PipeWire capture restarting after recoverable error "
                "(%d/%d, backoff %.1fs, stream_errors=%d): %s",
                restarts + 1,
                _MAX_STREAM_RESTARTS,
                delay,
                errors,
                message,
            )
            callback(CaptureState.STARTING, "Reconnecting the capture stream")
            Thread(
                target=self._restart_after,
                args=(delay, parameters, message, callback),
                name="narrator-pipewire-restart",
                daemon=True,
            ).start()
            return
        if recoverable:
            # The retry budget is spent: report the persistent failure rather
            # than looping forever or hiding it.
            message = (
                f"{message} (capture did not recover after "
                f"{_MAX_STREAM_RESTARTS} restarts)"
            )
        callback(CaptureState.SOURCE_LOST, message)

    def _restart_after(
        self,
        delay: float,
        parameters: dict[str, object],
        message: str,
        callback: Callable[[CaptureState, str], None],
    ) -> None:
        if self._stopping.wait(delay):
            return
        with self._lock:
            if self._stopping.is_set():
                return
            self._restarts += 1
            self._restart_pending = True
        try:
            self.start(**parameters)  # type: ignore[arg-type]
        except Exception as error:
            with self._lock:
                self._restart_pending = False
            logger.warning("Narrator PipeWire capture restart failed: %s", error)
            callback(
                CaptureState.SOURCE_LOST,
                f"{message} (restart failed: {error})",
            )

__all__ = ["GStreamerPipeWireTransport", "PnmStreamDecoder"]
