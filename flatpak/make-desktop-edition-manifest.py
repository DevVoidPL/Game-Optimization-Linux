#!/usr/bin/env python3
"""Generate Desktop using the maintained QML UI and existing edition gate."""
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "io.github.DevVoidPL.GameOptimizationLinux.yml"
TARGET = HERE / "io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml"
ANCHOR = "      - python3 -m pip install --no-index --no-deps --no-build-isolation --prefix=${FLATPAK_DEST} .\n"


def main() -> int:
    text = SOURCE.read_text(encoding="utf-8")
    if text.count(ANCHOR) != 1:
        raise SystemExit("The pip install command of the application module was not found exactly once")
    # This checkout has no GTK frontend. Keep the maintained Qt UI/backend and
    # use the existing build-time edition gate, changing only Builder's copy.
    text = text.replace(ANCHOR,
                        "      - printf 'desktop\\n' > src/game_optimization_linux/edition.txt\n"
                        "      - rm -rf src/game_optimization_linux/qml/couch src/game_optimization_linux/assets/audio\n" + ANCHOR)
    text = text.replace("  - --device=input\n", "")
    TARGET.write_text("# Generated Qt Desktop Edition; same App ID and profiles.\n" + text, encoding="utf-8")
    print(TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
