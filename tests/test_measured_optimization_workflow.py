"""Measured optimization controller: whole workflow with synthetic sessions.

Temporary roots only; no game, launcher or Steam library is touched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from game_optimization_linux.controllers.measured_optimization_controller import MeasuredOptimizationController
from game_optimization_linux.models import Launcher
from game_optimization_linux.services import tweaks_capabilities
from game_optimization_linux.services.game_optimization import OptimizationChangeService
from game_optimization_linux.services.mangohud import MangoHudProfileRepository
from game_optimization_linux.services.measured_optimization.store import MeasuredOptimizationStore
from game_optimization_linux.services.optimization_profiles import GameOptimizationProfileRepository as OptimizationProfileRepository
from game_optimization_linux.services.performance_session import BaselineSessionRepository
from game_optimization_linux.services.proton_tweaks import ProtonTweaksRepository
from tests.test_measured_optimization_core import write_log


class Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class FakeOptimizationController:
    def __init__(self, app):
        self.app = app
        self.running = False

    def _post_environment(self, key):
        return {}

    def _process_detector(self):
        return SimpleNamespace(game_process_running=lambda _id: self.running)

    def _active_optimization_change(self, game):
        return self.app._optimization_change_service.active_change(game)

    def _start(self, kind):
        session = self.app._baseline_sessions.create(self.app.key, self.app.game.id, kind=kind)
        return {"success": True, "baselineSession": session.to_dict()}

    def recordOptimizationBaseline(self, game_id):
        if self._active_optimization_change(self.app.game) is not None:
            return {"success": False, "error": "pending"}
        return self._start("baseline")

    def recordOptimizationComparison(self, game_id):
        if self._active_optimization_change(self.app.game) is None:
            return {"success": False, "error": "No optimization change is pending comparison"}
        return self._start("comparison")

    def cancelOptimizationMeasurement(self, game_id):
        session = self.app._baseline_sessions.load(self.app.key)
        self.app._baseline_sessions.fail(self.app.key, "Recording cancelled", session.id)
        return {"success": True}


def make_app(tmp_path: Path, monkeypatch, *, launcher=Launcher.STEAM, variant="native"):
    monkeypatch.setattr(tweaks_capabilities, "runner_capabilities",
                        lambda game, home=None: {"selection": "unknown", "runnerPath": "", "support": {}})
    games = tmp_path / "config" / "games"
    key = "224760" if launcher is Launcher.STEAM else "heroic-legendary-fixture"
    game = SimpleNamespace(id=key, profile_key=key, name="Fixture", launcher=launcher, launcher_variant=variant,
                           data_source=launcher.value, executable_path="", library_available=True,
                           update_in_progress=False, steam_build_id="777" if launcher is Launcher.STEAM else None,
                           steam_app_id=key if launcher is Launcher.STEAM else None, install_path=tmp_path / "game",
                           library_path=tmp_path / "library")
    app = SimpleNamespace(
        key=key, game=game, _domain_games={game.id: game},
        _resolve_game=lambda game_id, show_error=False: game if game_id == game.id else None,
        _optimization_profile_repository=OptimizationProfileRepository(games),
        _proton_tweaks_repository=ProtonTweaksRepository(games),
        _mangohud_repository=MangoHudProfileRepository(games),
        _optiscaler_service=SimpleNamespace(profile_repository=None),
        _runtime_tool_detector=SimpleNamespace(detect=lambda: (
            SimpleNamespace(available=True), SimpleNamespace(available=True, supported_filters=("linear", "fsr")))),
        _system_info={"cpuModel": "Fixture CPU", "gpuModel": "Fixture GPU", "gpuDriver": "Mesa 25", "vramGb": 16},
        _optimization_analyses={}, _baseline_sessions=BaselineSessionRepository(tmp_path / "sessions"),
        _optimization_change_service=OptimizationChangeService(tmp_path / "changes", process_checker=lambda _g: False),
        _launcher_integration=SimpleNamespace(status=lambda g: {"state": app.integration}, launcher_running=lambda g: False,
                                              apply=lambda g: app.launcher_applies.append(g.id)),
        _runner_report_path=lambda k: tmp_path / "reports" / f"{k}.json",
        _gpu_memory_service=SimpleNamespace(apply=lambda g, v: None),
        _optimization_applied_changes={}, _optimization_comparisons={}, optimizationAnalysisChanged=Signal(),
        integration="applied", launcher_applies=[])
    app._optimization_controller = FakeOptimizationController(app)
    (tmp_path / "game").mkdir()
    controller = MeasuredOptimizationController(app, MeasuredOptimizationStore(tmp_path / "state"))
    return app, controller


def finish_session(app, controller, session_id, frametime, *, wrappers=None):
    session = app._baseline_sessions.load(app.key)
    assert session.id == session_id
    write_log(Path(session.log_directory) / "Game_2026.csv", frametime)
    if wrappers is not None:
        report = app._runner_report_path(app.key)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"baselineSessionId": session_id, "wrappers": wrappers}))
    controller.on_measurement_processed(app.game, app.key, session)
    app._baseline_sessions.complete(app.key, session_id)


def files_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path).encode() + path.read_bytes())
    return digest.hexdigest()


def to_baseline_ready(app, controller, frametime=10.0):
    assert controller.start(app.game.id, "maximum_performance", {})["success"]
    result = controller.record_baseline(app.game.id)
    assert result["success"], result
    finish_session(app, controller, result["sessionId"], frametime, wrappers=[])
    assert controller.store.workflow(app.key)["state"] == "baseline_ready"


def test_full_workflow_keep_saves_scoped_evidence(tmp_path: Path, monkeypatch) -> None:
    app, controller = make_app(tmp_path, monkeypatch)
    view = controller.status(app.game.id)
    assert view["support"]["supported"] and view["workflow"] == {}
    to_baseline_ready(app, controller)
    view = controller.status(app.game.id)
    assert view["proposal"]["id"] == "gamemode" and view["step"] == 3
    assert view["fingerprint"]["hardware"]["gpu"] == "Fixture GPU"
    assert controller.apply_candidate(app.game.id, "")["success"]
    assert app._optimization_profile_repository.load(app.key).gamemode_enabled is True
    assert app._optimization_change_service.active_change(app.game)["kind"] == "measured_experiment"
    assert not controller.record_baseline(app.game.id)["success"]  # no new baseline while testing
    result = controller.record_candidate(app.game.id)
    assert result["success"], result
    finish_session(app, controller, result["sessionId"], 9.3, wrappers=["gamemode"])
    workflow = controller.store.workflow(app.key)
    assert workflow["state"] == "awaiting_confirmation" and workflow["comparison"]["activation"]["verified"]
    check = controller.status(app.game.id)["comparabilityCheck"]
    assert check["violations"] == [] and {row["field"] for row in check["unverified"]} >= {"graphicsSettings"}
    unconfirmed = controller.confirm(app.game.id, "controlled", {})["result"]
    assert unconfirmed["comparability"] == "approximate" and unconfirmed["verified"] is False
    confirmed = controller.confirm(app.game.id, "controlled", {"unverifiedConfirmed": True, "visual": "same"})["result"]
    assert confirmed["decision"] == "keep" and confirmed["verified"] is True
    kept = controller.keep(app.game.id)
    assert kept["success"] and kept["evidence"]["userAction"] == "kept"
    assert app._optimization_change_service.active_change(app.game) is None
    assert app._optimization_profile_repository.load(app.key).gamemode_enabled is True
    assert controller.accepted_for(app.key, "maximum_performance") == {"gamemode_enabled": True}
    assert controller.accepted_for(app.key, "low_power") == {}
    # The same candidate is not proposed again in the same scope.
    to_baseline_ready(app, controller)
    rows = {row["id"]: row for row in controller.status(app.game.id)["candidates"]}
    assert rows["gamemode"]["applicability"]["code"] == "tested_in_scope"


def test_revert_and_cancel_restore_exact_bytes(tmp_path: Path, monkeypatch) -> None:
    app, controller = make_app(tmp_path, monkeypatch)
    path = app._optimization_profile_repository.path(app.key)
    app._optimization_profile_repository.save(app._optimization_profile_repository.load(app.key))
    original = path.read_bytes()
    to_baseline_ready(app, controller)
    assert controller.apply_candidate(app.game.id, "gamemode")["success"]
    assert path.read_bytes() != original
    assert controller.cancel(app.game.id)["success"]
    assert path.read_bytes() == original and app._optimization_change_service.active_change(app.game) is None
    to_baseline_ready(app, controller)
    assert controller.apply_candidate(app.game.id, "gamemode")["success"]
    session_id = controller.record_candidate(app.game.id)["sessionId"]
    finish_session(app, controller, session_id, 10.8, wrappers=["gamemode"])
    result = controller.confirm(app.game.id, "controlled", {"unverifiedConfirmed": True})["result"]
    assert result["decision"] == "revert"
    reverted = controller.revert(app.game.id)
    assert reverted["success"] and reverted["evidence"]["decision"] == "revert"
    assert path.read_bytes() == original


def test_cancel_during_recording_stops_measurement_and_restores(tmp_path: Path, monkeypatch) -> None:
    app, controller = make_app(tmp_path, monkeypatch)
    to_baseline_ready(app, controller)
    path = app._optimization_profile_repository.path(app.key)
    before = path.read_bytes() if path.exists() else None
    assert controller.apply_candidate(app.game.id, "gamemode")["success"]
    assert controller.record_candidate(app.game.id)["success"]
    assert controller.cancel(app.game.id)["success"]
    assert app._baseline_sessions.load(app.key).status == "failed"
    assert (path.read_bytes() if path.exists() else None) == before
    assert controller.store.workflow(app.key)["state"] == "cancelled"


def test_no_modification_while_game_runs_and_read_only_status(tmp_path: Path, monkeypatch) -> None:
    app, controller = make_app(tmp_path, monkeypatch)
    to_baseline_ready(app, controller)
    snapshot = files_hash(tmp_path)
    for _ in range(3):
        controller.status(app.game.id)
    assert files_hash(tmp_path) == snapshot
    app._optimization_controller.running = True
    refused = controller.apply_candidate(app.game.id, "gamemode")
    assert refused == {"success": False, "error": "The game is running"}
    assert files_hash(tmp_path) == snapshot


def test_short_baseline_is_rejected(tmp_path: Path, monkeypatch) -> None:
    app, controller = make_app(tmp_path, monkeypatch)
    assert controller.start(app.game.id, "low_power", {"fpsFloor": 5})["success"] is False
    assert controller.start(app.game.id, "low_power", {"fpsFloor": 60})["success"]
    session_id = controller.record_baseline(app.game.id)["sessionId"]
    session = app._baseline_sessions.load(app.key)
    write_log(Path(session.log_directory) / "Game.csv", 10.0, seconds=40)
    controller.on_measurement_processed(app.game, app.key, session)
    workflow = controller.store.workflow(app.key)
    assert workflow["state"] == "goal_selected" and "too short" in workflow["error"]
    assert session_id


def test_heroic_requires_launcher_integration_and_resyncs(tmp_path: Path, monkeypatch) -> None:
    app, controller = make_app(tmp_path, monkeypatch, launcher=Launcher.HEROIC, variant="flatpak")
    app.integration = "not_applied"
    to_baseline_ready(app, controller)
    rows = {row["id"]: row for row in controller.status(app.game.id)["candidates"]}
    assert rows["gamemode"]["applicability"]["code"] == "integration_not_applied"
    app.integration = "applied"
    assert controller.apply_candidate(app.game.id, "gamemode")["success"]
    assert app.launcher_applies == [app.game.id]
    session_id = controller.record_candidate(app.game.id)["sessionId"]
    finish_session(app, controller, session_id, 9.0)
    activation = controller.store.workflow(app.key)["comparison"]["activation"]
    assert activation["level"] == "configured"
    assert controller.cancel(app.game.id)["success"] and app.launcher_applies == [app.game.id] * 2


@pytest.mark.parametrize("launcher", [Launcher.LUTRIS])
def test_unsupported_launcher_has_exact_reason(tmp_path: Path, monkeypatch, launcher) -> None:
    app, controller = make_app(tmp_path, monkeypatch)
    app.game.launcher = launcher
    view = controller.status(app.game.id)
    assert view["support"]["supported"] is False and "Lutris" in view["support"]["reason"]
    assert controller.start(app.game.id, "balanced", {})["success"] is False
