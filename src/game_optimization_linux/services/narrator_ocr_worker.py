"""Isolated persistent Tesseract C API worker for subtitle OCR."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
from ctypes.util import find_library
import json
from pathlib import Path
import sys
import time
from typing import Any


TESS_OEM_LSTM_ONLY = 1
TESS_PSM_SINGLE_BLOCK = 6
TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)


def normalize_tsv(payload: str) -> str:
    """Give C API TSV the same header expected from the current CLI path."""

    text = str(payload)
    if text.startswith("level\t"):
        return text
    return TSV_HEADER + text


class TesseractApiEngine:
    """One initialized language model, owned only by the worker process."""

    def __init__(self, model_path: Path, language: str) -> None:
        model = Path(model_path).resolve()
        expected_name = "pol.traineddata" if language == "pol" else "eng.traineddata"
        if language not in {"eng", "pol"} or model.name != expected_name:
            raise ValueError("The OCR worker received an invalid language model")
        if not model.is_file():
            raise RuntimeError("The verified OCR model is missing")

        tesseract_name = find_library("tesseract") or "libtesseract.so"
        leptonica_name = find_library("leptonica") or "libleptonica.so"
        self._tesseract = ctypes.CDLL(tesseract_name)
        self._leptonica = ctypes.CDLL(leptonica_name)
        self.tesseract_library = str(tesseract_name)
        self.leptonica_library = str(leptonica_name)
        self.last_decode_ms = 0.0
        self.last_recognition_ms = 0.0
        self._configure_api()
        self._api = self._tesseract.TessBaseAPICreate()
        if not self._api:
            raise RuntimeError("Tesseract could not create an OCR engine")
        initialized = self._tesseract.TessBaseAPIInit2(
            self._api,
            str(model.parent).encode("utf-8"),
            language.encode("ascii"),
            TESS_OEM_LSTM_ONLY,
        )
        if initialized != 0:
            self._tesseract.TessBaseAPIDelete(self._api)
            self._api = None
            raise RuntimeError("Tesseract could not initialize the OCR model")
        self._tesseract.TessBaseAPISetPageSegMode(
            self._api, TESS_PSM_SINGLE_BLOCK
        )
        if not self._tesseract.TessBaseAPISetVariable(
            self._api, b"tessedit_create_tsv", b"1"
        ):
            self.close()
            raise RuntimeError("Tesseract could not enable TSV output")

    def recognize_png(self, payload: bytes, *, source_resolution: int) -> str:
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("The OCR worker accepts PNG subtitle images only")
        encoded = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        decode_started = time.monotonic()
        pix = self._leptonica.pixReadMemPng(encoded, len(payload))
        self.last_decode_ms = max(
            0.0, (time.monotonic() - decode_started) * 1000.0
        )
        if not pix:
            raise RuntimeError("Leptonica could not decode the subtitle image")
        pix_pointer = ctypes.c_void_p(pix)
        try:
            self._tesseract.TessBaseAPISetImage2(self._api, pix)
            self._tesseract.TessBaseAPISetSourceResolution(
                self._api, max(70, min(600, int(source_resolution)))
            )
            recognition_started = time.monotonic()
            recognition_status = self._tesseract.TessBaseAPIRecognize(
                self._api, None
            )
            self.last_recognition_ms = max(
                0.0, (time.monotonic() - recognition_started) * 1000.0
            )
            if recognition_status != 0:
                raise RuntimeError("Tesseract recognition failed")
            text_pointer = self._tesseract.TessBaseAPIGetTsvText(self._api, 0)
            if not text_pointer:
                raise RuntimeError("Tesseract returned no TSV result")
            try:
                result = ctypes.string_at(text_pointer).decode(
                    "utf-8", errors="replace"
                )
            finally:
                self._tesseract.TessDeleteText(text_pointer)
            return normalize_tsv(result)
        finally:
            self._tesseract.TessBaseAPIClear(self._api)
            self._leptonica.pixDestroy(ctypes.byref(pix_pointer))

    def close(self) -> None:
        api = getattr(self, "_api", None)
        self._api = None
        if api:
            self._tesseract.TessBaseAPIDelete(api)

    def _configure_api(self) -> None:
        api = self._tesseract
        api.TessBaseAPICreate.restype = ctypes.c_void_p
        api.TessBaseAPIInit2.argtypes = (
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        )
        api.TessBaseAPIInit2.restype = ctypes.c_int
        api.TessBaseAPISetPageSegMode.argtypes = (ctypes.c_void_p, ctypes.c_int)
        api.TessBaseAPISetVariable.argtypes = (
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        )
        api.TessBaseAPISetVariable.restype = ctypes.c_int
        api.TessBaseAPISetImage2.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        api.TessBaseAPISetSourceResolution.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
        )
        api.TessBaseAPIRecognize.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        api.TessBaseAPIRecognize.restype = ctypes.c_int
        api.TessBaseAPIGetTsvText.argtypes = (ctypes.c_void_p, ctypes.c_int)
        api.TessBaseAPIGetTsvText.restype = ctypes.c_void_p
        api.TessDeleteText.argtypes = (ctypes.c_void_p,)
        api.TessBaseAPIClear.argtypes = (ctypes.c_void_p,)
        api.TessBaseAPIDelete.argtypes = (ctypes.c_void_p,)

        lept = self._leptonica
        lept.pixReadMemPng.argtypes = (
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_size_t,
        )
        lept.pixReadMemPng.restype = ctypes.c_void_p
        lept.pixDestroy.argtypes = (ctypes.POINTER(ctypes.c_void_p),)


def handle_request(
    request: dict[str, Any], engine: TesseractApiEngine
) -> dict[str, Any]:
    request_id = request.get("request_id")
    if not isinstance(request_id, int) or request_id < 1:
        raise ValueError("The OCR worker received an invalid request ID")
    if request.get("command") != "recognize":
        raise ValueError("The OCR worker received an unsupported command")
    try:
        payload = base64.b64decode(
            str(request.get("png_base64", "")), validate=True
        )
    except (binascii.Error, ValueError) as error:
        raise ValueError("The OCR worker received invalid image data") from error
    try:
        resolution = int(request.get("source_resolution", 96))
    except (TypeError, ValueError) as error:
        raise ValueError("The OCR worker received an invalid resolution") from error
    started = time.monotonic()
    tsv = engine.recognize_png(payload, source_resolution=resolution)
    return {
        "request_id": request_id,
        "ok": True,
        "tsv": tsv,
        "elapsed_ms": max(0.0, (time.monotonic() - started) * 1000.0),
        "decode_ms": getattr(engine, "last_decode_ms", None),
        "recognition_ms": getattr(engine, "last_recognition_ms", None),
    }


def _write_response(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model", required=True)
    parser.add_argument("--language", choices=("eng", "pol"), required=True)
    values = parser.parse_args(argv)
    try:
        started = time.monotonic()
        engine = TesseractApiEngine(Path(values.model), values.language)
        _write_response(
            {
                "status": "ready",
                "initialization_ms": max(
                    0.0, (time.monotonic() - started) * 1000.0
                ),
                "tesseract_library": engine.tesseract_library,
                "leptonica_library": engine.leptonica_library,
            }
        )
    except Exception as error:
        _write_response({"status": "error", "message": str(error)})
        return 1
    try:
        for line in sys.stdin:
            request: dict[str, Any] | None = None
            try:
                parsed = json.loads(line)
                if not isinstance(parsed, dict):
                    raise ValueError("The OCR worker received invalid JSON")
                request = parsed
                if request.get("command") == "shutdown":
                    return 0
                _write_response(handle_request(request, engine))
            except Exception as error:
                request_id = (
                    request.get("request_id")
                    if request is not None
                    else None
                )
                _write_response(
                    {"request_id": request_id, "ok": False, "error": str(error)}
                )
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
