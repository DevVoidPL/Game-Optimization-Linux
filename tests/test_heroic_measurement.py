"""Baseline recording for Heroic games (Hogwarts Legacy regression).

The Steam runner never sees Heroic launches. GameOpti writes the private
MangoHud logging variables into the game's Heroic settings, starts Heroic,
observes the real game process and removes the variables when the game has
ended. No real Heroic, Flatpak or game is used; HOME is temporary.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import time

import pytest

from game_optimization_linux.services import BaselineSessionRepository
from game_optimization_linux.services.narrator_activity import NarratorGameActivityDetector
from test_game_analysis import _mangohud_log
from test_heroic_optiscaler import APP_NAME, _controller, _flatpak_heroic
from test_heroic_tool_plans import _Host, _MangoDetector, _Tools


class _Processes:
    """Host process list as seen by ``ps``; edited by the test."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self):
        return tuple(self.lines)


class _Launcher:
    def __init__(self, fail: bool = False) -> None:
        self.launched: list[str] = []
        self.fail = fail

    def launch(self, game):
        if self.fail:
            raise RuntimeError("Could not start Heroic: test")
        self.launched.append(game.id)
        return ["flatpak", "run", "com.heroicgameslauncher.hgl", "--no-gui", "heroic://launch"]


@pytest.fixture
def measured(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    made = []

    def make(*, launcher_fails: bool = False):
        game = _flatpak_heroic(tmp_path)
        controller = _controller(tmp_path, game)
        made.append(controller)
        controller._host_service = _Host()
        controller._runtime_tool_detector = _Tools()
        controller._mangohud_detector = _MangoDetector()
        controller._baseline_sessions = BaselineSessionRepository(tmp_path / "sessions")
        controller._launcher_native = _Launcher(fail=launcher_fails)
        processes = _Processes()
        optimization = controller._optimization_controller
        optimization._launcher_activity = NarratorGameActivityDetector(
            lambda key: game if key == game.id else None,
            host_processes=processes, sandboxed=True, min_interval=0.0,
        )
        optimization.LAUNCHER_PROCESS_CHECK_SECONDS = 0.0
        # The Heroic sandbox probe (MangoHud in the launcher) answers first.
        deadline = time.monotonic() + 5.0
        while (
            optimization._launcher_tool_environments(game)["mangohud"]["launcher"] is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        config = (tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig"
                  / f"{APP_NAME}.json")
        return game, controller, processes, config

    yield make
    for controller in made:
        controller.shutdown()


def _env(config: Path) -> dict[str, str]:
    section = json.loads(config.read_text())[APP_NAME]
    return {item["key"]: item["value"] for item in section["enviromentOptions"]}


def _game_line(game) -> str:
    """Wine/Proton process line of the real game executable."""
    exe = game.install_path / "SpellProject/Binaries/Win64/SpellGame.exe"
    return "Z:" + str(exe).replace("/", "\\")


def test_record_baseline_starts_heroic_and_writes_only_session_variables(measured, tmp_path) -> None:
    game, controller, _processes, config = measured()

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is True, result
    assert result["status"] == "waiting_for_launcher"
    assert controller._launcher_native.launched == [game.id]
    session = controller._baseline_sessions.load(game.profile_key)
    env = _env(config)
    assert env["MANGOHUD"] == "1"
    assert Path(env["MANGOHUD_CONFIGFILE"]) == session.config_path
    # Written where the Heroic Flatpak may write (~/Games/Heroic:create).
    root = tmp_path / "home/Games/Heroic/.gameopti-measurements"
    assert root in session.config_path.parents and root in session.log_directory.parents
    assert f"output_folder={session.log_directory}" in session.config_path.read_text()
    # The session record itself stays in GameOpti's repository.
    assert session.directory.parent.parent == tmp_path / "sessions"
    assert "SteamAppId" not in env and "Steam AppID" not in json.dumps(result)


def test_game_lifecycle_feeds_the_real_log_to_analysis_and_cleans_up(measured) -> None:
    game, controller, processes, config = measured()
    original_env = _env(config)
    controller.recordOptimizationBaseline(game.id)
    session = controller._baseline_sessions.load(game.profile_key)
    optimization = controller._optimization_controller

    optimization._poll_baseline_sessions()
    assert controller._baseline_sessions.load(game.profile_key).status == "waiting_for_launcher"

    processes.lines = ["/app/bin/heroic/heroic --no-gui", _game_line(game)]
    optimization._poll_baseline_sessions()
    assert controller._baseline_sessions.load(game.profile_key).status == "recording"
    assert "MANGOHUD_CONFIGFILE" in _env(config)  # kept while the game runs

    _mangohud_log(session.log_directory / "SpellGame_2026-10-21_12-00-00.csv")
    processes.lines = []
    analysed: list[str] = []
    optimization.analyzeGameOptimization = (
        lambda game_id, log: analysed.append(log) or {"success": True}
    )
    for _ in range(optimization.LAUNCHER_EXIT_CONFIRMATIONS):
        optimization._poll_baseline_sessions()

    assert controller._baseline_sessions.load(game.profile_key).status == "processing"
    assert analysed and Path(analysed[0]).parent == session.log_directory
    # Heroic has closed: the measurement variables go, everything else stays.
    optimization._launcher_cleanup_at = 0.0
    optimization._cleanup_launcher_measurements()
    assert _env(config) == original_env
    assert not controller._launcher_integration.measurement_pending(game)


def test_game_exit_without_log_fails_with_reason_not_fake_data(measured) -> None:
    game, controller, processes, _config = measured()
    controller.recordOptimizationBaseline(game.id)
    optimization = controller._optimization_controller
    processes.lines = [_game_line(game)]
    optimization._poll_baseline_sessions()
    processes.lines = []
    for _ in range(optimization.LAUNCHER_EXIT_CONFIRMATIONS):
        optimization._poll_baseline_sessions()

    current = controller._baseline_sessions.load(game.profile_key)
    assert current.status == "failed"
    assert "MangoHud did not write a log" in current.error
    assert controller._baseline_sessions.load_measurement(game.profile_key, slot="before") is None


def test_short_process_gap_does_not_end_the_recording(measured) -> None:
    game, controller, processes, _config = measured()
    controller.recordOptimizationBaseline(game.id)
    optimization = controller._optimization_controller
    processes.lines = [_game_line(game)]
    optimization._poll_baseline_sessions()
    processes.lines = []
    optimization._poll_baseline_sessions()  # one miss (e.g. a ps hiccup)
    processes.lines = [_game_line(game)]
    optimization._poll_baseline_sessions()
    assert controller._baseline_sessions.load(game.profile_key).status == "recording"



def test_cancel_removes_the_session_variables(measured) -> None:
    game, controller, _processes, config = measured()
    original_env = _env(config)
    controller.recordOptimizationBaseline(game.id)

    result = controller.cancelOptimizationMeasurement(game.id)

    assert result["success"] is True
    current = controller._baseline_sessions.load(game.profile_key)
    assert current.status == "failed" and current.error == "Recording cancelled"
    assert _env(config) == original_env
    assert controller.cancelOptimizationMeasurement(game.id)["success"] is False


def test_game_that_never_starts_times_out_and_cleans_up(measured) -> None:
    game, controller, _processes, config = measured()
    original_env = _env(config)
    controller.recordOptimizationBaseline(game.id)
    sessions = controller._baseline_sessions
    session = sessions.load(game.profile_key)
    sessions._save(replace(session, created_at=datetime.now(UTC) - timedelta(seconds=301)))
    optimization = controller._optimization_controller

    optimization._poll_baseline_sessions()
    optimization._launcher_cleanup_at = 0.0
    optimization._cleanup_launcher_measurements()

    current = sessions.load(game.profile_key)
    assert current.status == "failed" and "did not start within 300 seconds" in current.error
    assert _env(config) == original_env


def test_launcher_start_failure_leaves_heroic_settings_unchanged(measured) -> None:
    game, controller, _processes, config = measured(launcher_fails=True)
    before = json.loads(config.read_text())

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is False and result["code"] == "session_start_failed"
    assert json.loads(config.read_text()) == before
    assert controller._baseline_sessions.load(game.profile_key).status == "failed"
    assert not controller._launcher_integration.measurement_pending(game)


def test_running_heroic_is_not_overwritten(measured) -> None:
    game, controller, _processes, config = measured()
    before = config.read_text()
    controller._launcher_integration._process_commands = (
        lambda: ("/app/bin/heroic/heroic --no-sandbox",)
    )

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is False and result["code"] == "launcher_running"
    assert config.read_text() == before
    assert controller._launcher_native.launched == []


def test_steam_runner_never_claims_a_heroic_session(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    key = "heroic-legendary-x"
    sessions.create(key, key, launcher="Heroic", output_root=tmp_path / "out")
    claimed, reason = sessions.claim_with_reason(key)
    assert claimed is None and "Heroic" in reason
    assert sessions.load(key).status == "waiting_for_launcher"
    # No runner heartbeat: a launcher session is not dropped as abandoned.
    sessions.recover_abandoned(now=datetime.now(UTC) + timedelta(hours=1))
    assert sessions.load(key).status == "waiting_for_launcher"


def test_steam_session_keeps_its_private_folder(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    session = sessions.create("440", "steam-440")
    assert session.status == "waiting_for_steam" and session.launcher == ""
    assert session.log_directory == session.directory / "logs"
    with pytest.raises(ValueError):
        sessions.create("441", "steam-441", output_root=tmp_path / "out")
