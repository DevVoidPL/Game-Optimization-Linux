"""Adapted from GameOpti LXB backend/steam_integration.py (local 2026-11-13).

Applying GameOpti's runner to a Steam game's Launch Options, and undoing it.

Steam keeps a game's launch options in the signed-in account's
``userdata/<account id>/config/localconfig.vdf``::

    "UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {
        "<AppID>" { "LaunchOptions" "<text>" ... } } } } } }

Steam reads that file when it starts and writes it back while it runs and when
it exits, so a change made while Steam is running is lost (or races Steam's
own writes).  The workflow here is therefore:

1. resolve the Steam installation the game belongs to (native or Flatpak), the
   account (``config/loginusers.vdf``, MostRecent, else the only one) and the
   AppID;
2. if Steam is running: refuse while a game is running or something is
   downloading, otherwise ask Steam to quit (``steam -shutdown``) and wait;
3. edit only that one value in the text — every other byte of the file stays
   as it was — after a timestamped backup and with a journal, write it
   atomically, parse it again and check that nothing else changed; any failure
   puts the backup back;
4. optionally start Steam again.

The new value keeps what the user had: environment assignments and other
wrappers stay in front, arguments stay after ``%command%``; only
``"<runner>" --appid N --`` is inserted directly before ``%command%``.  An
existing GameOpti LXB runner or known tool wrappers (gamemoderun, mangohud,
gamescope …) are reported, and the user decides whether LXB takes over
(they are removed, and Restore puts them back) or nothing is changed.

Restore writes back the exact original text, or, if the user changed the
options in Steam after Apply, offers to remove only LXB's part.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Iterable, Sequence

from ..providers.keyvalues import parse_keyvalues
from .. import config as paths

logger = logging.getLogger(__name__)

STEAM_ID64_BASE = 76561197960265728
RUNNER_NAME = "game-optimization-run"
BETA_RUNNER_NAME = "gameopti-lxb-run"
TOOL_WRAPPERS = ("gamemoderun", "mangohud", "gamescope", "game-performance", "obs-gamecapture", "prime-run")
_BACKUP_KEEP = 10


class SteamIntegrationError(RuntimeError):
    """A user-readable reason the change was not made."""


# -- the text format -----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # "str" | "{" | "}"
    value: str
    start: int
    end: int


def _tokens(text: str) -> list[_Token]:
    from ..providers.keyvalues import tokenize_keyvalues
    parse_keyvalues(text)
    tokens: list[_Token] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n\ufeff":
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            if close < 0:
                raise SteamIntegrationError("Unterminated Steam comment")
            index = close + 2
        elif char in "{}":
            tokens.append(_Token(char, char, index, index + 1))
            index += 1
        elif char == '"':
            start = index
            index += 1
            out: list[str] = []
            while index < length and text[index] != '"':
                if text[index] == "\\" and index + 1 < length:
                    out.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(text[index + 1], text[index + 1]))
                    index += 2
                else:
                    out.append(text[index])
                    index += 1
            if index >= length:
                raise SteamIntegrationError("Steam's configuration file is damaged (unterminated text)")
            index += 1
            tokens.append(_Token("str", "".join(out), start, index))
        else:  # unquoted token (Steam never writes these here, but be tolerant)
            start = index
            while index < length and text[index] not in ' \t\r\n{}"':
                index += 1
            tokens.append(_Token("str", text[start:index], start, index))
    # The surgical editor must agree with the project's actual VDF parser.
    decoded = tuple(("open" if t.kind == "{" else "close" if t.kind == "}" else "value", t.value)
                    for t in tokens)
    if decoded != tokenize_keyvalues(text):
        raise SteamIntegrationError("Unsupported Steam configuration syntax; nothing was changed")
    return tokens


@dataclass(frozen=True, slots=True)
class _Node:
    key: _Token
    open: int  # index of "{" token
    close: int  # index of "}" token


def _children(tokens: list[_Token], open_index: int) -> tuple[list[_Node], list[tuple[_Token, _Token]], int]:
    """Blocks and values directly inside the block opened at ``open_index``."""

    nodes: list[_Node] = []
    values: list[tuple[_Token, _Token]] = []
    index = open_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "}":
            return nodes, values, index
        if token.kind != "str" or index + 1 >= len(tokens):
            raise SteamIntegrationError("Steam's configuration file is damaged (unexpected brace)")
        following = tokens[index + 1]
        if following.kind == "{":
            depth, scan = 1, index + 2
            while scan < len(tokens) and depth:
                if tokens[scan].kind == "{":
                    depth += 1
                elif tokens[scan].kind == "}":
                    depth -= 1
                scan += 1
            if depth:
                raise SteamIntegrationError("Steam's configuration file is damaged (unbalanced braces)")
            nodes.append(_Node(token, index + 1, scan - 1))
            index = scan
        elif following.kind == "str":
            values.append((token, following))
            index += 2
        else:
            raise SteamIntegrationError("Steam's configuration file is damaged")
    raise SteamIntegrationError("Steam's configuration file is damaged (unbalanced braces)")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _indent_before(text: str, position: int) -> str:
    line_start = text.rfind("\n", 0, position) + 1
    return text[line_start:position] if text[line_start:position].strip() == "" else ""


@dataclass(frozen=True, slots=True)
class AppLocation:
    app_open: int | None  # token index of the app's "{", or None when the app block is missing
    apps_close: int  # token index of "apps"' "}"
    value: _Token | None  # the LaunchOptions value token
    key: _Token | None


def locate_app(text: str, app_id: str) -> tuple[list[_Token], AppLocation]:
    tokens = _tokens(text)
    if len(tokens) < 3 or tokens[0].kind != "str" or tokens[1].kind != "{":
        raise SteamIntegrationError("Steam's configuration file has an unexpected shape")
    open_index = 1
    for name in ("software", "valve", "steam", "apps"):
        nodes, _values, _close = _children(tokens, open_index)
        matches = [node for node in nodes if node.key.value.casefold() == name]
        if len(matches) > 1:
            raise SteamIntegrationError("Ambiguous Steam configuration path")
        found = next(iter(matches), None)
        if found is None:
            raise SteamIntegrationError("Steam has not written its per-game settings yet; start the game once in Steam")
        open_index = found.open
    apps_nodes, _values, apps_close = _children(tokens, open_index)
    matches = [node for node in apps_nodes if node.key.value == app_id]
    if len(matches) > 1:
        raise SteamIntegrationError("Duplicate Steam app entries")
    app = next(iter(matches), None)
    if app is None:
        return tokens, AppLocation(None, apps_close, None, None)
    _nodes, values, _close = _children(tokens, app.open)
    if sum(key.value.casefold() == "launchoptions" for key, value in values) > 1:
        raise SteamIntegrationError("Duplicate Steam Launch Options")
    for key, value in values:
        if key.value.casefold() == "launchoptions":
            return tokens, AppLocation(app.open, apps_close, value, key)
    return tokens, AppLocation(app.open, apps_close, None, None)


def read_launch_options(text: str, app_id: str) -> str | None:
    """The game's LaunchOptions, or None where Steam has none for it."""

    _tokens_, location = locate_app(text, app_id)
    return location.value.value if location.value is not None else None


