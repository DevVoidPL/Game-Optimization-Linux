"""Before-launch / after-exit commands for launchers that only offer a wrapper.

The helper is a standalone stdlib Python script placed where the launcher runs
its wrapper chain. It runs the before-launch commands directly, then the exact
argv the launcher supplied, becomes a child subreaper so orphaned game
processes are reparented to it, waits for that process tree, runs the
after-exit commands and returns the game's status. Nothing is parsed by a shell.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from game_optimization_linux.models import Game, Launcher
from game_optimization_linux.models.mangohud import validate_game_key

HELPER_NAME = "gameopti-launch-hooks"
HELPER_VERSION = 1
WINESERVER_GRACE = 10.0

HELPER_SOURCE = '''#!/usr/bin/env python3
"""GameOpti launch hooks (version %(version)d). Usage: HELPER --hooks FILE -- ARGV..."""
import ctypes, json, os, signal, subprocess, sys, time

def say(text):
    print("gameopti-launch-hooks: " + text, file=sys.stderr, flush=True)

def run(entry):
    try:
        return subprocess.run(list(entry["argv"]), stdin=subprocess.DEVNULL, timeout=int(entry["timeout"]),
                              check=False, shell=False).returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127

def children(me):
    result = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                with open("/proc/%%s/stat" %% name) as handle:
                    data = handle.read()
                comm, rest = data[data.index("(") + 1:data.rindex(")")], data[data.rindex(")") + 2:].split()
                if int(rest[1]) == me:
                    result.append((int(name), comm))
            except (OSError, ValueError, IndexError):
                continue
    return result

def wait_tree(grace):
    me, since = os.getpid(), None
    while True:
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                break
        remaining = children(me)
        if not remaining:
            return
        if all(comm == "wineserver" for _pid, comm in remaining):
            since = since or time.monotonic()
            if time.monotonic() - since > grace:
                return
        else:
            since = None
        time.sleep(0.5)

def main(argv):
    if len(argv) < 5 or argv[1] != "--hooks" or argv[3] != "--":
        say("usage: --hooks FILE -- ARGV")
        return 2
    with open(argv[2], encoding="utf-8") as handle:
        hooks = json.load(handle)
    command = argv[4:]
    post_if_not_started = [e for e in hooks.get("post", []) if e.get("runIfNotStarted")]
    for index, entry in enumerate(hooks.get("pre", []), 1):
        status = run(entry)
        if status:
            if entry.get("onFailure") == "continue":
                say("before-launch command %%d failed (status %%d); continuing" %% (index, status))
                continue
            say("before-launch command %%d failed (status %%d); the game was not started" %% (index, status))
            for post in post_if_not_started:
                run(post)
            return 1
    ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    try:
        child = subprocess.Popen(command, shell=False)
    except OSError as error:
        say("the launch command could not start: %%s" %% error)
        for post in post_if_not_started:
            run(post)
        return 127
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, lambda signum, _frame: child.poll() is None and child.send_signal(signum))
    status = child.wait()
    wait_tree(%(grace)s)
    for index, entry in enumerate(hooks.get("post", []), 1):
        result = run(entry)
        if result:
            say("after-exit command %%d failed (status %%d)" %% (index, result))
    return status if status >= 0 else 128 - status

if __name__ == "__main__":
    sys.exit(main(sys.argv))
''' % {"version": HELPER_VERSION, "grace": WINESERVER_GRACE}


def support(game: Game, *, umu_active: bool | None = None) -> tuple[bool, str, str]:
    """(available, execution environment, reason) for wrapper-based hooks.

    Only paths where the helper is the ancestor of the real game process tree
    are offered. Tree waiting was checked against the launcher sources:
    Heroic 2.22.4 spawns wrapperOptions before the game command; Lutris 0.5.22
    prepends prefix_command inside its own lutris-wrapper subreaper.
    """
    if game.launcher is Launcher.LUTRIS:
        if game.launcher_variant == "flatpak":
            return False, "", "Lutris Flatpak is not installed here; its sandbox permissions and process tree were not verified"
        return True, "Lutris (host, inside lutris-wrapper)", ""
    if game.launcher is Launcher.HEROIC:
        if game.launcher_variant == "flatpak":
            if umu_active is not False:
                return False, "", ("Heroic Flatpak starts Proton through UMU, whose pressure-vessel container runs the "
                                   "game in a Flatpak sub-sandbox outside the wrapper's process tree; the game end "
                                   "cannot be awaited reliably. Available only when UMU is off for this game")
            return True, "Heroic Flatpak sandbox", ""
        return True, "Heroic (host)", ""
    return False, "", "Not a wrapper-based launcher"


def location(game: Game, home: Path) -> Path:
    key = validate_game_key(game.profile_key)
    if game.launcher is Launcher.HEROIC and game.launcher_variant == "flatpak":
        # Heroic Flatpak has ~/Games/Heroic:create; nothing else of GameOpti is visible there.
        return home / "Games/Heroic/.gameopti-launch-hooks" / key
    return home / ".local/share/game-optimization-linux/launch-hooks" / key


def hooks_document(profile: Any) -> dict[str, Any]:
    return {"version": HELPER_VERSION,
            "pre": [{"argv": list(c.argv), "timeout": c.timeout, "onFailure": c.on_failure} for c in profile.active_pre()],
            "post": [{"argv": list(c.argv), "timeout": c.timeout, "runIfNotStarted": c.run_if_not_started}
                     for c in profile.active_post()]}


def prefix(game: Game, profile: Any, home: Path, *, write: bool) -> list[str]:
    """Wrapper argv. Files are written only for Apply (status/preview write nothing)."""
    directory = location(game, home)
    # Content-addressed: a failed Apply never changes the hooks an earlier,
    # still-active launcher configuration points at.
    helper, hooks = directory / HELPER_NAME, directory / f"hooks-{digest(profile)}.json"
    if write:
        _atomic(helper, HELPER_SOURCE, 0o700)
        _atomic(hooks, json.dumps(hooks_document(profile), indent=2, ensure_ascii=False) + "\n", 0o600)
    return [str(helper), "--hooks", str(hooks), "--"]


def prune(game: Game, home: Path, keep: Any = ()) -> None:
    """Remove GameOpti hook files of this game except the ones the launcher still uses."""
    directory = location(game, home)
    if not directory.is_dir() or directory.is_symlink():
        return
    keep = {str(item) for item in keep}
    for path in directory.iterdir():
        owned = path.name == HELPER_NAME or (path.name.startswith("hooks-") and path.name.endswith(".json"))
        if owned and str(path) not in keep and path.is_file() and not path.is_symlink():
            path.unlink()
    if not keep:
        try:
            directory.rmdir()
        except OSError:
            pass


def digest(profile: Any) -> str:
    return hashlib.sha256(json.dumps(hooks_document(profile), sort_keys=True).encode()).hexdigest()[:12]


def _atomic(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"Refusing to replace a symlink: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
