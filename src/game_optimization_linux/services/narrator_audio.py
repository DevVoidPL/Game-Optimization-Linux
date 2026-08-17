"""Qt audio output for synthesized narrator PCM."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Any

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from game_optimization_linux.models.narrator import PcmAudio


@dataclass(slots=True)
class _Playback:
    audio: PcmAudio
    volume: float
    request_id: int
    started_callback: Callable[[float], None]
    completed_callback: Callable[[], None]
    error_callback: Callable[[str], None]
    queued_at: float


class QtNarratorAudioOutput(QObject):
    """Play PCM without temporary files and keep only the newest queued line."""

    provider_id = "qt-audio"

    _playRequested = Signal(object)
    _stopRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None
        self._bytes: QByteArray | None = None
        self._current: _Playback | None = None
        self._pending: _Playback | None = None
        self._playRequested.connect(
            self._queue_playback, Qt.ConnectionType.QueuedConnection
        )
        self._stopRequested.connect(self._stop_all, Qt.ConnectionType.QueuedConnection)

    @property
    def available(self) -> bool:
        return True

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
            self._pending = playback
            return
        self._start(playback)

    @Slot()
    def _stop_all(self) -> None:
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
        device = QMediaDevices.defaultAudioOutput()
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
        self._sink = QAudioSink(device, audio_format, self)
        self._sink.setVolume(playback.volume)
        self._sink.stateChanged.connect(self._state_changed)
        self._current = playback
        self._sink.start(self._buffer)
        if self._current is playback:
            playback.started_callback(
                max(0.0, (time.monotonic() - playback.queued_at) * 1000.0)
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
        if (
            state == QtAudio.State.StoppedState
            and current is not None
            and sink is not None
            and sink.error() != QtAudio.Error.NoError
        ):
            self._notify_failure(current, "Narrator audio playback failed")
        completed = state == QtAudio.State.IdleState and current is not None
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
