"""Read-only Btrfs compression analysis and profile estimation.

The analyzer in this module deliberately has no mutating filesystem methods.
It traverses with ``lstat``/``scandir``, never follows directory symlinks, and
only invokes the read-only ``compsize`` utility when it is available.  Sample
compression happens in memory; original game files are never rewritten.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import importlib
import logging
import math
import os
from pathlib import Path
import random
import re
import shutil
import stat
import subprocess
from threading import Event
import time
from typing import Any, Protocol

from gameforge.models.enums import CompressionProfile, FilesystemType
from gameforge.models.game import Game
from gameforge.models.system import FilesystemInfo


logger = logging.getLogger(__name__)

MIB = 1024 * 1024
GIB = 1024 * MIB
ANALYZER_VERSION = 2
PROFILE_LEVELS: Mapping[CompressionProfile, int | None] = {
    CompressionProfile.FAST: 1,
    CompressionProfile.BALANCED: 3,
    CompressionProfile.MAXIMUM: 9,
    CompressionProfile.AUTO: None,
}
AUTO_LEVELS = (1, 3, 6, 9)

_COMPSIZE_ROW = re.compile(
    r"^\s*(?P<kind>\S+)\s+"
    r"(?P<percent>[0-9]+(?:\.[0-9]+)?)%\s+"
    r"(?P<disk>\S+)\s+(?P<uncompressed>\S+)\s+(?P<referenced>\S+)\s*$",
    re.IGNORECASE,
)
_RAW_BTRFS_DU_ROW = re.compile(
    r"^\s*(?P<total>[0-9]+)\s+"
    r"(?P<exclusive>[0-9]+)\s+"
    r"(?P<set_shared>[0-9]+)(?:\s+.*)?$"
)
_SIZE_VALUE = re.compile(
    r"^(?P<number>[0-9]+(?:\.[0-9]+)?)\s*(?P<suffix>[kmgtpe]?)(?:i?b)?$",
    re.IGNORECASE,
)

_PRECOMPRESSED_EXTENSIONS = frozenset(
    {
        ".7z",
        ".apk",
        ".avi",
        ".bz2",
        ".flac",
        ".gz",
        ".jpg",
        ".jpeg",
        ".m4a",
        ".mkv",
        ".mp3",
        ".mp4",
        ".ogg",
        ".pak",
        ".png",
        ".rar",
        ".vpk",
        ".webm",
        ".wem",
        ".xz",
        ".zip",
    }
)
_TEXT_EXTENSIONS = frozenset(
    {
        ".cfg",
        ".csv",
        ".ini",
        ".json",
        ".log",
        ".lua",
        ".md",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_EXECUTABLE_EXTENSIONS = frozenset(
    {"", ".bin", ".dll", ".exe", ".so", ".wasm"}
)


class FilesystemInspector(Protocol):
    def inspect(self, path: Path) -> FilesystemInfo: ...


class AnalysisCancelled(RuntimeError):
    """Raised when a cooperative cancellation request stops analysis."""


class InvalidAnalysisPath(ValueError):
    """Raised only for a syntactically unusable path argument."""


@dataclass(frozen=True, slots=True)
class AnalysisLimits:
    """Resource limits for a normal interactive analysis."""

    max_sample_bytes: int = 512 * MIB
    max_bytes_per_file: int = 8 * MIB
    max_sample_candidates_per_group: int = 512
    timeout_seconds: float = 120.0
    command_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        for value, name in (
            (self.max_sample_bytes, "max_sample_bytes"),
            (self.max_bytes_per_file, "max_bytes_per_file"),
            (
                self.max_sample_candidates_per_group,
                "max_sample_candidates_per_group",
            ),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for value, name in (
            (self.timeout_seconds, "timeout_seconds"),
            (self.command_timeout_seconds, "command_timeout_seconds"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number")


@dataclass(frozen=True, slots=True)
class AnalysisProgress:
    stage: str
    progress: float
    scanned_files: int
    analyzed_bytes: int
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "progress": min(1.0, max(0.0, self.progress)),
            "scanned_files": max(0, self.scanned_files),
            "analyzed_bytes": max(0, self.analyzed_bytes),
            "elapsed_seconds": max(0.0, self.elapsed_seconds),
        }


@dataclass(frozen=True, slots=True)
class CompsizeResult:
    available: bool
    message: str
    disk_usage_bytes: int | None = None
    uncompressed_bytes: int | None = None
    referenced_bytes: int | None = None
    current_compression_ratio: float | None = None
    compression_types: Mapping[str, int] = field(default_factory=dict)
    saved_bytes: int | None = None
    possible_shared_extents: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "message": self.message,
            "disk_usage_bytes": self.disk_usage_bytes,
            "uncompressed_bytes": self.uncompressed_bytes,
            "referenced_bytes": self.referenced_bytes,
            "current_compression_ratio": self.current_compression_ratio,
            "compression_types": dict(self.compression_types),
            "saved_bytes": self.saved_bytes,
            "possible_shared_extents": self.possible_shared_extents,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CompsizeResult:
        return cls(
            available=bool(raw.get("available", False)),
            message=str(raw.get("message", "")),
            disk_usage_bytes=_optional_int(raw.get("disk_usage_bytes")),
            uncompressed_bytes=_optional_int(raw.get("uncompressed_bytes")),
            referenced_bytes=_optional_int(raw.get("referenced_bytes")),
            current_compression_ratio=_optional_float(
                raw.get("current_compression_ratio")
            ),
            compression_types={
                str(key): max(0, int(value))
                for key, value in _mapping(raw.get("compression_types")).items()
                if _is_number(value)
            },
            saved_bytes=_optional_int(raw.get("saved_bytes")),
            possible_shared_extents=_optional_bool(
                raw.get("possible_shared_extents")
            ),
)


def _short_process_text(value: Any, *, limit: int = 512) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass(frozen=True, slots=True)
class BtrfsDuResult:
    """Read-only FIEMAP accounting reported by ``btrfs filesystem du``.

    ``estimated_growth_bytes`` is a conservative upper bound for bytes that
    could need new allocation if every shared reference below the selected
    directory were rewritten.  ``unknown`` deliberately means that a future
    defragmentation must fail closed.
    """

    available: bool
    state: str
    total_bytes: int | None
    exclusive_bytes: int | None
    set_shared_bytes: int | None
    estimated_growth_bytes: int | None
    message: str

    @property
    def shared_extents(self) -> bool | None:
        if self.state == "detected":
            return True
        if self.state == "not_detected":
            return False
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "state": self.state,
            "total_bytes": self.total_bytes,
            "exclusive_bytes": self.exclusive_bytes,
            "set_shared_bytes": self.set_shared_bytes,
            "estimated_growth_bytes": self.estimated_growth_bytes,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BtrfsDuResult:
        state = str(raw.get("state", "unknown")).strip().casefold()
        total = _optional_int(raw.get("total_bytes"))
        exclusive = _optional_int(raw.get("exclusive_bytes"))
        set_shared = _optional_int(raw.get("set_shared_bytes"))
        estimated_growth = _optional_int(raw.get("estimated_growth_bytes"))
        message = str(raw.get("message", "Shared-extent measurement is unavailable"))
        available = raw.get("available") is True
        if (
            not available
            or state not in {"detected", "not_detected"}
            or total is None
            or exclusive is None
            or set_shared is None
            or exclusive > total
            or set_shared > total
        ):
            return cls.unknown(message)
        expected_growth = max(0, total - exclusive, set_shared)
        if estimated_growth != expected_growth:
            estimated_growth = expected_growth
        # With one input path ``Set shared`` can legitimately be zero while
        # Total > Exclusive: those extents are shared with an inode outside
        # the measured set.  Either signal therefore means reflink risk.
        expected_state = (
            "detected"
            if set_shared > 0 or expected_growth > 0
            else "not_detected"
        )
        if state != expected_state:
            return cls.unknown(
                "Cached btrfs filesystem du state is inconsistent"
            )
        return cls(
            available=True,
            state=expected_state,
            total_bytes=total,
            exclusive_bytes=exclusive,
            set_shared_bytes=set_shared,
            estimated_growth_bytes=estimated_growth,
            message=message,
        )

    @classmethod
    def unknown(cls, message: str) -> BtrfsDuResult:
        return cls(
            available=False,
            state="unknown",
            total_bytes=None,
            exclusive_bytes=None,
            set_shared_bytes=None,
            estimated_growth_bytes=None,
            message=str(message),
        )


@dataclass(frozen=True, slots=True)
class ProfileEstimate:
    profile: CompressionProfile
    persistent_compression_algorithm: str
    one_time_recompression_level: int
    estimated_size_low_bytes: int | None
    estimated_size_high_bytes: int | None
    estimated_savings_low_bytes: int | None
    estimated_savings_high_bytes: int | None
    estimated_time_low_seconds: float | None
    estimated_time_high_seconds: float | None
    cpu_usage: str
    sample_ratio: float | None
    estimated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "persistent_compression_algorithm": self.persistent_compression_algorithm,
            "one_time_recompression_level": self.one_time_recompression_level,
            "estimated_size_low_bytes": self.estimated_size_low_bytes,
            "estimated_size_high_bytes": self.estimated_size_high_bytes,
            "estimated_savings_low_bytes": self.estimated_savings_low_bytes,
            "estimated_savings_high_bytes": self.estimated_savings_high_bytes,
            "estimated_time_low_seconds": self.estimated_time_low_seconds,
            "estimated_time_high_seconds": self.estimated_time_high_seconds,
            "cpu_usage": self.cpu_usage,
            "sample_ratio": self.sample_ratio,
            "estimated": self.estimated,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProfileEstimate:
        profile = _profile(str(raw.get("profile", CompressionProfile.AUTO.value)))
        return cls(
            profile=profile,
            persistent_compression_algorithm=str(
                raw.get("persistent_compression_algorithm", "zstd")
            ),
            one_time_recompression_level=max(
                1, int(raw.get("one_time_recompression_level", 3))
            ),
            estimated_size_low_bytes=_optional_int(
                raw.get("estimated_size_low_bytes")
            ),
            estimated_size_high_bytes=_optional_int(
                raw.get("estimated_size_high_bytes")
            ),
            estimated_savings_low_bytes=_optional_int(
                raw.get("estimated_savings_low_bytes")
            ),
            estimated_savings_high_bytes=_optional_int(
                raw.get("estimated_savings_high_bytes")
            ),
            estimated_time_low_seconds=_optional_float(
                raw.get("estimated_time_low_seconds")
            ),
            estimated_time_high_seconds=_optional_float(
                raw.get("estimated_time_high_seconds")
            ),
            cpu_usage=str(raw.get("cpu_usage", "Unknown")),
            sample_ratio=_optional_float(raw.get("sample_ratio")),
            estimated=bool(raw.get("estimated", False)),
        )


@dataclass(frozen=True, slots=True)
class BtrfsAnalysisReport:
    """Serializable result of one read-only game-directory inspection."""

    analyzer_version: int
    game_id: str
    app_id: str
    game_name: str
    path: str
    path_exists: bool
    path_is_directory: bool
    filesystem: str
    is_btrfs: bool
    writable: bool
    mount_point: str
    filesystem_device: str
    available_bytes: int | None
    logical_bytes: int
    physical_bytes: int
    file_count: int
    directory_count: int
    symlink_count: int
    hardlink_count: int
    permission_errors: tuple[str, ...]
    scan_complete: bool
    existing_compression_state: str
    persistent_compression_algorithm: str | None
    mount_compression_level: int | None
    compsize: CompsizeResult
    btrfs_du: BtrfsDuResult
    possible_shared_extents: bool | None
    game_running: bool
    running_process_ids: tuple[int, ...]
    sampled_bytes: int
    sampled_files: int
    sampling_codec: str
    sampling_complete: bool
    selected_auto_level: int
    profiles: Mapping[str, ProfileEstimate]
    profiles_unlocked: bool
    compression_eligible: bool
    benefit: str
    warnings: tuple[str, ...]
    created_at: datetime
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyzer_version": self.analyzer_version,
            "game_id": self.game_id,
            "app_id": self.app_id,
            "game_name": self.game_name,
            "path": self.path,
            "path_exists": self.path_exists,
            "path_is_directory": self.path_is_directory,
            "filesystem": self.filesystem,
            "is_btrfs": self.is_btrfs,
            "writable": self.writable,
            "mount_point": self.mount_point,
            "filesystem_device": self.filesystem_device,
            "available_bytes": self.available_bytes,
            "logical_bytes": self.logical_bytes,
            "physical_bytes": self.physical_bytes,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "symlink_count": self.symlink_count,
            "hardlink_count": self.hardlink_count,
            "permission_errors": list(self.permission_errors),
            "scan_complete": self.scan_complete,
            "existing_compression_state": self.existing_compression_state,
            "persistent_compression_algorithm": self.persistent_compression_algorithm,
            "mount_compression_level": self.mount_compression_level,
            "compsize": self.compsize.to_dict(),
            "btrfs_du": self.btrfs_du.to_dict(),
            "possible_shared_extents": self.possible_shared_extents,
            "game_running": self.game_running,
            "running_process_ids": list(self.running_process_ids),
            "sampled_bytes": self.sampled_bytes,
            "sampled_files": self.sampled_files,
            "sampling_codec": self.sampling_codec,
            "sampling_complete": self.sampling_complete,
            "selected_auto_level": self.selected_auto_level,
            "profiles": {
                name: estimate.to_dict() for name, estimate in self.profiles.items()
            },
            "profiles_unlocked": self.profiles_unlocked,
            "compression_eligible": self.compression_eligible,
            "benefit": self.benefit,
            "warnings": list(self.warnings),
            "created_at": self.created_at.isoformat(),
            "elapsed_seconds": self.elapsed_seconds,
            "read_only": True,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BtrfsAnalysisReport:
        created_raw = raw.get("created_at")
        try:
            created = datetime.fromisoformat(str(created_raw))
        except (TypeError, ValueError):
            created = datetime.now(UTC)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        profiles_raw = _mapping(raw.get("profiles"))
        profiles = {
            str(name): ProfileEstimate.from_dict(_mapping(value))
            for name, value in profiles_raw.items()
            if isinstance(value, Mapping)
        }
        return cls(
            analyzer_version=int(raw.get("analyzer_version", 0)),
            game_id=str(raw.get("game_id", "")),
            app_id=str(raw.get("app_id", "")),
            game_name=str(raw.get("game_name", "")),
            path=str(raw.get("path", "")),
            path_exists=bool(raw.get("path_exists", False)),
            path_is_directory=bool(raw.get("path_is_directory", False)),
            filesystem=str(raw.get("filesystem", FilesystemType.UNKNOWN.value)),
            is_btrfs=bool(raw.get("is_btrfs", False)),
            writable=bool(raw.get("writable", False)),
            mount_point=str(raw.get("mount_point", "")),
            filesystem_device=str(raw.get("filesystem_device", "")),
            available_bytes=_optional_int(raw.get("available_bytes")),
            logical_bytes=max(0, int(raw.get("logical_bytes", 0))),
            physical_bytes=max(0, int(raw.get("physical_bytes", 0))),
            file_count=max(0, int(raw.get("file_count", 0))),
            directory_count=max(0, int(raw.get("directory_count", 0))),
            symlink_count=max(0, int(raw.get("symlink_count", 0))),
            hardlink_count=max(0, int(raw.get("hardlink_count", 0))),
            permission_errors=tuple(
                str(value) for value in _sequence(raw.get("permission_errors"))
            ),
            scan_complete=bool(raw.get("scan_complete", False)),
            existing_compression_state=str(
                raw.get("existing_compression_state", "unknown")
            ),
            persistent_compression_algorithm=(
                str(raw["persistent_compression_algorithm"])
                if raw.get("persistent_compression_algorithm") is not None
                else None
            ),
            mount_compression_level=_optional_int(
                raw.get("mount_compression_level")
            ),
            compsize=CompsizeResult.from_dict(_mapping(raw.get("compsize"))),
            btrfs_du=BtrfsDuResult.from_dict(_mapping(raw.get("btrfs_du"))),
            possible_shared_extents=_optional_bool(
                raw.get("possible_shared_extents")
            ),
            game_running=bool(raw.get("game_running", False)),
            running_process_ids=tuple(
                int(value)
                for value in _sequence(raw.get("running_process_ids"))
                if _is_number(value)
            ),
            sampled_bytes=max(0, int(raw.get("sampled_bytes", 0))),
            sampled_files=max(0, int(raw.get("sampled_files", 0))),
            sampling_codec=str(raw.get("sampling_codec", "unavailable")),
            sampling_complete=bool(raw.get("sampling_complete", False)),
            selected_auto_level=max(1, int(raw.get("selected_auto_level", 3))),
            profiles=profiles,
            profiles_unlocked=bool(raw.get("profiles_unlocked", False)),
            compression_eligible=bool(raw.get("compression_eligible", False)),
            benefit=str(raw.get("benefit", "Not estimated")),
            warnings=tuple(str(value) for value in _sequence(raw.get("warnings"))),
            created_at=created,
            elapsed_seconds=max(0.0, float(raw.get("elapsed_seconds", 0.0))),
        )


@dataclass(slots=True)
class _ScanResult:
    logical_bytes: int = 0
    physical_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0
    symlink_count: int = 0
    hardlink_count: int = 0
    errors: list[str] = field(default_factory=list)
    complete: bool = True
    candidates: list[_SampleCandidate] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _SampleCandidate:
    path: Path
    size: int
    category: str
    device: int
    inode: int
    parent_device: int
    parent_inode: int


@dataclass(frozen=True, slots=True)
class _SamplingResult:
    bytes_read: int
    files_read: int
    output_by_level: Mapping[int, int]
    seconds_by_level: Mapping[int, float]
    codec: str
    complete: bool
    warnings: tuple[str, ...]


class _CandidatePool:
    """Bounded per-category reservoir; memory does not grow with a game."""

    def __init__(self, per_group: int) -> None:
        self._per_group = per_group
        self._groups: dict[str, list[_SampleCandidate]] = {}
        self._seen: dict[str, int] = {}
        self._random = random.Random(0x4A17)

    def add(self, candidate: _SampleCandidate) -> None:
        group = self._groups.setdefault(candidate.category, [])
        seen = self._seen.get(candidate.category, 0) + 1
        self._seen[candidate.category] = seen
        if len(group) < self._per_group:
            group.append(candidate)
            return
        replacement = self._random.randrange(seen)
        if replacement < self._per_group:
            group[replacement] = candidate

    def ordered(self) -> list[_SampleCandidate]:
        groups: list[list[_SampleCandidate]] = []
        for category in sorted(self._groups):
            source = sorted(self._groups[category], key=lambda item: item.size)
            ordered: list[_SampleCandidate] = []
            left = 0
            right = len(source) - 1
            while left <= right:
                ordered.append(source[left])
                left += 1
                if left <= right:
                    ordered.append(source[right])
                    right -= 1
            groups.append(ordered)

        result: list[_SampleCandidate] = []
        while any(groups):
            for group in groups:
                if group:
                    result.append(group.pop(0))
        return result


class BtrfsCompressionAnalyzer:
    """Analyze one game directory without changing it or its filesystem."""

    def __init__(
        self,
        filesystem_provider: FilesystemInspector | None = None,
        *,
        limits: AnalysisLimits | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
        executable_finder: Callable[[str], str | None] = shutil.which,
        disk_usage_reader: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
        access_checker: Callable[[str | os.PathLike[str], int], bool] = os.access,
        compressor: Callable[[bytes, int], bytes] | None = None,
        process_detector: Callable[[Path, Event | None], Sequence[int]] | None = None,
        proc_root: Path = Path("/proc"),
        clock: Callable[[], float] = time.monotonic,
        host_service: object | None = None,
    ) -> None:
        if filesystem_provider is None:
            from gameforge.providers.linux_filesystem import LinuxFilesystemProvider

            filesystem_provider = LinuxFilesystemProvider()
        self._filesystem_provider = filesystem_provider
        self._limits = limits or AnalysisLimits()
        self._command_runner = command_runner
        self._uses_default_command_runner = command_runner is subprocess.run
        self._executable_finder = executable_finder
        self._disk_usage_reader = disk_usage_reader
        self._access_checker = access_checker
        self._compressor = compressor
        self._process_detector = process_detector
        self._proc_root = Path(proc_root)
        self._clock = clock
        self._host_service = host_service

    def analyze(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
        progress_callback: Callable[[AnalysisProgress], None] | None = None,
        sample_files: bool = True,
        measure_compsize: bool = True,
    ) -> BtrfsAnalysisReport:
        """Return a report while performing only read operations on game data.

        ``measure_compsize=False`` is reserved for callers that obtain a
        separate authoritative compsize result, such as the Polkit baseline.
        """

        if not isinstance(game, Game):
            raise TypeError("game must be a Game")
        root = Path(game.install_path).expanduser()
        if not os.fspath(root).strip():
            raise InvalidAnalysisPath("game path cannot be empty")

        started = self._clock()
        deadline = started + self._limits.timeout_seconds
        warnings: list[str] = []
        self._check_cancel(cancel_event)
        self._emit_progress(
            progress_callback, "Validating path", 0.01, 0, 0, started
        )

        try:
            root_stat = os.lstat(root)
        except FileNotFoundError:
            return self._invalid_report(
                game,
                root,
                path_exists=False,
                path_is_directory=False,
                warning="The game path does not exist.",
                started=started,
            )
        except OSError as error:
            return self._invalid_report(
                game,
                root,
                path_exists=False,
                path_is_directory=False,
                warning=f"The game path could not be inspected: {error}",
                started=started,
            )

        root_is_symlink = stat.S_ISLNK(root_stat.st_mode)
        path_is_directory = stat.S_ISDIR(root_stat.st_mode)
        if root_is_symlink or not path_is_directory:
            reason = (
                "A symbolic link cannot be used as the analysis root."
                if root_is_symlink
                else "The game path is not a directory."
            )
            return self._invalid_report(
                game,
                root,
                path_exists=True,
                path_is_directory=False,
                warning=reason,
                started=started,
            )

        filesystem = self._inspect_filesystem(root, game, warnings)
        filesystem_name = filesystem.filesystem_name or filesystem.filesystem.value
        is_btrfs = (
            filesystem.filesystem is FilesystemType.BTRFS
            or filesystem_name.casefold() == "btrfs"
        )
        try:
            writable = bool(self._access_checker(root, os.W_OK))
        except OSError as error:
            writable = False
            warnings.append(f"Write access could not be checked: {error}")
        if filesystem.writable is False:
            writable = False

        available_bytes = self._available_bytes(filesystem.mount_point, warnings)
        persistent_algorithm, mount_level = self._mount_compression(
            filesystem.mount_options
        )
        self._emit_progress(
            progress_callback, "Scanning files", 0.05, 0, 0, started
        )
        scan = self._scan_directory(
            root,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
            started=started,
            deadline=deadline,
        )
        warnings.extend(scan.errors)
        if not scan.complete:
            warnings.append("The file scan reached its time limit; totals are partial.")

        self._check_cancel(cancel_event)
        process_ids = tuple(
            sorted(set(self._detect_processes(root, cancel_event, warnings)))
        )
        game_running = bool(process_ids)
        if game_running:
            warnings.append("The game appears to be running; future writes must wait.")

        compsize = CompsizeResult(False, "Not run on a non-Btrfs filesystem")
        btrfs_du = BtrfsDuResult.unknown(
            "Not run on a non-Btrfs filesystem"
        )
        if is_btrfs:
            self._emit_progress(
                progress_callback,
                "Checking shared extents",
                0.58,
                scan.file_count,
                0,
                started,
            )
            host_measurement_used = False
            if measure_compsize and self._host_service is not None:
                try:
                    compsize, btrfs_du = self._measure_host(game, cancel_event)
                    host_measurement_used = True
                except Exception as error:
                    message = (
                        "Host compression measurement failed: "
                        + (str(error).strip() or type(error).__name__)
                    )
                    compsize = CompsizeResult(False, message)
                    # Exact compsize is optional.  Shared-extent safety still
                    # uses the bundled, read-only btrfs filesystem du command.
                    btrfs_du = self._measure_btrfs_du(
                        root, cancel_event, deadline=deadline
                    )
            else:
                btrfs_du = self._measure_btrfs_du(
                    root,
                    cancel_event,
                    deadline=deadline,
                )
            self._append_shared_extent_warning(warnings, btrfs_du)
            if not measure_compsize:
                compsize = CompsizeResult(
                    False,
                    "Supplied by privileged baseline measurement",
                )
            elif not host_measurement_used and self._host_service is None:
                self._emit_progress(
                    progress_callback,
                    "Measuring existing compression",
                    0.60,
                    scan.file_count,
                    0,
                    started,
                )
                compsize = self._measure_compsize(
                    root,
                    cancel_event,
                    deadline=deadline,
                )
            if measure_compsize and not compsize.available:
                warnings.append(compsize.message)

        sampling = _SamplingResult(
            bytes_read=0,
            files_read=0,
            output_by_level={},
            seconds_by_level={},
            codec="not run",
            complete=False,
            warnings=(),
        )
        if is_btrfs and sample_files and scan.candidates and self._clock() < deadline:
            sampling = self._sample_files(
                root,
                scan.candidates,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
                scanned_files=scan.file_count,
                started=started,
                deadline=deadline,
            )
            warnings.extend(sampling.warnings)
        elif is_btrfs and sample_files and self._clock() >= deadline:
            warnings.append("Sampling was skipped because the analysis time limit expired.")

        profiles, auto_level = self._estimate_profiles(
            logical_bytes=scan.logical_bytes,
            physical_bytes=(
                compsize.disk_usage_bytes
                if compsize.disk_usage_bytes is not None
                else scan.physical_bytes
            ),
            sampling=sampling,
        )
        benefit = self._benefit(profiles.get(CompressionProfile.AUTO.value))
        existing_state = self._existing_compression_state(compsize)
        shared = btrfs_du.shared_extents
        if shared is None and compsize.possible_shared_extents is True:
            # Keep the older compsize signal as a conservative hint, but never
            # use it to declare that sharing was not detected.
            shared = True
        profiles_unlocked = bool(
            is_btrfs
            and scan.complete
        )
        compression_eligible = bool(
            profiles_unlocked and writable and not game_running and path_is_directory
        )
        if not is_btrfs:
            warnings.append(
                f"Compression analysis is unavailable on {filesystem_name or 'this filesystem'}."
            )
        if not writable:
            warnings.append("The game directory is not writable by the current user.")

        self._emit_progress(
            progress_callback,
            "Finalizing report",
            0.98,
            scan.file_count,
            sampling.bytes_read,
            started,
        )
        report = BtrfsAnalysisReport(
            analyzer_version=ANALYZER_VERSION,
            game_id=game.id,
            app_id=str(game.steam_app_id or game.id),
            game_name=game.name,
            path=os.path.abspath(os.fspath(root)),
            path_exists=True,
            path_is_directory=True,
            filesystem=filesystem_name,
            is_btrfs=is_btrfs,
            writable=writable,
            mount_point=os.fspath(filesystem.mount_point),
            filesystem_device=str(filesystem.device or ""),
            available_bytes=available_bytes,
            logical_bytes=scan.logical_bytes,
            physical_bytes=scan.physical_bytes,
            file_count=scan.file_count,
            directory_count=scan.directory_count,
            symlink_count=scan.symlink_count,
            hardlink_count=scan.hardlink_count,
            permission_errors=tuple(scan.errors),
            scan_complete=scan.complete,
            existing_compression_state=existing_state,
            persistent_compression_algorithm=persistent_algorithm,
            mount_compression_level=mount_level,
            compsize=compsize,
            btrfs_du=btrfs_du,
            possible_shared_extents=shared,
            game_running=game_running,
            running_process_ids=process_ids,
            sampled_bytes=sampling.bytes_read,
            sampled_files=sampling.files_read,
            sampling_codec=sampling.codec,
            sampling_complete=sampling.complete,
            selected_auto_level=auto_level,
            profiles=profiles,
            profiles_unlocked=profiles_unlocked,
            compression_eligible=compression_eligible,
            benefit=benefit,
            warnings=tuple(dict.fromkeys(warnings)),
            created_at=datetime.now(UTC),
            elapsed_seconds=max(0.0, self._clock() - started),
        )
        self._emit_progress(
            progress_callback,
            "Completed",
            1.0,
            scan.file_count,
            sampling.bytes_read,
            started,
        )
        return report

    def detect_running_processes(
        self,
        path: Path,
        *,
        cancel_event: Event | None = None,
    ) -> tuple[int, ...]:
        """Poll processes that reference ``path`` using the safe detector."""

        warnings: list[str] = []
        return tuple(
            sorted(
                set(
                    self._detect_processes(
                        Path(path).expanduser(),
                        cancel_event,
                        warnings,
                    )
                )
            )
        )

    def refresh_cached_report(
        self,
        game: Game,
        report: BtrfsAnalysisReport,
        *,
        cancel_event: Event | None = None,
    ) -> BtrfsAnalysisReport:
        """Refresh volatile mount/process/compsize fields of a cached scan."""

        self._check_cancel(cancel_event)
        deadline = self._clock() + self._limits.timeout_seconds
        root = Path(game.install_path).expanduser()
        warnings = [
            warning
            for warning in report.warnings
            if not any(
                marker in warning
                for marker in (
                    "appears to be running",
                    "is not writable",
                    "compsize",
                    "Shared Btrfs extents/reflinks were detected",
                    "Shared Btrfs extent status is unknown",
                    "Compression analysis is unavailable on",
                    "Available space could not be measured",
                )
            )
        ]
        filesystem = self._inspect_filesystem(root, game, warnings)
        filesystem_name = filesystem.filesystem_name or filesystem.filesystem.value
        is_btrfs = (
            filesystem.filesystem is FilesystemType.BTRFS
            or filesystem_name.casefold() == "btrfs"
        )
        try:
            writable = bool(self._access_checker(root, os.W_OK))
        except OSError as error:
            writable = False
            warnings.append(f"Write access could not be checked: {error}")
        if filesystem.writable is False:
            writable = False
        available_bytes = self._available_bytes(filesystem.mount_point, warnings)
        process_ids = tuple(
            sorted(set(self._detect_processes(root, cancel_event, warnings)))
        )
        game_running = bool(process_ids)
        if game_running:
            warnings.append("The game appears to be running; future writes must wait.")
        if not writable:
            warnings.append("The game directory is not writable by the current user.")
        persistent_algorithm, mount_level = self._mount_compression(
            filesystem.mount_options
        )
        if is_btrfs and self._host_service is not None:
            try:
                compsize, btrfs_du = self._measure_host(game, cancel_event)
            except Exception as error:
                message = "Host compression measurement failed: " + (
                    str(error).strip() or type(error).__name__
                )
                compsize = CompsizeResult(False, message)
                btrfs_du = self._measure_btrfs_du(
                    root, cancel_event, deadline=deadline
                )
        else:
            btrfs_du = (
                self._measure_btrfs_du(root, cancel_event, deadline=deadline)
                if is_btrfs
                else BtrfsDuResult.unknown("Not run on a non-Btrfs filesystem")
            )
            compsize = (
                self._measure_compsize(root, cancel_event, deadline=deadline)
                if is_btrfs
                else CompsizeResult(False, "Not run on a non-Btrfs filesystem")
            )
        if is_btrfs:
            self._append_shared_extent_warning(warnings, btrfs_du)
        if is_btrfs and not compsize.available:
            warnings.append(compsize.message)
        if not is_btrfs:
            warnings.append(
                f"Compression analysis is unavailable on {filesystem_name or 'this filesystem'}."
            )
        profiles_unlocked = bool(
            is_btrfs
            and report.scan_complete
        )
        return replace(
            report,
            filesystem=filesystem_name,
            is_btrfs=is_btrfs,
            writable=writable,
            mount_point=os.fspath(filesystem.mount_point),
            filesystem_device=str(filesystem.device or ""),
            available_bytes=available_bytes,
            existing_compression_state=self._existing_compression_state(compsize),
            persistent_compression_algorithm=persistent_algorithm,
            mount_compression_level=mount_level,
            compsize=compsize,
            btrfs_du=btrfs_du,
            possible_shared_extents=(
                btrfs_du.shared_extents
                if btrfs_du.shared_extents is not None
                else (
                    True
                    if compsize.possible_shared_extents is True
                    else None
                )
            ),
            game_running=game_running,
            running_process_ids=process_ids,
            profiles_unlocked=profiles_unlocked,
            compression_eligible=bool(
                profiles_unlocked and writable and not game_running
            ),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @classmethod
    def parse_btrfs_du(cls, output: str) -> BtrfsDuResult:
        """Parse one raw summarized ``btrfs filesystem du`` result.

        The command reports Total, Exclusive and Set shared followed by the
        path.  Only raw decimal byte values are accepted; malformed or
        internally inconsistent output remains ``unknown``.
        """

        if not isinstance(output, str):
            raise TypeError("btrfs filesystem du output must be text")
        rows: list[tuple[int, int, int]] = []
        for line in output.splitlines():
            match = _RAW_BTRFS_DU_ROW.match(line)
            if match is None:
                continue
            rows.append(
                (
                    int(match.group("total")),
                    int(match.group("exclusive")),
                    int(match.group("set_shared")),
                )
            )
        if len(rows) != 1:
            return BtrfsDuResult.unknown(
                "btrfs filesystem du returned an unrecognized report"
            )

        total, exclusive, set_shared = rows[0]
        shared_references = total - exclusive
        if (
            exclusive > total
            or set_shared > total
        ):
            return BtrfsDuResult.unknown(
                "btrfs filesystem du returned inconsistent extent totals"
            )

        # ``Set shared`` only describes sharing among the paths in the input
        # set.  Total > Exclusive also detects extents shared with paths
        # outside that set (notably a single file checked before defrag).
        state = (
            "detected"
            if set_shared > 0 or shared_references > 0
            else "not_detected"
        )
        return BtrfsDuResult(
            available=True,
            state=state,
            total_bytes=total,
            exclusive_bytes=exclusive,
            set_shared_bytes=set_shared,
            estimated_growth_bytes=max(0, shared_references, set_shared),
            message="Measured with btrfs filesystem du (read-only FIEMAP)",
        )

    @classmethod
    def parse_compsize(cls, output: str) -> CompsizeResult:
        """Parse current compsize table output without locale-dependent guessing."""

        if not isinstance(output, str):
            raise TypeError("compsize output must be text")
        total: tuple[int, int, int] | None = None
        compression_types: dict[str, int] = {}
        for line in output.splitlines():
            match = _COMPSIZE_ROW.match(line)
            if match is None:
                continue
            try:
                disk = cls._parse_size(match.group("disk"))
                uncompressed = cls._parse_size(match.group("uncompressed"))
                referenced = cls._parse_size(match.group("referenced"))
            except ValueError:
                continue
            kind = match.group("kind")
            if kind.casefold() == "total":
                total = (disk, uncompressed, referenced)
            elif kind.casefold() not in {"none", "uncompressed"}:
                compression_types[kind] = disk

        if total is None:
            return CompsizeResult(False, "compsize returned an unrecognized report")
        disk, uncompressed, referenced = total
        ratio = (uncompressed / disk) if disk > 0 else None
        return CompsizeResult(
            available=True,
            message="Measured with compsize",
            disk_usage_bytes=disk,
            uncompressed_bytes=uncompressed,
            referenced_bytes=referenced,
            current_compression_ratio=ratio,
            compression_types=compression_types,
            saved_bytes=max(0, uncompressed - disk),
            possible_shared_extents=referenced > uncompressed,
        )

    @staticmethod
    def _parse_size(raw: str) -> int:
        normalized = raw.strip().replace(",", "")
        match = _SIZE_VALUE.match(normalized)
        if match is None:
            raise ValueError(f"invalid size: {raw!r}")
        number = float(match.group("number"))
        powers = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4, "p": 5, "e": 6}
        power = powers[match.group("suffix").casefold()]
        return max(0, int(round(number * (1024**power))))

    def _scan_directory(
        self,
        root: Path,
        *,
        cancel_event: Event | None,
        progress_callback: Callable[[AnalysisProgress], None] | None,
        started: float,
        deadline: float,
    ) -> _ScanResult:
        result = _ScanResult(directory_count=1)
        root_real = os.path.realpath(root)
        seen_files: set[tuple[int, int]] = set()
        seen_directories: set[tuple[int, int]] = set()
        pool = _CandidatePool(self._limits.max_sample_candidates_per_group)
        try:
            root_stat = os.lstat(root)
            root_identity = (root_stat.st_dev, root_stat.st_ino)
            seen_directories.add(root_identity)
        except OSError as error:
            result.errors.append(self._error(root, error))
            result.complete = False
            return result
        pending = [(root, root_identity)]

        while pending:
            self._check_cancel(cancel_event)
            if self._clock() >= deadline:
                result.complete = False
                break
            current, expected_identity = pending.pop()
            descriptor: int | None = None
            try:
                descriptor = self._open_verified_directory(
                    current,
                    expected_identity=expected_identity,
                    root_real=root_real,
                )
                with os.scandir(descriptor) as entries:
                    while True:
                        self._check_cancel(cancel_event)
                        if self._clock() >= deadline:
                            result.complete = False
                            break
                        try:
                            entry = next(entries)
                        except StopIteration:
                            break
                        except OSError as error:
                            result.errors.append(self._error(current, error))
                            result.complete = False
                            break
                        path = current / entry.name
                        try:
                            entry_stat = entry.stat(follow_symlinks=False)
                        except OSError as error:
                            result.errors.append(self._error(path, error))
                            result.complete = False
                            continue
                        mode = entry_stat.st_mode
                        if stat.S_ISLNK(mode):
                            result.symlink_count += 1
                            continue
                        identity = (entry_stat.st_dev, entry_stat.st_ino)
                        if stat.S_ISDIR(mode):
                            if identity in seen_directories:
                                continue
                            seen_directories.add(identity)
                            result.directory_count += 1
                            pending.append((path, identity))
                            continue
                        if not stat.S_ISREG(mode):
                            continue
                        result.file_count += 1
                        if identity in seen_files:
                            result.hardlink_count += 1
                            continue
                        seen_files.add(identity)
                        result.logical_bytes += max(0, entry_stat.st_size)
                        result.physical_bytes += self._physical_size(entry_stat)
                        if entry_stat.st_size > 0:
                            pool.add(
                                _SampleCandidate(
                                    path=path,
                                    size=entry_stat.st_size,
                                    category=self._file_category(path),
                                    device=entry_stat.st_dev,
                                    inode=entry_stat.st_ino,
                                    parent_device=expected_identity[0],
                                    parent_inode=expected_identity[1],
                                )
                            )
                        if result.file_count == 1 or result.file_count % 128 == 0:
                            progress = min(
                                0.55,
                                0.05 + 0.50 * (1.0 - math.exp(-result.file_count / 5000)),
                            )
                            self._emit_progress(
                                progress_callback,
                                "Scanning files",
                                progress,
                                result.file_count,
                                0,
                                started,
                            )
            except OSError as error:
                result.errors.append(self._error(current, error))
                result.complete = False
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        result.candidates = pool.ordered()
        return result

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
            identity = (opened_stat.st_dev, opened_stat.st_ino)
            if identity != expected_identity or not stat.S_ISDIR(opened_stat.st_mode):
                raise OSError("directory changed while it was being analyzed")
            descriptor_link = Path("/proc/self/fd") / str(descriptor)
            try:
                resolved = os.path.realpath(os.readlink(descriptor_link))
            except OSError as error:
                raise OSError(
                    "open directory containment could not be verified"
                ) from error
            try:
                contained = os.path.commonpath((resolved, root_real)) == root_real
            except ValueError:
                contained = False
            if not contained:
                raise OSError("directory moved outside the game analysis root")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _sample_files(
        self,
        root: Path,
        candidates: Sequence[_SampleCandidate],
        *,
        cancel_event: Event | None,
        progress_callback: Callable[[AnalysisProgress], None] | None,
        scanned_files: int,
        started: float,
        deadline: float,
    ) -> _SamplingResult:
        compressor, codec, codec_warning = self._select_compressor(
            cancel_event=cancel_event,
            deadline=deadline,
        )
        if compressor is None:
            return _SamplingResult(
                bytes_read=0,
                files_read=0,
                output_by_level={},
                seconds_by_level={},
                codec=codec,
                complete=False,
                warnings=(codec_warning,),
            )
        output_by_level = {level: 0 for level in AUTO_LEVELS}
        seconds_by_level = {level: 0.0 for level in AUTO_LEVELS}
        bytes_read = 0
        files_read = 0
        warnings: list[str] = []
        if codec_warning:
            warnings.append(codec_warning)
        complete = True
        total_candidate_bytes = sum(
            min(candidate.size, self._limits.max_bytes_per_file)
            for candidate in candidates
        )
        target_bytes = min(self._limits.max_sample_bytes, total_candidate_bytes)

        for candidate in candidates:
            self._check_cancel(cancel_event)
            if bytes_read >= target_bytes:
                break
            if self._clock() >= deadline:
                complete = False
                warnings.append("The sample test stopped at its time limit.")
                break
            remaining = target_bytes - bytes_read
            read_limit = min(self._limits.max_bytes_per_file, remaining)
            try:
                sample = self._read_sample(root, candidate, read_limit)
            except OSError as error:
                warnings.append(self._error(candidate.path, error))
                continue
            if not sample:
                continue
            files_read += 1
            bytes_read += len(sample)
            for level in AUTO_LEVELS:
                self._check_cancel(cancel_event)
                if self._clock() >= deadline:
                    warnings.append("The sample test stopped at its time limit.")
                    return _SamplingResult(
                        bytes_read,
                        files_read,
                        {},
                        {},
                        codec,
                        False,
                        tuple(dict.fromkeys(warnings)),
                    )
                compression_started = self._clock()
                try:
                    compressed = compressor(sample, level)
                except AnalysisCancelled:
                    raise
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    warnings.append(
                        f"Sample compression at level {level} failed: {error}"
                    )
                    complete = False
                    output_by_level.clear()
                    seconds_by_level.clear()
                    return _SamplingResult(
                        bytes_read,
                        files_read,
                        output_by_level,
                        seconds_by_level,
                        codec,
                        complete,
                        tuple(dict.fromkeys(warnings)),
                    )
                seconds_by_level[level] += max(
                    1e-9, self._clock() - compression_started
                )
                output_by_level[level] += len(compressed)
                del compressed
            progress = 0.65 + 0.30 * (bytes_read / max(1, target_bytes))
            self._emit_progress(
                progress_callback,
                "Testing samples",
                progress,
                scanned_files,
                bytes_read,
                started,
            )
        return _SamplingResult(
            bytes_read=bytes_read,
            files_read=files_read,
            output_by_level=output_by_level,
            seconds_by_level=seconds_by_level,
            codec=codec,
            complete=complete and bytes_read >= target_bytes,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _read_sample(
        self, root: Path, candidate: _SampleCandidate, limit: int
    ) -> bytes:
        path = candidate.path
        root_absolute = os.path.realpath(root)
        parent_descriptor = self._open_verified_directory(
            path.parent,
            expected_identity=(candidate.parent_device, candidate.parent_inode),
            root_real=root_absolute,
        )
        descriptor: int | None = None
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        try:
            leaf_name = path.name
            if not leaf_name or leaf_name in {".", ".."} or os.sep in leaf_name:
                raise OSError("sample has an invalid relative file name")
            descriptor = os.open(leaf_name, flags, dir_fd=parent_descriptor)
            descriptor_stat = os.fstat(descriptor)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise OSError("sample is no longer a regular file")
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                candidate.device,
                candidate.inode,
            ):
                raise OSError("sample file changed while it was being analyzed")
            size = max(0, descriptor_stat.st_size)
            wanted = min(max(0, limit), size)
            if wanted == 0:
                return b""
            if size <= wanted:
                return self._read_from_descriptor(descriptor, wanted)

            piece = max(1, wanted // 3)
            positions = (0, max(0, size // 2 - piece // 2), max(0, size - piece))
            data = bytearray()
            for index, position in enumerate(positions):
                remaining = wanted - len(data)
                if remaining <= 0:
                    break
                length = remaining if index == len(positions) - 1 else min(piece, remaining)
                os.lseek(descriptor, position, os.SEEK_SET)
                data.extend(self._read_from_descriptor(descriptor, length))
            return bytes(data)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_descriptor)

    @staticmethod
    def _read_from_descriptor(descriptor: int, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            block = os.read(descriptor, min(256 * 1024, length - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        return bytes(chunks)

    def _select_compressor(
        self,
        *,
        cancel_event: Event | None,
        deadline: float,
    ) -> tuple[Callable[[bytes, int], bytes] | None, str, str]:
        if self._compressor is not None:
            return self._compressor, "zstd (injected)", ""
        try:
            module = importlib.import_module("zstandard")
        except ImportError:
            module = None
        if module is not None:
            def compress_module(data: bytes, level: int) -> bytes:
                return module.ZstdCompressor(level=level).compress(data)

            return compress_module, "zstd", ""

        executable = self._executable_finder("zstd")
        if executable:
            def compress_command(data: bytes, level: int) -> bytes:
                command = [executable, "--quiet", "--stdout", f"-{level}"]
                completed = self._run_read_only_command(
                    command,
                    input_data=data,
                    text=False,
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
                if completed.returncode != 0:
                    stderr = completed.stderr
                    if isinstance(stderr, bytes):
                        stderr = stderr.decode("utf-8", errors="replace")
                    raise RuntimeError(str(stderr or "zstd failed").strip())
                stdout = completed.stdout
                return stdout if isinstance(stdout, bytes) else stdout.encode()

            return compress_command, "zstd", ""

        return (
            None,
            "unavailable",
            "ZSTD sampling support is unavailable; no numeric savings estimate was generated.",
        )

    def _measure_host(
        self,
        game: Game,
        cancel_event: Event | None,
    ) -> tuple[CompsizeResult, BtrfsDuResult]:
        self._check_cancel(cancel_event)
        analyze = getattr(self._host_service, "analysis", None)
        if not callable(analyze):
            raise RuntimeError("GameForge host component does not support analysis")
        payload = analyze(game)
        if not isinstance(payload, Mapping):
            raise RuntimeError("GameForge host component returned invalid analysis data")
        raw_compsize = payload.get("compsize")
        raw_du = payload.get("btrfs_filesystem_du")
        compsize_map = dict(raw_compsize) if isinstance(raw_compsize, Mapping) else {}
        du_map = dict(raw_du) if isinstance(raw_du, Mapping) else {}

        def required_int(source: Mapping[str, Any], key: str) -> int:
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError(f"Host measurement is missing {key}")
            return value

        disk = required_int(compsize_map, "disk_usage_bytes")
        uncompressed = required_int(compsize_map, "uncompressed_bytes")
        referenced = required_int(compsize_map, "referenced_bytes")
        types_raw = compsize_map.get("compression_types")
        compression_types = {
            str(name): int(value)
            for name, value in (
                types_raw.items() if isinstance(types_raw, Mapping) else ()
            )
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        compsize = CompsizeResult(
            available=True,
            message="Measured on the host with privileged compsize",
            disk_usage_bytes=disk,
            uncompressed_bytes=uncompressed,
            referenced_bytes=referenced,
            current_compression_ratio=(uncompressed / disk if disk > 0 else None),
            compression_types=compression_types,
            saved_bytes=max(0, uncompressed - disk),
        )
        total = required_int(du_map, "total_bytes")
        exclusive = required_int(du_map, "exclusive_bytes")
        shared = required_int(du_map, "set_shared_bytes")
        btrfs_du = BtrfsDuResult.from_dict(
            {
                **du_map,
                "available": True,
                "estimated_growth_bytes": max(0, total - exclusive, shared),
                "message": "Measured on the host with btrfs filesystem du",
            }
        )
        if not btrfs_du.available:
            raise RuntimeError(btrfs_du.message)
        self._check_cancel(cancel_event)
        return compsize, btrfs_du

    def _measure_btrfs_du(
        self,
        root: Path,
        cancel_event: Event | None,
        *,
        deadline: float,
    ) -> BtrfsDuResult:
        self._check_cancel(cancel_event)
        executable = self._executable_finder("btrfs")
        if not executable:
            return BtrfsDuResult.unknown("btrfs command not installed")
        command = [
            executable,
            "filesystem",
            "du",
            "--raw",
            "--summarize",
            os.fspath(root),
        ]
        try:
            completed = self._run_read_only_command(
                command,
                text=True,
                cancel_event=cancel_event,
                deadline=deadline,
            )
        except FileNotFoundError:
            return BtrfsDuResult.unknown("btrfs command not installed")
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            return BtrfsDuResult.unknown(
                f"btrfs filesystem du could not be run: {error}"
            )
        self._check_cancel(cancel_event)
        if completed.returncode != 0:
            stderr = str(completed.stderr or "").strip()
            detail = f": {stderr}" if stderr else ""
            return BtrfsDuResult.unknown(
                "btrfs filesystem du exited with status "
                f"{completed.returncode}{detail}"
            )
        return self.parse_btrfs_du(str(completed.stdout or ""))

    @staticmethod
    def _append_shared_extent_warning(
        warnings: list[str],
        result: BtrfsDuResult,
    ) -> None:
        if result.state == "detected":
            warnings.append(
                "Shared Btrfs extents/reflinks were detected "
                f"(Set shared: {result.set_shared_bytes or 0} bytes; "
                "conservative possible allocation growth: "
                f"up to {result.estimated_growth_bytes or 0} bytes). "
                "Any future recursive defragmentation could break sharing "
                "and increase physical disk usage, so it must remain blocked "
                "until a dedicated per-file safety plan is available."
            )
        elif result.state == "unknown":
            warnings.append(
                "Shared Btrfs extent status is unknown "
                f"({result.message}). Any future defragmentation must remain "
                "blocked because reflink safety could not be verified."
            )

    def _measure_compsize(
        self,
        root: Path,
        cancel_event: Event | None,
        *,
        deadline: float,
    ) -> CompsizeResult:
        self._check_cancel(cancel_event)
        executable = self._executable_finder("compsize")
        if not executable:
            return CompsizeResult(False, "compsize not installed")
        # Raw bytes avoid rounding a real saving to zero and ``-x`` prevents
        # an analysis rooted at a game directory from crossing a nested mount.
        command = [
            executable,
            "--bytes",
            "--one-file-system",
            os.fspath(root),
        ]
        try:
            completed = self._run_read_only_command(
                command,
                text=True,
                cancel_event=cancel_event,
                deadline=deadline,
            )
        except FileNotFoundError:
            return CompsizeResult(False, "compsize not installed")
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            return CompsizeResult(False, f"compsize could not be run: {error}")
        self._check_cancel(cancel_event)
        if completed.returncode != 0:
            stderr = str(completed.stderr or "").strip()
            detail = f": {stderr}" if stderr else ""
            return CompsizeResult(
                False, f"compsize exited with status {completed.returncode}{detail}"
            )
        return self.parse_compsize(str(completed.stdout or ""))

    def _run_read_only_command(
        self,
        command: Sequence[str],
        *,
        input_data: bytes | str | None = None,
        text: bool,
        cancel_event: Event | None,
        deadline: float,
    ) -> subprocess.CompletedProcess[Any]:
        """Run one argv-only helper within both command and analysis limits.

        Production helpers are polled so cancellation can terminate them.  A
        test-injected command runner keeps the small dependency-injection seam
        used by parser tests, but receives the same bounded timeout and
        ``shell=False`` contract.
        """

        self._check_cancel(cancel_event)
        command_argv = [os.fspath(argument) for argument in command]
        logger.info("Starting read-only analysis helper argv=%r", command_argv)
        now = self._clock()
        remaining = min(
            self._limits.command_timeout_seconds,
            deadline - now,
        )
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command_argv, 0)
        command_deadline = now + remaining

        if not self._uses_default_command_runner:
            kwargs: dict[str, Any] = {
                "capture_output": True,
                "text": text,
                "check": False,
                "timeout": remaining,
                "shell": False,
            }
            if input_data is not None:
                kwargs["input"] = input_data
            completed = self._command_runner(command_argv, **kwargs)
            self._check_cancel(cancel_event)
            logger.info(
                "Read-only analysis helper finished argv=%r "
                "exit_code=%d stderr=%r",
                command_argv,
                int(completed.returncode),
                _short_process_text(completed.stderr),
            )
            return completed

        process = subprocess.Popen(
            command_argv,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            shell=False,
        )
        pending_input = input_data
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._terminate_process(process)
                    raise AnalysisCancelled("Compression analysis was cancelled")
                remaining = command_deadline - self._clock()
                if remaining <= 0:
                    self._terminate_process(process)
                    raise subprocess.TimeoutExpired(command_argv, 0)
                try:
                    stdout, stderr = process.communicate(
                        input=pending_input,
                        timeout=min(0.05, remaining),
                    )
                except subprocess.TimeoutExpired:
                    pending_input = None
                    continue
                completed = subprocess.CompletedProcess(
                    command_argv,
                    process.returncode,
                    stdout,
                    stderr,
                )
                logger.info(
                    "Read-only analysis helper finished argv=%r "
                    "exit_code=%d stderr=%r",
                    command_argv,
                    int(completed.returncode),
                    _short_process_text(completed.stderr),
                )
                return completed
        except BaseException:
            if process.poll() is None:
                self._terminate_process(process)
            raise

    @staticmethod
    def _terminate_process(process: subprocess.Popen[Any]) -> None:
        """Reap a helper promptly, escalating only if it ignores terminate."""

        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.communicate(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    def _estimate_profiles(
        self,
        *,
        logical_bytes: int,
        physical_bytes: int,
        sampling: _SamplingResult,
    ) -> tuple[dict[str, ProfileEstimate], int]:
        if sampling.bytes_read <= 0 or not sampling.output_by_level:
            auto_level = 3
            return (
                {
                    profile.value: self._unknown_estimate(
                        profile,
                        auto_level if profile is CompressionProfile.AUTO else int(level),
                    )
                    for profile, level in PROFILE_LEVELS.items()
                },
                auto_level,
            )

        ratios = {
            level: min(1.5, max(0.0, output / sampling.bytes_read))
            for level, output in sampling.output_by_level.items()
        }
        auto_level = self._choose_auto_level(ratios, sampling.seconds_by_level)
        estimates: dict[str, ProfileEstimate] = {}
        for profile, configured_level in PROFILE_LEVELS.items():
            level = auto_level if configured_level is None else configured_level
            ratio = ratios.get(level)
            seconds = sampling.seconds_by_level.get(level, 0.0)
            if ratio is None or seconds <= 0:
                estimates[profile.value] = self._unknown_estimate(profile, level)
                continue
            coverage = min(1.0, sampling.bytes_read / max(1, logical_bytes))
            spread = max(0.025, 0.13 * (1.0 - coverage))
            low_ratio = max(0.05, ratio - spread)
            high_ratio = min(1.0, ratio + spread)
            size_low = max(0, int(logical_bytes * low_ratio))
            size_high = max(size_low, int(logical_bytes * high_ratio))
            current = max(physical_bytes, 0)
            savings_low = max(0, current - size_high)
            savings_high = max(savings_low, current - size_low)
            throughput = sampling.bytes_read / seconds
            central_time = logical_bytes / max(1.0, throughput)
            estimates[profile.value] = ProfileEstimate(
                profile=profile,
                persistent_compression_algorithm="zstd",
                one_time_recompression_level=level,
                estimated_size_low_bytes=size_low,
                estimated_size_high_bytes=size_high,
                estimated_savings_low_bytes=savings_low,
                estimated_savings_high_bytes=savings_high,
                estimated_time_low_seconds=max(0.0, central_time * 0.80),
                estimated_time_high_seconds=max(0.0, central_time * 1.45),
                cpu_usage=self._cpu_usage(level),
                sample_ratio=ratio,
                estimated=True,
            )
        return estimates, auto_level

    @staticmethod
    def _choose_auto_level(
        ratios: Mapping[int, float], seconds_by_level: Mapping[int, float]
    ) -> int:
        available = [level for level in AUTO_LEVELS if level in ratios]
        if not available:
            return 3
        baseline_time = max(1e-9, seconds_by_level.get(available[0], 1e-9))
        best_level = available[0]
        best_score = -math.inf
        for level in available:
            saving = 1.0 - ratios[level]
            relative_time = seconds_by_level.get(level, baseline_time) / baseline_time
            score = saving - 0.012 * max(0.0, relative_time - 1.0)
            if score > best_score:
                best_score = score
                best_level = level
        if best_level == 9 and 6 in ratios:
            incremental_gain = ratios[6] - ratios[9]
            if incremental_gain < 0.01:
                best_level = 6
        if best_level > 3 and 3 in ratios:
            gain_over_balanced = ratios[3] - ratios[best_level]
            if gain_over_balanced < 0.008:
                best_level = 3
        return best_level

    @staticmethod
    def _unknown_estimate(
        profile: CompressionProfile, level: int
    ) -> ProfileEstimate:
        return ProfileEstimate(
            profile=profile,
            persistent_compression_algorithm="zstd",
            one_time_recompression_level=level,
            estimated_size_low_bytes=None,
            estimated_size_high_bytes=None,
            estimated_savings_low_bytes=None,
            estimated_savings_high_bytes=None,
            estimated_time_low_seconds=None,
            estimated_time_high_seconds=None,
            cpu_usage=BtrfsCompressionAnalyzer._cpu_usage(level),
            sample_ratio=None,
            estimated=False,
        )

    @staticmethod
    def _cpu_usage(level: int) -> str:
        if level <= 1:
            return "Low"
        if level <= 3:
            return "Moderate"
        if level <= 6:
            return "High"
        return "Very high"

    @staticmethod
    def _benefit(estimate: ProfileEstimate | None) -> str:
        if (
            estimate is None
            or estimate.estimated_savings_high_bytes is None
            or estimate.estimated_size_high_bytes is None
        ):
            return "Not estimated"
        denominator = (
            estimate.estimated_size_high_bytes
            + estimate.estimated_savings_high_bytes
        )
        ratio = estimate.estimated_savings_high_bytes / max(1, denominator)
        if ratio >= 0.15:
            return "High benefit"
        if ratio >= 0.05:
            return "Moderate benefit"
        return "Low benefit"

    def _detect_processes(
        self, root: Path, cancel_event: Event | None, warnings: list[str]
    ) -> Sequence[int]:
        if self._process_detector is not None:
            try:
                return self._process_detector(root, cancel_event)
            except OSError as error:
                warnings.append(f"Running-process detection failed: {error}")
                return ()
        return self._scan_proc(root, cancel_event)

    def _scan_proc(self, root: Path, cancel_event: Event | None) -> tuple[int, ...]:
        root_path = os.path.abspath(os.fspath(root))
        matches: list[int] = []
        try:
            entries = tuple(self._proc_root.iterdir())
        except OSError:
            return ()
        for process_dir in entries:
            self._check_cancel(cancel_event)
            if not process_dir.name.isdigit():
                continue
            matched = False
            for link_name in ("cwd", "exe"):
                try:
                    target = os.readlink(process_dir / link_name)
                except OSError:
                    continue
                if self._path_within(target, root_path):
                    matched = True
                    break
            if not matched:
                try:
                    raw = (process_dir / "cmdline").read_bytes()[:128 * 1024]
                except OSError:
                    raw = b""
                for argument in raw.split(b"\0"):
                    if not argument:
                        continue
                    decoded = os.fsdecode(argument)
                    if os.path.isabs(decoded) and self._path_within(
                        decoded, root_path
                    ):
                        matched = True
                        break
            if matched:
                matches.append(int(process_dir.name))
        return tuple(matches)

    @staticmethod
    def _path_within(candidate: str, root: str) -> bool:
        try:
            return os.path.commonpath((os.path.abspath(candidate), root)) == root
        except (OSError, ValueError):
            return False

    def _inspect_filesystem(
        self, root: Path, game: Game, warnings: list[str]
    ) -> FilesystemInfo:
        try:
            return self._filesystem_provider.inspect(root)
        except Exception as error:
            del game
            warnings.append(f"Filesystem detection failed: {error}")
            return FilesystemInfo(
                mount_point=root,
                filesystem=FilesystemType.UNKNOWN,
                compression_supported=False,
                device=None,
                mount_options=(),
                writable=None,
                filesystem_name=FilesystemType.UNKNOWN.value,
            )

    def _available_bytes(self, mount_point: Path, warnings: list[str]) -> int | None:
        try:
            usage = self._disk_usage_reader(mount_point)
            return max(0, int(usage.free))
        except (OSError, TypeError, ValueError, AttributeError) as error:
            warnings.append(f"Available space could not be measured: {error}")
            return None

    @staticmethod
    def _mount_compression(options: Sequence[str]) -> tuple[str | None, int | None]:
        for option in options:
            name, separator, raw_value = str(option).partition("=")
            if name.casefold() not in {"compress", "compress-force"}:
                continue
            value = raw_value.strip() if separator else "zlib"
            algorithm, level_separator, level_raw = value.partition(":")
            level: int | None = None
            if level_separator:
                try:
                    level = int(level_raw)
                except ValueError:
                    level = None
            return algorithm.casefold() or None, level
        return None, None

    @staticmethod
    def _existing_compression_state(compsize: CompsizeResult) -> str:
        if not compsize.available:
            return "unknown"
        if compsize.compression_types:
            return "compressed"
        return "uncompressed"

    def _invalid_report(
        self,
        game: Game,
        root: Path,
        *,
        path_exists: bool,
        path_is_directory: bool,
        warning: str,
        started: float,
    ) -> BtrfsAnalysisReport:
        profiles = {
            profile.value: self._unknown_estimate(
                profile, 3 if level is None else int(level)
            )
            for profile, level in PROFILE_LEVELS.items()
        }
        return BtrfsAnalysisReport(
            analyzer_version=ANALYZER_VERSION,
            game_id=game.id,
            app_id=str(game.steam_app_id or game.id),
            game_name=game.name,
            path=os.path.abspath(os.fspath(root)),
            path_exists=path_exists,
            path_is_directory=path_is_directory,
            filesystem=FilesystemType.UNKNOWN.value,
            is_btrfs=False,
            writable=False,
            mount_point="",
            filesystem_device="",
            available_bytes=None,
            logical_bytes=0,
            physical_bytes=0,
            file_count=0,
            directory_count=0,
            symlink_count=0,
            hardlink_count=0,
            permission_errors=(warning,),
            scan_complete=False,
            existing_compression_state="unknown",
            persistent_compression_algorithm=None,
            mount_compression_level=None,
            compsize=CompsizeResult(False, "Not run"),
            btrfs_du=BtrfsDuResult.unknown("Not run"),
            possible_shared_extents=None,
            game_running=False,
            running_process_ids=(),
            sampled_bytes=0,
            sampled_files=0,
            sampling_codec="not run",
            sampling_complete=False,
            selected_auto_level=3,
            profiles=profiles,
            profiles_unlocked=False,
            compression_eligible=False,
            benefit="Not estimated",
            warnings=(warning,),
            created_at=datetime.now(UTC),
            elapsed_seconds=max(0.0, self._clock() - started),
        )

    def _emit_progress(
        self,
        callback: Callable[[AnalysisProgress], None] | None,
        stage: str,
        progress: float,
        scanned_files: int,
        analyzed_bytes: int,
        started: float,
    ) -> None:
        if callback is None:
            return
        value = AnalysisProgress(
            stage=stage,
            progress=min(1.0, max(0.0, progress)),
            scanned_files=max(0, scanned_files),
            analyzed_bytes=max(0, analyzed_bytes),
            elapsed_seconds=max(0.0, self._clock() - started),
        )
        try:
            callback(value)
        except Exception:
            logger.debug("Analysis progress callback failed", exc_info=True)

    @staticmethod
    def _check_cancel(cancel_event: Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AnalysisCancelled("Compression analysis was cancelled")

    @staticmethod
    def _physical_size(file_stat: os.stat_result) -> int:
        blocks = getattr(file_stat, "st_blocks", None)
        if isinstance(blocks, int) and not isinstance(blocks, bool) and blocks >= 0:
            return blocks * 512
        return max(0, file_stat.st_size)

    @staticmethod
    def _file_category(path: Path) -> str:
        suffix = path.suffix.casefold()
        if suffix in _TEXT_EXTENSIONS:
            return "text"
        if suffix in _PRECOMPRESSED_EXTENSIONS:
            return "precompressed"
        if suffix in _EXECUTABLE_EXTENSIONS:
            return "executable"
        return "other"

    @staticmethod
    def _error(path: Path, error: OSError) -> str:
        return f"{path}: {error}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _optional_int(value: Any) -> int | None:
    return max(0, int(value)) if _is_number(value) else None


def _optional_float(value: Any) -> float | None:
    if not _is_number(value):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _profile(value: str) -> CompressionProfile:
    normalized = value.strip().casefold()
    for profile in CompressionProfile:
        if normalized in {profile.value.casefold(), profile.name.casefold()}:
            return profile
    return CompressionProfile.AUTO


__all__ = [
    "ANALYZER_VERSION",
    "AUTO_LEVELS",
    "AnalysisCancelled",
    "AnalysisLimits",
    "AnalysisProgress",
    "BtrfsAnalysisReport",
    "BtrfsCompressionAnalyzer",
    "BtrfsDuResult",
    "CompsizeResult",
    "InvalidAnalysisPath",
    "PROFILE_LEVELS",
    "ProfileEstimate",
]
