"""Measured optimization: pure backend pieces (synthetic files only)."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.services.measured_optimization import comparison as cmp
from game_optimization_linux.services.measured_optimization import fingerprint as fp
from game_optimization_linux.services.measured_optimization.metrics import SessionMetrics, session_metrics
from game_optimization_linux.services.optimization_advisor import OptimizationAdvisor

HEADER = "fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,swap_used,process_rss,cpu_mhz,elapsed"


def write_log(path: Path, frametimes, *, seconds=120.0, gpu_power=150.0, cpu_power=40.0, stutter_every=0) -> Path:
    count = int(seconds * 10)
    lines = ["os,cpu,gpu,ram,kernel,driver,cpuscheduler", "Linux,CPU,GPU,32,6.x,Mesa,", HEADER]
    for index in range(count):
        ft = frametimes(index) if callable(frametimes) else frametimes
        if stutter_every and index % stutter_every == 0 and index:
            ft *= 3
        power = gpu_power(index) if callable(gpu_power) else gpu_power
        lines.append(f"{1000/ft:.2f},{ft:.3f},40,{cpu_power},95,60,70,2500,1200,6.5,{power},12.0,0,3.1,4200,{int(index*1e8)}")
    path.write_text("\n".join(lines) + "\n")
    return path


def metrics(tmp_path: Path, name: str, frametime: float, **kwargs) -> SessionMetrics:
    return session_metrics(write_log(tmp_path / f"{name}.csv", frametime, **kwargs))


# Profiles -----------------------------------------------------------------
def test_quiet_profile_migrates_to_low_power_without_losing_settings() -> None:
    saved = {**GameOptimizationProfile.default("224760").to_dict(), "preset": "quiet", "target_fps": 45,
             "target_fps_mode": "manual", "gamemode_enabled": False, "future_key": {"x": 1}}
    profile = GameOptimizationProfile.from_dict(saved, expected_app_id="224760")
    assert profile.preset == "low_power"
    assert (profile.target_fps, profile.target_fps_mode) == (45, "manual")
    assert profile.to_dict()["future_key"] == {"x": 1}
    legacy = GameOptimizationProfile.from_dict({"app_id": "1", "profile": "Quiet"}, expected_app_id="1")
    assert legacy.preset == "low_power"


def test_custom_profile_is_not_reinterpreted_by_evidence() -> None:
    profile = replace(GameOptimizationProfile.default("1"), preset="custom", gamemode_enabled=False)
    plan = OptimizationAdvisor().resolve_preset(profile, None, gamemode_available=True, gamescope_available=True,
                                                accepted_results={"gamemode_enabled": True})
    assert plan.profile.gamemode_enabled is False and plan.changes == {}


@pytest.mark.parametrize("preset", ["automatic", "lowest_latency", "stable_image", "best_quality"])
def test_goal_presets_are_conservative_without_evidence(preset: str) -> None:
    profile = replace(GameOptimizationProfile.default("1"), preset=preset, game_category="competitive")
    plan = OptimizationAdvisor().resolve_preset(profile, None, gamemode_available=True, gamescope_available=True)
    assert plan.profile.gamemode_enabled is False and plan.profile.gamescope_enabled is False
    with_evidence = OptimizationAdvisor().resolve_preset(profile, None, gamemode_available=True, gamescope_available=True,
                                                         accepted_results={"gamemode_enabled": True})
    assert with_evidence.profile.gamemode_enabled is True
    assert "verified local measurements" in with_evidence.sources


def test_low_power_keeps_former_quiet_values() -> None:
    profile = replace(GameOptimizationProfile.default("1"), preset="low_power")
    plan = OptimizationAdvisor().resolve_preset(profile, None, gamemode_available=True, gamescope_available=True)
    assert (plan.profile.target_fps, plan.profile.target_fps_mode, plan.profile.user_goal) == (45, "manual", "low_power")


# Fingerprint --------------------------------------------------------------
def make_pe(path: Path, imports=(), delay=()) -> Path:
    """Minimal PE32+ with one section holding import and delay-import tables."""
    data = bytearray(0x400)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    directories = optional + 112
    section = optional + 240
    struct.pack_into("<IIII", data, section + 8, 0x1000, 0x1000, 0x200, 0x200)
    body = bytearray(0x200)
    names_at = 0x100
    descriptors = []
    for name in (*imports, *delay):
        encoded = name.encode() + b"\0"
        body[names_at:names_at + len(encoded)] = encoded
        descriptors.append((name, 0x1000 + names_at))
        names_at += len(encoded)
    offset = 0
    for name, rva in descriptors[:len(imports)]:
        struct.pack_into("<I", body, offset + 12, rva)
        offset += 20
    struct.pack_into("<II", data, directories + 8, 0x1000, max(20, offset + 20))
    delay_at = 0x80
    cursor = delay_at
    for name, rva in descriptors[len(imports):]:
        struct.pack_into("<I", body, cursor + 4, rva)
        cursor += 32
    if delay:
        struct.pack_into("<II", data, directories + 13 * 8, 0x1000 + delay_at, cursor - delay_at + 32)
    path.write_bytes(bytes(data[:0x200]) + bytes(body))
    return path


def test_pe_imports_and_api_evidence(tmp_path: Path) -> None:
    exe = make_pe(tmp_path / "Game.exe", imports=("KERNEL32.dll", "d3d12.dll"), delay=("UnityPlayer.dll",))
    imports = fp.pe_imports(exe)
    assert imports == ("kernel32.dll", "d3d12.dll", "unityplayer.dll")
    api = fp.graphics_api(imports, None)
    assert (api.value, api.source) == ("Direct3D 12", "executable import table") and api.confidence == 0.7
    assert fp.engine(imports, {"value": "Unknown", "confidence": 0}).value == "Unity"
    configured = fp.graphics_api(imports, {"value": "Vulkan", "confidence": 0.92, "source": "active engine configuration"})
    assert configured.value == "Vulkan"


def test_unknown_stays_unknown(tmp_path: Path) -> None:
    (tmp_path / "game").write_bytes(b"\x7fELF" + b"\0" * 60)
    assert fp.pe_imports(tmp_path / "game") is None
    assert fp.graphics_api(None, None).value == "Unknown"
    two = fp.graphics_api(("d3d11.dll", "vulkan-1.dll"), None)
    assert two.value == "Unknown" and len(two.evidence) == 2
    assert fp.engine((), None).value == "Unknown"
    assert fp.executable_identity(None, None) == {"available": False, "reason": "executable not resolved"}


def test_proton_runtime_and_config_snapshot(tmp_path: Path) -> None:
    proton = tmp_path / "GE-Proton10-1"
    proton.mkdir()
    (proton / "proton").write_text("#!/bin/sh\n")
    (proton / "version").write_text("1700000000 GE-Proton10-1\n")
    (proton / "toolmanifest.vdf").write_text('"manifest"\n{\n "require_tool_appid" "1628350"\n}\n')
    runtime = fp.proton_runtime(proton / "proton", "selected", False)
    assert runtime == {"kind": "proton", "name": "GE-Proton10-1", "version": "GE-Proton10-1",
                       "steamRuntime": "Steam Linux Runtime 3.0 (sniper)"}
    games = tmp_path / "games"
    (games / "1").mkdir(parents=True)
    (games / "1" / "optimization.json").write_text("{}")
    first = fp.config_snapshot(games, "1")
    (games / "1" / "optimization.json").write_text('{"a": 1}')
    assert fp.config_snapshot(games, "1")["revision"] != first["revision"]
    assert first["files"]["mangohud.json"] is None


# Metrics ------------------------------------------------------------------
def test_session_metrics_contract(tmp_path: Path) -> None:
    result = metrics(tmp_path, "a", 10.0, stutter_every=100)
    assert result.sufficient and result.fps_kind == "rendered"
    assert result.warmup_seconds == 20 and 94 <= result.gameplay_seconds <= 96
    assert result.value("averageFps") == pytest.approx(1000 / (10 * (1 + 2 / 100)), rel=0.02)
    assert result.value("p50FrametimeMs") == pytest.approx(10.0)
    assert result.stutter_count > 0 and result.stutter_threshold_ms == pytest.approx(20.0)
    assert result.value("vramMb") == pytest.approx(6.5 * 1024) and result.value("gpuPowerW") == pytest.approx(150)
    assert result.value("gttMb") is None
    assert SessionMetrics.from_dict(result.to_dict()).value("averageFps") == pytest.approx(result.value("averageFps"), abs=0.01)


def test_missing_power_and_short_session(tmp_path: Path) -> None:
    short = metrics(tmp_path, "s", 10.0, seconds=40, gpu_power=0, cpu_power=0)
    assert not short.sufficient and "too_short" in short.issues
    assert short.value("gpuPowerW") is None and short.value("cpuPowerW") is None
    generated = session_metrics(write_log(tmp_path / "fg.csv", 10.0), frame_generation_active=True)
    assert generated.fps_kind == "output"


# Comparability ------------------------------------------------------------
BASE = {"build": "1", "executable": "e", "displayResolution": "2560x1440@165", "renderResolution": "2560x1440",
        "api": "Direct3D 12", "runtime": "GE 10", "graphicsSettings": "s", "rayTracing": "settings s", "upscaler": "None",
        "frameGeneration": "off", "gpu": "RX", "driver": "Mesa 25"}


def test_controlled_approximate_and_invalid_comparisons(tmp_path: Path) -> None:
    a, b = metrics(tmp_path, "a", 10.0), metrics(tmp_path, "b", 9.5)
    ok = cmp.check_comparability(BASE, dict(BASE), before_metrics=a, after_metrics=b)
    assert ok["ceiling"] == "controlled"
    assert cmp.final_level(ok, "controlled") == "controlled"
    assert cmp.final_level(ok, "approximate") == "approximate"
    unknown = cmp.check_comparability({**BASE, "rayTracing": "Unknown"}, dict(BASE), before_metrics=a, after_metrics=b)
    assert unknown["ceiling"] == "approximate" and unknown["unverified"][0]["field"] == "rayTracing"
    assert cmp.final_level(unknown, "controlled") == "approximate"
    assert cmp.final_level(unknown, "controlled", unverified_confirmed=True) == "controlled"
    for field, value in (("build", "2"), ("displayResolution", "1920x1080@60"), ("api", "Vulkan"), ("runtime", "GE 9"),
                         ("graphicsSettings", "t"), ("upscaler", "OptiScaler"), ("frameGeneration", "lsfg")):
        bad = cmp.check_comparability(BASE, {**BASE, field: value}, before_metrics=a, after_metrics=b)
        assert bad["ceiling"] == "not_comparable", field
        assert cmp.final_level(bad, "controlled") == "not_comparable"
    intended = cmp.check_comparability(BASE, {**BASE, "renderResolution": "1970x1108", "upscaler": "Gamescope FSR"},
                                       intended=("renderResolution", "upscaler"), before_metrics=a, after_metrics=b)
    assert intended["ceiling"] == "controlled"
    two_vars = cmp.check_comparability(BASE, dict(BASE), before_metrics=a, after_metrics=b, config_diff_ok=False)
    assert "more_than_one_variable" in two_vars["issues"]
    short = cmp.check_comparability(BASE, dict(BASE), before_metrics=a,
                                    after_metrics=metrics(tmp_path, "c", 9.5, seconds=40))
    assert short["ceiling"] == "not_comparable" and "candidate_too_short" in short["issues"]


# Scoring ------------------------------------------------------------------
ACTIVE = {"verified": True}
NONE = {"imageQuality": "none"}


def decide(goal, a, b, *, candidate=NONE, level="controlled", **kwargs):
    return cmp.score(goal, a, b, candidate, level=level, activation=kwargs.pop("activation", ACTIVE), **kwargs)


def test_maximum_performance_keep_revert_and_hard_rejection(tmp_path: Path) -> None:
    base = metrics(tmp_path, "a", 10.0)
    faster = decide("maximum_performance", base, metrics(tmp_path, "b", 9.4))
    assert faster["decision"] == "keep" and faster["verified"]
    row = next(item for item in faster["metrics"] if item["metric"] == "averageFps")
    assert row["before"] == pytest.approx(100, abs=0.1) and row["percent"] == pytest.approx(6.4, abs=0.2)
    assert decide("maximum_performance", base, metrics(tmp_path, "c", 10.6))["decision"] == "revert"
    assert decide("maximum_performance", base, metrics(tmp_path, "d", 10.1))["decision"] == "inconclusive"
    spikes = metrics(tmp_path, "e", lambda i: 9.0 if i % 50 else 40.0)
    severe = decide("maximum_performance", base, spikes)
    assert severe["decision"] == "revert" and severe["reasons"][0]["code"] == "severe_p99"


def test_balanced_pacing_and_power_cost(tmp_path: Path) -> None:
    base = metrics(tmp_path, "a", 10.0, stutter_every=50)
    smooth = metrics(tmp_path, "b", 10.0)
    assert decide("balanced", base, smooth)["decision"] == "keep"
    hungry = metrics(tmp_path, "c", 10.0, gpu_power=200)
    assert decide("balanced", base, hungry)["reasons"][0]["code"] == "power_cost"


def test_low_power_requires_floor_and_measured_power(tmp_path: Path) -> None:
    base = metrics(tmp_path, "a", 10.0, gpu_power=150)
    capped = metrics(tmp_path, "b", 16.0, gpu_power=110)
    assert decide("low_power", base, capped, fps_floor=0)["decision"] == "inconclusive"
    assert decide("low_power", base, capped, fps_floor=60)["decision"] == "keep"
    assert decide("low_power", base, capped, fps_floor=90)["decision"] == "revert"
    no_power = metrics(tmp_path, "c", 16.0, gpu_power=0, cpu_power=0)
    assert decide("low_power", base, no_power, fps_floor=60)["reasons"][0]["code"] == "power_not_measured"


def test_lowest_latency_penalizes_frame_generation_and_states_proxy(tmp_path: Path) -> None:
    base, fast = metrics(tmp_path, "a", 10.0), metrics(tmp_path, "b", 9.0)
    result = decide("lowest_latency", base, fast)
    assert result["decision"] == "keep" and any(c["code"] == "latency_not_measured" for c in result["costs"])
    fg = decide("lowest_latency", base, fast, frame_generation=(False, True))
    assert fg["decision"] == "revert" and fg["reasons"][0]["code"] == "frame_generation_penalty"
    both = decide("maximum_performance", base, fast, frame_generation=(True, True))
    assert both["decision"] == "inconclusive" and both["reasons"][0]["code"] == "rendered_fps_unavailable"


def test_stable_image_and_best_quality_reject_lossy_changes(tmp_path: Path) -> None:
    base, fast = metrics(tmp_path, "a", 10.0, stutter_every=50), metrics(tmp_path, "b", 8.0)
    lossy = {"imageQuality": "lossy_upscaling"}
    # Not evaluated: these goals cannot decide without the owner's image-quality answer.
    for goal in ("stable_image", "best_quality"):
        unevaluated = decide(goal, base, fast, candidate=lossy, fps_floor=60)
        assert unevaluated["decision"] == "inconclusive" and unevaluated["reasons"][0]["code"] == "quality_not_evaluated"
    seen = {"visual": "same"}
    assert decide("stable_image", base, fast, candidate=lossy, observations=seen)["decision"] == "revert"
    assert decide("best_quality", base, fast, candidate=lossy, fps_floor=60, observations=seen)["decision"] == "revert"
    assert decide("best_quality", base, fast, fps_floor=60)["decision"] == "keep"
    assert decide("best_quality", base, fast, fps_floor=150)["decision"] == "revert"
    worse = decide("stable_image", base, metrics(tmp_path, "c", 10.0), observations={"visual": "worse"})
    assert worse["decision"] == "revert"


def test_crash_incomplete_unverified_and_approximate(tmp_path: Path) -> None:
    base, fast = metrics(tmp_path, "a", 10.0), metrics(tmp_path, "b", 9.0)
    assert decide("maximum_performance", base, fast, observations={"stability": "crash"})["decision"] == "revert"
    assert decide("maximum_performance", base, fast, after_exit_code=1)["decision"] == "inconclusive"
    assert decide("maximum_performance", base, None)["decision"] == "inconclusive"
    assert decide("maximum_performance", base, fast, activation={"verified": False})["decision"] == "inconclusive"
    assert decide("maximum_performance", base, fast, level="not_comparable")["decision"] == "inconclusive"
    approximate = decide("maximum_performance", base, fast, level="approximate")
    assert approximate["decision"] == "keep" and approximate["verified"] is False
    assert approximate["confidence"] == "approximate"
