"""Qt audio output for synthesized narrator PCM."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Any

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from game_optimization_linux.models.narrator import PcmAudio


logger = logging.getLogger(__name__)


_SAMPLE_WIDTHS = {"u8": 1, "s16le": 2, "s32le": 4, "f32le": 4}

# A sink may report marginally less processed audio than was submitted even on a
# clean finish, so require almost all of it rather than exactly all.
_COMPLETION_RATIO = 0.98


@dataclass(slots=True)
class _Playback:
    audio: PcmAudio
    volume: float
    request_id: int
    started_callback: Callable[[float], None]
    completed_callback: Callable[[], None]
    error_callback: Callable[[str], None]
    queued_at: float
    started_at: float = 0.0

    @property
    def expected_seconds(self) -> float:
        """Duration implied by the PCM itself, independent of backend state."""

        width = _SAMPLE_WIDTHS.get(self.audio.sample_format.casefold(), 2)
        frame_bytes = max(1, width * max(1, self.audio.channels))
        rate = max(1, self.audio.sample_rate)
        return len(self.audio.samples) / frame_bytes / rate


@dataclass(frozen=True, slots=True)
class PlaybackRecord:
    """One playback attempt, for diagnosing truncated speech."""

    request_id: int
    pcm_bytes: int
    sample_rate: int
    channels: int
    sample_format: str
    bytes_per_sample: int
    expected_seconds: float
    processed_seconds: float
    elapsed_seconds: float
    terminal_state: str
    backend_error: str
    stop_requested: bool
    superseded: bool
    result: str
    reason: str


class QtNarratorAudioOutput(QObject):
    """Play PCM without temporary files and keep only the newest queued line."""

    provider_id = "qt-audio"

    _playRequested = Signal(object)
    _stopRequested = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        sink_factory: Callable[..., Any] | None = None,
        device_provider: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        # Injectable so truncated playback can be reproduced without a device.
        self._sink_factory = sink_factory or QAudioSink
        self._device_provider = device_provider or QMediaDevices.defaultAudioOutput
        self._clock = clock
        self._history: deque[PlaybackRecord] = deque(maxlen=10)
        self._completed_count = 0
        self._interrupted_count = 0
        self._stop_requested = False
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None
        self._bytes: QByteArray | None = None
        self._current: _Playback | None = None
        self._pending: _Playback | None = None
        self._superseded_count = 0
        self._playRequested.connect(
            self._queue_playback, Qt.ConnectionType.QueuedConnection
        )
        self._stopRequested.connect(self._stop_all, Qt.ConnectionType.QueuedConnection)

    @property
    def available(self) -> bool:
        return True

    @property
    def completed_count(self) -> int:
        """Utterances whose PCM was actually played to the end."""

        return self._completed_count

    @property
    def interrupted_count(self) -> int:
        """Utterances that stopped before their PCM was consumed."""

        return self._interrupted_count

    @property
    def playback_history(self) -> tuple[PlaybackRecord, ...]:
        return tuple(self._history)

    @property
    def last_playback(self) -> PlaybackRecord | None:
        return self._history[-1] if self._history else None

    @property
    def superseded_count(self) -> int:
        """Number of synthesized, not-yet-playing lines replaced by newer work."""

        return self._superseded_count

    def play(
        self,
        audio: PcmAudio,
        *,
        volume: float,
        request_id: int,
        started_callback: Callable[[float], None],
        completed_callback: Callable[[], None],
        error_callback: Callable[[str], None],
    ) -> None:
        self._playRequested.emit(
            _Playback(
                audio=audio,
                volume=max(0.0, min(1.0, float(volume))),
                request_id=request_id,
                started_callback=started_callback,
                completed_callback=completed_callback,
                error_callback=error_callback,
                queued_at=time.monotonic(),
            )
        )

    def stop(self) -> None:
        self._stopRequested.emit()

    @Slot(object)
    def _queue_playback(self, playback: object) -> None:
        if not isinstance(playback, _Playback):
            return
        if self._current is not None:
            if self._pending is not None:
                self._superseded_count += 1
            self._pending = playback
            return
        self._start(playback)

    @Slot()
    def _stop_all(self) -> None:
        # An explicit stop is intentional. Record it so the terminal state that
        # follows is classified as a stop rather than a truncated utterance, and
        # so it never triggers a replay.
        current = self._current
        if current is not None:
            self._stop_requested = True
            self._record(
                current,
                processed_seconds=self._processed_seconds(self._sink)
                if self._sink is not None
                else 0.0,
                elapsed_seconds=(
                    max(0.0, self._clock() - current.started_at)
                    if current.started_at
                    else 0.0
                ),
                terminal_state="StopRequested",
                backend_error="NoError",
                result="stopped",
                reason="session or user stop",
            )
        self._pending = None
        self._current = None
        sink = self._sink
        buffer = self._buffer
        self._sink = None
        self._buffer = None
        self._bytes = None
        if sink is not None:
            sink.stateChanged.disconnect(self._state_changed)
            sink.stop()
            sink.deleteLater()
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()

    def _start(self, playback: _Playback) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(playback.audio.sample_rate)
        audio_format.setChannelCount(playback.audio.channels)
        sample_formats = {
            "u8": QAudioFormat.SampleFormat.UInt8,
            "s16le": QAudioFormat.SampleFormat.Int16,
            "s32le": QAudioFormat.SampleFormat.Int32,
            "f32le": QAudioFormat.SampleFormat.Float,
        }
        sample_format = sample_formats.get(playback.audio.sample_format.casefold())
        if sample_format is None:
            self._notify_failure(
                playback,
                f"Unsupported narrator PCM format: {playback.audio.sample_format}",
            )
            return
        audio_format.setSampleFormat(sample_format)
        device = self._device_provider()
        if device.isNull():
            self._notify_failure(playback, "No audio output device is available")
            return
        if not device.isFormatSupported(audio_format):
            self._notify_failure(
                playback,
                "The audio output does not support the narrator PCM format",
            )
            return
        self._bytes = QByteArray(playback.audio.samples)
        self._buffer = QBuffer(self._bytes, self)
        if not self._buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            self._buffer = None
            self._bytes = None
            self._notify_failure(
                playback, "Narrator PCM playback buffer could not be opened"
            )
            return
        self._sink = self._sink_factory(device, audio_format, self)
        self._sink.setVolume(playback.volume)
        self._sink.stateChanged.connect(self._state_changed)
        playback.started_at = self._clock()
        self._stop_requested = False
        self._current = playback
        self._sink.start(self._buffer)
        if self._current is playback:
            playback.started_callback(
                max(0.0, (self._clock() - playback.queued_at) * 1000.0)
            )

    @Slot()
    def _state_changed(self) -> None:
        sink = self._sink
        if sink is None:
            return
        state = sink.state()
        if state not in {QtAudio.State.IdleState, QtAudio.State.StoppedState}:
            return
        current = self._current
        # IdleState only means the sink has no data to process right now. That
        # happens on an underrun as well as at true end of stream, so it must
        # never count as success on its own: compare what the sink actually
        # processed against the duration the PCM implies.
        processed_seconds = 0.0
        backend_error = "NoError"
        expected_seconds = 0.0
        elapsed_seconds = 0.0
        if current is not None:
            processed_seconds = self._processed_seconds(sink)
            backend_error = self._error_name(sink)
            expected_seconds = current.expected_seconds
            if current.started_at:
                elapsed_seconds = max(0.0, self._clock() - current.started_at)
        errored = backend_error != "NoError"
        played_everything = (
            expected_seconds <= 0.0
            or processed_seconds >= expected_seconds * _COMPLETION_RATIO
        )
        completed = (
            current is not None
            and state == QtAudio.State.IdleState
            and not errored
            and played_everything
        )
        if current is not None:
            if completed:
                result, reason = "completed", ""
            elif self._stop_requested:
                result, reason = "stopped", "session or user stop"
            elif errored:
                result, reason = "interrupted", f"backend error {backend_error}"
            else:
                result, reason = (
                    "interrupted",
                    f"{state.name} after {processed_seconds:.2f}s of "
                    f"{expected_seconds:.2f}s of PCM",
                )
            self._record(
                current,
                processed_seconds=processed_seconds,
                elapsed_seconds=elapsed_seconds,
                terminal_state=state.name,
                backend_error=backend_error,
                result=result,
                reason=reason,
            )
            if result == "completed":
                self._completed_count += 1
            elif result == "interrupted":
                self._interrupted_count += 1
                logger.warning(
                    "Narrator audio playback interrupted request=%d %s "
                    "(processed=%.2fs expected=%.2fs elapsed=%.2fs state=%s "
                    "error=%s)",
                    current.request_id,
                    reason,
                    processed_seconds,
                    expected_seconds,
                    elapsed_seconds,
                    state.name,
                    backend_error,
                )
        if current is not None and errored:
            self._notify_failure(current, "Narrator audio playback failed")
        pending = self._pending
        self._pending = None
        self._current = None
        sink = self._sink
        buffer = self._buffer
        self._sink = None
        self._buffer = None
        self._bytes = None
        if sink is not None:
            sink.stateChanged.disconnect(self._state_changed)
            sink.stop()
            sink.deleteLater()
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()
        if completed and current is not None:
            self._notify_completed(current)
        if pending is not None:
            self._start(pending)

    @staticmethod
    def _processed_seconds(sink: Any) -> float:
        """How much audio the backend actually processed."""

        try:
            return max(0.0, float(sink.processedUSecs()) / 1_000_000.0)
        except Exception:
            return 0.0

    @staticmethod
    def _error_name(sink: Any) -> str:
        try:
            error = sink.error()
        except Exception:
            return "NoError"
        if error == QtAudio.Error.NoError:
            return "NoError"
        return getattr(error, "name", str(error))

    def _record(
        self,
        playback: _Playback,
        *,
        processed_seconds: float,
        elapsed_seconds: float,
        terminal_state: str,
        backend_error: str,
        result: str,
        reason: str,
    ) -> None:
        audio = playback.audio
        width = _SAMPLE_WIDTHS.get(audio.sample_format.casefold(), 2)
        self._history.append(
            PlaybackRecord(
                request_id=playback.request_id,
                pcm_bytes=len(audio.samples),
                sample_rate=audio.sample_rate,
                channels=audio.channels,
                sample_format=audio.sample_format,
                bytes_per_sample=width,
                expected_seconds=playback.expected_seconds,
                processed_seconds=processed_seconds,
                elapsed_seconds=elapsed_seconds,
                terminal_state=terminal_state,
                backend_error=backend_error,
                stop_requested=self._stop_requested,
                superseded=False,
                result=result,
                reason=reason,
            )
        )

    @staticmethod
    def _notify_failure(playback: _Playback, message: str) -> None:
        try:
            playback.error_callback(message)
        except Exception:
            return

    @staticmethod
    def _notify_completed(playback: _Playback) -> None:
        try:
            playback.completed_callback()
        except Exception:
            return


__all__ = ["QtNarratorAudioOutput"]
