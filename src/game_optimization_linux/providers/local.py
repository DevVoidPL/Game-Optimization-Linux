from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shlex
from threading import Event, RLock
from uuid import uuid4

from game_optimization_linux.models import (
    FilesystemType,
    Game,
    GameStatus,
    Launcher,
    ManualGameConfig,
    SizeScanStatus,
)
from game_optimization_linux.services.game_executable import GameExecutableResolver
from game_optimization_linux.services.manual_games import ManualGameStore

from .base import FilesystemProvider, GameProvider


logger = logging.getLogger(__name__)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class LocalGameProvider(GameProvider):
    def __init__(
        self,
        filesystem_provider: FilesystemProvider,
        roots: Iterable[str | Path],
        *,
        choices_path: Path,
        executable_resolver: GameExecutableResolver | None = None,
        manual_store: ManualGameStore | None = None,
    ) -> None:
        self._filesystem_provider = filesystem_provider
        self._resolver = executable_resolver or GameExecutableResolver()
        self._choices_path = Path(choices_path)
        self._roots = self._normalize_roots(roots)
        self._choices = self._load_choices()
        self._manual_store = manual_store
        self._games: dict[str, Game] = {}
        self._lock = RLock()

    @staticmethod
    def _normalize_roots(values: Iterable[str | Path]) -> tuple[Path, ...]:
        unique: dict[str, Path] = {}
        for value in values:
            try:
                path = Path(value).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if path.is_dir():
                unique.setdefault(_path_key(path), path)
        return tuple(unique.values())

    @property
    def roots(self) -> tuple[Path, ...]:
        with self._lock:
            return self._roots

    def set_roots(self, roots: Iterable[str | Path]) -> None:
        normalized = self._normalize_roots(roots)
        with self._lock:
            self._roots = normalized

    def list_games(self) -> Sequence[Game]:
        with self._lock:
            return tuple(sorted(self._games.values(), key=lambda game: (game.name.casefold(), game.id)))

    def get_game(self, game_id: str) -> Game | None:
        with self._lock:
            return self._games.get(str(game_id))

    def add_game(self, game: Game) -> Game:
        raise ValueError("local games are discovered from configured directories")

    def refresh(self, *, cancel_event: Event | None = None) -> Sequence[Game]:
        discovered: dict[str, Game] = {}
        for root in self.roots:
            if cancel_event is not None and cancel_event.is_set():
                return self.list_games()
            try:
                children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
            except OSError as error:
                logger.warning("Could not scan local game directory %s: %s", root, error)
                continue
            for child in children:
                if cancel_event is not None and cancel_event.is_set():
                    return self.list_games()
                game = self._game_from_child(root, child)
                if game is not None:
                    discovered[game.id] = game
        with self._lock:
            # Serialize the short store snapshot with explicit add/edit/remove
            # operations. A scan that started earlier must not publish stale
            # manual records after a user save completes.
            if self._manual_store is not None:
                for configuration in self._manual_store.load():
                    if cancel_event is not None and cancel_event.is_set():
                        return self.list_games()
                    discovered[configuration.id] = self._manual_game(configuration)
            self._games = discovered
            return self.list_games()

    def _inspect_game_path(self, path: Path) -> dict[str, object]:
        try:
            filesystem = self._filesystem_provider.inspect(path)
        except Exception as error:
            logger.debug("Could not inspect manual/local game %s: %s", path, error)
            return {
                "filesystem": FilesystemType.UNKNOWN,
                "filesystem_name": "unknown",
                "compression_available": False,
                "mount_point": None,
                "filesystem_device": None,
                "mount_options": (),
                "is_writable": None,
            }
        return {
            "filesystem": filesystem.filesystem,
            "filesystem_name": filesystem.filesystem_name or filesystem.filesystem.value,
            "compression_available": filesystem.compression_supported,
            "mount_point": filesystem.mount_point,
            "filesystem_device": filesystem.device,
            "mount_options": filesystem.mount_options,
            "is_writable": filesystem.writable,
        }

    def _manual_game(self, configuration: ManualGameConfig) -> Game:
        install_available = configuration.install_directory.is_dir()
        executable_available = configuration.executable.is_file()
        working_available = bool(
            configuration.working_directory is None
            or configuration.working_directory.is_dir()
        )
        prefix_available = bool(
            configuration.wine_prefix is None or configuration.wine_prefix.is_dir()
        )
        runner_available = True
        if configuration.runner_command:
            runner = configuration.runner_command[0]
            runner_path = Path(runner)
            if runner_path.is_absolute():
                runner_available = runner_path.is_file()
        executable_permitted = bool(
            executable_available
            and (
                configuration.runner_command
                or os.access(configuration.executable, os.X_OK)
            )
        )
        unavailable_reason = ""
        if not install_available:
            unavailable_reason = "The configured game directory is missing or inaccessible"
        elif not executable_available:
            unavailable_reason = "The configured game executable is missing or inaccessible"
        elif not executable_permitted:
            unavailable_reason = "The configured native executable is not executable"
        elif not working_available:
            unavailable_reason = "The configured working directory is missing or inaccessible"
        elif not prefix_available:
            unavailable_reason = "The configured Wine prefix is missing or inaccessible"
        elif not runner_available:
            unavailable_reason = "The configured Wine/Proton executable is missing"

        metadata = self._inspect_game_path(configuration.install_directory)
        return Game(
            id=configuration.id,
            name=configuration.name,
            launcher=Launcher.MANUAL,
            launcher_game_id=configuration.stable_uuid,
            install_path=configuration.install_directory,
            library_path=configuration.install_directory.parent,
            logical_size_gb=0.0,
            physical_size_gb=0.0,
            status=(GameStatus.READY if not unavailable_reason else GameStatus.MISSING_FILES),
            data_source="Manual",
            last_scanned_at=datetime.now(UTC),
            size_scan_status=SizeScanStatus.NOT_REQUESTED,
            portrait_artwork_path=configuration.portrait_artwork,
            header_artwork_path=configuration.header_artwork,
            executable_path=str(configuration.executable),
            executable_resolution="selected" if executable_available else "missing",
            executable_candidates=(str(configuration.executable),),
            store="custom",
            runner=shlex.join(configuration.runner_command),
            wine_prefix=configuration.wine_prefix,
            working_directory=configuration.working_directory,
            launch_available=not unavailable_reason,
            launch_unavailable_reason=unavailable_reason,
            library_available=install_available,
            **metadata,
        )

    def manual_config(self, game_id: str) -> ManualGameConfig | None:
        if self._manual_store is None:
            return None
        return self._manual_store.get(game_id)

    def save_manual_config(self, configuration: ManualGameConfig) -> Game:
        if self._manual_store is None:
            raise ValueError("manual game storage is unavailable")
        with self._lock:
            self._manual_store.upsert(configuration)
            game = self._manual_game(configuration)
            self._games[game.id] = game
        return game

    def remove_manual_config(self, game_id: str) -> ManualGameConfig:
        if self._manual_store is None:
            raise ValueError("manual game storage is unavailable")
        with self._lock:
            removed = self._manual_store.remove(game_id)
            self._games.pop(game_id, None)
        return removed

    def _game_from_child(self, root: Path, child: Path) -> Game | None:
        try:
            if child.is_symlink() or not child.is_dir():
                return None
            canonical = child.resolve(strict=True)
            canonical.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
        identifier = "local-" + sha256(os.fsencode(_path_key(canonical))).hexdigest()[:24]
        try:
            filesystem = self._filesystem_provider.inspect(canonical)
        except Exception as error:
            logger.warning("Could not inspect local game %s: %s", canonical, error)
            filesystem_type = FilesystemType.UNKNOWN
            filesystem_name = "unknown"
            compression_supported = False
            mount_point = None
            device = None
            mount_options: tuple[str, ...] = ()
            writable = None
        else:
            filesystem_type = filesystem.filesystem
            filesystem_name = filesystem.filesystem_name or filesystem.filesystem.value
            compression_supported = filesystem.compression_supported
            mount_point = filesystem.mount_point
            device = filesystem.device
            mount_options = filesystem.mount_options
            writable = filesystem.writable
        game = Game(
            id=identifier,
            name=child.name,
            launcher=Launcher.MANUAL,
            install_path=canonical,
            library_path=root,
            logical_size_gb=0.0,
            physical_size_gb=0.0,
            filesystem=filesystem_type,
            filesystem_name=filesystem_name,
            compression_available=compression_supported,
            status=GameStatus.READY,
            data_source="Local",
            last_scanned_at=datetime.now(UTC),
            size_scan_status=SizeScanStatus.NOT_REQUESTED,
            mount_point=mount_point,
            filesystem_device=device,
            mount_options=mount_options,
            is_writable=writable,
        )
        resolution = self._resolver.resolve(game, self._choices.get(identifier, ""))
        if not resolution.candidates and resolution.selected is None:
            return None
        return replace(
            game,
            executable_path=(resolution.selected.relative_path if resolution.selected else ""),
            executable_resolution=resolution.status,
            executable_candidates=tuple(
                candidate.relative_path for candidate in resolution.candidates
            ),
        )

    def select_executable(self, game_id: str, executable: str) -> Game:
        with self._lock:
            game = self._games.get(str(game_id))
        if game is None:
            raise ValueError("local game was not found")
        selected = self._resolver.validate_selected(game, executable)
        if selected is None:
            raise ValueError("executable must be a game file inside the local game directory")
        self._choices[game.id] = selected.relative_path
        self._save_choices()
        updated = replace(
            game,
            executable_path=selected.relative_path,
            executable_resolution="selected",
            executable_candidates=tuple(
                dict.fromkeys((*game.executable_candidates, selected.relative_path))
            ),
        )
        with self._lock:
            self._games[game.id] = updated
        return updated

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
                size_scan_status=(SizeScanStatus.FAILED if error else SizeScanStatus.COMPLETED),
                size_scan_error=error,
                last_scanned_at=datetime.now(UTC),
            )
            self._games[game_id] = updated
            return updated

    def _load_choices(self) -> dict[str, str]:
        try:
            raw = json.loads(self._choices_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if not isinstance(choices, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in choices.items()
            if str(key).startswith("local-") and isinstance(value, str) and value
        }

    def _save_choices(self) -> None:
        payload = json.dumps(
            {"version": 1, "choices": self._choices},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        self._choices_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._choices_path.with_name(
            f".{self._choices_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._choices_path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise


class ConfiguredGameProvider(GameProvider):
    """Aggregate launcher providers while keeping their scans independent."""

    def __init__(
        self,
        steam: GameProvider,
        local: LocalGameProvider,
        *,
        heroic: GameProvider | None = None,
        lutris: GameProvider | None = None,
    ) -> None:
        self.steam = steam
        self.local = local
        self.heroic = heroic
        self.lutris = lutris
        self._provider_errors: dict[Launcher, str] = {}
        self._lock = RLock()

    @property
    def providers(self) -> tuple[GameProvider, ...]:
        return tuple(
            provider
            for provider in (self.steam, self.heroic, self.lutris, self.local)
            if provider is not None
        )

    @property
    def last_report(self):
        return getattr(self.steam, "last_report", None)

    @property
    def last_reports(self) -> dict[str, object]:
        return {
            launcher.value: report
            for launcher, provider in (
                (Launcher.STEAM, self.steam),
                (Launcher.HEROIC, self.heroic),
                (Launcher.LUTRIS, self.lutris),
            )
            if provider is not None
            and (report := getattr(provider, "last_report", None)) is not None
        }

    @property
    def provider_errors(self) -> dict[str, str]:
        with self._lock:
            return {launcher.value: message for launcher, message in self._provider_errors.items()}

    @property
    def failed_launchers(self) -> tuple[Launcher, ...]:
        with self._lock:
            return tuple(self._provider_errors)

    @property
    def steam_found(self) -> bool:
        return bool(getattr(self.steam, "steam_found", False))

    @property
    def configured_roots(self):
        return getattr(self.steam, "configured_roots", ())

    def set_additional_roots(self, roots: Iterable[str | Path]) -> None:
        setter = getattr(self.steam, "set_additional_roots")
        setter(roots)

    def set_local_roots(self, roots: Iterable[str | Path]) -> None:
        self.local.set_roots(roots)

    def discover_libraries(self):
        method = getattr(self.steam, "discover_libraries")
        return method()

    def refresh(self, *, cancel_event: Event | None = None) -> Sequence[Game]:
        providers = self.providers
        launcher_by_provider = {
            id(self.steam): Launcher.STEAM,
            id(self.local): Launcher.MANUAL,
        }
        if self.heroic is not None:
            launcher_by_provider[id(self.heroic)] = Launcher.HEROIC
        if self.lutris is not None:
            launcher_by_provider[id(self.lutris)] = Launcher.LUTRIS
        errors: dict[Launcher, str] = {}
        # The outer LibraryScanner already owns a worker thread. Running the
        # independent bounded providers concurrently prevents one slow root or
        # database from serially delaying all other launchers.
        with ThreadPoolExecutor(
            max_workers=min(4, len(providers)),
            thread_name_prefix="game-provider",
        ) as executor:
            futures = {
                executor.submit(provider.refresh, cancel_event=cancel_event): provider
                for provider in providers
            }
            for future in as_completed(futures):
                provider = futures[future]
                if cancel_event is not None and cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    return self.list_games()
                try:
                    future.result()
                except Exception as error:
                    launcher = launcher_by_provider[id(provider)]
                    errors[launcher] = str(error).strip() or type(error).__name__
                    logger.exception("%s library provider failed", launcher.value)
        with self._lock:
            self._provider_errors = errors
        return self.list_games()

    def list_games(self) -> Sequence[Game]:
        return tuple(
            sorted(
                (
                    game
                    for provider in self.providers
                    for game in provider.list_games()
                ),
                key=lambda game: (game.name.casefold(), game.id),
            )
        )

    def get_game(self, game_id: str) -> Game | None:
        for provider in self.providers:
            game = provider.get_game(game_id)
            if game is not None:
                return game
        return None

    def add_game(self, game: Game) -> Game:
        return self.steam.add_game(game)

    def update_game_sizes(self, game_id: str, *args, **kwargs) -> Game | None:
        prefix_map = {
            "local-": self.local,
            "manual-": self.local,
            "heroic-": self.heroic,
            "lutris-": self.lutris,
        }
        provider = next(
            (
                candidate
                for prefix, candidate in prefix_map.items()
                if str(game_id).startswith(prefix) and candidate is not None
            ),
            self.steam,
        )
        method = getattr(provider, "update_game_sizes", None)
        return method(game_id, *args, **kwargs) if callable(method) else None

    def select_local_executable(self, game_id: str, executable: str) -> Game:
        return self.local.select_executable(game_id, executable)

    def manual_config(self, game_id: str) -> ManualGameConfig | None:
        return self.local.manual_config(game_id)

    def save_manual_config(self, configuration: ManualGameConfig) -> Game:
        return self.local.save_manual_config(configuration)

    def remove_manual_config(self, game_id: str) -> ManualGameConfig:
        return self.local.remove_manual_config(game_id)


__all__ = ["ConfiguredGameProvider", "LocalGameProvider"]
