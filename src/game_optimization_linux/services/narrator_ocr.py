"""Local English OCR provider backed by Tesseract."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
import shutil
import subprocess
from threading import Lock
import time
from typing import Callable
from math import sqrt

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from game_optimization_linux.config import NARRATOR_COMPONENTS_DIR
from game_optimization_linux.models.narrator import CaptureFrame, OcrResult


TESSERACT_COMPONENT_ID = "ocr.english-local"
TESSERACT_MODEL_RELATIVE_PATH = Path("tessdata") / "eng.traineddata"
OCR_UPSCALE_FACTOR = 2.0
OCR_MAX_PREPROCESSED_PIXELS = 4_000_000
OCR_CONTRAST_TILE_WIDTH = 96
OCR_CONTRAST_TILE_HEIGHT = 64


class TesseractOcrProvider:
    provider_id = "tesseract-fast-eng"

    def __init__(
        self,
        component_root: Path = NARRATOR_COMPONENTS_DIR,
        *,
        executable: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._component_root = Path(component_root)
        self._explicit_executable = str(executable) if executable is not None else ""
        self._runner = runner
        self._clock = clock
        self._processes: set[subprocess.Popen[bytes]] = set()
        self._lock = Lock()

    @property
    def executable(self) -> str:
        return self._explicit_executable or shutil.which("tesseract") or ""

    @property
    def model_path(self) -> Path:
        return (
            self._component_root
            / TESSERACT_COMPONENT_ID
            / TESSERACT_MODEL_RELATIVE_PATH
        )

    @property
    def available(self) -> bool:
        return bool(self.executable and self.model_path.is_file())

    @property
    def status_message(self) -> str:
        if not self.executable:
            return "The Tesseract OCR runtime is unavailable"
        if not self.model_path.is_file():
            return "Install the verified English OCR model"
        return "Tesseract English subtitle OCR is ready"

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
        if language.casefold() != "en":
            raise ValueError("The current OCR provider supports English only")
        executable = self.executable
        model = self.model_path
        if not executable:
            raise RuntimeError("The Tesseract OCR runtime is unavailable")
        if not model.is_file():
            raise RuntimeError("The verified English OCR model is not installed")
        started = self._clock()
        image = self._preprocess_frame(frame)
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(
            buffer, "PNG"
        ):
            raise RuntimeError("The subtitle image could not be prepared for OCR")
        buffer.close()
        argv = [
            executable,
            "stdin",
            "stdout",
            "--tessdata-dir",
            str(model.parent),
            "-l",
            "eng",
            "--oem",
            "1",
            "--psm",
            "6",
            "-c",
            "tessedit_create_tsv=1",
        ]
        completed = self._run(argv, bytes(encoded))
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"Tesseract exited with code {completed.returncode}")
        text, confidence = self._parse_tsv(
            completed.stdout.decode("utf-8", errors="replace")
        )
        return OcrResult(
            text=text,
            confidence=confidence,
            provider_id=self.provider_id,
            elapsed_ms=max(0.0, (self._clock() - started) * 1000.0),
        )

    def cancel(self) -> None:
        with self._lock:
            processes = tuple(self._processes)
        for process in processes:
            if process.poll() is None:
                process.terminate()

    def _run(self, argv: list[str], payload: bytes) -> subprocess.CompletedProcess[bytes]:
        if self._runner is not subprocess.run:
            return self._runner(
                argv,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15.0,
                check=False,
            )
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        with self._lock:
            self._processes.add(process)
        try:
            stdout, stderr = process.communicate(input=payload, timeout=15.0)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise RuntimeError("Tesseract OCR timed out") from error
        finally:
            with self._lock:
                self._processes.discard(process)
        return subprocess.CompletedProcess(
            argv,
            process.returncode,
            stdout,
            stderr,
        )

    @staticmethod
    def _frame_image(frame: CaptureFrame) -> QImage:
        formats = {
            "rgba8888": QImage.Format.Format_RGBA8888,
            "bgra8888": QImage.Format.Format_ARGB32,
            "rgb888": QImage.Format.Format_RGB888,
            "bgr888": QImage.Format.Format_BGR888,
            "gray8": QImage.Format.Format_Grayscale8,
        }
        image_format = formats.get(frame.pixel_format.casefold())
        if image_format is None:
            raise ValueError(f"unsupported OCR pixel format: {frame.pixel_format}")
        image = QImage(
            frame.pixels,
            frame.width,
            frame.height,
            frame.stride,
            image_format,
        ).copy()
        if image.isNull():
            raise RuntimeError("The subtitle image is invalid")
        return image

    @classmethod
    def _preprocess_frame(cls, frame: CaptureFrame) -> QImage:
        """Prepare an already-cropped subtitle ROI without binarizing its edges."""
        grayscale = cls._frame_image(frame).convertToFormat(
            QImage.Format.Format_Grayscale8
        )
        contrasted = cls._improve_local_contrast(grayscale)
        source_pixels = max(1, contrasted.width() * contrasted.height())
        bounded_factor = min(
            OCR_UPSCALE_FACTOR,
            sqrt(OCR_MAX_PREPROCESSED_PIXELS / source_pixels),
        )
        if bounded_factor <= 1.05:
            return contrasted
        return contrasted.scaled(
            max(1, round(contrasted.width() * bounded_factor)),
            max(1, round(contrasted.height() * bounded_factor)),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _improve_local_contrast(image: QImage) -> QImage:
        """Apply gentle tile-local percentile stretching to an 8-bit image.

        Only 35% of the stretched value is blended in. This helps pale text on
        uneven backgrounds while retaining anti-aliased glyph edge pixels.
        """
        width = image.width()
        height = image.height()
        if width <= 0 or height <= 0:
            return image
        source = image.constBits()
        stride = image.bytesPerLine()
        output = bytearray(width * height)
        for top in range(0, height, OCR_CONTRAST_TILE_HEIGHT):
            tile_height = min(OCR_CONTRAST_TILE_HEIGHT, height - top)
            for left in range(0, width, OCR_CONTRAST_TILE_WIDTH):
                tile_width = min(OCR_CONTRAST_TILE_WIDTH, width - left)
                histogram = [0] * 256
                for row in range(top, top + tile_height):
                    start = row * stride + left
                    for value in source[start : start + tile_width]:
                        histogram[value] += 1
                count = tile_width * tile_height
                tail = max(1, round(count * 0.02))
                low = TesseractOcrProvider._histogram_percentile(histogram, tail)
                high = TesseractOcrProvider._histogram_percentile(
                    histogram, count - tail
                )
                if high - low < 32:
                    table = bytes(range(256))
                else:
                    span = high - low
                    table = bytes(
                        max(
                            0,
                            min(
                                255,
                                round(
                                    value * 0.65
                                    + max(0, min(255, (value - low) * 255 / span))
                                    * 0.35
                                ),
                            ),
                        )
                        for value in range(256)
                    )
                for row in range(top, top + tile_height):
                    source_start = row * stride + left
                    output_start = row * width + left
                    values = bytes(
                        source[source_start : source_start + tile_width]
                    ).translate(table)
                    output[output_start : output_start + tile_width] = values
        return QImage(
            bytes(output),
            width,
            height,
            width,
            QImage.Format.Format_Grayscale8,
        ).copy()

    @staticmethod
    def _histogram_percentile(histogram: list[int], target: int) -> int:
        seen = 0
        for value, count in enumerate(histogram):
            seen += count
            if seen >= target:
                return value
        return 255

    @staticmethod
    def _parse_tsv(payload: str) -> tuple[str, float | None]:
        lines: dict[tuple[str, str, str, str], list[str]] = {}
        weighted_confidence = 0.0
        confidence_weight = 0
        for row in csv.DictReader(StringIO(payload), delimiter="\t"):
            if str(row.get("level", "5")) != "5":
                continue
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            key = tuple(str(row.get(name, "")) for name in (
                "page_num",
                "block_num",
                "par_num",
                "line_num",
            ))
            lines.setdefault(key, []).append(text)
            try:
                confidence = float(row.get("conf", "-1"))
            except (TypeError, ValueError):
                continue
            if confidence < 0:
                continue
            credible_characters = sum(character.isalnum() for character in text)
            if credible_characters:
                weighted_confidence += (confidence / 100.0) * credible_characters
                confidence_weight += credible_characters
        text = "\n".join(" ".join(words) for words in lines.values())
        average = (
            weighted_confidence / confidence_weight if confidence_weight else None
        )
        return text, average


__all__ = [
    "TESSERACT_COMPONENT_ID",
    "TESSERACT_MODEL_RELATIVE_PATH",
    "OCR_UPSCALE_FACTOR",
    "TesseractOcrProvider",
]
