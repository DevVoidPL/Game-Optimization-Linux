"""Read-only discovery of locally installed Steam games."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import re
import stat
from threading import Event, RLock
from time import perf_counter
from typing import Iterable, Mapping, Sequence

from gameforge.formatting import bytes_to_gib
from gameforge.models.enums import (
    FilesystemType,
    GameStatus,
    Launcher,
    SizeScanStatus,
)
from gameforge.models.game import Game
from gameforge.models.system import FilesystemInfo

from .base import FilesystemProvider, GameProvider
from .keyvalues import (
    KVValue,
    VDFParseError,
    load_keyvalues,
    parse_keyvalues,
    tokenize_keyvalues,
)
from .steam_tools import is_steam_tool_name


LOGGER = logging.getLogger(__name__)
_MANIFEST_NAME = re.compile(r"^appmanifest_.+\.acf$", re.IGNORECASE)
_COVER_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
# Steam's low byte contains stable installation/runtime flags. Higher bits are
# used by update, validation, staging, commit and related write phases. Treat
# all of them conservatively as busy; a false positive merely delays work.
_STEAM_ACTIVE_WRITE_STATE_MASK = ~0xFF


@dataclass(frozen=True, slots=True)
class SteamArtworkPaths:
    """Local Steam artwork split by the shape expected by each view."""

    portrait_artwork_path: Path | None = None
    header_artwork_path: Path | None = None
    fallback_artwork_path: Path | None = None

    @property
    def preferred_path(self) -> Path | None:
        """Return a backwards-compatible single-cover choice."""

        return (
            self.portrait_artwork_path
            or self.header_artwork_path
            or self.fallback_artwork_path
        )


def _artwork_kind(filename: str) -> str:
    """Classify known Steam cache names without decoding image contents."""

    name = filename.casefold()
    if "600x900" in name or "library_capsule" in name:
        return "portrait"
    if any(
        marker in name
        for marker in (
            "header",
            "library_hero",
            "hero",
            "main_capsule",
            "capsule_231x87",
            "capsule_467x181",
        )
    ):
        return "header"
    return "fallback"


def _resolved_artwork(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def find_local_steam_artwork(
    app_id: str,
    steam_roots: Iterable[str | Path],
) -> SteamArtworkPaths:
    """Return shape-specific local Steam artwork without network access.

    The function looks only at files associated with ``app_id`` and tolerates
    both the flat and per-AppID cache layouts used by Steam. It performs only
    inexpensive directory metadata checks so a large library stays responsive.
    """

    normalized_app_id = str(app_id).strip()
    if not normalized_app_id.isascii() or not normalized_app_id.isdecimal():
        return SteamArtworkPaths()

    candidates: dict[str, list[tuple[int, str, Path]]] = {
        "portrait": [],
        "header": [],
        "fallback": [],
    }
    exact_names = (
        ("portrait", f"{normalized_app_id}_library_600x900.jpg"),
        ("portrait", f"{normalized_app_id}_library_600x900.png"),
        ("portrait", f"{normalized_app_id}_library_600x900_2x.jpg"),
        ("portrait", f"{normalized_app_id}_library_600x900_2x.png"),
        ("header", f"{normalized_app_id}_header.jpg"),
        ("header", f"{normalized_app_id}_header.png"),
    )
    nested_names = (
        ("portrait", "library_600x900.jpg"),
        ("portrait", "library_600x900.png"),
        ("portrait", "library_capsule.jpg"),
        ("portrait", "library_capsule.png"),
        ("header", "header.jpg"),
        ("header", "header.png"),
        ("header", "library_hero.jpg"),
        ("header", "library_hero.png"),
    )

    for root_value in steam_roots:
        cache = Path(root_value) / "appcache" / "librarycache"
        for index, (kind, name) in enumerate(exact_names):
            path = cache / name
            try:
                if path.is_file():
                    candidates[kind].append((index, path.name.casefold(), path))
            except OSError:
                continue
        app_cache = cache / normalized_app_id
        for index, (kind, name) in enumerate(nested_names):
            path = app_cache / name
            try:
                if path.is_file():
                    candidates[kind].append((index, path.name.casefold(), path))
            except OSError:
                continue

        for directory, require_prefix in ((cache, True), (app_cache, False)):
            try:
                entries = directory.iterdir()
                for entry in entries:
                    name = entry.name.casefold()
                    if require_prefix and not name.startswith(
                        (normalized_app_id + "_", normalized_app_id + ".")
                    ):
                        continue
                    if entry.suffix.casefold() not in _COVER_SUFFIXES:
                        continue
                    try:
                        is_file = entry.is_file()
                    except OSError:
                        continue
                    if not is_file:
                        continue
                    kind = _artwork_kind(name)
                    candidates[kind].append((100, name, entry))
            except OSError:
                continue

    def best(kind: str) -> Path | None:
        matches = candidates[kind]
        if not matches:
            return None
        selected = min(matches, key=lambda item: (item[0], item[1]))[2]
        return _resolved_artwork(selected)

    return SteamArtworkPaths(
        portrait_artwork_path=best("portrait"),
        header_artwork_path=best("header"),
        fallback_artwork_path=best("fallback"),
    )


def find_local_steam_cover(
    app_id: str,
    steam_roots: Iterable[str | Path],
) -> Path | None:
    """Return the legacy single-cover choice for existing callers."""

    return find_local_steam_artwork(app_id, steam_roots).preferred_path


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Diagnostics from the most recent Steam scan."""

    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float
    steam_found: bool
    roots_scanned: int
    roots_found: int
    libraries_found: int
    manifests_seen: int
    invalid_manifests: int
    games_found: int
    duplicate_libraries: int = 0
    duplicate_games: int = 0
    steam_roots: tuple[Path, ...] = ()
    libraries: tuple[Path, ...] = ()
    configured_libraries: tuple[Path, ...] = ()
    inaccessible_paths: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "ScanReport":
        now = datetime.now(UTC)
        return cls(
            started_at=now,
            completed_at=now,
            elapsed_seconds=0.0,
            steam_found=False,
            roots_scanned=0,
            roots_found=0,
            libraries_found=0,
            manifests_seen=0,
            invalid_manifests=0,
            games_found=0,
        )

    @property
    def duration_seconds(self) -> float:
        """Alias useful to presentation and logging code."""

        return self.elapsed_seconds

    @property
    def manifest_count(self) -> int:
        return self.manifests_seen

    @property
    def invalid_manifest_count(self) -> int:
        return self.invalid_manifests

    @property
    def game_count(self) -> int:
        return self.games_found

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


