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

    def game_process_running(self, game_key: str) -> bool | None:
        return self.is_active(game_key)

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


def test_autostart_waits_for_stable_game_process_then_autostops(narrator) -> None:
    controller, controller_narrator, pipeline, capture, activity, game, save = narrator
    save()
    clock = [100.0]
    controller_narrator._clock = pipeline._clock = lambda: clock[0]
    activity.active = False
    controller_narrator.poll()                     # game started outside GameOpti
    activity.active = True
    controller_narrator.poll()
    clock[0] += 10.0
    controller_narrator.poll()
    assert not pipeline.active                     # process not stable long enough
    clock[0] += 5.0
    controller_narrator.poll()
    assert pipeline.active and capture.requests    # started with the game
    assert controller.getNarratorSessionState(game.id)["cardState"] == "running"
    activity.active = False
    controller_narrator.poll()
    clock[0] += 3.0
    controller_narrator.poll()
    assert not pipeline.active                     # stopped after the game exited


def test_manual_stop_is_not_undone_and_gamepad_shortcut_toggles(narrator) -> None:
    controller, controller_narrator, pipeline, _capture, activity, game, save = narrator
    save()
    sounds = []
    controller._ui_sound_service.play_feedback = sounds.append
    activity.active = True
    assert controller_narrator.toggle_for_running_game() and pipeline.active
    controller_narrator.poll()
    controller._gamepad_service.shortcutTriggered.emit("narrator_toggle")
    assert not pipeline.active and sounds == ["open", "close"]
    controller_narrator._clock = lambda: 10_000.0
    controller_narrator.poll()
    assert not pipeline.active                     # the user stopped it; no autostart


def test_choose_window_again_clears_only_this_game(narrator) -> None:
    controller, controller_narrator, pipeline, _capture, _activity, game, save = narrator
    grants = pipeline.capture._grants = type("G", (), {})()
    grants.tokens = {"other": "t2", controller_narrator._game_key(game): "t1"}
    grants.save_token = lambda key, token: grants.tokens.__setitem__(key, token)
    grants.load_token = lambda key: grants.tokens.get(key, "")
    assert controller.chooseNarratorWindowAgain(game.id)
    assert grants.tokens == {"other": "t2", controller_narrator._game_key(game): ""}


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
    # Autostart needs the game's own executable, not a wrapper or launcher.
    clock[0] += 5.0
    lines[:] = [f"reaper SteamLaunch AppId=292030 -- {root}/Play.sh", f"Z:{str(root).replace('/', chr(92))}\\PlayGTAV.exe"]
    assert detector.is_active("292030") is True and detector.game_process_running("292030") is False
    clock[0] += 5.0
    lines.append(f"Z:{str(root).replace('/', chr(92))}\\bin\\witcher3.exe -dx12")
    assert detector.game_process_running("292030") is True
    sandbox_without_host = NarratorGameActivityDetector(lambda _key: game, sandboxed=True)
    assert sandbox_without_host.is_active("292030") is None
    assert sandbox_without_host.supports(game) is False


def test_gamepad_select_y_hold_emits_shortcut_once(monkeypatch) -> None:
    from game_optimization_linux.models import GamepadEvent
    from game_optimization_linux.providers import FakeGamepadProvider
    from game_optimization_linux.services import GamepadService
    import game_optimization_linux.services.gamepad as gamepad_module

    provider = FakeGamepadProvider()
    service = GamepadService(provider)
    shortcuts, actions, now = [], [], [50.0]
    service.shortcutTriggered.connect(shortcuts.append)
    service.actionTriggered.connect(actions.append)
    monkeypatch.setattr(gamepad_module.time, "monotonic", lambda: now[0])
    for control in ("back", "north"):
        provider.emit(GamepadEvent("button", 1, control, True, 1.0))
    service.pollNow()
    now[0] += 0.5
    service.pollNow()
    assert shortcuts == []
    now[0] += 0.6
    service.pollNow()
    service.pollNow()
    assert shortcuts == ["narrator_toggle"]
    provider.emit(GamepadEvent("button", 1, "back", False, 0.0))
    service.pollNow()
    assert "ContextAction1" not in actions and "context_action_1" not in actions


