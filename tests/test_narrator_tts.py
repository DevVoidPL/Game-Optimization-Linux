from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from game_optimization_linux.services.narrator_piper_worker import (
    _load_voice,
    serve,
)
from game_optimization_linux.services.narrator_tts import (
    PIPER_BASS_COMPONENT_ID,
    PIPER_BASS_CONFIG_RELATIVE_PATH,
    PIPER_BASS_MODEL_RELATIVE_PATH,
    PIPER_BASS_VOICE_ID,
    PIPER_COMPONENT_ID,
    PIPER_CONFIG_RELATIVE_PATH,
    PIPER_MODEL_RELATIVE_PATH,
    PIPER_PROVIDER_ID,
    PIPER_VOICE_ID,
    PiperPolishTtsProvider,
    PiperSynthesis,
)


def _install_voice(root: Path) -> tuple[Path, Path]:
    component = root / PIPER_COMPONENT_ID
    model = component / PIPER_MODEL_RELATIVE_PATH
    config = component / PIPER_CONFIG_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    config.write_text("{}", encoding="utf-8")
    return model, config


def _install_bass_voice(root: Path) -> tuple[Path, Path]:
    component = root / PIPER_BASS_COMPONENT_ID
    model = component / PIPER_BASS_MODEL_RELATIVE_PATH
    config = component / PIPER_BASS_CONFIG_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"bass-model")
    config.write_text("{}", encoding="utf-8")
    return model, config


class _Worker:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, float]] = []
        self.cancel_count = 0
        self.close_count = 0
        self.start_count = 0

    def start(self) -> None:
        self.start_count += 1

    def synthesize(self, text: str, *, speech_rate: float) -> PiperSynthesis:
        self.calls.append((text, speech_rate))
        if self.error is not None:
            raise self.error
        return PiperSynthesis(b"\x01\x00\x02\x00", 22050, 1)

    def cancel(self) -> None:
        self.cancel_count += 1

    def close(self) -> None:
        self.close_count += 1


def test_piper_provider_synthesizes_mono_s16_pcm_on_cpu_component(
    tmp_path: Path,
) -> None:
    model, config = _install_voice(tmp_path)
    worker = _Worker()
    created: list[tuple[Path, Path]] = []
    clock_values = iter((10.0, 10.0, 10.125))

    def factory(model_path: Path, config_path: Path) -> _Worker:
        created.append((model_path, config_path))
        return worker

    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=factory,
        runtime_available=lambda: True,
        clock=lambda: next(clock_values),
    )

    audio = provider.synthesize(
        "  Musimy się stąd   wydostać. ",
        language="pl",
        voice_id=PIPER_VOICE_ID,
        speech_rate=1.2,
    )

    assert created == [(model, config)]
    assert worker.calls == [("Musimy się stąd wydostać.", 1.2)]
    assert audio.samples == b"\x01\x00\x02\x00"
    assert (audio.sample_rate, audio.channels, audio.sample_format) == (
        22050,
        1,
        "s16le",
    )
    assert audio.provider_id == PIPER_PROVIDER_ID
    assert audio.elapsed_ms == pytest.approx(125.0)
    provider.close()
    assert worker.close_count == 1


def test_piper_provider_prewarms_selected_worker_and_reuses_it(
    tmp_path: Path,
) -> None:
    _install_bass_voice(tmp_path)
    worker = _Worker()
    clock_values = iter((10.0, 10.0, 10.125))
    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=lambda _model, _config: worker,
        runtime_available=lambda: True,
        clock=lambda: next(clock_values),
    )

    provider.prepare(PIPER_BASS_VOICE_ID)
    audio = provider.synthesize(
        "Uciekaj!",
        language="pl",
        voice_id=PIPER_BASS_VOICE_ID,
        speech_rate=1.1,
    )

    assert worker.start_count == 1
    assert worker.calls == [("Uciekaj!", 1.1)]
    assert audio.elapsed_ms == pytest.approx(125.0)


