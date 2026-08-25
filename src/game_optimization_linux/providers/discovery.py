"""Shared diagnostics and normalization for launcher metadata providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable

from game_optimization_linux.models import (
    FilesystemType,
    GameStatus,
)

from .base import FilesystemProvider


@dataclass(frozen=True, slots=True)
class LauncherRootDiagnostic:
    path: Path
    variant: str
    state: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class LauncherScanReport:
    launcher: str
    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float
    roots: tuple[LauncherRootDiagnostic, ...] = ()
    records_seen: int = 0
    malformed_records: int = 0
    duplicate_roots: int = 0
    duplicate_games: int = 0
    games_found: int = 0
    errors: tuple[str, ...] = ()

    @classmethod
    def empty(cls, launcher: str) -> "LauncherScanReport":
        now = datetime.now(UTC)
        return cls(launcher, now, now, 0.0)

    @property
    def installation_found(self) -> bool:
        return any(root.state == "found" for root in self.roots)

    @property
    def inaccessible_paths(self) -> tuple[Path, ...]:
        return tuple(root.path for root in self.roots if root.state == "inaccessible")


@dataclass(slots=True)
class ScanReportBuilder:
    launcher: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    timer: float = field(default_factory=perf_counter)
    roots: list[LauncherRootDiagnostic] = field(default_factory=list)
    records_seen: int = 0
    malformed_records: int = 0
    duplicate_roots: int = 0
    duplicate_games: int = 0
    errors: list[str] = field(default_factory=list)

    def finish(self, games_found: int) -> LauncherScanReport:
        return LauncherScanReport(
            launcher=self.launcher,
            started_at=self.started_at,
            completed_at=datetime.now(UTC),
            elapsed_seconds=max(0.0, perf_counter() - self.timer),
            roots=tuple(self.roots),
            records_seen=self.records_seen,
            malformed_records=self.malformed_records,
            duplicate_roots=self.duplicate_roots,
            duplicate_games=self.duplicate_games,
            games_found=games_found,
            errors=tuple(self.errors),
        )


def inspect_install_path(
    filesystem_provider: FilesystemProvider,
    install_path: Path,
) -> tuple[
    FilesystemType,
    str,
    bool,
    GameStatus,
    Path | None,
    str | None,
    tuple[str, ...],
    bool | None,
]:
    """Use the shared filesystem provider for normalized launcher records."""

    try:
        available = install_path.is_dir()
    except OSError:
        available = False
    status = GameStatus.READY if available else GameStatus.MISSING_FILES
    try:
        info = filesystem_provider.inspect(install_path)
    except Exception:
        return (
            FilesystemType.UNKNOWN,
            "unknown",
            False,
            status,
            None,
            None,
            (),
            None,
        )
    return (
        info.filesystem,
        info.filesystem_name or info.filesystem.value,
        bool(info.compression_supported and available),
        status,
        info.mount_point,
        info.device,
        tuple(info.mount_options),
        info.writable if available else False,
    )


def first_existing_file(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path.resolve(strict=False)
        except OSError:
            continue
    return None


__all__ = [
    "LauncherRootDiagnostic",
    "LauncherScanReport",
    "ScanReportBuilder",
    "first_existing_file",
    "inspect_install_path",
]
