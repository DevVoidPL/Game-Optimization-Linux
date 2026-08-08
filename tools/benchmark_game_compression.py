#!/usr/bin/env python3
"""Read-only, stratified compression benchmark for an installed game.

The tool deliberately never writes below the game directory.  It traverses the
tree with directory descriptors and ``O_NOFOLLOW``, does not cross the root
filesystem boundary, and copies only bounded sample windows into a temporary
file.  Compression is performed against that temporary file.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import dataclasses
import datetime as dt
import errno
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Final


SCHEMA_VERSION: Final[int] = 2
METHODOLOGY_VERSION: Final[int] = 2
TOOL_VERSION: Final[str] = "2.0.0"
DEFAULT_SAMPLE_LIMIT: Final[int] = 512 * 1024 * 1024
DEFAULT_REFERENCE_LIMIT: Final[int] = 128 * 1024 * 1024
DEFAULT_WINDOW_SIZE: Final[int] = 4 * 1024 * 1024
MAX_SAMPLE_LIMIT: Final[int] = 2 * 1024 * 1024 * 1024
MAX_REFERENCE_LIMIT: Final[int] = 512 * 1024 * 1024
MAX_WINDOW_SIZE: Final[int] = 64 * 1024 * 1024
DEFAULT_EXTERNAL_TIMEOUT_SECONDS: Final[float] = 15 * 60
MAX_EXTERNAL_TIMEOUT_SECONDS: Final[float] = 60 * 60
DEFAULT_BENCHMARK_TIMEOUT_SECONDS: Final[float] = 2 * 60 * 60
MAX_BENCHMARK_TIMEOUT_SECONDS: Final[float] = 8 * 60 * 60
REFERENCE_MEMORY_LIMIT_MIB: Final[int] = 1024
TEMP_SPACE_RESERVE: Final[int] = 256 * 1024 * 1024
READ_CHUNK: Final[int] = 1024 * 1024
BTRFS_EXTENT_CHUNK: Final[int] = 128 * 1024
SIGNIFICANT_GROUP_SHARE: Final[float] = 0.005

ProgressCallback = Callable[[str, float, str], None]
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class BenchmarkCancelled(RuntimeError):
    """Raised when a benchmark is cancelled without modifying source data."""


@dataclasses.dataclass(frozen=True, slots=True)
class FileRecord:
    components: tuple[str, ...]
    size: int
    device: int
    inode: int
    mtime_ns: int
    extension: str
    directory_group: str
    link_count: int = 1

    @property
    def relative_path(self) -> str:
        return "/".join(self.components)

    @property
    def group_key(self) -> tuple[str, str]:
        return self.directory_group, self.extension


@dataclasses.dataclass(slots=True)
class FileGroup:
    directory: str
    extension: str
    files: list[FileRecord] = dataclasses.field(default_factory=list)
    logical_bytes: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return self.directory, self.extension


@dataclasses.dataclass(frozen=True, slots=True)
class SampleSlice:
    file: FileRecord
    offset: int
    length: int


@dataclasses.dataclass(frozen=True, slots=True)
class MaterializedSegment:
    offset: int
    length: int
    group_key: tuple[str, str]


@dataclasses.dataclass(slots=True)
class ScanResult:
    root: Path
    root_device: int
    files: list[FileRecord]
    directory_count: int
    logical_bytes: int
    symlinks_skipped: int
    cross_filesystem_skipped: int
    special_files_skipped: int
    permission_errors: list[str]
    namespace_file_count: int = 0
    namespace_logical_bytes: int = 0
    hardlinked_files: int = 0
    hardlink_entries_skipped: int = 0
    hardlink_duplicate_bytes: int = 0
    sparse_files: int = 0
    sparse_hole_bytes: int = 0
    sparse_unknown_files: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class SteamManifestSnapshot:
    path: Path
    app_id: str
    name: str
    install_dir: str
    state_flags: int
    build_id: str
    size_on_disk: int | None
    sha256: str


@dataclasses.dataclass(slots=True)
class SamplingPlan:
    slices: list[SampleSlice]
    group_logical_bytes: dict[tuple[str, str], int]
    group_sampled_bytes: dict[tuple[str, str], int]
    sample_limit: int
    window_size: int
    file_sampled_bytes: dict[tuple[str, ...], int] = dataclasses.field(
        default_factory=dict
    )
    group_file_distribution_tv: dict[tuple[str, str], float] = (
        dataclasses.field(default_factory=dict)
    )
    group_size_strata_distribution_tv: dict[tuple[str, str], float] = (
        dataclasses.field(default_factory=dict)
    )
    group_files_sampled: dict[tuple[str, str], int] = dataclasses.field(
        default_factory=dict
    )
    group_file_population_bytes_covered: dict[tuple[str, str], int] = (
        dataclasses.field(default_factory=dict)
    )

    @property
    def sampled_bytes(self) -> int:
        return sum(item.length for item in self.slices)


@dataclasses.dataclass(frozen=True, slots=True)
class CapabilityReport:
    btrfs_path: str | None
    btrfs_version: str | None
    btrfs_help: str
    kernel_release: str
    btrfs_zstd: bool
    btrfs_zlib: bool
    btrfs_level_syntax: bool
    btrfs_zstd_level_range: tuple[int, int] | None
    btrfs_zlib_level_range: tuple[int, int] | None
    btrfs_zstd_levels: tuple[int, ...]
    btrfs_zlib_levels: tuple[int, ...]
    zstd_path: str | None
    zstd_version: str | None
    zstd_help: str
    xz_path: str | None
    xz_version: str | None
    evidence: tuple[str, ...]


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise BenchmarkCancelled("benchmark cancelled")


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _first_line(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().splitlines()[0] if value.strip() else None


def _feature_file_enabled(name: str) -> bool:
    path = Path("/sys/fs/btrfs/features") / name
    # Presence describes kernel support.  Several feature files legitimately
    # contain ``0`` until a mounted filesystem uses that feature.
    return path.is_file()


def _parse_btrfs_defrag_help(help_text: str) -> tuple[bool, bool, bool]:
    lowered = help_text.lower()
    has_zstd = "zstd" in lowered
    has_zlib = "zlib" in lowered
    level_syntax = bool(
        re.search(r"(?:zstd|zlib)[^\n]{0,32}(?::|\[?level\]?)", lowered)
        or re.search(r"-c[^\n]{0,80}level", lowered)
    )
    return has_zstd, has_zlib, level_syntax


def _parse_level_range(help_text: str, algorithm: str) -> tuple[int, int] | None:
    match = re.search(
        rf"{re.escape(algorithm)}\s*:\s*(-?\d+)\s*\.\.\s*(-?\d+)",
        help_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (min(low, high), max(low, high))


def _representative_levels(
    supported_range: tuple[int, int] | None,
    candidates: Sequence[int],
) -> tuple[int, ...]:
    if supported_range is None:
        return ()
    low, high = supported_range
    return tuple(level for level in candidates if low <= level <= high)


def detect_capabilities(
    *,
    runner: CommandRunner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
) -> CapabilityReport:
    """Detect algorithms and command syntax without executing a write action."""

    evidence: list[str] = []
    btrfs_path = which("btrfs")
    btrfs_version: str | None = None
    btrfs_help = ""
    help_zstd = help_zlib = level_syntax = False
    if btrfs_path:
        try:
            version_result = runner([btrfs_path, "--version"])
            btrfs_version = _first_line(
                (version_result.stdout or "") + (version_result.stderr or "")
            )
            evidence.append("btrfs --version")
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            help_result = runner(
                [btrfs_path, "filesystem", "defragment", "--help"]
            )
            btrfs_help = (help_result.stdout or "") + (help_result.stderr or "")
            help_zstd, help_zlib, level_syntax = _parse_btrfs_defrag_help(
                btrfs_help
            )
            evidence.append("btrfs filesystem defragment --help")
        except (OSError, subprocess.SubprocessError):
            pass

    sysfs_zstd = _feature_file_enabled("compress_zstd")
    sysfs_zlib = _feature_file_enabled("compress_zlib")
    if sysfs_zstd or sysfs_zlib:
        evidence.append("/sys/fs/btrfs/features")
    btrfs_zstd = help_zstd and (
        sysfs_zstd or not Path("/sys/fs/btrfs/features").exists()
    )
    # zlib is Btrfs' baseline compressor and has no compress_zlib feature file.
    btrfs_zlib = help_zlib
    zstd_range = _parse_level_range(btrfs_help, "zstd")
    zlib_range = _parse_level_range(btrfs_help, "zlib")
    # Select representative low/default/high points only from the ranges
    # printed by the installed btrfs-progs.  External zstd-19 remains separate.
    zstd_levels = (
        _representative_levels(zstd_range, (1, 3, 6, 9, 15))
        if btrfs_zstd and level_syntax
        else ()
    )
    zlib_levels = (
        _representative_levels(zlib_range, (1, 3, 6, 9))
        if btrfs_zlib and level_syntax
        else ()
    )

    zstd_path = which("zstd")
    zstd_version: str | None = None
    zstd_help = ""
    if zstd_path:
        try:
            version_result = runner([zstd_path, "--version"])
            zstd_version = _first_line(
                (version_result.stdout or "") + (version_result.stderr or "")
            )
            help_result = runner([zstd_path, "--help"])
            zstd_help = (help_result.stdout or "") + (help_result.stderr or "")
            evidence.extend(("zstd --version", "zstd --help"))
        except (OSError, subprocess.SubprocessError):
            zstd_path = None

    xz_path = which("xz")
    xz_version: str | None = None
    if xz_path:
        try:
            version_result = runner([xz_path, "--version"])
            xz_version = _first_line(
                (version_result.stdout or "") + (version_result.stderr or "")
            )
            evidence.append("xz --version")
        except (OSError, subprocess.SubprocessError):
            xz_path = None

    return CapabilityReport(
        btrfs_path=btrfs_path,
        btrfs_version=btrfs_version,
        btrfs_help=btrfs_help,
        kernel_release=platform.release(),
        btrfs_zstd=btrfs_zstd,
        btrfs_zlib=btrfs_zlib,
        btrfs_level_syntax=level_syntax,
        btrfs_zstd_level_range=zstd_range,
        btrfs_zlib_level_range=zlib_range,
        btrfs_zstd_levels=zstd_levels,
        btrfs_zlib_levels=zlib_levels,
        zstd_path=zstd_path,
        zstd_version=zstd_version,
        zstd_help=zstd_help,
        xz_path=xz_path,
        xz_version=xz_version,
        evidence=tuple(evidence),
    )


def _extension_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if suffix else "[no extension]"


def _directory_group(components: tuple[str, ...]) -> str:
    if len(components) <= 1:
        return "."
    return components[0]


def _readonly_flags(*, directory: bool = False) -> int:
    """Return fail-closed flags that do not update source access times."""

    if not hasattr(os, "O_NOATIME"):
        raise OSError(
            "O_NOATIME is unavailable; refusing to risk changing source atime"
        )
    flags = os.O_RDONLY | os.O_NOATIME
    if directory:
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _read_noatime_nofollow(path: Path, *, maximum_bytes: int) -> bytes:
    file_fd = os.open(path, _readonly_flags())
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError(f"not a regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError(f"file exceeds {maximum_bytes} bytes: {path}")
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def read_steam_manifest(
    manifest_path: Path | str,
    *,
    expected_app_id: str,
    expected_game_path: Path,
) -> SteamManifestSnapshot:
    """Read and validate a Steam appmanifest without changing its atime."""

    unresolved = Path(manifest_path).expanduser()
    if unresolved.is_symlink():
        raise OSError("Steam manifest must not be a symlink")
    path = unresolved.resolve(strict=True)
    raw = _read_noatime_nofollow(path, maximum_bytes=4 * 1024 * 1024)
    text_value = raw.decode("utf-8", errors="replace")
    pairs = {
        key.casefold(): value
        for key, value in re.findall(
            r'^\s*"([^"]+)"\s+"([^"]*)"\s*$',
            text_value,
            flags=re.MULTILINE,
        )
    }
    app_id = pairs.get("appid", "")
    install_dir = pairs.get("installdir", "")
    name = pairs.get("name", "")
    build_id = pairs.get("buildid", "")
    try:
        state_flags = int(pairs.get("stateflags", ""))
    except ValueError as exc:
        raise ValueError("Steam manifest has invalid StateFlags") from exc
    if app_id != str(expected_app_id):
        raise ValueError(
            f"Steam manifest AppID {app_id!r} does not match "
            f"{expected_app_id!r}"
        )
    if state_flags != 4:
        raise ValueError(
            f"Steam game is not in the fully-installed state "
            f"(StateFlags={state_flags})"
        )
    if not name or not install_dir or not build_id:
        raise ValueError("Steam manifest lacks name, installdir or buildid")
    expected_root = (
        path.parent / "common" / install_dir
    ).resolve(strict=True)
    if expected_root != expected_game_path.resolve(strict=True):
        raise ValueError(
            "Steam manifest installdir does not resolve to the selected game"
        )
    size_text = pairs.get("sizeondisk")
    try:
        size_on_disk = int(size_text) if size_text is not None else None
    except ValueError:
        size_on_disk = None
    return SteamManifestSnapshot(
        path=path,
        app_id=app_id,
        name=name,
        install_dir=install_dir,
        state_flags=state_flags,
        build_id=build_id,
        size_on_disk=size_on_disk,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _sparse_hole_bytes(
    directory_fd: int,
    name: str,
    expected: os.stat_result,
) -> int | None:
    """Detect true holes with SEEK_DATA/SEEK_HOLE without reading content."""

    if expected.st_size <= 0:
        return 0
    if not hasattr(os, "SEEK_DATA") or not hasattr(os, "SEEK_HOLE"):
        return None
    # Fully allocated files cannot contain an allocation-saving hole. This
    # avoids an extra open for the normal game-file case.
    if expected.st_blocks * 512 >= expected.st_size:
        return 0
    file_fd = os.open(name, _readonly_flags(), dir_fd=directory_fd)
    try:
        opened = os.fstat(file_fd)
        if (
            opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or opened.st_size != expected.st_size
        ):
            raise OSError("file changed while checking sparse ranges")
        data_bytes = 0
        position = 0
        while position < expected.st_size:
            try:
                data_offset = os.lseek(file_fd, position, os.SEEK_DATA)
            except OSError as exc:
                if exc.errno == errno.ENXIO:
                    break
                if exc.errno in {errno.EINVAL, errno.ENOTSUP}:
                    return None
                raise
            hole_offset = os.lseek(file_fd, data_offset, os.SEEK_HOLE)
            end = min(expected.st_size, hole_offset)
            data_bytes += max(0, end - data_offset)
            if end <= position:
                raise OSError("invalid SEEK_HOLE result")
            position = end
        return max(0, expected.st_size - data_bytes)
    finally:
        os.close(file_fd)


def scan_game(
    game_path: Path | str,
    *,
    cancel: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Scan regular files without following links or crossing filesystems."""

    token = cancel or CancellationToken()
    root = Path(game_path).expanduser()
    root_flags = _readonly_flags(directory=True)
    root_fd = os.open(root, root_flags)
    try:
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise NotADirectoryError(str(root))
        resolved_root = root.resolve(strict=True)
        files: list[FileRecord] = []
        seen_inodes: set[tuple[int, int]] = set()
        permission_errors: list[str] = []
        counters = {
            "directories": 1,
            "logical": 0,
            "namespace_files": 0,
            "namespace_logical": 0,
            "symlinks": 0,
            "cross_fs": 0,
            "special": 0,
            "hardlinked_files": 0,
            "hardlink_entries": 0,
            "hardlink_bytes": 0,
            "sparse_files": 0,
            "sparse_hole_bytes": 0,
            "sparse_unknown_files": 0,
        }

        def walk(directory_fd: int, components: tuple[str, ...]) -> None:
            token.check()
            try:
                with os.scandir(directory_fd) as entries:
                    ordered = sorted(entries, key=lambda item: item.name)
            except PermissionError:
                permission_errors.append("/".join(components) or ".")
                return
            for entry in ordered:
                token.check()
                child_components = components + (entry.name,)
                relative = "/".join(child_components)
                try:
                    child_stat = entry.stat(follow_symlinks=False)
                except PermissionError:
                    permission_errors.append(relative)
                    continue
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(child_stat.st_mode):
                    counters["symlinks"] += 1
                    continue
                if child_stat.st_dev != root_stat.st_dev:
                    counters["cross_fs"] += 1
                    continue
                if stat.S_ISREG(child_stat.st_mode):
                    counters["namespace_files"] += 1
                    counters["namespace_logical"] += child_stat.st_size
                    inode_key = (child_stat.st_dev, child_stat.st_ino)
                    if inode_key in seen_inodes:
                        counters["hardlink_entries"] += 1
                        counters["hardlink_bytes"] += child_stat.st_size
                        continue
                    seen_inodes.add(inode_key)
                    if child_stat.st_nlink > 1:
                        counters["hardlinked_files"] += 1
                    try:
                        hole_bytes = _sparse_hole_bytes(
                            directory_fd,
                            entry.name,
                            child_stat,
                        )
                    except PermissionError:
                        permission_errors.append(relative)
                        continue
                    if hole_bytes is None:
                        counters["sparse_unknown_files"] += 1
                    elif hole_bytes:
                        counters["sparse_files"] += 1
                        counters["sparse_hole_bytes"] += hole_bytes
                    record = FileRecord(
                        components=child_components,
                        size=child_stat.st_size,
                        device=child_stat.st_dev,
                        inode=child_stat.st_ino,
                        mtime_ns=child_stat.st_mtime_ns,
                        extension=_extension_for(entry.name),
                        directory_group=_directory_group(child_components),
                        link_count=child_stat.st_nlink,
                    )
                    files.append(record)
                    counters["logical"] += record.size
                    if progress and len(files) % 256 == 0:
                        progress(
                            "scan",
                            0.0,
                            f"Scanned {len(files)} files "
                            f"({counters['logical']} bytes)",
                        )
                    continue
                if stat.S_ISDIR(child_stat.st_mode):
                    flags = _readonly_flags(directory=True)
                    try:
                        child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
                        opened_stat = os.fstat(child_fd)
                        if (
                            opened_stat.st_dev != root_stat.st_dev
                            or opened_stat.st_ino != child_stat.st_ino
                            or not stat.S_ISDIR(opened_stat.st_mode)
                        ):
                            os.close(child_fd)
                            counters["cross_fs"] += 1
                            continue
                    except PermissionError:
                        permission_errors.append(relative)
                        continue
                    except (FileNotFoundError, NotADirectoryError, OSError):
                        continue
                    counters["directories"] += 1
                    try:
                        walk(child_fd, child_components)
                    finally:
                        os.close(child_fd)
                    continue
                counters["special"] += 1

        walk(root_fd, ())
        if progress:
            progress("scan", 1.0, f"Scanned {len(files)} files")
        return ScanResult(
            root=resolved_root,
            root_device=root_stat.st_dev,
            files=files,
            directory_count=counters["directories"],
            logical_bytes=counters["logical"],
            symlinks_skipped=counters["symlinks"],
            cross_filesystem_skipped=counters["cross_fs"],
            special_files_skipped=counters["special"],
            permission_errors=permission_errors,
            namespace_file_count=counters["namespace_files"],
            namespace_logical_bytes=counters["namespace_logical"],
            hardlinked_files=counters["hardlinked_files"],
            hardlink_entries_skipped=counters["hardlink_entries"],
            hardlink_duplicate_bytes=counters["hardlink_bytes"],
            sparse_files=counters["sparse_files"],
            sparse_hole_bytes=counters["sparse_hole_bytes"],
            sparse_unknown_files=counters["sparse_unknown_files"],
        )
    finally:
        os.close(root_fd)