def write_launch_options(text: str, app_id: str, value: str | None) -> str:
    """The same text with only this game's LaunchOptions set (None removes it)."""

    tokens, location = locate_app(text, app_id)
    if location.value is not None:
        if value is None:
            line_start = text.rfind("\n", 0, location.key.start) + 1
            line_end = text.find("\n", location.value.end)
            suffix_end = len(text) if line_end < 0 else line_end
            if text[line_start:location.key.start].strip() or text[location.value.end:suffix_end].strip():
                return text[:location.key.start] + text[location.value.end:]
            line_end = len(text) if line_end < 0 else line_end + 1
            return text[:line_start] + text[line_end:]
        return text[: location.value.start] + f'"{_escape(value)}"' + text[location.value.end:]
    if value is None:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    if location.app_open is not None:
        brace = tokens[location.app_open]
        close = tokens[_children(tokens, location.app_open)[2]]
        indent = _indent_before(text, close.start) + "\t"
        insert_at = text.rfind("\n", 0, close.start) + 1
        line = f'{indent}"LaunchOptions"\t\t"{_escape(value)}"{newline}'
        if insert_at <= brace.end:  # "{ }" on one line
            return text[: brace.end] + newline + line + text[brace.end:]
        return text[:insert_at] + line + text[insert_at:]
    close = tokens[location.apps_close]
    outer = _indent_before(text, close.start)
    inner = outer + "\t"
    block = (f'{inner}"{app_id}"{newline}{inner}{{{newline}{inner}\t"LaunchOptions"\t\t"{_escape(value)}"{newline}{inner}}}{newline}')
    insert_at = text.rfind("\n", 0, close.start) + 1
    if text[insert_at:close.start].strip():
        insert_at = close.start
    return text[:insert_at] + block + text[insert_at:]


