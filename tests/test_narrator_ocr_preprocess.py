from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from game_optimization_linux.models.narrator import CaptureFrame, OcrResult
from game_optimization_linux.services.narrator_ocr import (
    OCR_BRIGHT_MASK_FALLBACK_DARK_TEXT,
    OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT,
    OCR_PREPROCESS_BRIGHT_MASK,
    OCR_PREPROCESS_DEFAULT,
    OCR_PREPROCESS_ENV,
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
        session_id="preprocess",
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


def _subtitle_on_texture() -> CaptureFrame:
    """Dark, textured background (40..110) with a bright 230 'text' bar."""
    rows = []
    for y in range(HEIGHT):
        row = bytearray(40 + ((x * 7 + y * 13) % 71) for x in range(WIDTH))
        if 26 <= y <= 33:
            row[40:160] = bytes([230]) * 120
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("0", None),
        ("bright", None),
        ("default", OCR_PREPROCESS_DEFAULT),
        ("classic", OCR_PREPROCESS_DEFAULT),
        (" Classic ", OCR_PREPROCESS_DEFAULT),
        ("bright_mask", OCR_PREPROCESS_BRIGHT_MASK),
        (" BRIGHT_MASK ", OCR_PREPROCESS_BRIGHT_MASK),
    ],
)
def test_only_the_exact_opt_in_value_enables_the_mask(value, expected) -> None:
    assert TesseractOcrProvider._resolve_preprocess_mode(value) == expected


def test_without_env_the_setting_decides_and_defaults_to_outline_adaptive(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.delenv(OCR_PREPROCESS_ENV, raising=False)
    with caplog.at_level(logging.INFO):
        provider = _provider(tmp_path)
    assert provider.preprocess_mode == OCR_PREPROCESS_OUTLINE_ADAPTIVE
    assert (
        "Narrator OCR preprocess selected mode=outline_adaptive source=settings "
        "setting=automatic"
    ) in caplog.text
    provider.set_settings_mode("classic")
    assert provider.preprocess_mode == OCR_PREPROCESS_DEFAULT
    provider.set_settings_mode("automatic")
    assert provider.preprocess_mode == OCR_PREPROCESS_OUTLINE_ADAPTIVE
    provider.set_settings_mode("nonsense")
    assert provider.preprocess_mode == OCR_PREPROCESS_OUTLINE_ADAPTIVE


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("classic", OCR_PREPROCESS_DEFAULT),
        ("bright_mask", OCR_PREPROCESS_BRIGHT_MASK),
        ("outline_adaptive", OCR_PREPROCESS_OUTLINE_ADAPTIVE),
    ],
)
def test_env_overrides_the_setting(tmp_path: Path, monkeypatch, env, expected) -> None:
    monkeypatch.setenv(OCR_PREPROCESS_ENV, env)
    provider = _provider(tmp_path)
    assert provider.preprocess_mode == expected
    for setting in ("classic", "automatic"):
        provider.set_settings_mode(setting)
        assert provider.preprocess_mode == expected