@dataclass(slots=True)
class _ScanStats:
    roots_scanned: int = 0
    duplicate_libraries: int = 0
    manifests_seen: int = 0
    invalid_manifests: int = 0
    duplicate_games: int = 0
    steam_roots: list[Path] | None = None
    configured_libraries: list[Path] | None = None
    inaccessible_paths: list[Path] | None = None
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        self.steam_roots = []
        self.configured_libraries = []
        self.inaccessible_paths = []
        self.errors = []


@dataclass(frozen=True, slots=True)
class _SteamLibrary:
    path: Path
    source: str


class _ManifestError(ValueError):
    pass


class SteamGameProvider(GameProvider):
    """Discover Steam manifests without executing commands or changing files.

    Passing ``roots`` supplies the complete root list.  This is useful for
    tests and portable installations because standard locations are then not
    touched.  With ``roots=None``, standard native and Flatpak locations are
    checked, followed by ``additional_roots``.
    """

    SOURCE_STEAM = "Steam"
    SOURCE_FLATPAK = "Steam Flatpak"

    def __init__(
        self,
        filesystem_provider: FilesystemProvider,
        roots: Iterable[str | Path] | None = None,
        *,
        additional_roots: Iterable[str | Path] = (),
        home: str | Path | None = None,
    ) -> None:
        self._filesystem_provider = filesystem_provider
        if roots is None:
            self._base_roots = self.standard_roots(home)
        else:
            self._base_roots = self._deduplicate_paths(roots)
        self._additional_roots = self._deduplicate_paths(additional_roots)
        self._games: dict[str, Game] = {}
        self._manual_games: dict[str, Game] = {}
        self._last_report = ScanReport.empty()
        self._lock = RLock()

    @staticmethod
    def standard_roots(home: str | Path | None = None) -> tuple[Path, ...]:
        """Return conventional native and Flatpak Steam roots."""

        home_path = Path.home() if home is None else Path(home).expanduser()
        return (
            home_path / ".local" / "share" / "Steam",
            home_path / ".steam" / "steam",
            home_path / ".steam" / "root",
            home_path
            / ".var"
            / "app"
            / "com.valvesoftware.Steam"
            / "data"
            / "Steam",
        )

    @property
    def configured_roots(self) -> tuple[Path, ...]:
        with self._lock:
            return self._deduplicate_paths(
                (*self._base_roots, *self._additional_roots)
            )

    @property
    def roots(self) -> tuple[Path, ...]:
        return self.configured_roots

    @property
    def additional_roots(self) -> tuple[Path, ...]:
        with self._lock:
            return self._additional_roots

    def set_additional_roots(self, roots: Iterable[str | Path]) -> None:
        """Replace user-configured roots; scanning remains an explicit action."""

        normalized = self._deduplicate_paths(roots)
        with self._lock:
            self._additional_roots = normalized

    @property
    def last_report(self) -> ScanReport:
        with self._lock:
            return self._last_report

    @property
    def steam_found(self) -> bool:
        return self.last_report.steam_found

    def list_games(self) -> Sequence[Game]:
        with self._lock:
            return tuple(
                sorted(
                    self._games.values(),
                    key=lambda game: (game.name.casefold(), game.id),
                )
            )

    def get_game(self, game_id: str) -> Game | None:
        with self._lock:
            return self._games.get(game_id)

    def refresh(self, *, cancel_event: Event | None = None) -> Sequence[Game]:
        """Synchronously rescan metadata; callers may run this in a worker."""

        started_at = datetime.now(UTC)
        timer = perf_counter()
        stats = _ScanStats()
        configured_roots = self.configured_roots
        stats.roots_scanned = len(configured_roots)
        libraries = self._discover_libraries(
            configured_roots, stats, cancel_event=cancel_event
        )
        discovered: dict[str, Game] = {}

        for library in libraries:
            if self._is_cancelled(cancel_event):
                LOGGER.info("Steam scan cancelled before manifest processing completed")
                return self.list_games()
            for manifest_path in self._manifest_paths(
                library.path, stats, cancel_event=cancel_event
            ):
                if self._is_cancelled(cancel_event):
                    LOGGER.info("Steam scan cancelled during manifest processing")
                    return self.list_games()
                stats.manifests_seen += 1
                try:
                    game = self._read_manifest(manifest_path, library, stats)
                except (
                    OSError,
                    UnicodeError,
                    VDFParseError,
                    _ManifestError,
                    ValueError,
                ) as error:
                    stats.invalid_manifests += 1
                    message = f"Invalid Steam manifest {manifest_path}: {error}"
                    stats.errors.append(message)  # type: ignore[union-attr]
                    LOGGER.warning(message)
                    continue

                existing = discovered.get(game.id)
                if existing is not None:
                    stats.duplicate_games += 1
                    # Prefer a usable installation over a stale manifest.
                    if (
                        existing.status is GameStatus.MISSING_FILES
                        and game.status is not GameStatus.MISSING_FILES
                    ):
                        discovered[game.id] = game
                    continue
                discovered[game.id] = game

        if self._is_cancelled(cancel_event):
            LOGGER.info("Steam scan cancelled before applying results")
            return self.list_games()

        with self._lock:
            for game_id, manual_game in self._manual_games.items():
                discovered.setdefault(game_id, manual_game)
            self._games = discovered
            completed_at = datetime.now(UTC)
            self._last_report = ScanReport(
                started_at=started_at,
                completed_at=completed_at,
                elapsed_seconds=max(0.0, perf_counter() - timer),
                steam_found=bool(stats.steam_roots),
                roots_scanned=stats.roots_scanned,
                roots_found=len(stats.steam_roots),  # type: ignore[arg-type]
                libraries_found=len(libraries),
                manifests_seen=stats.manifests_seen,
                invalid_manifests=stats.invalid_manifests,
                games_found=len(discovered),
                duplicate_libraries=stats.duplicate_libraries,
                duplicate_games=stats.duplicate_games,
                steam_roots=tuple(stats.steam_roots),  # type: ignore[arg-type]
                libraries=tuple(library.path for library in libraries),
                configured_libraries=tuple(
                    stats.configured_libraries  # type: ignore[arg-type]
                ),
                inaccessible_paths=tuple(stats.inaccessible_paths),  # type: ignore[arg-type]
                errors=tuple(stats.errors),  # type: ignore[arg-type]
            )
            result = self.list_games()

        report = self.last_report
        LOGGER.info(
            "Steam scan finished in %.3fs: roots=%d libraries=%d "
            "manifests=%d invalid=%d games=%d",
            report.elapsed_seconds,
            report.roots_found,
            report.libraries_found,
            report.manifests_seen,
            report.invalid_manifests,
            report.games_found,
        )
        return result

    def discover_libraries(self) -> tuple[Path, ...]:
        """Discover accessible libraries without changing the game collection."""

        stats = _ScanStats(roots_scanned=len(self.configured_roots))
        return tuple(
            library.path
            for library in self._discover_libraries(self.configured_roots, stats)
        )

    def add_game(self, game: Game) -> Game:
        """Add an in-memory manual record; no Steam files are changed."""

        with self._lock:
            if game.id in self._games or game.id in self._manual_games:
                raise ValueError(f"game already exists: {game.id}")
            self._manual_games[game.id] = game
            self._games[game.id] = game
            return game

    def replace_game(self, game: Game) -> Game | None:
        """Atomically replace a known record (used by background scanners)."""

        with self._lock:
            if game.id not in self._games:
                return None
            self._games[game.id] = game
            if game.id in self._manual_games:
                self._manual_games[game.id] = game
            return game

    update_game = replace_game

    def mark_game_size_calculating(self, game_id: str) -> Game | None:
        with self._lock:
            game = self._games.get(game_id)
            if game is None:
                return None
            updated = replace(
                game,
                size_scan_status=SizeScanStatus.CALCULATING,
                size_scan_error=None,
            )
            self._games[game_id] = updated
            if game_id in self._manual_games:
                self._manual_games[game_id] = updated
            return updated

    def update_game_sizes(
        self,
        game_id: str,
        logical_size_gb: float,
        physical_size_gb: float,
        *,
        error: str | None = None,
    ) -> Game | None:
        """Apply a background size result while holding the provider lock."""

        with self._lock:
            game = self._games.get(game_id)
            if game is None:
                return None
            error_message = (
                error.strip() or "Size scan failed" if error is not None else None
            )
            updated = replace(
                game,
                logical_size_gb=logical_size_gb,
                physical_size_gb=physical_size_gb,
                size_scan_status=(
                    SizeScanStatus.FAILED
                    if error_message is not None
                    else SizeScanStatus.COMPLETED
                ),
                size_scan_error=error_message,
                last_scanned_at=datetime.now(UTC),
            )
            self._games[game_id] = updated
            if game_id in self._manual_games:
                self._manual_games[game_id] = updated
            return updated

    def _discover_libraries(
        self,
        roots: Sequence[Path],
        stats: _ScanStats,
        *,
        cancel_event: Event | None = None,
    ) -> tuple[_SteamLibrary, ...]:
        libraries: list[_SteamLibrary] = []
        seen: set[str] = set()

        def add_library(path: Path, source: str) -> None:
            if self._is_cancelled(cancel_event):
                return
            normalized = self._normalized_path(path)
            key = self._path_key(normalized)
            configured_keys = {
                self._path_key(value)
                for value in stats.configured_libraries or ()
            }
            if key not in configured_keys:
                stats.configured_libraries.append(normalized)  # type: ignore[union-attr]
            if key in seen:
                stats.duplicate_libraries += 1
                return
            try:
                available = normalized.is_dir() and (
                    normalized / "steamapps"
                ).is_dir()
            except OSError as error:
                self._record_path_error(normalized, error, stats)
                return
            if not available:
                stats.inaccessible_paths.append(normalized)  # type: ignore[union-attr]
                LOGGER.warning("Steam library is unavailable: %s", normalized)
                return
            seen.add(key)
            libraries.append(_SteamLibrary(normalized, source))
            LOGGER.info("Found Steam library: %s", normalized)

        for root_value in roots:
            if self._is_cancelled(cancel_event):
                break
            root = self._normalized_path(root_value)
            source = (
                self.SOURCE_FLATPAK
                if self._is_flatpak_root(root)
                else self.SOURCE_STEAM
            )
            library_file = root / "steamapps" / "libraryfolders.vdf"
            try:
                root_is_library = (root / "steamapps").is_dir()
                has_library_file = library_file.is_file()
            except OSError as error:
                self._record_path_error(root, error, stats)
                continue
            if not root_is_library and not has_library_file:
                continue

            stats.steam_roots.append(root)  # type: ignore[union-attr]
            LOGGER.info("Found Steam installation: %s", root)
            if root_is_library:
                add_library(root, source)
            if not has_library_file:
                continue
            try:
                document = load_keyvalues(library_file)
            except VDFParseError as error:
                try:
                    damaged_text = library_file.read_text(
                        encoding="utf-8-sig", errors="strict"
                    )
                except (OSError, UnicodeError):
                    recovered_paths: tuple[Path, ...] = ()
                else:
                    recovered_paths = self._library_paths_from_damaged_text(
                        damaged_text
                    )
                message = f"Cannot parse Steam library list {library_file}: {error}"
                stats.errors.append(message)  # type: ignore[union-attr]
                LOGGER.warning(
                    "%s; recovered %d independent path entries",
                    message,
                    len(recovered_paths),
                )
                for library_path in recovered_paths:
                    if self._is_cancelled(cancel_event):
                        break
                    add_library(library_path, source)
                continue
            except (OSError, UnicodeError) as error:
                message = f"Cannot read Steam library list {library_file}: {error}"
                stats.errors.append(message)  # type: ignore[union-attr]
                LOGGER.warning(message)
                continue

            for library_path in self._library_paths_from_document(document):
                if self._is_cancelled(cancel_event):
                    break
                add_library(library_path, source)

        return tuple(libraries)

    def _manifest_paths(
        self,
        library: Path,
        stats: _ScanStats,
        *,
        cancel_event: Event | None = None,
    ) -> tuple[Path, ...]:
        steamapps = library / "steamapps"
        try:
            entries = tuple(steamapps.iterdir())
        except OSError as error:
            self._record_path_error(steamapps, error, stats)
            return ()
        manifests: list[Path] = []
        for entry in entries:
            if self._is_cancelled(cancel_event):
                break
            if not _MANIFEST_NAME.match(entry.name):
                continue
            try:
                if not entry.is_symlink() and entry.is_file():
                    manifests.append(entry)
            except OSError as error:
                self._record_path_error(entry, error, stats)
        return tuple(sorted(manifests, key=lambda path: path.name.casefold()))

    def _read_manifest(
        self, manifest_path: Path, library: _SteamLibrary, stats: _ScanStats
    ) -> Game:
        document, manifest_stat = self._load_stable_manifest(manifest_path)
        app_state_value = self._casefold_get(document, "appstate")
        if not isinstance(app_state_value, Mapping):
            raise _ManifestError("missing AppState section")
        app_state = app_state_value

        app_id = self._required_scalar(app_state, "appid")
        if not app_id.isascii() or not app_id.isdecimal() or int(app_id) <= 0:
            raise _ManifestError("AppID must be a positive decimal number")
        name = self._required_scalar(app_state, "name")
        install_dir = self._required_scalar(app_state, "installdir")
        install_path = self._safe_install_path(library.path, install_dir)

        size_bytes = self._non_negative_integer(
            self._casefold_get(app_state, "sizeondisk")
        )
        size_gib = bytes_to_gib(size_bytes)
        state_flags = self._optional_non_negative_integer(
            self._casefold_get(app_state, "stateflags")
        )
        build_id_value = self._casefold_get(app_state, "buildid")
        build_id = (
            build_id_value.strip()
            if isinstance(build_id_value, str) and build_id_value.strip()
            else None
        )
        last_updated = self._timestamp(
            self._casefold_get(app_state, "lastupdated")
        )
        language_value = self._casefold_get(app_state, "language")
        if not isinstance(language_value, str):
            user_config = self._casefold_get(app_state, "userconfig")
            if isinstance(user_config, Mapping):
                language_value = self._casefold_get(user_config, "language")
        language = (
            language_value.strip()
            if isinstance(language_value, str) and language_value.strip()
            else None
        )

        try:
            installed = install_path.is_dir()
        except OSError as error:
            installed = False
            self._record_path_error(install_path, error, stats)
        filesystem = self._inspect_filesystem(install_path, stats)
        artwork = find_local_steam_artwork(app_id, stats.steam_roots or ())
        return Game(
            id=f"steam-{app_id}",
            steam_app_id=app_id,
            name=name,
            launcher=Launcher.STEAM,
            install_path=install_path,
            library_path=library.path,
            logical_size_gb=size_gib,
            physical_size_gb=size_gib,
            filesystem=filesystem.filesystem,
            compression_available=filesystem.compression_supported,
            status=GameStatus.READY if installed else GameStatus.MISSING_FILES,
            cover_asset=str(artwork.preferred_path or ""),
            portrait_artwork_path=artwork.portrait_artwork_path,
            header_artwork_path=artwork.header_artwork_path,
            fallback_artwork_path=artwork.fallback_artwork_path,
            data_source=library.source,
            last_scanned_at=datetime.now(UTC),
            last_updated_at=last_updated,
            language=language,
            state_flags=state_flags,
            steam_build_id=build_id,
            steam_manifest_path=Path(os.path.abspath(manifest_path)),
            steam_manifest_mtime_ns=manifest_stat.st_mtime_ns,
            steam_manifest_size_bytes=manifest_stat.st_size,
            steam_size_on_disk_bytes=size_bytes,
            update_in_progress=self._steam_update_in_progress(state_flags),
            size_scan_status=SizeScanStatus.NOT_REQUESTED,
            filesystem_name=(
                filesystem.filesystem_name or filesystem.filesystem.value
            ),
            mount_point=filesystem.mount_point,
            filesystem_device=filesystem.device,
            mount_options=filesystem.mount_options,
            is_writable=filesystem.writable,
            is_steam_tool=is_steam_tool_name(name),
        )

    @staticmethod
    def _load_stable_manifest(
        manifest_path: Path,
    ) -> tuple[Mapping[str, KVValue], os.stat_result]:
        """Read one regular manifest through a no-follow descriptor.

        Steam commonly replaces manifests atomically. Comparing both descriptor
        statistics and the final path identity prevents a mixed or obsolete
        observation from being published as the current installation state.
        """

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(manifest_path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise _ManifestError("manifest is not a regular file")
            if before.st_size > _MAX_MANIFEST_BYTES:
                raise _ManifestError("manifest exceeds the safe size limit")

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_MANIFEST_BYTES:
                    raise _ManifestError("manifest exceeds the safe size limit")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            path_after = os.stat(manifest_path, follow_symlinks=False)
        finally:
            os.close(descriptor)

        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        path_identity = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
            path_after.st_ctime_ns,
        )
        if identity_before != identity_after or identity_after != path_identity:
            raise _ManifestError("manifest changed while it was read")

        text = b"".join(chunks).decode("utf-8-sig", errors="strict")
        return parse_keyvalues(text), after

    @staticmethod
    def _steam_update_in_progress(state_flags: int | None) -> bool:
        if state_flags is None:
            return False
        return bool(state_flags & _STEAM_ACTIVE_WRITE_STATE_MASK)

    def _inspect_filesystem(
        self, path: Path, stats: _ScanStats
    ) -> FilesystemInfo:
        try:
            return self._filesystem_provider.inspect(path)
        except Exception as error:  # provider boundary: keep scanning other games
            message = f"Cannot inspect filesystem for {path}: {error}"
            stats.errors.append(message)  # type: ignore[union-attr]
            LOGGER.warning(message)
            return FilesystemInfo(
                mount_point=path,
                filesystem=FilesystemType.UNKNOWN,
                compression_supported=False,
                label="Filesystem unavailable",
                filesystem_name="Unknown",
            )

    @staticmethod
    def _library_paths_from_document(
        document: Mapping[str, KVValue],
    ) -> tuple[Path, ...]:
        section_value = SteamGameProvider._casefold_get(
            document, "libraryfolders"
        )
        section = section_value if isinstance(section_value, Mapping) else document
        paths: list[Path] = []
        for key, value in section.items():
            if not str(key).strip().isdecimal():
                continue
            path_value: KVValue | None = value
            if isinstance(value, Mapping):
                path_value = SteamGameProvider._casefold_get(value, "path")
            if not isinstance(path_value, str) or not path_value.strip():
                continue
            path = SteamGameProvider._absolute_library_path(path_value)
            if path is not None:
                paths.append(path)
        return tuple(paths)

    @staticmethod
    def _library_paths_from_damaged_text(text: str) -> tuple[Path, ...]:
        """Recover independent absolute path entries from tokenizable data.

        This fallback never tries to repair the full document.  It only
        accepts the two known library entry shapes (numeric key plus scalar
        path, or numeric key plus an object containing ``path``).
        """

        tokens = tokenize_keyvalues(text, allow_partial=True)
        start = 0
        for index in range(len(tokens) - 1):
            if (
                tokens[index][0] == "value"
                and tokens[index][1].casefold() == "libraryfolders"
                and tokens[index + 1][0] == "open"
            ):
                start = index + 2
                break

        recovered: list[Path] = []
        for index in range(start, len(tokens) - 1):
            kind, key = tokens[index]
            if kind != "value" or not key.strip().isdecimal():
                continue
            next_kind, next_value = tokens[index + 1]
            if next_kind == "value":
                path = SteamGameProvider._absolute_library_path(next_value)
                if path is not None:
                    recovered.append(path)
                continue
            if next_kind != "open":
                continue

            depth = 1
            cursor = index + 2
            while cursor < len(tokens) - 1 and depth > 0:
                token_kind, token_value = tokens[cursor]
                if token_kind == "open":
                    depth += 1
                elif token_kind == "close":
                    depth -= 1
                elif (
                    token_kind == "value"
                    and token_value.casefold() == "path"
                    and tokens[cursor + 1][0] == "value"
                ):
                    path = SteamGameProvider._absolute_library_path(
                        tokens[cursor + 1][1]
                    )
                    if path is not None:
                        recovered.append(path)
                    break
                cursor += 1
        return tuple(recovered)

    @staticmethod
    def _absolute_library_path(value: str) -> Path | None:
        if "\x00" in value:
            return None
        path = Path(value.strip())
        # Steam writes absolute paths. Reject relative or tilde-prefixed data
        # rather than resolving it against the application's environment.
        return path if path.is_absolute() else None

    @staticmethod
    def _safe_install_path(library: Path, install_dir: str) -> Path:
        if "\x00" in install_dir:
            raise _ManifestError("installdir contains a null byte")
        normalized_value = install_dir.replace("\\", "/")
        relative = Path(normalized_value)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise _ManifestError("installdir escapes steamapps/common")
        useful_parts = tuple(part for part in relative.parts if part not in ("", "."))
        if not useful_parts:
            raise _ManifestError("installdir is empty")
        common = Path(os.path.abspath(library / "steamapps" / "common"))
        candidate = Path(os.path.abspath(common.joinpath(*useful_parts)))
        if not candidate.is_relative_to(common):
            raise _ManifestError("installdir escapes steamapps/common")
        return candidate

    @staticmethod
    def _required_scalar(mapping: Mapping[str, KVValue], key: str) -> str:
        value = SteamGameProvider._casefold_get(mapping, key)
        if not isinstance(value, str) or not value.strip():
            raise _ManifestError(f"missing {key}")
        return value.strip()

    @staticmethod
    def _casefold_get(
        mapping: Mapping[str, KVValue], key: str
    ) -> KVValue | None:
        folded = key.casefold()
        for candidate, value in mapping.items():
            if str(candidate).casefold() == folded:
                return value
        return None

    @staticmethod
    def _non_negative_integer(value: KVValue | None) -> int:
        parsed = SteamGameProvider._optional_non_negative_integer(value)
        return parsed if parsed is not None else 0

    @staticmethod
    def _optional_non_negative_integer(value: KVValue | None) -> int | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = int(value.strip(), 10)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _timestamp(value: KVValue | None) -> datetime | None:
        seconds = SteamGameProvider._optional_non_negative_integer(value)
        if seconds is None:
            return None
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _is_flatpak_root(path: Path) -> bool:
        return "com.valvesoftware.Steam" in path.parts and ".var" in path.parts

    @staticmethod
    def _normalized_path(path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        try:
            return candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return Path(os.path.abspath(candidate))

    @classmethod
    def _path_key(cls, path: str | Path) -> str:
        return os.path.normcase(os.fspath(cls._normalized_path(path)))

    @classmethod
    def _deduplicate_paths(
        cls, paths: Iterable[str | Path]
    ) -> tuple[Path, ...]:
        result: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            normalized = cls._normalized_path(path)
            key = cls._path_key(normalized)
            if key not in seen:
                seen.add(key)
                result.append(normalized)
        return tuple(result)

    @staticmethod
    def _record_path_error(
        path: Path, error: Exception, stats: _ScanStats
    ) -> None:
        stats.inaccessible_paths.append(path)  # type: ignore[union-attr]
        message = f"Steam path unavailable {path}: {error}"
        stats.errors.append(message)  # type: ignore[union-attr]
        LOGGER.warning(message)

    @staticmethod
    def _is_cancelled(cancel_event: Event | None) -> bool:
        return cancel_event is not None and cancel_event.is_set()


__all__ = [
    "ScanReport",
    "SteamArtworkPaths",
    "SteamGameProvider",
    "find_local_steam_artwork",
    "find_local_steam_cover",
]
