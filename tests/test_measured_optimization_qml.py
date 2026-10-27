"""MeasuredOptimizationPanel (shared Desktop/Couch UI) on the real measured
optimization backend with temporary data; synthetic sessions only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QTranslator, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402

import qml_runtime_probe as probe  # noqa: E402
from qml_runtime_probe import _item, _settle, _view  # noqa: E402
from test_measured_optimization_workflow import finish_session, make_app  # noqa: E402

TRANSLATIONS = Path(__file__).resolve().parents[1] / "src/game_optimization_linux/translations"


class Bridge(QObject):
    """The AppController slots of the measured workflow, on a test backend."""

    def __init__(self, controller):
        super().__init__()
        self.c = controller

    @Slot(str, result="QVariantMap")
    def getMeasuredOptimization(self, game_id):
        return self.c.status(game_id)

    @Slot(str, str, "QVariantMap", result="QVariantMap")
    def startMeasuredOptimization(self, game_id, goal, options):
        return self.c.start(game_id, goal, options)

    @Slot(str, result="QVariantMap")
    def recordMeasuredBaseline(self, game_id):
        return self.c.record_baseline(game_id)

    @Slot(str, str, result="QVariantMap")
    def applyMeasuredCandidate(self, game_id, candidate_id):
        return self.c.apply_candidate(game_id, candidate_id)

    @Slot(str, str, result="QVariantMap")
    def skipMeasuredCandidate(self, game_id, candidate_id):
        return self.c.skip_candidate(game_id, candidate_id)

    @Slot(str, result="QVariantMap")
    def finishMeasuredWithoutCandidate(self, game_id):
        return self.c.finish_without_candidate(game_id)

    @Slot(str, result="QVariantMap")
    def recordMeasuredCandidate(self, game_id):
        return self.c.record_candidate(game_id)

    @Slot(str, str, "QVariantMap", result="QVariantMap")
    def confirmMeasuredComparison(self, game_id, level, observations):
        return self.c.confirm(game_id, level, observations)

    @Slot(str, str, result="QVariantMap")
    def keepMeasuredResult(self, game_id, quality=""):
        return self.c.keep(game_id, quality)

    @Slot(str, str, str, result="QVariantMap")
    def recordMeasuredQuality(self, game_id, record_id, decision):
        return self.c.record_quality(game_id, record_id, decision)

    @Slot(str, result="QVariantMap")
    def prepareMeasuredFingerprint(self, game_id):
        return self.c.prepare_fingerprint(game_id)

    @Slot(str, result="QVariantMap")
    def revertMeasuredResult(self, game_id):
        return self.c.revert(game_id)

    @Slot(str, result="QVariantMap")
    def cancelMeasuredOptimization(self, game_id):
        return self.c.cancel(game_id)


def _eval(root, expression):
    result = QQmlExpression(QQmlEngine.contextForObject(root), root, expression).evaluate()
    return result[0] if isinstance(result, tuple) else result


@pytest.fixture
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def panel(app, controller, game_id, *, couch=False, width=1100, height=1800, language=""):
    translator = None
    if language:
        translator = QTranslator()
        assert translator.load(str(TRANSLATIONS / f"game_optimization_{language}.qm"))
        app.installTranslator(translator)
    probe.MESSAGES.clear()
    view, root = _view(app, "pages/details/MeasuredOptimizationPanel.qml", width, height, initial_properties={
        "controller": controller, "gameData": {"id": game_id}, "couchMode": couch})
    _settle(app, 6)
    view._translator = translator
    return view, root


def close(app, view):
    if view._translator is not None:
        app.removeTranslator(view._translator)
    view.close(); view.deleteLater(); _settle(app, 4)


def errors():
    return [m for m in probe.MESSAGES if "Error" in m or "Unable to assign" in m or "undefined" in m]


def visible(root, name):
    return bool(_item(root, name).property("visible"))


def text(root, name):
    item = _item(root, name)
    value = item.property("text")
    return str(value if value is not None else item.property("value"))


def setup_steam(tmp_path, monkeypatch):
    app_ns, controller = make_app(tmp_path, monkeypatch)
    bridge = Bridge(controller)
    return app_ns, controller, bridge


def to_baseline(app_ns, controller, root, frametime=10.0):
    _eval(root, 'panel.edit("goal", "maximum_performance")')
    assert _eval(root, "panel.start()") is True
    assert _eval(root, "panel.recordBaseline()") is True
    session_id = controller.store.workflow(app_ns.key)["baseline"]["sessionId"]
    finish_session(app_ns, controller, session_id, frametime, wrappers=[])
    _eval(root, "panel.refresh()")


def test_desktop_full_workflow_keep_and_history(app, tmp_path, monkeypatch) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        assert visible(root, "measuredGoalChoices") and not visible(root, "measuredUnsupported")
        assert _item(root, "measuredStart").property("enabled") is False  # no goal yet
        # The test backend has no game files: the exact reason is shown instead of Unknown.
        assert text(root, "measuredEngine") == "Not detected" and text(root, "measuredApi") == "Not detected"
        assert "Not detected: The game executable was not found" == text(root, "measuredDetection")
        assert not visible(root, "measuredDetect")
        to_baseline(app_ns, controller, root)
        assert text(root, "measuredBaselineState").endswith("Valid")
        assert text(root, "measuredCandidateTitle") == "GameMode" and visible(root, "measuredApply")
        assert _eval(root, "panel.applyCandidate()") is True
        assert visible(root, "measuredApplied") and visible(root, "measuredRecordCandidate")
        assert _eval(root, "panel.recordCandidate()") is True
        session_id = controller.store.workflow(app_ns.key)["comparison"]["sessionId"]
        finish_session(app_ns, controller, session_id, 9.3, wrappers=["gamemode"])
        _eval(root, "panel.refresh()")
        assert visible(root, "measuredComparability")
        assert _item(root, "measuredConfirm").property("enabled") is False  # no answer yet
        for name in ("measuredLevel_controlled", "measuredLevel_approximate", "measuredLevel_not_comparable"):
            assert visible(root, name)
        _eval(root, 'panel.edit("level", "controlled"); panel.edit("unverifiedConfirmed", true)')
        assert _eval(root, "panel.confirmComparison()") is True
        assert text(root, "measuredDecision").endswith("Keep")
        assert text(root, "measuredConfidence").endswith("verified")
        assert text(root, "measuredOverall") == "Fully verified result"
        assert text(root, "measuredQualityStatus") == "Not affected by this change"
        assert "+" in text(root, "measuredMetric_averageFps") and "%" in text(root, "measuredMetric_averageFps")
        assert text(root, "measuredMetric_gttMb") == "Not measured in either session"  # never shown as zero
        assert _eval(root, "panel.keep()") is True
        assert visible(root, "measuredHistoryToggle") and not visible(root, "measuredCancel")
        assert controller.accepted_for(app_ns.key, "maximum_performance") == {"gamemode_enabled": True}
        assert errors() == [], errors()
    finally:
        close(app, view)


def test_approximate_not_comparable_revert_and_cancel_restore(app, tmp_path, monkeypatch) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    path = app_ns._optimization_profile_repository.path(app_ns.key)
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        to_baseline(app_ns, controller, root)
        before = path.read_bytes() if path.exists() else None
        _eval(root, "panel.applyCandidate()")
        _eval(root, "panel.recordCandidate()")
        finish_session(app_ns, controller, controller.store.workflow(app_ns.key)["comparison"]["sessionId"], 9.3,
                       wrappers=["gamemode"])
        _eval(root, "panel.refresh()")
        _eval(root, 'panel.edit("level", "approximate")')
        _eval(root, "panel.confirmComparison()")
        assert text(root, "measuredConfidence").endswith("not verified")
        assert text(root, "measuredOverall") == "Not a verified result"
        _eval(root, 'panel.edit("level", "not_comparable")')
        _eval(root, "panel.confirmComparison()")
        assert text(root, "measuredDecision").endswith("Inconclusive")
        assert _eval(root, "panel.revert()") is True
        assert (path.read_bytes() if path.exists() else None) == before
        # A second run cancelled after Apply restores the same bytes.
        to_baseline(app_ns, controller, root)
        _eval(root, "panel.applyCandidate()")
        assert text(root, "measuredCancel") == "Cancel and restore"
        assert _eval(root, "panel.cancel()") is True
        assert (path.read_bytes() if path.exists() else None) == before
        assert errors() == [], errors()
    finally:
        close(app, view)


def test_dirty_draft_survives_refresh_and_preview_writes_nothing(app, tmp_path, monkeypatch) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        _eval(root, 'panel.edit("goal", "low_power"); panel.edit("fpsFloor", 50)')
        state = sorted(str(p) for p in tmp_path.rglob("*") if p.is_file())
        for _ in range(3):
            _eval(root, "panel.refresh()")
        assert _eval(root, "panel.goal") == "low_power" and _eval(root, "panel.fpsFloor") == 50
        assert visible(root, "measuredFpsFloor")
        assert sorted(str(p) for p in tmp_path.rglob("*") if p.is_file()) == state
    finally:
        close(app, view)


def test_failed_recording_and_escaped_process_are_reported(app, tmp_path, monkeypatch) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        _eval(root, 'panel.edit("goal", "balanced")')
        _eval(root, "panel.start()")
        _eval(root, "panel.recordBaseline()")
        session = app_ns._baseline_sessions.load(app_ns.key)
        app_ns._baseline_sessions.fail(app_ns.key, "The game process left the tracked process tree; GameOpti cannot verify when it ends",
                                       session.id)
        _eval(root, "panel.refresh()")
        assert "left the tracked process tree" in text(root, "measuredBaselineProgress")
        assert visible(root, "measuredCancel")
    finally:
        close(app, view)


def test_custom_launch_conflict_is_shown_with_reason(app, tmp_path, monkeypatch) -> None:
    from game_optimization_linux.services.custom_launch import CustomLaunchProfile, CustomLaunchRepository
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    wrapper = tmp_path / "bin" / "gamemoderun"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\nexec \"$@\"\n")
    wrapper.chmod(0o755)
    CustomLaunchRepository(app_ns._optimization_profile_repository.root).save(
        CustomLaunchProfile.from_dict(app_ns.key, {"wrapper": {"argv": [str(wrapper)]}}))
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        to_baseline(app_ns, controller, root)
        assert text(root, "measuredCandidateTitle") != "GameMode"
        _eval(root, "panel.technicalOpen = true")
        texts = [str(item.property("text")) for item in probe._descendants(_item(root, "measuredTechnical"))
                 if item.property("text")]
        assert any("GameMode: gamemoderun is already part of your launch command" in line for line in texts), texts
    finally:
        close(app, view)


def test_couch_focus_confirm_and_back(app, tmp_path, monkeypatch) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    for width, height in ((1280, 720), (1920, 1080)):
        view, root = panel(app, bridge, app_ns.game.id, couch=True, width=width, height=height)
        try:
            assert _eval(root, 'panel.handlePadAction("Focus")') is True
            first = _eval(root, "panel.padFocusedItem.objectName")
            assert first == "measuredGoal_maximum_performance"
            assert _eval(root, "panel.padFocusedItem.activeFocus") is True
            _eval(root, 'panel.handlePadAction("NavigateDown")')
            assert _eval(root, "panel.padFocusedItem.objectName") == "measuredGoal_balanced"
            _eval(root, 'panel.handlePadAction("Confirm")')
            _settle(app, 6)
            assert _eval(root, "panel.goal") == "balanced"
            # Every pad control shows a focus indicator (button border or ring).
            _eval(root, "panel.technicalOpen = true")
            assert _eval(root, 'panel.handlePadAction("Back")') is True and _eval(root, "panel.technicalOpen") is False
            assert _eval(root, 'panel.handlePadAction("Back")') is False  # the overlay closes next
            assert _eval(root, "panel.titleSize") == 22
        finally:
            close(app, view)


@pytest.mark.parametrize("language, expected", [("pl", "Optymalizacja z pomiarem"), ("es", "Optimización medida"),
                                                ("en", "Measured optimization")])
def test_translations(app, tmp_path, monkeypatch, language, expected) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    view, root = panel(app, bridge, app_ns.game.id, language=language)
    try:
        labels = [str(item.property("text")) for item in probe._descendants(root) if item.property("text")]
        assert expected in labels
        _eval(root, 'panel.edit("goal", "low_power")')
        if language == "pl":
            assert _eval(root, 'panel.goalLabel("low_power")') == "Niski pobór energii"
            assert _eval(root, 'panel.tr("GameMode is not available on this system")') == "GameMode jest niedostępny w tym systemie"
            assert _eval(root, 'panel.overallLabel("performance_only")') == "Zmierzono poprawę wydajności; jakości obrazu nie oceniono"
            assert _eval(root, 'panel.qualityLabel("not_evaluated")') == "Nie oceniono"
            assert _eval(root, 'panel.tr("Several executables match; choose the main game executable on the OptiScaler page first")').startswith("Pasuje kilka")
        if language == "es":
            assert _eval(root, 'panel.goalLabel("best_quality")') == "Máxima calidad"
            assert _eval(root, 'panel.overallLabel("verified")') == "Resultado totalmente verificado"
    finally:
        close(app, view)


def test_desktop_narrow_layout(app, tmp_path, monkeypatch) -> None:
    app_ns, controller, bridge = setup_steam(tmp_path, monkeypatch)
    view, root = panel(app, bridge, app_ns.game.id, width=420)
    try:
        assert _eval(root, "panel.wide") is False
        assert float(_item(root, "measuredGoalChoices").property("width")) <= 420
        assert errors() == [], errors()
    finally:
        close(app, view)


def test_lossy_result_shows_split_confidence_and_needs_quality_decision(app, tmp_path, monkeypatch) -> None:
    from test_measured_optimization_quality import _lossy_ready
    app_ns, controller, _before = _lossy_ready(tmp_path, monkeypatch)
    bridge = Bridge(controller)
    controller.confirm(app_ns.game.id, "controlled", {"unverifiedConfirmed": True})
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        assert text(root, "measuredDecision").endswith("Keep")
        assert text(root, "measuredConfidence") == "Controlled - same scene and settings  ·  verified"
        assert text(root, "measuredPerformanceStatus") == "Improvement measured"
        assert text(root, "measuredQualityStatus") == "Not evaluated"
        assert text(root, "measuredOverall") == "Performance improvement measured; image quality not evaluated"
        assert not visible(root, "measuredKeep") and visible(root, "measuredKeepPerformanceOnly")
        assert visible(root, "measuredQualityHint")
        # Answering the quality question turns it into a fully verified result.
        _eval(root, 'panel.edit("level", "controlled"); panel.edit("unverifiedConfirmed", true); panel.edit("visual", "same")')
        assert _eval(root, "panel.confirmComparison()") is True
        assert text(root, "measuredOverall") == "Fully verified result" and visible(root, "measuredKeep")
        assert text(root, "measuredQualityStatus") == "Same (your check)"
        assert errors() == [], errors()
    finally:
        close(app, view)


def test_history_quality_decision_for_a_performance_only_result(app, tmp_path, monkeypatch) -> None:
    from test_measured_optimization_quality import _lossy_ready
    app_ns, controller, _before = _lossy_ready(tmp_path, monkeypatch)
    controller.confirm(app_ns.game.id, "controlled", {"unverifiedConfirmed": True})
    controller.keep(app_ns.game.id, "keep_unevaluated")
    bridge = Bridge(controller)
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        _eval(root, "panel.historyOpen = true"); _settle(app, 4)
        assert "image quality not evaluated" in text(root, "measuredHistoryStatus")
        assert visible(root, "measuredHistoryQualitySame")
        record_id = controller.store.load(app_ns.key)["history"][0]["id"]
        assert _eval(root, f'panel.recordQuality("{record_id}", "same")') is True
        _settle(app, 4)
        assert text(root, "measuredHistoryStatus").startswith("Fully verified result")
    finally:
        close(app, view)


def test_detection_runs_automatically_and_keeps_the_draft(app, tmp_path, monkeypatch) -> None:
    from test_measured_optimization_quality import _detection_app
    app_ns, controller, started = _detection_app(tmp_path, monkeypatch, selected="Game.exe")
    bridge = Bridge(controller)
    view, root = panel(app, bridge, app_ns.game.id)
    try:
        assert started == [app_ns.game.id]  # started by opening the workflow, no button
        assert text(root, "measuredEngine") == "Detecting…" and text(root, "measuredDisplay") == "Detecting…"
        assert text(root, "measuredDetection") == "Detecting the game files in the background…"
        _eval(root, 'panel.edit("goal", "low_power"); panel.edit("fpsFloor", 55)')
        for _ in range(3):
            _eval(root, "panel.refresh()")
        assert _eval(root, "panel.goal") == "low_power" and _eval(root, "panel.fpsFloor") == 55 and len(started) == 1
    finally:
        close(app, view)
    app_ns2, controller2, started2 = _detection_app(tmp_path / "b", monkeypatch) if (tmp_path / "b").mkdir() is None else None
    bridge2 = Bridge(controller2)
    view, root = panel(app, bridge2, app_ns2.game.id)
    try:
        line = text(root, "measuredDetection")
        assert line.startswith("Not detected: Several executables match") and "BatmanOrigins.exe" in line
        assert started2 == []
    finally:
        close(app, view)
