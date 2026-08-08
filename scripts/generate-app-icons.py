#!/usr/bin/env python3
"""Generate hand-tuned small App Icon Mark variants and a contact sheet."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen


APP_ID = "io.github.DevVoidPL.GameOptimizationLinux"
PROJECT = Path(__file__).resolve().parent.parent
FULL_ICON = PROJECT / "src" / "game_optimization_linux" / "resources" / "GameOptimizationLinuxIcon.png"
RESOURCE_DIR = PROJECT / "src" / "game_optimization_linux" / "resources" / "app-icons"
HICOLOR_DIR = PROJECT / "data" / "icons" / "hicolor"
CONTACT_SHEET = PROJECT / "reports" / "ui" / "game-optimization-icon-contact-sheet.png"

# Each small size has its own final-pixel geometry.  These are not reductions
# of the detailed sidebar logo.
SMALL_SPECS = {
    16: {"margin": 0.7, "radius": 3.8, "inset": 3.2, "stroke": 2.25},
    22: {"margin": 1.0, "radius": 5.0, "inset": 4.2, "stroke": 3.0},
    24: {"margin": 1.0, "radius": 5.5, "inset": 4.5, "stroke": 3.2},
    32: {"margin": 1.3, "radius": 7.0, "inset": 6.0, "stroke": 4.2},
    48: {"margin": 1.8, "radius": 10.5, "inset": 8.5, "stroke": 6.2},
}
LARGE_SIZES = (64, 128, 256)


def draw_mark(size: int) -> QImage:
    spec = SMALL_SPECS[size]
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    margin = spec["margin"]
    canvas = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.setPen(QPen(QColor("#47dcb0"), max(0.8, size / 34.0)))
    painter.setBrush(QColor("#0b3f35"))
    painter.drawRoundedRect(canvas, spec["radius"], spec["radius"])

    inset = spec["inset"]
    glyph = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    pen = QPen(QColor("#74f3c2"), spec["stroke"])
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    # A broad G with a deliberate right-side opening stays legible at 16 px.
    painter.drawArc(glyph, 42 * 16, 276 * 16)
    center_y = size * 0.53
    painter.drawLine(
        int(size * 0.51),
        int(center_y),
        int(size - inset * 0.72),
        int(center_y),
    )
    painter.end()
    return image


def write_icon(image: QImage, size: int) -> None:
    resource_target = RESOURCE_DIR / f"{size}x{size}.png"
    hicolor_target = HICOLOR_DIR / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
    resource_target.parent.mkdir(parents=True, exist_ok=True)
    hicolor_target.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(resource_target), "PNG"):
        raise RuntimeError(f"could not save {resource_target}")
    shutil.copyfile(resource_target, hicolor_target)


def make_contact_sheet(sizes: tuple[int, ...]) -> None:
    cell_width = 205
    sheet = QImage(cell_width * len(sizes), 330, QImage.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#111722"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QColor("#e9eef6"))
    title_font = QFont("Sans Serif", 15, QFont.Bold)
    painter.setFont(title_font)
    painter.drawText(QRectF(0, 8, sheet.width(), 28), Qt.AlignCenter, "Game Optimization App Icon Mark · native and pixel zoom")
    label_font = QFont("Sans Serif", 11, QFont.Bold)
    painter.setFont(label_font)
    for index, size in enumerate(sizes):
        x = index * cell_width
        icon = QImage(str(RESOURCE_DIR / f"{size}x{size}.png"))
        painter.setPen(QColor("#9eabc0"))
        painter.drawText(QRectF(x, 42, cell_width, 24), Qt.AlignCenter, f"{size} × {size}")
        painter.drawImage(
            int(x + (cell_width - size) / 2),
            70,
            icon,
        )
        zoom = min(8, 176 // size)
        zoomed = icon.scaled(
            QSize(size * zoom, size * zoom),
            Qt.KeepAspectRatio,
            Qt.FastTransformation,
        )
        painter.drawImage(
            int(x + (cell_width - zoomed.width()) / 2),
            135,
            zoomed,
        )
        painter.setPen(QColor("#627087"))
        painter.drawRect(QRectF(x + 0.5, 39.5, cell_width - 1, 278))
    painter.end()
    CONTACT_SHEET.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(CONTACT_SHEET), "PNG"):
        raise RuntimeError(f"could not save {CONTACT_SHEET}")


def main() -> int:
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    if not FULL_ICON.is_file():
        raise FileNotFoundError(FULL_ICON)
    for size in SMALL_SPECS:
        write_icon(draw_mark(size), size)
    full = QImage(str(FULL_ICON))
    if full.isNull():
        raise RuntimeError(f"could not decode {FULL_ICON}")
    for size in LARGE_SIZES:
        write_icon(
            full.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation),
            size,
        )
    make_contact_sheet(tuple(SMALL_SPECS))
    del application
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
