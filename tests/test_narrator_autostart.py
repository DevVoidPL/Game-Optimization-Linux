"""Narrator: effective voice, unknown activity, honest card states, autostart."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.models.narrator import NarratorGameSettings
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.narrator_activity import NarratorGameActivityDetector
from game_optimization_linux.services.narrator_components import NarratorComponentManager
from game_optimization_linux.services.narrator_persistence import NarratorSettingsRepository
from tests.test_narrator_end_to_end import _ManualExecutor, _pipeline, _settings, _Tts

BASS = "pl_PL-bass-high"


class _BassOnly(_Tts):
    available_voice_ids = (BASS,)
    default_voice_id = "pl_PL-gosia-medium"      # the removed voice


class _Activity:
    """Fake game process: controllable, counts probes, supports autostart."""

    def __init__(self, active: bool | None = None, supported: bool = True) -> None:
        self.active, self.supported, self.calls = active, supported, 0

    def is_active(self, _game_key: str) -> bool | None:
        self.calls += 1
        return self.active

    def supports(self, _game: object) -> bool:
        return self.supported

    def invalidate(self, _game_key: str = "") -> None:
        pass


@pytest.fixture()
def narrator(tmp_path: Path):
    pipeline, capture, _activity, _translator, _tts, _audio = _pipeline(tmp_path, _ManualExecutor(), tts=_BassOnly())
    activity = _Activity()
    pipeline.activity = activity
    repository = NarratorSettingsRepository(tmp_path / "games")
    controller = AppController(
        game_provider=DemoGameProvider(), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=repository,
        narrator_component_manager=NarratorComponentManager(tmp_path / "components"),
        narrator_pipeline=pipeline, auto_refresh=False,
    )
    narrator = controller._narrator_controller
    narrator._missing_requirements = lambda settings=None: ()   # components ready
    game_id = controller.games[0]["id"]
    game = controller._resolve_game(game_id, show_error=False)
    key = narrator._game_key(game)

    def save(**changes):
        base = replace(_settings(**changes), game_key=key)
        repository.save(base)

    yield controller, narrator, pipeline, capture, activity, game, save
    controller.shutdown()


def test_first_installed_voice_is_used_when_saved_voice_is_missing(narrator) -> None:
    _controller, controller_narrator, pipeline, _capture, _activity, _game, _save = narrator
    for saved in ("", "pl_PL-gosia-medium"):
        settings = replace(NarratorGameSettings.default("292030"), voice_id=saved)
        assert controller_narrator.effective_voice_id(settings) == BASS
    snapshot = pipeline.start(_settings(voice_id="pl_PL-gosia-medium"))   # no rejection
    assert pipeline._settings.voice_id == BASS and snapshot is not None


def test_unknown_game_state_does_not_block_manual_start(narrator) -> None:
    controller, _n, pipeline, capture, activity, game, save = narrator
    save(voice_id="")
    activity.active = None                        # Flatpak without a process list
    state = controller.getNarratorSessionState(game.id)
    assert state["canStart"] is True and state["gameActivity"] == "unknown"
    assert state["voiceId"] == BASS
    assert controller.startNarrator(game.id) is True and pipeline.active and capture.requests


@pytest.mark.parametrize(
    ("enabled", "missing", "supported", "expected"),
    [(False, (), True, "disabled"), (True, ("tts",), True, "missing"),
     (True, (), True, "waiting_for_game"), (True, (), False, "manual")],
)
def test_card_state_mapping(narrator, enabled, missing, supported, expected) -> None:
    controller, controller_narrator, _p, _c, activity, game, save = narrator
    save(enabled=enabled)
    controller_narrator._missing_requirements = lambda settings=None: missing
    activity.supported = supported
    state = controller.getNarratorSessionState(game.id)
    assert state["cardState"] == expected
    if expected == "missing":
        assert state["missingRequirements"] == ["tts"]


def test_autostart_with_game_and_autostop_after_exit(narrator) -> None:
    controller, controller_narrator, pipeline, capture, activity, game, save = narrator
    save()
    activity.active = False
    controller_narrator.game_launched(game)
    controller_narrator.poll()
    assert not pipeline.active                     # game not running yet
    activity.active = True
    controller_narrator.poll()
    assert pipeline.active and capture.requests    # started with the game
    assert controller.getNarratorSessionState(game.id)["cardState"] == "running"
    clock = [1000.0]
    pipeline._clock = lambda: clock[0]
    activity.active = False
    controller_narrator.poll()
    clock[0] += 3.0
    controller_narrator.poll()
    assert not pipeline.active                     # stopped after the game exited


def _game(root: Path, app_id: str = "292030") -> Game:
    return Game(id=f"steam-{app_id}", steam_app_id=app_id, name="Witcher", launcher=Launcher.STEAM,
                install_path=root, logical_size_gb=1, physical_size_gb=1,
                filesystem=FilesystemType.EXT4, compression_available=False)


def test_host_process_detection_is_throttled_and_matches_steam_reaper(tmp_path: Path) -> None:
    root = tmp_path / "steamapps" / "common" / "The Witcher 3"
    game = _game(root)
    calls, clock = [], [0.0]
    lines = ["/home/u/.steam/ubuntu12_32/reaper SteamLaunch AppId=292030 -- proton waitforexitandrun"]

    def processes():
        calls.append(1)
        return lines

    detector = NarratorGameActivityDetector(lambda _key: game, host_processes=processes,
                                            sandboxed=True, clock=lambda: clock[0])
    for _ in range(50):                            # e.g. 150 ms UI ticks
        assert detector.is_active("292030") is True
    assert len(calls) == 1
    clock[0] += 5.0
    lines[:] = ["/usr/bin/steam", "reaper SteamLaunch AppId=2920301 -- x"]
    assert detector.is_active("292030") is False and len(calls) == 2
    lines[:] = [f"Z:{str(root).replace('/', chr(92))}\\bin\\x64_dx12\\witcher3.exe"]
    clock[0] += 5.0
    assert detector.is_active("292030") is True    # Wine path (Heroic / custom)
    sandbox_without_host = NarratorGameActivityDetector(lambda _key: game, sandboxed=True)
    assert sandbox_without_host.is_active("292030") is None
    assert sandbox_without_host.supports(game) is False
