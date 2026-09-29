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
    assert not pipeline.active                     # fixed 15 s delay not reached
    clock[0] += 6.0
    controller_narrator.poll()
    assert pipeline.active and capture.requests    # started with the game
    assert controller.getNarratorSessionState(game.id)["cardState"] == "running"
    activity.active = False
    controller_narrator.poll()
    assert pipeline.active                         # one negative read is not "ended"
    clock[0] += 3.0
    controller_narrator.poll()
    assert not pipeline.active                     # automatic session stopped after 2 reads


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


def test_gamepad_select_y_press_fires_shortcut_with_cooldown(monkeypatch, caplog) -> None:
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

    def tap_y() -> None:
        provider.emit(GamepadEvent("button", 1, "north", True, 1.0))
        provider.emit(GamepadEvent("button", 1, "north", False, 0.0))
        service.pollNow()

    provider.emit(GamepadEvent("button", 1, "back", True, 1.0))
    with caplog.at_level("INFO", logger="game_optimization_linux.services.gamepad"):
        tap_y()                                     # Select held + Y tap: fires now
    assert shortcuts == ["narrator_toggle"]
    assert "Select+Y fired" in " ".join(r.getMessage() for r in caplog.records)
    now[0] += 1.0
    tap_y()                                         # within 1.5 s cooldown
    assert shortcuts == ["narrator_toggle"]
    now[0] += 0.6
    tap_y()                                         # cooldown over
    assert shortcuts == ["narrator_toggle", "narrator_toggle"]
    provider.emit(GamepadEvent("button", 1, "back", False, 0.0))
    service.pollNow()
    # Neither Y ("More actions") nor the Select release reached the UI.
    assert not {"MoreActions", "ContextAction1"} & set(actions)
    # Y alone (Select not held) stays a normal button.
    now[0] += 5.0
    tap_y()
    assert shortcuts == ["narrator_toggle", "narrator_toggle"] and "MoreActions" in actions


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


def test_capture_closed_rechecks_after_8s_and_retries_only_short_sessions(narrator) -> None:
    _controller, controller_narrator, _p, _c, activity, game, _save = narrator
    key = controller_narrator._game_key(game)
    clock = [1000.0]
    controller_narrator._clock = lambda: clock[0]
    starts: list[str] = []
    stops: list[int] = []
    toasts: list[tuple[str, str]] = []
    controller_narrator.start = lambda game_id, automatic=False: (starts.append(game_id) or True)
    controller_narrator.stop = lambda: (stops.append(1) or True)
    _controller._emit_toast = lambda message, kind: toasts.append((message, kind))

    class _Event:
        def __init__(self, message: str) -> None:
            self.status = type("S", (), {"value": "error"})()
            self.message = message
            self.game_key = key

    closed = _Event("The portal capture session closed")

    def close_after(session_seconds: float) -> None:
        controller_narrator._session_started[key] = (clock[0] - session_seconds, True)
        assert controller_narrator._retry_after_capture_closed(closed, game) is True

    # Splash -> main window: short session, game still running after 8 s.
    activity.active = True
    close_after(20.0)
    clock[0] += 7.0
    controller_narrator._poll_capture_closed(clock[0])
    assert starts == [] and stops == []            # nothing before 8 s
    clock[0] += 1.5
    controller_narrator._poll_capture_closed(clock[0])
    assert starts == [game.id] and stops == []     # one retry
    # A second quick closure is not retried again: silent stop.
    close_after(10.0)
    clock[0] += 9.0
    controller_narrator._poll_capture_closed(clock[0])
    assert starts == [game.id] and stops == [1]
    # Game exit: the window closes after a long session -> silent stop even if
    # the process is still visible a moment later.
    controller_narrator._capture_retry_session.clear()
    close_after(600.0)
    clock[0] += 9.0
    controller_narrator._poll_capture_closed(clock[0])
    assert starts == [game.id] and stops == [1, 1]
    # Short session but the game ended within the 8 s: silent stop.
    activity.active = False
    close_after(20.0)
    clock[0] += 9.0
    controller_narrator._poll_capture_closed(clock[0])
    assert starts == [game.id] and stops == [1, 1, 1]
    assert all(kind != "error" for _message, kind in toasts)
    # Unrelated errors are not taken over.
    assert controller_narrator._retry_after_capture_closed(_Event("Screen capture stopped"), game) is False


