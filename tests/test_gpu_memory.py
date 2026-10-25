import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from game_optimization_linux.services import gpu_memory as gm
from game_optimization_linux.services import gpu_memory_host as host
from game_optimization_linux.runner import _host_launcher_payload


def test_pressure_preserves_existing_flags_and_detects_changed_proton(tmp_path, monkeypatch):
    profile = {"priority": False, "pressure": "upload-system-memory", "toolHash": "same",
               "effectiveApi": "d3d12", "dedicatedRebar": True}
    monkeypatch.setattr(gm, "load_profile", lambda *_: profile)
    monkeypatch.setattr(gm, "vkd3d_tool", lambda *_: {"hash": "same", "noUploadHvv": True})
    assert gm.pressure_environment("1", tmp_path, {"VKD3D_CONFIG": "dxr;foo"}, []) == {
        "VKD3D_CONFIG": "dxr,foo,no_upload_hvv"}
    assert gm.pressure_environment("1", tmp_path, {"VKD3D_CONFIG": "no_upload_hvv,foo"}, []) == {
        "VKD3D_CONFIG": "no_upload_hvv,foo"}
    profile["toolHash"] = "old"
    with pytest.raises(ValueError, match="changed"):
        gm.pressure_environment("1", tmp_path, {}, [])


def test_default_does_not_touch_independently_set_option(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "load_profile", lambda *_: {"pressure": "default"})
    assert gm.pressure_environment("1", tmp_path, {"VKD3D_CONFIG": "no_upload_hvv"}, []) == {}


def test_host_launcher_preserves_exact_command_and_env_with_scope(tmp_path):
    plan = SimpleNamespace(command=("env", "EXISTING=kept", "game", "a b"),
        steam_command=("game", "a b"), wrapper_environment_removed=(), wrapper_environment_overrides={})
    payload = _host_launcher_payload(plan, {"SAVED": "some value"}, gpu_prefix=("python3", "/scope bridge", "run", "--"))
    assert b"export SAVED" in payload
    assert b"exec python3 '/scope bridge' run -- env EXISTING=kept \"$@\"" in payload


def test_snapshot_requires_every_ancestor_region_and_detects_pid_identity(tmp_path, monkeypatch):
    cg = tmp_path / "cgroup"
    child = cg / "user.slice" / "app.slice" / "app-gameopti-a-b.scope"
    child.mkdir(parents=True)
    (cg / "dmem.capacity").write_text("drm/pci/vram 8000\n")
    for group in (child, child.parent, child.parent.parent):
        (group / "dmem.low").write_text("drm/pci/vram 8000\n")
    monkeypatch.setattr(host, "CG", cg)
    monkeypatch.setattr(host, "cgroup", lambda _pid: "/user.slice/app.slice/app-gameopti-a-b.scope")
    assert host.snapshot(999999, "drm/pci/vram")["lowChainVerified"]
    assert not host.snapshot(999999, "drm/pci/vram")["protectionVerified"]  # no GPU allocations
    (child.parent / "dmem.low").write_text("drm/pci/vram 0\n")
    assert not host.snapshot(999999, "drm/pci/vram")["lowChainVerified"]
    assert not host.snapshot(999999, "other-gpu/vram")["lowChainVerified"]


def test_restore_keeps_independent_services_and_changed_limits(tmp_path, monkeypatch):
    cg = tmp_path / "cg"; cg.mkdir()
    low = cg / "dmem.low"; low.write_text("region 999\n")
    record = tmp_path / "setup.json"
    record.write_text(json.dumps({"uid": 1000, "user": {"active": "active", "enabled": "enabled"},
        "system": {"active": "active", "enabled": "enabled"},
        "limits": {"dmem.low": {"region": 10}}, "written": {"dmem.low": {"region": 8000}}}))
    calls = []
    monkeypatch.setattr(host, "CG", cg); monkeypatch.setattr(host, "RECORD", record)
    monkeypatch.setattr(host.os, "getuid", lambda: 0)
    monkeypatch.setenv("PKEXEC_UID", "1000")
    monkeypatch.setattr(host, "command", lambda argv, **_kw: calls.append(argv))
    monkeypatch.setattr(host, "user_command", lambda uid, argv: calls.append(argv))
    monkeypatch.setattr(host, "unit_state", lambda *args: {"active": "inactive"})
    assert host.setup(restore=True)["restored"]
    assert not calls
    assert host.pairs(low) == {"region": 999}


