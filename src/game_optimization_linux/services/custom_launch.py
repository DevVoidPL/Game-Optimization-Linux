"""Per-game custom launch configuration: environment, arguments, wrapper, hooks.

Every value is stored as structured data (argv lists, key/value pairs). Nothing
is ever joined into a shell command line; a user who needs shell syntax points
GameOpti at a script file they own.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from ..config import GAMES_CONFIG_DIR
from ..models.mangohud import validate_game_key

SCHEMA_VERSION = 1
FILE_NAME = "custom-launch.json"
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENTRIES = 64
MAX_TEXT = 4096
MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT = 1, 600, 30
# Values of keys matching this are redacted in logs and copied diagnostics.
SECRET_KEY = re.compile(r"(TOKEN|SECRET|PASSW(OR)?D|API_?KEY|ACCESS_?KEY|PRIVATE|CREDENTIAL|AUTH)", re.I)
# Wrappers GameOpti already places itself; a custom copy would stack them.
MANAGED_WRAPPERS = {"gamescope": "Gamescope", "gamemoderun": "GameMode", "mangohud": "MangoHud",
                    "mako-launch": "Frame generation (MAKO)"}
PRIVILEGED = {"sudo", "pkexec", "doas", "su", "run0"}
SHELL_PROGRAMS = {"sh", "bash", "dash", "zsh", "fish", "ksh", "csh", "tcsh", "busybox"}


class CustomLaunchError(ValueError):
    pass


def redact(key: str, value: str) -> str:
    return "<redacted>" if SECRET_KEY.search(key) else value


def _text(value: object, label: str) -> str:
    text = str(value if value is not None else "")
    if "\x00" in text:
        raise CustomLaunchError(f"{label} cannot contain NUL characters")
    if len(text) > MAX_TEXT:
        raise CustomLaunchError(f"{label} is too long")
    return text


def _argv(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CustomLaunchError(f"{label} must be a list of separate arguments")
    argv = tuple(_text(item, label) for item in value)
    if len(argv) > MAX_ENTRIES:
        raise CustomLaunchError(f"{label} has too many arguments")
    return argv


def validate_program(argv: Sequence[str], label: str) -> None:
    """A program is one executable: absolute path or PATH name, never shell text."""
    if not argv or not argv[0].strip():
        raise CustomLaunchError(f"{label}: the executable is empty")
    program = argv[0]
    name = Path(program).name
    if program != program.strip() or any(c in program for c in "\n\r\t"):
        raise CustomLaunchError(f"{label}: the executable contains whitespace control characters")
    if "/" in program and not program.startswith("/"):
        raise CustomLaunchError(f"{label}: use an absolute path or a program name from PATH")
    if name in PRIVILEGED:
        raise CustomLaunchError(f"{label}: privileged programs ({name}) are not allowed")
    if name in SHELL_PROGRAMS:
        raise CustomLaunchError(f"{label}: shell interpreters are not accepted; point GameOpti at your own script file instead")


@dataclass(frozen=True, slots=True)
class EnvEntry:
    key: str
    value: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ArgEntry:
    value: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class CommandEntry:
    argv: tuple[str, ...]
    enabled: bool = True
    timeout: int = DEFAULT_TIMEOUT
    on_failure: str = "stop"        # pre-launch only: stop | continue
    run_if_not_started: bool = False  # post-exit only


@dataclass(frozen=True, slots=True)
class CustomLaunchProfile:
    key: str
    environment: tuple[EnvEntry, ...] = ()
    arguments: tuple[ArgEntry, ...] = ()
    wrapper: CommandEntry | None = None
    pre_launch: tuple[CommandEntry, ...] = ()
    post_exit: tuple[CommandEntry, ...] = ()
    updated_at: str = ""

    @property
    def empty(self) -> bool:
        return not (self.environment or self.arguments or self.wrapper or self.pre_launch or self.post_exit)

    def active_environment(self) -> dict[str, str]:
        return {e.key: e.value for e in self.environment if e.enabled}

    def active_arguments(self) -> tuple[str, ...]:
        return tuple(a.value for a in self.arguments if a.enabled)

    def active_wrapper(self) -> tuple[str, ...]:
        return self.wrapper.argv if self.wrapper and self.wrapper.enabled else ()

    def active_pre(self) -> tuple[CommandEntry, ...]:
        return tuple(c for c in self.pre_launch if c.enabled)

    def active_post(self) -> tuple[CommandEntry, ...]:
        return tuple(c for c in self.post_exit if c.enabled)

    def to_dict(self) -> dict[str, Any]:
        def command(c: CommandEntry) -> dict[str, Any]:
            return {"argv": list(c.argv), "enabled": c.enabled, "timeout": c.timeout,
                    "onFailure": c.on_failure, "runIfNotStarted": c.run_if_not_started}
        return {"schemaVersion": SCHEMA_VERSION, "key": self.key, "updatedAt": self.updated_at,
                "environment": [{"key": e.key, "value": e.value, "enabled": e.enabled} for e in self.environment],
                "arguments": [{"value": a.value, "enabled": a.enabled} for a in self.arguments],
                "wrapper": command(self.wrapper) if self.wrapper else None,
                "preLaunch": [command(c) for c in self.pre_launch],
                "postExit": [command(c) for c in self.post_exit]}

    @classmethod
    def from_dict(cls, key: str, value: Mapping[str, Any]) -> "CustomLaunchProfile":
        if not isinstance(value, Mapping):
            raise CustomLaunchError("Invalid custom launch profile")
        version = int(value.get("schemaVersion", SCHEMA_VERSION))
        if version > SCHEMA_VERSION:
            raise CustomLaunchError("The custom launch profile was saved by a newer GameOpti")

        def entries(name: str) -> list[Mapping[str, Any]]:
            items = value.get(name) or []
            if not isinstance(items, list) or len(items) > MAX_ENTRIES:
                raise CustomLaunchError(f"Invalid {name} list")
            if not all(isinstance(item, Mapping) for item in items):
                raise CustomLaunchError(f"Invalid {name} entry")
            return items

        environment = []
        seen = set()
        for item in entries("environment"):
            name = _text(item.get("key", ""), "Environment variable name").strip()
            if not ENV_KEY.fullmatch(name):
                raise CustomLaunchError(f"Invalid environment variable name: {name or '(empty)'}")
            if name in seen:
                raise CustomLaunchError(f"Environment variable {name} is listed twice")
            seen.add(name)
            environment.append(EnvEntry(name, _text(item.get("value", ""), f"Environment variable {name}"), bool(item.get("enabled", True))))
        arguments = [ArgEntry(_text(item.get("value", ""), "Game argument"), bool(item.get("enabled", True)))
                     for item in entries("arguments")]

        def command(item: Mapping[str, Any], label: str, *, pre: bool) -> CommandEntry:
            argv = _argv(item.get("argv", []), label)
            validate_program(argv, label)
            try:
                timeout = int(item.get("timeout", DEFAULT_TIMEOUT))
            except (TypeError, ValueError) as error:
                raise CustomLaunchError(f"{label}: timeout must be a whole number of seconds") from error
            if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
                raise CustomLaunchError(f"{label}: timeout must be {MIN_TIMEOUT}-{MAX_TIMEOUT} seconds")
            on_failure = str(item.get("onFailure", "stop"))
            if on_failure not in {"stop", "continue"}:
                raise CustomLaunchError(f"{label}: invalid failure behaviour")
            return CommandEntry(argv, bool(item.get("enabled", True)), timeout,
                                on_failure if pre else "continue", bool(item.get("runIfNotStarted", False)) and not pre)

        wrapper = None
        if value.get("wrapper"):
            if not isinstance(value["wrapper"], Mapping):
                raise CustomLaunchError("Invalid wrapper entry")
            wrapper = command(value["wrapper"], "Wrapper", pre=True)
            wrapper = replace(wrapper, timeout=DEFAULT_TIMEOUT, on_failure="stop")
        return cls(key=validate_game_key(key), environment=tuple(environment), arguments=tuple(arguments),
                   wrapper=wrapper,
                   pre_launch=tuple(command(i, "Before launch", pre=True) for i in entries("preLaunch")),
                   post_exit=tuple(command(i, "After exit", pre=False) for i in entries("postExit")),
                   updated_at=str(value.get("updatedAt", "")))


def field_errors(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every validation problem with its section and row, for messages beside the fields."""
    errors: list[dict[str, Any]] = []

    def check(section: str, index: int, field_name: str, document: Mapping[str, Any]) -> None:
        try:
            CustomLaunchProfile.from_dict("1", document)
        except CustomLaunchError as error:
            errors.append({"section": section, "index": index, "field": field_name, "message": str(error)})

    if not isinstance(payload, Mapping):
        return [{"section": "", "index": -1, "field": "", "message": "Invalid custom launch profile"}]
    for section, field_name in (("environment", "key"), ("arguments", "value"), ("preLaunch", "argv"), ("postExit", "argv")):
        items = payload.get(section) or []
        if not isinstance(items, list) or len(items) > MAX_ENTRIES:
            errors.append({"section": section, "index": -1, "field": "", "message": f"Invalid {section} list"})
            continue
        for index, item in enumerate(items):
            check(section, index, field_name, {section: [item]})
    keys = [str((item or {}).get("key", "")).strip() for item in payload.get("environment") or [] if isinstance(item, Mapping)]
    for index, key in enumerate(keys):
        if key and keys.index(key) != index:
            errors.append({"section": "environment", "index": index, "field": "key",
                           "message": f"Environment variable {key} is listed twice"})
    if payload.get("wrapper"):
        check("wrapper", 0, "argv", {"wrapper": payload["wrapper"]})
    return errors


