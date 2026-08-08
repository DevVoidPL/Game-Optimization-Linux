"""Validated archive readers used by the OptiScaler installer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
from typing import Final
from zipfile import BadZipFile, ZipFile, ZipInfo, is_zipfile

import py7zr


MAX_ARCHIVE_FILES: Final = 4096
MAX_ARCHIVE_BYTES: Final = 4 * 1024**3
MAX_MEMBER_BYTES: Final = 1024**3
BUNDLED_7ZIP_HELPER: Final = Path("/app/libexec/game-optimization-7zz")
SEVENZIP_EXTRACTION_TIMEOUT_SECONDS: Final = 300


class ArchiveReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    archive_name: str
    relative_path: str
    size: int
    is_directory: bool = False
    is_symlink: bool = False


def _safe_relative_path(raw_name: str) -> str:
    name = str(raw_name or "").replace("\\", "/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or any(":" in part for part in path.parts)
        or "\0" in name
    ):
        raise ArchiveReadError(f"unsafe archive path: {raw_name}")
    normalized = path.as_posix().rstrip("/")
    if not normalized or normalized == ".":
        raise ArchiveReadError(f"unsafe archive path: {raw_name}")
    return normalized


class ArchiveReader(ABC):
    """Common, format-independent interface with mandatory validation."""

    format_name: str

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve(strict=False)
        if not self.path.is_file():
            raise ArchiveReadError("OptiScaler archive does not exist")
        self._verify_format()
        self._entries = self._validate_entries(self._read_entries())

    @property
    def entries(self) -> tuple[ArchiveEntry, ...]:
        return self._entries

    @abstractmethod
    def _verify_format(self) -> None: ...

    @abstractmethod
    def _read_entries(self) -> tuple[ArchiveEntry, ...]: ...

    @abstractmethod
    def _extract_validated(self, destination: Path) -> None: ...

    @staticmethod
    def _validate_entries(
        entries: tuple[ArchiveEntry, ...],
    ) -> tuple[ArchiveEntry, ...]:
        if len(entries) > MAX_ARCHIVE_FILES:
            raise ArchiveReadError("OptiScaler archive contains too many files")
        normalized: list[ArchiveEntry] = []
        seen: set[str] = set()
        total = 0
        for entry in entries:
            relative = _safe_relative_path(entry.archive_name)
            duplicate_key = relative.casefold()
            if duplicate_key in seen:
                raise ArchiveReadError(f"duplicate archive path: {relative}")
            seen.add(duplicate_key)
            if entry.is_symlink:
                raise ArchiveReadError(f"symbolic links are not allowed: {relative}")
            size = int(entry.size)
            if size < 0 or size > MAX_MEMBER_BYTES:
                raise ArchiveReadError(f"archive member is too large: {relative}")
            if not entry.is_directory:
                total += size
                if total > MAX_ARCHIVE_BYTES:
                    raise ArchiveReadError("OptiScaler archive is too large")
            normalized.append(
                ArchiveEntry(
                    archive_name=entry.archive_name,
                    relative_path=relative,
                    size=size,
                    is_directory=entry.is_directory,
                    is_symlink=False,
                )
            )
        return tuple(normalized)

    def extract_to(self, destination: Path) -> None:
        target = Path(destination).resolve(strict=True)
        if not target.is_dir() or any(target.iterdir()):
            raise ArchiveReadError("archive destination must be a new empty directory")
        self._extract_validated(target)
        self._validate_extracted_tree(target)

    def _validate_extracted_tree(self, root: Path) -> None:
        expected_files = {
            entry.relative_path: entry.size
            for entry in self.entries
            if not entry.is_directory
        }
        actual_files: set[str] = set()
        file_count = 0
        total = 0
        for directory, names, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            for name in (*names, *files):
                path = parent / name
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise ArchiveReadError(
                        f"symbolic links are not allowed: {path.relative_to(root)}"
                    )
                try:
                    path.resolve(strict=True).relative_to(root)
                except ValueError as error:
                    raise ArchiveReadError("extracted path escapes the temporary directory") from error
                if stat.S_ISREG(metadata.st_mode):
                    relative = path.relative_to(root).as_posix()
                    if relative not in expected_files:
                        raise ArchiveReadError(
                            f"archive extracted an unexpected file: {relative}"
                        )
                    if metadata.st_size != expected_files[relative]:
                        raise ArchiveReadError(
                            f"extracted file size does not match the archive: {relative}"
                        )
                    actual_files.add(relative)
                    file_count += 1
                    total += metadata.st_size
                elif not stat.S_ISDIR(metadata.st_mode):
                    raise ArchiveReadError(
                        f"unsupported extracted entry: {path.relative_to(root)}"
                    )
                if file_count > MAX_ARCHIVE_FILES or total > MAX_ARCHIVE_BYTES:
                    raise ArchiveReadError("extracted archive exceeds the safety limits")
        missing = sorted(set(expected_files) - actual_files)
        if missing:
            raise ArchiveReadError(
                f"archive extraction did not produce a validated file: {missing[0]}"
            )


class ZipArchiveReader(ArchiveReader):
    format_name = "ZIP"

    def _verify_format(self) -> None:
        if self.path.suffix.casefold() != ".zip" or not is_zipfile(self.path):
            raise ArchiveReadError("file extension and archive format do not match ZIP")

    @staticmethod
    def _is_symlink(info: ZipInfo) -> bool:
        mode = (info.external_attr >> 16) & 0xFFFF
        return stat.S_ISLNK(mode)

    def _read_entries(self) -> tuple[ArchiveEntry, ...]:
        try:
            with ZipFile(self.path) as archive:
                return tuple(
                    ArchiveEntry(
                        archive_name=info.filename,
                        relative_path="",
                        size=info.file_size,
                        is_directory=info.is_dir(),
                        is_symlink=self._is_symlink(info),
                    )
                    for info in archive.infolist()
                )
        except (BadZipFile, OSError) as error:
            raise ArchiveReadError("OptiScaler archive is not a valid ZIP file") from error

    def _extract_validated(self, destination: Path) -> None:
        try:
            with ZipFile(self.path) as archive:
                entries = {entry.archive_name: entry for entry in self.entries}
                for info in archive.infolist():
                    entry = entries[info.filename]
                    target = destination / PurePosixPath(entry.relative_path)
                    if entry.is_directory:
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
        except (BadZipFile, OSError, KeyError) as error:
            raise ArchiveReadError("ZIP extraction failed") from error


class SevenZipArchiveReader(ArchiveReader):
    format_name = "7Z"

    def _verify_format(self) -> None:
        try:
            matches = self.path.suffix.casefold() == ".7z" and py7zr.is_7zfile(self.path)
        except OSError as error:
            raise ArchiveReadError("could not inspect the 7z archive") from error
        if not matches:
            raise ArchiveReadError("file extension and archive format do not match 7z")

    def _read_entries(self) -> tuple[ArchiveEntry, ...]:
        try:
            with py7zr.SevenZipFile(
                self.path, "r", max_extract_size=MAX_ARCHIVE_BYTES
            ) as archive:
                if archive.needs_password():
                    raise ArchiveReadError("encrypted 7z archives are not supported")
                return tuple(
                    ArchiveEntry(
                        archive_name=info.filename,
                        relative_path="",
                        size=info.uncompressed,
                        is_directory=info.is_directory,
                        is_symlink=info.is_symlink,
                    )
                    for info in archive.list()
                )
        except ArchiveReadError:
            raise
        except (py7zr.Bad7zFile, OSError, ValueError) as error:
            raise ArchiveReadError("OptiScaler archive is not a valid 7z file") from error

    def _extract_with_bundled_helper(self, destination: Path) -> None:
        helper = BUNDLED_7ZIP_HELPER
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise ArchiveReadError(
                "this 7z archive uses BCJ2 and the bundled extractor is unavailable"
            )
        try:
            completed = subprocess.run(
                [
                    str(helper),
                    "x",
                    "-y",
                    "-bb0",
                    "-bd",
                    f"-o{destination}",
                    str(self.path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SEVENZIP_EXTRACTION_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ArchiveReadError("bundled 7z extraction failed") from error
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise ArchiveReadError(
                f"bundled 7z extraction exited with status {completed.returncode}{suffix}"
            )

    def _extract_validated(self, destination: Path) -> None:
        # Always decode into a private sibling directory first. The target is
        # populated only after the result exactly matches the prevalidated
        # entry list, regardless of whether py7zr or the bundled BCJ2-capable
        # extractor performed the decoding.
        with tempfile.TemporaryDirectory(
            prefix="game-optimization-7z-", dir=destination.parent
        ) as raw_staging:
            staging = Path(raw_staging)
            try:
                with py7zr.SevenZipFile(
                    self.path, "r", max_extract_size=MAX_ARCHIVE_BYTES
                ) as archive:
                    archive.extractall(path=staging)
            except py7zr.exceptions.UnsupportedCompressionMethodError:
                shutil.rmtree(staging)
                staging.mkdir(mode=0o700)
                self._extract_with_bundled_helper(staging)
            except (py7zr.Bad7zFile, OSError, ValueError) as error:
                raise ArchiveReadError("7z extraction failed") from error
            self._validate_extracted_tree(staging)
            for child in staging.iterdir():
                shutil.move(str(child), destination / child.name)


def open_archive(path: Path) -> ArchiveReader:
    suffix = Path(path).suffix.casefold()
    if suffix == ".zip":
        return ZipArchiveReader(Path(path))
    if suffix == ".7z":
        return SevenZipArchiveReader(Path(path))
    raise ArchiveReadError("this version supports local ZIP and 7z archives")


__all__ = [
    "ArchiveEntry",
    "ArchiveReadError",
    "ArchiveReader",
    "BUNDLED_7ZIP_HELPER",
    "SevenZipArchiveReader",
    "ZipArchiveReader",
    "open_archive",
]