def group_files(scan: ScanResult) -> list[FileGroup]:
    grouped: dict[tuple[str, str], FileGroup] = {}
    for record in scan.files:
        group = grouped.get(record.group_key)
        if group is None:
            group = FileGroup(record.directory_group, record.extension)
            grouped[record.group_key] = group
        group.files.append(record)
        group.logical_bytes += record.size
    return sorted(
        grouped.values(),
        key=lambda item: (-item.logical_bytes, item.directory, item.extension),
    )


def _inventory_fingerprint(scan: ScanResult) -> str:
    digest = hashlib.sha256()
    digest.update(f"root-device:{scan.root_device}\0".encode())
    for record in sorted(scan.files, key=lambda item: item.components):
        digest.update(
            record.relative_path.encode("utf-8", errors="surrogateescape")
        )
        digest.update(b"\0")
        digest.update(
            (
                f"{record.size}:{record.device}:{record.inode}:"
                f"{record.mtime_ns}:{record.link_count}\0"
            ).encode()
        )
    return digest.hexdigest()


def _tool_source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _merge_cluster_offsets(
    record: FileRecord,
    offsets: Sequence[int],
    window_size: int,
) -> list[SampleSlice]:
    """Merge adjacent file-aligned Btrfs clusters into bounded windows."""

    slices: list[SampleSlice] = []
    for offset in sorted(set(offsets)):
        if offset < 0 or offset % BTRFS_EXTENT_CHUNK:
            raise RuntimeError("sample cluster is not file aligned")
        length = min(BTRFS_EXTENT_CHUNK, record.size - offset)
        if length <= 0:
            raise RuntimeError("sample cluster starts beyond end of file")
        if (
            slices
            and slices[-1].file == record
            and slices[-1].offset + slices[-1].length == offset
            and slices[-1].length + length <= window_size
        ):
            previous = slices[-1]
            slices[-1] = SampleSlice(
                record,
                previous.offset,
                previous.length + length,
            )
        else:
            slices.append(SampleSlice(record, offset, length))
    return slices


