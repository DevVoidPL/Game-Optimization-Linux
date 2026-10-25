"""Desktop nested Gamescope: capabilities, argument mapping, launch order, test run."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import signal
import threading
import time
from typing import Any

import pytest

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import GameOptimizationProfile, MangoHudProfile
from game_optimization_linux.models.optimization_profile import (
    GAMESCOPE_PROFILE_FIELDS,
    sharpness_cli_to_percent,
    sharpness_percent_to_cli,
)
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.runner import main as runner_main
from game_optimization_linux.services import (
    GameOptimizationProfileRepository,
    GamescopeTestRun,
    MangoHudConfigWriter,
    MangoHudProfileRepository,
    MockTaskService,
    OptimizationLaunchPlanner,
    RuntimeToolAvailability,
    SettingsStore,
)
from game_optimization_linux.services import mangohud_preview as preview_module
from game_optimization_linux.services.optimization_runtime import (
    GAMESCOPE_KNOWN_OPTIONS,
    detect_gamescope_wsi,
    parse_gamescope_backends,
    parse_gamescope_help,
    parse_gamescope_version,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gamescope"
APP_ID = "224760"
GAME = ["/games/Game", "--flag"]


def _help(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _gamescope(help_name: str = "help-3.16.30.txt", **changes: Any) -> RuntimeToolAvailability:
    text = _help(help_name)
    options, filters, scalers = parse_gamescope_help(text)
    tool = RuntimeToolAvailability(
        "Gamescope", True, "/usr/bin/gamescope", "3.16.30", options,
        "Gamescope is available", filters, scalers, parse_gamescope_backends(text),
    )
    return replace(tool, **changes)


def _gamemode() -> RuntimeToolAvailability:
    return RuntimeToolAvailability("GameMode", False)


def _profile(**changes: Any) -> GameOptimizationProfile:
    base = replace(
        GameOptimizationProfile.default(APP_ID),
        preset="custom", gamescope_enabled=True, gamescope_mode="custom",
        target_fps_mode="unlimited",
    )
    return replace(base, **changes)


def _flags(profile: GameOptimizationProfile, tool: RuntimeToolAvailability | None = None, **kwargs: Any) -> list[str]:
    plan = OptimizationLaunchPlanner().build(
        profile, GAME, gamemode=_gamemode(), gamescope=tool or _gamescope(), **kwargs
    )
    return list(plan.gamescope_wrapper[1:-1])


def _value(flags: list[str], option: str) -> str:
    return flags[flags.index(option) + 1]


# A. Parser -----------------------------------------------------------------


def test_a_parses_local_3_16_help_with_values_backends_and_hdr_names() -> None:
    text = _help("help-3.16.30.txt")
    options, filters, scalers = parse_gamescope_help(text)
    assert parse_gamescope_version(text) == "3.16.30"
    assert filters == ("linear", "nearest", "fsr", "nis", "pixel", "sgsr")
    assert scalers == ("auto", "integer", "fit", "fill", "stretch")
    assert parse_gamescope_backends(text) == ("auto", "drm", "sdl", "openvr", "headless", "wayland")
    assert "--hdr-itm-enabled" in options and "--hdr-itm-enable" not in options
    for option in ("--framerate-limit", "--display-index", "--backend", "-o", "-m",
                   "--force-windows-fullscreen", "--mangoapp", "--sharpness", "--fsr-sharpness",
                   "--sdr-gamut-wideness", "--force-grab-cursor", "-C", "-s", "-e"):
        assert option in options
    # Debug, realtime and embedded-only flags are never known options.
    assert not {"--rt", "--immediate-flips", "--debug-hud", "--prefer-output"} & set(GAMESCOPE_KNOWN_OPTIONS)


def test_a_parses_legacy_help_with_older_names() -> None:
    options, filters, scalers = parse_gamescope_help(_help("help-legacy.txt"))
    assert "--hdr-itm-enable" in options and "--hdr-itm-enabled" not in options
    assert "--fsr-sharpness" in options and "--sharpness" not in options
    assert filters == ("linear", "nearest", "fsr", "nis", "pixel")
    assert scalers == ("auto", "integer", "fit", "fill", "stretch")
    assert parse_gamescope_backends(_help("help-legacy.txt")) == ()
    for missing in ("--framerate-limit", "--display-index", "--backend", "--sdr-gamut-wideness"):
        assert missing not in options


def test_a_wsi_variable_comes_only_from_the_installed_manifest(tmp_path: Path) -> None:
    layer = tmp_path / "implicit_layer.d"
    layer.mkdir()
    assert detect_gamescope_wsi((layer,)) == ()
    (layer / "VkLayer_FROG_gamescope_wsi.x86_64.json").write_text(json.dumps({
        "file_format_version": "1.0.0",
        "layer": {"name": "VK_LAYER_FROG_gamescope_wsi_x86_64",
                  "enable_environment": {"ENABLE_GAMESCOPE_WSI": "1"},
                  "disable_environment": {"DISABLE_GAMESCOPE_WSI": "1"}},
    }), encoding="utf-8")
    assert detect_gamescope_wsi((layer,)) == ("ENABLE_GAMESCOPE_WSI", "1")


# B. Never an unsupported flag ----------------------------------------------


EVERYTHING = dict(
    gamescope_input_width=1280, gamescope_input_height=720, gamescope_display_index=1,
    gamescope_force_windows_fullscreen=True, gamescope_backend="wayland",
    gamescope_max_scale=2.0, gamescope_nested_refresh=120, gamescope_framerate_limit=60,
    gamescope_unfocused_refresh=15, gamescope_filter="fsr", gamescope_sharpness=4,
    gamescope_adaptive_sync=True, gamescope_grab_keyboard=True, gamescope_hdr=True,
    gamescope_hdr_sdr_content_nits=300, gamescope_sdr_gamut_wideness=0.5,
    gamescope_hdr_itm=True, gamescope_hdr_itm_sdr_nits=120, gamescope_hdr_itm_target_nits=1000,
    gamescope_force_grab_cursor=True, gamescope_hide_cursor_delay=3000,
    gamescope_mouse_sensitivity=1.5, gamescope_expose_wayland=True,
    gamescope_steam_integration=True,
)


@pytest.mark.parametrize("help_name", ["help-3.16.30.txt", "help-legacy.txt"])
def test_b_generator_never_emits_a_flag_missing_from_capabilities(help_name: str) -> None:
    tool = _gamescope(help_name)
    changes = dict(EVERYTHING)
    if "--sharpness" not in tool.supported_options:
        changes["gamescope_sharpness"] = 4  # --fsr-sharpness alias is used
    plan = OptimizationLaunchPlanner().build(
        _profile(**changes), GAME, gamemode=_gamemode(), gamescope=tool,
        mangohud_activation_owner="steam_environment",
    )
    flags = [value for value in plan.gamescope_wrapper[1:-1] if value.startswith("-")]
    assert set(flags) <= set(tool.supported_options)
    if help_name == "help-legacy.txt":
        assert "--hdr-itm-enable" in flags and "--fsr-sharpness" in flags
        assert "--framerate-limit" not in flags and "--backend" not in flags
        assert any("does not support --framerate-limit" in warning for warning in plan.warnings)


def test_b_unreadable_help_keeps_basic_integration_only() -> None:
    tool = _gamescope(supported_options=(), supported_filters=(), supported_backends=())
    assert tool.help_verified is False
    flags = _flags(_profile(gamescope_adaptive_sync=False, gamescope_force_grab_cursor=True,
                            gamescope_window_mode="borderless"), tool)
    assert flags == ["-W", "1920", "-H", "1080", "-b"]


# C. Mapping ----------------------------------------------------------------


def test_c_resolution_window_mode_and_backend() -> None:
    flags = _flags(_profile(gamescope_input_width=1280, gamescope_input_height=720,
                            gamescope_output_width=2560, gamescope_output_height=1440,
                            gamescope_window_mode="windowed", gamescope_backend="sdl",
                            gamescope_display_index=0, gamescope_force_windows_fullscreen=True))
    assert flags == ["-w", "1280", "-h", "720", "-W", "2560", "-H", "1440",
                     "--display-index", "0", "--force-windows-fullscreen", "--backend", "sdl"]
    assert _flags(_profile(gamescope_input_auto=True, gamescope_output_auto=True)) == ["-f"]
    auto_output = _flags(_profile(gamescope_output_auto=True, gamescope_input_width=1600,
                                  gamescope_input_height=900))
    assert auto_output[:4] == ["-w", "1600", "-h", "900"] and "-W" not in auto_output
    assert "-b" in _flags(_profile(gamescope_window_mode="borderless"))
    fullscreen = _flags(_profile(gamescope_window_mode="fullscreen"))
    assert fullscreen.count("-f") == 1 and "-b" not in fullscreen


def test_c_scaling_filter_and_inverted_sharpness() -> None:
    flags = _flags(_profile(gamescope_scaler="integer", gamescope_filter="nearest", gamescope_max_scale=3))
    assert _value(flags, "-S") == "integer" and _value(flags, "-F") == "nearest"
    assert _value(flags, "-m") == "3" and "--sharpness" not in flags
    # UI 100 % = most sharpening = CLI 0; 0 % = least = CLI 20.
    assert (sharpness_percent_to_cli(100), sharpness_percent_to_cli(0), sharpness_percent_to_cli(75)) == (0, 20, 5)
    assert sharpness_cli_to_percent(5) == 75 and sharpness_percent_to_cli(-1) == -1
    fsr = _flags(_profile(gamescope_filter="fsr", gamescope_sharpness=sharpness_percent_to_cli(75)))
    assert _value(fsr, "--sharpness") == "5"
    sgsr = _flags(_profile(gamescope_filter="sgsr", gamescope_sharpness=5))
    assert "--sharpness" not in sgsr


def test_c_refresh_fps_limit_unfocused_and_vrr() -> None:
    plan = OptimizationLaunchPlanner().build(
        _profile(gamescope_nested_refresh=120, gamescope_framerate_limit=60,
                 gamescope_unfocused_refresh=15, gamescope_adaptive_sync=True),
        GAME, gamemode=_gamemode(), gamescope=_gamescope(),
    )
    flags = list(plan.gamescope_wrapper[1:-1])
    assert _value(flags, "-r") == "120" and _value(flags, "--framerate-limit") == "60"
    assert _value(flags, "-o") == "15" and "--adaptive-sync" in flags
    assert (plan.fps_limit_owner, plan.fps_limit) == ("gamescope", 60)
    assert "--immediate-flips" not in flags and "--rt" not in flags


def test_c_hdr_itm_and_input_mapping_with_wsi_environment() -> None:
    tool = _gamescope(wsi_environment=("ENABLE_GAMESCOPE_WSI", "1"))
    plan = OptimizationLaunchPlanner().build(
        _profile(**{key: value for key, value in EVERYTHING.items() if "hdr" in key or "sdr" in key},
                 gamescope_grab_keyboard=True, gamescope_force_grab_cursor=True,
                 gamescope_hide_cursor_delay=2500, gamescope_mouse_sensitivity=0.5,
                 gamescope_expose_wayland=True, gamescope_steam_integration=True),
        GAME, gamemode=_gamemode(), gamescope=tool,
    )
    flags = list(plan.gamescope_wrapper[1:-1])
    assert "--hdr-enabled" in flags and "--hdr-itm-enabled" in flags
    assert _value(flags, "--hdr-sdr-content-nits") == "300"
    assert _value(flags, "--sdr-gamut-wideness") == "0.5"
    assert _value(flags, "--hdr-itm-sdr-nits") == "120"
    assert _value(flags, "--hdr-itm-target-nits") == "1000"
    assert {"-g", "--force-grab-cursor", "--expose-wayland", "-e"} <= set(flags)
    assert _value(flags, "-C") == "2500" and _value(flags, "-s") == "0.5"
    assert plan.environment == {"ENABLE_GAMESCOPE_WSI": "1"}
    no_hdr = OptimizationLaunchPlanner().build(
        _profile(), GAME, gamemode=_gamemode(), gamescope=tool
    )
    assert "ENABLE_GAMESCOPE_WSI" not in no_hdr.environment


# D. Validation and dependencies ---------------------------------------------


@pytest.mark.parametrize(("field", "value"), [
    ("gamescope_input_width", 100), ("gamescope_output_height", 20000),
    ("gamescope_framerate_limit", -1), ("gamescope_framerate_limit", 1001),
    ("gamescope_nested_refresh", -30), ("gamescope_unfocused_refresh", 5000),
    ("gamescope_max_scale", 0.5), ("gamescope_max_scale", 9),
    ("gamescope_hdr_sdr_content_nits", 50), ("gamescope_hdr_itm_sdr_nits", 1001),
    ("gamescope_hdr_itm_target_nits", 100), ("gamescope_hdr_itm_target_nits", 10001),
    ("gamescope_sdr_gamut_wideness", 1.5), ("gamescope_hide_cursor_delay", 50),
    ("gamescope_mouse_sensitivity", 11), ("gamescope_display_index", -2),
    ("gamescope_backend", "drm"), ("gamescope_backend", "openvr"),
])
def test_d_rejects_out_of_range_and_non_desktop_values(field: str, value: object) -> None:
    data = GameOptimizationProfile.default(APP_ID).to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        GameOptimizationProfile.from_dict(data)


def test_d_zero_means_off_never_an_active_limit() -> None:
    profile = _profile(gamescope_framerate_limit=0, gamescope_unfocused_refresh=0,
                       gamescope_nested_refresh=0, gamescope_hide_cursor_delay=0)
    assert _flags(profile) == ["-W", "1920", "-H", "1080", "-f"]


def test_d_hdr_sub_options_require_hdr_and_itm_requires_hdr_and_itm() -> None:
    sub = dict(gamescope_hdr_sdr_content_nits=300, gamescope_sdr_gamut_wideness=0.3,
               gamescope_hdr_itm=True, gamescope_hdr_itm_sdr_nits=120,
               gamescope_hdr_itm_target_nits=1500)
    without_hdr = _flags(_profile(gamescope_hdr=False, **sub))
    assert not [flag for flag in without_hdr if "hdr" in flag or "sdr" in flag]
    hdr_only = _flags(_profile(gamescope_hdr=True, **{**sub, "gamescope_hdr_itm": False}))
    assert "--hdr-enabled" in hdr_only and "--hdr-sdr-content-nits" in hdr_only
    assert not [flag for flag in hdr_only if "itm" in flag]
    with_itm = _flags(_profile(gamescope_hdr=True, **sub))
    assert {"--hdr-itm-enabled", "--hdr-itm-sdr-nits", "--hdr-itm-target-nits"} <= set(with_itm)


# E. Old profiles and round-trip ---------------------------------------------


def test_e_old_profile_produces_identical_arguments() -> None:
    old = {
        "schema_version": 1, "app_id": APP_ID, "preset": "custom",
        "target_fps_mode": "manual", "target_fps": 90, "gamescope_enabled": True,
        "gamescope_mode": "performance", "gamescope_input_width": 1280,
        "gamescope_input_height": 720, "gamescope_output_width": 1920,
        "gamescope_output_height": 1080, "gamescope_refresh_rate": 144,
        "gamescope_fullscreen": False, "gamescope_scaler": "fit",
        "gamescope_filter": "fsr", "gamescope_sharpness": 5,
        "gamescope_adaptive_sync": True, "gamescope_grab_keyboard": True,
        "gamescope_hdr": True, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    profile = GameOptimizationProfile.from_dict(old)
    tool = _gamescope(supported_options=(
        "-W", "-H", "-w", "-h", "-r", "--framerate-limit", "-f", "-b", "-S", "-F",
        "--display-index", "--sharpness", "--fsr-sharpness", "--adaptive-sync", "-g",
        "--hdr-enabled", "--mangoapp",
    ))
    plan = OptimizationLaunchPlanner().build(profile, GAME, gamemode=_gamemode(), gamescope=tool)
    # The argv the pre-change generator produced for this profile.
    assert plan.command == [
        "/usr/bin/gamescope", "-w", "1280", "-h", "720", "-W", "1920", "-H", "1080",
        "-r", "90", "-S", "fit", "-F", "fsr", "--sharpness", "5", "-b",
        "--adaptive-sync", "-g", "--hdr-enabled", "--", *GAME,
    ]
    assert plan.environment == {}


def test_e_new_and_unknown_fields_round_trip(tmp_path: Path) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    profile = _profile(**EVERYTHING)
    data = profile.to_dict()
    data["future_gamescope_option"] = {"level": 3}
    path = repository.path(APP_ID)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = repository.load(APP_ID)
    assert all(getattr(loaded, key) == getattr(profile, key) for key in EVERYTHING)
    repository.save(replace(loaded, target_fps=75))
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["future_gamescope_option"] == {"level": 3}
    assert stored["target_fps"] == 75 and stored["gamescope_hdr_itm_target_nits"] == 1000
    assert not list(path.parent.glob(".*"))  # atomic write leaves no temporary file


def test_e_new_profile_keeps_gamescope_off_with_neutral_defaults() -> None:
    profile = GameOptimizationProfile.default(APP_ID)
    assert profile.gamescope_enabled is False
    assert (profile.gamescope_framerate_limit, profile.gamescope_nested_refresh,
            profile.gamescope_display_index, profile.gamescope_backend,
            profile.gamescope_hdr_itm, profile.gamescope_steam_integration) == (0, 0, -1, "auto", False, False)


# F. Launcher order -----------------------------------------------------------


@pytest.mark.parametrize("game_argv", [
    ["/steam/steamapps/common/Proton/proton", "waitforexitandrun", "/games/Game.exe"],
    ["lutris", "lutris:rungameid/12"],
    ["heroic", "--no-gui", "heroic://launch/legendary/abc"],
    ["/home/user/Games/manual/start.sh", "--windowed"],
])
def test_f_gamescope_args_then_separator_then_existing_game_command(game_argv: list[str]) -> None:
    gamemode = RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun")
    plan = OptimizationLaunchPlanner().build(
        _profile(gamemode_enabled=True, gamescope_framerate_limit=60),
        game_argv, gamemode=gamemode, gamescope=_gamescope(),
    )
    command = plan.command
    separator = command.index("--")
    assert command[0] == "/usr/bin/gamescope"
    assert command[1:separator] == list(plan.gamescope_wrapper[1:-1])
    assert command.count("--") == 1 + game_argv.count("--")
    assert command[-len(game_argv):] == game_argv
    assert command[separator + 1:-len(game_argv)] == ["/usr/bin/gamemoderun"]


def test_f_runner_executes_gamescope_argv_for_steam_command(tmp_path: Path) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(_profile(gamescope_framerate_limit=45, gamescope_window_mode="borderless"))
    captured: dict[str, Any] = {}

    class Detector:
        def detect(self, *, refresh: bool = False):
            return _gamemode(), _gamescope()

    def execute(executable: str, argv: list[str], environment: dict[str, str]) -> int:
        captured.update(executable=executable, argv=list(argv))
        return 0

    assert runner_main(
        ["--appid", APP_ID, "--", "/game", "-dx12"], repository=repository,
        mangohud_repository=MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs"),
        detector=Detector(),  # type: ignore[arg-type]
        executor=execute, report_root=tmp_path / "reports",
    ) == 0
    argv = captured["argv"]
    assert argv[0] == "/usr/bin/gamescope" and argv[-2:] == ["/game", "-dx12"]
    assert argv[argv.index("--") - 3:argv.index("--")] == ["--framerate-limit", "45", "-b"]


# G. MangoHud + Gamescope ------------------------------------------------------


def test_g_mangoapp_is_the_single_overlay_and_keeps_per_game_config() -> None:
    config = "/home/user/.config/game-optimization-linux/games/224760/MangoHud.conf"
    plan = OptimizationLaunchPlanner().build(
        _profile(), GAME, gamemode=_gamemode(), gamescope=_gamescope(),
        existing_environment={"MANGOHUD": "1", "MANGOHUD_CONFIG": "fps"},
        mangohud_activation_owner="steam_environment", mangohud_config_path=config,
    )
    assert plan.command.count("--mangoapp") == 1
    assert plan.mangohud_activation_owner == "mangoapp"
    assert "mangohud" not in [Path(value).name for value in plan.command]
    assert plan.environment["MANGOHUD_CONFIGFILE"] == config
    assert {"MANGOHUD", "MANGOHUD_CONFIG"} <= set(plan.wrapper_environment_removed)
    process = plan.process_environment({"MANGOHUD": "1", "MANGOHUD_CONFIG": "fps", **plan.environment})
    assert "MANGOHUD" not in process and process["MANGOHUD_CONFIGFILE"] == config
    assert not any(value.startswith("MANGOHUD=") for value in plan.command)


def test_g_without_mangoapp_the_existing_mechanism_stays_with_a_warning() -> None:
    tool = _gamescope(supported_options=tuple(
        option for option in _gamescope().supported_options if option != "--mangoapp"
    ))
    plan = OptimizationLaunchPlanner().build(
        _profile(), GAME, gamemode=_gamemode(), gamescope=tool,
        existing_environment={"MANGOHUD": "1"},
        mangohud_activation_owner="steam_environment", mangohud_config_path="/x/MangoHud.conf",
    )
    assert "--mangoapp" not in plan.command
    assert plan.mangohud_activation_owner == "steam_environment"
    assert "MANGOHUD" not in plan.wrapper_environment_removed
    assert any("no --mangoapp" in warning for warning in plan.warnings)


def test_g_runner_uses_generated_mangohud_profile_with_mangoapp(tmp_path: Path) -> None:
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    repository.save(_profile())
    mango = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    mango_profile = MangoHudProfile.default(APP_ID).apply_preset("basic")
    mango.save(mango_profile)
    MangoHudConfigWriter().write(mango_profile, mango.config_path(APP_ID))
    captured: dict[str, Any] = {}

    class Detector:
        def detect(self, *, refresh: bool = False):
            return _gamemode(), _gamescope()

    def execute(_executable: str, argv: list[str], environment: dict[str, str]) -> int:
        captured.update(argv=list(argv), environment=dict(environment))
        return 0

    import os
    previous = os.environ.get("MANGOHUD")
    os.environ["MANGOHUD"] = "1"
    try:
        assert runner_main(
            ["--appid", APP_ID, "--", "/game"], repository=repository, mangohud_repository=mango,
            detector=Detector(),  # type: ignore[arg-type]
            executor=execute, report_root=tmp_path / "reports",
        ) == 0
    finally:
        if previous is None:
            os.environ.pop("MANGOHUD", None)
        else:
            os.environ["MANGOHUD"] = previous
    report = json.loads((tmp_path / "reports" / f"{APP_ID}.json").read_text())
    assert report["mangoHudActivationOwner"] == "mangoapp"
    assert captured["argv"].count("--mangoapp") == 1
    assert captured["environment"]["MANGOHUD_CONFIGFILE"] == str(mango.config_path(APP_ID))
    assert "MANGOHUD" not in captured["environment"]


# H. Gamescope test run --------------------------------------------------------


class _Process:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.pid = 5151
        self.returncode: int | None = None
        self._result = returncode
        self._stderr = stderr
        self._done = threading.Event()

    def finish(self) -> None:
        self._done.set()

    def communicate(self) -> tuple[str, str]:
        self._done.wait(5)
        self.returncode = self._result
        return "", self._stderr

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._done.wait(timeout)
        self.returncode = self._result
        return self._result

    def send_signal(self, _sig: int) -> None:
        self.finish()


class _Popen:
    def __init__(self, process: _Process | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.process = process or _Process()

    def __call__(self, argv: list[str], **kwargs: Any) -> _Process:
        self.calls.append((list(argv), kwargs))
        return self.process


def _idle(run: GamescopeTestRun) -> None:
    deadline = time.monotonic() + 5
    while run.running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not run.running


_VKCUBE_HELP = "Usage:\n  vkcube\t[--wsi <xcb|xlib|wayland|display>]\n"


def _vkcube_help(argv: list[str], **kwargs: Any):
    import subprocess

    assert argv[1:] == ["--help"] and kwargs.get("shell") in (None, False)
    return subprocess.CompletedProcess(argv, 1, _VKCUBE_HELP, "")


def _test_run(popen: _Popen, *, vkcube: bool = True) -> GamescopeTestRun:
    return GamescopeTestRun(
        environment={"HOME": "/home/user", "MANGOHUD": "1"},
        which=lambda name: "/usr/bin/vkcube" if name == "vkcube" and vkcube else None,
        popen=popen,
        runner=_vkcube_help,
    )


def test_h_test_run_uses_argv_blocks_duplicates_and_stops_only_its_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen = _Popen()
    run = _test_run(popen)
    killed: list[tuple[int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        popen.process.finish()

    monkeypatch.setattr(preview_module.os, "killpg", killpg)
    finished: list[Any] = []
    run.on_finished = finished.append
    assert run.start("/usr/bin/gamescope", ["-W", "1920", "-f"], {"MANGOHUD_CONFIGFILE": "/c.conf"}).success
    argv, kwargs = popen.calls[0]
    # vkcube presents through XWayland inside nested Gamescope.
    assert argv == ["/usr/bin/gamescope", "-W", "1920", "-f", "--", "/usr/bin/vkcube", "--wsi", "xcb"]
    assert kwargs["shell"] is False and kwargs["start_new_session"] is True
    assert "MANGOHUD" not in kwargs["env"] and kwargs["env"]["MANGOHUD_CONFIGFILE"] == "/c.conf"
    assert run.start("/usr/bin/gamescope", ["-f"]).code == "already_running"
    assert len(popen.calls) == 1
    assert run.stop() is True
    _idle(run)
    assert killed == [(5151, signal.SIGTERM)]
    assert finished[0].success is True and run.stop() is False


def test_h_missing_vkcube_and_process_error_are_reported() -> None:
    popen = _Popen()
    missing = _test_run(popen, vkcube=False).start("/usr/bin/gamescope", ["-f"])
    assert (missing.success, missing.code) == (False, "vkcube_missing") and popen.calls == []
    failing = _Popen(_Process(returncode=1, stderr="gamescope: Failed to create backend"))
    run = _test_run(failing)
    finished: list[Any] = []
    run.on_finished = finished.append
    run.start("/usr/bin/gamescope", ["-f"])
    failing.process.finish()
    _idle(run)
    assert finished[0].code == "process_failed" and "status 1" in finished[0].message
    assert _test_run(_Popen()).start("/usr/bin/gamescope", ["--", "sh"]).code == "invalid"


def test_h_flatpak_test_goes_through_flatpak_spawn() -> None:
    popen = _Popen()

    class Host:
        def host_command_available(self, name: str) -> bool:
            return name in {"gamescope", "vkcube"}

        def host_command_help(self, name: str) -> str:
            return _VKCUBE_HELP if name == "vkcube" else ""

    run = GamescopeTestRun(
        environment={"FLATPAK_ID": "io.example.App"},
        which=lambda name: "/usr/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
        popen=popen, host_service=Host(),
    )
    assert run.start("/usr/bin/gamescope", ["-f"], {"ENABLE_GAMESCOPE_WSI": "1"}).success
    argv = popen.calls[0][0]
    assert argv[:2] == ["/usr/bin/flatpak-spawn", "--host"]
    assert "--unset-env=MANGOHUD" in argv and "--env=ENABLE_GAMESCOPE_WSI=1" in argv
    assert argv[-6:] == ["gamescope", "-f", "--", "vkcube", "--wsi", "xcb"]
    popen.process.finish()
    _idle(run)


def test_h_controller_test_saves_nothing_and_hdr_needs_confirmation(tmp_path: Path) -> None:
    game = replace(demo_games()[0], steam_app_id=APP_ID)
    repository = GameOptimizationProfileRepository(tmp_path / "games")

    class Detector:
        def detect(self, *, refresh: bool = False):
            return _gamemode(), _gamescope()

    popen = _Popen()
    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=repository, initial_games=(game,),
        runtime_tool_detector=Detector(),  # type: ignore[arg-type]
        mangohud_repository=MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs"),
        gamescope_test_run=_test_run(popen), auto_refresh=False,
    )
    try:
        draft = {"preset": "custom", "gamescopeEnabled": True, "gamescopeMode": "native",
                 "gamescopeHdr": True, "gamescopeFramerateLimit": 60,
                 "gamescopeSharpnessPercent": 75, "gamescopeFilter": "fsr"}
        result = controller.startGamescopeTest(game.id, draft, False)
        assert result["success"] is True and result["running"] is True
        argv = popen.calls[0][0]
        assert "--hdr-enabled" not in argv and "--framerate-limit" in argv
        assert argv[argv.index("--sharpness") + 1] == "5"
        assert controller.startGamescopeTest(game.id, draft, True)["code"] == "already_running"
        popen.process.finish()
        _idle(controller._gamescope_test_run)
        popen.process = _Process()
        assert controller.startGamescopeTest(game.id, draft, True)["success"] is True
        assert "--hdr-enabled" in popen.calls[1][0]
        popen.process.finish()
        _idle(controller._gamescope_test_run)
        assert not repository.path(APP_ID).exists()

        preview = controller.previewOptimizationProfile(game.id, draft)
        assert preview["gamescopeSharpnessPercent"] == 75
        assert preview["gamescopeSteamLaunchOptions"].startswith("gamescope ")
        assert preview["gamescopeSteamLaunchOptions"].endswith(" -- %command%")
        assert "--hdr-enabled" in preview["gamescopeArguments"]
        assert preview["gamescopeHdrReadiness"]["state"] in {"ready", "warning"}
    finally:
        controller.shutdown()


def test_restore_resets_only_gamescope_fields(tmp_path: Path) -> None:
    game = replace(demo_games()[0], steam_app_id=APP_ID)
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    stored = replace(_profile(**EVERYTHING), gamemode_enabled=True, game_category="retro",
                     target_fps=75, extra_fields={"future": 1})
    repository.save(stored)

    class Detector:
        def detect(self, *, refresh: bool = False):
            return _gamemode(), _gamescope()

    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_profile_repository=repository, initial_games=(game,),
        runtime_tool_detector=Detector(),  # type: ignore[arg-type]
        mangohud_repository=MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs"),
        auto_refresh=False,
    )
    try:
        assert controller.resetGamescopeSettings(game.id)["success"] is True
    finally:
        controller.shutdown()
    reset = repository.load(APP_ID)
    defaults = GameOptimizationProfile.default(APP_ID)
    assert all(getattr(reset, name) == getattr(defaults, name) for name in GAMESCOPE_PROFILE_FIELDS)
    assert (reset.gamemode_enabled, reset.game_category, reset.target_fps) == (True, "retro", 75)
    assert reset.extra_fields == {"future": 1}


@pytest.mark.parametrize(('changes', 'expected'), [
    ({'gamescope_nested_refresh': 120}, 120),
    ({'gamescope_framerate_limit': 60}, 60),
    ({'gamescope_nested_refresh': 120, 'gamescope_framerate_limit': 60}, 60),
    ({}, 0),
])
def test_unlimited_target_still_respects_explicit_gamescope_limiter(tmp_path: Path, changes: dict, expected: int) -> None:
    from game_optimization_linux.services.mangohud import KNOWN_CONFIG_KEYS, MangoHudAvailability, MangoHudLaunchIntegration
    game = replace(demo_games()[0], steam_app_id=APP_ID)
    optimization = GameOptimizationProfileRepository(tmp_path / 'games')
    mango = MangoHudProfileRepository(tmp_path / 'games', log_root=tmp_path / 'logs')
    mango.save(replace(MangoHudProfile.default(APP_ID).apply_preset('basic'), fps_limit=90))

    class Detector:
        def detect(self, *, refresh=False):
            return _gamemode(), _gamescope()

    class MangoDetector:
        def detect(self, steam_type='native'):
            return MangoHudAvailability(True, steam_type, supported_keys=tuple(KNOWN_CONFIG_KEYS), message='MangoHud detected')

    detector = MangoDetector()
    controller = AppController(
        game_provider=DemoGameProvider((game,)), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / 'settings.json'),
        optimization_profile_repository=optimization, mangohud_repository=mango,
        initial_games=(game,), auto_refresh=False, runtime_tool_detector=Detector(),
        mangohud_detector=detector,
        mangohud_launch_integration=MangoHudLaunchIntegration(mango, detector,
            application_config_root=tmp_path / 'MangoHud', native_steam_environment=lambda: {'MANGOHUD': '1'}),
    )
    try:
        profile = _profile(**changes)
        optimization.save(profile)
        controller._sync_mangohud_fps_owner(game)
        status = controller.getMangoHudProfile(game.id)
        plan = controller.getOptimizationProfile(game.id)['launchPlan']
        assert status['gamescopeFpsLimit'] == expected
        assert status['fpsLimitOwner'] == plan['fpsLimitOwner'] == ('gamescope' if expected else 'mangohud')
        assert mango.load(APP_ID).fps_limit == 90
        assert ('fps_limit=90' in mango.config_path(APP_ID).read_text()) == (expected == 0)
        # Changing the central limit alone must preserve overlay appearance and logging.
        if not expected:
            before = mango.load(APP_ID).to_dict()
            saved = controller.saveMangoHudProfile(game.id, {'fpsLimit': 75})
            assert saved['success']
            after = mango.load(APP_ID).to_dict()
            assert after['fps_limit'] == 75
            for key in ('metrics', 'position', 'font_size', 'log_duration', 'logging_enabled', 'text_color'):
                assert after[key] == before[key]
    finally:
        controller.shutdown()
