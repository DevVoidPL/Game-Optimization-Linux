from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from game_optimization_linux.services.narrator_components import DEFAULT_NARRATOR_COMPONENTS
from game_optimization_linux.services.narrator_rapidocr import RapidOcrProvider
from game_optimization_linux.services.narrator_rapidocr_worker import verify_models
from tests.test_narrator_roi_qml import roi_runtime
from tests.test_narrator_ocr_worker import _frame
from tests.test_narrator_end_to_end import _pipeline, _ManualExecutor, _settings, _Ocr


def line(text, score=.96, x=0, y=0):
    return {"text": text, "score": score,
            "box": [[x, y], [x + 30, y], [x + 30, y + 10], [x, y + 10]]}


class Worker:
    last_recognition_ms = 12
    last_worker_execution_ms = 13
    last_decode_ms = 1
    last_roundtrip_ms = 15
    last_lock_wait_ms = 0
    startup_ms = 100

    def __init__(self, lines):
        self.lines = lines
        self.cancelled = self.closed = False
        self.payload = b""

    def recognize_response(self, payload):
        self.payload = payload
        return {"lines": self.lines}

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


def provider(tmp_path, monkeypatch, lines):
    value = RapidOcrProvider(tmp_path)
    worker = Worker(lines)
    monkeypatch.setattr(value, "language_available", lambda language: language in ("en", "pl"))
    value._worker = worker
    return value, worker


def test_rapid_keeps_full_roi_multiline_short_and_numeric_text(tmp_path, monkeypatch):
    value, worker = provider(tmp_path, monkeypatch,
        [line("Stop!", x=0, y=0), line("42", x=50, y=20), line("Zażółć gęślą jaźń.", y=40)])
    result = value.recognize(_frame(), language="pl")
    assert result.text == "Stop!\n42\nZażółć gęślą jaźń."
    image = QImage.fromData(worker.payload, "PNG")
    assert (image.width(), image.height()) == (_frame().width, _frame().height)
    assert result.provider_id == "rapidocr-local"
    assert result.preprocess == "rapidocr_color"
    assert result.line_count == 3
    assert result.minimum_token_confidence is None
    assert not result.clean_short_phrase_evidence
    assert result.recognition_ms == 12
    # The frame is a left-to-right gradient: no Tesseract outline mask applied.
    assert image.pixelColor(0, 0).red() == 0
    assert image.pixelColor(image.width() - 1, 0).red() == 255


def test_rapid_empty_frame_is_an_empty_observation(tmp_path, monkeypatch):
    value, _ = provider(tmp_path, monkeypatch, [])
    result = value.recognize(_frame())
    assert result.text == "" and result.confidence is None and result.line_count == 0


@pytest.mark.parametrize("bad", [None, [line("bad", float("nan"))],
    [line("bad", 1.1)], [{"text": "bad", "score": .9, "box": []}]])
def test_invalid_worker_data_cancels_without_tesseract_fallback(tmp_path, monkeypatch, bad):
    value, worker = provider(tmp_path, monkeypatch, bad)
    with pytest.raises(RuntimeError, match="RapidOCR"):
        value.recognize(_frame())
    assert worker.cancelled


def test_missing_models_do_not_start_a_worker(tmp_path):
    def factory(*args, **kwargs):
        pytest.fail("Worker must not start before an explicit model installation")
    value = RapidOcrProvider(tmp_path, worker_factory=factory)
    assert not value.available
    with pytest.raises(RuntimeError, match="Install"):
        value.recognize(_frame())
    assert not list(tmp_path.iterdir())


def test_model_verification_rejects_absent_or_modified_files(tmp_path):
    with pytest.raises(RuntimeError, match="verified RapidOCR"):
        verify_models(tmp_path)


def test_selected_engine_readiness_is_independent_of_tesseract(tmp_path):
    pipeline, *_ = _pipeline(tmp_path, _ManualExecutor())
    rapid = _Ocr()
    rapid.provider_id = "rapidocr-local"
    pipeline._ocr_providers[rapid.provider_id] = rapid
    pipeline.ocr.available = False
    settings = _settings(ocr_provider_id=rapid.provider_id)
    try:
        assert "ocr" not in pipeline.missing_requirements(settings)
        snapshot = pipeline.start(settings)
        assert pipeline.ocr is rapid
        pipeline.stop()
        assert "ocr" in pipeline.missing_requirements(_settings())
        assert pipeline.ocr_provider_for("").provider_id == "test-ocr"
    finally:
        pipeline.shutdown()


