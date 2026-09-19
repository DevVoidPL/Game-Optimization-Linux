"""Correlation logging for one playback request. Diagnostics only.

Hypothesis under test: a corrected re-recognition of the same subtitle stops the
utterance being played and restarts it from the beginning.

The code refutes the "stops the playing one" half: _queue_playback never touches
self._current, and audio.stop() has only two callers, neither on the acceptance
path. These tests pin that, and pin the logging that distinguishes the three
possible causes of a perceived interruption.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from PySide6.QtMultimedia import QtAudio

from game_optimization_linux.models.narrator import PcmAudio
from game_optimization_linux.services.narrator_audio import (
    QtNarratorAudioOutput,
    _Playback,
)

_LOGGER = "game_optimization_linux.services.narrator_audio"
_RATE = 22050


def _audio(seconds: float) -> PcmAudio:
    return PcmAudio(
        samples=b"\x00\x01" * int(_RATE * seconds),
        sample_rate=_RATE,
        channels=1,
        sample_format="s16le",
    )


class _FakeSink:
    def __init__(self, _device, _format, _parent=None) -> None:
        self.state_slot = None
        self._state = QtAudio.State.ActiveState
        self._processed = 0
        self.stateChanged = SimpleNamespace(
            connect=self._connect, disconnect=lambda _s: None
        )

    def _connect(self, slot) -> None:
        self.state_slot = slot

    def setVolume(self, _v) -> None: ...
    def setBufferSize(self, _size) -> None: ...
    def bufferSize(self) -> int: return 11024
    def start(self, _device) -> None: ...
    def stop(self) -> None: ...
    def deleteLater(self) -> None: ...
    def state(self): return self._state
    def error(self): return QtAudio.Error.NoError
    def processedUSecs(self) -> int: return self._processed

    def finish(self, played: float, state=None) -> None:
        self._processed = int(played * 1_000_000)
        self._state = state or QtAudio.State.IdleState
        assert self.state_slot is not None
        self.state_slot()


class _Device:
    @staticmethod
    def isNull() -> bool: return False
    @staticmethod
    def isFormatSupported(_f) -> bool: return True


@pytest.fixture
def output() -> QtNarratorAudioOutput:
    sinks: list[_FakeSink] = []

    def factory(device, fmt, parent=None):
        sink = _FakeSink(device, fmt, parent)
        sinks.append(sink)
        return sink

    provider = QtNarratorAudioOutput(
        sink_factory=factory, device_provider=lambda: _Device()
    )
    provider.sinks = sinks  # type: ignore[attr-defined]
    return provider


def _playback(request_id: int, text: str, seconds: float = 3.0) -> _Playback:
    return _Playback(
        audio=_audio(seconds),
        volume=1.0,
        request_id=request_id,
        started_callback=lambda _ms: None,
        completed_callback=lambda: None,
        error_callback=lambda _m: None,
        queued_at=0.0,
        text=text,
    )


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "Narrator playback event=" in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# The code-level refutation
# ---------------------------------------------------------------------------


def test_a_new_request_never_stops_the_playing_one(
    output: QtNarratorAudioOutput,
) -> None:
    """The core refutation: _queue_playback leaves self._current untouched."""

    first = _playback(1, "Starałam sie. tato.")
    output._start(first)
    assert output._current is first

    output._queue_playback(_playback(2, "Starałam się, tato."))

    # Still playing the original; the newcomer only waits.
    assert output._current is first
    assert output._pending is not None
    assert output._pending.request_id == 2
    assert output.interrupted_count == 0
    assert output.completed_count == 0
    assert output._stop_requested is False


def test_corrected_variant_plays_after_the_first_completes(
    output: QtNarratorAudioOutput,
) -> None:
    """What the user actually hears: the line, then the line again from the start.

    Not an interruption - two sequential completions.
    """

    output._start(_playback(1, "Starałam sie. tato."))
    output._queue_playback(_playback(2, "Starałam się, tato."))

    output.sinks[0].finish(3.0)  # type: ignore[attr-defined]

    assert output.completed_count == 1
    assert output.interrupted_count == 0
    # The corrected variant is now the one playing, from its beginning.
    assert output._current is not None
    assert output._current.request_id == 2
    assert output._pending is None


# ---------------------------------------------------------------------------
# The three causes must be distinguishable in the log
# ---------------------------------------------------------------------------


def test_queued_event_names_the_actively_playing_request(
    output: QtNarratorAudioOutput,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output._start(_playback(1, "pierwsza kwestia"))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        output._queue_playback(_playback(2, "druga kwestia"))

    queued = [line for line in _events(caplog) if "event=queued" in line]
    assert queued, _events(caplog)
    assert "request=2" in queued[0]
    assert "waits_for_request_id=1" in queued[0]
    assert "actively_playing_text='pierwsza kwestia'" in queued[0]


def test_superseded_event_reports_replacement_of_a_queued_item(
    output: QtNarratorAudioOutput,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Case 1: only a WAITING item is displaced, never a playing one."""

    output._start(_playback(1, "kwestia w toku"))
    output._queue_playback(_playback(2, "Starałam sie. tato."))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        output._queue_playback(_playback(3, "Starałam się, tato."))

    superseded = [line for line in _events(caplog) if "event=superseded" in line]
    assert superseded, _events(caplog)
    line = superseded[0]
    assert "request=3" in line
    assert "replaces_request_id=2" in line
    assert "replaced_state=queued" in line
    assert "similarity=" in line
    # A corrected variant of the same sentence is highly similar.
    ratio = float(line.split("similarity=")[1].split()[0])
    assert ratio > 0.8
    # The playing utterance was not the one replaced.
    assert output._current is not None
    assert output._current.request_id == 1


