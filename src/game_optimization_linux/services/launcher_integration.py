"""Apply saved GameOpti settings to Heroic and Lutris per-game launcher configs.

Heroic and Lutris start games themselves, so the Steam Launch Options runner is
not used.  Instead the settings of one game are written into that launcher's own
per-game configuration (environment variables and a wrapper command).  Only the
entries recorded here are ever changed or removed; a backup of the original file
is kept and nothing is written while the launcher is running.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import stat
import sqlite3
import tempfile
from typing import Any
from urllib.parse import unquote

import yaml

from game_optimization_linux.models import Game, Launcher
from game_optimization_linux.models.mangohud import validate_game_key

from .optiscaler import merge_wine_dll_overrides

logger = logging.getLogger(__name__)

RECORD_VERSION = 2
_READABLE_RECORD_VERSIONS = frozenset({1, 2})
MAX_CONFIG_BYTES = 4 * 1024 * 1024
BACKUPS_KEPT = 5
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LAUNCHER_PROCESS = {
    Launcher.HEROIC: re.compile(r"(?:^|/)heroic(?:\s|$)|com\.heroicgameslauncher\.hgl", re.IGNORECASE),
    Launcher.LUTRIS: re.compile(r"(?:^|/)lutris(?:\s|$)|net\.lutris\.Lutris", re.IGNORECASE),
}


class LauncherSyncError(RuntimeError):
    """The launcher configuration cannot be read or changed safely."""


@dataclass(frozen=True, slots=True)
class LauncherPlan:
    """What the saved profiles ask the launcher to run: wrappers and environment."""

    prefix: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    # Reasons the plan cannot work for this launcher (e.g. the sandbox cannot
    # be checked at all).  Apply is refused while any blocker is present.
    blockers: tuple[str, ...] = ()
    # Per feature (gamemode, gamescope, mangohud, optiscaler, proton):
    # {"enabled": saved profile asks for it, "available": bool, "reason": str}.
    # A feature that is enabled but unavailable is left out of this plan on
    # its own; it does not block the other features.
    features: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # Features whose wrapper/environment is in ``prefix``/``environment``.
    included: tuple[str, ...] = ()
    # A background check (the launcher's Flatpak sandbox) has not answered
    # yet; Apply waits for it instead of blocking the GUI thread.
    pending: bool = False
    # The launcher's own per-game options GameOpti sets (Heroic only, from
    # ``_HEROIC_MANAGED_SETTINGS``); recorded with the previous value and
    # restored like the environment entries.
    settings: Mapping[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        content: dict[str, Any] = {
            "prefix": list(self.prefix), "environment": dict(sorted(self.environment.items())),
        }
        if self.settings:
            content["settings"] = dict(sorted(self.settings.items()))
        payload = json.dumps(content, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @property
    def empty(self) -> bool:
        return not self.prefix and not self.environment and not self.settings


# Heroic per-game options GameOpti may write, with their accepted types.
# ``disableUMU`` is Heroic's own "Disable UMU" switch (Game settings > Other).
_HEROIC_MANAGED_SETTINGS: Mapping[str, type] = {"disableUMU": bool, "launcherArgs": str}
# Lutris 0.5.22 game options GameOpti may write (game section).
_LUTRIS_MANAGED_SETTINGS: Mapping[str, type] = {"args": str}
# Options that hold the user's own game arguments: never overwritten when set.
ARGUMENT_SETTINGS = frozenset({"launcherArgs", "args"})


def launcher_argument_setting(game: Game) -> str:
    """Launcher option that carries game arguments, '' when not verified for this game type.

    Heroic 2.22.4: ``launcherArgs`` is appended to legendary's launch command and
    split with shlex (backend/storeManagers/legendary/games.ts). Other Heroic
    runners were not verified. Lutris 0.5.22: ``game.args`` is split with shlex
    for the wine and linux runners.
    """
    if game.launcher is Launcher.HEROIC:
        return "launcherArgs" if str(game.id).startswith("heroic-legendary-") else ""
    if game.launcher is Launcher.LUTRIS:
        return "args" if str(getattr(game, "runner", "")).casefold() in {"wine", "linux"} else ""
    return ""
_MISSING = "__gameopti_missing__"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _read_text(path: Path) -> tuple[str, tuple[int, int, int, int]]:
    """Read a regular, user-owned launcher file without following a symlink."""

    try:
        info = path.lstat()
    except OSError as error:
        raise LauncherSyncError(f"The launcher configuration is unavailable: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LauncherSyncError("The launcher configuration is not a regular file")
    if info.st_uid != os.getuid():
        raise LauncherSyncError("The launcher configuration belongs to another user")
    if info.st_size > MAX_CONFIG_BYTES:
        raise LauncherSyncError("The launcher configuration is too large")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        raise LauncherSyncError(f"The launcher configuration cannot be read: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(info):
            raise LauncherSyncError("The launcher configuration changed while it was read")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as error:
        raise LauncherSyncError(f"The launcher configuration cannot be read: {error}") from error
    finally:
        os.close(descriptor)
    if len(raw) > MAX_CONFIG_BYTES:
        raise LauncherSyncError("The launcher configuration is too large")
    try:
        return raw.decode("utf-8"), _identity(info)
    except UnicodeError as error:
        raise LauncherSyncError(f"The launcher configuration cannot be read: {error}") from error


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    if mode is None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise LauncherSyncError("The launcher configuration is not a regular file")
        mode = info.st_mode & 0o777
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _parse_dll_overrides(value: str) -> dict[str, str]:
    """``a,b=n,b;c=d`` -> ``{"a": "n,b", "b": "n,b", "c": "d"}``."""

    result: dict[str, str] = {}
    for part in str(value or "").split(";"):
        names, separator, mode = part.partition("=")
        if not separator:
            continue
        for name in names.split(","):
            name = name.strip()
            if name:
                result[name] = mode.strip()
    return result


class _ConfigFile:
    """Per-launcher read/modify/write of the managed entries."""

    launcher: Launcher

    def __init__(self, path: Path) -> None:
        self.path = path
        self.text = ""
        self.identity: tuple[int, int, int, int] | None = None

    def read(self) -> str:
        self.text, self.identity = _read_text(self.path)
        return self.text

    def unchanged_on_disk(self) -> bool:
        """The file is still the one that was loaded (closes the read/write race)."""
        try:
            return _identity(self.path.lstat()) == self.identity
        except OSError:
            return False

    def writable(self) -> bool:
        return os.access(self.path, os.W_OK) and os.access(self.path.parent, os.W_OK | os.X_OK)

    def load(self) -> Any:
        raise NotImplementedError

    def dump(self, document: Any) -> str:
        raise NotImplementedError

    def env(self, document: Any) -> dict[str, str]:
        raise NotImplementedError

    def set_env(self, document: Any, key: str, value: str | None) -> None:
        raise NotImplementedError

    def has_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> bool:
        raise NotImplementedError

    def add_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> None:
        raise NotImplementedError

    def remove_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> None:
        raise NotImplementedError

    def wrapper_for(self, prefix: Sequence[str]) -> dict[str, str]:
        raise NotImplementedError

    def foreign_wrapper(self, document: Any, wrapper: Mapping[str, str] | None) -> bool:
        """True when the game has a wrapper that GameOpti did not add."""
        raise NotImplementedError

    # Heroic passes WINEDLLOVERRIDES through its environment; the Lutris wine
    # runner rebuilds it from ``wine.overrides`` and ignores ``system.env``.
    def dll_overrides(self, document: Any) -> dict[str, str] | None:
        return None

    def set_dll_override(self, document: Any, name: str, mode: str | None) -> None:
        raise NotImplementedError

    def builtin_features(self, document: Any) -> dict[str, bool]:
        """Tools the launcher itself already enables for this game."""
        return {}

    def proton_runner(self, document: Any) -> bool | None:
        """True only when the launcher config confirms a Proton runner."""
        return None

    def wrapper_text(self, document: Any) -> str:
        """The launcher's current wrapper chain, for messages."""
        return ""

    def setting(self, document: Any, key: str) -> Any:
        """Current value of a managed launcher option (``_MISSING`` if unset)."""
        raise LauncherSyncError("This launcher has no options GameOpti can set")

    def set_setting(self, document: Any, key: str, value: Any) -> None:
        raise LauncherSyncError("This launcher has no options GameOpti can set")

    def umu_active(self, document: Any) -> bool | None:
        """Whether the launcher starts this game through UMU (None: unknown)."""
        return None


