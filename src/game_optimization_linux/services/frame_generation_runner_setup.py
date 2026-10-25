"""Surgical, reversible Steam runner setup; never edit Steam's live config."""
import json
from pathlib import Path
import re
import shlex
from uuid import uuid4

from .frame_generation import profile_path
from .host_bootstrap import host_home_directory
from .mangohud import _atomic_write
from .runner_integration import RunnerIntegration


def _quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def launch_option_span(text, app_id):
    # Retain every unrelated byte, comments and duplicate VDF keys.
    tokens = list(re.finditer(r'//[^\n]*|"(?:\\.|[^"\\])*"|[{}]', text))
    stack, pending, app_close = [], None, None
    for token in tokens:
        raw = token.group()
        if raw.startswith("//"): continue
        if raw == "{":
            stack.append(pending or ""); pending = None
        elif raw == "}":
            if len(stack) >= 2 and stack[-1] == str(app_id) and stack[-2].casefold() == "apps":
                app_close = token.start()
            if stack: stack.pop()
            pending = None
        elif pending is None:
            pending = json.loads(raw)
        else:
            if len(stack) >= 2 and stack[-1] == str(app_id) and stack[-2].casefold() == "apps" and pending.casefold() == "launchoptions":
                return token.start(), token.end(), json.loads(raw), True
            pending = None
    if app_close is not None: return app_close, app_close, "", False
    raise ValueError("Steam has no settings entry for this game; open its Properties once, close Steam, then retry")


def assert_steam_closed(host):
    commands = host.host_process_commands()
    if commands is None or any(re.search(r"(?:^|/)(?:steam|steam\.sh)(?:\s|$)", line) for line in commands):
        raise ValueError("Close Steam completely before automatic runner setup; Steam keeps launch options in memory")


def configure_steam(game, host, root, *, restore=False):
    assert_steam_closed(host)
    record = profile_path(game.profile_key, root).with_name("frame-generation-runner-backup.json")
    if restore and not record.exists(): return
    home = host_home_directory()
    paths = {p.resolve() for suffix in (".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam")
             for p in (home / suffix / "userdata").glob("*/config/localconfig.vdf")}
    candidates = []
    for path in paths:
        if path.stat().st_size > 32 * 1024 * 1024: continue
        text = path.read_text()
        try: span = launch_option_span(text, game.steam_app_id)
        except ValueError: continue
        candidates.append((path, text, span))
    if len(candidates) != 1: raise ValueError("Cannot select a unique Steam user configuration for this game")
    path, text, (start, end, previous, existed) = candidates[0]
    runner = RunnerIntegration(steam_config_paths=[path])
    if restore:
        saved = json.loads(record.read_text())
        if saved["path"] != str(path) or previous != saved["applied"]:
            raise ValueError("Steam launch options changed independently; automatic restore stopped")
        replacement = _quoted(saved["previous"])
    else:
        if runner.steam_launch_option_status(game).configured: return
        tokens = shlex.split(previous)
        if (previous.count("%command%") > 1 or ("%command%" in previous and tokens.count("%command%") != 1)
                or any(c in previous for c in "\n\r;|<>`")):
            raise ValueError("Complex Steam launch options require review; nothing was overwritten")
        if "game-optimization-run" in previous:
            raise ValueError("Existing GameOpti runner options are invalid or use another AppID; nothing was overwritten")
        command = runner.steam_command(game.steam_app_id)
        applied = previous.replace("%command%", command, 1) if "%command%" in previous else (command + " " + previous).strip()
        # Preserve the complete original file as recovery data, and the owned field for restore.
        _atomic_write(record, json.dumps({"path": str(path), "previous": previous, "applied": applied,
                                         "originalFile": text}, ensure_ascii=False, indent=2))
        replacement = _quoted(applied)
    if not existed: replacement = '\t"LaunchOptions"\t' + replacement + "\n"
    if path.read_text() != text: raise ValueError("Steam configuration changed while preparing runner setup")
    _atomic_write(path, text[:start] + replacement + text[end:])
    if restore: record.rename(record.with_name("frame-generation-runner-restored-" + uuid4().hex + ".json"))
