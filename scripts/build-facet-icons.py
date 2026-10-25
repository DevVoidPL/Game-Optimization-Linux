#!/usr/bin/env python3
"""GameOpti "Facet" icon set: solid silhouettes split by one diagonal light cut.

Every icon is drawn on a 24 x 24 grid as filled planes (no strokes). One
slanted gap splits the silhouette into a body plane and a mint accent plane,
echoing the faceted hexagon of the GameOpti brand symbol. Geometry is authored
here as polygons; the gap is produced by clipping, so the output uses only
plain <path> elements with fill-rule="evenodd" (no masks, filters, text or
currentColor), which Qt's SVG renderer handles exactly.

Usage:
    python3 scripts/build-facet-icons.py          # write SVGs + manifest
    python3 scripts/build-facet-icons.py --check  # verify packaged files match
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/game_optimization_linux/assets/ui-icons"

# Palettes: body plane, accent plane.
DARK = ("#CFDDE6", "#4FE3B8")
LIGHT = ("#24313F", "#128A66")
STATUS_DARK = {
    "success": ("#34D399", "#9FF2CB"),
    "warning": ("#F5C451", "#FFE3A1"),
    "error": ("#F2685F", "#FFB3AC"),
    "info": ("#5B9DF5", "#B2D1FC"),
}
STATUS_LIGHT = {
    "success": ("#168954", "#4FC48D"),
    "warning": ("#A86808", "#E0A43C"),
    "error": ("#C43D4D", "#F08A95"),
    "info": ("#277DC1", "#6FB3EC"),
}

GAP = 1.5          # width of the light cut in grid units
Point = tuple[float, float]


# --------------------------------------------------------------------------
# Geometry primitives (all return a polygon: list of points, counter-clockwise)
# --------------------------------------------------------------------------

def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int) -> list[Point]:
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def circle(cx: float, cy: float, r: float, n: int = 48) -> list[Point]:
    return _arc(cx, cy, r, 0, 360, n)[:-1]


def rect(x: float, y: float, w: float, h: float, r: float = 0.0) -> list[Point]:
    if r <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    r = min(r, w / 2, h / 2)
    pts: list[Point] = []
    pts += _arc(x + w - r, y + r, r, -90, 0, 6)
    pts += _arc(x + w - r, y + h - r, r, 0, 90, 6)
    pts += _arc(x + r, y + h - r, r, 90, 180, 6)
    pts += _arc(x + r, y + r, r, 180, 270, 6)
    return pts


def capsule(x1: float, y1: float, x2: float, y2: float, w: float) -> list[Point]:
    """A thick line segment with round ends."""
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    r = w / 2
    return _arc(x1, y1, r, ang + 90, ang + 270, 10) + _arc(x2, y2, r, ang - 90, ang + 90, 10)


def band(cx: float, cy: float, r_out: float, r_in: float, a0: float, a1: float, n: int = 32) -> list[Point]:
    """Ring sector between two radii (angles in degrees, clockwise on screen)."""
    return _arc(cx, cy, r_out, a0, a1, n) + list(reversed(_arc(cx, cy, r_in, a0, a1, n)))


def ring(cx: float, cy: float, r_out: float, r_in: float) -> "Shape":
    return Shape(circle(cx, cy, r_out), holes=[circle(cx, cy, r_in)])


def tri(ax, ay, bx, by, cx, cy) -> list[Point]:
    return [(ax, ay), (bx, by), (cx, cy)]


def poly(*pts: float) -> list[Point]:
    return [(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)]


def arrow(x1, y1, x2, y2, w, head=3.2, head_w=None) -> list["Shape"]:
    """Shaft capsule + triangular head pointing from (x1,y1) to (x2,y2)."""
    head_w = head_w or head * 1.15
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    bx, by = x2 - ux * head, y2 - uy * head
    shaft = capsule(x1, y1, bx + ux * 0.4, by + uy * 0.4, w)
    tip = [(x2, y2), (bx + nx * head_w / 2, by + ny * head_w / 2), (bx - nx * head_w / 2, by - ny * head_w / 2)]
    return [Shape(shaft), Shape(tip)]


def chevron(cx, cy, size, w, direction: str) -> list[Point]:
    """V-shaped polygon with round joins approximated by two capsules merged."""
    s = size
    if direction == "right":
        a, b, c = (cx - s / 2, cy - s), (cx + s / 2, cy), (cx - s / 2, cy + s)
    elif direction == "left":
        a, b, c = (cx + s / 2, cy - s), (cx - s / 2, cy), (cx + s / 2, cy + s)
    elif direction == "up":
        a, b, c = (cx - s, cy + s / 2), (cx, cy - s / 2), (cx + s, cy + s / 2)
    else:
        a, b, c = (cx - s, cy - s / 2), (cx, cy + s / 2), (cx + s, cy - s / 2)
    return [capsule(*a, *b, w), capsule(*b, *c, w)]


def check(cx, cy, size, w) -> list[list[Point]]:
    s = size
    a, b, c = (cx - s, cy), (cx - s * 0.3, cy + s * 0.7), (cx + s, cy - s * 0.75)
    return [capsule(*a, *b, w), capsule(*b, *c, w)]


def cross(cx, cy, size, w) -> list[list[Point]]:
    s = size
    return [capsule(cx - s, cy - s, cx + s, cy + s, w), capsule(cx - s, cy + s, cx + s, cy - s, w)]


def star4(cx, cy, r, inner=0.38) -> list[Point]:
    pts = []
    for i in range(8):
        rad = r if i % 2 == 0 else r * inner
        a = math.radians(-90 + i * 45)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def gear(cx, cy, r_out, r_in, teeth=8, hole=0.0) -> "Shape":
    pts = []
    step = 360 / teeth
    for i in range(teeth):
        a = -90 + i * step
        for off, rad in ((-step * 0.24, r_in), (-step * 0.16, r_out), (step * 0.16, r_out), (step * 0.24, r_in)):
            pts.append((cx + rad * math.cos(math.radians(a + off)), cy + rad * math.sin(math.radians(a + off))))
    return Shape(pts, holes=[circle(cx, cy, hole)] if hole else [])


# --------------------------------------------------------------------------
# Shapes, the diagonal cut and SVG emission
# --------------------------------------------------------------------------

class Shape:
    """A filled polygon with optional holes and a plane assignment.

    plane: "split" (cut by the diagonal into body + accent), "body", "accent".
    """

    def __init__(self, outer: list[Point], holes: list[list[Point]] | None = None,
                 plane: str = "split", opacity: float = 1.0):
        self.outer = outer
        self.holes = holes or []
        self.plane = plane
        self.opacity = opacity

    def as_(self, plane: str, opacity: float = 1.0) -> "Shape":
        return Shape(self.outer, self.holes, plane, opacity)


def body(outer, holes=None, opacity=1.0) -> Shape:
    return Shape(outer, holes, "body", opacity)


def accent(outer, holes=None, opacity=1.0) -> Shape:
    return Shape(outer, holes, "accent", opacity)


def _clip_half(polygon: list[Point], px: float, py: float, nx: float, ny: float) -> list[Point]:
    """Sutherland-Hodgman clip against the half-plane n·(q - p) >= 0."""
    if not polygon:
        return []
    out: list[Point] = []
    prev = polygon[-1]
    prev_in = (prev[0] - px) * nx + (prev[1] - py) * ny >= 0
    for cur in polygon:
        cur_in = (cur[0] - px) * nx + (cur[1] - py) * ny >= 0
        if cur_in != prev_in:
            d_prev = (prev[0] - px) * nx + (prev[1] - py) * ny
            d_cur = (cur[0] - px) * nx + (cur[1] - py) * ny
            t = d_prev / (d_prev - d_cur)
            out.append((prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t))
        if cur_in:
            out.append(cur)
        prev, prev_in = cur, cur_in
    return out


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _path_d(polys: list[list[Point]]) -> str:
    parts = []
    for p in polys:
        if len(p) < 3:
            continue
        parts.append("M" + " ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in p) + "Z")
    return "".join(parts)


class Cut:
    """Diagonal light cut: a line through (cx, cy) at `angle` degrees.

    The accent plane lies on the side the normal points to (lower-right by
    default, like the mint facet of the brand symbol).
    """

    def __init__(self, cx: float = 12.0, cy: float = 12.0, angle: float = -58.0, gap: float = GAP):
        self.cx, self.cy, self.gap = cx, cy, gap
        rad = math.radians(angle)
        # Direction along the cut; the normal points to the accent side.
        dx, dy = math.cos(rad), math.sin(rad)
        self.nx, self.ny = -dy, dx
        if self.nx + self.ny < 0:  # keep accent on the lower-right half
            self.nx, self.ny = -self.nx, -self.ny

    def split(self, polygon: list[Point]) -> tuple[list[Point], list[Point]]:
        h = self.gap / 2
        bx, by = self.cx - self.nx * h, self.cy - self.ny * h
        ax, ay = self.cx + self.nx * h, self.cy + self.ny * h
        body_part = _clip_half(polygon, bx, by, -self.nx, -self.ny)
        accent_part = _clip_half(polygon, ax, ay, self.nx, self.ny)
        return body_part, accent_part


SAFE_MARGIN = 1.5


def check_bounds(title: str, shapes: list[Shape]) -> None:
    for shape in shapes:
        for x, y in shape.outer:
            if not (SAFE_MARGIN - 1e-6 <= x <= 24 - SAFE_MARGIN + 1e-6 and SAFE_MARGIN - 1e-6 <= y <= 24 - SAFE_MARGIN + 1e-6):
                raise ValueError(f"{title}: point ({x:.2f}, {y:.2f}) breaks the {SAFE_MARGIN}-unit safe margin")


def render_svg(title: str, shapes: list[Shape], palette: tuple[str, str], cut: Cut) -> str:
    check_bounds(title, shapes)
    body_c, accent_c = palette
    layers: list[tuple[str, float, list[list[Point]]]] = []

    def oriented(p: list[Point], positive: bool) -> list[Point]:
        area = sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))
        return p if (area > 0) == positive else list(reversed(p))

    def add(color: str, opacity: float, outer: list[Point], holes: list[list[Point]]) -> None:
        if len(outer) < 3:
            return
        polys = [oriented(outer, True)] + [oriented(h, False) for h in holes if len(h) >= 3]
        layers.append((color, opacity, polys))

    for shape in shapes:
        if shape.plane == "body":
            add(body_c, shape.opacity, shape.outer, shape.holes)
        elif shape.plane == "accent":
            add(accent_c, shape.opacity, shape.outer, shape.holes)
        else:
            b_outer, a_outer = cut.split(shape.outer)
            add(body_c, shape.opacity, b_outer, [cut.split(h)[0] for h in shape.holes])
            add(accent_c, shape.opacity, a_outer, [cut.split(h)[1] for h in shape.holes])

    # Merge consecutive layers with the same colour/opacity into one path.
    merged: list[tuple[str, float, list[list[Point]]]] = []
    for color, opacity, polys in layers:
        if merged and merged[-1][0] == color and merged[-1][1] == opacity:
            merged[-1][2].extend(polys)
        else:
            merged.append((color, opacity, list(polys)))

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill-rule="nonzero" stroke="none">',
        f"  <title>{title}</title>",
    ]
    for color, opacity, polys in merged:
        op = "" if opacity >= 1 else f' fill-opacity="{_fmt(opacity)}"'
        lines.append(f'  <path d="{_path_d(polys)}" fill="{color}"{op} />')
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Icon designs.  Each function returns a list of Shapes on the 24-grid.
# Default: shapes are "split" by the diagonal cut (body upper-left, accent
# lower-right).  Small details are pinned to a plane explicitly.
# --------------------------------------------------------------------------

def d_games() -> list[Shape]:
    # Game library: a wide gamepad body; the D-pad cross and two face buttons
    # are cut out so the silhouette reads as "game" at any size.
    g = (_arc(7.2, 9.4, 5.2, 180, 270, 6) + _arc(16.8, 9.4, 5.2, 270, 360, 6)
         + [(22.0, 16.4)] + _arc(19.2, 18.2, 3.0, 10, 120, 6) + [(15.3, 16.6), (8.7, 16.6)]
         + _arc(4.8, 18.2, 3.0, 60, 170, 6) + [(2.0, 16.4)])
    dpad = poly(6.6, 6.9, 8.6, 6.9, 8.6, 9.3, 11, 9.3, 11, 11.3, 8.6, 11.3, 8.6, 13.7, 6.6, 13.7, 6.6, 11.3, 4.2, 11.3, 4.2, 9.3, 6.6, 9.3)
    return [Shape(g, holes=[dpad, circle(15.4, 8.4, 1.35, 20), circle(18.2, 11.2, 1.35, 20)])], Cut(13.6, 11.4, -58)


def d_updates() -> list[Shape]:
    # Updates: a version badge (disc) with an upward "upgrade" arrow cut out.
    up = poly(12, 5.6, 17.6, 11.4, 14.1, 11.4, 14.1, 17.6, 9.9, 17.6, 9.9, 11.4, 6.4, 11.4)
    return [Shape(circle(12, 12, 9.8), holes=[up])], Cut(12, 12, -58)


def d_tasks() -> list[Shape]:
    # Clipboard: a board with a clip; two task rows done (accent), one pending.
    return [
        Shape(rect(3.5, 4, 17, 18, 2), holes=[rect(7, 9.5, 10, 2, 1), rect(7, 13.5, 10, 2, 1), rect(7, 17.5, 6.5, 2, 1)]),
        Shape(rect(8.5, 2, 7, 4.2, 1.4)),
    ], Cut(16, 16, -58)


def d_system() -> list[Shape]:
    # A processor die with three leads on each side.
    pins = []
    for i in range(3):
        o = 8 + i * 4
        pins += [rect(o - 1.1, 1.6, 2.2, 3.2, 0.6), rect(o - 1.1, 19.2, 2.2, 3.2, 0.6),
                 rect(1.6, o - 1.1, 3.2, 2.2, 0.6), rect(19.2, o - 1.1, 3.2, 2.2, 0.6)]
    return [Shape(rect(4.6, 4.6, 14.8, 14.8, 2.2), holes=[rect(9.2, 9.2, 5.6, 5.6, 1)]), *[Shape(p) for p in pins]]


def d_settings() -> list[Shape]:
    return [gear(12, 12, 10, 7.6, teeth=8, hole=3.4)]


def d_narrator() -> list[Shape]:
    # Lektor: a speech bubble whose interior is two subtitle lines; the tail
    # points down-left toward the "screen" the narrator reads from.
    r = 3.4
    bubble = (_arc(21.5 - r, 3 + r, r, -90, 0, 6) + _arc(21.5 - r, 16.4 - r, r, 0, 90, 6)
              + [(11.2, 16.4), (5.2, 21.6), (6.6, 16.4)]
              + _arc(2.5 + r, 16.4 - r, r, 90, 180, 6) + _arc(2.5 + r, 3 + r, r, 180, 270, 6))
    return [Shape(bubble, holes=[rect(6.5, 7, 11, 1.9, 0.95), rect(6.5, 10.6, 7.5, 1.9, 0.95)])], Cut(13.5, 10.5, -58)


def d_donate() -> list[Shape]:
    pts = []
    for i in range(72):
        t = math.radians(i * 5)
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((12 + x * 0.56, 11.2 + y * 0.56))
    return [Shape(pts)]


# -- action family ------------------------------------------------------------

def d_analyze() -> list[Shape]:
    # Magnifier whose lens holds a bar chart.
    return [
        ring(10, 10, 7.5, 5.6),
        Shape(capsule(15.4, 15.4, 20.8, 20.8, 3)),
        accent(rect(7.2, 10.2, 1.7, 3.3, 0.5)), accent(rect(9.6, 7.6, 1.7, 5.9, 0.5)), accent(rect(12, 9, 1.7, 4.5, 0.5)),
    ]


def d_verify() -> list[Shape]:
    shield = [(12, 2), (21, 5.3), (21, 11.5), (19.5, 16.5), (12, 22), (4.5, 16.5), (3, 11.5), (3, 5.3)]
    c = check(12, 12.3, 3.6, 2.2)
    return [Shape(shield, holes=[c[0], c[1]])]


def d_measure() -> list[Shape]:
    # Measure: a slanted ruler; notches are cut from its upper edge.
    ux, uy = 1 / math.sqrt(2), -1 / math.sqrt(2)      # along the ruler
    vx, vy = 1 / math.sqrt(2), 1 / math.sqrt(2)       # across (down-right)
    ax, ay = 2.2, 16.3                                 # upper-left corner
    length, depth = 19.5, 7.6
    body_pts = [(ax, ay), (ax + ux * length, ay + uy * length),
                (ax + ux * length + vx * depth, ay + uy * length + vy * depth), (ax + vx * depth, ay + vy * depth)]
    holes = []
    for i in range(5):
        t = 2.8 + i * 3.5
        d = 3.6 if i % 2 == 0 else 2.2
        w = 1.4
        p0 = (ax + ux * (t - w / 2), ay + uy * (t - w / 2))
        holes.append([p0, (p0[0] + ux * w, p0[1] + uy * w),
                      (p0[0] + ux * w + vx * d, p0[1] + uy * w + vy * d),
                      (p0[0] + vx * d, p0[1] + vy * d)])
    return [Shape(body_pts, holes=holes)], Cut(12, 12, -58)


def d_scan() -> list[Shape]:
    corners = []
    for (x, y, sx, sy) in ((2.5, 2.5, 1, 1), (21.5, 2.5, -1, 1), (2.5, 21.5, 1, -1), (21.5, 21.5, -1, -1)):
        corners.append(poly(x, y, x + 7 * sx, y, x + 7 * sx, y + 2.4 * sy, x + 2.4 * sx, y + 2.4 * sy,
                            x + 2.4 * sx, y + 7 * sy, x, y + 7 * sy))
    return [*[Shape(c) for c in corners], accent(rect(6, 10.8, 12, 2.4, 1.2))]


def d_download() -> list[Shape]:
    # Download: a rounded tile with a downward arrow cut out, and a base bar.
    down = poly(12, 16.2, 6.6, 10.6, 10, 10.6, 10, 4.6, 14, 4.6, 14, 10.6, 17.4, 10.6)
    return [Shape(rect(3, 2, 18, 16.5, 2.4), holes=[down]), Shape(rect(3, 20, 18, 2.4, 1.2))], Cut(17, 13.5, -58)


def d_install() -> list[Shape]:
    box = poly(12, 2.5, 21.5, 7, 21.5, 17, 12, 21.5, 2.5, 17, 2.5, 7)
    return [Shape(box, holes=[rect(10.9, 8.5, 2.2, 7, 0.6), rect(8.5, 10.9, 7, 2.2, 0.6)])], Cut(16.6, 15.4, -58)


def d_remove() -> list[Shape]:
    return [
        Shape(poly(4, 8.5, 20, 8.5, 18.6, 21.5, 5.4, 21.5),
              holes=[rect(8.3, 11, 1.8, 7.5, 0.9), rect(11.1, 11, 1.8, 7.5, 0.9), rect(13.9, 11, 1.8, 7.5, 0.9)]),
        Shape(poly(3, 4.8, 9, 4.8, 9.8, 2.5, 14.2, 2.5, 15, 4.8, 21, 4.8, 21, 7, 3, 7)),
    ]


def d_restore() -> list[Shape]:
    return [Shape(band(12, 12.5, 9, 5.8, 20, 320)), Shape(tri(20.9, 5.2, 21.6, 12.6, 15.3, 8.6)), accent(circle(12, 12.5, 2.2))]


def d_launch() -> list[Shape]:
    return [Shape(poly(7, 3, 21.5, 12, 7, 21)), accent(rect(2.5, 8.5, 2.4, 7, 1.2))]


def d_refresh() -> list[Shape]:
    return [
        Shape(band(12, 12, 9, 5.8, 200, 330)), Shape(tri(21.6, 5, 22.2, 12.6, 15.7, 8.6)),
        Shape(band(12, 12, 9, 5.8, 20, 150)), Shape(tri(2.4, 19, 1.8, 11.4, 8.3, 15.4)),
    ]


def d_search() -> list[Shape]:
    return [ring(10.2, 10.2, 7.7, 5.4), Shape(capsule(15.6, 15.6, 20.8, 20.8, 3.2))]


def d_advanced() -> list[Shape]:
    return [
        Shape(rect(2.5, 5, 19, 2, 1)), accent(circle(8, 6, 2.6)),
        Shape(rect(2.5, 11, 19, 2, 1)), accent(circle(16, 12, 2.6)),
        Shape(rect(2.5, 17, 19, 2, 1)), accent(circle(11, 18, 2.6)),
    ]


def d_apply() -> list[Shape]:
    c = check(12, 12, 8, 3.6)
    return [Shape(c[0]), Shape(c[1])]


def d_cancel() -> list[Shape]:
    c = cross(12, 12, 6.5, 3.4)
    return [Shape(c[0]), Shape(c[1])]


def d_install_scaler() -> list[Shape]:
    # Install OptiScaler: an upscaling frame (small->large corner) receiving a
    # download arrow - the *action* of installing the integration, distinct
    # from the OptiScaler identity (brand wordmark) and from generic Install.
    return [
        body(rect(2.5, 9.5, 12, 12, 1.8), holes=[rect(5.5, 12.5, 6, 6, 1)]),
        *[s.as_("accent") for s in arrow(17.5, 3.4, 17.5, 11.4, 2.6, head=3.8, head_w=6.4)],
        accent(rect(13.6, 12.8, 7.8, 2.2, 1.1)),
    ]


# -- compression profiles (Storage tab) ------------------------------------------
# All four share one chassis: a "compressed block" notched on both sides, so the
# family reads as one; the cut-out states the level.

def _profile_block() -> list[Point]:
    return poly(4, 3, 20, 3, 20, 8.5, 17.6, 12, 20, 15.5, 20, 21, 4, 21, 4, 15.5, 6.4, 12, 4, 8.5)


def d_profile_fast() -> list[Shape]:
    # Fast: a lightning bolt cut out of the block.
    bolt = poly(13.2, 6, 8.8, 13, 11.8, 13, 10.8, 18, 15.2, 11, 12.2, 11)
    return [Shape(_profile_block(), holes=[bolt])], Cut(16.5, 16.5, -58)


def d_profile_balanced() -> list[Shape]:
    # Balanced: a level beam with two equal pans.
    return [Shape(_profile_block(), holes=[rect(7.2, 9.2, 9.6, 2, 1), rect(11, 6.6, 2, 2.8, 0.7),
                                            rect(6.8, 13, 3.8, 3, 1.5), rect(13.4, 13, 3.8, 3, 1.5)])], Cut(16.5, 16.5, -58)


def d_profile_maximum() -> list[Shape]:
    # Maximum: a full three-bar level meter cut out.
    return [Shape(_profile_block(), holes=[rect(7.6, 13, 2.4, 4.5, 0.7), rect(10.8, 9.5, 2.4, 8, 0.7), rect(14, 6.5, 2.4, 11, 0.7)])], Cut(16.5, 16.5, -58)


def d_profile_auto() -> list[Shape]:
    # Automatic: a sparkle (measured, chosen for you).
    return [Shape(_profile_block(), holes=[star4(12, 12, 5.2, 0.36)])], Cut(16.5, 16.5, -58)


# -- storage / Btrfs compression -------------------------------------------------

def d_compress() -> list[Shape]:
    # Compression: two arrows squeezing a slim core bar from both sides.
    return [
        Shape(rect(10.7, 3, 2.6, 18, 1.3)),
        *arrow(3.2, 12, 9.4, 12, 2.8, head=3.8, head_w=7),
        *arrow(20.8, 12, 14.6, 12, 2.8, head=3.8, head_w=7),
    ], Cut(12, 12, -58)


def d_compression_verify() -> list[Shape]:
    # Verify compression: a compressed block (notched sides) with a check cut out.
    c = check(12, 12.3, 3.9, 2.3)
    block = poly(4, 3, 20, 3, 20, 8.5, 17.6, 12, 20, 15.5, 20, 21, 4, 21, 4, 15.5, 6.4, 12, 4, 8.5)
    return [Shape(block, holes=[c[0], c[1]])], Cut(16.5, 16.5, -58)


def d_compressed_block() -> list[Shape]:
    # Compression profile: the squeezed block with a level bar instead of a check.
    block = poly(4, 3, 20, 3, 20, 8.5, 17.6, 12, 20, 15.5, 20, 21, 4, 21, 4, 15.5, 6.4, 12, 4, 8.5)
    return [Shape(block, holes=[rect(8.5, 10.9, 7, 2.2, 1.1)])], Cut(16.5, 16.5, -58)


def d_exact_measure() -> list[Shape]:
    # Exact measurement: a crosshair reticle with a precise centre point.
    ticks = [rect(11, 1.8, 2, 4.6, 0.8), rect(11, 17.6, 2, 4.6, 0.8), rect(1.8, 11, 4.6, 2, 0.8), rect(17.6, 11, 4.6, 2, 0.8)]
    return [ring(12, 12, 8.2, 5.6), *[Shape(t) for t in ticks], accent(circle(12, 12, 2.2))], Cut(12, 12, -58)


# -- overview tiles ----------------------------------------------------------------

def d_logical_size() -> list[Shape]:
    # Logical size: the apparent, "unpacked" size - a hollow box outline.
    return [Shape(rect(3, 3, 18, 18, 3), holes=[rect(6.2, 6.2, 11.6, 11.6, 1.6)])], Cut(14, 14, -58)


def d_physical_size() -> list[Shape]:
    # Physical size: what is really on the platter - a solid disk with a hub.
    return [Shape(circle(12, 12, 9.6), holes=[circle(12, 12, 2.4)])], Cut(17.6, 17.6, -58)


def d_saved_space() -> list[Shape]:
    # Saved space: a pie with one slice removed; the freed slice sits apart.
    return [body(band(12.8, 12.8, 8.8, 0.01, -90, 180, 40)), accent(band(10.6, 10.6, 8.8, 0.01, 180, 270, 12))]


def d_optimization_profile() -> list[Shape]:
    # Optimization profile: a chevron-up arrowhead on a rising bar.
    return [Shape(poly(12, 2.5, 21.5, 11.5, 16.4, 11.5, 16.4, 21.5, 7.6, 21.5, 7.6, 11.5, 2.5, 11.5))]


# -- graphics: remaster + scaling integrations -------------------------------------

def d_remaster_classic() -> list[Shape]:
    # Classic enhancement: an image frame with a sharpen diamond.
    return [Shape(rect(2.5, 4, 19, 16, 2.4), holes=[poly(10.5, 7.2, 15.3, 12, 10.5, 16.8, 5.7, 12)])], Cut(17.4, 14.6, -58)


def d_remaster_light_ai() -> list[Shape]:
    # Light AI: frame with one sparkle cut out.
    return [Shape(rect(2.5, 4, 19, 16, 2.4), holes=[star4(10.5, 12, 5.4, 0.36)])], Cut(17.4, 14.6, -58)


def d_remaster_quality_ai() -> list[Shape]:
    # Quality AI: frame with a large and a small sparkle.
    return [Shape(rect(2.5, 4, 19, 16, 2.4), holes=[star4(9.4, 12.8, 5.2, 0.36), star4(15.2, 8.2, 2.3, 0.4)])], Cut(17.4, 14.6, -58)


# -- status, files, system --------------------------------------------------------

STATUS_CUT = Cut(17.2, 17.2, -58)   # keeps the symbol whole; the facet is a corner highlight


def d_status_success() -> list[Shape]:
    c = check(12, 12.5, 4.2, 2.6)
    return [Shape(circle(12, 12, 9.8), holes=[c[0], c[1]])], STATUS_CUT


def d_status_warning() -> list[Shape]:
    return [Shape(poly(12, 2.5, 22, 20.5, 2, 20.5), holes=[rect(10.8, 8.4, 2.4, 6, 1.2), circle(12, 17.4, 1.4)])], Cut(17.8, 18, -58)


def d_status_error() -> list[Shape]:
    c = cross(12, 12, 3.4, 2.4)
    return [Shape(poly(8.2, 2.5, 15.8, 2.5, 21.5, 8.2, 21.5, 15.8, 15.8, 21.5, 8.2, 21.5, 2.5, 15.8, 2.5, 8.2),
                  holes=[c[0], c[1]])], STATUS_CUT


def d_status_information() -> list[Shape]:
    return [Shape(circle(12, 12, 9.8), holes=[circle(12, 7.3, 1.45), rect(10.8, 10.3, 2.4, 7, 1.2)])], STATUS_CUT


def d_status_experimental() -> list[Shape]:
    # Experimental: a lab flask with a bubbling accent.
    flask = poly(9.5, 2.5, 14.5, 2.5, 14.5, 9, 21, 19.5, 19.5, 21.5, 4.5, 21.5, 3, 19.5, 9.5, 9)
    return [Shape(flask, holes=[circle(10.5, 15.5, 1.3), circle(13.8, 18.2, 1)])]


def d_file() -> list[Shape]:
    # File: a sheet with the folded corner as a separate accent facet.
    return [body(poly(4.5, 2.5, 13.5, 2.5, 13.5, 9, 19.5, 9, 19.5, 21.5, 4.5, 21.5)),
            accent(poly(15, 2.5, 19.5, 7, 15, 7))]


def d_folder() -> list[Shape]:
    return [Shape(poly(2.5, 4.5, 9.5, 4.5, 11.8, 7, 21.5, 7, 21.5, 20, 2.5, 20))], Cut(14, 14, -58)


def d_controllers() -> list[Shape]:
    # Controllers: a compact gamepad with a wireless signal arc above it.
    g = (_arc(7.6, 12.4, 4.6, 180, 270, 6) + _arc(16.4, 12.4, 4.6, 270, 360, 6)
         + [(21.4, 18.4)] + _arc(18.8, 19.9, 2.5, 10, 120, 6) + [(15, 18.6), (9, 18.6)]
         + _arc(5.2, 19.9, 2.5, 60, 170, 6) + [(2.6, 18.4)])
    dpad = poly(7, 10, 8.6, 10, 8.6, 12, 10.6, 12, 10.6, 13.6, 8.6, 13.6, 8.6, 15.6, 7, 15.6, 7, 13.6, 5, 13.6, 5, 12, 7, 12)
    return [Shape(g, holes=[dpad, circle(15.2, 11.4, 1.15, 20), circle(17.6, 13.8, 1.15, 20)]),
            accent(band(12, 7.5, 5.2, 3.4, 225, 315, 10)), accent(circle(12, 6.4, 1.2))], Cut(13.4, 14, -58)


def d_runtime() -> list[Shape]:
    # Runtime: a layered stack (Flatpak-like) with the top layer lifted.
    return [
        Shape(poly(12, 2.5, 21.5, 7.5, 12, 12.5, 2.5, 7.5)),
        Shape(poly(2.5, 11.5, 5.2, 10, 12, 13.8, 18.8, 10, 21.5, 11.5, 12, 16.5)),
        Shape(poly(2.5, 15.5, 5.2, 14, 12, 17.8, 18.8, 14, 21.5, 15.5, 12, 20.5)),
    ]


# stem: (category, semantic name, design)
DESKTOP: dict[str, tuple[str, str, object]] = {
    "sidebar-games": ("sidebar", "Games", d_games),
    "sidebar-updates": ("sidebar", "Updates", d_updates),
    "sidebar-tasks": ("sidebar", "Tasks", d_tasks),
    "sidebar-system": ("sidebar", "System", d_system),
    "sidebar-settings": ("sidebar", "Settings", d_settings),
    "sidebar-narrator": ("sidebar", "Narrator", d_narrator),
    "sidebar-donate": ("sidebar", "Donate", d_donate),
    "action-analyze": ("action", "Analyze", d_analyze),
    "action-verify": ("action", "Verify", d_verify),
    "action-measure": ("action", "Measure", d_measure),
    "action-scan": ("action", "Scan", d_scan),
    "action-download": ("action", "Download", d_download),
    "action-install": ("action", "Install", d_install),
    "action-remove": ("action", "Remove", d_remove),
    "action-restore": ("action", "Restore", d_restore),
    "action-launch": ("action", "Launch game", d_launch),
    "action-refresh": ("action", "Refresh", d_refresh),
    "action-search": ("action", "Search", d_search),
    "action-advanced-settings": ("action", "Advanced settings", d_advanced),
    "action-apply": ("action", "Apply", d_apply),
    "action-cancel": ("action", "Cancel", d_cancel),
    "action-install-scaler": ("action", "Install OptiScaler", d_install_scaler),
    "profile-fast": ("compression-profile", "Fast", d_profile_fast),
    "profile-balanced": ("compression-profile", "Balanced", d_profile_balanced),
    "profile-maximum": ("compression-profile", "Maximum", d_profile_maximum),
    "profile-auto": ("compression-profile", "Automatic", d_profile_auto),
    "btrfs-analyze-compression": ("btrfs", "Analyze compression", d_compress),
    "btrfs-verify-compression": ("btrfs", "Verify compression", d_compression_verify),
    "btrfs-exact-measurement": ("btrfs", "Exact measurement", d_exact_measure),
    "overview-logical-size": ("overview", "Logical size", d_logical_size),
    "overview-physical-size": ("overview", "Physical size", d_physical_size),
    "overview-saved-space": ("overview", "Saved space", d_saved_space),
    "overview-optimization-profile": ("overview", "Optimization profile", d_optimization_profile),
    "remaster-classic": ("graphics-remaster", "Classic Enhancement", d_remaster_classic),
    "remaster-light-ai": ("graphics-remaster", "Light AI", d_remaster_light_ai),
    "remaster-quality-ai": ("graphics-remaster", "Quality AI", d_remaster_quality_ai),
    # Scaling/effects tabs for third-party projects (DLSS Enabler, lsfg-vk,
    # ReShade, vkBasalt) intentionally carry no decorative header icon: none of
    # them publishes a reusable official mark and GameOpti does not invent one.
    "status-success": ("status", "Success", d_status_success),
    "status-warning": ("status", "Warning", d_status_warning),
    "status-error": ("status", "Error", d_status_error),
    "status-information": ("status", "Information", d_status_information),
    "status-experimental": ("status", "Experimental", d_status_experimental),
    "file-file": ("file", "File", d_file),
    "file-folder": ("file", "Folder", d_folder),
    "system-controllers": ("system", "Controller", d_controllers),
    "system-runtime": ("system", "Runtime", d_runtime),
}

# -- Couch-only designs ------------------------------------------------------------

def d_check_updates() -> list[Shape]:
    # Check for updates: the update badge with a magnifier handle.
    up = poly(10.5, 5.2, 15.4, 10.2, 12.3, 10.2, 12.3, 15.4, 8.7, 15.4, 8.7, 10.2, 5.6, 10.2)
    return [Shape(circle(10.5, 10.5, 8.2), holes=[up]), Shape(capsule(16.6, 16.6, 20.4, 20.4, 3.2))], Cut(14, 14, -58)


def d_overview() -> list[Shape]:
    # Overview: a dashboard of four tiles, one highlighted.
    return [body(rect(2.5, 2.5, 8.6, 8.6, 1.8)), body(rect(12.9, 2.5, 8.6, 8.6, 1.8)),
            body(rect(2.5, 12.9, 8.6, 8.6, 1.8)), accent(rect(12.9, 12.9, 8.6, 8.6, 1.8))]


def d_storage() -> list[Shape]:
    # Storage: a drive enclosure with an activity light.
    return [Shape(rect(2.5, 4, 19, 7, 1.8), holes=[circle(17.5, 7.5, 1.1)]),
            Shape(rect(2.5, 13, 19, 7, 1.8), holes=[circle(17.5, 16.5, 1.1)])], Cut(12, 12, -58)


def d_optimization() -> list[Shape]:
    # Optimization: a gauge dial with the needle pushed into the high range.
    return [Shape(band(12, 14, 9.8, 5.4, 180, 360, 30)),
            accent(capsule(12, 14, 17.2, 7.8, 2.4)), accent(circle(12, 14, 2.4))], Cut(12, 12, -58)


def d_optiscaler_tile() -> list[Shape]:
    # OptiScaler: a frame being scaled up along the diagonal (upscaling).
    return [body(rect(2.5, 9.5, 12, 12, 1.8), holes=[rect(5.5, 12.5, 6, 6, 1)]),
            accent(poly(13, 2.5, 21.5, 2.5, 21.5, 11, 18.6, 11, 18.6, 7.5, 13.2, 12.9, 11.1, 10.8, 16.5, 5.4, 13, 5.4))]


def d_polish_subtitles() -> list[Shape]:
    # Polish subtitles: a subtitle bar with a sparkle.
    return [Shape(rect(2.5, 14, 19, 7.5, 2), holes=[rect(5.5, 16.6, 9, 2.2, 1.1)]), accent(star4(16, 7.5, 4.8, 0.36))]


def d_capture_window() -> list[Shape]:
    # Capture window: viewfinder corners around a window pane.
    cs = []
    for (x, y, sx, sy) in ((2.5, 2.5, 1, 1), (21.5, 2.5, -1, 1), (2.5, 21.5, 1, -1), (21.5, 21.5, -1, -1)):
        cs.append(poly(x, y, x + 6 * sx, y, x + 6 * sx, y + 2.2 * sy, x + 2.2 * sx, y + 2.2 * sy, x + 2.2 * sx, y + 6 * sy, x, y + 6 * sy))
    return [*[body(c) for c in cs], accent(rect(7, 7.5, 10, 9, 1.2), holes=[rect(7, 10, 10, 0.01)])]


def d_voice() -> list[Shape]:
    # Voice: a microphone capsule with stand.
    return [Shape(rect(8.5, 2.5, 7, 11.5, 3.5)), Shape(band(12, 11, 7, 4.6, 0, 180, 20)),
            Shape(rect(10.9, 17.5, 2.2, 3, 0)), Shape(rect(7, 19.8, 10, 2.2, 1.1))], Cut(13, 11, -58)


def d_volume() -> list[Shape]:
    # Volume: a speaker with two sound arcs.
    return [body(poly(2.5, 8.5, 6.5, 8.5, 12, 4, 12, 20, 6.5, 15.5, 2.5, 15.5)),
            accent(band(12.5, 12, 6.4, 4.2, -50, 50, 12)), accent(band(12.5, 12, 9.8, 7.6, -50, 50, 12))]


def d_speech_speed() -> list[Shape]:
    # Speech rate: a speech bubble trailing motion dashes.
    r = 2.6
    bubble = (_arc(21.5 - r, 4 + r, r, -90, 0, 5) + _arc(21.5 - r, 15 - r, r, 0, 90, 5)
              + [(13, 15), (10.5, 18.5), (10.5, 15)] + _arc(8 + r, 15 - r, r, 90, 180, 5) + _arc(8 + r, 4 + r, r, 180, 270, 5))
    return [body(bubble), accent(rect(2, 7, 4.5, 2, 1)), accent(rect(3.5, 11, 3, 2, 1)), accent(rect(1.5, 15, 3.5, 2, 1))]


def d_subtitle_area() -> list[Shape]:
    # Subtitle area: a screen with the caption region highlighted at the bottom.
    return [body(rect(2.5, 3, 19, 18, 2.2), holes=[rect(5, 14.5, 14, 4, 1)]), accent(rect(5, 14.5, 14, 4, 1))]


def d_gamemode() -> list[Shape]:
    # GameMode: a bold lightning bolt (performance governor).
    bolt = poly(14.5, 1.5, 3.5, 13.5, 10.6, 13.5, 8.6, 22.5, 20.5, 9.5, 13.2, 9.5)
    return [Shape(bolt)], Cut(12.5, 12, -58)


def d_gamescope() -> list[Shape]:
    # Gamescope: a micro-compositor - the display frame holds a smaller game
    # window, and an accent arrow pushes it toward the frame corner (scaling /
    # fitting the game to the output). Distinct from the GameMode bolt.
    return [body(rect(2, 3.5, 20, 14.5, 2), holes=[rect(5, 6.5, 14, 8.5, 1)]),
            accent(rect(6.2, 9.4, 5.6, 4.2, 0.8)),
            *[s.as_("accent") for s in arrow(13.2, 12.4, 17.6, 8, 1.8, head=2.8, head_w=4)],
            body(rect(8, 19.5, 8, 2.2, 1.1))]


# -- glyph-only designs (Couch controls, metrics, Gamescope/MangoHud settings) ------

def screen(inner: list[Shape] | None = None) -> list[Shape]:
    """Shared display chassis used by the Gamescope/monitor family."""
    return [body(rect(2, 3.5, 20, 15, 1.8), holes=[rect(4.6, 6.1, 14.8, 9.8, 0.8)]),
            *(inner or []), body(rect(7.5, 20, 9, 2.2, 1.1))]


def d_backspace() -> list[Shape]:
    c = cross(13.6, 12, 2.4, 1.9)
    return [Shape(poly(2.5, 12, 8.5, 4.5, 21.5, 4.5, 21.5, 19.5, 8.5, 19.5), holes=[c[0], c[1]])], Cut(16, 15, -58)


def d_borderless() -> list[Shape]:
    # Borderless window: the pane fills the whole screen, no bezel at all.
    return [Shape(rect(2.5, 4.5, 19, 15, 1.6))], Cut(13, 12, -58)


def d_fullscreen() -> list[Shape]:
    cs = []
    for (x, y, sx, sy) in ((2.5, 2.5, 1, 1), (21.5, 2.5, -1, 1), (2.5, 21.5, 1, -1), (21.5, 21.5, -1, -1)):
        cs.append(poly(x, y, x + 7.5 * sx, y, x + 7.5 * sx, y + 2.6 * sy, x + 2.6 * sx, y + 2.6 * sy,
                       x + 2.6 * sx, y + 7.5 * sy, x, y + 7.5 * sy))
    return [Shape(c) for c in cs]


def d_windowed() -> list[Shape]:
    return [Shape(rect(2.5, 4, 19, 16, 1.8), holes=[rect(5, 9, 14, 8.5, 0.8)])], Cut(14, 14, -58)


def d_capture() -> list[Shape]:
    # Capture: a camera body with a lens.
    return [Shape(poly(2.5, 7, 7.5, 7, 9, 4, 15, 4, 16.5, 7, 21.5, 7, 21.5, 20, 2.5, 20), holes=[circle(12, 13.5, 4.2, 32)]),
            accent(circle(12, 13.5, 2.3))], Cut(18, 16, -58)


def d_desktop() -> list[Shape]:
    return screen(), Cut(16, 13, -58)


def d_details() -> list[Shape]:
    return [Shape(rect(3.5, 2.5, 17, 19, 2.2), holes=[rect(7, 7, 10, 2, 1), rect(7, 11, 10, 2, 1), rect(7, 15, 6.5, 2, 1)])], Cut(16, 16, -58)


def d_filter() -> list[Shape]:
    return [Shape(poly(2.5, 4, 21.5, 4, 14.2, 13, 14.2, 20.5, 9.8, 22, 9.8, 13))]


def d_fit() -> list[Shape]:
    # Image fit: a frame with inward arrows at opposite corners.
    return [body(rect(2.5, 3.5, 19, 17, 2), holes=[rect(5.5, 6.5, 13, 11, 1)]),
            *[s.as_("accent") for s in arrow(6.8, 15.2, 10.6, 11.4, 1.8, head=2.8, head_w=3.8)],
            *[s.as_("accent") for s in arrow(17.2, 8.8, 13.4, 12.6, 1.8, head=2.8, head_w=3.8)]]


def d_fps() -> list[Shape]:
    return [Shape(band(12, 14.5, 10, 6, 180, 360, 30)), accent(capsule(12, 14.5, 17, 8.2, 2.3)), accent(circle(12, 14.5, 2.3))], Cut(12, 12, -58)


def d_fps_limit() -> list[Shape]:
    return [Shape(band(12, 15.5, 9.6, 5.6, 180, 360, 30)), accent(capsule(12, 15.5, 12, 9, 2.3)), accent(circle(12, 15.5, 2.3)),
            body(rect(6, 2.5, 12, 2.4, 1.2))], Cut(12, 13, -58)


def d_frametime() -> list[Shape]:
    return [body(rect(2.5, 18.5, 19, 2.2, 1.1)), body(rect(3.5, 11, 2.6, 6, 1)), body(rect(7.6, 12, 2.6, 5, 1)),
            accent(rect(11.7, 4, 2.6, 13, 1)), body(rect(15.8, 11.5, 2.6, 5.5, 1)), body(rect(18.3, 12.5, 2.6, 4.5, 1))]


def d_grab() -> list[Shape]:
    # Capture keyboard: a key row the compositor takes over.
    return [Shape(rect(2.5, 6, 19, 12, 2.4), holes=[rect(6, 9.5, 3, 2, 0.6), rect(10.5, 9.5, 3, 2, 0.6), rect(15, 9.5, 3, 2, 0.6), rect(7, 13.5, 10, 2, 0.8)])], Cut(16, 14, -58)


def d_hdr() -> list[Shape]:
    # HDR: a sun with eight rays - extended brightness range.
    rays = []
    for i in range(8):
        a = math.radians(i * 45)
        rays.append(capsule(12 + 7.2 * math.cos(a), 12 + 7.2 * math.sin(a), 12 + 9.2 * math.cos(a), 12 + 9.2 * math.sin(a), 2.2))
    return [Shape(circle(12, 12, 5.2)), *[Shape(r) for r in rays]], Cut(12, 12, -58)


def d_keyboard() -> list[Shape]:
    holes = [rect(5 + i * 3.2, 7.5, 2, 2, 0.5) for i in range(5)] + [rect(5 + i * 3.2, 11, 2, 2, 0.5) for i in range(5)] + [rect(7, 14.5, 10, 2, 0.8)]
    return [Shape(rect(2.5, 5, 19, 14, 2), holes=holes)], Cut(17, 15, -58)


def d_library_offline() -> list[Shape]:
    # Library offline: the gamepad, cut through by a wide diagonal gap with
    # the lower half dimmed - "disconnected".
    shapes, _ = d_games()
    return [Shape(shapes[0].outer, shapes[0].holes, opacity=1.0)], Cut(12, 12, 45, gap=3.0)


def d_memory() -> list[Shape]:
    # Memory: a RAM module - two chips cut out, a contact row below.
    return [Shape(rect(2.5, 4, 19, 12, 1.6), holes=[rect(5.5, 7, 5.5, 6, 0.8), rect(13, 7, 5.5, 6, 0.8)]),
            *[Shape(rect(4 + i * 3.2, 17.5, 2.2, 3, 0.6)) for i in range(6)]], Cut(14, 12, -58)


def d_more() -> list[Shape]:
    return [body(circle(5, 12, 2.4)), Shape(circle(12, 12, 2.4)), accent(circle(19, 12, 2.4))]


def d_next() -> list[Shape]:
    c = chevron(12, 12, 7, 3.2, "right")
    return [Shape(c[0]), Shape(c[1])]


def d_previous() -> list[Shape]:
    c = chevron(12, 12, 7, 3.2, "left")
    return [Shape(c[0]), Shape(c[1])]


def d_position() -> list[Shape]:
    # Overlay position: a HUD box pinned in one corner of the screen.
    return screen([accent(rect(5.6, 7.1, 6, 4, 0.6))])


def d_preset() -> list[Shape]:
    # Preset: a stack of cards with the top card lifted.
    return [body(rect(4.5, 9, 15, 11, 1.8)), accent(rect(2.5, 3.5, 15, 11, 1.8), holes=[rect(5.5, 6.5, 9, 2, 1), rect(5.5, 10, 6, 2, 1)])]


def d_region() -> list[Shape]:
    # Subtitle region: a selection rectangle with corner handles.
    return [Shape(rect(3.5, 5.5, 17, 13, 1.2), holes=[rect(6, 8, 12, 8, 0.6)]),
            accent(circle(3.5, 5.5, 2)), accent(circle(20.5, 5.5, 2)), accent(circle(3.5, 18.5, 2)), accent(circle(20.5, 18.5, 2))], Cut(16, 15, -58)


def d_resolution_game() -> list[Shape]:
    return screen([accent(rect(7.5, 8.5, 9, 5, 0.8))])


def d_resolution_output() -> list[Shape]:
    return screen([accent(rect(4.6, 6.1, 14.8, 9.8, 0.8))])


def d_save() -> list[Shape]:
    return [Shape(poly(2.5, 2.5, 17.5, 2.5, 21.5, 6.5, 21.5, 21.5, 2.5, 21.5), holes=[rect(6, 2.5, 10, 5.5, 0.6), rect(6.5, 13, 11, 6, 1)])], Cut(16, 16, -58)


def d_scan_source() -> list[Shape]:
    # Recognition source: a scanning beam across the screen.
    return screen([accent(rect(4.6, 10, 14.8, 2.2, 1.1))])


def d_sharpness() -> list[Shape]:
    return [Shape(poly(12, 2.5, 21.5, 12, 12, 21.5, 2.5, 12), holes=[poly(12, 7.5, 16.5, 12, 12, 16.5, 7.5, 12)])]


def d_sort() -> list[Shape]:
    return [body(rect(2.5, 4, 19, 2.6, 1.3)), body(rect(2.5, 10.7, 13, 2.6, 1.3)), accent(rect(2.5, 17.4, 7, 2.6, 1.3))]


def d_source() -> list[Shape]:
    # Game source / launcher: a tag.
    return [Shape(poly(2.5, 2.5, 12, 2.5, 21.5, 12, 12, 21.5, 2.5, 12), holes=[circle(7.2, 7.2, 1.7)])]


def d_start() -> list[Shape]:
    return [Shape(poly(6, 3, 21, 12, 6, 21))]


def d_stop() -> list[Shape]:
    return [Shape(rect(4, 4, 16, 16, 2.4))]


def d_subtitles() -> list[Shape]:
    return [Shape(rect(2.5, 4, 19, 16, 2.2), holes=[rect(5.5, 12, 7, 2.2, 1.1), rect(14, 12, 4.5, 2.2, 1.1), rect(5.5, 15.5, 4, 2.2, 1.1), rect(11, 15.5, 7.5, 2.2, 1.1)])], Cut(16, 13, -58)


def d_temperature() -> list[Shape]:
    return [Shape(rect(9, 2.5, 6, 13, 3), holes=[rect(11, 7, 2, 7)]), Shape(circle(12, 17.5, 4.5)), accent(circle(12, 17.5, 2.2)), accent(rect(11, 10, 2, 7))], Cut(14, 14, -58)


def d_text_size() -> list[Shape]:
    big = poly(1.8, 20, 7.3, 3.5, 10.8, 3.5, 16.3, 20, 13.1, 20, 11.8, 15.8, 6.3, 15.8, 5, 20)
    small = poly(14.6, 20, 17.7, 11, 19.5, 11, 22.4, 20, 20.3, 20, 19.7, 18, 17.4, 18, 16.8, 20)
    return [body(big, holes=[poly(7.3, 12.8, 9.05, 7.2, 10.8, 12.8)]), accent(small, holes=[poly(18, 16, 18.6, 13.6, 19.2, 16)])]


def d_toggle_on() -> list[Shape]:
    return [accent(rect(2.5, 6.5, 19, 11, 5.5), holes=[circle(16.5, 12, 3.8)])]


def d_toggle_off() -> list[Shape]:
    return [body(rect(2.5, 6.5, 19, 11, 5.5), holes=[rect(4.6, 8.6, 14.8, 6.8, 3.4)]), body(circle(7.5, 12, 2.6))]


def d_usage() -> list[Shape]:
    return [body(rect(2.5, 14, 4, 7.5, 1)), body(rect(8, 10, 4, 11.5, 1)), accent(rect(13.5, 6, 4, 15.5, 1)), accent(rect(17.5, 2.5, 4, 19, 1))]


def d_vrr() -> list[Shape]:
    # VRR: a refresh wave whose wavelength varies across the screen.
    wave = []
    for i in range(0, 41):
        t = i / 40
        wave.append((4.6 + t * 14.8, 11 + 2.4 * math.sin(t * 3.0 * math.pi * (0.6 + t))))
    thick = [(x, y - 1.1) for x, y in wave] + [(x, y + 1.1) for x, y in reversed(wave)]
    return screen([accent(thick)])


# Couch tiles (couch/*.svg) and actions (couch/actions/*.svg): dark-surface only
# artwork, tinted monochrome by CouchIcon on light surfaces.
COUCH_TILES: dict[str, tuple[str, object]] = {
    "library": ("Library", d_games),
    "tasks": ("Tasks", d_tasks),
    "updates": ("Updates", d_updates),
    "settings": ("Settings", d_settings),
    "launch": ("Launch", d_launch),
    "check-updates": ("Check for updates", d_check_updates),
    "overview": ("Overview", d_overview),
    "storage": ("Storage", d_storage),
    "optimization": ("Optimization", d_optimization),
    "optiscaler": ("OptiScaler", d_optiscaler_tile),
    "narrator": ("Narrator", d_narrator),
}
COUCH_ACTIONS: dict[str, tuple[str, object]] = {
    "analyze": ("Analyze", d_analyze),
    "verify-compression": ("Verify compression", d_compression_verify),
    "apply-optimization": ("Apply optimization", d_optimization_profile),
    "polish-subtitles": ("Polish subtitles", d_polish_subtitles),
    "capture-window": ("Capture window", d_capture_window),
    "voice": ("Voice", d_voice),
    "volume": ("Volume", d_volume),
    "speech-speed": ("Speech speed", d_speech_speed),
    "subtitle-area": ("Subtitle area", d_subtitle_area),
    "install": ("Install", d_install),
    "remove": ("Remove", d_remove),
    "gamemode": ("GameMode", d_gamemode),
    "gamescope": ("Gamescope", d_gamescope),
    # "mangohud" is upstream brand artwork (brands/README.md) - never generated.
}
COUCH_DARK = ("#E3ECF2", "#4FE3B8")

# Couch glyphs (couch/glyphs/*.svg + *-on-light.svg). "mangohud" is brand artwork.
GLYPHS: dict[str, tuple[str, object]] = {
    "analyze": ("Analyze", d_analyze), "backspace": ("Backspace", d_backspace),
    "borderless": ("Borderless", d_borderless), "cancel": ("Cancel", d_cancel),
    "capture": ("Capture", d_capture), "compress": ("Compress", d_compress),
    "compression-profile": ("Compression profile", d_compressed_block),
    "controller": ("Controller", d_games), "desktop": ("Desktop", d_desktop),
    "details": ("Details", d_details), "download": ("Download", d_download),
    "filter": ("Filter", d_filter), "fit": ("Image fit", d_fit),
    "fps-limit": ("FPS limit", d_fps_limit), "fps": ("FPS", d_fps),
    "frametime": ("Frametime", d_frametime), "fullscreen": ("Fullscreen", d_fullscreen),
    "gamemode": ("GameMode", d_gamemode), "gamescope": ("Gamescope", d_gamescope),
    "grab": ("Capture keyboard", d_grab), "hdr": ("HDR", d_hdr),
    "info": ("Information", d_status_information), "install": ("Install", d_install),
    "keyboard": ("Keyboard", d_keyboard), "launch": ("Launch", d_launch),
    "library-offline": ("Library offline", d_library_offline), "library": ("Library", d_games),
    "memory": ("Memory", d_memory), "more": ("More", d_more), "narrator": ("Narrator", d_narrator),
    "next": ("Next", d_next), "optimization": ("Optimization", d_optimization),
    "optiscaler": ("OptiScaler", d_optiscaler_tile), "overview": ("Overview", d_overview),
    "position": ("Position", d_position), "preset": ("Preset", d_preset),
    "previous": ("Previous", d_previous), "refresh": ("Refresh", d_refresh),
    "region": ("Region", d_region), "remove": ("Remove", d_remove),
    "resolution-game": ("Game resolution", d_resolution_game),
    "resolution-output": ("Output resolution", d_resolution_output),
    "save": ("Save", d_save), "scan-source": ("Recognition source", d_scan_source),
    "search": ("Search", d_search), "settings": ("Settings", d_settings),
    "sharpness": ("Sharpness", d_sharpness), "sort": ("Sort", d_sort), "source": ("Source", d_source),
    "speech-rate": ("Speech rate", d_speech_speed), "start": ("Start", d_start),
    "status-error": ("Error", d_status_error), "status-ready": ("Ready", d_status_success),
    "status-warning": ("Warning", d_status_warning), "stop": ("Stop", d_stop),
    "storage": ("Storage", d_storage), "subtitles": ("Subtitles", d_subtitles),
    "tasks": ("Tasks", d_tasks), "temperature": ("Temperature", d_temperature),
    "text-size": ("Text size", d_text_size), "toggle-off": ("Off", d_toggle_off),
    "toggle-on": ("On", d_toggle_on), "tune": ("Tune", d_advanced), "updates": ("Updates", d_updates),
    "usage": ("Usage", d_usage), "verify-compression": ("Verify compression", d_compression_verify),
    "verify": ("Verify", d_verify), "voice": ("Voice", d_voice), "volume": ("Volume", d_volume),
    "vrr": ("VRR", d_vrr), "windowed": ("Windowed", d_windowed),
}
GLYPH_STATUS_TONE = {"status-error": "error", "status-ready": "success", "status-warning": "warning"}

# Status icons keep their semantic colour in both themes.
STATUS_TONE = {"status-success": "success", "status-warning": "warning",
               "status-error": "error", "status-information": "info"}


def palette_for(stem: str, theme: str) -> tuple[str, str]:
    tone = STATUS_TONE.get(stem)
    if tone:
        return (STATUS_DARK if theme == "dark" else STATUS_LIGHT)[tone]
    return DARK if theme == "dark" else LIGHT


def unpack(design) -> tuple[list[Shape], Cut]:
    """Designs return either a shape list or (shapes, custom Cut)."""
    if isinstance(design, tuple):
        return design[0], design[1]
    return design, Cut()


def build_all() -> dict[str, str]:
    files: dict[str, str] = {}
    manifest = []
    for stem, (category, name, design) in DESKTOP.items():
        shapes, cut = unpack(design())
        for suffix, theme in (("", "dark"), ("-on-light", "light")):
            palette = palette_for(stem, theme)
            files[f"{stem}{suffix}.svg"] = render_svg(name, shapes, palette, cut)
            manifest.append({"filename": f"{stem}{suffix}.svg", "semantic_name": name,
                             "category": category, "default_color": palette[1], "theme": theme})
    files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    for stem, (name, design) in COUCH_TILES.items():
        shapes, cut = unpack(design())
        files[f"couch/{stem}.svg"] = render_svg(name, shapes, COUCH_DARK, cut)
    for stem, (name, design) in COUCH_ACTIONS.items():
        shapes, cut = unpack(design())
        files[f"couch/actions/{stem}.svg"] = render_svg(name, shapes, COUCH_DARK, cut)
    for stem, (name, design) in GLYPHS.items():
        shapes, cut = unpack(design())
        tone = GLYPH_STATUS_TONE.get(stem)
        dark = STATUS_DARK[tone] if tone else COUCH_DARK
        light = STATUS_LIGHT[tone] if tone else LIGHT
        files[f"couch/glyphs/{stem}.svg"] = render_svg(name, shapes, dark, cut)
        files[f"couch/glyphs/{stem}-on-light.svg"] = render_svg(name, shapes, light, cut)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=ASSETS)
    args = parser.parse_args()
    files = build_all()
    stale = []
    # Generated SVGs that are no longer produced must not linger in the
    # package (every packaged Desktop SVG has to be referenced by UiIcons.qml).
    for existing in list(args.out.glob("*.svg")) + list(args.out.glob("couch/*.svg")) \
            + list(args.out.glob("couch/actions/*.svg")) + list(args.out.glob("couch/glyphs/*.svg")):
        rel = existing.relative_to(args.out).as_posix()
        if rel not in files and "mangohud" not in rel:
            if args.check:
                stale.append(rel + " (orphan)")
            else:
                existing.unlink()
    for rel, content in files.items():
        path = args.out / rel
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if args.check:
        print("\n".join(stale) if stale else "up to date")
        return 1 if stale else 0
    print(f"wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