class _HeroicConfig(_ConfigFile):
    launcher = Launcher.HEROIC

    def __init__(self, path: Path, app_name: str) -> None:
        super().__init__(path)
        self.app_name = app_name

    def load(self) -> dict[str, Any]:
        try:
            document = json.loads(self.read())
        except json.JSONDecodeError as error:
            raise LauncherSyncError(f"The Heroic game configuration is invalid: {error}") from error
        if not isinstance(document, dict) or not isinstance(document.get(self.app_name), dict):
            raise LauncherSyncError(
                "Heroic has no settings for this game yet; open the game's settings in Heroic once"
            )
        return document

    def dump(self, document: Any) -> str:
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    def _section(self, document: Any) -> dict[str, Any]:
        return document[self.app_name]

    def env(self, document: Any) -> dict[str, str]:
        entries = self._section(document).get("enviromentOptions")
        return {
            str(item["key"]): str(item.get("value", ""))
            for item in (entries if isinstance(entries, list) else [])
            if isinstance(item, dict) and "key" in item
        }

    def set_env(self, document: Any, key: str, value: str | None) -> None:
        section = self._section(document)
        entries = section.get("enviromentOptions")
        if not isinstance(entries, list):
            entries = []
            section["enviromentOptions"] = entries
        entries[:] = [
            item for item in entries if not (isinstance(item, dict) and item.get("key") == key)
        ]
        if value is not None:
            entries.append({"key": key, "value": value})

    def _wrappers(self, document: Any) -> list[Any]:
        section = self._section(document)
        wrappers = section.get("wrapperOptions")
        if not isinstance(wrappers, list):
            wrappers = []
            section["wrapperOptions"] = wrappers
        return wrappers

    @staticmethod
    def _same(item: Any, wrapper: Mapping[str, str]) -> bool:
        return (
            isinstance(item, dict)
            and item.get("exe") == wrapper["exe"]
            and item.get("args", "") == wrapper["args"]
        )

    def has_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> bool:
        return any(self._same(item, wrapper) for item in self._wrappers(document))

    def add_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> None:
        self._wrappers(document).append({"exe": wrapper["exe"], "args": wrapper["args"]})

    def remove_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> None:
        # Remove only the last matching entry (the one GameOpti appended); an
        # identical wrapper the user had before stays in place.
        wrappers = self._wrappers(document)
        for index in range(len(wrappers) - 1, -1, -1):
            if self._same(wrappers[index], wrapper):
                del wrappers[index]
                return

    def wrapper_for(self, prefix: Sequence[str]) -> dict[str, str]:
        return {"exe": prefix[0], "args": shlex.join(prefix[1:])}

    def foreign_wrapper(self, document: Any, wrapper: Mapping[str, str] | None) -> bool:
        items = list(self._wrappers(document))
        if wrapper:
            for index in range(len(items) - 1, -1, -1):
                if self._same(items[index], wrapper):
                    del items[index]
                    break
        return bool(items)

    def builtin_features(self, document: Any) -> dict[str, bool]:
        section = self._section(document)
        gamescope = section.get("gamescope")
        return {
            "gamemode": section.get("useGameMode") is True,
            "mangohud": section.get("showMangohud") is True,
            # Heroic starts Gamescope when upscaling or its FPS limiter is on.
            "gamescope": isinstance(gamescope, dict)
            and (gamescope.get("enableUpscaling") is True or gamescope.get("enableLimiter") is True),
        }

    def proton_runner(self, document: Any) -> bool | None:
        version = self._section(document).get("wineVersion")
        if not isinstance(version, dict):
            return None
        return str(version.get("type") or "").casefold() == "proton"

    def setting(self, document: Any, key: str) -> Any:
        if key not in _HEROIC_MANAGED_SETTINGS:
            raise LauncherSyncError(f"GameOpti does not manage the Heroic option {key}")
        return self._section(document).get(key, _MISSING)

    def set_setting(self, document: Any, key: str, value: Any) -> None:
        expected = _HEROIC_MANAGED_SETTINGS.get(key)
        if expected is None:
            raise LauncherSyncError(f"GameOpti does not manage the Heroic option {key}")
        section = self._section(document)
        if value == _MISSING:
            section.pop(key, None)
            return
        if not isinstance(value, expected):
            raise LauncherSyncError(f"Invalid value for the Heroic option {key}")
        section[key] = value

    def umu_active(self, document: Any) -> bool | None:
        # Heroic uses UMU for Proton runners unless "Disable UMU" is on
        # (backend/utils/compatibility_layers.ts: isUmuSupported).
        proton = self.proton_runner(document)
        if proton is None:
            return None
        return proton and self._section(document).get("disableUMU") is not True

    def wrapper_text(self, document: Any) -> str:
        return "; ".join(
            " ".join(part for part in (str(item.get("exe", "")), str(item.get("args", ""))) if part)
            for item in self._wrappers(document)
            if isinstance(item, dict)
        )


