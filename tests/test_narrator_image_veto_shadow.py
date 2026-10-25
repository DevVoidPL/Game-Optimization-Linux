"""Image veto, shadow mode (GAME_OPTIMIZATION_NARRATOR_IMAGE_VETO_SHADOW=1).

Log fields only: a pooled outline-mask signature of the OCR'd frame, IoU with
the last emission, and whether the veto *would* block. Decisions, TTS and
audio must be identical with and without the env.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from game_optimization_linux.models.narrator import CaptureFrame
from game_optimization_linux.services.narrator_ocr import (
    IMAGE_VETO_SHADOW_ENV,
    TesseractOcrProvider,
)
from game_optimization_linux.services.narrator_pipeline import (
    _ImageVetoShadow,
    mask_signature_iou,
)
from test_narrator_behavior_contract import ContractReplay

REPEAT_LINES = (
    ["Hipsterzy uwielbiają mówić, że nienawidzą hipsterów."] * 2
    + ["Hipsterzy uwielbiają mówić, że nieqawiczą hiosterów."] * 2
)
SIG = (10, 2, 0b11110000111100001111)
OTHER_SIG = (10, 2, 0b00001111000011110000)


def _reference_signature(mask: bytes, width: int, height: int, block: int = 4):
    columns, rows = width // block, height // block
    bits = 0
    for row in range(rows):
        for column in range(columns):
            cell = any(
                mask[y * width + x]
                for y in range(row * block, row * block + block)
                for x in range(column * block, column * block + block)
            )
            bits = (bits << 1) | int(cell)
    return columns, rows, bits


def test_mask_signature_matches_block_any_definition() -> None:
    width, height = 23, 10  # partial right/bottom blocks are dropped
    mask = bytes(255 if (x * 7 + y * 3) % 11 == 0 else 0 for y in range(height) for x in range(width))
    assert TesseractOcrProvider._mask_signature(mask, width, height) == (
        _reference_signature(mask, width, height)
    )
    assert TesseractOcrProvider._mask_signature(bytes(width * height), width, height)[2] == 0


def test_mask_signature_iou() -> None:
    assert mask_signature_iou(SIG, SIG) == 1.0
    assert mask_signature_iou(SIG, OTHER_SIG) == 0.0
    half = (10, 2, 0b11110000111100000000)
    assert mask_signature_iou(SIG, half) == pytest.approx(8 / 12)
    assert mask_signature_iou(SIG, (11, 2, SIG[2])) is None  # other ROI size
    assert mask_signature_iou(SIG, None) is None
    assert mask_signature_iou((10, 2, 0), (10, 2, 0)) is None


def _fields(suffix: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in suffix.split())


def test_shadow_rule_needs_iou_time_no_disappearance_and_similar_text() -> None:
    text = "Hipsterzy uwielbiają mówić, że nienawidzą hipsterów."
    damaged = "Hipsterzy uwielbiają mówić, że nieqawiczą hiosterów."
    shadow = _ImageVetoShadow()
    first = _fields(shadow.accepted(signature=SIG, now=10.0, text=text))
    assert first["veto_iou"] == "none" and first["veto_would_block"] == "no"
    assert _fields(shadow.accepted(signature=SIG, now=12.0, text=damaged))["veto_would_block"] == "yes"
    # Different image.
    assert _fields(shadow.accepted(signature=OTHER_SIG, now=13.0, text=damaged))["veto_would_block"] == "no"
    # Too late.
    shadow.accepted(signature=SIG, now=14.0, text=text)
    late = _fields(shadow.accepted(signature=SIG, now=22.5, text=damaged))
    assert late["veto_dt"] == "8.5" and late["veto_would_block"] == "no"
    # Text vanished in between.
    shadow.observe(reason_code="confirmed_disappearance")
    gone = _fields(shadow.accepted(signature=SIG, now=23.0, text=text))
    assert gone["veto_disappeared"] == "yes" and gone["veto_would_block"] == "no"
    # Same image, but clearly different text (new line in the same layout).
    other = _fields(shadow.accepted(signature=SIG, now=24.0, text="Jesteś protohipsterem."))
    assert other["veto_would_block"] == "no"
    # No signature (fallback image, classic mode, bright_mask).
    assert _fields(shadow.accepted(signature=None, now=25.0, text=text))["veto_iou"] == "none"


def _frame(width: int = 64, height: int = 24) -> CaptureFrame:
    rows = []
    for y in range(height):
        row = bytearray([60]) * width
        if 6 <= y <= 17:
            row[8:56] = bytes([10]) * 48
        if 9 <= y <= 14:
            row[12:52] = bytes([240]) * 40
        rows.append(bytes(row))
    return CaptureFrame(
        session_id="veto", generation=1, timestamp_monotonic=1.0, width=width,
        height=height, stride=width, pixel_format="gray8", pixels=b"".join(rows),
    )


def test_signature_only_with_env_and_outline_mask() -> None:
    plain = TesseractOcrProvider._preprocess_outline(_frame(), adaptive=True)
    assert plain.mask_signature is None
    signed = TesseractOcrProvider._preprocess_outline(_frame(), adaptive=True, signature=True)
    columns, rows, bits = signed.mask_signature
    assert (columns, rows) == (16, 6) and bits != 0
    light = CaptureFrame(
        session_id="veto", generation=1, timestamp_monotonic=1.0, width=64,
        height=24, stride=64, pixel_format="gray8", pixels=bytes([160]) * (64 * 24),
    )
    fell_back = TesseractOcrProvider._preprocess_outline(light, adaptive=True, signature=True)
    assert fell_back.fallback and fell_back.mask_signature is None


def _run(tmp_path: Path, caplog, *, signature) -> tuple[list[str], list[str]]:
    replay = ContractReplay(tmp_path)
    try:
        if signature is not None:
            base = replay.pipeline.ocr.recognize
            replay.pipeline.ocr.recognize = lambda frame, *, language: replace(  # type: ignore[method-assign]
                base(frame, language=language), preprocess_mask=signature
            )
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="game_optimization_linux.services.narrator_pipeline"):
            for text in REPEAT_LINES:
                replay.observe(text)
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Narrator OCR: raw=")]
        return list(replay.tts.values), lines
    finally:
        replay.close()


def test_without_env_the_ocr_log_has_no_veto_fields(tmp_path: Path, monkeypatch, caplog) -> None:
    monkeypatch.delenv(IMAGE_VETO_SHADOW_ENV, raising=False)
    tts, lines = _run(tmp_path, caplog, signature=SIG)
    assert len(tts) == 2 and lines
    assert all("veto_" not in line for line in lines)


def test_shadow_marks_the_repeat_and_changes_no_decision(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.delenv(IMAGE_VETO_SHADOW_ENV, raising=False)
    tts_off, lines_off = _run(tmp_path / "off", caplog, signature=SIG)
    monkeypatch.setenv(IMAGE_VETO_SHADOW_ENV, "1")
    tts_on, lines_on = _run(tmp_path / "on", caplog, signature=SIG)
    assert tts_on == tts_off == [REPEAT_LINES[0], REPEAT_LINES[2]]
    accepted = [line for line in lines_on if "decision=accepted" in line]
    assert len(accepted) == 2
    assert "veto_would_block=no" in accepted[0]
    assert "veto_iou=1.00" in accepted[1] and "veto_would_block=yes" in accepted[1]
    # Apart from the appended fields the lines are the same.
    strip = lambda line: line.split(" veto_iou=")[0]  # noqa: E731
    assert [strip(line) for line in lines_on] == lines_off


def test_shadow_does_not_flag_a_new_line_with_another_image(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv(IMAGE_VETO_SHADOW_ENV, "1")
    replay = ContractReplay(tmp_path)
    try:
        base = replay.pipeline.ocr.recognize
        signatures = iter([SIG, SIG, OTHER_SIG, OTHER_SIG])
        replay.pipeline.ocr.recognize = lambda frame, *, language: replace(  # type: ignore[method-assign]
            base(frame, language=language), preprocess_mask=next(signatures)
        )
        with caplog.at_level(logging.INFO, logger="game_optimization_linux.services.narrator_pipeline"):
            for text in ["Kurwa, ja ich serio nienawidzę."] * 2 + ["Samonienawiść. Częsta przypadłość hipsterów."] * 2:
                replay.observe(text)
        accepted = [r.getMessage() for r in caplog.records if "decision=accepted" in r.getMessage()]
        assert len(replay.tts.values) == 2 and len(accepted) == 2
        assert "veto_iou=0.00" in accepted[1] and "veto_would_block=no" in accepted[1]
    finally:
        replay.close()
