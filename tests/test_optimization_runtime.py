from __future__ import annotations

from dataclasses import replace
import json
import inspect
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import (
    FilesystemType,
    Game,
    GameOptimizationProfile,
    Launcher,
    MangoHudProfile,
)
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.runner import main as runner_main
import game_optimization_linux.runner as runner_module
import game_optimization_linux.services.optimization_runtime as runtime_module
from game_optimization_linux.services import (
    BaselineSessionRepository,
    DisplayDetector,
    GameOptimizationProfileRepository,
    KNOWN_CONFIG_KEYS,
    MangoHudAvailability,
    MangoHudLaunchIntegration,
    MangoHudProfileRepository,
    OptiScalerProfileRepository,
    ProtonTweaksRepository,
    OptimizationAdvisor,
    OptimizationLaunchPlanner,
    RunnerIntegration,
    RuntimeToolAvailability,
    RuntimeToolDetector,
    MockTaskService,
    SettingsStore,
)
from game_optimization_linux.models import OptiScalerProfile


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _Size:
    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
    def width(self) -> int: return self._width
    def height(self) -> int: return self._height


class _Screen:
    def __init__(self, name: str, width: int, height: int, refresh: float, ratio: float = 1.0) -> None:
        self._name, self._size, self._refresh, self._ratio = name, _Size(width, height), refresh, ratio
    def name(self) -> str: return self._name
    def manufacturer(self) -> str: return "Example"
    def model(self) -> str: return "Panel"
    def size(self) -> _Size: return self._size
    def refreshRate(self) -> float: return self._refresh
    def devicePixelRatio(self) -> float: return self._ratio


def test_profile_is_atomic_per_appid_and_survives_restart(tmp_path: Path) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    profile = replace(
        repository.default("224760"), preset="custom", game_category="platformer_2d",
        target_fps_mode="manual", target_fps=75,
    )
    repository.save(profile)
    restarted = GameOptimizationProfileRepository(tmp_path / "games")
    assert restarted.load("224760") == profile
    assert restarted.path("224760").name == "optimization.json"
    assert not list(restarted.path("224760").parent.glob("*.tmp"))


def test_schema_zero_profile_is_migrated() -> None:
    profile = GameOptimizationProfile.from_dict({
        "app_id": "224760", "profile": "Balanced", "gamemode": True,
        "gamescope": False, "fps_limit": 90,
    })
    assert profile.schema_version == 1
    assert profile.preset == "balanced"
    assert profile.gamemode_enabled is True
    assert profile.target_fps == 90


def test_display_detection_and_manual_override() -> None:
    first = _Screen("DP-1", 2560, 1440, 199.8, 1.25)
    second = _Screen("HDMI-A-1", 1920, 1080, 60.0)
    displays = DisplayDetector().detect([first, second], second)
    assert displays[0].to_dict()["refreshRate"] == 199.8
    assert displays[1].primary is True
    profile = replace(GameOptimizationProfile.default("224760"), target_display_id=displays[0].display_id, manual_overrides={"display": True})
    assert profile.target_display_id == "screen-0:DP-1"
    assert profile.manual_overrides["display"] is True


@pytest.mark.parametrize(
    ("category", "expected", "gamemode"),
    [("competitive", 200, True), ("platformer_2d", 60, False), ("unknown", 60, False)],
)
def test_advisor_without_measurements_is_conservative(
    category: str, expected: int, gamemode: bool,
) -> None:
    display = DisplayDetector().detect([_Screen("DP-1", 3440, 1440, 200)], None)[0]
    profile = replace(GameOptimizationProfile.default("224760"), game_category=category)
    result = OptimizationAdvisor().recommend(profile, display)
    assert result.target_fps == expected
    assert result.gamemode_recommended is gamemode
    assert result.preliminary is True
    assert any("No saved session measurements" in reason for reason in result.reasons)


def test_user_low_power_goal_changes_recommendation() -> None:
    display = DisplayDetector().detect([_Screen("DP-1", 1920, 1080, 165)], None)[0]
    profile = replace(GameOptimizationProfile.default("224760"), game_category="competitive", user_goal="low_power")
    result = OptimizationAdvisor().recommend(profile, display)
    assert result.target_fps == 60
    assert result.gamemode_recommended is False


@pytest.mark.parametrize(
    ("preset", "target", "gamemode", "goal"),
    [
        ("maximum_performance", 165, True, "lowest_latency"),
        ("balanced", 60, True, "stable_image"),
        ("quiet", 45, False, "low_power"),
    ],
)
def test_presets_resolve_to_explainable_runtime_plans(
    preset: str, target: int, gamemode: bool, goal: str,
) -> None:
    display = DisplayDetector().detect(
        [_Screen("DP-1", 2560, 1440, 165)], None
    )[0]
    profile = replace(
        GameOptimizationProfile.default("224760"),
        preset=preset,
        game_category="fast_action",
    )
    plan = OptimizationAdvisor().resolve_preset(
        profile,
        display,
        gamemode_available=True,
        gamescope_available=True,
        system_info={"cpuModel": "Fixture CPU", "gpuModel": "Fixture GPU"},
    )

    assert plan.profile.target_fps == target
    assert plan.profile.gamemode_enabled is gamemode
    assert plan.profile.user_goal == goal
    assert plan.profile.gamescope_enabled is False
    assert plan.reasons
    assert "runtime tool availability" in plan.sources


def test_automatic_keeps_unknown_category_conservative_and_reports_sources() -> None:
    profile = GameOptimizationProfile.default("224760")
    plan = OptimizationAdvisor().resolve_preset(
        profile,
        None,
        gamemode_available=True,
        gamescope_available=False,
        system_info={"cpuModel": "Fixture CPU"},
    )

    assert plan.profile.game_category == "unknown"
    assert plan.profile.gamemode_enabled is False
    assert plan.profile.gamescope_enabled is False
    assert plan.profile.target_fps == 60
    assert any("Gamescope is unavailable" in item for item in plan.conflicts)


def test_profile_rejects_invalid_resolution_refresh_and_fps() -> None:
    base = GameOptimizationProfile.default("224760")
    with pytest.raises(ValueError, match="gamescope_input_width"):
        replace(base, gamescope_input_width=100)
    with pytest.raises(ValueError, match="gamescope_refresh_rate"):
        replace(base, gamescope_refresh_rate=10)
    with pytest.raises(ValueError, match="target_fps"):
        replace(base, target_fps=1001)


def test_tool_detection_uses_argv_and_never_shell() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        output = "--framerate-limit -w -h -W -H -r -f -b -S -F --display-index" if "--help" in argv else "v1"
        return subprocess.CompletedProcess(argv, 0, output, "")
    detector = RuntimeToolDetector(which=lambda name: f"/usr/bin/{name}", runner=run)
    gamemode, gamescope = detector.detect()
    assert gamemode.available and gamescope.available
    assert "--framerate-limit" in gamescope.supported_options
    assert all("shell" not in kwargs for _, kwargs in calls)


