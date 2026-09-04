"""Local subtitle OCR provider backed by separately managed Tesseract models."""

from __future__ import annotations

import base64
from collections import deque
import json
import logging
from math import isfinite, sqrt
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
from threading import Lock, RLock, Thread
import time
from typing import Callable, Protocol, TextIO
import unicodedata
from uuid import uuid4

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from game_optimization_linux.config import NARRATOR_COMPONENTS_DIR, STATE_DIR
from game_optimization_linux.models.narrator import CaptureFrame, OcrResult


TESSERACT_COMPONENT_ID = "ocr.english-local"
TESSERACT_MODEL_RELATIVE_PATH = Path("tessdata") / "eng.traineddata"
TESSERACT_POLISH_COMPONENT_ID = "ocr.polish-local"
TESSERACT_POLISH_MODEL_RELATIVE_PATH = Path("tessdata") / "pol.traineddata"
OCR_UPSCALE_FACTOR = 2.0
OCR_MAX_PREPROCESSED_PIXELS = 4_000_000
OCR_CONTRAST_TILE_WIDTH = 96
OCR_CONTRAST_TILE_HEIGHT = 64
OCR_WORKER_TIMEOUT_SECONDS = 15.0
OCR_DEBUG_CAPTURE_ENV = "GAME_OPTIMIZATION_NARRATOR_OCR_DEBUG"
OCR_DEBUG_CAPTURE_LIMIT_ENV = "GAME_OPTIMIZATION_NARRATOR_OCR_DEBUG_LIMIT"
OCR_DEBUG_CAPTURE_DEFAULT_LIMIT = 6
OCR_DEBUG_CAPTURE_MAX_LIMIT = 20
OCR_DEBUG_CAPTURE_DIR = STATE_DIR / "narrator" / "ocr-debug"
_TESSERACT_TSV_COLUMNS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)
_TESSERACT_TSV_INTEGER_COLUMNS = _TESSERACT_TSV_COLUMNS[:10]
_WORKER_BOOTSTRAP = (
    "import runpy, sys; "
    "path = sys.argv.pop(1); "
    "sys.argv[0] = path; "
    "runpy.run_path(path, run_name='__main__')"
)


logger = logging.getLogger(__name__)


class OcrWorker(Protocol):
    def start(self) -> None: ...

    def recognize_png(self, payload: bytes, *, source_resolution: int) -> str: ...

    def cancel(self) -> None: ...

    def close(self) -> None: ...


