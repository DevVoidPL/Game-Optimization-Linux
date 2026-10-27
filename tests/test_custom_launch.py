"""Per-game custom launch: environment, arguments, wrapper, before-launch/after-exit.

Everything runs in temporary directories; real processes are disposable
recorders. No real Steam library, Heroic or Lutris configuration is touched.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest
import yaml

from game_optimization_linux.models import FilesystemType, Game, GameOptimizationProfile, Launcher
from game_optimization_linux.services.custom_launch import (
    CustomLaunchError,
    CustomLaunchProfile,
    CustomLaunchRepository,
    field_errors,
    launch_key,
    redact,
)
from game_optimization_linux.services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolAvailability

SRC = Path(__file__).resolve().parents[1] / "src/game_optimization_linux"
GAMEMODE = RuntimeToolAvailability("gamemode", True, "/usr/bin/gamemoderun")
NO_GAMEMODE = RuntimeToolAvailability("gamemode", False)
GAMESCOPE = RuntimeToolAvailability("gamescope", True, "/usr/bin/gamescope",
                                    supported_options=("-W", "-H", "-w", "-h", "-r", "-f", "-b", "-F", "-S"))
NO_GAMESCOPE = RuntimeToolAvailability("gamescope", False)
TRICKY = ["a b", "it's", 'say "hi"', "$HOME", "`id`", "; rm -rf /", "|cat", ">out", "*", "zażółć ✓", ""]


_CONTROLLERS: list = []


@pytest.fixture(autouse=True)
def _shutdown_controllers():
    yield
    while _CONTROLLERS:
        _CONTROLLERS.pop().shutdown()


def _track(controller):
    _CONTROLLERS.append(controller)
    return controller


def profile(**values) -> CustomLaunchProfile:
    return CustomLaunchProfile.from_dict("42", values)


def recorder(tmp_path: Path, name: str = "rec", exit_variable: str = "REC_EXIT") -> Path:
    path = tmp_path / name
    path.write_text("#!/usr/bin/env python3\nimport json,os,sys,time\n"
                    "open(os.environ['REC_LOG'],'a').write(json.dumps({'t':time.time(),'argv':sys.argv[1:],"
                    "'env':{k:v for k,v in os.environ.items() if k.startswith('CL_')}})+'\\n')\n"
                    f"sys.exit(int(os.environ.get('{exit_variable}','0')))\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def records(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# -- model ----------------------------------------------------------------------

def test_round_trip_keeps_boundaries_enabled_flags_and_unicode() -> None:
    value = profile(environment=[{"key": "CL_A", "value": "x y"}, {"key": "CL_B", "value": "2", "enabled": False}],
                    arguments=[{"value": item} for item in TRICKY] + [{"value": "-off", "enabled": False}],
                    wrapper={"argv": ["/opt/w x/run", "--flag", "a b"]},
                    preLaunch=[{"argv": ["/bin/true", "ż"], "timeout": 5, "onFailure": "continue"}],
                    postExit=[{"argv": ["/bin/true"], "timeout": 600, "runIfNotStarted": True}])
    again = CustomLaunchProfile.from_dict("42", json.loads(json.dumps(value.to_dict())))
    assert again == value
    assert value.active_environment() == {"CL_A": "x y"}
    assert value.active_arguments() == tuple(TRICKY)
    assert value.active_wrapper() == ("/opt/w x/run", "--flag", "a b")
    assert value.active_pre()[0].on_failure == "continue" and value.active_post()[0].run_if_not_started


@pytest.mark.parametrize("payload, message", [
    ({"environment": [{"key": "1BAD", "value": "x"}]}, "Invalid environment variable name"),
    ({"environment": [{"key": "A-B", "value": "x"}]}, "Invalid environment variable name"),
    ({"environment": [{"key": "OK", "value": "a\x00b"}]}, "NUL"),
    ({"environment": [{"key": "A", "value": "1"}, {"key": "A", "value": "2"}]}, "listed twice"),
    ({"arguments": [{"value": "a\x00"}]}, "NUL"),
    ({"arguments": "-a -b"}, "Invalid arguments list"),
    ({"preLaunch": [{"argv": "echo hi"}]}, "list of separate arguments"),
    ({"preLaunch": [{"argv": ["sudo", "x"]}]}, "privileged"),
    ({"preLaunch": [{"argv": ["/usr/bin/pkexec", "x"]}]}, "privileged"),
    ({"postExit": [{"argv": ["bash", "-c", "x"]}]}, "shell interpreters"),
    ({"wrapper": {"argv": ["/bin/sh", "-c", "x"]}}, "shell interpreters"),
    ({"preLaunch": [{"argv": ["./relative"]}]}, "absolute path"),
    ({"preLaunch": [{"argv": ["/bin/true"], "timeout": 0}]}, "timeout"),
    ({"preLaunch": [{"argv": ["/bin/true"], "timeout": 601}]}, "timeout"),
    ({"preLaunch": [{"argv": ["/bin/true"], "onFailure": "retry"}]}, "failure behaviour"),
])
def test_invalid_entries_are_rejected(payload, message) -> None:
    with pytest.raises(CustomLaunchError, match=message):
        CustomLaunchProfile.from_dict("42", payload)


def test_field_errors_point_at_the_row() -> None:
    errors = field_errors({"environment": [{"key": "GOOD", "value": "1"}, {"key": "bad key", "value": ""}],
                           "preLaunch": [{"argv": ["/bin/true"]}, {"argv": ["sudo"]}]})
    assert [(e["section"], e["index"]) for e in errors] == [("environment", 1), ("preLaunch", 1)]


def test_secret_values_are_redacted() -> None:
    for key in ("API_KEY", "MY_TOKEN", "DB_PASSWORD", "CLIENT_SECRET"):
        assert redact(key, "v") == "<redacted>"
    assert redact("DXVK_HUD", "fps") == "fps"


def test_repository_is_private_atomic_and_refuses_symlinks(tmp_path: Path) -> None:
    repo = CustomLaunchRepository(tmp_path)
    assert repo.load("42").empty and repo.read_bytes("42") is None
    saved = repo.save(profile(arguments=[{"value": "-x"}]))
    path = repo.path("42")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and saved.updated_at
    before = repo.read_bytes("42")
    repo.save(profile())
    repo.restore_bytes("42", before)
    assert path.read_bytes() == before
    repo.restore_bytes("42", None)
    assert not path.exists()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(CustomLaunchError):
        repo.load("42")


def _game(launcher: Launcher, game_id: str, **extra) -> Game:
    return Game(id=game_id, name="T", launcher=launcher, install_path=Path("/games/t"), logical_size_gb=1.0,
                physical_size_gb=1.0, filesystem=FilesystemType.EXT4, compression_available=False, **extra)


def test_profile_keys_keep_launchers_and_manual_games_separate() -> None:
    steam = _game(Launcher.STEAM, "steam-1", steam_app_id="1", launcher_game_id="1")
    heroic = _game(Launcher.HEROIC, "heroic-legendary-abc", launcher_game_id="abc")
    lutris = _game(Launcher.LUTRIS, "lutris-7", launcher_game_id="7")
    manual_a = _game(Launcher.MANUAL, "manual-11111111-1111-4111-8111-111111111111", data_source="manual")
    manual_b = _game(Launcher.MANUAL, "manual-22222222-2222-4222-8222-222222222222", data_source="manual")
    keys = [launch_key(g) for g in (steam, heroic, lutris, manual_a, manual_b)]
    assert len(set(keys)) == 5 and keys[1] == "heroic-legendary-abc" and keys[3].startswith("local-")
    from game_optimization_linux.services.gpu_memory import game_key
    assert keys[3] != game_key(manual_a)  # never shares the GPU-memory identity


# -- planner --------------------------------------------------------------------

def build(custom, *, gamemode=NO_GAMEMODE, gamescope=False, steam=("/usr/bin/env", "-0"), **kwargs):
    base = replace(GameOptimizationProfile.default("42"), gamemode_enabled=gamemode.available,
                   gamescope_enabled=gamescope, gamescope_mode="native" if gamescope else "disabled")
    return OptimizationLaunchPlanner().build(base, list(steam), gamemode=gamemode,
                                             gamescope=GAMESCOPE if gamescope else NO_GAMESCOPE,
                                             custom_launch=custom, **kwargs)


def test_planner_appends_arguments_and_orders_wrappers() -> None:
    custom = profile(arguments=[{"value": v} for v in TRICKY], wrapper={"argv": ["/opt/wrap", "--w"]},
                     environment=[{"key": "CL_X", "value": "1"}])
    plan = build(custom, gamemode=GAMEMODE, gamescope=True, steam=("reaper", "SteamLaunch", "--", "game"))
    command = list(plan.command)
    assert command[-len(TRICKY):] == TRICKY
    assert command.index("/usr/bin/gamescope") < command.index("/usr/bin/gamemoderun") < command.index("/opt/wrap")
    assert command.index("/opt/wrap") < command.index("reaper")
    assert plan.environment["CL_X"] == "1" and plan.environment_sources["CL_X"] == "custom_launch"
    assert "custom" in plan.wrappers


def test_disabled_entries_change_nothing() -> None:
    custom = profile(environment=[{"key": "CL_X", "value": "1", "enabled": False}],
                     arguments=[{"value": "-x", "enabled": False}],
                     wrapper={"argv": ["/opt/wrap"], "enabled": False})
    assert build(custom).command == build(None).command
    assert "CL_X" not in build(custom).environment


def test_runtime_and_managed_keys_are_never_overwritten() -> None:
    custom = profile(environment=[{"key": k, "value": "user"} for k in
                                  ("LD_PRELOAD", "PATH", "WINEDLLOVERRIDES", "VK_LOADER_LAYERS_ENABLE",
                                   "STEAM_COMPAT_DATA_PATH", "PROTON_ENABLE_WAYLAND")])
    plan = build(custom, proton_environment={"PROTON_ENABLE_WAYLAND": "1"})
    for key in ("LD_PRELOAD", "PATH", "WINEDLLOVERRIDES", "VK_LOADER_LAYERS_ENABLE", "STEAM_COMPAT_DATA_PATH"):
        assert plan.environment.get(key) != "user"
        assert f"custom:{key}" in plan.environment_conflicts
    assert plan.environment["PROTON_ENABLE_WAYLAND"] == "1"  # Proton Tweaks keep their value
    assert "custom:PROTON_ENABLE_WAYLAND" in plan.environment_conflicts
    rows = {row["key"]: row for row in plan.environment_classification}
    assert rows["LD_PRELOAD"]["category"] == "unresolved" or any(
        r.get("code") == "custom_refused" for r in plan.environment_classification)


@pytest.mark.parametrize("tool", ["gamescope", "/usr/bin/gamemoderun", "mangohud", "mako-launch"])
def test_managed_wrappers_cannot_be_stacked_as_custom_wrapper(tool) -> None:
    plan = build(profile(wrapper={"argv": [tool, "--"]}))
    assert "custom" not in plan.wrappers and tool not in plan.command
    assert any("wrapper" in w.casefold() for w in plan.warnings)


def test_inherited_unrelated_variables_survive_and_custom_reaches_the_game(tmp_path: Path) -> None:
    inherited = {"PATH": "/usr/bin:/bin", "KEEP_ME": "a b", "CL_SHARED": "old"}
    custom = profile(environment=[{"key": "CL_NEW", "value": "$HOME `x` ;*"}])
    plan = build(custom, existing_environment=inherited)
    result = subprocess.run(list(plan.command), env=plan.process_environment({**inherited, **plan.environment}),
                            capture_output=True, check=True)
    game = dict(item.split("=", 1) for item in result.stdout.decode().split("\0") if item)
    assert game["KEEP_ME"] == "a b" and game["CL_SHARED"] == "old" and game["CL_NEW"] == "$HOME `x` ;*"


def test_custom_env_stays_out_of_the_gamescope_compositor() -> None:
    plan = build(profile(environment=[{"key": "CL_GAME_ONLY", "value": "1"}]), gamescope=True)
    outer = plan.process_environment({"PATH": "/usr/bin", **plan.environment})
    assert "CL_GAME_ONLY" not in outer
    assert "CL_GAME_ONLY=1" in plan.command


# -- Steam / Local host payload (real /bin/sh) -------------------------------------

STEAM = ("steam-launch-wrapper", "--", "reaper", "SteamLaunch", "AppId=42", "--")


def _payload(tmp_path: Path, custom, *, steam=STEAM, game_exit=0, extra=()):
    from game_optimization_linux.runner import _host_launcher_payload
    game = recorder(tmp_path, "game", "GAME_EXIT")
    steam_command = (*steam, str(game), *extra)
    plan = build(custom, steam=steam_command)
    script = tmp_path / "payload.sh"
    script.write_bytes(_host_launcher_payload(plan, plan.environment))
    log = tmp_path / "log.jsonl"
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "REC_LOG": str(log), "GAME_EXIT": str(game_exit)}
    # Steam's reaper/launch wrapper are replaced by `env` so the real game argv runs.
    for name in ("steam-launch-wrapper", "reaper"):
        shim = tmp_path / name
        shim.write_text("#!/bin/sh\nwhile [ \"$1\" != \"--\" ]; do shift; done; shift; exec \"$@\"\n")
        shim.chmod(0o755)
    (tmp_path / "SteamLaunch").write_text("#!/bin/sh\nshift; [ \"$1\" = \"--\" ] && shift; exec \"$@\"\n")
    (tmp_path / "SteamLaunch").chmod(0o755)
    completed = subprocess.run(["/bin/sh", str(script), *steam_command], env=env, capture_output=True, timeout=60)
    return completed, records(log), script.read_text()


def test_steam_payload_runs_exact_argv_and_hooks_in_order(tmp_path: Path) -> None:
    rec = recorder(tmp_path)
    custom = profile(arguments=[{"value": v} for v in TRICKY], environment=[{"key": "CL_E", "value": "é v"}],
                     preLaunch=[{"argv": [str(rec), "pre", "a b", "$HOME|x"], "timeout": 5}],
                     postExit=[{"argv": [str(rec), "post"], "timeout": 5}])
    completed, log, script = _payload(tmp_path, custom, game_exit=3, extra=("-steamarg", "x y"))
    assert completed.returncode == 3, completed.stderr  # the game status is preserved
    assert [r["argv"] for r in log] == [["pre", "a b", "$HOME|x"], ["-steamarg", "x y", *TRICKY], ["post"]]
    assert log[1]["env"]["CL_E"] == "é v"
    assert "sh -c" not in script and "eval" not in script


def test_failed_pre_stops_and_only_allowed_posts_run(tmp_path: Path) -> None:
    rec = recorder(tmp_path)
    custom = profile(preLaunch=[{"argv": ["/bin/false"], "timeout": 5}],
                     postExit=[{"argv": [str(rec), "always"], "timeout": 5, "runIfNotStarted": True},
                               {"argv": [str(rec), "normal"], "timeout": 5}])
    completed, log, _ = _payload(tmp_path, custom)
    assert completed.returncode == 1 and b"the game was not started" in completed.stderr
    assert [r["argv"] for r in log] == [["always"]]


def test_continue_policy_and_timeout(tmp_path: Path) -> None:
    custom = profile(preLaunch=[{"argv": ["/bin/sleep", "30"], "timeout": 1, "onFailure": "continue"}],
                     postExit=[{"argv": ["/bin/false"], "timeout": 5}])
    started = time.monotonic()
    completed, log, _ = _payload(tmp_path, custom, game_exit=0)
    assert time.monotonic() - started < 15
    assert completed.returncode == 0  # a failed after-exit command does not change the game status
    assert b"status 124); continuing" in completed.stderr and b"after-exit command 1 failed" in completed.stderr
    assert len(log) == 1


def test_steam_payload_without_hooks_still_execs(tmp_path: Path) -> None:
    completed, log, script = _payload(tmp_path, profile(arguments=[{"value": "-x"}]))
    assert completed.returncode == 0 and log[0]["argv"] == ["-x"]
    assert "exec " in script and "systemd-run" not in script


def test_local_payload_tracks_the_tree_in_a_scope_without_steam_reaper(tmp_path: Path) -> None:
    from game_optimization_linux.runner import _host_launcher_payload
    plan = build(profile(postExit=[{"argv": ["/bin/true"], "timeout": 5}]), steam=("/games/local/run",))
    script = _host_launcher_payload(plan, plan.environment).decode()
    assert "systemd-run --user --scope --collect --quiet --unit=app-gameopti-launch-" in script
    assert "while systemctl --user is-active --quiet" in script
    steam_plan = build(profile(postExit=[{"argv": ["/bin/true"], "timeout": 5}]), steam=(*STEAM, "/g"))
    assert "systemd-run" not in _host_launcher_payload(steam_plan, steam_plan.environment).decode()


def test_native_runner_hooks_never_use_a_shell(monkeypatch) -> None:
    from game_optimization_linux import runner
    calls = []

    class Popen:
        def __init__(self, argv, **kw):
            calls.append(("game", list(argv), kw.get("shell")))

        def wait(self):
            return 5

    monkeypatch.setattr(runner.subprocess, "Popen", Popen)
    monkeypatch.setattr(runner, "_wait_scope", lambda unit, run=None: calls.append(("wait", unit)))
    monkeypatch.setattr(runner, "run_hook", lambda argv, timeout, env, **kw: calls.append(("hook", list(argv), False)) or 0)
    plan = build(profile(preLaunch=[{"argv": ["/bin/true", "a b"]}], postExit=[{"argv": ["/bin/true"]}]),
                 steam=("/g",))
    assert runner._run_with_hooks(plan, ["/g", "x y"], {}) == 5
    assert calls[0] == ("hook", ["/bin/true", "a b"], False)
    assert calls[1][0] == "game" and calls[1][1][-2:] == ["/g", "x y"] and calls[1][2] is False
    assert calls[1][1][0] == "systemd-run" and calls[2][0] == "wait" and calls[3][0] == "hook"


# -- launcher wrapper helper (real processes) ----------------------------------------

@pytest.fixture
def helper(tmp_path: Path) -> Path:
    from game_optimization_linux.services.launch_hooks import HELPER_SOURCE
    path = tmp_path / "gameopti-launch-hooks"
    path.write_text(HELPER_SOURCE)
    path.chmod(0o700)
    return path


def _hooks(tmp_path: Path, pre=(), post=()) -> Path:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"pre": list(pre), "post": list(post)}))
    return path


def test_helper_waits_for_detached_descendants_and_keeps_status(tmp_path: Path, helper: Path) -> None:
    rec = recorder(tmp_path)
    detach = tmp_path / "detach"
    detach.write_text("#!/usr/bin/env python3\nimport os,sys,time\nif os.fork()==0:\n os.setsid()\n"
                      " if os.fork()==0:\n  time.sleep(2); os.execv(sys.argv[1],[sys.argv[1],'child'])\n os._exit(0)\n"
                      "sys.exit(7)\n")
    detach.chmod(0o755)
    hooks = _hooks(tmp_path, [{"argv": [str(rec), "pre", *TRICKY], "timeout": 5}],
                   [{"argv": [str(rec), "post"], "timeout": 5}])
    env = {**os.environ, "REC_LOG": str(tmp_path / "log")}
    completed = subprocess.run([str(helper), "--hooks", str(hooks), "--", str(detach), str(rec)], env=env, timeout=60)
    log = records(tmp_path / "log")
    assert completed.returncode == 7
    assert [r["argv"][0] for r in log] == ["pre", "child", "post"] and log[0]["argv"][1:] == TRICKY
    assert log[2]["t"] >= log[1]["t"]


def test_helper_stop_continue_timeout_and_missing_program(tmp_path: Path, helper: Path) -> None:
    rec = recorder(tmp_path)
    env = {**os.environ, "REC_LOG": str(tmp_path / "log")}
    stop = _hooks(tmp_path, [{"argv": ["/bin/false"], "timeout": 5}],
                  [{"argv": [str(rec), "allowed"], "timeout": 5, "runIfNotStarted": True},
                   {"argv": [str(rec), "normal"], "timeout": 5}])
    assert subprocess.run([str(helper), "--hooks", str(stop), "--", str(rec), "GAME"], env=env).returncode == 1
    assert [r["argv"] for r in records(tmp_path / "log")] == [["allowed"]]
    (tmp_path / "log").unlink()
    cont = _hooks(tmp_path, [{"argv": ["/bin/sleep", "30"], "timeout": 1, "onFailure": "continue"},
                             {"argv": ["/nonexistent/program"], "timeout": 1, "onFailure": "continue"}])
    started = time.monotonic()
    assert subprocess.run([str(helper), "--hooks", str(cont), "--", str(rec), "GAME"], env=env).returncode == 0
    assert time.monotonic() - started < 10 and [r["argv"] for r in records(tmp_path / "log")] == [["GAME"]]
    missing = _hooks(tmp_path, post=[{"argv": [str(rec), "allowed"], "timeout": 5, "runIfNotStarted": True}])
    (tmp_path / "log").unlink()
    assert subprocess.run([str(helper), "--hooks", str(missing), "--", "/nonexistent/game"], env=env).returncode == 127
    assert [r["argv"] for r in records(tmp_path / "log")] == [["allowed"]]


def test_helper_forwards_termination_to_the_game(tmp_path: Path, helper: Path) -> None:
    hooks = _hooks(tmp_path)
    process = subprocess.Popen([str(helper), "--hooks", str(hooks), "--", "/bin/sleep", "60"])
    time.sleep(1.0)
    process.terminate()
    assert process.wait(timeout=20) == 128 + 15 or process.returncode == -15 or process.returncode == 143


def test_helper_source_has_no_shell() -> None:
    from game_optimization_linux.services.launch_hooks import HELPER_SOURCE
    assert "shell=False" in HELPER_SOURCE and "shell=True" not in HELPER_SOURCE and "os.system" not in HELPER_SOURCE


def test_support_matrix_for_wrapper_hooks() -> None:
    from game_optimization_linux.services.launch_hooks import support
    heroic = _game(Launcher.HEROIC, "heroic-legendary-abc", launcher_game_id="abc")
    flat = replace(heroic, launcher_variant="flatpak")
    lutris = _game(Launcher.LUTRIS, "lutris-7", launcher_game_id="7")
    assert support(heroic)[0] and support(lutris)[0]
    assert not support(flat, umu_active=True)[0] and "UMU" in support(flat, umu_active=True)[2]
    assert not support(flat, umu_active=None)[0]
    assert support(flat, umu_active=False)[0]
    assert not support(replace(lutris, launcher_variant="flatpak"))[0]


# -- Heroic / Lutris through the real controller and integration -----------------------

def _heroic_controller(tmp_path: Path, monkeypatch, *, variant="native", section=None):
    from test_heroic_optiscaler import APP_NAME, _controller, _heroic_game
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    game = _heroic_game(tmp_path, variant=variant)
    base = tmp_path / (".var/app/com.heroicgameslauncher.hgl/config/heroic" if variant == "flatpak"
                       else ".config/heroic") / "GamesConfig"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{APP_NAME}.json"
    path.write_text(json.dumps({APP_NAME: section or {
        "enviromentOptions": [{"key": "FOREIGN", "value": "keep me"}], "wrapperOptions": [],
        "wineVersion": {"type": "wine"}}, "version": "v0"}, indent=2), encoding="utf-8")
    controller = _track(_controller(tmp_path, game))
    return game, controller, path


def _save(controller, game, payload, confirmed=True):
    return controller._custom_launch_controller.save(game.id, payload, confirmed)


def test_heroic_native_apply_writes_env_args_wrapper_and_hooks_then_restores(tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import APP_NAME
    game, controller, path = _heroic_controller(tmp_path, monkeypatch)
    original = path.read_bytes()
    payload = {"environment": [{"key": "CL_A", "value": "1"}], "arguments": [{"value": "-dx12"}, {"value": "a b"}],
               "wrapper": {"argv": ["/usr/bin/env", "CL_W=1"]},
               "preLaunch": [{"argv": ["/bin/true"], "timeout": 5}], "postExit": [{"argv": ["/bin/true"]}]}
    preview = controller._custom_launch_controller.preview(game.id, payload)
    assert not list((tmp_path / ".local/share/game-optimization-linux").rglob("*"))  # preview writes nothing
    assert preview["dirty"] and preview["draftResolved"]["support"]["preLaunch"]["state"] == "supported"
    assert not _save(controller, game, payload, confirmed=False)["success"]  # confirmation required
    assert path.read_bytes() == original and not controller._custom_launch_controller.repository.path(game.id).exists()
    result = _save(controller, game, payload)
    assert result["success"], result
    section = json.loads(path.read_text())[APP_NAME]
    env = {e["key"]: e["value"] for e in section["enviromentOptions"]}
    assert env["FOREIGN"] == "keep me" and env["CL_A"] == "1"
    assert section["launcherArgs"] == "-dx12 'a b'"
    wrapper = section["wrapperOptions"][0]
    assert wrapper["exe"].endswith("/gameopti-launch-hooks") and "/usr/bin/env CL_W=1" in wrapper["args"]
    helper_dir = Path(wrapper["exe"]).parent
    assert stat.S_IMODE(Path(wrapper["exe"]).stat().st_mode) == 0o700
    assert len(list(helper_dir.glob("hooks-*.json"))) == 1
    controller._optimization_controller.restoreLauncherIntegration(game.id)
    assert path.read_bytes() == original
    assert not helper_dir.exists()


def test_heroic_foreign_arguments_and_wrapper_block_apply_and_roll_back_the_profile(tmp_path, monkeypatch) -> None:
    game, controller, path = _heroic_controller(tmp_path, monkeypatch, section={
        "enviromentOptions": [], "wrapperOptions": [], "launcherArgs": "-user", "wineVersion": {"type": "wine"}})
    original = path.read_bytes()
    repo = controller._custom_launch_controller.repository
    repo.save(CustomLaunchProfile.from_dict(game.id, {"environment": [{"key": "CL_OLD", "value": "1"}]}))
    before = repo.read_bytes(game.id)
    result = _save(controller, game, {"arguments": [{"value": "-dx12"}]})
    assert not result["success"] and result["rolledBack"] and "own game arguments" in result["error"]
    assert path.read_bytes() == original and repo.read_bytes(game.id) == before
    game, controller, path = _heroic_controller(tmp_path / "w", monkeypatch, section={
        "enviromentOptions": [], "wrapperOptions": [{"exe": "/opt/foreign", "args": ""}], "wineVersion": {"type": "wine"}})
    original = path.read_bytes()
    assert not _save(controller, game, {"wrapper": {"argv": ["/usr/bin/env"]}})["success"]
    assert path.read_bytes() == original


def test_heroic_arguments_only_for_legendary(tmp_path, monkeypatch) -> None:
    game, controller, _path = _heroic_controller(tmp_path, monkeypatch)
    gog = replace(game, id="heroic-gog-1", launcher_game_id="1")
    support = controller._custom_launch_controller.support(gog)
    assert support["arguments"]["state"] == "unavailable" and "legendary" in support["arguments"]["reason"]


def test_heroic_flatpak_with_umu_has_no_hooks_but_keeps_env(tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import APP_NAME
    game, controller, path = _heroic_controller(tmp_path, monkeypatch, variant="flatpak", section={
        "enviromentOptions": [], "wrapperOptions": [], "wineVersion": {"type": "proton"}})
    payload = {"environment": [{"key": "CL_A", "value": "1"}], "postExit": [{"argv": ["/bin/true"]}]}
    preview = controller._custom_launch_controller.preview(game.id, payload)
    assert preview["draftResolved"]["support"]["postExit"]["state"] == "unavailable"
    assert any(item["feature"] == "postExit" for item in preview["draftResolved"]["unsupported"])
    assert _save(controller, game, payload)["success"]
    section = json.loads(path.read_text())[APP_NAME]
    assert section["wrapperOptions"] == [] and {"key": "CL_A", "value": "1"} in section["enviromentOptions"]
    assert not (tmp_path / "Games/Heroic/.gameopti-launch-hooks").exists()


def test_heroic_flatpak_without_umu_puts_the_helper_where_the_sandbox_can_see_it(tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import APP_NAME
    game, controller, path = _heroic_controller(tmp_path, monkeypatch, variant="flatpak", section={
        "enviromentOptions": [], "wrapperOptions": [], "wineVersion": {"type": "wine"}})
    assert _save(controller, game, {"preLaunch": [{"argv": ["/bin/true"]}]})["success"]
    exe = json.loads(path.read_text())[APP_NAME]["wrapperOptions"][0]["exe"]
    assert exe.startswith(str(tmp_path / "Games/Heroic/.gameopti-launch-hooks"))


def _lutris_controller(tmp_path: Path, monkeypatch, *, runner="wine", variant="native", system=None):
    import sqlite3
    from test_heroic_optiscaler import _controller
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    base = tmp_path / ".var/app/net.lutris.Lutris" if variant == "flatpak" else tmp_path
    config = base / ("config/lutris" if variant == "flatpak" else ".config/lutris")
    data = base / ("data/lutris" if variant == "flatpak" else ".local/share/lutris")
    (config / "games").mkdir(parents=True)
    data.mkdir(parents=True)
    connection = sqlite3.connect(data / "pga.db")
    connection.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, configpath TEXT)")
    connection.execute("INSERT INTO games VALUES (7, 'g-1')")
    connection.commit()
    connection.close()
    path = config / "games/g-1.yml"
    path.write_text(yaml.safe_dump({"game": {"exe": "/games/t/a.exe", "custom": "unknown field"},
                                    "system": system or {"env": {"FOREIGN": "keep"}}}), encoding="utf-8")
    game = _game(Launcher.LUTRIS, "lutris-7", launcher_game_id="7", runner=runner, launcher_variant=variant,
                 data_source="Lutris")
    return game, _track(_controller(tmp_path, game)), path


def test_lutris_native_args_env_wrapper_hooks_and_byte_restore(tmp_path, monkeypatch) -> None:
    game, controller, path = _lutris_controller(tmp_path, monkeypatch)
    original = path.read_bytes()
    payload = {"environment": [{"key": "CL_A", "value": "x y"}], "arguments": [{"value": "-x"}, {"value": "zażółć ✓"}],
               "postExit": [{"argv": ["/bin/true"]}]}
    assert _save(controller, game, payload)["success"]
    document = yaml.safe_load(path.read_text())
    assert document["game"]["args"] == "-x 'zażółć ✓'" and document["game"]["custom"] == "unknown field"
    assert document["system"]["env"] == {"FOREIGN": "keep", "CL_A": "x y"}
    assert "gameopti-launch-hooks" in document["system"]["prefix_command"]
    import shlex
    assert shlex.split(document["game"]["args"]) == ["-x", "zażółć ✓"]  # Lutris split_arguments is shlex
    controller._optimization_controller.restoreLauncherIntegration(game.id)
    assert path.read_bytes() == original


def test_lutris_foreign_prefix_blocks_and_flatpak_has_no_hooks(tmp_path, monkeypatch) -> None:
    game, controller, path = _lutris_controller(tmp_path, monkeypatch, system={"prefix_command": "/opt/foreign"})
    original = path.read_bytes()
    assert not _save(controller, game, {"wrapper": {"argv": ["/usr/bin/env"]}})["success"]
    assert path.read_bytes() == original
    game, controller, path = _lutris_controller(tmp_path / "f", monkeypatch, variant="flatpak")
    support = controller._custom_launch_controller.support(game)
    assert support["preLaunch"]["state"] == "unavailable" and support["environment"]["state"] == "supported"


def test_lutris_arguments_unsupported_for_other_runners(tmp_path, monkeypatch) -> None:
    game, controller, _ = _lutris_controller(tmp_path, monkeypatch, runner="dosbox")
    assert controller._custom_launch_controller.support(game)["arguments"]["state"] == "unavailable"


def test_missing_launcher_config_is_an_explicit_unavailable_state(tmp_path, monkeypatch) -> None:
    game, controller, path = _heroic_controller(tmp_path, monkeypatch)
    path.unlink()
    resolved = controller._custom_launch_controller.preview(game.id, {"arguments": [{"value": "-x"}]})["draftResolved"]
    assert resolved["launcherError"] and all(row["state"] == "unavailable" for row in resolved["support"].values())


# -- Manual / Custom ----------------------------------------------------------------

def _manual(tmp_path: Path, *, wine: bool = False):
    from game_optimization_linux.models.manual_game import ManualGameConfig
    root = tmp_path / "Gry z odstępem ł"
    (root / "bin dir").mkdir(parents=True)
    exe = root / "bin dir" / ("gra.exe" if wine else "gra ✓")
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    runner = ()
    if wine:
        runner_path = tmp_path / "proton dir" / "wine"
        runner_path.parent.mkdir()
        runner_path.write_text("#!/bin/sh\nexit 0\n")
        runner_path.chmod(0o755)
        runner = (str(runner_path),)
    return ManualGameConfig(id="manual-11111111-1111-4111-8111-111111111111", name="Gra", executable=exe,
                            install_directory=root, working_directory=root / "bin dir", arguments=("-own",),
                            environment=(("CL_MANUAL", "manual"),), runner_command=runner)


def test_manual_custom_layers_on_top_of_native_and_wine_configs(tmp_path: Path) -> None:
    from game_optimization_linux.services.launcher_native import ManualGameLauncher
    launcher = ManualGameLauncher(environment={})
    for wine in (False, True):
        configuration = _manual(tmp_path / str(wine), wine=wine)
        plan = launcher.prepare(configuration)
        custom = CustomLaunchProfile.from_dict("local-0123456789abcdef01234567", {
            "environment": [{"key": "CL_MANUAL", "value": "custom"}, {"key": "CL_NEW", "value": "1"}],
            "arguments": [{"value": "a b"}], "wrapper": {"argv": ["/usr/bin/env"]},
            "postExit": [{"argv": ["/bin/true"]}]})
        resolved = launcher.apply_custom(plan, custom, tree_prefix=["systemd-run", "--"], tree_unit="u.scope")
        command = list(resolved.game_command)
        assert command[:3] == ["systemd-run", "--", "/usr/bin/env"]
        assert command[3:] == [*configuration.runner_command, str(configuration.executable), "-own", "a b"]
        env = dict(resolved.environment)
        assert env["CL_MANUAL"] == "manual" and env["CL_NEW"] == "1"  # manual configuration wins
        assert resolved.warnings and resolved.tree_unit == "u.scope" and resolved.cwd == configuration.working_directory


def test_manual_execute_hooks_order_policy_and_tree_wait(tmp_path: Path) -> None:
    from game_optimization_linux.services.launcher_native import ManualGameLauncher
    calls = []

    def run(argv, **kw):
        assert kw.get("shell") is False
        calls.append(list(argv))
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(argv, 0 if calls.count(list(argv)) < 2 else 3)
        return subprocess.CompletedProcess(argv, 1 if "fail" in argv else 0)

    class Popen:
        def __init__(self, argv, **kw):
            calls.append(["GAME", *argv])

        def wait(self):
            return 9

    launcher = ManualGameLauncher(environment={}, run=run, popen=Popen)
    plan = launcher.prepare(_manual(tmp_path))
    custom = CustomLaunchProfile.from_dict("local-0123456789abcdef01234567", {
        "preLaunch": [{"argv": ["/bin/true", "fail"], "onFailure": "continue"}, {"argv": ["/bin/true", "ok"]}],
        "postExit": [{"argv": ["/bin/true", "post"]}]})
    plan = launcher.apply_custom(plan, custom, tree_prefix=["systemd-run", "--"], tree_unit="u.scope")
    monkey_sleep = time.sleep
    try:
        time.sleep = lambda _s: None
        result = launcher.execute(plan)
    finally:
        time.sleep = monkey_sleep
    names = [c[0] if c[0] != "timeout" else c[4:] for c in calls]
    assert result.game_exit_code == 9
    assert names[0] == ["/bin/true", "fail"] and names[1] == ["/bin/true", "ok"] and names[2] == "GAME"
    assert names.count("systemctl") == 2 and names[-1] == ["/bin/true", "post"]
    assert calls[0][:4] == ["timeout", "-k", "5", "30"]


def test_manual_stop_policy_skips_the_game(tmp_path: Path) -> None:
    from game_optimization_linux.services.launcher_native import ManualGameLauncher
    calls = []
    launcher = ManualGameLauncher(environment={}, run=lambda argv, **kw: calls.append(argv) or
                                  subprocess.CompletedProcess(argv, 2 if "/bin/false" in argv else 0),
                                  popen=lambda *a, **k: pytest.fail("the game must not start"))
    plan = launcher.apply_custom(launcher.prepare(_manual(tmp_path)), CustomLaunchProfile.from_dict("local-0123456789abcdef01234567", {
        "preLaunch": [{"argv": ["/bin/false"]}],
        "postExit": [{"argv": ["/bin/true", "allowed"], "runIfNotStarted": True}, {"argv": ["/bin/true", "no"]}]}))
    result = launcher.execute(plan)
    assert result.game_exit_code is None and "not started" in result.error
    assert [c[4:] for c in calls] == [["/bin/false"], ["/bin/true", "allowed"]]


def test_manual_real_process_tree_scope(tmp_path: Path) -> None:
    """systemd user scope really outlives the main process until a detached child ends."""
    if subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode not in (0, 1):
        pytest.skip("no systemd user manager")
    from game_optimization_linux.services.launcher_native import ManualGameLauncher
    rec = recorder(tmp_path)
    detach = tmp_path / "detach"
    detach.write_text(f"#!/bin/sh\n( sleep 2; {rec} child \"$@\" ) &\nexit 4\n")
    detach.chmod(0o755)
    from game_optimization_linux.models.manual_game import ManualGameConfig
    configuration = ManualGameConfig(id="manual-11111111-1111-4111-8111-111111111111", name="G", executable=detach,
                                     install_directory=tmp_path, environment=(("REC_LOG", str(tmp_path / "log")),),
                                     arguments=("$HOME;|x", "${USER}"))
    launcher = ManualGameLauncher(environment={"PATH": os.environ["PATH"]})
    unit, tree = launcher.tree_tracking()
    plan = launcher.apply_custom(launcher.prepare(configuration), CustomLaunchProfile.from_dict(
        "local-0123456789abcdef01234567", {"postExit": [{"argv": [str(rec), "post"], "timeout": 5}]}), tree_prefix=tree, tree_unit=unit)
    environment = {"PATH": os.environ["PATH"], "REC_LOG": str(tmp_path / "log"),
                   **{k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ}}
    launcher._environment.update(environment)
    result = launcher.execute(plan)
    log = records(tmp_path / "log")
    assert result.game_exit_code == 4
    # systemd >= 254 would expand $HOME/${USER} without --expand-environment=no.
    assert [r["argv"] for r in log] == [["child", "$HOME;|x", "${USER}"], ["post"]]


def test_local_windows_exe_without_runner_is_unavailable(tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import _controller
    game = _game(Launcher.MANUAL, "local-abcdefabcdefabcdefabcdef", data_source="local", executable_path="/g/a.exe")
    controller = _track(_controller(tmp_path, game))
    controller._custom_launch_controller._local_windows = lambda g: True
    support = controller._custom_launch_controller.support(game)
    assert all(row["state"] == "unavailable" for row in support.values())
    assert "Proton runner" in support["environment"]["reason"]


def test_local_game_from_the_flatpak_build_is_reported_unavailable(tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import _controller
    game = _game(Launcher.MANUAL, "local-abcdefabcdefabcdefabcdef", data_source="local", executable_path="/g/run")
    controller = _track(_controller(tmp_path, game))
    controller._custom_launch_controller._local_windows = lambda g: False
    assert controller._custom_launch_controller.support(game)["postExit"]["state"] == "supported"
    monkeypatch.setenv("FLATPAK_ID", "io.github.DevVoidPL.GameOptimizationLinux")
    support = controller._custom_launch_controller.support(game)
    assert all(row["code"] == "local_flatpak_host_runner" for row in support.values())


def test_steam_flatpak_is_reported_unavailable(tmp_path) -> None:
    from test_heroic_optiscaler import _controller
    game = _game(Launcher.STEAM, "1", steam_app_id="1", launcher_game_id="1", data_source="Steam Flatpak")
    support = _track(_controller(tmp_path, game))._custom_launch_controller.support(game)
    assert all(row["state"] == "unavailable" for row in support.values())


# -- controller preview / draft ----------------------------------------------------

def test_steam_preview_separates_saved_and_draft_and_never_executes(tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import _controller
    game = _game(Launcher.STEAM, "42", steam_app_id="42", launcher_game_id="42", data_source="Steam")
    controller = _track(_controller(tmp_path, game))
    custom = controller._custom_launch_controller
    marker = tmp_path / "executed"
    payload = {"preLaunch": [{"argv": ["/usr/bin/touch", str(marker)]}], "arguments": [{"value": "-x"}],
               "environment": [{"key": "API_TOKEN", "value": "s3cret"}]}
    preview = custom.preview(game.id, payload)
    assert preview["success"] and preview["dirty"] and preview["profile"]["preLaunch"] == []
    resolved = preview["draftResolved"]
    assert resolved["argv"][-1] == "-x" and resolved["pre"][0]["where"] == "host_runner"
    assert "s3cret" not in resolved["technicalText"] and "<redacted>" in resolved["technicalText"]
    assert not marker.exists() and not custom.repository.path("42").exists()
    assert custom.save(game.id, payload)["success"]  # Steam: no launcher file, no confirmation
    assert custom.get(game.id)["profile"]["arguments"] == [{"value": "-x", "enabled": True}]
    assert not marker.exists()


def test_invalid_draft_is_reported_and_not_saved(tmp_path) -> None:
    from test_heroic_optiscaler import _controller
    game = _game(Launcher.STEAM, "42", steam_app_id="42", launcher_game_id="42", data_source="Steam")
    custom = _track(_controller(tmp_path, game))._custom_launch_controller
    result = custom.save(game.id, {"environment": [{"key": "BAD KEY", "value": "1"}]})
    assert not result["success"] and result["errors"][0]["section"] == "environment"
    assert not custom.repository.path("42").exists()


def test_implemented_paths_have_no_shell_true() -> None:
    for name in ("services/custom_launch.py", "services/launch_hooks.py", "controllers/custom_launch_controller.py",
                 "services/launcher_native.py", "runner.py"):
        text = (SRC / name).read_text()
        assert "shell=True" not in text and "os.system(" not in text, name
