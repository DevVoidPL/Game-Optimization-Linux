"""A saved Off is not a request to mutate every available generator's environment.

Expected behaviour mirrors Vulkan-Loader HEAD 3172c4859f61 (loader.c and
loader_environment.c). Fixtures are synthetic manifests in tmp_path.
"""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import copy
import json

import pytest

from game_optimization_linux.services import frame_generation_isolation as isolation
from game_optimization_linux.services.frame_generation_isolation import (
    discover_layers, isolation_plan, matching_tokens, parse_filters, selected_blocker,
    search_directories as real_search_directories,
)
from game_optimization_linux.services.frame_generation import launch_settings, profile_path, resolve_launch

AFMF = {"name": "VK_LAYER_AFMF", "enable": {"AFMF_ENABLE": "1"}, "disable": {"DISABLE_AFMF": "1"}}
MAKO = {"name": "VK_LAYER_MAKO_render", "enable": {"ENABLE_MAKO": "1"}, "disable": {"DISABLE_MAKO": "1"}}
SPATIAL = {"name": "VK_LAYER_MAKO_spatial_scaling", "enable": {"ENABLE_MAKO_SPATIAL_SCALING": "1"},
           "disable": {"DISABLE_MAKO_SPATIAL_SCALING": "1"}}
LSFG = {"name": "VK_LAYER_LSFGVK_frame_generation", "enable": None, "disable": {"DISABLE_LSFGVK": "1"}}


def manifest(directory: Path, spec: dict, *, library=True, file=None):
    directory.mkdir(parents=True, exist_ok=True)
    stem = file or spec["name"]
    (directory / f"{stem}.so").write_bytes(b"not-executed-test-library") if library else None
    layer = {"name": spec["name"], "type": "GLOBAL", "library_path": f"./{stem}.so"}
    if spec.get("enable"): layer["enable_environment"] = spec["enable"]
    if spec.get("disable"): layer["disable_environment"] = spec["disable"]
    (directory / f"{stem}.json").write_text(json.dumps({"file_format_version": "1.0.0", "layer": layer}))
    return directory


def layers(tmp_path, *specs, library=True):
    for spec in specs:
        manifest(tmp_path / "implicit_layer.d", spec, library=library)
    return discover_layers({}, [tmp_path / "implicit_layer.d"])


# 1. Absent backend -------------------------------------------------------------

def test_absent_backend_adds_no_activation_guard_or_diagnostic(tmp_path):
    assert isolation_plan({"AFMF_ENABLE": "1"}, [])["environment"] == {}
    missing = layers(tmp_path, AFMF, library=False)
    assert missing[0]["libraryPresent"] is False
    plan = isolation_plan({"AFMF_ENABLE": "1", "VK_INSTANCE_LAYERS": "VK_LAYER_AFMF"}, missing)
    assert plan["environment"] == {} and plan["entries"] == [] and plan["requested"] == []
    assert plan["warnings"] == []


# 2. Installed but inactive opt-in backend -------------------------------------

def test_installed_opt_in_backend_with_saved_off_is_detected_only(tmp_path):
    found = layers(tmp_path, AFMF, MAKO)
    assert {layer["backend"] for layer in found} == {"afmf", "mako"} and all(l["libraryPresent"] for l in found)
    plan = isolation_plan({"LD_PRELOAD": "/user/a.so"}, found)
    assert plan["environment"] == {} and plan["entries"] == [] and plan["requested"] == []
    assert len(plan["detected"]) == 2  # detection is not loading or activation
    for value in ("0", "", "true", "1 "):  # the loader compares the exact string
        assert isolation_plan({"AFMF_ENABLE": value}, found)["environment"] == {}


