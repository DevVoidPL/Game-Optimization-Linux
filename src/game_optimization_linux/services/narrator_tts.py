"""Polish speech synthesis through the optional Piper runtime."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import importlib.util
import json
from math import isfinite
from pathlib import Path
import selectors
import subprocess
import sys
from threading import Lock, RLock, Thread
import time
from typing import Protocol, TextIO

from game_optimization_linux.config import NARRATOR_COMPONENTS_DIR
from game_optimization_linux.models.narrator import PcmAudio


PIPER_COMPONENT_ID = "tts.polish-voice"
PIPER_PROVIDER_ID = "piper-1.7.0-pl-pl-gosia-medium-058271f"
PIPER_VOICE_ID = "pl_PL-gosia-medium"
PIPER_VOICE_VERSION = "058271fb41b630e96989367e15b4514992a25b42"
PIPER_MODEL_RELATIVE_PATH = Path("voices") / "pl_PL-gosia-medium.onnx"
PIPER_CONFIG_RELATIVE_PATH = Path("voices") / "pl_PL-gosia-medium.onnx.json"
_WORKER_BOOTSTRAP = (
    "import runpy, sys; "
    "path = sys.argv.pop(1); "
    "sys.argv[0] = path; "
    "runpy.run_path(path, run_name='__main__')"
)


@dataclass(frozen=True, slots=True)
class PolishVoice:
    voice_id: str
    name: str
    language: str
    version: str


@dataclass(frozen=True, slots=True)
class PiperSynthesis:
    samples: bytes
    sample_rate: int
    channels: int
    sample_format: str = "s16le"


POLISH_VOICES = (
    PolishVoice(
        voice_id=PIPER_VOICE_ID,
        name="Gosia",
        language="pl",
        version=PIPER_VOICE_VERSION,
    ),
)


class PiperWorker(Protocol):
    def synthesize(self, text: str, *, speech_rate: float) -> PiperSynthesis: ...

    def cancel(self) -> None: ...

    def close(self) -> None: ...


def _piper_runtime_available() -> bool:
    try:
        return importlib.util.find_spec("piper") is not None
    except (ImportError, ValueError):
        return False


class PiperWorkerClient:
    """Persistent, narrow client for the Piper synthesis process."""

    def __init__(
        self,
        model_path: Path,
        config_path: Path,
        *,
        executable: str | Path = sys.executable,
        timeout_seconds: float = 60.0,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self._model_path = Path(model_path)
        self._config_path = Path(config_path)
        self._executable = str(executable)
        self._worker_script = Path(__file__).with_name("narrator_piper_worker.py").resolve()
        self._timeout_seconds = float(timeout_seconds)
        if self._timeout_seconds <= 0:
            raise ValueError("Piper worker timeout must be positive")
        self._popen = popen
        self._state_lock = RLock()
        self._io_lock = Lock()
        self._process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._stderr_tail: deque[str] = deque(maxlen=40)

    def synthesize(self, text: str, *, speech_rate: float) -> PiperSynthesis:
        with self._io_lock:
            process = self._ensure_process()
            self._request_id += 1
            request_id = self._request_id
            request = {
                "command": "synthesize",
                "request_id": request_id,
                "text": text,
                "speech_rate": speech_rate,
            }
            try:
                assert process.stdin is not None
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                response = self._read_response(process)
            except Exception:
                self._discard(process)
                raise
            if response.get("request_id") != request_id:
                self._discard(process)
                raise RuntimeError("The Piper worker returned an unexpected response")
            if not response.get("ok"):
                message = str(response.get("error", "")).strip()
                raise RuntimeError(message or "Piper speech synthesis failed")
            try:
                import base64

                samples = base64.b64decode(
                    str(response["samples_base64"]), validate=True
                )
                sample_rate = int(response["sample_rate"])
                channels = int(response["channels"])
                sample_format = str(response["sample_format"])
            except (KeyError, TypeError, ValueError) as error:
                self._discard(process)
                raise RuntimeError("The Piper worker returned invalid audio data") from error
            return PiperSynthesis(samples, sample_rate, channels, sample_format)

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
            argv = [
                self._executable,
                "-c",
                _WORKER_BOOTSTRAP,
                str(self._worker_script),
                "--model",
                str(self._model_path),
                "--config",
                str(self._config_path),
            ]
            process = self._popen(
                argv,
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
                name="narrator-piper-stderr",
                daemon=True,
            ).start()
            return process

    def _read_response(self, process: subprocess.Popen[str]) -> dict[str, object]:
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(self._timeout_seconds):
                raise RuntimeError("Piper speech synthesis timed out")
            line = process.stdout.readline()
        finally:
            selector.close()
        if not line:
            detail = "\n".join(self._stderr_tail).strip()
            raise RuntimeError(detail or "The Piper worker stopped unexpectedly")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError("The Piper worker returned invalid JSON") from error
        if not isinstance(response, dict):
            raise RuntimeError("The Piper worker returned an invalid response")
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


class PiperPolishTtsProvider:
    provider_id = PIPER_PROVIDER_ID
    voices = POLISH_VOICES
    default_voice_id = PIPER_VOICE_ID

    def __init__(
        self,
        component_root: Path = NARRATOR_COMPONENTS_DIR,
        *,
        worker_factory: Callable[[Path, Path], PiperWorker] = PiperWorkerClient,
        runtime_available: Callable[[], bool] = _piper_runtime_available,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._component_root = Path(component_root)
        self._worker_factory = worker_factory
        self._runtime_available = runtime_available
        self._clock = clock
        self._state_lock = RLock()
        self._inference_lock = Lock()
        self._worker: PiperWorker | None = None

    @property
    def model_path(self) -> Path:
        return (
            self._component_root
            / PIPER_COMPONENT_ID
            / PIPER_MODEL_RELATIVE_PATH
        )

    @property
    def config_path(self) -> Path:
        return (
            self._component_root
            / PIPER_COMPONENT_ID
            / PIPER_CONFIG_RELATIVE_PATH
        )

    @property
    def available(self) -> bool:
        return bool(
            self._runtime_available()
            and self.model_path.is_file()
            and self.config_path.is_file()
        )

    @property
    def status_message(self) -> str:
        if not self._runtime_available():
            return "The Piper CPU runtime is unavailable"
        if not self.model_path.is_file() or not self.config_path.is_file():
            return "Install the verified Polish Piper voice"
        return "Piper Polish speech synthesis is ready"

    @property
    def available_voice_ids(self) -> tuple[str, ...]:
        return tuple(voice.voice_id for voice in self.voices)

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio:
        phrase = " ".join(str(text).split())
        if not phrase:
            raise ValueError("Speech text must not be empty")
        if language.casefold() not in {"pl", "pl-pl"}:
            raise ValueError("The current Piper provider supports Polish only")
        selected_voice = voice_id.strip() or PIPER_VOICE_ID
        if selected_voice not in self.available_voice_ids:
            raise ValueError(f"Unsupported Polish voice: {selected_voice}")
        try:
            normalized_rate = float(speech_rate)
        except (TypeError, ValueError) as error:
            raise ValueError("Speech rate must be a number") from error
        if not isfinite(normalized_rate) or not 0.5 <= normalized_rate <= 2.0:
            raise ValueError("Speech rate must be between 0.5 and 2.0")
        if not self.available:
            raise RuntimeError(self.status_message)

        started = self._clock()
        with self._inference_lock:
            worker = self._get_worker()
            try:
                result = worker.synthesize(phrase, speech_rate=normalized_rate)
            except Exception:
                self._drop_worker(worker)
                raise
        if result.sample_format != "s16le":
            raise RuntimeError(
                f"Piper returned unsupported PCM format: {result.sample_format}"
            )
        if result.sample_rate <= 0 or result.channels != 1:
            raise RuntimeError("Piper returned an unsupported audio format")
        if not result.samples or len(result.samples) % 2:
            raise RuntimeError("Piper returned invalid PCM samples")
        return PcmAudio(
            samples=result.samples,
            sample_rate=result.sample_rate,
            channels=result.channels,
            sample_format=result.sample_format,
            provider_id=self.provider_id,
            elapsed_ms=max(0.0, (self._clock() - started) * 1000.0),
        )

    def cancel(self) -> None:
        with self._state_lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            worker.cancel()

    def close(self) -> None:
        with self._state_lock:
            worker = self._worker
            self._worker = None
        if worker is not None:
            worker.close()

    def _get_worker(self) -> PiperWorker:
        with self._state_lock:
            if self._worker is None:
                self._worker = self._worker_factory(
                    self.model_path,
                    self.config_path,
                )
            return self._worker

    def _drop_worker(self, worker: PiperWorker) -> None:
        with self._state_lock:
            if self._worker is worker:
                self._worker = None
        worker.close()


__all__ = [
    "PIPER_COMPONENT_ID",
    "PIPER_CONFIG_RELATIVE_PATH",
    "PIPER_MODEL_RELATIVE_PATH",
    "PIPER_PROVIDER_ID",
    "PIPER_VOICE_ID",
    "PIPER_VOICE_VERSION",
    "POLISH_VOICES",
    "PiperPolishTtsProvider",
    "PiperSynthesis",
    "PiperWorkerClient",
    "PolishVoice",
]
