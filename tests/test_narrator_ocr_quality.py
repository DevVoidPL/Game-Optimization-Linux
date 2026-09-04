from __future__ import annotations

import json
import stat
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
from game_optimization_linux.services.narrator_ocr_worker import TSV_HEADER
from game_optimization_linux.services.narrator_pipeline import (
    SubtitleRegionStabilizer,
    SubtitleTextGate,
)


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


@pytest.mark.parametrize(
    ("first_text", "second_text", "expected"),
    (
        ("Już idę.", "Juz ide", "Już idę."),
        ("Uciekaj!", "Uciekał!", "Uciekaj!"),
        ("Zostań tutaj.", "Zostan tutaj", "Zostań tutaj."),
    ),
)
def test_minor_polish_ocr_variation_reaches_consensus(
    first_text: str,
    second_text: str,
    expected: str,
) -> None:
    gate = SubtitleTextGate()

    first = gate.observe(first_text, 0.94, now=1.0)
    second = gate.observe(second_text, 0.90, now=1.2)

    assert first.decision == "candidate_started"
    assert first.candidate_observation_count == 1
    assert second.decision == "accepted_consensus"
    assert second.candidate_observation_count == 2
    assert second.candidate_match_kind in {"normalized_exact", "single_edit"}
    assert second.accepted_text == expected


def test_short_distinct_polish_words_do_not_use_single_edit_relaxation() -> None:
    gate = SubtitleTextGate()

    gate.observe("Nie.", 0.94, now=1.0)
    observation = gate.observe("Nic.", 0.94, now=1.2)

    assert observation.accepted_text == ""
    assert observation.decision == "candidate_replaced_dissimilar"
    assert observation.candidate_observation_count == 1


def test_two_confirmed_one_letter_phrases_remain_distinct_dialogue() -> None:
    gate = SubtitleTextGate()

    gate.observe("Uciekaj!", 0.94, now=1.0)
    assert gate.observe("Uciekaj!", 0.94, now=1.2).accepted_text == "Uciekaj!"
    next_first = gate.observe("Uciekał!", 0.94, now=1.4)
    next_second = gate.observe("Uciekał!", 0.94, now=1.6)

    assert next_first.decision == "candidate_started"
    assert next_second.accepted_text == "Uciekał!"


def test_similar_phrases_with_changed_numbers_never_share_consensus() -> None:
    gate = SubtitleTextGate()

    first = gate.observe("Pokój 101.", 0.94, now=1.0)
    changed = gate.observe("Pokój 102.", 0.94, now=1.2)
    confirmed = gate.observe("Pokój 102.", 0.94, now=1.4)

    assert first.decision == "candidate_started"
    assert changed.decision == "candidate_replaced_dissimilar"
    assert changed.accepted_text == ""
    assert confirmed.candidate_match_kind == "normalized_exact"
    assert confirmed.accepted_text == "Pokój 102."


def test_one_confidence_failure_retains_candidate_without_counting_it() -> None:
    gate = SubtitleTextGate()

    gate.observe("Zostań tutaj.", 0.94, now=1.0)
    observation = gate.observe("Zostań tutaj.", 0.40, now=1.2)

    assert observation.credible is False
    assert observation.rejection_reason == "low_confidence"
    assert observation.decision == "candidate_retained_after_low_confidence"
    assert observation.candidate_observation_count == 1
    assert observation.needs_confirmation is True

    reset = gate.observe("Zostań tutaj.", 0.40, now=1.3)
    assert reset.decision == "candidate_reset_low_confidence"
    assert reset.candidate_observation_count == 0


def test_wildly_different_ocr_frames_do_not_emit_a_phrase() -> None:
    gate = SubtitleTextGate()

    first = gate.observe("Open the door.", 0.90, now=1.0)
    second = gate.observe("Leave Gotham now!", 0.90, now=1.2)

    assert first.accepted_text == ""
    assert second.accepted_text == ""
    assert second.rejection_reason == "unstable"
    assert second.candidate_replaced is True


def test_one_empty_observation_does_not_destroy_two_frame_consensus() -> None:
    gate = SubtitleTextGate(min_confidence=0.62)
    assert gate.observe("No.", 0.61, now=1.0).rejection_reason == "low_confidence"
    assert gate.observe("No.", 0.90, now=1.2).rejection_reason == "unstable"
    gap = gate.observe("", None, now=1.3)
    assert gap.decision == "candidate_retained_after_empty"
    assert gap.candidate_observation_count == 1
    assert gate.observe("No.", 0.90, now=1.4).accepted_text == "No."


