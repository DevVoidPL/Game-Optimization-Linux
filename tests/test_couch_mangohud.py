"""Couch MangoHud menu: real config written through the controller, one FPS
limiter owner, preset/metrics mapping, Save and Cancel."""

from __future__ import annotations

from dataclasses import replace
import re

import pytest
from PySide6.QtCore import Q_ARG, QCoreApplication, QMetaObject, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config
from game_optimization_linux.services.mangohud import KNOWN_CONFIG_KEYS, MangoHudAvailability, MangoHudLaunchIntegration
from game_optimization_linux.services.optimization_runtime import RuntimeToolAvailability

GAMESCOPE = RuntimeToolAvailability("Gamescope", True, "/usr/bin/gamescope", "3.16.28",
                                    ("-w", "-h", "-W", "-H", "-r", "-S", "-F", "-f", "-b"), "",
                                    ("linear", "fsr"), ("auto",))


class _Tools:
    def detect(self, *, refresh: bool = False):
        return RuntimeToolAvailability("GameMode", False), GAMESCOPE


class _Mango:
    def detect(self, steam_type: str = "native") -> MangoHudAvailability:
        return MangoHudAvailability(True, steam_type, supported_keys=tuple(KNOWN_CONFIG_KEYS), message="MangoHud detected")


def _pump(rounds: int = 4) -> None:
    for _ in range(rounds):
        QCoreApplication.processEvents()


def _call(root, method, *args) -> None:
    QMetaObject.invokeMethod(root, method, Qt.DirectConnection, *[Q_ARG("QVariant", a) for a in args])
    _pump(3)


def _plain(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


@pytest.fixture()
def setup(tmp_path):
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.models import FilesystemType, Game, Launcher
    from game_optimization_linux.providers import DemoGameProvider
    from game_optimization_linux.services import (
        GameOptimizationProfileRepository, MangoHudProfileRepository, SettingsStore,
    )

    (tmp_path / "game").mkdir()
    game = Game(id="steam-440002", name="Synthetic", launcher=Launcher.STEAM, install_path=tmp_path / "game",
                logical_size_gb=1, physical_size_gb=1, filesystem=FilesystemType.BTRFS, compression_available=True,
                steam_app_id="440002", data_source="Steam")
    mango = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    detector = _Mango()
    controller = AppController(
        game_provider=DemoGameProvider((game,)), initial_games=(game,), auto_refresh=False,
        settings_store=SettingsStore(tmp_path / "s.json"),
        optimization_profile_repository=GameOptimizationProfileRepository(tmp_path / "games"),
        mangohud_repository=mango, mangohud_detector=detector,  # type: ignore[arg-type]
        mangohud_launch_integration=MangoHudLaunchIntegration(
            mango, detector, application_config_root=tmp_path / "MangoHud",  # type: ignore[arg-type]
            native_steam_environment=lambda: {"MANGOHUD": "1"}),
        runtime_tool_detector=_Tools())
    assert controller.openGame(game.id)
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(config.QML_DIR / "couch" / "CouchGameDetails.qml")))
    root = component.createWithInitialProperties({"controller": controller, "width": 1920, "height": 1080})
    assert root is not None, [e.toString() for e in component.errors()]
    _pump()
    yield root, controller, mango, game
    root.deleteLater()
    _pump()
    controller.shutdown()
    del component, engine


def _rows(root) -> list[str]:
    return [row["id"] for row in _plain(root.property("mangoHudRows"))]


def _save(root, **draft) -> None:
    _call(root, "openMangoHudOverlay")
    for key, value in draft.items():
        root.setProperty(key, value)
    _pump()
    root.setProperty("mangoHudRow", _rows(root).index("save"))
    _call(root, "handleMangoHudAction", "Confirm")


def _config(mango) -> str:
    return mango.config_path("440002").read_text()


def _set_gamescope_limit(controller, game, limit: int | None) -> None:
    values = {"preset": "custom", "gamescopeEnabled": limit is not None, "gamescopeMode": "custom" if limit else "disabled",
              "targetFpsMode": "manual" if limit else "unlimited", "targetFps": limit or 60}
    assert controller.saveOptimizationProfile(game.id, values)["success"] is True


