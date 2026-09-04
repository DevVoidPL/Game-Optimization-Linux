"""Speech-rate and articulation parameter handling for the Piper narrator.

These tests assert the parameters handed to Piper, never the audio itself.
Piper's length_scale is inverse to speed: below 1 is faster, above 1 is slower.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

from game_optimization_linux.models.narrator import NarratorGameSettings
from game_optimization_linux.services.narrator_piper_worker import (
    MAX_LENGTH_SCALE,
    MIN_LENGTH_SCALE,
    _effective_length_scale,
    _optional_noise,
    _voice_default_length_scale,
)
from game_optimization_linux.services.narrator_tts import (
    PIPER_COMPONENT_ID,
    PIPER_CONFIG_RELATIVE_PATH,
    PIPER_MODEL_RELATIVE_PATH,
    PIPER_VOICE_ID,
    PiperPolishTtsProvider,
    PiperSynthesis,
)


def _voice(default_length_scale: object) -> object:
    return SimpleNamespace(config=SimpleNamespace(length_scale=default_length_scale))


# --------------------------------------------------------------------------
# Relative length_scale math
# --------------------------------------------------------------------------


def test_rate_of_one_keeps_the_voice_default_length_scale() -> None:
    assert _effective_length_scale(_voice(1.0), 1.0) == pytest.approx(1.0)
    assert _effective_length_scale(_voice(1.2), 1.0) == pytest.approx(1.2)


def test_faster_rate_shortens_length_scale_and_slower_lengthens_it() -> None:
    voice = _voice(1.0)
    baseline = _effective_length_scale(voice, 1.0)
    faster = _effective_length_scale(voice, 1.30)
    slower = _effective_length_scale(voice, 0.80)

    assert faster < baseline, "1.30x must produce a shorter length_scale"
    assert slower > baseline, "0.80x must produce a longer length_scale"
    assert faster == pytest.approx(1.0 / 1.30)
    assert slower == pytest.approx(1.0 / 0.80)


def test_length_scale_is_relative_to_each_voice_default() -> None:
    """The same multiplier must yield different absolute values per voice."""

    standard = _effective_length_scale(_voice(1.0), 1.30)
    slower_voice = _effective_length_scale(_voice(1.2), 1.30)

    assert standard == pytest.approx(1.0 / 1.30)
    assert slower_voice == pytest.approx(1.2 / 1.30)
    assert slower_voice != pytest.approx(standard)
    # Both are still faster than their own voice's natural pace.
    assert standard < 1.0
    assert slower_voice < 1.2


def test_installed_polish_voices_are_unaffected_by_relative_math() -> None:
    """Both shipped voices default to 1.0, so behaviour is unchanged."""

    for rate in (0.5, 0.8, 1.0, 1.3, 2.0):
        assert _effective_length_scale(_voice(1.0), rate) == pytest.approx(
            1.0 / rate
        )


@pytest.mark.parametrize("broken", (None, 0.0, -1.0, "fast", float("nan")))
def test_unusable_voice_default_falls_back_to_one(broken: object) -> None:
    assert _voice_default_length_scale(_voice(broken)) == pytest.approx(1.0)


def test_voice_without_a_config_falls_back_to_one() -> None:
    assert _voice_default_length_scale(object()) == pytest.approx(1.0)


def test_length_scale_is_clamped_at_both_bounds() -> None:
    # A very slow voice at the slowest rate would exceed the upper bound.
    assert _effective_length_scale(_voice(100.0), 0.5) == pytest.approx(
        MAX_LENGTH_SCALE
    )
    # A very fast voice at the fastest rate would fall below the lower bound.
    assert _effective_length_scale(_voice(0.01), 2.0) == pytest.approx(
        MIN_LENGTH_SCALE
    )


# --------------------------------------------------------------------------
# Advanced articulation overrides
# --------------------------------------------------------------------------


def test_unset_noise_override_stays_none() -> None:
    assert _optional_noise(None, "noise_scale") is None


@pytest.mark.parametrize("value", (0.0, 0.667, 0.8, 2.0))
def test_valid_noise_override_is_accepted(value: float) -> None:
    assert _optional_noise(value, "noise_scale") == pytest.approx(value)


@pytest.mark.parametrize("value", (-0.1, 2.1, "loud", float("inf")))
def test_invalid_noise_override_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="noise_w_scale"):
        _optional_noise(value, "noise_w_scale")


@dataclass
class _FakeSynthesisConfig:
    speaker_id: object = None
    length_scale: object = None
    noise_scale: object = None
    noise_w_scale: object = None
    normalize_audio: bool = True
    volume: float = 1.0


class _FakeVoice:
    def __init__(self, default_length_scale: float = 1.0) -> None:
        self.config = SimpleNamespace(length_scale=default_length_scale)
        self.received: list[_FakeSynthesisConfig] = []

    def synthesize(self, text: str, syn_config: object = None):
        assert text
        assert isinstance(syn_config, _FakeSynthesisConfig)
        self.received.append(syn_config)
        yield SimpleNamespace(
            sample_rate=22050,
            sample_channels=1,
            sample_width=2,
            audio_int16_bytes=b"\x00\x01" * 8,
        )


@pytest.fixture
def piper_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Piper is a Flatpak-only dependency, so stub the import boundary."""

    module = ModuleType("piper")
    module.SynthesisConfig = _FakeSynthesisConfig  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", module)