def _systematic_cluster_sample(
    scan: ScanResult,
    effective_limit: int,
    window_size: int,
) -> list[SampleSlice]:
    """Select equal-probability file-aligned 128 KiB source clusters.

    Midpoints are spread over the complete logical byte population. Mapping
    every point back to its containing file-aligned cluster gives each source
    byte the same deterministic inclusion rule while preserving the actual
    Btrfs compressor reset boundaries.
    """

    records = sorted(
        (record for record in scan.files if record.size > 0),
        key=lambda item: (
            item.directory_group,
            item.extension,
            item.relative_path,
        ),
    )
    if not records or effective_limit <= 0:
        return []

    selected: dict[tuple[str, ...], list[int]] = defaultdict(list)
    if effective_limit >= scan.logical_bytes:
        for record in records:
            selected[record.components].extend(
                range(0, record.size, BTRFS_EXTENT_CHUNK)
            )
    else:
        unit_counts = {
            record.components: math.ceil(
                record.size / BTRFS_EXTENT_CHUNK
            )
            for record in records
        }
        total_units = sum(unit_counts.values())
        estimated_units = round(
            effective_limit * total_units / scan.logical_bytes
        )
        if estimated_units <= 0:
            raise ValueError(
                "sample_limit must cover at least one 128 KiB Btrfs cluster"
            )

        def select_units(
            count: int,
        ) -> tuple[dict[tuple[str, ...], list[int]], int]:
            points = [
                ((2 * index + 1) * total_units) // (2 * count)
                for index in range(count)
            ]
            chosen: dict[tuple[str, ...], list[int]] = defaultdict(list)
            point_index = 0
            population_unit = 0
            selected_bytes = 0
            for record in records:
                record_end = (
                    population_unit + unit_counts[record.components]
                )
                while (
                    point_index < len(points)
                    and points[point_index] < record_end
                ):
                    cluster_index = (
                        points[point_index] - population_unit
                    )
                    cluster_offset = (
                        cluster_index * BTRFS_EXTENT_CHUNK
                    )
                    chosen[record.components].append(cluster_offset)
                    selected_bytes += min(
                        BTRFS_EXTENT_CHUNK,
                        record.size - cluster_offset,
                    )
                    point_index += 1
                population_unit = record_end
            if point_index != len(points):
                raise RuntimeError(
                    "systematic sampling did not cover all cluster units"
                )
            return chosen, selected_bytes

        best: tuple[int, int, dict[tuple[str, ...], list[int]]] | None = None
        radius = min(128, total_units - 1)
        lower = max(1, estimated_units - radius)
        upper = min(total_units - 1, estimated_units + radius)
        for unit_count in range(lower, upper + 1):
            candidate, candidate_bytes = select_units(unit_count)
            if candidate_bytes > effective_limit:
                continue
            score = (candidate_bytes, -abs(unit_count - estimated_units))
            if best is None or score > (best[0], best[1]):
                best = (
                    candidate_bytes,
                    -abs(unit_count - estimated_units),
                    candidate,
                )
        if best is None:
            unit_count = lower - 1
            while unit_count > 0:
                candidate, candidate_bytes = select_units(unit_count)
                if candidate_bytes <= effective_limit:
                    best = (candidate_bytes, 0, candidate)
                    break
                unit_count -= 1
        if best is None:
            raise RuntimeError("no cluster sample fits the declared byte cap")
        selected = best[2]
        if any(len(values) != len(set(values)) for values in selected.values()):
            raise RuntimeError("systematic sampling selected a cluster twice")

    record_by_components = {
        record.components: record for record in records
    }
    slices: list[SampleSlice] = []
    for components in sorted(selected):
        slices.extend(
            _merge_cluster_offsets(
                record_by_components[components],
                selected[components],
                window_size,
            )
        )
    if sum(item.length for item in slices) > effective_limit:
        raise RuntimeError("cluster sampling exceeded the declared byte cap")
    return slices


def _file_distribution_diagnostics(
    group: FileGroup,
    allocations: dict[tuple[str, ...], int],
) -> tuple[float, int, int]:
    """Return total-variation distance, sampled files and covered bytes."""

    nonempty = [record for record in group.files if record.size > 0]
    population = sum(record.size for record in nonempty)
    sampled = sum(allocations.get(record.components, 0) for record in nonempty)
    if population <= 0 or sampled <= 0:
        return 1.0 if population > 0 else 0.0, 0, 0
    variation = 0.5 * sum(
        abs(
            record.size / population
            - allocations.get(record.components, 0) / sampled
        )
        for record in nonempty
    )
    sampled_records = [
        record
        for record in nonempty
        if allocations.get(record.components, 0) > 0
    ]
    return (
        variation,
        len(sampled_records),
        sum(record.size for record in sampled_records),
    )


def _size_strata_distribution_tv(
    group: FileGroup,
    allocations: dict[tuple[str, ...], int],
) -> float:
    """Compare population/sample shares in logarithmic file-size strata."""

    population_by_stratum: dict[int, int] = defaultdict(int)
    sample_by_stratum: dict[int, int] = defaultdict(int)
    for record in group.files:
        if record.size <= 0:
            continue
        stratum = record.size.bit_length()
        population_by_stratum[stratum] += record.size
        sample_by_stratum[stratum] += allocations.get(record.components, 0)
    population = sum(population_by_stratum.values())
    sampled = sum(sample_by_stratum.values())
    if population <= 0 or sampled <= 0:
        return 1.0 if population > 0 else 0.0
    return 0.5 * sum(
        abs(
            population_by_stratum[key] / population
            - sample_by_stratum.get(key, 0) / sampled
        )
        for key in population_by_stratum
    )


def build_sampling_plan(
    scan: ScanResult,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> SamplingPlan:
    if sample_limit < BTRFS_EXTENT_CHUNK:
        raise ValueError("sample_limit must be at least 128 KiB")
    if window_size < BTRFS_EXTENT_CHUNK:
        raise ValueError("window_size must be at least 128 KiB")
    if window_size % BTRFS_EXTENT_CHUNK:
        raise ValueError("window_size must be a multiple of 128 KiB")
    groups = group_files(scan)
    effective_limit = min(sample_limit, scan.logical_bytes)
    slices = _systematic_cluster_sample(
        scan,
        effective_limit,
        window_size,
    )
    sampled_by_group: dict[tuple[str, str], int] = defaultdict(int)
    sampled_by_file: dict[tuple[str, ...], int] = defaultdict(int)
    for item in slices:
        sampled_by_group[item.file.group_key] += item.length
        sampled_by_file[item.file.components] += item.length

    distribution_tv: dict[tuple[str, str], float] = {}
    size_strata_tv: dict[tuple[str, str], float] = {}
    files_sampled: dict[tuple[str, str], int] = {}
    population_bytes_covered: dict[tuple[str, str], int] = {}
    for group in groups:
        file_allocations = {
            record.components: sampled_by_file.get(record.components, 0)
            for record in group.files
        }
        (
            distribution_tv[group.key],
            files_sampled[group.key],
            population_bytes_covered[group.key],
        ) = _file_distribution_diagnostics(group, file_allocations)
        size_strata_tv[group.key] = _size_strata_distribution_tv(
            group,
            file_allocations,
        )
    return SamplingPlan(
        slices=slices,
        group_logical_bytes={
            item.key: item.logical_bytes for item in groups
        },
        group_sampled_bytes=dict(sampled_by_group),
        file_sampled_bytes=dict(sampled_by_file),
        group_file_distribution_tv=distribution_tv,
        group_size_strata_distribution_tv=size_strata_tv,
        group_files_sampled=files_sampled,
        group_file_population_bytes_covered=population_bytes_covered,
        sample_limit=sample_limit,
        window_size=window_size,
    )


def _open_root(path: Path) -> int:
    return os.open(path, _readonly_flags(directory=True))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _safe_temp_directory(game_root: Path) -> Path:
    candidates = (Path("/tmp"), Path(tempfile.gettempdir()))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and not _is_within(resolved, game_root):
            return resolved
    raise OSError("no temporary directory outside the game tree is available")


def _open_record(root_fd: int, root_device: int, record: FileRecord) -> int:
    current_fd = os.dup(root_fd)
    try:
        for component in record.components[:-1]:
            flags = _readonly_flags(directory=True)
            child_fd = os.open(component, flags, dir_fd=current_fd)
            child_stat = os.fstat(child_fd)
            os.close(current_fd)
            current_fd = child_fd
            if child_stat.st_dev != root_device or not stat.S_ISDIR(
                child_stat.st_mode
            ):
                raise OSError("sample path crossed the game filesystem")
        flags = _readonly_flags()
        file_fd = os.open(record.components[-1], flags, dir_fd=current_fd)
        file_stat = os.fstat(file_fd)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_dev != root_device
            or file_stat.st_dev != record.device
            or file_stat.st_ino != record.inode
            or file_stat.st_size != record.size
            or file_stat.st_mtime_ns != record.mtime_ns
        ):
            os.close(file_fd)
            raise OSError(f"sample file changed after scan: {record.relative_path}")
        return file_fd
    finally:
        os.close(current_fd)


