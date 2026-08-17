# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow subprocess boundary for the GPL-licensed Piper runtime."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import json
from pathlib import Path
import sys
from typing import Any, TextIO


def _load_voice(model_path: Path, config_path: Path) -> object:
    from piper import PiperVoice

    return PiperVoice.load(
        str(model_path),
        config_path=str(config_path),
        use_cuda=False,
    )


def _synthesize(voice: object, text: str, speech_rate: float) -> dict[str, Any]:
    from piper import SynthesisConfig

    config = SynthesisConfig(length_scale=1.0 / speech_rate)
    samples = bytearray()
    sample_rate = 0
    channels = 0
    sample_width = 0
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
    if not samples or sample_rate <= 0:
        raise RuntimeError("Piper produced no speech audio")
    if channels != 1 or sample_width != 2:
        raise RuntimeError("Piper produced an unsupported PCM format")
    return {
        "samples_base64": base64.b64encode(samples).decode("ascii"),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_format": "s16le",
    }


def serve(
    model_path: Path,
    config_path: Path,
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    voice_loader: Callable[[Path, Path], object] = _load_voice,
) -> int:
    voice = voice_loader(model_path, config_path)
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
            response = {
                "ok": True,
                "request_id": request_id,
                **_synthesize(voice, text, speech_rate),
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
        return serve(values.model, values.config, sys.stdin, sys.stdout)
    except Exception as error:
        print(str(error) or error.__class__.__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