def test_noise_overrides_are_forwarded_when_set(piper_stub: None) -> None:
    from game_optimization_linux.services.narrator_piper_worker import _synthesize

    voice = _FakeVoice()
    result = _synthesize(
        voice, "Batman wraca.", 1.0, noise_scale=0.4, noise_w_scale=0.5
    )

    config = voice.received[0]
    assert config.noise_scale == pytest.approx(0.4)
    assert config.noise_w_scale == pytest.approx(0.5)
    assert result["length_scale"] == pytest.approx(1.0)


def test_unset_noise_overrides_leave_voice_defaults(piper_stub: None) -> None:
    """None must reach Piper, which then uses the voice's own values."""

    from game_optimization_linux.services.narrator_piper_worker import _synthesize

    voice = _FakeVoice()
    _synthesize(voice, "Batman wraca.", 1.0)

    config = voice.received[0]
    assert config.noise_scale is None
    assert config.noise_w_scale is None


def test_worker_reports_the_effective_and_default_length_scale(
    piper_stub: None,
) -> None:
    from game_optimization_linux.services.narrator_piper_worker import _synthesize

    voice = _FakeVoice(default_length_scale=1.2)
    result = _synthesize(voice, "Batman wraca.", 1.30)

    assert result["voice_default_length_scale"] == pytest.approx(1.2)
    assert result["length_scale"] == pytest.approx(1.2 / 1.30)
    assert voice.received[0].length_scale == pytest.approx(1.2 / 1.30)


# --------------------------------------------------------------------------
# Settings persistence
# --------------------------------------------------------------------------


def _settings(**values: object) -> NarratorGameSettings:
    return NarratorGameSettings(game_key="292030", **values)  # type: ignore[arg-type]


def test_articulation_defaults_are_unset() -> None:
    settings = _settings()

    assert settings.noise_scale is None
    assert settings.noise_w_scale is None


def test_unset_articulation_round_trips_as_none() -> None:
    restored = NarratorGameSettings.from_dict(
        _settings().to_dict(), expected_game_key="292030"
    )

    assert restored.noise_scale is None
    assert restored.noise_w_scale is None


def test_set_articulation_round_trips_exactly() -> None:
    original = _settings(noise_scale=0.4, noise_w_scale=0.5)
    payload = original.to_dict()

    assert payload["noise_scale"] == pytest.approx(0.4)
    assert payload["noise_w_scale"] == pytest.approx(0.5)

    restored = NarratorGameSettings.from_dict(
        payload, expected_game_key="292030"
    )
    assert restored.noise_scale == pytest.approx(0.4)
    assert restored.noise_w_scale == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("noise_scale", -0.1),
        ("noise_scale", 2.5),
        ("noise_w_scale", -1.0),
        ("noise_w_scale", 99.0),
        ("noise_w_scale", "loud"),
    ),
)
def test_invalid_articulation_values_are_rejected(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=field):
        _settings(**{field: value})


