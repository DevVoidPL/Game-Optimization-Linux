"""Render the project desktop entry from central application metadata."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys

from .config import APP_ID, APP_NAME


def render_desktop_entry() -> str:
    return (
        "# Generated from game_optimization_linux.config; do not edit the name here.\n"
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={APP_NAME}\n"
        "Comment=Manage and inspect local Linux game libraries\n"
        "Exec=game-optimization-linux\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
        "StartupNotify=true\n"
        f"StartupWMClass={APP_ID}\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python -m game_optimization_linux.desktop_entry OUTPUT.desktop", file=sys.stderr)
        return 2
    output = Path(arguments[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_desktop_entry(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "render_desktop_entry"]
