"""Measured optimization UI with the real AppController: Custom/Manual entries,
profile names and the Quiet -> Low Power migration. Temporary data only."""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QGuiApplication  # noqa: E402

import qml_runtime_probe as probe  # noqa: E402
from qml_runtime_probe import QML_ROOT, _item, _settle, _view  # noqa: E402
from game_optimization_linux.controllers.app_controller import AppController  # noqa: E402
from game_optimization_linux.models.optimization_profile import OPTIMIZATION_PRESETS  # noqa: E402
from game_optimization_linux.providers import DemoGameProvider, DemoSystemProvider  # noqa: E402
from game_optimization_linux.providers.local import ConfiguredGameProvider  # noqa: E402
from game_optimization_linux.services import ManualGameStore, MockTaskService, SettingsStore  # noqa: E402
from game_optimization_linux.services.measured_optimization import manual  # noqa: E402
from test_manual_games import _provider  # noqa: E402
from test_measured_optimization_qml import _eval, close, errors, text, visible  # noqa: E402


@pytest.fixture
def app():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def controller(tmp_path):
    store = ManualGameStore(tmp_path / "config" / "manual.json")
    provider = ConfiguredGameProvider(DemoGameProvider(()), _provider(tmp_path, store))
    value = AppController(game_provider=provider, manual_game_store=store, task_service=MockTaskService(),
                          settings_store=SettingsStore(tmp_path / "settings.json"), system_provider=DemoSystemProvider(),
                          initial_games=(), demo_mode=False, auto_refresh=False)
    yield value
    value.shutdown()


def add(controller, tmp_path, kind):
    root = tmp_path / f"Game {kind}"
    root.mkdir()
    values = {"name": f"Disposable {kind}", "installDirectory": str(root)}
    if kind == "native":
        exe = root / "game"
        exe.write_bytes(b"\x7fELF" + b"\0" * 60)
        exe.chmod(0o755)
    else:
        exe = root / "Game.exe"
        exe.write_bytes(b"MZ" + b"\0" * 62)
        if kind == "wine":
            runner = tmp_path / "bin" / "wine"
        elif kind == "proton":
            runner = tmp_path / "GE-Proton-UI" / "proton"
        if kind in ("wine", "proton"):
            runner.parent.mkdir(parents=True, exist_ok=True)
            runner.write_text("#!/bin/sh\n")
            runner.chmod(0o755)
            values["runnerCommand"] = str(runner)
            if kind == "proton":
                (runner.parent / "version").write_text("1 GE-Proton-UI\n")
    values["executable"] = str(exe)
    created = controller.saveManualGame(values)
    assert created["success"], created
    return created["id"]


@pytest.mark.parametrize("kind, runner", [("native", "Native Linux"), ("wine", "Wine"), ("proton", "Proton")])
def test_manual_supported_paths_show_identity_runner_and_workflow(app, tmp_path, controller, kind, runner) -> None:
    if not manual.tree_tracking_available():
        pytest.skip("needs a systemd user manager")
    game_id = add(controller, tmp_path, kind)
    assert controller.getGameCapabilities(game_id)["optimization"]["available"] is True
    view, root = probe_panel(app, controller, game_id)
    try:
        assert text(root, "measuredIdentity") == game_id
        assert text(root, "measuredRunner").startswith(runner)
        assert text(root, "measuredManualSupport").startswith("Supported: GameOpti Custom launch")
        assert visible(root, "measuredGoalChoices") and not visible(root, "measuredUnsupported")
        assert errors() == [], errors()
    finally:
        close(app, view)


def test_manual_without_runner_or_scope_is_unsupported_with_reason(app, tmp_path, controller, monkeypatch) -> None:
    game_id = add(controller, tmp_path, "noRunner")
    capability = controller.getGameCapabilities(game_id)["optimization"]
    assert capability["available"] is False and "no configured Wine or Proton runner" in capability["reason"]
    view, root = probe_panel(app, controller, game_id)
    try:
        assert visible(root, "measuredUnsupported") and "GameOpti does not guess one" in text(root, "measuredUnsupported")
        assert not visible(root, "measuredGoalChoices") and not visible(root, "measuredStart")
    finally:
        close(app, view)
    native = add(controller, tmp_path, "native")
    monkeypatch.setattr(manual, "tree_tracking_available", lambda **_kw: False)
    view, root = probe_panel(app, controller, native)
    try:
        assert "No host systemd user scope" in text(root, "measuredUnsupported")
        assert controller.startMeasuredOptimization(native, "balanced", {})["success"] is False
    finally:
        close(app, view)


def probe_panel(app, controller, game_id):
    probe.MESSAGES.clear()
    view, root = _view(app, "pages/details/MeasuredOptimizationPanel.qml", 1100, 1800, initial_properties={
        "controller": controller, "gameData": {"id": game_id}, "couchMode": False})
    _settle(app, 6)
    view._translator = None
    return view, root


