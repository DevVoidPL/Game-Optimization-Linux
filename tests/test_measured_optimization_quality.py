"""Measured optimization fixes: split confidence for image-quality candidates,
read-time status of saved results, one goal selection with a Custom profile,
and automatic fingerprint detection with exact reasons. Synthetic data only."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from game_optimization_linux.services import measured_optimization as mo
from game_optimization_linux.services.measured_optimization import comparison as cmp
from game_optimization_linux.services.measured_optimization.store import MeasuredOptimizationStore, record_status
from tests.test_measured_optimization_core import metrics
from tests.test_measured_optimization_workflow import finish_session, make_app

LOSSY = {"imageQuality": "lossy_upscaling"}
SCOPE = {"key": "3240220", "gpu": "RX", "cpu": "R7", "vramGb": 16.0, "build": "1", "driver": "Mesa",
         "runtime": "GE", "resolution": "2560x1440@165", "api": "Direct3D 12"}


def test_lossy_maximum_performance_is_performance_only_until_quality_is_evaluated(tmp_path: Path) -> None:
    base, fast = metrics(tmp_path, "a", 13.85), metrics(tmp_path, "b", 12.3)  # ~72 -> ~81 FPS
    result = cmp.score("maximum_performance", base, fast, LOSSY, level="controlled", activation={"verified": True},
                       observations={"visual": "not_checked"})
    assert result["decision"] == "keep"
    assert result["comparabilityVerified"] and result["performanceVerified"] and result["performance"] == "improved"
    assert result["imageQuality"] == "not_evaluated" and result["overall"] == "performance_only"
    assert result["verified"] is False and result["fullyVerified"] is False
    assert any(c["text"] == "Performance improvement measured; image quality not evaluated" for c in result["costs"])
    same = cmp.score("maximum_performance", base, fast, LOSSY, level="controlled", activation={"verified": True},
                     observations={"visual": "same"})
    assert same["overall"] == "verified" and same["verified"] and same["imageQuality"] == "evaluated_same"
    native = cmp.score("maximum_performance", base, fast, {"imageQuality": "none"}, level="controlled",
                       activation={"verified": True})
    assert native["imageQuality"] == "not_affected" and native["overall"] == "verified"


def test_historical_gta_like_record_is_reinterpreted_without_rewriting(tmp_path: Path) -> None:
    store = MeasuredOptimizationStore(tmp_path)
    legacy = {"goal": "maximum_performance", "candidateId": "gamescope_fsr_upscale", "decision": "keep",
              "verified": True, "confidence": "controlled", "userAction": "kept", "scope": SCOPE,
              "comparability": {"final": "controlled", "observations": {"visual": "not_checked", "stability": "same"}},
              "metrics": [{"metric": "averageFps", "before": 72.2, "after": 81.2, "percent": 12.4}],
              "changes": [{"target": "optimization", "setting": "gamescope_filter", "after": "fsr"}]}
    record = store.add_evidence("3240220", legacy)
    raw = store.path("3240220").read_bytes()
    status = record_status(record)
    assert status["legacy"] and status["overall"] == "performance_only" and status["imageQuality"] == "not_evaluated"
    assert store.path("3240220").read_bytes() == raw  # reading never rewrites the saved measurement
    assert mo.accepted_results(store.load("3240220")["history"], SCOPE, "maximum_performance") == {}
    annotated = store.annotate("3240220", record["id"], qualityDecision="same")
    assert annotated["metrics"] == legacy["metrics"] and annotated["verified"] is True
    assert record_status(annotated)["overall"] == "verified"
    assert mo.accepted_results(store.load("3240220")["history"], SCOPE, "maximum_performance") == {"gamescope_filter": "fsr"}
    store.annotate("3240220", record["id"], qualityDecision="worse")
    assert mo.accepted_results(store.load("3240220")["history"], SCOPE, "maximum_performance") == {}


def _lossy_ready(tmp_path, monkeypatch, *, preset="custom"):
    app, controller = make_app(tmp_path, monkeypatch)
    repo = app._optimization_profile_repository
    profile = replace(repo.load(app.key), preset=preset, gamescope_enabled=True, gamescope_mode="native",
                      gamescope_input_width=2560, gamescope_input_height=1440, gamescope_output_width=2560,
                      gamescope_output_height=1440, gamescope_framerate_limit=0, gamemode_enabled=True,
                      manual_overrides={"gamemode": True}, target_fps=117)
    repo.save(profile)
    custom_before = repo.load(app.key)
    assert controller.start(app.game.id, "maximum_performance", {"qualityConsent": True, "explicitExperiments": True})["success"]
    session = controller.record_baseline(app.game.id)["sessionId"]
    finish_session(app, controller, session, 13.85, wrappers=["gamescope"])
    assert controller.apply_candidate(app.game.id, "gamescope_fsr_upscale")["success"]
    session = controller.record_candidate(app.game.id)["sessionId"]
    report = app._runner_report_path(app.key)
    finish_session(app, controller, session, 12.3)
    report.write_text(json.dumps({"baselineSessionId": session, "wrappers": ["gamescope"],
                                  "gamescopeWrapper": ["gamescope", "-w", "1970", "-F", "fsr", "--"]}))
    workflow = controller.store.workflow(app.key)
    comparison = dict(workflow["comparison"])
    comparison["activation"] = mo.verify_activation(workflow["candidate"], launcher="steam_native",
                                                    report=json.loads(report.read_text()), session_id=session)
    controller.store.update(app.key, workflow["id"], comparison=comparison)
    return app, controller, custom_before


def test_keep_requires_an_explicit_quality_decision_and_preserves_custom(tmp_path: Path, monkeypatch) -> None:
    app, controller, custom_before = _lossy_ready(tmp_path, monkeypatch)
    result = controller.confirm(app.game.id, "controlled", {"unverifiedConfirmed": True})["result"]
    assert result["decision"] == "keep" and result["overall"] == "performance_only"
    refused = controller.keep(app.game.id)
    assert refused["success"] is False and refused["code"] == "quality_decision_required"
    kept = controller.keep(app.game.id, "keep_unevaluated")
    assert kept["success"] and kept["evidence"]["overall"] == "performance_only"
    assert "qualityDecision" not in kept["evidence"]
    saved = app._optimization_profile_repository.load(app.key)
    # Custom stays Custom; only the three tested settings changed.
    assert saved.preset == "custom" and saved.gamemode_enabled is True and saved.target_fps == 117
    assert saved.manual_overrides == custom_before.manual_overrides
    assert (saved.gamescope_input_width, saved.gamescope_input_height, saved.gamescope_filter) == (1970, 1108, "fsr")
    assert controller.accepted_for(app.key, "maximum_performance") == {}  # performance-only is never applied
    annotated = controller.record_quality(app.game.id, kept["evidence"]["id"], "same")
    assert annotated["success"]
    assert controller.accepted_for(app.key, "maximum_performance") == {
        "gamescope_input_width": 1970, "gamescope_input_height": 1108, "gamescope_filter": "fsr"}
    history = controller.status(app.game.id)["history"][0]
    assert history["status"]["overall"] == "verified" and history["metrics"] == kept["evidence"]["metrics"]


def test_quality_answer_before_keep_stores_a_fully_accepted_result(tmp_path: Path, monkeypatch) -> None:
    app, controller, _before = _lossy_ready(tmp_path, monkeypatch)
    result = controller.confirm(app.game.id, "controlled", {"unverifiedConfirmed": True, "visual": "same"})["result"]
    assert result["overall"] == "verified"
    kept = controller.keep(app.game.id)
    assert kept["success"] and kept["evidence"]["fullyVerified"] is True


def test_custom_profile_with_measured_goal_and_automatic_use_of_results(tmp_path: Path, monkeypatch) -> None:
    from game_optimization_linux.services.optimization_advisor import OptimizationAdvisor
    app, controller, _before = _lossy_ready(tmp_path, monkeypatch)
    controller.confirm(app.game.id, "controlled", {"unverifiedConfirmed": True, "visual": "same"})
    controller.keep(app.game.id)
    view = controller.status(app.game.id)
    assert view["configuration"]["preset"] == "custom"
    assert view["workflow"]["goal"]["id"] == "maximum_performance"
    accepted = controller.accepted_for(app.key, "maximum_performance")
    profile = app._optimization_profile_repository.load(app.key)
    custom = OptimizationAdvisor().resolve_preset(profile, None, gamemode_available=True, gamescope_available=True,
                                                  accepted_results=accepted)
    assert custom.changes == {}  # Custom is never reinterpreted
    automatic = OptimizationAdvisor().resolve_preset(replace(profile, preset="automatic", gamescope_filter="linear"), None,
                                                     gamemode_available=True, gamescope_available=True,
                                                     accepted_results=accepted)
    assert automatic.profile.gamescope_filter == "fsr" and "verified local measurements" in automatic.sources


def _detection_app(tmp_path, monkeypatch, *, resolution="ambiguous", selected=""):
    app, controller = make_app(tmp_path, monkeypatch)
    started = []
    app._optimization_jobs = {}
    app._optimization_controller._selected_executable = lambda game, key: selected
    app._optimization_controller._restore_persisted_analysis = lambda game, key: None

    def analyze(game_id):
        started.append(game_id)
        app._optimization_jobs[game_id] = object()
        return {"success": True, "status": "running"}

    app._optimization_controller.analyzeGameOptimization = analyze
    candidates = (SimpleNamespace(relative_path="SinglePlayer/Binaries/Win32/BatmanOrigins.exe"),
                  SimpleNamespace(relative_path="Online/Binaries/Win32/BatmanOriginsOnline.exe"))
    app._mangohud_launch_integration = SimpleNamespace(executable_resolver=SimpleNamespace(
        resolve=lambda game: SimpleNamespace(status=resolution, candidates=candidates if resolution == "ambiguous" else ())))
    return app, controller, started


def test_detection_reasons_and_automatic_start(tmp_path: Path, monkeypatch) -> None:
    app, controller, started = _detection_app(tmp_path, monkeypatch)
    detection = controller.status(app.game.id)["fingerprint"]["detection"]
    assert detection["state"] == "unavailable" and detection["code"] == "executable_ambiguous"
    assert detection["candidates"][0].endswith("BatmanOrigins.exe")
    assert controller.prepare_fingerprint(app.game.id)["detection"]["code"] == "executable_ambiguous" and started == []
    app.game.library_available = False
    assert controller.status(app.game.id)["fingerprint"]["detection"]["code"] == "library_unavailable"
    app.game.library_available = True
    app.game.install_path = tmp_path / "missing"
    assert controller.status(app.game.id)["fingerprint"]["detection"]["code"] == "directory_missing"
    # A saved executable choice (like Batman's OptiScaler profile) makes detection possible.
    (tmp_path / "second").mkdir()
    app2, controller2, started2 = _detection_app(tmp_path / "second", monkeypatch,
                                                 selected="SinglePlayer/Binaries/Win32/BatmanOrigins.exe")
    assert controller2.status(app2.game.id)["fingerprint"]["detection"]["state"] == "not_started"
    snapshot = sorted(str(p) for p in (tmp_path / "second").rglob("*") if p.is_file())
    assert controller2.prepare_fingerprint(app2.game.id)["detection"]["state"] == "detecting"
    assert started2 == [app2.game.id]
    assert controller2.prepare_fingerprint(app2.game.id)["detection"]["state"] == "detecting" and len(started2) == 1
    assert sorted(str(p) for p in (tmp_path / "second").rglob("*") if p.is_file()) == snapshot
    app2._optimization_jobs.clear()
    app2._optimization_analyses[app2.game.id] = {"success": False, "status": "failed", "error": "boom"}
    failed = controller2.status(app2.game.id)["fingerprint"]["detection"]
    assert failed["state"] == "failed" and failed["detail"] == "boom"


def test_baseline_starts_detection_when_needed(tmp_path: Path, monkeypatch) -> None:
    app, controller, started = _detection_app(tmp_path, monkeypatch, selected="Game.exe")
    assert controller.start(app.game.id, "balanced", {})["success"]
    assert controller.record_baseline(app.game.id)["success"]
    assert started == [app.game.id]
