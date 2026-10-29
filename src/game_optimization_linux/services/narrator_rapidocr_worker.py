"""Isolated CPU RapidOCR worker. No network server or automatic model downloads."""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import time

MODEL_FILES = {
    "PP-OCRv6_det_small.onnx": (9_929_594, "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "PP-OCRv6_rec_small.onnx": (21_234_383, "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": (585_532, "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
}


def verify_models(directory: Path) -> None:
    for name, (size, digest) in MODEL_FILES.items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise RuntimeError("Install the verified RapidOCR models through the application")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise RuntimeError("RapidOCR model checksum mismatch: " + name)


class RapidEngine:
    def __init__(self, model_directory: Path) -> None:
        verify_models(model_directory)
        # Imports and ONNX sessions live only in this child, never in the Qt loop.
        import cv2
        import numpy as np
        from rapidocr import RapidOCR
        self.cv2, self.np = cv2, np
        cv2.setNumThreads(1)
        self.engine = RapidOCR(params={
            "Global.use_cls": False,
            "Global.text_score": 0.0,  # The shared pipeline owns confidence gating.
            "Global.log_level": "error",
            # A subtitle strip must not be enlarged until its *short* side is
            # 736 px (the upstream document-OCR default). Bound the long side.
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            "Det.model_path": str(model_directory / "PP-OCRv6_det_small.onnx"),
            "Rec.model_path": str(model_directory / "PP-OCRv6_rec_small.onnx"),
            "Cls.model_path": str(model_directory / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": False,
        })
        # Warm both models through the public API, without a download fallback.
        blank = np.full((64, 320, 3), 255, dtype=np.uint8)
        self.engine(blank, use_det=False, use_cls=False, use_rec=True)
        self.engine(blank, use_det=True, use_cls=False, use_rec=True)

    def recognize(self, payload: bytes) -> dict[str, object]:
        started = time.monotonic()
        image = self.cv2.imdecode(self.np.frombuffer(payload, dtype=self.np.uint8), self.cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Invalid OCR image")
        decoded = time.monotonic()
        result = self.engine(image, use_det=True, use_cls=False, use_rec=True,
                             text_score=0.0)
        finished = time.monotonic()
        texts = result.txts if result.txts is not None else ()
        scores = result.scores if result.scores is not None else ()
        boxes = result.boxes if result.boxes is not None else ()
        if not (len(texts) == len(scores) == len(boxes)):
            raise RuntimeError("RapidOCR returned inconsistent line results")
        return {
            "lines": [{"text": str(text), "score": float(score), "box": box.tolist()}
                      for text, score, box in zip(texts, scores, boxes)],
            "decode_ms": (decoded - started) * 1000,
            "recognition_ms": (finished - decoded) * 1000,
            "elapsed_ms": (finished - started) * 1000,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--language", choices=("en", "pl"), required=True)
    args = parser.parse_args()
    output = sys.stdout

    def send(value: dict[str, object]) -> None:
        output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        output.flush()

    try:
        started = time.monotonic()
        with redirect_stdout(sys.stderr):
            engine = RapidEngine(args.model)
        send({"status": "ready", "initialization_ms": (time.monotonic() - started) * 1000})
    except Exception as error:
        send({"status": "error", "message": str(error)})
        return 1
    for raw in sys.stdin:
        request: dict[str, object] = {}
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                request = {}
                raise ValueError("Invalid OCR request")
            if request.get("command") == "shutdown":
                return 0
            if request.get("command") != "recognize":
                raise ValueError("Unsupported OCR command")
            payload = base64.b64decode(request["png_base64"], validate=True)
            with redirect_stdout(sys.stderr):
                result = engine.recognize(payload)
            send({"ok": True, "request_id": request.get("request_id"), **result})
        except Exception as error:
            send({"ok": False, "request_id": request.get("request_id"), "error": str(error)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
