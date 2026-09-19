from __future__ import annotations

from array import array
from concurrent.futures import Future
import importlib.util
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
import wave

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.controllers.narrator_controller import NarratorController
from game_optimization_linux.models import AppSettings
from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
from game_optimization_linux.services import (
    GamepadService,
    NoOpUiSoundService,
    SettingsStore,
    UiSoundService,
)


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])
_ROOT = Path(__file__).resolve().parents[1]
_AUDIO_ROOT = _ROOT / "src/game_optimization_linux/assets/audio"


def _callback_service() -> tuple[UiSoundService, list[str], list[tuple[str, float]]]:
    effects: list[str] = []
    music: list[tuple[str, float]] = []
    service = UiSoundService(
        player=effects.append,
        music_player=lambda event, volume: music.append((event, volume)),
    )
    service.configure_menu(
        sounds_enabled=True,
        sounds_volume=40,
        music_enabled=True,
        music_volume=20,
    )
    return service, effects, music


def test_couch_entry_exit_and_desktop_music_lifecycle() -> None:
    service, _effects, music = _callback_service()
    service.set_window_active(True)
    assert music == []  # Desktop Mode never starts Couch music.

    service.set_couch_active(True)
    assert music[:2] == [("start", 0.2), ("volume", 0.2)]
    assert service.musicPlaying is True

    service.set_couch_active(False)
    assert music[-2:] == [("volume", 0.0), ("pause", 0.0)]
    assert service.musicPlaying is False


def test_successful_launch_stays_silent_until_window_returns() -> None:
    service, effects, music = _callback_service()
    service.set_couch_active(True)
    service.set_window_active(True)

    service.begin_game_launch()
    assert effects == ["launch"]
    assert music[-2:] == [("volume", 0.0), ("pause", 0.0)]
    assert service.musicPlaying is False

    service.set_window_active(False)
    assert service.musicPlaying is False
    service.set_window_active(True)
    assert music[-2:] == [("start", 0.2), ("volume", 0.2)]
    assert service.musicPlaying is True


def test_failed_async_launch_restores_music_and_success_waits_for_return() -> None:
    service, _effects, _music = _callback_service()
    service.set_couch_active(True)
    service.set_window_active(True)

    class PollTarget:
        def __init__(self) -> None:
            self._manual_launch_jobs: dict[str, Future[Any]] = {}
            self._domain_games: dict[str, object] = {}
            self.toasts: list[tuple[str, str]] = []

        def _cancel_couch_launch_audio(self) -> None:
            service.cancel_game_launch()

        def _emit_toast(self, message: str, level: str) -> None:
            self.toasts.append((message, level))

    target = PollTarget()
    failed: Future[Any] = Future()
    service.begin_game_launch()
    failed.set_exception(RuntimeError("launch failed"))
    target._manual_launch_jobs["failed"] = failed
    AppController._poll_manual_launch_jobs(target)  # type: ignore[arg-type]
    assert service.musicPlaying is True
    assert target._manual_launch_jobs == {}
    assert target.toasts[-1] == ("Manual game: launch failed", "error")

    running: Future[Any] = Future()
    service.begin_game_launch()
    target._manual_launch_jobs["running"] = running
    AppController._poll_manual_launch_jobs(target)  # type: ignore[arg-type]
    assert service.musicPlaying is False
    assert "running" in target._manual_launch_jobs

    running.set_result(SimpleNamespace(error="", game_exit_code=0))
    AppController._poll_manual_launch_jobs(target)  # type: ignore[arg-type]
    assert service.musicPlaying is True
    assert target._manual_launch_jobs == {}


