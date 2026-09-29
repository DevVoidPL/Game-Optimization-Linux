"""Real Qt boundary regressions: native selector -> controller -> QML -> OCR."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, Qt, QUrl
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models.narrator import CaptureFrame
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.narrator_components import (
    DEFAULT_NARRATOR_COMPONENTS, NarratorComponentManager,
)
from game_optimization_linux.services.narrator_persistence import NarratorSettingsRepository
from game_optimization_linux.services.narrator_pipeline import crop_frame
from test_narrator_foundation import _AlwaysStable, _ManualExecutor, _pipeline, _settings


@pytest.fixture
def roi_runtime(tmp_path):
    executor = _ManualExecutor()
    pipeline, capture, _activity, ocr, *_ = _pipeline(tmp_path, executor)
    repository = NarratorSettingsRepository(tmp_path / "narrator-games")
    components = NarratorComponentManager(
        tmp_path / "components",
        definitions=tuple(replace(item, install_ready=False) for item in DEFAULT_NARRATOR_COMPONENTS),
    )
    pipeline.tts.available_voice_ids = ("voice-pl",)
    controller = AppController(
        game_provider=DemoGameProvider(), task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=repository,
        narrator_component_manager=components,
        narrator_pipeline=pipeline, auto_refresh=False,
    )
    for component in DEFAULT_NARRATOR_COMPONENTS:
        components.set_runtime_state(component.component_id, True)
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setInitialProperties({"controller": controller})
    view.setSource(QUrl.fromLocalFile(str(
        Path(__file__).resolve().parents[1]
        / "src/game_optimization_linux/qml/pages/NarratorPage.qml"
    )))
    assert view.status() == QQuickView.Status.Ready, view.errors()
    view.resize(1280, 900)
    view.show()
    QCoreApplication.processEvents()
    page = view.rootObject()
    assert page is not None
    game_id = page.property("selectedGameId")
    assert game_id
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    repository.save(replace(_settings(), game_key=game_key))
    page.loadSettings()
    try:
        yield controller, page, repository, pipeline, capture, executor, ocr
    finally:
        view.close()
        view.setSource(QUrl())
        controller.shutdown()
        QCoreApplication.processEvents()


def _wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert predicate()


def _pixels(request, timestamp=1.0):
    return CaptureFrame(
        session_id=request.session_id, generation=request.generation,
        timestamp_monotonic=timestamp, width=100, height=100, stride=100,
        pixel_format="gray8", pixels=bytes(y for y in range(100) for _ in range(100)),
    )


def _page_region(page):
    return {name: page.property(prop) for name, prop in (
        ("x", "cropX"), ("y", "cropY"), ("width", "cropWidth"), ("height", "cropHeight")
    )}


def test_real_controller_signals_are_readable_by_qml(roi_runtime):
    controller, page, *_ = roi_runtime
    game_id = page.property("selectedGameId")
    page.setProperty("regionPreviewLoading", True)
    controller.narratorRegionPreviewChanged.emit(game_id, {
        "success": True, "state": "ready", "imageUrl": "", "sourceWidth": 100,
        "sourceHeight": 80,
    })
    assert page.property("regionPreviewLoading") is False
    assert page.property("regionPreviewSourceWidth") == 100


def test_real_selection_signal_updates_canonical_qml_roi(roi_runtime):
    controller, page, *_ = roi_runtime
    game_id = page.property("selectedGameId")
    page.setProperty("regionPreviewLoading", True)
    selected = {"x": 0.2, "y": 0.4, "width": 0.5, "height": 0.25}
    controller.narratorRegionSelectionChanged.emit(game_id, selected)
    assert page.property("regionPreviewLoading") is False
    assert _page_region(page) == selected


def test_native_roi_a_b_survive_qml_save_and_runtime_restart(roi_runtime):
    controller, page, repository, pipeline, capture, executor, ocr = roi_runtime
    game_id = page.property("selectedGameId")
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    selected_regions = []
    for x, y, width, height in ((0.15, 0.2, 0.6, 0.2), (0.2, 0.6, 0.5, 0.25)):
        page.requestRegionPreview()
        assert page.property("regionPreviewLoading") is True
        capture.frame_callback(_pixels(capture.requests[-1]))
        coordinator = controller._narrator_region_selector
        _wait_for(lambda: coordinator.active_window is not None)
        window = coordinator.active_window
        canvas = window.canvas
        canvas.reset_selection()
        fitted = canvas.fitted_image_rect()
        start = QPoint(round(fitted.x() + x * fitted.width()), round(fitted.y() + y * fitted.height()))
        end = QPoint(round(start.x() + width * fitted.width()), round(start.y() + height * fitted.height()))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, end)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        selected = canvas.normalized_region()
        assert selected is not None
        selected_regions.append(selected)
        QTest.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
        assert coordinator.active_window is None
        assert repository.load(game_key).subtitle_region == selected
        assert page.property("regionPreviewLoading") is False
        assert _page_region(page) == pytest.approx(selected.to_dict())
        assert page.saveSettings() is True
        assert repository.load(game_key).subtitle_region == selected
        restarted = NarratorSettingsRepository(repository.root)
        assert restarted.load(game_key).subtitle_region == selected
        controller._narrator_settings_repository = restarted
        pipeline._stabilizer = _AlwaysStable()
        page.startNarrator()
        assert pipeline.active
        frame = _pixels(capture.requests[-1])
        capture.frame_callback(frame)
        assert executor.jobs
        executor.run_next()
        assert ocr.values[-1] == crop_frame(frame, selected).pixels[0]
        assert controller.stopNarrator() is True
    assert selected_regions[0] != selected_regions[1]
    assert ocr.values[0] != ocr.values[-1]
    assert repository.load("456").subtitle_region != selected_regions[-1]


def test_changed_roi_stops_only_its_active_session(roi_runtime):
    controller, page, repository, pipeline, capture, executor, ocr = roi_runtime
    game_id = page.property("selectedGameId")
    original = _page_region(page)
    assert controller.startNarrator(game_id)
    assert controller.saveNarratorGameSettings(game_id, {"subtitleRegion": original})
    assert pipeline.active
    other = next(game["id"] for game in controller.games if game["id"] != game_id)
    region_b = {"x": 0.1, "y": 0.65, "width": 0.8, "height": 0.2}
    assert controller.saveNarratorGameSettings(other, {"subtitleRegion": region_b})
    assert pipeline.active
    assert controller.saveNarratorGameSettings(game_id, {"subtitleRegion": region_b})
    assert not pipeline.active, "Saved ROI must not be displayed while OCR still uses old ROI"
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    pipeline._stabilizer = _AlwaysStable()
    assert controller.startNarrator(game_id)
    capture.frame_callback(_pixels(capture.requests[-1]))
    executor.run_next()
    assert ocr.values[-1] == 65
    assert repository.load(game_key).subtitle_region.to_dict() == region_b


def test_cancelled_preview_clears_real_qml_loading(roi_runtime):
    controller, page, *_rest = roi_runtime
    page.requestRegionPreview()
    assert page.property("regionPreviewLoading")
    controller.cancelNarratorRegionPreview(page.property("selectedGameId"))
    assert not page.property("regionPreviewLoading")


def test_preview_generation_guard_rejects_stale_and_negative_stop(roi_runtime):
    controller, page, _repository, _pipeline, capture, *_ = roi_runtime
    page.requestRegionPreview()
    game_id = page.property("selectedGameId")
    narrator = controller._narrator_controller
    generation = narrator._preview_generations[game_id]
    stops = capture.stop_calls
    narrator.finish_region_preview_capture(game_id, -1)
    narrator.finish_region_preview_capture(game_id, generation - 1)
    assert capture.stop_calls == stops
    narrator.finish_region_preview_capture(game_id, generation)
    assert capture.stop_calls == stops + 1


def test_native_cancel_preserves_saved_region(roi_runtime):
    controller, page, repository, _pipeline, capture, *_ = roi_runtime
    game_id = page.property("selectedGameId")
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    original = repository.load(game_key).subtitle_region
    page.requestRegionPreview()
    capture.frame_callback(_pixels(capture.requests[-1]))
    coordinator = controller._narrator_region_selector
    _wait_for(lambda: coordinator.active_window is not None)
    QTest.mouseClick(coordinator.active_window.cancel_button, Qt.MouseButton.LeftButton)
    assert coordinator.active_window is None
    assert not page.property("regionPreviewLoading")
    assert repository.load(game_key).subtitle_region == original
    assert _page_region(page) == original.to_dict()


def test_capture_error_and_other_game_signals_do_not_change_roi(roi_runtime):
    controller, page, _repository, _pipeline, capture, *_ = roi_runtime
    from game_optimization_linux.models.narrator import CaptureState

    original = _page_region(page)
    page.requestRegionPreview()
    capture.state_callback(CaptureState.PERMISSION_DENIED, "Capture denied")
    assert not page.property("regionPreviewLoading")
    assert page.property("regionPreviewError") == "Capture denied"
    controller.narratorRegionSelectionChanged.emit(
        "another-game", {"x": 0.2, "y": 0.4, "width": 0.5, "height": 0.25}
    )
    assert _page_region(page) == original


def test_gamepad_drives_native_selector_even_when_main_window_inactive(roi_runtime):
    """LB/RB presets, D-pad move, L2/R2 size and A save reach the selector
    although the main window is hidden (inactive) behind it."""

    controller, page, repository, _pipeline, capture, _executor, _ocr = roi_runtime
    game_id = page.property("selectedGameId")
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    page.requestRegionPreview()
    capture.frame_callback(_pixels(capture.requests[-1]))
    coordinator = controller._narrator_region_selector
    _wait_for(lambda: coordinator.active_window is not None)
    window = coordinator.active_window
    controller.setCouchWindowActive(False)          # main window hidden
    navigated: list[str] = []
    controller.gamepadAction.connect(navigated.append)
    emit = controller._gamepad_service.actionTriggered.emit

    emit("NextTab")                                  # first preset (no preset yet)
    assert window.canvas.preset_id == "bottom_20"
    emit("NextTab")
    assert window.canvas.normalized_region().to_dict() == pytest.approx(
        {"x": 0.0, "y": 0.70, "width": 1.0, "height": 0.30})
    emit("NavigateUp")
    assert window.canvas.normalized_region().y == pytest.approx(0.68)
    emit("PageUp")                                   # L2 / right stick up: smaller
    shrunk = window.canvas.normalized_region()
    assert shrunk.height == pytest.approx(0.30 / 1.1)
    assert window.canvas.preset_id == "" and window.gamepad_hint.text()
    emit("Confirm")                                  # A saves
    assert coordinator.active_window is None
    assert repository.load(game_key).subtitle_region == shrunk
    assert navigated == []                           # nothing leaked into the Couch UI


def test_gamepad_b_cancels_native_selector(roi_runtime):
    controller, page, repository, _pipeline, capture, _executor, _ocr = roi_runtime
    game_id = page.property("selectedGameId")
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    before = repository.load(game_key).subtitle_region
    page.requestRegionPreview()
    capture.frame_callback(_pixels(capture.requests[-1]))
    coordinator = controller._narrator_region_selector
    _wait_for(lambda: coordinator.active_window is not None)
    controller._gamepad_service.actionTriggered.emit("NavigateDown")
    controller._gamepad_service.actionTriggered.emit("Back")
    assert coordinator.active_window is None
    assert repository.load(game_key).subtitle_region == before
