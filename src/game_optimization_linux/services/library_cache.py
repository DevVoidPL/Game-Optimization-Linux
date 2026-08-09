"""Versioned, metadata-only cache for the discovered Steam library."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import json
import logging
import os
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from game_optimization_linux.models.enums import (
    BackupStatus,
    FilesystemType,
    GameStatus,
    Launcher,
    OptimizationProfile,
    SizeScanStatus,
    TaskStatus,
    TextureCompatibility,
)
from game_optimization_linux.models.game import Game
from game_optimization_linux.providers.steam_tools import is_steam_tool_name


logger = logging.getLogger(__name__)
CACHE_FORMAT_VERSION = 2
_LEGACY_CACHE_FORMAT_VERSIONS = frozenset({1})
_EnumT = TypeVar("_EnumT", bound=Enum)


class LibraryCacheError(RuntimeError):
    """Raised when library metadata cannot be written safely."""


def _enum_value(enum_type: type[_EnumT], value: Any, default: _EnumT) -> _EnumT:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


class LibraryCache:
    """Atomically persist only game metadata, never Steam file contents."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[Game]:
        """Return cached games or an empty list for absent/corrupt data."""

        if not self._path.is_file():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            logger.warning("Ignoring unreadable Steam library cache %s: %s", self._path, error)
            return []
        if not isinstance(payload, dict):
            logger.warning("Ignoring malformed Steam library cache at %s", self._path)
            return []
        version = payload.get("version")
        if version not in {CACHE_FORMAT_VERSION, *_LEGACY_CACHE_FORMAT_VERSIONS}:
            logger.info("Ignoring unsupported Steam library cache format at %s", self._path)
            return []
        if version in _LEGACY_CACHE_FORMAT_VERSIONS:
            logger.info("Loading legacy Steam library cache format %s", version)
        raw_games = payload.get("games")
        if not isinstance(raw_games, list):
            logger.warning("Ignoring malformed Steam library cache at %s", self._path)
            return []

        games: list[Game] = []
        for index, raw_game in enumerate(raw_games):
            try:
                games.append(self._decode_game(raw_game))
            except (TypeError, ValueError, KeyError) as error:
                logger.warning(
                    "Skipping invalid cached game entry %d in %s: %s",
                    index,
                    self._path,
                    error,
                )
        return games

    def save(self, games: list[Game] | tuple[Game, ...]) -> None:
        payload = {
            "version": CACHE_FORMAT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "games": [self._encode_game(game) for game in games],
        }
        temporary_path = self._path.with_name(
            f".{self._path.name}.{uuid4().hex}.tmp"
        )
        try:
            serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self._path)
        except (OSError, TypeError, ValueError) as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not clean cache temporary file", exc_info=True)
            raise LibraryCacheError(
                f"could not save Steam library cache to {self._path}: {error}"
            ) from error

    @staticmethod
    def _encode_game(game: Game) -> dict[str, Any]:
        raw = game.to_dict()
        # ``calculating`` only describes a live worker in this process.  It
        # must never survive a crash or cancellation as a misleading state.
        if game.size_scan_status is SizeScanStatus.CALCULATING:
            raw["size_scan_status"] = SizeScanStatus.NOT_REQUESTED.value
        # Per-file error details can contain deeper directory names. They are
        # useful in the runtime log, but are unnecessary in a metadata cache.
        raw["size_scan_error"] = None
        return raw

    @staticmethod
    def _decode_game(raw: Any) -> Game:
        if not isinstance(raw, dict):
            raise TypeError("game entry must be an object")
        last_task_raw = raw.get("last_task_status")
        last_task = (
            _enum_value(TaskStatus, last_task_raw, TaskStatus.CANCELLED)
            if last_task_raw
            else None
        )
        scanned_at = _optional_datetime(raw.get("last_scanned_at")) or datetime.now(UTC)
        library_path_raw = raw.get("library_path")
        mount_point_raw = raw.get("mount_point")
        mount_options = raw.get("mount_options", [])
        if not isinstance(mount_options, list) or not all(
            isinstance(option, str) for option in mount_options
        ):
            mount_options = []
        executable_candidates = raw.get("executable_candidates", [])
        if not isinstance(executable_candidates, list) or not all(
            isinstance(value, str) for value in executable_candidates
        ):
            executable_candidates = []
        size_scan_status = _enum_value(
            SizeScanStatus,
            raw.get("size_scan_status"),
            SizeScanStatus.NOT_REQUESTED,
        )
        if size_scan_status is SizeScanStatus.CALCULATING:
            size_scan_status = SizeScanStatus.NOT_REQUESTED

        launcher = _enum_value(Launcher, raw.get("launcher"), Launcher.STEAM)
        data_source = str(raw.get("data_source") or "Steam cache")
        state_flags = _optional_non_negative_int(raw.get("state_flags"))
        update_in_progress_raw = raw.get("update_in_progress")
        update_in_progress = (
            update_in_progress_raw
            if isinstance(update_in_progress_raw, bool)
            else bool(state_flags is not None and state_flags & ~0xFF)
        )
        normal_steam_record = (
            launcher is Launcher.STEAM and data_source.casefold() != "demo"
        )
        return Game(
            id=str(raw["id"]),
            name=str(raw["name"]),
            launcher=launcher,
            install_path=Path(str(raw["install_path"])),
            logical_size_gb=float(raw.get("logical_size_gb", 0.0)),
            physical_size_gb=float(raw.get("physical_size_gb", 0.0)),
            filesystem=_enum_value(
                FilesystemType, raw.get("filesystem"), FilesystemType.UNKNOWN
            ),
            compression_available=bool(raw.get("compression_available", False)),
            saved_space_gb=float(raw.get("saved_space_gb", 0.0)),
            last_task_status=last_task,
            status=_enum_value(GameStatus, raw.get("status"), GameStatus.NEEDS_ATTENTION),
            cover_asset=str(raw.get("cover_asset", "")),
            portrait_artwork_path=(
                Path(str(raw["portrait_artwork_path"]))
                if raw.get("portrait_artwork_path")
                else None
            ),
            header_artwork_path=(
                Path(str(raw["header_artwork_path"]))
                if raw.get("header_artwork_path")
                else None
            ),
            fallback_artwork_path=(
                Path(str(raw["fallback_artwork_path"]))
                if raw.get("fallback_artwork_path")
                else None
            ),
            active_optimization_profile=_enum_value(
                OptimizationProfile,
                raw.get("active_optimization_profile"),
                OptimizationProfile.BALANCED,
            ),
            backup_status=(
                BackupStatus.NOT_DETECTED
                if normal_steam_record
                else _enum_value(
                    BackupStatus,
                    raw.get("backup_status"),
                    BackupStatus.NOT_DETECTED,
                )
            ),
            texture_compatibility=(
                TextureCompatibility.NOT_CHECKED
                if normal_steam_record
                else _enum_value(
                    TextureCompatibility,
                    raw.get("texture_compatibility"),
                    TextureCompatibility.NOT_CHECKED,
                )
            ),
            has_anticheat=bool(raw.get("has_anticheat", False)),
            steam_app_id=str(raw.get("steam_app_id") or "") or None,
            library_path=Path(str(library_path_raw)) if library_path_raw else None,
            data_source=data_source,
            last_scanned_at=scanned_at,
            last_updated_at=_optional_datetime(raw.get("last_updated_at")),
            language=str(raw.get("language") or "") or None,
            state_flags=state_flags,
            steam_build_id=str(raw.get("steam_build_id") or "").strip() or None,
            steam_manifest_path=(
                Path(str(raw["steam_manifest_path"]))
                if raw.get("steam_manifest_path")
                else None
            ),
            steam_manifest_mtime_ns=_optional_non_negative_int(
                raw.get("steam_manifest_mtime_ns")
            ),
            steam_manifest_size_bytes=_optional_non_negative_int(
                raw.get("steam_manifest_size_bytes")
            ),
            steam_size_on_disk_bytes=_optional_non_negative_int(
                raw.get("steam_size_on_disk_bytes")
            ),
            update_in_progress=update_in_progress,
            size_scan_status=size_scan_status,
            size_scan_error=None,
            filesystem_name=str(raw.get("filesystem_name") or ""),
            mount_point=Path(str(mount_point_raw)) if mount_point_raw else None,
            filesystem_device=str(raw.get("filesystem_device") or "") or None,
            mount_options=tuple(mount_options),
            is_writable=(
                raw.get("is_writable")
                if isinstance(raw.get("is_writable"), bool)
                else None
            ),
            is_steam_tool=bool(
                raw.get("is_steam_tool", is_steam_tool_name(str(raw.get("name", ""))))
            ),
            library_available=bool(raw.get("library_available", True)),
            executable_path=str(raw.get("executable_path") or ""),
            executable_resolution=str(
                raw.get("executable_resolution") or "not_scanned"
            ),
            executable_candidates=tuple(executable_candidates),
        )


__all__ = ["CACHE_FORMAT_VERSION", "LibraryCache", "LibraryCacheError"]
