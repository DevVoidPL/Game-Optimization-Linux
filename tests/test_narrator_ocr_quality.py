from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from game_optimization_linux.models.narrator import CaptureFrame
from game_optimization_linux.services.narrator_ocr import (
    OCR_UPSCALE_FACTOR,
    TESSERACT_COMPONENT_ID,
    TESSERACT_MODEL_RELATIVE_PATH,
    TesseractOcrProvider,
)
from game_optimization_linux.services.narrator_pipeline import SubtitleTextGate


def _observe_twice(
    text: str,
    *,
    confidence: float = 0.90,
) -> tuple[str, str]:
    gate = SubtitleTextGate()
    first = gate.observe(text, confidence, now=1.0)
    second = gate.observe(text, confidence, now=1.2)
    return first.rejection_reason, second.accepted_text


@pytest.mark.parametrize(
    ("text", "reason"),
    (
        ("... |] /\\ ###", "min_alphabetic"),
        ("2490SDAJCXZNJQ2 88#", "alphanumeric_noise"),
        ("12 Go 89", "digit_ratio"),
        ("A B C D", "isolated_fragments"),
    ),
)
def test_background_and_alphanumeric_garbage_are_rejected(
    text: str, reason: str
) -> None:
    observation = SubtitleTextGate().observe(text, 0.92, now=1.0)

    assert observation.accepted_text == ""
    assert observation.rejection_reason == reason


@pytest.mark.parametrize(
    "text",
    (
        "Where is Batman?",
        "No.",
        "Meet me in Room 101.",
        "Run!",
        "Batman!",
        "Don't open it.",
        "I'm... here.",
        "Go!!",
    ),
)
def test_credible_english_subtitles_stabilize(text: str) -> None:
    first_reason, accepted = _observe_twice(text)

    assert first_reason == "unstable"
    assert accepted == text


def test_minor_punctuation_differences_stabilize_as_one_phrase() -> None:
    gate = SubtitleTextGate()

    first = gate.observe("Where is Batman?", 0.86, now=1.0)
    second = gate.observe("Where is Batman", 0.91, now=1.2)

    assert first.rejection_reason == "unstable"
    assert second.accepted_text == "Where is Batman"


def test_wildly_different_ocr_frames_do_not_emit_a_phrase() -> None:
    gate = SubtitleTextGate()

    first = gate.observe("Open the door.", 0.90, now=1.0)
    second = gate.observe("Leave Gotham now!", 0.90, now=1.2)

    assert first.accepted_text == ""
    assert second.accepted_text == ""
    assert second.rejection_reason == "unstable"


def test_low_confidence_and_disappearance_clear_consensus() -> None:
    gate = SubtitleTextGate(min_confidence=0.62)
    assert gate.observe("No.", 0.61, now=1.0).rejection_reason == "low_confidence"
    assert gate.observe("No.", 0.90, now=1.2).rejection_reason == "unstable"
    assert gate.observe("", None, now=1.3).rejection_reason == "empty"
    assert gate.observe("No.", 0.90, now=1.4).rejection_reason == "unstable"


def _frame(width: int = 80, height: int = 24) -> CaptureFrame:
    row = bytes(round(column * 255 / (width - 1)) for column in range(width))
    return CaptureFrame(
        session_id="quality",
        generation=1,
        timestamp_monotonic=1.0,
        width=width,
        height=height,
        stride=width,
        pixel_format="gray8",
        pixels=row * height,
    )


def test_preprocessing_is_grayscale_upscaled_and_keeps_antialias_levels() -> None:
    started = time.perf_counter()
    image = TesseractOcrProvider._preprocess_frame(_frame(640, 120))
    elapsed = time.perf_counter() - started

    assert image.format() == QImage.Format.Format_Grayscale8
    assert image.width() == round(640 * OCR_UPSCALE_FACTOR)
    assert image.height() == round(120 * OCR_UPSCALE_FACTOR)
    assert 0 < image.pixelColor(641, 100).red() < 255
    assert elapsed < 1.0


def test_tsv_confidence_is_character_weighted_and_ignores_negative_entries() -> None:
    payload = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tconf\ttext\n"
        "4\t1\t1\t1\t1\t99\tduplicate line\n"
        "5\t1\t1\t1\t1\t90\tBatman\n"
        "5\t1\t1\t1\t1\t-1\tartifact\n"
        "5\t1\t1\t1\t1\t60\tNo\n"
    )

    text, confidence = TesseractOcrProvider._parse_tsv(payload)

    assert text == "Batman artifact No"
    assert confidence == pytest.approx((0.9 * 6 + 0.6 * 2) / 8)


def test_provider_sends_preprocessed_png_to_tesseract(tmp_path: Path) -> None:
    model = (
        tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    )
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model")
    sizes: list[tuple[int, int, QImage.Format]] = []
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tconf\ttext\n"
        "5\t1\t1\t1\t1\t92\tHello\n"
    )

    def runner(argv, **values):
        encoded = QByteArray(values["input"])
        buffer = QBuffer(encoded)
        assert buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        image = QImage()
        assert image.load(buffer, "PNG")
        sizes.append((image.width(), image.height(), image.format()))
        return subprocess.CompletedProcess(argv, 0, tsv.encode(), b"")

    result = TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=runner,
    ).recognize(_frame(), language="en")

    assert result.text == "Hello"
    assert sizes[0][:2] == (160, 48)
    assert sizes[0][2] == QImage.Format.Format_Grayscale8
