"""Console entry point for Game Optimization Linux."""

from __future__ import annotations

from collections.abc import Sequence
import sys


def main(argv: Sequence[str] | None = None) -> int:
    """Start the application and return its process exit code."""

    arguments = list(argv) if argv is not None else list(sys.argv)
    if "--gtk" in arguments:
        from .gtk_frontend import run

        return run([argument for argument in arguments if argument != "--gtk"])
    from .app import run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
