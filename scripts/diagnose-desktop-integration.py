#!/usr/bin/env python3
"""Read-only diagnostics for the Game Optimization desktop identity and icon assets."""

from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

from game_optimization_linux.app import _set_application_metadata  # noqa: E402
from game_optimization_linux.config import APP_ID  # noqa: E402


ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def data_roots() -> tuple[Path, ...]:
    configured_home = os.environ.get("XDG_DATA_HOME", "").strip()
    home = Path(configured_home).expanduser() if configured_home else Path.home() / ".local/share"
    configured_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    roots = [home]
    roots.extend(Path(value) for value in configured_dirs.split(":") if value)
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


def alpha_bounds(path: Path) -> str:
    image = QImage(str(path))
    if image.isNull():
        return "invalid image"
    left = image.width()
    top = image.height()
    right = -1
    bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() == 0:
                continue
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    bounds = "empty" if right < 0 else f"{left},{top}..{right},{bottom}"
    return f"{image.width()}x{image.height()} alpha_bbox={bounds}"


def main() -> int:
    _set_application_metadata()
    print(f"desktopFileName: {QGuiApplication.desktopFileName()}")

    roots = data_roots()
    desktop_name = f"{APP_ID}.desktop"
    desktop_matches = [root / "applications" / desktop_name for root in roots]
    installed_desktop = next((path for path in desktop_matches if path.is_file()), None)
    print(f"desktop entry: {installed_desktop or 'NOT FOUND'}")

    for size in ICON_SIZES:
        relative = Path("icons/hicolor") / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
        installed = next((root / relative for root in roots if (root / relative).is_file()), None)
        if installed is None:
            print(f"icon {size}x{size}: NOT FOUND")
        else:
            print(f"icon {size}x{size}: {installed} ({alpha_bounds(installed)})")

    source_icon = PROJECT_ROOT / "src/game_optimization_linux/resources/GameOptimizationLinuxIcon.png"
    print(f"source icon: {source_icon} ({alpha_bounds(source_icon)})")
    contact_sheet = PROJECT_ROOT / "reports/ui/game-optimization-icon-contact-sheet.png"
    print(f"contact sheet: {contact_sheet} ({alpha_bounds(contact_sheet)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
