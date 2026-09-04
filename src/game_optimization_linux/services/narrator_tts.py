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
PIPER_BASS_COMPONENT_ID = "tts.polish-bass"
PIPER_BASS_VOICE_ID = "pl_PL-bass-high"
PIPER_BASS_VOICE_VERSION = "5b44ec7bab7c5822cfec48fbd5aa99db71a823d6"
PIPER_BASS_MODEL_RELATIVE_PATH = Path("voices") / "pl_PL-bass-high.onnx"
PIPER_BASS_CONFIG_RELATIVE_PATH = Path("voices") / "pl_PL-bass-high.onnx.json"
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
    component_id: str
    model_relative_path: Path
    config_relative_path: Path


@dataclass(frozen=True, slots=True)
class PiperSynthesis:
    samples: bytes
    sample_rate: int
    channels: int
    sample_format: str = "s16le"
    worker_synthesis_ms: float | None = None
    serialization_ms: float | None = None
    worker_roundtrip_ms: float | None = None
    client_decode_ms: float | None = None
    worker_startup_ms: float | None = None
    worker_reused: bool = True


POLISH_VOICES = (
    PolishVoice(
        voice_id=PIPER_VOICE_ID,
        name="Gosia",
        language="pl",
        version=PIPER_VOICE_VERSION,
        component_id=PIPER_COMPONENT_ID,
        model_relative_path=PIPER_MODEL_RELATIVE_PATH,
        config_relative_path=PIPER_CONFIG_RELATIVE_PATH,
    ),
    PolishVoice(
        voice_id=PIPER_BASS_VOICE_ID,
        name="Bass",
        language="pl",
        version=PIPER_BASS_VOICE_VERSION,
        component_id=PIPER_BASS_COMPONENT_ID,
        model_relative_path=PIPER_BASS_MODEL_RELATIVE_PATH,
        config_relative_path=PIPER_BASS_CONFIG_RELATIVE_PATH,
    ),
)


