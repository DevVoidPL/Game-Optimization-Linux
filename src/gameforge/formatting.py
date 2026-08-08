"""Side-effect-free formatting helpers shared by providers and presenters."""

from __future__ import annotations

from math import isfinite


def bytes_to_gib(value: int) -> float:
    """Convert a validated byte count to gibibytes."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("byte count must be a non-negative integer")
    return value / (1024**3)


def format_bytes(value: int | float) -> str:
    """Format a byte count using compact IEC units."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError("byte count must be a finite non-negative number")
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    unit_index = 0
    while amount >= 1024.0 and unit_index < len(units) - 1:
        amount /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(amount)} B"
    return f"{amount:.1f} {units[unit_index]}"


__all__ = ["bytes_to_gib", "format_bytes"]
