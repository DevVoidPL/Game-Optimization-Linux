from __future__ import annotations

from dataclasses import replace
import json
import inspect
from pathlib import Path
import subprocess

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import GameOptimizationProfile, MangoHudProfile
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.runner import main as runner_main
import game_optimization_linux.runner as runner_module
import game_optimization_linux.services.optimization_runtime as runtime_module
from game_optimization_linux.services import (
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
    assert any("display index 1" in reason for reason in plan.reasons)
    assert ["--display-index", "1"] == plan.command[plan.command.index("--display-index"):plan.command.index("--display-index") + 2]


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
) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(
        replace(
            GameOptimizationProfile.default("224760"),
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
    monkeypatch.setenv("FLATPAK_ID", "io.github.DevVoidPL.GameOptimizationLinux")
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
        ["--appid", "224760", "--", "/nix/store/game/bin/game", "a b"],
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
    report = json.loads((tmp_path / "reports/224760.json").read_text())
    assert report["executionTransport"] == "flatpak-spawn-host"


def test_runner_and_launch_planner_never_use_a_shell() -> None:
    source = inspect.getsource(runner_module) + inspect.getsource(runtime_module)
    assert "shell=True" not in source
    assert "bash -c" not in source
    assert "eval(" not in source


def test_stable_steam_command_never_changes_with_profile(tmp_path: Path) -> None:
    integration = RunnerIntegration(tmp_path / "game-optimization-run")
    command = integration.steam_command("224760")
    assert command == f'"{tmp_path / "game-optimization-run"}" --appid 224760 -- %command%'


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