def test_restart_repairs_zero_ancestor_but_preserves_custom_protection(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "CG", tmp_path)
    (tmp_path / "dmem.capacity").write_text("gpu/vram 8000\n")
    groups = ("user.slice", "user.slice/user-1000.slice", "user.slice/user-1000.slice/user@1000.service",
              "user.slice/user-1000.slice/user@1000.service/app.slice")
    for group in groups:
        path = tmp_path / group; path.mkdir(exist_ok=True)
        (path / "dmem.low").write_text("gpu/vram 0\n")
    (tmp_path / groups[1] / "dmem.low").write_text("gpu/vram 1234\n")
    state = {"limits": {}, "written": {}}
    assert len(host.prepare_ancestors(1000, state)) == 3
    assert host.pairs(tmp_path / "user.slice/dmem.low") == {"gpu/vram": 8000}
    assert host.pairs(tmp_path / groups[1] / "dmem.low") == {"gpu/vram": 1234}
    assert not host.prepare_ancestors(1000, state)
    assert state["limits"]["user.slice/dmem.low"] == {"gpu/vram": 0}


def test_no_root_game_or_arbitrary_scope_write(monkeypatch):
    monkeypatch.setattr(host.os, "getuid", lambda: 0)
    with pytest.raises(ValueError, match="unprivileged"):
        host.enter(SimpleNamespace())
    with pytest.raises(ValueError, match="unprivileged"):
        host.run_scope(SimpleNamespace())


def test_invalid_profile_fails_closed_without_overwriting(tmp_path):
    path = gm.profile_path("123", tmp_path); path.parent.mkdir()
    path.write_text('{"pressure":"invented"}')
    with pytest.raises(ValueError):
        gm.load_profile("123", tmp_path)
    assert path.read_text() == '{"pressure":"invented"}'


def test_bitness_and_actual_proton_version_are_checked(tmp_path):
    proton = tmp_path / "Proton" / "proton"; proton.parent.mkdir(); proton.touch()
    base = proton.parent / "files/lib/wine/vkd3d-proton"
    for bits in ("x86_64", "i386"):
        folder = base / f"{bits}-windows"; folder.mkdir(parents=True)
        (folder / "d3d12core.dll").write_bytes(b"test no_upload_hvv\0")
    (base / "version").write_text("pinned-test-version")
    assert gm.vkd3d_tool(["/unrelated/proton"] ) == {}
    assert gm.vkd3d_tool([str(proton)])["noUploadHvv"]
    (base / "i386-windows/d3d12core.dll").write_bytes(b"unsupported 32-bit build")
    assert not gm.vkd3d_tool([str(proton)])["noUploadHvv"]


def test_metrics_wrapper_is_opt_in_and_off_is_observation_only(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "ensure_host_bridge", lambda: Path("/host bridge.py"))
    assert gm.launch_prefix("1", tmp_path, metrics=tmp_path / "metrics") == []
    path = gm.profile_path("1", tmp_path); path.parent.mkdir()
    path.write_text('{"priority":false,"pressure":"default","region":"gpu/vram"}')
    args = gm.launch_prefix("1", tmp_path, metrics=tmp_path / "metrics")
    assert args[args.index("--protect") + 1] == "0"
    assert "--metrics" in args
    assert gm.launch_prefix("1", tmp_path) == []


def test_controller_status_queries_do_not_start_operations_or_duplicate(monkeypatch):
    from concurrent.futures import Future
    from game_optimization_linux.controllers import gpu_memory_controller as controller
    class Executor:
        def __init__(self, **kwargs): self.calls = []
        def submit(self, *args):
            future = Future(); self.calls.append((args, future)); return future
    monkeypatch.setattr(controller, "ThreadPoolExecutor", Executor)
    app = SimpleNamespace(_resolve_game=lambda *args, **kw: "game")
    service = SimpleNamespace(status=lambda _: {}, apply=lambda *_: {}, setup=lambda _: {}, restore_system=lambda _: {})
    ctrl = controller.GpuMemoryController(app, service)
    for _ in range(20):
        assert not ctrl.status("1")["busy"]
    assert len(ctrl.executor.calls) == 1
    assert ctrl.executor.calls[0][0][0] == service.status
    assert ctrl.submit("1", "apply", {"priority": False})
    assert ctrl.status("1")["busy"]
    assert len(ctrl.executor.calls) == 2
    ctrl.executor.calls[0][1].set_result({"state": "off"})
    ctrl.executor.calls[1][1].set_result({"state": "configured"})
    assert ctrl.status("1")["state"] == "configured"
    assert not ctrl.status("1")["busy"]


def test_launcher_flatpak_is_not_falsely_supported():
    game = SimpleNamespace(launcher=SimpleNamespace(value="Heroic"), launcher_variant="flatpak")
    assert not gm.GpuMemoryService.supported_launcher(game)
    game.launcher_variant = "native"
    assert gm.GpuMemoryService.supported_launcher(game)
    game.launcher = SimpleNamespace(value="Steam"); game.launcher_variant = "flatpak"
    assert not gm.GpuMemoryService.supported_launcher(game)


