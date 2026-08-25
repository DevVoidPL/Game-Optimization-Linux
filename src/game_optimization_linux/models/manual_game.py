"""Persistent, argv-safe configuration for explicitly added games."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse
from uuid import UUID


MANUAL_GAME_SCHEMA_VERSION = 1
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def local_path(value: object) -> Path | None:
    """Convert a local path or ``file:`` URL without resolving inaccessible files."""

    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("only local file selections are supported")
        raw = unquote(parsed.path)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("paths must be absolute")
    return path


def parse_command_line(value: object, *, field_name: str) -> tuple[str, ...]:
    """Parse one UI command-line field into argv using predictable POSIX quoting."""

    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = tuple(str(item) for item in value)
    else:
        raw = str(value).strip()
        if not raw:
            return ()
        try:
            result = tuple(shlex.split(raw, posix=True))
        except ValueError as error:
            raise ValueError(f"{field_name}: {error}") from error
    if any("\x00" in item for item in result):
        raise ValueError(f"{field_name} cannot contain NUL characters")
    return result


def _localize_first_program(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv or not argv[0].startswith("file:"):
        return argv
    program = local_path(argv[0])
    return (str(program), *argv[1:]) if program is not None else argv


def normalize_environment(value: object) -> tuple[tuple[str, str], ...]:
    """Normalize environment input while allowing empty and Unicode values."""

    pairs: list[tuple[str, str]] = []
    if value is None:
        return ()
    if isinstance(value, Mapping):
        pairs = [(str(key).strip(), str(item)) for key, item in value.items()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            if isinstance(item, Mapping):
                pairs.append(
                    (str(item.get("key", "")).strip(), str(item.get("value", "")))
                )
            elif isinstance(item, Sequence) and not isinstance(
                item, (str, bytes, bytearray)
            ) and len(item) == 2:
                pairs.append((str(item[0]).strip(), str(item[1])))
            else:
                raise ValueError("environment entries must contain a key and value")
    else:
        raise ValueError("environment variables must be structured key/value entries")

    normalized: dict[str, str] = {}
    for key, item in pairs:
        if not _ENVIRONMENT_NAME.fullmatch(key):
            raise ValueError(f"invalid environment variable name: {key or '(empty)'}")
        if "\x00" in item:
            raise ValueError(f"environment variable {key} cannot contain NUL characters")
        normalized[key] = item
    return tuple(sorted(normalized.items()))


@dataclass(frozen=True, slots=True)
class ManualGameConfig:
    id: str
    name: str
    executable: Path
    install_directory: Path
    working_directory: Path | None = None
    arguments: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    runner_command: tuple[str, ...] = ()
    wine_prefix: Path | None = None
    pre_launch: tuple[str, ...] = ()
    post_launch: tuple[str, ...] = ()
    portrait_artwork: Path | None = None
    header_artwork: Path | None = None

    def __post_init__(self) -> None:
        if not self.id.startswith("manual-") or len(self.id) <= len("manual-"):
            raise ValueError("manual game id must use the manual- prefix")
        raw_uuid = self.id.removeprefix("manual-")
        try:
            parsed_uuid = UUID(raw_uuid)
        except ValueError as error:
            raise ValueError("manual game id must contain a UUID") from error
        if str(parsed_uuid) != raw_uuid:
            raise ValueError("manual game id must use canonical UUID form")
        if not self.name.strip():
            raise ValueError("name is required")
        for value, field_name in (
            (self.executable, "executable"),
            (self.install_directory, "game directory"),
        ):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{field_name} must be an absolute path")
        for value, field_name in (
            (self.working_directory, "working directory"),
            (self.wine_prefix, "Wine prefix"),
            (self.portrait_artwork, "portrait artwork"),
            (self.header_artwork, "header artwork"),
        ):
            if value is not None and (not isinstance(value, Path) or not value.is_absolute()):
                raise ValueError(f"{field_name} must be an absolute path")
        for values, field_name in (
            (self.arguments, "arguments"),
            (self.runner_command, "Wine/Proton command"),
            (self.pre_launch, "pre-launch command"),
            (self.post_launch, "post-launch command"),
        ):
            if not isinstance(values, tuple) or not all(isinstance(item, str) for item in values):
                raise ValueError(f"{field_name} must be an argv tuple")
        normalize_environment(self.environment)

    @property
    def stable_uuid(self) -> str:
        return self.id.removeprefix("manual-")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "executable": str(self.executable),
            "install_directory": str(self.install_directory),
            "working_directory": str(self.working_directory or ""),
            "arguments": list(self.arguments),
            "environment": [
                {"key": key, "value": value} for key, value in self.environment
            ],
            "runner_command": list(self.runner_command),
            "wine_prefix": str(self.wine_prefix or ""),
            "pre_launch": list(self.pre_launch),
            "post_launch": list(self.post_launch),
            "portrait_artwork": str(self.portrait_artwork or ""),
            "header_artwork": str(self.header_artwork or ""),
        }

    def to_qml(self) -> dict[str, Any]:
        return {
            "success": True,
            "id": self.id,
            "name": self.name,
            "executable": str(self.executable),
            "installDirectory": str(self.install_directory),
            "workingDirectory": str(self.working_directory or ""),
            "arguments": shlex.join(self.arguments),
            "environment": [
                {"key": key, "value": value} for key, value in self.environment
            ],
            "runnerCommand": shlex.join(self.runner_command),
            "winePrefix": str(self.wine_prefix or ""),
            "preLaunchCommand": shlex.join(self.pre_launch),
            "postLaunchCommand": shlex.join(self.post_launch),
            "portraitArtwork": str(self.portrait_artwork or ""),
            "headerArtwork": str(self.header_artwork or ""),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManualGameConfig":
        executable = local_path(value.get("executable"))
        install = local_path(value.get("install_directory"))
        if executable is None or install is None:
            raise ValueError("name, executable and game directory are required")
        return cls(
            id=str(value.get("id", "")).strip(),
            name=str(value.get("name", "")).strip(),
            executable=executable,
            install_directory=install,
            working_directory=local_path(value.get("working_directory")),
            arguments=parse_command_line(value.get("arguments"), field_name="arguments"),
            environment=normalize_environment(value.get("environment")),
            runner_command=parse_command_line(
                value.get("runner_command"), field_name="Wine/Proton command"
            ),
            wine_prefix=local_path(value.get("wine_prefix")),
            pre_launch=parse_command_line(
                value.get("pre_launch"), field_name="pre-launch command"
            ),
            post_launch=parse_command_line(
                value.get("post_launch"), field_name="post-launch command"
            ),
            portrait_artwork=local_path(value.get("portrait_artwork")),
            header_artwork=local_path(value.get("header_artwork")),
        )

    @classmethod
    def from_qml(
        cls, value: Mapping[str, Any], *, identifier: str
    ) -> "ManualGameConfig":
        executable = local_path(value.get("executable"))
        if executable is None:
            raise ValueError("executable is required")
        install = local_path(value.get("installDirectory")) or executable.parent
        return cls(
            id=identifier,
            name=str(value.get("name", "")).strip(),
            executable=executable,
            install_directory=install,
            working_directory=local_path(value.get("workingDirectory")),
            arguments=parse_command_line(value.get("arguments"), field_name="arguments"),
            environment=normalize_environment(value.get("environment")),
            runner_command=_localize_first_program(
                parse_command_line(
                    value.get("runnerCommand"), field_name="Wine/Proton command"
                )
            ),
            wine_prefix=local_path(value.get("winePrefix")),
            pre_launch=parse_command_line(
                value.get("preLaunchCommand"), field_name="pre-launch command"
            ),
            post_launch=parse_command_line(
                value.get("postLaunchCommand"), field_name="post-launch command"
            ),
            portrait_artwork=local_path(value.get("portraitArtwork")),
            header_artwork=local_path(value.get("headerArtwork")),
        )


__all__ = [
    "MANUAL_GAME_SCHEMA_VERSION",
    "ManualGameConfig",
    "local_path",
    "normalize_environment",
    "parse_command_line",
]
