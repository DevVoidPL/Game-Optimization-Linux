"""Atomic installation of the narrow host components shipped by the Flatpak."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import pwd
import stat
import tempfile
from typing import Mapping


APP_ID = "io.github.DevVoidPL.GameOptimizationLinux"
HOST_COMPONENT_DIRECTORY = Path(".local/share/game-optimization-linux/bin")


class HostBootstrapError(RuntimeError):
    """A safe, user-facing failure while installing host components."""


@dataclass(frozen=True, slots=True)
class HostBootstrapResult:
    attempted: bool
    changed: tuple[str, ...]
    target_directory: Path | None


def host_home_directory(
    environment: Mapping[str, str] | None = None,
    *,
    uid: int | None = None,
) -> Path:
    """Return the real host home, not Flatpak's redirected XDG directory."""

    values = os.environ if environment is None else environment
    if str(values.get("FLATPAK_ID", "")).strip():
        try:
            record = pwd.getpwuid(os.getuid() if uid is None else int(uid))
        except (KeyError, OSError) as error:
            raise HostBootstrapError(
                "Could not determine the host home directory"
            ) from error
        candidate = Path(record.pw_dir)
    else:
        candidate = Path(str(values.get("HOME", "")).strip() or Path.home())
    if not candidate.is_absolute() or candidate == Path("/"):
        raise HostBootstrapError("The host home directory is invalid")
    return candidate


def default_runner_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    return host_home_directory(environment) / HOST_COMPONENT_DIRECTORY / "game-optimization-run"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _is_current(source: Path, target: Path) -> bool:
    try:
        target_info = target.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(target_info.st_mode) or stat.S_ISLNK(target_info.st_mode):
        return False
    try:
        return bool(target_info.st_mode & 0o111) and _digest(source) == _digest(target)
    except OSError:
        return False


def _atomic_install(source: Path, target: Path) -> bool:
    try:
        source_info = source.lstat()
    except OSError as error:
        raise HostBootstrapError(f"Bundled host component is unavailable: {source}") from error
    if not stat.S_ISREG(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode):
        raise HostBootstrapError(f"Bundled host component is not a regular file: {source}")
    if _is_current(source, target):
        return False

    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise HostBootstrapError(
            f"Could not install host component {target.name}: {error}"
        ) from error
    return True


def bootstrap_flatpak_host_components(
    environment: Mapping[str, str] | None = None,
    *,
    app_prefix: Path = Path("/app"),
    target_home: Path | None = None,
) -> HostBootstrapResult:
    """Install or refresh the Python-free host-side runner shim."""

    values = os.environ if environment is None else environment
    if str(values.get("FLATPAK_ID", "")).strip() != APP_ID:
        return HostBootstrapResult(False, (), None)
    home = Path(target_home) if target_home is not None else host_home_directory(values)
    target_directory = home / HOST_COMPONENT_DIRECTORY
    sources = {
        "game-optimization-run": Path(app_prefix) / "libexec/game-optimization-run-host",
    }
    changed = tuple(
        name
        for name, source in sources.items()
        if _atomic_install(source, target_directory / name)
    )
    return HostBootstrapResult(True, changed, target_directory)


__all__ = [
    "HostBootstrapError",
    "HostBootstrapResult",
    "bootstrap_flatpak_host_components",
    "default_runner_path",
    "host_home_directory",
]