def test_two_empty_observations_still_clear_consensus() -> None:
    gate = SubtitleTextGate()
    gate.observe("Uciekaj!", 0.94, now=1.0)

    assert gate.observe("", None, now=1.1).needs_confirmation is True
    reset = gate.observe("", None, now=1.2)
    assert reset.decision == "candidate_reset_empty"
    assert gate.observe("Uciekaj!", 0.94, now=1.3).decision == "candidate_started"


@pytest.mark.parametrize(
    "text",
    (
        "Siema",
        "Cześć",
        "Co tam?",
        "Nie.",
        "Tak.",
        "Nie wiem.",
        "Uciekaj!",
        "Idź!",
        "Ja?",
        "Ty?",
        "OK",
        "3 km",
        "W drogę.",
    ),
)
def test_short_polish_dialogue_is_not_rejected_for_being_short(text: str) -> None:
    gate = SubtitleTextGate()

    first = gate.observe(text, 0.96, now=1.0)
    second = gate.observe(text, 0.96, now=1.2)

    assert first.decision == "candidate_started"
    assert first.rejection_reason == "unstable"
    assert second.decision == "accepted_consensus"
    assert second.accepted_text == text


def test_strong_clean_short_evidence_can_accept_one_transient_observation() -> None:
    gate = SubtitleTextGate()

    observation = gate.observe(
        "Siema",
        0.97,
        now=1.0,
        strong_short_phrase_evidence=True,
    )

    assert observation.accepted_text == "Siema"
    assert observation.decision == "accepted_strong_short_evidence"
    assert observation.required_observations == 1


def test_short_text_without_provider_evidence_still_requires_consensus() -> None:
    observation = SubtitleTextGate().observe("Siema", 0.99, now=1.0)

    assert observation.accepted_text == ""
    assert observation.decision == "candidate_started"
    assert observation.required_observations == 2


def test_transient_gap_cannot_extend_candidate_past_stability_window() -> None:
    gate = SubtitleTextGate()
    gate.observe("Zostań tutaj.", 0.94, now=1.0)

    expired = gate.observe("", None, now=2.3)

    assert expired.decision == "candidate_reset_empty"
    assert expired.needs_confirmation is False
    assert gate.observe("Zostań tutaj.", 0.94, now=2.4).decision == "candidate_started"


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


def test_localized_subtitle_change_is_not_diluted_by_a_large_roi() -> None:
    width = height = 100
    blank = CaptureFrame(
        session_id="quality",
        generation=1,
        timestamp_monotonic=1.0,
        width=width,
        height=height,
        stride=width,
        pixel_format="gray8",
        pixels=bytes(width * height),
    )
    subtitle_pixels = bytearray(blank.pixels)
    subtitle_pixels[4_000:4_400] = bytes([255]) * 400
    subtitle = CaptureFrame(
        session_id="quality",
        generation=1,
        timestamp_monotonic=1.2,
        width=width,
        height=height,
        stride=width,
        pixel_format="gray8",
        pixels=bytes(subtitle_pixels),
    )

    legacy = SubtitleRegionStabilizer()
    assert legacy.consider(
        blank, threshold=0.08, stabilization_seconds=0.2
    ) is None
    accepted_blank = CaptureFrame(
        session_id=blank.session_id,
        generation=blank.generation,
        timestamp_monotonic=1.3,
        width=blank.width,
        height=blank.height,
        stride=blank.stride,
        pixel_format=blank.pixel_format,
        pixels=blank.pixels,
    )
    assert legacy.consider(
        accepted_blank, threshold=0.08, stabilization_seconds=0.2
    ) == accepted_blank
    assert legacy.consider(
        subtitle, threshold=0.08, stabilization_seconds=0.2
    ) is None

    prompt = SubtitleRegionStabilizer()
    assert prompt.consider_for_ocr(blank, threshold=0.08) == blank
    assert prompt.last_decision == "initial_probe"
    assert prompt.consider_for_ocr(subtitle, threshold=0.08) == subtitle
    assert prompt.last_decision == "localized_change"
    assert prompt.last_localized_difference is not None
    assert prompt.last_localized_difference >= 0.08
    assert prompt.consider_for_ocr(subtitle, threshold=0.08) is None
    assert prompt.last_decision == "unchanged"


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
        TSV_HEADER
        + "4\t1\t1\t1\t1\t0\t0\t0\t0\t0\t99\tduplicate line\n"
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t8\t90\tBatman\n"
        "5\t1\t1\t1\t1\t2\t11\t0\t10\t8\t-1\tartifact\n"
        "5\t1\t1\t1\t1\t3\t22\t0\t10\t8\t60\tNo\n"
    )

    text, confidence = TesseractOcrProvider._parse_tsv(payload)

    assert text == "Batman artifact No"
    assert confidence == pytest.approx((0.9 * 6 + 0.6 * 2) / 8)

    _text, _confidence, tokens = TesseractOcrProvider._parse_tsv_detailed(
        payload
    )
    assert [token["text"] for token in tokens] == ["Batman", "artifact", "No"]
    assert tokens[1]["valid_confidence"] is False
    assert tokens[2]["left"] == 22