def _flatten(value, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], str]:
    out: dict[tuple[str, ...], str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            out.update(_flatten(child, (*prefix, str(key).casefold())))
    else:
        out[prefix] = str(value)
    return out


def changed_paths(before: str, after: str) -> set[tuple[str, ...]]:
    one, two = _flatten(parse_keyvalues(before)), _flatten(parse_keyvalues(after))
    return {key for key in one.keys() | two.keys() if one.get(key) != two.get(key)}


# -- the launch options themselves -----------------------------------------------------

@dataclass(frozen=True, slots=True)
class OptionsAnalysis:
    raw: str
    has_command: bool
    lxb: bool
    beta: bool
    other_runner: str
    tool_wrappers: tuple[str, ...]


def analyse_options(raw: str, runner: Path) -> OptionsAnalysis:
    from .runner_integration import is_gameopti_appimage
    try:
        tokens = shlex.split(raw or "", posix=True)
    except ValueError:
        tokens = (raw or "").split()
    names = [Path(token).name for token in tokens]
    command_index = tokens.index("%command%") if "%command%" in tokens else len(tokens)
    before = names[:command_index]
    other = ""
    for token in tokens[:command_index]:
        name = Path(token).name
        if name.endswith("-run") and name not in (RUNNER_NAME, BETA_RUNNER_NAME) and "/" in token:
            other = token
    return OptionsAnalysis(
        raw=raw or "",
        has_command="%command%" in tokens,
        lxb=RUNNER_NAME in before or any(is_gameopti_appimage(token) for token in tokens[:command_index]),
        beta=BETA_RUNNER_NAME in before,
        other_runner=other,
        tool_wrappers=tuple(name for name in before if name in TOOL_WRAPPERS),
    )


def runner_prefix(runner: Path, app_id: str) -> str:
    return f'"{runner}" --appid {app_id} --'


_RUNNER_SEGMENT = r'(?:"[^"]*{name}"|\S*{name})\s+--appid\s+\d+\s+--\s+'


def strip_runner(raw: str, name: str) -> str:
    return re.sub(_RUNNER_SEGMENT.format(name=re.escape(name)), "", raw)


def strip_tool_wrappers(raw: str) -> str:
    text = raw
    # gamescope [its options] -- before %command% (or before a runner)
    text = re.sub(r'(?:^|(?<=\s))(?:"[^"]*gamescope"|\S*gamescope)\b(?:(?!%command%).)*?\s--\s+', "", text)
    for wrapper in ("gamemoderun", "mangohud", "game-performance", "obs-gamecapture"):
        text = re.sub(rf'(?:^|(?<=\s))(?:"[^"]*{wrapper}"|\S*/{wrapper}|{wrapper})\s+(?=\S)', "", text)
    return text


def compose_options(raw: str | None, runner: Path, app_id: str, *, take_over: bool) -> str:
    """Insert the runner without changing ordinary arguments or known wrappers."""
    if not str(app_id).isascii() or not str(app_id).isdecimal():
        raise SteamIntegrationError("Invalid Steam AppID")
    text = (raw or "").strip()
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        raise SteamIntegrationError("Ambiguous Launch Options quoting") from error
    if any(c in text for c in (";", "|", "&", "`", "\n", "\r", "<", ">", "$", "\\")) or text.count("%command%") > 1:
        raise SteamIntegrationError("Complex shell Launch Options are preserved; automatic setup refused")
    from .runner_integration import RunnerIntegration, is_gameopti_appimage
    images = [token for token in tokens[:tokens.index("%command%") if "%command%" in tokens else len(tokens)]
              if is_gameopti_appimage(token)]
    if images:
        parsed = RunnerIntegration._parse_launch_options(text, app_id)
        if not parsed.configured or len(images) != 1:
            raise SteamIntegrationError("Existing GameOpti AppImage has a different AppID or unsupported syntax")
        executable = re.escape(images[0])
        segment = rf'(?:"{executable}"|\'{executable}\'|{executable})\s+--appid\s+{re.escape(app_id)}\s+--(?=\s+%command%)'
        matches = list(re.finditer(segment, text))
        if len(matches) != 1:
            raise SteamIntegrationError("Ambiguous GameOpti AppImage command; nothing was changed")
        match = matches[0]
        return text[:match.start()] + runner_prefix(runner, app_id) + text[match.end():]
    analysis = analyse_options(text, runner)
    if "%command%" in tokens:
        prefix_tokens = tokens[:tokens.index("%command%")]
        while prefix_tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", prefix_tokens[0]):
            prefix_tokens = prefix_tokens[1:]
        allowed = {*TOOL_WRAPPERS, RUNNER_NAME, BETA_RUNNER_NAME, "env"}
        if prefix_tokens and Path(prefix_tokens[0]).name not in allowed:
            raise SteamIntegrationError("Unknown launch wrapper is preserved; configure it manually in Steam")
    if (analysis.beta or analysis.other_runner) and not take_over:
        raise SteamIntegrationError("Another runner is already configured; nothing was changed")
    if "game-optimization-run" in text and not analysis.lxb:
        raise SteamIntegrationError("Unrecognized GameOpti runner syntax")
    if analysis.lxb:
        matches = list(re.finditer(_RUNNER_SEGMENT.format(name=re.escape(RUNNER_NAME)), text))
        if len(matches) != 1 or not re.search(r"--appid\s+" + re.escape(app_id) + r"\s+--\s+$", matches[0].group()):
            raise SteamIntegrationError("Existing GameOpti runner has a different AppID or unsupported syntax")
    text = strip_runner(text, RUNNER_NAME)  # never two of ours
    if take_over:
        text = strip_runner(text, BETA_RUNNER_NAME)
        text = strip_tool_wrappers(text)
    prefix = runner_prefix(runner, app_id)
    if "%command%" in text:
        return text.replace("%command%", f"{prefix} %command%", 1)
    # Without %command% Steam appends the options to the game's own command
    # line, so they are arguments.
    return f"{prefix} %command%" + (f" {text}" if text else "")


# -- Steam installations, accounts, processes --------------------------------------------

@dataclass(frozen=True, slots=True)
class SteamTarget:
    root: Path
    flatpak: bool
    account_id: str
    localconfig: Path


def steam_root_for(game_library: Path | None, data_source: str, home: Path | None = None) -> tuple[Path, bool]:
    home = Path.home() if home is None else home
    flatpak = data_source.casefold() == "steam flatpak"
    candidates = ([home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                   home / ".var/app/com.valvesoftware.Steam/data/Steam"] if flatpak else
                  [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root"])
    for candidate in candidates:
        if (candidate / "config" / "loginusers.vdf").is_file() or (candidate / "userdata").is_dir():
            resolved = candidate.resolve()
            sandbox = home / ".var/app/com.valvesoftware.Steam"
            return resolved, flatpak or resolved.is_relative_to(sandbox.resolve())
    raise SteamIntegrationError("The Steam installation of this game was not found")


def account_for(root: Path) -> str:
    users_file = root / "config" / "loginusers.vdf"
    userdata = root / "userdata"
    known = sorted(child.name for child in userdata.iterdir() if child.name.isdecimal()) if userdata.is_dir() else []
    try:
        users = parse_keyvalues(users_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        users = {}
    table = next((value for key, value in users.items() if str(key).casefold() == "users"), {}) \
        if isinstance(users, dict) else {}
    accounts: list[tuple[bool, int, str]] = []
    for steam_id, record in (table.items() if isinstance(table, dict) else ()):
        if not str(steam_id).isdecimal() or not isinstance(record, dict):
            continue
        account = str(int(steam_id) - STEAM_ID64_BASE)
        lowered = {str(k).casefold(): v for k, v in record.items()}
        recent = str(lowered.get("mostrecent", "0")) == "1"
        stamp = int(lowered.get("timestamp", "0") or 0) if str(lowered.get("timestamp", "0")).isdecimal() else 0
        accounts.append((recent, stamp, account))
    recent_accounts = [account for recent, _stamp, account in accounts if recent and account in known]
    if len(recent_accounts) == 1:
        return recent_accounts[0]
    if len(recent_accounts) > 1:
        raise SteamIntegrationError("Multiple MostRecent Steam accounts; nothing was changed")
    if len(known) == 1:
        return known[0]
    raise SteamIntegrationError("Which Steam account this game belongs to could not be determined")


def resolve_target(game_library: Path | None, data_source: str, home: Path | None = None) -> SteamTarget:
    root, flatpak = steam_root_for(game_library, data_source, home)
    account = account_for(root)
    localconfig = root / "userdata" / account / "config" / "localconfig.vdf"
    if not localconfig.is_file():
        raise SteamIntegrationError("Steam's per-account settings file was not found; start Steam once")
    return SteamTarget(root, flatpak, account, localconfig)


def _host_prefix() -> list[str]:
    if os.environ.get("FLATPAK_ID", "").strip():
        return ["flatpak-spawn", "--host"]
    return []


def process_commands() -> list[str]:
    """Command lines of this user's processes (the host's, from a Flatpak)."""

    if os.environ.get("FLATPAK_ID", "").strip():
        try:
            result = subprocess.run([*_host_prefix(), "ps", "-u", str(os.getuid()), "-o", "args=", "--width", "512"],
                                    capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise SteamIntegrationError("Steam process state could not be checked") from error
        if result.returncode:
            raise SteamIntegrationError("Steam process state could not be checked")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    commands = []
    uid = os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            commands.append((entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip())
        except OSError:
            continue
    return [command for command in commands if command]


def steam_running(commands: Sequence[str], flatpak: bool) -> bool:
    if commands is None:
        raise SteamIntegrationError("Steam process state is unknown; nothing was changed")
    for command in commands:
        first = command.split(" ", 1)[0]
        if Path(first).name in {"steam", "steam.sh", "steamwebhelper"}:
            return True
        if flatpak and "com.valvesoftware.Steam" in command and ("steam" in first or "bwrap" in first):
            return True
        if not flatpak and (first.endswith("/ubuntu12_32/steam") or first.endswith("steam.sh")):
            return True
    return False


def steam_readiness(commands: Sequence[str], flatpak: bool) -> str:
    """'absent' | 'starting' | 'ready' — whether Steam can take a launch request now.

    Steam reads ``-applaunch`` from its pipe only once the client is up; while
    the bootstrapper updates or the client is still logging in, a request sent
    through the pipe is lost (seen 2026-10-22: Steam restarted, updated itself
    for 14 s, and the request sent meanwhile never reached it).  The client's
    web helper starts when the client itself is up.
    """

    if not steam_running(commands, flatpak):
        return "absent"
    helper = any("steamwebhelper" in command.split(" ", 1)[0] for command in commands)
    return "ready" if helper else "starting"


_GAME_ACTION = re.compile(
    r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] GameAction \[AppID (\d+), ActionID \d+\] : "
    r"LaunchApp changed task to (\w+)")


def launch_tasks(root: Path, app_id: str, since: float, *, tail: int = 256 * 1024) -> list[str]:
    """Steam's own steps for launching ``app_id`` logged since ``since`` (epoch).

    From ``logs/console_log.txt``: proof that Steam took the request (and what
    it is doing: shader check, install script, …), which the exit status of
    ``steam -applaunch`` is not.
    """

    path = Path(root) / "logs" / "console_log.txt"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    tasks = []
    for line in text.splitlines():
        match = _GAME_ACTION.match(line)
        if not match or match.group(2) != str(app_id):
            continue
        try:
            when = time.mktime(time.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            continue
        if when >= since - 1:
            tasks.append(match.group(3))
    return tasks


def running_games(commands: Sequence[str]) -> list[str]:
    found = []
    for command in commands:
        match = re.search(r"\bSteamLaunch AppId=(\d+)", command)
        if match and "Install=1" not in command.split(" -- ", 1)[0]:
            found.append(match.group(1))
    return sorted(set(found))


def downloads_in_progress(libraries: Iterable[Path]) -> list[str]:
    busy = []
    for library in libraries:
        folder = Path(library) / "steamapps" / "downloading"
        try:
            for child in folder.iterdir():
                if child.is_dir() and child.name.isdecimal() and any(child.iterdir()):
                    busy.append(child.name)
        except OSError:
            continue
    return sorted(set(busy))


# -- the record of what was applied ---------------------------------------------------------

@dataclass
class Applied:
    app_id: str
    localconfig: str
    account_id: str
    original: str | None
    applied: str
    backup: str
    runner: str
    take_over: bool
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class SteamIntegration:
    def __init__(self, state_dir: Path | None = None, *, home: Path | None = None,
                 commands=process_commands, run=subprocess.run, popen=subprocess.Popen,
                 clock=time.monotonic, sleep=time.sleep, recover_on_init=True) -> None:
        self.state_dir = Path(state_dir or paths.STATE_DIR / "steam-integration")
        self.home = home
        self._commands = commands
        self._run = run
        self._popen = popen
        self._clock = clock
        self._sleep = sleep
        if recover_on_init:
            self.recover()

    # -- records ----------------------------------------------------------------

    def _record_path(self, app_id: str) -> Path:
        return self.state_dir / "applied" / f"{app_id}.json"

    def record(self, app_id: str) -> Applied | None:
        try:
            raw = json.loads(self._record_path(app_id).read_text(encoding="utf-8"))
            return Applied(**raw)
        except (OSError, ValueError, TypeError):
            return None

    def _save_record(self, record: Applied | None, app_id: str) -> None:
        path = self._record_path(app_id)
        if record is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic(path, json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n")

    @property
    def _journal(self) -> Path:
        return self.state_dir / "journal.json"

    # -- interrupted writes --------------------------------------------------------------

    def recover(self) -> str:
        """Finish or undo a write that was interrupted; answers what happened."""

        try:
            journal = json.loads(self._journal.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""
        except (OSError, ValueError):
            self._journal.unlink(missing_ok=True)
            return "journal unreadable; ignored"
        target = Path(journal["target"])
        backup = Path(journal["backup"])
        try:
            current = target.read_bytes()
        except OSError:
            current = b""
        if hashlib.sha256(current).hexdigest() == journal.get("written"):
            outcome = "completed"
        elif hashlib.sha256(current).hexdigest() == journal.get("before"):
            outcome = "not started"
        else:
            try:
                parse_keyvalues(current.decode("utf-8", errors="strict"))
                outcome = "changed by Steam; left alone"
            except (UnicodeError, ValueError):
                shutil.copy2(backup, target)
                outcome = "damaged write rolled back from backup"
        logger.warning("Steam integration: interrupted operation found: %s (%s)", outcome, target)
        self._journal.unlink(missing_ok=True)
        return outcome

    # -- state ----------------------------------------------------------------------------

    def status(self, app_id: str, library: Path | None, data_source: str, runner: Path) -> dict:
        try:
            target = resolve_target(library, data_source, self.home)
            text = target.localconfig.read_text(encoding="utf-8")
            raw = read_launch_options(text, app_id)
        except (OSError, UnicodeError, SteamIntegrationError, ValueError) as error:
            return {"kind": "unknown", "error": str(error), "raw": "", "record": self.record(app_id)}
        analysis = analyse_options(raw or "", runner)
        if raw:
            try:
                from .runner_integration import RunnerIntegration
                configured = RunnerIntegration._parse_launch_options(raw, app_id).configured
            except ValueError:
                configured = False
        else:
            configured = False
        if configured:
            kind = "lxb"
        elif analysis.lxb:
            kind = "unknown"
        elif analysis.beta:
            kind = "beta"
        elif raw and (analysis.tool_wrappers or analysis.other_runner):
            kind = "wrappers"
        elif raw:
            kind = "user"
        else:
            kind = "none"
        return {"kind": kind, "raw": raw or "", "analysis": analysis, "target": target,
                "record": self.record(app_id), "error": ""}

    def steam_state(self, data_source: str, libraries: Iterable[Path]) -> dict:
        commands = self._commands()
        flatpak = data_source.casefold() == "steam flatpak"
        return {"running": steam_running(commands, flatpak), "games": running_games(commands),
                "downloads": downloads_in_progress(libraries)}

    # -- Steam itself ------------------------------------------------------------------------

    def assert_runner_access(self, runner: Path, *, flatpak: bool) -> None:
        if not flatpak:
            return
        try:
            result = self._run([*_host_prefix(), "flatpak", "run", "--command=test",
                                "com.valvesoftware.Steam", "-x", str(runner)],
                               capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise SteamIntegrationError("Steam Flatpak runner access could not be verified") from error
        if result is None or result.returncode:
            raise SteamIntegrationError("Steam Flatpak has no verified access to the GameOpti runner; existing permissions were left unchanged")

    def close_steam(self, data_source: str, libraries: Iterable[Path], timeout: float = 60.0) -> None:
        state = self.steam_state(data_source, libraries)
        if state["games"]:
            raise SteamIntegrationError("A game is running in Steam; close it first")
        if state["downloads"]:
            raise SteamIntegrationError("Steam is downloading or updating; wait until it finishes")
        if not state["running"]:
            return
        flatpak = data_source.casefold() == "steam flatpak"
        command = ["flatpak", "run", "com.valvesoftware.Steam", "-shutdown"] if flatpak else ["steam", "-shutdown"]
        logger.info("Asking Steam to quit: %s", command)
        try:
            self._run([*_host_prefix(), *command], capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise SteamIntegrationError(f"Steam could not be asked to quit: {error}") from error
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if not steam_running(self._commands(), flatpak):
                self._sleep(1.0)  # its last write of the configuration
                return
            self._sleep(1.0)
        raise SteamIntegrationError("Steam did not quit within a minute; nothing was changed")

    def start_steam(self, data_source: str) -> None:
        flatpak = data_source.casefold() == "steam flatpak"
        command = ["flatpak", "run", "com.valvesoftware.Steam", "-silent"] if flatpak else ["steam", "-silent"]
        try:
            self._popen([*_host_prefix(), *command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        except OSError as error:
            raise SteamIntegrationError(f"Steam could not be started: {error}") from error

    # -- writes --------------------------------------------------------------------------------

    @staticmethod
    def _atomic(path: Path, text: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                shutil.copymode(path, temporary)
            except OSError:
                pass
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _write(self, target: SteamTarget, app_id: str, value: str | None, *, expected=None) -> tuple[str | None, str, Path]:
        """Set one game's LaunchOptions; answers (previous value, new text, backup)."""

        if target.localconfig.stat().st_size > 32 * 1024 * 1024:
            raise SteamIntegrationError("Steam configuration exceeds the safety size limit")
        original = target.localconfig.read_bytes()
        if expected is not None and original != expected:
            raise SteamIntegrationError("Steam configuration changed while preparing the operation")
        text = original.decode("utf-8")
        previous = read_launch_options(text, app_id)
        updated = write_launch_options(text, app_id, value)
        changed = changed_paths(text, updated)
        allowed = {("userlocalconfigstore", "software", "valve", "steam", "apps", app_id, "launchoptions")}
        if not changed <= allowed:
            raise SteamIntegrationError("Refusing a write that would change other Steam settings")
        if read_launch_options(updated, app_id) != value:
            raise SteamIntegrationError("The new launch options did not read back correctly")
        backups = self.state_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backups / f"localconfig-{target.account_id}-{stamp}.vdf"
        shutil.copy2(target.localconfig, backup)
        journal = {"target": str(target.localconfig), "backup": str(backup),
                   "before": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "written": hashlib.sha256(updated.encode("utf-8")).hexdigest()}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._atomic(self._journal, json.dumps(journal))
        try:
            if steam_running(self._commands(), target.flatpak):
                raise SteamIntegrationError("Steam started while preparing the change")
            if target.localconfig.read_bytes() != original:
                raise SteamIntegrationError("Steam configuration changed; write stopped")
            self._atomic(target.localconfig, updated)
            check = target.localconfig.read_bytes().decode("utf-8")
            if read_launch_options(check, app_id) != value or changed_paths(text, check) - allowed:
                raise SteamIntegrationError("The written file did not verify")
        except BaseException:
            # Never overwrite an independent change discovered before our write.
            if target.localconfig.read_bytes() == updated.encode("utf-8"):
                shutil.copy2(backup, target.localconfig)
            self._journal.unlink(missing_ok=True)
            raise
        self._journal.unlink(missing_ok=True)
        old = sorted(backups.glob(f"localconfig-{target.account_id}-*.vdf"))
        for stale in old[:-_BACKUP_KEEP]:
            stale.unlink(missing_ok=True)
        return previous, updated, backup

    def apply(self, app_id: str, library: Path | None, data_source: str, runner: Path, *, take_over: bool,
              libraries: Iterable[Path] = ()) -> Applied:
        if not runner.is_file() or not os.access(runner, os.X_OK):
            raise SteamIntegrationError(f"GameOpti's runner is missing: {runner}")
        target = resolve_target(library, data_source, self.home)
        if steam_running(self._commands(), target.flatpak):
            raise SteamIntegrationError("steam_running")
        original_bytes = target.localconfig.read_bytes()
        text = original_bytes.decode("utf-8")
        current = read_launch_options(text, app_id)
        analysis = analyse_options(current or "", runner)
        if analysis.beta and not take_over:
            raise SteamIntegrationError("GameOpti LXB's runner stays in charge of this game; nothing was changed")
        record = self.record(app_id)
        original = record.original if record is not None and analyse_options(current or "", runner).lxb else current
        value = compose_options(current, runner, app_id, take_over=take_over)
        _previous, _text, backup = self._write(target, app_id, value, expected=original_bytes)
        applied = Applied(app_id, str(target.localconfig), target.account_id, original, value, str(backup),
                          str(runner), take_over)
        try:
            self._save_record(applied, app_id)
        except BaseException:
            if target.localconfig.read_bytes() == _text.encode("utf-8"):
                self._atomic(target.localconfig, original_bytes.decode("utf-8"))
            raise
        logger.info("Steam launch options of %s: %r -> %r", app_id, current, value)
        return applied

    def restore(self, app_id: str, library: Path | None, data_source: str, *, mode: str = "original") -> str | None:
        """``original``: put back the text from before Apply; ``strip``: remove only LXB's runner."""

        target = resolve_target(library, data_source, self.home)
        if steam_running(self._commands(), target.flatpak):
            raise SteamIntegrationError("steam_running")
        record = self.record(app_id)
        original_bytes = target.localconfig.read_bytes()
        current = read_launch_options(original_bytes.decode("utf-8"), app_id)
        if mode == "original":
            if record is None:
                raise SteamIntegrationError("There is no record of what GameOpti changed for this game")
            if record.localconfig != str(target.localconfig) or record.account_id != target.account_id:
                raise SteamIntegrationError("The Steam account changed; automatic restore stopped")
            if current != record.applied:
                raise SteamIntegrationError("changed_since_apply")
            value = record.original
        else:
            stripped = strip_runner(current or "", RUNNER_NAME).strip()
            if stripped in ("", "%command%"):
                value = "" if current is not None else None
            else:
                value = stripped
        self._write(target, app_id, value, expected=original_bytes)
        self._save_record(None, app_id)
        logger.info("Steam launch options of %s restored to %r", app_id, value)
        return value
