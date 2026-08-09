from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import (
    BackupStatus,
    FilesystemType,
    Game,
    GameStatus,
    Launcher,
    OptimizationProfile,
    SizeScanStatus,
    TextureCompatibility,
)
from ..services import GameUpdateRecord, aggregate_library_compression
from .presenters import game_to_qml, settings_to_qml

if TYPE_CHECKING:
    from .app_controller import AppController, GameProviderLike

logger = logging.getLogger(__name__)


class LibraryController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    def refreshGames(self) -> bool:
        """Queue a debounced manual library refresh."""

        accepted = self._app.requestLibraryScan("legacy_refresh_slot", "", "manual")
        # Preserve the public slot's historical non-blocking/immediate start
        # contract. Named UI/watcher requests use requestLibraryScan directly
        # and receive the debounce window.
        if accepted and self._app._scan_debounce_timer.isActive():
            self._app._scan_debounce_timer.stop()
            self._app._start_requested_library_scan()
        return accepted

    def requestLibraryScan(
        self,
        trigger: str,
        event_path: str = "",
        event_kind: str = "manual",
    ) -> bool:
        """Coalesce high-level Steam inventory events into bounded scans."""

        if self._app._shutdown_requested:
            return False
        normalized_trigger = str(trigger).strip() or "unknown"
        normalized_path = str(event_path).strip()
        classified = self._app._classify_library_event(normalized_path, event_kind)
        active_task = self._app._has_active_game_optimization_task()
        group = self._app._scan_request_groups.setdefault(
            classified,
            {"count": 0, "triggers": set(), "paths": set(), "active_task": False},
        )
        group["count"] += 1
        group["triggers"].add(normalized_trigger)
        if normalized_path and len(group["paths"]) < 5:
            group["paths"].add(normalized_path)
        group["active_task"] = bool(group["active_task"] or active_task)

        if classified == "game_file":
            self._app._ignored_scan_event_timer.start()
            return False
        if self._app._scan_worker_active:
            self._app._scan_retry_pending = True
            decision = "coalesced_retry"
        elif self._app._scan_debounce_timer.isActive():
            decision = "coalesced_debounce"
        else:
            self._app._set_scan_state(
                status="scan-queued",
                message="Steam library refresh queued",
                is_scanning=True,
            )
            self._app._scan_debounce_timer.start()
            decision = "scheduled"
        logger.info(
            "Steam scan request: trigger=%s path=%s time=%s activeTask=%s "
            "source=%s decision=%s",
            normalized_trigger,
            normalized_path or "-",
            datetime.now(UTC).isoformat(),
            str(active_task).lower(),
            classified,
            decision,
        )
        return True

    def _start_requested_library_scan(self) -> None:
        if self._app._shutdown_requested or self._app._scan_worker_active:
            return
        groups = self._app._scan_request_groups
        self._app._scan_request_groups = {}
        logger.info(
            "Steam scan batch starting: decision=executed groups=%s "
            "currentGeneration=%d games=%d",
            self._app._format_scan_request_groups(groups),
            self._app._games_model_generation,
            len(self._app._games),
        )
        try:
            self._app._active_scan_generation = self._app._library_scanner.start(
                self._app._game_provider,
                directory_scanner=(
                    None if self._app._demo_mode else self._app._directory_size_scanner
                ),
            )
        except Exception as error:
            self._app._scan_worker_active = False
            self._app._set_scan_state(is_scanning=False)
            self._app._report_error("starting the library scan", error)
            return
        self._app._scan_worker_active = True

    def _flush_ignored_scan_events(self) -> None:
        group = self._app._scan_request_groups.pop("game_file", None)
        if not group:
            return
        logger.info(
            "Steam scan events grouped: time=%s source=game_file count=%d "
            "triggers=%s paths=%s activeTask=%s decision=ignored",
            datetime.now(UTC).isoformat(),
            int(group["count"]),
            sorted(group["triggers"]),
            sorted(group["paths"]),
            str(bool(group["active_task"])).lower(),
        )

    def _has_active_game_optimization_task(self) -> bool:
        active = {"queued", "running", "paused", "cancelling"}
        return any(
            str(row.get("status") or "").casefold() in active
            for row in (*self._app._tasks, *self._app._operational_tasks.values())
        )

    def forgetLibrary(self, library_path: str) -> bool:
        """Forget app cache for a library only after current Steam config drops it."""

        raw_path = str(library_path).strip()
        if not raw_path or self._app._demo_mode:
            return False
        candidate = Path(raw_path).expanduser()
        try:
            normalized = Path(os.path.abspath(os.fspath(candidate)))
        except (OSError, TypeError, ValueError):
            return False
        configured = self._app._provider_configured_library_paths()
        if any(self._app._same_path(normalized, path) for path in configured):
            self._app._emit_toast(
                "Remove this library from Steam before forgetting its cache",
                "warning",
            )
            return False
        try:
            if normalized.exists():
                self._app._emit_toast(
                    "The library path still exists and cannot be forgotten safely",
                    "warning",
                )
                return False
        except OSError:
            return False
        game_ids = {
            game.id
            for game in self._app._domain_games.values()
            if self._app._path_is_within_library(game, normalized)
        }
        if not game_ids:
            return False
        if any(self._app._active_task_for_game(game_id) is not None for game_id in game_ids):
            self._app._emit_toast(
                "A task for this library is still active",
                "warning",
            )
            return False

        for row in self._app._updates:
            if str(row.get("gameId", "")) not in game_ids:
                continue
            row_id = str(row.get("rowId", ""))
            if row_id:
                self._app._dismissed_updates[row_id] = str(
                    row.get("displayVersion") or row_id
                )
        for game_id in game_ids:
            self._app._domain_games.pop(game_id, None)
            self._app._analysis_reports.pop(game_id, None)
            self._app._pending_automatic_games.discard(game_id)
        tracker = self._app._update_tracker
        forget = getattr(tracker, "forget_games", None)
        if callable(forget):
            try:
                forget(tuple(game_ids))
            except Exception as error:
                logger.warning("Could not forget update records: %s", error)
        self._app._save_update_display_state()
        self._app._save_library_cache()
        self._app._reload_games()
        self._app._reload_tasks()
        self._app._reload_updates()
        self._app._reload_system_info()
        self._app._emit_toast("Library cache was forgotten", "success")
        return True

    def ignoreLibrary(self, library_path: str) -> bool:
        """Hide one Steam library locally without editing Steam configuration."""

        normalized = self._app._canonical_library_path(library_path)
        if normalized is None or self._app._demo_mode:
            return False
        game_ids = {
            game.id
            for game in self._app._domain_games.values()
            if self._app._path_is_within_library(game, normalized)
        }
        configured = any(
            self._app._same_path(normalized, path)
            for path in self._app._provider_configured_library_paths()
        )
        if not game_ids and not configured:
            return False
        if any(self._app._active_task_for_game(game_id) is not None for game_id in game_ids):
            self._app._emit_toast("A task for this library is still active", "warning")
            return False

        ignored = list(self._app._settings_model.ignored_steam_libraries)
        if not any(self._app._same_path(normalized, path) for path in ignored):
            ignored.append(normalized)
        updated_settings = replace(
            self._app._settings_model,
            ignored_steam_libraries=tuple(ignored),
        )
        try:
            self._app._settings_store.save(updated_settings)
        except Exception as error:
            self._app._report_error("saving the ignored Steam library", error)
            return False
        self._app._settings_model = updated_settings
        self._app._settings = settings_to_qml(updated_settings)
        self._app.settingsChanged.emit()

        for row in self._app._updates:
            if str(row.get("gameId", "")) not in game_ids:
                continue
            row_id = str(row.get("rowId", ""))
            if row_id:
                self._app._dismissed_updates[row_id] = str(
                    row.get("displayVersion") or row_id
                )
        for game_id in game_ids:
            self._app._domain_games.pop(game_id, None)
            self._app._analysis_reports.pop(game_id, None)
            self._app._pending_automatic_games.discard(game_id)
        tracker = self._app._update_tracker
        forget = getattr(tracker, "forget_games", None)
        if callable(forget) and game_ids:
            try:
                forget(tuple(game_ids))
            except Exception as error:
                logger.warning("Could not forget ignored-library updates: %s", error)
        self._app._save_update_display_state()
        self._app._save_library_cache()
        self._app._reload_games()
        self._app._reload_tasks()
        self._app._reload_updates()
        self._app._reload_system_info()
        self._app._emit_toast("Library was forgotten in Game Optimization", "success")
        return True

    def restoreIgnoredLibrary(self, library_path: str) -> bool:
        """Remove a local ignored-library record and schedule a safe refresh."""

        normalized = self._app._canonical_library_path(library_path)
        if normalized is None:
            return False
        retained = tuple(
            path
            for path in self._app._settings_model.ignored_steam_libraries
            if not self._app._same_path(path, normalized)
        )
        if len(retained) == len(self._app._settings_model.ignored_steam_libraries):
            return False
        updated_settings = replace(
            self._app._settings_model,
            ignored_steam_libraries=retained,
        )
        try:
            self._app._settings_store.save(updated_settings)
        except Exception as error:
            self._app._report_error("restoring the ignored Steam library", error)
            return False
        self._app._settings_model = updated_settings
        self._app._settings = settings_to_qml(updated_settings)
        self._app.settingsChanged.emit()
        self._app._emit_toast("Library was restored in Game Optimization", "success")
        if not self._app._is_scanning:
            self._app.requestLibraryScan(
                "restore_ignored_library",
                str(normalized),
                "library_added",
            )
        return True

    def addManualGame(self) -> bool:
        """Add a descriptive in-memory entry; no path is touched or scanned."""

        if not self._app._demo_mode:
            self._app._emit_toast(
                "Manual game entries are available only in Demo mode",
                "info",
            )
            return False

        try:
            game = self._app._new_manual_game()
            self._app._game_provider.add_game(game)
            self._app._set_domain_games(
                self._app._game_provider.list_games(),
                reason="manual_game_added",
            )
        except Exception as error:
            self._app._report_error("adding a manual demo game", error)
            return False

        logger.info("Added in-memory manual demo game %s", game.id)
        self._app._emit_toast(f"Added {game.name} to the demo library", "success")
        return True

    def openGame(self, game_id: str) -> bool:
        game = self._app._find_game(game_id)
        if game is None:
            self._app._emit_toast("The selected game could not be found", "error")
            return False

        self._app._selected_game_id = game.id
        compression_result = self._app._latest_compression_results().get(game.id)
        verification_result = self._app._latest_verification_results().get(game.id)
        self._app._selected_game = self._app._present_game(
            game,
            analysis_report=self._app._analysis_reports.get(game.id),
            compression_result=compression_result,
            verification_result=verification_result,
            benchmark_estimate=self._app._benchmark_estimates.estimate_for(game),
        )
        self._app._reload_selected_history()
        self._app.selectedGameChanged.emit()
        self._app._set_current_page("gameDetails")
        return True

    def localExecutableInfo(self, game_id: str) -> dict[str, Any]:
        game = self._app._find_game(game_id)
        if game is None or game.launcher is not Launcher.MANUAL:
            return {"available": False, "error": "This is not a local game"}
        return {
            "available": True,
            "gameId": game.id,
            "selected": game.executable_path,
            "status": game.executable_resolution,
            "candidates": list(game.executable_candidates),
        }

    def selectLocalExecutable(self, game_id: str, executable: str) -> bool:
        selector = getattr(self._app._game_provider, "select_local_executable", None)
        if not callable(selector):
            return False
        try:
            updated = selector(str(game_id), str(executable))
        except Exception as error:
            self._app._report_error("selecting the local game executable", error)
            return False
        self._app._domain_games[updated.id] = updated
        self._app._reload_games(reason="local_executable_selected")
        if self._app._selected_game_id == updated.id:
            self._app.openGame(updated.id)
        self._app._emit_toast("Local game executable saved", "success")
        return True

    def _create_steam_provider(self) -> GameProviderLike:
        """Build Linux read-only integrations lazily for normal operation."""

        # Imported only outside Demo mode so GUI fixtures have no host coupling.
        from ..providers.linux_filesystem import LinuxFilesystemProvider
        from ..providers.local import ConfiguredGameProvider, LocalGameProvider
        from ..providers.steam import SteamGameProvider
        from ..services.directory_size import DirectorySizeScanner
        from ..config import LOCAL_EXECUTABLE_CHOICES_FILE

        if self._app._filesystem_provider is None:
            self._app._filesystem_provider = LinuxFilesystemProvider()
        if self._app._directory_size_scanner is None:
            self._app._directory_size_scanner = DirectorySizeScanner()
        steam = SteamGameProvider(
            self._app._filesystem_provider,
            additional_roots=self._app._settings_model.steam_installation_directories,
        )
        local = LocalGameProvider(
            self._app._filesystem_provider,
            self._app._settings_model.library_directories,
            choices_path=LOCAL_EXECUTABLE_CHOICES_FILE,
        )
        return ConfiguredGameProvider(steam, local)

    def _initial_games(self, initial_games: Sequence[Game] | None) -> list[Game]:
        if initial_games is not None:
            return [game for game in initial_games if isinstance(game, Game)]
        if self._app._library_cache is not None and not self._app._demo_mode:
            try:
                cached_games = list(self._app._library_cache.load())
            except Exception as error:
                logger.warning("Could not load the Steam library cache: %s", error)
            else:
                if cached_games:
                    logger.info(
                        "Loaded %d games from the library cache",
                        len(cached_games),
                    )
                    return cached_games
        try:
            return list(self._app._game_provider.list_games())
        except Exception as error:
            logger.warning("Could not read initial provider games: %s", error)
            return []

    def _on_library_scan_started(self, generation: int) -> None:
        self._app._active_scan_generation = generation
        if not self._app._demo_mode:
            now = datetime.now(UTC).isoformat()
            self._app._operational_tasks = {
                self._app._scan_task_id(generation): self._app._operational_task(
                    task_id=self._app._scan_task_id(generation),
                    title="Scan game libraries",
                    operation="Library scan",
                    status="running",
                    progress=0.0,
                    created_at=now,
                )
            }
            self._app._reload_tasks()
        self._app._set_scan_state(
            status="scanning",
            message=(
                "Refreshing demonstration library"
                if self._app._demo_mode
                else "Scanning configured game libraries…"
            ),
            is_scanning=True,
        )

    def _on_library_ready(self, generation: int, raw_games: object) -> None:
        if generation != self._app._active_scan_generation or self._app._shutdown_requested:
            return
        games = [
            game
            for game in (raw_games if isinstance(raw_games, Sequence) else ())
            if isinstance(game, Game)
        ]
        steam_found = False if self._app._demo_mode else self._app._provider_steam_found(games)

        if not self._app._demo_mode:
            self._app._update_operational_task(
                self._app._scan_task_id(generation),
                progress=0.35 if games and self._app._directory_size_scanner is not None else 1.0,
                status=(
                    "running"
                    if games and self._app._directory_size_scanner is not None
                    else "completed"
                ),
            )

        if self._app._directory_size_scanner is not None and not self._app._demo_mode:
            games = [
                replace(
                    game,
                    size_scan_status=SizeScanStatus.CALCULATING,
                    size_scan_error=None,
                )
                if game.status is not GameStatus.MISSING_FILES
                else game
                for game in games
            ]

        if not self._app._demo_mode:
            games = self._app._merge_unavailable_cached_games(games)

        self._app._set_domain_games(
            games,
            reason="library_scan_inventory",
            publish=False,
        )
        self._app._reload_system_info()
        self._app._set_scan_state(
            message=(
                f"Found {len(games)} games; calculating exact disk usage…"
                if games and self._app._directory_size_scanner is not None
                else f"Found {len(games)} games"
            ),
            steam_found=steam_found,
        )
        self._app._add_steam_system_info()
        self._app.systemInfoChanged.emit()
        logger.info("Library scan returned %d games", len(games))

    def _on_library_failed(self, generation: int, message: str) -> None:
        if generation != self._app._active_scan_generation or self._app._shutdown_requested:
            return
        logger.error("Library scan generation %d failed: %s", generation, message)
        readable = str(message).strip() or "unknown provider error"
        if not self._app._demo_mode:
            self._app._update_operational_task(
                self._app._scan_task_id(generation),
                progress=1.0,
                status="failed",
                error=readable,
            )
        self._app._set_scan_state(
            status="error",
            message=f"Steam library scan failed: {readable}",
        )
        self._app._emit_toast(
            "Steam library scan failed; cached games remain available",
            "error",
        )

    def _on_game_size_started(self, generation: int, game_id: str) -> None:
        if generation != self._app._active_scan_generation or self._app._shutdown_requested:
            return
        game = self._app._domain_games.get(game_id)
        if game is None or self._app._demo_mode:
            return
        task_id = self._app._size_task_id(generation, game_id)
        self._app._operational_tasks[task_id] = self._app._operational_task(
            task_id=task_id,
            title=f"Calculate size: {game.name}",
            operation="Size calculation",
            status="running",
            progress=0.0,
            game_id=game.id,
            game_name=game.name,
        )
        self._app._reload_tasks()

    def _on_game_size_ready(
        self,
        generation: int,
        game_id: str,
        result: object,
    ) -> None:
        if generation != self._app._active_scan_generation or self._app._shutdown_requested:
            return
        game = self._app._domain_games.get(game_id)
        if game is None:
            return
        try:
            logical_size_gb, physical_size_gb = self._app._size_result_gb(result)
            if isinstance(result, Mapping):
                complete = result.get("complete", True)
                errors = result.get("errors", ())
            else:
                complete = getattr(result, "complete", True)
                errors = getattr(result, "errors", ())
            error_values = [str(error) for error in errors if str(error)]
            error_text = "; ".join(error_values[:3])
            if len(error_values) > 3:
                error_text += f"; and {len(error_values) - 3} more errors"
            if not bool(complete) and not error_text:
                error_text = "directory changed or could not be read completely"
            if error_text:
                logger.warning(
                    "Exact size scan for %s was incomplete: %s",
                    game_id,
                    error_text,
                )
            updater = getattr(self._app._game_provider, "update_game_sizes", None)
            provider_game = None
            if callable(updater):
                provider_game = updater(
                    game_id,
                    logical_size_gb,
                    physical_size_gb,
                    error=error_text or None,
                )
            updated = (
                provider_game
                if isinstance(provider_game, Game)
                else replace(
                    game,
                    logical_size_gb=logical_size_gb,
                    physical_size_gb=physical_size_gb,
                    size_scan_status=(
                        SizeScanStatus.COMPLETED
                        if bool(complete) and not error_text
                        else SizeScanStatus.FAILED
                    ),
                    size_scan_error=error_text or None,
                )
            )
        except Exception as error:
            self._app._on_game_size_failed(generation, game_id, str(error))
            return
        self._app._domain_games[game_id] = updated
        if not self._app._demo_mode:
            self._app._update_operational_task(
                self._app._size_task_id(generation, game_id),
                progress=1.0,
                status=(
                    "completed"
                    if updated.size_scan_status is SizeScanStatus.COMPLETED
                    else "failed"
                ),
                error=updated.size_scan_error or "",
            )

    def _on_game_size_failed(
        self,
        generation: int,
        game_id: str,
        message: str,
    ) -> None:
        if generation != self._app._active_scan_generation or self._app._shutdown_requested:
            return
        game = self._app._domain_games.get(game_id)
        if game is None:
            return
        readable = str(message).strip() or "directory could not be read"
        logger.warning("Exact size scan failed for %s: %s", game_id, readable)
        self._app._domain_games[game_id] = replace(
            game,
            size_scan_status=SizeScanStatus.FAILED,
            size_scan_error=readable,
        )
        if not self._app._demo_mode:
            self._app._update_operational_task(
                self._app._size_task_id(generation, game_id),
                progress=1.0,
                status="failed",
                error=readable,
            )

    def _on_library_scan_finished(self, generation: int) -> None:
        if generation != self._app._active_scan_generation or self._app._shutdown_requested:
            return
        self._app._scan_worker_active = False
        if self._app._library_scan_status == "error":
            self._app._set_scan_state(is_scanning=False)
            self._app._schedule_coalesced_scan_retry()
            return

        if not self._app._demo_mode:
            self._app._update_operational_task(
                self._app._scan_task_id(generation),
                progress=1.0,
                status="completed",
            )

        failed_sizes = sum(
            game.size_scan_status is SizeScanStatus.FAILED
            for game in self._app._domain_games.values()
        )
        if self._app._demo_mode:
            status = "demo"
            message = f"Demo library ready · {len(self._app._domain_games)} games"
        elif not self._app._steam_found and not self._app._domain_games:
            status = "steam-not-found"
            message = "Steam was not found in standard or configured locations"
        elif not self._app._domain_games:
            status = "empty"
            message = "Steam was found, but no installed games were detected"
        else:
            status = "ready"
            message = f"Game library ready · {len(self._app._domain_games)} games"
            if not self._app._steam_found:
                message += " · Steam not found"
            if failed_sizes:
                message += f" · {failed_sizes} size scans unavailable"
        self._app._set_scan_state(
            status=status,
            message=message,
            is_scanning=False,
        )
        self._app._reload_games(reason="library_scan_finished")
        self._app._save_library_cache()
        self._app._reload_updates()
        self._app._schedule_update_observations()
        self._app._schedule_coalesced_scan_retry()

    def _schedule_coalesced_scan_retry(self) -> None:
        if not self._app._scan_retry_pending or self._app._shutdown_requested:
            return
        self._app._scan_retry_pending = False
        self._app._set_scan_state(
            status="scan-queued",
            message="A coalesced Steam library refresh is queued",
            is_scanning=True,
        )
        if not self._app._scan_debounce_timer.isActive():
            self._app._scan_debounce_timer.start()

    def _set_scan_state(
        self,
        *,
        status: str | None = None,
        message: str | None = None,
        steam_found: bool | None = None,
        is_scanning: bool | None = None,
    ) -> None:
        if status is not None and status != self._app._library_scan_status:
            self._app._library_scan_status = status
            self._app.libraryScanStatusChanged.emit()
        if message is not None and message != self._app._library_scan_message:
            self._app._library_scan_message = message
            self._app.libraryScanMessageChanged.emit()
        if steam_found is not None and steam_found != self._app._steam_found:
            self._app._steam_found = steam_found
            self._app.steamFoundChanged.emit()
        if is_scanning is not None and is_scanning != self._app._is_scanning:
            self._app._is_scanning = is_scanning
            self._app.isScanningChanged.emit()

    def _set_domain_games(
        self,
        games: Sequence[Game],
        *,
        reason: str = "domain_games_replaced",
        publish: bool = True,
    ) -> None:
        self._app._artwork_resolver.invalidate()
        self._app._domain_games = {
            game.id: self._app._resolve_game_artwork(game)
            for game in games
            if not self._app._game_is_in_ignored_library(game)
        }
        if publish:
            self._app._reload_games(reason=reason)
        else:
            logger.info(
                "Games snapshot prepared without model commit: reason=%s games=%d",
                reason,
                len(self._app._domain_games),
            )

    def _artwork_roots(self) -> tuple[Path, ...]:
        """Return local Steam roots without depending on a game install path."""

        values: list[Path] = []
        configured = getattr(self._app._game_provider, "configured_roots", ())
        if callable(configured):
            try:
                configured = configured()
            except Exception:
                configured = ()
        if isinstance(configured, Sequence) and not isinstance(
            configured, (str, bytes, bytearray)
        ):
            values.extend(Path(value) for value in configured)
        report = getattr(self._app._game_provider, "last_report", None)
        raw_report_roots = (
            report.get("steam_roots", ())
            if isinstance(report, Mapping)
            else getattr(report, "steam_roots", ())
            if report is not None
            else ()
        )
        if isinstance(raw_report_roots, Sequence) and not isinstance(
            raw_report_roots, (str, bytes, bytearray)
        ):
            values.extend(Path(value) for value in raw_report_roots)
        values.extend(
            Path(value)
            for value in self._app._settings_model.steam_installation_directories
        )
        unique: dict[str, Path] = {}
        for value in values:
            try:
                key = os.path.normcase(os.path.abspath(os.fspath(value)))
            except (OSError, TypeError, ValueError):
                continue
            unique.setdefault(key, value)
        return tuple(unique.values())

    def _resolve_game_artwork(self, game: Game) -> Game:
        return self._app._artwork_resolver.resolve(game, self._app._artwork_roots()).game

    def _present_game(self, game: Game, **kwargs: Any) -> dict[str, Any]:
        """Use the exact same artwork resolution for Games, Tasks and Updates."""

        resolved = self._app._resolve_game_artwork(game)
        if hasattr(self, "_domain_games") and self._app._domain_games.get(game.id) != resolved:
            self._app._domain_games[game.id] = resolved
        presented = game_to_qml(resolved, **kwargs)
        effective_url = str(presented.get("effectiveArtworkUrl") or "")
        if self._app._effective_artwork_urls.get(game.id) != effective_url:
            self._app._effective_artwork_urls[game.id] = effective_url
            logger.info(
                "Artwork presentation change: gameId=%s effectiveArtworkUrl=%s "
                "reason=%s",
                game.id,
                effective_url,
                "local_image" if effective_url else "placeholder_no_effective_url",
            )
        return presented

    def _merge_unavailable_cached_games(
        self,
        discovered_games: Sequence[Game],
    ) -> list[Game]:
        """Retain only cached games whose Steam library is known unavailable.

        A normal refresh remains authoritative for accessible libraries, so an
        uninstalled game is not resurrected from cache.  The exception is a
        library path explicitly reported as inaccessible by the provider.
        """

        merged = {game.id: game for game in discovered_games}
        inaccessible = self._app._provider_inaccessible_paths()
        configured = self._app._provider_configured_library_paths()
        retained = 0
        for game in self._app._domain_games.values():
            if game.launcher is not Launcher.STEAM:
                continue
            if game.id in merged:
                merged[game.id] = self._app._preserve_cached_artwork(
                    merged[game.id], game
                )
                continue
            belongs_to_inaccessible = any(
                self._app._path_is_within_library(game, root) for root in inaccessible
            )
            still_configured = any(
                self._app._path_is_within_library(game, root) for root in configured
            )
            if not belongs_to_inaccessible or not still_configured:
                continue
            merged[game.id] = replace(
                game,
                status=GameStatus.DRIVE_DISCONNECTED,
                compression_available=False,
                library_available=False,
                is_writable=False,
                size_scan_status=SizeScanStatus.NOT_REQUESTED,
                size_scan_error=None,
            )
            retained += 1
        if retained:
            logger.info(
                "Retained %d cached games from unavailable Steam libraries",
                retained,
            )
        self._app._log_library_decisions(discovered_games)
        return list(merged.values())

    def _provider_accessible_library_paths(self) -> tuple[Path, ...]:
        report = getattr(self._app._game_provider, "last_report", None)
        if report is None:
            return ()
        raw_paths = (
            report.get("libraries", ())
            if isinstance(report, Mapping)
            else getattr(report, "libraries", ())
        )
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return ()
        result: list[Path] = []
        for raw_path in raw_paths:
            try:
                result.append(Path(os.fspath(raw_path)))
            except TypeError:
                continue
        return tuple(result)

    def _log_library_decisions(self, discovered_games: Sequence[Game]) -> None:
        """Explain which Steam library evidence controlled the active model."""

        configured = self._app._provider_configured_library_paths()
        accessible = self._app._provider_accessible_library_paths()
        cached_games = tuple(
            game
            for game in self._app._domain_games.values()
            if game.launcher is Launcher.STEAM and game.library_path is not None
        )
        current_games = tuple(
            game
            for game in discovered_games
            if game.launcher is Launcher.STEAM and game.library_path is not None
        )
        libraries: list[Path] = []
        for path in (
            *configured,
            *accessible,
            *(game.library_path for game in cached_games),
            *(game.library_path for game in current_games),
        ):
            if path is None or any(self._app._same_path(path, known) for known in libraries):
                continue
            libraries.append(path)

        for library in libraries:
            is_configured = any(
                self._app._same_path(library, path) for path in configured
            )
            is_available = any(
                self._app._same_path(library, path) for path in accessible
            )
            matching_games = [
                game
                for game in (*current_games, *cached_games)
                if game.library_path is not None
                and self._app._same_path(game.library_path, library)
            ]
            filesystem = next(
                (
                    game.filesystem_name or game.filesystem.value
                    for game in matching_games
                    if game.filesystem_name or game.filesystem.value
                ),
                "unknown",
            )
            if is_configured and is_available:
                decision = "active"
            elif is_configured:
                decision = "disconnected"
            else:
                try:
                    exists = library.exists()
                except OSError:
                    exists = False
                decision = "orphaned" if exists else "removed"
            logger.info(
                "Steam library diagnostic: path=%s source=%s available=%s "
                "filesystem=%s decision=%s",
                library,
                "libraryfolders.vdf" if is_configured else "cache",
                str(is_available).lower(),
                filesystem,
                decision,
            )

    def _provider_inaccessible_paths(self) -> tuple[Path, ...]:
        report = getattr(self._app._game_provider, "last_report", None)
        if report is None:
            return ()
        raw_paths = (
            report.get("inaccessible_paths", ())
            if isinstance(report, Mapping)
            else getattr(report, "inaccessible_paths", ())
        )
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return ()
        paths: list[Path] = []
        for raw_path in raw_paths:
            try:
                paths.append(Path(os.fspath(raw_path)))
            except TypeError:
                continue
        return tuple(paths)

    def _provider_configured_library_paths(self) -> tuple[Path, ...]:
        report = getattr(self._app._game_provider, "last_report", None)
        if report is None:
            return ()
        missing = object()
        raw_paths = (
            report.get("configured_libraries", missing)
            if isinstance(report, Mapping)
            else getattr(report, "configured_libraries", missing)
        )
        # Compatibility for injected providers which predate this diagnostic:
        # their explicit inaccessible paths are the only available evidence.
        if raw_paths is missing:
            return self._app._provider_inaccessible_paths()
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return ()
        paths: list[Path] = []
        for raw_path in raw_paths:
            try:
                paths.append(Path(os.fspath(raw_path)))
            except TypeError:
                continue
        return tuple(paths)

    def _library_is_ignored(self, value: str | Path) -> bool:
        normalized = self._app._canonical_library_path(value)
        return bool(
            normalized is not None
            and any(
                self._app._same_path(normalized, ignored)
                for ignored in self._app._settings_model.ignored_steam_libraries
            )
        )

    def _game_is_in_ignored_library(self, game: Game) -> bool:
        candidate = game.library_path
        return bool(candidate is not None and self._app._library_is_ignored(candidate))

    def _update_record_is_in_ignored_library(
        self,
        record: GameUpdateRecord,
    ) -> bool:
        game = self._app._domain_games.get(record.game_id)
        if game is not None:
            return self._app._game_is_in_ignored_library(game)
        for observation in (
            record.pending_observation,
            record.current_observation,
            record.compression_observation,
        ):
            if observation is not None and observation.library_path:
                return self._app._library_is_ignored(observation.library_path)
        return False

    def _save_library_cache(self) -> None:
        if self._app._library_cache is None or self._app._demo_mode:
            return
        try:
            self._app._library_cache.save(tuple(self._app._domain_games.values()))
        except Exception as error:
            logger.warning("Could not save the Steam library cache: %s", error)

    def _provider_steam_found(self, games: Sequence[Game]) -> bool:
        if any(game.launcher is Launcher.STEAM for game in games):
            return True
        direct = getattr(self._app._game_provider, "steam_found", None)
        if isinstance(direct, bool):
            return direct
        report = getattr(self._app._game_provider, "last_report", None)
        if report is None:
            return False
        if isinstance(report, Mapping):
            getter = report.get
        else:
            getter = lambda name, default=None: getattr(report, name, default)
        for name in ("steam_found", "installation_found", "found"):
            value = getter(name, None)
            if isinstance(value, bool):
                return value
        for name in (
            "installations",
            "steam_roots",
            "installation_roots",
            "detected_roots",
        ):
            value = getter(name, None)
            if value:
                return True
        for name in ("installation_count", "installations_found"):
            value = getter(name, None)
            if isinstance(value, int):
                return value > 0
        return False

    def _reload_games(
        self,
        *,
        emit_signal: bool = True,
        reason: str = "state_update",
    ) -> None:
        all_games = list(self._app._domain_games.values())
        compression_results = self._app._latest_compression_results()
        verification_results = self._app._latest_verification_results()
        show_tools = self._app._demo_mode or bool(
            getattr(self._app._settings_model, "show_steam_tools_and_runtimes", False)
        )
        games = [
            game for game in all_games if show_tools or not game.is_steam_tool
        ]
        next_games = [
            self._app._present_game(
                game,
                analysis_report=self._app._analysis_reports.get(game.id),
                compression_result=compression_results.get(game.id),
                verification_result=verification_results.get(game.id),
                benchmark_estimate=self._app._benchmark_estimates.estimate_for(game),
            )
            for game in games
        ]
        # Build the entire presentation snapshot off to the side.  The legacy
        # QVariantList remains available to non-delegate consumers, but is not
        # replaced unless the incremental model found a visible change.
        previous_games = self._app._games
        summaries = aggregate_library_compression(next_games)
        configured_libraries = self._app._provider_configured_library_paths()
        for summary in summaries:
            library_path = Path(str(summary.get("libraryPath", "")))
            library_games = [
                game
                for game in games
                if game.library_path is not None
                and self._app._same_path(game.library_path, library_path)
            ]
            configured = any(
                self._app._same_path(library_path, configured_path)
                for configured_path in configured_libraries
            )
            try:
                path_exists = library_path.exists()
            except OSError:
                path_exists = False
            summary.update(
                {
                    "libraryAvailable": any(
                        game.library_available for game in library_games
                    ),
                    "libraryConfigured": configured,
                    "canForgetLibrary": bool(
                        self._app._library_scan_status == "ready"
                        and not configured
                        and not path_exists
                        and library_games
                        and not any(
                            self._app._active_task_for_game(game.id) is not None
                            for game in library_games
                        )
                    ),
                    "canIgnoreLibrary": bool(
                        not self._app._demo_mode
                        and library_games
                        and not any(game.library_available for game in library_games)
                        and not any(
                            self._app._active_task_for_game(game.id) is not None
                            for game in library_games
                        )
                    ),
                }
            )
        self._app._compression_library_summaries = summaries
        if self._app._selected_game_id:
            selected = next(
                (game for game in all_games if game.id == self._app._selected_game_id), None
            )
            if selected is None:
                self._app._selected_game_id = ""
                self._app._selected_game = {}
            else:
                self._app._selected_game = self._app._present_game(
                    selected,
                    analysis_report=self._app._analysis_reports.get(selected.id),
                    compression_result=compression_results.get(selected.id),
                    verification_result=verification_results.get(selected.id),
                    benchmark_estimate=self._app._benchmark_estimates.estimate_for(selected),
                )
            if emit_signal:
                self._app.selectedGameChanged.emit()
        mutation = self._app._games_model.apply_snapshot(next_games, reason=str(reason))
        if mutation["changed"] is not True:
            self._app._games = previous_games
            logger.info(
                "Games model unchanged, refresh skipped: generation=%d "
                "reason=%s games=%d modelReset=%d",
                self._app._games_model_generation,
                str(reason),
                len(next_games),
                self._app._games_model.modelResetCount,
            )
            return
        self._app._games = next_games
        self._app._games_model_generation += 1
        logger.info(
            "Games model incremental commit: generation=%d reason=%s games=%d "
            "inserted=%d removed=%d updated=%d moved=%d modelReset=%d",
            self._app._games_model_generation,
            str(reason),
            len(self._app._games),
            mutation["inserted"],
            mutation["removed"],
            mutation["updated"],
            mutation["moved"],
            mutation["resets"],
        )
        if emit_signal:
            self._app.gamesModelRefreshed.emit(
                self._app._games_model_generation,
                str(reason),
                len(self._app._games),
            )
            self._app.gamesChanged.emit()

    def _find_game(self, game_id: str) -> Game | None:
        normalized_id = str(game_id).strip()
        if not normalized_id:
            return None
        cached = self._app._domain_games.get(normalized_id)
        if cached is not None:
            return cached
        try:
            resolved = self._app._game_provider.get_game(normalized_id)
            if resolved is not None and self._app._game_is_in_ignored_library(resolved):
                return None
            return resolved
        except (KeyError, LookupError):
            return None

    def _resolve_game(self, game_id: str, *, show_error: bool = True) -> Game | None:
        resolved_id = str(game_id).strip() or self._app._selected_game_id
        game = self._app._find_game(resolved_id)
        if game is None and show_error:
            logger.warning("Action requested for unknown game id %r", resolved_id)
            self._app._emit_toast("Select an available game first", "warning")
        return game

    def _new_manual_game(self) -> Game:
        known_ids = {game["id"] for game in self._app._games}
        while f"manual-demo-{self._app._manual_game_number}" in known_ids:
            self._app._manual_game_number += 1
        number = self._app._manual_game_number
        self._app._manual_game_number += 1
        return Game(
            id=f"manual-demo-{number}",
            name=f"Manual Demo Game {number}",
            launcher=Launcher.MANUAL,
            install_path=Path("/demo/manual") / f"Manual Demo Game {number}",
            logical_size_gb=12.0 + number,
            physical_size_gb=11.2 + number,
            filesystem=FilesystemType.BTRFS,
            compression_available=True,
            saved_space_gb=0.0,
            status=GameStatus.READY,
            active_optimization_profile=OptimizationProfile.BALANCED,
            backup_status=BackupStatus.AVAILABLE,
            texture_compatibility=TextureCompatibility.PARTIAL_SUPPORT,
        )