def test_switching_back_and_default_dont_reuse_last_engine(tmp_path):
    pipeline, *_ = _pipeline(tmp_path, _ManualExecutor())
    default = pipeline.ocr
    rapid = _Ocr()
    rapid.provider_id = "rapidocr-local"
    pipeline._ocr_providers[rapid.provider_id] = rapid
    try:
        pipeline.start(_settings(ocr_provider_id="rapidocr-local"))
        pipeline.stop()
        pipeline.start(_settings(ocr_provider_id=""))
        assert pipeline.ocr is default
        pipeline.stop()
        with pytest.raises(RuntimeError, match="selected OCR"):
            pipeline.start(_settings(ocr_provider_id="missing-engine"))
        assert "ocr" in pipeline.missing_requirements(_settings(ocr_provider_id="missing-engine"))
    finally:
        pipeline.shutdown()


def test_shutdown_closes_every_engine(tmp_path):
    pipeline, *_ = _pipeline(tmp_path, _ManualExecutor())
    closed = []
    pipeline.ocr.close = lambda: closed.append("default")
    rapid = _Ocr()
    rapid.provider_id = "rapidocr-local"
    rapid.close = lambda: closed.append("rapid")
    pipeline._ocr_providers[rapid.provider_id] = rapid
    pipeline.shutdown()
    assert sorted(closed) == ["default", "rapid"]


def test_rapid_models_have_immutable_download_and_attribution():
    definition = next(item for item in DEFAULT_NARRATOR_COMPONENTS
                      if item.component_id == "ocr.rapidocr-local")
    assert definition.install_ready
    assert definition.download_size_bytes == 27_288_134
    assert len(definition.artifacts) == 1
    assert definition.artifacts[0].archive_format == "zip"
    assert definition.artifacts[0].source_url.endswith("rapidocr-3.10.0-py3-none-any.whl")
    assert definition.artifact_license_id == "Apache-2.0"
    assert definition.attribution


def test_fragmented_visual_lines_are_joined_by_real_geometry():
    result = RapidOcrProvider._lines({"lines": [line("world!", x=40, y=1),
        line("Next line.", y=30), line("Hello", x=0, y=0)]})
    assert [text for text, _ in result] == ["Hello world!", "Next line."]


def test_engine_selection_inherits_but_game_override_is_independent(tmp_path):
    from game_optimization_linux.services.narrator_persistence import NarratorSettingsRepository
    repository = NarratorSettingsRepository(tmp_path)
    defaults = repository.load_defaults()
    repository.save_defaults(replace(defaults, ocr_provider_id="rapidocr-local"))
    assert repository.load("123").ocr_provider_id == "rapidocr-local"
    repository.save_overrides("123", {"ocr_provider_id": "tesseract-fast-eng"})
    assert repository.load("123").ocr_provider_id == "tesseract-fast-eng"
    assert repository.load("456").ocr_provider_id == "rapidocr-local"
    repository.clear_overrides("123", ("ocr_provider_id",))
    assert repository.load("123").ocr_provider_id == "rapidocr-local"


def test_localized_engine_labels():
    from PySide6.QtCore import QCoreApplication
    from game_optimization_linux.translations import TranslationManager
    manager = TranslationManager(QCoreApplication.instance())
    try:
        for code, label in (("en", "OCR engine"), ("pl", "Silnik OCR"), ("es", "Motor OCR")):
            assert manager.set_language(code)
            for context in ("NarratorPage", "CouchGameDetails"):
                assert QCoreApplication.translate(context, "OCR engine") == label
    finally:
        manager.set_language("en")


def test_desktop_engine_selection_uses_managed_models_and_saves_per_game(roi_runtime):
    from tests.test_narrator_end_to_end import _Ocr
    controller, page, repository, pipeline, *_ = roi_runtime
    game_id = page.property("selectedGameId")
    rapid = _Ocr()
    rapid.provider_id = "rapidocr-local"
    pipeline._ocr_providers[rapid.provider_id] = rapid
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
    selector = page.findChild(QObject, "narratorOcrEngine")
    assert selector is not None and selector.property("enabled")
    assert QMetaObject.invokeMethod(selector, "activated", Qt.DirectConnection, Q_ARG(int, 1))
    assert page.property("ocrProviderId") == "rapidocr-local"
    assert page.saveSettings()
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    assert repository.load(game_key).ocr_provider_id == "rapidocr-local"
    page.loadSettings()
    assert page.property("ocrProviderId") == "rapidocr-local"
    assert page.ocrComponentId() == "ocr.rapidocr-local"
    controller._narrator_component_manager.set_runtime_state("ocr.rapidocr-local", False)
    assert "ocr" in controller._narrator_controller._missing_requirements(repository.load(game_key))
    # Selecting an engine alone does not download anything or start its worker.
    assert not controller._narrator_controller._component_jobs


def test_cancelled_rapid_worker_cannot_spawn_after_stop(tmp_path):
    from game_optimization_linux.services.narrator_ocr import TesseractWorkerClient
    def forbidden_spawn(*args, **kwargs):
        pytest.fail("Cancelled warm-up must not launch a late worker")
    worker = TesseractWorkerClient(tmp_path, "en", restart_after_cancel=False,
                                  worker_name="RapidOCR", popen=forbidden_spawn)
    worker.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        worker.start()
