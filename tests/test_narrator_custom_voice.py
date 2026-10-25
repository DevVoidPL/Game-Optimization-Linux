"""User-imported Piper voices: validation, copy, listing, removal, same worker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl

from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.narrator_components import (
    NarratorComponentManager,
)
from game_optimization_linux.services.narrator_persistence import (
    NarratorSettingsRepository,
)
from game_optimization_linux.services.narrator_tts import (
    PIPER_CUSTOM_COMPONENT_ID,
    PIPER_VOICE_ID,
    CustomVoiceError,
    CustomVoiceLanguageError,
    PiperPolishTtsProvider,
    PiperSynthesis,
)
from tests.test_narrator_end_to_end import _ManualExecutor, _pipeline

_CONFIG = {
    "num_symbols": 256,
    "num_speakers": 1,
    "audio": {"sample_rate": 22050, "quality": "medium"},
    "espeak": {"voice": "pl"},
    "language": {"code": "pl_PL"},
    "phoneme_id_map": {"a": [1]},
}


class _Worker:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = 0

    def start(self) -> None:
        return

    def synthesize(self, text: str, *, speech_rate: float) -> PiperSynthesis:
        self.calls.append(text)
        return PiperSynthesis(b"\x01\x00", 22050, 1)

    def cancel(self) -> None:
        return

    def close(self) -> None:
        self.closed += 1


def _voice_files(folder: Path, name: str = "moj-glos-medium", config=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    model = folder / f"{name}.onnx"
    model.write_bytes(b"user-model")
    if config is not None:
        payload = config if isinstance(config, str) else json.dumps(config)
        (folder / f"{name}.onnx.json").write_text(payload, encoding="utf-8")
    return model


def _provider(root: Path, created: list[tuple[Path, Path, _Worker]]):
    def factory(model: Path, config: Path) -> _Worker:
        worker = _Worker()
        created.append((model, config, worker))
        return worker

    return PiperPolishTtsProvider(
        root, worker_factory=factory, runtime_available=lambda: True
    )


def test_valid_import_copies_both_files_and_uses_the_same_worker(tmp_path: Path) -> None:
    created: list[tuple[Path, Path, _Worker]] = []
    provider = _provider(tmp_path / "components", created)
    source = _voice_files(tmp_path / "downloads", config=_CONFIG)

    voice = provider.import_custom_voice(source)

    assert voice.custom is True
    assert voice.voice_id == "custom-moj-glos-medium"
    assert voice.name == "moj-glos-medium"
    target = tmp_path / "components" / PIPER_CUSTOM_COMPONENT_ID / "voices"
    assert (target / "moj-glos-medium.onnx").read_bytes() == b"user-model"
    assert json.loads((target / "moj-glos-medium.onnx.json").read_text()) == _CONFIG
    assert voice.voice_id in provider.available_voice_ids
    assert provider.default_voice_id == PIPER_VOICE_ID

    audio = provider.synthesize(
        "Własny głos.", language="pl", voice_id=voice.voice_id, speech_rate=1.0
    )
    assert audio.samples
    # Same Piper worker factory (and therefore the same ONNX limits) as built-ins.
    assert created[0][:2] == (
        target / "moj-glos-medium.onnx",
        target / "moj-glos-medium.onnx.json",
    )
    assert created[0][2].calls == ["Własny głos."]


def test_missing_config_is_a_readable_error_and_copies_nothing(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "components", [])
    source = _voice_files(tmp_path / "downloads", config=None)

    with pytest.raises(CustomVoiceError) as error:
        provider.import_custom_voice(source)

    assert error.value.code == "missing_config"
    assert error.value.detail == "moj-glos-medium.onnx.json"
    assert not (tmp_path / "components" / PIPER_CUSTOM_COMPONENT_ID).exists()
    assert provider.custom_voices == ()


@pytest.mark.parametrize(
    ("config", "code"),
    [
        ("{ not json", "config_invalid_json"),
        ('["list"]', "config_not_piper"),
        ({"num_symbols": 256, "audio": {}}, "config_not_piper"),
        ({**_CONFIG, "espeak": {}}, "config_not_piper"),
    ],
)
def test_broken_or_non_piper_config_is_rejected(
    tmp_path: Path, config: object, code: str
) -> None:
    provider = _provider(tmp_path / "components", [])
    source = _voice_files(tmp_path / "downloads", config=config)

    with pytest.raises(CustomVoiceError) as error:
        provider.import_custom_voice(source)

    assert error.value.code == code
    assert provider.custom_voices == ()


def test_non_polish_voice_needs_confirmation(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "components", [])
    english = {**_CONFIG, "espeak": {"voice": "en-us"}, "language": {"code": "en_US"}}
    source = _voice_files(tmp_path / "downloads", "english-medium", config=english)

    with pytest.raises(CustomVoiceLanguageError) as error:
        provider.import_custom_voice(source)
    assert error.value.language == "en_US"
    assert provider.custom_voices == ()

    voice = provider.import_custom_voice(source, allow_other_language=True)
    assert voice.voice_id == "custom-english-medium"


def test_wrong_extension_and_duplicate_are_rejected(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "components", [])
    source = _voice_files(tmp_path / "downloads", config=_CONFIG)
    text_file = tmp_path / "voice.txt"
    text_file.write_text("x", encoding="utf-8")

    with pytest.raises(CustomVoiceError) as error:
        provider.import_custom_voice(text_file)
    assert error.value.code == "not_onnx"

    provider.import_custom_voice(source)
    with pytest.raises(CustomVoiceError) as duplicate:
        provider.import_custom_voice(source)
    assert duplicate.value.code == "already_exists"


def test_remove_deletes_files_and_closes_its_worker(tmp_path: Path) -> None:
    created: list[tuple[Path, Path, _Worker]] = []
    provider = _provider(tmp_path / "components", created)
    voice = provider.import_custom_voice(
        _voice_files(tmp_path / "downloads", config=_CONFIG)
    )
    provider.synthesize("Test.", language="pl", voice_id=voice.voice_id, speech_rate=1.0)

    provider.remove_custom_voice(voice.voice_id)

    assert created[0][2].closed == 1
    assert provider.custom_voices == ()
    target = tmp_path / "components" / PIPER_CUSTOM_COMPONENT_ID / "voices"
    assert not any(target.iterdir())
    with pytest.raises(CustomVoiceError):
        provider.remove_custom_voice(PIPER_VOICE_ID)


@pytest.fixture()
def controller(tmp_path: Path):
    components = tmp_path / "components"
    tts = PiperPolishTtsProvider(components, runtime_available=lambda: True)
    pipeline, *_rest = _pipeline(tmp_path, _ManualExecutor(), tts=tts)
    app = AppController(
        game_provider=DemoGameProvider(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=NarratorSettingsRepository(tmp_path / "games"),
        narrator_component_manager=NarratorComponentManager(components),
        narrator_pipeline=pipeline,
        auto_refresh=False,
    )
    yield app
    app.shutdown()


def test_controller_imports_lists_and_removes_custom_voice(
    controller, tmp_path: Path
) -> None:
    game_id = controller.games[0]["id"]
    source = _voice_files(tmp_path / "downloads", config=_CONFIG)
    changes: list[bool] = []
    controller.narratorComponentsChanged.connect(lambda: changes.append(True))

    result = controller.importNarratorCustomVoice(
        QUrl.fromLocalFile(str(source)).toString(), False
    )

    assert result["success"] is True
    assert result["voiceId"] == "custom-moj-glos-medium"
    assert changes
    voices = {
        voice["id"]: voice
        for voice in controller.getNarratorGameSettings(game_id)["voices"]
    }
    custom = voices["custom-moj-glos-medium"]
    assert custom["custom"] is True
    assert custom["installed"] is True and custom["available"] is True
    assert custom["managed"] is False
    # A selected custom voice satisfies the speech requirement by itself.
    settings = controller._narrator_settings_repository.load(
        controller._narrator_controller._game_key(
            controller._resolve_game(game_id, show_error=False)
        )
    )
    from dataclasses import replace

    missing = controller._narrator_controller._missing_requirements(
        replace(settings, voice_id="custom-moj-glos-medium")
    )
    assert "tts" not in missing

    assert controller.removeNarratorCustomVoice("custom-moj-glos-medium") is True
    ids = {voice["id"] for voice in controller.getNarratorGameSettings(game_id)["voices"]}
    assert "custom-moj-glos-medium" not in ids


def test_controller_reports_error_codes(controller, tmp_path: Path) -> None:
    missing = _voice_files(tmp_path / "a", config=None)
    broken = _voice_files(tmp_path / "b", config="{ nope")
    english = _voice_files(
        tmp_path / "c",
        "english-medium",
        config={**_CONFIG, "espeak": {"voice": "en-us"}, "language": {"code": "en_US"}},
    )

    assert controller.importNarratorCustomVoice(str(missing), False)["errorCode"] == "missing_config"
    assert controller.importNarratorCustomVoice(str(broken), False)["errorCode"] == "config_invalid_json"
    pending = controller.importNarratorCustomVoice(str(english), False)
    assert pending["needsLanguageConfirmation"] is True
    assert pending["language"] == "en_US"
    assert controller.importNarratorCustomVoice(str(english), True)["success"] is True
    assert controller.importNarratorCustomVoice("https://example.com/v.onnx", False)[
        "errorCode"
    ] == "not_local"


def test_desktop_ui_exposes_custom_voice_import() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    ).read_text(encoding="utf-8")
    assert 'qsTr("Add custom voice...")' in source
    assert 'nameFilters: [qsTr("Piper voice models (*.onnx)")]' in source
    assert 'qsTr("Custom: %1")' in source
    assert "controller.removeNarratorCustomVoice(" in source
    assert "customVoiceLanguageDialog.ask(" in source
    assert "you are responsible for its license" in source
