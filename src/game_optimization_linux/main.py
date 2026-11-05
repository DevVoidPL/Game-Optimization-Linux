"""Console entry point for Game Optimization Linux."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Start the application and return its process exit code."""

    from .app import run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
