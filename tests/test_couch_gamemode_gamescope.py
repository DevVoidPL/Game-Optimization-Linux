"""GameMode and Gamescope: canonical settings -> exact argv, capability gating,
dedicated Couch menus (routing, save, cancel)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QCoreApplication, QMetaObject, QObject, Qt, QUrl, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config
from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.models.optimization_profile import (
    GAMESCOPE_FILTERS, GAMESCOPE_SCALERS, GAMESCOPE_WINDOW_MODES,
)
from game_optimization_linux.services.optimization_advisor import OptimizationAdvisor
from game_optimization_linux.services.optimization_runtime import (
    OptimizationLaunchPlanner, RuntimeToolAvailability, parse_gamescope_help, parse_gamescope_version,
)

# Excerpt of `gamescope --help` from gamescope 3.16.28 (no SGSR).
HELP = """
  -W, --output-width             output width
  -H, --output-height            output height
  -w, --nested-width             game width
  -h, --nested-height            game height
  -r, --nested-refresh           game refresh rate (frames per second)
  -S, --scaler                   upscaler type (auto, integer, fit, fill, stretch)
  -F, --filter                   upscaler filter (linear, nearest, fsr, nis, pixel)
  --sharpness, --fsr-sharpness   upscaler sharpness from 0 (max) to 20 (min)
  --hdr-enabled                  enable HDR output
  --framerate-limit              Set a simple framerate limit.
  --mangoapp                     Launch with the mangoapp (mangohud) performance overlay enabled.
  --adaptive-sync                Enable adaptive sync if available (variable rate refresh)
  -b, --borderless               make the window borderless
  -f, --fullscreen               make the window fullscreen
  -g, --grab                     grab the keyboard
  --force-grab-cursor            always use relative mouse mode
