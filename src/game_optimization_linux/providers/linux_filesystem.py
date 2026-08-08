"""Read-only Linux filesystem discovery.

The provider prefers util-linux ``findmnt`` because its JSON output avoids
having to interpret column-oriented command output.  ``/proc/self/mountinfo``
is used as a local, read-only fallback when the command is unavailable or its
output cannot be used.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Sequence

from game_optimization_linux.models.enums import FilesystemType
from game_optimization_linux.models.game import Game
from game_optimization_linux.models.system import FilesystemInfo

from .base import FilesystemProvider


logger = logging.getLogger(__name__)

_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
_DEFAULT_MOUNTINFO = Path("/proc/self/mountinfo")
_PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "proc",
        "pstore",
        "ramfs",
        "rpc_pipefs",
        "securityfs",
        "selinuxfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)
_SYSTEM_TOP_LEVEL_TARGETS = frozenset(
    {"boot", "dev", "etc", "opt", "proc", "run", "sys", "tmp", "usr", "var"}
)


@dataclass(frozen=True, slots=True)
class _MountRecord:
    target: Path
    source: str | None
    filesystem_name: str
    options: tuple[str, ...]
    size_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None


class LinuxFilesystemProvider(FilesystemProvider):
    """Inspect Linux mount metadata without changing the host system."""

    def __init__(
        self,
        mountinfo_path: Path = _DEFAULT_MOUNTINFO,
        *,
        timeout: float = 2.0,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a positive number")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._mountinfo_path = Path(mountinfo_path)
        self._timeout = float(timeout)

    def inspect(self, path: Path) -> FilesystemInfo:
        """Return information for *path* or its nearest existing parent."""

        query_path = self._nearest_existing_path(Path(path))
        records = self._findmnt_records(query_path)
        record = self._longest_match(records, query_path)
        if record is None:
            records = self._mountinfo_records()
            record = self._longest_match(records, query_path)
        if record is None:
            logger.warning("Could not determine filesystem for %s", query_path)
            return self._unknown_info(query_path)
        return self._to_info(record, query_path)

    def for_game(self, game: Game) -> FilesystemInfo:
        return self.inspect(game.install_path)

    def list_filesystems(
        self,
        *,
        game_paths: Sequence[Path] = (),
        show_system_mounts: bool = False,
    ) -> Sequence[FilesystemInfo]:
        """List useful mounted filesystems from the process mount namespace.

        The default view contains the root filesystem, a separate ``/home``,
        mounts containing game-library paths, and user-facing physical mounts.
        Kernel and service pseudo-filesystems remain available through the
        explicit ``show_system_mounts`` diagnostic view.
        """

        records = self._findmnt_records()
        if not records:
            records = self._mountinfo_records()

        # A target can appear more than once for stacked mounts.  The last
        # record describes the mount currently visible at that path.
        by_target: dict[str, _MountRecord] = {}
        for record in records:
            by_target[self._normal_path(record.target)] = record
        visible_records = self._visible_records(
            tuple(by_target.values()),
            game_paths=game_paths,
            show_system_mounts=show_system_mounts,
        )
        return tuple(
            self._to_info(record, record.target, check_path_access=False)
            for record in visible_records
        )

    def _findmnt_records(self, target: Path | None = None) -> tuple[_MountRecord, ...]:
        command = [
            "findmnt",
            "--json",
            "--bytes",
            "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,USED,AVAIL",
        ]
        if target is not None:
            command.extend(("--target", os.fspath(target)))

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            logger.debug("findmnt is unavailable: %s", error)
            return ()
        if completed.returncode != 0:
            logger.debug("findmnt exited with status %s", completed.returncode)
            return ()

        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            logger.debug("findmnt returned invalid JSON: %s", error)
            return ()
        if not isinstance(payload, dict):
            return ()

        raw_filesystems = payload.get("filesystems")
        if not isinstance(raw_filesystems, list):
            return ()
        records: list[_MountRecord] = []
        for raw in self._flatten_findmnt(raw_filesystems):
            record = self._record_from_findmnt(raw)
            if record is not None:
                records.append(record)
        return tuple(records)

    @classmethod
    def _flatten_findmnt(
        cls, entries: Iterable[object]
    ) -> Iterable[dict[str, Any]]:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            yield entry
            children = entry.get("children")
            if isinstance(children, list):
                yield from cls._flatten_findmnt(children)

    @classmethod
    def _record_from_findmnt(cls, raw: dict[str, Any]) -> _MountRecord | None:
        target_value = cls._mapping_value(raw, "target")
        filesystem_value = cls._mapping_value(raw, "fstype")
        if not isinstance(target_value, str) or not target_value:
            return None
        if not isinstance(filesystem_value, str) or not filesystem_value.strip():
            return None

        source_value = cls._mapping_value(raw, "source")
        source = source_value if isinstance(source_value, str) else None
        if source in {"", "-", "none"}:
            source = None

        return _MountRecord(
            target=Path(cls._decode_mount_field(target_value)),
            source=cls._decode_mount_field(source) if source is not None else None,
            filesystem_name=filesystem_value.strip(),
            options=cls._parse_options(cls._mapping_value(raw, "options")),
            size_bytes=cls._parse_size(cls._mapping_value(raw, "size")),
            used_bytes=cls._parse_size(cls._mapping_value(raw, "used")),
            available_bytes=cls._parse_size(cls._mapping_value(raw, "avail")),
        )

    @staticmethod
    def _mapping_value(mapping: dict[str, Any], name: str) -> Any:
        # util-linux currently emits lower-case keys.  Accept upper-case keys
        # as well so fixtures and older versions remain harmless.
        return mapping.get(name, mapping.get(name.upper()))

    def _mountinfo_records(self) -> tuple[_MountRecord, ...]:
        try:
            lines = self._mountinfo_path.read_text(
                encoding="utf-8", errors="surrogateescape"
            ).splitlines()
        except OSError as error:
            logger.debug("cannot read %s: %s", self._mountinfo_path, error)
            return ()

        records: list[_MountRecord] = []
        for line in lines:
            record = self._record_from_mountinfo(line)
            if record is not None:
                records.append(record)
        return tuple(records)

    @classmethod
    def _record_from_mountinfo(cls, line: str) -> _MountRecord | None:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            return None
        # See proc_pid_mountinfo(5): fields 5 and 6 precede optional fields;
        # filesystem type and source are the second and third fields after '-'.
        if len(fields) < 6 or separator + 2 >= len(fields):
            return None

        target = cls._decode_mount_field(fields[4])
        filesystem_name = fields[separator + 1]
        source_value = cls._decode_mount_field(fields[separator + 2])
        source = None if source_value in {"", "-", "none"} else source_value

        option_values = list(cls._parse_options(fields[5]))
        if separator + 3 < len(fields):
            option_values.extend(cls._parse_options(fields[separator + 3]))
        options = tuple(dict.fromkeys(option_values))
        return _MountRecord(
            target=Path(target),
            source=source,
            filesystem_name=filesystem_name,
            options=options,
        )

    @staticmethod
    def _parse_options(raw: Any) -> tuple[str, ...]:
        if isinstance(raw, str):
            values = raw.split(",")
        elif isinstance(raw, (list, tuple)):
            values = (str(value) for value in raw)
        else:
            return ()
        return tuple(value.strip() for value in values if value.strip())

    @staticmethod
    def _parse_size(raw: Any) -> int | None:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, int):
            return raw if raw >= 0 else None
        if isinstance(raw, float):
            return int(raw) if raw >= 0 and raw.is_integer() else None
        if not isinstance(raw, str):
            return None

        value = raw.strip()
        if not value or value == "-":
            return None
        if value.isdecimal():
            return int(value)
        match = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*([KMGTPE]?)(?:i?B)?", value, re.I
        )
        if match is None:
            return None
        prefix = match.group(2).upper()
        exponent = "KMGTPE".find(prefix) + 1 if prefix else 0
        multiplier = 1024**exponent if exponent else 1
        return int(float(match.group(1)) * multiplier)

    @staticmethod
    def _decode_mount_field(value: str) -> str:
        return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)

    @classmethod
    def _longest_match(
        cls, records: Sequence[_MountRecord], path: Path
    ) -> _MountRecord | None:
        query = cls._normal_path(path)
        matching: list[tuple[int, int, _MountRecord]] = []
        for index, record in enumerate(records):
            mount = cls._normal_path(record.target)
            try:
                belongs_to_mount = os.path.commonpath((query, mount)) == mount
            except (OSError, ValueError):
                belongs_to_mount = False
            if belongs_to_mount:
                matching.append((len(mount), index, record))
        if not matching:
            return None
        return max(matching, key=lambda item: (item[0], item[1]))[2]

    @staticmethod
    def _normal_path(path: Path) -> str:
        return os.path.normpath(os.path.abspath(os.fspath(path)))

    @classmethod
    def _nearest_existing_path(cls, path: Path) -> Path:
        candidate = Path(cls._normal_path(path.expanduser()))
        while True:
            try:
                if candidate.exists():
                    try:
                        return candidate.resolve(strict=True)
                    except OSError:
                        return candidate
            except OSError:
                pass
            parent = candidate.parent
            if parent == candidate:
                return candidate
            candidate = parent

    @staticmethod
    def _filesystem_type(filesystem_name: str) -> FilesystemType:
        normalized = filesystem_name.strip().casefold()
        if normalized == "btrfs":
            return FilesystemType.BTRFS
        if normalized == "ext4":
            return FilesystemType.EXT4
        if normalized == "xfs":
            return FilesystemType.XFS
        if normalized in {"ntfs", "ntfs3", "fuseblk"}:
            return FilesystemType.NTFS
        if normalized:
            return FilesystemType.OTHER
        return FilesystemType.UNKNOWN

    @classmethod
    def _visible_records(
        cls,
        records: Sequence[_MountRecord],
        *,
        game_paths: Sequence[Path],
        show_system_mounts: bool,
    ) -> tuple[_MountRecord, ...]:
        if show_system_mounts:
            return tuple(sorted(records, key=cls._record_sort_key))

        candidates = tuple(
            record
            for record in records
            if not cls._is_pseudo_filesystem(record.filesystem_name)
        )
        selected: dict[str, _MountRecord] = {}

        for target in (Path("/"), Path("/home")):
            record = next(
                (
                    item
                    for item in reversed(candidates)
                    if cls._normal_path(item.target) == cls._normal_path(target)
                ),
                None,
            )
            if record is not None:
                selected[cls._normal_path(record.target)] = record

        for path in game_paths:
            record = cls._longest_match(candidates, Path(path))
            if record is not None:
                selected[cls._normal_path(record.target)] = record

        for record in candidates:
            if cls._is_user_facing_mount(record):
                selected[cls._normal_path(record.target)] = record

        return tuple(sorted(selected.values(), key=cls._record_sort_key))

    @staticmethod
    def _is_pseudo_filesystem(filesystem_name: str) -> bool:
        return filesystem_name.strip().casefold() in _PSEUDO_FILESYSTEMS

    @classmethod
    def _is_user_facing_mount(cls, record: _MountRecord) -> bool:
        target = cls._normal_path(record.target)
        if target in {"/", "/home"}:
            return True

        home = cls._normal_path(Path.home())
        user_prefixes = (home, "/media", "/mnt", "/run/media")
        if any(
            target == prefix or target.startswith(prefix + os.sep)
            for prefix in user_prefixes
        ):
            return True

        source = (record.source or "").casefold()
        physical = source.startswith("/dev/") or source.startswith(
            ("uuid=", "label=", "partuuid=", "partlabel=")
        )
        relative_parts = Path(target).parts[1:]
        return bool(
            physical
            and len(relative_parts) == 1
            and relative_parts[0] not in _SYSTEM_TOP_LEVEL_TARGETS
        )

    @classmethod
    def _record_sort_key(cls, record: _MountRecord) -> tuple[int, str]:
        target = cls._normal_path(record.target)
        priority = 0 if target == "/" else 1 if target == "/home" else 2
        return priority, target.casefold()

    @staticmethod
    def _space_from_statvfs(path: Path) -> tuple[int | None, int | None, int | None]:
        try:
            values = os.statvfs(path)
        except OSError:
            return None, None, None
        block_size = values.f_frsize or values.f_bsize
        if block_size <= 0:
            return None, None, None
        size = max(0, int(values.f_blocks) * block_size)
        used = max(0, size - int(values.f_bfree) * block_size)
        available = max(0, int(values.f_bavail) * block_size)
        return size, used, available

    @classmethod
    def _to_info(
        cls,
        record: _MountRecord,
        writable_path: Path,
        *,
        check_path_access: bool = True,
    ) -> FilesystemInfo:
        filesystem = cls._filesystem_type(record.filesystem_name)
        option_set = {option.casefold() for option in record.options}
        mount_writable = (
            False if "ro" in option_set else True if "rw" in option_set else None
        )
        if check_path_access or mount_writable is None:
            try:
                path_writable = os.access(writable_path, os.W_OK)
            except OSError:
                path_writable = False
            writable = path_writable and mount_writable is not False
        else:
            writable = mount_writable

        fallback_size: int | None = None
        fallback_used: int | None = None
        fallback_available: int | None = None
        if (
            record.size_bytes is None
            or record.used_bytes is None
            or record.available_bytes is None
        ):
            fallback_size, fallback_used, fallback_available = cls._space_from_statvfs(
                record.target
            )
        return FilesystemInfo(
            mount_point=record.target,
            filesystem=filesystem,
            compression_supported=filesystem is FilesystemType.BTRFS,
            label=record.source or record.target.name or os.fspath(record.target),
            device=record.source,
            mount_options=record.options,
            writable=writable,
            filesystem_name=record.filesystem_name or FilesystemType.UNKNOWN.value,
            size_bytes=(
                record.size_bytes if record.size_bytes is not None else fallback_size
            ),
            used_bytes=(
                record.used_bytes if record.used_bytes is not None else fallback_used
            ),
            available_bytes=(
                record.available_bytes
                if record.available_bytes is not None
                else fallback_available
            ),
        )

    @staticmethod
    def _unknown_info(query_path: Path) -> FilesystemInfo:
        try:
            writable = os.access(query_path, os.W_OK)
        except OSError:
            writable = False
        return FilesystemInfo(
            mount_point=query_path,
            filesystem=FilesystemType.UNKNOWN,
            compression_supported=False,
            label="Unknown filesystem",
            writable=writable,
            filesystem_name=FilesystemType.UNKNOWN.value,
        )