class _LutrisConfig(_ConfigFile):
    launcher = Launcher.LUTRIS

    def load(self) -> dict[str, Any]:
        try:
            document = yaml.safe_load(self.read())
        except yaml.YAMLError as error:
            raise LauncherSyncError(f"The Lutris game configuration is invalid: {error}") from error
        if not isinstance(document, dict):
            raise LauncherSyncError("The Lutris game configuration is empty")
        return document

    def dump(self, document: Any) -> str:
        return yaml.safe_dump(
            document, default_flow_style=False, sort_keys=False, allow_unicode=True
        )

    @staticmethod
    def _system(document: Any) -> dict[str, Any]:
        system = document.get("system")
        if not isinstance(system, dict):
            system = {}
            document["system"] = system
        return system

    def env(self, document: Any) -> dict[str, str]:
        env = self._system(document).get("env")
        return {str(key): str(value) for key, value in env.items()} if isinstance(env, dict) else {}

    def set_env(self, document: Any, key: str, value: str | None) -> None:
        system = self._system(document)
        env = system.get("env")
        if not isinstance(env, dict):
            env = {}
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
        if env:
            system["env"] = env
        else:
            system.pop("env", None)

    def _prefix_tokens(self, document: Any) -> list[str]:
        text = str(self._system(document).get("prefix_command") or "").strip()
        try:
            return shlex.split(text)
        except ValueError:
            return [text] if text else []

    def has_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> bool:
        ours = shlex.split(wrapper["text"])
        return bool(ours) and self._prefix_tokens(document)[: len(ours)] == ours

    def add_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> None:
        system = self._system(document)
        previous = str(system.get("prefix_command") or "").strip()
        system["prefix_command"] = f"{wrapper['text']} {previous}".strip()

    def remove_wrapper(self, document: Any, wrapper: Mapping[str, str]) -> None:
        if not self.has_wrapper(document, wrapper):
            return
        system = self._system(document)
        rest = self._prefix_tokens(document)[len(shlex.split(wrapper["text"])):]
        if rest:
            system["prefix_command"] = shlex.join(rest)
        else:
            system.pop("prefix_command", None)

    def wrapper_for(self, prefix: Sequence[str]) -> dict[str, str]:
        # Lutris runs os.path.expandvars() on prefix_command before splitting it.
        if any("$" in part for part in prefix):
            raise LauncherSyncError("The wrapper command contains '$', which Lutris would expand")
        return {"text": shlex.join(prefix)}

    def foreign_wrapper(self, document: Any, wrapper: Mapping[str, str] | None) -> bool:
        tokens = self._prefix_tokens(document)
        if wrapper and self.has_wrapper(document, wrapper):
            tokens = tokens[len(shlex.split(wrapper["text"])):]
        return bool(tokens)

    def builtin_features(self, document: Any) -> dict[str, bool]:
        system = self._system(document)
        return {name: system.get(name) is True for name in ("gamemode", "mangohud", "gamescope")}

    def proton_runner(self, document: Any) -> bool | None:
        if str(document.get("runner") or "") != "wine":
            return False
        wine = document.get("wine")
        version = str(wine.get("version") or "") if isinstance(wine, dict) else ""
        return "proton" in version.casefold() if version else None

    def wrapper_text(self, document: Any) -> str:
        return str(self._system(document).get("prefix_command") or "").strip()

    @staticmethod
    def _game(document: Any) -> dict[str, Any]:
        game = document.get("game")
        if not isinstance(game, dict):
            game = {}
            document["game"] = game
        return game

    def setting(self, document: Any, key: str) -> Any:
        if key not in _LUTRIS_MANAGED_SETTINGS:
            raise LauncherSyncError(f"GameOpti does not manage the Lutris option {key}")
        game = document.get("game")
        return game.get(key, _MISSING) if isinstance(game, dict) else _MISSING

    def set_setting(self, document: Any, key: str, value: Any) -> None:
        if key not in _LUTRIS_MANAGED_SETTINGS:
            raise LauncherSyncError(f"GameOpti does not manage the Lutris option {key}")
        game = self._game(document)
        if value == _MISSING:
            game.pop(key, None)
        elif isinstance(value, _LUTRIS_MANAGED_SETTINGS[key]):
            game[key] = value
        else:
            raise LauncherSyncError(f"Invalid value for the Lutris option {key}")

    def dll_overrides(self, document: Any) -> dict[str, str] | None:
        if str(document.get("runner") or "") not in {"", "wine"}:
            return None
        wine = document.get("wine")
        overrides = wine.get("overrides") if isinstance(wine, dict) else None
        return {str(k): str(v) for k, v in overrides.items()} if isinstance(overrides, dict) else {}

    def set_dll_override(self, document: Any, name: str, mode: str | None) -> None:
        wine = document.get("wine")
        if not isinstance(wine, dict):
            wine = {}
            document["wine"] = wine
        overrides = wine.get("overrides")
        if not isinstance(overrides, dict):
            overrides = {}
        if mode is None:
            overrides.pop(name, None)
        else:
            overrides[name] = mode
        if overrides:
            wine["overrides"] = overrides
        else:
            wine.pop("overrides", None)
        if not wine:
            document.pop("wine", None)