def materialize_sample(
    scan: ScanResult,
    plan: SamplingPlan,
    destination: Any,
    *,
    cancel: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[
    int,
    dict[tuple[str, str], tuple[int, int]],
    list[MaterializedSegment],
]:
    """Copy selected byte windows into a temporary seekable file."""

    token = cancel or CancellationToken()
    root_fd = _open_root(scan.root)
    root_stat = os.fstat(root_fd)
    if root_stat.st_dev != scan.root_device:
        os.close(root_fd)
        raise OSError("game root filesystem changed after scan")
    written = 0
    group_ranges: dict[tuple[str, str], tuple[int, int]] = {}
    segments: list[MaterializedSegment] = []
    try:
        for item in sorted(
            plan.slices,
            key=lambda value: (
                value.file.directory_group,
                value.file.extension,
                value.file.relative_path,
                value.offset,
            ),
        ):
            token.check()
            key = item.file.group_key
            segment_start = written
            group_start, group_length = group_ranges.get(key, (written, 0))
            file_fd = _open_record(root_fd, scan.root_device, item.file)
            try:
                offset = item.offset
                remaining = item.length
                while remaining:
                    token.check()
                    chunk = os.pread(
                        file_fd,
                        min(READ_CHUNK, remaining),
                        offset,
                    )
                    if not chunk:
                        raise OSError(
                            f"short read from {item.file.relative_path}"
                        )
                    destination.write(chunk)
                    offset += len(chunk)
                    remaining -= len(chunk)
                    written += len(chunk)
                    group_length += len(chunk)
                    if progress:
                        progress(
                            "sampling",
                            written / max(1, plan.sampled_bytes),
                            f"Sampled {written} of {plan.sampled_bytes} bytes",
                        )
            finally:
                os.close(file_fd)
            segments.append(
                MaterializedSegment(segment_start, item.length, key)
            )
            group_ranges[key] = (group_start, group_length)
        destination.flush()
        destination.seek(0)
        return written, group_ranges, segments
    finally:
        os.close(root_fd)


def _copy_stratified_reference(
    source: Any,
    segments: Sequence[MaterializedSegment],
    byte_limit: int,
    destination: Any,
    *,
    cancel: CancellationToken,
) -> int:
    """Create a bounded reference stream without taking an ordered prefix."""

    total = sum(item.length for item in segments)
    target = min(total, byte_limit)
    if target <= 0:
        return 0
    raw_quotas = [target * item.length / total for item in segments]
    quotas = [min(item.length, int(value)) for item, value in zip(segments, raw_quotas)]
    residue = target - sum(quotas)
    by_remainder = sorted(
        range(len(segments)),
        key=lambda index: raw_quotas[index] - int(raw_quotas[index]),
        reverse=True,
    )
    for index in by_remainder:
        if residue <= 0:
            break
        room = segments[index].length - quotas[index]
        added = 1 if room > 0 else 0
        quotas[index] += added
        residue -= added

    written = 0
    source_fd = source.fileno()
    for segment, quota in zip(segments, quotas):
        cancel.check()
        if quota <= 0:
            continue
        # Spread each quota over the segment instead of copying just its start.
        piece_count = min(8, max(1, math.ceil(quota / READ_CHUNK)))
        base_piece = quota // piece_count
        piece_residue = quota % piece_count
        consumed = 0
        for piece_index in range(piece_count):
            length = base_piece + (1 if piece_index < piece_residue else 0)
            if length <= 0:
                continue
            available_start = segment.length - length
            relative_offset = (
                0
                if piece_count == 1
                else round(piece_index * available_start / (piece_count - 1))
            )
            chunk = os.pread(
                source_fd,
                length,
                segment.offset + relative_offset,
            )
            if len(chunk) != length:
                raise OSError("short read while preparing reference sample")
            destination.write(chunk)
            written += len(chunk)
            consumed += len(chunk)
        if consumed != quota:
            raise OSError("reference sample quota was not fully copied")
    destination.flush()
    destination.seek(0)
    return written


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _check_deadline(deadline_monotonic: float, label: str) -> None:
    if time.monotonic() >= deadline_monotonic:
        raise TimeoutError(f"{label} exceeded its deadline")


def _compress_external(
    args: Sequence[str],
    *,
    cancel: CancellationToken,
    popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    temp_directory: Path | str | None = None,
    timeout_seconds: float = DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    deadline_monotonic: float | None = None,
) -> tuple[int, str]:
    """Run a compressor with stdout in a temporary file, never in RAM."""

    if timeout_seconds <= 0 or timeout_seconds > MAX_EXTERNAL_TIMEOUT_SECONDS:
        raise ValueError("external compressor timeout is outside safe bounds")
    with tempfile.TemporaryFile(dir=temp_directory) as output, tempfile.TemporaryFile(
        dir=temp_directory
    ) as errors:
        process = popen_factory(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            shell=False,
        )
        deadline = time.monotonic() + timeout_seconds
        if deadline_monotonic is not None:
            deadline = min(deadline, deadline_monotonic)
        try:
            while process.poll() is None:
                if cancel.cancelled:
                    _terminate_process(process)
                    raise BenchmarkCancelled("benchmark cancelled")
                if time.monotonic() >= deadline:
                    _terminate_process(process)
                    raise TimeoutError(
                        f"compressor exceeded {timeout_seconds:.0f} seconds"
                    )
                time.sleep(0.05)
            return_code = process.wait()
            errors.seek(0)
            error_text = errors.read(16 * 1024).decode(
                "utf-8", errors="replace"
            )
            if return_code != 0:
                raise RuntimeError(
                    f"compressor exited with {return_code}: {error_text.strip()}"
                )
            output.flush()
            return os.fstat(output.fileno()).st_size, error_text.strip()
        except BaseException:
            _terminate_process(process)
            raise


def _reference_command_args(
    executable: str,
    family: str,
    level: int,
    source_path: Path | str,
) -> list[str]:
    args = [executable, "-T1", "-q", "-c", f"-{level}"]
    if family == "xz":
        args.extend(
            [
                f"--memlimit-compress={REFERENCE_MEMORY_LIMIT_MIB}MiB",
                "--no-adjust",
            ]
        )
    elif family == "zstd":
        args.append(f"-M{REFERENCE_MEMORY_LIMIT_MIB}")
    else:
        raise ValueError(f"unsupported reference compressor: {family}")
    args.append(str(source_path))
    return args


class _ZstdLibrary:
    """Small binding to the stable public single-shot libzstd API."""

    def __init__(self, library_path: str | None = None) -> None:
        resolved = library_path or ctypes.util.find_library("zstd")
        if not resolved:
            raise FileNotFoundError("libzstd is not installed")
        self._library = ctypes.CDLL(resolved)
        self._library.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
        self._library.ZSTD_compressBound.restype = ctypes.c_size_t
        self._library.ZSTD_compress.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self._library.ZSTD_compress.restype = ctypes.c_size_t
        self._library.ZSTD_isError.argtypes = [ctypes.c_size_t]
        self._library.ZSTD_isError.restype = ctypes.c_uint
        self._library.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
        self._library.ZSTD_getErrorName.restype = ctypes.c_char_p

    def compress(self, data: bytes, level: int) -> int:
        source_size = len(data)
        bound = self._library.ZSTD_compressBound(source_size)
        destination = ctypes.create_string_buffer(bound)
        source = ctypes.create_string_buffer(data, source_size)
        result = self._library.ZSTD_compress(
            destination,
            bound,
            source,
            source_size,
            level,
        )
        if self._library.ZSTD_isError(result):
            name = self._library.ZSTD_getErrorName(result)
            message = name.decode("utf-8", errors="replace") if name else "unknown"
            raise RuntimeError(f"libzstd compression failed: {message}")
        return int(result)


def _iter_extent_chunks(
    source: Any,
    segments: Sequence[MaterializedSegment],
    *,
    byte_limit: int,
    cancel: CancellationToken,
) -> Iterator[bytes]:
    remaining = byte_limit
    for segment in segments:
        if remaining <= 0:
            break
        cancel.check()
        source.seek(segment.offset)
        segment_remaining = min(segment.length, remaining)
        while segment_remaining:
            cancel.check()
            chunk = source.read(min(BTRFS_EXTENT_CHUNK, segment_remaining))
            if not chunk:
                raise OSError("short read from temporary benchmark sample")
            segment_remaining -= len(chunk)
            remaining -= len(chunk)
            yield chunk


def _compress_btrfs_blocks(
    source: Any,
    family: str,
    level: int,
    *,
    segments: Sequence[MaterializedSegment],
    byte_limit: int,
    cancel: CancellationToken,
    sectorsize: int,
    deadline_monotonic: float | None = None,
    zstd_library: _ZstdLibrary | None = None,
) -> int:
    if sectorsize <= 0:
        raise ValueError("sectorsize must be positive")
    compressed_bytes = 0
    zstd = zstd_library
    if family == "zstd" and zstd is None:
        zstd = _ZstdLibrary()
    for chunk in _iter_extent_chunks(
        source,
        segments,
        byte_limit=byte_limit,
        cancel=cancel,
    ):
        if deadline_monotonic is not None:
            _check_deadline(deadline_monotonic, "benchmark")
        if family == "zlib":
            encoded_size = len(zlib.compress(chunk, level))
        elif family == "zstd" and zstd is not None:
            encoded_size = zstd.compress(chunk, level)
        else:
            raise ValueError(f"unsupported Btrfs compressor: {family}")
        # Btrfs allocates data in sectors and leaves an extent uncompressed
        # when the rounded encoded representation would not be smaller.
        encoded_allocated = (
            (encoded_size + sectorsize - 1) // sectorsize
        ) * sectorsize
        plain_allocated = (
            (len(chunk) + sectorsize - 1) // sectorsize
        ) * sectorsize
        compressed_bytes += min(plain_allocated, encoded_allocated)
    return compressed_bytes


def _heuristic_sensitivity_margin(
    group_ratios: dict[tuple[str, str], float],
    group_logical_bytes: dict[tuple[str, str], int],
    sampled_bytes: int,
    total_bytes: int,
) -> float:
    if not group_ratios or sampled_bytes <= 0 or total_bytes <= 0:
        return 0.25
    represented = sum(
        group_logical_bytes.get(key, 0) for key in group_ratios
    )
    if represented <= 0:
        return 0.25
    coverage = sampled_bytes / max(1, total_bytes)
    represented_share = represented / max(1, total_bytes)
    if coverage >= 0.999999 and represented_share >= 0.999999:
        return 0.0
    # This is deliberately a heuristic sensitivity band, not a confidence
    # interval. Group heterogeneity is not sampling error because groups are
    # projected with their known logical weights. The penalty instead grows
    # when the bounded sample is small or covers little of the population.
    sample_scale = min(1.0, sampled_bytes / DEFAULT_SAMPLE_LIMIT)
    coverage_scale = min(1.0, coverage * 20.0)
    unsampled_group_penalty = max(0.0, 1.0 - represented_share)
    return min(
        0.25,
        0.015
        + 0.02 * (1.0 - sample_scale)
        + 0.02 * (1.0 - coverage_scale)
        + 0.5 * unsampled_group_penalty,
    )


def _weighted_game_compressed_bytes(
    group_ratios: dict[tuple[str, str], float],
    group_logical_bytes: dict[tuple[str, str], int],
) -> int:
    """Project per-group ratios without over-weighting small sampled groups.

    Groups that could not be sampled are conservatively treated as
    incompressible.  This is intentionally different from applying the raw
    ratio of the materialized sample to the whole game: the sampling plan gives
    small but meaningful groups a minimum budget, so the raw sample is not a
    population-proportional stream.
    """

    estimated = 0
    for key, logical_bytes in group_logical_bytes.items():
        ratio = min(1.0, max(0.0, group_ratios.get(key, 1.0)))
        estimated += round(logical_bytes * ratio)
    return estimated


def _allocate_group_budget(
    group_ranges: dict[tuple[str, str], tuple[int, int]],
    group_logical_bytes: dict[tuple[str, str], int],
    byte_limit: int,
) -> dict[tuple[str, str], int]:
    """Allocate a bounded reference budget by each group's logical weight."""

    capacities = {
        key: max(0, length)
        for key, (_offset, length) in group_ranges.items()
        if length > 0
    }
    target = min(max(0, byte_limit), sum(capacities.values()))
    allocations = {key: 0 for key in capacities}
    remaining = target
    active = set(capacities)
    while remaining > 0 and active:
        total_weight = sum(
            max(1, group_logical_bytes.get(key, 0)) for key in active
        )
        progressed = 0
        for key in sorted(active):
            capacity_left = capacities[key] - allocations[key]
            if capacity_left <= 0:
                continue
            share = max(
                1,
                remaining
                * max(1, group_logical_bytes.get(key, 0))
                // max(1, total_weight),
            )
            added = min(capacity_left, share, remaining - progressed)
            if added <= 0:
                continue
            allocations[key] += added
            progressed += added
            if progressed >= remaining:
                break
        remaining -= progressed
        active = {
            key for key in active if allocations[key] < capacities[key]
        }
        if progressed <= 0:
            break
    return {key: value for key, value in allocations.items() if value > 0}


def _algorithm_result(
    *,
    algorithm_id: str,
    family: str,
    level: int,
    role: str,
    btrfs_compatible: bool,
    source_bytes: int,
    compressed_bytes: int,
    total_bytes: int,
    uncertainty_margin: float,
    estimated_game_compressed_bytes: int | None = None,
    group_ratios: dict[tuple[str, str], float] | None = None,
    available: bool = True,
    error: str | None = None,
) -> dict[str, Any]:
    if source_bytes <= 0:
        available = False
        error = error or "sample is empty"
    if not available:
        return {
            "id": algorithm_id,
            "family": family,
            "level": level,
            "role": role,
            "btrfs_compatible": btrfs_compatible,
            "available": available,
            "error": error,
            "sample_bytes": source_bytes,
            "measurement_method": (
                "independent-128-kib-sectorsize-rounded-simulation"
                if btrfs_compatible
                else "group-weighted-continuous-external-streams"
            ),
        }
    sample_ratio = compressed_bytes / source_bytes
    projected_bytes = (
        int(estimated_game_compressed_bytes)
        if estimated_game_compressed_bytes is not None
        else int(total_bytes * sample_ratio)
    )
    ratio = projected_bytes / total_bytes if total_bytes > 0 else sample_ratio
    savings_ratio = max(0.0, 1.0 - ratio)
    low_savings_ratio = max(0.0, savings_ratio - uncertainty_margin)
    high_savings_ratio = min(1.0, savings_ratio + uncertainty_margin)
    return {
        "id": algorithm_id,
        "family": family,
        "level": level,
        "role": role,
        "btrfs_compatible": btrfs_compatible,
        "available": True,
        "measurement_method": (
            "independent-128-kib-sectorsize-rounded-simulation"
            if btrfs_compatible
            else "group-weighted-continuous-external-streams"
        ),
        "sample_bytes": source_bytes,
        "compressed_bytes": compressed_bytes,
        "sample_compression_ratio": sample_ratio,
        "compression_ratio": ratio,
        "sample_savings_ratio": max(0.0, 1.0 - sample_ratio),
        "estimated_total_payload_reduction_from_uncompressed_baseline_ratio": (
            savings_ratio
        ),
        "estimated_game_compressed_bytes": projected_bytes,
        "estimated_total_payload_reduction_from_uncompressed_baseline_bytes": (
            int(total_bytes * savings_ratio)
        ),
        "heuristic_payload_reduction_low_bytes": int(
            total_bytes * low_savings_ratio
        ),
        "heuristic_payload_reduction_high_bytes": int(
            total_bytes * high_savings_ratio
        ),
        "heuristic_sensitivity_ratio": uncertainty_margin,
        "estimated_incremental_disk_savings_bytes": None,
        "incremental_measurement_state": (
            "unavailable_without-before-after-compsize-shared-measurement"
        ),
        "group_weighted": estimated_game_compressed_bytes is not None,
        "sampled_group_count": len(group_ratios or {}),
        "error": error,
    }


def _is_reliable(
    scan: ScanResult,
    plan: SamplingPlan,
    group_ranges: dict[tuple[str, str], tuple[int, int]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    # The 512-MiB default is the minimum recommendation-grade target.  A
    # smaller pilot can still be useful, but the Jedi validation demonstrated
    # that 128 MiB may miss strong heterogeneity inside a very large asset
    # group and must not unlock a recommendation.
    minimum = min(scan.logical_bytes, DEFAULT_SAMPLE_LIMIT)
    # A few bytes can be lost when aligned windows collapse to the same
    # 128-KiB offset.  Treat only a material shortfall as insufficient.
    sampling_tolerance = 4 * BTRFS_EXTENT_CHUNK
    if plan.sampled_bytes + sampling_tolerance < minimum:
        reasons.append("less than the minimum representative byte sample")
    if len(group_ranges) < min(3, len(plan.group_logical_bytes)):
        reasons.append("too few extension/directory groups were sampled")
    minimum_slices = (
        1
        if scan.logical_bytes <= plan.window_size
        else min(8, math.ceil(scan.logical_bytes / plan.window_size))
    )
    if len(plan.slices) < minimum_slices:
        reasons.append("too few independent file windows were sampled")
    if scan.permission_errors:
        reasons.append("some directories could not be read")
    if scan.hardlinked_files:
        reasons.append(
            "hard-linked inodes require a filesystem-level incremental "
            "savings measurement"
        )
    for key, logical_bytes in plan.group_logical_bytes.items():
        if (
            scan.logical_bytes
            and logical_bytes / scan.logical_bytes >= SIGNIFICANT_GROUP_SHARE
            and plan.group_sampled_bytes.get(key, 0) > 0
            and plan.group_size_strata_distribution_tv.get(key, 1.0) > 0.08
        ):
            reasons.append(
                "a material group has a non-representative file-size-strata "
                "sample"
            )
            break
    unsampled = set(plan.group_logical_bytes) - set(group_ranges)
    unsampled_share = sum(plan.group_logical_bytes[key] for key in unsampled)
    if scan.logical_bytes and unsampled_share / scan.logical_bytes > 0.05:
        reasons.append("more than 5% of logical data belongs to unsampled groups")
    return not reasons, reasons


def benchmark_game(
    game_path: Path | str,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    reference_limit: int = DEFAULT_REFERENCE_LIMIT,
    window_size: int = DEFAULT_WINDOW_SIZE,
    cancel: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
    capabilities: CapabilityReport | None = None,
    popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    external_timeout_seconds: float = DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    benchmark_timeout_seconds: float = DEFAULT_BENCHMARK_TIMEOUT_SECONDS,
    steam_app_id: str | None = None,
    steam_manifest: Path | str | None = None,
    game_name: str | None = None,
) -> dict[str, Any]:
    if (
        sample_limit < BTRFS_EXTENT_CHUNK
        or sample_limit > MAX_SAMPLE_LIMIT
    ):
        raise ValueError("sample_limit is outside safe bounds")
    if reference_limit <= 0 or reference_limit > MAX_REFERENCE_LIMIT:
        raise ValueError("reference_limit is outside safe bounds")
    if (
        window_size < BTRFS_EXTENT_CHUNK
        or window_size > MAX_WINDOW_SIZE
        or window_size % BTRFS_EXTENT_CHUNK
    ):
        raise ValueError("window_size is outside safe bounds")
    if (
        external_timeout_seconds <= 0
        or external_timeout_seconds > MAX_EXTERNAL_TIMEOUT_SECONDS
    ):
        raise ValueError("external compressor timeout is outside safe bounds")
    if (
        benchmark_timeout_seconds <= 0
        or benchmark_timeout_seconds > MAX_BENCHMARK_TIMEOUT_SECONDS
    ):
        raise ValueError("benchmark timeout is outside safe bounds")
    if bool(steam_app_id) != bool(steam_manifest):
        raise ValueError(
            "steam_app_id and steam_manifest must be supplied together"
        )
    token = cancel or CancellationToken()
    started = time.monotonic()
    benchmark_deadline = started + benchmark_timeout_seconds
    detected = capabilities or detect_capabilities()
    manifest_before = (
        read_steam_manifest(
            steam_manifest,
            expected_app_id=str(steam_app_id),
            expected_game_path=Path(game_path),
        )
        if steam_app_id is not None and steam_manifest is not None
        else None
    )
    scan = scan_game(game_path, cancel=token, progress=progress)
    _check_deadline(benchmark_deadline, "benchmark")
    token.check()
    if scan.sparse_files:
        raise RuntimeError(
            "sparse files were detected; refusing to project holes as "
            "compressible payload"
        )
    if scan.sparse_unknown_files:
        raise RuntimeError(
            "sparse-file state could not be determined; refusing to assume "
            "that unallocated ranges are compressible data"
        )
    inventory_fingerprint = _inventory_fingerprint(scan)
    plan = build_sampling_plan(
        scan, sample_limit=sample_limit, window_size=window_size
    )
    groups = group_files(scan)
    largest_file = max(scan.files, key=lambda item: item.size, default=None)
    safe_temp_directory = _safe_temp_directory(scan.root)
    filesystem_sectorsize = max(1, os.statvfs(scan.root).f_frsize)
    effective_sample_bytes = min(sample_limit, scan.logical_bytes)
    effective_reference_bytes = min(
        reference_limit,
        effective_sample_bytes,
    )
    required_temp_bytes = (
        effective_sample_bytes
        + 2 * effective_reference_bytes
        + TEMP_SPACE_RESERVE
    )
    available_temp_bytes = shutil.disk_usage(safe_temp_directory).free
    if available_temp_bytes < required_temp_bytes:
        raise OSError(
            "insufficient temporary disk space: "
            f"need {required_temp_bytes} bytes, "
            f"have {available_temp_bytes} bytes"
        )

    with tempfile.NamedTemporaryFile(
        prefix="game-optimization-compression-sample-",
        suffix=".bin",
        dir=safe_temp_directory,
    ) as sample:
        sampled_bytes, group_ranges, materialized_segments = materialize_sample(
            scan,
            plan,
            sample,
            cancel=token,
            progress=progress,
        )
        token.check()
        segments_by_group: dict[
            tuple[str, str], list[MaterializedSegment]
        ] = defaultdict(list)
        for segment in materialized_segments:
            segments_by_group[segment.group_key].append(segment)
        group_ratios: dict[tuple[str, str], float] = {}
        for key, (_offset, length) in group_ranges.items():
            if length:
                compressed = _compress_btrfs_blocks(
                    sample,
                    "zlib",
                    6,
                    segments=segments_by_group[key],
                    byte_limit=length,
                    cancel=token,
                    sectorsize=filesystem_sectorsize,
                    deadline_monotonic=benchmark_deadline,
                )
                group_ratios[key] = compressed / length
        sensitivity = _heuristic_sensitivity_margin(
            group_ratios,
            plan.group_logical_bytes,
            sampled_bytes,
            scan.logical_bytes,
        )

        jobs: list[tuple[str, str, int, str, bool]] = []
        if detected.btrfs_zstd:
            jobs.extend(
                (f"btrfs-zstd-{level}", "zstd", level, "btrfs", True)
                for level in detected.btrfs_zstd_levels
            )
        if detected.btrfs_zlib:
            jobs.extend(
                (f"btrfs-zlib-{level}", "zlib", level, "btrfs", True)
                for level in detected.btrfs_zlib_levels
            )
        jobs.extend(
            (
                ("reference-zstd-19", "zstd", 19, "reference", False),
                ("reference-xz-9", "xz", 9, "reference", False),
            )
        )
        results: list[dict[str, Any]] = []
        zstd_library: _ZstdLibrary | None = None
        if detected.btrfs_zstd:
            try:
                zstd_library = _ZstdLibrary()
            except (OSError, FileNotFoundError):
                zstd_library = None
        reference_quotas = _allocate_group_budget(
            group_ranges,
            plan.group_logical_bytes,
            min(sampled_bytes, reference_limit),
        )
        for index, (algorithm_id, family, level, role, compatible) in enumerate(
            jobs
        ):
            token.check()
            _check_deadline(benchmark_deadline, "benchmark")
            algorithm_deadline = (
                min(
                    benchmark_deadline,
                    time.monotonic() + external_timeout_seconds,
                )
                if role == "reference"
                else benchmark_deadline
            )
            if progress:
                progress(
                    "compression",
                    index / max(1, len(jobs)),
                    f"Testing {algorithm_id}",
                )
            source_bytes = sampled_bytes if role == "btrfs" else sum(
                reference_quotas.values()
            )
            try:
                algorithm_group_ratios: dict[tuple[str, str], float] = {}
                compressed_bytes = 0
                if role == "btrfs":
                    if family == "zstd" and zstd_library is None:
                        raise FileNotFoundError("libzstd is not installed")
                    for key, (_offset, length) in group_ranges.items():
                        token.check()
                        if length <= 0:
                            continue
                        group_compressed = _compress_btrfs_blocks(
                            sample,
                            family,
                            level,
                            segments=segments_by_group[key],
                            byte_limit=length,
                            cancel=token,
                            sectorsize=filesystem_sectorsize,
                            deadline_monotonic=benchmark_deadline,
                            zstd_library=zstd_library,
                        )
                        compressed_bytes += group_compressed
                        algorithm_group_ratios[key] = (
                            group_compressed / length
                        )
                else:
                    executable = (
                        detected.zstd_path
                        if family == "zstd"
                        else detected.xz_path
                    )
                    if not executable:
                        raise FileNotFoundError(
                            f"{family} executable is not installed"
                        )
                    for key, quota in reference_quotas.items():
                        token.check()
                        reference_copy = tempfile.NamedTemporaryFile(
                            prefix="game-optimization-reference-sample-",
                            suffix=".bin",
                            dir=safe_temp_directory,
                        )
                        try:
                            copied = _copy_stratified_reference(
                                sample,
                                segments_by_group[key],
                                quota,
                                reference_copy,
                                cancel=token,
                            )
                            if copied != quota:
                                raise OSError(
                                    "reference group did not reach its byte limit"
                                )
                            reference_args = _reference_command_args(
                                executable,
                                family,
                                level,
                                reference_copy.name,
                            )
                            group_compressed, _ = _compress_external(
                                reference_args,
                                cancel=token,
                                popen_factory=popen_factory,
                                temp_directory=safe_temp_directory,
                                timeout_seconds=external_timeout_seconds,
                                deadline_monotonic=algorithm_deadline,
                            )
                            # A reference compressor is never credited with
                            # negative savings for a group it would enlarge.
                            group_compressed = min(copied, group_compressed)
                            compressed_bytes += group_compressed
                            algorithm_group_ratios[key] = (
                                group_compressed / copied
                            )
                        finally:
                            reference_copy.close()
                weighted_compressed = _weighted_game_compressed_bytes(
                    algorithm_group_ratios,
                    plan.group_logical_bytes,
                )
                algorithm_sensitivity = _heuristic_sensitivity_margin(
                    algorithm_group_ratios,
                    plan.group_logical_bytes,
                    source_bytes,
                    scan.logical_bytes,
                )
                results.append(
                    _algorithm_result(
                        algorithm_id=algorithm_id,
                        family=family,
                        level=level,
                        role=role,
                        btrfs_compatible=compatible,
                        source_bytes=source_bytes,
                        compressed_bytes=compressed_bytes,
                        total_bytes=scan.logical_bytes,
                        uncertainty_margin=algorithm_sensitivity,
                        estimated_game_compressed_bytes=weighted_compressed,
                        group_ratios=algorithm_group_ratios,
                    )
                )
            except FileNotFoundError as exc:
                results.append(
                    _algorithm_result(
                        algorithm_id=algorithm_id,
                        family=family,
                        level=level,
                        role=role,
                        btrfs_compatible=compatible,
                        source_bytes=source_bytes,
                        compressed_bytes=0,
                        total_bytes=scan.logical_bytes,
                        uncertainty_margin=sensitivity,
                        available=False,
                        error=str(exc),
                    )
                )
            except BenchmarkCancelled:
                raise
            except (OSError, RuntimeError) as exc:
                results.append(
                    _algorithm_result(
                        algorithm_id=algorithm_id,
                        family=family,
                        level=level,
                        role=role,
                        btrfs_compatible=compatible,
                        source_bytes=source_bytes,
                        compressed_bytes=0,
                        total_bytes=scan.logical_bytes,
                        uncertainty_margin=sensitivity,
                        available=False,
                        error=str(exc),
                    )
                )
        if progress:
            progress("compression", 1.0, "Compression comparisons complete")

    if progress:
        progress("verification", 0.0, "Rechecking the game inventory")
    post_scan = scan_game(game_path, cancel=token, progress=None)
    _check_deadline(benchmark_deadline, "benchmark")
    post_inventory_fingerprint = _inventory_fingerprint(post_scan)
    manifest_after = (
        read_steam_manifest(
            manifest_before.path,
            expected_app_id=manifest_before.app_id,
            expected_game_path=scan.root,
        )
        if manifest_before is not None
        else None
    )
    manifest_stable = (
        manifest_before is None
        or (
            manifest_after is not None
            and manifest_before.sha256 == manifest_after.sha256
            and manifest_before.build_id == manifest_after.build_id
            and manifest_after.state_flags == 4
        )
    )
    inventory_stable = (
        inventory_fingerprint == post_inventory_fingerprint
        and scan.namespace_file_count == post_scan.namespace_file_count
        and scan.namespace_logical_bytes == post_scan.namespace_logical_bytes
        and manifest_stable
    )
    if progress:
        progress(
            "verification",
            1.0,
            "Game inventory unchanged"
            if inventory_stable
            else "Game inventory changed during the benchmark",
        )

    group_details: list[dict[str, Any]] = []
    for group in groups:
        sampled = plan.group_sampled_bytes.get(group.key, 0)
        ratio = group_ratios.get(group.key)
        group_details.append(
            {
                "directory": group.directory,
                "extension": group.extension,
                "file_count": len(group.files),
                "logical_bytes": group.logical_bytes,
                "game_share": (
                    group.logical_bytes / scan.logical_bytes
                    if scan.logical_bytes
                    else 0.0
                ),
                "sampled_bytes": sampled,
                "files_sampled": plan.group_files_sampled.get(group.key, 0),
                "file_coverage_percent": (
                    100.0
                    * plan.group_files_sampled.get(group.key, 0)
                    / len(group.files)
                    if group.files
                    else 0.0
                ),
                "sampled_file_population_bytes": (
                    plan.group_file_population_bytes_covered.get(group.key, 0)
                ),
                "sampled_file_population_coverage_percent": (
                    100.0
                    * plan.group_file_population_bytes_covered.get(
                        group.key,
                        0,
                    )
                    / group.logical_bytes
                    if group.logical_bytes
                    else 0.0
                ),
                "within_group_file_distribution_tv": (
                    plan.group_file_distribution_tv.get(group.key)
                ),
                "within_group_size_strata_distribution_tv": (
                    plan.group_size_strata_distribution_tv.get(group.key)
                ),
                "screening_compression_ratio": ratio,
                "classification": (
                    "well-compressible"
                    if ratio is not None and ratio <= 0.75
                    else "nearly-incompressible"
                    if ratio is not None and ratio >= 0.97
                    else "mixed"
                    if ratio is not None
                    else "not-sampled"
                ),
            }
        )
    reliable, reliability_reasons = _is_reliable(scan, plan, group_ranges)
    if not inventory_stable:
        reliability_reasons.append(
            "the game inventory changed during the benchmark"
        )
        reliable = False
    dominant_share = (
        largest_file.size / scan.logical_bytes
        if largest_file is not None and scan.logical_bytes
        else 0.0
    )
    available_btrfs = [
        item
        for item in results
        if item.get("available") and item.get("btrfs_compatible")
    ]
    best_btrfs = min(
        available_btrfs,
        key=lambda item: item["compression_ratio"],
        default=None,
    )
    if reliable and best_btrfs is not None:
        material_savings_floor = max(
            16 * 1024 * 1024,
            int(scan.logical_bytes * 0.005),
        )
        if (
            best_btrfs.get("heuristic_payload_reduction_low_bytes", 0)
            <= material_savings_floor
        ):
            reliable = False
            reliability_reasons.append(
                "the heuristic sensitivity band does not show a material "
                "payload reduction"
            )
    return {
        "report_type": "game-compression-benchmark",
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": "benchmark_game_compression.py",
            "version": TOOL_VERSION,
            "methodology_version": METHODOLOGY_VERSION,
            "source_sha256": _tool_source_sha256(),
        },
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration_seconds": time.monotonic() - started,
        "cancelled": False,
        "game": {
            "name": (
                game_name
                or (
                    manifest_before.name
                    if manifest_before is not None
                    else scan.root.name
                )
            ),
            "path": str(scan.root),
            "steam_app_id": (
                manifest_before.app_id
                if manifest_before is not None
                else None
            ),
            "steam_build_id": (
                manifest_before.build_id
                if manifest_before is not None
                else None
            ),
            "steam_state_flags": (
                manifest_before.state_flags
                if manifest_before is not None
                else None
            ),
            "steam_size_on_disk": (
                manifest_before.size_on_disk
                if manifest_before is not None
                else None
            ),
            "steam_manifest_path": (
                str(manifest_before.path)
                if manifest_before is not None
                else None
            ),
            "steam_manifest_sha256": (
                manifest_before.sha256
                if manifest_before is not None
                else None
            ),
            "steam_manifest_stable": manifest_stable,
            "logical_bytes": scan.logical_bytes,
            "file_count": len(scan.files),
            "namespace_logical_bytes": (
                scan.namespace_logical_bytes or scan.logical_bytes
            ),
            "namespace_file_count": (
                scan.namespace_file_count or len(scan.files)
            ),
            "directory_count": scan.directory_count,
            "root_device": scan.root_device,
            "inventory_fingerprint": inventory_fingerprint,
            "post_inventory_fingerprint": post_inventory_fingerprint,
            "inventory_stable": inventory_stable,
        },
        "safety": {
            "read_only_source": True,
            "noatime": True,
            "nofollow": True,
            "cross_filesystem": False,
            "symlinks_skipped": scan.symlinks_skipped,
            "cross_filesystem_entries_skipped": scan.cross_filesystem_skipped,
            "special_files_skipped": scan.special_files_skipped,
            "permission_errors": scan.permission_errors,
            "hardlinked_files": scan.hardlinked_files,
            "hardlink_entries_skipped": scan.hardlink_entries_skipped,
            "hardlink_duplicate_bytes": scan.hardlink_duplicate_bytes,
            "sparse_files": scan.sparse_files,
            "sparse_hole_bytes": scan.sparse_hole_bytes,
            "sparse_unknown_files": scan.sparse_unknown_files,
        },
        "capabilities": {
            "kernel_release": detected.kernel_release,
            "btrfs_path": detected.btrfs_path,
            "btrfs_version": detected.btrfs_version,
            "btrfs_zstd": detected.btrfs_zstd,
            "btrfs_zlib": detected.btrfs_zlib,
            "btrfs_level_syntax": detected.btrfs_level_syntax,
            "btrfs_zstd_level_range": (
                list(detected.btrfs_zstd_level_range)
                if detected.btrfs_zstd_level_range is not None
                else None
            ),
            "btrfs_zlib_level_range": (
                list(detected.btrfs_zlib_level_range)
                if detected.btrfs_zlib_level_range is not None
                else None
            ),
            "btrfs_zstd_levels_tested": list(detected.btrfs_zstd_levels),
            "btrfs_zlib_levels_tested": list(detected.btrfs_zlib_levels),
            "zstd_path": detected.zstd_path,
            "zstd_version": detected.zstd_version,
            "xz_path": detected.xz_path,
            "xz_version": detected.xz_version,
            "evidence": list(detected.evidence),
        },
        "methodology": {
            "version": METHODOLOGY_VERSION,
            "btrfs_extent_chunk_bytes": BTRFS_EXTENT_CHUNK,
            "filesystem_sectorsize_bytes": filesystem_sectorsize,
            "filesystem_sectorsize_source": "statvfs.f_frsize",
            "btrfs_algorithms": (
                "Each selected file window is split into independent 128 KiB "
                "chunks. Every chunk gets a fresh zstd/zlib compression "
                "context; encoded and plain sizes are rounded to the detected "
                "filesystem sector size, and chunks that would not save a "
                "sector are counted as uncompressed. A separate ratio is "
                "calculated for every "
                "directory/extension group and weighted by that group's "
                "logical share of the game. File-aligned 128 KiB clusters "
                "(or the real EOF tail) are selected systematically with an "
                "equal inclusion rule over the complete cluster population; "
                "adjacent selected clusters may be merged only for bounded "
                "I/O. Sample and cluster boundaries are preserved. "
                "This estimates payload compression and does not model every "
                "Btrfs allocation, metadata or write-time heuristic."
            ),
            "reference_algorithms": (
                "External zstd-19 and xz-9 use bounded continuous streams per "
                "sampled directory/extension group, one CPU thread and a hard "
                "deadline; group results are weighted "
                "by logical game share. They are reference points, not Btrfs "
                "modes and not equivalents of Windows LZX."
            ),
            "group_screening": (
                "Group classifications use independent 128 KiB zlib-6 chunks "
                "as a uniform screening metric."
            ),
            "sensitivity_band": (
                "The low/high values are a conservative heuristic sensitivity "
                "band derived from weighted group dispersion and sample "
                "coverage. They are not a statistical confidence interval."
            ),
        },
        "sampling": {
            "limit_bytes": sample_limit,
            "reference_limit_bytes": reference_limit,
            "window_bytes": window_size,
            "external_timeout_seconds": external_timeout_seconds,
            "benchmark_timeout_seconds": benchmark_timeout_seconds,
            "reference_threads": 1,
            "reference_memory_limit_mib": REFERENCE_MEMORY_LIMIT_MIB,
            "temporary_space_required_bytes": required_temp_bytes,
            "temporary_space_available_bytes": available_temp_bytes,
            "sampled_bytes": plan.sampled_bytes,
            "coverage_percent": (
                100.0 * plan.sampled_bytes / scan.logical_bytes
                if scan.logical_bytes
                else 0.0
            ),
            "slice_count": len(plan.slices),
            "groups_total": len(plan.group_logical_bytes),
            "groups_sampled": len(group_ranges),
            "groups": group_details,
            "single_huge_file_dominated": dominant_share >= 0.5,
            "largest_file": (
                {
                    "path": largest_file.relative_path,
                    "bytes": largest_file.size,
                    "game_share": dominant_share,
                }
                if largest_file is not None
                else None
            ),
        },
        "algorithms": results,
        "findings": {
            "well_compressible_groups": [
                item
                for item in group_details
                if item["classification"] == "well-compressible"
            ],
            "nearly_incompressible_groups": [
                item
                for item in group_details
                if item["classification"] == "nearly-incompressible"
            ],
            "reliable_for_recommendation": reliable,
            "reliability_reasons": reliability_reasons,
            "best_btrfs_algorithm": (
                best_btrfs["id"] if best_btrfs is not None else None
            ),
        },
    }


def _human_bytes(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def format_text_report(report: dict[str, Any]) -> str:
    game = report["game"]
    sampling = report["sampling"]
    findings = report["findings"]
    lines = [
        "Game Optimization read-only compression benchmark",
        f"Game: {game['name']}",
        f"Path: {game['path']}",
        (
            f"Steam AppID/build: {game['steam_app_id']}/"
            f"{game['steam_build_id']}"
            if game.get("steam_app_id")
            else "Steam AppID/build: not supplied"
        ),
        f"Logical size: {_human_bytes(game['logical_bytes'])}",
        f"Files: {game['file_count']}",
        (
            f"Sample: {_human_bytes(sampling['sampled_bytes'])} "
            f"({sampling['coverage_percent']:.3f}% in "
            f"{sampling['slice_count']} windows)"
        ),
        (
            "Single huge file dominated: "
            f"{'yes' if sampling['single_huge_file_dominated'] else 'no'}"
        ),
        "",
        "Algorithms:",
    ]
    for item in report["algorithms"]:
        if not item.get("available"):
            lines.append(f"  {item['id']}: unavailable ({item.get('error')})")
            continue
        lines.append(
            f"  {item['id']}: estimated whole-game ratio "
            f"{item['compression_ratio'] * 100:.2f}% "
            f"(raw sample {item['sample_compression_ratio'] * 100:.2f}%); "
            "estimated total payload reduction from an uncompressed "
            "baseline "
            f"{_human_bytes(item['estimated_total_payload_reduction_from_uncompressed_baseline_bytes'])} "
            "(heuristic sensitivity band "
            f"{_human_bytes(item['heuristic_payload_reduction_low_bytes'])}–"
            f"{_human_bytes(item['heuristic_payload_reduction_high_bytes'])}); "
            "incremental disk saving: not measured"
        )
    lines.extend(
        (
            "",
            "Groups:",
            (
                "  Well-compressible: "
                f"{len(findings['well_compressible_groups'])}"
            ),
            (
                "  Nearly-incompressible: "
                f"{len(findings['nearly_incompressible_groups'])}"
            ),
            (
                "Reliable for recommendation: "
                f"{'yes' if findings['reliable_for_recommendation'] else 'no'}"
            ),
        )
    )
    for reason in findings["reliability_reasons"]:
        lines.append(f"  - {reason}")
    lines.extend(
        (
            "",
            "Safety: source files were opened read-only with O_NOATIME; "
            "symlinks were not followed; mount boundaries were not crossed.",
            (
                "Btrfs estimates reset the compressor for every independent "
                f"{BTRFS_EXTENT_CHUNK // 1024} KiB chunk."
            ),
            (
                "These are total-payload estimates from an uncompressed "
                "baseline, not additional space Game Optimization can reclaim now. "
                "Only before/after compsize, shared/exclusive and filesystem "
                "measurements can establish incremental disk savings."
            ),
            "XZ is only an external reference and is not LZX or a Btrfs "
            "compression mode.",
        )
    )
    return "\n".join(lines) + "\n"


def write_reports(
    report: dict[str, Any],
    output_directory: Path | str,
) -> tuple[Path, Path]:
    directory = Path(output_directory).expanduser().resolve(strict=False)
    game_root = Path(report["game"]["path"]).resolve(strict=True)
    if _is_within(directory, game_root):
        raise ValueError("report output directory must be outside the game tree")
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", report["game"]["name"]).strip(
        "-."
    ) or "game"
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = directory / f"{safe_name}-{timestamp}.json"
    text_path = directory / f"{safe_name}-{timestamp}.txt"
    payloads = (
        (
            json_path,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        ),
        (text_path, format_text_report(report)),
    )
    for destination, payload in payloads:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=directory,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return json_path, text_path


def _progress_to_stderr(stage: str, fraction: float, message: str) -> None:
    if fraction > 0:
        prefix = f"[{stage} {fraction * 100:5.1f}%]"
    else:
        prefix = f"[{stage}]"
    print(f"\r{prefix} {message}", file=sys.stderr, flush=True)


def _parse_size_mib(value: str, maximum: int) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    parsed = number * 1024 * 1024
    if parsed > maximum:
        raise argparse.ArgumentTypeError(
            f"value exceeds the safe maximum of {maximum // (1024 * 1024)} MiB"
        )
    return parsed


def _parse_sample_mib(value: str) -> int:
    return _parse_size_mib(value, MAX_SAMPLE_LIMIT)


def _parse_reference_mib(value: str) -> int:
    return _parse_size_mib(value, MAX_REFERENCE_LIMIT)


def _parse_window_mib(value: str) -> int:
    return _parse_size_mib(value, MAX_WINDOW_SIZE)


def _parse_timeout_seconds(value: str) -> float:
    number = float(value)
    if number <= 0 or number > MAX_EXTERNAL_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            "timeout must be greater than zero and no more than "
            f"{MAX_EXTERNAL_TIMEOUT_SECONDS:.0f} seconds"
        )
    return number


def _parse_benchmark_timeout_seconds(value: str) -> float:
    number = float(value)
    if number <= 0 or number > MAX_BENCHMARK_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            "benchmark timeout must be greater than zero and no more than "
            f"{MAX_BENCHMARK_TIMEOUT_SECONDS:.0f} seconds"
        )
    return number


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only stratified compression benchmark for a game directory"
        )
    )
    parser.add_argument("game_path", type=Path)
    parser.add_argument(
        "--steam-appid",
        help="validate and record the installed Steam AppID",
    )
    parser.add_argument(
        "--steam-manifest",
        type=Path,
        help="appmanifest_<appid>.acf validated before and after the run",
    )
    parser.add_argument(
        "--game-name",
        help="display name for a non-Steam benchmark",
    )
    parser.add_argument(
        "--sample-limit-mib",
        type=_parse_sample_mib,
        default=DEFAULT_SAMPLE_LIMIT,
        metavar="MiB",
    )
    parser.add_argument(
        "--reference-limit-mib",
        type=_parse_reference_mib,
        default=DEFAULT_REFERENCE_LIMIT,
        metavar="MiB",
    )
    parser.add_argument(
        "--window-mib",
        type=_parse_window_mib,
        default=DEFAULT_WINDOW_SIZE,
        metavar="MiB",
    )
    parser.add_argument(
        "--external-timeout-seconds",
        type=_parse_timeout_seconds,
        default=DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="hard deadline for each external reference compressor",
    )
    parser.add_argument(
        "--benchmark-timeout-seconds",
        type=_parse_benchmark_timeout_seconds,
        default=DEFAULT_BENCHMARK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="hard deadline for the complete benchmark",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("reports/compression_benchmarks"),
    )
    parser.add_argument(
        "--json-stdout",
        action="store_true",
        help="print JSON instead of the text report to stdout",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable progress messages on stderr",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    token = CancellationToken()
    previous_handlers: dict[int, Any] = {}

    def handle_signal(signum: int, _frame: Any) -> None:
        if token.cancelled:
            raise KeyboardInterrupt
        token.cancel()
        print(
            f"\nCancellation requested by signal {signum}; stopping safely...",
            file=sys.stderr,
            flush=True,
        )

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, handle_signal)
    try:
        report = benchmark_game(
            args.game_path,
            sample_limit=args.sample_limit_mib,
            reference_limit=args.reference_limit_mib,
            window_size=args.window_mib,
            external_timeout_seconds=args.external_timeout_seconds,
            benchmark_timeout_seconds=args.benchmark_timeout_seconds,
            steam_app_id=args.steam_appid,
            steam_manifest=args.steam_manifest,
            game_name=args.game_name,
            cancel=token,
            progress=None if args.no_progress else _progress_to_stderr,
        )
        json_path, text_path = write_reports(report, args.output_directory)
        if not args.no_progress:
            print(file=sys.stderr)
        if args.json_stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(format_text_report(report), end="")
        print(f"JSON report: {json_path}", file=sys.stderr)
        print(f"Text report: {text_path}", file=sys.stderr)
        return 0
    except BenchmarkCancelled:
        print("\nBenchmark cancelled; source files were not modified.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("\nBenchmark interrupted; source files were not modified.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"\nBenchmark failed safely: {exc}", file=sys.stderr)
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
