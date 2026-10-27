"""Custom/Manual games in the measured optimization workflow.

Identity: every Custom/Manual entry already has a persistent ``manual-<UUID>``
id stored in ``manual-games-v1.json``; editing the name, executable or artwork
keeps it. The optimization key is derived from that UUID only (namespaced
like the custom-launch and GPU-memory keys), so it never depends on the name
or path and never collides with the Steam/Heroic/Lutris entry of the same
executable. Nothing is migrated or rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
from uuid import UUID

from game_optimization_linux.models import Launcher

_NAMESPACE = "optimization:"
WINE_NAMES = ("wine", "wine64", "wine-staging", "wine64-staging", "wine-development")
# Programs that start a different launcher instead of the game itself.
EXTERNAL_LAUNCHERS = ("steam", "heroic", "lutris", "flatpak", "xdg-open", "gio", "legendary", "bottles-cli",
                      "umu-run", "steam-runtime")
MANUAL_KINDS = ("manual_native", "manual_wine", "manual_proton")


def is_manual(game: Any) -> bool:
    return getattr(game, "launcher", None) is Launcher.MANUAL and str(getattr(game, "data_source", "")).casefold() == "manual"


def manual_uuid(game_id: str) -> str:
    if not str(game_id).startswith("manual-"):
        return ""
    raw = str(game_id).removeprefix("manual-")
    try:
        return raw if str(UUID(raw)) == raw else ""
    except ValueError:
        return ""


def optimization_key(game: Any) -> str:
    """Per-game optimization profile/evidence key ("" when there is none)."""

    if game is None:
        return ""
    if getattr(game, "profile_key", ""):
        return str(game.profile_key)
    if is_manual(game):
        uuid = manual_uuid(game.id)
        if uuid:
            return "local-" + hashlib.sha256((_NAMESPACE + uuid).encode()).hexdigest()[:24]
    return ""


def is_windows_executable(path: Path) -> bool:
    try:
        with Path(path).open("rb") as stream:
            return stream.read(2) == b"MZ"
    except OSError:
        return False


def _partial_hash(path: Path) -> str:
    try:
        resolved = Path(path).resolve(strict=True)
        stat = resolved.stat()
        digest = hashlib.sha256(f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        with resolved.open("rb") as stream:
            digest.update(stream.read(1024 * 1024))
        return digest.hexdigest()[:16]
    except OSError:
        return ""


def runner_info(configuration: Any, *, which=shutil.which, in_flatpak: bool | None = None) -> dict[str, Any]:
    """Classify the configured launch path. Never guesses a runner."""

    in_flatpak = bool(os.environ.get("FLATPAK_ID")) if in_flatpak is None else in_flatpak
    executable = Path(configuration.executable)
    runner = list(configuration.runner_command)
    windows = is_windows_executable(executable)
    name = Path(executable).name.casefold()
    if not runner:
        if windows or name.endswith(".exe"):
            return {"kind": "unsupported", "code": "manual_windows_no_runner", "runner": ""}
        if Path(name).stem in EXTERNAL_LAUNCHERS:
            return {"kind": "unsupported", "code": "manual_external_launcher", "runner": str(executable)}
        return {"kind": "manual_native", "code": "", "runner": "", "runnerName": "Native Linux"}
    first = Path(runner[0])
    candidates = [Path(item) for item in runner[:2]]
    proton = next((item for item in candidates if item.name == "proton"), None)
    if proton is not None:
        script = proton if proton.is_absolute() else None
        version = ""
        if script is not None:
            try:
                version = (script.parent / "version").read_text(errors="replace").split()[-1]
            except (OSError, IndexError):
                version = ""
        return {"kind": "manual_proton", "code": "", "runner": str(proton), "script": str(script or ""),
                "runnerName": script.parent.name if script else "proton",
                "runnerVersion": version or (_partial_hash(script) if script else ""),
                "argv": runner}
    if first.name in WINE_NAMES:
        resolved = first if first.is_absolute() else Path(which(first.name) or first.name)
        return {"kind": "manual_wine", "code": "", "runner": str(resolved), "runnerName": first.name,
                "runnerVersion": _partial_hash(resolved) if resolved.is_absolute() else "", "argv": runner}
    if first.stem in EXTERNAL_LAUNCHERS:
        return {"kind": "unsupported", "code": "manual_external_launcher", "runner": str(first)}
    return {"kind": "unsupported", "code": "manual_unknown_runner", "runner": str(first)}


def tree_tracking_available(*, which=shutil.which, in_flatpak: bool | None = None) -> bool:
    """The game tree is tracked by a host systemd user scope."""

    in_flatpak = bool(os.environ.get("FLATPAK_ID")) if in_flatpak is None else in_flatpak
    if in_flatpak:
        return bool(which("flatpak-spawn"))
    return bool(which("systemd-run")) and Path("/run/systemd/system").exists()


def launch_profile_hash(configuration: Any) -> str:
    """Hash of everything that shapes the Manual launch except name/artwork."""

    data = configuration.to_dict()
    for name in ("name", "portrait_artwork", "header_artwork", "id"):
        data.pop(name, None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


class ManualProcessMatcher:
    """Whether one process line is the configured game executable itself.

    Pre/post commands, custom wrappers, the runner, Gamescope, GameMode and
    systemd-run never match: native games must have the executable as argv[0];
    Windows games must appear as the Wine-rewritten ``X:\\...\\game.exe``.
    """

    def __init__(self, configuration: Any, kind: str) -> None:
        self.executable = str(Path(configuration.executable)).casefold()
        self.name = Path(configuration.executable).name.casefold()
        self.windows = kind in ("manual_wine", "manual_proton")

    def matches(self, line: str) -> bool:
        folded = str(line).strip().casefold()
        if self.windows:
            if len(folded) < 3 or folded[1:3] != ":\\":
                return False
            end = folded.find(self.name)
            while end >= 0:
                before = folded[end - 1] if end else ""
                after = folded[end + len(self.name):end + len(self.name) + 1]
                if before in ("\\", "/") and after in ("", " ", '"'):
                    return True
                end = folded.find(self.name, end + 1)
            return False
        return folded == self.executable or folded.startswith(self.executable + " ")

    def running(self, commands: Any) -> bool | None:
        if commands is None:
            return None
        return any(self.matches(line) for line in commands)


__all__ = [
    "EXTERNAL_LAUNCHERS", "MANUAL_KINDS", "ManualProcessMatcher", "is_manual", "is_windows_executable",
    "launch_profile_hash", "manual_uuid", "optimization_key", "runner_info", "tree_tracking_available",
]