def test_piper_provider_reports_runtime_and_voice_component_separately(
    tmp_path: Path,
) -> None:
    missing_runtime = PiperPolishTtsProvider(
        tmp_path,
        runtime_available=lambda: False,
    )
    assert missing_runtime.available is False
    assert missing_runtime.status_message == "The Piper CPU runtime is unavailable"

    missing_voice = PiperPolishTtsProvider(
        tmp_path,
        runtime_available=lambda: True,
    )
    assert missing_voice.available is False
    assert missing_voice.status_message == "Install the verified Polish Piper voice"

    _install_voice(tmp_path)
    assert missing_voice.available is True
    assert missing_voice.status_message == "Piper Polish speech synthesis is ready"


def test_piper_provider_lists_gosia_and_bass_without_falling_back(
    tmp_path: Path,
) -> None:
    _install_voice(tmp_path)
    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=lambda _model, _config: _Worker(),
        runtime_available=lambda: True,
    )

    assert [(voice.voice_id, voice.name) for voice in provider.voices] == [
        (PIPER_VOICE_ID, "Gosia"),
        (PIPER_BASS_VOICE_ID, "Bass"),
    ]
    assert provider.available_voice_ids == (PIPER_VOICE_ID,)
    assert provider.voice_installed(PIPER_BASS_VOICE_ID) is False

    with pytest.raises(RuntimeError, match="selected Polish voice is not installed"):
        provider.synthesize(
            "Bass nie jest zainstalowany",
            language="pl",
            voice_id=PIPER_BASS_VOICE_ID,
            speech_rate=1.0,
        )


def test_piper_provider_closes_resident_worker_when_voice_changes(
    tmp_path: Path,
) -> None:
    gosia_paths = _install_voice(tmp_path)
    bass_paths = _install_bass_voice(tmp_path)
    created: list[tuple[Path, Path, _Worker]] = []

    def factory(model_path: Path, config_path: Path) -> _Worker:
        worker = _Worker()
        created.append((model_path, config_path, worker))
        return worker

    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=factory,
        runtime_available=lambda: True,
    )
    provider.synthesize(
        "Gosia",
        language="pl",
        voice_id=PIPER_VOICE_ID,
        speech_rate=1.0,
    )
    provider.synthesize(
        "Gosia ponownie",
        language="pl",
        voice_id=PIPER_VOICE_ID,
        speech_rate=1.0,
    )

    assert len(created) == 1
    assert created[0][:2] == gosia_paths
    provider.select_voice(PIPER_BASS_VOICE_ID)
    assert created[0][2].close_count == 1

    provider.synthesize(
        "Bass",
        language="pl",
        voice_id=PIPER_BASS_VOICE_ID,
        speech_rate=1.0,
    )
    assert len(created) == 2
    assert created[1][:2] == bass_paths
    provider.close()
    assert created[1][2].close_count == 1


@pytest.mark.parametrize(
    ("language", "voice_id", "speech_rate", "message"),
    [
        ("en", PIPER_VOICE_ID, 1.0, "supports Polish only"),
        ("pl", "unknown", 1.0, "Unsupported Polish voice"),
        ("pl", PIPER_VOICE_ID, 0.1, "between 0.5 and 2.0"),
    ],
)
def test_piper_provider_rejects_unsupported_synthesis_values(
    tmp_path: Path,
    language: str,
    voice_id: str,
    speech_rate: float,
    message: str,
) -> None:
    _install_voice(tmp_path)
    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=lambda _model, _config: _Worker(),
        runtime_available=lambda: True,
    )

    with pytest.raises(ValueError, match=message):
        provider.synthesize(
            "Tekst",
            language=language,
            voice_id=voice_id,
            speech_rate=speech_rate,
        )


def test_piper_provider_recovers_after_worker_failure(tmp_path: Path) -> None:
    _install_voice(tmp_path)
    first = _Worker(error=RuntimeError("model inference failed"))
    second = _Worker()
    workers = iter((first, second))
    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=lambda _model, _config: next(workers),
        runtime_available=lambda: True,
    )

    with pytest.raises(RuntimeError, match="model inference failed"):
        provider.synthesize(
            "Pierwsza kwestia",
            language="pl",
            voice_id="",
            speech_rate=1.0,
        )
    assert first.close_count == 1

    audio = provider.synthesize(
        "Druga kwestia",
        language="pl-PL",
        voice_id="",
        speech_rate=1.0,
    )
    assert audio.samples
    assert second.calls == [("Druga kwestia", 1.0)]

    provider.cancel()
    assert second.cancel_count == 1
    provider.close()
    assert second.close_count == 0