def test_missing_gamemode_and_gamescope_are_reported() -> None:
    detector = RuntimeToolDetector(which=lambda _name: None)
    gamemode, gamescope = detector.detect()
    assert gamemode.available is False
    assert gamescope.available is False


def test_runtime_detector_uses_host_path_results_without_fhs_paths() -> None:
    class Host:
        def tool_info(self, name: str) -> dict[str, object]:
            return {
                "available": True,
                "runtime_available": True,
                "executable": "gamemoderun" if name == "gamemode" else "gamescope",
                "version": "host-v1",
                "supported_options": ["-w", "-h", "-W", "-H", "-r", "-f"],
                "diagnostic_message": "resolved from host PATH",
            }

    gamemode, gamescope = RuntimeToolDetector(host_service=Host()).detect()
    assert gamemode.available is True
    assert gamemode.executable == "gamemoderun"
    assert gamescope.available is True
    assert gamescope.executable == "gamescope"
    assert "/usr/bin" not in gamemode.executable + gamescope.executable


def test_runtime_detector_keeps_missing_host_tools_independent() -> None:
    class Host:
        def tool_info(self, name: str) -> dict[str, object]:
            if name == "gamemode":
                raise OSError("portal unavailable for this probe")
            return {
                "available": False,
                "runtime_available": False,
                "executable": "",
                "diagnostic_message": "not installed",
            }

    gamemode, gamescope = RuntimeToolDetector(host_service=Host()).detect()
    assert gamemode.available is False
    assert "probe failed" in gamemode.message
    assert gamescope.available is False
    assert gamescope.message == "not installed"


def test_gamemode_service_diagnostic_failure_disables_wrapper() -> None:
    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--status" in argv:
            return subprocess.CompletedProcess(argv, 255, "", "Could not connect to bus")
        return subprocess.CompletedProcess(argv, 0, "v1.8.2", "")
    detector = RuntimeToolDetector(which=lambda name: f"/usr/bin/{name}", runner=run)
    gamemode = detector.gamemode()
    assert gamemode.available is False
    assert "service is unavailable" in gamemode.message


def _tool(name: str, path: str, options: tuple[str, ...] = ()) -> RuntimeToolAvailability:
    return RuntimeToolAvailability(name, True, path, supported_options=options)


def test_launch_plan_orders_gamescope_outside_direct_gamemode_wrapper() -> None:
    profile = replace(
        GameOptimizationProfile.default("224760"), gamemode_enabled=True,
        gamescope_enabled=True, gamescope_mode="custom", target_fps_mode="manual",
        target_fps=90, target_display_id="screen-1:HDMI-A-1",
    )
    options = ("-W", "-H", "-w", "-h", "-r", "--framerate-limit", "-f", "-b", "-S", "-F", "--display-index")
    plan = OptimizationLaunchPlanner().build(
        profile, ["/game path/game.exe", "--safe"],
        gamemode=_tool("GameMode", "/usr/bin/gamemoderun"),
        gamescope=_tool("Gamescope", "/usr/bin/gamescope", options),
    )
    assert plan.executable == "/usr/bin/gamescope"
    separator = plan.command.index("--")
    assert plan.command[separator + 1:] == ["/usr/bin/gamemoderun", "/game path/game.exe", "--safe"]
    assert plan.wrappers == ("gamescope", "gamemode")
    assert plan.command.count("-r") == 1
    assert plan.command[plan.command.index("-r") + 1] == "90"
    assert "--framerate-limit" not in plan.command
    assert plan.fps_limit_owner == "gamescope"
    assert plan.fps_limit == 90
    assert any("owns the FPS limit at 90 FPS" in reason for reason in plan.reasons)
    assert "--display-index" not in plan.command
    assert "-S" not in plan.command
    assert "-F" not in plan.command
    assert "-w" not in plan.command
    assert "-h" not in plan.command
    assert any("cannot be mapped safely" in warning for warning in plan.warnings)


@pytest.mark.parametrize(
    ("gamescope_enabled", "gamemode_enabled", "expected_wrappers"),
    (
        (False, False, ()),
        (False, True, ("gamemode",)),
        (True, False, ("gamescope",)),
        (True, True, ("gamescope", "gamemode")),
    ),
)
def test_gamescope_and_gamemode_are_independent_features(
    gamescope_enabled: bool,
    gamemode_enabled: bool,
    expected_wrappers: tuple[str, ...],
) -> None:
    profile = replace(
        GameOptimizationProfile.default("292030"),
        gamescope_enabled=gamescope_enabled,
        gamescope_mode="native" if gamescope_enabled else "disabled",
        gamemode_enabled=gamemode_enabled,
        target_fps_mode="manual",
        target_fps=90,
    )
    plan = OptimizationLaunchPlanner().build(
        profile,
        ["SteamLaunch", "AppId=292030", "REDprelauncher.exe"],
        gamemode=_tool("GameMode", "gamemoderun"),
        gamescope=_tool(
            "Gamescope",
            "gamescope",
            ("-W", "-H", "-w", "-h", "-r", "-f", "-b", "-S", "-F"),
        ),
    )

    assert plan.wrappers == expected_wrappers
    assert list(plan.steam_command) == [
        "SteamLaunch", "AppId=292030", "REDprelauncher.exe"
    ]
    assert ("gamescope" in plan.command) is gamescope_enabled
    assert ("gamemoderun" in plan.command) is gamemode_enabled
    assert plan.command[-3:] == [
        "SteamLaunch", "AppId=292030", "REDprelauncher.exe"
    ]


def test_coredump_prone_gamescope_plan_is_minimal_and_isolates_host_wrapper() -> None:
    steam_command = [
        "/steam/steam-launch-wrapper",
        "--",
        "/steam/reaper",
        "SteamLaunch",
        "AppId=292030",
        "--",
        "/compatibilitytools.d/GE-Proton/proton",
        "waitforexitandrun",
        "/games/The Witcher 3/REDprelauncher.exe",
    ]
    profile = replace(
        GameOptimizationProfile.default("292030"),
        gamemode_enabled=True,
        gamescope_enabled=True,
        gamescope_mode="native",
        target_fps_mode="manual",
        target_fps=90,
        target_display_id="screen-0:DP-1",
    )
    original_environment = {
        "PATH": "/fake/steam-runtime/bin:/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/fake/steam-runtime/pinned_libs_64",
        "LD_PRELOAD": "/fake/gameoverlayrenderer.so",
        "STEAM_RUNTIME": "1",
        "PRESSURE_VESSEL_RUNTIME": "/fake/pressure-vessel",
        "VK_ADD_LAYER_PATH": "/fake/steam-runtime/vulkan",
        "SteamAppId": "292030",
    }
    plan = OptimizationLaunchPlanner().build(
        profile,
        steam_command,
        gamemode=_tool("GameMode", "gamemoderun"),
        gamescope=_tool(
            "Gamescope",
            "gamescope",
            ("-W", "-H", "-w", "-h", "-r", "-f", "-b", "-S", "-F", "--display-index"),
        ),
        existing_environment=original_environment,
    )

    assert plan.command[:8] == [
        "gamescope", "-W", "1920", "-H", "1080", "-r", "90", "-f"
    ]
    assert plan.command[8:10] == ["--", "env"]
    assert "--display-index" not in plan.command
    assert "-S" not in plan.command
    assert "-F" not in plan.command
    assert "-w" not in plan.command
    assert "-h" not in plan.command
    assert plan.command[-len(steam_command):] == steam_command
    assert plan.command[-len(steam_command) - 1] == "gamemoderun"
    assert set(plan.wrapper_environment_removed) == {
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PATH",
        "PRESSURE_VESSEL_RUNTIME",
        "STEAM_RUNTIME",
        "VK_ADD_LAYER_PATH",
    }
    wrapper_environment = plan.process_environment(original_environment)
    assert "LD_LIBRARY_PATH" not in wrapper_environment
    assert "LD_PRELOAD" not in wrapper_environment
    assert "STEAM_RUNTIME" not in wrapper_environment
    assert "PRESSURE_VESSEL_RUNTIME" not in wrapper_environment
    assert "VK_ADD_LAYER_PATH" not in wrapper_environment
    assert wrapper_environment["PATH"] == "/usr/bin:/bin"
    assert any(
        value == "LD_LIBRARY_PATH=/fake/steam-runtime/pinned_libs_64"
        for value in plan.command
    )
    assert "LD_LIBRARY_PATH=<preserved>" in plan.diagnostic_command
    assert all("pinned_libs_64" not in value for value in plan.diagnostic_command)