"""
OPTIONS, FILTERS, SCALERS = parse_gamescope_help(HELP)
GAMEMODE = RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun")
GAMESCOPE = RuntimeToolAvailability("Gamescope", True, "/usr/bin/gamescope", "3.16.28",
                                    OPTIONS, "", FILTERS, SCALERS)
ENV = {"PATH": "/usr/bin"}


def _profile(**values) -> GameOptimizationProfile:
    return replace(GameOptimizationProfile.default("123"), **values)


def _argv(profile: GameOptimizationProfile, gamescope: RuntimeToolAvailability = GAMESCOPE) -> list[str]:
    plan = OptimizationLaunchPlanner().build(
        profile, ["%command%"], gamemode=GAMEMODE, gamescope=gamescope,
        existing_environment=ENV, allow_placeholder=True)
    return plan.command


def _gamescope(**values) -> GameOptimizationProfile:
    base = dict(gamescope_enabled=True, gamescope_mode="custom", target_fps_mode="manual", target_fps=60,
                gamescope_input_width=1920, gamescope_input_height=1080,
                gamescope_output_width=3840, gamescope_output_height=2160, gamescope_filter="fsr",
                gamescope_window_mode="fullscreen")
    base.update(values)
    return _profile(**base)


def test_help_parser_and_version_reflect_the_local_release() -> None:
    assert {"--sharpness", "--adaptive-sync", "-g", "--hdr-enabled", "-r", "-F", "-S"} <= set(OPTIONS)
    assert FILTERS == ("linear", "nearest", "fsr", "nis", "pixel")
    assert "sgsr" not in FILTERS
    assert SCALERS == ("auto", "integer", "fit", "fill", "stretch")
    assert parse_gamescope_version("[gamescope] [\x1b[0;34mInfo\x1b[0m]  console: gamescope version 3.16.28 (gcc 16.2.1)") == "3.16.28"


@pytest.mark.parametrize("enabled", [False, True])
def test_gamemode_wrapper_follows_the_saved_choice(enabled: bool) -> None:
    argv = _argv(_profile(gamemode_enabled=enabled))
    assert argv.count("/usr/bin/gamemoderun") == (1 if enabled else 0)
    assert argv[-1] == "%command%"


def test_gamescope_disabled_adds_no_wrapper() -> None:
    assert "/usr/bin/gamescope" not in _argv(_profile(gamescope_enabled=False))


def test_1080p_to_4k_fsr_60_fullscreen_builds_separate_argv_elements() -> None:
    argv = _argv(_gamescope(gamemode_enabled=True))
    assert argv == [
        "/usr/bin/gamescope", "-w", "1920", "-h", "1080", "-W", "3840", "-H", "2160",
        "-r", "60", "-F", "fsr", "-f", "--", "/usr/bin/gamemoderun", "%command%",
    ]
    assert all(" " not in part for part in argv)  # no shell-joined fragments


@pytest.mark.parametrize(("mode", "expected"), [("windowed", []), ("borderless", ["-b"]), ("fullscreen", ["-f"])])
def test_window_modes_are_mutually_exclusive(mode: str, expected: list[str]) -> None:
    argv = _argv(_gamescope(gamescope_window_mode=mode))
    flags = argv[:argv.index("--")]
    assert [flag for flag in flags if flag in ("-f", "-b")] == expected


def test_sharpness_only_for_fsr_and_nis() -> None:
    assert "--sharpness" in _argv(_gamescope(gamescope_filter="fsr", gamescope_sharpness=5))
    assert "--sharpness" not in _argv(_gamescope(gamescope_filter="nearest", gamescope_sharpness=5))


@pytest.mark.parametrize("values", [{"gamescope_filter": "sgsr"}, {"gamescope_hdr": True}])
def test_locally_unsupported_options_are_never_generated(values: dict) -> None:
    tool = GAMESCOPE if "gamescope_filter" in values else replace(
        GAMESCOPE, supported_options=tuple(o for o in OPTIONS if o != "--hdr-enabled"))
    with pytest.raises(ValueError, match="does not support"):
        _argv(_gamescope(**values), tool)


def test_model_values_are_canonical_and_legacy_profiles_migrate() -> None:
    data = _gamescope().to_dict()
    assert data["gamescope_window_mode"] in GAMESCOPE_WINDOW_MODES
    assert data["gamescope_filter"] in GAMESCOPE_FILTERS and data["gamescope_scaler"] in GAMESCOPE_SCALERS
    legacy = {"schema_version": 1, "app_id": "123", "gamescope_fullscreen": False}
    migrated = GameOptimizationProfile.from_dict(legacy)
    assert (migrated.gamescope_window_mode, migrated.gamescope_fullscreen) == ("borderless", False)
    assert GameOptimizationProfile.from_dict({"schema_version": 1, "app_id": "123"}).gamescope_window_mode == "fullscreen"


def test_explicit_feature_choice_survives_the_automatic_preset() -> None:
    profile = _profile(preset="automatic", gamemode_enabled=True, manual_overrides={"gamemode": True},
                       gamescope_enabled=True, gamescope_mode="custom")
    profile = replace(profile, manual_overrides={"gamemode": True, "gamescope": True})
    resolved = OptimizationAdvisor().resolve_preset(
        profile, None, gamemode_available=True, gamescope_available=True).profile
    assert resolved.gamemode_enabled is True and resolved.gamescope_enabled is True
    assert resolved.preset == "automatic"


# ---- Couch menus ------------------------------------------------------------

class FakeController(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.saved: list[dict] = []
        self.profile = {
            "success": True, "preset": "automatic", "gamemodeEnabled": False, "gamescopeEnabled": False,
            "gamescopeMode": "disabled", "gamescopeInputWidth": 1920, "gamescopeInputHeight": 1080,
            "gamescopeOutputWidth": 1920, "gamescopeOutputHeight": 1080, "gamescopeScaler": "auto",
            "gamescopeFilter": "linear", "gamescopeSharpness": -1, "gamescopeWindowMode": "fullscreen",
            "targetFpsMode": "automatic", "targetFps": 60, "manualOverrides": {}, "displays": [],
            "gamemode": {"available": True, "message": ""},
            "gamescope": {"available": True, "version": "3.16.28", "supportedOptions": list(OPTIONS),
                          "supportedFilters": list(FILTERS), "supportedScalers": list(SCALERS)},
            "launchActivation": {"state": "active", "message": ""},
        }

    @Slot(str, result="QVariant")
    def getOptimizationProfile(self, game_id):
        return self.profile

    @Slot(str, "QVariantMap", result="QVariantMap")
    def previewOptimizationProfile(self, game_id, values):
        return {"success": True, "launchPlan": {"gameModeWrapper": ["/usr/bin/gamemoderun"] if values.get("gamemodeEnabled") else []}}

    @Slot(str, "QVariantMap", result="QVariantMap")
    def saveOptimizationProfile(self, game_id, values):
        self.saved.append(dict(values))
        return {"success": True}

    selectedGame = property(lambda self: {"id": "g1", "name": "Game", "launcher": "Steam", "launchAllowed": True})


def _call(obj, method, *args):
    result = QMetaObject.invokeMethod(obj, method, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                      *[Q_ARG("QVariant", a) for a in args])
    for _ in range(3):
        QCoreApplication.processEvents()
    return result.toVariant() if hasattr(result, "toVariant") else result


@pytest.fixture()
def details():
    controller = FakeController()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(config.QML_DIR / "couch" / "CouchGameDetails.qml")))
    root = component.createWithInitialProperties({"controller": controller, "width": 1920, "height": 1080})
    assert root is not None, [e.toString() for e in component.errors()]
    root.setProperty("game", {"id": "g1", "name": "Game", "launcher": "Steam", "launchAllowed": True})
    root.setProperty("optimizationData", controller.profile)
    for _ in range(4):
        QCoreApplication.processEvents()
    yield root, controller
    root.deleteLater()
    QCoreApplication.processEvents()
    del component, engine


def test_gamemode_and_gamescope_cards_open_different_menus(details) -> None:
    root, _controller = details
    root.setProperty("headerIndex", 1)
    _call(root, "activateHeader")
    assert root.property("gameModeOverlayOpen") is True and root.property("optimizationOverlayOpen") is False
    _call(root, "closeGameModeOverlay")
    root.setProperty("headerIndex", 2)
    _call(root, "activateHeader")
    assert root.property("gamescopeOverlayOpen") is True and root.property("optimizationOverlayOpen") is False


def test_gamemode_cancel_saves_nothing_and_save_persists(details) -> None:
    root, controller = details
    _call(root, "openGameModeOverlay")
    _call(root, "handleGameModeAction", "Confirm")          # toggle on
    root.setProperty("gameModeRow", 2)
    _call(root, "handleGameModeAction", "Confirm")          # Cancel
    assert controller.saved == [] and root.property("gameModeOverlayOpen") is False
    _call(root, "openGameModeOverlay")
    _call(root, "handleGameModeAction", "Confirm")
    root.setProperty("gameModeRow", 1)
    _call(root, "handleGameModeAction", "Confirm")          # Save
    saved = controller.saved[-1]
    assert saved["gamemodeEnabled"] is True and saved["manualOverrides"]["gamemode"] is True
    assert saved["preset"] == "automatic"


def _rows(root) -> list[str]:
    value = root.property("gamescopeRows")
    value = value.toVariant() if hasattr(value, "toVariant") else value
    return [row["id"] for row in value]


def test_gamescope_menu_hides_unsupported_values_and_saves_canonical_ids(details) -> None:
    root, controller = details
    _call(root, "openGamescopeOverlay")
    assert "sgsr" not in _call(root, "gsOptions", "filter")
    rows = _rows(root)
    assert rows[:3] == ["enabled", "input", "output"] and rows[-2:] == ["save", "cancel"]
    assert "hdr" in rows and "vrr" in rows and "grab" in rows      # supported locally
    assert "sharpness" not in rows                                  # linear filter
    root.setProperty("gamescopeDraft", {"enabled": True, "input": "1920x1080", "output": "3840x2160",
                                        "scaler": "auto", "filter": "fsr", "sharpness": -1, "fps": 60,
                                        "window": "fullscreen", "vrr": False, "grab": False, "hdr": False})
    rows = _rows(root)
    assert "sharpness" in rows                                      # FSR uses sharpness
    root.setProperty("gamescopeRow", rows.index("save"))
    _call(root, "handleGamescopeAction", "Confirm")          # Save
    saved = controller.saved[-1]
    assert (saved["gamescopeWindowMode"], saved["gamescopeFilter"]) == ("fullscreen", "fsr")
    assert (saved["gamescopeInputWidth"], saved["gamescopeOutputWidth"], saved["targetFps"]) == (1920, 3840, 60)
    assert saved["manualOverrides"]["gamescope"] is True and saved["gamescopeMode"] == "custom"


# ---- Real boundary: Couch QML -> AppController -> model/repository -> planner --

class _Detector:
    def detect(self, *, refresh: bool = False):
        return GAMEMODE, GAMESCOPE


@pytest.fixture()
def real_details(tmp_path):
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.models import FilesystemType, Game, Launcher
    from game_optimization_linux.providers import DemoGameProvider
    from game_optimization_linux.services import GameOptimizationProfileRepository, SettingsStore

    (tmp_path / "game").mkdir()
    game = Game(id="steam-440001", name="Synthetic", launcher=Launcher.STEAM, install_path=tmp_path / "game",
                logical_size_gb=1, physical_size_gb=1, filesystem=FilesystemType.BTRFS, compression_available=True,
                steam_app_id="440001", data_source="Steam")
    repository = GameOptimizationProfileRepository(tmp_path / "games")
    controller = AppController(game_provider=DemoGameProvider((game,)), initial_games=(game,), auto_refresh=False,
                               settings_store=SettingsStore(tmp_path / "s.json"),
                               optimization_profile_repository=repository, runtime_tool_detector=_Detector())
    assert controller.openGame("steam-440001")
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(config.QML_DIR / "couch" / "CouchGameDetails.qml")))
    root = component.createWithInitialProperties({"controller": controller, "width": 1920, "height": 1080})
    assert root is not None, [e.toString() for e in component.errors()]
    for _ in range(4):
        QCoreApplication.processEvents()
    yield root, repository, "440001"
    root.deleteLater()
    QCoreApplication.processEvents()
    controller.shutdown()
    del component, engine


def _save_draft(root, **draft) -> bool:
    _call(root, "openGamescopeOverlay")
    values = {"enabled": True, "input": "1920x1080", "output": "3840x2160", "scaler": "auto", "fps": 60,
              "window": "fullscreen", "vrr": False, "grab": False, "hdr": False}
    values.update(draft)
    root.setProperty("gamescopeDraft", values)
    root.setProperty("gamescopeRow", _rows(root).index("save"))
    _call(root, "handleGamescopeAction", "Confirm")
    return root.property("gamescopeOverlayOpen") is False


@pytest.mark.parametrize(("filter_id", "sharpness", "flag"), [
    ("linear", -1.0, None), ("fsr", 0.0, "0"), ("fsr", 5.0, "5"), ("fsr", 10.0, "10"),
    ("fsr", 15.0, "15"), ("fsr", 20.0, "20"),
])
def test_qml_sharpness_values_save_as_integers(real_details, filter_id, sharpness, flag) -> None:
    root, repository, app_id = real_details
    assert _save_draft(root, filter=filter_id, sharpness=sharpness)
    saved = repository.load(app_id)
    assert type(saved.gamescope_sharpness) is int
    assert saved.gamescope_sharpness == (int(sharpness) if filter_id == "fsr" else -1)
    argv = _argv(saved)
    assert (argv[argv.index("--sharpness") + 1] if "--sharpness" in argv else None) == flag


def test_fractional_sharpness_is_rejected_and_cancel_saves_nothing(real_details) -> None:
    root, repository, app_id = real_details
    before = repository.load(app_id).to_dict()
    assert not _save_draft(root, filter="fsr", sharpness=10.5)   # overlay stays open: validation error
    _call(root, "closeGamescopeOverlay")
    _call(root, "openGamescopeOverlay")
    root.setProperty("gamescopeDraft", dict(root.property("gamescopeDraft").toVariant(), enabled=True))
    root.setProperty("gamescopeRow", _rows(root).index("cancel"))
    _call(root, "handleGamescopeAction", "Confirm")
    after = repository.load(app_id).to_dict()
    before.pop("updated_at"); after.pop("updated_at")
    assert after == before


@pytest.mark.parametrize("value", [10.5, float("nan"), float("inf"), True, "ten", 21, -2])
def test_controller_rejects_non_whole_sharpness(value) -> None:
    from game_optimization_linux.controllers.optimization_controller import OptimizationController
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value).is_integer():
        GameOptimizationProfile.default("1")  # range is enforced by the model below
        with pytest.raises(ValueError):
            replace(GameOptimizationProfile.default("1"), gamescope_sharpness=OptimizationController._whole_number(value, "s"))
    else:
        with pytest.raises(ValueError, match="whole number"):
            OptimizationController._whole_number(value, "gamescope_sharpness")
