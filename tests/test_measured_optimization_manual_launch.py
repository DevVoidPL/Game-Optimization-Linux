"""Custom/Manual measurement through the real AppController and Manual launcher.

Disposable executables in tmp_path stand in for the game, the Wine/Proton
runner and GameMode. The "game" writes a MangoHud-format CSV into the
output folder of the session config (like MangoHud would) and reports the
environment it received. Requires a systemd user manager for the scope.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest

from game_optimization_linux.controllers.app_controller import AppController
from game_optimization_linux.providers import DemoGameProvider, DemoSystemProvider
from game_optimization_linux.providers.local import ConfiguredGameProvider
from game_optimization_linux.services import ManualGameStore, MockTaskService, SettingsStore
from game_optimization_linux.services.custom_launch import CustomLaunchProfile, CustomLaunchRepository, launch_key
from game_optimization_linux.services.measured_optimization.manual import optimization_key
from game_optimization_linux.services.optimization_runtime import RuntimeToolAvailability
from tests.test_manual_games import _provider

pytestmark = pytest.mark.skipif(
    not shutil.which("systemd-run") or subprocess.run(["systemctl", "--user", "is-system-running"],
                                                      capture_output=True).returncode not in (0, 1),
    reason="needs a systemd user manager")

PLAY = r'''
import json, os, re, subprocess, sys, time
report = os.environ["REPORT_DIR"]
info = {name: os.environ.get(name) for name in ("MANGOHUD", "MANGOHUD_CONFIGFILE", "USER_VAR", "CUSTOM_VAR", "WRAPPED", "GAMEMODE_FAKE")}
info["argv"] = sys.argv
json.dump(info, open(os.path.join(report, "game.json"), "w"))
config = os.environ.get("MANGOHUD_CONFIGFILE")
if config:
    folder = re.search(r"^output_folder=(.*)$", open(config).read(), re.M).group(1)
    override = os.path.join(report, "frametime.txt")
    frametime = float(open(override).read()) if os.path.exists(override) else float(os.environ.get("FRAMETIME", "10"))
    rows = ["os,cpu,gpu,ram,kernel,driver,cpuscheduler", "Linux,CPU,GPU,32,6,Mesa,",
            "fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,swap_used,process_rss,cpu_mhz,elapsed"]
    rows += [f"{1000/frametime:.2f},{frametime},40,30,95,60,70,2500,1200,6.5,150,12,0,3,4200,{i*100000000}" for i in range(1300)]
    open(os.path.join(folder, "Game_2026-10-27_12-00-00.csv"), "w").write("\n".join(rows) + "\n")
mode = os.environ.get("PLAY_MODE", "")
if mode == "detach":  # the game re-starts itself detached and the first process exits
    subprocess.Popen([os.environ["GAME_EXE"], "-c", "import time; time.sleep(5)"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
elif mode == "handoff":
    subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", "--", sys.executable.replace("python3", "python3"),
                      "-c", "import time; time.sleep(9)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(float(os.environ.get("PLAY_SECONDS", "7")))
'''
HELPER = '#!/bin/sh\npython3 -c "import json,os,sys; json.dump({k: os.environ.get(k) for k in (\'MANGOHUD\', \'MANGOHUD_CONFIGFILE\')}, open(sys.argv[1], \'w\'))" "$REPORT_DIR/$1.json"\n'


def script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "Game Dir"
    root.mkdir()
    reports = tmp_path / "reports"
    reports.mkdir()
    play = script(root / "play.py", PLAY)
    store = ManualGameStore(tmp_path / "config" / "manual.json")
    provider = ConfiguredGameProvider(DemoGameProvider(()), _provider(tmp_path, store))
    controller = AppController(game_provider=provider, manual_game_store=store, task_service=MockTaskService(),
                               settings_store=SettingsStore(tmp_path / "settings.json"),
                               system_provider=DemoSystemProvider(), initial_games=(), demo_mode=False,
                               auto_refresh=False)
    gamemode = script(tmp_path / "bin" / "gamemoderun", '#!/bin/sh\nGAMEMODE_FAKE=1 exec "$@"\n')
    controller._runtime_tool_detector.detect = lambda: (
        RuntimeToolAvailability("GameMode", True, str(gamemode)), RuntimeToolAvailability("Gamescope", False))
    controller._mangohud_detector.detect = lambda _kind: type("A", (), {"available": True, "message": ""})()
    yield {"tmp": tmp_path, "root": root, "reports": reports, "play": play, "controller": controller}
    controller.shutdown()


def add_game(world, *, kind="native", play_mode="", seconds="7", frametime="10") -> tuple:
    root, controller = world["root"], world["controller"]
    environment = [{"key": "REPORT_DIR", "value": str(world["reports"])}, {"key": "USER_VAR", "value": "user value"},
                   {"key": "PLAY_MODE", "value": play_mode}, {"key": "PLAY_SECONDS", "value": seconds},
                   {"key": "FRAMETIME", "value": frametime}, {"key": "PLAY", "value": str(world["play"])},
                   {"key": "GAME_EXE", "value": str(root / "game")}]
    pre = script(root / "pre.sh", HELPER)
    post = script(root / "post.sh", HELPER)
    values = {"name": "Disposable", "installDirectory": str(root), "environment": environment,
              "preLaunchCommand": shlex.join([str(pre), "pre"]), "postLaunchCommand": shlex.join([str(post), "post"])}
    if kind == "native":
        executable = root / "game"
        if not executable.exists():
            executable.symlink_to(shutil.which("python3"))
        values.update(executable=str(executable), arguments=shlex.quote(str(world["play"])))
    else:
        executable = root / "Game.exe"
        executable.write_bytes(b"MZ" + b"\0" * 62)
        body = '#!/bin/bash\n[ "$1" = run ] && shift\nexe="$1"; shift\nexec -a "Z:${exe//\\//\\\\}" python3 "$PLAY" "$@"\n'
        if kind == "wine":
            runner = script(world["tmp"] / "bin" / "wine", body)
            values["runnerCommand"] = str(runner)
        else:
            runner = script(world["tmp"] / "GE-Proton-Test" / "proton", body + "# PROTON_NO_NTSYNC\n")
            (runner.parent / "version").write_text("1 GE-Proton-Test\n")
            values["runnerCommand"] = shlex.join([str(runner), "run"])
        values.update(executable=str(executable))
    created = controller.saveManualGame(values)
    assert created["success"], created
    game = controller._resolve_game(created["id"], show_error=False)
    return game, optimization_key(game)


def poll(controller, game, key, *, until=("processing", "failed"), timeout=40.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller._poll_manual_launch_jobs()
        controller._optimization_controller._poll_baseline_sessions()
        if "completed" in until:
            controller._optimization_controller._poll_analysis_jobs()
        session = controller._baseline_sessions.load(key)
        if session.status in until and game.id not in controller._manual_launch_jobs:
            return session
        time.sleep(0.3)
    raise AssertionError(f"session stuck in {controller._baseline_sessions.load(key).status}")


def reported(world, name):
    return json.loads((world["reports"] / f"{name}.json").read_text())


@pytest.mark.parametrize("kind", ["native", "wine", "proton"])
def test_manual_recording_isolates_measurement_and_keeps_user_launch(world, kind) -> None:
    controller = world["controller"]
    game, key = add_game(world, kind=kind)
    custom_key = launch_key(game)
    wrapper = script(world["tmp"] / "bin" / "mywrapper", '#!/bin/sh\nWRAPPED=1 exec "$@"\n')
    CustomLaunchRepository(controller._post_processing_service.root).save(CustomLaunchProfile.from_dict(custom_key, {
        "environment": [{"key": "CUSTOM_VAR", "value": "custom value"}],
        "arguments": [{"value": "--custom-arg"}], "wrapper": {"argv": [str(wrapper)]}}))
    result = controller._optimization_controller.recordOptimizationBaseline(game.id)
    assert result["success"], result
    session = poll(controller, game, key)
    assert session.status == "processing", session.error
    assert session.launcher == "Manual" and session.started_at is not None  # the game process was observed
    assert list(Path(session.log_directory).glob("*.csv"))
    played = reported(world, "game")
    assert played["MANGOHUD"] == "1" and played["MANGOHUD_CONFIGFILE"] == str(session.config_path)
    assert (played["USER_VAR"], played["CUSTOM_VAR"], played["WRAPPED"]) == ("user value", "custom value", "1")
    assert played["argv"][-1] == "--custom-arg"
    for helper in ("pre", "post"):
        assert reported(world, helper) == {"MANGOHUD": None, "MANGOHUD_CONFIGFILE": None}
    report = json.loads(controller._runner_report_path(key).read_text())
    assert report["baselineSessionId"] == session.id and report["transport"] == "manual"
    assert key.startswith("local-") and not (controller._optimization_profile_repository.path(key)).exists()


def test_detached_child_keeps_the_session_until_the_tree_ends(world) -> None:
    controller = world["controller"]
    game, key = add_game(world, play_mode="detach", seconds="1")
    started = time.monotonic()
    assert controller._optimization_controller.recordOptimizationBaseline(game.id)["success"]
    session = poll(controller, game, key)
    # The first process exits after 1 s; the detached 5 s game process keeps the tree alive.
    assert session.status == "processing", session.error
    assert time.monotonic() - started >= 4.8


def test_process_leaving_the_tracked_tree_is_not_a_valid_session(world) -> None:
    controller = world["controller"]
    game, key = add_game(world, play_mode="handoff", seconds="6")
    # The handed-off process must look like the game itself.
    play = world["play"].read_text().replace('sys.executable.replace("python3", "python3"),',
                                              f'"{world["root"] / "game"}",')
    world["play"].write_text(play)
    assert controller._optimization_controller.recordOptimizationBaseline(game.id)["success"]
    session = poll(controller, game, key, timeout=30)
    assert session.status == "failed" and "left the tracked process tree" in session.error
    subprocess.run(["pkill", "-f", "[i]mport time; time.sleep(9)"], check=False)


def test_missing_runner_and_unsupported_entries_are_refused(world) -> None:
    controller = world["controller"]
    root = world["root"]
    (root / "Game.exe").write_bytes(b"MZ" + b"\0" * 62)
    created = controller.saveManualGame({"name": "No runner", "executable": str(root / "Game.exe"),
                                         "installDirectory": str(root)})
    game = controller._resolve_game(created["id"], show_error=False)
    refused = controller._optimization_controller.recordOptimizationBaseline(game.id)
    assert refused["success"] is False and refused["code"] == "manual_windows_no_runner"
    view = controller.getMeasuredOptimization(game.id)
    assert view["support"]["supported"] is False and "Wine or Proton" in view["support"]["reason"]
    assert controller.startMeasuredOptimization(game.id, "balanced", {})["success"] is False


def test_full_manual_workflow_candidate_comparison_and_exact_revert(world) -> None:
    controller = world["controller"]
    game, key = add_game(world, kind="proton")
    measured = controller._measured_optimization
    view = controller.getMeasuredOptimization(game.id)
    assert view["support"]["supported"] and view["manual"]["runner"] == "manual_proton"
    assert view["fingerprint"]["runtime"]["name"] == "GE-Proton-Test"
    assert "manual-launch" in view["fingerprint"]["config"]["files"]
    assert controller.startMeasuredOptimization(game.id, "maximum_performance", {})["success"]
    assert controller.recordMeasuredBaseline(game.id)["success"]
    session = poll(controller, game, key, until=("completed", "failed", "recorded_unrepresentative"))
    assert session.status == "completed", session.error
    assert measured.store.workflow(key)["state"] == "baseline_ready", measured.store.workflow(key)["error"]
    profile_path = controller._optimization_profile_repository.path(key)
    before = profile_path.read_bytes() if profile_path.exists() else None
    proposal = controller.getMeasuredOptimization(game.id)["proposal"]
    assert proposal["id"] == "gamemode"
    assert controller.applyMeasuredCandidate(game.id, "gamemode")["success"]
    # The faster candidate run: 9 ms frame time instead of 10 ms.
    environment = [item for item in controller.manualGameConfig(game.id)["environment"] if item["key"] != "FRAMETIME"]
    controller.saveManualGame({**controller.manualGameConfig(game.id), "environment": environment + [
        {"key": "FRAMETIME", "value": "9"}]})
    game = controller._resolve_game(game.id, show_error=False)
    started = controller.recordMeasuredCandidate(game.id)
    assert started["success"], started
    session = poll(controller, game, key, until=("completed", "failed", "recorded_unrepresentative"))
    assert reported(world, "game")["GAMEMODE_FAKE"] == "1"
    assert session.status == "completed", session.error
    workflow = measured.store.workflow(key)
    assert workflow["state"] == "awaiting_confirmation" and workflow["comparison"]["activation"]["verified"]
    # The Custom launch configuration changed between the sessions: not one variable.
    check = controller.getMeasuredOptimization(game.id)["comparabilityCheck"]
    assert "more_than_one_variable" in check["issues"]
    result = controller.confirmMeasuredComparison(game.id, "controlled", {"unverifiedConfirmed": True})["result"]
    assert result["decision"] == "inconclusive" and result["comparability"] == "not_comparable"
    # External edit after Apply blocks a destructive restore.
    saved = profile_path.read_bytes()
    profile_path.write_bytes(saved.replace(b'"target_fps": 60', b'"target_fps": 61'))
    refused = controller.revertMeasuredResult(game.id)
    assert refused["success"] is False and "changed after the candidate was applied" in refused["error"]
    profile_path.write_bytes(saved)
    assert controller.revertMeasuredResult(game.id)["success"]
    assert (profile_path.read_bytes() if profile_path.exists() else None) == before
    assert controller._optimization_change_service.active_change(game) is None
    history = measured.store.load(key)["history"]
    assert history[-1]["decision"] == "inconclusive" and history[-1]["scope"]["key"] == key


def test_controlled_manual_comparison_keeps_candidate_and_user_files(world) -> None:
    controller = world["controller"]
    game, key = add_game(world, kind="wine")
    custom = CustomLaunchRepository(controller._post_processing_service.root)
    custom.save(CustomLaunchProfile.from_dict(launch_key(game), {"environment": [{"key": "CUSTOM_VAR", "value": "x"}]}))
    custom_bytes = custom.read_bytes(launch_key(game))
    manual_bytes = controller._manual_game_store.path.read_bytes() if hasattr(controller, "_manual_game_store") else None
    measured = controller._measured_optimization
    assert controller.startMeasuredOptimization(game.id, "maximum_performance", {})["success"]
    assert controller.recordMeasuredBaseline(game.id)["success"]
    assert poll(controller, game, key, until=("completed", "failed", "recorded_unrepresentative")).status == "completed"
    assert controller.applyMeasuredCandidate(game.id, "gamemode")["success"]
    (world["reports"] / "frametime.txt").write_text("9")  # the candidate run is faster; no config change
    assert controller.recordMeasuredCandidate(game.id)["success"]
    assert poll(controller, game, key, until=("completed", "failed", "recorded_unrepresentative")).status == "completed"
    check = controller.getMeasuredOptimization(game.id)["comparabilityCheck"]
    assert check["issues"] == [] and check["violations"] == []
    result = controller.confirmMeasuredComparison(game.id, "controlled", {"unverifiedConfirmed": True})["result"]
    assert result["decision"] == "keep" and result["verified"], result["reasons"]
    fps = next(row for row in result["metrics"] if row["metric"] == "averageFps")
    assert fps["before"] == pytest.approx(100, abs=0.5) and fps["after"] == pytest.approx(111.1, abs=0.5)
    assert controller.keepMeasuredResult(game.id)["success"]
    assert custom.read_bytes(launch_key(game)) == custom_bytes  # user-owned, never rewritten
    if manual_bytes is not None:
        assert controller._manual_game_store.path.read_bytes() == manual_bytes
    assert measured.accepted_for(key, "maximum_performance") == {"gamemode_enabled": True}
    # The next normal launch of this Custom game uses the kept GameMode setting.
    configuration = controller._game_provider.manual_config(game.id)
    plan = controller._manual_launch_plan(game, configuration)
    assert any(part.endswith("/gamemoderun") for part in plan.game_command)
    assert "MANGOHUD" not in dict(plan.game_environment) and dict(plan.environment)["CUSTOM_VAR"] == "x"