class TesseractWorkerClient:
    """Persistent client for the isolated Tesseract C API worker."""

    def __init__(
        self,
        model_path: Path,
        language: str,
        *,
        executable: str | Path = sys.executable,
        timeout_seconds: float = OCR_WORKER_TIMEOUT_SECONDS,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        worker_script: Path | None = None,
    ) -> None:
        self._model_path = Path(model_path).resolve()
        self._language = str(language)
        self._executable = str(executable)
        self._worker_script = (
            Path(worker_script)
            if worker_script is not None
            else Path(__file__).with_name("narrator_ocr_worker.py")
        ).resolve()
        self._timeout_seconds = float(timeout_seconds)
        if self._timeout_seconds <= 0:
            raise ValueError("OCR worker timeout must be positive")
        self._popen = popen
        self._state_lock = RLock()
        self._io_lock = Lock()
        self._process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self.initialization_ms: float | None = None
        self.startup_ms: float | None = None
        self.last_worker_execution_ms: float | None = None
        self.last_decode_ms: float | None = None
        self.last_recognition_ms: float | None = None
        self.last_roundtrip_ms: float | None = None
        self.last_lock_wait_ms: float | None = None
        self.tesseract_library = ""
        self.leptonica_library = ""

    def start(self) -> None:
        """Initialize the selected model without submitting an OCR image."""
        with self._io_lock:
            self._ensure_process()

    def recognize_png(self, payload: bytes, *, source_resolution: int) -> str:
        lock_started = time.monotonic()
        with self._io_lock:
            self.last_lock_wait_ms = max(
                0.0, (time.monotonic() - lock_started) * 1000.0
            )
            process = self._ensure_process()
            self._request_id += 1
            request_id = self._request_id
            request = {
                "command": "recognize",
                "request_id": request_id,
                "png_base64": base64.b64encode(payload).decode("ascii"),
                "source_resolution": int(source_resolution),
            }
            roundtrip_started = time.monotonic()
            try:
                assert process.stdin is not None
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                response = self._read_response(process)
            except Exception:
                self._discard(process)
                raise
            if response.get("request_id") != request_id:
                self._discard(process)
                raise RuntimeError("The OCR worker returned an unexpected response")
            if not response.get("ok"):
                message = str(response.get("error", "")).strip()
                raise RuntimeError(message or "Persistent Tesseract OCR failed")
            self.last_roundtrip_ms = max(
                0.0, (time.monotonic() - roundtrip_started) * 1000.0
            )
            self.last_worker_execution_ms = self._response_float(
                response, "elapsed_ms"
            )
            self.last_decode_ms = self._response_float(response, "decode_ms")
            self.last_recognition_ms = self._response_float(
                response, "recognition_ms"
            )
            tsv = response.get("tsv")
            if not isinstance(tsv, str) or not tsv.startswith("level\t"):
                self._discard(process)
                raise RuntimeError("The OCR worker returned invalid TSV data")
            return tsv

    def cancel(self) -> None:
        with self._state_lock:
            process = self._process
            self._process = None
        if process is not None:
            self._terminate(process)

    def close(self) -> None:
        with self._state_lock:
            process = self._process
            self._process = None
        if process is None:
            return
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=2.0)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self._terminate(process)

    def _ensure_process(self) -> subprocess.Popen[str]:
        with self._state_lock:
            process = self._process
            if process is not None and process.poll() is None:
                return process
            process = self._popen(
                [
                    self._executable,
                    "-c",
                    _WORKER_BOOTSTRAP,
                    str(self._worker_script),
                    "--model",
                    str(self._model_path),
                    "--language",
                    self._language,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                close_fds=True,
                cwd=str(self._model_path.parent),
            )
            self._process = process
            self._stderr_tail.clear()
            assert process.stderr is not None
            Thread(
                target=self._drain_stderr,
                args=(process, process.stderr),
                name="narrator-ocr-stderr",
                daemon=True,
            ).start()
        startup_started = time.monotonic()
        response = self._read_response(process)
        self.startup_ms = max(
            0.0, (time.monotonic() - startup_started) * 1000.0
        )
        if response.get("status") != "ready":
            message = str(
                response.get("message", "The OCR model could not be loaded")
            ).strip()
            self._discard(process)
            raise RuntimeError(message or "The OCR model could not be loaded")
        try:
            self.initialization_ms = float(response.get("initialization_ms", 0.0))
        except (TypeError, ValueError):
            self.initialization_ms = None
        self.tesseract_library = str(response.get("tesseract_library", ""))
        self.leptonica_library = str(response.get("leptonica_library", ""))
        logger.debug(
            "Persistent Tesseract worker ready language=%s startup=%.1fms "
            "initialization=%s tesseract=%s leptonica=%s",
            self._language,
            self.startup_ms or 0.0,
            (
                f"{self.initialization_ms:.1f}ms"
                if self.initialization_ms is not None
                else "unknown"
            ),
            self.tesseract_library or "unknown",
            self.leptonica_library or "unknown",
        )
        return process

    @staticmethod
    def _response_float(
        response: dict[str, object], name: str
    ) -> float | None:
        try:
            return max(0.0, float(response[name]))
        except (KeyError, TypeError, ValueError):
            return None

    def _read_response(self, process: subprocess.Popen[str]) -> dict[str, object]:
        stdout = process.stdout
        if stdout is None:
            raise RuntimeError("The OCR worker has no output channel")
        selector = selectors.DefaultSelector()
        try:
            selector.register(stdout, selectors.EVENT_READ)
            if not selector.select(self._timeout_seconds):
                raise RuntimeError("Persistent Tesseract OCR timed out")
            line = stdout.readline()
        finally:
            selector.close()
        if not line:
            detail = "\n".join(self._stderr_tail).strip()
            raise RuntimeError(detail or "The OCR worker stopped unexpectedly")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError("The OCR worker returned invalid JSON") from error
        if not isinstance(response, dict):
            raise RuntimeError("The OCR worker returned an invalid response")
        return response

    def _discard(self, process: subprocess.Popen[str]) -> None:
        with self._state_lock:
            if self._process is process:
                self._process = None
        self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    def _drain_stderr(self, process: subprocess.Popen[str], stream: TextIO) -> None:
        try:
            for line in stream:
                if process is not self._process:
                    break
                value = str(line).strip()
                if value:
                    self._stderr_tail.append(value)
        except (OSError, ValueError):
            return


class TesseractOcrProvider:
    provider_id = "tesseract-fast-eng"

    def __init__(
        self,
        component_root: Path = NARRATOR_COMPONENTS_DIR,
        *,
        executable: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        worker_factory: Callable[[Path, str], OcrWorker] = TesseractWorkerClient,
        persistent_worker_enabled: bool | None = None,
        debug_capture_enabled: bool | None = None,
        debug_capture_limit: int | None = None,
        debug_capture_root: Path = OCR_DEBUG_CAPTURE_DIR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._component_root = Path(component_root)
        self._explicit_executable = str(executable) if executable is not None else ""
        self._runner = runner
        self._worker_factory = worker_factory
        self._persistent_worker_enabled = (
            runner is subprocess.run
            if persistent_worker_enabled is None
            else bool(persistent_worker_enabled)
        )
        self._clock = clock
        self._debug_capture_enabled = (
            self._environment_flag(OCR_DEBUG_CAPTURE_ENV)
            if debug_capture_enabled is None
            else bool(debug_capture_enabled)
        )
        configured_limit = (
            self._environment_int(
                OCR_DEBUG_CAPTURE_LIMIT_ENV,
                OCR_DEBUG_CAPTURE_DEFAULT_LIMIT,
            )
            if debug_capture_limit is None
            else int(debug_capture_limit)
        )
        self._debug_capture_limit = max(
            1, min(OCR_DEBUG_CAPTURE_MAX_LIMIT, configured_limit)
        )
        self._debug_capture_root = Path(debug_capture_root)
        self._debug_capture_session: Path | None = None
        self._debug_capture_count = 0
        self._processes: set[subprocess.Popen[bytes]] = set()
        self._lock = Lock()
        self._worker_lock = RLock()
        self._worker: OcrWorker | None = None
        self._worker_language = ""
        self._cancel_epoch = 0
        self._worker_unavailable_until = 0.0
        self._worker_restart_count = 0
        self._cli_fallback_count = 0
        self._last_fallback_reason = ""

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
    def polish_model_path(self) -> Path:
        return (
            self._component_root
            / TESSERACT_POLISH_COMPONENT_ID
            / TESSERACT_POLISH_MODEL_RELATIVE_PATH
        )

    @property
    def available(self) -> bool:
        return bool(self.executable and self.available_languages)

    @property
    def available_languages(self) -> tuple[str, ...]:
        return tuple(
            language
            for language in ("en", "pl")
            if self._model_path(language).is_file()
        )

    def language_available(self, language: str) -> bool:
        try:
            model = self._model_path(language)
        except ValueError:
            return False
        return bool(self.executable and model.is_file())

    @property
    def status_message(self) -> str:
        if not self.executable:
            return "The Tesseract OCR runtime is unavailable"
        if not self.available_languages:
            return "Install the verified English OCR model"
        return "Tesseract subtitle OCR is ready"

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
        normalized_language = self._normalized_language(language)
        executable = self.executable
        model = self._model_path(normalized_language)
        if not executable:
            raise RuntimeError("The Tesseract OCR runtime is unavailable")
        if not model.is_file():
            language_name = "Polish" if normalized_language == "pl" else "English"
            raise RuntimeError(
                f"The verified {language_name} OCR model is not installed"
            )
        started = self._clock()
        image_started = self._clock()
        roi_image = self._frame_image(frame)
        image = self._preprocess_frame(frame)
        image_preprocessing_ms = max(
            0.0, (self._clock() - image_started) * 1000.0
        )
        encoding_started = self._clock()
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(
            buffer, "PNG"
        ):
            raise RuntimeError("The subtitle image could not be prepared for OCR")
        buffer.close()
        png_encoding_ms = max(
            0.0, (self._clock() - encoding_started) * 1000.0
        )
        preprocessing_ms = image_preprocessing_ms + png_encoding_ms
        encoded_png = bytes(encoded)
        tesseract_language = "pol" if normalized_language == "pl" else "eng"
        source_resolution = self._source_resolution(image)
        persistent = self._persistent_tsv(
            encoded_png,
            model=model,
            language=tesseract_language,
            source_resolution=source_resolution,
        )
        if persistent is None:
            tsv = self._cli_tsv(
                encoded_png,
                executable=executable,
                model=model,
                language=tesseract_language,
            )
            backend = "cli_fallback"
            recognition_ms = None
            worker_execution_ms = None
            worker_decode_ms = None
            worker_roundtrip_ms = None
            worker_lock_wait_ms = None
            worker_startup_ms = None
            worker_restarted = False
            fallback_reason = self._last_fallback_reason
        else:
            tsv, worker_restarted = persistent
            worker = self._worker
            backend = "persistent"
            recognition_ms = self._worker_metric(worker, "last_recognition_ms")
            worker_execution_ms = self._worker_metric(
                worker, "last_worker_execution_ms"
            )
            worker_decode_ms = self._worker_metric(worker, "last_decode_ms")
            worker_roundtrip_ms = self._worker_metric(
                worker, "last_roundtrip_ms"
            )
            worker_lock_wait_ms = self._worker_metric(
                worker, "last_lock_wait_ms"
            )
            worker_startup_ms = self._worker_metric(worker, "startup_ms")
            fallback_reason = ""
        (
            raw_text,
            filtered_text,
            confidence,
            raw_confidence,
            tokens,
        ) = self._analyze_tsv(tsv)
        quality = self._quality_evidence(
            filtered_text,
            confidence,
            tokens,
            image_width=image.width(),
            image_height=image.height(),
        )
        elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)
        client_overhead_ms = (
            max(0.0, elapsed_ms - preprocessing_ms - worker_execution_ms)
            if worker_execution_ms is not None
            else None
        )
        debug_capture_path = self._write_debug_capture(
            frame=frame,
            roi_image=roi_image,
            processed_png=encoded_png,
            processed_image=image,
            tsv=tsv,
            tokens=tokens,
            text=raw_text,
            filtered_text=filtered_text,
            confidence=confidence,
            raw_confidence=raw_confidence,
            language=tesseract_language,
            backend=backend,
            fallback_reason=fallback_reason,
            source_resolution=source_resolution,
            image_preprocessing_ms=image_preprocessing_ms,
            png_encoding_ms=png_encoding_ms,
            worker_decode_ms=worker_decode_ms,
            recognition_ms=recognition_ms,
            worker_execution_ms=worker_execution_ms,
            worker_roundtrip_ms=worker_roundtrip_ms,
            worker_lock_wait_ms=worker_lock_wait_ms,
            client_overhead_ms=client_overhead_ms,
            elapsed_ms=elapsed_ms,
        )
        return OcrResult(
            text=filtered_text,
            confidence=confidence,
            raw_text=raw_text,
            filtered_text=filtered_text,
            raw_confidence=raw_confidence,
            provider_id=self.provider_id,
            elapsed_ms=elapsed_ms,
            preprocessing_ms=preprocessing_ms,
            image_preprocessing_ms=image_preprocessing_ms,
            png_encoding_ms=png_encoding_ms,
            backend=backend,
            recognition_ms=recognition_ms,
            worker_execution_ms=worker_execution_ms,
            worker_decode_ms=worker_decode_ms,
            worker_roundtrip_ms=worker_roundtrip_ms,
            worker_lock_wait_ms=worker_lock_wait_ms,
            client_overhead_ms=client_overhead_ms,
            worker_startup_ms=worker_startup_ms,
            worker_restarted=worker_restarted,
            fallback_reason=fallback_reason,
            debug_capture_path=debug_capture_path,
            token_count=quality["token_count"],
            included_token_count=quality["included_token_count"],
            line_count=quality["line_count"],
            dropped_token_count=quality["dropped_token_count"],
            minimum_token_confidence=quality["minimum_token_confidence"],
            geometry_coherent=quality["geometry_coherent"],
            clean_short_phrase_evidence=quality["clean_short_phrase_evidence"],
            filter_summary=quality["filter_summary"],
        )

    def prepare(self, language: str) -> None:
        """Warm only the selected language model in the isolated worker."""
        normalized_language = self._normalized_language(language)
        if not self._persistent_worker_enabled:
            return
        model = self._model_path(normalized_language)
        if not self.executable or not model.is_file():
            return
        tesseract_language = "pol" if normalized_language == "pl" else "eng"
        with self._worker_lock:
            epoch = self._cancel_epoch
        worker = self._worker_for(model, tesseract_language)
        start = getattr(worker, "start", None)
        if callable(start):
            try:
                start()
            except Exception:
                self._discard_worker(worker)
                raise
        with self._worker_lock:
            cancelled = epoch != self._cancel_epoch
        if cancelled:
            self._discard_worker(worker)
            raise RuntimeError("Tesseract OCR preparation was cancelled")

    def _cli_tsv(
        self,
        payload: bytes,
        *,
        executable: str,
        model: Path,
        language: str,
    ) -> str:
        argv = [
            executable,
            "stdin",
            "stdout",
            "--tessdata-dir",
            str(model.parent),
            "-l",
            language,
            "--oem",
            "1",
            "--psm",
            "6",
            "-c",
            "tessedit_create_tsv=1",
        ]
        completed = self._run(argv, payload)
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                detail or f"Tesseract exited with code {completed.returncode}"
            )
        return completed.stdout.decode("utf-8", errors="replace")

    def _persistent_tsv(
        self,
        payload: bytes,
        *,
        model: Path,
        language: str,
        source_resolution: int,
    ) -> tuple[str, bool] | None:
        if (
            not self._persistent_worker_enabled
            or self._clock() < self._worker_unavailable_until
        ):
            return None
        with self._worker_lock:
            epoch = self._cancel_epoch
        errors: list[str] = []
        for attempt in range(2):
            worker: OcrWorker | None = None
            try:
                worker = self._worker_for(model, language)
                result = worker.recognize_png(
                    payload, source_resolution=source_resolution
                )
                with self._worker_lock:
                    if epoch != self._cancel_epoch:
                        raise RuntimeError("Tesseract OCR was cancelled")
                if attempt:
                    self._worker_restart_count += 1
                    logger.debug(
                        "Persistent Tesseract OCR recovered after worker restart"
                    )
                self._last_fallback_reason = ""
                return result, bool(attempt)
            except Exception as error:
                errors.append(str(error) or error.__class__.__name__)
                self._discard_worker(worker)
                with self._worker_lock:
                    if epoch != self._cancel_epoch:
                        raise RuntimeError("Tesseract OCR was cancelled") from error
        reason = " | ".join(errors)
        with self._worker_lock:
            self._worker_unavailable_until = self._clock() + 30.0
            self._cli_fallback_count += 1
            self._last_fallback_reason = reason
        logger.warning(
            "Persistent Tesseract OCR failed twice; using CLI fallback: %s",
            reason,
        )
        return None

    @staticmethod
    def _worker_metric(worker: object, name: str) -> float | None:
        try:
            value = getattr(worker, name)
            return max(0.0, float(value))
        except (AttributeError, TypeError, ValueError):
            return None

    @property
    def worker_restart_count(self) -> int:
        return self._worker_restart_count

    @property
    def cli_fallback_count(self) -> int:
        return self._cli_fallback_count

    def _worker_for(self, model: Path, language: str) -> OcrWorker:
        with self._worker_lock:
            worker = self._worker
            if worker is not None and self._worker_language == language:
                return worker
            self._worker = None
            self._worker_language = ""
            if worker is not None:
                worker.close()
            worker = self._worker_factory(model, language)
            self._worker = worker
            self._worker_language = language
            return worker

    def _discard_worker(self, worker: OcrWorker | None) -> None:
        if worker is None:
            return
        with self._worker_lock:
            if self._worker is worker:
                self._worker = None
                self._worker_language = ""
        close = getattr(worker, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _source_resolution(image: QImage) -> int:
        """Report the image DPI that Tesseract should assume.

        In practice this is always 96 today: capture frames are raw pixel
        buffers, CaptureFrame carries no DPI field, and nothing calls
        setDotsPerMeter, so every QImage keeps Qt's default dotsPerMeterX of
        3780 -- exactly 96 DPI. The older CLI path reported the same value,
        because Qt's PNG encoder writes that number into the pHYs chunk and
        Tesseract read the DPI from the file.

        Open follow-up (deliberately not changed here): _preprocess_frame
        upscales by roughly OCR_UPSCALE_FACTOR, and QImage.scaled() copies DPI
        metadata unchanged, so the enlarged image still declares 96 DPI while
        being closer to ~192 DPI relative to the original content. This is a
        tuning question, not a defect: real sessions have reached 0.963 phrase
        confidence with near-perfect text at 96 DPI, so the current value
        demonstrably does not prevent good recognition. Deciding it needs a
        measurement rather than a guess -- declared DPI (96 vs 192 vs ~300)
        plotted against phrase confidence and character accuracy over one fixed
        corpus of saved ROI captures, with every other OCR setting held constant.
        """

        dots_per_meter = image.dotsPerMeterX()
        if dots_per_meter <= 0:
            return 96
        return max(70, min(600, round(dots_per_meter * 0.0254)))

    @staticmethod
    def _normalized_language(language: str) -> str:
        normalized = str(language).strip().casefold().replace("_", "-")
        if normalized in {"en", "en-us", "en-gb"}:
            return "en"
        if normalized in {"pl", "pl-pl"}:
            return "pl"
        raise ValueError(f"The current OCR provider does not support {language}")

    def _model_path(self, language: str) -> Path:
        normalized = self._normalized_language(language)
        return self.polish_model_path if normalized == "pl" else self.model_path

    def cancel(self) -> None:
        with self._worker_lock:
            self._cancel_epoch += 1
            worker = self._worker
            self._worker = None
            self._worker_language = ""
            self._worker_unavailable_until = 0.0
        if worker is not None:
            worker.cancel()
        with self._lock:
            processes = tuple(self._processes)
        for process in processes:
            if process.poll() is None:
                process.terminate()

    def close(self) -> None:
        with self._worker_lock:
            self._cancel_epoch += 1
            worker = self._worker
            self._worker = None
            self._worker_language = ""
        if worker is not None:
            worker.close()
        self.cancel()

    def _run(
        self, argv: list[str], payload: bytes
    ) -> subprocess.CompletedProcess[bytes]:
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
        text, confidence, _tokens = TesseractOcrProvider._parse_tsv_detailed(
            payload
        )
        return text, confidence

    @staticmethod
    def _parse_tsv_detailed(
        payload: str,
    ) -> tuple[str, float | None, list[dict[str, object]]]:
        lines: dict[tuple[str, str, str, str], list[str]] = {}
        weighted_confidence = 0.0
        confidence_weight = 0
        tokens: list[dict[str, object]] = []
        for row in TesseractOcrProvider._validated_tsv_rows(payload):
            if row["level"] != "5":
                continue
            text = row["text"].strip()
            key = tuple(row[name] for name in (
                "page_num",
                "block_num",
                "par_num",
                "line_num",
            ))
            confidence = float(row["conf"])
            token = {
                "text": text,
                "confidence": confidence,
                "left": int(row["left"]),
                "top": int(row["top"]),
                "width": int(row["width"]),
                "height": int(row["height"]),
                "page": int(row["page_num"]),
                "block": int(row["block_num"]),
                "paragraph": int(row["par_num"]),
                "line": int(row["line_num"]),
                "word": int(row["word_num"]),
                "alphanumeric_characters": sum(
                    character.isalnum() for character in text
                ),
                "alphabetic_characters": sum(
                    character.isalpha() for character in text
                ),
                "isolated_character": len(text) == 1,
                "punctuation_only": bool(text)
                and not any(character.isalnum() for character in text),
                "valid_confidence": confidence >= 0,
            }
            tokens.append(token)
            if not text:
                continue
            lines.setdefault(key, []).append(text)
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
        return text, average, tokens

    @staticmethod
    def _analyze_tsv(
        payload: str,
    ) -> tuple[
        str,
        str,
        float | None,
        float | None,
        list[dict[str, object]],
    ]:
        """Keep literal OCR text while conservatively excluding proven noise.

        Tesseract confidence is attached to individual word rows.  Previously,
        invalid/weak rows were omitted from the phrase confidence calculation
        but still copied into the phrase itself.  A strong subtitle line could
        therefore make a phrase pass while unrelated zero-confidence lines or
        suffix clusters were narrated with it.
        """

        raw_text, raw_confidence, parsed_tokens = (
            TesseractOcrProvider._parse_tsv_detailed(payload)
        )
        tokens = [dict(token) for token in parsed_tokens]
        line_groups: dict[tuple[int, int, int, int], list[dict[str, object]]] = {}
        for token in tokens:
            token["included"] = bool(str(token["text"]).strip())
            token["filter_reason"] = ""
            key = tuple(
                int(token[name])
                for name in ("page", "block", "paragraph", "line")
            )
            line_groups.setdefault(key, []).append(token)

        line_confidences = {
            key: TesseractOcrProvider._token_confidence(values)
            for key, values in line_groups.items()
        }
        strongest_line = max(
            (value for value in line_confidences.values() if value is not None),
            default=None,
        )
        for key, line_tokens in line_groups.items():
            line_confidence = line_confidences[key]
            for token in line_tokens:
                token["line_confidence"] = line_confidence
                if not bool(token["valid_confidence"]):
                    token["included"] = False
                    token["filter_reason"] = "invalid_confidence"
            # A previous version removed every line below 0.50 whenever another
            # line was strong. That could erase a legitimate short subtitle if
            # a HUD label happened to receive the stronger score. Keep an
            # uncertain, natural-looking line; remove only a very weak line or
            # one whose token composition independently identifies it as noise.
            if (
                strongest_line is not None
                and strongest_line >= 0.80
                and (line_confidence is None or line_confidence < 0.50)
                and (
                    line_confidence is None
                    or strongest_line - line_confidence >= 0.30
                )
                and (
                    line_confidence is None
                    or line_confidence < 0.35
                    or TesseractOcrProvider._line_is_fragment_noise(line_tokens)
                )
            ):
                for token in line_tokens:
                    if bool(token["included"]):
                        token["included"] = False
                        token["filter_reason"] = "weak_isolated_line"
                continue
            TesseractOcrProvider._filter_separated_weak_suffix(line_tokens)

        filtered_lines: list[str] = []
        for line_tokens in line_groups.values():
            words = [
                str(token["text"])
                for token in line_tokens
                if bool(token["included"]) and str(token["text"]).strip()
            ]
            if words:
                filtered_lines.append(" ".join(words))
        filtered_text = "\n".join(filtered_lines)
        included_tokens = [token for token in tokens if bool(token["included"])]
        confidence = TesseractOcrProvider._token_confidence(included_tokens)
        return raw_text, filtered_text, confidence, raw_confidence, tokens

    @staticmethod
    def _token_confidence(tokens: list[dict[str, object]]) -> float | None:
        weighted = 0.0
        weight = 0
        for token in tokens:
            if not bool(token.get("valid_confidence", False)):
                continue
            characters = int(token.get("alphanumeric_characters", 0))
            if characters <= 0:
                continue
            weighted += (float(token["confidence"]) / 100.0) * characters
            weight += characters
        return weighted / weight if weight else None

    @staticmethod
    def _filter_separated_weak_suffix(
        line_tokens: list[dict[str, object]],
    ) -> None:
        included = [token for token in line_tokens if bool(token["included"])]
        if len(included) < 4:
            return
        # Split on actual geometry rather than scanning backwards until the
        # first individually high-confidence glyph. Real suffix noise can
        # contain one falsely confident "E" while the cluster as a whole is
        # weak. A large gap, a strong main clause and fragment-heavy suffix are
        # all required, so valid short words are not deleted merely for being
        # short.
        candidates: list[
            tuple[int, list[dict[str, object]], list[dict[str, object]]]
        ] = []
        for split_index in range(2, len(included) - 1):
            prefix = included[:split_index]
            suffix = included[split_index:]
            prefix_right = max(
                int(token["left"]) + int(token["width"]) for token in prefix
            )
            suffix_left = min(int(token["left"]) for token in suffix)
            gap = suffix_left - prefix_right
            typical_height = max(
                1,
                round(
                    sum(int(token["height"]) for token in prefix) / len(prefix)
                ),
            )
            if gap < max(16, round(typical_height * 1.5)):
                continue
            candidates.append((gap, prefix, suffix))
        for _gap, prefix, suffix in sorted(candidates, reverse=True, key=lambda item: item[0]):
            prefix_confidence = TesseractOcrProvider._token_confidence(prefix)
            suffix_confidence = TesseractOcrProvider._token_confidence(suffix)
            if (
                prefix_confidence is None
                or prefix_confidence < 0.80
                or suffix_confidence is None
                or suffix_confidence > 0.55
                or prefix_confidence - suffix_confidence < 0.30
            ):
                continue
            fragment_count = sum(
                bool(token["punctuation_only"])
                or int(token["alphanumeric_characters"]) <= 2
                for token in suffix
            )
            if fragment_count / len(suffix) < 0.50:
                continue
            for token in suffix:
                token["included"] = False
                token["filter_reason"] = "separated_weak_suffix"
            return

    @staticmethod
    def _line_is_fragment_noise(tokens: list[dict[str, object]]) -> bool:
        visible = [token for token in tokens if str(token["text"]).strip()]
        if not visible:
            return True
        if not any(int(token["alphanumeric_characters"]) for token in visible):
            return True
        fragments = sum(
            bool(token["punctuation_only"])
            or int(token["alphanumeric_characters"]) <= 2
            for token in visible
        )
        return len(visible) >= 2 and fragments / len(visible) >= 0.60

    @staticmethod
    def _quality_evidence(
        text: str,
        confidence: float | None,
        tokens: list[dict[str, object]],
        *,
        image_width: int,
        image_height: int,
    ) -> dict[str, object]:
        visible = [token for token in tokens if str(token["text"]).strip()]
        included = [token for token in visible if bool(token.get("included"))]
        word_tokens = [
            token for token in included if int(token["alphanumeric_characters"]) > 0
        ]
        line_keys = {
            tuple(int(token[name]) for name in ("page", "block", "paragraph", "line"))
            for token in included
        }
        dropped = [token for token in visible if not bool(token.get("included"))]
        valid_confidences = [
            float(token["confidence"]) / 100.0
            for token in included
            if bool(token.get("valid_confidence"))
            and int(token["alphanumeric_characters"]) > 0
        ]
        minimum_confidence = min(valid_confidences, default=None)
        geometry_coherent = False
        if included and len(line_keys) == 1 and image_width > 0 and image_height > 0:
            left = min(int(token["left"]) for token in included)
            top = min(int(token["top"]) for token in included)
            right = max(
                int(token["left"]) + int(token["width"]) for token in included
            )
            bottom = max(
                int(token["top"]) + int(token["height"]) for token in included
            )
            geometry_coherent = (
                (right - left) / image_width >= 0.04
                and (bottom - top) / image_height >= 0.04
            )

        visible_characters = [character for character in text if not character.isspace()]
        alpha_count = sum(character.isalpha() for character in visible_characters)
        allowed_punctuation = set("'’.,!?…:;-\N{EN DASH}\N{EM DASH}„”\"")
        clean_composition = all(
            character.isalnum() or character in allowed_punctuation
            for character in visible_characters
        )
        fragments = sum(
            bool(token["punctuation_only"])
            or int(token["alphanumeric_characters"]) <= 1
            for token in word_tokens
        )
        has_real_word = any(
            int(token["alphabetic_characters"]) >= 3 for token in word_tokens
        )
        all_upper_single = (
            len(word_tokens) == 1
            and str(word_tokens[0]["text"]).isupper()
            and len(str(word_tokens[0]["text"])) <= 3
        )
        clean_short = bool(
            text
            and 1 <= len(word_tokens) <= 3
            and len(line_keys) == 1
            and not dropped
            and confidence is not None
            and confidence >= 0.94
            and minimum_confidence is not None
            and minimum_confidence >= 0.90
            and alpha_count >= 3
            and len(visible_characters) <= 32
            and clean_composition
            and has_real_word
            and not all_upper_single
            and fragments / len(word_tokens) < 0.60
            and geometry_coherent
        )
        reasons = sorted(
            {
                str(token.get("filter_reason", ""))
                for token in dropped
                if str(token.get("filter_reason", ""))
            }
        )
        return {
            "token_count": len(visible),
            "included_token_count": len(included),
            "line_count": len(line_keys),
            "dropped_token_count": len(dropped),
            "minimum_token_confidence": minimum_confidence,
            "geometry_coherent": geometry_coherent,
            "clean_short_phrase_evidence": clean_short,
            "filter_summary": ",".join(reasons) if reasons else "unchanged",
        }

    @staticmethod
    def _validated_tsv_rows(payload: str) -> list[dict[str, str]]:
        """Parse Tesseract TSV without applying CSV quote semantics.

        Tesseract's final column is literal OCR text. In particular, an ASCII
        double quote does not start a quoted TSV field. Every physical output
        line must therefore remain one complete 12-column record.
        """

        physical_lines = str(payload).split("\n")
        if physical_lines and physical_lines[-1] == "":
            physical_lines.pop()
        if not physical_lines:
            raise ValueError("Tesseract returned empty TSV output")

        header_line = physical_lines[0]
        if header_line.endswith("\r"):
            header_line = header_line[:-1]
        header = tuple(header_line.split("\t"))
        if header != _TESSERACT_TSV_COLUMNS:
            raise ValueError("Tesseract returned an invalid TSV header")

        rows: list[dict[str, str]] = []
        for line_number, physical_line in enumerate(physical_lines[1:], start=2):
            if physical_line.endswith("\r"):
                physical_line = physical_line[:-1]
            if not physical_line:
                raise ValueError(
                    f"Tesseract returned an empty TSV row at line {line_number}"
                )
            fields = physical_line.split("\t")
            if len(fields) != len(_TESSERACT_TSV_COLUMNS):
                raise ValueError(
                    "Tesseract returned a malformed TSV row at line "
                    f"{line_number}: expected 12 columns, got {len(fields)}"
                )
            row = dict(zip(_TESSERACT_TSV_COLUMNS, fields, strict=True))
            for name in _TESSERACT_TSV_INTEGER_COLUMNS:
                try:
                    int(row[name])
                except ValueError as error:
                    raise ValueError(
                        "Tesseract returned non-integer TSV metadata at line "
                        f"{line_number}: {name}"
                    ) from error
            try:
                confidence = float(row["conf"])
            except ValueError as error:
                raise ValueError(
                    "Tesseract returned invalid confidence at line "
                    f"{line_number}"
                ) from error
            if not isfinite(confidence):
                raise ValueError(
                    "Tesseract returned non-finite confidence at line "
                    f"{line_number}"
                )
            if any(
                character in "\t\r\n"
                or unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"}
                for character in row["text"]
            ):
                raise ValueError(
                    "Tesseract returned control data in OCR text at line "
                    f"{line_number}"
                )
            rows.append(row)
        return rows

    def _write_debug_capture(
        self,
        *,
        frame: CaptureFrame,
        roi_image: QImage,
        processed_png: bytes,
        processed_image: QImage,
        tsv: str,
        tokens: list[dict[str, object]],
        text: str,
        filtered_text: str,
        confidence: float | None,
        raw_confidence: float | None,
        language: str,
        backend: str,
        fallback_reason: str,
        source_resolution: int,
        image_preprocessing_ms: float,
        png_encoding_ms: float,
        worker_decode_ms: float | None,
        recognition_ms: float | None,
        worker_execution_ms: float | None,
        worker_roundtrip_ms: float | None,
        worker_lock_wait_ms: float | None,
        client_overhead_ms: float | None,
        elapsed_ms: float,
    ) -> str:
        if (
            not self._debug_capture_enabled
            or self._debug_capture_count >= self._debug_capture_limit
            or (
                not text.strip()
                and not any(str(token.get("text", "")).strip() for token in tokens)
            )
        ):
            return ""
        try:
            session = self._debug_session_dir()
            self._debug_capture_count += 1
            stem = f"observation-{self._debug_capture_count:02d}"
            roi_png = self._encode_image(roi_image)
            files = {
                "roi": session / f"{stem}-roi.png",
                "processed": session / f"{stem}-processed.png",
                "tsv": session / f"{stem}.tsv",
                "metadata": session / f"{stem}.json",
            }
            self._atomic_private_write(files["roi"], roi_png)
            self._atomic_private_write(files["processed"], processed_png)
            self._atomic_private_write(files["tsv"], tsv.encode("utf-8"))
            metadata = {
                "schema": 1,
                "observation": self._debug_capture_count,
                "captured_at_unix_ns": time.time_ns(),
                "source": {
                    "id": frame.source_id,
                    "session_id": frame.session_id,
                    "generation": frame.generation,
                    "timestamp_monotonic": frame.timestamp_monotonic,
                },
                "roi": {
                    "width": frame.width,
                    "height": frame.height,
                    "stride": frame.stride,
                    "pixel_format": frame.pixel_format,
                    "file": files["roi"].name,
                },
                "processed": {
                    "width": processed_image.width(),
                    "height": processed_image.height(),
                    "format": "grayscale8_png",
                    "source_resolution": source_resolution,
                    "upscale_limit": OCR_UPSCALE_FACTOR,
                    "local_contrast": True,
                    "file": files["processed"].name,
                },
                "ocr": {
                    "language": language,
                    "oem": 1,
                    "psm": 6,
                    "backend": backend,
                    "fallback_reason": fallback_reason,
                    "raw_phrase": text,
                    "filtered_phrase": filtered_text,
                    "normalized_phrase": self._normalize_debug_phrase(
                        filtered_text
                    ),
                    "phrase_confidence": confidence,
                    "raw_phrase_confidence": raw_confidence,
                    "tokens": tokens,
                    "line_geometry": self._line_geometry(tokens),
                    "tsv_file": files["tsv"].name,
                },
                "timings_ms": {
                    "image_preprocessing": image_preprocessing_ms,
                    "png_encoding": png_encoding_ms,
                    "worker_lock_wait": worker_lock_wait_ms,
                    "worker_roundtrip": worker_roundtrip_ms,
                    "worker_execution": worker_execution_ms,
                    "worker_decode": worker_decode_ms,
                    "recognition_only": recognition_ms,
                    "client_overhead": client_overhead_ms,
                    "complete_ocr": elapsed_ms,
                },
            }
            self._atomic_private_write(
                files["metadata"],
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
            )
            logger.info(
                "Saved opt-in Narrator OCR diagnostic %d/%d: %s",
                self._debug_capture_count,
                self._debug_capture_limit,
                files["metadata"],
            )
            return str(files["metadata"])
        except Exception:
            logger.exception("Could not save opt-in Narrator OCR diagnostic")
            return ""

    def _debug_session_dir(self) -> Path:
        if self._debug_capture_session is not None:
            return self._debug_capture_session
        root = self._debug_capture_root
        if root.is_symlink():
            raise RuntimeError("OCR debug capture root must not be a symlink")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        session = root / f"session-{time.time_ns()}-{os.getpid()}"
        session.mkdir(mode=0o700)
        self._debug_capture_session = session
        return session

    @staticmethod
    def _encode_image(image: QImage) -> bytes:
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(
            buffer, "PNG"
        ):
            raise RuntimeError("The OCR debug image could not be encoded")
        buffer.close()
        return bytes(encoded)

    @staticmethod
    def _atomic_private_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _normalize_debug_phrase(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(text))
        printable = "".join(
            " " if unicodedata.category(character).startswith("C") else character
            for character in normalized
        )
        return " ".join(printable.split())

    @staticmethod
    def _line_geometry(
        tokens: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        lines: dict[tuple[int, int, int, int], list[dict[str, object]]] = {}
        for token in tokens:
            if not str(token.get("text", "")):
                continue
            key = tuple(
                int(token.get(name, 0))
                for name in ("page", "block", "paragraph", "line")
            )
            lines.setdefault(key, []).append(token)
        diagnostics: list[dict[str, object]] = []
        for key, values in lines.items():
            tops = [int(token["top"]) for token in values]
            bottoms = [
                int(token["top"]) + int(token["height"])
                for token in values
            ]
            diagnostics.append(
                {
                    "page": key[0],
                    "block": key[1],
                    "paragraph": key[2],
                    "line": key[3],
                    "token_count": len(values),
                    "top_min": min(tops),
                    "top_max": max(tops),
                    "bottom_min": min(bottoms),
                    "bottom_max": max(bottoms),
                }
            )
        return diagnostics

    @staticmethod
    def _environment_flag(name: str) -> bool:
        return os.environ.get(name, "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _environment_int(name: str, fallback: int) -> int:
        try:
            return int(os.environ.get(name, str(fallback)).strip())
        except ValueError:
            return fallback


__all__ = [
    "TESSERACT_COMPONENT_ID",
    "TESSERACT_MODEL_RELATIVE_PATH",
    "TESSERACT_POLISH_COMPONENT_ID",
    "TESSERACT_POLISH_MODEL_RELATIVE_PATH",
    "OCR_UPSCALE_FACTOR",
    "TesseractOcrProvider",
    "TesseractWorkerClient",
]
