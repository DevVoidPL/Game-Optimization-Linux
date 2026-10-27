"""Voice catalog: Voidek is the only catalog voice and the default; former voices stay usable
until the user removes them; custom import is unchanged. Synthetic files only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from game_optimization_linux.services.narrator_components import (
    DEFAULT_NARRATOR_COMPONENTS,
    NarratorComponentManager,
)
from game_optimization_linux.services.narrator_tts import (
    LEGACY_POLISH_VOICES,
    MANAGED_VOICE_COMPONENTS,
    PIPER_DEFAULT_VOICE_ID,
    PIPER_VOIDEK_COMPONENT_ID,
    POLISH_VOICES,
    PiperPolishTtsProvider,
)

ROOT = Path(__file__).resolve().parents[1]
VOIDEK = next(item for item in DEFAULT_NARRATOR_COMPONENTS if item.component_id == PIPER_VOIDEK_COMPONENT_ID)
LEGACY = ("tts.polish-voice", "tts.polish-bass", "tts.polish-darkman")


def test_voidek_is_the_only_catalog_voice_and_the_default() -> None:
    assert [voice.voice_id for voice in POLISH_VOICES] == ["voidek-pl-e991"]
    assert PIPER_DEFAULT_VOICE_ID == "voidek-pl-e991"
    assert PiperPolishTtsProvider.default_voice_id == "voidek-pl-e991"
    assert {voice.component_id for voice in LEGACY_POLISH_VOICES} == set(LEGACY)
    assert MANAGED_VOICE_COMPONENTS[PIPER_VOIDEK_COMPONENT_ID] == "voidek-pl-e991"


def test_voidek_component_is_pinned_and_has_no_placeholder_url() -> None:
    files = {item.target_relative_path: item for item in VOIDEK.pinned_files}
    assert set(files) == {"voices/voidek-pl-e991.onnx", "voices/voidek-pl-e991.onnx.json"}
    assert files["voices/voidek-pl-e991.onnx"].size_bytes == 63_516_050
    assert files["voices/voidek-pl-e991.onnx.json"].size_bytes == 5_033
    assert files["voices/voidek-pl-e991.onnx"].sha256.startswith("ca2ad0a22273e385")
    assert VOIDEK.download_size_bytes == 63_521_083
    # Until the release asset exists the build never offers a download.
    assert VOIDEK.install_ready is False and VOIDEK.artifacts == () and VOIDEK.source_url == ""
    if VOIDEK.install_ready:  # once published: the artifacts must be exactly the pinned files
        assert {(a.target_relative_path, a.size_bytes, a.sha256) for a in VOIDEK.artifacts} == {
            (f.target_relative_path, f.size_bytes, f.sha256) for f in VOIDEK.pinned_files}
    manager = NarratorComponentManager(Path("/nonexistent"))
    assert manager.can_install(PIPER_VOIDEK_COMPONENT_ID) is False
    assert manager.offer_message(PIPER_VOIDEK_COMPONENT_ID) == "The Voidek voice is included with GameOpti"


def _pinned_copy(tmp_path: Path) -> tuple[NarratorComponentManager, dict[str, Path]]:
    """A test definition whose pinned digests match small synthetic files."""
    from dataclasses import replace
    from game_optimization_linux.services.narrator_components import NarratorPinnedFile
    model, config = tmp_path / "src/a.onnx", tmp_path / "src/a.onnx.json"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"synthetic-voidek-model")
    config.write_text(json.dumps({"audio": {"sample_rate": 22050}, "espeak": {"voice": "pl"}}))
    pinned = tuple(NarratorPinnedFile(target, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
                   for target, path in (("voices/voidek-pl-e991.onnx", model), ("voices/voidek-pl-e991.onnx.json", config)))
    definitions = tuple(replace(item, pinned_files=pinned) if item.component_id == PIPER_VOIDEK_COMPONENT_ID else item
                        for item in DEFAULT_NARRATOR_COMPONENTS)
    manager = NarratorComponentManager(tmp_path / "components", definitions=definitions)
    return manager, {"voices/voidek-pl-e991.onnx": model, "voices/voidek-pl-e991.onnx.json": config}


def test_local_install_accepts_only_the_pinned_bytes_and_makes_voidek_the_effective_voice(tmp_path) -> None:
    manager, sources = _pinned_copy(tmp_path)
    tampered = tmp_path / "tampered.onnx"
    tampered.write_bytes(b"synthetic-voidek-modeX")
    with pytest.raises(RuntimeError, match="failed verification"):
        manager.install_local(PIPER_VOIDEK_COMPONENT_ID, {**sources, "voices/voidek-pl-e991.onnx": tampered})
    assert not (tmp_path / "components" / PIPER_VOIDEK_COMPONENT_ID).exists()
    with pytest.raises(RuntimeError, match="exactly the pinned files"):
        manager.install_local(PIPER_VOIDEK_COMPONENT_ID, {"voices/voidek-pl-e991.onnx": sources["voices/voidek-pl-e991.onnx"]})
    manager.install_local(PIPER_VOIDEK_COMPONENT_ID, sources)
    assert manager.verify_installed(PIPER_VOIDEK_COMPONENT_ID) == (True, "")
    manifest = json.loads((tmp_path / "components" / PIPER_VOIDEK_COMPONENT_ID / "component.json").read_text())
    assert manifest["source"] == "local-pinned" and "source_url" not in manifest
    provider = PiperPolishTtsProvider(tmp_path / "components", runtime_available=lambda: True)
    assert provider.available_voice_ids == ("voidek-pl-e991",)
    # Removal only touches GameOpti's own component directory.
    assert manager.remove(PIPER_VOIDEK_COMPONENT_ID) is True
    assert sources["voices/voidek-pl-e991.onnx"].is_file()


def test_former_voices_are_hidden_and_never_downloaded_but_their_files_are_kept(tmp_path) -> None:
    root = tmp_path / "components"
    manager = NarratorComponentManager(root)
    listed = {component.component_id for component in manager.list_components()}
    assert PIPER_VOIDEK_COMPONENT_ID in listed and not listed & set(LEGACY)
    for component_id in LEGACY:
        assert manager.can_install(component_id) is False and manager.is_legacy(component_id)
        with pytest.raises(RuntimeError, match="no longer offered"):
            manager.install(component_id)
    # A user who already downloaded Darkman keeps the files after the update.
    directory = root / "tts.polish-darkman"
    (directory / "voices").mkdir(parents=True)
    model = directory / "voices/pl_PL-darkman-medium.onnx"
    config = directory / "voices/pl_PL-darkman-medium.onnx.json"
    model.write_bytes(b"darkman")
    config.write_text("{}")
    from game_optimization_linux.services.narrator_components import NARRATOR_COMPONENT_SCHEMA_VERSION
    (directory / "component.json").write_text(json.dumps({
        "schema_version": NARRATOR_COMPONENT_SCHEMA_VERSION, "managed_by": "game-optimization-linux", "component_id": "tts.polish-darkman", "version": "piper-voices-c10ece1",
        "files": [{"path": "voices/pl_PL-darkman-medium.onnx", "sha256": hashlib.sha256(b"darkman").hexdigest()},
                  {"path": "voices/pl_PL-darkman-medium.onnx.json", "sha256": hashlib.sha256(b"{}").hexdigest()}]}))
    assert "tts.polish-darkman" not in {component.component_id for component in manager.list_components()}
    assert manager.installed_legacy_components() == ("tts.polish-darkman",)
    provider = PiperPolishTtsProvider(root, runtime_available=lambda: True)
    assert provider.available_voice_ids == ()  # hidden from the normal selector
    assert model.is_file() and config.is_file()  # an update never deletes the files
    assert manager.remove("tts.polish-darkman") is True  # only a separate explicit action
    assert not directory.exists()


def test_custom_import_is_unchanged(tmp_path) -> None:
    source = tmp_path / "moj-glos.onnx"
    source.write_bytes(b"user-model")
    source.with_name("moj-glos.onnx.json").write_text(json.dumps({
        "num_symbols": 256, "num_speakers": 1, "audio": {"sample_rate": 22050, "quality": "medium"},
        "espeak": {"voice": "pl"}, "language": {"code": "pl_PL"}, "phoneme_id_map": {"a": [1]}}))
    provider = PiperPolishTtsProvider(tmp_path / "components", runtime_available=lambda: True)
    voice = provider.import_custom_voice(source)
    assert voice.custom and voice.voice_id in provider.available_voice_ids


def test_no_voice_model_is_part_of_the_repository_or_package_inputs() -> None:
    for pattern in ("*.onnx", "*.onnx.json", "*.ckpt"):
        found = [path for path in ROOT.joinpath("src").rglob(pattern)]
        found += [path for path in ROOT.joinpath("data").rglob(pattern)] if ROOT.joinpath("data").is_dir() else []
        assert not found, found
    # Release Flatpaks bundle exactly the pinned Voidek files and no other voice.
    from game_optimization_linux.services.narrator_components import NarratorComponentManager
    pinned = {item.sha256 for item in NarratorComponentManager()._definitions["tts.voidek"].pinned_files}
    for name in ("io.github.DevVoidPL.GameOptimizationLinux.yml", "io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml"):
        manifest = (ROOT / "flatpak" / name).read_text()
        import re
        assert set(re.findall(r"[\w.-]+\.onnx(?:\.json)?", manifest)) == {"voidek-pl-e991.onnx", "voidek-pl-e991.onnx.json"}, name
        assert all(digest in manifest for digest in pinned), name
        for forbidden in ("gosia", "bass", "darkman", "custom-voices", "piper-voice-", "epoch"):
            assert forbidden not in manifest.casefold(), (name, forbidden)


def test_locally_installed_voidek_is_ready_without_a_download_url_and_is_the_default(tmp_path) -> None:
    """The real controller path: hash-verified local Voidek counts as installed and ready."""
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.providers import DemoGameProvider
    from game_optimization_linux.services import MockTaskService, SettingsStore
    from game_optimization_linux.services.narrator_persistence import NarratorSettingsRepository
    from tests.test_narrator_end_to_end import _ManualExecutor, _pipeline
    manager, sources = _pinned_copy(tmp_path)
    manager.install_local(PIPER_VOIDEK_COMPONENT_ID, sources)
    definition = manager._definitions[PIPER_VOIDEK_COMPONENT_ID]
    assert definition.install_ready is False and definition.artifacts == ()
    tts = PiperPolishTtsProvider(tmp_path / "components", runtime_available=lambda: True)
    pipeline, *_rest = _pipeline(tmp_path, _ManualExecutor(), tts=tts)
    app = AppController(game_provider=DemoGameProvider(), task_service=MockTaskService(),
                        settings_store=SettingsStore(tmp_path / "settings.json"),
                        narrator_settings_repository=NarratorSettingsRepository(tmp_path / "games"),
                        narrator_component_manager=manager, narrator_pipeline=pipeline, auto_refresh=False)
    try:
        app._narrator_controller._refresh_component_runtime(PIPER_VOIDEK_COMPONENT_ID)
        component = {c["componentId"]: c for c in app._narrator_controller.components()}[PIPER_VOIDEK_COMPONENT_ID]
        assert component["state"] == "available" and component["managed"] is True and component["canInstall"] is False
        settings = app.getNarratorGameSettings(app.games[0]["id"])
        assert settings["voiceId"] == "voidek-pl-e991"
        assert [voice["id"] for voice in settings["voices"]] == ["voidek-pl-e991"]
        assert settings["voices"][0]["installed"] is True and settings["voices"][0]["available"] is True
    finally:
        app.shutdown()
