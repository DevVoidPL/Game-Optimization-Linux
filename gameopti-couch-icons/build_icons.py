#!/usr/bin/env python3
"""
GameOpti — Couch Mode icon set generator.

Jedna definicja geometrii -> trzy warianty eksportu:
  svg/        dwukolorowe (baza jasna + akcent mint), atrybuty inline (bez CSS) — bezpieczne dla Qt SVG
  svg-mono/   jednokolorowe, stroke="currentColor" (do kolorowania w aplikacji)
  (HTML reference sheet używa tej samej geometrii przez klasy .b / .a)

Zmiana kolorów: edytuj BASE i ACCENT poniżej i uruchom skrypt ponownie.
Siatka: viewBox 48x48, obrys 4, zaokrąglone końcówki i łączenia, pole robocze linii środkowej 6..42.
"""
import math
import os
import sys

BASE = "#E6ECEF"    # jasny biało-szary
ACCENT = "#45D0B5"  # subtelny turkus/mint
STROKE = 4

HERE = os.path.dirname(os.path.abspath(__file__))


def f(n):
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def pol(cx, cy, r, deg):
    """Punkt na okręgu; kąt matematyczny (CCW), oś Y ekranu w dół."""
    a = math.radians(deg)
    return cx + r * math.cos(a), cy - r * math.sin(a)


# ---------------------------------------------------------------- geometria
def gear_path(cx=24, cy=24, r_root=14.5, r_top=19, teeth=6, root_hw=13, top_hw=7, start=90):
    step = 360 / teeth
    d = []
    for i in range(teeth):
        t = start + i * step
        p_r1 = pol(cx, cy, r_root, t - root_hw)
        p_t1 = pol(cx, cy, r_top, t - top_hw)
        p_t2 = pol(cx, cy, r_top, t + top_hw)
        p_r2 = pol(cx, cy, r_root, t + root_hw)
        nxt = pol(cx, cy, r_root, t + step - root_hw)
        if i == 0:
            d.append(f"M{f(p_r1[0])} {f(p_r1[1])}")
        d.append(f"L{f(p_t1[0])} {f(p_t1[1])}")
        d.append(f"L{f(p_t2[0])} {f(p_t2[1])}")
        d.append(f"L{f(p_r2[0])} {f(p_r2[1])}")
        d.append(f"A{r_root} {r_root} 0 0 0 {f(nxt[0])} {f(nxt[1])}")
    d.append("Z")
    return "".join(d)


def refresh_geometry(cx=24, cy=24, r=17, a0=55, a1=-25, head=8.5, head_deg=42):
    """Łuk ~280° z grotem na końcu (CCW)."""
    sx, sy = pol(cx, cy, r, a0)
    ex, ey = pol(cx, cy, r, a1)
    arc = f"M{f(sx)} {f(sy)}A{r} {r} 0 1 0 {f(ex)} {f(ey)}"
    # kierunek jazdy w punkcie końcowym: styczna CCW w układzie matematycznym
    # to (-sin, cos); na ekranie oś Y jest odwrócona => (-sin, -cos)
    th = math.radians(a1)
    tx, ty = -math.sin(th), -math.cos(th)
    bx, by = -tx, -ty  # wstecz
    pts = []
    for sgn in (+1, -1):
        rot = math.radians(sgn * head_deg)
        rx = bx * math.cos(rot) - by * math.sin(rot)
        ry = bx * math.sin(rot) + by * math.cos(rot)
        pts.append((ex + head * rx, ey + head * ry))
    headpath = f"M{f(pts[0][0])} {f(pts[0][1])}L{f(ex)} {f(ey)}L{f(pts[1][0])} {f(pts[1][1])}"
    return arc, headpath


def gauge_geometry(cx=24, cy=26, r=18, a0=220, a1=-40):
    sx, sy = pol(cx, cy, r, a0)
    ex, ey = pol(cx, cy, r, a1)
    arc = f"M{f(sx)} {f(sy)}A{r} {r} 0 1 1 {f(ex)} {f(ey)}"
    nx, ny = pol(cx, cy, 12.5, 55)
    return arc, (cx, cy), (nx, ny)


_refresh_arc, _refresh_head = refresh_geometry()
_gauge_arc, _gauge_c, _gauge_n = gauge_geometry()