def test_filtered_phrase_excludes_invalid_confidence_without_hiding_raw() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t70\t24\t96\tBatman\n"
        + "5\t1\t1\t1\t1\t2\t80\t0\t60\t24\t95\twraca.\n"
        + "5\t1\t1\t1\t1\t3\t150\t0\t40\t24\t-1\tartifact\n"
    )

    raw, filtered, confidence, raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == "Batman wraca. artifact"
    assert filtered == "Batman wraca."
    assert confidence == pytest.approx(raw_confidence)
    assert tokens[-1]["filter_reason"] == "invalid_confidence"


def test_strong_subtitle_line_excludes_low_confidence_garbage_line() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t100\t120\t100\t32\t96\tSystem\n"
        + "5\t1\t1\t1\t1\t2\t210\t120\t110\t32\t95\tdziała.\n"
        + "5\t1\t2\t1\t1\t1\t800\t230\t50\t20\t18\tPZA\n"
        + "5\t1\t2\t1\t1\t2\t860\t230\t12\t20\t8\tM\n"
        + "5\t1\t2\t1\t1\t3\t880\t230\t12\t20\t4\tw\n"
        + "5\t1\t2\t1\t1\t4\t900\t230\t12\t20\t0\t=\n"
    )

    raw, filtered, confidence, raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == "System działa.\nPZA M w ="
    assert filtered == "System działa."
    assert confidence == pytest.approx((0.96 * 6 + 0.95 * 6) / 12)
    assert raw_confidence is not None and raw_confidence < confidence
    assert {token["filter_reason"] for token in tokens[-4:]} == {
        "weak_isolated_line"
    }


def test_uncertain_natural_short_line_is_not_deleted_by_stronger_line() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t90\t28\t96\tHUD\n"
        + "5\t1\t2\t1\t1\t1\t100\t70\t90\t30\t46\tSiema\n"
    )

    raw, filtered, _confidence, _raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == filtered == "HUD\nSiema"
    assert tokens[-1]["included"] is True
    assert tokens[-1]["filter_reason"] == ""


def test_spatially_separated_weak_suffix_is_removed_conservatively() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t90\t30\t96\tOdbierz\n"
        + "5\t1\t1\t1\t1\t2\t100\t0\t120\t30\t95\tkomunikaty.\n"
        + "5\t1\t1\t1\t1\t3\t400\t0\t25\t22\t22\twi\n"
        + "5\t1\t1\t1\t1\t4\t435\t0\t12\t22\t12\tE\n"
        + "5\t1\t1\t1\t1\t5\t455\t0\t12\t22\t8\te\n"
        + "5\t1\t1\t1\t1\t6\t475\t0\t18\t22\t4\t||\n"
    )

    raw, filtered, confidence, _raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == "Odbierz komunikaty. wi E e ||"
    assert filtered == "Odbierz komunikaty."
    assert confidence is not None and confidence > 0.94
    assert {token["filter_reason"] for token in tokens[-4:]} == {
        "separated_weak_suffix"
    }