def standard_roots(launcher: Launcher, variant: str, home: Path) -> tuple[Path, ...]:
    if launcher is Launcher.HEROIC:
        return (
            (home / ".var/app/com.heroicgameslauncher.hgl/config/heroic",)
            if variant == "flatpak"
            else (home / ".config/heroic",)
        )
    if variant == "flatpak":
        base = home / ".var/app/net.lutris.Lutris"
        return (base / "config/lutris", base / "data/lutris")
    return (home / ".config/lutris", home / ".local/share/lutris")


def _inside(path: Path, root: Path) -> bool:
    """The file's real folder is inside the launcher's real folder."""

    real_parent = os.path.realpath(path.parent)
    real_root = os.path.realpath(root)
    return os.path.commonpath((real_parent, real_root)) == real_root


def locate_config(game: Game, home: Path | None = None) -> _ConfigFile:
    """The per-game launcher config file for one Heroic/Lutris game."""

    home = Path.home() if home is None else home
    launcher_id = str(game.launcher_game_id or "")
    if not launcher_id:
        raise LauncherSyncError("The launcher did not report an ID for this game")
    if game.launcher is Launcher.HEROIC:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", launcher_id) or ".." in launcher_id:
            raise LauncherSyncError("The Heroic game ID is not a safe file name")
        for root in standard_roots(Launcher.HEROIC, game.launcher_variant, home):
            path = root / "GamesConfig" / f"{launcher_id}.json"
            if os.path.lexists(path):
                if not _inside(path, root):
                    raise LauncherSyncError("The Heroic settings folder points outside Heroic")
                return _HeroicConfig(path, launcher_id)
        raise LauncherSyncError(
            "Heroic has no settings for this game yet; open the game's settings in Heroic once"
        )
    if game.launcher is Launcher.LUTRIS:
        roots = standard_roots(Launcher.LUTRIS, game.launcher_variant, home)
        config_name = ""
        database = roots[1] / "pga.db"
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2.0)
            try:
                row = connection.execute(
                    "SELECT configpath FROM games WHERE id = ?", (int(unquote(launcher_id)),)
                ).fetchone()
            finally:
                connection.close()
            config_name = str(row[0] or "") if row else ""
        except (sqlite3.Error, ValueError) as error:
            raise LauncherSyncError(f"The Lutris database cannot be read: {error}") from error
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", config_name) or ".." in config_name:
            raise LauncherSyncError("Lutris has no configuration for this game")
        for root in roots:
            path = root / "games" / f"{config_name}.yml"
            if os.path.lexists(path):
                if not _inside(path, root):
                    raise LauncherSyncError("The Lutris games folder points outside Lutris")
                return _LutrisConfig(path)
        raise LauncherSyncError("The Lutris game configuration file was not found")
    raise LauncherSyncError("The selected game does not use Heroic or Lutris")


@dataclass(frozen=True, slots=True)
class LauncherFacts:
    """What the launcher's own per-game config says (read only, cheap)."""

    config_found: bool
    reason: str = ""
    config_path: str = ""
    builtins: Mapping[str, bool] = field(default_factory=dict)
    proton_runner: bool | None = None
    # Heroic: Proton through UMU (pressure-vessel container). None = unknown.
    umu_active: bool | None = None


def launcher_facts(game: Game, home: Path | None = None) -> LauncherFacts:
    try:
        config = locate_config(game, home)
        document = config.load()
    except LauncherSyncError as error:
        return LauncherFacts(False, str(error))
    return LauncherFacts(
        True,
        "",
        str(config.path),
        config.builtin_features(document),
        config.proton_runner(document),
        config.umu_active(document),
    )


def default_host_writer() -> Callable[[Path, Path], None] | None:
    """Host-side rename through the existing host bridge (Flatpak only)."""

    if not os.environ.get("FLATPAK_ID"):
        return None
    from .host_service import HostServiceClient

    return HostServiceClient().replace_file


