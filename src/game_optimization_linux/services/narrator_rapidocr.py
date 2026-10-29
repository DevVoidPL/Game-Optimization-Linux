"""Optional RapidOCR adapter for the existing subtitle pipeline.

Inspired by Shadow775711's RapidOCR experiment; intentionally keeps the complete
user ROI and the common consensus/deduplication, without a local HTTP server.
"""
from __future__ import annotations

import importlib.util
from math import isfinite
from pathlib import Path
from threading import RLock
import time
from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice

from game_optimization_linux.config import NARRATOR_COMPONENTS_DIR
from game_optimization_linux.models.narrator import CaptureFrame, OcrResult
from .narrator_ocr import TesseractOcrProvider, TesseractWorkerClient
from .narrator_rapidocr_worker import MODEL_FILES

RAPIDOCR_PROVIDER_ID = "rapidocr-local"
RAPIDOCR_COMPONENT_ID = "ocr.rapidocr-local"


class RapidOcrProvider:
    provider_id = RAPIDOCR_PROVIDER_ID

    def __init__(self, components_directory: Path = NARRATOR_COMPONENTS_DIR, *,
                 worker_factory: Callable[..., TesseractWorkerClient] = TesseractWorkerClient) -> None:
        self.model_directory = Path(components_directory) / RAPIDOCR_COMPONENT_ID / "rapidocr" / "models"
        self._worker_factory = worker_factory
        self._worker: TesseractWorkerClient | None = None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        return self.language_available("en")

    def language_available(self, language: str) -> bool:
        try:
            return self._language_available(language)
        except OSError:
            # A concurrent component removal must report unavailable, not
            # break the component-status refresh in the UI.
            return False

    def _language_available(self, language: str) -> bool:
        return (language in {"en", "eng", "pl", "pol"}
                and all(importlib.util.find_spec(name) is not None
                        for name in ("rapidocr", "onnxruntime"))
                and all((self.model_directory / name).is_file()
                        and not (self.model_directory / name).is_symlink()
                        and (self.model_directory / name).stat().st_size == size
                        for name, (size, _) in MODEL_FILES.items()))

    @property
    def status_message(self) -> str:
        return ("RapidOCR English and Polish subtitle OCR is ready" if self.available
                else "Install the verified RapidOCR models and runtime")

    def _client(self, language: str) -> TesseractWorkerClient:
        if not self.language_available(language):
            raise RuntimeError(self.status_message)
        with self._lock:
            if self._worker is None:
                self._worker = self._worker_factory(
                    self.model_directory, "pl" if language in {"pl", "pol"} else "en",
                    worker_script=Path(__file__).with_name("narrator_rapidocr_worker.py"),
                    timeout_seconds=30.0,
                    worker_name="RapidOCR",
                    restart_after_cancel=False,
                )
            return self._worker

    def prepare(self, language: str) -> None:
        worker = self._client(language)
        try:
            worker.start()
        except Exception:
            self._discard_worker(worker)
            raise

    def recognize(self, frame: CaptureFrame, *, language: str = "en") -> OcrResult:
        worker = self._client(language)
        started = time.monotonic()
        image = TesseractOcrProvider._frame_image(frame)
        # The recognizer is trained on natural colour images. Tesseract's outline
        # masks remain Tesseract-only; no second crop or hardcoded subtitle box.
        prepared = time.monotonic()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("The subtitle image could not be encoded")
        buffer.close()
        encoded = time.monotonic()
        try:
            response = worker.recognize_response(bytes(data))
            lines = self._lines(response)
        except Exception:
            self._discard_worker(worker)
            raise  # Never silently substitute Tesseract.
        text = "\n".join(line[0] for line in lines)
        confidence = min((line[1] for line in lines), default=None)
        strongest = max(lines, key=lambda line: line[1], default=("", None))
        return OcrResult(
            text=text, raw_text=text, filtered_text=text,
            confidence=confidence, raw_confidence=confidence,
            provider_id=self.provider_id, backend="rapidocr-onnx-cpu",
            elapsed_ms=(time.monotonic() - started) * 1000,
            preprocessing_ms=(encoded - started) * 1000,
            image_preprocessing_ms=(prepared - started) * 1000,
            png_encoding_ms=(encoded - prepared) * 1000,
            recognition_ms=worker.last_recognition_ms,
            worker_execution_ms=worker.last_worker_execution_ms,
            worker_decode_ms=worker.last_decode_ms,
            worker_roundtrip_ms=worker.last_roundtrip_ms,
            worker_lock_wait_ms=worker.last_lock_wait_ms,
            worker_startup_ms=worker.startup_ms,
            line_count=len(lines), token_count=len(text.split()),
            included_token_count=len(text.split()),
            strongest_line_text=strongest[0], strongest_line_confidence=strongest[1],
            selected_line_confidence=confidence,
            # Scores describe complete lines, not per-word confidence. Leave
            # Tesseract-specific geometry/quality evidence unknown.
            filter_summary="rapidocr_line_scores", preprocess="rapidocr_color",
            input_width=frame.width, input_height=frame.height,
            preprocessed_width=image.width(), preprocessed_height=image.height(),
        )

    @staticmethod
    def _lines(response: dict[str, object]) -> list[tuple[str, float]]:
        raw_lines = response.get("lines")
        if not isinstance(raw_lines, list):
            raise RuntimeError("RapidOCR returned invalid line results")
        fragments = []
        for line in raw_lines:
            if not isinstance(line, dict):
                raise RuntimeError("RapidOCR returned an invalid line")
            text = line.get("text")
            score = line.get("score")
            box = line.get("box")
            if (not isinstance(text, str) or isinstance(score, bool)
                    or not isinstance(score, (float, int)) or not isfinite(score)
                    or not 0 <= score <= 1 or not isinstance(box, list)
                    or len(box) != 4 or any(not isinstance(point, list) or len(point) != 2
                        or any(isinstance(v, bool) or not isinstance(v, (float, int))
                               or not isfinite(v) for v in point) for point in box)):
                raise RuntimeError("RapidOCR returned invalid line geometry or confidence")
            if text.strip():
                xs, ys = zip(*box)
                if max(xs) <= min(xs) or max(ys) <= min(ys):
                    raise RuntimeError("RapidOCR returned an empty line box")
                fragments.append((min(xs), min(ys), max(ys), text.strip(), float(score)))
        # The detector may split a visual subtitle line into several boxes.
        # Rejoin only boxes with substantial vertical overlap, then read left
        # to right. No word boxes/confidences are invented.
        rows: list[list[tuple[float, float, float, str, float]]] = []
        for fragment in sorted(fragments, key=lambda item: (item[1], item[0])):
            _, top, bottom, _, _ = fragment
            row = next((row for row in rows
                        if min(bottom, min(part[2] for part in row))
                        - max(top, max(part[1] for part in row))
                        > 0.5 * min(bottom - top, min(part[2] - part[1] for part in row))), None)
            if row is None:
                rows.append([fragment])
            else:
                row.append(fragment)
        return [(" ".join(part[3] for part in sorted(row)), min(part[4] for part in row))
                for row in rows]

    def _discard_worker(self, worker: TesseractWorkerClient) -> None:
        with self._lock:
            if self._worker is worker:
                self._worker = None
        worker.cancel()

    def cancel(self) -> None:
        with self._lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            worker.cancel()

    def close(self) -> None:
        with self._lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            worker.close()