def test_strict_detection_matches_proton_wrapper_launched_game_exe(tmp_path: Path) -> None:
    """Steam/Proton launch the title through a wrapper (reaper/proton/wine),
    so the game's own .exe is only a later argv token. Autostart must still
    recognise it, while bare wrappers and launcher stubs must not count."""

    root = tmp_path / "steamapps" / "common" / "Batman Arkham Knight"
    game = _game(root, "208650")
    lines: list[str] = []
    detector = NarratorGameActivityDetector(
        lambda _key: game, host_processes=lambda: list(lines), sandboxed=True, clock=lambda: 0.0
    )
    wine = "Z:" + str(root).replace("/", chr(92))
    # Reaper wrapper as argv[0], game .exe as a later argument (unix path form).
    lines[:] = [f"reaper SteamLaunch AppId=208650 -- {root}/Binaries/Win64/BatmanAK.exe -nomovies"]
    assert detector.game_process_running("208650") is True
    detector.invalidate("208650")
    # Wine loader as argv[0], game .exe in Z: wine-path form as a later argument.
    lines[:] = [f".../proton/dist/bin/wine64 {wine}\\Binaries\\Win64\\BatmanAK.exe"]
    assert detector.game_process_running("208650") is True
    detector.invalidate("208650")
    # Bare wrapper and launcher stubs never count as the running game.
    lines[:] = [
        "reaper SteamLaunch AppId=208650 -- proton waitforexitandrun",
        f"{wine}\\PlayGTAV.exe",
        f"reaper SteamLaunch AppId=208650 -- {root}/Play.sh",
    ]
    assert detector.game_process_running("208650") is False


def test_capture_closed_triggers_one_automatic_retry(narrator) -> None:
    _controller, controller_narrator, _p, _c, _activity, game, _save = narrator
    key = controller_narrator._game_key(game)
    starts: list[str] = []
    controller_narrator.start = lambda game_id, automatic=False: (starts.append(game_id) or True)

    class _Event:
        def __init__(self, status: str, message: str) -> None:
            self.status = type("S", (), {"value": status})()
            self.message = message
            self.game_key = key

    closed = _Event("error", "The portal capture session closed")
    # First closure retries once; a second identical closure does not.
    assert controller_narrator._retry_after_capture_closed(closed, game) is True
    assert controller_narrator._retry_after_capture_closed(closed, game) is False
    assert starts == [game.id]
    # A healthy session clears the guard, so a later closure can retry again.
    controller_narrator._capture_retry_session.discard(key)
    assert controller_narrator._retry_after_capture_closed(closed, game) is True
    # Unrelated errors are never retried.
    other = _Event("error", "Screen capture stopped")
    assert controller_narrator._retry_after_capture_closed(other, game) is False
    assert starts == [game.id, game.id]


def test_gamepad_navigation_is_gated_by_window_active_but_shortcut_is_not(narrator) -> None:
    controller, controller_narrator, _p, _c, _a, _game, _save = narrator
    controller._interface_mode = "couch"
    dispatched: list[str] = []
    controller._couch_navigation.dispatch = lambda value: (dispatched.append(value) or True)
    toggles: list[int] = []
    controller_narrator.toggle_for_running_game = lambda: toggles.append(1)

    # Window inactive (a game holds focus): navigation is ignored...
    controller.setCouchWindowActive(False)
    controller._on_gamepad_action("NavigateDown")
    assert dispatched == []
    # ...but the Select+Y narrator shortcut still works.
    controller._on_gamepad_shortcut("narrator_toggle")
    assert toggles == [1]

    # Window active again: navigation is processed.
    controller.setCouchWindowActive(True)
    controller._on_gamepad_action("NavigateDown")
    assert dispatched == ["NavigateDown"]
