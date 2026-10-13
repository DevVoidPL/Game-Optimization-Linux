#!/usr/bin/env python3
"""Generate the Couch Mode glyph set (single source of geometry).

Design language
* 24x24 grid, 2 px live-area padding, stroke 2, round caps and joins.
* Two tones: a neutral base and one accent detail. Status glyphs use the
  semantic tone as a filled disc/triangle with a contrasting mark.
* Two files per glyph: ``<name>.svg`` for dark surfaces and
  ``<name>-on-light.svg`` for light surfaces. No CSS, no currentColor, no
  text, no filters: plain Qt 6 SVG Tiny compatible paths.

Usage:  python3 build_home_glyphs.py            (writes SVGs)
        python3 build_home_glyphs.py --sheet    (also renders the contact sheet)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "game_optimization_linux" / "assets" / "ui-icons" / "couch" / "glyphs"
SHEET = Path(__file__).resolve().parent / "couch-glyphs-contact-sheet.png"

PALETTES = {
    "dark": {"base": "#DCE6EF", "accent": "#61E6B6", "ink": "#0B1018",
             "success": "#62D993", "warning": "#F2C260", "danger": "#FF7A86"},
    "light": {"base": "#243242", "accent": "#168C68", "ink": "#FFFFFF",
              "success": "#168954", "warning": "#A86808", "danger": "#C43D4D"},
}


def stroke(d: str, color: str, width: float = 2.0) -> str:
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def fill(d: str, color: str) -> str:
    return f'<path d="{d}" fill="{color}"/>'


def circle(cx, cy, r, *, fill_color="none", stroke_color=None, width=2.0) -> str:
    s = f' stroke="{stroke_color}" stroke-width="{width}"' if stroke_color else ""
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill_color}"{s}/>'


def rect(x, y, w, h, rx, *, fill_color="none", stroke_color=None, width=2.0) -> str:
    s = (f' stroke="{stroke_color}" stroke-width="{width}" stroke-linejoin="round"'
         if stroke_color else "")
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill_color}"{s}/>'


def gear_path(cx=12.0, cy=12.0, outer=9.0, inner=6.9, teeth=8) -> str:
    points = []
    step = 2 * math.pi / teeth
    for i in range(teeth):
        a = i * step - math.pi / 2
        for offset, radius in ((-0.30, inner), (-0.17, outer), (0.17, outer), (0.30, inner)):
            angle = a + offset * step * 1.35
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in points) + " Z"


CYLINDER_TOP = "M4 7c0-1.66 3.58-3 8-3s8 1.34 8 3-3.58 3-8 3-8-1.34-8-3Z"
CYLINDER_BODY = "M4 7v10c0 1.66 3.58 3 8 3s8-1.34 8-3V7"
CYCLE_ARCS = "M19.6 9.2A8.2 8.2 0 0 0 5.4 7.3M4.4 14.8a8.2 8.2 0 0 0 14.2 1.9"
CYCLE_HEADS = "M5.2 3.6v3.9h3.9M18.8 20.4v-3.9h-3.9"
DISPLAY_STAND = "M9 20.5h6M12 17v3.5"


def glyphs(p: dict[str, str]) -> dict[str, str]:
    b, a, ink = p["base"], p["accent"], p["ink"]
    sliders = (stroke("M4 8h4M13.2 8H20M4 16h9M18.2 16H20", b)
               + circle(10.6, 8, 2.6, stroke_color=a) + circle(15.6, 16, 2.6, stroke_color=a))
    return {
        # ---- Home screen ---------------------------------------------------
        "launch": fill("M8.2 5.1c0-.92 1-1.5 1.8-1.02l9.1 5.9c.72.47.72 1.53 0 2l-9.1 5.9c-.8.5-1.8-.1-1.8-1.02Z", a),
        "details": sliders,
        "more": circle(5.5, 12, 1.9, fill_color=b) + circle(12, 12, 1.9, fill_color=a) + circle(18.5, 12, 1.9, fill_color=b),
        "library": (rect(4, 4, 6.8, 6.8, 1.8, stroke_color=a) + rect(13.2, 4, 6.8, 6.8, 1.8, stroke_color=b)
                    + rect(4, 13.2, 6.8, 6.8, 1.8, stroke_color=b) + rect(13.2, 13.2, 6.8, 6.8, 1.8, stroke_color=b)),
        "tasks": (stroke("M11 6.5h9M11 12h9M11 17.5h9", b)
                  + stroke("M4 6.6l1.5 1.5L8 5.3M4 12.1l1.5 1.5L8 10.8M4 17.6l1.5 1.5L8 16.3", a)),
        "updates": (stroke(CYCLE_ARCS, b) + stroke(CYCLE_HEADS, a)
                    + stroke("M10.2 9.4h2.4l1.4 1.4v3.8h-3.8Z", a, 1.6)),
        "settings": stroke(gear_path(), b) + circle(12, 12, 2.6, stroke_color=a),
        "controller": (stroke("M7.6 7h8.8a4.4 4.4 0 0 1 4.3 3.5l.95 4.9a2.3 2.3 0 0 1-3.95 2l-2.1-2.3H8.4l-2.1 2.3a2.3 2.3 0 0 1-3.95-2l.95-4.9A4.4 4.4 0 0 1 7.6 7Z", b)
                       + stroke("M8 9.9v4M6 11.9h4", a)
                       + circle(15.6, 10.6, 1.15, fill_color=a) + circle(17.8, 12.9, 1.15, fill_color=b)),
        "keyboard": (rect(2.8, 6.2, 18.4, 11.6, 2.4, stroke_color=b)
                     + stroke("M6.5 9.8h.01M9.5 9.8h.01M12.5 9.8h.01M15.5 9.8h.01M18 9.8h.01", b, 2.2)
                     + stroke("M8 14h8", a)),
        "source": (stroke("M12 3.4 19.6 7.6v8.8L12 20.6l-7.6-4.2V7.6Z", b)
                   + stroke("M4.6 7.7 12 11.9l7.4-4.2M12 11.9v8.5", a)),
        "status-ready": circle(12, 12, 9.2, fill_color=p["success"]) + stroke("M8 12.3l2.7 2.7L16.2 9.3", ink, 2.2),
        "status-warning": (fill("M10.3 4.1a2 2 0 0 1 3.4 0l7.5 13a2 2 0 0 1-1.7 3H4.5a2 2 0 0 1-1.7-3Z", p["warning"])
                           + stroke("M12 9v4.4", ink, 2.2) + circle(12, 16.6, 1.25, fill_color=ink)),
        "status-error": circle(12, 12, 9.2, fill_color=p["danger"]) + stroke("M9 9l6 6M15 9l-6 6", ink, 2.2),
        "previous": stroke("M14.5 5.5 8 12l6.5 6.5", b, 2.4),
        "next": stroke("M9.5 5.5 16 12l-6.5 6.5", b, 2.4),
        # ---- Game details: features ----------------------------------------
        "gamemode": (rect(6, 6, 12, 12, 2.4, stroke_color=b)
                     + stroke("M9.5 3v3M14.5 3v3M9.5 18v3M14.5 18v3M3 9.5h3M3 14.5h3M18 9.5h3M18 14.5h3", b)
                     + fill("M12.9 8.2 9.6 12.6h2.3l-.8 3.2 3.3-4.4h-2.3Z", a)),
        "gamescope": (rect(3, 4.5, 18, 12.5, 2.2, stroke_color=b) + stroke(DISPLAY_STAND, b)
                      + rect(7.5, 8, 9, 5.5, 1.2, stroke_color=a)),
        "mangohud": (rect(3, 4.5, 18, 12.5, 2.2, stroke_color=b) + stroke(DISPLAY_STAND, b)
                     + rect(5.6, 7, 6.6, 4.6, 1.2, fill_color=a) + stroke("M14.5 13.5h3.5", b)),
        "optiscaler": rect(3.5, 3.5, 17, 17, 3, stroke_color=b) + stroke("M8 16 16 8M11 8h5v5", a),
        "narrator": (stroke("M5 5h14a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-7l-4.5 3.5v-3.5H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z", b)
                     + stroke("M8 10.7v1.6M11 9v5M14 8v7M17 10.2v2.6", a)),
        # ---- Game details: tabs ----------------------------------------------
        "overview": rect(4, 4, 16, 16, 2.6, stroke_color=b) + stroke("M8 9h8", a) + stroke("M8 13h8M8 16.5h5", b),
        "storage": stroke(CYLINDER_TOP, b) + stroke(CYLINDER_BODY, b) + stroke("M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3", a),
        "optimization": (stroke("M4.3 16.8a8.4 8.4 0 1 1 15.4 0", b)
                         + stroke("M12 14.2l3.6-4.6", a) + circle(12, 14.2, 1.7, fill_color=a)),
        # ---- Game details: actions -------------------------------------------
        "analyze": circle(10.5, 10.5, 6.2, stroke_color=b) + stroke("M15.2 15.2 20 20", a),
        "verify-compression": stroke(CYLINDER_TOP, b) + stroke(CYLINDER_BODY, b) + stroke("M8.6 14.2l2.3 2.3 4.6-4.8", a),
        "compression-profile": (stroke("M12 4 3.5 8.5 12 13l8.5-4.5Z", b) + stroke("M3.5 12.5 12 17l8.5-4.5", a)
                                + stroke("M3.5 16.3 12 20.8l8.5-4.5", b)),
        "compress": stroke("M4 12h16", b) + stroke("M12 3v6M9 6l3 3 3-3M12 21v-6M9 18l3-3 3 3", a),
        "install": stroke("M4 14.5V18a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3.5", b) + stroke("M12 4v10.5M8 10.5l4 4 4-4", a),
        "download": (stroke("M7 17.5a4 4 0 0 1-.7-7.94A5.6 5.6 0 0 1 17.2 8.3a3.8 3.8 0 0 1 .3 7.6", b)
                     + stroke("M12 11v9M9 17l3 3 3-3", a)),
        "verify": (stroke("M12 3.2 19 6v5.6c0 4.3-2.9 7.7-7 9.2-4.1-1.5-7-4.9-7-9.2V6Z", b)
                   + stroke("M8.8 12.2l2.2 2.2 4.4-4.6", a)),
        "remove": (stroke("M4 7h16M9.5 7V4.6h5V7", b)
                   + stroke("M6.5 7l.9 12a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5l.9-12", b)
                   + stroke("M10.2 11v5.5M13.8 11v5.5", a)),
        "refresh": stroke(CYCLE_ARCS, b) + stroke(CYCLE_HEADS, a),
        "tune": sliders,
        "subtitles": rect(3, 5, 18, 14, 2.6, stroke_color=b) + stroke("M7 12.5h5M14.5 12.5H17M7 15.5h2.5M12 15.5h5", a),
        "scan-source": (stroke("M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16", b)
                        + stroke("M4 12h16", a)),
        "capture": rect(3, 5, 18, 14, 2.6, stroke_color=b) + stroke("M3 9h18", b) + circle(12, 14, 2.4, fill_color=a),
        "voice": (circle(9, 8, 3.2, stroke_color=b) + stroke("M3.5 19.5a5.5 5.5 0 0 1 11 0", b)
                  + stroke("M17 8.5a4 4 0 0 1 0 5M19.6 6a7.6 7.6 0 0 1 0 10", a)),
        "volume": stroke("M4 9.5h3l4.5-4v13L7 14.5H4Z", b) + stroke("M15 9a4 4 0 0 1 0 6M17.8 6.5a7.6 7.6 0 0 1 0 11", a),
        "speech-rate": circle(12, 13.2, 7.4, stroke_color=b) + stroke("M10 3h4M12 3v2.4", b) + stroke("M12 13.2l3.2-3.2", a),
        "region": (f'<rect x="3.5" y="5.5" width="17" height="13" rx="2" fill="none" stroke="{b}" '
                   'stroke-width="2" stroke-dasharray="3 2.6" stroke-linecap="round"/>' + stroke("M8 14.5h8", a)),
        "start": circle(12, 12, 9, stroke_color=b) + fill("M10.2 8.4v7.2c0 .5.55.8.97.53l5.5-3.6a.63.63 0 0 0 0-1.06l-5.5-3.6a.63.63 0 0 0-.97.53Z", a),
        "stop": circle(12, 12, 9, stroke_color=b) + rect(8.6, 8.6, 6.8, 6.8, 1.4, fill_color=a),
        "info": circle(12, 12, 9, stroke_color=b) + stroke("M12 11v5.4", a) + circle(12, 7.8, 1.25, fill_color=a),
        # ---- GameMode / Gamescope menus ----------------------------------------
        "toggle-on": rect(2.5, 7, 19, 10, 5, stroke_color=b) + circle(16.5, 12, 3.2, fill_color=a),
        "toggle-off": rect(2.5, 7, 19, 10, 5, stroke_color=b) + circle(7.5, 12, 3.2, stroke_color=b),
        "resolution-game": (rect(4, 6, 16, 12, 2, stroke_color=b)
                            + stroke("M8.5 10h.01M12 10h.01M15.5 10h.01M8.5 14h.01M12 14h.01M15.5 14h.01", a, 2.4)),
        "resolution-output": (rect(3, 4.5, 18, 12.5, 2.2, stroke_color=b) + stroke(DISPLAY_STAND, b)
                              + stroke("M7 10V7.5h2.5M17 10V7.5h-2.5M7 11.5V14h2.5M17 11.5V14h-2.5", a, 1.8)),
        "fit": rect(3, 5, 18, 14, 2.2, stroke_color=b) + rect(6.5, 8.5, 11, 7, 1.2, stroke_color=a),
        "filter": stroke("M4 5h16l-6 7.2V18l-4 2v-7.8Z", b) + stroke("M9.5 9h5", a),
        "sharpness": stroke("M12 3.5 20.5 19.5h-17Z", b) + fill("M12 6.6v11.4H5.9Z", a),
        "fps": rect(7.5, 4.5, 12.5, 10, 2, stroke_color=b) + rect(4, 9.5, 12.5, 10, 2, stroke_color=a),
        "vrr": (rect(3, 4.5, 18, 12.5, 2.2, stroke_color=b) + stroke(DISPLAY_STAND, b)
                + stroke("M6.5 11.2c1.2-2.4 2.4-2.4 3.6 0s2.4 2.4 3.6 0 2.4-2.4 3.6 0", a)),
        "windowed": (rect(3, 5, 18, 14, 2.2, stroke_color=b) + stroke("M3 9h18", b)
                     + stroke("M6 7h.01M8.6 7h.01", a, 2.2)),
        "borderless": rect(3, 5, 18, 14, 2.2, stroke_color=b) + rect(6, 8, 12, 8, 1.2, fill_color=a),
        "fullscreen": stroke("M3.5 8.5v-5h5M15.5 3.5h5v5M20.5 15.5v5h-5M8.5 20.5h-5v-5", a) + rect(8, 8, 8, 8, 1.4, stroke_color=b),
        "grab": (rect(2.8, 10, 18.4, 9.5, 2.2, stroke_color=b)
                 + stroke("M6.5 13.6h.01M9.5 13.6h.01M12.5 13.6h.01M15.5 13.6h.01M8 16.6h8", b, 2)
                 + stroke("M9.3 10V7.4a2.7 2.7 0 0 1 5.4 0V10", a)),
        "hdr": (circle(12, 12, 4.2, stroke_color=b) + fill("M12 7.8a4.2 4.2 0 0 1 0 8.4Z", a)
                + stroke("M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4", a)),
        "save": circle(12, 12, 9, stroke_color=b) + stroke("M8 12.4l2.8 2.8 5.4-5.8", a, 2.2),
        "cancel": circle(12, 12, 9, stroke_color=b) + stroke("M9 9l6 6M15 9l-6 6", b, 2.2),
        # ---- MangoHud menu -------------------------------------------------------
        "preset": (rect(4, 3.8, 16, 4.4, 1.6, stroke_color=b) + rect(4, 9.8, 16, 4.4, 1.6, stroke_color=a)
                   + rect(4, 15.8, 16, 4.4, 1.6, stroke_color=b)),
        "position": (rect(3, 4.5, 18, 15, 2.2, stroke_color=b) + rect(5.6, 7.1, 5.4, 3.6, 0.9, fill_color=a)
                     + stroke("M17.5 8.9h.01M6.5 16.6h.01M17.5 16.6h.01", b, 2.2)),
        "text-size": rect(3.5, 12, 6.5, 7.5, 1.2, stroke_color=b) + rect(12, 4.5, 8.5, 15, 1.6, stroke_color=a),
        "fps-limit": stroke("M4 6.5h16", a) + stroke("M7 19.5v-7M12 19.5v-10M17 19.5v-5", b),
        "frametime": stroke("M4 4v16h16", b) + stroke("M7 14.5l3-5 3 4 3-7 3 5", a),
        "usage": (rect(5, 5, 14, 14, 2.4, stroke_color=b) + stroke("M9 2.8V5M15 2.8V5M9 19v2.2M15 19v2.2", b)
                  + stroke("M9 15.5v-3M12 15.5V9M15 15.5v-4.5", a)),
        "temperature": (stroke("M10 14.6V5.2a2 2 0 0 1 4 0v9.4a4 4 0 1 1-4 0Z", b)
                        + stroke("M12 9.5v6.5", a) + circle(12, 17.6, 1.8, fill_color=a)),
        "memory": (rect(3, 7, 18, 9, 1.6, stroke_color=b) + stroke("M6.5 16v3M10.2 16v3M13.8 16v3M17.5 16v3", b)
                   + stroke("M7 10.2v2.6M11 10.2v2.6M15 10.2v2.6", a)),
        # ---- Library -------------------------------------------------------------
        # Search differs from "analyze" by a neutral handle and an accent glint.
        "search": (circle(10.5, 10.5, 6.2, stroke_color=b) + stroke("M15.2 15.2 20 20", b, 2.4)
                   + stroke("M7.7 9.4a3.1 3.1 0 0 1 2.3-1.9", a)),
        "sort": stroke("M4 6h9M4 12h6.5M4 18h4", b) + stroke("M17.5 5v14M14.5 16l3 3 3-3", a),
        # A drive with an unplugged cable: the library is offline, not deleted.
        "library-offline": (rect(3, 4.5, 18, 9.5, 2.2, stroke_color=b) + stroke("M7 9.25h5", b)
                            + circle(16.5, 9.25, 1.3, fill_color=p["warning"])
                            + stroke("M12 14v2.2M12 19.6v1.4", b) + stroke("M9 20.4l6-4.4", p["warning"])),
        # ---- On-screen keyboard ----------------------------------------------------
        "backspace": (stroke("M9 5.5h10a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-6-6.5Z", b)
                      + stroke("M11.8 9.5l5 5M16.8 9.5l-5 5", a)),
    }


def svg(body: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'width="24" height="24">{body}</svg>\n')


def write() -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    dark, light = glyphs(PALETTES["dark"]), glyphs(PALETTES["light"])
    for name in dark:
        (OUT / f"{name}.svg").write_text(svg(dark[name]), encoding="utf-8")
        (OUT / f"{name}-on-light.svg").write_text(svg(light[name]), encoding="utf-8")
    return list(dark)


def render_sheet(names: list[str]) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication([])
    sizes = (24, 32, 48)
    cell_w, row_h, label_w, pad = 64, 64, 150, 16
    panels = (("dark", "#0B1018"), ("dark", "#151D29"), ("light", "#F3F6FA"), ("light", "#FFFFFF"))
    rows = (len(names) + 1) // 2
    block_w = label_w + len(panels) * len(sizes) * cell_w
    width = pad * 3 + block_w * 2
    height = 56 + rows * row_h + pad
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#1A2230"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(QFont("Sans", 10, QFont.Bold))
    for block in range(2):
        origin = pad + block * (block_w + pad)
        x = origin + label_w
        for theme, bg in panels:
            for size in sizes:
                painter.fillRect(QRectF(x, 0, cell_w, height), QColor(bg))
                painter.setPen(QColor("#8793A2"))
                painter.drawText(QRectF(x, 6, cell_w, 20), Qt.AlignCenter, theme)
                painter.drawText(QRectF(x, 24, cell_w, 20), Qt.AlignCenter, str(size))
                x += cell_w
    for index, name in enumerate(names):
        block, row = divmod(index, rows)
        origin = pad + block * (block_w + pad)
        y = 56 + row * row_h
        painter.setPen(QColor("#E6ECF2"))
        painter.drawText(QRectF(origin, y, label_w - 8, row_h), Qt.AlignVCenter | Qt.AlignLeft, name)
        x = origin + label_w
        for theme, _bg in panels:
            file = OUT / (f"{name}.svg" if theme == "dark" else f"{name}-on-light.svg")
            renderer = QSvgRenderer(str(file))
            assert renderer.isValid(), file
            for size in sizes:
                renderer.render(painter, QRectF(x + (cell_w - size) / 2, y + (row_h - size) / 2, size, size))
                x += cell_w
    painter.end()
    image.save(str(SHEET))
    del app


if __name__ == "__main__":
    written = write()
    print(f"wrote {len(written) * 2} SVGs to {OUT}")
    if "--sheet" in sys.argv:
        render_sheet(written)
        print(f"contact sheet: {SHEET}")
