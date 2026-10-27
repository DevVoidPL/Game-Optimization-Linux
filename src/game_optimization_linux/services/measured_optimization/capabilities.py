"""Which launch paths can run the measured workflow, with exact reasons.

Support is stated only for paths whose recording and launch integration
already exist and were verified for that launcher; another launcher's test
is never used as evidence.
"""

from __future__ import annotations

from typing import Any

from game_optimization_linux.models import Launcher

REASONS = {
    "steam_flatpak": "Recording through the GameOpti runner in Flatpak Steam has not been verified",
    "lutris": "Automatic recording is not available for Lutris games yet; GameOpti can only import a MangoHud log",
    "local_wine": "A compatible Proton runner has not been configured for this local Windows game",
    "local_in_flatpak": "The host runner used by the GameOpti Flatpak accepts only Steam AppIDs, so local games cannot be recorded from it",
    "manual": "This Custom entry has no stable profile identity",
    "manual_windows_no_runner": "This Windows executable has no configured Wine or Proton runner; GameOpti does not guess one",
    "manual_external_launcher": "This Custom entry starts another launcher, so the real game process cannot be identified",
    "manual_unknown_runner": "The configured runner is neither Wine nor a Proton script; GameOpti cannot verify how it starts the game",
    "manual_tree_unverified": "No host systemd user scope is available to track the game's process tree",
    "manual_config_missing": "The Custom game configuration was not found",
    "manual_identity_invalid": "This Custom entry has no persistent UUID identity",
    "manual_unavailable": "The Custom game files are missing or not runnable",
    "no_profile": "The game has no per-game profile key",
    "unavailable": "The game is not available in its library",
}


def launcher_kind(game: Any, *, flatpak_steam: bool = False, in_flatpak: bool = False,
                  local_wine: bool = False, manual: tuple[str, str] | None = None) -> tuple[str, str]:
    """(kind, unsupported reason code or "")."""

    if not getattr(game, "library_available", True):
        return "unavailable", "unavailable"
    launcher = game.launcher
    variant = str(getattr(game, "launcher_variant", "") or "native")
    if launcher is Launcher.STEAM:
        return ("steam_flatpak", "steam_flatpak") if flatpak_steam else ("steam_native", "")
    if launcher is Launcher.HEROIC:
        return ("heroic_flatpak" if variant == "flatpak" else "heroic_native"), ""
    if launcher is Launcher.LUTRIS:
        return "lutris", "lutris"
    if launcher is Launcher.MANUAL and str(game.data_source).casefold() == "local":
        if local_wine:
            return "local_wine", "local_wine"
        if in_flatpak:
            return "local_in_flatpak", "local_in_flatpak"
        return "local_native", ""
    if launcher is Launcher.MANUAL:
        # (kind, code) from the classified Custom configuration (manual.runner_info).
        return manual if manual is not None else ("manual", "manual")
    return "unknown", "no_profile"


def support(kind: str, code: str) -> dict[str, Any]:
    recording = {"steam_native": "GameOpti runner", "local_native": "GameOpti runner",
                 "heroic_native": "Heroic launcher measurement", "heroic_flatpak": "Heroic launcher measurement",
                 "manual_native": "GameOpti Custom launch", "manual_wine": "GameOpti Custom launch (Wine)",
                 "manual_proton": "GameOpti Custom launch (Proton)"}
    return {"kind": kind, "supported": not code, "code": code, "reason": REASONS.get(code, ""),
            "recording": recording.get(kind, ""),
            "activation": ("observed in the runner report" if kind in ("steam_native", "local_native")
                           else "observed in the GameOpti Custom launch plan" if kind.startswith("manual_")
                           else "configured in the launcher settings" if kind.startswith("heroic") else "")}


MATRIX = (
    ("steam_native", ""), ("steam_flatpak", "steam_flatpak"), ("heroic_native", ""), ("heroic_flatpak", ""),
    ("lutris", "lutris"), ("local_native", ""), ("local_wine", "local_wine"),
    ("local_in_flatpak", "local_in_flatpak"), ("manual_native", ""), ("manual_wine", ""), ("manual_proton", ""),
    ("manual", "manual_windows_no_runner"), ("manual", "manual_external_launcher"), ("manual", "manual_unknown_runner"),
    ("manual", "manual_tree_unverified"),
)

__all__ = ["MATRIX", "REASONS", "launcher_kind", "support"]
