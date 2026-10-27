"""Measured optimization: candidates, restore points, evidence scope, launcher matrix."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from game_optimization_linux.models import GameOptimizationProfile, Launcher
from game_optimization_linux.services import measured_optimization as mo
from game_optimization_linux.services.measured_optimization.store import MeasuredOptimizationStore, RestoreConflict
from tests.test_measured_optimization_core import metrics

DISPLAY = {"width": 2560, "height": 1440, "refresh": 165}


def ctx(**values):
    base = dict(launcher="steam_native", profile=GameOptimizationProfile.default("1"), goal="maximum_performance",
                gamemode_available=True, gamescope_available=True, gamescope_filters=("linear", "fsr"), display=DISPLAY)
    base.update(values)
    return mo.CandidateContext(**base)


def rows_by_id(context, tested=None):
    return {row["id"]: row for row in mo.evaluate(context, tested)}


def test_every_candidate_declares_the_contract() -> None:
    for item in mo.REGISTRY:
        declared = item.declaration()
        for field in ("settings", "prerequisites", "conflicts", "launchers", "expected", "imageQuality",
                      "renderedFps", "restore", "evidenceSource"):
            assert declared[field] not in (None, "", [], {}), (item.id, field)
        assert declared["generatedFrames"] is False
    forbidden = ("frame_generation", "reshade", "vkbasalt", "optiscaler", "dlss", "fsr4", "engine_ini", "sysctl")
    assert not any(word in item.id for item in mo.REGISTRY for word in forbidden)


def test_one_proposal_with_exact_changes_and_goal_ordering() -> None:
    rows = mo.evaluate(ctx())
    proposal = mo.proposal(rows)
    assert proposal["id"] == "gamemode"
    assert proposal["changes"] == [{"target": "optimization", "setting": "gamemode_enabled", "before": False,
                                    "after": True, "label": "GameMode"}]
    on = rows_by_id(ctx(profile=replace(GameOptimizationProfile.default("1"), gamemode_enabled=True)))
    assert on["gamemode"]["changes"][0]["after"] is False  # baseline already on: test without it
    stable = mo.proposal(mo.evaluate(ctx(goal="stable_image")))
    assert stable["id"] == "gamescope_native"
    assert {c["setting"]: c["after"] for c in stable["changes"]}["target_fps_mode"] == "unlimited"


def test_unavailable_reasons_and_explicit_experiments(tmp_path: Path) -> None:
    rows = rows_by_id(ctx(gamemode_available=False, gamescope_available=False))
    assert rows["gamemode"]["applicability"]["code"] == "tool_unavailable"
    assert rows["fps_cap"]["applicability"]["code"] == "requires_gamescope"
    assert rows["gpu_memory_priority"]["applicability"]["code"] == "explicit_only"
    explicit = rows_by_id(ctx(explicit_experiments=True, api="Direct3D 12", api_confidence=0.7))
    assert explicit["vkd3d_no_upload_hvv"]["applicability"]["code"] == "api_not_d3d12"
    assert explicit["proton_no_ntsync"]["applicability"]["code"] == "not_proton"
    assert explicit["gpu_memory_priority"]["applicability"]["code"] == "gpu_memory_not_ready"
    gpu = rows_by_id(ctx(explicit_experiments=True, gpu_memory={"ready": True, "region": "drm/0/vram",
                                                                "baselineProtection": "unprotected"}))
    assert gpu["gpu_memory_priority"]["applicability"]["available"]
    external = rows_by_id(ctx(explicit_experiments=True, gpu_memory={"ready": True, "region": "drm/0/vram",
                                                                     "baselineProtection": "protected"}))
    assert external["gpu_memory_priority"]["applicability"]["code"] == "external_protection"
    override = rows_by_id(ctx(profile=replace(GameOptimizationProfile.default("1"), manual_overrides={"gamemode": True})))
    assert override["gamemode"]["applicability"]["code"] == "manual_override"
    tested = rows_by_id(ctx(), {"gamemode": "revert"})
    assert tested["gamemode"]["applicability"]["code"] == "tested_in_scope"


def test_measured_fps_cap_and_quality_consent(tmp_path: Path) -> None:
    gamescope = replace(GameOptimizationProfile.default("1"), gamescope_enabled=True, gamescope_mode="native",
                        target_fps_mode="automatic", target_fps=165, gamescope_input_width=2560,
                        gamescope_input_height=1440, gamescope_output_width=2560, gamescope_output_height=1440)
    baseline = metrics(tmp_path, "b", 10.0)  # 100 FPS
    cap = rows_by_id(ctx(goal="balanced", profile=gamescope, baseline=baseline))["fps_cap"]
    assert {c["setting"]: c["after"] for c in cap["changes"]} == {"target_fps_mode": "manual", "target_fps": 90}
    floor = rows_by_id(ctx(goal="low_power", profile=gamescope, baseline=baseline, fps_floor=55))["fps_cap"]
    assert {c["setting"]: c["after"] for c in floor["changes"]}["target_fps"] == 60
    capped = replace(gamescope, target_fps_mode="manual", target_fps=60)
    assert rows_by_id(ctx(goal="balanced", profile=capped, baseline=baseline))["fps_cap"]["applicability"]["code"] == "already_capped"
    upscale = rows_by_id(ctx(profile=gamescope, explicit_experiments=True))["gamescope_fsr_upscale"]
    assert upscale["applicability"]["code"] == "quality_consent_required"
    allowed = rows_by_id(ctx(profile=gamescope, explicit_experiments=True, quality_consent=True))["gamescope_fsr_upscale"]
    assert {c["setting"]: c["after"] for c in allowed["changes"]} == {
        "gamescope_input_width": 1970, "gamescope_input_height": 1108, "gamescope_filter": "fsr"}
    assert allowed["imageQuality"] == "lossy_upscaling"


def test_launcher_matrix_and_candidate_support() -> None:
    def game(launcher, variant="native", source="Steam", exe=""):
        return SimpleNamespace(launcher=launcher, launcher_variant=variant, data_source=source,
                               executable_path=exe, library_available=True)
    assert mo.launcher_kind(game(Launcher.STEAM)) == ("steam_native", "")
    assert mo.launcher_kind(game(Launcher.STEAM), flatpak_steam=True) == ("steam_flatpak", "steam_flatpak")
    assert mo.launcher_kind(game(Launcher.HEROIC, "flatpak")) == ("heroic_flatpak", "")
    assert mo.launcher_kind(game(Launcher.HEROIC)) == ("heroic_native", "")
    assert mo.launcher_kind(game(Launcher.LUTRIS)) == ("lutris", "lutris")
    assert mo.launcher_kind(game(Launcher.MANUAL, source="local")) == ("local_native", "")
    assert mo.launcher_kind(game(Launcher.MANUAL, source="local"), in_flatpak=True)[1] == "local_in_flatpak"
    assert mo.launcher_kind(game(Launcher.MANUAL, source="local"), local_wine=True)[1] == "local_wine"
    assert mo.launcher_kind(game(Launcher.MANUAL, source="Manual")) == ("manual", "manual")
    for kind, code in mo.MATRIX:
        row = mo.support(kind, code)
        assert row["supported"] is (not code) and (row["reason"] if code else row["recording"])
    heroic = rows_by_id(ctx(launcher="heroic_flatpak", explicit_experiments=True))
    assert heroic["gamemode"]["applicability"]["available"]
    assert heroic["gpu_memory_priority"]["applicability"]["code"] == "launcher_unsupported"
    assert heroic["vkd3d_no_upload_hvv"]["applicability"]["code"] == "launcher_unsupported"
    not_applied = rows_by_id(ctx(launcher="heroic_native", launcher_integration_active=False))
    assert not_applied["gamemode"]["applicability"]["code"] == "integration_not_applied"


def test_activation_verification() -> None:
    gm = {"id": "gamemode", "changes": [{"setting": "gamemode_enabled", "after": True}]}
    report = {"baselineSessionId": "s1", "wrappers": ["gamemode"]}
    assert mo.verify_activation(gm, launcher="steam_native", report=report, session_id="s1")["verified"]
    assert not mo.verify_activation(gm, launcher="steam_native", report=report, session_id="s2")["verified"]
    assert not mo.verify_activation(gm, launcher="steam_native", report={**report, "wrappers": []}, session_id="s1")["verified"]
    cap = {"id": "fps_cap", "changes": [{"setting": "target_fps", "after": 90}]}
    assert mo.verify_activation(cap, launcher="steam_native", session_id="s1",
                                report={"baselineSessionId": "s1", "fpsLimitOwner": "gamescope", "fpsLimit": 90})["verified"]
    gpu = {"id": "gpu_memory_priority", "changes": []}
    live = mo.verify_activation(gpu, launcher="steam_native", report={"baselineSessionId": "s1"}, session_id="s1",
                                gpu_summary={"priorityRequested": True, "samples": 100, "protectedSamples": 60})
    assert live["verified"] is False and "60 of 100" in live["reason"]
    heroic = mo.verify_activation(gm, launcher="heroic_flatpak", report=None, session_id="s1",
                                  launcher_status={"state": "applied"})
    assert heroic == {**heroic, "verified": True, "level": "configured"}


def test_restore_point_is_byte_exact_and_refuses_outside_changes(tmp_path: Path) -> None:
    store = MeasuredOptimizationStore(tmp_path / "state")
    profile = tmp_path / "games" / "1" / "optimization.json"
    profile.parent.mkdir(parents=True)
    original = b'{"a": 1,  "spacing kept": true}\n'
    profile.write_bytes(original)
    missing = tmp_path / "games" / "1" / "proton-tweaks.json"
    entries = store.create_restore_point("1", "w1", {"optimization.json": profile, "proton-tweaks.json": missing})
    profile.write_bytes(b'{"changed": 1}')
    missing.write_text("{}")
    sealed = store.seal(entries)
    store.restore("1", "w1", sealed)
    assert profile.read_bytes() == original and not missing.exists()
    store.restore("1", "w1", sealed)  # repeated restore is harmless
    profile.write_bytes(b'{"changed": 1}')
    profile.write_bytes(b'{"user edited": 2}')
    with pytest.raises(RestoreConflict):
        store.restore("1", "w1", sealed)
    assert profile.read_bytes() == b'{"user edited": 2}'


def test_failed_atomic_write_keeps_previous_document(tmp_path: Path) -> None:
    from game_optimization_linux.services.measured_optimization import store as module
    path = tmp_path / "doc.json"
    path.write_bytes(b"old")

    def broken(_source, _target):
        raise OSError("disk full")

    with pytest.raises(OSError):
        module.atomic_write(path, b"new", replace=broken)
    assert path.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["doc.json"]


def test_workflow_state_machine(tmp_path: Path) -> None:
    store = MeasuredOptimizationStore(tmp_path)
    workflow = store.start("1", "g1", {"id": "balanced"})
    with pytest.raises(RuntimeError):
        store.start("1", "g1", {"id": "balanced"})
    with pytest.raises(RuntimeError):
        store.update("1", workflow["id"], "result_ready")
    store.update("1", workflow["id"], "baseline_recording")
    store.update("1", workflow["id"], "cancelled")
    assert store.start("1", "g1", {"id": "low_power"})["goal"]["id"] == "low_power"
    assert store.load("2")["workflow"] == {}


SCOPE = {"key": "1", "gpu": "RX 9070", "cpu": "R7", "vramGb": 16.0, "build": "100", "driver": "Mesa 25.2",
         "runtime": "GE 10", "resolution": "2560x1440@165", "api": "Direct3D 12"}


def test_evidence_scope_and_invalidation() -> None:
    assert mo.scope_match(SCOPE, dict(SCOPE))["state"] == "current"
    assert mo.scope_match(SCOPE, {**SCOPE, "key": "2"})["state"] == "other_game"
    assert mo.scope_match(SCOPE, {**SCOPE, "gpu": "RTX"})["state"] == "hardware_changed"
    assert mo.scope_match(SCOPE, {**SCOPE, "build": "101"})["state"] == "build_changed"
    assert mo.scope_match(SCOPE, {**SCOPE, "driver": "Mesa 25.3"})["state"] == "software_changed"
    assert mo.scope_match(SCOPE, {**SCOPE, "api": "Unknown"})["state"] == "unverified_scope"
    both = mo.scope_match({**SCOPE, "runtime": "Unknown"}, {**SCOPE, "runtime": "Unknown"})
    assert both["state"] == "current" and both["unprovenFields"] == ["runtime"]
    kept = {"goal": "balanced", "verified": True, "decision": "keep", "userAction": "kept", "candidateId": "gamemode",
            "scope": SCOPE, "changes": [{"target": "optimization", "setting": "gamemode_enabled", "after": True}]}
    history = [kept]
    assert mo.accepted_results(history, SCOPE, "balanced") == {"gamemode_enabled": True}
    assert mo.accepted_results(history, SCOPE, "low_power") == {}
    assert mo.accepted_results(history, {**SCOPE, "key": "2"}, "balanced") == {}  # Jedi is not GTA
    assert mo.accepted_results(history, {**SCOPE, "driver": "Mesa 26"}, "balanced") == {}
    assert mo.accepted_results([{**kept, "verified": False}], SCOPE, "balanced") == {}
    assert mo.accepted_results([{**kept, "decision": "revert"}], SCOPE, "balanced") == {}
    newer_revert = {**kept, "decision": "revert", "userAction": "reverted"}
    assert mo.accepted_results([kept, newer_revert], SCOPE, "balanced") == {}
    assert mo.tested_candidates(history, SCOPE, "balanced") == {"gamemode": "keep"}
    assert mo.tested_candidates(history, {**SCOPE, "build": "200"}, "balanced") == {}
