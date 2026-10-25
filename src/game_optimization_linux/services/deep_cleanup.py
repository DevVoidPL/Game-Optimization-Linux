"""Reversible quarantine executor for the deep cleanup (stage 1).

Only files from the latest complete plan that the plan marks as removable
are ever touched, and only by atomic ``rename``/``link`` on the game's own
filesystem.  Nothing is copied, nothing is overwritten, and deleting the
quarantine requires an explicit confirmation.  No shell is used.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
from threading import Event, RLock
from typing import Any

from game_optimization_linux.config import DATA_DIR
from game_optimization_linux.models import Game
from game_optimization_linux.models.deep_cleanup import (
    FINALIZE_CONFIRMATION_WORDS,
    SETTLED_STATES,
    CleanupEntry,
    CleanupTransaction,
    validate_relative_path,
)
from game_optimization_linux.models.deep_optimization import DeepOptimizationPlan

logger = logging.getLogger(__name__)

CLEANUP_JOURNAL_DIRECTORY = DATA_DIR / "deep-cleanup"
QUARANTINE_DIRECTORY_NAME = ".gameoptimization-quarantine"
MANIFEST_NAME = "transaction.json"
MIN_FREE_BYTES = 8 * 1024 * 1024
SMALL_FILE_HASH_LIMIT = 4 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class CleanupError(RuntimeError):
    """A refused or failed cleanup step; ``code`` is translated by the UI."""

    def __init__(self, code: str, detail: str = "", *, stale: bool = False) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.stale = stale


def _durable_write(path: Path, text: str) -> None:
    """Temp file + fsync + atomic replace + directory fsync."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


class CleanupJournal:
    """Transactions as JSON journals in the application data directory."""

    def __init__(self, root: Path = CLEANUP_JOURNAL_DIRECTORY) -> None:
        self.root = Path(root)
        self._lock = RLock()

    def _directory(self, game_id: str) -> Path:
        return self.root / hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:32]

    def path(self, transaction: CleanupTransaction) -> Path:
        return self._directory(transaction.game_id) / f"{transaction.transaction_id}.json"

    def save(self, transaction: CleanupTransaction) -> CleanupTransaction:
        with self._lock:
            _durable_write(self.path(transaction), json.dumps(transaction.to_dict(), ensure_ascii=False, indent=1) + "\n")
        return transaction

    def all(self) -> list[CleanupTransaction]:
        result: list[CleanupTransaction] = []
        try:
            files = sorted(self.root.glob("*/*.json"))
        except OSError:
            return result
        for path in files:
            try:
                result.append(CleanupTransaction.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError) as error:
                logger.warning("Ignoring unreadable cleanup journal %s: %s", path.name, error)
        return result

    def for_game(self, game_id: str) -> list[CleanupTransaction]:
        return sorted(
            (item for item in self.all() if item.game_id == game_id),
            key=lambda item: item.created_at, reverse=True,
        )


