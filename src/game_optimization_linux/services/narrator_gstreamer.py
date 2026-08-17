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
        if probe_plugins:
            self._probe_runtime()

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
        argv = [
            self._executable,
            "-q",
            "pipewiresrc",
            f"fd={inherited_fd}",
            f"{target_property}={target_object}",
            "do-timestamp=true",
            "!",
            "queue",
            "leaky=downstream",
            "max-size-buffers=1",
            "!",
            "videoconvert",
            "!",
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

    def _stream_failed(
        self,
        attempt: int,
        callback: Callable[[CaptureState, str], None],
        message: str,
    ) -> None:
        with self._lock:
            if attempt != self._attempt or self._stopping.is_set():
                return
        callback(CaptureState.SOURCE_LOST, message)

__all__ = ["GStreamerPipeWireTransport", "PnmStreamDecoder"]
