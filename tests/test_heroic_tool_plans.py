"""Heroic Flatpak launch plans for MangoHud, Gamescope and both (round 21).

HOME and the Heroic config are temporary; no real Flatpak is used.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import time

import pytest

from game_optimization_linux.services.mangohud import MangoHudAvailability
from game_optimization_linux.services.optimization_runtime import RuntimeToolAvailability
from test_heroic_optiscaler import APP_NAME, _controller, _flatpak_heroic

HEROIC = "com.heroicgameslauncher.hgl"


class _Host:
    def __init__(self, *, config_dir_readable: bool = True) -> None:
        self.config_dir_readable = config_dir_readable

    def flatpak_app_capabilities(self, app_id, commands):
        return {
            "commands": [c for c in commands if c in {"gamemoderun", "gamescope", "mangohud"}],
            "extensions": ["MangoHud", "gamescope"],
            "configDirs": ["MangoHud"] if self.config_dir_readable else [],
        }

    def flatpak_runtime(self, app_id):
        return "org.freedesktop.Platform/x86_64/25.08"


class _Tools:
    def detect(self, *, refresh: bool = False):
        return (
            RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun"),
            RuntimeToolAvailability(
                "Gamescope", True, "/usr/bin/gamescope",
                supported_options=("-W", "-H", "-r", "-f", "--mangoapp", "--framerate-limit"),
            ),
        )


class _MangoDetector:
    def detect(self, steam_type: str = "native"):
        from game_optimization_linux.services.mangohud import KNOWN_CONFIG_KEYS

        return MangoHudAvailability(available=True, steam_type="native", message="MangoHud detected",
                                    supported_keys=tuple(KNOWN_CONFIG_KEYS))


@pytest.fixture
def heroic(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)

    def make(*, mangohud: bool, gamescope: bool, readable: bool = True):
        game = _flatpak_heroic(tmp_path)
        controller = _controller(tmp_path, game)
        controller._host_service = _Host(config_dir_readable=readable)
        controller._runtime_tool_detector = _Tools()
        controller._mangohud_detector = _MangoDetector()
        repository = controller._mangohud_repository
        repository.save(replace(repository.load(game.id), enabled=mangohud))
        profiles = controller._optimization_profile_repository
        profiles.save(replace(profiles.load(game.id), gamemode_enabled=False,
                              gamescope_enabled=gamescope,
                              gamescope_mode="native" if gamescope else "disabled"))
        plan = controller._optimization_controller.launcher_plan(game)  # starts the probe
        deadline = time.monotonic() + 5.0
        while plan.pending and time.monotonic() < deadline:
            time.sleep(0.02)
            plan = controller._optimization_controller.launcher_plan(game)
        return game, controller, plan

    made = []

    def factory(**kwargs):
        result = make(**kwargs)
        made.append(result[1])
        return result

    yield factory
    for controller in made:
        controller.shutdown()


def test_mangohud_only_activates_mangohud_with_a_heroic_readable_config(heroic, tmp_path) -> None:
    game, controller, plan = heroic(mangohud=True, gamescope=False)
    expected = tmp_path / "home/.config/MangoHud" / f"gameopti-{game.id}.conf"
    assert plan.prefix == ()  # no Gamescope wrapper
    assert plan.environment["MANGOHUD"] == "1"
    assert plan.environment["MANGOHUD_CONFIGFILE"] == str(expected)
    assert "/.var/app/io.github.DevVoidPL" not in plan.environment["MANGOHUD_CONFIGFILE"]
    assert plan.blockers == ()
    status = controller.getLauncherIntegration(game.id)
    assert "environment MANGOHUD=1" in status["changes"]
    assert f"environment MANGOHUD_CONFIGFILE={expected}" in status["changes"]
    assert controller.applyLauncherIntegration(game.id)["state"] == "applied"
    config = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig" / f"{APP_NAME}.json"
    env = {item["key"]: item["value"] for item in json.loads(config.read_text())[APP_NAME]["enviromentOptions"]}
    assert env["MANGOHUD"] == "1" and env["MANGOHUD_CONFIGFILE"] == str(expected)
    assert expected.is_file()


def test_gamescope_only_has_no_mangoapp(heroic) -> None:
    _game, _controller, plan = heroic(mangohud=False, gamescope=True)
    assert plan.prefix and "gamescope" in plan.prefix[0]
    assert "--mangoapp" not in plan.prefix
    assert "MANGOHUD" not in plan.environment and "MANGOHUD_CONFIGFILE" not in plan.environment
    assert plan.blockers == ()


def test_gamescope_wrapper_uses_the_sandbox_command_and_disables_umu(heroic, tmp_path) -> None:
    """Regression (Hogwarts Legacy, Heroic Flatpak): UMU's pressure-vessel
    sub-sandbox hands the game the host DISPLAY, so Gamescope's WSI layer
    reported "Hooking has failed". The plan now turns on Heroic's own
    disableUMU and wraps with the sandbox ``gamescope`` (no host path)."""
    game, controller, plan = heroic(mangohud=False, gamescope=True)
    assert plan.prefix[0] == "gamescope" and plan.prefix[-1] == "--"
    assert plan.settings == {"disableUMU": True}
    assert any("Disable UMU" in item for item in plan.warnings)
    assert controller.applyLauncherIntegration(game.id)["state"] == "applied"
    config = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig" / f"{APP_NAME}.json"
    section = json.loads(config.read_text())[APP_NAME]
    assert section["disableUMU"] is True
    assert section["wrapperOptions"][-1]["exe"] == "gamescope"
    assert controller.restoreLauncherIntegration(game.id)["state"] == "not_applied"
    section = json.loads(config.read_text())[APP_NAME]
    assert "disableUMU" not in section and section["wrapperOptions"] == []


def test_restore_keeps_a_user_changed_disable_umu(heroic, tmp_path) -> None:
    game, controller, _plan = heroic(mangohud=False, gamescope=True)
    config = tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig" / f"{APP_NAME}.json"
    controller.applyLauncherIntegration(game.id)
    document = json.loads(config.read_text())
    document[APP_NAME]["disableUMU"] = False  # user changed it in Heroic
    config.write_text(json.dumps(document))
    controller.restoreLauncherIntegration(game.id)
    assert json.loads(config.read_text())[APP_NAME]["disableUMU"] is False


def test_mangohud_with_gamescope_uses_mangoapp_for_heroic(heroic, tmp_path) -> None:
    game, controller, plan = heroic(mangohud=True, gamescope=True)
    expected = tmp_path / "home/.config/MangoHud" / f"gameopti-{game.id}.conf"
    assert plan.blockers == ()
    assert plan.prefix[0] == "gamescope" and "--mangoapp" in plan.prefix
    # mangoapp draws the overlay: no second in-game MangoHud layer.
    assert "MANGOHUD" not in plan.environment
    assert plan.environment["MANGOHUD_CONFIGFILE"] == str(expected)
    assert plan.settings == {"disableUMU": True}
    assert plan.features["mangohud"]["available"] is True
    assert plan.features["gamescope"]["available"] is True
    assert controller.getLauncherIntegration(game.id)["canApply"] is True


def test_mangohud_only_does_not_touch_umu(heroic) -> None:
    _game, _controller, plan = heroic(mangohud=True, gamescope=False)
    assert plan.settings == {}


def test_unreadable_config_folder_is_not_applied(heroic) -> None:
    _game, _controller, plan = heroic(mangohud=True, gamescope=False, readable=False)
    assert "MANGOHUD" not in plan.environment and "MANGOHUD_CONFIGFILE" not in plan.environment
    assert plan.features["mangohud"]["available"] is False
    assert "cannot read ~/.config/MangoHud" in plan.features["mangohud"]["reason"]


def test_restore_keeps_foreign_mangohud_configs(heroic, tmp_path) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    foreign = tmp_path / "home/.config/MangoHud/MangoHud.conf"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("fps\n", encoding="utf-8")
    controller.applyLauncherIntegration(game.id)
    assert controller.restoreLauncherIntegration(game.id)["state"] == "not_applied"
    assert foreign.read_text() == "fps\n"


def test_components_report_installed_and_applied_separately(heroic) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    service = controller._optimization_controller._components
    from game_optimization_linux.services.launcher_components import _ToolState
    service._set((HEROIC, "mangohud"), _ToolState("ready", "MangoHud is available in the launcher sandbox"))
    service._inspections[(HEROIC, ("mangohud",))] = __import__(
        "game_optimization_linux.services.launcher_components", fromlist=["_Inspection"]
    )._Inspection(checked_at=time.monotonic(), scope="system", arch="x86_64", branch="25.08",
                  remote="flathub", runtime="org.freedesktop.Platform/x86_64/25.08")
    item = controller.getLauncherComponents(game.id)["components"][0]
    assert item["state"] == "ready" and item["applied"] is False and item["runtimeVerified"] is False
    controller.applyLauncherIntegration(game.id)
    item = controller.getLauncherComponents(game.id)["components"][0]
    assert item["applied"] is True and item["runtimeVerified"] is False