def test_new_translations_complete_only_feature_scope():
    import xml.etree.ElementTree as ET
    from game_optimization_linux.config import TRANSLATIONS_DIR
    for code in ("en", "pl", "es"):
        contexts = ET.parse(TRANSLATIONS_DIR / f"game_optimization_{code}.ts").findall(".//context")
        messages = next(c for c in contexts if c.findtext("name") == "GpuMemoryCard").findall("message")
        assert len(messages) > 30
        import re
        from game_optimization_linux.config import QML_DIR
        sources = set(re.findall(r'qsTr\("([^"\n]+)"\)', (QML_DIR / "pages/details/GpuMemoryCard.qml").read_text()))
        assert sources <= {m.findtext("source") for m in messages}
        assert all(m.find("translation").get("type") != "unfinished" and m.findtext("translation") for m in messages)


def test_memory_profile_has_its_own_planner_path_and_gamescope_boundary():
    from dataclasses import replace
    from game_optimization_linux.models import GameOptimizationProfile
    from game_optimization_linux.services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolAvailability, parse_gamescope_help
    profile = replace(GameOptimizationProfile.default("123"), gamescope_enabled=True,
                      gamescope_mode="native", gamemode_enabled=False)
    plan = OptimizationLaunchPlanner().build(profile, ["proton", "run", "game.exe"],
        gamemode=RuntimeToolAvailability("GameMode", False),
        gamescope=RuntimeToolAvailability("Gamescope", True, "/usr/bin/gamescope", "3.16.30",
            parse_gamescope_help((Path(__file__).parent / "fixtures/gamescope/help-3.16.30.txt").read_text())[0]),
        gpu_memory_environment={"VKD3D_CONFIG": "dxr,no_upload_hvv"},
        post_processing_environment={"ENABLE_VKBASALT": "1"})
    assert plan.environment["VKD3D_CONFIG"] == "dxr,no_upload_hvv"
    assert "VKD3D_CONFIG" in plan.wrapper_environment_removed
    assert plan.command.index("VKD3D_CONFIG=dxr,no_upload_hvv") > plan.command.index("/usr/bin/gamescope")
    assert "ENABLE_VKBASALT=1" in plan.command


def test_native_launcher_apply_failure_restores_only_memory_profile(tmp_path):
    post = SimpleNamespace(root=tmp_path, optiscaler=SimpleNamespace(_process_detector=lambda _: []))
    service = gm.GpuMemoryService(post)
    state = {"ready": True, "system": {"regions": {"gpu/vram": 8000}, "readyRegions": ["gpu/vram"]},
             "detectedApi": "vulkan", "pressureSupported": False, "dedicatedRebar": True, "tool": {}}
    service.status = lambda _: state
    def fail(_): raise ValueError("Foreign wrapper conflict")
    service.apply_launcher = fail
    game = SimpleNamespace(profile_key="123", install_path=tmp_path,
                           launcher=SimpleNamespace(value="Heroic"), launcher_variant="native")
    path = gm.profile_path("123", tmp_path); path.parent.mkdir()
    original = '{"priority":false,"pressure":"default"}\n'; path.write_text(original)
    unrelated = path.with_name("post-processing.json"); unrelated.write_text("owner's preset")
    with pytest.raises(ValueError, match="Foreign wrapper"):
        service.apply(game, {"priority": True, "region": "gpu/vram"})
    assert path.read_text() == original
    assert unrelated.read_text() == "owner's preset"
    assert callable(service.setup) and callable(service.restore_system)


def test_sampled_spikes_are_distinct_from_all_frames(tmp_path):
    log = tmp_path / "MangoHud.csv"
    log.write_text("metadata\nfps,frametime\n60,16.6\n60,16.6\n10,100\n")
    assert gm.sampled_hitches(log) == {"samples": 3, "spikes": 1, "thresholdMs": 50}
    assert gm.sampled_hitches(tmp_path / "missing") is None


def test_scope_lifetime_uses_cgroup_population_not_only_launcher_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "CG", tmp_path)
    unit = "app-gameopti-abc-def.scope"
    group = tmp_path / "app.slice" / unit; group.mkdir(parents=True)
    event = group / "cgroup.events"; event.write_text("populated 1\nfrozen 0\n")
    output = tmp_path / "session.json"; output.write_text(json.dumps({"cgroup": "/app.slice/" + unit}))
    assert host.scope_populated(output, unit)
    assert not host.scope_populated(output, "different.scope")
    event.write_text("populated 0\nfrozen 0\n")
    assert not host.scope_populated(output, unit)


def test_manual_game_keeps_existing_identity_and_default_launch(tmp_path):
    game = SimpleNamespace(profile_key="", id="manual-d86d3413-433b-4ac0-86f8-8b17e8d2b447",
                           launcher=SimpleNamespace(value="Manual"), data_source="Manual")
    key = gm.game_key(game)
    assert key.startswith("local-") and len(key) == 30
    assert gm.launch_prefix(key, tmp_path) == []
    assert gm.game_key(game) == key
    assert game.profile_key == ""


