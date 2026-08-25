#!/usr/bin/env python3
"""Synchronize the supplied Monolithic Core application icon assets.

The final icon package already contains hand-authored SVG and native PNG
variants. This helper deliberately performs no rendering or resizing.
"""

from __future__ import annotations

from pathlib import Path
import shutil


APP_ID = "io.github.DevVoidPL.GameOptimizationLinux"
PROJECT = Path(__file__).resolve().parent.parent
BRANDING_DIR = PROJECT / "src" / "game_optimization_linux" / "assets" / "branding"
RESOURCE_DIR = PROJECT / "src" / "game_optimization_linux" / "resources"
APP_ICON_DIR = RESOURCE_DIR / "app-icons"
HICOLOR_DIR = PROJECT / "data" / "icons" / "hicolor"
ICON_SIZES = (16, 32, 48, 64, 128, 256, 512)


def _copy(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main() -> int:
    for size in ICON_SIZES:
        source = BRANDING_DIR / f"game-optimization-linux-{size}x{size}.png"
        resource_target = APP_ICON_DIR / f"{size}x{size}.png"
        hicolor_target = (
            HICOLOR_DIR / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
        )
        _copy(source, resource_target)
        _copy(source, hicolor_target)

    _copy(
        BRANDING_DIR / "game-optimization-linux-512x512.png",
        RESOURCE_DIR / "GameOptimizationLinuxIcon.png",
    )
    _copy(
        BRANDING_DIR / "game-optimization-linux-app-icon.svg",
        HICOLOR_DIR / "scalable" / "apps" / f"{APP_ID}.svg",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
