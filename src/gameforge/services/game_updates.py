"""Local-only Steam installation/update tracking.

The module deliberately has no Qt dependency. It observes immutable ``Game``
metadata, fingerprints regular files without following symlinks or crossing a
filesystem boundary, and stores state outside game directories.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import stat
from threading import Event, RLock
import time
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from gameforge.models.game import Game
from gameforge.providers.keyvalues import KVValue, parse_keyvalues


logger = logging.getLogger(__name__)

UPDATE_STATE_FORMAT_VERSION = 2
_LEGACY_UPDATE_STATE_VERSIONS = frozenset({1})
_MAX_STEAM_MANIFEST_BYTES = 16 * 1024 * 1024
# Steam's low byte contains ordinary installed/runtime state. Higher bits are
# update, validation, staging and commit phases during which an installation
# must not be recompressed.
_STEAM_ACTIVE_WRITE_STATE_MASK = ~0xFF


class UpdateScanCancelled(RuntimeError):
    """Raised when a caller cooperatively cancels fingerprint collection."""


class UpdateStateStoreError(RuntimeError):
    """Raised when update state cannot be persisted atomically."""


class GameUpdateStatus(str, Enum):
    INVENTORY = "inventory"
    UP_TO_DATE = "up_to_date"
    WAITING_FOR_LAUNCHER = "waiting_for_launcher"
    WAITING_FOR_STABILITY = "waiting_for_stability"
    ANALYSIS_REQUIRED = "analysis_required"
    IGNORED = "ignored"
    LIBRARY_UNAVAILABLE = "library_unavailable"
    ERROR = "error"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_datetime(value: Any, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            parsed = default or _utc_now()
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value))
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class _ManifestObservationError(ValueError):
    """A current Steam manifest could not be verified safely."""


def _casefold_get(
    mapping: Mapping[str, KVValue],
    key: str,
) -> KVValue | None:
    folded = key.casefold()
    for candidate, value in mapping.items():
        if str(candidate).casefold() == folded:
            return value
    return None


def _required_manifest_scalar(
    mapping: Mapping[str, KVValue],
    key: str,
) -> str:
    value = _casefold_get(mapping, key)
    if not isinstance(value, str) or not value.strip():
        raise _ManifestObservationError(f"missing {key}")
    return value.strip()


def _optional_manifest_integer(
    mapping: Mapping[str, KVValue],
    key: str,
) -> int | None:
    value = _casefold_get(mapping, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _ManifestObservationError(f"{key} is not a scalar")
    try:
        parsed = int(value.strip(), 10)
    except ValueError as error:
        raise _ManifestObservationError(f"{key} is not an integer") from error
    if parsed < 0:
        raise _ManifestObservationError(f"{key} cannot be negative")
    return parsed


def _safe_manifest_install_path(library_path: str, install_dir: str) -> str:
    if "\x00" in install_dir:
        raise _ManifestObservationError("installdir contains a null byte")
    normalized = install_dir.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise _ManifestObservationError("installdir escapes steamapps/common")
    parts = tuple(part for part in relative.parts if part not in ("", "."))
    if not parts:
        raise _ManifestObservationError("installdir is empty")
    common = os.path.abspath(
        os.path.join(library_path, "steamapps", "common")
    )
    candidate = os.path.abspath(os.path.join(common, *parts))
    try:
        contained = os.path.commonpath((common, candidate)) == common
    except ValueError:
        contained = False
    if not contained:
        raise _ManifestObservationError("installdir escapes steamapps/common")
    return candidate


def _stable_manifest_document(
    steamapps_path: str,
    manifest_name: str,
) -> tuple[Mapping[str, KVValue], os.stat_result]:
    """Read a bounded regular manifest through no-follow descriptors."""

    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_flags |= getattr(os, "O_NONBLOCK", 0)
    directory_fd = os.open(steamapps_path, directory_flags)
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise _ManifestObservationError("steamapps is not a directory")

        file_flags = os.O_RDONLY
        file_flags |= getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(manifest_name, file_flags, dir_fd=directory_fd)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise _ManifestObservationError(
                    "appmanifest is not a regular file"
                )
            if before.st_size > _MAX_STEAM_MANIFEST_BYTES:
                raise _ManifestObservationError(
                    "appmanifest exceeds the safe size limit"
                )

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_STEAM_MANIFEST_BYTES:
                    raise _ManifestObservationError(
                        "appmanifest exceeds the safe size limit"
                    )
                chunks.append(chunk)

            after = os.fstat(descriptor)
            relative_path_stat = os.stat(
                manifest_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            absolute_path_stat = os.stat(
                os.path.join(steamapps_path, manifest_name),
                follow_symlinks=False,
            )
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)

    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    if not (
        identity(before)
        == identity(after)
        == identity(relative_path_stat)
        == identity(absolute_path_stat)
    ):
        raise _ManifestObservationError("appmanifest changed while it was read")

    try:
        text = b"".join(chunks).decode("utf-8-sig", errors="strict")
        document = parse_keyvalues(text)
    except (UnicodeError, ValueError) as error:
        raise _ManifestObservationError(
            f"appmanifest is malformed: {error}"
        ) from error
    return document, after


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    relative_path: str
    size: int
    mtime_ns: int
    ctime_ns: int

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("fingerprint path must be a contained relative path")
        for value, name in (
            (self.size, "size"),
            (self.mtime_ns, "mtime_ns"),
            (self.ctime_ns, "ctime_ns"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FileFingerprint":
        return cls(
            relative_path=str(raw.get("relative_path", "")),
            size=max(0, _safe_int(raw.get("size"))),
            mtime_ns=max(0, _safe_int(raw.get("mtime_ns"))),
            ctime_ns=max(0, _safe_int(raw.get("ctime_ns"))),
        )


@dataclass(frozen=True, slots=True)
class FingerprintSnapshot:
    root_path: str
    root_device: int | None
    files: tuple[FileFingerprint, ...]
    complete: bool
    logical_bytes: int
    symlink_count: int = 0
    cross_device_count: int = 0
    errors: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.root_path:
            raise ValueError("root_path cannot be empty")
        if self.root_device is not None and self.root_device < 0:
            raise ValueError("root_device must be non-negative")
        for value, name in (
            (self.logical_bytes, "logical_bytes"),
            (self.symlink_count, "symlink_count"),
            (self.cross_device_count, "cross_device_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("fingerprints must be uniquely sorted by relative path")

    @property
    def signature(self) -> str:
        digest = hashlib.blake2b(digest_size=32)
        digest.update(os.fsencode(self.root_path))
        digest.update(b"\0")
        digest.update(str(self.root_device).encode("ascii"))
        for item in self.files:
            digest.update(b"\0")
            digest.update(os.fsencode(item.relative_path))
            digest.update(b"\0")
            digest.update(str(item.size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(item.mtime_ns).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(item.ctime_ns).encode("ascii"))
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_path": self.root_path,
            "root_device": self.root_device,
            "files": [item.to_dict() for item in self.files],
            "complete": self.complete,
            "logical_bytes": self.logical_bytes,
            "symlink_count": self.symlink_count,
            "cross_device_count": self.cross_device_count,
            "errors": list(self.errors),
            "created_at": self.created_at.isoformat(),
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FingerprintSnapshot":
        raw_files = raw.get("files", ())
        files: list[FileFingerprint] = []
        if isinstance(raw_files, Sequence) and not isinstance(
            raw_files, (str, bytes, bytearray)
        ):
            for value in raw_files:
                if isinstance(value, Mapping):
                    files.append(FileFingerprint.from_dict(value))
        files.sort(key=lambda item: item.relative_path)
        return cls(
            root_path=str(raw.get("root_path", "")),
            root_device=(
                max(0, _safe_int(raw.get("root_device")))
                if raw.get("root_device") is not None
                else None
            ),
            files=tuple(files),
            complete=raw.get("complete") is True,
            logical_bytes=max(0, _safe_int(raw.get("logical_bytes"))),
            symlink_count=max(0, _safe_int(raw.get("symlink_count"))),
            cross_device_count=max(0, _safe_int(raw.get("cross_device_count"))),
            errors=tuple(str(value) for value in raw.get("errors", ()) if str(value)),
            created_at=_aware_datetime(raw.get("created_at")),
        )


@dataclass(frozen=True, slots=True)
class GameChangeSet:
    new_files: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    unchanged_files: int = 0
    changed_bytes: int = 0
    reliable: bool = True
    reason: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.new_files or self.modified_files or self.deleted_files)

    @property
    def files_to_process(self) -> tuple[str, ...]:
        return self.new_files + self.modified_files

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_files": list(self.new_files),
            "modified_files": list(self.modified_files),
            "deleted_files": list(self.deleted_files),
            "unchanged_files": self.unchanged_files,
            "changed_bytes": self.changed_bytes,
            "reliable": self.reliable,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GameChangeSet":
        def paths(key: str) -> tuple[str, ...]:
            value = raw.get(key, ())
            if not isinstance(value, Sequence) or isinstance(
                value, (str, bytes, bytearray)
            ):
                return ()
            return tuple(sorted(str(item) for item in value if str(item)))

        return cls(
            new_files=paths("new_files"),
            modified_files=paths("modified_files"),
            deleted_files=paths("deleted_files"),
            unchanged_files=max(0, _safe_int(raw.get("unchanged_files"))),
            changed_bytes=max(0, _safe_int(raw.get("changed_bytes"))),
            reliable=raw.get("reliable") is True,
            reason=str(raw.get("reason", "")),
        )


def diff_fingerprints(
    previous: FingerprintSnapshot | None,
    current: FingerprintSnapshot,
) -> GameChangeSet:
    """Compare two complete snapshots without reading file contents."""

    if previous is None:
        return GameChangeSet(
            new_files=tuple(item.relative_path for item in current.files),
            changed_bytes=current.logical_bytes,
            reliable=False,
            reason="no verified baseline",
        )
    if not previous.complete or not current.complete:
        return GameChangeSet(
            reliable=False,
            reason="a fingerprint scan was incomplete",
        )
    if (
        previous.root_path != current.root_path
        or previous.root_device != current.root_device
    ):
        return GameChangeSet(
            reliable=False,
            reason="the installation path or filesystem changed",
        )

    old = {item.relative_path: item for item in previous.files}
    new = {item.relative_path: item for item in current.files}
    new_paths = tuple(sorted(new.keys() - old.keys()))
    deleted_paths = tuple(sorted(old.keys() - new.keys()))
    common = old.keys() & new.keys()
    modified_paths = tuple(sorted(path for path in common if old[path] != new[path]))
    unchanged = len(common) - len(modified_paths)
    changed_bytes = sum(new[path].size for path in new_paths + modified_paths)
    return GameChangeSet(
        new_files=new_paths,
        modified_files=modified_paths,
        deleted_files=deleted_paths,
        unchanged_files=unchanged,
        changed_bytes=changed_bytes,
        reliable=True,
    )


@dataclass(frozen=True, slots=True)
class FingerprintLimits:
    timeout_seconds: float = 30.0
    max_entries: int = 1_000_000
    max_pending_directories: int = 65_536

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_entries <= 0 or self.max_pending_directories <= 0:
            raise ValueError("fingerprint limits must be positive")


class GameFingerprintScanner:
    """Collect regular-file metadata under one verified directory tree."""

    def __init__(self, limits: FingerprintLimits | None = None) -> None:
        self.limits = limits or FingerprintLimits()

    def scan(
        self,
        root: Path,
        *,
        cancel_event: Event | None = None,
    ) -> FingerprintSnapshot:
        started = time.monotonic()
        root = Path(root).expanduser()
        root_absolute = os.path.abspath(os.fspath(root))
        root_real = os.path.realpath(root_absolute)
        fingerprints: list[FileFingerprint] = []
        errors: list[str] = []
        logical_bytes = 0
        symlink_count = 0
        cross_device_count = 0
        entry_count = 0
        complete = True

        try:
            root_stat = os.lstat(root)
        except OSError as error:
            return FingerprintSnapshot(
                root_path=root_absolute,
                root_device=None,
                files=(),
                complete=False,
                logical_bytes=0,
                errors=(f"root-stat:{error.errno}",),
            )
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            return FingerprintSnapshot(
                root_path=root_absolute,
                root_device=root_stat.st_dev,
                files=(),
                complete=False,
                logical_bytes=0,
                errors=("root-is-not-a-real-directory",),
            )

        root_device = root_stat.st_dev
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        pending: deque[tuple[Path, tuple[int, int]]] = deque(
            ((root, root_identity),)
        )
        seen_directories = {root_identity}

        while pending:
            self._check_cancelled(cancel_event)
            if time.monotonic() - started >= self.limits.timeout_seconds:
                errors.append("time-budget-exceeded")
                complete = False
                break
            current, expected_identity = pending.pop()
            descriptor: int | None = None
            try:
                descriptor = self._open_verified_directory(
                    current,
                    expected_identity=expected_identity,
                    root_real=root_real,
                    root_device=root_device,
                )
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        self._check_cancelled(cancel_event)
                        if entry_count >= self.limits.max_entries:
                            errors.append("entry-budget-exceeded")
                            complete = False
                            pending.clear()
                            break
                        entry_count += 1
                        try:
                            entry_stat = entry.stat(follow_symlinks=False)
                        except OSError as error:
                            errors.append(f"entry-stat:{error.errno}")
                            complete = False
                            continue
                        mode = entry_stat.st_mode
                        if stat.S_ISLNK(mode):
                            symlink_count += 1
                            continue
                        if entry_stat.st_dev != root_device:
                            cross_device_count += 1
                            continue
                        path = current / entry.name
                        if stat.S_ISREG(mode):
                            relative = os.path.relpath(path, root_absolute)
                            relative_path = Path(relative).as_posix()
                            try:
                                fingerprint = FileFingerprint(
                                    relative_path=relative_path,
                                    size=max(0, entry_stat.st_size),
                                    mtime_ns=max(0, entry_stat.st_mtime_ns),
                                    ctime_ns=max(0, entry_stat.st_ctime_ns),
                                )
                            except ValueError:
                                errors.append("entry-escaped-root")
                                complete = False
                                continue
                            fingerprints.append(fingerprint)
                            logical_bytes += fingerprint.size
                            continue
                        if not stat.S_ISDIR(mode):
                            continue
                        identity = (entry_stat.st_dev, entry_stat.st_ino)
                        if identity in seen_directories:
                            continue
                        seen_directories.add(identity)
                        if len(pending) >= self.limits.max_pending_directories:
                            errors.append("directory-memory-budget-exceeded")
                            complete = False
                            pending.clear()
                            break
                        pending.append((path, identity))
            except OSError as error:
                errors.append(f"directory-open:{error.errno}")
                complete = False
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        fingerprints.sort(key=lambda item: item.relative_path)
        return FingerprintSnapshot(
            root_path=root_absolute,
            root_device=root_device,
            files=tuple(fingerprints),
            complete=complete,
            logical_bytes=logical_bytes,
            symlink_count=symlink_count,
            cross_device_count=cross_device_count,
            errors=tuple(errors),
        )

    @staticmethod
    def _check_cancelled(cancel_event: Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateScanCancelled("Steam update fingerprint scan was cancelled")

    @staticmethod
    def _open_verified_directory(
        path: Path,
        *,
        expected_identity: tuple[int, int],
        root_real: str,
        root_device: int,
    ) -> int:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != expected_identity
                or opened.st_dev != root_device
            ):
                raise OSError("directory changed during fingerprint scan")
            descriptor_link = Path("/proc/self/fd") / str(descriptor)
            if not descriptor_link.exists():
                raise OSError("descriptor containment verification is unavailable")
            resolved = os.path.realpath(descriptor_link)
            try:
                contained = os.path.commonpath((root_real, resolved)) == root_real
            except ValueError:
                contained = False
            if not contained:
                raise OSError("directory escaped the game root")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise


@dataclass(frozen=True, slots=True)
class ManifestObservation:
    game_id: str
    app_id: str
    install_path: str
    library_path: str
    build_id: str | None
    manifest_path: str
    manifest_mtime_ns: int | None
    manifest_size_bytes: int | None
    install_size_bytes: int | None
    state_flags: int | None
    library_available: bool
    update_in_progress: bool
    manifest_error: str = ""
    observed_at: datetime = field(default_factory=_utc_now)

    @classmethod
    def from_game(
        cls,
        game: Game,
        *,
        observed_at: datetime | None = None,
    ) -> "ManifestObservation":
        app_id = str(game.steam_app_id or "").strip()
        install_path = os.path.abspath(
            os.fspath(game.install_path.expanduser())
        )
        library_path = (
            os.path.abspath(os.fspath(game.library_path.expanduser()))
            if game.library_path is not None
            else ""
        )
        expected_manifest = (
            os.path.abspath(
                os.path.join(
                    library_path,
                    "steamapps",
                    f"appmanifest_{app_id}.acf",
                )
            )
            if library_path and app_id
            else ""
        )
        stated_manifest = (
            os.path.abspath(os.fspath(game.steam_manifest_path.expanduser()))
            if game.steam_manifest_path is not None
            else expected_manifest
        )
        base = {
            "game_id": game.id,
            "app_id": app_id or str(game.id),
            "install_path": install_path,
            "library_path": library_path,
            "build_id": game.steam_build_id,
            "manifest_path": stated_manifest,
            "manifest_mtime_ns": game.steam_manifest_mtime_ns,
            "manifest_size_bytes": game.steam_manifest_size_bytes,
            "install_size_bytes": game.steam_size_on_disk_bytes,
            "state_flags": game.state_flags,
            "library_available": game.library_available,
            "update_in_progress": game.update_in_progress,
            "observed_at": observed_at or _utc_now(),
        }

        # A disconnected library is a distinct, expected state. Keep its last
        # cached metadata without attempting to walk an unavailable mount.
        if not game.library_available:
            return cls(**base)

        try:
            if not app_id.isascii() or not app_id.isdecimal() or int(app_id) <= 0:
                raise _ManifestObservationError(
                    "AppID must be a positive decimal number"
                )
            if not library_path:
                raise _ManifestObservationError("Steam library path is missing")
            if stated_manifest != expected_manifest:
                raise _ManifestObservationError(
                    "appmanifest path does not match the Steam library"
                )

            document, manifest_stat = _stable_manifest_document(
                os.path.join(library_path, "steamapps"),
                f"appmanifest_{app_id}.acf",
            )
            app_state = _casefold_get(document, "appstate")
            if not isinstance(app_state, Mapping):
                raise _ManifestObservationError("missing AppState section")
            manifest_app_id = _required_manifest_scalar(app_state, "appid")
            if manifest_app_id != app_id:
                raise _ManifestObservationError(
                    "appmanifest AppID does not match the selected game"
                )
            install_dir = _required_manifest_scalar(app_state, "installdir")
            manifest_install_path = _safe_manifest_install_path(
                library_path,
                install_dir,
            )
            if manifest_install_path != install_path:
                raise _ManifestObservationError(
                    "appmanifest installdir does not match the selected game"
                )

            build_value = _casefold_get(app_state, "buildid")
            if build_value is not None and not isinstance(build_value, str):
                raise _ManifestObservationError("buildid is not a scalar")
            build_id = (
                build_value.strip()
                if isinstance(build_value, str) and build_value.strip()
                else None
            )
            state_flags = _optional_manifest_integer(app_state, "stateflags")
            install_size = _optional_manifest_integer(app_state, "sizeondisk")
            return cls(
                **{
                    **base,
                    "build_id": build_id,
                    "manifest_path": expected_manifest,
                    "manifest_mtime_ns": manifest_stat.st_mtime_ns,
                    "manifest_size_bytes": manifest_stat.st_size,
                    "install_size_bytes": install_size,
                    "state_flags": state_flags,
                    "update_in_progress": bool(
                        state_flags is not None
                        and state_flags & _STEAM_ACTIVE_WRITE_STATE_MASK
                    ),
                }
            )
        except (OSError, UnicodeError, ValueError) as error:
            # Keep cached values only for diagnostics. ``manifest_error`` and
            # ``update_in_progress`` make the observation fail closed; the
            # tracker publishes ERROR and never fingerprints/recompresses it.
            return cls(
                **{
                    **base,
                    "update_in_progress": True,
                    "manifest_error": (
                        "Steam appmanifest could not be verified: "
                        f"{error}"
                    ),
                }
            )

    @property
    def signature(self) -> str:
        payload = (
            self.app_id,
            self.install_path,
            self.library_path,
            self.build_id or "",
            str(self.manifest_mtime_ns),
            str(self.manifest_size_bytes),
            str(self.install_size_bytes),
            str(self.state_flags),
            self.manifest_error,
        )
        digest = hashlib.blake2b(digest_size=24)
        digest.update("\0".join(payload).encode("utf-8", errors="surrogateescape"))
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "app_id": self.app_id,
            "install_path": self.install_path,
            "library_path": self.library_path,
            "build_id": self.build_id,
            "manifest_path": self.manifest_path,
            "manifest_mtime_ns": self.manifest_mtime_ns,
            "manifest_size_bytes": self.manifest_size_bytes,
            "install_size_bytes": self.install_size_bytes,
            "state_flags": self.state_flags,
            "library_available": self.library_available,
            "update_in_progress": self.update_in_progress,
            "manifest_error": self.manifest_error,
            "observed_at": self.observed_at.isoformat(),
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ManifestObservation":
        def optional_int(key: str) -> int | None:
            if raw.get(key) is None:
                return None
            return max(0, _safe_int(raw.get(key)))

        return cls(
            game_id=str(raw.get("game_id", "")),
            app_id=str(raw.get("app_id", "")),
            install_path=str(raw.get("install_path", "")),
            library_path=str(raw.get("library_path", "")),
            build_id=str(raw.get("build_id") or "").strip() or None,
            manifest_path=str(raw.get("manifest_path", "")),
            manifest_mtime_ns=optional_int("manifest_mtime_ns"),
            manifest_size_bytes=optional_int("manifest_size_bytes"),
            install_size_bytes=optional_int("install_size_bytes"),
            state_flags=optional_int("state_flags"),
            library_available=raw.get("library_available", True) is True,
            update_in_progress=raw.get("update_in_progress", False) is True,
            manifest_error=str(raw.get("manifest_error", "")),
            observed_at=_aware_datetime(raw.get("observed_at")),
        )


@dataclass(frozen=True, slots=True)
class GameUpdateRecord:
    game_id: str
    app_id: str
    status: GameUpdateStatus
    current_observation: ManifestObservation | None = None
    current_snapshot: FingerprintSnapshot | None = None
    pending_observation: ManifestObservation | None = None
    pending_snapshot: FingerprintSnapshot | None = None
    pending_since: datetime | None = None
    compression_observation: ManifestObservation | None = None
    compression_snapshot: FingerprintSnapshot | None = None
    changes: GameChangeSet = field(default_factory=GameChangeSet)
    detected_at: datetime | None = None
    ignored_signature: str = ""
    requires_full_analysis: bool = False
    installation_detected: bool = False
    last_error: str = ""
    updated_at: datetime = field(default_factory=_utc_now)

    @property
    def pending_signature(self) -> str:
        if self.pending_observation is None:
            return ""
        snapshot_signature = (
            self.pending_snapshot.signature if self.pending_snapshot is not None else ""
        )
        return f"{self.pending_observation.signature}:{snapshot_signature}"

    @property
    def current_signature(self) -> str:
        if self.current_observation is None:
            return ""
        snapshot_signature = (
            self.current_snapshot.signature if self.current_snapshot is not None else ""
        )
        return f"{self.current_observation.signature}:{snapshot_signature}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "app_id": self.app_id,
            "status": self.status.value,
            "current_observation": (
                self.current_observation.to_dict()
                if self.current_observation is not None
                else None
            ),
            "current_snapshot": (
                self.current_snapshot.to_dict()
                if self.current_snapshot is not None
                else None
            ),
            "pending_observation": (
                self.pending_observation.to_dict()
                if self.pending_observation is not None
                else None
            ),
            "pending_snapshot": (
                self.pending_snapshot.to_dict()
                if self.pending_snapshot is not None
                else None
            ),
            "pending_since": (
                self.pending_since.isoformat() if self.pending_since is not None else None
            ),
            "compression_observation": (
                self.compression_observation.to_dict()
                if self.compression_observation is not None
                else None
            ),
            "compression_snapshot": (
                self.compression_snapshot.to_dict()
                if self.compression_snapshot is not None
                else None
            ),
            "changes": self.changes.to_dict(),
            "detected_at": (
                self.detected_at.isoformat() if self.detected_at is not None else None
            ),
            "ignored_signature": self.ignored_signature,
            "requires_full_analysis": self.requires_full_analysis,
            "installation_detected": self.installation_detected,
            "last_error": self.last_error,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GameUpdateRecord":
        def observation(key: str) -> ManifestObservation | None:
            value = raw.get(key)
            return (
                ManifestObservation.from_dict(value)
                if isinstance(value, Mapping)
                else None
            )

        def snapshot(key: str) -> FingerprintSnapshot | None:
            value = raw.get(key)
            return (
                FingerprintSnapshot.from_dict(value)
                if isinstance(value, Mapping)
                else None
            )

        try:
            status = GameUpdateStatus(str(raw.get("status", "inventory")))
        except ValueError:
            status = GameUpdateStatus.ERROR
        changes_raw = raw.get("changes")
        return cls(
            game_id=str(raw.get("game_id", "")),
            app_id=str(raw.get("app_id", "")),
            status=status,
            current_observation=observation("current_observation"),
            current_snapshot=snapshot("current_snapshot"),
            pending_observation=observation("pending_observation"),
            pending_snapshot=snapshot("pending_snapshot"),
            pending_since=_optional_datetime(raw.get("pending_since")),
            compression_observation=observation("compression_observation"),
            compression_snapshot=snapshot("compression_snapshot"),
            changes=(
                GameChangeSet.from_dict(changes_raw)
                if isinstance(changes_raw, Mapping)
                else GameChangeSet()
            ),
            detected_at=_optional_datetime(raw.get("detected_at")),
            ignored_signature=str(raw.get("ignored_signature", "")),
            requires_full_analysis=raw.get("requires_full_analysis") is True,
            installation_detected=raw.get("installation_detected") is True,
            last_error=str(raw.get("last_error", "")),
            updated_at=_aware_datetime(raw.get("updated_at")),
        )


@dataclass(frozen=True, slots=True)
class UpdateStateDatabase:
    records: Mapping[str, GameUpdateRecord] = field(default_factory=dict)
    initial_inventory_complete: bool = False


class GameUpdateStateStore:
    """Versioned atomic JSON state at an explicit caller-supplied XDG path."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> UpdateStateDatabase:
        with self._lock:
            if not self.path.is_file():
                return UpdateStateDatabase()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                logger.warning("Ignoring unreadable game update state: %s", error)
                return UpdateStateDatabase()
        if not isinstance(payload, Mapping):
            return UpdateStateDatabase()
        version = payload.get("format_version", payload.get("version"))
        if version not in {
            UPDATE_STATE_FORMAT_VERSION,
            *_LEGACY_UPDATE_STATE_VERSIONS,
        }:
            logger.info("Ignoring unsupported game update state format %r", version)
            return UpdateStateDatabase()
        raw_records = payload.get("records", {})
        if isinstance(raw_records, Sequence) and not isinstance(
            raw_records, (str, bytes, bytearray)
        ):
            candidates = {
                str(item.get("game_id", "")): item
                for item in raw_records
                if isinstance(item, Mapping) and item.get("game_id")
            }
        elif isinstance(raw_records, Mapping):
            candidates = raw_records
        else:
            candidates = {}
        records: dict[str, GameUpdateRecord] = {}
        for key, value in candidates.items():
            if not isinstance(value, Mapping):
                continue
            try:
                record = GameUpdateRecord.from_dict(value)
            except (TypeError, ValueError, OverflowError) as error:
                logger.warning("Skipping invalid update state %s: %s", key, error)
                continue
            if record.game_id:
                records[record.game_id] = record
        return UpdateStateDatabase(
            records=records,
            initial_inventory_complete=(
                payload.get("initial_inventory_complete") is True
            ),
        )

    def save(self, database: UpdateStateDatabase) -> None:
        payload = {
            "format_version": UPDATE_STATE_FORMAT_VERSION,
            "initial_inventory_complete": database.initial_inventory_complete,
            "records": {
                game_id: record.to_dict()
                for game_id, record in sorted(database.records.items())
            },
            "updated_at": _utc_now().isoformat(),
        }
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid4().hex}.tmp"
        )
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(serialized)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(self.path)
                try:
                    directory_fd = os.open(
                        self.path.parent,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    )
                except OSError:
                    directory_fd = None
                if directory_fd is not None:
                    try:
                        try:
                            os.fsync(directory_fd)
                        except OSError:
                            logger.debug(
                                "Directory fsync is unavailable for update state",
                                exc_info=True,
                            )
                    finally:
                        os.close(directory_fd)
        except (OSError, TypeError, ValueError) as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove update-state temporary file")
            raise UpdateStateStoreError(
                f"could not save game update state to {self.path}: {error}"
            ) from error


