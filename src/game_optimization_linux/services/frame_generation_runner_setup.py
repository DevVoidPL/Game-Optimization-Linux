"""Surgical, reversible Steam runner setup; never edit Steam's live config."""
import json
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4

from .frame_generation import profile_path
from .host_bootstrap import host_home_directory
from .mangohud import _atomic_write


def _quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class SteamAppEntryMissing(ValueError):
    """A valid user configuration has no entry for the requested AppID."""


def launch_option_span(text, app_id):
    from ..providers.keyvalues import parse_keyvalues, tokenize_keyvalues

    parse_keyvalues(text)
    # Retain every unrelated byte, comments and duplicate VDF keys.
    tokens = []
    decoded = []
    for token in re.finditer(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[{}]|[^\s{}]+', text.removeprefix("\ufeff")):
        raw = token.group()
        if raw.startswith(("//", "/*")):
            continue
        kind = "open" if raw == "{" else "close" if raw == "}" else "value"
        value = parse_keyvalues('"value" ' + raw)["value"] if raw.startswith('"') else raw
        tokens.append((token, value))
        decoded.append((kind, value))
    if tuple(decoded) != tokenize_keyvalues(text):
        raise ValueError("Unsupported Steam configuration syntax; nothing was changed")
    offset = 1 if text.startswith("\ufeff") else 0
    stack, pending, app_close = [], None, None
    matches = []
    app_count = 0
    for token, value in tokens:
        raw = token.group()
        if raw == "{":
            stack.append(pending or ""); pending = None
        elif raw == "}":
            if len(stack) >= 2 and stack[-1] == str(app_id) and stack[-2].casefold() == "apps":
                app_close = token.start() + offset
                app_count += 1
            if stack: stack.pop()
            pending = None
        elif pending is None:
            pending = value
        else:
            if len(stack) >= 2 and stack[-1] == str(app_id) and stack[-2].casefold() == "apps" and pending.casefold() == "launchoptions":
                matches.append((token.start() + offset, token.end() + offset, value, True))
            pending = None
    if app_count > 1 or len(matches) > 1:
        raise ValueError("Ambiguous Steam app entry or duplicate Launch Options; nothing was changed")
    if matches:
        return matches[0]
    if app_close is not None: return app_close, app_close, "", False
    raise SteamAppEntryMissing("Steam has no settings entry for this game; open its Properties once, close Steam, then retry")


def assert_steam_closed(host):
    commands = host.host_process_commands()
    if commands is None or any(re.search(r"(?:^|/)(?:steam|steam\.sh)(?:\s|$)", line) for line in commands):
        raise ValueError("Close Steam completely before automatic runner setup; Steam keeps launch options in memory")


def configure_empty_steam(game, host, root, *, integration=None):
    """Explicit opt-in only: fill an existing empty field, never merge commands.

    This is an offline localconfig edit, not a Steam API. Refuse ambiguity,
    unknown Steam process state, missing fields, and independent modifications.
    """
    from ..providers.keyvalues import parse_keyvalues
    from .runner_integration import RunnerIntegration

    assert_steam_closed(host)
    record = profile_path(game.profile_key, root).with_name("frame-generation-runner-backup.json")
    if record.exists():
        raise ValueError("A runner backup already exists; restore or inspect it before another setup")
    home = host_home_directory()
    sandbox = (home / ".var/app/com.valvesoftware.Steam").resolve()
    paths = {p.resolve() for suffix in (".local/share/Steam", ".steam/steam")
             for p in (home / suffix / "userdata").glob("*/config/localconfig.vdf")
             if not p.resolve().is_relative_to(sandbox)}
    candidates = []
    for path in paths:
        if path.stat().st_size > 32 * 1024 * 1024 or path.is_symlink():
            continue
        original = path.read_bytes()
        text = original.decode("utf-8")
        parse_keyvalues(text)  # Reject malformed data before considering a span.
        try:
            span = launch_option_span(text, game.steam_app_id)
        except SteamAppEntryMissing:
            continue
        candidates.append((path, original, text, span))
    if len(candidates) != 1:
        raise ValueError("Cannot select a unique Steam user configuration for this game")
    path, original, text, (start, end, previous, existed) = candidates[0]
    if previous:
        raise ValueError("Existing Launch Options are preserved; automatic setup only accepts an empty field")
    if not existed:
        raise ValueError("Open this game's Properties once in Steam, then close Steam and retry")
    runner = integration or RunnerIntegration()
    if not runner.status().installed:
        raise ValueError("The GameOpti runner is unavailable; no Steam settings were changed")
    command = runner.steam_command(game.steam_app_id)
    updated = text[:start] + _quoted(command) + text[end:]
    parse_keyvalues(updated)
    assert_steam_closed(host)
    if path.read_bytes() != original:
        raise ValueError("Steam configuration changed while preparing runner setup")
    record.parent.mkdir(parents=True, exist_ok=True)
    backup = record.with_name("steam-localconfig-" + uuid4().hex + ".vdf")
    with backup.open("xb") as stream:
        os.chmod(backup, 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    _atomic_write(record, json.dumps({"path": str(path), "previous": previous,
        "applied": command, "fullBackup": str(backup),
        "sha256": hashlib.sha256(original).hexdigest()}, indent=2))
    assert_steam_closed(host)
    if path.read_bytes() != original:
        raise ValueError("Steam configuration changed; backup kept, write stopped")
    _atomic_write(path, updated)


def configure_steam(game, host, root, *, restore=False):
    if not restore:
        raise ValueError("Use Steam Library > Properties > General > Launch Options; automatic Steam configuration writes are disabled")
    assert_steam_closed(host)
    record = profile_path(game.profile_key, root).with_name("frame-generation-runner-backup.json")
    if restore and not record.exists(): return
    home = host_home_directory()
    paths = {p.resolve() for suffix in (".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam")
             for p in (home / suffix / "userdata").glob("*/config/localconfig.vdf")}
    candidates = []
    for path in paths:
        if path.stat().st_size > 32 * 1024 * 1024: continue
        text = path.read_bytes().decode("utf-8")
        try: span = launch_option_span(text, game.steam_app_id)
        except SteamAppEntryMissing: continue
        candidates.append((path, text, span))
    if len(candidates) != 1: raise ValueError("Cannot select a unique Steam user configuration for this game")
    path, text, (start, end, previous, existed) = candidates[0]
    if restore:
        from ..providers.keyvalues import parse_keyvalues
        parse_keyvalues(text)
        saved = json.loads(record.read_text())
        if saved["path"] != str(path) or previous != saved["applied"]:
            raise ValueError("Steam launch options changed independently; automatic restore stopped")
        replacement = _quoted(saved["previous"])
    if not existed: replacement = '\t"LaunchOptions"\t' + replacement + "\n"
    assert_steam_closed(host)
    if path.read_bytes() != text.encode("utf-8"): raise ValueError("Steam configuration changed while preparing runner setup")
    _atomic_write(path, text[:start] + replacement + text[end:])
    if restore: record.rename(record.with_name("frame-generation-runner-restored-" + uuid4().hex + ".json"))