def test_status_consumes_host_service_process_id_contract(tmp_path, monkeypatch):
    executable = tmp_path / "game.exe"
    post = SimpleNamespace(root=tmp_path, executable=lambda game: executable,
                           host=SimpleNamespace(game_process_modules=lambda name: ({"processId": 12345, "modules": [str(executable)]},)))
    game = SimpleNamespace(profile_key="123", install_path=tmp_path, launcher=SimpleNamespace(value="Steam"), launcher_variant="native")
    system = {"gpus": [], "regions": {"gpu/vram": 8000}, "readyRegions": ["gpu/vram"], "delegated": True,
              "systemBooster": {"active": "active"}, "userBooster": {"active": "active"}}
    def call(*args, **kwargs):
        if args[0] == "probe": return system
        assert args[2] == "12345"
        return {"pid": 12345, "cgroup": "/external.scope", "protectionVerified": True,
                "vkd3dNoUploadHvv": False}
    monkeypatch.setattr(gm, "host_call", call)
    result = gm.GpuMemoryService(post).status(game)
    assert result["gameProcesses"][0]["pid"] == 12345
    assert result["runtimeProtectionVerified"]
    assert result["state"] == "off"  # independent protection is not activation of our profile
    assert result["runtimeState"] == "protected"
    assert result["protectionOrigin"] == "independent"
    assert result["observedAt"]


def test_host_scope_accepts_existing_encoded_launcher_keys(monkeypatch):
    monkeypatch.setattr(host.sys, "argv", ["bridge", "run", "--key", "heroic-epic-name%20extra",
                                         "--output", "/tmp/gpu-session.json", "--", "/bin/true"])
    def run(args):
        assert args.key == "heroic-epic-name%20extra"
        return 0
    monkeypatch.setattr(host, "run_scope", run)
    assert host.main() == 0


@pytest.mark.parametrize("protection,expected", [
    (("protected", "protected"), False),  # Gamescope protects both despite requested Off/On
    (("unknown", "protected"), False),  # legacy/absent target evidence is not a baseline
    (("unprotected", "protected"), True),
])
def test_comparison_uses_actual_game_protection_not_requested_switch(tmp_path, monkeypatch, protection, expected):
    from game_optimization_linux.services.performance_session import BaselineSessionRepository
    measurements = {}
    for index, (slot, observed) in enumerate(zip(("before", "after"), protection)):
        folder = tmp_path / slot; (folder / "logs").mkdir(parents=True)
        path = folder / "logs/MangoHud.csv"; path.write_text("fps,frametime\n60,16\n")
        summary = {"key": "123", "samples": 20, "createdAt": slot, "exitCode": 0,
                   "priorityRequested": bool(index), "vkd3dNoUploadHvv": False,
                   "targetExecutable": "/game/JediSurvivor.exe", "targetSamples": 20,
                   "targetProtection": {observed: 20}, "targetNoUploadHvv": False}
        if observed == "unknown":
            summary.pop("targetProtection")
        (folder / "gpu-memory-summary.json").write_text(json.dumps(summary))
        measurements[slot] = SimpleNamespace(source_path=str(path), average_fps=40 + 10 * index,
            p95_frametime_ms=30 - 5 * index, p99_frametime_ms=40 - 5 * index,
            samples=20, available=True)
    monkeypatch.setattr(BaselineSessionRepository, "load_measurement", lambda self, key, *, slot: measurements[slot])
    service = gm.GpuMemoryService(SimpleNamespace(root=tmp_path))
    result = service.measurements(SimpleNamespace(profile_key="123"), True)
    assert result["matched"] is expected
    assert result["measuredImprovement"] is expected
    assert result["sameProtectionState"] is (protection[0] == protection[1])


def test_protection_absence_is_distinct_from_missing_evidence_and_matches_target(monkeypatch):
    sample = {"protectionVerified": False, "drmClients": {"gpu:1": {}},
              "chain": [{"low": {"gpu/vram": 0}}]}
    assert host.protection_observation(sample, "gpu/vram") == "unprotected"
    assert host.protection_observation(sample, "other/vram") == "unknown"
    sample["drmClients"] = {}
    assert host.protection_observation(sample, "gpu/vram") == "unknown"
    monkeypatch.setattr(host, "read", lambda path: "100-200 r-xp 0 08:01 123 /game/Jedi Survivor/JediSurvivor.exe\n")
    assert host.target_process(10, "/game/Jedi Survivor/JediSurvivor.exe")
    assert not host.target_process(10, "/game/Jedi Survivor/JediSurvivor.exe.backup")
    assert not host.target_process(10, "")
