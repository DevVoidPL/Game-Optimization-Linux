"""Read-only discovery of installed games from Lutris metadata."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import sqlite3
from threading import Event, RLock
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

import yaml

from game_optimization_linux.models import Game, Launcher, SizeScanStatus

from .base import FilesystemProvider, GameProvider
from .discovery import (
    LauncherRootDiagnostic,
    LauncherScanReport,
    ScanReportBuilder,
    first_existing_file,
    inspect_install_path,
)


LOGGER = logging.getLogger(__name__)
_MAX_YAML_BYTES = 2 * 1024 * 1024
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_KNOWN_COLUMNS = (
    "id",
    "name",
    "slug",
    "platform",
    "runner",
    "executable",
    "directory",
    "installed",
    "configpath",
    "service",
    "service_id",
)


@dataclass(frozen=True, slots=True)
class LutrisRoot:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    variant: str


def _clean(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class LutrisGameProvider(GameProvider):
    """Read installed rows from ``pga.db`` and correlate bounded YAML files."""

    def __init__(
        self,
        filesystem_provider: FilesystemProvider,
        roots: Iterable[str | Path | LutrisRoot] | None = None,
        *,
        home: str | Path | None = None,
    ) -> None:
        self._filesystem_provider = filesystem_provider
        self._roots = (
            self.standard_roots(home) if roots is None else self._normalize_roots(roots)
        )
        self._games: dict[str, Game] = {}
        self._last_report = LauncherScanReport.empty("Lutris")
        self._lock = RLock()

    @staticmethod
    def standard_roots(home: str | Path | None = None) -> tuple[LutrisRoot, ...]:
        base = Path.home() if home is None else Path(home).expanduser()
        return (
            LutrisRoot(
                base / ".config" / "lutris",
                base / ".local" / "share" / "lutris",
                base / ".cache" / "lutris",
                "native",
            ),
            LutrisRoot(
                base / ".var" / "app" / "net.lutris.Lutris" / "config" / "lutris",
                base / ".var" / "app" / "net.lutris.Lutris" / "data" / "lutris",
                base / ".var" / "app" / "net.lutris.Lutris" / "cache" / "lutris",
                "flatpak",
            ),
        )

    @staticmethod
    def _normalize_roots(
        roots: Iterable[str | Path | LutrisRoot],
    ) -> tuple[LutrisRoot, ...]:
        normalized: list[LutrisRoot] = []
        for raw in roots:
            if isinstance(raw, LutrisRoot):
                normalized.append(
                    LutrisRoot(
                        Path(raw.config_dir).expanduser(),
                        Path(raw.data_dir).expanduser(),
                        Path(raw.cache_dir).expanduser(),
                        raw.variant,
                    )
                )
                continue
            path = Path(raw).expanduser()
            variant = "flatpak" if "net.lutris.Lutris" in path.parts else "native"
            if (path / "data" / "lutris" / "pga.db").is_file():
                normalized.append(
                    LutrisRoot(
                        path / "config" / "lutris",
                        path / "data" / "lutris",
                        path / "cache" / "lutris",
                        variant,
                    )
                )
            else:
                normalized.append(LutrisRoot(path, path, path, variant))
        return tuple(normalized)

    @property
    def configured_roots(self) -> tuple[Path, ...]:
        return tuple(root.data_dir for root in self._roots)

    @property
    def last_report(self) -> LauncherScanReport:
        with self._lock:
            return self._last_report

    def list_games(self) -> Sequence[Game]:
        with self._lock:
            return tuple(sorted(self._games.values(), key=lambda game: (game.name.casefold(), game.id)))

    def get_game(self, game_id: str) -> Game | None:
        with self._lock:
            return self._games.get(str(game_id))

    def add_game(self, game: Game) -> Game:
        raise ValueError("Lutris games are discovered read-only")

    def refresh(self, *, cancel_event: Event | None = None) -> Sequence[Game]:
        report = ScanReportBuilder("Lutris")
        discovered: dict[str, Game] = {}
        seen_databases: set[str] = set()
        for root in self._roots:
            if cancel_event is not None and cancel_event.is_set():
                return self.list_games()
            database = root.data_dir / "pga.db"
            key = _path_key(database)
            if key in seen_databases:
                report.duplicate_roots += 1
                continue
            seen_databases.add(key)
            state, message = self._root_state(root, database)
            report.roots.append(
                LauncherRootDiagnostic(root.data_dir, root.variant, state, message)
            )
            if state != "found":
                if message:
                    report.errors.append(message)
                continue
            try:
                rows = self._database_rows(database)
            except (OSError, sqlite3.Error, ValueError) as error:
                report.malformed_records += 1
                diagnostic = f"Could not read Lutris database {database}: {error}"
                report.errors.append(diagnostic)
                report.roots[-1] = LauncherRootDiagnostic(
                    root.data_dir, root.variant, "malformed", diagnostic
                )
                LOGGER.warning(diagnostic)
                continue
            for row in rows:
                if cancel_event is not None and cancel_event.is_set():
                    return self.list_games()
                report.records_seen += 1
                try:
                    config = self._load_config(root, row, report)
                    game = self._to_game(root, row, config)
                except (OSError, TypeError, ValueError) as error:
                    report.malformed_records += 1
                    diagnostic = f"Malformed Lutris record {_clean(row.get('id'))!r}: {error}"
                    report.errors.append(diagnostic)
                    LOGGER.warning(diagnostic)
                    continue
                existing = discovered.get(game.id)
                if existing is not None:
                    report.duplicate_games += 1
                    if not existing.library_available and game.library_available:
                        discovered[game.id] = game
                    continue
                discovered[game.id] = game

        if cancel_event is not None and cancel_event.is_set():
            return self.list_games()
        completed = report.finish(len(discovered))
        with self._lock:
            self._games = discovered
            self._last_report = completed
        LOGGER.info(
            "Lutris scan finished in %.3fs: roots=%d records=%d malformed=%d games=%d",
            completed.elapsed_seconds,
            sum(item.state == "found" for item in completed.roots),
            completed.records_seen,
            completed.malformed_records,
            completed.games_found,
        )
        return self.list_games()

    @staticmethod
    def _root_state(root: LutrisRoot, database: Path) -> tuple[str, str]:
        try:
            if database.is_file():
                return "found", ""
            if root.data_dir.exists():
                return "malformed", f"Lutris data root has no pga.db: {root.data_dir}"
            return "missing", ""
        except OSError as error:
            return "inaccessible", f"Lutris root is inaccessible: {root.data_dir}: {error}"

    @staticmethod
    def _database_rows(database: Path) -> tuple[dict[str, Any], ...]:
        uri = "file:" + quote(os.fspath(database.resolve(strict=False)), safe="/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            columns = {
                str(row[1])
                for row in connection.execute('PRAGMA table_info("games")').fetchall()
            }
            required = {"id", "name", "installed"}
            missing = required.difference(columns)
            if missing:
                raise ValueError(
                    "games table lacks required columns: "
                    + ", ".join(sorted(missing))
                )
            selected = [column for column in _KNOWN_COLUMNS if column in columns]
            query = "SELECT " + ", ".join(f'\"{name}\"' for name in selected) + ' FROM "games"'
            query += ' WHERE COALESCE("installed", 0) != 0'
            return tuple(dict(row) for row in connection.execute(query).fetchall())
        finally:
            connection.close()

    def _load_config(
        self,
        root: LutrisRoot,
        row: Mapping[str, Any],
        report: ScanReportBuilder,
    ) -> Mapping[str, Any]:
        config_id = _clean(row.get("configpath"))
        if not config_id:
            return {}
        path = root.config_dir / "games" / f"{config_id}.yml"
        try:
            if not path.is_file():
                return {}
            if path.stat().st_size > _MAX_YAML_BYTES:
                raise ValueError(f"file exceeds {_MAX_YAML_BYTES} bytes")
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
            report.malformed_records += 1
            message = f"Could not read Lutris game config {path}: {error}"
            report.errors.append(message)
            LOGGER.warning(message)
            return {}
        return _mapping(payload)

    def _to_game(
        self,
        root: LutrisRoot,
        row: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> Game:
        launcher_id = _clean(row.get("id"))
        if not launcher_id:
            raise ValueError("missing Lutris database ID")
        name = _clean(row.get("name"))
        if not name:
            raise ValueError("missing game name")
        game_config = _mapping(config.get("game"))
        system_config = _mapping(config.get("system"))
        runner = _clean(row.get("runner"))
        executable = _clean(game_config.get("exe") or row.get("executable"))
        directory_raw = _clean(row.get("directory"))
        if not directory_raw:
            absolute_executable = Path(executable).expanduser() if executable else None
            if absolute_executable is not None and absolute_executable.is_absolute():
                directory_raw = os.fspath(absolute_executable.parent)
        if not directory_raw:
            raise ValueError("installed record has no installation directory")
        install_path = Path(directory_raw).expanduser()
        executable_path = executable
        if executable and not Path(executable).is_absolute():
            executable_path = os.fspath(install_path / executable)
        working_raw = _clean(
            game_config.get("working_dir")
            or system_config.get("working_dir")
            or directory_raw
        )
        working_directory = Path(working_raw).expanduser() if working_raw else install_path
        prefix_raw = _clean(game_config.get("prefix") or config.get("prefix"))
        wine_prefix = Path(prefix_raw).expanduser() if prefix_raw else None
        slug = _clean(row.get("slug"))
        service = _clean(row.get("service"))
        store = self._store(service, _clean(row.get("platform")), runner)
        (
            filesystem,
            filesystem_name,
            compression_available,
            status,
            mount_point,
            device,
            mount_options,
            writable,
        ) = inspect_install_path(self._filesystem_provider, install_path)
        portrait, header, fallback = self._artwork(root, slug, launcher_id)
        return Game(
            id=f"lutris-{quote(launcher_id, safe='._-')}",
            name=name,
            launcher=Launcher.LUTRIS,
            launcher_game_id=launcher_id,
            store=store,
            launch_uri=f"lutris:rungameid/{quote(launcher_id, safe='')}",
            launcher_variant=root.variant,
            runner=runner,
            wine_prefix=wine_prefix,
            working_directory=working_directory,
            install_path=install_path,
            library_path=install_path.parent,
            logical_size_gb=0.0,
            physical_size_gb=0.0,
            filesystem=filesystem,
            filesystem_name=filesystem_name,
            compression_available=compression_available,
            status=status,
            data_source="Lutris Flatpak" if root.variant == "flatpak" else "Lutris",
            last_scanned_at=datetime.now(UTC),
            size_scan_status=SizeScanStatus.NOT_REQUESTED,
            mount_point=mount_point,
            filesystem_device=device,
            mount_options=mount_options,
            is_writable=writable,
            library_available=status.value != "Missing files",
            executable_path=executable_path,
            executable_resolution="launcher_metadata" if executable_path else "launcher_managed",
            portrait_artwork_path=portrait,
            header_artwork_path=header,
            fallback_artwork_path=fallback,
            launch_available=bool(row.get("configpath")),
            launch_unavailable_reason=(
                "Lutris launch metadata is incomplete (missing config path)"
                if not row.get("configpath")
                else ""
            ),
        )

    @staticmethod
    def _store(service: str, platform: str, runner: str) -> str:
        known = {
            "steam": "Steam",
            "gog": "GOG",
            "egs": "Epic",
            "epic": "Epic",
            "amazon": "Amazon",
        }
        if service.casefold() in known:
            return known[service.casefold()]
        if runner.casefold() == "linux" or "linux" in platform.casefold():
            return "native Linux"
        if runner.casefold() in {"wine", "proton"}:
            return "Wine"
        return "unknown"

    @staticmethod
    def _artwork(
        root: LutrisRoot, slug: str, launcher_id: str
    ) -> tuple[Path | None, Path | None, Path | None]:
        names = tuple(dict.fromkeys(value for value in (slug, launcher_id) if value))
        portrait = first_existing_file(
            root.data_dir / "coverart" / f"{name}{suffix}"
            for name in names
            for suffix in _IMAGE_SUFFIXES
        )
        header = first_existing_file(
            root.data_dir / "banners" / f"{name}{suffix}"
            for name in names
            for suffix in _IMAGE_SUFFIXES
        )
        icon_root = root.data_dir.parent / "icons" / "hicolor" / "128x128" / "apps"
        fallback = first_existing_file(
            icon_root / f"lutris_{name}{suffix}"
            for name in names
            for suffix in _IMAGE_SUFFIXES
        )
        return portrait, header, fallback

    def update_game_sizes(
        self,
        game_id: str,
        logical_size_gb: float,
        physical_size_gb: float,
        *,
        error: str | None = None,
    ) -> Game | None:
        with self._lock:
            game = self._games.get(game_id)
            if game is None:
                return None
            updated = replace(
                game,
                logical_size_gb=logical_size_gb,
                physical_size_gb=physical_size_gb,
                size_scan_status=SizeScanStatus.FAILED if error else SizeScanStatus.COMPLETED,
                size_scan_error=error,
                last_scanned_at=datetime.now(UTC),
            )
            self._games[game_id] = updated
            return updated


__all__ = ["LutrisGameProvider", "LutrisRoot"]
