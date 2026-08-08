"""Optional in-memory UI feedback tones; no audio assets or files are created."""

from __future__ import annotations

from array import array
import logging
import math
from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer


logger = logging.getLogger(__name__)


class UiSoundService(QObject):
    """Play short low-volume tones lazily when the user explicitly enables them."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        player: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._enabled = False
        self._player = player
        self._active: list[tuple[object, QBuffer]] = []
        self._audio_error_reported = False

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop()

    def play(self, kind: str) -> bool:
        if not self._enabled:
            return False
        normalized = str(kind).strip().casefold()
        if self._player is not None:
            self._player(normalized)
            return True
        try:
            self._play_tone(normalized)
        except Exception as error:
            if not self._audio_error_reported:
                logger.warning("Optional interface sounds are unavailable: %s", error)
                self._audio_error_reported = True
            return False
        return True

    def _play_tone(self, kind: str) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        frequency = {"accept": 720.0, "back": 420.0}.get(kind, 560.0)
        duration_ms = 48 if kind == "navigate" else 72
        sample_rate = 22_050
        frame_count = int(sample_rate * duration_ms / 1000)
        samples = array("h")
        for frame in range(frame_count):
            envelope = min(1.0, frame / 80.0, (frame_count - frame) / 120.0)
            value = int(2200 * envelope * math.sin(2.0 * math.pi * frequency * frame / sample_rate))
            samples.append(value)

        audio_format = QAudioFormat()
        audio_format.setSampleRate(sample_rate)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise RuntimeError("no audio output device")
        if not device.isFormatSupported(audio_format):
            raise RuntimeError("the audio device does not support the UI tone format")

        buffer = QBuffer(self)
        buffer.setData(QByteArray(samples.tobytes()))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("could not open the in-memory audio buffer")
        sink = QAudioSink(device, audio_format, self)
        sink.setVolume(0.12)
        self._active.append((sink, buffer))
        sink.start(buffer)
        QTimer.singleShot(duration_ms + 180, lambda: self._finish(sink, buffer))

    def _finish(self, sink: object, buffer: QBuffer) -> None:
        pair = (sink, buffer)
        if pair not in self._active:
            return
        self._active.remove(pair)
        stop = getattr(sink, "stop", None)
        if callable(stop):
            stop()
        buffer.close()
        delete_sink = getattr(sink, "deleteLater", None)
        if callable(delete_sink):
            delete_sink()
        buffer.deleteLater()

    def stop(self) -> None:
        for sink, buffer in tuple(self._active):
            self._finish(sink, buffer)


__all__ = ["UiSoundService"]
