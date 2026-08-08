"""Conservative classification of Steam tools and runtime packages."""

from __future__ import annotations

import re


_EXACT_TOOL_NAMES = {
    "shader pre-caching",
    "steam controller configs",
    "steam input",
    "steam linux runtime",
    "steam runtime",
    "steamworks common redistributables",
}

_PROTON_NAME = re.compile(
    r"^proton(?:\s+(?:experimental|hotfix|easyanticheat|battleye|next|\d+(?:\.\d+)*))"
    r"(?:\b|$)",
    re.IGNORECASE,
)


def is_steam_tool_name(name: str) -> bool:
    """Return ``True`` only for names that are confidently technical entries.

    The classifier deliberately keeps ambiguous entries visible. For example,
    a game whose title merely contains ``server`` is not hidden.
    """

    normalized = " ".join(str(name).strip().casefold().split())
    if not normalized:
        return False
    if normalized in _EXACT_TOOL_NAMES:
        return True
    if normalized.startswith(("steam linux runtime ", "steam runtime ")):
        return True
    if _PROTON_NAME.match(normalized):
        return True
    if normalized.startswith(("source sdk", "steamworks sdk")):
        return True
    if normalized.endswith(" dedicated server"):
        return True
    return False


__all__ = ["is_steam_tool_name"]