@dataclass(frozen=True, slots=True)
class CleanupProgress:
    kind: str
    files: int
    bytes: int
    total_files: int
    total_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "files": self.files, "bytes": self.bytes,
                "totalFiles": self.total_files, "totalBytes": self.total_bytes}


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _regular_identity(path: Path) -> os.stat_result | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def _small_hash(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    handle = os.open(path, flags)
    try:
        digest = hashlib.sha256()
        while chunk := os.read(handle, 1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(handle)


class DeepCleanupExecutor:
    """Prepare, quarantine, restore, finalize and recover cleanup transactions."""

    def __init__(
        self,
        journal: CleanupJournal,
        *,
        running_detector: Callable[[Path], Sequence[int]] | None = None,
        update_detector: Callable[[Game], bool] | None = None,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
        rename: Callable[[Path, Path], None] = os.rename,
        link: Callable[[Path, Path], None] = os.link,
        unlink: Callable[[Path], None] = os.unlink,
        user_id: Callable[[], int] = os.getuid,
    ) -> None:
        self.journal = journal
        self._running_detector = running_detector
        self._update_detector = update_detector
        self._disk_usage = disk_usage
        self._rename = rename
        self._link = link
        self._unlink = unlink
        self._user_id = user_id

    # -- selection ----------------------------------------------------------

    @staticmethod
    def selectable_groups(plan: DeepOptimizationPlan) -> dict[str, Any]:
        return {group.group_id: group for group in plan.groups if group.removable and group.group_id in plan.items}

    # -- guards -------------------------------------------------------------

    def _assert_idle(self, game: Game, root: Path) -> None:
        try:
            running = tuple(self._running_detector(root)) if self._running_detector else ()
        except Exception:
            running = ()
        if running:
            raise CleanupError("game_running")
        updating = False
        if self._update_detector is not None:
            try:
                updating = bool(self._update_detector(game))
            except Exception:
                updating = False
        library = getattr(game, "library_path", None)
        app_id = str(getattr(game, "steam_app_id", "") or "")
        if library is not None and app_id.isdecimal():
            for name in ("downloading", "temp"):
                if os.path.lexists(Path(library) / "steamapps" / name / app_id):
                    updating = True
        if updating:
            raise CleanupError("game_updating")

    @staticmethod
    def _check_root(path: Path, real: str, device: int, inode: int) -> str:
        try:
            info = os.lstat(path)
        except OSError as error:
            raise CleanupError("game_root_changed", stale=True) from error
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or os.path.realpath(path) != real
            or (info.st_dev, info.st_ino) != (device, inode)
        ):
            raise CleanupError("game_root_changed", stale=True)
        return real

    def quarantine_base(self, game: Game, root_real: str) -> Path:
        library = getattr(game, "library_path", None)
        app_id = str(getattr(game, "steam_app_id", "") or "")
        if library is not None and app_id.isdecimal():
            steamapps = os.path.realpath(Path(library) / "steamapps")
            if _within(root_real, steamapps):
                return Path(steamapps) / QUARANTINE_DIRECTORY_NAME / app_id
        name = _SAFE_NAME.sub("_", str(game.id)).strip("._")[:64] or "game"
        return Path(root_real).parent / QUARANTINE_DIRECTORY_NAME / name

    def _secure_directory(self, path: Path, *, create: bool) -> None:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if not create:
                raise CleanupError("quarantine_missing") from None
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                pass
            except OSError as error:
                raise CleanupError("quarantine_not_writable", type(error).__name__) from error
            info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CleanupError("quarantine_insecure")
        if info.st_uid != self._user_id() or info.st_mode & 0o022:
            raise CleanupError("quarantine_insecure")

    # -- prepare (preflight) ----------------------------------------------------

    def prepare(
        self,
        game: Game,
        plan: DeepOptimizationPlan,
        plan_fingerprint: str,
        group_ids: Iterable[str],
    ) -> CleanupTransaction:
        """Validate everything before the first file is touched."""

        requested = sorted({str(value) for value in group_ids})
        if not requested:
            raise CleanupError("nothing_selected")
        if plan.status != "completed" or not plan.complete:
            raise CleanupError("plan_incomplete")
        if plan.stale:
            raise CleanupError("plan_stale", stale=True)
        if plan.game_id != game.id or not plan.root_real:
            raise CleanupError("plan_not_executable")
        selectable = self.selectable_groups(plan)
        refused = [group_id for group_id in requested if group_id not in selectable]
        if refused:
            raise CleanupError("not_selectable", ", ".join(refused))
        root_path = Path(game.install_path)
        root_real = self._check_root(root_path, plan.root_real, plan.root_device, plan.root_inode)
        self._assert_idle(game, root_path)
        entries: list[CleanupEntry] = []
        for group_id in requested:
            group = selectable[group_id]
            for item in plan.items[group_id]:
                try:
                    relative = validate_relative_path(item.relative_path)
                except ValueError as error:
                    raise CleanupError("unsafe_path", item.relative_path) from error
                path = Path(root_real) / relative
                if os.path.realpath(path) != os.path.join(root_real, relative):
                    raise CleanupError("unsafe_path", relative, stale=True)
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    raise CleanupError("plan_stale", relative, stale=True) from None
                except OSError as error:
                    raise CleanupError("plan_stale", relative, stale=True) from error
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise CleanupError("unsafe_path", relative, stale=True)
                if (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) != (
                    item.size, item.mtime_ns, item.device, item.inode
                ):
                    raise CleanupError("plan_stale", relative, stale=True)
                entries.append(CleanupEntry(
                    relative_path=relative, size=item.size, mtime_ns=item.mtime_ns,
                    device=item.device, inode=item.inode, category=group.category,
                    key=group.key, language=group.language, safety=group.safety,
                    reason=group.key,
                ))
        if not entries:
            raise CleanupError("nothing_selected")
        base = self.quarantine_base(game, root_real)
        if _within(os.path.realpath(base), root_real):
            raise CleanupError("quarantine_inside_game")
        transaction_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + secrets.token_hex(6)
        transaction = CleanupTransaction(
            transaction_id=transaction_id, game_id=game.id,
            app_id=str(getattr(game, "steam_app_id", "") or ""), game_name=game.name,
            platform=str(getattr(game.launcher, "value", game.launcher)),
            game_root=root_real, game_root_device=plan.root_device, game_root_inode=plan.root_inode,
            quarantine_root=os.fspath(base / transaction_id), plan_fingerprint=plan_fingerprint,
            target_language=plan.target_language, state="preparing", entries=tuple(entries),
        )
        self.journal.save(transaction)  # before any filesystem change
        try:
            self._create_quarantine(transaction, base, root_path)
        except Exception:
            self.journal.save(transaction.with_state("restored", note="preflight_failed"))
            raise
        return transaction

    def _create_quarantine(self, transaction: CleanupTransaction, base: Path, root: Path) -> None:
        parent = base.parent
        # <library>/steamapps or <parent of game> must already exist.
        container = parent.parent
        if not container.is_dir():
            raise CleanupError("quarantine_not_writable")
        self._secure_directory(parent, create=True)
        self._secure_directory(base, create=True)
        target = Path(transaction.quarantine_root)
        try:
            os.mkdir(target, 0o700)
        except OSError as error:
            raise CleanupError("quarantine_not_writable", type(error).__name__) from error
        try:
            if os.lstat(target).st_dev != os.lstat(root).st_dev:
                raise CleanupError("quarantine_other_device")
            if not os.access(target, os.W_OK | os.X_OK):
                raise CleanupError("quarantine_not_writable")
            try:
                free = int(self._disk_usage(target).free)
            except (OSError, AttributeError, TypeError, ValueError):
                free = 0
            if free < MIN_FREE_BYTES:
                raise CleanupError("not_enough_space")
            os.mkdir(target / "files", 0o700)
            self._write_manifest(transaction)
        except Exception:
            for leftover in (target / MANIFEST_NAME, target / "files"):
                try:
                    if leftover.is_dir() and not leftover.is_symlink():
                        os.rmdir(leftover)
                    else:
                        leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                os.rmdir(target)
            except OSError:
                pass
            raise

    @staticmethod
    def _write_manifest(transaction: CleanupTransaction) -> None:
        """Minimal identification copy, readable without the app data directory."""

        payload = {
            "schemaVersion": transaction.schema_version, "kind": "game-optimization-quarantine",
            "transactionId": transaction.transaction_id, "gameId": transaction.game_id,
            "appId": transaction.app_id, "gameName": transaction.game_name,
            "createdAt": transaction.created_at.astimezone(UTC).isoformat(),
            "files": [[entry.relative_path, entry.size] for entry in transaction.entries],
        }
        _durable_write(Path(transaction.quarantine_root) / MANIFEST_NAME, json.dumps(payload) + "\n")

    # -- quarantine ----------------------------------------------------------------

    def quarantine(
        self,
        transaction: CleanupTransaction,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[CleanupProgress], None] | None = None,
    ) -> CleanupTransaction:
        if transaction.state != "preparing":
            raise CleanupError("invalid_state", transaction.state)
        transaction = self.journal.save(transaction.with_state("quarantining"))
        root = transaction.game_root
        files_root = Path(transaction.quarantine_root) / "files"
        total_bytes = transaction.total_bytes
        moved_bytes = 0
        failure = ""
        for index, entry in enumerate(transaction.entries):
            if cancel_event is not None and cancel_event.is_set():
                failure = "cancelled"
                break
            source = Path(root) / entry.relative_path
            target = files_root / entry.relative_path
            try:
                info = _regular_identity(source)
                if info is None or (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) != (
                    entry.size, entry.mtime_ns, entry.device, entry.inode
                ):
                    raise CleanupError("file_changed", entry.relative_path)
                if os.path.realpath(source) != os.path.join(root, entry.relative_path):
                    raise CleanupError("unsafe_path", entry.relative_path)
                self._make_parents(files_root, entry.relative_path)
                if os.path.lexists(target):
                    raise CleanupError("quarantine_conflict", entry.relative_path)
                self._rename(source, target)  # same filesystem: atomic, never a copy
            except Exception as error:
                code = error.code if isinstance(error, CleanupError) else type(error).__name__
                transaction = self.journal.save(transaction.with_entry(index, status="error", error=code))
                failure = code
                break
            transaction = self.journal.save(transaction.with_entry(index, status="quarantined"))
            moved_bytes += entry.size
            if progress is not None:
                progress(CleanupProgress("quarantine", index + 1, moved_bytes,
                                         len(transaction.entries), total_bytes))
        if not failure:
            return self.journal.save(transaction.with_state("quarantined"))
        return self._rollback(transaction, failure, progress)

    def _rollback(self, transaction, failure, progress) -> CleanupTransaction:
        """Undo every moved file in reverse order; never report success."""

        transaction = self.journal.save(transaction.with_state("rolling_back", note=failure))
        transaction = self._restore_entries(transaction, progress, kind="rollback")
        remaining = any(entry.status in {"quarantined", "conflict"} for entry in transaction.entries)
        if not remaining:
            self._remove_transaction_directory(transaction)
        state = "partial" if remaining else ("restored" if failure == "cancelled" else "failed")
        return self.journal.save(transaction.with_state(state, note=failure))

    # -- restore ---------------------------------------------------------------------

    def restore(
        self,
        transaction: CleanupTransaction,
        game: Game,
        *,
        progress: Callable[[CleanupProgress], None] | None = None,
    ) -> CleanupTransaction:
        if transaction.state not in {"quarantined", "partial", "failed"}:
            raise CleanupError("invalid_state", transaction.state)
        self._check_root(Path(game.install_path), transaction.game_root,
                         transaction.game_root_device, transaction.game_root_inode)
        self._assert_idle(game, Path(game.install_path))
        transaction = self.journal.save(transaction.with_state("restoring"))
        transaction = self._restore_entries(transaction, progress, kind="restore")
        remaining = any(entry.status in {"quarantined", "conflict"} for entry in transaction.entries)
        if not remaining:
            self._remove_transaction_directory(transaction)
            return self.journal.save(transaction.with_state("restored"))
        return self.journal.save(transaction.with_state("partial", note="restore_conflicts"))

    def _restore_entries(self, transaction, progress, *, kind: str) -> CleanupTransaction:
        root = transaction.game_root
        files_root = Path(transaction.quarantine_root) / "files"
        candidates = [index for index, entry in enumerate(transaction.entries)
                      if entry.status in {"quarantined", "conflict"}]
        done = 0
        for index in reversed(candidates):
            entry = transaction.entries[index]
            source = files_root / entry.relative_path
            target = Path(root) / entry.relative_path
            status, conflict, error = "restored", "", ""
            try:
                info = _regular_identity(source)
                if info is None or not _within(os.path.realpath(source), os.path.realpath(files_root)):
                    raise CleanupError("quarantine_file_missing", entry.relative_path)
                self._make_parents(Path(root), entry.relative_path, game_tree=True)
                existing = _regular_identity(target) if os.path.lexists(target) else None
                if os.path.lexists(target):
                    status, conflict = "conflict", self._conflict_kind(existing, source, target, entry)
                else:
                    self._place_without_overwrite(source, target)
            except FileExistsError:
                status, conflict = "conflict", "exists"
            except Exception as failure:
                status = "error"
                error = failure.code if isinstance(failure, CleanupError) else type(failure).__name__
                if entry.status == "conflict":
                    status = "conflict"
            if status == "error" and entry.status == "quarantined":
                transaction = self.journal.save(transaction.with_entry(index, status="quarantined", error=error))
            else:
                transaction = self.journal.save(transaction.with_entry(index, status=status, conflict=conflict, error=error))
            done += 1
            if progress is not None:
                progress(CleanupProgress(kind, done, 0, len(candidates), 0))
        return transaction

    def _place_without_overwrite(self, source: Path, target: Path) -> None:
        """Hard link (fails if the target exists) and drop the quarantine name."""

        try:
            self._link(source, target)
        except FileExistsError:
            raise
        except OSError as error:
            if error.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EMLINK}:
                raise
            if os.path.lexists(target):
                raise FileExistsError(target) from error
            self._rename(source, target)
            return
        self._unlink(source)

    @staticmethod
    def _conflict_kind(existing, source: Path, target: Path, entry: CleanupEntry) -> str:
        if existing is None:
            return "not_a_file"
        if (existing.st_size, existing.st_mtime_ns) == (entry.size, entry.mtime_ns):
            return "same_size_and_time"
        if existing.st_size == entry.size and entry.size <= SMALL_FILE_HASH_LIMIT:
            try:
                if _small_hash(source) == _small_hash(target):
                    return "same_content"
            except OSError:
                pass
        return "different"

    @staticmethod
    def _make_parents(base: Path, relative: str, *, game_tree: bool = False) -> None:
        current = base
        for part in Path(relative).parts[:-1]:
            current = current / part
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                os.mkdir(current, 0o755 if game_tree else 0o700)
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise CleanupError("parent_not_directory", relative)

    # -- finalize ----------------------------------------------------------------------

    def finalize(
        self,
        transaction: CleanupTransaction,
        game: Game,
        confirmation: str,
        *,
        progress: Callable[[CleanupProgress], None] | None = None,
    ) -> CleanupTransaction:
        if transaction.state != "quarantined":
            raise CleanupError("invalid_state", transaction.state)
        if str(confirmation or "").strip() not in FINALIZE_CONFIRMATION_WORDS:
            raise CleanupError("confirmation_required")
        self._assert_idle(game, Path(game.install_path))
        transaction = self.journal.save(transaction.with_state("finalizing"))
        quarantine = os.path.realpath(transaction.quarantine_root)
        files_root = os.path.join(quarantine, "files")
        done_bytes = 0
        targets = [index for index, entry in enumerate(transaction.entries) if entry.status == "quarantined"]
        for count, index in enumerate(targets, start=1):
            entry = transaction.entries[index]
            path = Path(files_root) / entry.relative_path
            try:
                info = _regular_identity(path)
                if (
                    info is None
                    or os.path.realpath(path) != os.path.join(files_root, entry.relative_path)
                    or (info.st_dev, info.st_ino) != (entry.device, entry.inode)
                ):
                    raise CleanupError("quarantine_file_changed", entry.relative_path)
                self._unlink(path)
            except Exception as error:
                code = error.code if isinstance(error, CleanupError) else type(error).__name__
                transaction = self.journal.save(transaction.with_entry(index, error=code))
                return self.journal.save(transaction.with_state("partial", note="finalize_failed"))
            transaction = self.journal.save(transaction.with_entry(index, status="deleted"))
            done_bytes += entry.size
            if progress is not None:
                progress(CleanupProgress("finalize", count, done_bytes, len(targets), transaction.total_bytes))
        self._remove_transaction_directory(transaction)
        return self.journal.save(transaction.with_state("finalized"))

    @staticmethod
    def _remove_transaction_directory(transaction: CleanupTransaction) -> None:
        """Remove only empty directories of this transaction and its manifest."""

        target = Path(transaction.quarantine_root)
        try:
            info = os.lstat(target)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return
        files_root = target / "files"
        for directory, subdirectories, files in os.walk(files_root, topdown=False, followlinks=False):
            if files:
                return  # never delete a file that is not recorded as handled
            for name in subdirectories:
                candidate = Path(directory) / name
                if candidate.is_symlink():
                    return
                try:
                    os.rmdir(candidate)
                except OSError:
                    return
        try:
            if files_root.is_dir() and not files_root.is_symlink():
                os.rmdir(files_root)
            (target / MANIFEST_NAME).unlink(missing_ok=True)
            os.rmdir(target)
        except OSError:
            return

    # -- crash recovery ------------------------------------------------------------------

    def recover(self) -> list[CleanupTransaction]:
        """Reconcile unfinished journals from the files present; never finalize."""

        recovered: list[CleanupTransaction] = []
        for transaction in self.journal.all():
            if transaction.state in SETTLED_STATES:
                continue
            recovered.append(self._recover_one(transaction))
        return recovered

    def _recover_one(self, transaction: CleanupTransaction) -> CleanupTransaction:
        files_root = Path(transaction.quarantine_root) / "files"
        interrupted = transaction.state
        entries = list(transaction.entries)
        for index, entry in enumerate(entries):
            source = Path(transaction.game_root) / entry.relative_path
            quarantined = files_root / entry.relative_path
            in_game = _regular_identity(source)
            in_quarantine = _regular_identity(quarantined)
            same = lambda info: info is not None and (info.st_dev, info.st_ino) == (entry.device, entry.inode)  # noqa: E731
            if same(in_game) and same(in_quarantine):
                # A restore finished linking but not unlinking: same inode twice.
                try:
                    self._unlink(quarantined)
                    status = "restored"
                except OSError:
                    status = "quarantined"
            elif same(in_quarantine):
                status = "conflict" if in_game is not None or os.path.lexists(source) else "quarantined"
            elif same(in_game):
                status = "restored" if entry.status in {"quarantined", "restored", "conflict"} else "pending"
            elif entry.status == "deleted":
                status = "deleted"
            else:
                status = "missing"
            entries[index] = CleanupEntry.from_dict({**entry.to_dict(), "status": status})
        transaction = CleanupTransaction.from_dict({
            **transaction.to_dict(), "entries": [entry.to_dict() for entry in entries],
        })
        counts = transaction.counts()
        moved = counts["quarantined"] + counts["conflict"]
        if interrupted == "preparing" and not moved and not counts["deleted"]:
            state, note = "restored", "cancelled_before_start"
        elif interrupted == "finalizing":
            if counts["deleted"] and not moved:
                state, note = "finalized", "finalize_completed_before_crash"
            elif not counts["deleted"] and counts["quarantined"] == len(entries):
                state, note = "quarantined", "finalize_interrupted"
            else:
                state, note = "partial", "finalize_interrupted"
        elif moved and counts["quarantined"] == len(entries):
            state, note = "quarantined", f"{interrupted}_interrupted"
        elif not moved and not counts["missing"] and not counts["deleted"]:
            state, note = "restored", f"{interrupted}_interrupted"
        else:
            state, note = "partial", f"{interrupted}_interrupted"
        if state in {"restored", "finalized"}:
            self._remove_transaction_directory(transaction)
        return self.journal.save(transaction.with_state(state, recovered=True, note=note))


__all__ = [
    "CLEANUP_JOURNAL_DIRECTORY",
    "CleanupError",
    "CleanupJournal",
    "CleanupProgress",
    "DeepCleanupExecutor",
    "MANIFEST_NAME",
    "QUARANTINE_DIRECTORY_NAME",
]