def test_gamescope_missing_required_option_blocks_launch() -> None:
    profile = replace(
        GameOptimizationProfile.default("292030"),
        gamescope_enabled=True,
        gamescope_mode="native",
    )

    with pytest.raises(ValueError, match="does not support -H"):
        OptimizationLaunchPlanner().build(
            profile,
            ["REDprelauncher.exe"],
            gamemode=RuntimeToolAvailability("GameMode", False),
            gamescope=_tool("Gamescope", "gamescope", ("-W",)),
        )


def test_disabled_tools_leave_original_argv_and_warn_about_advisory_fps() -> None:
    profile = replace(GameOptimizationProfile.default("224760"), target_fps_mode="manual", target_fps=60)
    missing = RuntimeToolAvailability("tool", False)
    plan = OptimizationLaunchPlanner().build(profile, ["/game", "a b"], gamemode=missing, gamescope=missing)
    assert plan.command == ["/game", "a b"]
    assert plan.wrappers == ()
    assert "advisory" in plan.warnings[0]


def test_mangohud_owns_limit_only_when_gamescope_is_inactive() -> None:
    profile = replace(
        GameOptimizationProfile.default("224760"),
        target_fps_mode="unlimited",
    )
    missing = RuntimeToolAvailability("tool", False)
    plan = OptimizationLaunchPlanner().build(
        profile, ["/game"], gamemode=missing, gamescope=missing,
        mangohud_fps_limit=60,
    )
    assert plan.command == ["/game"]
    assert plan.fps_limit_owner == "mangohud"
    assert plan.fps_limit == 60
    assert any("MangoHud owns" in reason for reason in plan.reasons)


def test_launch_plan_merges_optiscaler_with_existing_wine_overrides() -> None:
    profile = GameOptimizationProfile.default("224760")
    missing = RuntimeToolAvailability("tool", False)
    plan = OptimizationLaunchPlanner().build(
        profile,
        ["/game"],
        gamemode=missing,
        gamescope=missing,
        optiscaler_override="dxgi=n,b",
        existing_wine_overrides="d3d11=b;dxgi=b;userloader=n",
    )
    assert plan.environment == {
        "WINEDLLOVERRIDES": "d3d11=b;dxgi=n,b;userloader=n"
    }
    assert "WINEDLLOVERRIDES:dxgi" in plan.environment_conflicts
    assert any("native-first order" in warning for warning in plan.warnings)
    assert any("OptiScaler Proton override" in reason for reason in plan.reasons)


def test_proton_tweaks_and_optiscaler_share_one_deterministic_environment() -> None:
    profile = replace(
        GameOptimizationProfile.default("224760"),
        gamemode_enabled=False,
        gamescope_enabled=False,
    )
    missing = RuntimeToolAvailability("tool", False)

    plan = OptimizationLaunchPlanner().build(
        profile,
        ["/game"],
        gamemode=missing,
        gamescope=missing,
        proton_environment={
            "PROTON_LOG": "1",
            "PROTON_NO_ESYNC": "1",
        },
        optiscaler_override="dxgi=n,b",
        existing_wine_overrides="xaudio2_7=n",
    )

    assert plan.environment == {
        "PROTON_LOG": "1",
        "PROTON_NO_ESYNC": "1",
        "WINEDLLOVERRIDES": "xaudio2_7=n;dxgi=n,b",
    }
    assert plan.environment_sources == {
        "PROTON_LOG": "proton_tweaks",
        "PROTON_NO_ESYNC": "proton_tweaks",
        "WINEDLLOVERRIDES": "optiscaler",
    }


def test_proton_tweak_conflict_with_inherited_environment_is_reported() -> None:
    profile = GameOptimizationProfile.default("224760")
    missing = RuntimeToolAvailability("tool", False)

    plan = OptimizationLaunchPlanner().build(
        profile,
        ["/game"],
        gamemode=missing,
        gamescope=missing,
        proton_environment={"PROTON_LOG": "1"},
        existing_environment={"PROTON_LOG": "0"},
    )

    assert plan.environment["PROTON_LOG"] == "1"
    assert plan.environment_conflicts == ("PROTON_LOG",)
    assert any("PROTON_LOG" in warning for warning in plan.warnings)


def test_runner_applies_installed_optiscaler_profile_without_shell(
    tmp_path: Path,
) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(GameOptimizationProfile.default("224760"))
    optiscaler = OptiScalerProfileRepository(tmp_path / "games")
    optiscaler.save(
        replace(
            OptiScalerProfile.default("224760"),
            enabled=True,
            installation_state="installed",
            executable="Game.exe",
            install_directory="/tmp/game",
            proton_override="dxgi=n,b",
            manifest_id="manifest-test",
        )
    )
    called: list[object] = []

    def execute(executable: str, argv: list[str], environment: dict[str, str]) -> int:
        called.extend((executable, argv, environment))
        return 0

    assert runner_main(
        ["--appid", "224760", "--", "/game"],
        repository=repository,
        optiscaler_repository=optiscaler,
        detector=RuntimeToolDetector(which=lambda _name: None),
        executor=execute,
        report_root=tmp_path / "reports",
    ) == 0
    environment = called[2]
    assert isinstance(environment, dict)
    assert environment["WINEDLLOVERRIDES"].endswith("dxgi=n,b")
    report = json.loads((tmp_path / "reports" / "224760.json").read_text())
    assert report["environmentKeys"] == ["WINEDLLOVERRIDES"]


