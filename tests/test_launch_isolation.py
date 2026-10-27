"""Preserve foreign loader settings while isolating managed game integrations.

The game environment is checked by executing the planned wrapper chain with the
real /bin/sh and /usr/bin/env (only the compositor binary itself is skipped).
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess

import pytest

from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolAvailability
from game_optimization_linux.services.frame_generation import profile_path, resolve_launch

LSFG = {"name": "VK_LAYER_LSFGVK_frame_generation", "backend": "lsfg", "manifest": "/l.json", "kind": "implicit",
        "libraryPresent": True, "enable": None, "disable": "DISABLE_LSFGVK"}
AFMF = {"name": "VK_LAYER_AFMF", "backend": "afmf", "manifest": "/a.json", "kind": "implicit",
        "libraryPresent": True, "enable": ["AFMF_ENABLE", "1"], "disable": "DISABLE_AFMF"}
MAKO = {"name": "VK_LAYER_MAKO_render", "backend": "mako", "manifest": "/m.json", "kind": "implicit",
        "libraryPresent": True, "enable": ["ENABLE_MAKO", "1"], "disable": "DISABLE_MAKO"}
GAMESCOPE = RuntimeToolAvailability("gamescope", True, "/usr/bin/gamescope",
                                    supported_options=("-W", "-H", "-w", "-h", "-r", "-f", "-b", "-F", "-S"))
MISSING = RuntimeToolAvailability("gamemode", False)


@pytest.fixture
def discovered(monkeypatch):
    from game_optimization_linux.services import frame_generation as fg
    from game_optimization_linux.services import frame_generation_isolation as isolation
    current = {"layers": [AFMF, MAKO]}
    monkeypatch.setattr(fg, "discover_layers", lambda env: [dict(x) for x in current["layers"]])
    monkeypatch.setattr(isolation, "discover_layers", lambda env, d=None: [dict(x) for x in current["layers"]])
    return current


def off_profile(root):
    path = profile_path("42", root); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"backend": "off"}))


def build(inherited, root, *, gamescope=False, steam=("/usr/bin/env", "-0"), pp=None):
    resolved = resolve_launch("42", root, inherited=inherited, gamescope=gamescope)
    profile = replace(GameOptimizationProfile.default("42"), gamescope_enabled=gamescope,
                      gamescope_mode="native" if gamescope else "disabled")
    plan = OptimizationLaunchPlanner().build(profile, list(steam), gamescope=GAMESCOPE, gamemode=MISSING,
        existing_environment=inherited, post_processing_environment=pp or {},
        frame_generation_environment=resolved.environment, frame_generation_prefix=resolved.prefix,
        frame_generation_report=resolved.report())
    return resolved, plan


def run_chain(plan, inherited):
    """Return (compositor env, game env) by executing the real env/sh chain."""
    outer = plan.process_environment({**inherited, **plan.environment})
    command = list(plan.command)
    if plan.gamescope_wrapper:
        command = command[command.index("--") + 1:]  # Gamescope passes its environment to the child
    result = subprocess.run(command, env=outer, capture_output=True, check=True)
    game = dict(item.split("=", 1) for item in result.stdout.decode().split("\0") if item)
    return outer, game


# 6. Environments ---------------------------------------------------------------

def test_off_without_activation_leaves_every_inherited_entry_untouched(tmp_path, discovered):
    off_profile(tmp_path)
    inherited = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": "/custom/lib:/steam/ubuntu12_64",
                 "LD_PRELOAD": "/custom/monitor.so:/steam/ubuntu12_64/gameoverlayrenderer.so:/steam/ubuntu12_32/gameoverlayrenderer.so",
                 "VK_INSTANCE_LAYERS": "VK_LAYER_MESA_device_select:VK_LAYER_CUSTOM_AFMF_monitor",
                 "VK_LOADER_LAYERS_DISABLE": "VK_LAYER_USER_blocked,~explicit~", "VK_ADD_LAYER_PATH": "/custom/layers",
                 "SteamDeck": "1", "PROTON_FSR4_UPGRADE": "1"}
    resolved, plan = build(inherited, tmp_path)
    assert resolved.environment == {} and plan.frame_generation["entries"] == ()
    assert not any(source == "frame_generation" for source in plan.environment_sources.values())
    _, game = run_chain(plan, inherited)
    assert {k: game[k] for k in inherited} == inherited


def test_concrete_activation_keeps_foreign_entries_without_duplicates(tmp_path, discovered):
    off_profile(tmp_path)
    discovered["layers"] = [AFMF, MAKO, LSFG]
    inherited = {"PATH": "/usr/bin:/bin", "LD_PRELOAD": "/custom/monitor.so:/steam/gameoverlayrenderer.so",
                 "LD_LIBRARY_PATH": "/custom/lib", "ENABLE_MAKO": "1",
                 "VK_INSTANCE_LAYERS": "VK_LAYER_MESA_device_select:VK_LAYER_LSFGVK_frame_generation:VK_LAYER_CUSTOM_AFMF_monitor",
                 "VK_LOADER_LAYERS_ENABLE": "VK_LAYER_user", "VK_LOADER_LAYERS_DISABLE": "VK_LAYER_USER_blocked"}
    pp = {"VK_INSTANCE_LAYERS": "VK_LAYER_VKBASALT_post_processing", "VK_ADD_LAYER_PATH": "/owned/pp"}
    resolved, plan = build(inherited, tmp_path, pp=pp)
    assert {e["reason"] for e in resolved.entries} == {"inherited_enable", "instance_layers", "automatic_load"}
    _, game = run_chain(plan, inherited)
    assert game["VK_INSTANCE_LAYERS"] == "VK_LAYER_VKBASALT_post_processing:VK_LAYER_MESA_device_select:VK_LAYER_CUSTOM_AFMF_monitor"
    assert game["DISABLE_MAKO"] == "1" and game["DISABLE_LSFGVK"] == "1" and "DISABLE_AFMF" not in game
    assert game["ENABLE_MAKO"] == "1"  # inherited activation kept, blocked by the gate
    for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "VK_LOADER_LAYERS_ENABLE", "VK_LOADER_LAYERS_DISABLE"):
        assert game[key] == inherited[key]
    for value in game.values():
        parts = value.split(":")
        assert len(parts) == len(set(parts)) or "/" in value


def test_compositor_is_analyzed_separately_from_the_selected_game_generator(tmp_path, discovered, monkeypatch):
    from game_optimization_linux.services import frame_generation as fg
    monkeypatch.setattr(fg, "FrameGenerationArtifacts", lambda *a: SimpleNamespace(
        status=lambda b: {"installed": True, "version": fg.VERSIONS[b]}, directory=lambda b: tmp_path / b))
    path = profile_path("42", tmp_path); path.parent.mkdir(); path.write_text(json.dumps({"backend": "afmf", "settings": {}}))
    inherited = {"PATH": "/usr/bin:/bin", "ENABLE_MAKO": "1", "LD_PRELOAD": "/custom/a.so:/steam/ubuntu12_64/gameoverlayrenderer.so"}
    _, plan = build(inherited, tmp_path, gamescope=True)
    compositor, game = run_chain(plan, inherited)
    assert compositor["DISABLE_MAKO"] == "1" and "AFMF_ENABLE" not in compositor
    assert compositor["LD_PRELOAD"] == "/custom/a.so"
    assert game["AFMF_ENABLE"] == "1" and game["DISABLE_MAKO"] == "1" and "DISABLE_AFMF" not in game
    assert game["LD_PRELOAD"] == inherited["LD_PRELOAD"]
    targets = sorted((e["target"], e["key"]) for e in plan.frame_generation["entries"])
    assert targets == [("compositor", "DISABLE_MAKO"), ("game", "DISABLE_MAKO")]


def test_compositor_only_guard_does_not_change_an_unmanaged_game(tmp_path, discovered):
    inherited = {"PATH": "/usr/bin:/bin", "AFMF_ENABLE": "1"}  # no GameOpti frame-generation profile
    resolved, plan = build(inherited, tmp_path, gamescope=True)
    assert resolved.backend == "" and resolved.environment == {}
    compositor, game = run_chain(plan, inherited)
    assert compositor["DISABLE_AFMF"] == "1"
    assert game["AFMF_ENABLE"] == "1" and "DISABLE_AFMF" not in game
    assert plan.environment_unset_keys == ("DISABLE_AFMF",)


def test_host_payload_quoting_round_trips_through_real_sh(tmp_path, discovered):
    from game_optimization_linux.runner import _host_launcher_payload
    off_profile(tmp_path)
    discovered["layers"] = [{**AFMF, "disable": "AFMF_OFF"}]  # forces the exact disable-filter path
    inherited = {"PATH": "/usr/bin:/bin", "AFMF_ENABLE": "1",
                 "VK_LOADER_LAYERS_DISABLE": "VK_LAYER_user*,*mon*,~explicit~",
                 "WEIRD": "a b 'c' \"d\" $HOME `x` ;*"}
    resolved, plan = build(inherited, tmp_path)
    assert resolved.environment == {"VK_LOADER_LAYERS_DISABLE": "VK_LAYER_user*,*mon*,~explicit~,VK_LAYER_AFMF"}
    script = tmp_path / "launcher"; script.write_bytes(_host_launcher_payload(plan, plan.environment))
    result = subprocess.run(["/bin/sh", str(script), *plan.steam_command], env=inherited, capture_output=True, check=True)
    game = dict(item.split("=", 1) for item in result.stdout.decode().split("\0") if item)
    assert game["VK_LOADER_LAYERS_DISABLE"] == resolved.environment["VK_LOADER_LAYERS_DISABLE"]
    assert game["WEIRD"] == inherited["WEIRD"]


def test_runtime_path_names_do_not_match_unrelated_substrings():
    from game_optimization_linux.services.optimization_runtime import _gamescope_environment_boundary
    inherited = {"LD_LIBRARY_PATH": "/custom/mysteamrttools/lib::/custom/not-steam-runtime/lib:/steam/SteamLinuxRuntime_4/lib"}
    restore, removed, outer = _gamescope_environment_boundary(inherited)
    assert outer["LD_LIBRARY_PATH"] == "/custom/mysteamrttools/lib::/custom/not-steam-runtime/lib"
    assert restore == inherited and removed == ("LD_LIBRARY_PATH",)


# Saved profile -> runner -> process environment --------------------------------

class _Detector:
    def detect(self):
        return RuntimeToolAvailability("gamemode", True, "/usr/bin/gamemoderun"), GAMESCOPE


@pytest.mark.parametrize("gamescope", [False, True])
@pytest.mark.parametrize("gamemode", [False, True])
@pytest.mark.parametrize("deck,fsr4", [(False, False), (True, False), (False, True), (True, True)])
def test_runner_follows_each_saved_toggle(tmp_path, discovered, monkeypatch, gamescope, gamemode, deck, fsr4):
    from game_optimization_linux.runner import main as runner_main
    from game_optimization_linux.services.optimization_profiles import GameOptimizationProfileRepository
    from game_optimization_linux.services.proton_tweaks import ProtonTweaksRepository
    from game_optimization_linux.models.proton_tweaks import ProtonTweaksProfile
    root = tmp_path / "games"
    repository = GameOptimizationProfileRepository(root)
    repository.save(replace(GameOptimizationProfile.default("42"), gamemode_enabled=gamemode,
                            gamescope_enabled=gamescope, gamescope_mode="native" if gamescope else "disabled"))
    states = {**({"steam_deck_spoof": "enabled"} if deck else {}), **({"fsr4_upgrade": "enabled"} if fsr4 else {})}
    tweaks = ProtonTweaksRepository(root)
    tweaks.save(replace(ProtonTweaksProfile.default("42"), states=states))
    off_profile(root)
    for key in ("SteamDeck", "PROTON_FSR4_UPGRADE", "AFMF_ENABLE", "ENABLE_MAKO", "VK_INSTANCE_LAYERS"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    assert runner_main(["--appid", "42", "--", "/game"], repository=repository, proton_tweaks_repository=tweaks,
                       detector=_Detector(), executor=lambda exe, argv, env: calls.append((argv, env)) or 0,
                       report_root=tmp_path / "reports") == 0
    argv, outer = calls[0]
    joined = " ".join(argv)
    assert (argv[0] == "/usr/bin/gamescope") is gamescope
    assert ("/usr/bin/gamemoderun" in argv) is gamemode
    game = {**outer}
    if gamescope:  # the restored game values are passed explicitly after Gamescope
        game.update(item.split("=", 1) for item in argv[argv.index("--") + 1:] if "=" in item and not item.startswith("/"))
    assert (game.get("SteamDeck") == "1") is deck and (game.get("PROTON_FSR4_UPGRADE") == "1") is fsr4
    assert "DISABLE_AFMF" not in joined and "DISABLE_MAKO" not in game
    report = json.loads((tmp_path / "reports" / "42.json").read_text())
    assert report["frameGenerationResolvedBackend"] == "off" and report["frameGenerationIsolation"] == []


# Post-processing backends and technical classification ------------------------

RESHADE_PP = {"WINEDLLOVERRIDES": "dxgi=n,b", "ENABLE_VKBASALT": "0", "DISABLE_VKBASALT": "1"}
VKBASALT_PP = {"ENABLE_VKBASALT": "1", "DISABLE_VKBASALT": "0", "VKBASALT_CONFIG_FILE": "/g/.p/vkBasalt.conf",
               "VK_ADD_LAYER_PATH": "/g/.p/layer", "VK_INSTANCE_LAYERS": "VK_LAYER_VKBASALT_post_processing"}


def _rows(plan):
    return {(row["target"], row["key"]): row for row in plan.environment_classification}


def test_reshade_keeps_its_dxgi_override_and_adds_no_vkbasalt_variables(tmp_path, discovered):
    off_profile(tmp_path)
    inherited = {"PATH": "/usr/bin:/bin", "WINEDLLOVERRIDES": "winemenubuilder.exe=d",
                 "VK_INSTANCE_LAYERS": "VK_LAYER_MESA_device_select", "LD_PRELOAD": "/steam/gameoverlayrenderer.so"}
    _, plan = build(inherited, tmp_path, pp=RESHADE_PP)
    assert "ENABLE_VKBASALT" not in plan.environment and "DISABLE_VKBASALT" not in plan.environment
    assert plan.environment["WINEDLLOVERRIDES"] == "dxgi=n,b;winemenubuilder.exe=d" or "dxgi=n,b" in plan.environment["WINEDLLOVERRIDES"]
    row = _rows(plan)[("game", "WINEDLLOVERRIDES")]
    assert (row["category"], row["owner"]) == ("activation", "ReShade")
    assert not any(r["owner"] == "vkBasalt" for r in plan.environment_classification)
    _, game = run_chain(plan, inherited)
    assert game["VK_INSTANCE_LAYERS"] == inherited["VK_INSTANCE_LAYERS"] and game["LD_PRELOAD"] == inherited["LD_PRELOAD"]
    # A concrete inherited vkBasalt activation gets one targeted gate.
    _, plan = build({**inherited, "ENABLE_VKBASALT": "1"}, tmp_path, pp=RESHADE_PP)
    assert plan.environment.get("DISABLE_VKBASALT") == "1" and "ENABLE_VKBASALT" not in plan.environment
    assert _rows(plan)[("game", "DISABLE_VKBASALT")]["category"] == "suppression"


def test_selected_vkbasalt_is_activation_and_its_duplicate_gate_is_suppression(tmp_path, discovered):
    off_profile(tmp_path)
    _, plan = build({"PATH": "/usr/bin:/bin"}, tmp_path, pp=VKBASALT_PP)
    rows = _rows(plan)
    assert rows[("game", "ENABLE_VKBASALT")]["category"] == "activation"
    assert rows[("game", "VK_INSTANCE_LAYERS")]["owner"] == "vkBasalt"
    assert rows[("game", "DISABLE_VKBASALT")]["category"] == "suppression"


def test_every_variable_is_classified_and_guards_are_never_activation(tmp_path, discovered, monkeypatch):
    from game_optimization_linux.services import frame_generation as fg
    monkeypatch.setattr(fg, "FrameGenerationArtifacts", lambda *a: SimpleNamespace(
        status=lambda b: {"installed": True, "version": fg.VERSIONS[b]}, directory=lambda b: tmp_path / b))
    path = profile_path("42", tmp_path); path.parent.mkdir(); path.write_text(json.dumps({"backend": "afmf", "settings": {}}))
    inherited = {"PATH": "/usr/bin:/bin", "ENABLE_MAKO": "1", "LD_PRELOAD": "/custom/a.so:/s/ubuntu12_64/gameoverlayrenderer.so"}
    _, plan = build(inherited, tmp_path, gamescope=True)
    rows = _rows(plan)
    assert set(plan.environment) <= {key for target, key in rows if target == "game"}
    assert rows[("game", "AFMF_ENABLE")]["category"] == "activation"
    assert rows[("game", "DISABLE_MAKO")]["category"] == "suppression"
    assert rows[("compositor", "DISABLE_MAKO")]["category"] == "suppression"
    assert rows[("game", "LD_PRELOAD")]["category"] == "boundary"
    assert {r["category"] for r in plan.environment_classification} <= {"activation", "inherited", "suppression", "boundary", "unresolved"}
    assert plan.to_dict()["environmentClassification"]


def test_unresolved_conflicts_are_reported_without_changes(tmp_path, discovered):
    off_profile(tmp_path)
    discovered["layers"] = [{**AFMF, "disable": None}]
    inherited = {"PATH": "/usr/bin:/bin", "AFMF_ENABLE": "1", "VK_LOADER_LAYERS_ENABLE": "VK_LAYER_*"}
    resolved, plan = build(inherited, tmp_path)
    assert resolved.environment == {}
    unresolved = [r for r in plan.environment_classification if r["category"] == "unresolved"]
    assert len(unresolved) == 1 and "VK_LAYER_*" in unresolved[0]["reason"]
