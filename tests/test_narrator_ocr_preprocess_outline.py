"""Opt-in outline preprocessing (GAME_OPTIMIZATION_NARRATOR_OCR_PREPROCESS).

outline: bright (>=190) pixels with a dark (<=110) pixel within radius 2.
outline_adaptive: same, threshold 220 when >15% of pixels are >=190.
Both use the bright-mask fallback rule; default and bright_mask are unchanged.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from game_optimization_linux.models.narrator import CaptureFrame, OcrResult
from game_optimization_linux.services.narrator_ocr import (
    OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT,
    OCR_PREPROCESS_BRIGHT_MASK,
    OCR_PREPROCESS_DEFAULT,
    OCR_PREPROCESS_ENV,
    OCR_PREPROCESS_OUTLINE,
    OCR_PREPROCESS_OUTLINE_ADAPTIVE,
    OCR_UPSCALE_FACTOR,
    TESSERACT_COMPONENT_ID,
    TESSERACT_MODEL_RELATIVE_PATH,
    TesseractOcrProvider,
)
from game_optimization_linux.services.narrator_ocr_worker import TSV_HEADER
from game_optimization_linux.services.narrator_pipeline import _preprocess_log_suffix

WIDTH, HEIGHT = 200, 60
TSV = TSV_HEADER + "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t92\tHello\n"


def _gray_frame(rows: list[bytes]) -> CaptureFrame:
    width = len(rows[0])
    return CaptureFrame(
        session_id="outline",
        generation=1,
        timestamp_monotonic=1.0,
        width=width,
        height=len(rows),
        stride=width,
        pixel_format="gray8",
        pixels=b"".join(rows),
    )


def _uniform(value: int) -> CaptureFrame:
    return _gray_frame([bytes([value]) * WIDTH for _ in range(HEIGHT)])


def _scene(*, background: int, bright_patch: int | None = None) -> CaptureFrame:
    """Outlined subtitle bar (240 inside a 10 px dark outline) on a background.

    A bright patch (x 0-29, no outline) stands for sand/sky: it must not reach
    the outline mask.
    """
    rows = []
    for y in range(HEIGHT):
        row = bytearray([background]) * WIDTH
        if 20 <= y <= 39:
            row[50:150] = bytes([10]) * 100  # dark outline box
        if 25 <= y <= 34:
            row[60:140] = bytes([240]) * 80  # glyphs
        if bright_patch is not None:
            row[0:30] = bytes([bright_patch]) * 30
        rows.append(bytes(row))
    return _gray_frame(rows)


def _provider(tmp_path: Path, mode: str | None = None, sink: list | None = None):
    model = tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"test model")

    def runner(argv, **values):
        if sink is not None:
            encoded = QByteArray(values["input"])
            buffer = QBuffer(encoded)
            assert buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            image = QImage()
            assert image.load(buffer, "PNG")
            sink.append(image.convertToFormat(QImage.Format.Format_Grayscale8))
        return subprocess.CompletedProcess(argv, 0, TSV.encode(), b"")

    return TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=runner,
        preprocess_mode=mode,
    )


def _reference_outline(pixels, width, height, threshold, dark, radius) -> bytes:
    """Direct per-pixel definition used to check the shift-based version."""
    out = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            if pixels[y * width + x] < threshold:
                continue
            for yy in range(max(0, y - radius), min(height, y + radius + 1)):
                row = pixels[yy * width : (yy + 1) * width]
                if min(row[max(0, x - radius) : min(width, x + radius + 1)]) <= dark:
                    out[y * width + x] = 255
                    break
    return bytes(out)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("outline", OCR_PREPROCESS_OUTLINE),
        (" Outline_Adaptive ", OCR_PREPROCESS_OUTLINE_ADAPTIVE),
        ("bright_mask", OCR_PREPROCESS_BRIGHT_MASK),
        ("outline-adaptive", OCR_PREPROCESS_DEFAULT),
        ("outlines", OCR_PREPROCESS_DEFAULT),
        ("", OCR_PREPROCESS_DEFAULT),
    ],
)
def test_only_exact_values_enable_the_outline_modes(value, expected) -> None:
    assert TesseractOcrProvider._resolve_preprocess_mode(value) == expected


def test_env_selects_outline_modes_and_logs_their_parameters(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv(OCR_PREPROCESS_ENV, "outline_adaptive")
    with caplog.at_level(logging.INFO):
        provider = _provider(tmp_path)
    assert provider.preprocess_mode == OCR_PREPROCESS_OUTLINE_ADAPTIVE
    assert (
        "Narrator OCR preprocess mode=outline_adaptive threshold=190 "
        "dark_level=110 radius=2 bright_background_threshold=220 "
        "bright_background_fraction=0.150"
    ) in caplog.text
    caplog.clear()
    monkeypatch.setenv(OCR_PREPROCESS_ENV, "outline")
    with caplog.at_level(logging.INFO):
        assert _provider(tmp_path).preprocess_mode == OCR_PREPROCESS_OUTLINE
    assert "mode=outline threshold=190 dark_level=110 radius=2 " in caplog.text
    assert "bright_background_threshold=none" in caplog.text


@pytest.mark.parametrize(("radius", "threshold", "dark"), [(2, 190, 110), (1, 220, 70), (3, 190, 110)])
def test_outline_mask_matches_the_per_pixel_definition(radius, threshold, dark) -> None:
    width, height = 23, 11
    pixels = bytes((x * 37 + y * 91 + x * y * 13) % 256 for y in range(height) for x in range(width))
    assert TesseractOcrProvider._outline_mask(
        pixels, width, height, threshold=threshold, dark_level=dark, radius=radius
    ) == _reference_outline(pixels, width, height, threshold, dark, radius)


def test_outline_mask_does_not_leak_across_row_edges() -> None:
    # Dark pixel at the end of row 0, bright pixel at the start of row 1: they
    # are adjacent in memory, but in the image 9 columns apart (> radius).
    width, height, radius = 10, 2, 2
    pixels = bytearray([150]) * (width * height)
    pixels[width - 1] = 0
    pixels[width] = 250
    mask = TesseractOcrProvider._outline_mask(
        bytes(pixels), width, height, threshold=190, dark_level=110, radius=radius
    )
    assert mask[width] == 0


def test_outline_keeps_outlined_glyphs_and_drops_bright_background() -> None:
    frame = _scene(background=120, bright_patch=250)
    prepared = TesseractOcrProvider._preprocess_outline(frame, adaptive=False)
    assert (prepared.preprocess, prepared.fallback) == (OCR_PREPROCESS_OUTLINE, "")
    assert prepared.threshold == 190 and prepared.switched == ""
    image = prepared.image
    assert image.width() == round(WIDTH * OCR_UPSCALE_FACTOR)
    assert image.pixelColor(121, 61).red() == 255  # glyph next to outline
    assert image.pixelColor(2 * 100, 2 * 30).red() == 0  # glyph far from outline
    assert image.pixelColor(2 * 10, 2 * 30).red() == 0  # bright patch, no outline
    # The bright mask, in contrast, keeps the unoutlined bright patch.
    masked, _ = TesseractOcrProvider._preprocess_bright_mask(frame)
    assert masked.pixelColor(2 * 10, 2 * 30).red() == 255


def test_adaptive_switches_to_220_only_on_bright_background() -> None:
    dark_scene = _scene(background=60)
    calm = TesseractOcrProvider._preprocess_outline(dark_scene, adaptive=True)
    assert calm.bright_fraction is not None and calm.bright_fraction <= 0.15
    assert (calm.preprocess, calm.threshold, calm.switched) == (
        OCR_PREPROCESS_OUTLINE_ADAPTIVE,
        190,
        "no",
    )
    bright_scene = _scene(background=60, bright_patch=250)  # 30/200 + bar > 15%
    switched = TesseractOcrProvider._preprocess_outline(bright_scene, adaptive=True)
    assert 0.15 < switched.bright_fraction <= 0.35
    assert (switched.preprocess, switched.threshold, switched.switched) == (
        OCR_PREPROCESS_OUTLINE_ADAPTIVE,
        220,
        "yes",
    )
    plain = TesseractOcrProvider._preprocess_outline(bright_scene, adaptive=False)
    assert plain.threshold == 190


@pytest.mark.parametrize("adaptive", [False, True])
def test_outline_uses_the_bright_mask_fallback_rule(adaptive) -> None:
    frame = _uniform(240)
    prepared = TesseractOcrProvider._preprocess_outline(frame, adaptive=adaptive)
    assert prepared.preprocess == OCR_PREPROCESS_DEFAULT
    assert prepared.fallback == OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT
    assert prepared.threshold == 0
    assert prepared.image == TesseractOcrProvider._preprocess_frame(frame)
    _, mask_fallback = TesseractOcrProvider._preprocess_bright_mask(frame)
    assert prepared.fallback == mask_fallback


def test_outline_dark_empty_frame_stays_black() -> None:
    prepared = TesseractOcrProvider._preprocess_outline(_uniform(0), adaptive=True)
    assert prepared.fallback == ""
    assert prepared.image.pixelColor(50, 50).red() == 0


def test_recognize_reports_outline_fields(tmp_path: Path) -> None:
    sent: list[QImage] = []
    provider = _provider(tmp_path, mode="outline_adaptive", sink=sent)
    result = provider.recognize(_scene(background=60, bright_patch=250), language="en")
    assert result.text == "Hello"
    assert (result.preprocess, result.preprocess_threshold, result.preprocess_switched) == (
        OCR_PREPROCESS_OUTLINE_ADAPTIVE,
        220,
        "yes",
    )
    assert result.preprocess_bright_fraction is not None
    assert sent[-1].pixelColor(2 * 10, 2 * 30).red() == 0
    fell_back = provider.recognize(_uniform(240), language="en")
    assert fell_back.preprocess == OCR_PREPROCESS_DEFAULT
    assert fell_back.preprocess_fallback == OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT
    assert fell_back.preprocess_threshold == 0


def test_default_and_bright_mask_results_carry_no_outline_fields(tmp_path: Path) -> None:
    for mode in ("default", "bright_mask"):
        result = _provider(tmp_path, mode=mode).recognize(
            _scene(background=60), language="en"
        )
        assert result.preprocess_bright_fraction is None
        assert result.preprocess_threshold == 0
        assert result.preprocess_switched == ""


def test_log_suffix_for_outline_modes() -> None:
    assert _preprocess_log_suffix(
        OcrResult(
            "x",
            preprocess="outline_adaptive",
            preprocess_bright_fraction=0.2314,
            preprocess_threshold=220,
            preprocess_switched="yes",
        )
    ) == (
        " preprocess=outline_adaptive preprocess_fallback=none"
        " preprocess_bright_fraction=0.231 preprocess_threshold=220"
        " preprocess_switched=yes"
    )
    assert _preprocess_log_suffix(
        OcrResult(
            "x",
            preprocess="outline",
            preprocess_bright_fraction=0.05,
            preprocess_threshold=190,
        )
    ) == (
        " preprocess=outline preprocess_fallback=none"
        " preprocess_bright_fraction=0.050 preprocess_threshold=190"
    )
    assert _preprocess_log_suffix(
        OcrResult(
            "x",
            preprocess_fallback=OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT,
            preprocess_bright_fraction=0.9,
            preprocess_threshold=0,
            preprocess_switched="no",
        )
    ) == (
        " preprocess=default preprocess_fallback=too_many_bright_pixels"
        " preprocess_bright_fraction=0.900 preprocess_threshold=0"
        " preprocess_switched=no"
    )
    # bright_mask lines are unchanged.
    assert (
        _preprocess_log_suffix(OcrResult("x", preprocess="bright_mask"))
        == " preprocess=bright_mask preprocess_fallback=none"
    )