def default_process_commands() -> tuple[str, ...] | None:
    """Command lines of running processes (host ``ps`` inside the sandbox)."""

    if os.environ.get("FLATPAK_ID"):
        from .host_service import HostServiceClient

        try:
            return HostServiceClient().host_process_commands()
        except Exception:  # noqa: BLE001 - an unknown state must not break the UI
            return None
    commands: list[str] = []
    try:
        for entry in Path("/proc").iterdir():
            if entry.name.isdecimal():
                try:
                    commands.append(
                        (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                    )
                except OSError:
                    continue
    except OSError:
        return None
    return tuple(commands)


class LauncherIntegrationService:
    """Status, apply and restore of the managed launcher entries for one game."""

    def __init__(
        self,
        state_dir: Path,
        plan_provider: Callable[..., LauncherPlan],
        *,
        home: Path | None = None,
        process_commands: Callable[[], tuple[str, ...] | None] = default_process_commands,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        host_writer: Callable[[Path, Path], None] | None | object = ...,
    ) -> None:
        self._root = Path(state_dir) / "launcher-integration"
        self._host_writer = default_host_writer() if host_writer is ... else host_writer
        self._plan_provider = plan_provider
        self._home = home
        self._process_commands = process_commands
        self._clock = clock

    @staticmethod
    def supports(game: Game | None) -> bool:
        return bool(game is not None and game.launcher in {Launcher.HEROIC, Launcher.LUTRIS})

    def managed_env_value(self, game: Game, key: str) -> str | None:
        """Value GameOpti applied for ``key`` - only while all of GameOpti's
        entries are still present in the launcher file; None otherwise
        (not applied, restored, or changed in the launcher)."""
        if not self.supports(game):
            return None
        record = self._load_record(game)
        if record is None:
            return None
        try:
            config = locate_config(game, self._home)
            document = config.load()
        except LauncherSyncError:
            return None
        if not self._entries_present(record, config, document):
            return None
        item = record.get("env", {}).get(key)
        if not isinstance(item, Mapping):
            return None
        value = item.get("value")
        return str(value) if value is not None and config.env(document).get(key) == value else None

    def config_access(self, game: Game) -> tuple[bool, str]:
        """Whether GameOpti could write this game's launcher config (cheap)."""
        try:
            config = locate_config(game, self._home)
            config.load()
        except LauncherSyncError as error:
            return False, str(error)
        method, reason = self._write_method(config)
        if method:
            return True, ""
        return False, f"GameOpti cannot write the {game.launcher.value} settings: {reason}"

    # -- records ---------------------------------------------------------------
    def _record_path(self, game: Game) -> Path:
        return self._root / f"{validate_game_key(game.id)}.json"

    def _load_record(self, game: Game) -> dict[str, Any] | None:
        try:
            record = json.loads(self._record_path(game).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("version") not in _READABLE_RECORD_VERSIONS:
            return None
        return record

    def _save_record(self, game: Game, record: Mapping[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._record_path(game)
        descriptor, temporary = tempfile.mkstemp(prefix=".record.", dir=self._root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _archive_record(self, game: Game, record: Mapping[str, Any]) -> None:
        """Keep a superseded record next to the backups (history only)."""
        directory = self._root / "backups" / validate_game_key(game.id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = self._clock().strftime("%Y%m%dT%H%M%S%fZ")
        target = directory / f"superseded-{stamp}.record.json"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(record), handle, indent=2, sort_keys=True)
        logger.info(
            "Launcher integration for %s: the previous record no longer matches the "
            "launcher file; the current state is the new restore point (old record kept: %s)",
            game.id, target,
        )

    def _delete_record(self, game: Game) -> None:
        try:
            self._record_path(game).unlink()
        except FileNotFoundError:
            pass

    def _backup(
        self, game: Game, config: _ConfigFile, *, original: bool = False, text: str | None = None
    ) -> str:
        """Save the text that was read (or ``text``).  The original
        (pre-Apply) copy is never rotated; only the ``*.bak`` safety copies are."""

        directory = self._root / "backups" / validate_game_key(game.id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = self._clock().strftime("%Y%m%dT%H%M%S%fZ")
        if original:
            target = directory / f"original-{stamp}{config.path.suffix}.orig"
        else:
            target = directory / f"{stamp}{config.path.suffix}.bak"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(config.text if text is None else text)
            handle.flush()
            os.fsync(handle.fileno())
        if not original:
            for old in sorted(directory.glob("*.bak"))[:-BACKUPS_KEPT]:
                old.unlink(missing_ok=True)
        return str(target)

    def _original_text(self, record: Mapping[str, Any]) -> str | None:
        """The pre-first-Apply text, only if its backup is intact."""

        path, digest = record.get("originalBackupPath"), record.get("originalDigest")
        if not path or not digest:
            return None
        backups = (self._root / "backups").resolve()
        candidate = Path(str(path))
        try:
            if not candidate.resolve().is_relative_to(backups):
                return None
            text, _identity_ = _read_text(candidate)
        except (OSError, LauncherSyncError):
            return None
        return text if _digest(text) == digest else None

    def _staging_dir(self) -> Path:
        return self._root / "staging"

    def _write_method(self, config: _ConfigFile) -> tuple[str, str]:
        """(``direct`` | ``host-bridge`` | ``""``, reason when unavailable)."""

        if config.writable():
            return "direct", ""
        if not callable(self._host_writer):
            return "", "the file is read-only here and no host bridge is available"
        try:
            staging = self._staging_dir()
            staging.mkdir(parents=True, exist_ok=True, mode=0o700)
            same_device = staging.stat().st_dev == config.path.parent.stat().st_dev
        except OSError as error:
            return "", f"the host-bridge staging folder cannot be prepared: {error}"
        if not same_device:
            return "", "the launcher folder is on another filesystem than GameOpti's data"
        return "host-bridge", ""

    def _write(self, config: _ConfigFile, text: str) -> str:
        method, reason = self._write_method(config)
        if not method:
            raise LauncherSyncError(f"GameOpti cannot write the launcher settings: {reason}")
        if not config.unchanged_on_disk():
            raise LauncherSyncError(
                "The launcher changed the game settings while GameOpti was working; try again"
            )
        if method == "direct":
            _atomic_write(config.path, text)
        else:
            mode = config.path.lstat().st_mode & 0o777
            descriptor, staged = tempfile.mkstemp(
                prefix=f".{config.path.name}.", dir=self._staging_dir()
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(staged, mode)
                if not config.unchanged_on_disk():
                    raise LauncherSyncError(
                        "The launcher changed the game settings while GameOpti was working; try again"
                    )
                logger.info(
                    "Replacing %s through the host bridge: mv --no-copy -f -T -- %s %s",
                    config.path, staged, config.path,
                )
                try:
                    self._host_writer(Path(staged), config.path)  # type: ignore[misc]
                except Exception as error:  # noqa: BLE001 - reported to the user
                    raise LauncherSyncError(f"The host bridge could not write the file: {error}") from error
            finally:
                try:
                    os.unlink(staged)
                except FileNotFoundError:
                    pass
        # Read back what the launcher will read; never report an unverified write.
        try:
            written, _written_identity = _read_text(config.path)
        except (OSError, LauncherSyncError) as error:
            raise LauncherSyncError(f"The written launcher file cannot be verified: {error}") from error
        if _digest(written) != _digest(text):
            raise LauncherSyncError("The launcher file does not contain what GameOpti wrote")
        return method

    # -- state -----------------------------------------------------------------
    def launcher_running(self, game: Game) -> bool | None:
        commands = self._process_commands()
        if commands is None:
            return None
        pattern = _LAUNCHER_PROCESS.get(game.launcher)
        return bool(pattern and any(pattern.search(line) for line in commands))

    def _entries_present(self, record: Mapping[str, Any], config: _ConfigFile, document: Any) -> bool:
        env = config.env(document)
        if any(env.get(key) != item["value"] for key, item in record.get("env", {}).items()):
            return False
        overrides = config.dll_overrides(document) or {}
        if any(overrides.get(name) != item["value"] for name, item in record.get("dll", {}).items()):
            return False
        for key, item in record.get("settings", {}).items():
            try:
                if config.setting(document, key) != item["value"]:
                    return False
            except LauncherSyncError:
                return False
        wrapper = record.get("wrapper")
        return not wrapper or config.has_wrapper(document, wrapper)

    def _conflicts(
        self, plan: LauncherPlan, record: Mapping[str, Any] | None,
        config: _ConfigFile, document: Any,
    ) -> list[str]:
        """Launcher settings GameOpti would have to overwrite.  Never done
        silently: each one blocks Apply with the reason."""

        launcher = config.launcher.value
        managed_env = (record or {}).get("env", {})
        managed_dll = (record or {}).get("dll", {})
        reasons: list[str] = []
        wrapper = (record or {}).get("wrapper")
        if plan.prefix and config.foreign_wrapper(document, wrapper):
            reasons.append(
                f"{launcher} already runs this game through a wrapper GameOpti did not add "
                f"({config.wrapper_text(document)}); GameOpti will not change it. Remove it in "
                f"{launcher} or turn off the GameOpti wrapper features (GameMode/Gamescope)"
            )
        managed_settings = (record or {}).get("settings", {})
        for key, value in sorted(plan.settings.items()):
            if key not in ARGUMENT_SETTINGS:
                continue
            try:
                current = config.setting(document, key)
            except LauncherSyncError:
                continue
            owned = key in managed_settings and managed_settings[key].get("value") == current
            if current not in (_MISSING, "", None, value) and not owned:
                reasons.append(
                    f"{launcher} already passes its own game arguments ({key}: {current}); "
                    "GameOpti will not replace them. Clear them in the launcher or disable the custom arguments"
                )
        env = config.env(document)
        overrides = config.dll_overrides(document)
        for key, value in sorted(plan.environment.items()):
            value = str(value)
            current = env.get(key)
            if key == "WINEDLLOVERRIDES":
                existing = (
                    {name: mode for name, mode in overrides.items()
                     if managed_dll.get(name, {}).get("value") != mode}
                    if overrides is not None
                    else _parse_dll_overrides(
                        current if not (key in managed_env and managed_env[key]["value"] == current)
                        else managed_env[key].get("previous") or ""
                    )
                )
                for name, mode in sorted(_parse_dll_overrides(value).items()):
                    if name in existing and existing[name] != mode:
                        reasons.append(
                            f"{launcher} already sets the DLL override {name}={existing[name]}; "
                            f"GameOpti needs {name}={mode} and will not change it"
                        )
                continue
            if current is None or current == value:
                continue
            if key == "DXIL_SPIRV_CONFIG":
                old_tokens = set(re.split(r"[,;]", current)) - {""}
                new_tokens = set(re.split(r"[,;]", value)) - {""}
                if old_tokens <= new_tokens:
                    continue
            if key in managed_env and managed_env[key].get("value") == current:
                continue
            reasons.append(
                f"{launcher} already sets {key}={current}; GameOpti needs {key}={value} "
                "and will not change it"
            )
        return reasons

    @staticmethod
    def _changes(plan: LauncherPlan, config: _ConfigFile) -> list[str]:
        """Human-readable preview of what Apply writes."""

        launcher = config.launcher.value
        lines = [f"{launcher} config: {config.path}"]
        for key, value in sorted(plan.environment.items()):
            lines.append(f"environment {key}={value}")
        for key, value in sorted(plan.settings.items()):
            lines.append(f"setting {key}={json.dumps(value)}")
        if plan.prefix:
            lines.append(f"wrapper {shlex.join(plan.prefix)}")
        return lines

    def status(self, game: Game) -> dict[str, Any]:
        if not self.supports(game):
            return {"state": "unsupported", "message": "The game does not use Heroic or Lutris"}
        launcher = game.launcher.value
        result: dict[str, Any] = {
            "launcher": launcher,
            "variant": game.launcher_variant,
            "sandboxed": game.launcher_variant == "flatpak",
            "launcherRunning": False,
            "canApply": False,
            "canRestore": False,
        }
        try:
            plan = self._plan_provider(game)
        except Exception as error:  # noqa: BLE001 - shown as an unavailable state
            return {**result, "state": "unavailable", "message": str(error)}
        result.update(
            {
                "prefix": list(plan.prefix),
                "environment": dict(plan.environment),
                "settings": dict(plan.settings),
                "warnings": list(plan.warnings),
                "features": {name: dict(item) for name, item in plan.features.items()},
                "included": list(plan.included),
                "preview": shlex.join(plan.prefix),
                "pending": bool(plan.pending),
            }
        )
        record = self._load_record(game)
        try:
            config = locate_config(game, self._home)
            document = config.load()
        except LauncherSyncError as error:
            return {**result, "state": "unavailable", "message": str(error),
                    "canRestore": record is not None}
        result["configPath"] = str(config.path)
        running = self.launcher_running(game)
        result["launcherRunning"] = bool(running)
        write_method, write_reason = self._write_method(config)
        file_writable = bool(write_method)
        result["configWritable"] = file_writable
        result["writeVia"] = write_method
        conflicts = self._conflicts(plan, record, config, document)
        blockers = [*plan.blockers, *conflicts]
        result["blockers"] = blockers
        result["conflicts"] = conflicts
        result["changes"] = self._changes(plan, config) if not plan.empty else []
        result["builtins"] = dict(config.builtin_features(document))
        writable = running is not True and file_writable
        applied_digest = record.get("appliedDigest") if record else None
        result["editedOutside"] = bool(applied_digest and _digest(config.text) != applied_digest)
        if record is None:
            state = "not_applied"
        elif not self._entries_present(record, config, document):
            state = "modified"
        elif record.get("planDigest") != plan.digest():
            state = "outdated"
        else:
            state = "applied"
        result.update(
            {
                "state": state,
                "canApply": writable and (not plan.empty or record is not None) and not blockers and state != "applied",
                "canRestore": writable and record is not None,
            }
        )
        if running is True:
            result["message"] = f"Close {launcher} first; it can overwrite the game settings"
        elif not file_writable:
            result["message"] = f"GameOpti cannot write the {launcher} settings: {write_reason}"
        elif blockers and state != "applied":
            result["message"] = blockers[0]
        elif state == "applied":
            result["message"] = f"{launcher} starts this game with the GameOpti settings"
        elif plan.empty and record is None:
            result["message"] = "The saved profile adds nothing to the launch"
        else:
            result["message"] = f"Saved, but not applied to {launcher}"
        return result

    # -- changes ---------------------------------------------------------------
    def _revert(self, record: Mapping[str, Any], config: _ConfigFile, document: Any) -> None:
        """Remove only GameOpti's entries; anything else stays as it is now."""

        env = config.env(document)
        for key, item in record.get("env", {}).items():
            if env.get(key) == item["value"]:
                config.set_env(document, key, item.get("previous"))
        overrides = config.dll_overrides(document) or {}
        for name, item in record.get("dll", {}).items():
            if overrides.get(name) == item["value"]:
                config.set_dll_override(document, name, item.get("previous"))
        for key, item in record.get("settings", {}).items():
            # Only GameOpti's value is put back; a value the user changed
            # since then stays.
            try:
                if config.setting(document, key) == item["value"]:
                    config.set_setting(document, key, item.get("previous", _MISSING))
            except LauncherSyncError:
                continue
        wrapper = record.get("wrapper")
        if wrapper:
            config.remove_wrapper(document, wrapper)

    def _ensure_writable(self, game: Game, config: _ConfigFile) -> None:
        method, reason = self._write_method(config)
        if not method:
            raise LauncherSyncError(f"GameOpti cannot write the {game.launcher.value} settings: {reason}")

    def apply(self, game: Game) -> dict[str, Any]:
        if not self.supports(game):
            raise LauncherSyncError("The game does not use Heroic or Lutris")
        plan = self._plan_provider(game, write=True)
        if plan.empty:
            if self._load_record(game) is not None and not plan.blockers and not plan.pending:
                return self.restore(game)
            raise LauncherSyncError("The saved profile adds nothing to the launch")
        if plan.blockers:
            raise LauncherSyncError(plan.blockers[0])
        if self.launcher_running(game) is True:
            raise LauncherSyncError(f"Close {game.launcher.value} first; it can overwrite the game settings")
        config = locate_config(game, self._home)
        document = config.load()
        self._ensure_writable(game, config)
        record = self._load_record(game)
        conflicts = self._conflicts(plan, record, config, document)
        if conflicts:
            raise LauncherSyncError(conflicts[0])
        backup = self._backup(game, config)
        # The old original may only be reused while the launcher file is still
        # exactly what GameOpti wrote. Once the user or the launcher changed it
        # (other prefix, runner, useGameMode, ...), restoring that old copy would
        # silently undo those changes, so the current state becomes the new
        # original instead and the old record is kept as history.
        still_applied = bool(
            record is not None
            and record.get("appliedDigest")
            and _digest(config.text) == record["appliedDigest"]
        )
        untouched_document = copy.deepcopy(document)
        if record is not None:
            self._revert(record, config, document)
        if still_applied and record.get("originalBackupPath") and self._original_text(record):
            original_path = str(record["originalBackupPath"])
            original_digest = str(record["originalDigest"])
        else:
            if record is not None:
                self._archive_record(game, record)
            # State directly before this Apply, without GameOpti's old entries.
            base = config.text if document == untouched_document else config.dump(document)
            original_path = self._backup(game, config, original=True, text=base)
            original_digest = _digest(base)
        env = config.env(document)
        overrides = config.dll_overrides(document)
        entries: dict[str, dict[str, Any]] = {}
        dll_entries: dict[str, dict[str, Any]] = {}
        for key, value in sorted(plan.environment.items()):
            value = str(value)
            if not _ENV_KEY.fullmatch(key) or "\0" in value:
                raise LauncherSyncError(f"Invalid environment variable: {key!r}")
            if key == "WINEDLLOVERRIDES" and overrides is not None:
                # The Lutris wine runner replaces system.env's value with wine.overrides.
                for name, mode in sorted(_parse_dll_overrides(value).items()):
                    dll_entries[name] = {"value": mode, "previous": overrides.get(name)}
                    config.set_dll_override(document, name, mode)
                continue
            if key == "WINEDLLOVERRIDES" and env.get(key):
                value = merge_wine_dll_overrides(env[key], value)
            entries[key] = {"value": value, "previous": env.get(key)}
            config.set_env(document, key, value)
        setting_entries: dict[str, dict[str, Any]] = {}
        for key, value in sorted(plan.settings.items()):
            previous = config.setting(document, key)
            setting_entries[key] = {"value": value, "previous": previous}
            config.set_setting(document, key, value)
        wrapper: dict[str, str] | None = None
        if plan.prefix:
            if any("\0" in part for part in plan.prefix):
                raise LauncherSyncError("The wrapper command contains a null byte")
            wrapper = config.wrapper_for(plan.prefix)
            config.add_wrapper(document, wrapper)
        text = config.dump(document)
        method = self._write(config, text)
        self._save_record(
            game,
            {
                "version": RECORD_VERSION,
                "gameId": game.id,
                "launcher": game.launcher.value,
                "configPath": str(config.path),
                "planDigest": plan.digest(),
                "env": entries,
                "dll": dll_entries,
                "settings": setting_entries,
                "wrapper": wrapper,
                "backupPath": backup,
                "originalBackupPath": original_path,
                "originalDigest": original_digest,
                "appliedDigest": _digest(text),
                "appliedAt": self._clock().isoformat(),
            },
        )
        logger.info(
            "Applied GameOpti settings to %s for %s via %s: env=%s dll=%s settings=%s wrapper=%s",
            game.launcher.value, game.id, method, sorted(entries), sorted(dll_entries),
            {key: item["value"] for key, item in setting_entries.items()}, wrapper,
        )
        return self.status(game)

    # -- one measurement session ----------------------------------------------
    _MEASUREMENT_KEYS = frozenset({"MANGOHUD", "MANGOHUD_CONFIG", "MANGOHUD_CONFIGFILE"})

    def _measurement_path(self, game: Game) -> Path:
        return self._root / f"{validate_game_key(game.id)}.measurement.json"

    def measurement_pending(self, game: Game) -> bool:
        """Measurement entries are (still) in the launcher file."""
        return self._measurement_path(game).is_file()

    def measurement_games(self) -> tuple[str, ...]:
        """Game IDs with measurement entries that still have to be removed."""
        try:
            return tuple(
                path.name[: -len(".measurement.json")]
                for path in self._root.glob("*.measurement.json")
            )
        except OSError:
            return ()

    def begin_measurement(self, game: Game, environment: Mapping[str, str]) -> None:
        """Write the private MangoHud logging variables for one launch.

        Only MANGOHUD, MANGOHUD_CONFIG and MANGOHUD_CONFIGFILE may be set; the
        previous values are kept in a separate record and put back by
        :meth:`end_measurement`. The applied integration record is untouched.
        """

        if not self.supports(game):
            raise LauncherSyncError("The game does not use Heroic or Lutris")
        if set(environment) - self._MEASUREMENT_KEYS or not environment:
            raise LauncherSyncError("Invalid measurement environment")
        if self.measurement_pending(game):
            raise LauncherSyncError("The previous measurement settings are still being removed")
        if self.launcher_running(game) is True:
            raise LauncherSyncError(
                f"Close {game.launcher.value} first; GameOpti starts it for the measurement"
            )
        config = locate_config(game, self._home)
        document = config.load()
        self._ensure_writable(game, config)
        self._backup(game, config)
        current = config.env(document)
        entries: dict[str, dict[str, Any]] = {}
        for key, value in sorted(environment.items()):
            value = str(value)
            if "\0" in value:
                raise LauncherSyncError(f"Invalid environment variable: {key!r}")
            entries[key] = {"value": value, "previous": current.get(key)}
            config.set_env(document, key, value)
        # The record is saved before the write: a crash in between leaves a
        # record whose values are simply not found, which end_measurement
        # handles as "nothing to undo".
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._new_private_file(
            self._measurement_path(game),
            {"gameId": game.id, "env": entries, "startedAt": self._clock().isoformat()},
        )
        try:
            method = self._write(config, config.dump(document))
        except Exception:
            self._measurement_path(game).unlink(missing_ok=True)
            raise
        logger.info(
            "Measurement settings written to %s for %s via %s: %s",
            game.launcher.value, game.id, method, sorted(entries),
        )

    @staticmethod
    def _new_private_file(path: Path, payload: Mapping[str, Any]) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())

    def end_measurement(self, game: Game) -> bool:
        """Remove the measurement variables again. False while the launcher
        still runs (it could overwrite the file); then try again later."""

        path = self._measurement_path(game)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            record = {}
        if self.launcher_running(game) is True:
            return False
        entries = record.get("env") if isinstance(record, dict) else None
        try:
            config = locate_config(game, self._home)
            document = config.load()
        except LauncherSyncError:
            path.unlink(missing_ok=True)
            return True
        changed = False
        current = config.env(document)
        for key, item in (entries or {}).items():
            if key in self._MEASUREMENT_KEYS and isinstance(item, Mapping) and current.get(key) == item.get("value"):
                config.set_env(document, key, item.get("previous"))
                changed = True
        if changed:
            self._ensure_writable(game, config)
            self._backup(game, config)
            self._write(config, config.dump(document))
        path.unlink(missing_ok=True)
        logger.info("Measurement settings removed from %s for %s", game.launcher.value, game.id)
        return True

    def restore(self, game: Game) -> dict[str, Any]:
        """Undo GameOpti's changes.

        If the file is exactly what GameOpti wrote, the original file is put
        back byte for byte.  If it was edited since (by the user or the
        launcher), only GameOpti's entries are removed so those edits survive.
        """

        if not self.supports(game):
            raise LauncherSyncError("The game does not use Heroic or Lutris")
        record = self._load_record(game)
        if record is None:
            return self.status(game)
        if self.launcher_running(game) is True:
            raise LauncherSyncError(f"Close {game.launcher.value} first; it can overwrite the game settings")
        try:
            config = locate_config(game, self._home)
            document = config.load()
        except LauncherSyncError:
            # The launcher file is gone: nothing left to undo, drop the record.
            self._delete_record(game)
            return self.status(game)
        self._ensure_writable(game, config)
        self._backup(game, config)
        original = self._original_text(record)
        untouched = bool(record.get("appliedDigest")) and _digest(config.text) == record["appliedDigest"]
        if original is not None and untouched:
            text, how = original, "original file"
        else:
            self._revert(record, config, document)
            text, how = config.dump(document), "GameOpti entries removed"
        method = self._write(config, text)
        self._delete_record(game)
        logger.info("Restored %s settings for %s (%s) via %s", game.launcher.value, game.id, how, method)
        return self.status(game)


__all__ = [
    "LauncherFacts",
    "LauncherIntegrationService",
    "LauncherPlan",
    "LauncherSyncError",
    "default_host_writer",
    "default_process_commands",
    "launcher_facts",
    "locate_config",
]