def test_piper_provider_rejects_invalid_worker_audio(tmp_path: Path) -> None:
    class InvalidWorker(_Worker):
        def synthesize(self, text: str, *, speech_rate: float) -> PiperSynthesis:
            del text, speech_rate
            return PiperSynthesis(b"\0", 22050, 1)

    _install_voice(tmp_path)
    provider = PiperPolishTtsProvider(
        tmp_path,
        worker_factory=lambda _model, _config: InvalidWorker(),
        runtime_available=lambda: True,
    )
    with pytest.raises(RuntimeError, match="invalid PCM samples"):
        provider.synthesize(
            "Tekst",
            language="pl",
            voice_id=PIPER_VOICE_ID,
            speech_rate=1.0,
        )


def test_piper_worker_loads_voice_with_cpu_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class Voice:
        @staticmethod
        def load(model_path: str, **values):
            calls.append((model_path, values))
            return object()

    monkeypatch.setitem(__import__("sys").modules, "piper", SimpleNamespace(PiperVoice=Voice))
    _load_voice(Path("voice.onnx"), Path("voice.onnx.json"))

    assert calls == [
        (
            "voice.onnx",
            {"config_path": "voice.onnx.json", "use_cuda": False},
        )
    ]


def test_piper_worker_keeps_voice_loaded_and_returns_framed_pcm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[float] = []

    class SynthesisConfig:
        def __init__(self, *, length_scale: float) -> None:
            configs.append(length_scale)

    class Chunk:
        sample_rate = 22050
        sample_channels = 1
        sample_width = 2
        audio_int16_bytes = b"\x01\x00\x02\x00"

    class Voice:
        def __init__(self) -> None:
            self.phrases: list[str] = []

        def synthesize(self, text: str, *, syn_config: object):
            del syn_config
            self.phrases.append(text)
            yield Chunk()

    monkeypatch.setitem(
        __import__("sys").modules,
        "piper",
        SimpleNamespace(SynthesisConfig=SynthesisConfig),
    )
    voice = Voice()
    source = StringIO(
        json.dumps(
            {
                "command": "synthesize",
                "request_id": 1,
                "text": "Pierwsza kwestia",
                "speech_rate": 1.25,
            }
        )
        + "\n"
        + json.dumps(
            {
                "command": "synthesize",
                "request_id": 2,
                "text": "Druga kwestia",
                "speech_rate": 1.0,
            }
        )
        + '\n{"command":"shutdown"}\n'
    )
    output = StringIO()
    load_count = 0

    def load(_model: Path, _config: Path) -> Voice:
        nonlocal load_count
        load_count += 1
        return voice

    assert serve(Path("model"), Path("config"), source, output, voice_loader=load) == 0

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    ready, *responses = messages
    assert load_count == 1
    assert ready["status"] == "ready"
    assert ready["initialization_ms"] >= 0.0
    assert voice.phrases == ["Pierwsza kwestia", "Druga kwestia"]
    assert configs == pytest.approx([0.8, 1.0])
    assert [item["request_id"] for item in responses] == [1, 2]
    assert all(item["ok"] is True for item in responses)
    assert responses[0]["sample_rate"] == 22050
    assert responses[0]["channels"] == 1
    assert responses[0]["sample_format"] == "s16le"


def test_piper_worker_reports_bad_request_without_stopping_server() -> None:
    source = StringIO('not-json\n{"command":"shutdown"}\n')
    output = StringIO()

    assert serve(
        Path("model"),
        Path("config"),
        source,
        output,
        voice_loader=lambda _model, _config: object(),
    ) == 0
    ready, response = [
        json.loads(line) for line in output.getvalue().splitlines()
    ]
    assert ready["status"] == "ready"
    assert response["ok"] is False
    assert response["request_id"] is None
