from __future__ import annotations

import pytest

from gameforge.formatting import bytes_to_gib, format_bytes


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (5 * 1024**2, "5.0 MiB"),
        (72.4 * 1024**3, "72.4 GiB"),
    ],
)
def test_format_bytes(value: int | float, expected: str) -> None:
    assert format_bytes(value) == expected


def test_bytes_to_gib_and_invalid_values() -> None:
    assert bytes_to_gib(1024**3) == 1.0
    with pytest.raises(ValueError):
        bytes_to_gib(-1)
    with pytest.raises(ValueError):
        format_bytes(float("nan"))