def launch_key(game: Any) -> str:
    """Stable per-game identity; the same game from different launchers stays separate."""
    if getattr(game, "profile_key", ""):
        return validate_game_key(game.profile_key)
    if game.launcher.value == "Manual" and str(game.data_source).casefold() == "manual":
        return "local-" + hashlib.sha256(("custom-launch:" + game.id).encode()).hexdigest()[:24]
    raise CustomLaunchError("This game has no stable profile identity for custom launch settings")


def profile_path(key: str, root: Path = GAMES_CONFIG_DIR) -> Path:
    return Path(root) / validate_game_key(key) / FILE_NAME


class CustomLaunchRepository:
    def __init__(self, root: Path = GAMES_CONFIG_DIR) -> None:
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return profile_path(key, self.root)

    def load(self, key: str) -> CustomLaunchProfile:
        path = self.path(key)
        if not path.is_file():
            return CustomLaunchProfile(key=validate_game_key(key))
        if path.is_symlink() or path.stat().st_size > 256 * 1024:
            raise CustomLaunchError("The custom launch profile file is not a regular small file")
        return CustomLaunchProfile.from_dict(key, json.loads(path.read_text(encoding="utf-8")))

    def save(self, profile: CustomLaunchProfile) -> CustomLaunchProfile:
        saved = replace(profile, updated_at=datetime.now(UTC).isoformat())
        CustomLaunchProfile.from_dict(saved.key, saved.to_dict())  # same validation as load
        path = self.path(saved.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(saved.to_dict(), indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return saved

    def read_bytes(self, key: str) -> bytes | None:
        path = self.path(key)
        return path.read_bytes() if path.is_file() and not path.is_symlink() else None

    def restore_bytes(self, key: str, data: bytes | None) -> None:
        """Put back exactly what was there before a failed transaction."""
        path = self.path(key)
        if data is None:
            path.unlink(missing_ok=True)
            return
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def modified_at(self, key: str) -> str:
        path = self.path(key)
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat() if path.is_file() else ""

    def digest(self, key: str) -> str:
        path = self.path(key)
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else ""