def test_runner_combines_proton_tweaks_with_optiscaler_without_shell(
    tmp_path: Path,
) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(GameOptimizationProfile.default("224760"))
    optiscaler = OptiScalerProfileRepository(tmp_path / "games")
    optiscaler.save(
        replace(
            OptiScalerProfile.default("224760"),
            enabled=True,
            installation_state="installed",
            executable="Game.exe",
            install_directory="/tmp/game",
            proton_override="dxgi=n,b",
            manifest_id="manifest-test",
        )
    )
    proton = ProtonTweaksRepository(tmp_path / "games")
    proton.save(
        proton.from_payload(
            "224760",
            {"toggles": {"proton_log": True, "no_fsync": True}},
        )
    )
    captured: dict[str, str] = {}

    def execute(_executable: str, _argv: list[str], environment: dict[str, str]) -> int:
        captured.update(environment)
        return 0

    assert runner_main(
        ["--appid", "224760", "--", "/game"],
        repository=repository,
        optiscaler_repository=optiscaler,
        proton_tweaks_repository=proton,
        detector=RuntimeToolDetector(which=lambda _name: None),
        executor=execute,
        report_root=tmp_path / "reports",
    ) == 0
    assert captured["PROTON_LOG"] == "1"
    assert captured["PROTON_NO_FSYNC"] == "1"
    assert captured["WINEDLLOVERRIDES"] == "dxgi=n,b"


def test_runner_records_single_mangohud_application_activation_owner(
    tmp_path: Path,
) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(GameOptimizationProfile.default("224760"))
    mango = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    mango.save(
        replace(
            MangoHudProfile.default("224760"),
            enabled=True,
            preset="fps_only",
            executable_path="Game.exe",
        )
    )
    captured: dict[str, str] = {}

    def execute(_executable: str, _argv: list[str], environment: dict[str, str]) -> int:
        captured.update(environment)
        return 0

    assert runner_main(
        ["--appid", "224760", "--", "/game"],
        repository=repository,
        mangohud_repository=mango,
        detector=RuntimeToolDetector(which=lambda _name: None),
        executor=execute,
        report_root=tmp_path / "reports",
    ) == 0
    report = json.loads((tmp_path / "reports/224760.json").read_text())
    assert report["mangoHudActivationOwner"] == "per_application_config"
    assert "MANGOHUD" not in captured
    assert "--mangoapp" not in report["arguments"]


def test_runner_records_one_private_mangohud_baseline_without_changing_profile(
    tmp_path: Path,
) -> None:
    profiles = GameOptimizationProfileRepository(tmp_path / "games")
    profiles.save(GameOptimizationProfile.default("224760"))
    mango = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "saved-logs")
    saved = replace(
        MangoHudProfile.default("224760"),
        enabled=True,
        preset="fps_only",
        executable_path="Game.exe",
        fps_limit=75,
    )
    mango.save(saved)
    stored_before = mango.load("224760")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    created = sessions.create("224760", "steam-224760")
    captured: dict[str, str] = {}

    def execute(_executable: str, _argv: list[str], environment: dict[str, str]) -> int:
        captured.update(environment)
        created.log_directory.joinpath("Game_2026-08-09.csv").write_text(
            "MangoHud v0.8.1\n"
            "time,fps,frametime,cpu_load,gpu_load,ram,vram,gpu_temp\n"
            "0.0,60,16.667,40,95,4096,6144,70\n",
            encoding="utf-8",
        )
        return 0

    result = runner_main(
        ["--appid", "224760", "--", "/game"],
        repository=profiles,
        mangohud_repository=mango,
        baseline_sessions=sessions,
        detector=RuntimeToolDetector(which=lambda _name: None),
        executor=execute,
        report_root=tmp_path / "reports",
    )

    assert result == 0
    assert captured["MANGOHUD"] == "1"
    assert captured["MANGOHUD_CONFIGFILE"] == str(created.config_path)
    assert "autostart_log=1" in created.config_path.read_text(encoding="utf-8")
    assert "output_folder=" + str(created.log_directory) in created.config_path.read_text(
        encoding="utf-8"
    )
    assert sessions.load("224760").status == "processing"
    assert sessions.newest_log("224760").name == "Game_2026-08-09.csv"
    assert mango.load("224760") == stored_before
    report = json.loads((tmp_path / "reports/224760.json").read_text())
    assert report["mangoHudActivationOwner"] == "measurement_session"
    assert report["fpsLimitOwner"] == "none"
    assert report["environmentSources"]["MANGOHUD_CONFIGFILE"] == "baseline_measurement"


def test_runner_waits_for_a_real_baseline_child_process(tmp_path: Path) -> None:
    profiles = GameOptimizationProfileRepository(tmp_path / "games")
    profiles.save(GameOptimizationProfile.default("224760"))
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    sessions.create("224760", "steam-224760")

    result = runner_main(
        ["--appid", "224760", "--", "/usr/bin/true"],
        repository=profiles,
        baseline_sessions=sessions,
        detector=RuntimeToolDetector(which=lambda _name: None),
        report_root=tmp_path / "reports",
    )

    assert result == 0
    assert sessions.load("224760").status == "processing"


def test_reentrant_runner_tracks_wrapper_child_and_completes_session_once(
    tmp_path: Path,
) -> None:
    class CountingSessions(BaselineSessionRepository):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.completed_transitions = 0

        def finish(
            self,
            app_id: object,
            exit_code: int,
            session_id: str = "",
            runner_token: str = "",
        ):
            before = self.load(app_id)
            result = super().finish(
                app_id, exit_code, session_id, runner_token
            )
            if (
                before is not None
                and result is not None
                and before.status != result.status
                and result.status in {"processing", "failed"}
            ):
                self.completed_transitions += 1
            return result

    app_id = "224760"
    profiles = GameOptimizationProfileRepository(tmp_path / "games")
    profiles.save(GameOptimizationProfile.default(app_id))
    sessions = CountingSessions(tmp_path / "sessions")
    created = sessions.create(app_id, f"steam-{app_id}")
    launcher_runner = sessions.claim(app_id, runner_pid=1001)
    assert launcher_runner is not None

    child_pid = tmp_path / "child.pid"
    measurement_config = tmp_path / "measurement-config.txt"
    wrapper = tmp_path / "steam-proton-wrapper.py"
    wrapper.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(0.15)'])\n"
        "Path(sys.argv[2]).write_text(str(child.pid), encoding='utf-8')\n"
        "Path(sys.argv[3]).write_text("
        "os.environ.get('MANGOHUD_CONFIGFILE', ''), encoding='utf-8')\n"
        "status = child.wait()\n"
        "Path(sys.argv[1]).write_text("
        "'time,fps,frametime\\n0.0,60,16.67\\n', encoding='utf-8')\n"
        "raise SystemExit(status)\n",
        encoding="utf-8",
    )
    log = created.log_directory / "wrapper-child.csv"

    result = runner_main(
        [
            "--appid", app_id, "--", sys.executable, str(wrapper),
            str(log), str(child_pid), str(measurement_config),
        ],
        repository=profiles,
        baseline_sessions=sessions,
        detector=RuntimeToolDetector(which=lambda _name: None),
        report_root=tmp_path / "reports",
    )

    finished = sessions.load(app_id)
    assert result == 0
    assert child_pid.read_text(encoding="utf-8").isdecimal()
    assert measurement_config.read_text(encoding="utf-8") == str(
        created.config_path
    )
    assert finished is not None
    assert finished.id == created.id
    assert finished.status == "processing"
    assert finished.runner_completed_at is not None
    assert finished.spawned_pid is not None
    assert finished.observed_processes == (
        f"pid={finished.spawned_pid} state=exited code=0",
    )
    assert sessions.completed_transitions == 1

    sessions.finish(
        app_id, 0, created.id, launcher_runner.runner_token
    )
    assert sessions.load(app_id).status == "processing"
    assert sessions.completed_transitions == 1