def test_live_audio_settings_update_service_and_persist(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    effects: list[str] = []
    music: list[tuple[str, float]] = []
    service = UiSoundService(
        player=effects.append,
        music_player=lambda event, volume: music.append((event, volume)),
    )
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=GamepadService(FakeGamepadProvider()),
        ui_sound_service=service,
        initial_interface_mode="couch",
        auto_refresh=False,
    )

    class Effect:
        volume = -1.0
        stopped = False

        def setVolume(self, value: float) -> None:
            self.volume = value

        def stop(self) -> None:
            self.stopped = True

    loaded_effect = Effect()
    service._effects["navigate"] = loaded_effect
    controller._configure_couch_audio()
    service.set_couch_active(True)
    service.set_window_active(True)
    try:
        assert controller.previewCouchAudioSetting("couchMusicVolume", 45) is True
        assert music[-1] == ("volume", pytest.approx(0.45))
        assert controller.settings["couchMusicVolume"] == 20
        assert store.load().couch_music_volume == 20

        assert controller.previewCouchAudioSetting("couchMenuSoundsVolume", 70) is True
        assert loaded_effect.volume == pytest.approx(0.70)
        assert controller.settings["couchMenuSoundsVolume"] == 40
        assert store.load().couch_menu_sounds_volume == 40
        assert controller.previewCouchAudioSetting("unrelatedSetting", 50) is False

        assert controller.saveSetting("couchMusicVolume", 35) is True
        assert music[-1] == ("volume", pytest.approx(0.35))
        assert controller.saveSetting("couchMusicEnabled", False) is True
        assert music[-1] == ("pause", 0.0)
        assert controller.saveSetting("couchMusicEnabled", True) is True
        assert music[-2:] == [("start", 0.35), ("volume", 0.35)]

        assert controller.saveSetting("couchMenuSoundsVolume", 65) is True
        assert loaded_effect.volume == pytest.approx(0.65)
        assert controller.playCouchSound("navigate") is True
        assert controller.saveSetting("couchMenuSoundsEnabled", False) is True
        assert loaded_effect.stopped is True
        assert controller.playCouchSound("confirm") is False
        assert controller.saveSetting("couchMenuSoundsEnabled", True) is True
        assert controller.playCouchSound("confirm") is True
    finally:
        controller.shutdown()

    restored = store.load()
    assert restored.couch_music_enabled is True
    assert restored.couch_music_volume == 35
    assert restored.couch_menu_sounds_enabled is True
    assert restored.couch_menu_sounds_volume == 65


def test_narrator_ducking_is_reversible_and_idempotent() -> None:
    service, _effects, music = _callback_service()
    service.set_couch_active(True)
    service.set_window_active(True)

    class Pipeline:
        def __init__(self) -> None:
            self.snapshot = SimpleNamespace(
                status=SimpleNamespace(value="speaking"),
                audio_status="speaking",
            )

        def poll_game_activity(self) -> None:
            return None

        def drain_events(self) -> tuple[object, ...]:
            return ()

    pipeline = Pipeline()
    app = SimpleNamespace(
        _narrator_pipeline=pipeline,
        _ui_sound_service=service,
        narratorChanged=SimpleNamespace(emit=lambda _game_id: None),
        _emit_toast=lambda _message, _level: None,
    )
    narrator = NarratorController.__new__(NarratorController)
    narrator._app = app
    narrator._poll_component_jobs = lambda: None  # type: ignore[method-assign]

    NarratorController.poll(narrator)
    duck_event_count = len(music)
    assert service.musicDucked is True
    assert music[-1] == ("volume", pytest.approx(0.056))

    NarratorController.poll(narrator)
    assert len(music) == duck_event_count

    pipeline.snapshot = SimpleNamespace(
        status=SimpleNamespace(value="idle"),
        audio_status="idle",
    )
    NarratorController.poll(narrator)
    unduck_event_count = len(music)
    assert service.musicDucked is False
    assert music[-1] == ("volume", pytest.approx(0.2))

    NarratorController.poll(narrator)
    assert len(music) == unduck_event_count


def test_shutdown_is_idempotent() -> None:
    service, _effects, music = _callback_service()
    service.set_couch_active(True)
    service.set_window_active(True)
    service.stop()
    events_after_first_stop = list(music)
    service.stop()
    assert music == events_after_first_stop
    assert service.musicPlaying is False
    assert service.playConfirm() is False


def test_missing_audio_device_and_missing_assets_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import PySide6.QtMultimedia as multimedia

    class NullDevice:
        @staticmethod
        def isNull() -> bool:
            return True

    class Devices:
        @staticmethod
        def defaultAudioOutput() -> NullDevice:
            return NullDevice()

    monkeypatch.setattr(multimedia, "QMediaDevices", Devices)
    no_device = UiSoundService(asset_root=_AUDIO_ROOT)
    no_device.configure_menu(
        sounds_enabled=True,
        sounds_volume=40,
        music_enabled=True,
        music_volume=20,
    )
    no_device.set_couch_active(True)
    no_device.set_window_active(True)
    assert no_device.musicPlaying is False
    no_device.stop()

    empty_assets = tmp_path / "empty-audio"
    empty_assets.mkdir()
    missing = UiSoundService(asset_root=empty_assets)
    missing.configure_menu(
        sounds_enabled=True,
        sounds_volume=40,
        music_enabled=True,
        music_volume=20,
    )
    missing.set_couch_active(True)
    missing.set_window_active(True)
    assert missing.playConfirm() is False
    assert missing.musicPlaying is False
    missing.stop()