def test_single_fps_limiter_owner_is_resolved_without_erasing_mangohud(setup) -> None:
    root, controller, mango, game = setup
    _save(root, mangoHudDraftEnabled=True, mangoHudPreset="basic", mangoHudFpsLimit=90)
    assert "fps_limit=90" in _config(mango)                     # Gamescope off: MangoHud limits

    _set_gamescope_limit(controller, game, 60)                    # Gamescope -r 60 takes over
    assert "fps_limit=" not in _config(mango)
    assert mango.load("440002").fps_limit == 90                   # preference kept
    assert "-r" in controller.getOptimizationProfile(game.id)["launchPlan"]["command"]
    _call(root, "loadMangoHudProfile")
    fps_row = next(r for r in _plain(root.property("mangoHudRows")) if r["id"] == "fps")
    assert fps_row["enabled"] is False and "60" in fps_row["value"]

    _save(root, mangoHudPosition="bottom-right")                  # saving MangoHud keeps it inactive
    assert "fps_limit=" not in _config(mango) and mango.load("440002").fps_limit == 90

    _set_gamescope_limit(controller, game, None)                  # Gamescope off again
    assert "fps_limit=90" in _config(mango)


@pytest.mark.parametrize(("preset", "expected", "absent"), [
    ("fps_only", ["fps", "frametime"], ["gpu_temp", "ram"]),
    ("basic", ["fps", "gpu_temp", "cpu_temp", "ram", "vram"], ["gpu_core_clock"]),
])
def test_presets_write_real_metrics(setup, preset, expected, absent) -> None:
    root, _controller, mango, _game = setup
    _save(root, mangoHudDraftEnabled=True, mangoHudPreset=preset)
    lines = set(_config(mango).splitlines())
    assert all(key in lines for key in expected), lines
    assert not any(line.startswith(key) for key in absent for line in lines)


def test_custom_metric_groups_and_cancel(setup) -> None:
    root, _controller, mango, _game = setup
    _call(root, "openMangoHudOverlay")
    root.setProperty("mangoHudDraftEnabled", True)
    root.setProperty("mangoHudPreset", "basic")
    root.setProperty("mangoHudRow", _rows(root).index("preset"))
    _call(root, "handleMangoHudAction", "NavigateRight")          # basic -> extended
    _call(root, "handleMangoHudAction", "NavigateRight")          # extended -> custom (starts from basic set)
    rows = _rows(root)
    assert {"metric-fps", "metric-usage", "metric-temps", "metric-memory"} <= set(rows)
    root.setProperty("mangoHudRow", rows.index("metric-temps"))
    _call(root, "handleMangoHudAction", "Confirm")                # temperatures off
    root.setProperty("mangoHudRow", _rows(root).index("cancel"))
    _call(root, "handleMangoHudAction", "Confirm")
    assert not mango.config_path("440002").exists()               # Cancel wrote nothing
    _save(root, mangoHudDraftEnabled=True, mangoHudPreset="custom",
          mangoHudDraftMetrics=["fps", "frametime", "ram", "vram"])
    lines = set(_config(mango).splitlines())
    assert {"ram", "vram"} <= lines and "gpu_temp" not in lines and "cpu_stats=0" in lines


def test_mangohud_menu_uses_couch_glyphs_only() -> None:
    source = (config.QML_DIR / "couch" / "CouchGameDetails.qml").read_text(encoding="utf-8")
    start = source.index("// ---- MangoHud menu")
    block = source[start:source.index("// ---- Dedicated GameMode and Gamescope menus")]
    overlay = source[source.index("visible: page.mangoHudOverlayOpen"):]
    overlay = overlay[:overlay.index("CouchOverlayFrame {")]
    for name in ("Preset", "Position", "TextSize", "FpsLimit", "Frametime", "Usage", "Temperature", "Memory", "Mangohud"):
        assert f"couchGlyph{name}" in block + overlay
    assert '"symbol"' not in block and "symbol:" not in overlay
    icons = set(re.findall(r"App\.UiIcons\.(\w+)", block + overlay))
    assert all(name.startswith("couchGlyph") for name in icons), icons
