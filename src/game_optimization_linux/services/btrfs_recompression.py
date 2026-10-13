"""Deep-cleanup stage 2B: controlled per-file Btrfs recompression.

Extends the existing Btrfs integration (``BtrfsCompressionProvider`` runs
every command: argv lists, no shell, no root).  Planning is read-only;
execution recompresses single files with the syntax the local btrfs-progs
documents, never recursively, never with ``compress-force`` and never for
shared/reflinked extents.  Recompression is not reversible from the app.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
from threading import Event, RLock
import time
from typing import Any
import zlib

from game_optimization_linux.config import DATA_DIR
from game_optimization_linux.models import FilesystemType, Game
from game_optimization_linux.models.btrfs_recompression import (
    SETTLED_JOB_STATES,
    BtrfsCompressionPlan,
    CompressionJob,
    CompressionJobEntry,
    RecompressionCandidate,
    RecompressionCapabilities,
)
from game_optimization_linux.models.deep_cleanup import validate_relative_path
from game_optimization_linux.models.deep_optimization import DeepOptimizationPlan

from .deep_classify import PACKAGE_EXTENSIONS, PACKAGE_NAMES, TEXTURE_EXTENSIONS, extension_of
from .deep_cleanup import _durable_write

logger = logging.getLogger(__name__)

RECOMPRESSION_DIRECTORY = DATA_DIR / "deep-recompression"
SKIPPED_FORMATS = frozenset({
    ".zip", ".7z", ".rar", ".gz", ".xz", ".zst", ".bz2", ".lz4",
    ".pak", ".ucas", ".utoc", ".vpk", ".rpf", ".bundle", ".assets",
    ".mp3", ".ogg", ".opus", ".aac", ".m4a", ".flac", ".wem", ".bnk", ".pck", ".fsb",
    ".mp4", ".mkv", ".webm", ".bik", ".bk2", ".usm", ".avi",
    ".png", ".jpg", ".jpeg", ".webp", ".avif",
    ".dds", ".ktx", ".ktx2", ".astc", ".basis", ".crn", ".pvr", ".vtf",
})
MIN_CANDIDATE_BYTES = 64 * 1024
MIN_SAMPLED_GAIN = 0.10
SAMPLE_CHUNK = 1024 * 1024          # three chunks: start, middle, end
FULL_HASH_LIMIT = 8 * 1024 * 1024
DEFAULT_TOTAL_SAMPLE_LIMIT = 512 * 1024 * 1024
FREE_SPACE_MARGIN = 512 * 1024 * 1024
DU_BATCH = 64
NO_GAIN_LIMIT = 3
_FIEMAP = 0xC020660B
_FIEMAP_EXTENT_ENCODED = 0x8
_FIEMAP_EXTENT_SHARED = 0x2000
_FIEMAP_EXTENT_LAST = 0x1


class RecompressionError(RuntimeError):
    def __init__(self, code: str, detail: str = "", *, stale: bool = False) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.stale = stale


# -- capabilities -------------------------------------------------------------------


def parse_capabilities(
    version_text: str,
    defragment_help: str,
    du_help: str,
    property_help: str,
    *,
    compsize_available: bool = False,
    exact_measurement_available: bool = False,
) -> RecompressionCapabilities:
    """Read only what the local help documents; unknown syntax stays unused."""

    version = re.search(r"btrfs-progs\s+v?([0-9][0-9.]*)", version_text or "")
    algorithms: tuple[str, ...] = ()
    bracket = re.search(r"-c\[([a-z0-9,]+)\]", defragment_help or "")
    if bracket:
        algorithms = tuple(item for item in bracket.group(1).split(",") if item)
    level_flag = ""
    if re.search(r"(^|\s)-L,\s*--level\b", defragment_help or "", re.MULTILINE):
        level_flag = "-L"
    zstd_range = None
    found = re.search(r"zstd:\s*(-?\d+)\s*\.\.\s*(-?\d+)", defragment_help or "")
    if found and level_flag:
        zstd_range = (int(found.group(1)), int(found.group(2)))
    flush = bool(re.search(r"(^|\s)-f\s+flush data", defragment_help or "", re.MULTILINE))
    du_raw = "--raw" in (du_help or "") and bool(re.search(r"-s,\s*--summarize", du_help or ""))
    property_get = "btrfs property get" in (property_help or "")
    property_set = "btrfs property set" in (property_help or "")
    # Restoring "not set" needs a documented reset value; the help of many
    # btrfs-progs releases does not state one, so the option stays hidden.
    reset_documented = bool(re.search(r"compression.*(empty string|reset)", property_help or "", re.IGNORECASE | re.DOTALL))
    digest = hashlib.sha256("\0".join((version_text or "", defragment_help or "", du_help or "",
                                       property_help or "")).encode("utf-8")).hexdigest()[:24]
    return RecompressionCapabilities(
        btrfs_version=version.group(1) if version else "",
        algorithms=algorithms, level_flag=level_flag, zstd_level_range=zstd_range,
        flush_flag=flush, du_raw=du_raw, property_get=property_get, property_set=property_set,
        property_reset_documented=reset_documented, compsize_available=compsize_available,
        exact_measurement_available=exact_measurement_available, help_digest=digest,
    )


def build_defragment_argv(
    btrfs: str, capabilities: RecompressionCapabilities, algorithm: str, level: int | None, path: str
) -> list[str]:
    """Exactly one file, ``-c<algo>`` (+ documented level/flush), never ``-r``."""

    if algorithm not in capabilities.algorithms:
        raise RecompressionError("algorithm_unsupported", algorithm)
    argv = [btrfs, "filesystem", "defragment"]
    if capabilities.flush_flag:
        argv.append("-f")
    argv.append(f"-c{algorithm}")
    if level is not None:
        if level not in capabilities.levels:
            raise RecompressionError("level_unsupported", str(level))
        argv.extend([capabilities.level_flag, str(level)])
    argv.append(path)
    if "-r" in argv or "--recursive" in argv:
        raise RecompressionError("recursive_forbidden")
    return argv


# -- read-only probes ---------------------------------------------------------------------


def fiemap_summary(descriptor: int, size: int) -> tuple[float, bool]:
    """Fraction of bytes in compressed extents and whether any extent is shared."""

    if size <= 0:
        return 0.0, False
    encoded = 0
    total = 0
    shared = False
    start = 0
    count = 256
    while True:
        header = struct.pack("=QQLLLL", start, 0xFFFFFFFFFFFFFFFF - start, 0, 0, count, 0)
        buffer = bytearray(header + b"\0" * (56 * count))
        fcntl.ioctl(descriptor, _FIEMAP, buffer, True)
        mapped = struct.unpack_from("=QQLLLL", buffer)[3]
        if not mapped:
            break
        last = False
        for index in range(mapped):
            logical, _physical, length, _r1, _r2, flags = struct.unpack_from("=QQQQQL", buffer, 32 + index * 56)
            total += length
            if flags & _FIEMAP_EXTENT_ENCODED:
                encoded += length
            if flags & _FIEMAP_EXTENT_SHARED:
                shared = True
            start = logical + length
            last = last or bool(flags & _FIEMAP_EXTENT_LAST)
        if last or mapped < count:
            break
    return (encoded / total if total else 0.0), shared


def _open_regular(root_fd: int, relative: str) -> int:
    """Descriptor-only, no-symlink walk from the verified root."""

    parts = Path(validate_relative_path(relative)).parts
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors: list[int] = []
    try:
        current = root_fd
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def sample_digest(descriptor: int, size: int) -> str:
    """Full hash for small files, start/middle/end samples otherwise (no content kept)."""

    digest = hashlib.sha256()
    if size <= FULL_HASH_LIMIT:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return "full:" + digest.hexdigest()
    for offset in (0, max(0, size // 2 - SAMPLE_CHUNK // 2), max(0, size - SAMPLE_CHUNK)):
        digest.update(os.pread(descriptor, SAMPLE_CHUNK, offset))
    return "sample:" + digest.hexdigest()


def sampled_gain(descriptor: int, size: int) -> tuple[float, int]:
    """zlib ratio of bounded samples: an *estimate* of compressibility, not Zstd."""

    offsets = [0] if size <= SAMPLE_CHUNK else [0, max(0, size // 2 - SAMPLE_CHUNK // 2), max(0, size - SAMPLE_CHUNK)]
    raw = 0
    packed = 0
    for offset in dict.fromkeys(offsets):
        chunk = os.pread(descriptor, min(SAMPLE_CHUNK, size), offset)
        if not chunk:
            continue
        raw += len(chunk)
        packed += len(zlib.compress(chunk, 3))
    if raw == 0:
        return 0.0, 0
    return max(0.0, 1.0 - packed / raw), raw


# -- planner ----------------------------------------------------------------------------------


class BtrfsRecompressionPlanner:
    """Read-only planning, measurement probes and the job journal."""

    def __init__(
        self,
        provider: Any,
        *,
        root: Path = RECOMPRESSION_DIRECTORY,
        executable_finder: Callable[[str], str | None] = shutil.which,
        filesystem_provider: Any = None,
        total_sample_limit: int = DEFAULT_TOTAL_SAMPLE_LIMIT,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
        statvfs: Callable[[Any], Any] = os.statvfs,
        fiemap: Callable[[int, int], tuple[float, bool]] = fiemap_summary,
    ) -> None:
        self._provider = provider
        self.root = Path(root)
        self._which = executable_finder
        self._filesystem_provider = filesystem_provider
        self._total_sample_limit = int(total_sample_limit)
        self._disk_usage = disk_usage
        self._statvfs = statvfs
        self._fiemap = fiemap
        self._capabilities: RecompressionCapabilities | None = None
        self._lock = RLock()

    # capabilities --------------------------------------------------------------

    def capabilities(self, *, refresh: bool = False) -> RecompressionCapabilities:
        with self._lock:
            if self._capabilities is not None and not refresh:
                return self._capabilities
            btrfs = self._which("btrfs")
            if not btrfs:
                self._capabilities = RecompressionCapabilities()
                return self._capabilities
            read = getattr(self._provider, "_read_help")
            capabilities = parse_capabilities(
                read([btrfs, "--version"]),
                read([btrfs, "filesystem", "defragment", "--help"]),
                read([btrfs, "filesystem", "du", "--help"]),
                read([btrfs, "property", "--help"]),
                compsize_available=bool(self._which("compsize")),
                exact_measurement_available=bool(getattr(getattr(self._provider, "_measurement_provider", None),
                                                         "installed", False)),
            )
            self._capabilities = capabilities
            return capabilities

    # measurement helpers ---------------------------------------------------------

    def _du_shared(self, root_fd: int, relatives: Sequence[str], cancel: Event | None) -> dict[str, tuple[int, int, int] | None]:
        """``btrfs filesystem du --raw`` per file, in batches of fd paths.

        Each row is mapped back through its ``/proc/self/fd/N`` label, so an
        unexpected or missing row leaves that file "unknown" (skipped).
        """

        btrfs = self._which("btrfs")
        result: dict[str, tuple[int, int, int] | None] = {relative: None for relative in relatives}
        if not btrfs:
            return result
        for start in range(0, len(relatives), DU_BATCH):
            if cancel is not None and cancel.is_set():
                raise RecompressionError("cancelled")
            batch = list(relatives[start:start + DU_BATCH])
            descriptors: dict[int, str] = {}
            try:
                for relative in batch:
                    try:
                        descriptors[_open_regular(root_fd, relative)] = relative
                    except OSError:
                        continue
                if not descriptors:
                    continue
                argv = [btrfs, "filesystem", "du", "--raw", *(f"/proc/self/fd/{fd}" for fd in descriptors)]
                completed = self._provider._run_command(argv, cancel_event=None, pass_fds=tuple(descriptors), timeout=120.0)
                if completed.returncode != 0:
                    continue
                for line in str(completed.stdout or "").splitlines():
                    match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+/proc/self/fd/(\d+)\s*$", line)
                    if match and int(match.group(4)) in descriptors:
                        result[descriptors[int(match.group(4))]] = (
                            int(match.group(1)), int(match.group(2)), int(match.group(3)))
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)
        return result

    # planning -------------------------------------------------------------------------

    def plan(
        self,
        game: Game,
        deep_plan: DeepOptimizationPlan,
        deep_fingerprint: str,
        level: int | None,
        *,
        measurement_source: str = "none",
        cancel: Event | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> BtrfsCompressionPlan:
        capabilities = self.capabilities()
        if not capabilities.recompression_available:
            raise RecompressionError("capabilities_missing")
        if deep_plan.status != "completed" or not deep_plan.complete or deep_plan.stale or not deep_plan.root_real:
            raise RecompressionError("deep_plan_required", stale=deep_plan.stale)
        if not deep_plan.btrfs.is_btrfs:
            raise RecompressionError("not_btrfs")
        if level is not None and level not in capabilities.levels:
            raise RecompressionError("level_unsupported", str(level))
        root = Path(game.install_path)
        root_fd = self._open_root(root, deep_plan.root_real, deep_plan.root_device, deep_plan.root_inode)
        # Files the user may move to quarantine are never recompressed.
        selectable = {group.group_id for group in deep_plan.groups if group.actionability == "selectable"}
        removal = {item.relative_path for group_id, items in deep_plan.items.items()
                   if group_id in selectable for item in items}
        candidates: list[RecompressionCandidate] = []
        pending_shared: list[int] = []
        sampled_total = 0
        try:
            stack: list[tuple[int, str]] = [(os.dup(root_fd), "")]
            seen = {(deep_plan.root_device, deep_plan.root_inode)}
            last = time.monotonic()
            while stack:
                directory_fd, relative_directory = stack.pop()
                try:
                    with os.scandir(directory_fd) as iterator:
                        entries = list(iterator)
                    for entry in entries:
                        if cancel is not None and cancel.is_set():
                            raise RecompressionError("cancelled")
                        relative = f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISLNK(info.st_mode):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if info.st_dev != deep_plan.root_device or (info.st_dev, info.st_ino) in seen:
                                continue  # mount points and loops are never entered
                            seen.add((info.st_dev, info.st_ino))
                            try:
                                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                                dir_fd=directory_fd)
                            except OSError:
                                continue
                            stack.append((child, relative))
                            continue
                        if not stat.S_ISREG(info.st_mode) or info.st_dev != deep_plan.root_device:
                            continue
                        candidate, read = self._classify(directory_fd, entry.name, relative, info,
                                                         removal, sampled_total)
                        sampled_total += read
                        candidates.append(candidate)
                        if candidate.status == "candidate" or candidate.reason == "shared_unknown":
                            pending_shared.append(len(candidates) - 1)
                        if progress is not None and time.monotonic() - last >= 0.2:
                            last = time.monotonic()
                            progress({"stage": "sampling", "files": len(candidates), "sampledBytes": sampled_total})
                finally:
                    os.close(directory_fd)
            # Shared extents: confirmed per file, in batches; unknown -> skipped.
            relatives = [candidates[index].relative_path for index in pending_shared]
            if progress is not None:
                progress({"stage": "shared_extents", "files": len(candidates), "sampledBytes": sampled_total})
            shared = self._du_shared(root_fd, relatives, cancel)
            for index in pending_shared:
                item = candidates[index]
                values = shared.get(item.relative_path)
                if values is None:
                    candidates[index] = replace(item, status="skipped", reason="shared_unknown",
                                                estimated_savings_low=0, estimated_savings_high=0)
                elif values[2] > 0:
                    candidates[index] = replace(item, status="skipped", reason="shared_extents", shared_bytes=values[2],
                                                exclusive_bytes=values[1], estimated_savings_low=0, estimated_savings_high=0)
                else:
                    candidates[index] = replace(item, status="candidate", reason="", shared_bytes=0,
                                                exclusive_bytes=values[1])
        finally:
            os.close(root_fd)
        candidates.sort(key=lambda item: item.relative_path)
        return BtrfsCompressionPlan(
            plan_id=datetime.now(UTC).strftime("%Y%m%d%H%M%S") + secrets.token_hex(5),
            game_id=game.id, app_id=str(game.steam_app_id or ""), game_root=deep_plan.root_real,
            root_device=deep_plan.root_device, root_inode=deep_plan.root_inode,
            deep_plan_fingerprint=deep_fingerprint, capabilities=capabilities,
            measurement_source=measurement_source, profile_level=level, algorithm="zstd",
            candidates=tuple(candidates),
        )

    def _classify(self, directory_fd, name, relative, info, removal, sampled_total):
        extension = extension_of(name)
        base = dict(relative_path=relative, size=int(info.st_size),
                    allocated=int(getattr(info, "st_blocks", 0)) * 512, device=info.st_dev, inode=info.st_ino,
                    mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns, nlink=info.st_nlink, extension=extension)

        def skipped(reason: str, **changes) -> tuple[RecompressionCandidate, int]:
            return RecompressionCandidate(**base, status="skipped", reason=reason, **changes), 0

        if relative in removal:
            return skipped("selected_for_removal")
        if extension in PACKAGE_EXTENSIONS or name.casefold() in PACKAGE_NAMES:
            return skipped("core_archive")
        if extension in TEXTURE_EXTENSIONS:
            return skipped("required_texture")
        if extension in SKIPPED_FORMATS:
            return skipped("format_skipped")
        if info.st_nlink > 1:
            return skipped("hardlink")
        if info.st_size < MIN_CANDIDATE_BYTES:
            return skipped("too_small")
        if int(getattr(info, "st_blocks", 0)) * 512 + 4096 < info.st_size:
            return skipped("sparse")  # rewriting could allocate the holes
        if sampled_total >= self._total_sample_limit:
            return skipped("not_sampled")
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        except OSError:
            return skipped("unreadable")
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                return skipped("unreadable")
            encoded, shared = self._fiemap(descriptor, info.st_size)
            if shared:
                return skipped("shared_extents")
            if encoded >= 0.9:
                return RecompressionCandidate(**base, status="already_compressed"), 0
            gain, read = sampled_gain(descriptor, info.st_size)
        except OSError:
            return skipped("unreadable")
        finally:
            os.close(descriptor)
        if gain < MIN_SAMPLED_GAIN:
            return RecompressionCandidate(**base, status="skipped", reason="low_compressibility",
                                          sample_bytes=read, estimated_gain=round(gain, 3)), read
        low = int(info.st_size * max(0.0, gain - 0.15))
        high = int(info.st_size * min(0.95, gain + 0.05))
        # Shared state is unknown until the batched du check confirms 0.
        return RecompressionCandidate(**base, status="skipped", reason="shared_unknown", sample_bytes=read,
                                      estimated_gain=round(gain, 3), estimated_savings_low=low,
                                      estimated_savings_high=high), read

    def _open_root(self, root: Path, real: str, device: int, inode: int) -> int:
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            raise RecompressionError("game_root_changed", stale=True) from error
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (device, inode) or os.path.realpath(root) != real:
            os.close(descriptor)
            raise RecompressionError("game_root_changed", stale=True)
        return descriptor

    # journal ----------------------------------------------------------------------------

    def _job_path(self, job: CompressionJob) -> Path:
        return self.root / hashlib.sha256(job.game_id.encode("utf-8")).hexdigest()[:32] / f"{job.job_id}.json"

    def save_job(self, job: CompressionJob) -> CompressionJob:
        _durable_write(self._job_path(job), json.dumps(job.to_dict(), indent=1) + "\n")
        return job

    def jobs(self, game_id: str | None = None) -> list[CompressionJob]:
        result = []
        for path in sorted(self.root.glob("*/*.json")):
            try:
                job = CompressionJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if game_id is None or job.game_id == game_id:
                result.append(job)
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    def recover(self) -> list[CompressionJob]:
        """Never resume: mark interrupted jobs partial and re-measure finished files."""

        recovered = []
        for job in self.jobs():
            if job.state in SETTLED_JOB_STATES:
                continue
            entries = []
            for entry in job.entries:
                if entry.status == "running":
                    entry = replace(entry, status="interrupted", reason="crash")
                elif entry.status == "compressed":
                    entry = replace(entry, encoded_after=self._remeasure(job.game_root, entry))
                entries.append(entry)
            job = replace(job, entries=tuple(entries))
            done = job.counts()["compressed"] + job.counts()["no_gain"]
            job = self.save_job(job.with_state("partial" if done else "cancelled", recovered=True,
                                               note="interrupted_new_analysis_required"))
            recovered.append(job)
        return recovered

    def _remeasure(self, root: str, entry: CompressionJobEntry) -> float | None:
        try:
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            descriptor = _open_regular(root_fd, entry.relative_path)
        except OSError:
            return None
        finally:
            os.close(root_fd)
        try:
            return round(self._fiemap(descriptor, entry.size)[0], 3)
        except OSError:
            return None
        finally:
            os.close(descriptor)


# -- executor -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecompressionGuards:
    game_running: Callable[[Game], bool]
    game_updating: Callable[[Game], bool]
    quarantine_active: Callable[[Game], bool]


class BtrfsRecompressionExecutor:
    """Recompress the selected candidates one by one; never in parallel."""

    def __init__(self, planner: BtrfsRecompressionPlanner, guards: RecompressionGuards) -> None:
        self.planner = planner
        self._guards = guards

    def preflight(
        self,
        game: Game,
        plan: BtrfsCompressionPlan,
        deep_plan: DeepOptimizationPlan,
        deep_fingerprint: str,
        selected_types: Iterable[str],
    ) -> list[RecompressionCandidate]:
        types = {str(value) for value in selected_types}
        selected = [item for item in plan.candidates
                    if item.status == "candidate" and (item.extension or "(none)") in types]
        if not selected:
            raise RecompressionError("nothing_selected")
        if self._guards.game_running(game):
            raise RecompressionError("game_running")
        if self._guards.game_updating(game):
            raise RecompressionError("game_updating")
        if self._guards.quarantine_active(game):
            raise RecompressionError("quarantine_active")
        if deep_plan.stale or not deep_plan.complete or deep_fingerprint != plan.deep_plan_fingerprint:
            raise RecompressionError("plan_stale", stale=True)
        capabilities = self.planner.capabilities(refresh=True)
        if capabilities.help_digest != plan.capabilities.help_digest or not capabilities.recompression_available:
            raise RecompressionError("capabilities_changed", stale=True)
        inspect = getattr(self.planner._filesystem_provider, "inspect", None)
        if callable(inspect):
            try:
                if inspect(Path(game.install_path)).filesystem != FilesystemType.BTRFS:
                    raise RecompressionError("not_btrfs")
            except RecompressionError:
                raise
            except Exception as error:
                raise RecompressionError("not_btrfs", type(error).__name__) from error
        root_fd = self.planner._open_root(Path(game.install_path), plan.game_root, plan.root_device, plan.root_inode)
        try:
            try:
                flags = self.planner._statvfs(root_fd).f_flag
            except OSError as error:
                raise RecompressionError("filesystem_read_only") from error
            if flags & getattr(os, "ST_RDONLY", 1):
                raise RecompressionError("filesystem_read_only")
            for item in selected:
                try:
                    descriptor = _open_regular(root_fd, item.relative_path)
                except OSError as error:
                    raise RecompressionError("plan_stale", item.relative_path, stale=True) from error
                try:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                            != (item.device, item.inode, item.size, item.mtime_ns)):
                        raise RecompressionError("plan_stale", item.relative_path, stale=True)
                    if int(getattr(info, "st_blocks", 0)) * 512 + 4096 < info.st_size:
                        raise RecompressionError("plan_stale", item.relative_path, stale=True)
                finally:
                    os.close(descriptor)
            shared = self.planner._du_shared(root_fd, [item.relative_path for item in selected], None)
            for item in selected:
                values = shared.get(item.relative_path)
                if values is None or values[2] != 0:
                    raise RecompressionError("shared_extents", item.relative_path, stale=True)
            try:
                free = int(self.planner._disk_usage(Path(plan.game_root)).free)
            except (OSError, AttributeError, TypeError, ValueError):
                free = 0
            # Files are processed one at a time: CoW needs room for the largest.
            if free < max(item.size for item in selected) + FREE_SPACE_MARGIN:
                raise RecompressionError("not_enough_space")
        finally:
            os.close(root_fd)
        return selected

    def run(
        self,
        game: Game,
        plan: BtrfsCompressionPlan,
        deep_plan: DeepOptimizationPlan,
        deep_fingerprint: str,
        selected_types: Iterable[str],
        *,
        baseline: Mapping[str, Any] | None = None,
        cancel: Event | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> CompressionJob:
        selected = self.preflight(game, plan, deep_plan, deep_fingerprint, selected_types)
        job = CompressionJob(
            job_id=datetime.now(UTC).strftime("%Y%m%d%H%M%S") + secrets.token_hex(5),
            game_id=game.id, plan_id=plan.plan_id, deep_plan_fingerprint=deep_fingerprint,
            game_root=plan.game_root, root_device=plan.root_device, root_inode=plan.root_inode,
            algorithm=plan.algorithm, profile_level=plan.profile_level,
            capabilities=plan.capabilities, state="planned", baseline=dict(baseline or {}),
            entries=tuple(CompressionJobEntry(item.relative_path, item.extension, item.size, item.device,
                                              item.inode, item.mtime_ns) for item in selected),
        )
        job = self.planner.save_job(job)  # before the first file
        job = self.planner.save_job(job.with_state("running"))
        btrfs = self.planner._which("btrfs")
        started = time.monotonic()
        processed_bytes = 0
        no_gain: dict[str, int] = {}
        blocked_types: set[str] = set()
        for index, entry in enumerate(job.entries):
            if cancel is not None and cancel.is_set():
                # Already processed files stay as they are: no rollback exists.
                job = self.planner.save_job(job.with_state("cancel_requested"))
                break
            if entry.extension in blocked_types:
                job = self.planner.save_job(job.with_entry(index, status="skipped", reason="no_gain_limit"))
                continue
            if self._guards.game_running(game) or self._guards.game_updating(game):
                job = self.planner.save_job(job.with_entry(index, status="skipped", reason="game_became_busy"))
                job = self.planner.save_job(job.with_state("partial", note="game_became_busy"))
                return job
            if progress is not None:
                progress({"stage": "recompressing", "files": index, "totalFiles": len(job.entries),
                          "bytes": processed_bytes, "totalBytes": sum(item.size for item in job.entries),
                          "currentFile": entry.relative_path, "elapsed": round(time.monotonic() - started, 1)})
            job = self.planner.save_job(job.with_entry(index, status="running"))
            file_started = time.monotonic()
            try:
                status, reason, before, after = self._recompress_one(btrfs, job, entry)
            except RecompressionError as error:
                job = self.planner.save_job(job.with_entry(index, status="failed", reason=error.code))
                job = self.planner.save_job(job.with_state("failed", note=error.code,
                                                           errors=(*job.errors, error.code)))
                return job  # content/identity changes stop everything at once
            job = self.planner.save_job(job.with_entry(
                index, status=status, reason=reason, encoded_before=before, encoded_after=after,
                seconds=round(time.monotonic() - file_started, 2)))
            processed_bytes += entry.size
            if status == "no_gain":
                no_gain[entry.extension] = no_gain.get(entry.extension, 0) + 1
                if no_gain[entry.extension] >= NO_GAIN_LIMIT:
                    blocked_types.add(entry.extension)
            else:
                no_gain[entry.extension] = 0
        counts = job.counts()
        if job.state == "cancel_requested":
            state, note = "cancelled", "cancelled_no_rollback"
        elif blocked_types:
            state, note = "partial", "no_gain_limit_new_analysis"
        else:
            state, note = "completed", ""
        if counts["failed"]:
            state = "failed"
        return self.planner.save_job(job.with_state(state, note=note))

    def _recompress_one(self, btrfs: str | None, job: CompressionJob, entry: CompressionJobEntry):
        if not btrfs:
            raise RecompressionError("btrfs_missing")
        root_fd = self.planner._open_root(Path(job.game_root), job.game_root, job.root_device, job.root_inode)
        try:
            descriptor = _open_regular(root_fd, entry.relative_path)
        except OSError as error:
            os.close(root_fd)
            raise RecompressionError("file_changed", entry.relative_path) from error
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    != (entry.device, entry.inode, entry.size, entry.mtime_ns)):
                raise RecompressionError("file_changed", entry.relative_path)
            encoded_before, shared_before = self.planner._fiemap(descriptor, entry.size)
            values = self.planner._du_shared(root_fd, [entry.relative_path], None).get(entry.relative_path)
            if shared_before or values is None or values[2] != 0:
                return "skipped", "shared_extents", round(encoded_before, 3), None
            digest_before = sample_digest(descriptor, entry.size)
            argv = build_defragment_argv(btrfs, job.capabilities, job.algorithm, job.profile_level,
                                         f"/proc/self/fd/{descriptor}")
            # cancel_event=None: a running defragment is never killed mid-file.
            completed = self.planner._provider._run_command(argv, cancel_event=None, pass_fds=(descriptor,),
                                                             timeout=3600.0)
            if completed.returncode != 0:
                raise RecompressionError("defragment_failed", str(completed.returncode))
            after_info = os.fstat(descriptor)
            if (after_info.st_dev, after_info.st_ino, after_info.st_size, after_info.st_mtime_ns) != (
                    entry.device, entry.inode, entry.size, entry.mtime_ns):
                raise RecompressionError("file_changed", entry.relative_path)
            if sample_digest(descriptor, entry.size) != digest_before:
                raise RecompressionError("content_changed", entry.relative_path)
            encoded_after, shared_after = self.planner._fiemap(descriptor, entry.size)
            after_values = self.planner._du_shared(root_fd, [entry.relative_path], None).get(entry.relative_path)
            grew = after_values is None or shared_after or after_values[1] > values[1] + 4096
            if grew or encoded_after < max(0.5, encoded_before + 0.1):
                return "no_gain", "allocation_grew" if grew else "kernel_kept_uncompressed", \
                    round(encoded_before, 3), round(encoded_after, 3)
            return "compressed", "", round(encoded_before, 3), round(encoded_after, 3)
        finally:
            os.close(descriptor)
            os.close(root_fd)


# -- future files property ---------------------------------------------------------------------------


class FutureCompressionProperty:
    """``btrfs property`` on the game root only, when restoring is documented."""

    def __init__(self, planner: BtrfsRecompressionPlanner) -> None:
        self.planner = planner

    def _state_path(self, game_id: str) -> Path:
        return self.planner.root / hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:32] / "property.json"

    def read(self, root: Path) -> str:
        capabilities = self.planner.capabilities()
        btrfs = self.planner._which("btrfs")
        if not capabilities.property_get or not btrfs:
            raise RecompressionError("property_unavailable")
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            completed = self.planner._provider._run_command(
                [btrfs, "property", "get", "-t", "inode", f"/proc/self/fd/{descriptor}", "compression"],
                cancel_event=None, pass_fds=(descriptor,), timeout=30.0)
        finally:
            os.close(descriptor)
        if completed.returncode != 0:
            raise RecompressionError("property_unreadable")
        text = str(completed.stdout or "").strip()
        match = re.fullmatch(r"(?:compression=)?([a-z0-9:]*)", text)
        if match is None:
            raise RecompressionError("property_unreadable")
        return match.group(1)

    def state(self, game_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._state_path(game_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def enable(self, game: Game) -> dict[str, Any]:
        capabilities = self.planner.capabilities()
        if not capabilities.future_property_available:
            raise RecompressionError("property_unavailable")
        root = Path(game.install_path)
        previous = self.read(root)
        _durable_write(self._state_path(game.id), json.dumps({"previous": previous, "enabled": True}) + "\n")
        self._set(root, "zstd")  # a level is not documented for the property
        return {"previous": previous, "enabled": True}

    def restore(self, game: Game) -> dict[str, Any]:
        state = self.state(game.id)
        if not state.get("enabled"):
            raise RecompressionError("property_not_changed")
        self._set(Path(game.install_path), str(state.get("previous", "")))
        _durable_write(self._state_path(game.id), json.dumps({"previous": state.get("previous", ""), "enabled": False}) + "\n")
        return {"enabled": False}

    def _set(self, root: Path, value: str) -> None:
        btrfs = self.planner._which("btrfs")
        if not btrfs or not self.planner.capabilities().future_property_available:
            raise RecompressionError("property_unavailable")
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            completed = self.planner._provider._run_command(
                [btrfs, "property", "set", "-t", "inode", f"/proc/self/fd/{descriptor}", "compression", value],
                cancel_event=None, pass_fds=(descriptor,), timeout=30.0)
        finally:
            os.close(descriptor)
        if completed.returncode != 0:
            raise RecompressionError("property_set_failed")


__all__ = [
    "BtrfsRecompressionExecutor",
    "BtrfsRecompressionPlanner",
    "FutureCompressionProperty",
    "RECOMPRESSION_DIRECTORY",
    "RecompressionError",
    "RecompressionGuards",
    "SKIPPED_FORMATS",
    "build_defragment_argv",
    "fiemap_summary",
    "parse_capabilities",
    "sample_digest",
    "sampled_gain",
]