def test_stopped_event_is_distinct_from_an_interruption(
    output: QtNarratorAudioOutput,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Case 2: an explicit stop, which only session stop or a failure triggers."""

    output._start(_playback(1, "przerwana kwestia"))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        output._stop_all()

    stopped = [line for line in _events(caplog) if "event=stopped" in line]
    assert stopped, _events(caplog)
    assert "stop_requested=True" in stopped[0]
    assert "request=1" in stopped[0]
    assert output.interrupted_count == 0
    assert output.completed_count == 0


def test_interrupted_event_reports_played_and_expected(
    output: QtNarratorAudioOutput,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Case 3: the backend cut the PCM short, with no stop requested."""

    output._start(_playback(1, "ucieta kwestia", seconds=4.0))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        output.sinks[0].finish(0.25)  # type: ignore[attr-defined]

    interrupted = [line for line in _events(caplog) if "event=interrupted" in line]
    assert interrupted, _events(caplog)
    line = interrupted[0]
    assert "played_ms=250" in line
    assert "expected_ms=4000" in line
    assert "stop_requested=False" in line
    assert "terminal_state=IdleState" in line


def test_completed_event_reports_matching_durations(
    output: QtNarratorAudioOutput,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output._start(_playback(1, "cala kwestia", seconds=3.0))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        output.sinks[0].finish(3.0)  # type: ignore[attr-defined]

    completed = [line for line in _events(caplog) if "event=completed" in line]
    assert completed, _events(caplog)
    assert "played_ms=3000" in completed[0]
    assert "expected_ms=3000" in completed[0]


def test_submitted_event_carries_the_phrase(
    output: QtNarratorAudioOutput,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        output.play(
            _audio(2.0),
            volume=1.0,
            request_id=42,
            started_callback=lambda _ms: None,
            completed_callback=lambda: None,
            error_callback=lambda _m: None,
            text="Starałam się, tato.",
        )

    submitted = [line for line in _events(caplog) if "event=submitted" in line]
    assert submitted, _events(caplog)
    assert "request=42" in submitted[0]
    assert "Starałam się, tato." in submitted[0]


def test_play_without_text_still_works(output: QtNarratorAudioOutput) -> None:
    """text is optional, so existing callers are unaffected."""

    output.play(
        _audio(1.0),
        volume=1.0,
        request_id=1,
        started_callback=lambda _ms: None,
        completed_callback=lambda: None,
        error_callback=lambda _m: None,
    )


def test_similarity_is_reported_not_used_for_decisions() -> None:
    assert QtNarratorAudioOutput._similarity("", "abc") == "n/a"
    assert QtNarratorAudioOutput._similarity("abc", "") == "n/a"
    assert float(QtNarratorAudioOutput._similarity("Stój", "stoi")) < 1.0
    assert QtNarratorAudioOutput._similarity("same", "same") == "1.000"
