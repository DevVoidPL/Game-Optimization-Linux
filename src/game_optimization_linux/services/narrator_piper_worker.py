# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow subprocess boundary for the GPL-licensed Piper runtime."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import json
from math import isfinite
import os
from pathlib import Path
import sys
from threading import current_thread
import time
from typing import Any, TextIO

# Piper's own fallback when a voice config omits the value.
DEFAULT_LENGTH_SCALE = 1.0

# The user-facing rate is validated to 0.5x..2.0x and realistic voice defaults
# sit within 0.5..2.0, so dividing one by the other stays inside 0.25..4.0.
# Clamping there accepts every legitimate combination while refusing degenerate
# values that would otherwise produce silence or an enormous audio buffer.
MIN_LENGTH_SCALE = 0.25
MAX_LENGTH_SCALE = 4.0

# Piper accepts any non-negative noise value; these bounds keep the advanced
# controls in the range where output remains intelligible.
MIN_NOISE = 0.0
MAX_NOISE = 2.0

ONNX_INTRA_OP_THREADS = 1
ONNX_INTER_OP_THREADS = 1
ONNX_EXECUTION_MODE = "sequential"
PIPER_WORKER_NICE_INCREMENT = 5


def _session_options(onnxruntime: object) -> object:
    """Create narrator-only ORT options without process-global environment edits."""

    options = onnxruntime.SessionOptions()  # type: ignore[attr-defined]
    options.intra_op_num_threads = ONNX_INTRA_OP_THREADS
    options.inter_op_num_threads = ONNX_INTER_OP_THREADS
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL  # type: ignore[attr-defined]
    add_entry = getattr(options, "add_session_config_entry", None)
    if callable(add_entry):
        add_entry("session.intra_op.allow_spinning", "0")
        add_entry("session.inter_op.allow_spinning", "0")
    return options


def _load_voice(model_path: Path, config_path: Path) -> object:
    import onnxruntime
    from piper import PiperConfig, PiperVoice

    config_values = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config_values, dict):
        raise ValueError("Piper voice configuration must contain an object")
    session = onnxruntime.InferenceSession(
        str(model_path),
        sess_options=_session_options(onnxruntime),
        providers=["CPUExecutionProvider"],
    )
    return PiperVoice(
        session=session,
        config=PiperConfig.from_dict(config_values),
    )


def _voice_default_length_scale(voice: object) -> float:
    """Read the voice's own default length_scale from its loaded config.

    Piper stores this in the ``inference`` block of the ``.onnx.json`` file and
    exposes it as ``PiperVoice.config.length_scale``.  Both currently installed
    Polish voices default to 1.0, so this changes nothing for them today; it
    keeps the rate control correct if a voice with another default is added.
    """

    config = getattr(voice, "config", None)
    value = getattr(config, "length_scale", None)
    try:
        default = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LENGTH_SCALE
    if not isfinite(default) or default <= 0.0:
        return DEFAULT_LENGTH_SCALE
    return default


def _effective_length_scale(voice: object, speech_rate: float) -> float:
    """Convert a user-facing speed multiplier into a Piper length_scale.

    length_scale is inverse to speed: values below 1 are faster, above 1 are
    slower.  The multiplier is therefore divided into the voice's own default
    rather than passed through, so 1.30x really is 30% faster than that voice's
    natural pace.
    """

    default = _voice_default_length_scale(voice)
    scale = default / speech_rate
    return max(MIN_LENGTH_SCALE, min(MAX_LENGTH_SCALE, scale))