def test_ended_needs_two_fresh_reads_and_never_stops_a_manual_session(narrator) -> None:
    _controller, controller_narrator, pipeline, _c, activity, game, save = narrator
    save()
    key = controller_narrator._game_key(game)
    clock = [100.0]
    controller_narrator._clock = pipeline._clock = lambda: clock[0]
    activity.active = True
    activity.commands_generation = 1
    controller_narrator.poll()
    assert controller_narrator.start(game.id) and pipeline.active   # manual start
    activity.active = False
    for _ in range(3):                             # same process list: one read
        clock[0] += 1.0
        controller_narrator.poll()
    assert key in controller_narrator._seen_since and pipeline.active
    activity.commands_generation = 2               # second fresh negative list
    clock[0] += 4.0
    controller_narrator.poll()
    assert key not in controller_narrator._seen_since   # confirmed "ended"
    assert pipeline.active                         # the manual session keeps running


def test_frame_watchdog_restarts_once_silently(narrator) -> None:
    controller, controller_narrator, pipeline, capture, activity, game, save = narrator
    save()
    clock = [100.0]
    controller_narrator._clock = pipeline._clock = lambda: clock[0]
    activity.active = True
    starts: list[bool] = []
    real_start = controller_narrator.start

    def start(game_id, automatic=False):
        starts.append(automatic)
        return real_start(game_id, automatic=automatic)

    controller_narrator.start = start
    toasts: list[str] = []
    controller._emit_toast = lambda message, kind: toasts.append(kind)
    capture.frames_received = 0
    assert controller_narrator.start(game.id, automatic=True)
    pipeline._snapshot = replace(pipeline._snapshot, capture_state="active")
    controller_narrator.poll()                     # armed
    clock[0] += 9.0
    controller_narrator.poll()
    assert starts == [True]                        # not before 10 s
    clock[0] += 2.0
    controller_narrator.poll()
    assert starts == [True, True] and pipeline.active   # one silent restart
    pipeline._snapshot = replace(pipeline._snapshot, capture_state="active")
    controller_narrator.poll()
    clock[0] += 11.0
    controller_narrator.poll()
    assert starts == [True, True]                  # never a second restart
    assert "error" not in toasts


def test_gamepad_real_select_y_sequence_fires_shortcut(monkeypatch) -> None:
    """back down, ~0.5 s, north down, north up < 100 ms, back up."""

    from game_optimization_linux.models import GamepadDevice, GamepadEvent, GamepadType
    from game_optimization_linux.providers import FakeGamepadProvider
    from game_optimization_linux.services import GamepadService
    import game_optimization_linux.services.gamepad as gamepad_module

    provider = FakeGamepadProvider((GamepadDevice(0, "Pad", GamepadType.XBOX),))
    service = GamepadService(provider)
    service.start()
    now = [200.0]
    monkeypatch.setattr(gamepad_module.time, "monotonic", lambda: now[0])
    shortcuts, actions = [], []
    service.shortcutTriggered.connect(shortcuts.append)
    service.actionTriggered.connect(actions.append)

    def step(control: str, pressed: bool, dt: float) -> None:
        now[0] += dt
        provider.emit(GamepadEvent("button", 0, control, pressed, 1.0 if pressed else 0.0, now[0]))
        service.pollNow()

    step("back", True, 0.0)
    step("north", True, 0.5)
    step("north", False, 0.08)
    step("back", False, 0.3)
    service.stop()
    assert shortcuts == ["narrator_toggle"]
    assert not {"MoreActions", "ContextAction1"} & set(actions)


def test_unknown_process_list_does_not_end_a_watched_game(narrator) -> None:
    controller, controller_narrator, _p, _c, activity, game, save = narrator
    save()
    clock = [100.0]
    controller_narrator._clock = lambda: clock[0]
    activity.active = True
    controller_narrator.poll()                     # game seen running
    key = controller_narrator._game_key(game)
    assert key in controller_narrator._seen_since
    activity.active = None                          # transient ps failure/timeout
    clock[0] += 1.0
    controller_narrator.poll()
    # Unknown must not be treated as "ended": the watch state survives.
    assert key in controller_narrator._seen_since


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
