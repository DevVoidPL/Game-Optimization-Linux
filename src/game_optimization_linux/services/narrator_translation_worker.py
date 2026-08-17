"""Narrow JSON-lines worker for CPU subtitle translation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any


class _BpeTokenizer:
    def __init__(self, model_file: str) -> None:
        from sacremoses.normalize import MosesPunctNormalizer
        from sacremoses.tokenize import MosesDetokenizer, MosesTokenizer
        from subword_nmt.apply_bpe import BPE

        self._normalizer = MosesPunctNormalizer("en")
        self._tokenizer = MosesTokenizer("en")
        self._detokenizer = MosesDetokenizer("pl")
        with Path(model_file).open("r", encoding="utf-8") as codes:
            self._bpe = BPE(codes)

    def encode(self, text: str, *, out_type: type[str]) -> list[str]:
        if out_type is not str:
            raise TypeError("The BPE tokenizer returns string tokens")
        normalized = self._normalizer.normalize(text)
        tokens = self._tokenizer.tokenize(normalized)
        return self._bpe.segment_tokens(tokens)

    def decode(self, tokens: list[str]) -> str:
        words = " ".join(tokens).replace("@@ ", "").split(" ")
        return self._detokenizer.detokenize(words)


class _TranslationEngine:
    def __init__(
        self,
        model_dir: Path,
        tokenizer_path: Path,
        target_prefix: str = "",
        *,
        translator_factory: Any = None,
        tokenizer_factory: Any = None,
    ) -> None:
        if translator_factory is None:
            import ctranslate2

            translator_factory = ctranslate2.Translator
        tokenizer_factory = tokenizer_factory or _BpeTokenizer
        self._tokenizer = tokenizer_factory(model_file=str(tokenizer_path))
        self._translator = translator_factory(
            str(model_dir),
            device="cpu",
            compute_type="auto",
            inter_threads=1,
            intra_threads=0,
        )
        self._target_prefix = target_prefix

    def translate(self, text: str, *, beam_size: int) -> str:
        tokens = self._tokenizer.encode(text, out_type=str)
        if not tokens:
            raise RuntimeError("The translation tokenizer returned no tokens")
        options: dict[str, object] = {
            "beam_size": beam_size,
            "num_hypotheses": 1,
            "length_penalty": 0.2,
            "replace_unknowns": True,
        }
        if self._target_prefix:
            options["target_prefix"] = [[self._target_prefix]]
        results = self._translator.translate_batch([tokens], **options)
        if not results or not results[0].hypotheses:
            raise RuntimeError("The translation model returned no hypothesis")
        translated = str(self._tokenizer.decode(results[0].hypotheses[0])).strip()
        if self._target_prefix and translated.startswith(self._target_prefix):
            translated = translated[len(self._target_prefix) :].lstrip()
        if not translated:
            raise RuntimeError("The translation model returned empty text")
        return translated


def _write(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--target-prefix", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        engine = _TranslationEngine(
            arguments.model_dir,
            arguments.tokenizer,
            arguments.target_prefix,
        )
    except Exception as error:
        _write({"status": "error", "message": f"Model initialization failed: {error}"})
        return 1
    _write({"status": "ready"})
    for line in sys.stdin:
        request: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("id")
            if request.get("operation") != "translate":
                raise ValueError("unsupported operation")
            text = str(request.get("text", "")).strip()
            if not text or len(text) > 8_000:
                raise ValueError("invalid translation text")
            beam_size = int(request.get("beam_size", 4))
            if beam_size not in {1, 4}:
                raise ValueError("invalid beam size")
            started = time.monotonic()
            translated = engine.translate(text, beam_size=beam_size)
            _write(
                {
                    "id": request_id,
                    "status": "ok",
                    "text": translated,
                    "elapsed_ms": max(0.0, (time.monotonic() - started) * 1000.0),
                }
            )
        except Exception as error:
            request_id = request.get("id") if isinstance(request, dict) else None
            _write({"id": request_id, "status": "error", "message": str(error)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