class PiperWorker(Protocol):
    def start(self) -> None: ...

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
        self.initialization_ms: float | None = None
        self.startup_ms: float | None = None

    def start(self) -> None:
        with self._io_lock:
            self._ensure_process()

    def synthesize(self, text: str, *, speech_rate: float) -> PiperSynthesis:
        with self._io_lock:
            existing = self._process
            reused = existing is not None and existing.poll() is None
            process = self._ensure_process()
            self._request_id += 1
            request_id = self._request_id
            request = {
                "command": "synthesize",
                "request_id": request_id,
                "text": text,
                "speech_rate": speech_rate,
            }
            roundtrip_started = time.monotonic()
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

                decode_started = time.monotonic()
                samples = base64.b64decode(
                    str(response["samples_base64"]), validate=True
                )
                decode_ms = max(
                    0.0, (time.monotonic() - decode_started) * 1000.0
                )
                sample_rate = int(response["sample_rate"])
                channels = int(response["channels"])
                sample_format = str(response["sample_format"])
            except (KeyError, TypeError, ValueError) as error:
                self._discard(process)
                raise RuntimeError("The Piper worker returned invalid audio data") from error
            return PiperSynthesis(
                samples,
                sample_rate,
                channels,
                sample_format,
                worker_synthesis_ms=self._response_float(
                    response, "synthesis_ms"
                ),
                serialization_ms=self._response_float(
                    response, "serialization_ms"
                ),
                worker_roundtrip_ms=max(
                    0.0, (time.monotonic() - roundtrip_started) * 1000.0
                ),
                client_decode_ms=decode_ms,
                worker_startup_ms=self.startup_ms if not reused else None,
                worker_reused=reused,
            )

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
            startup_started = time.monotonic()
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
        response = self._read_response(process)
        self.startup_ms = max(
            0.0, (time.monotonic() - startup_started) * 1000.0
        )
        if response.get("status") != "ready":
            message = str(
                response.get("message", "The Piper voice could not be loaded")
            ).strip()
            self._discard(process)
            raise RuntimeError(message or "The Piper voice could not be loaded")
        self.initialization_ms = self._response_float(
            response, "initialization_ms"
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
        self._worker_voice_id = ""

    def prepare(self, voice_id: str) -> None:
        """Load only the selected voice in the persistent worker."""
        selected_voice = voice_id.strip() or PIPER_VOICE_ID
        if not self._runtime_available() or not self.voice_installed(selected_voice):
            return
        with self._inference_lock:
            worker = self._get_worker(selected_voice)
            start = getattr(worker, "start", None)
            if callable(start):
                try:
                    start()
                except Exception:
                    self._drop_worker(worker)
                    raise

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
        return bool(self.available_voice_ids)

    @property
    def status_message(self) -> str:
        if not self._runtime_available():
            return "The Piper CPU runtime is unavailable"
        if not self.installed_voice_ids:
            return "Install the verified Polish Piper voice"
        return "Piper Polish speech synthesis is ready"

    @property
    def available_voice_ids(self) -> tuple[str, ...]:
        if not self._runtime_available():
            return ()
        return self.installed_voice_ids

    @property
    def installed_voice_ids(self) -> tuple[str, ...]:
        return tuple(
            voice.voice_id
            for voice in self.voices
            if self.voice_installed(voice.voice_id)
        )

    def voice_installed(self, voice_id: str) -> bool:
        voice = self._voice(voice_id)
        model, config = self._voice_paths(voice)
        return model.is_file() and config.is_file()

    def voice_available(self, voice_id: str) -> bool:
        return bool(self._runtime_available() and self.voice_installed(voice_id))

    def select_voice(self, voice_id: str) -> None:
        """Release a resident worker when the persisted voice selection changes."""

        self._voice(voice_id)
        with self._inference_lock:
            with self._state_lock:
                if self._worker is None or self._worker_voice_id == voice_id:
                    return
                worker = self._worker
                self._worker = None
                self._worker_voice_id = ""
            worker.close()

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
        if selected_voice not in {voice.voice_id for voice in self.voices}:
            raise ValueError(f"Unsupported Polish voice: {selected_voice}")
        try:
            normalized_rate = float(speech_rate)
        except (TypeError, ValueError) as error:
            raise ValueError("Speech rate must be a number") from error
        if not isfinite(normalized_rate) or not 0.5 <= normalized_rate <= 2.0:
            raise ValueError("Speech rate must be between 0.5 and 2.0")
        if not self._runtime_available():
            raise RuntimeError("The Piper CPU runtime is unavailable")
        if not self.voice_installed(selected_voice):
            raise RuntimeError(
                f"The selected Polish voice is not installed: {selected_voice}"
            )

        started = self._clock()
        wait_started = started
        with self._inference_lock:
            queue_wait_ms = max(
                0.0, (self._clock() - wait_started) * 1000.0
            )
            worker = self._get_worker(selected_voice)
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
            queue_wait_ms=queue_wait_ms,
            worker_roundtrip_ms=result.worker_roundtrip_ms,
            inference_ms=result.worker_synthesis_ms,
            serialization_ms=result.serialization_ms,
            worker_startup_ms=result.worker_startup_ms,
            worker_reused=result.worker_reused,
            audio_duration_ms=(
                len(result.samples) / 2 / result.sample_rate * 1000.0
            ),
        )

    def cancel(self) -> None:
        with self._state_lock:
            worker = self._worker
            self._worker = None
            self._worker_voice_id = ""
        if worker is not None:
            worker.cancel()

    def close(self) -> None:
        with self._state_lock:
            worker = self._worker
            self._worker = None
            self._worker_voice_id = ""
        if worker is not None:
            worker.close()

    def _get_worker(self, voice_id: str) -> PiperWorker:
        with self._state_lock:
            if self._worker is not None and self._worker_voice_id == voice_id:
                return self._worker
            if self._worker is not None:
                self._worker.close()
                self._worker = None
                self._worker_voice_id = ""
            voice = self._voice(voice_id)
            model, config = self._voice_paths(voice)
            self._worker = self._worker_factory(model, config)
            self._worker_voice_id = voice_id
            return self._worker

    def _drop_worker(self, worker: PiperWorker) -> None:
        with self._state_lock:
            if self._worker is worker:
                self._worker = None
                self._worker_voice_id = ""
        worker.close()

    def _voice(self, voice_id: str) -> PolishVoice:
        for voice in self.voices:
            if voice.voice_id == voice_id:
                return voice
        raise ValueError(f"Unsupported Polish voice: {voice_id}")

    def _voice_paths(self, voice: PolishVoice) -> tuple[Path, Path]:
        component = self._component_root / voice.component_id
        return (
            component / voice.model_relative_path,
            component / voice.config_relative_path,
        )


__all__ = [
    "PIPER_COMPONENT_ID",
    "PIPER_BASS_COMPONENT_ID",
    "PIPER_BASS_CONFIG_RELATIVE_PATH",
    "PIPER_BASS_MODEL_RELATIVE_PATH",
    "PIPER_BASS_VOICE_ID",
    "PIPER_BASS_VOICE_VERSION",
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