def test_speech_rate_bounds_are_unchanged() -> None:
    assert _settings(speech_rate=0.5).speech_rate == pytest.approx(0.5)
    assert _settings(speech_rate=2.0).speech_rate == pytest.approx(2.0)
    with pytest.raises(ValueError, match="speech_rate"):
        _settings(speech_rate=2.1)


# --------------------------------------------------------------------------
# Observability of the effective value
# --------------------------------------------------------------------------


class _RecordingWorker:
    """Stands in for the Piper subprocess client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def start(self) -> None:
        return None

    def synthesize(
        self,
        text: str,
        *,
        speech_rate: float,
        noise_scale: float | None = None,
        noise_w_scale: float | None = None,
    ) -> PiperSynthesis:
        self.calls.append(
            {
                "text": text,
                "speech_rate": speech_rate,
                "noise_scale": noise_scale,
                "noise_w_scale": noise_w_scale,
            }
        )
        return PiperSynthesis(
            samples=b"\x00\x01" * 16,
            sample_rate=22050,
            channels=1,
            sample_format="s16le",
            length_scale=1.0 / speech_rate,
            voice_default_length_scale=1.0,
        )

    def close(self) -> None:
        return None


def _provider_with(worker: _RecordingWorker, root) -> PiperPolishTtsProvider:
    component = root / PIPER_COMPONENT_ID
    model = component / PIPER_MODEL_RELATIVE_PATH
    config = component / PIPER_CONFIG_RELATIVE_PATH
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model fixture")
    config.write_text("{}", encoding="utf-8")
    return PiperPolishTtsProvider(
        root,
        worker_factory=lambda _model, _config: worker,
        runtime_available=lambda: True,
    )


def test_effective_length_scale_is_logged_per_utterance(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    worker = _RecordingWorker()
    provider = _provider_with(worker, tmp_path)

    with caplog.at_level(
        logging.DEBUG, logger="game_optimization_linux.services.narrator_tts"
    ):
        provider.synthesize(
            "Batman wraca.",
            language="pl",
            voice_id=PIPER_VOICE_ID,
            speech_rate=1.30,
        )

    messages = [record.getMessage() for record in caplog.records]
    logged = [m for m in messages if "Narrator speech synthesis" in m]
    assert logged, f"no synthesis log emitted; saw: {messages}"
    assert "rate=1.30x" in logged[0]
    # 1.0 / 1.30 == 0.7692...
    assert "length_scale=0.7692" in logged[0]
    assert "voice_default" in logged[0]


def test_provider_omits_unset_articulation_and_forwards_set_values(
    tmp_path,
) -> None:
    worker = _RecordingWorker()
    provider = _provider_with(worker, tmp_path)

    provider.synthesize(
        "Batman wraca.",
        language="pl",
        voice_id=PIPER_VOICE_ID,
        speech_rate=1.0,
    )
    provider.synthesize(
        "Batman wraca.",
        language="pl",
        voice_id=PIPER_VOICE_ID,
        speech_rate=1.0,
        noise_scale=0.4,
        noise_w_scale=0.5,
    )

    assert worker.calls[0]["noise_scale"] is None
    assert worker.calls[0]["noise_w_scale"] is None
    assert worker.calls[1]["noise_scale"] == pytest.approx(0.4)
    assert worker.calls[1]["noise_w_scale"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# Latency reporting: start points must be distinguishable
# --------------------------------------------------------------------------


def test_snapshot_exposes_all_three_latency_start_points() -> None:
    """Each latency value must survive to the QML payload under its own key."""

    from game_optimization_linux.models.narrator import NarratorSessionSnapshot

    snapshot = NarratorSessionSnapshot(
        session_id="s1",
        game_key="292030",
        first_visible_frame_to_audio_start_ms=1800.0,
        accepted_to_audio_start_ms=1211.0,
        confirming_frame_to_audio_start_ms=1240.0,
        total_capture_to_audio_start_ms=1240.0,
    )
    payload = snapshot.to_dict()

    assert payload["firstVisibleFrameToAudioStartMs"] == pytest.approx(1800.0)
    assert payload["acceptedToAudioStartMs"] == pytest.approx(1211.0)
    assert payload["totalCaptureToAudioStartMs"] == pytest.approx(1240.0)
    # The true end-to-end value must be the largest: it starts earliest.
    assert (
        payload["firstVisibleFrameToAudioStartMs"]
        > payload["acceptedToAudioStartMs"]
    )


def test_first_visible_frame_timestamp_marks_the_first_observation() -> None:
    """The true end-to-end clock must start when the candidate first appears."""

    from pathlib import Path

    source = Path(
        "src/game_optimization_linux/services/narrator_pipeline.py"
    ).read_text(encoding="utf-8")

    assert "if observation.candidate_started:" in source
    assert (
        "self._first_visible_frame_timestamp = frame_timestamp" in source
    ), "the first-observation clock must be set at candidate_started"


def test_narrator_page_labels_each_latency_unambiguously() -> None:
    """The old "Subtitle to speech" label measured from the confirming frame."""

    from pathlib import Path

    source = Path(
        "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    ).read_text(encoding="utf-8")

    # The misleading label must be gone.
    assert 'qsTr("Subtitle to speech: %1")' not in source
    # Each remaining label must name its own start point.
    assert 'qsTr("Subtitle on screen to speech: %1")' in source
    assert 'qsTr("Phrase accepted to speech: %1")' in source
    assert 'qsTr("Confirming frame to speech: %1")' in source
    assert 'qsTr("Consensus wait: %1")' in source
    # And be backed by the matching field.
    assert '["firstVisibleFrameToAudioStartMs"]' in source
    assert '["acceptedToAudioStartMs"]' in source
    assert '["totalCaptureToAudioStartMs"]' in source
    # The dropped-utterance counter must be visible too.
    assert 'qsTr("Dropped spoken lines: %1")' in source
    assert '["audioSupersessions"]' in source


def test_consensus_wait_is_derived_from_existing_values_only() -> None:
    """The helper must subtract two reported values and guard missing data."""

    from pathlib import Path

    source = Path(
        "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    ).read_text(encoding="utf-8")

    assert "function formatConsensusWait(session)" in source
    helper = source.split("function formatConsensusWait(session)", 1)[1]
    helper = helper.split("\n    }", 1)[0]
    # Derived by subtraction, not measured anew.
    assert "firstVisibleFrameToAudioStartMs" in helper
    assert "acceptedToAudioStartMs" in helper
    assert "Number(firstSeen) - Number(accepted)" in helper
    # Missing or non-finite inputs must not render a bogus number.
    assert "Not measured" in helper
    assert "Math.max(0," in helper


# --------------------------------------------------------------------------
# Dropped spoken lines
# --------------------------------------------------------------------------


def test_superseded_count_increments_when_a_pending_line_is_replaced() -> None:
    """A backlog replaces the waiting line; the playing line is never cut."""

    from game_optimization_linux.services.narrator_audio import (
        QtNarratorAudioOutput,
        _Playback,
    )

    provider = QtNarratorAudioOutput()

    def playback(request_id: int) -> _Playback:
        return _Playback(
            audio=SimpleNamespace(
                samples=b"\x00\x01",
                sample_rate=22050,
                channels=1,
                sample_format="s16le",
            ),
            volume=1.0,
            request_id=request_id,
            started_callback=lambda _ms: None,
            completed_callback=lambda: None,
            error_callback=lambda _message: None,
            queued_at=0.0,
        )

    # Pretend a line is already playing so nothing touches a real audio device.
    provider._current = playback(1)
    assert provider.superseded_count == 0

    # The first arrival only becomes pending: nothing is dropped yet.
    provider._queue_playback(playback(2))
    assert provider.superseded_count == 0
    assert provider._pending is not None

    # The next arrival replaces that pending line, which counts as dropped.
    provider._queue_playback(playback(3))
    assert provider.superseded_count == 1
    assert provider._pending is not None
    assert provider._pending.request_id == 3

    provider._queue_playback(playback(4))
    assert provider.superseded_count == 2
    # The originally playing line was never replaced or interrupted.
    assert provider._current is not None
    assert provider._current.request_id == 1
