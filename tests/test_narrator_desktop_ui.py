"""Desktop Narrator UI: voice catalog, shared speaker-name toggle, action bar."""

from __future__ import annotations

from pathlib import Path

import pytest

from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.narrator_components import (
    DEFAULT_NARRATOR_COMPONENTS,
    NarratorComponentManager,
)
from game_optimization_linux.services.narrator_persistence import (
    NarratorSettingsRepository,
)
from game_optimization_linux.services.narrator_tts import (
    PIPER_BASS_VOICE_ID,
    PIPER_VOIDEK_COMPONENT_ID,
    PIPER_VOIDEK_CONFIG_RELATIVE_PATH,
    PIPER_DARKMAN_VOICE_ID,
    PIPER_VOIDEK_MODEL_RELATIVE_PATH,
    PIPER_DEFAULT_VOICE_ID,
    PiperPolishTtsProvider,
)
from tests.test_narrator_end_to_end import _ManualExecutor, _pipeline

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "src/game_optimization_linux/qml"


@pytest.fixture()
def controller(tmp_path: Path):
    components = tmp_path / "components"
    model = components / PIPER_VOIDEK_COMPONENT_ID / PIPER_VOIDEK_MODEL_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"gosia")
    (components / PIPER_VOIDEK_COMPONENT_ID / PIPER_VOIDEK_CONFIG_RELATIVE_PATH).write_text(
        "{}", encoding="utf-8"
    )
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


def test_voice_list_contains_every_catalog_voice_with_metadata(controller) -> None:
    game_id = controller.games[0]["id"]
    voices = {
        voice["id"]: voice
        for voice in controller.getNarratorGameSettings(game_id)["voices"]
    }
    # Voidek is the only catalog voice; former catalog voices are never offered.
    assert set(voices) == {"voidek-pl-e991"}
    assert voices["voidek-pl-e991"]["legacy"] is False
    definitions = {
        definition.component_id: definition
        for definition in DEFAULT_NARRATOR_COMPONENTS
    }
    for voice in voices.values():
        definition = definitions[voice["componentId"]]
        assert voice["downloadSizeBytes"] == definition.download_size_bytes
        assert voice["licenseId"] == definition.license_id
        assert voice["runtimeLicenseId"] == definition.runtime_license_id
        assert voice["artifactLicenseId"] == definition.artifact_license_id
        assert voice["attribution"] == definition.attribution
        assert voice["version"] == definition.version
    assert voices["voidek-pl-e991"]["installed"] is True  # fixture: local Voidek files
    assert voices["voidek-pl-e991"]["canInstall"] is False  # no download URL published
    assert voices["voidek-pl-e991"]["downloadSizeBytes"] == 63_521_083
    components = {c["componentId"] for c in controller._narrator_controller.components()}
    assert "tts.voidek" in components and not components & {"tts.polish-voice", "tts.polish-bass", "tts.polish-darkman"}


def test_speaker_name_toggle_is_one_shared_setting(controller) -> None:
    game_id = controller.games[0]["id"]
    assert controller.getNarratorGameSettings(game_id)["readSpeakerNames"] is False
    # Both UIs call the same global save with the same key.
    assert controller.saveNarratorGlobalSettings({"readSpeakerNames": True})
    assert controller.getNarratorGlobalSettings()["readSpeakerNames"] is True
    assert controller.getNarratorGameSettings(game_id)["readSpeakerNames"] is True
    assert controller.saveNarratorGlobalSettings({"readSpeakerNames": False})
    assert controller.getNarratorGameSettings(game_id)["readSpeakerNames"] is False


def test_desktop_and_couch_toggles_write_the_same_global_value() -> None:
    desktop = (QML / "pages/NarratorPage.qml").read_text(encoding="utf-8")
    couch = (QML / "couch/CouchSettings.qml").read_text(encoding="utf-8")
    assert 'saveNarratorGlobalSettings({ "readSpeakerNames": enabled })' in desktop
    assert '"narratorReadSpeakerNames"' in desktop
    assert '"narrator-global-speaker-names"' in couch
    assert 'saveNarratorGlobal({ "readSpeakerNames":' in couch
    # Per-game saves never pin the shared value as a game override.
    payload = desktop[desktop.index("function settingsPayload()") :]
    payload = payload[: payload.index("function saveSettings()")]
    assert "readSpeakerNames" not in payload


def test_desktop_voices_moved_out_of_optional_components() -> None:
    desktop = (QML / "pages/NarratorPage.qml").read_text(encoding="utf-8")
    assert "model: page.nonVoiceComponents()" in desktop
    assert '!== "tts"' in desktop
    assert '"narratorVoiceList"' in desktop
    assert 'qsTr("Download (%1)")' in desktop
    assert 'qsTr("License and attribution")' in desktop
    assert "controller.removeNarratorComponent(" in desktop
    # Downloads reuse the existing component install path.
    assert "installSelectedVoice()" in desktop


def test_start_stop_lives_in_a_fixed_action_bar() -> None:
    desktop = (QML / "pages/NarratorPage.qml").read_text(encoding="utf-8")
    bar = desktop.index('objectName: "narratorActionBar"')
    scroll = desktop.index("ScrollView {")
    assert bar < scroll
    assert "anchors.top: actionBar.bottom" in desktop
    assert desktop.count('qsTr("Start narrator")') == 1
    assert desktop.index('objectName: "narratorStartStop"') < scroll