# (rodzaj, tag, atrybuty)   rodzaj: "b" = baza, "a" = akcent
ICONS = {
    # ---- główna nawigacja ------------------------------------------------
    "library": {
        "label": "Biblioteka",
        "group": "nav",
        "el": [
            ("a", "rect", 'x="8" y="8" width="12" height="12" rx="3.5"'),
            ("b", "rect", 'x="28" y="8" width="12" height="12" rx="3.5"'),
            ("b", "rect", 'x="8" y="28" width="12" height="12" rx="3.5"'),
            ("b", "rect", 'x="28" y="28" width="12" height="12" rx="3.5"'),
        ],
    },
    "tasks": {
        "label": "Zadania",
        "group": "nav",
        "el": [
            ("a", "path", 'd="M7 12.5L11.5 17L19 8"'),
            ("b", "path", 'd="M27 12H42"'),
            ("b", "path", 'd="M13 24h.01"'),
            ("b", "path", 'd="M27 24H42"'),
            ("b", "path", 'd="M13 36h.01"'),
            ("b", "path", 'd="M27 36H42"'),
        ],
    },
    "updates": {
        "label": "Aktualizacje",
        "group": "nav",
        "el": [
            ("a", "path", 'd="M24 8V28"'),
            ("a", "path", 'd="M15 20L24 29L33 20"'),
            ("b", "path", 'd="M8 30V35Q8 40 13 40H35Q40 40 40 35V30"'),
        ],
    },
    "settings": {
        "label": "Ustawienia",
        "group": "nav",
        "el": [
            ("b", "path", f'd="{gear_path()}"'),
            ("a", "circle", 'cx="24" cy="24" r="5.5"'),
        ],
    },
    # ---- szczegóły gry ---------------------------------------------------
    "launch": {
        "label": "Uruchom",
        "group": "game",
        "el": [
            ("b", "circle", 'cx="24" cy="24" r="18"'),
            ("a", "path", 'd="M20 15.5L34 24L20 32.5Z"'),
        ],
    },
    "check-updates": {
        "label": "Sprawdź aktualizacje",
        "group": "game",
        "el": [
            ("b", "path", f'd="{_refresh_arc}"'),
            ("b", "path", f'd="{_refresh_head}"'),
            ("a", "path", 'd="M17.5 24.5L22 29L30.5 19.5"'),
        ],
    },
    "overview": {
        "label": "Przegląd",
        "group": "game",
        "el": [
            ("b", "path", 'd="M8 8V40H42"'),
            ("b", "path", 'd="M18 33V26"'),
            ("a", "path", 'd="M27 33V15"'),
            ("b", "path", 'd="M36 33V22"'),
        ],
    },
    "storage": {
        "label": "Pamięć masowa",
        "group": "game",
        "el": [
            ("b", "ellipse", 'cx="24" cy="13" rx="16" ry="6"'),
            ("b", "path", 'd="M8 13V35C8 38.3 15.2 41 24 41S40 38.3 40 35V13"'),
            ("a", "path", 'd="M8 24C8 27.3 15.2 30 24 30S40 27.3 40 24"'),
        ],
    },
    "optimization": {
        "label": "Optymalizacja",
        "group": "game",
        "el": [
            ("b", "path", f'd="{_gauge_arc}"'),
            ("a", "circle", f'cx="{_gauge_c[0]}" cy="{_gauge_c[1]}" r="2.5"'),
            ("a", "path", f'd="M{f(_gauge_c[0])} {f(_gauge_c[1])}L{f(_gauge_n[0])} {f(_gauge_n[1])}"'),
        ],
    },
    "optiscaler": {
        "label": "OptiScaler",
        "group": "game",
        "el": [
            ("b", "rect", 'x="6" y="6" width="36" height="36" rx="7"'),
            ("a", "rect", 'x="12" y="28" width="8" height="8" rx="2.5"'),
            ("a", "path", 'd="M25 23L35 13"'),
            ("a", "path", 'd="M27 13H35V21"'),
        ],
    },
    "narrator": {
        "label": "Lektor",
        "group": "game",
        "el": [
            (
                "b",
                "path",
                'd="M14 8H34A8 8 0 0 1 42 16V26A8 8 0 0 1 34 34H26L17 41V34H14A8 8 0 0 1 6 26V16A8 8 0 0 1 14 8Z"',
            ),
            ("a", "path", 'd="M16 19V25"'),
            ("a", "path", 'd="M24 15V29"'),
            ("a", "path", 'd="M32 18V26"'),
        ],
    },
}

ORDER = [
    "library", "tasks", "updates", "settings",
    "launch", "check-updates", "overview", "storage", "optimization", "optiscaler", "narrator",
]


# ---------------------------------------------------------------- render
def elements(name, mode):
    out = []
    for kind, tag, attrs in ICONS[name]["el"]:
        if mode == "twotone":
            paint = f'stroke="{ACCENT if kind == "a" else BASE}"'
        elif mode == "mono":
            paint = 'stroke="currentColor"'
        else:  # "class" — do inline HTML
            paint = f'class="{kind}"'
        out.append(f"<{tag} {attrs} {paint}/>")
    return out


def svg_file(name, mode):
    label = ICONS[name]["label"]
    body = "\n  ".join(elements(name, mode))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48" '
        f'fill="none" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round" '
        f'role="img" aria-label="{label}">\n'
        f"  <title>{label}</title>\n  {body}\n</svg>\n"
    )


def symbol(name):
    body = "".join(elements(name, "class"))
    return f'<symbol id="i-{name}" viewBox="0 0 48 48">{body}</symbol>'


def export(out_dir):
    for sub, mode in (("svg", "twotone"), ("svg-mono", "mono")):
        d = os.path.join(out_dir, sub)
        os.makedirs(d, exist_ok=True)
        for n in ORDER:
            with open(os.path.join(d, f"{n}.svg"), "w", encoding="utf-8") as fh:
                fh.write(svg_file(n, mode))


if __name__ == "__main__":
    export(sys.argv[1] if len(sys.argv) > 1 else HERE)
    print("OK:", len(ORDER), "ikon ->", ("svg/", "svg-mono/"))