class GameUpdateTracker:
    """Debounce Steam observations and expose only stable update decisions."""

    def __init__(
        self,
        store: GameUpdateStateStore,
        *,
        scanner: GameFingerprintScanner | None = None,
        stability_delay_seconds: float = 300.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if stability_delay_seconds < 0:
            raise ValueError("stability_delay_seconds cannot be negative")
        self._store = store
        self._scanner = scanner or GameFingerprintScanner()
        self._delay = float(stability_delay_seconds)
        self._clock = clock
        loaded = store.load()
        self._records = dict(loaded.records)
        self._initial_inventory_complete = loaded.initial_inventory_complete
        self._lock = RLock()

    @property
    def initial_inventory_complete(self) -> bool:
        with self._lock:
            return self._initial_inventory_complete

    def complete_initial_inventory(self) -> None:
        with self._lock:
            self._initial_inventory_complete = True
            self._save_locked()

    def list_records(self) -> tuple[GameUpdateRecord, ...]:
        with self._lock:
            return tuple(
                self._records[key] for key in sorted(self._records)
            )

    def get(self, game_id: str) -> GameUpdateRecord | None:
        with self._lock:
            return self._records.get(str(game_id))

    def forget_games(self, game_ids: Sequence[str]) -> int:
        """Remove application state for games from an explicitly forgotten library."""

        normalized = {str(game_id) for game_id in game_ids if str(game_id)}
        with self._lock:
            removed = 0
            for game_id in normalized:
                if self._records.pop(game_id, None) is not None:
                    removed += 1
            if removed:
                self._save_locked()
            return removed

    def observe(
        self,
        game: Game,
        *,
        snapshot: FingerprintSnapshot | None = None,
        cancel_event: Event | None = None,
        observed_at: datetime | None = None,
    ) -> GameUpdateRecord:
        now = _aware_datetime(observed_at or self._clock())
        observation = ManifestObservation.from_game(game, observed_at=now)
        with self._lock:
            previous = self._records.get(game.id)

        if not game.library_available:
            record = self._unavailable_record(game, previous, observation, now)
            return self._store_record(record)
        if observation.manifest_error:
            record = self._manifest_error_record(
                game,
                previous,
                observation,
                now,
            )
            return self._store_record(record)
        if observation.update_in_progress:
            record = self._waiting_for_launcher(game, previous, observation, now)
            return self._store_record(record)

        current_snapshot = snapshot or self._scanner.scan(
            game.install_path,
            cancel_event=cancel_event,
        )
        if not current_snapshot.complete:
            record = self._error_record(
                game,
                previous,
                observation,
                current_snapshot,
                now,
            )
            return self._store_record(record)

        with self._lock:
            inventory_complete = self._initial_inventory_complete
        if previous is None and not inventory_complete:
            return self._store_record(
                GameUpdateRecord(
                    game_id=game.id,
                    app_id=observation.app_id,
                    status=GameUpdateStatus.INVENTORY,
                    current_observation=observation,
                    current_snapshot=current_snapshot,
                    updated_at=now,
                )
            )

        if previous is not None and self._matches_current(
            previous,
            observation,
            current_snapshot,
        ):
            if previous.status in {
                GameUpdateStatus.ANALYSIS_REQUIRED,
                GameUpdateStatus.IGNORED,
                GameUpdateStatus.UP_TO_DATE,
            }:
                return previous
            record = replace(
                previous,
                status=GameUpdateStatus.UP_TO_DATE,
                pending_observation=None,
                pending_snapshot=None,
                pending_since=None,
                last_error="",
                updated_at=now,
            )
            return self._store_record(record)

        candidate_signature = self._combined_signature(
            observation,
            current_snapshot,
        )
        pending_matches = (
            previous is not None
            and previous.pending_signature == candidate_signature
            and previous.pending_since is not None
        )
        pending_since = (
            previous.pending_since if pending_matches and previous is not None else now
        )
        elapsed = max(0.0, (now - pending_since).total_seconds())
        if elapsed < self._delay:
            base = previous or GameUpdateRecord(
                game_id=game.id,
                app_id=observation.app_id,
                status=GameUpdateStatus.WAITING_FOR_STABILITY,
            )
            return self._store_record(
                replace(
                    base,
                    status=GameUpdateStatus.WAITING_FOR_STABILITY,
                    pending_observation=observation,
                    pending_snapshot=current_snapshot,
                    pending_since=pending_since,
                    last_error="",
                    updated_at=now,
                )
            )

        return self._store_record(
            self._finalize_stable_change(
                game,
                previous,
                observation,
                current_snapshot,
                now,
            )
        )

    def ignore(self, game_id: str) -> GameUpdateRecord:
        with self._lock:
            record = self._records.get(str(game_id))
            if (
                record is None
                or record.current_observation is None
                or record.current_snapshot is None
            ):
                raise KeyError(f"no stable update state for {game_id}")
            updated = replace(
                record,
                status=GameUpdateStatus.IGNORED,
                ignored_signature=record.current_signature,
                pending_observation=None,
                pending_snapshot=None,
                pending_since=None,
                updated_at=_aware_datetime(self._clock()),
            )
            self._records[updated.game_id] = updated
            self._save_locked()
            return updated

    def mark_compression_verified(self, game_id: str) -> GameUpdateRecord:
        with self._lock:
            record = self._records.get(str(game_id))
            if (
                record is None
                or record.current_observation is None
                or record.current_snapshot is None
                or not record.current_snapshot.complete
            ):
                raise KeyError(f"no complete current state for {game_id}")
            updated = replace(
                record,
                status=GameUpdateStatus.UP_TO_DATE,
                compression_observation=record.current_observation,
                compression_snapshot=record.current_snapshot,
                changes=GameChangeSet(
                    unchanged_files=len(record.current_snapshot.files)
                ),
                detected_at=None,
                ignored_signature="",
                requires_full_analysis=False,
                installation_detected=False,
                last_error="",
                updated_at=_aware_datetime(self._clock()),
            )
            self._records[updated.game_id] = updated
            self._save_locked()
            return updated

    def record_verified_compression(
        self,
        game: Game,
        *,
        snapshot: FingerprintSnapshot | None = None,
        cancel_event: Event | None = None,
        observed_at: datetime | None = None,
    ) -> GameUpdateRecord:
        """Persist a fresh post-compression baseline.

        Recompression can change inode metadata even when file contents stay
        identical.  Reusing the pre-operation fingerprint would therefore
        make the next Steam check report GameForge's own work as an update.
        """

        now = _aware_datetime(observed_at or self._clock())
        observation = ManifestObservation.from_game(game, observed_at=now)
        if observation.manifest_error or observation.update_in_progress:
            reason = (
                observation.manifest_error
                or "Steam is currently updating the installation"
            )
            raise UpdateStateStoreError(
                f"post-compression manifest verification failed: {reason}"
            )
        current_snapshot = snapshot or self._scanner.scan(
            game.install_path,
            cancel_event=cancel_event,
        )
        if not current_snapshot.complete:
            raise UpdateStateStoreError(
                "post-compression fingerprint scan was incomplete"
            )
        with self._lock:
            previous = self._records.get(game.id)
            updated = GameUpdateRecord(
                game_id=game.id,
                app_id=observation.app_id,
                status=GameUpdateStatus.UP_TO_DATE,
                current_observation=observation,
                current_snapshot=current_snapshot,
                compression_observation=observation,
                compression_snapshot=current_snapshot,
                changes=GameChangeSet(
                    unchanged_files=len(current_snapshot.files)
                ),
                ignored_signature="",
                requires_full_analysis=False,
                installation_detected=False,
                last_error="",
                updated_at=now,
                detected_at=None,
            )
            if previous is not None and previous.game_id != game.id:
                raise UpdateStateStoreError(
                    "post-compression update record belongs to another game"
                )
            self._records[game.id] = updated
            self._save_locked()
            return updated

    @staticmethod
    def _matches_current(
        record: GameUpdateRecord,
        observation: ManifestObservation,
        snapshot: FingerprintSnapshot,
    ) -> bool:
        return record.current_signature == GameUpdateTracker._combined_signature(
            observation,
            snapshot,
        )

    @staticmethod
    def _combined_signature(
        observation: ManifestObservation,
        snapshot: FingerprintSnapshot,
    ) -> str:
        return f"{observation.signature}:{snapshot.signature}"

    def _finalize_stable_change(
        self,
        game: Game,
        previous: GameUpdateRecord | None,
        observation: ManifestObservation,
        snapshot: FingerprintSnapshot,
        now: datetime,
    ) -> GameUpdateRecord:
        installation = previous is None or previous.current_snapshot is None
        baseline = (
            previous.compression_snapshot
            if previous is not None and previous.compression_snapshot is not None
            else previous.current_snapshot if previous is not None else None
        )
        changes = diff_fingerprints(baseline, snapshot)
        requires_full = (
            previous is None
            or previous.compression_snapshot is None
            or not changes.reliable
        )
        record = GameUpdateRecord(
            game_id=game.id,
            app_id=observation.app_id,
            status=GameUpdateStatus.ANALYSIS_REQUIRED,
            current_observation=observation,
            current_snapshot=snapshot,
            compression_observation=(
                previous.compression_observation if previous is not None else None
            ),
            compression_snapshot=(
                previous.compression_snapshot if previous is not None else None
            ),
            changes=changes,
            detected_at=now,
            requires_full_analysis=requires_full,
            installation_detected=installation,
            updated_at=now,
        )
        if (
            previous is not None
            and previous.ignored_signature
            == self._combined_signature(observation, snapshot)
        ):
            record = replace(
                record,
                status=GameUpdateStatus.IGNORED,
                ignored_signature=previous.ignored_signature,
            )
        return record

    @staticmethod
    def _unavailable_record(
        game: Game,
        previous: GameUpdateRecord | None,
        observation: ManifestObservation,
        now: datetime,
    ) -> GameUpdateRecord:
        base = previous or GameUpdateRecord(
            game_id=game.id,
            app_id=observation.app_id,
            status=GameUpdateStatus.LIBRARY_UNAVAILABLE,
        )
        return replace(
            base,
            status=GameUpdateStatus.LIBRARY_UNAVAILABLE,
            pending_observation=None,
            pending_snapshot=None,
            pending_since=None,
            updated_at=now,
        )

    @staticmethod
    def _waiting_for_launcher(
        game: Game,
        previous: GameUpdateRecord | None,
        observation: ManifestObservation,
        now: datetime,
    ) -> GameUpdateRecord:
        base = previous or GameUpdateRecord(
            game_id=game.id,
            app_id=observation.app_id,
            status=GameUpdateStatus.WAITING_FOR_LAUNCHER,
        )
        return replace(
            base,
            status=GameUpdateStatus.WAITING_FOR_LAUNCHER,
            pending_observation=observation,
            pending_snapshot=None,
            pending_since=now,
            last_error="",
            updated_at=now,
        )

    @staticmethod
    def _manifest_error_record(
        game: Game,
        previous: GameUpdateRecord | None,
        observation: ManifestObservation,
        now: datetime,
    ) -> GameUpdateRecord:
        base = previous or GameUpdateRecord(
            game_id=game.id,
            app_id=observation.app_id,
            status=GameUpdateStatus.ERROR,
        )
        return replace(
            base,
            status=GameUpdateStatus.ERROR,
            pending_observation=observation,
            pending_snapshot=None,
            pending_since=None,
            last_error=observation.manifest_error,
            updated_at=now,
        )

    @staticmethod
    def _error_record(
        game: Game,
        previous: GameUpdateRecord | None,
        observation: ManifestObservation,
        snapshot: FingerprintSnapshot,
        now: datetime,
    ) -> GameUpdateRecord:
        base = previous or GameUpdateRecord(
            game_id=game.id,
            app_id=observation.app_id,
            status=GameUpdateStatus.ERROR,
        )
        return replace(
            base,
            status=GameUpdateStatus.ERROR,
            pending_observation=observation,
            pending_snapshot=snapshot,
            pending_since=None,
            last_error="; ".join(snapshot.errors) or "fingerprint scan incomplete",
            updated_at=now,
        )

    def _store_record(self, record: GameUpdateRecord) -> GameUpdateRecord:
        with self._lock:
            self._records[record.game_id] = record
            self._save_locked()
        return record

    def _save_locked(self) -> None:
        self._store.save(
            UpdateStateDatabase(
                records=dict(self._records),
                initial_inventory_complete=self._initial_inventory_complete,
            )
        )


__all__ = [
    "UPDATE_STATE_FORMAT_VERSION",
    "FileFingerprint",
    "FingerprintLimits",
    "FingerprintSnapshot",
    "GameChangeSet",
    "GameFingerprintScanner",
    "GameUpdateRecord",
    "GameUpdateStateStore",
    "GameUpdateStatus",
    "GameUpdateTracker",
    "ManifestObservation",
    "UpdateScanCancelled",
    "UpdateStateDatabase",
    "UpdateStateStoreError",
    "diff_fingerprints",
]