def test_unknown_env_value_is_ignored_with_a_warning(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv(OCR_PREPROCESS_ENV, "bright")
    with caplog.at_level(logging.WARNING):
        provider = _provider(tmp_path)
    assert provider.preprocess_mode == OCR_PREPROCESS_OUTLINE_ADAPTIVE
    assert "Ignoring unknown GAME_OPTIMIZATION_NARRATOR_OCR_PREPROCESS" in caplog.text


def test_bright_mask_env_logs_its_unchanged_parameter_line(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv(OCR_PREPROCESS_ENV, "bright_mask")
    with caplog.at_level(logging.INFO):
        assert _provider(tmp_path).preprocess_mode == OCR_PREPROCESS_BRIGHT_MASK
    assert (
        "Narrator OCR preprocess mode=bright_mask threshold=190 "
        "fallback_max_fraction=0.350 fallback_min_fraction=0.003"
    ) in caplog.text


@pytest.mark.parametrize("how", ["env", "setting"])
def test_classic_ocr_image_is_the_unchanged_default(
    tmp_path: Path, monkeypatch, how
) -> None:
    if how == "env":
        monkeypatch.setenv(OCR_PREPROCESS_ENV, "classic")
    else:
        monkeypatch.delenv(OCR_PREPROCESS_ENV, raising=False)
    sent: list[QImage] = []
    provider = _provider(tmp_path, sink=sent)
    if how == "setting":
        provider.set_settings_mode("classic")
    for frame in (_subtitle_on_texture(), _uniform(240), _uniform(0)):
        image, used, fallback = provider._prepare_ocr_image(frame)
        expected = TesseractOcrProvider._preprocess_frame(frame)
        assert (used, fallback) == (OCR_PREPROCESS_DEFAULT, "")
        assert image == expected
        result = provider.recognize(frame, language="en")
        assert (result.preprocess, result.preprocess_fallback) == ("default", "")
        assert _preprocess_log_suffix(result) == ""
        assert sent[-1] == expected


def test_bright_mask_turns_bright_glyphs_white_and_background_black() -> None:
    image, fallback = TesseractOcrProvider._preprocess_bright_mask(
        _subtitle_on_texture()
    )
    assert fallback == ""
    assert image.format() == QImage.Format.Format_Grayscale8
    assert image.width() == round(WIDTH * OCR_UPSCALE_FACTOR)
    assert image.height() == round(HEIGHT * OCR_UPSCALE_FACTOR)
    assert image.pixelColor(200, 59).red() == 255  # inside the bar
    assert image.pixelColor(20, 10).red() == 0  # textured background
    assert image.pixelColor(380, 110).red() == 0


def test_mostly_bright_frame_falls_back_to_the_default_image() -> None:
    frame = _uniform(240)
    image, fallback = TesseractOcrProvider._preprocess_bright_mask(frame)
    assert fallback == OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT
    assert image == TesseractOcrProvider._preprocess_frame(frame)


def test_light_background_without_bright_glyphs_falls_back() -> None:
    frame = _uniform(160)
    image, fallback = TesseractOcrProvider._preprocess_bright_mask(frame)
    assert fallback == OCR_BRIGHT_MASK_FALLBACK_DARK_TEXT
    assert image == TesseractOcrProvider._preprocess_frame(frame)


def test_dark_text_on_light_background_never_uses_the_mask() -> None:
    rows = []
    for y in range(HEIGHT):
        row = bytearray([235]) * WIDTH
        if 26 <= y <= 33:
            row[40:160] = bytes([20]) * 120
        rows.append(bytes(row))
    _, fallback = TesseractOcrProvider._preprocess_bright_mask(_gray_frame(rows))
    assert fallback in {
        OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT,
        OCR_BRIGHT_MASK_FALLBACK_DARK_TEXT,
    }


def test_dark_empty_frame_keeps_the_mask_and_stays_black() -> None:
    image, fallback = TesseractOcrProvider._preprocess_bright_mask(_uniform(0))
    assert fallback == ""
    assert image.pixelColor(10, 10).red() == 0
    assert image.pixelColor(300, 100).red() == 0


def test_recognize_reports_used_preprocess_and_fallback(tmp_path: Path) -> None:
    provider = _provider(tmp_path, mode="bright_mask")
    masked = provider.recognize(_subtitle_on_texture(), language="en")
    assert (masked.preprocess, masked.preprocess_fallback) == ("bright_mask", "")
    assert masked.text == "Hello"
    fell_back = provider.recognize(_uniform(240), language="en")
    assert fell_back.preprocess == "default"
    assert fell_back.preprocess_fallback == OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT


def test_grayscale_bytes_ignore_qimage_row_padding() -> None:
    rows = [bytes(range(y * 7, y * 7 + 7)) for y in range(5)]  # 7 px: padded rows
    image = TesseractOcrProvider._frame_image(_gray_frame(rows))
    assert image.bytesPerLine() != 7
    assert TesseractOcrProvider._grayscale_bytes(image) == b"".join(rows)


def test_log_suffix_is_empty_for_default_and_explicit_otherwise() -> None:
    assert _preprocess_log_suffix(OcrResult("x")) == ""
    assert _preprocess_log_suffix(object()) == ""
    assert (
        _preprocess_log_suffix(OcrResult("x", preprocess="bright_mask"))
        == " preprocess=bright_mask preprocess_fallback=none"
    )
    assert _preprocess_log_suffix(
        OcrResult("x", preprocess_fallback=OCR_BRIGHT_MASK_FALLBACK_TOO_BRIGHT)
    ) == (" preprocess=default preprocess_fallback=too_many_bright_pixels")