def test_quiet_profile_is_shown_and_saved_as_low_power(app, tmp_path, controller) -> None:
    if not manual.tree_tracking_available():
        pytest.skip("needs a systemd user manager")
    game_id = add(controller, tmp_path, "native")
    game = controller._resolve_game(game_id, show_error=False)
    key = manual.optimization_key(game)
    path = controller._optimization_profile_repository.path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "app_id": key, "preset": "quiet", "target_fps": 45,
                                "target_fps_mode": "manual", "gamemode_enabled": False}))
    profile = controller.getOptimizationProfile(game_id)
    assert profile["preset"] == "low_power" and profile["targetFps"] == 45
    view, root = _view(app, "pages/details/OptimizationTab.qml", 1300, 1600, initial_properties={
        "controller": controller, "gameData": {"id": game_id, "launcher": "Manual"}, "section": "performance"})
    _settle(app, 12)
    try:
        assert _eval(root, "tab.preset") == "low_power"
        labels = json.loads(_eval(root, "JSON.stringify(tab.presetLabels)"))
        assert labels == ["Automatic", "Maximum Performance", "Balanced", "Low Power", "Lowest Latency",
                                "Stable Image", "Best Quality", "Custom"]
        # One goal selection: no preset button grid; the saved configuration is a status in the workflow.
        names = {item.objectName() for item in probe._descendants(root)}
        assert not names & {"optimizationProfileCard", "presetChooser", "currentConfigurationBadge"}
        assert not any(str(item.property("text")) == "User goal" for item in probe._descendants(root))
        assert text(root, "measuredCurrentConfiguration") == "Low Power"
        _eval(root, 'tab.choosePreset("automatic")'); _eval(root, "tab.saveProfile()")
        _settle(app, 8)
        panel = next(item for item in probe._descendants(root) if item.objectName() == "measuredOptimizationPanel")
        from PySide6.QtCore import QMetaObject
        QMetaObject.invokeMethod(panel, "refresh"); _settle(app, 6)
        assert text(root, "measuredCurrentConfiguration") == "Automatic"
        assert visible(root, "automaticProfileNote") and "may recommend no change" in text(root, "automaticProfileNote")
        assert visible(root, "measuredOptimizationPanel")
    finally:
        view.close(); view.deleteLater(); _settle(app, 4)


def test_desktop_and_couch_profile_lists_match_the_model() -> None:
    desktop = (QML_ROOT / "pages/details/OptimizationTab.qml").read_text()
    couch = (QML_ROOT / "couch/CouchGameDetails.qml").read_text()
    for source, name in ((desktop, "presetValues"), (couch, "optimizationPresets")):
        values = re.search(name + r": \[([^\]]*)\]", source).group(1)
        assert tuple(re.findall(r'"([a-z_]+)"', values)) == OPTIMIZATION_PRESETS
    assert '"quiet"' not in re.search(r"presetValues: \[([^\]]*)\]", desktop).group(1)
    assert "measured-optimization" in couch and "couchMeasuredPanel" in couch


def test_couch_page_overlay_focus_and_back(app, tmp_path, controller) -> None:
    if not manual.tree_tracking_available():
        pytest.skip("needs a systemd user manager")
    game_id = add(controller, tmp_path, "wine")
    game = next(item for item in controller.games if item["id"] == game_id)
    for width, height in ((1280, 720), (1920, 1080)):
        probe.MESSAGES.clear()
        view, page = _view(app, "couch/CouchGameDetails.qml", width, height,
                           initial_properties={"controller": controller, "game": game})
        _settle(app, 8)
        try:
            actions = json.loads(_eval(page, "JSON.stringify(page.optimizationActions().map(function(a) { return a.id }))"))
            assert "measured-optimization" in actions
            assert _eval(page, "page.optimizationActions()[0].title").startswith("Configuration: ")
            _eval(page, "page.openMeasuredOverlay()")
            _settle(app, 8)
            panel = _item(page, "couchMeasuredPanel")
            assert panel.isVisible() and _eval(page, "page.measuredOverlayOpen") is True
            assert _eval(page, "measuredPanel.padFocusedItem.objectName") == "measuredGoal_maximum_performance"
            _eval(page, 'page.handleAction("NavigateDown")'); _settle(app, 3)
            assert _eval(page, "measuredPanel.padFocusedItem.objectName") == "measuredGoal_balanced"
            _eval(page, 'page.handleAction("Confirm")'); _settle(app, 6)
            assert _eval(page, "measuredPanel.goal") == "balanced"
            assert _eval(page, "measuredPanel.padFocusedItem.activeFocus") is True
            _eval(page, "measuredPanel.technicalOpen = true")
            _eval(page, 'page.handleAction("Back")'); _settle(app, 3)
            assert _eval(page, "measuredPanel.technicalOpen") is False and _eval(page, "page.measuredOverlayOpen") is True
            _eval(page, 'page.handleAction("Back")'); _settle(app, 3)
            assert _eval(page, "page.measuredOverlayOpen") is False
            assert [m for m in probe.MESSAGES if "MeasuredOptimization" in m] == []
        finally:
            view.close(); view.deleteLater(); _settle(app, 4)