def test_saved_off_profile_resolves_empty_and_never_activates(tmp_path, monkeypatch):
    import game_optimization_linux.services.frame_generation as fg
    found = layers(tmp_path / "layers", AFMF, MAKO)
    monkeypatch.setattr(fg, "discover_layers", lambda env: found)
    path = profile_path("42", tmp_path); path.parent.mkdir(); path.write_text(json.dumps({"backend": "off"}))
    resolved = resolve_launch("42", tmp_path)
    assert (resolved.environment, resolved.prefix, resolved.entries, resolved.backend) == ({}, [], (), "off")
    assert launch_settings("42", tmp_path, inherited={"AFMF_ENABLE": "1"}) == ({"DISABLE_AFMF": "1"}, [])
    path.unlink()
    assert resolve_launch("42", tmp_path, inherited={"AFMF_ENABLE": "1"}).backend == ""  # not managed


# 3. Concrete inherited activation ----------------------------------------------

def test_concrete_activation_blocks_only_that_backend_with_exact_reason(tmp_path):
    found = layers(tmp_path, AFMF, MAKO)
    inherited = {"ENABLE_MAKO": "1", "LD_PRELOAD": "/user/a.so:/steam/gameoverlayrenderer.so",
                 "LD_LIBRARY_PATH": "/user/lib", "VK_INSTANCE_LAYERS": "VK_LAYER_MESA_device_select",
                 "VK_LOADER_LAYERS_DISABLE": "VK_LAYER_USER_blocked"}
    before = copy.deepcopy(inherited)
    plan = isolation_plan(inherited, found)
    assert plan["environment"] == {"DISABLE_MAKO": "1"}
    assert inherited == before
    [entry] = plan["entries"]
    assert entry | {"manifest": ""} == {"target": "game", "key": "DISABLE_MAKO", "value": "1", "layer": "VK_LAYER_MAKO_render",
                                        "backend": "mako", "backendName": "MAKO Renderer", "reason": "inherited_enable",
                                        "detail": "ENABLE_MAKO=1", "manifest": ""}
    assert isolation_plan(inherited, found, selected="mako")["environment"] == {}


def test_disable_gate_is_a_presence_check_and_filters_already_disabling_need_nothing(tmp_path):
    found = layers(tmp_path, AFMF)
    for value in ("0", "", "1"):
        assert isolation_plan({"AFMF_ENABLE": "1", "DISABLE_AFMF": value}, found)["environment"] == {}
    for mask in ("*afmf", "vk_layer_AFMF", "VK_LAYER_*", "*AFM*", "~implicit~", "~all~", "*"):  # "**": see 1.4.321 test
        assert isolation_plan({"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": mask}, found)["environment"] == {}, mask
    # ALLOW exempts it from the disable filter; the manifest gate is then needed.
    allowed = {"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": "~implicit~", "VK_LOADER_LAYERS_ALLOW": "~all~"}
    assert isolation_plan(allowed, found)["environment"] == {"DISABLE_AFMF": "1"}
    # ~explicit~ and a mid-string * do not disable an implicit layer.
    for mask in ("~explicit~", "VK_*_AFMF"):
        assert isolation_plan({"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": mask}, found)["environment"] == {"DISABLE_AFMF": "1"}


def test_explicit_instance_request_is_removed_exactly_and_bypasses_gates(tmp_path):
    found = layers(tmp_path, AFMF)
    env = {"VK_INSTANCE_LAYERS": "VK_LAYER_MESA_device_select:VK_LAYER_AFMF_monitor:VK_LAYER_AFMF:VK_LAYER_x",
           "DISABLE_AFMF": "1"}  # the gate does not stop VK_INSTANCE_LAYERS
    plan = isolation_plan(env, found)
    assert plan["environment"] == {"VK_INSTANCE_LAYERS": "VK_LAYER_MESA_device_select:VK_LAYER_AFMF_monitor:VK_LAYER_x"}
    assert [e["reason"] for e in plan["entries"]] == ["instance_layers"]
    # strstr quirk (1.4.363 only): see the 1.4.321 test for the gate added for the older loader.