def test_imported_mangohud_log_is_copied_to_private_session(tmp_path: Path) -> None:
    source = tmp_path / "existing.csv"
    source.write_text(
        "time,fps,frametime\n0.0,60,16.67\n",
        encoding="utf-8",
    )
    sessions = BaselineSessionRepository(tmp_path / "sessions")

    session = sessions.import_log("224760", "steam-224760", source)

    imported = sessions.newest_log("224760")
    assert session.status == "processing"
    assert imported is not None
    assert imported != source
    assert imported.read_bytes() == source.read_bytes()


def test_corrupt_optional_optiscaler_profile_does_not_block_game_launch(
    tmp_path: Path,
) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(GameOptimizationProfile.default("224760"))
    optiscaler = OptiScalerProfileRepository(tmp_path / "games")
    optiscaler.path("224760").write_text("not-json", encoding="utf-8")
    called: list[str] = []

    result = runner_main(
        ["--appid", "224760", "--", "/game"],
        repository=repository,
        optiscaler_repository=optiscaler,
        detector=RuntimeToolDetector(which=lambda _name: None),
        executor=lambda executable, _argv, _environment: called.append(executable),
        report_root=tmp_path / "reports",
    )

    assert result == 0
    assert called == ["/game"]
    report = json.loads((tmp_path / "reports" / "224760.json").read_text())
    assert any("OptiScaler profile ignored" in item for item in report["warnings"])


def test_runner_passes_steam_command_as_argv_and_returns_executor_code(tmp_path: Path) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(GameOptimizationProfile.default("224760"))
    detector = RuntimeToolDetector(which=lambda _name: None)
    called: list[object] = []
    def execute(executable: str, argv: list[str], environment: dict[str, str]) -> int:
        called.extend((executable, argv, environment))
        return 7
    result = runner_main(
        ["--appid", "224760", "--", "/game path/game", "--option", "a b"],
        repository=repository, detector=detector, executor=execute,
        report_root=tmp_path / "reports",
    )
    assert result == 7
    assert called[0] == "/game path/game"
    assert called[1] == ["/game path/game", "--option", "a b"]
    report = json.loads((tmp_path / "reports" / "224760.json").read_text())
    assert "environment" not in report
    assert report["environmentKeys"] == []
    assert report["fpsLimitOwner"] == "none"


def test_runner_plan_only_does_not_execute(tmp_path: Path) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    called: list[object] = []
    result = runner_main(
        ["--appid", "224760", "--plan-only", "--", "/usr/bin/true"],
        repository=repository, detector=RuntimeToolDetector(which=lambda _name: None),
        executor=lambda *_args: called.append(object()), report_root=tmp_path / "reports",
    )
    assert result == 0
    assert called == []


def test_flatpak_runner_executes_complete_plan_on_host_without_host_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(
        replace(
            GameOptimizationProfile.default("292030"),
            gamemode_enabled=True,
            gamescope_enabled=True,
            gamescope_mode="native",
            target_fps_mode="manual",
            target_fps=60,
        )
    )

    class Host:
        def tool_info(self, name: str) -> dict[str, object]:
            return {
                "available": True,
                "runtime_available": True,
                "executable": "gamemoderun" if name == "gamemode" else "gamescope",
                "version": "v1",
                "supported_options": ["-w", "-h", "-W", "-H", "-r", "-S", "-F", "-f", "-b"],
                "diagnostic_message": "available",
            }

    executed: list[object] = []
    steam_environment = {
        "LD_LIBRARY_PATH": "/fake/steam-runtime/pinned_libs_64",
        "LD_PRELOAD": "/fake/gameoverlayrenderer.so",
        "STEAM_RUNTIME": "1",
        "PRESSURE_VESSEL_RUNTIME": "/fake/pressure-vessel",
        "SteamAppId": "292030",
        "SteamGameId": "292030",
        "STEAM_COMPAT_APP_ID": "292030",
        "STEAM_COMPAT_DATA_PATH": "/fake/compatdata/292030",
    }
    monkeypatch.setenv("FLATPAK_ID", "io.github.DevVoidPL.GameOptimizationLinux")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    monkeypatch.setattr(
        runner_module,
        "_load_steam_environment",
        lambda _environment: dict(steam_environment),
    )
    monkeypatch.setattr(runner_module, "HostServiceClient", Host)
    monkeypatch.setattr(
        runner_module.shutil,
        "which",
        lambda name: "/app/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
    )
    monkeypatch.setattr(
        runner_module.os,
        "execvpe",
        lambda executable, argv, environment: executed.extend(
            (executable, list(argv), dict(environment))
        ) or 0,
    )

    result = runner_main(
        ["--appid", "292030", "--", "/nix/store/game/bin/game", "a b"],
        repository=repository,
        report_root=tmp_path / "reports",
    )
    assert result == 0
    assert executed[0] == "/app/bin/flatpak-spawn"
    argv = executed[1]
    assert isinstance(argv, list)
    assert argv[:2] == ["/app/bin/flatpak-spawn", "--host"]
    assert "gamescope" in argv
    assert "gamemoderun" in argv
    assert "/nix/store/game/bin/game" in argv
    assert not any("python" in item.casefold() for item in argv)
    for key in (
        "LD_LIBRARY_PATH", "LD_PRELOAD", "PRESSURE_VESSEL_RUNTIME", "STEAM_RUNTIME"
    ):
        assert not any(value.startswith(f"--env={key}=") for value in argv)
        assert f"{key}={steam_environment[key]}" in argv
    for key in (
        "SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID", "STEAM_COMPAT_DATA_PATH"
    ):
        assert f"--env={key}={steam_environment[key]}" in argv
    helper_environment = executed[2]
    assert isinstance(helper_environment, dict)
    assert "LD_LIBRARY_PATH" not in helper_environment
    assert "LD_PRELOAD" not in helper_environment
    report = json.loads((tmp_path / "reports/292030.json").read_text())
    assert report["executionTransport"] == "flatpak-spawn-host"
    assert report["steamContextAppId"] == "292030"
    assert report["steamContextGameId"] == "292030"
    assert report["steamCommand"] == ["/nix/store/game/bin/game", "a b"]
    assert report["gameModeWrapper"] == ["gamemoderun"]
    assert report["gamescopeWrapper"][0] == "gamescope"
    assert "LD_LIBRARY_PATH=<preserved>" in report["diagnosticCommand"]
    assert report["steamCommandShell"] == "/nix/store/game/bin/game 'a b'"
    assert "pinned_libs_64" not in report["diagnosticCommandShell"]
    diagnostics = capsys.readouterr().err
    assert "Steam command: /nix/store/game/bin/game 'a b'" in diagnostics
    assert "GameMode wrapper: gamemoderun" in diagnostics
    assert "Gamescope wrapper: gamescope" in diagnostics
    assert "host wrapper environment isolation" in diagnostics
    assert "pinned_libs_64" not in diagnostics


