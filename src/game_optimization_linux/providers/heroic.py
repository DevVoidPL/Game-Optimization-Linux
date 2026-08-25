"""Read-only discovery of games installed through Heroic Games Launcher."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
from threading import Event, RLock
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlencode, quote

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
_MAX_JSON_BYTES = 32 * 1024 * 1024
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


@dataclass(frozen=True, slots=True)
class HeroicRoot:
    path: Path
    variant: str


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean(value: object) -> str:
    return str(value or "").strip()


class HeroicGameProvider(GameProvider):
    """Normalize Heroic's bounded local install metadata without network I/O."""

    def __init__(
        self,
        filesystem_provider: FilesystemProvider,
        roots: Iterable[str | Path | HeroicRoot] | None = None,
        *,
        home: str | Path | None = None,
    ) -> None:
        self._filesystem_provider = filesystem_provider
        self._roots = (
            self.standard_roots(home) if roots is None else self._normalize_roots(roots)
        )
        self._games: dict[str, Game] = {}
        self._last_report = LauncherScanReport.empty("Heroic")
        self._lock = RLock()

    @staticmethod
    def standard_roots(home: str | Path | None = None) -> tuple[HeroicRoot, ...]:
        base = Path.home() if home is None else Path(home).expanduser()
        return (
            HeroicRoot(base / ".config" / "heroic", "native"),
            HeroicRoot(
                base / ".var" / "app" / "com.heroicgameslauncher.hgl" / "config" / "heroic",
                "flatpak",
            ),
        )

    @staticmethod
    def _normalize_roots(
        roots: Iterable[str | Path | HeroicRoot],
    ) -> tuple[HeroicRoot, ...]:
        normalized: list[HeroicRoot] = []
        for raw in roots:
            if isinstance(raw, HeroicRoot):
                normalized.append(HeroicRoot(Path(raw.path).expanduser(), raw.variant))
            else:
                path = Path(raw).expanduser()
                variant = "flatpak" if "com.heroicgameslauncher.hgl" in path.parts else "native"
                normalized.append(HeroicRoot(path, variant))
        return tuple(normalized)

    @property
    def configured_roots(self) -> tuple[Path, ...]:
        return tuple(root.path for root in self._roots)

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
        raise ValueError("Heroic games are discovered read-only")

    def refresh(self, *, cancel_event: Event | None = None) -> Sequence[Game]:
        report = ScanReportBuilder("Heroic")
        discovered: dict[str, Game] = {}
        seen_roots: set[str] = set()
        for root in self._roots:
            if cancel_event is not None and cancel_event.is_set():
                return self.list_games()
            path = Path(root.path).expanduser()
            key = _path_key(path)
            if key in seen_roots:
                report.duplicate_roots += 1
                continue
            seen_roots.add(key)
            state, message = self._root_state(path)
            report.roots.append(LauncherRootDiagnostic(path, root.variant, state, message))
            if state != "found":
                if message:
                    report.errors.append(message)
                continue
            metadata = self._library_metadata(path, report)
            settings = self._game_settings(path, report)
            for runner, store, records in self._installed_records(path, report):
                for app_id, record in records:
                    if cancel_event is not None and cancel_event.is_set():
                        return self.list_games()
                    report.records_seen += 1
                    try:
                        game = self._to_game(
                            path,
                            root.variant,
                            runner,
                            store,
                            app_id,
                            record,
                            metadata.get((runner, app_id), {}),
                            settings.get(app_id, {}),
                        )
                    except (OSError, TypeError, ValueError) as error:
                        report.malformed_records += 1
                        diagnostic = f"Malformed Heroic {runner} record {app_id!r}: {error}"
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
            "Heroic scan finished in %.3fs: roots=%d records=%d malformed=%d games=%d",
            completed.elapsed_seconds,
            sum(item.state == "found" for item in completed.roots),
            completed.records_seen,
            completed.malformed_records,
            completed.games_found,
        )
        return self.list_games()

    @staticmethod
    def _root_state(path: Path) -> tuple[str, str]:
        try:
            exists = path.exists()
            is_dir = path.is_dir()
        except OSError as error:
            return "inaccessible", f"Heroic root is inaccessible: {path}: {error}"
        if not exists:
            return "missing", ""
        if not is_dir:
            return "malformed", f"Heroic root is not a directory: {path}"
        try:
            next(path.iterdir(), None)
        except OSError as error:
            return "inaccessible", f"Heroic root is inaccessible: {path}: {error}"
        return "found", ""

    def _read_json(self, path: Path, report: ScanReportBuilder) -> object | None:
        try:
            size = path.stat().st_size
            if size > _MAX_JSON_BYTES:
                raise ValueError(f"file exceeds {_MAX_JSON_BYTES} bytes")
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            report.malformed_records += 1
            message = f"Could not read Heroic metadata {path}: {error}"
            report.errors.append(message)
            LOGGER.warning(message)
            return None

    def _installed_records(
        self, root: Path, report: ScanReportBuilder
    ) -> tuple[tuple[str, str, tuple[tuple[str, Mapping[str, Any]], ...]], ...]:
        groups: list[tuple[str, str, tuple[tuple[str, Mapping[str, Any]], ...]]] = []
        legendary_paths = (
            root / "legendaryConfig" / "legendary" / "installed.json",
            root / "legendary" / "installed.json",
            root.parent / "legendary" / "installed.json",
        )
        legendary = self._first_json(legendary_paths, report)
        epic_rows: list[tuple[str, Mapping[str, Any]]] = []
        if isinstance(legendary, Mapping):
            epic_rows = [
                (_clean(app_id), _mapping(value))
                for app_id, value in legendary.items()
                if _clean(app_id) and isinstance(value, Mapping)
            ]
        groups.append(("legendary", "Epic", tuple(epic_rows)))

        gog = self._read_if_file(root / "gog_store" / "installed.json", report)
        raw_gog = _mapping(gog).get("installed", gog)
        gog_rows: list[tuple[str, Mapping[str, Any]]] = []
        if isinstance(raw_gog, Sequence) and not isinstance(raw_gog, (str, bytes)):
            for item in raw_gog:
                row = _mapping(item)
                app_id = _clean(row.get("appName") or row.get("app_name") or row.get("id"))
                if app_id:
                    gog_rows.append((app_id, row))
        elif isinstance(raw_gog, Mapping):
            for app_id, value in raw_gog.items():
                if app_id != "__timestamp" and isinstance(value, Mapping):
                    gog_rows.append((_clean(app_id), value))
        groups.append(("gog", "GOG", tuple(gog_rows)))

        nile = self._read_if_file(root / "nile_config" / "nile" / "installed.json", report)
        nile_rows: list[tuple[str, Mapping[str, Any]]] = []
        raw_nile = _mapping(nile).get("installed", nile)
        if isinstance(raw_nile, Sequence) and not isinstance(raw_nile, (str, bytes)):
            for item in raw_nile:
                row = _mapping(item)
                app_id = _clean(row.get("id") or row.get("app_name") or row.get("appName"))
                if app_id:
                    nile_rows.append((app_id, row))
        elif isinstance(raw_nile, Mapping):
            for app_id, value in raw_nile.items():
                if app_id != "__timestamp" and isinstance(value, Mapping):
                    nile_rows.append((_clean(app_id), value))
        groups.append(("nile", "Amazon", tuple(nile_rows)))
        return tuple(groups)

    def _first_json(self, paths: Iterable[Path], report: ScanReportBuilder) -> object | None:
        for path in paths:
            try:
                if path.is_file():
                    return self._read_json(path, report)
            except OSError:
                continue
        return None

    def _read_if_file(self, path: Path, report: ScanReportBuilder) -> object | None:
        try:
            return self._read_json(path, report) if path.is_file() else None
        except OSError:
            return None

    def _library_metadata(
        self, root: Path, report: ScanReportBuilder
    ) -> dict[tuple[str, str], Mapping[str, Any]]:
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        for runner in ("legendary", "gog", "nile"):
            payload = self._read_if_file(root / "store_cache" / f"{runner}_library.json", report)
            rows = _mapping(payload).get("library", payload)
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                continue
            for item in rows:
                row = _mapping(item)
                app_id = _clean(row.get("app_name") or row.get("appName") or row.get("id"))
                if app_id:
                    result[(runner, app_id)] = row
        return result

    def _game_settings(
        self, root: Path, report: ScanReportBuilder
    ) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        directory = root / "GamesConfig"
        try:
            paths = sorted(directory.glob("*.json"), key=lambda item: item.name.casefold())
        except OSError:
            return result
        for path in paths:
            payload = self._read_json(path, report)
            outer = _mapping(payload)
            app_id = path.stem
            row = _mapping(outer.get(app_id))
            if row:
                result[app_id] = row
        return result

    def _to_game(
        self,
        root: Path,
        variant: str,
        runner: str,
        store: str,
        app_id: str,
        record: Mapping[str, Any],
        metadata: Mapping[str, Any],
        settings: Mapping[str, Any],
    ) -> Game:
        if not app_id:
            raise ValueError("missing launcher game identifier")
        install = _mapping(record.get("install"))
        metadata_install = _mapping(metadata.get("install"))
        raw_install_path = _clean(
            record.get("install_path")
            or record.get("installPath")
            or record.get("path")
            or install.get("install_path")
            or install.get("path")
            or metadata_install.get("install_path")
        )
        if not raw_install_path:
            raise ValueError("installed record has no installation path")
        install_path = Path(raw_install_path).expanduser()
        title = _clean(
            record.get("title")
            or record.get("name")
            or metadata.get("title")
            or app_id
        )
        executable = _clean(
            record.get("executable")
            or install.get("executable")
            or metadata_install.get("executable")
        )
        executable_path = executable
        if executable and not Path(executable).is_absolute():
            executable_path = os.fspath(install_path / executable)
        wine_prefix_raw = _clean(settings.get("winePrefix") or settings.get("wine_prefix"))
        wine_prefix = Path(wine_prefix_raw).expanduser() if wine_prefix_raw else None
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
        artwork = self._artwork(root, app_id)
        stable_id = f"heroic-{runner.casefold()}-{quote(app_id, safe='._-')}"
        launch_uri = "heroic://launch?" + urlencode(
            {"appName": app_id, "runner": runner}, quote_via=quote
        )
        return Game(
            id=stable_id,
            name=title,
            launcher=Launcher.HEROIC,
            launcher_game_id=app_id,
            store=store,
            launch_uri=launch_uri,
            launcher_variant=variant,
            runner=runner,
            wine_prefix=wine_prefix,
            working_directory=install_path,
            install_path=install_path,
            library_path=install_path.parent,
            logical_size_gb=0.0,
            physical_size_gb=0.0,
            filesystem=filesystem,
            filesystem_name=filesystem_name,
            compression_available=compression_available,
            status=status,
            data_source="Heroic Flatpak" if variant == "flatpak" else "Heroic",
            last_scanned_at=datetime.now(UTC),
            size_scan_status=SizeScanStatus.NOT_REQUESTED,
            mount_point=mount_point,
            filesystem_device=device,
            mount_options=mount_options,
            is_writable=writable,
            library_available=status.value != "Missing files",
            executable_path=executable_path,
            executable_resolution="launcher_metadata" if executable_path else "launcher_managed",
            portrait_artwork_path=artwork,
            fallback_artwork_path=artwork,
            launch_available=True,
        )

    @staticmethod
    def _artwork(root: Path, app_id: str) -> Path | None:
        safe_names = (app_id, quote(app_id, safe="._-"))
        candidates = [
            root / "icons" / f"{name}{suffix}"
            for name in safe_names
            for suffix in _IMAGE_SUFFIXES
        ]
        return first_existing_file(candidates)

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


__all__ = ["HeroicGameProvider", "HeroicRoot"]