def test_enable_filter_is_case_insensitive_and_the_gate_has_priority(tmp_path):
    found = layers(tmp_path, AFMF)
    for mask in ("vk_layer_afmf", "*AFMF", "VK_LAYER_A*", "~all~", "*"):
        plan = isolation_plan({"VK_LOADER_LAYERS_ENABLE": "VK_LAYER_user," + mask}, found)
        assert plan["environment"] == {"DISABLE_AFMF": "1"}, mask  # filter text kept intact
        assert plan["entries"][0]["reason"] == "enable_filter" and mask in plan["entries"][0]["detail"]
    for mask in ("~implicit~", "VK_*_AFMF"):  # match nothing in the enable filter
        assert isolation_plan({"VK_LOADER_LAYERS_ENABLE": mask}, found)["environment"] == {}


@pytest.mark.parametrize(("mask", "hit"), [
    ("VK_LAYER_AFMF", True), ("vk_layer_afmf", True), ("VK_LAYER_AF*", True), ("*_AFMF", True),
    ("*LAYER_A*", True), ("VK_*_AFMF", False), ("VK_LAYER_AFMF_x", False), ("~all~", True), ("*", True),
    ("**", True), ("~implicit~", False), ("~explicit~", False), ("", False),
])
def test_filter_semantics_match_the_loader(mask, hit):
    assert bool(matching_tokens("VK_LAYER_AFMF", parse_filters(mask))) is hit


def test_only_the_first_sixteen_filters_count():
    filters = ",".join(f"VK_LAYER_user{i}" for i in range(16)) + ",VK_LAYER_AFMF"
    assert not matching_tokens("VK_LAYER_AFMF", parse_filters(filters))
    assert parse_filters("~all~," + filters, disable=True)["all"]


def test_gateless_generator_uses_an_exact_disable_filter_without_losing_user_filters(tmp_path):
    spec = {"name": "VK_LAYER_AFMF", "enable": {"AFMF_ENABLE": "1"}, "disable": {"AFMF_OFF": "1"}}
    found = layers(tmp_path, spec)
    plan = isolation_plan({"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": "VK_LAYER_USER_blocked"}, found)
    assert plan["environment"] == {"VK_LOADER_LAYERS_DISABLE": "VK_LAYER_USER_blocked,VK_LAYER_AFMF"}
    full = ",".join(f"VK_LAYER_user{i}" for i in range(16))
    limited = isolation_plan({"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": full}, found)
    assert limited["environment"] == {} and "16 name filters" in limited["warnings"][0]
    broad = isolation_plan({"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_ENABLE": "VK_LAYER_*"}, found)
    assert broad["environment"] == {} and "unrelated layers" in broad["warnings"][0]


def test_explicit_generator_exact_enable_entry_is_removed_and_broad_force_is_reported(tmp_path):
    manifest(tmp_path / "explicit_layer.d", {"name": "VK_LAYER_AFMF", "enable": None, "disable": None})
    found = discover_layers({}, [tmp_path / "explicit_layer.d"])
    assert found[0]["kind"] == "explicit"
    assert isolation_plan({}, found)["environment"] == {}  # explicit layers never load by themselves
    plan = isolation_plan({"VK_LOADER_LAYERS_ENABLE": "VK_LAYER_user,vk_layer_afmf"}, found)
    assert plan["environment"] == {"VK_LOADER_LAYERS_ENABLE": "VK_LAYER_user"}
    broad = isolation_plan({"VK_LOADER_LAYERS_ENABLE": "~all~"}, found)
    assert broad["environment"] == {} and broad["warnings"]


def test_ungated_layer_is_guarded_as_automatic_load_not_generation(tmp_path):
    found = layers(tmp_path, LSFG)
    plan = isolation_plan({}, found)
    assert plan["environment"] == {"DISABLE_LSFGVK": "1"}
    assert plan["entries"][0]["reason"] == "automatic_load"


# 5. MAKO spatial scaling -------------------------------------------------------