def _null_environment(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in path.read_bytes().split(b"\0"):
        if not entry:
            continue
        key, separator, value = entry.partition(b"=")
        assert separator
        result[os.fsdecode(key)] = os.fsdecode(value)
    return result


def test_runner_loads_private_environment_handoff_and_removes_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    directory = home / ".local/share/game-optimization-linux/run-env"
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    snapshot = directory / "steam-env.test"
    snapshot.write_bytes(
        b"SteamAppId=292030\0SteamGameId=292030\0"
        b"LD_LIBRARY_PATH=/fake/steam-runtime/pinned_libs_64\0"
        b"LD_PRELOAD=/fake/gameoverlayrenderer.so\0"
    )
    snapshot.chmod(0o600)
    monkeypatch.setattr(runner_module, "host_home_directory", lambda _env: home)

    loaded = runner_module._load_steam_environment(
        {
            "FLATPAK_ID": "io.github.DevVoidPL.GameOptimizationLinux",
            "GAME_OPTIMIZATION_STEAM_ENV_FILE": str(snapshot),
        }
    )

    assert loaded["SteamAppId"] == "292030"
    assert loaded["SteamGameId"] == "292030"
    assert loaded["LD_LIBRARY_PATH"].endswith("pinned_libs_64")
    assert loaded["LD_PRELOAD"].endswith("gameoverlayrenderer.so")
    assert not snapshot.exists()


def test_runner_replaces_only_zero_steam_context_with_requested_appid() -> None:
    restored = runner_module._restore_steam_app_context(
        "292030",
        {
            "SteamAppId": "0",
            "SteamGameId": "0",
            "STEAM_COMPAT_APP_ID": "0",
            "STEAM_COMPAT_DATA_PATH": "/games/steamapps/compatdata/0",
        },
    )

    assert restored["SteamAppId"] == "292030"
    assert restored["SteamGameId"] == "292030"
    assert restored["STEAM_COMPAT_APP_ID"] == "292030"
    assert restored["STEAM_COMPAT_DATA_PATH"].endswith("compatdata/292030")


@pytest.mark.parametrize(
    ("steam_installation", "game_command"),
    (
        ("native", ("/games/native-game", "--windowed")),
        ("native", ("/compatibilitytools.d/Proton/proton", "waitforexitandrun", "Game.exe")),
        ("flatpak", ("/games/native-game", "--windowed")),
        ("flatpak", ("/compatibilitytools.d/Proton/proton", "waitforexitandrun", "Game.exe")),
    ),
)
def test_host_runner_isolates_flatpak_env_and_preserves_steam_context(
    tmp_path: Path,
    steam_installation: str,
    game_command: tuple[str, ...],
) -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "libexec/game-optimization-run-host"
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    clean_environment_file = tmp_path / "flatpak-environment"
    original_environment_file = tmp_path / "original-environment"
    arguments_file = tmp_path / "flatpak-arguments"
    transport = "flatpak" if steam_installation == "native" else "flatpak-spawn"
    transport_script = tools / transport
    transport_script.write_text(
        "#!/bin/sh\n"
        f"/usr/bin/env -0 > {clean_environment_file!s}\n"
        "snapshot=\n"
        "for value in \"$@\"; do\n"
        "  case \"$value\" in\n"
        "    --env=GAME_OPTIMIZATION_STEAM_ENV_FILE=*) "
        "snapshot=${value#--env=GAME_OPTIMIZATION_STEAM_ENV_FILE=} ;;\n"
        "  esac\n"
        "done\n"
        f"/usr/bin/printf '%s\\n' \"$@\" > {arguments_file!s}\n"
        f"/usr/bin/cp \"$snapshot\" {original_environment_file!s}\n",
        encoding="utf-8",
    )
    transport_script.chmod(0o755)
    if steam_installation == "flatpak":
        for command in ("chmod", "dirname", "env", "mkdir", "mktemp", "rm"):
            executable = shutil.which(command)
            assert executable is not None
            (tools / command).symlink_to(executable)

    environment = {
        "HOME": str(home),
        "USER": "tester",
        "LOGNAME": "tester",
        "PATH": f"{tools}:/fake/steam-runtime/pinned_libs_64:/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/fake/steam-runtime/pinned_libs_64",
        "LD_PRELOAD": "/fake/gameoverlayrenderer.so",
        "STEAM_RUNTIME": "1",
        "STEAM_RUNTIME_LIBRARY_PATH": "/fake/steam-runtime/lib",
        "PRESSURE_VESSEL_RUNTIME": "/fake/pressure-vessel",
        "SteamAppId": "292030",
        "SteamGameId": "292030",
        "STEAM_COMPAT_APP_ID": "292030",
        "STEAM_COMPAT_DATA_PATH": "/fake/compatdata/292030",
        "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
        "LANG": "C.UTF-8",
    }
    if steam_installation == "flatpak":
        environment["FLATPAK_ID"] = "com.valvesoftware.Steam"

    completed = subprocess.run(
        [
            "/bin/sh",
            str(wrapper),
            "--appid",
            "292030",
            "--",
            *game_command,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    clean_environment = _null_environment(clean_environment_file)
    original_environment = _null_environment(original_environment_file)
    assert "LD_LIBRARY_PATH" not in clean_environment
    assert "LD_PRELOAD" not in clean_environment
    assert "STEAM_RUNTIME" not in clean_environment
    assert "STEAM_RUNTIME_LIBRARY_PATH" not in clean_environment
    assert not any(key.startswith("PRESSURE_VESSEL_") for key in clean_environment)
    assert "/fake/steam-runtime" not in clean_environment["PATH"]
    assert original_environment["LD_LIBRARY_PATH"].endswith("pinned_libs_64")
    assert original_environment["LD_PRELOAD"] == "/fake/gameoverlayrenderer.so"
    assert original_environment["SteamAppId"] == "292030"
    assert original_environment["SteamGameId"] == "292030"
    assert original_environment["STEAM_COMPAT_DATA_PATH"].endswith("compatdata/292030")
    assert "compatdata/0" not in original_environment["STEAM_COMPAT_DATA_PATH"]
    arguments = arguments_file.read_text(encoding="utf-8")
    assert "--appid\n292030\n--\n" in arguments
    assert all(value in arguments for value in game_command)


def test_runner_and_launch_planner_never_use_a_shell() -> None:
    source = inspect.getsource(runner_module) + inspect.getsource(runtime_module)
    assert "shell=True" not in source
    assert "bash -c" not in source
    assert "eval(" not in source


def test_stable_steam_command_never_changes_with_profile(tmp_path: Path) -> None:
    integration = RunnerIntegration(tmp_path / "game-optimization-run")
    command = integration.steam_command("224760")
    assert command == f'"{tmp_path / "game-optimization-run"}" --appid 224760 -- %command%'


def test_runner_launch_option_preflight_detects_configured_and_missing(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "game-optimization-run"
    localconfig = tmp_path / "localconfig.vdf"
    game = replace(demo_games()[0], steam_app_id="224760")
    integration = RunnerIntegration(
        runner, steam_config_paths=(localconfig,)
    )
    command = integration.steam_command("224760")
    vdf_command = command.replace('"', '\\"')
    localconfig.write_text(
        '"UserLocalConfigStore"\n{\n"Software"\n{\n"Valve"\n{\n"Steam"\n{\n'
        f'"apps"\n{{\n"224760"\n{{\n"LaunchOptions"\n"{vdf_command}"\n}}\n}}\n'
        '}\n}\n}\n}\n',
        encoding="utf-8",
    )

    configured = integration.steam_launch_option_status(game)
    assert configured.configured is True
    assert configured.source == str(localconfig)

    localconfig.write_text(
        '"UserLocalConfigStore"\n{\n"Software"\n{\n"Valve"\n{\n"Steam"\n{\n'
        '"apps"\n{\n"224760"\n{\n"LaunchOptions"\n"mangohud %command%"\n}\n}\n'
        '}\n}\n}\n}\n',
        encoding="utf-8",
    )
    missing = integration.steam_launch_option_status(game)
    assert missing.configured is False
    assert missing.command == command


def test_runner_preflight_uses_canonical_launch_options_for_duplicate_app_nodes(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "Game Optimization" / "game-optimization-run"
    localconfig = tmp_path / "localconfig.vdf"
    integration = RunnerIntegration(runner, steam_config_paths=(localconfig,))
    app_ids = ("242550", "292030", "1774580")
    commands = {
        app_id: integration.steam_command(app_id).replace('"', '\\"')
        for app_id in app_ids
    }
    localconfig.write_text(
        '"UserLocalConfigStore"\n{\n'
        '"apps"\n{\n'
        '"242550"\n{\n"controller_config"\n"1"\n}\n'
        '"1774580"\n{\n"OverlayAppEnable"\n"1"\n}\n'
        '}\n'
        '"Software"\n{\n"Valve"\n{\n"Steam"\n{\n"apps"\n{\n'
        + "".join(
            f'"{app_id}"\n{{\n"LaunchOptions"\n"{commands[app_id]}"\n}}\n'
            for app_id in app_ids
        )
        + '}\n}\n}\n}\n}\n',
        encoding="utf-8",
    )

    for app_id in app_ids:
        game = replace(demo_games()[0], steam_app_id=app_id)
        status = integration.steam_launch_option_status(game)
        assert status.configured is True
        assert status.source == str(localconfig)
        assert status.app_node_found is True
        assert status.app_node_path.endswith(
            f"Software/Valve/Steam/apps/{app_id}"
        )
        assert status.raw_launch_options == integration.steam_command(app_id)
        assert status.parsed_executable == str(runner)
        assert status.parsed_app_id == app_id
        assert status.separator_found is True
        assert status.command_placeholder_found is True
        assert status.reason == "configured"


def test_runner_preflight_parses_launch_options_semantically(tmp_path: Path) -> None:
    configured_runner = (
        tmp_path / "installed runner" / "game-optimization-run"
    )
    integration = RunnerIntegration(tmp_path / "current" / "game-optimization-run")
    value = f'"{configured_runner}" --appid 242550 -- %command%'

    parsed = integration._parse_launch_options(value, "242550")

    assert parsed.configured is True
    assert parsed.executable == str(configured_runner)
    assert parsed.app_id == "242550"
    assert parsed.separator_found is True
    assert parsed.command_placeholder_found is True


def test_controller_saves_desktop_profile_per_appid(tmp_path: Path) -> None:
    game = replace(demo_games()[0], steam_app_id="224760")
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=repository, initial_games=(game,),
        auto_refresh=False,
    )
    try:
        result = controller.saveOptimizationProfile(game.id, {
            "preset": "custom", "gameCategory": "platformer_2d",
            "userGoal": "stable_image", "targetFpsMode": "manual",
            "targetFps": 75, "gamemodeEnabled": False,
            "gamescopeEnabled": False, "gamescopeMode": "disabled",
        })
    finally:
        controller.shutdown()
    assert result["success"] is True
    saved = repository.load("224760")
    assert saved.game_category == "platformer_2d"
    assert saved.target_fps == 75


def test_record_baseline_runs_steam_runner_and_produces_recommendations(
    tmp_path: Path,
) -> None:
    app_id = "990001"
    root = tmp_path / "SyntheticUnreal"
    executable = root / "Project/Binaries/Win64/Project-Win64-Shipping.exe"
    executable.parent.mkdir(parents=True)
    image = bytearray(512)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", image, 0x80 + 24, 0x20B)
    executable.write_bytes(image)
    (root / "Engine").mkdir()
    paks = root / "Project/Content/Paks"
    paks.mkdir(parents=True)
    (paks / "game.pak").write_bytes(b"pak")
    config = root / "Config"
    config.mkdir()
    (config / "DefaultEngine.ini").write_text(
        "r.Streaming.PoolSize=7600\n", encoding="utf-8"
    )
    game = Game(
        id=f"steam-{app_id}",
        name="Synthetic Unreal",
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=1,
        physical_size_gb=1,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id=app_id,
        executable_path="Project/Binaries/Win64/Project-Win64-Shipping.exe",
        data_source="Steam",
    )
    profiles = GameOptimizationProfileRepository(tmp_path / "games")
    profiles.save(GameOptimizationProfile.default(app_id))
    mango = MangoHudProfileRepository(
        tmp_path / "games", log_root=tmp_path / "saved-logs"
    )
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    runner_path = tmp_path / "game-optimization-run"
    runner_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runner_path.chmod(0o755)
    localconfig = tmp_path / "localconfig.vdf"
    integration = RunnerIntegration(
        runner_path, steam_config_paths=(localconfig,)
    )
    command = integration.steam_command(app_id).replace('"', '\\"')
    localconfig.write_text(
        '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { '
        f'"apps" {{ "{app_id}" {{ "LaunchOptions" "{command}" }} }} '
        '} } } } }',
        encoding="utf-8",
    )

    class MangoDetector:
        def detect(self, steam_type: str = "native") -> MangoHudAvailability:
            return MangoHudAvailability(True, steam_type, message="MangoHud detected")

    detector = MangoDetector()
    launch_calls: list[str] = []

    class LauncherStub:
        def launch(self, launched: Game) -> tuple[str, ...]:
            launch_calls.append(launched.id)

            def execute(
                _executable: str, _argv: list[str], environment: dict[str, str]
            ) -> int:
                active = sessions.load(app_id)
                assert active is not None
                assert environment["MANGOHUD_CONFIGFILE"] == str(active.config_path)
                rows = [
                    "MangoHud v0.8.1",
                    "time,fps,frametime,cpu_load,gpu_load,ram,vram,gpu_temp",
                ]
                for index in range(320):
                    rows.append(
                        f"{index / 10:.1f},50,20,42,98,4096,7800,72"
                    )
                active.log_directory.joinpath("baseline.csv").write_text(
                    "\n".join(rows) + "\n", encoding="utf-8"
                )
                return 0

            result = runner_main(
                ["--appid", app_id, "--", "/synthetic/game"],
                repository=profiles,
                mangohud_repository=mango,
                baseline_sessions=sessions,
                detector=RuntimeToolDetector(which=lambda _name: None),
                executor=execute,
                report_root=tmp_path / "reports",
            )
            assert result == 0
            return ("steam", "-applaunch", app_id)

    launch = LauncherStub()
    mango_integration = MangoHudLaunchIntegration(
        mango,
        detector,  # type: ignore[arg-type]
        application_config_root=tmp_path / "MangoHud",
    )
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        game_launcher=launch,
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        mangohud_repository=mango,
        mangohud_detector=detector,  # type: ignore[arg-type]
        mangohud_launch_integration=mango_integration,
        optimization_profile_repository=profiles,
        runner_integration=integration,
        proton_tweaks_repository=ProtonTweaksRepository(tmp_path / "games"),
        initial_games=(game,),
        auto_refresh=False,
    )
    controller._baseline_sessions = sessions
    controller._system_info = {
        "cpu": "Synthetic CPU", "gpu": "Synthetic GPU",
        "ram_gb": 16.0, "vram_gb": 8.0,
    }
    try:
        started = controller.recordOptimizationBaseline(game.id)
        assert started["success"] is True
        assert launch_calls == [game.id]
        deadline = time.monotonic() + 5
        result: dict[str, object] = {}
        while time.monotonic() < deadline:
            controller._optimization_controller._poll_baseline_sessions()
            controller._optimization_controller._poll_analysis_jobs()
            QCoreApplication.processEvents()
            result = controller.getGameOptimizationAnalysis(game.id)
            if result.get("baselineSession", {}).get("status") == "completed":
                break
            time.sleep(0.01)
    finally:
        controller.shutdown()

    assert result["baselineAvailable"] is True
    assert result["measurement"]["quality"] == "high"
    assert result["measurement"]["samples"] == 320
    assert result["bottleneck"]["conclusion"] == "vram_pressure"
    assert any(
        item["id"] == "unreal_streaming_pool"
        for item in result["candidates"]
    )
    assert sessions.load_measurement(app_id, slot="before") is not None


def test_controller_saves_proton_tweaks_and_updates_combined_preview(
    tmp_path: Path,
) -> None:
    game = replace(demo_games()[0], steam_app_id="224760")
    optimization_repository = GameOptimizationProfileRepository(tmp_path / "games")
    proton_repository = ProtonTweaksRepository(tmp_path / "games")
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=optimization_repository,
        proton_tweaks_repository=proton_repository,
        initial_games=(game,),
        auto_refresh=False,
    )
    try:
        saved = controller.saveProtonTweaks(
            game.id,
            {
                "toggles": {
                    "use_wined3d": True,
                    "proton_log": True,
                },
                "optiscalerFsr4Update": False,
            },
        )
        preview = controller.getOptimizationProfile(game.id)
    finally:
        controller.shutdown()

    assert saved["success"] is True
    assert saved["environment"] == {
        "PROTON_USE_WINED3D": "1",
        "PROTON_LOG": "1",
    }
    assert preview["launchPlan"]["environment"]["PROTON_LOG"] == "1"
    assert "PROTON_USE_WINED3D=1" in preview["protonOverrides"]


def test_gamescope_profile_removes_and_rejects_mangohud_fps_limit(
    tmp_path: Path,
) -> None:
    game = replace(demo_games()[0], steam_app_id="224760")
    optimization_repository = GameOptimizationProfileRepository(tmp_path / "games")
    mangohud_repository = MangoHudProfileRepository(
        tmp_path / "games", log_root=tmp_path / "logs"
    )
    mangohud_repository.save(
        replace(MangoHudProfile.default("224760"), fps_limit=90)
    )

    class ToolDetector:
        def detect(self, *, refresh: bool = False) -> tuple[RuntimeToolAvailability, RuntimeToolAvailability]:
            del refresh
            return (
                RuntimeToolAvailability("GameMode", False),
                _tool("Gamescope", "/usr/bin/gamescope", ("-w", "-h", "-W", "-H", "-r", "-S", "-F", "-f", "-b")),
            )

    class MangoDetector:
        def detect(self, steam_type: str = "native") -> MangoHudAvailability:
            return MangoHudAvailability(
                True, steam_type, supported_keys=tuple(KNOWN_CONFIG_KEYS),
                message="MangoHud detected",
            )

    mango_detector = MangoDetector()
    integration = MangoHudLaunchIntegration(
        mangohud_repository, mango_detector,  # type: ignore[arg-type]
        application_config_root=tmp_path / "MangoHud",
        native_steam_environment=lambda: {"MANGOHUD": "1"},
    )
    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=optimization_repository,
        mangohud_repository=mangohud_repository,
        mangohud_detector=mango_detector,  # type: ignore[arg-type]
        mangohud_launch_integration=integration,
        runtime_tool_detector=ToolDetector(),  # type: ignore[arg-type]
        initial_games=(game,), auto_refresh=False,
    )
    try:
        saved = controller.saveOptimizationProfile(game.id, {
            "preset": "custom", "gameCategory": "platformer_2d",
            "userGoal": "stable_image", "targetFpsMode": "manual",
            "targetFps": 90, "gamemodeEnabled": False,
            "gamescopeEnabled": True, "gamescopeMode": "native",
        })
        assert saved["success"] is True
        assert saved["launchPlan"]["fpsLimitOwner"] == "gamescope"
        assert saved["launchPlan"]["command"].count("-r") == 1
        assert "--framerate-limit" not in saved["launchPlan"]["command"]
        assert mangohud_repository.load("224760").fps_limit is None
        assert "fps_limit=" not in mangohud_repository.config_path("224760").read_text()

        mango_saved = controller.saveMangoHudProfile(game.id, {
            "enabled": True, "preset": "custom", "metrics": ["fps"],
            "fpsLimit": 120,
        })
        assert mango_saved["success"] is True
        assert mango_saved["fpsLimit"] == 0
        assert mango_saved["fpsLimitOwner"] == "gamescope"
        assert mangohud_repository.load("224760").fps_limit is None
    finally:
        controller.shutdown()


def test_development_installer_has_no_sudo_or_venv_path() -> None:
    content = (Path(__file__).parents[1] / "scripts" / "install-game-optimization-runner.sh").read_text(encoding="utf-8")
    assert "sudo" not in content
    assert ".venv" not in content
    assert "--no-deps" in content
    assert "--no-build-isolation" in content
