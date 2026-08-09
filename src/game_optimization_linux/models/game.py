"""Game and feature-specific value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

from .enums import (
    BackupStatus,
    CompressionProfile,
    FilesystemType,
    GameStatus,
    Launcher,
    OptimizationProfile,
    SizeScanStatus,
    TaskStatus,
    TextureCompatibility,
    TextureMode,
    TextureScope,
)


def _require_non_negative(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field_name} must be a finite non-negative number")


def _require_positive(value: float, field_name: str) -> None:
    _require_non_negative(value, field_name)
    if value == 0:
        raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class Game:
    """A launcher-neutral description of an installed game.

    Demo paths are descriptive only. Real launcher providers may inspect paths
    read-only to determine installation, size, and filesystem metadata.
    """

    id: str
    name: str
    launcher: Launcher
    install_path: Path
    logical_size_gb: float
    physical_size_gb: float
    filesystem: FilesystemType
    compression_available: bool
    saved_space_gb: float = 0.0
    last_task_status: TaskStatus | None = None
    status: GameStatus = GameStatus.READY
    # ``cover_asset`` remains the legacy preferred image. Shape-specific paths
    # let QML avoid reusing a portrait capsule in horizontal layouts.
    cover_asset: str = ""
    portrait_artwork_path: Path | None = None
    header_artwork_path: Path | None = None
    fallback_artwork_path: Path | None = None
    active_optimization_profile: OptimizationProfile = OptimizationProfile.BALANCED
    backup_status: BackupStatus = BackupStatus.NOT_DETECTED
    texture_compatibility: TextureCompatibility = TextureCompatibility.NOT_CHECKED
    has_anticheat: bool = False
    steam_app_id: str | None = None
    library_path: Path | None = None
    data_source: str = "Demo"
    last_scanned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_updated_at: datetime | None = None
    language: str | None = None
    state_flags: int | None = None
    steam_build_id: str | None = None
    steam_manifest_path: Path | None = None
    steam_manifest_mtime_ns: int | None = None
    steam_manifest_size_bytes: int | None = None
    steam_size_on_disk_bytes: int | None = None
    update_in_progress: bool = False
    size_scan_status: SizeScanStatus = SizeScanStatus.NOT_REQUESTED
    size_scan_error: str | None = None
    filesystem_name: str = ""
    mount_point: Path | None = None
    filesystem_device: str | None = None
    mount_options: tuple[str, ...] = ()
    is_writable: bool | None = None
    is_steam_tool: bool = False
    library_available: bool = True
    executable_path: str = ""
    executable_resolution: str = "not_scanned"
    executable_candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("game id cannot be empty")
        if not self.name.strip():
            raise ValueError("game name cannot be empty")
        _require_non_negative(self.logical_size_gb, "logical_size_gb")
        _require_non_negative(self.physical_size_gb, "physical_size_gb")
        _require_non_negative(self.saved_space_gb, "saved_space_gb")
        if self.state_flags is not None and (
            isinstance(self.state_flags, bool)
            or not isinstance(self.state_flags, int)
            or self.state_flags < 0
        ):
            raise ValueError("state_flags must be a non-negative integer")
        if self.steam_build_id is not None and (
            not isinstance(self.steam_build_id, str)
            or not self.steam_build_id.strip()
        ):
            raise ValueError("steam_build_id must be a non-empty string")
        if self.steam_manifest_path is not None and not isinstance(
            self.steam_manifest_path, Path
        ):
            raise ValueError("steam_manifest_path must be a Path")
        for value, field_name in (
            (self.steam_manifest_mtime_ns, "steam_manifest_mtime_ns"),
            (self.steam_manifest_size_bytes, "steam_manifest_size_bytes"),
            (self.steam_size_on_disk_bytes, "steam_size_on_disk_bytes"),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.update_in_progress, bool):
            raise ValueError("update_in_progress must be a boolean")
        if not isinstance(self.is_steam_tool, bool):
            raise ValueError("is_steam_tool must be a boolean")
        if not isinstance(self.library_available, bool):
            raise ValueError("library_available must be a boolean")
        if not isinstance(self.executable_path, str):
            raise ValueError("executable_path must be a string")
        if not isinstance(self.executable_resolution, str):
            raise ValueError("executable_resolution must be a string")
        if not isinstance(self.executable_candidates, tuple) or not all(
            isinstance(value, str) for value in self.executable_candidates
        ):
            raise ValueError("executable_candidates must be a tuple of strings")

    @property
    def size_label(self) -> str:
        return f"{self.logical_size_gb:.1f} GB"

    @property
    def saved_space_label(self) -> str:
        return f"{self.saved_space_gb:.1f} GB"

    @property
    def source(self) -> str:
        if self.launcher is Launcher.STEAM:
            return "steam"
        if self.data_source.casefold() == "local":
            return "local"
        return self.data_source.casefold() or "manual"

    def to_dict(self) -> dict[str, Any]:
        """Return primitives suitable for QVariant/JSON conversion."""

        return {
            "id": self.id,
            "name": self.name,
            "launcher": self.launcher.value,
            "install_path": str(self.install_path),
            "logical_size_gb": self.logical_size_gb,
            "physical_size_gb": self.physical_size_gb,
            "filesystem": self.filesystem.value,
            "compression_available": self.compression_available,
            "saved_space_gb": self.saved_space_gb,
            "last_task_status": (
                self.last_task_status.value if self.last_task_status is not None else None
            ),
            "status": self.status.value,
            "cover_asset": self.cover_asset,
            "portrait_artwork_path": (
                str(self.portrait_artwork_path)
                if self.portrait_artwork_path is not None
                else None
            ),
            "header_artwork_path": (
                str(self.header_artwork_path)
                if self.header_artwork_path is not None
                else None
            ),
            "fallback_artwork_path": (
                str(self.fallback_artwork_path)
                if self.fallback_artwork_path is not None
                else None
            ),
            "active_optimization_profile": self.active_optimization_profile.value,
            "backup_status": self.backup_status.value,
            "texture_compatibility": self.texture_compatibility.value,
            "has_anticheat": self.has_anticheat,
            "steam_app_id": self.steam_app_id,
            "library_path": (
                str(self.library_path) if self.library_path is not None else None
            ),
            "data_source": self.data_source,
            "source": self.source,
            "last_scanned_at": self.last_scanned_at.isoformat(),
            "last_updated_at": (
                self.last_updated_at.isoformat()
                if self.last_updated_at is not None
                else None
            ),
            "language": self.language,
            "state_flags": self.state_flags,
            "steam_build_id": self.steam_build_id,
            "steam_manifest_path": (
                str(self.steam_manifest_path)
                if self.steam_manifest_path is not None
                else None
            ),
            "steam_manifest_mtime_ns": self.steam_manifest_mtime_ns,
            "steam_manifest_size_bytes": self.steam_manifest_size_bytes,
            "steam_size_on_disk_bytes": self.steam_size_on_disk_bytes,
            "update_in_progress": self.update_in_progress,
            "size_scan_status": self.size_scan_status.value,
            "size_scan_error": self.size_scan_error,
            "filesystem_name": self.filesystem_name,
            "mount_point": (
                str(self.mount_point) if self.mount_point is not None else None
            ),
            "filesystem_device": self.filesystem_device,
            "mount_options": list(self.mount_options),
            "is_writable": self.is_writable,
            "is_steam_tool": self.is_steam_tool,
            "library_available": self.library_available,
            "executable_path": self.executable_path,
            "executable_resolution": self.executable_resolution,
            "executable_candidates": list(self.executable_candidates),
        }


@dataclass(frozen=True, slots=True)
class CompressionModeInfo:
    profile: CompressionProfile
    description: str
    level: str


@dataclass(frozen=True, slots=True)
class CompressionEstimate:
    game_id: str
    profile: CompressionProfile
    current_size_gb: float
    estimated_size_gb: float
    estimated_savings_gb: float
    compatible: bool
    filesystem: FilesystemType
    level: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.game_id.strip():
            raise ValueError("game id cannot be empty")
        _require_non_negative(self.current_size_gb, "current_size_gb")
        _require_non_negative(self.estimated_size_gb, "estimated_size_gb")
        _require_non_negative(self.estimated_savings_gb, "estimated_savings_gb")


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    game_id: str
    scanned_size_gb: float
    estimated_savings_gb: float
    summary: str
    recommendations: tuple[str, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.game_id.strip():
            raise ValueError("game id cannot be empty")
        _require_non_negative(self.scanned_size_gb, "scanned_size_gb")
        _require_non_negative(self.estimated_savings_gb, "estimated_savings_gb")

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "scanned_size_gb": self.scanned_size_gb,
            "estimated_savings_gb": self.estimated_savings_gb,
            "summary": self.summary,
            "recommendations": list(self.recommendations),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class OptimizationOptions:
    profile: OptimizationProfile = OptimizationProfile.BALANCED
    gamemode: bool = True
    gamescope: bool = False
    mangohud: bool = False
    fps_limit: int | None = None
    adaptive_sync: bool = True
    cursor_grab: bool = False
    cpu_performance_profile: bool = False
    memory_monitoring: bool = True
    optiscaler: bool = False

    def __post_init__(self) -> None:
        if self.fps_limit is not None:
            if (
                isinstance(self.fps_limit, bool)
                or not isinstance(self.fps_limit, int)
                or not 15 <= self.fps_limit <= 1000
            ):
                raise ValueError("fps_limit must be an integer between 15 and 1000")


@dataclass(frozen=True, slots=True)
class OptimizationCompatibility:
    compatible: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TextureOptions:
    mode: TextureMode = TextureMode.CLASSIC_ENHANCE
    scale: str = "Auto"
    max_vram_gb: float = 8.0
    max_output_size_gb: float = 20.0
    scope: TextureScope = TextureScope.LOW_QUALITY_ONLY
    pause_while_gaming: bool = True
    automatic_backup: bool = True

    def __post_init__(self) -> None:
        if self.scale not in {"Auto", "2x", "4x"}:
            raise ValueError("scale must be Auto, 2x, or 4x")
        _require_positive(self.max_vram_gb, "max_vram_gb")
        _require_positive(self.max_output_size_gb, "max_output_size_gb")


@dataclass(frozen=True, slots=True)
class TexturePreview:
    game_id: str
    compatibility: TextureCompatibility
    source_asset: str
    enhanced_asset: str
    estimated_output_size_gb: float
    notice: str = "Preview only; no game files are modified."

    def __post_init__(self) -> None:
        if not self.game_id.strip():
            raise ValueError("game id cannot be empty")
        _require_non_negative(
            self.estimated_output_size_gb, "estimated_output_size_gb"
        )
