"""Build-time edition of this installation.

Both Flatpak editions are built from the same source and install under the
same App ID, so user data and profile formats are shared. The Desktop Edition
build writes ``edition.txt`` next to this module and leaves out the Couch QML
and its assets; without that file (source checkouts, Couch Edition) the
complete application is used.
"""

from __future__ import annotations

from pathlib import Path

EDITIONS = ("couch", "desktop")
EDITION_FILE = Path(__file__).with_name("edition.txt")
EDITION_NAMES = {"couch": "Couch Edition", "desktop": "Desktop Edition"}


def _read_edition(path: Path = EDITION_FILE) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip().casefold()
    except OSError:
        return "couch"
    if value not in EDITIONS:
        raise RuntimeError(f"Unsupported GameOpti edition marker: {value!r}")
    return value


EDITION = _read_edition()


def couch_available(edition: str = EDITION) -> bool:
    """Couch Mode, controller navigation and controller settings."""
    return edition == "couch"


__all__ = ["EDITION", "EDITIONS", "EDITION_FILE", "EDITION_NAMES", "couch_available"]
