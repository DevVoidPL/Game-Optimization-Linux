"""Truncated narrator speech: completion must mean the PCM was actually played.

Real evidence: the accepted subtitle and the spoken phrase both held the complete
sentence, "Superseded in audio queue" stayed 0, and capture was healthy - yet only
about half the utterance was audible. So the loss happens after complete text
reached the audio path.

QAudioSink enters IdleState whenever it has no data to process, which includes a
buffer underrun, not only true end of stream. Treating IdleState as success is
therefore how a half-played utterance gets reported as finished.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtMultimedia import QtAudio

from game_optimization_linux.models.narrator import PcmAudio
from game_optimization_linux.services.narrator_audio import (
    QtNarratorAudioOutput,
    _Playback,
)


# Real Piper output format: 22050 Hz, mono, s16le.
_RATE = 22050
_CHANNELS = 1
_WIDTH = 2


def _audio(seconds: float) -> PcmAudio:
    frames = int(_RATE * seconds)
    return PcmAudio(
        samples=b"\x00\x01" * frames,
        sample_rate=_RATE,
        channels=_CHANNELS,
        sample_format="s16le",
    )


class _FakeSink:
    """Deterministic stand-in for QAudioSink."""

    def __init__(self, _device, _format, _parent=None) -> None:
        self.state_slot = None
        self._state = QtAudio.State.ActiveState
        self._error = QtAudio.Error.NoError
        self._processed_usecs = 0
        self.stopped = False
        self.stateChanged = SimpleNamespace(
            connect=self._connect, disconnect=lambda _slot: None
        )

    def _connect(self, slot) -> None:
        self.state_slot = slot

    def setVolume(self, _volume: float) -> None:
        return None

    def start(self, _device) -> None:
        self._state = QtAudio.State.ActiveState

    def stop(self) -> None:
        self.stopped = True

    def deleteLater(self) -> None:
        return None

    def state(self):
        return self._state

    def error(self):
        return self._error

    def processedUSecs(self) -> int:
        return self._processed_usecs

    # Test helpers -------------------------------------------------------
    def finish(self, played_seconds: float, *, state=None, error=None) -> None:
        self._processed_usecs = int(played_seconds * 1_000_000)
        self._state = state or QtAudio.State.IdleState
        if error is not None:
            self._error = error
        assert self.state_slot is not None
        self.state_slot()


class _Device:
    @staticmethod
    def isNull() -> bool:
        return False

    @staticmethod
    def isFormatSupported(_format) -> bool:
        return True


@pytest.fixture
def output() -> QtNarratorAudioOutput:
    sinks: list[_FakeSink] = []

    def factory(device, audio_format, parent=None):
        sink = _FakeSink(device, audio_format, parent)
        sinks.append(sink)
        return sink

    provider = QtNarratorAudioOutput(
        sink_factory=factory, device_provider=lambda: _Device()
    )
    provider.sinks = sinks  # type: ignore[attr-defined]
    return provider


def _play(provider: QtNarratorAudioOutput, seconds: float, request_id: int = 1):
    events: dict[str, object] = {"completed": 0, "errors": []}
    playback = _Playback(
        audio=_audio(seconds),
        volume=1.0,
        request_id=request_id,
        started_callback=lambda _ms: None,
        completed_callback=lambda: events.__setitem__(
            "completed", int(events["completed"]) + 1
        ),
        error_callback=lambda message: events["errors"].append(message),  # type: ignore[union-attr]
        queued_at=0.0,
    )
    provider._start(playback)
    return events


# ---------------------------------------------------------------------------
# Expected duration
# ---------------------------------------------------------------------------


def test_expected_duration_matches_real_22050_mono_s16le_format() -> None:
    playback = _Playback(
        audio=_audio(4.0),
        volume=1.0,
        request_id=1,
        started_callback=lambda _ms: None,
        completed_callback=lambda: None,
        error_callback=lambda _m: None,
        queued_at=0.0,
    )

    assert playback.expected_seconds == pytest.approx(4.0)
    # 4 s of 22050 Hz mono s16le is 176400 bytes.
    assert len(playback.audio.samples) == 4 * _RATE * _CHANNELS * _WIDTH


# ---------------------------------------------------------------------------
# The reproduction
# ---------------------------------------------------------------------------


def test_idle_halfway_through_pcm_is_interrupted_not_finished(
    output: QtNarratorAudioOutput,
) -> None:
    """The exact reported symptom: about half the sentence is audible."""

    events = _play(output, 4.0)

    # The sink goes idle having processed only half the audio: an underrun.
    output.sinks[0].finish(2.0)  # type: ignore[attr-defined]

    assert events["completed"] == 0, "a half-played utterance must not finish"
    assert output.completed_count == 0
    assert output.interrupted_count == 1
    record = output.last_playback
    assert record is not None
    assert record.expected_seconds == pytest.approx(4.0)
    assert record.processed_seconds == pytest.approx(2.0)
    assert record.result == "interrupted"
    assert "2.00s of 4.00s" in record.reason
    # No supersession was involved, yet the loss is still detected.
    assert record.superseded is False
    assert output.superseded_count == 0


def test_full_playback_completes_and_counts_as_finished(
    output: QtNarratorAudioOutput,
) -> None:
    events = _play(output, 4.0)

    output.sinks[0].finish(4.0)  # type: ignore[attr-defined]

    assert events["completed"] == 1
    assert output.completed_count == 1
    assert output.interrupted_count == 0
    record = output.last_playback
    assert record is not None
    assert record.result == "completed"
    assert record.processed_seconds == pytest.approx(record.expected_seconds)


def test_marginally_short_processed_time_still_completes(
    output: QtNarratorAudioOutput,
) -> None:
    """A backend may under-report slightly on a clean finish."""

    events = _play(output, 4.0)

    output.sinks[0].finish(3.95)  # type: ignore[attr-defined]

    assert events["completed"] == 1
    assert output.interrupted_count == 0


def test_backend_error_halfway_is_interrupted(
    output: QtNarratorAudioOutput,
) -> None:
    events = _play(output, 4.0)

    output.sinks[0].finish(  # type: ignore[attr-defined]
        2.0,
        state=QtAudio.State.StoppedState,
        error=QtAudio.Error.UnderrunError,
    )

    assert events["completed"] == 0
    assert output.interrupted_count == 1
    assert events["errors"], "an errored playback must report failure"
    record = output.last_playback
    assert record is not None
    assert record.result == "interrupted"
    assert "UnderrunError" in record.reason or "UnderrunError" in record.backend_error


def test_stopped_state_without_error_halfway_is_interrupted(
    output: QtNarratorAudioOutput,
) -> None:
    """Previously this produced no callback at all: a silent loss."""

    events = _play(output, 4.0)

    output.sinks[0].finish(1.5, state=QtAudio.State.StoppedState)  # type: ignore[attr-defined]

    assert events["completed"] == 0
    assert output.interrupted_count == 1
    assert output.last_playback is not None
    assert output.last_playback.result == "interrupted"


def test_explicit_stop_is_classified_as_stop_not_interruption(
    output: QtNarratorAudioOutput,
) -> None:
    _play(output, 4.0)

    output._stop_all()

    record = output.last_playback
    assert record is not None
    assert record.result == "stopped"
    assert record.stop_requested is True
    # A deliberate stop is neither a completion nor a fault.
    assert output.completed_count == 0
    assert output.interrupted_count == 0


def test_stop_does_not_restart_or_replay_speech(
    output: QtNarratorAudioOutput,
) -> None:
    _play(output, 4.0)
    output._stop_all()

    assert output._current is None
    assert output._pending is None
    # Exactly one sink was ever created: nothing was replayed.
    assert len(output.sinks) == 1  # type: ignore[attr-defined]


def test_history_is_bounded_to_ten_attempts(
    output: QtNarratorAudioOutput,
) -> None:
    for index in range(14):
        _play(output, 1.0, request_id=index + 1)
        output.sinks[-1].finish(1.0)  # type: ignore[attr-defined]

    assert len(output.playback_history) == 10
    assert output.completed_count == 14
    # The oldest attempts were discarded, the newest retained.
    assert output.playback_history[-1].request_id == 14


def test_records_carry_the_full_pcm_description(
    output: QtNarratorAudioOutput,
) -> None:
    _play(output, 2.0, request_id=7)
    output.sinks[0].finish(2.0)  # type: ignore[attr-defined]

    record = output.last_playback
    assert record is not None
    assert record.request_id == 7
    assert record.pcm_bytes == 2 * _RATE * _WIDTH
    assert record.sample_rate == _RATE
    assert record.channels == _CHANNELS
    assert record.sample_format == "s16le"
    assert record.bytes_per_sample == _WIDTH
    assert record.terminal_state == "IdleState"
    assert record.backend_error == "NoError"


def test_queue_supersession_behaviour_is_unchanged(
    output: QtNarratorAudioOutput,
) -> None:
    """The existing depth-1 queue policy must not shift."""

    def playback(request_id: int) -> _Playback:
        return _Playback(
            audio=_audio(1.0),
            volume=1.0,
            request_id=request_id,
            started_callback=lambda _ms: None,
            completed_callback=lambda: None,
            error_callback=lambda _m: None,
            queued_at=0.0,
        )

    output._current = playback(1)

    output._queue_playback(playback(2))
    assert output.superseded_count == 0

    output._queue_playback(playback(3))
    assert output.superseded_count == 1
    assert output._pending is not None
    assert output._pending.request_id == 3

    # The playing line is still never interrupted by a newcomer.
    assert output._current is not None
    assert output._current.request_id == 1


def test_old_completion_rule_would_have_called_a_half_played_line_finished(
    output: QtNarratorAudioOutput,
) -> None:
    """Pin the defect, so the old semantics cannot be reintroduced.

    The previous rule was exactly:

        completed = state == QtAudio.State.IdleState and current is not None

    It consulted neither processed audio nor elapsed time, so an underrun that
    put the sink into IdleState halfway through the PCM invoked the completed
    callback and incremented the finished counter. This test asserts the old
    predicate and the new behaviour disagree on precisely that input.
    """

    events = _play(output, 4.0)
    sink = output.sinks[0]  # type: ignore[attr-defined]

    # Half the audio processed, sink idle, no backend error reported.
    sink.finish(2.0)

    old_rule_says_completed = (
        sink.state() == QtAudio.State.IdleState and True  # current was not None
    )
    assert old_rule_says_completed is True, "the old rule accepted this as success"

    # The new behaviour rejects it, which is the whole fix.
    assert events["completed"] == 0
    assert output.completed_count == 0
    assert output.interrupted_count == 1
    record = output.last_playback
    assert record is not None
    assert record.processed_seconds < record.expected_seconds
