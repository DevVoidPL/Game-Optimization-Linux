"""The per-game MangoHud config that a launcher reads from the host.

Only one location is ever written: ``~/.config/MangoHud/gameopti-<key>.conf``
of the real host home, with ``<key>`` a validated game profile key. The path
is derived here, never taken from QML or a launcher file. Writes are atomic
(temporary file in the same folder + rename) and refuse symlinks, files owned
by another user and a file that changed while it was being replaced; on any
error the previous complete file stays in place.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import stat
import tempfile

from game_optimization_linux.models.mangohud import validate_game_key

from .host_bootstrap import host_home_directory

MAX_CONFIG_BYTES = 256 * 1024


class LauncherMangoHudError(RuntimeError):
    """The launcher MangoHud config cannot be read or written safely."""


def launcher_mangohud_dir(environment: os._Environ[str] | dict[str, str] | None = None) -> Path:
    return host_home_directory(os.environ if environment is None else environment) / ".config/MangoHud"


def launcher_mangohud_path(profile_key: str, directory: Path | None = None) -> Path:
    key = validate_game_key(profile_key)
    return (launcher_mangohud_dir() if directory is None else Path(directory)) / f"gameopti-{key}.conf"


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _check_directory(directory: Path) -> None:
    try:
        info = directory.lstat()
    except OSError as error:
        raise LauncherMangoHudError(f"{directory} is unavailable: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LauncherMangoHudError(f"{directory} is not a regular folder")
    if info.st_uid != os.getuid():
        raise LauncherMangoHudError(f"{directory} belongs to another user")


def _existing(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LauncherMangoHudError(f"{path} cannot be checked: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LauncherMangoHudError(f"{path} is not a regular file; it is left unchanged")
    if info.st_uid != os.getuid():
        raise LauncherMangoHudError(f"{path} belongs to another user; it is left unchanged")
    return info


def read_launcher_mangohud(path: Path) -> str | None:
    """Current text of the managed file, None when missing or unsafe."""
    try:
        info = _existing(path)
    except LauncherMangoHudError:
        return None
    if info is None or info.st_size > MAX_CONFIG_BYTES:
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(MAX_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        return data.decode("utf-8")
    except UnicodeError:
        return None


def write_launcher_mangohud(
    path: Path,
    text: str,
    *,
    host_writer: Callable[[Path, Path], None] | None = None,
    staging_dir: Path | None = None,
) -> str:
    """Atomically replace ``path`` with ``text``. Returns "direct" or "host-bridge".

    GameOpti's Flatpak can write the host home, so the normal path is a
    temporary file in the same folder and ``os.replace``. Only when the
    folder is read-only here, a same-filesystem staging file is renamed on
    the host through ``host_writer`` (the existing ``mv --no-copy`` bridge).
    """

    path = Path(path)
    if not path.name.startswith("gameopti-") or path.suffix != ".conf":
        raise LauncherMangoHudError("Only GameOpti's own MangoHud config can be written")
    validate_game_key(path.name[len("gameopti-"):-len(".conf")])
    directory = path.parent
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    _check_directory(directory)
    before = _existing(path)
    direct = os.access(directory, os.W_OK | os.X_OK)
    if not direct and (host_writer is None or staging_dir is None):
        raise LauncherMangoHudError(f"{directory} is read-only here and no host bridge is available")
    target_dir = directory if direct else Path(staging_dir)
    if not direct:
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target_dir.stat().st_dev != directory.stat().st_dev:
            raise LauncherMangoHudError("The staging folder is on another filesystem")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        current = _existing(path)
        if (before is None) != (current is None) or (
            before is not None and current is not None and _identity(before) != _identity(current)
        ):
            raise LauncherMangoHudError(f"{path} changed while GameOpti was writing it; try again")
        if direct:
            os.replace(temporary, path)
            method = "direct"
        else:
            try:
                host_writer(Path(temporary), path)  # type: ignore[misc]
            except Exception as error:  # noqa: BLE001
                raise LauncherMangoHudError(f"The host bridge could not write {path}: {error}") from error
            method = "host-bridge"
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    if read_launcher_mangohud(path) != text:
        raise LauncherMangoHudError(f"{path} does not contain what GameOpti wrote")
    return method


__all__ = [
    "LauncherMangoHudError",
    "launcher_mangohud_dir",
    "launcher_mangohud_path",
    "read_launcher_mangohud",
    "write_launcher_mangohud",
]