def test_weak_suffix_cluster_is_removed_despite_one_false_confident_glyph() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t90\t30\t96\tOdbierz\n"
        + "5\t1\t1\t1\t1\t2\t100\t0\t120\t30\t95\tkomunikaty.\n"
        + "5\t1\t1\t1\t1\t3\t400\t0\t25\t22\t22\twi\n"
        + "5\t1\t1\t1\t1\t4\t435\t0\t12\t22\t65\tE\n"
        + "5\t1\t1\t1\t1\t5\t455\t0\t12\t22\t8\te\n"
        + "5\t1\t1\t1\t1\t6\t475\t0\t18\t22\t4\t||\n"
    )

    raw, filtered, confidence, _raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == "Odbierz komunikaty. wi E e ||"
    assert filtered == "Odbierz komunikaty."
    assert confidence is not None and confidence > 0.94
    assert {token["filter_reason"] for token in tokens[-4:]} == {
        "separated_weak_suffix"
    }


def test_low_confidence_punctuation_only_line_does_not_join_subtitle() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t90\t28\t96\tUciekaj!\n"
        + "5\t1\t2\t1\t1\t1\t700\t90\t12\t18\t12\t=\n"
        + "5\t1\t2\t1\t1\t2\t720\t90\t12\t18\t8\t€\n"
    )

    raw, filtered, confidence, _raw_confidence, tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == "Uciekaj!\n= €"
    assert filtered == "Uciekaj!"
    assert confidence == pytest.approx(0.96)
    assert {token["filter_reason"] for token in tokens[-2:]} == {
        "weak_isolated_line"
    }


def test_clean_two_lines_single_letters_diacritics_and_numbers_survive() -> None:
    payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t12\t24\t95\tA\n"
        + "5\t1\t1\t1\t1\t2\t20\t0\t12\t24\t94\tI\n"
        + "5\t1\t1\t1\t1\t3\t40\t0\t12\t24\t96\tw\n"
        + "5\t1\t1\t1\t1\t4\t60\t0\t120\t24\t95\tŁodzi.\n"
        + "5\t1\t1\t1\t2\t1\t0\t35\t100\t24\t93\tPokój\n"
        + "5\t1\t1\t1\t2\t2\t110\t35\t50\t24\t92\t101.\n"
    )

    raw, filtered, confidence, _raw_confidence, _tokens = (
        TesseractOcrProvider._analyze_tsv(payload)
    )

    assert raw == filtered == "A I w Łodzi.\nPokój 101."
    assert confidence is not None and confidence > 0.92


def test_real_style_noisy_frames_confirm_clean_high_confidence_candidate() -> None:
    subtitle = "W obliczu śmierci nie ma się już czego bać. Twoja zemsta nadejdzie."
    # Build the long line explicitly so the fixture remains valid 12-column TSV.
    words = subtitle.split()
    first_payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t80\t24\t10\tPZA\n"
        + "5\t1\t1\t1\t1\t2\t90\t0\t20\t24\t5\tM\n"
        + "".join(
            f"5\t1\t2\t1\t1\t{index}\t{200 + index * 70}\t90\t60\t30\t96\t{word}\n"
            for index, word in enumerate(words, start=1)
        )
    )
    second_payload = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t100\t90\t60\t30\t0\tSZRSY\n"
        + "5\t1\t1\t1\t1\t2\t165\t90\t35\t30\t35\tAN\n"
        + "".join(
            f"5\t1\t1\t1\t1\t{index + 2}\t{200 + index * 70}\t90\t60\t30\t96\t{word}\n"
            for index, word in enumerate(words, start=1)
        )
    )
    _raw1, filtered1, confidence1, _raw_conf1, _tokens1 = (
        TesseractOcrProvider._analyze_tsv(first_payload)
    )
    _raw2, filtered2, confidence2, _raw_conf2, _tokens2 = (
        TesseractOcrProvider._analyze_tsv(second_payload)
    )
    gate = SubtitleTextGate()

    first = gate.observe(filtered1, confidence1, now=1.0)
    second = gate.observe(filtered2, confidence2, now=1.2)

    assert filtered1 == subtitle
    assert filtered2 == f"SZRSY AN {subtitle}"
    assert first.decision == "candidate_started"
    assert second.candidate_match_kind == "similarity"
    assert second.accepted_text == subtitle


@pytest.mark.parametrize(
    "token_text",
    ('"word', '„word”', 'word"', "?!...", "Zażółć gęślą"),
)
def test_tsv_text_treats_quotes_punctuation_and_polish_as_literal(
    token_text: str,
) -> None:
    payload = (
        TSV_HEADER
        + f"5\t1\t1\t1\t1\t1\t10\t20\t80\t24\t93.5\t{token_text}\n"
    )

    text, confidence, tokens = TesseractOcrProvider._parse_tsv_detailed(payload)

    assert text == token_text
    if any(character.isalnum() for character in token_text):
        assert confidence == pytest.approx(0.935)
    else:
        assert confidence is None
    assert [token["text"] for token in tokens] == [token_text]