def test_effect_backend_error_is_reported_and_never_returns_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def connect(self, callback: Any) -> None:
            self.callbacks.append(callback)

        def disconnect(self, callback: Any) -> None:
            self.callbacks.remove(callback)

        def emit(self, *arguments: object) -> None:
            for callback in tuple(self.callbacks):
                callback(*arguments)

    class FailedEffect:
        played = False

        @staticmethod
        def status() -> object:
            return SimpleNamespace(name="Error")

        def play(self) -> None:
            self.played = True

    class FakeMusicPlayer:
        def __init__(self) -> None:
            self.errorOccurred = FakeSignal()
            self.mediaStatusChanged = FakeSignal()

        @staticmethod
        def errorString() -> str:
            return "fixture media load failure"

    service = UiSoundService(
        player=lambda _kind: None,
        music_player=lambda _event, _volume: None,
    )
    effect = FailedEffect()
    player = FakeMusicPlayer()
    service._player = None
    service._initialized = True
    service._couch_active = True
    service._effects["confirm"] = effect
    player.errorOccurred.connect(service._on_music_error)
    player.mediaStatusChanged.connect(service._on_music_status_changed)
    service._music_player = player
    try:
        with caplog.at_level(
            logging.WARNING,
            logger="game_optimization_linux.services.ui_sound",
        ):
            assert service.playConfirm() is False
            player.errorOccurred.emit(
                SimpleNamespace(name="ResourceError"),
                "fixture backend failure",
            )
            player.mediaStatusChanged.emit(SimpleNamespace(name="InvalidMedia"))
        assert effect.played is False
        assert "effect confirm load" in caplog.text
        assert "Status.Error" in caplog.text
        assert "menu music backend" in caplog.text
        assert "menu music load" in caplog.text
    finally:
        player.errorOccurred.disconnect(service._on_music_error)
        player.mediaStatusChanged.disconnect(service._on_music_status_changed)
        service._effects.clear()
        service._music_player = None
        service.shutdown()
    assert player.errorOccurred.callbacks == []
    assert player.mediaStatusChanged.callbacks == []


