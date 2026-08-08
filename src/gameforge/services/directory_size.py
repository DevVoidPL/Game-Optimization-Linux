"""Cancelable, read-only directory size scanning."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from threading import Event


@dataclass(frozen=True, slots=True)
class DirectorySizeResult:
    """Sizes collected by :class:`DirectorySizeScanner`.

    ``physical_bytes`` uses Linux's 512-byte ``st_blocks`` units.  ``complete``
    is false only when cancellation stopped traversal; recoverable filesystem
    errors are exposed separately while the rest of the tree is scanned.
    """

    logical_bytes: int
    physical_bytes: int
    errors: tuple[str, ...]
    complete: bool


class DirectorySizeScanner:
    """Measure regular files without following symbolic links."""

    _BLOCK_UNIT = 512

    def scan(
        self, path: Path, cancel_event: Event | None = None
    ) -> DirectorySizeResult:
        root = Path(path)
        logical_bytes = 0
        physical_bytes = 0
        errors: list[str] = []
        seen_files: set[tuple[int, int]] = set()
        seen_directories: set[tuple[int, int]] = set()
        pending = [root]

        while pending:
            if self._cancelled(cancel_event):
                return DirectorySizeResult(
                    logical_bytes, physical_bytes, tuple(errors), False
                )

            current = pending.pop()
            try:
                current_stat = os.lstat(current)
            except OSError as error:
                errors.append(self._format_error(current, error))
                continue

            mode = current_stat.st_mode
            if stat.S_ISLNK(mode):
                continue
            if stat.S_ISREG(mode):
                identity = (current_stat.st_dev, current_stat.st_ino)
                if identity not in seen_files:
                    seen_files.add(identity)
                    logical_bytes += current_stat.st_size
                    physical_bytes += self._physical_size(current_stat)
                continue
            if not stat.S_ISDIR(mode):
                continue

            identity = (current_stat.st_dev, current_stat.st_ino)
            if identity in seen_directories:
                continue
            seen_directories.add(identity)

            try:
                with os.scandir(current) as entries:
                    while True:
                        if self._cancelled(cancel_event):
                            return DirectorySizeResult(
                                logical_bytes, physical_bytes, tuple(errors), False
                            )
                        try:
                            entry = next(entries)
                        except StopIteration:
                            break
                        except OSError as error:
                            errors.append(self._format_error(current, error))
                            break
                        pending.append(Path(entry.path))
            except OSError as error:
                errors.append(self._format_error(current, error))

        return DirectorySizeResult(
            logical_bytes=logical_bytes,
            physical_bytes=physical_bytes,
            errors=tuple(errors),
            complete=True,
        )

    @staticmethod
    def _cancelled(cancel_event: Event | None) -> bool:
        return cancel_event is not None and cancel_event.is_set()

    @classmethod
    def _physical_size(cls, file_stat: os.stat_result) -> int:
        blocks = getattr(file_stat, "st_blocks", None)
        if isinstance(blocks, int) and not isinstance(blocks, bool) and blocks >= 0:
            return blocks * cls._BLOCK_UNIT
        return file_stat.st_size

    @staticmethod
    def _format_error(path: Path, error: OSError) -> str:
        return f"{path}: {error}"
