from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from pathlib import Path
from queue import Empty
from threading import Event
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ..models import Game, OptiScalerProfile
from ..services import OptiScalerCancelled, OptiScalerError
from ..services.optiscaler_online import (
    CachedOptiScalerArchive,
    OptiScalerOnlineError,
    OptiScalerRelease,
)

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class OptiScalerController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    def getOptiScalerStatus(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            result = self._app._optiscaler_service.status(game)
            release = self._app._cached_optiscaler_release()
            cached_archive = (
                self._app._cached_optiscaler_archive(release)
                if release is not None else None
            )
            app_id = self._app._optiscaler_service.game_key(game)
            installed_version = str(result.get("installedVersion", "")).strip()
            available_version = release.version if release is not None else ""
            installation_state = str(
                result.get("installationState", "not_installed")
            )
            installed = bool(result.get("installed"))
            if installation_state == "corrupt":
                online_state = "error"
            elif installed and available_version and (
                self._app._normalized_release_version(installed_version)
                != self._app._normalized_release_version(available_version)
            ):
                online_state = "update_available"
            elif installed:
                online_state = "installed"
            elif app_id in self._app._optiscaler_online_errors:
                online_state = "error"
            else:
                online_state = "not_installed"
            result.update(
                {
                    "onlineState": online_state,
                    "availableVersion": available_version,
                    "releaseUrl": release.html_url if release is not None else "",
                    "releaseSource": release.source if release is not None else "",
                    "releaseStale": bool(release.stale) if release is not None else False,
                    "archiveReady": cached_archive is not None,
                    "cachedArchivePath": (
                        str(cached_archive.path) if cached_archive is not None else ""
                    ),
                    "cachedArchiveSha256": (
                        cached_archive.sha256 if cached_archive is not None else ""
                    ),
                    "onlineError": self._app._optiscaler_online_errors.get(app_id, ""),
                }
            )
            return result
        except Exception as error:
            logger.warning("Could not inspect OptiScaler for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def _cached_optiscaler_release(self) -> OptiScalerRelease | None:
        loader = getattr(self._app._optiscaler_release_client, "cached_release", None)
        if not callable(loader):
            return None
        try:
            return loader()
        except OptiScalerOnlineError as error:
            logger.warning("Could not read cached OptiScaler metadata: %s", error)
            return None

    def _cached_optiscaler_archive(
        self, release: OptiScalerRelease
    ) -> CachedOptiScalerArchive | None:
        loader = getattr(self._app._optiscaler_release_client, "cached_archive", None)
        if not callable(loader):
            return None
        try:
            return loader(release)
        except OptiScalerOnlineError as error:
            logger.warning("Could not read cached OptiScaler archive: %s", error)
            return None

    def rememberOptiScalerExecutable(
        self, game_id: str, executable_value: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        value = str(executable_value or "").strip()
        url = QUrl(value)
        if url.scheme():
            if not url.isLocalFile():
                return {"success": False, "error": "only local executables are supported"}
            value = url.toLocalFile()
        try:
            profile = self._app._optiscaler_service.remember_executable(game, value)
            result = self._app._optiscaler_service.status(game)
            self._app.optiScalerChanged.emit(profile.app_id)
            return result
        except Exception as error:
            logger.warning("Could not save OptiScaler executable for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def refreshOptiScalerRelease(self, game_id: str, force_refresh: bool) -> bool:
        """Fetch and validate the official release without blocking the GUI."""

        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        try:
            app_id = self._app._optiscaler_service.game_key(game)
        except OptiScalerError:
            return False

        def operation(
            cancelled: Event, progress: Callable[[str, float], None]
        ) -> OptiScalerProfile:
            try:
                progress("Checking official release", 0.08)
                release = self._app._optiscaler_release_client.latest_release(
                    force_refresh=bool(force_refresh),
                    allow_stale_cache=True,
                )
                if cancelled.is_set():
                    raise OptiScalerCancelled("OptiScaler operation was cancelled")
                progress("Downloading and validating release", 0.35)
                self._app._optiscaler_release_client.ensure_archive(release)
                if cancelled.is_set():
                    raise OptiScalerCancelled("OptiScaler operation was cancelled")
                self._app._optiscaler_online_errors.pop(app_id, None)
                progress("Release ready", 1.0)
                return self._app._optiscaler_service.profile_repository.load(app_id)
            except OptiScalerOnlineError as error:
                self._app._optiscaler_online_errors[app_id] = str(error)
                raise

        return self._app._start_optiscaler_operation(
            game,
            "Check release",
            operation,
        )

    def inspectOnlineOptiScaler(
        self,
        game_id: str,
        executable: str,
        injection_dll: str,
        allow_anticheat_risk: bool,
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        release = self._app._cached_optiscaler_release()
        archive = (
            self._app._cached_optiscaler_archive(release)
            if release is not None else None
        )
        if release is None or archive is None:
            return {
                "success": False,
                "error": "Check the official OptiScaler release before creating an installation plan",
            }
        try:
            result = self._app._optiscaler_service.plan(
                game,
                archive.path,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                allow_anticheat_risk=bool(allow_anticheat_risk),
            ).to_dict()
            result.update(
                {
                    "officialRelease": True,
                    "releaseUrl": release.html_url,
                    "archiveSha256": archive.sha256,
                    "archiveFromCache": archive.from_cache,
                }
            )
            return result
        except Exception as error:
            logger.warning("Online OptiScaler plan rejected for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def installOnlineOptiScaler(
        self,
        game_id: str,
        executable: str,
        injection_dll: str,
        operation_name: str,
        allow_replace_conflicts: bool,
        allow_anticheat_risk: bool,
    ) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        release = self._app._cached_optiscaler_release()
        archive = (
            self._app._cached_optiscaler_archive(release)
            if release is not None else None
        )
        if release is None or archive is None:
            self._app._emit_toast(
                "Check the official OptiScaler release before installation",
                "warning",
            )
            return False
        operation = str(operation_name or "auto").strip().casefold()

        def install_operation(
            cancelled: Event, progress: Callable[[str, float], None]
        ) -> OptiScalerProfile:
            installed_profile = self._app._optiscaler_service.install(
                game,
                archive.path,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                operation=operation,
                allow_replace_conflicts=bool(allow_replace_conflicts),
                allow_anticheat_risk=bool(allow_anticheat_risk),
                cancel_event=cancelled,
                progress=progress,
                expected_archive_sha256=archive.sha256,
            )
            tweaks = self._app._proton_tweaks_repository.load(
                installed_profile.app_id
            )
            return self._app._optiscaler_service.configure_fsr4_update(
                game, tweaks.optiscaler_fsr4_update
            )

        return self._app._start_optiscaler_operation(
            game,
            operation.capitalize(),
            install_operation,
        )

    def inspectOptiScalerArchive(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            archive = self._app._local_file_argument(archive_value)
            return self._app._optiscaler_service.plan(
                game,
                archive,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
            ).to_dict()
        except Exception as error:
            logger.warning("OptiScaler plan rejected for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def _start_optiscaler_operation(
        self,
        game: Game,
        action: str,
        operation: Callable[
            [Event, Callable[[str, float], None]], OptiScalerProfile
        ],
    ) -> bool:
        if any(
            stored_game_id == game.id and not future.done()
            for future, _cancel, stored_game_id in self._app._optiscaler_jobs.values()
        ):
            self._app._emit_toast("An OptiScaler task for this game is already active", "warning")
            return False
        task_id = f"optiscaler-{action.casefold()}-{uuid4().hex}"
        cancel_event = Event()
        timestamp = datetime.now(UTC).isoformat()
        self._app._operational_tasks[task_id] = self._app._operational_task(
            task_id=task_id,
            title=f"OptiScaler: {action} - {game.name}",
            operation="OptiScaler",
            status="queued",
            progress=0.0,
            game_id=game.id,
            game_name=game.name,
            created_at=timestamp,
        )
        self._app._operational_tasks[task_id]["cancellable"] = True
        self._app._operational_tasks[task_id]["stage"] = "Queued"

        def report(stage: str, value: float) -> None:
            self._app._optiscaler_events.put((task_id, str(stage), float(value)))

        future = self._app._optiscaler_executor.submit(operation, cancel_event, report)
        self._app._optiscaler_jobs[task_id] = (future, cancel_event, game.id)
        self._app._reload_tasks()
        self._app._emit_toast(f"OptiScaler {action.casefold()} started", "info")
        return True

    def installOptiScaler(
        self,
        game_id: str,
        archive_value: str,
        executable: str,
        injection_dll: str,
        allow_replace_conflicts: bool,
    ) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return False
        try:
            archive = self._app._local_file_argument(archive_value)
        except ValueError as error:
            self._app._emit_toast(str(error), "error")
            return False
        return self._app._start_optiscaler_operation(
            game,
            "Install",
            lambda cancelled, progress: self._app._optiscaler_service.install(
                game,
                archive,
                executable=str(executable or ""),
                injection_dll=str(injection_dll or "auto"),
                allow_replace_conflicts=bool(allow_replace_conflicts),
                cancel_event=cancelled,
                progress=progress,
            ),
        )

    def removeOptiScaler(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        return bool(
            game is not None
            and self._app._start_optiscaler_operation(
                game,
                "Remove",
                lambda cancelled, progress: self._app._optiscaler_service.remove(
                    game, cancel_event=cancelled, progress=progress
                ),
            )
        )

    def restoreOptiScalerFiles(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        return bool(
            game is not None
            and self._app._start_optiscaler_operation(
                game,
                "Restore",
                lambda cancelled, progress: self._app._optiscaler_service.restore(
                    game, cancel_event=cancelled, progress=progress
                ),
            )
        )

    def verifyOptiScaler(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available Steam game first"}
        try:
            profile = self._app._optiscaler_service.verify(game)
            result = self._app._optiscaler_service.status(game)
            self._app.optiScalerChanged.emit(profile.app_id)
            return result
        except Exception as error:
            return {"success": False, "error": str(error)}

    def openOptiScalerDirectory(self, game_id: str) -> bool:
        status = self._app.getOptiScalerStatus(game_id)
        directory = Path(str(status.get("installDirectory", "")))
        return bool(
            status.get("success")
            and directory.is_dir()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        )

    def openOptiScalerManifest(self, game_id: str) -> bool:
        status = self._app.getOptiScalerStatus(game_id)
        manifest = Path(str(status.get("manifestPath", "")))
        return bool(
            status.get("success")
            and manifest.is_file()
            and QDesktopServices.openUrl(QUrl.fromLocalFile(str(manifest)))
        )

    def _poll_optiscaler_jobs(self) -> None:
        while True:
            try:
                task_id, stage, progress = self._app._optiscaler_events.get_nowait()
            except Empty:
                break
            task = self._app._operational_tasks.get(task_id)
            if task is None:
                continue
            task["status"] = "running"
            task["stage"] = stage
            task["progress"] = min(1.0, max(0.0, progress))
            task["progressPercent"] = task["progress"] * 100.0
            task["updatedAt"] = datetime.now(UTC).isoformat()

        for task_id, (future, cancelled, game_id) in tuple(
            self._app._optiscaler_jobs.items()
        ):
            if not future.done():
                continue
            self._app._optiscaler_jobs.pop(task_id, None)
            task = self._app._operational_tasks.get(task_id)
            if task is None:
                continue
            status = "completed"
            error_text = ""
            try:
                profile = future.result()
                game = self._app._domain_games.get(game_id)
                task["result"] = (
                    self._app._optiscaler_service.status(game)
                    if game is not None
                    else profile.to_dict()
                )
                self._app.optiScalerChanged.emit(profile.app_id)
            except OptiScalerCancelled as error:
                status = "cancelled"
                error_text = str(error)
            except Exception as error:
                status = "cancelled" if cancelled.is_set() else "failed"
                error_text = str(error)
                logger.warning("OptiScaler task %s failed: %s", task_id, error)
            task["status"] = status
            task["stage"] = (
                "Completed" if status == "completed"
                else "Cancelled" if status == "cancelled"
                else "Failed"
            )
            task["progress"] = 1.0
            task["progressPercent"] = 100.0
            task["error"] = error_text
            task["cancellable"] = False
            task["updatedAt"] = datetime.now(UTC).isoformat()
            self._app.taskFinished.emit(task_id, status)
            self._app._emit_toast(
                "OptiScaler operation completed"
                if status == "completed"
                else "OptiScaler operation cancelled"
                if status == "cancelled"
                else f"OptiScaler operation failed: {error_text}",
                "success" if status == "completed" else "warning"
                if status == "cancelled" else "error",
            )
