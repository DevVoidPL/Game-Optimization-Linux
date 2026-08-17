#!/usr/bin/env python3
"""Manual Wayland portal -> OCR -> translation -> Polish speech check."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

from game_optimization_linux.models.narrator import (
    CaptureSourceType,
    NarratorGameSettings,
    NormalizedRect,
)
from game_optimization_linux.services import (
    ArgosCTranslate2TranslationProvider,
    NarratorPipeline,
    PiperPolishTtsProvider,
    PortalScreenCaptureProvider,
    QtNarratorAudioOutput,
    QtPortalScreenCastBackend,
    TesseractOcrProvider,
    TranslationCache,
)


class _ActiveGame:
    def is_active(self, game_key: str) -> bool:
        del game_key
        return True


def _region(value: str) -> NormalizedRect:
    try:
        x, y, width, height = (float(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("crop must be x,y,width,height") from error
    try:
        return NormalizedRect(x, y, width, height)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=("window", "monitor"),
        default="window",
    )
    parser.add_argument("--crop", type=_region, default=_region("0.05,0.62,0.90,0.30"))
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--component-root", type=Path)
    parser.add_argument("--volume", type=float, default=0.85)
    parser.add_argument("--speech-rate", type=float, default=1.0)
    values = parser.parse_args()

    app = QGuiApplication(sys.argv[:1])
    backend = QtPortalScreenCastBackend(parent=app)
    capture = PortalScreenCaptureProvider(backend)
    ocr = (
        TesseractOcrProvider(values.component_root)
        if values.component_root is not None
        else TesseractOcrProvider()
    )
    translator = (
        ArgosCTranslate2TranslationProvider(values.component_root)
        if values.component_root is not None
        else ArgosCTranslate2TranslationProvider()
    )
    tts = (
        PiperPolishTtsProvider(values.component_root)
        if values.component_root is not None
        else PiperPolishTtsProvider()
    )
    audio = QtNarratorAudioOutput(parent=app)
    if not capture.capabilities().available:
        print(capture.capabilities().message, file=sys.stderr)
        return 2
    if not ocr.available:
        print(ocr.status_message, file=sys.stderr)
        return 2
    if not translator.available:
        print(translator.status_message, file=sys.stderr)
        return 2
    if not tts.available:
        print(tts.status_message, file=sys.stderr)
        return 2
    pipeline = NarratorPipeline(
        capture,
        ocr,
        translator,
        tts,
        audio,
        _ActiveGame(),
        TranslationCache(),
    )
    settings = replace(
        NarratorGameSettings.default("local-manual-wayland-test"),
        enabled=True,
        capture_source=CaptureSourceType(values.source),
        subtitle_region=values.crop,
        ocr_provider_id=ocr.provider_id,
        translation_provider_id=translator.provider_id,
        translation_profile_id=translator.default_profile_id,
        tts_provider_id=tts.provider_id,
        voice_id=tts.default_voice_id,
        volume=values.volume,
        speech_rate=values.speech_rate,
    )
    previous_text = ""
    previous_translation = ""
    previous_spoken = ""

    def poll() -> None:
        nonlocal previous_text, previous_translation, previous_spoken
        snapshot = pipeline.snapshot
        if snapshot.last_detected_text and snapshot.last_detected_text != previous_text:
            previous_text = snapshot.last_detected_text
            confidence = (
                f"{snapshot.ocr_confidence:.1%}"
                if snapshot.ocr_confidence is not None
                else "unknown"
            )
            print(
                f"OCR [{confidence}, {snapshot.ocr_ms:.1f} ms]: "
                f"{snapshot.last_detected_text}",
                flush=True,
            )
        if (
            snapshot.last_translation
            and snapshot.last_translation != previous_translation
        ):
            previous_translation = snapshot.last_translation
            print(
                f"PL [{snapshot.translation_ms or 0:.1f} ms]: "
                f"{snapshot.last_translation}",
                flush=True,
            )
        if snapshot.last_spoken_text and snapshot.last_spoken_text != previous_spoken:
            previous_spoken = snapshot.last_spoken_text
            print(
                f"AUDIO [{snapshot.total_capture_to_audio_start_ms or 0:.1f} ms]: "
                f"{snapshot.last_spoken_text}",
                flush=True,
            )
        if snapshot.status.value == "error":
            print(snapshot.message, file=sys.stderr, flush=True)
            app.exit(3)

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(poll)
    timer.start()
    QTimer.singleShot(max(1, values.seconds) * 1000, app.quit)
    try:
        pipeline.start(settings)
        print("Select the game window or monitor in the system portal.", flush=True)
        return app.exec()
    finally:
        pipeline.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