def test_unmatched_ascii_quote_cannot_consume_following_tsv_rows() -> None:
    payload = (
        TSV_HEADER
        + '5\t1\t1\t1\t1\t13\t1800\t42\t140\t24\t42.4\t"RALZESW\n'
        "5\t1\t1\t1\t1\t14\t2029\t41\t195\t25\t0.0\tNOO\n"
        "5\t1\t1\t1\t1\t15\t2194\t37\t34\t33\t0.0\tWA\n"
    )

    text, _confidence, tokens = TesseractOcrProvider._parse_tsv_detailed(payload)

    assert text == '"RALZESW NOO WA'
    assert [token["text"] for token in tokens] == ['"RALZESW', "NOO", "WA"]
    assert "2029" not in text
    assert "0.0" not in text
    assert "\t" not in text
    assert "\n" not in text


def test_tsv_metadata_cannot_reach_normalized_or_accepted_subtitle_text() -> None:
    payload = (
        TSV_HEADER
        + '5\t1\t1\t1\t1\t1\t2029\t41\t195\t25\t94\t"Batman\n'
        "5\t1\t1\t1\t1\t2\t2194\t37\t100\t33\t92\twraca.\n"
    )
    text, confidence = TesseractOcrProvider._parse_tsv(payload)
    normalized = TesseractOcrProvider._normalize_debug_phrase(text)
    gate = SubtitleTextGate()

    first = gate.observe(text, confidence, now=1.0)
    second = gate.observe(text, confidence, now=1.2)

    assert normalized == '"Batman wraca.'
    assert first.rejection_reason == "unstable"
    assert second.accepted_text == '"Batman wraca.'
    for value in (normalized, second.accepted_text):
        assert "2029" not in value
        assert "2194" not in value
        assert "\t" not in value
        assert "\n" not in value


@pytest.mark.parametrize(
    "malformed_row",
    (
        "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t92\tbad\textra\n",
        "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t92\tbad\ncontinued\n",
        "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\tnan\tbad\n",
    ),
)
def test_malformed_tsv_is_rejected_instead_of_becoming_spoken_text(
    malformed_row: str,
) -> None:
    with pytest.raises(ValueError, match="Tesseract returned"):
        TesseractOcrProvider._parse_tsv_detailed(TSV_HEADER + malformed_row)


def test_provider_sends_preprocessed_png_to_tesseract(tmp_path: Path) -> None:
    model = (
        tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    )
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model")
    sizes: list[tuple[int, int, QImage.Format]] = []
    tsv = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t92\tHello\n"
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


def test_provider_preserves_literal_raw_text_and_returns_filtered_phrase(
    tmp_path: Path,
) -> None:
    model = tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model")
    tsv = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t90\t28\t96\tBatman\n"
        + "5\t1\t1\t1\t1\t2\t100\t0\t80\t28\t95\twraca.\n"
        + "5\t1\t2\t1\t1\t1\t700\t90\t30\t18\t10\tPY\n"
        + "5\t1\t2\t1\t1\t2\t740\t90\t10\t18\t5\tI\n"
    )

    result = TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=lambda argv, **values: subprocess.CompletedProcess(
            argv, 0, tsv.encode(), b""
        ),
    ).recognize(_frame(), language="en")

    assert result.raw_text == "Batman wraca.\nPY I"
    assert result.filtered_text == "Batman wraca."
    assert result.text == result.filtered_text
    assert result.confidence is not None and result.confidence > 0.94
    assert result.raw_confidence is not None and result.raw_confidence < 0.90
    assert result.dropped_token_count == 2
    assert result.filter_summary == "weak_isolated_line"
    assert result.clean_short_phrase_evidence is False