def test_generated_audio_is_deterministic_packaged_and_flatpak_reachable(
    tmp_path: Path,
) -> None:
    generator_path = _ROOT / "scripts/generate-couch-audio.py"
    spec = importlib.util.spec_from_file_location("gameopti_couch_audio", generator_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generated = tmp_path / "generated"
    generator.OUTPUT_DIR = generated
    generator.main()

    expected = {
        "navigate.wav", "confirm.wav", "back.wav", "open.wav", "close.wav",
        "adjust.wav", "error.wav", "launch.wav", "menu-ambient.wav",
    }
    assert {path.name for path in generated.glob("*.wav")} == expected
    for name in expected:
        assert (generated / name).read_bytes() == (_AUDIO_ROOT / name).read_bytes()

    notice = (_AUDIO_ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "original procedural assets" in notice
    assert "License: MIT" in notice
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"assets/audio/*.wav"' in pyproject
    assert '"assets/audio/*.md"' in pyproject
    manifest = (
        _ROOT / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
    ).read_text(encoding="utf-8")
    assert "--socket=pulseaudio" in manifest
    assert "pip install --no-index --no-deps --no-build-isolation" in manifest
    assert _AUDIO_ROOT == Path(
        __import__("game_optimization_linux.services.ui_sound", fromlist=["_DEFAULT_ASSET_ROOT"])
        ._DEFAULT_ASSET_ROOT
    )


def test_menu_music_is_long_stereo_structured_and_loop_safe() -> None:
    path = _AUDIO_ROOT / "menu-ambient.wav"
    with wave.open(str(path), "rb") as source:
        assert source.getnchannels() == 2
        assert source.getsampwidth() == 2
        assert source.getframerate() == 44_100
        assert source.getnframes() == 120 * 44_100
        assert 90 <= source.getnframes() / source.getframerate() <= 180

        window_frames = source.getframerate() * 10
        left_sum = 0
        right_sum = 0
        difference_energy = 0
        peak = 0
        window_rms: list[float] = []
        first_frame: tuple[int, int] | None = None
        last_frame: tuple[int, int] | None = None
        total_frames = 0
        while True:
            raw = source.readframes(window_frames)
            if not raw:
                break
            samples = array("h")
            samples.frombytes(raw)
            if sys.byteorder != "little":
                samples.byteswap()
            energy = 0
            frame_count = len(samples) // 2
            for index in range(0, len(samples), 2):
                left = samples[index]
                right = samples[index + 1]
                if first_frame is None:
                    first_frame = (left, right)
                last_frame = (left, right)
                left_sum += left
                right_sum += right
                energy += left * left + right * right
                difference_energy += (left - right) ** 2
                peak = max(peak, abs(left), abs(right))
            total_frames += frame_count
            window_rms.append(
                math.sqrt(energy / (frame_count * 2)) / 32_767
            )

    assert total_frames == 120 * 44_100
    assert 0.01 < min(window_rms) < max(window_rms) < 0.20
    assert max(window_rms) - min(window_rms) > 0.003
    assert peak / 32_767 < 0.50
    assert abs(left_sum / total_frames / 32_767) < 0.001
    assert abs(right_sum / total_frames / 32_767) < 0.001
    assert math.sqrt(difference_energy / total_frames) / 32_767 > 0.004
    assert first_frame is not None and max(map(abs, first_frame)) <= 2
    assert last_frame is not None and max(map(abs, last_frame)) <= 2


def test_desktop_app_controller_does_not_initialize_multimedia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preload_calls: list[UiSoundService] = []
    monkeypatch.setattr(
        UiSoundService,
        "_preload_effects",
        lambda service: preload_calls.append(service),
    )
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(tmp_path / "lazy-settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider()),
        initial_interface_mode="desktop",
        auto_refresh=False,
    )
    try:
        assert controller.interfaceMode == "desktop"
        assert controller._ui_sound_service._initialized is False
        assert preload_calls == []
    finally:
        controller.shutdown()
    assert preload_calls == []


def test_noop_audio_service_exposes_complete_semantic_api() -> None:
    service = NoOpUiSoundService()
    required = (
        "playNavigate", "playConfirm", "playBack", "playOpen", "playClose",
        "playAdjust", "playError", "playLaunch", "startMenuMusic",
        "stopMenuMusic", "setMusicDucked", "configure_menu", "shutdown",
    )
    assert all(callable(getattr(service, name, None)) for name in required)
    service.configure_menu(
        sounds_enabled=True,
        sounds_volume=40,
        music_enabled=True,
        music_volume=20,
    )
    service.startMenuMusic()
    service.set_window_active(True)
    assert service.musicPlaying is True
    for name in required[:8]:
        assert getattr(service, name)() is True
    service.setMusicDucked(True)
    assert service.musicDucked is True
    service.stopMenuMusic()
    assert service.musicPlaying is False
    service.shutdown()
    service.shutdown()


def test_real_audio_shutdown_before_initialization_is_safe() -> None:
    service = UiSoundService()
    assert service._initialized is False
    assert service._effects == {}
    service.shutdown()
    service.shutdown()
    assert service._initialized is False
    assert service._effects == {}


def test_real_audio_shutdown_releases_initialized_objects_in_order() -> None:
    events: list[str] = []

    class Effect:
        def stop(self) -> None:
            events.append("effect-stop")

        def setSource(self, _source: object) -> None:
            events.append("effect-detach-source")

        def deleteLater(self) -> None:
            events.append("effect-delete")

    class Player:
        def stop(self) -> None:
            events.append("player-stop")

        def setAudioOutput(self, output: object | None) -> None:
            assert output is None
            events.append("player-detach-output")

        def deleteLater(self) -> None:
            events.append("player-delete")

    class Output:
        def setVolume(self, volume: float) -> None:
            assert volume == 0.0
            events.append("output-zero")

        def deleteLater(self) -> None:
            events.append("output-delete")

    service = UiSoundService(player=lambda _kind: None, music_player=lambda *_args: None)
    service._initialized = True
    service._effects["confirm"] = Effect()
    service._music_player = Player()
    service._music_output = Output()
    service._music_started = True
    service.shutdown()

    expected_order = (
        "output-zero",
        "effect-stop",
        "player-stop",
        "player-detach-output",
        "effect-detach-source",
        "effect-delete",
        "player-delete",
        "output-zero",
        "output-delete",
    )
    assert tuple(events) == expected_order
    assert service._effects == {}
    assert service._music_player is None
    assert service._music_output is None
    service.shutdown()
    assert tuple(events) == expected_order


def test_ducking_after_shutdown_cannot_initialize_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preload_calls: list[UiSoundService] = []
    monkeypatch.setattr(
        UiSoundService,
        "_preload_effects",
        lambda service: preload_calls.append(service),
    )
    service = UiSoundService()
    service.shutdown()
    service.set_music_ducked(True)
    service.setMusicDucked(False)
    assert service._initialized is False
    assert preload_calls == []


def test_couch_qml_probe_subprocess_exits_without_multimedia_teardown_crash(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(_ROOT / "src"),
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": "1.0",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "tests/qml_runtime_probe.py"),
            "couch",
            "--width",
            "1600",
            "--height",
            "900",
        ],
        cwd=_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "RESULT:" in completed.stdout
