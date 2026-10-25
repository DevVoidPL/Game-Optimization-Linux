"""Per-game feature availability derived from the game's actual data.

The details page asks this instead of guessing from the source (Steam ID,
launcher name).  Every entry is ``{"available": bool, "reason": str}``; a
feature that is not available always carries a concrete reason so the UI can
show it instead of hiding the feature.  Only cheap checks run here (stat and
reading the launcher's small per-game config); expensive work such as the
executable scan or the launcher's Flatpak sandbox check happens later, off the
GUI thread.
"""

from __future__ import annotations

import os
from typing import Any

from game_optimization_linux.models import Game, Launcher

from .launcher_integration import LauncherFacts


def _entry(available: bool, reason: str = "") -> dict[str, Any]:
    return {"available": bool(available), "reason": "" if available else reason}


def custom_manual_game(game: Game) -> bool:
    return bool(
        game.launcher is Launcher.MANUAL
        and game.id.startswith("manual-")
        and game.data_source.casefold() == "manual"
    )


def game_capabilities(
    game: Game,
    *,
    launcher_facts: LauncherFacts | None = None,
    launcher_writable: tuple[bool, str] | None = None,
    launcher_tools: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Availability of each optional feature for one game."""

    launcher_game = game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}
    launcher = game.launcher.value
    try:
        directory_ok = bool(game.library_available and os.path.isdir(game.install_path))
    except (OSError, TypeError, ValueError):
        directory_ok = False
    directory = _entry(directory_ok, "The game directory is unavailable")

    executable_path = str(game.executable_path or "")
    if game.launcher is Launcher.STEAM or game.data_source.casefold() == "local":
        executable = _entry(directory_ok, "The game directory is unavailable")
    elif executable_path and os.path.isfile(executable_path):
        executable = _entry(True)
    else:
        executable = _entry(
            False,
            f"{launcher} did not report an existing executable for this game",
        )

    if game.launcher is Launcher.STEAM:
        runner = _entry(True)
    elif launcher_game:
        prefix_ok = game.wine_prefix is None or os.path.isdir(game.wine_prefix)
        if launcher_facts is not None and launcher_facts.proton_runner is True and prefix_ok:
            runner = _entry(True)
        elif not prefix_ok:
            runner = _entry(False, f"The Wine prefix reported by {launcher} is unavailable")
        else:
            runner = _entry(False, f"{launcher} does not report a Proton runner for this game")
    else:
        runner = _entry(False, "No Wine/Proton runner is configured for this game")

    profile_key = game.profile_key
    if custom_manual_game(game):
        reason = "Optimization is not available for custom games yet"
        optimization = _entry(False, reason)
        optiscaler = _entry(False, reason)
        launcher_config = _entry(False, reason)
    elif launcher_game:
        if launcher_facts is None or not launcher_facts.config_found:
            launcher_config = _entry(
                False,
                (launcher_facts.reason if launcher_facts is not None else "")
                or f"The {launcher} settings for this game were not found",
            )
        elif launcher_writable is not None and not launcher_writable[0]:
            launcher_config = _entry(False, launcher_writable[1])
        else:
            launcher_config = _entry(True)
        if not profile_key:
            optimization = _entry(False, "The game has no stable profile key")
        else:
            optimization = launcher_config
        if not profile_key:
            optiscaler = _entry(False, "The game has no stable profile key")
        elif not directory_ok:
            optiscaler = directory
        elif not launcher_config["available"]:
            # Without the launcher config the DLL override cannot reach the game.
            optiscaler = _entry(
                False,
                f"OptiScaler needs the {launcher} game settings to load: {launcher_config['reason']}",
            )
        else:
            optiscaler = _entry(True)
    else:
        # Steam and scanned local games: unchanged behaviour (the tabs report
        # their own detailed state).
        launcher_config = _entry(False, "The game is not started by Heroic or Lutris")
        optimization = _entry(True)
        optiscaler = _entry(True)
    mangohud = dict(optimization)
    tools: dict[str, Any] = {}
    pending = False
    if launcher_game and launcher_tools is not None:
        # What matters is the environment that really starts the game: for a
        # Flatpak launcher its own sandbox, not the host or GameOpti.
        tools = {name: dict(entry) for name, entry in launcher_tools.items()}
        pending = any(entry.get("pending") for entry in tools.values())
        mango = tools.get("mangohud")
        if mangohud["available"] and mango is not None and not mango.get("available"):
            mangohud = _entry(False, str(mango.get("reason") or "MangoHud is not available"))
    return {
        "gameDirectory": directory,
        "executable": executable,
        "runner": runner,
        "launcherConfig": launcher_config,
        "optimization": optimization,
        "mangohud": mangohud,
        "optiscaler": optiscaler,
        "tools": tools,
        "pending": pending,
    }


__all__ = ["custom_manual_game", "game_capabilities"]