def test_mako_spatial_scaling_is_not_a_frame_generator(tmp_path):
    found = layers(tmp_path, SPATIAL, MAKO)
    assert [layer["name"] for layer in found] == ["VK_LAYER_MAKO_render"]
    plan = isolation_plan({"ENABLE_MAKO_SPATIAL_SCALING": "1"}, found)
    assert plan["environment"] == {} and plan["entries"] == []
    from game_optimization_linux.services.host_service import HostServiceClient
    records = "".join(f"\x1e/l/{name}.json\n" + json.dumps({"layer": {"name": name}})
                      for name in ("VK_LAYER_MAKO_spatial_scaling", "VK_LAYER_MAKO_render"))
    client = HostServiceClient(environment={}, which=lambda name: "/bin/" + name,
                               command_runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=records, stderr=""))
    assert [x["name"] for x in client.frame_generation_external_layers()] == ["VK_LAYER_MAKO_render"]


# 4. Explicitly selected backend ------------------------------------------------

def test_selected_backend_activates_only_itself_and_keeps_other_settings(tmp_path, monkeypatch):
    import game_optimization_linux.services.frame_generation as fg
    found = layers(tmp_path / "layers", AFMF, MAKO)
    monkeypatch.setattr(fg, "discover_layers", lambda env: found)
    monkeypatch.setattr(fg, "FrameGenerationArtifacts", lambda *a: SimpleNamespace(
        status=lambda backend: {"installed": True, "version": fg.VERSIONS[backend]}, directory=lambda backend: tmp_path / backend))
    path = profile_path("42", tmp_path); path.parent.mkdir()
    state = {"backend": "afmf", "settings": {"mako": {"multiplier": 4}, "lsfg": {"multiplier": 3}}}
    path.write_text(json.dumps(state))
    afmf = resolve_launch("42", tmp_path, inherited={"ENABLE_MAKO": "1"})
    assert afmf.environment["AFMF_ENABLE"] == "1" and afmf.environment["DISABLE_MAKO"] == "1"
    assert "ENABLE_MAKO" not in afmf.environment and "LSFGVK_CONFIG" not in afmf.environment
    assert afmf.prefix == ["env", "-u", "DISABLE_AFMF"] and [e["backend"] for e in afmf.entries] == ["mako"]
    dll = tmp_path / "fixture.dll"; dll.write_bytes(b"fixture")  # validated fixture, not a runtime test
    state.update(backend="lsfg", dlls={"lsfg": str(dll)},
                 validation={"lsfg": {"sha256": sha256(dll.read_bytes()).hexdigest(), "componentVersion": fg.VERSIONS["lsfg"]}})
    config = path.parent / "frame-generation/lsfg.toml"; config.parent.mkdir(); config.write_text("fixture")
    path.write_text(json.dumps(state))
    lsfg = resolve_launch("42", tmp_path)
    assert lsfg.environment["LSFGVK_CONFIG"] == str(config) and "AFMF_ENABLE" not in lsfg.environment
    state["backend"] = "off"; path.write_text(json.dumps(state))
    assert launch_settings("42", tmp_path) == ({}, [])
    saved = json.loads(path.read_text())["settings"]
    assert saved["mako"]["multiplier"] == 4 and saved["lsfg"]["multiplier"] == 3


def test_selected_backend_conflicts_follow_loader_semantics():
    afmf = {"AFMF_ENABLE": "1"}
    assert selected_blocker("afmf", afmf) == ""
    assert selected_blocker("afmf", {**afmf, "DISABLE_AFMF": "1"}) == ""  # the wrapper unsets it
    assert "VK_LOADER_LAYERS_DISABLE" in selected_blocker("afmf", {**afmf, "VK_LOADER_LAYERS_DISABLE": "vk_layer_afmf"})
    assert "VK_LOADER_LAYERS_DISABLE" in selected_blocker("afmf", {**afmf, "VK_LOADER_LAYERS_DISABLE": "**"})
    assert selected_blocker("afmf", {**afmf, "VK_LOADER_LAYERS_DISABLE": "~implicit~", "VK_LOADER_LAYERS_ALLOW": "VK_LAYER_AFMF"}) == ""
    assert "VK_INSTANCE_LAYERS" in selected_blocker("afmf", {**afmf, "VK_INSTANCE_LAYERS": "VK_LAYER_AFMF_monitor"})
    assert "VK_IMPLICIT_LAYER_PATH" in selected_blocker("lsfg", {"VK_IMPLICIT_LAYER_PATH": "/only/here"})


