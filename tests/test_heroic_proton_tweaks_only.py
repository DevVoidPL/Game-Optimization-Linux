"""Proton Tweaks alone are a valid Heroic launcher change (no Gamescope/GameMode needed).

Synthetic Heroic configurations and temporary XDG roots only.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from game_optimization_linux.services.proton_tweaks import PROTON_TWEAK_REGISTRY
from test_heroic_optiscaler import APP_NAME, _controller, _heroic_game

REQUIRES = {"latency_reflex": {"low_latency": "enabled"}}  # Reflex mode is a Low Latency Layer option


def _setup(tmp_path: Path, monkeypatch, *, variant: str = "native"):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    game = _heroic_game(tmp_path, variant=variant)
    base = tmp_path / (".var/app/com.heroicgameslauncher.hgl/config/heroic" if variant == "flatpak" else ".config/heroic")
    (base / "GamesConfig").mkdir(parents=True)
    path = base / "GamesConfig" / f"{APP_NAME}.json"
    path.write_text(json.dumps({APP_NAME: {"enviromentOptions": [{"key": "FOREIGN", "value": "1"}], "wrapperOptions": [],
                                           "wineVersion": {"type": "proton"}, "useGameMode": True}, "version": "v0"}, indent=2))
    controller = _controller(tmp_path, game)
    profiles = controller._optimization_profile_repository
    profiles.save(replace(profiles.load(game.id), gamemode_enabled=False, gamescope_enabled=False, gamescope_mode="disabled"))
    return game, controller, path


def _section(path: Path) -> dict:
    return json.loads(path.read_text())[APP_NAME]


@pytest.mark.parametrize("variant", ["native", "flatpak"])
def test_steam_deck_mode_alone_can_be_applied_and_restored(tmp_path, monkeypatch, variant) -> None:
    game, controller, path = _setup(tmp_path, monkeypatch, variant=variant)
    original = path.read_bytes()
    try:
        assert controller.getLauncherIntegration(game.id)["message"] == "The saved profile adds nothing to the launch"
        controller.saveProtonTweaks(game.id, {"states": {"steam_deck_spoof": "enabled"}})
        plan = controller._optimization_controller.launcher_plan(game)
        assert plan.environment == {"SteamDeck": "1"} and plan.prefix == () and plan.settings == {}
        status = controller.getLauncherIntegration(game.id)
        assert status["canApply"] is True and status["blockers"] == []
        assert controller.applyLauncherIntegration(game.id)["state"] == "applied"
        section = _section(path)
        assert section["enviromentOptions"] == [{"key": "FOREIGN", "value": "1"}, {"key": "SteamDeck", "value": "1"}]
        assert section["wrapperOptions"] == [] and "disableUMU" not in section
        # Back to Default: the plan is empty but the GameOpti record still has to be undone.
        controller.saveProtonTweaks(game.id, {"states": {}})
        status = controller.getLauncherIntegration(game.id)
        assert status["state"] == "outdated" and status["canApply"] is True and status["canRestore"] is True
        assert controller.applyLauncherIntegration(game.id)["state"] == "not_applied"
        assert path.read_bytes() == original
    finally:
        controller.shutdown()


@pytest.mark.parametrize("definition", PROTON_TWEAK_REGISTRY, ids=lambda item: item.id)
def test_every_tweak_alone_is_a_launcher_change(tmp_path, monkeypatch, definition) -> None:
    game, controller, path = _setup(tmp_path, monkeypatch)
    original = path.read_bytes()
    states = {**REQUIRES.get(definition.id, {}), definition.id: "enabled"}
    try:
        result = controller.saveProtonTweaks(game.id, {"states": states})
        assert result.get("success", True) is not False, result
        plan = controller._optimization_controller.launcher_plan(game)
        assert definition.environment_key in plan.environment, plan.environment
        assert plan.prefix == () and plan.blockers == ()
        status = controller.getLauncherIntegration(game.id)
        assert status["canApply"] is True, status["message"]
        controller.applyLauncherIntegration(game.id)
        written = {item["key"]: item["value"] for item in _section(path)["enviromentOptions"]}
        assert written["FOREIGN"] == "1" and definition.environment_key in written
        assert controller.restoreLauncherIntegration(game.id)["state"] == "not_applied"
        assert path.read_bytes() == original
    finally:
        controller.shutdown()


@pytest.mark.parametrize("definition", [d for d in PROTON_TWEAK_REGISTRY if d.disabled_value is not None], ids=lambda item: item.id)
def test_explicit_disabled_state_is_also_a_change(tmp_path, monkeypatch, definition) -> None:
    game, controller, path = _setup(tmp_path, monkeypatch)
    try:
        controller.saveProtonTweaks(game.id, {"states": {definition.id: "disabled"}})
        plan = controller._optimization_controller.launcher_plan(game)
        assert plan.environment.get(definition.environment_key) == definition.disabled_value
        assert controller.getLauncherIntegration(game.id)["canApply"] is True
    finally:
        controller.shutdown()


def test_tweaks_are_skipped_with_a_reason_for_a_non_proton_runner(tmp_path, monkeypatch) -> None:
    game, controller, path = _setup(tmp_path, monkeypatch)
    document = json.loads(path.read_text())
    document[APP_NAME]["wineVersion"] = {"type": "wine"}
    path.write_text(json.dumps(document))
    try:
        controller.saveProtonTweaks(game.id, {"states": {"steam_deck_spoof": "enabled"}})
        plan = controller._optimization_controller.launcher_plan(game)
        assert plan.environment == {} and any("Proton runner" in item for item in plan.warnings)
    finally:
        controller.shutdown()


def test_removed_optiscaler_profile_adds_no_dll_override(tmp_path, monkeypatch) -> None:
    """A stale profile (enabled, override kept, files removed) is not described as selected."""
    game, controller, path = _setup(tmp_path, monkeypatch)
    repository = controller._optiscaler_service.profile_repository
    try:
        repository.save(replace(repository.load(game.id), enabled=True, installation_state="removed",
                                proton_override="dxgi=n,b", injection_dll="dxgi.dll"))
        controller.saveProtonTweaks(game.id, {"states": {"steam_deck_spoof": "enabled"}})
        plan = controller._optimization_controller.launcher_plan(game)
        assert "WINEDLLOVERRIDES" not in plan.environment and plan.environment == {"SteamDeck": "1"}
        assert "optiscaler" not in plan.included
        assert plan.features["optiscaler"]["reason"] == "OptiScaler is enabled but not installed for this game"
        controller.applyLauncherIntegration(game.id)
        keys = [item["key"] for item in _section(path)["enviromentOptions"]]
        assert "WINEDLLOVERRIDES" not in keys
    finally:
        controller.shutdown()


@pytest.mark.parametrize("log, verified", [
    # Hogwarts Legacy, Heroic, 27.10 07:53: the layer loaded and interpolation started, but the
    # only closed swapchain generated nothing. This is not proof of generated frames.
    ("[AFMF info] layer active on instance 0x1 (WAYLAND_DISPLAY unset, DISPLAY set)\n"
     "[AFMF info] interpolation ready: 1920x1080, flow at 1920x1080\n"
     "[AFMF info] swapchain 0x7f created: 1920x1080, format 64\n"
     "[AFMF info] swapchain 0x7f destroyed after 1 presents: 0 generated, 1 skipped\n", False),
    ("[AFMF info] interpolation ready: 1920x1080\n"
     "[AFMF info] swapchain 0x7f destroyed after 5400 presents: 2690 generated, 10 skipped\n", True),
])
def test_afmf_installed_is_not_runtime_verified_without_generated_frames(tmp_path, log, verified) -> None:
    from game_optimization_linux.services.frame_generation import session_evidence
    state_root = tmp_path / "state"
    (state_root / "launch-reports").mkdir(parents=True)
    log_path = tmp_path / "afmf.log"
    log_path.write_text(log)
    state = {"backend": "afmf", "configuredAt": "2026-10-27T06:52:10Z"}
    (state_root / "launch-reports" / "heroic-legendary-abc.json").write_text(json.dumps({
        "frameGenerationBackend": "afmf", "frameGenerationConfiguredAt": state["configuredAt"],
        "status": "launched", "frameGenerationLog": str(log_path), "timestamp": "2026-10-27T06:53:45Z"}))
    assert session_evidence("heroic-legendary-abc", state, state_root)["generationVerified"] is verified