@pytest.mark.parametrize(
    ("text", "tokens", "expected"),
    (
        ("Siema", (("Siema", 97, 20, 10, 100, 30),), True),
        ("Co tam?", (("Co", 97, 20, 10, 40, 30), ("tam?", 96, 70, 10, 70, 30)), True),
        ("EE", (("EE", 99, 20, 10, 50, 30),), False),
        ("PZA M w", (("PZA", 99, 20, 10, 50, 30), ("M", 99, 80, 10, 20, 30), ("w", 99, 110, 10, 20, 30)), False),
    ),
)
def test_provider_identifies_only_clean_geometric_short_phrase_evidence(
    tmp_path: Path,
    text: str,
    tokens: tuple[tuple[str, int, int, int, int, int], ...],
    expected: bool,
) -> None:
    model = tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model")
    payload = TSV_HEADER + "".join(
        f"5\t1\t1\t1\t1\t{word}\t{left}\t{top}\t{width}\t{height}\t{confidence}\t{token}\n"
        for word, (token, confidence, left, top, width, height) in enumerate(tokens, 1)
    )
    result = TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=lambda argv, **values: subprocess.CompletedProcess(
            argv, 0, payload.encode(), b""
        ),
    ).recognize(_frame(), language="en")

    assert result.text == text
    assert result.clean_short_phrase_evidence is expected
    assert result.geometry_coherent is True
    assert result.line_count == 1


def test_provider_reports_preprocessing_separately_from_total_ocr(
    tmp_path: Path,
) -> None:
    model = tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model")
    times = iter((1.0, 1.0, 1.025, 1.025, 1.035, 1.100))
    tsv = (
        TSV_HEADER
        + "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t92\tHello\n"
    )

    result = TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=lambda argv, **values: subprocess.CompletedProcess(
            argv, 0, tsv.encode(), b""
        ),
        clock=lambda: next(times),
    ).recognize(_frame(), language="en")

    assert result.image_preprocessing_ms == pytest.approx(25.0)
    assert result.png_encoding_ms == pytest.approx(10.0)
    assert result.preprocessing_ms == pytest.approx(35.0)
    assert result.elapsed_ms == pytest.approx(100.0)


def test_opt_in_debug_capture_is_private_detailed_and_strictly_bounded(
    tmp_path: Path,
) -> None:
    model = tmp_path / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    model.parent.mkdir(parents=True)
    model.write_bytes(b"test model")
    processed_payloads: list[bytes] = []
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t4\t60\t18\t93\tBatman\n"
        "5\t1\t1\t1\t1\t2\t74\t4\t8\t18\t20\t7\n"
        "5\t1\t1\t1\t1\t3\t84\t4\t4\t18\t15\t|\n"
    )

    def runner(argv, **values):
        processed_payloads.append(values["input"])
        return subprocess.CompletedProcess(argv, 0, tsv.encode(), b"")

    debug_root = tmp_path / "ocr-debug"
    provider = TesseractOcrProvider(
        tmp_path,
        executable="/usr/bin/tesseract",
        runner=runner,
        debug_capture_enabled=True,
        debug_capture_limit=2,
        debug_capture_root=debug_root,
    )
    results = [provider.recognize(_frame(), language="en") for _ in range(3)]

    assert results[0].debug_capture_path
    assert results[1].debug_capture_path
    assert results[2].debug_capture_path == ""
    sessions = list(debug_root.iterdir())
    assert len(sessions) == 1
    session = sessions[0]
    assert stat.S_IMODE(debug_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.stat().st_mode) == 0o700
    metadata_files = sorted(session.glob("observation-*.json"))
    assert len(metadata_files) == 2
    metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
    assert metadata["roi"] == {
        "file": "observation-01-roi.png",
        "height": 24,
        "pixel_format": "gray8",
        "stride": 80,
        "width": 80,
    }
    assert metadata["processed"]["width"] == 160
    assert metadata["processed"]["height"] == 48
    assert metadata["ocr"]["raw_phrase"] == "Batman 7 |"
    assert metadata["ocr"]["filtered_phrase"] == "Batman 7 |"
    assert metadata["ocr"]["normalized_phrase"] == "Batman 7 |"
    assert metadata["ocr"]["phrase_confidence"] == pytest.approx(
        (0.93 * 6 + 0.20) / 7
    )
    tokens = metadata["ocr"]["tokens"]
    assert tokens[0]["left"] == 10
    assert tokens[1]["isolated_character"] is True
    assert tokens[2]["punctuation_only"] is True
    assert metadata["ocr"]["line_geometry"][0]["token_count"] == 3
    processed = session / metadata["processed"]["file"]
    assert processed.read_bytes() == processed_payloads[0]
    for path in session.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