# Discovery search paths --------------------------------------------------------

def test_search_directories_follow_the_loader_order():
    env = {"HOME": "/h", "VK_ADD_IMPLICIT_LAYER_PATH": "/add"}
    assert real_search_directories(env) == [
        "/add", "/h/.config/vulkan/implicit_layer.d", "/etc/xdg/vulkan/implicit_layer.d", "/etc/vulkan/implicit_layer.d",
        "/h/.local/share/vulkan/implicit_layer.d", "/usr/local/share/vulkan/implicit_layer.d", "/usr/share/vulkan/implicit_layer.d"]
    assert real_search_directories({**env, "VK_IMPLICIT_LAYER_PATH": "/only"}) == ["/only"]  # ADD is ignored
    xdg = {"HOME": "/h", "XDG_DATA_HOME": "/d", "XDG_DATA_DIRS": "/x:/y", "XDG_CONFIG_DIRS": "/c"}
    assert real_search_directories(xdg)[1:3] == ["/c/vulkan/implicit_layer.d", "/etc/vulkan/implicit_layer.d"]
    assert real_search_directories(xdg)[-3:] == ["/d/vulkan/implicit_layer.d", "/x/vulkan/implicit_layer.d", "/y/vulkan/implicit_layer.d"]
    # A Flatpak sandbox environment (GUI preview) is never the game's XDG view.
    assert "/d/vulkan/implicit_layer.d" not in real_search_directories({**xdg, "FLATPAK_ID": "io.example"})


def test_relative_absolute_and_bare_library_paths(tmp_path):
    directory = manifest(tmp_path / "implicit_layer.d", AFMF)
    data = json.loads((directory / "VK_LAYER_AFMF.json").read_text())
    data["layer"]["library_path"] = "libafmf.so"  # resolved by the dynamic linker
    (directory / "VK_LAYER_AFMF.json").write_text(json.dumps(data))
    assert discover_layers({}, [directory])[0]["libraryPresent"] is None
    data["layer"]["library_path"] = str(tmp_path / "missing.so")
    (directory / "VK_LAYER_AFMF.json").write_text(json.dumps(data))
    assert discover_layers({}, [directory])[0]["libraryPresent"] is False


# Loader 1.4.321 (Freedesktop 25.08 runtime) differs; verified 26.10.2026 --------

def test_plans_are_safe_for_both_verified_loader_versions(tmp_path):
    found = layers(tmp_path, AFMF)
    from game_optimization_linux.services.frame_generation_isolation import load_decision
    layer = found[0]
    # "**" disables everything on 1.4.363 but nothing on 1.4.321: the gate is still added.
    env = {"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": "**"}
    assert not load_decision(layer, env, legacy=False) and load_decision(layer, env, legacy=True)
    assert isolation_plan(env, found)["environment"] == {"DISABLE_AFMF": "1"}
    # 1.4.321 has no strstr skip for VK_INSTANCE_LAYERS.
    env = {"AFMF_ENABLE": "1", "VK_INSTANCE_LAYERS": "VK_LAYER_AFMF_monitor"}
    assert not load_decision(layer, env, legacy=False) and load_decision(layer, env, legacy=True)
    assert isolation_plan(env, found)["environment"] == {"DISABLE_AFMF": "1"}
    # A selected generator must load on both versions.
    assert "VK_LOADER_LAYERS_DISABLE" in selected_blocker("afmf", {"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": "~implicit~", "VK_LOADER_LAYERS_ALLOW": "**"})
    assert selected_blocker("afmf", {"AFMF_ENABLE": "1", "VK_LOADER_LAYERS_DISABLE": "~implicit~", "VK_LOADER_LAYERS_ALLOW": "VK_LAYER_AFMF"}) == ""