def _synthesize(
    voice: object,
    text: str,
    speech_rate: float,
    *,
    noise_scale: float | None = None,
    noise_w_scale: float | None = None,
) -> dict[str, Any]:
    from piper import SynthesisConfig

    length_scale = _effective_length_scale(voice, speech_rate)
    # Leaving noise_scale/noise_w_scale as None makes Piper fall back to the
    # voice's own configured values, so unset advanced controls change nothing.
    config = SynthesisConfig(
        length_scale=length_scale,
        noise_scale=noise_scale,
        noise_w_scale=noise_w_scale,
    )
    samples = bytearray()
    sample_rate = 0
    channels = 0
    sample_width = 0
    synthesis_started = time.monotonic()
    for chunk in voice.synthesize(text, syn_config=config):  # type: ignore[attr-defined]
        chunk_rate = int(chunk.sample_rate)
        chunk_channels = int(chunk.sample_channels)
        chunk_width = int(chunk.sample_width)
        if sample_rate and (chunk_rate, chunk_channels, chunk_width) != (
            sample_rate,
            channels,
            sample_width,
        ):
            raise RuntimeError("Piper changed audio format during synthesis")
        sample_rate = chunk_rate
        channels = chunk_channels
        sample_width = chunk_width
        samples.extend(chunk.audio_int16_bytes)
    synthesis_ms = max(
        0.0, (time.monotonic() - synthesis_started) * 1000.0
    )
    if not samples or sample_rate <= 0:
        raise RuntimeError("Piper produced no speech audio")
    if channels != 1 or sample_width != 2:
        raise RuntimeError("Piper produced an unsupported PCM format")
    serialization_started = time.monotonic()
    samples_base64 = base64.b64encode(samples).decode("ascii")
    serialization_ms = max(
        0.0, (time.monotonic() - serialization_started) * 1000.0
    )
    return {
        "samples_base64": samples_base64,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_format": "s16le",
        "synthesis_ms": synthesis_ms,
        "serialization_ms": serialization_ms,
        # Reported back so the main process can log what Piper actually used,
        # instead of only what was requested.
        "length_scale": length_scale,
        "voice_default_length_scale": _voice_default_length_scale(voice),
    }


def _optional_noise(value: object, name: str) -> float | None:
    """Validate an optional advanced noise override.

    ``None`` means "leave it to the voice", which is the default.
    """

    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not isfinite(number) or not MIN_NOISE <= number <= MAX_NOISE:
        raise ValueError(f"{name} must be between {MIN_NOISE} and {MAX_NOISE}")
    return number


def _worker_metadata(process_nice: int | None) -> dict[str, object]:
    return {
        "worker_pid": os.getpid(),
        "worker_thread": current_thread().name,
        "process_nice": process_nice,
        "onnx_intra_op_threads": ONNX_INTRA_OP_THREADS,
        "onnx_inter_op_threads": ONNX_INTER_OP_THREADS,
        "onnx_execution_mode": ONNX_EXECUTION_MODE,
    }


def _lower_process_priority(
    increment: int = PIPER_WORKER_NICE_INCREMENT,
) -> int | None:
    """Lower only this worker's priority; unavailable nice is non-fatal."""

    try:
        return os.nice(increment)
    except (AttributeError, OSError):
        return None


def serve(
    model_path: Path,
    config_path: Path,
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    voice_loader: Callable[[Path, Path], object] = _load_voice,
    process_nice: int | None = None,
) -> int:
    initialization_started = time.monotonic()
    voice = voice_loader(model_path, config_path)
    output_stream.write(
        json.dumps(
            {
                "status": "ready",
                "initialization_ms": max(
                    0.0,
                    (time.monotonic() - initialization_started) * 1000.0,
                ),
                **_worker_metadata(process_nice),
            }
        )
        + "\n"
    )
    output_stream.flush()
    for raw_line in input_stream:
        request: object = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            command = request.get("command")
            if command == "shutdown":
                return 0
            if command != "synthesize":
                raise ValueError("unsupported command")
            request_id = request.get("request_id")
            text = " ".join(str(request.get("text", "")).split())
            speech_rate = float(request.get("speech_rate", 1.0))
            if not isinstance(request_id, int) or request_id < 1:
                raise ValueError("request_id must be a positive integer")
            if not text or len(text) > 4000:
                raise ValueError("text must contain between 1 and 4000 characters")
            if not 0.5 <= speech_rate <= 2.0:
                raise ValueError("speech_rate must be between 0.5 and 2.0")
            noise_scale = _optional_noise(request.get("noise_scale"), "noise_scale")
            noise_w_scale = _optional_noise(
                request.get("noise_w_scale"), "noise_w_scale"
            )
            response = {
                "ok": True,
                "request_id": request_id,
                **_synthesize(
                    voice,
                    text,
                    speech_rate,
                    noise_scale=noise_scale,
                    noise_w_scale=noise_w_scale,
                ),
                **_worker_metadata(process_nice),
            }
        except Exception as error:
            response = {
                "ok": False,
                "request_id": (
                    request.get("request_id") if isinstance(request, dict) else None
                ),
                "error": str(error) or error.__class__.__name__,
            }
        output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
        output_stream.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    values = parser.parse_args(argv)
    if not values.model.is_file() or not values.config.is_file():
        print("Piper voice files are missing", file=sys.stderr)
        return 2
    try:
        return serve(
            values.model,
            values.config,
            sys.stdin,
            sys.stdout,
            process_nice=_lower_process_priority(),
        )
    except Exception as error:
        print(str(error) or error.__class__.__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
