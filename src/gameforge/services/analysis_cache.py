"""Versioned, atomic XDG cache for read-only compression reports."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import stat
from threading import Event, RLock
import time
from typing import Any, Mapping
from uuid import uuid4

from gameforge.models.game import Game

from .btrfs_analysis import ANALYZER_VERSION, AnalysisCancelled, BtrfsAnalysisReport


logger = logging.getLogger(__name__)

ANALYSIS_CACHE_FORMAT_VERSION = 2

_TREE_SIGNATURE_VERSION = 1
_TREE_SIGNATURE_TIMEOUT_SECONDS = 5.0
_TREE_SIGNATURE_MAX_ENTRIES = 500_000
_TREE_SIGNATURE_MAX_PENDING_DIRECTORIES = 32_768
_TREE_SIGNATURE_MASK = (1 << 256) - 1


class AnalysisCacheError(RuntimeError):
    """Raised when a cache result cannot be stored atomically."""


class AnalysisCache:
    """Store reports outside game directories and validate their state key."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
    ) -> BtrfsAnalysisReport | None:
        """Return a matching report, or ``None`` for stale/malformed data."""

        with self._lock:
            payload = self._read_payload()
        if payload is None:
            return None
        if payload.get("format_version") != ANALYSIS_CACHE_FORMAT_VERSION:
            return None
        entries = payload.get("entries")
        if not isinstance(entries, Mapping):
            return None
        entry = entries.get(self._entry_key(game))
        if not isinstance(entry, Mapping):
            return None
        expected_state = self.state_for(game, cancel_event=cancel_event)
        if not self._state_is_complete(expected_state):
            return None
        cached_state = entry.get("state")
        if not isinstance(cached_state, Mapping):
            return None
        if dict(cached_state) != expected_state:
            return None
        report_raw = entry.get("report")
        if not isinstance(report_raw, Mapping):
            return None
        try:
            report = BtrfsAnalysisReport.from_dict(report_raw)
        except (TypeError, ValueError, OverflowError):
            logger.warning("Ignoring invalid compression analysis cache entry")
            return None
        if report.analyzer_version != ANALYZER_VERSION:
            return None
        if report.game_id != game.id:
            return None
        return report

    def save(self, game: Game, report: BtrfsAnalysisReport) -> None:
        """Atomically replace the cache document after a complete analysis."""

        if report.game_id != game.id:
            raise AnalysisCacheError("report does not belong to the supplied game")
        if report.analyzer_version != ANALYZER_VERSION:
            raise AnalysisCacheError("report uses an unsupported analyzer version")
        state = self.state_for(game)
        if not self._state_is_complete(state):
            raise AnalysisCacheError(
                "game state could not be signed completely within the cache "
                "safety budget"
            )
        entry = {
            "app_id": str(game.steam_app_id or game.id),
            "game_id": game.id,
            "path": os.path.abspath(os.fspath(game.install_path.expanduser())),
            "state": state,
            "scanned_at": report.created_at.isoformat(),
            "report": report.to_dict(),
        }
        with self._lock:
            payload = self._read_payload()
            if (
                payload is None
                or payload.get("format_version") != ANALYSIS_CACHE_FORMAT_VERSION
            ):
                payload = {
                    "format_version": ANALYSIS_CACHE_FORMAT_VERSION,
                    "analyzer_version": ANALYZER_VERSION,
                    "entries": {},
                }
            entries = payload.get("entries")
            if not isinstance(entries, dict):
                entries = {}
                payload["entries"] = entries
            entries[self._entry_key(game)] = entry
            payload["format_version"] = ANALYSIS_CACHE_FORMAT_VERSION
            payload["analyzer_version"] = ANALYZER_VERSION
            payload["updated_at"] = datetime.now(UTC).isoformat()
            self._write_payload(payload)

    @staticmethod
    def state_for(
        game: Game,
        *,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Return the bounded full-tree state signature for a report."""

        path = game.install_path.expanduser()
        absolute_path = os.path.abspath(os.fspath(path))
        try:
            root_stat = os.lstat(path)
            directory_mtime_ns: int | None = root_stat.st_mtime_ns
            directory_device: int | None = root_stat.st_dev
            directory_inode: int | None = root_stat.st_ino
        except OSError:
            directory_mtime_ns = None
            directory_device = None
            directory_inode = None
        updated_at = (
            game.last_updated_at.isoformat()
            if game.last_updated_at is not None
            else None
        )
        return {
            "analyzer_version": ANALYZER_VERSION,
            "path": absolute_path,
            "reported_size_bytes": max(0, int(game.logical_size_gb * 1_000_000_000)),
            "reported_physical_bytes": max(
                0, int(game.physical_size_gb * 1_000_000_000)
            ),
            "directory_mtime_ns": directory_mtime_ns,
            "directory_device": directory_device,
            "directory_inode": directory_inode,
            "game_updated_at": updated_at,
            "state_flags": game.state_flags,
            "filesystem_name": game.filesystem_name or game.filesystem.value,
            "mount_point": str(game.mount_point or ""),
            "mount_options": list(game.mount_options),
            "tree_signature": AnalysisCache._tree_signature(
                path,
                cancel_event=cancel_event,
            ),
        }

    @staticmethod
    def _state_is_complete(state: Mapping[str, Any]) -> bool:
        signature = state.get("tree_signature")
        return isinstance(signature, Mapping) and signature.get("complete") is True

    @staticmethod
    def _tree_signature(
        root: Path,
        *,
        timeout_seconds: float = _TREE_SIGNATURE_TIMEOUT_SECONDS,
        max_entries: int = _TREE_SIGNATURE_MAX_ENTRIES,
        max_pending_directories: int = _TREE_SIGNATURE_MAX_PENDING_DIRECTORIES,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, order-independent signature of the complete tree.

        Every directory is opened read-only with ``O_NOFOLLOW`` and verified by
        descriptor before it is scanned.  Entry hashes are combined with both a
        modular sum and XOR, so directory iteration order cannot change the
        result and no directory's entries need to be materialized or sorted.
        If the full tree cannot be visited within the explicit budgets, the
        result is marked incomplete and callers refuse to use or store it.
        """

        started = time.monotonic()
        root = Path(root)
        root_absolute = os.path.abspath(os.fspath(root))
        root_real = os.path.realpath(root_absolute)
        entry_count = 0
        directory_count = 0
        regular_file_count = 0
        symlink_count = 0
        logical_bytes = 0
        digest_sum = 0
        digest_xor = 0

        def result(*, complete: bool, reason: str = "") -> dict[str, Any]:
            return {
                "version": _TREE_SIGNATURE_VERSION,
                "complete": complete,
                "reason": reason,
                "digest_sum": f"{digest_sum:064x}",
                "digest_xor": f"{digest_xor:064x}",
                "entry_count": entry_count,
                "directory_count": directory_count,
                "regular_file_count": regular_file_count,
                "symlink_count": symlink_count,
                "logical_bytes": logical_bytes,
            }

        def add_record(relative_path: str, values: os.stat_result) -> None:
            nonlocal digest_sum, digest_xor
            record = b"\0".join(
                (
                    os.fsencode(relative_path),
                    str(values.st_mode).encode("ascii"),
                    str(values.st_dev).encode("ascii"),
                    str(values.st_ino).encode("ascii"),
                    str(values.st_size).encode("ascii"),
                    str(values.st_mtime_ns).encode("ascii"),
                    str(values.st_ctime_ns).encode("ascii"),
                    str(getattr(values, "st_blocks", 0)).encode("ascii"),
                )
            )
            value = int.from_bytes(hashlib.blake2b(record, digest_size=32).digest())
            digest_sum = (digest_sum + value) & _TREE_SIGNATURE_MASK
            digest_xor ^= value

        try:
            root_stat = os.lstat(root)
        except OSError as error:
            return result(complete=False, reason=f"root-stat:{error.errno}")
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            return result(complete=False, reason="root-is-not-a-real-directory")

        add_record(".", root_stat)
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        pending: deque[tuple[Path, tuple[int, int]]] = deque(
            ((root, root_identity),)
        )
        seen_directories = {root_identity}

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                raise AnalysisCancelled("Compression analysis was cancelled")
            if time.monotonic() - started >= timeout_seconds:
                return result(complete=False, reason="time-budget-exceeded")
            current, expected_identity = pending.pop()
            descriptor: int | None = None
            try:
                descriptor = AnalysisCache._open_verified_directory(
                    current,
                    expected_identity=expected_identity,
                    root_real=root_real,
                )
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        if cancel_event is not None and cancel_event.is_set():
                            raise AnalysisCancelled(
                                "Compression analysis was cancelled"
                            )
                        if entry_count >= max_entries:
                            return result(
                                complete=False, reason="entry-budget-exceeded"
                            )
                        if entry_count % 128 == 0 and (
                            time.monotonic() - started >= timeout_seconds
                        ):
                            return result(
                                complete=False, reason="time-budget-exceeded"
                            )
                        path = current / entry.name
                        try:
                            entry_stat = entry.stat(follow_symlinks=False)
                        except OSError as error:
                            return result(
                                complete=False,
                                reason=f"entry-stat:{error.errno}",
                            )
                        relative = os.path.relpath(path, root_absolute)
                        add_record(relative, entry_stat)
                        entry_count += 1
                        mode = entry_stat.st_mode
                        if stat.S_ISLNK(mode):
                            symlink_count += 1
                            continue
                        if stat.S_ISREG(mode):
                            regular_file_count += 1
                            logical_bytes += max(0, entry_stat.st_size)
                            continue
                        if not stat.S_ISDIR(mode):
                            continue
                        directory_count += 1
                        identity = (entry_stat.st_dev, entry_stat.st_ino)
                        if identity in seen_directories:
                            continue
                        seen_directories.add(identity)
                        if len(pending) >= max_pending_directories:
                            return result(
                                complete=False,
                                reason="directory-memory-budget-exceeded",
                            )
                        pending.append((path, identity))
            except OSError as error:
                return result(
                    complete=False, reason=f"directory-open:{error.errno}"
                )
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        return result(complete=True)

    @staticmethod
    def _open_verified_directory(
        path: Path,
        *,
        expected_identity: tuple[int, int],
        root_real: str,
    ) -> int:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISDIR(opened_stat.st_mode) or (
                opened_stat.st_dev,
                opened_stat.st_ino,
            ) != expected_identity:
                raise OSError("directory changed during cache signature")
            descriptor_link = Path("/proc/self/fd") / str(descriptor)
            if not descriptor_link.exists():
                raise OSError("descriptor verification is unavailable")
            resolved = os.path.realpath(descriptor_link)
            try:
                contained = os.path.commonpath((resolved, root_real)) == root_real
            except ValueError:
                contained = False
            if not contained:
                raise OSError("directory escaped the cache signature root")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _read_payload(self) -> dict[str, Any] | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            logger.warning("Could not read analysis cache %s: %s", self.path, error)
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError) as error:
            logger.warning("Ignoring invalid analysis cache %s: %s", self.path, error)
            return None
        return payload if isinstance(payload, dict) else None

    def _write_payload(self, payload: Mapping[str, Any]) -> None:
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_name(
                f".{self.path.name}.{uuid4().hex}.tmp"
            )
            with temporary_path.open("x", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        except (OSError, TypeError, ValueError) as error:
            raise AnalysisCacheError(
                f"could not store analysis cache {self.path}: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug(
                        "Could not clean temporary analysis cache %s",
                        temporary_path,
                        exc_info=True,
                    )

    @staticmethod
    def _entry_key(game: Game) -> str:
        return str(game.steam_app_id or game.id)


__all__ = [
    "ANALYSIS_CACHE_FORMAT_VERSION",
    "AnalysisCache",
    "AnalysisCacheError",
]
