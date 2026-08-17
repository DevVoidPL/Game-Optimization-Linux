"""Local English to Polish translation backed by CTranslate2."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
import json
from pathlib import Path
import selectors
import subprocess
import sys
from threading import Lock
import time
from typing import Protocol

from game_optimization_linux.config import NARRATOR_COMPONENTS_DIR
from game_optimization_linux.models.narrator import TranslationResult


ARGOS_TRANSLATION_COMPONENT_ID = "translation.opus-en-pl"
ARGOS_TRANSLATION_MODEL_VERSION = "1.9"
ARGOS_TRANSLATION_PROVIDER_ID = "argos-ctranslate2-en-pl-1.9"


class TranslationWorker(Protocol):
    def translate(self, text: str, *, beam_size: int) -> str: ...

    def cancel(self) -> None: ...

    def close(self) -> None: ...


def _translation_runtime_available() -> bool:
    try:
        return (
            importlib.util.find_spec("ctranslate2") is not None
            and importlib.util.find_spec("sacremoses") is not None
            and importlib.util.find_spec("subword_nmt") is not None
        )
    except (ImportError, ValueError):
        return False


def _model_layout(component_path: Path) -> tuple[Path, Path, str] | None:
    roots = [component_path]
    if component_path.is_dir():
        roots.extend(
            child
            for child in sorted(component_path.iterdir())
            if child.is_dir() and not child.name.startswith(".")
        )
    for root in roots:
        model_dir = root / "model"
        tokenizer = root / "bpe.model"
        metadata_path = root / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(metadata, dict)
            and str(metadata.get("from_code", "")).casefold() == "en"
            and str(metadata.get("to_code", "")).casefold() == "pl"
            and str(metadata.get("package_version", "")) == ARGOS_TRANSLATION_MODEL_VERSION
            and tokenizer.is_file()
            and (model_dir / "config.json").is_file()
            and (model_dir / "model.bin").is_file()
        ):
            target_prefix = str(metadata.get("target_prefix", ""))
            if any(character in target_prefix for character in "\r\n\0"):
                continue
            return model_dir, tokenizer, target_prefix
    return None


class _SubprocessTranslationWorker:
    def __init__(
        self,
        model_dir: Path,
        tokenizer_path: Path,
        target_prefix: str = "",
        *,
        executable: str = sys.executable,
        startup_timeout: float = 30.0,
        request_timeout: float = 60.0,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._tokenizer_path = Path(tokenizer_path)
        self._target_prefix = target_prefix
        self._executable = executable
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._io_lock = Lock()
        self._state_lock = Lock()
        self._process: subprocess.Popen[str] | None = None
        self._next_request_id = 0

    def translate(self, text: str, *, beam_size: int) -> str:
        with self._io_lock:
            process = self._ensure_process()
            self._next_request_id += 1
            request_id = self._next_request_id
            request = {
                "id": request_id,
                "operation": "translate",
                "text": text,
                "beam_size": beam_size,
            }
            try:
                assert process.stdin is not None
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                response = self._read_response(process, self._request_timeout)
            except (BrokenPipeError, OSError, RuntimeError) as error:
                self._discard_process(process)
                raise RuntimeError(f"The translation worker stopped: {error}") from error
            if response.get("id") != request_id:
                self._discard_process(process)
                raise RuntimeError("The translation worker returned an invalid response")
            if response.get("status") != "ok":
                message = str(response.get("message", "Translation failed")).strip()
                raise RuntimeError(message or "Translation failed")
            translated = str(response.get("text", "")).strip()
            if not translated:
                raise RuntimeError("The translation model returned empty text")
            return translated

    def cancel(self) -> None:
        with self._state_lock:
            process = self._process
            self._process = None
        self._terminate(process)

    def close(self) -> None:
        self.cancel()

    def _ensure_process(self) -> subprocess.Popen[str]:
        with self._state_lock:
            process = self._process
            if process is not None and process.poll() is None:
                return process
            process = subprocess.Popen(
                [
                    self._executable,
                    "-m",
                    "game_optimization_linux.services.narrator_translation_worker",
                    "--model-dir",
                    str(self._model_dir),
                    "--tokenizer",
                    str(self._tokenizer_path),
                    "--target-prefix",
                    self._target_prefix,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                close_fds=True,
                start_new_session=True,
            )
            self._process = process
        try:
            response = self._read_response(process, self._startup_timeout)
        except RuntimeError:
            self._discard_process(process)
            raise
        if response.get("status") != "ready":
            message = str(
                response.get("message", "The translation model could not be loaded")
            ).strip()
            self._discard_process(process)
            raise RuntimeError(message or "The translation model could not be loaded")
        return process

    @staticmethod
    def _read_response(
        process: subprocess.Popen[str], timeout: float
    ) -> dict[str, object]:
        stdout = process.stdout
        if stdout is None:
            raise RuntimeError("The translation worker has no output channel")
        selector = selectors.DefaultSelector()
        try:
            selector.register(stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                raise RuntimeError("The translation worker timed out")
            line = stdout.readline()
        finally:
            selector.close()
        if not line:
            code = process.poll()
            detail = f" (exit code {code})" if code is not None else ""
            raise RuntimeError(f"The translation worker closed unexpectedly{detail}")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError("The translation worker returned malformed data") from error
        if not isinstance(response, dict):
            raise RuntimeError("The translation worker returned malformed data")
        return response

    def _discard_process(self, process: subprocess.Popen[str]) -> None:
        with self._state_lock:
            if self._process is process:
                self._process = None
        self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen[str] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


class ArgosCTranslate2TranslationProvider:
    """CPU translation using an audited, extracted Argos EN to PL package."""

    provider_id = ARGOS_TRANSLATION_PROVIDER_ID
    component_id = ARGOS_TRANSLATION_COMPONENT_ID
    model_version = ARGOS_TRANSLATION_MODEL_VERSION
    profile_ids = ("balanced", "fast")
    default_profile_id = "balanced"

    def __init__(
        self,
        component_root: Path = NARRATOR_COMPONENTS_DIR,
        *,
        component_path: Path | None = None,
        runtime_available: Callable[[], bool] = _translation_runtime_available,
        worker_factory: Callable[[Path, Path, str], TranslationWorker] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._component_path = (
            Path(component_path)
            if component_path is not None
            else Path(component_root) / self.component_id
        )
        self._runtime_available = runtime_available
        self._worker_factory = worker_factory or _SubprocessTranslationWorker
        self._clock = clock
        self._worker_lock = Lock()
        self._worker: TranslationWorker | None = None

    @property
    def available(self) -> bool:
        return self._runtime_available() and _model_layout(self._component_path) is not None

    @property
    def status_message(self) -> str:
        if not self._runtime_available():
            return "The local CTranslate2 translation runtime is unavailable"
        if _model_layout(self._component_path) is None:
            return "Install the verified English to Polish translation model"
        return "Local English to Polish translation is ready"

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult:
        source = source_language.strip().casefold()
        target = target_language.strip().casefold()
        if (source, target) != ("en", "pl"):
            raise ValueError("The current translation model supports English to Polish only")
        normalized = " ".join(str(text).split())
        if not normalized:
            raise ValueError("Translation text cannot be empty")
        if len(normalized) > 8_000:
            raise ValueError("Translation text is too long")
        profile = profile_id.strip().casefold() or "balanced"
        if profile not in self.profile_ids:
            raise ValueError(f"Unsupported translation profile: {profile_id}")
        if not self._runtime_available():
            raise RuntimeError("The local CTranslate2 translation runtime is unavailable")
        layout = _model_layout(self._component_path)
        if layout is None:
            raise RuntimeError("The verified English to Polish model is not installed")
        started = self._clock()
        worker = self._get_worker(*layout)
        translated = worker.translate(
            normalized,
            beam_size=4 if profile == "balanced" else 1,
        )
        return TranslationResult(
            source_text=normalized,
            translated_text=translated,
            source_language="en",
            target_language="pl",
            provider_id=self.provider_id,
            elapsed_ms=max(0.0, (self._clock() - started) * 1000.0),
        )

    def cancel(self) -> None:
        with self._worker_lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            worker.cancel()

    def close(self) -> None:
        with self._worker_lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            worker.close()

    def _get_worker(
        self, model_dir: Path, tokenizer: Path, target_prefix: str
    ) -> TranslationWorker:
        with self._worker_lock:
            if self._worker is None:
                self._worker = self._worker_factory(
                    model_dir, tokenizer, target_prefix
                )
            return self._worker


__all__ = [
    "ARGOS_TRANSLATION_COMPONENT_ID",
    "ARGOS_TRANSLATION_MODEL_VERSION",
    "ARGOS_TRANSLATION_PROVIDER_ID",
    "ArgosCTranslate2TranslationProvider",
    "TranslationWorker",
]
