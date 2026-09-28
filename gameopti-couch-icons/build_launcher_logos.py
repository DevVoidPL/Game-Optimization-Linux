#!/usr/bin/env python3
"""Prepare the launcher logos supplied by the project owner (GrafikiProgramu/).

Sources are never modified. Output: 512 px (longest side) PNGs with a real
alpha channel in src/.../assets/ui-icons/couch/launchers/{steam,heroic,lutris}.png.

* Steam  - SteamLogo.png already has alpha; it is only trimmed and downscaled.
* Heroic - HerocicLauncherLogo.png has a pure #000000 background, but the
           shield's own outline and sword are near-black (~#090A0C). Only
           pure-black pixels *connected to the image border* are removed
           (flood fill), so the logo's dark parts stay; the 1-2 px transition
           between background and outline gets a proportional alpha so the
           edge stays smooth without a halo.
* Lutris - LutrisLogo.png has a white background; the same connected flood
           fill removes near-white background, and anti-aliased fringe pixels
           are un-blended from white (colour-to-alpha) so no white halo stays.

Usage:  python3 build_launcher_logos.py            (writes PNGs)
        python3 build_launcher_logos.py --sheet    (also writes a preview sheet)
"""

from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "GrafikiProgramu"
OUT = ROOT / "src" / "game_optimization_linux" / "assets" / "ui-icons" / "couch" / "launchers"
SHEET = Path(__file__).resolve().parent / "launcher-logos-sheet.png"
SIZE = 512


def _qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])


def _rgba(image):
    from PySide6.QtGui import QImage

    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = converted.width(), converted.height()
    stride = converted.bytesPerLine()
    raw = bytes(converted.constBits())
    pixels = bytearray()
    for y in range(height):
        pixels += raw[y * stride:y * stride + width * 4]
    return width, height, pixels


def _image(width, height, pixels):
    from PySide6.QtGui import QImage

    image = QImage(bytes(pixels), width, height, width * 4, QImage.Format.Format_RGBA8888)
    return image.copy()


def _flood_background(width, height, pixels, is_background):
    """Mask of background pixels connected to the image border."""

    mask = bytearray(width * height)
    queue = deque()
    for x in range(width):
        queue.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        queue.extend(((0, y), (width - 1, y)))
    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if mask[index]:
            continue
        offset = index * 4
        if not is_background(pixels[offset], pixels[offset + 1], pixels[offset + 2]):
            continue
        mask[index] = 1
        if x > 0: queue.append((x - 1, y))
        if x < width - 1: queue.append((x + 1, y))
        if y > 0: queue.append((x, y - 1))
        if y < height - 1: queue.append((x, y + 1))
    return mask


def _edge_band(width, height, mask):
    """Opaque pixels touching the removed background (the anti-aliased rim)."""

    band = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if mask[index]:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height and mask[ny * width + nx]:
                    band[index] = 1
                    break
    return band


def remove_black_background(width, height, pixels, outline=(9, 10, 12)):
    mask = _flood_background(width, height, pixels, lambda r, g, b: max(r, g, b) <= 2)
    band = _edge_band(width, height, mask)
    top = max(outline)
    for index in range(width * height):
        offset = index * 4
        if mask[index]:
            pixels[offset:offset + 4] = b"\0\0\0\0"
        elif band[index]:
            value = max(pixels[offset:offset + 3])
            if value < top:  # blend of black and the dark outline
                pixels[offset:offset + 4] = bytes((*outline, round(255 * value / top)))
    return pixels


def remove_white_background(width, height, pixels):
    mask = _flood_background(width, height, pixels, lambda r, g, b: min(r, g, b) >= 250)
    band = _edge_band(width, height, mask)
    for index in range(width * height):
        offset = index * 4
        if mask[index]:
            pixels[offset:offset + 4] = b"\0\0\0\0"
        elif band[index]:
            r, g, b = pixels[offset:offset + 3]
            alpha = max(255 - r, 255 - g, 255 - b)  # colour-to-alpha from white
            if 0 < alpha < 255:
                unblend = [max(0, min(255, round(255 - (255 - c) * 255 / alpha))) for c in (r, g, b)]
                pixels[offset:offset + 4] = bytes((*unblend, alpha))
    return pixels


def _trim_and_scale(image):
    from PySide6.QtCore import QRect, Qt

    width, height, pixels = _rgba(image)
    xs = [x for y in range(height) for x in range(width) if pixels[(y * width + x) * 4 + 3] > 8]
    ys = [y for y in range(height) for x in range(width) if pixels[(y * width + x) * 4 + 3] > 8]
    box = QRect(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
    return image.copy(box).scaled(SIZE, SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)


def _drop_specks(image, keep_box_from_colour=True):
    """Crop to the saturated logo body (+ outline margin) to drop corner specks."""

    from PySide6.QtCore import QRect

    width, height, pixels = _rgba(image)
    xs, ys = [], []
    for y in range(height):
        for x in range(width):
            o = (y * width + x) * 4
            r, g, b = pixels[o:o + 3]
            if max(r, g, b) - min(r, g, b) > 60:
                xs.append(x); ys.append(y)
    margin = 24
    left, top = max(0, min(xs) - margin), max(0, min(ys) - margin)
    right, bottom = min(width - 1, max(xs) + margin), min(height - 1, max(ys) + margin)
    return image.copy(QRect(left, top, right - left + 1, bottom - top + 1))


def build() -> dict[str, Path]:
    from PySide6.QtGui import QImage

    _qt()
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}

    steam = QImage(str(SOURCE / "SteamLogo.png"))
    results["steam"] = _trim_and_scale(steam)

    heroic = _drop_specks(QImage(str(SOURCE / "HerocicLauncherLogo.png")))
    width, height, pixels = _rgba(heroic)
    results["heroic"] = _trim_and_scale(_image(width, height, remove_black_background(width, height, pixels)))

    lutris = QImage(str(SOURCE / "LutrisLogo.png"))
    width, height, pixels = _rgba(lutris)
    results["lutris"] = _trim_and_scale(_image(width, height, remove_white_background(width, height, pixels)))

    written = {}
    for name, image in results.items():
        path = OUT / f"{name}.png"
        assert image.save(str(path)), path
        written[name] = path
    return written


def render_sheet(paths: dict[str, Path]) -> None:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    sizes = (18, 24, 32, 64)
    backgrounds = ("#0B1018", "#151D29", "#F3F6FA", "#FFFFFF")
    cell = 96
    image = QImage(cell * len(sizes) * len(backgrounds), cell * len(paths), QImage.Format.Format_ARGB32)
    image.fill(QColor("#808080"))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
    for row, (name, path) in enumerate(paths.items()):
        logo = QImage(str(path))
        for b, background in enumerate(backgrounds):
            for s, size in enumerate(sizes):
                x = (b * len(sizes) + s) * cell
                painter.fillRect(QRectF(x, row * cell, cell, cell), QColor(background))
                scaled = logo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                painter.drawImage(int(x + (cell - scaled.width()) / 2), int(row * cell + (cell - scaled.height()) / 2), scaled)
    painter.end()
    image.save(str(SHEET))


if __name__ == "__main__":
    written = build()
    for name, path in written.items():
        print(f"{name}: {path}")
    if "--sheet" in sys.argv:
        render_sheet(written)
        print(f"sheet: {SHEET}")
