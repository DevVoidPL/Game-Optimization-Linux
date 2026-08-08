from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import logging
import os
from typing import TYPE_CHECKING, Any, cast

from ..models import (
    CompressionProfile,
    FilesystemType,
    Game,
    Task,
    TaskStatus,
    TaskType,
)
from ..services import BtrfsAnalysisReport, normalized_benchmark_projection
from .presenters import qml_value, task_to_qml

if TYPE_CHECKING:
    from .app_controller import AppController

_MEASUREMENT_AUTH_TOAST = "Waiting for authorization to measure compression"

_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.INTERRUPTED.value,
}

logger = logging.getLogger(__name__)


class CompressionController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    def analyzeGame(self, game_id: str = "") -> bool:
        game = self._app._resolve_game(game_id)
        if game is None:
            return False
        if not self._app._game_actions_allowed(game):
            self._app._emit_toast(f"Library unavailable for {game.name}", "warning")
            return False
        try:
            task = self._app._task_service.enqueue_analysis(game)
            self._app._reload_tasks()
        except Exception as error:
            self._app._report_error(f"queuing analysis for {game.name}", error)
            return False

        logger.info("Queued read-only analysis task %s for %s", task.id, game.name)
        self._app._emit_toast(f"Analysis queued for {game.name}", "success")
        self._app._set_current_page("tasks")
        return True

    def requestCompression(self, game_id: str, mode: str = "Balanced") -> bool:
        game = self._app._resolve_game(game_id)
        if game is None:
            return False
        if self._app._demo_mode and not game.compression_available:
            logger.warning("Rejected demo compression for incompatible game %s", game.id)
            self._app._emit_toast(
                f"Compression is unavailable for {game.name} on {game.filesystem.value}",
                "warning",
            )
            return False

        if not self._app._demo_mode:
            plan = self._app.prepareCompression(game.id, mode, False)
            if not bool(plan.get("valid", False)):
                return False
            self._app._emit_toast(
                "Review and confirm the compression plan before starting",
                "info",
            )
            return True

        try:
            profile = self._app._coerce_enum(CompressionProfile, mode)
            task = self._app._task_service.enqueue_compression(game, profile)
            self._app._reload_tasks()
        except Exception as error:
            self._app._report_error(f"queuing demo compression for {game.name}", error)
            return False

        logger.info(
            "Queued simulated compression task %s for %s (%s)",
            task.id,
            game.name,
            profile.value,
        )
        self._app._emit_toast(
            f"{profile.value} compression simulation queued for {game.name}",
            "success",
        )
        self._app._set_current_page("tasks")
        return True

    def analyzeChanges(self, game_id: str) -> bool:
        return self._app.analyzeGame(game_id)

    def verifyCompression(self, game_id: str) -> bool:
        """Queue one authenticated read-only measurement, never recompression."""

        game = self._app._resolve_game(game_id)
        if game is None or self._app._demo_mode:
            return False
        if not self._app._game_actions_allowed(game):
            self._app._emit_toast("The Steam library is unavailable", "warning")
            return False
        if game.filesystem is not FilesystemType.BTRFS and (
            game.filesystem_name.casefold() != "btrfs"
        ):
            self._app._emit_toast(
                "Compression verification requires Btrfs",
                "warning",
            )
            return False
        method = getattr(self._app._task_service, "enqueue_verification", None)
        if not callable(method):
            self._app._emit_toast(
                "Privileged compression measurement is unavailable",
                "error",
            )
            return False
        try:
            task = method(game)
        except Exception as error:
            self._app._report_error(f"verifying compression for {game.name}", error)
            return False
        self._app._reload_tasks()
        self._app._emit_toast(
            _MEASUREMENT_AUTH_TOAST,
            "info",
        )
        logger.info(
            "Queued read-only compression verification %s for %s",
            task.id,
            game.id,
        )
        return True

    def prepareCompression(
        self,
        game_id: str,
        mode: str = "Auto",
        changed_only: bool = False,
    ) -> dict[str, Any]:
        """Create a read-only plan; no Btrfs property or file is changed."""

        game = self._app._resolve_game(game_id)
        service = self._app._compression_service
        if game is None or service is None or self._app._demo_mode:
            return self._app._invalid_plan(
                "Real Btrfs compression is unavailable in this mode"
            )
        if not self._app._game_actions_allowed(game):
            return self._app._invalid_plan("The Steam library is unavailable")
        raw_report = self._app._analysis_reports.get(game.id)
        if not isinstance(raw_report, Mapping):
            self._app._emit_toast("Analyze the game before creating a plan", "warning")
            return self._app._invalid_plan("A completed analysis is required")
        try:
            report = BtrfsAnalysisReport.from_dict(raw_report)
            profile = self._app._coerce_enum(CompressionProfile, mode)
            plan = service.prepare(
                game,
                report,
                profile,
                changed_only=bool(changed_only),
                after_update=bool(changed_only),
                confirmation_required=True,
                minimum_free_bytes=int(
                    float(
                        self._app._settings_model.automatic_compression_min_free_gb
                    )
                    * (1024**3)
                ),
            )
        except Exception as error:
            self._app._report_error(f"preparing compression for {game.name}", error)
            return self._app._invalid_plan(str(error) or type(error).__name__)
        self._app._compression_plans[plan.id] = plan
        current_measurement = self._app._current_authoritative_compsize(game.id)
        profitability = normalized_benchmark_projection(
            self._app._benchmark_estimates.estimate_for(game),
            level=int(plan.one_time_recompression_level),
            current_uncompressed_bytes=current_measurement.get(
                "compsize_uncompressed_bytes"
            ),
            current_disk_usage_bytes=current_measurement.get(
                "compsize_disk_bytes"
            ),
            app_id=str(game.steam_app_id or ""),
            build_id=str(game.steam_build_id or ""),
        )
        presented = self._app._plan_to_qml(plan, profitability=profitability)
        if not plan.eligible:
            self._app._emit_toast(
                plan.blockers[0] if plan.blockers else "Compression is blocked",
                "warning",
            )
        return presented

    def startCompression(self, plan_id: str) -> bool:
        """Queue a plan after the QML confirmation dialog was accepted."""

        return self._app._start_compression_plan(
            str(plan_id),
            confirmed=True,
            automatic_authorized=False,
        )

    def _start_compression_plan(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        automatic_authorized: bool,
    ) -> bool:
        service = self._app._compression_service
        normalized_id = str(plan_id).strip()
        plan = self._app._compression_plans.get(normalized_id)
        if plan is None and service is not None:
            plan = service.get_plan(normalized_id)
        if service is None or plan is None:
            self._app._emit_toast("The compression plan is no longer available", "error")
            return False
        game = self._app._find_game(plan.game_id)
        if game is None or not self._app._game_actions_allowed(game):
            self._app._emit_toast("The game library is unavailable", "error")
            return False
        if not bool(plan.eligible):
            self._app._emit_toast(
                plan.blockers[0] if plan.blockers else "Compression is blocked",
                "error",
            )
            return False
        try:
            task = self._app._task_service.enqueue_compression_plan(
                game,
                plan,
                confirmed=bool(confirmed),
                automatic_authorized=bool(automatic_authorized),
            )
        except Exception as error:
            self._app._report_error(f"queuing compression for {game.name}", error)
            return False
        self._app._compression_plans.pop(normalized_id, None)
        self._app._pending_automatic_games.discard(game.id)
        self._app._reload_tasks()
        self._app._reload_updates()
        self._app._reload_system_info()
        if automatic_authorized:
            if self._app._settings_model.automatic_compression_notify:
                self._app._emit_toast(
                    f"Automatic compression queued for {game.name}",
                    "success",
                )
        else:
            self._app._emit_toast(f"Compression queued for {game.name}", "success")
            self._app._set_current_page("tasks")
        logger.info(
            "Queued compression task %s for %s from plan %s",
            task.id,
            game.id,
            plan.id,
        )
        return True

    def pauseTask(self, task_id: str) -> bool:
        return self._app._change_task_state("pause", task_id)

    def resumeTask(self, task_id: str) -> bool:
        return self._app._change_task_state("resume", task_id)

    def cancelTask(self, task_id: str) -> bool:
        return self._app._change_task_state("cancel", task_id)

    def clearFinishedTasks(self) -> int:
        method = getattr(self._app._task_service, "clear_finished", None)
        removed = 0
        if callable(method):
            try:
                removed = int(method())
            except Exception as error:
                self._app._report_error("clearing finished tasks", error)
                return 0
        operational_ids = [
            task_id
            for task_id, task in self._app._operational_tasks.items()
            if str(task.get("status", "")).lower() in _TERMINAL_STATUSES
        ]
        for task_id in operational_ids:
            self._app._operational_tasks.pop(task_id, None)
        removed += len(operational_ids)
        self._app._reported_terminal_tasks.clear()
        self._app._reload_tasks()
        return removed

    def removeFinishedTask(self, task_id: str) -> bool:
        operational = self._app._operational_tasks.get(str(task_id))
        if (
            operational is not None
            and str(operational.get("status", "")).lower() in _TERMINAL_STATUSES
        ):
            self._app._operational_tasks.pop(str(task_id), None)
            self._app._reported_terminal_tasks.discard(str(task_id))
            self._app._reload_tasks()
            return True
        method = getattr(self._app._task_service, "remove_finished", None)
        if not callable(method):
            return False
        try:
            removed = bool(method(str(task_id)))
        except Exception as error:
            self._app._report_error("removing a finished task", error)
            return False
        if removed:
            self._app._reported_terminal_tasks.discard(str(task_id))
            self._app._reload_tasks()
        return removed

    def cancelActiveCompressionTasks(self) -> bool:
        """Request cancellation for every active compression task.

        The task service owns a cooperative event for each worker.  The
        provider polls that event and terminates its matching child process;
        a global provider cancellation is reserved for final shutdown so one
        task cannot poison future work.
        """

        requested = False
        try:
            for task in self._app._task_service.list_tasks():
                if (
                    task.task_type is TaskType.COMPRESSION
                    and task.status.value not in _TERMINAL_STATUSES
                ):
                    try:
                        self._app._task_service.cancel(task.id)
                    except Exception as error:
                        logger.warning(
                            "Could not cancel compression task %s: %s",
                            task.id,
                            error,
                        )
                    else:
                        requested = True
        except Exception as error:
            logger.warning("Could not enumerate compression tasks: %s", error)
        self._app._reload_tasks()
        return requested

    def _compression_fingerprint(
        self,
        game: Game,
    ) -> Mapping[str, Mapping[str, Any]] | None:
        tracker = self._app._update_tracker
        if tracker is None:
            return None
        record = tracker.get(game.id)
        snapshot = record.compression_snapshot if record is not None else None
        if snapshot is None or not snapshot.complete:
            return None
        expected_path = os.path.abspath(os.fspath(game.install_path))
        if os.path.abspath(snapshot.root_path) != expected_path:
            return None
        return {
            item.relative_path: {
                "size": int(item.size),
                "mtime_ns": int(item.mtime_ns),
                "ctime_ns": int(item.ctime_ns),
            }
            for item in snapshot.files
        }

    def _mark_compression_verified(self, game: Game) -> None:
        tracker = self._app._update_tracker
        if tracker is None:
            return
        tracker.record_verified_compression(game)
        # The callback executes in a worker.  QML state is refreshed only by
        # the controller's GUI-thread timer.
        self._app._updates_dirty = True

    def _reload_selected_history(self, *, emit_signal: bool = True) -> None:
        service = self._app._compression_service
        if service is None or not self._app._selected_game_id:
            history: tuple[dict[str, Any], ...] = ()
        else:
            try:
                history = service.history(self._app._selected_game_id)
            except Exception as error:
                logger.warning("Could not read compression history: %s", error)
                history = ()
        self._app._selected_game_history = [
            {
                **cast(dict[str, Any], qml_value(dict(entry))),
                "historyId": str(entry.get("id", "")),
            }
            for entry in history
            if isinstance(entry, Mapping)
        ]
        if emit_signal:
            self._app.compressionHistoryChanged.emit()

    def _latest_compression_results(self) -> dict[str, Mapping[str, Any]]:
        service = self._app._compression_service
        if service is None:
            return {}
        try:
            history = service.history()
        except Exception as error:
            logger.warning("Could not read compression history for games: %s", error)
            return {}
        latest: dict[str, Mapping[str, Any]] = {}
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            game_id = str(entry.get("game_id", "")).strip()
            if game_id and game_id not in latest:
                latest[game_id] = entry
        return latest

    def _latest_verification_results(self) -> dict[str, Mapping[str, Any]]:
        """Return only the newest terminal verification for each game."""

        try:
            tasks = self._app._task_service.list_tasks()
        except Exception as error:
            logger.warning("Could not read verification task history: %s", error)
            return {}
        latest_tasks: dict[str, Task] = {}
        for task in tasks:
            if (
                task.task_type is not TaskType.VERIFICATION
                or task.status.value not in _TERMINAL_STATUSES
                or not task.game_id
            ):
                continue
            previous = latest_tasks.get(task.game_id)
            if previous is None or task.updated_at > previous.updated_at:
                latest_tasks[task.game_id] = task
        return {
            game_id: {
                "task_id": task.id,
                "game_id": task.game_id,
                "status": task.status.value,
                "error": str(task.error or ""),
                "result": qml_value(task.result or {}),
                "updated_at": task.updated_at.isoformat(),
            }
            for game_id, task in latest_tasks.items()
        }

    def _current_authoritative_compsize(self, game_id: str) -> dict[str, Any]:
        """Return the same newest complete compsize result used by the game header."""

        verification = self._app._latest_verification_results().get(game_id)
        if verification:
            result = verification.get("result")
            measurement = dict(result) if isinstance(result, Mapping) else {}
            if (
                str(verification.get("status") or "").casefold() == "completed"
                and self._app._complete_privileged_compsize(measurement)
            ):
                return measurement
            return {}
        compression = self._app._latest_compression_results().get(game_id)
        if not compression or compression.get("measurement_authoritative") is not True:
            return {}
        after = compression.get("after")
        measurement = dict(after) if isinstance(after, Mapping) else {}
        return (
            measurement
            if self._app._complete_privileged_compsize(measurement)
            else {}
        )

    def _reload_tasks(self, *, emit_signal: bool = True) -> None:
        rows = [
            task_to_qml(task) for task in self._app._task_service.list_tasks()
        ] + [dict(task) for task in self._app._operational_tasks.values()]
        for row in rows:
            game = self._app._domain_games.get(str(row.get("gameId", "")))
            if game is not None:
                presented_game = self._app._present_game(game)
                row.update(
                    {
                        "steamAppId": presented_game.get("steamAppId", ""),
                        "launcher": presented_game.get("launcher", ""),
                        "effectiveArtworkUrl": presented_game.get(
                            "effectiveArtworkUrl", ""
                        ),
                        "portraitArtwork": presented_game.get(
                            "portraitArtwork", ""
                        ),
                        "headerArtwork": presented_game.get(
                            "headerArtwork", ""
                        ),
                        "fallbackArtwork": presented_game.get(
                            "fallbackArtwork", ""
                        ),
                    }
                )
            elif str(row.get("gameId", "")).startswith("steam-"):
                row["steamAppId"] = str(row["gameId"]).removeprefix("steam-")
                row["launcher"] = "Steam"
        self._app._tasks = self._app._bounded_task_rows(
            rows
        )
        if emit_signal:
            self._app.tasksChanged.emit()

    def _poll_tasks(self) -> None:
        if self._app._shutdown_requested:
            return
        try:
            self._app._poll_update_jobs()
            self._app._poll_optiscaler_jobs()
            now = datetime.now(UTC)
            if (
                self._app._update_tracker is not None
                and not self._app._update_jobs
                and (now - self._app._last_periodic_rescan).total_seconds() >= 60.0
            ):
                self._app._schedule_update_observations()
            if self._app._demo_mode:
                self._app._task_service.tick(step=1.5)
            domain_tasks = list(self._app._task_service.list_tasks())
            service_tasks = [task_to_qml(task) for task in domain_tasks]
            updated = self._app._bounded_task_rows(
                service_tasks + list(self._app._operational_tasks.values())
            )
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received while polling tasks")
            self._app.shutdown()
            application = QCoreApplication.instance()
            if application is not None:
                application.quit()
            return
        except Exception as error:
            logger.exception("Task status timer failed: %s", error)
            if not self._app._timer_error_reported:
                self._app._emit_toast("The task list could not be updated", "error")
                self._app._timer_error_reported = True
            return

        self._app._timer_error_reported = False
        if updated != self._app._tasks:
            self._app._tasks = updated
            self._app.tasksChanged.emit()

        for task, presented in zip(domain_tasks, service_tasks, strict=True):
            status = presented["status"]
            task_id = presented["id"]
            if status not in _TERMINAL_STATUSES or task_id in self._app._reported_terminal_tasks:
                continue
            self._app._reported_terminal_tasks.add(task_id)
            if task.task_type is TaskType.VERIFICATION:
                self._app.toastDismissRequested.emit(_MEASUREMENT_AUTH_TOAST)
            if status == TaskStatus.COMPLETED.value:
                self._app._remember_analysis_report(task)
                if task.task_type is TaskType.COMPRESSION:
                    self._app._reload_games()
                    self._app._reload_selected_history()
                    self._app._reload_updates()
                    self._app._reload_system_info()
                elif task.task_type is TaskType.VERIFICATION:
                    self._app._reload_games()
                    self._app._reload_selected_history()
                elif (
                    task.task_type is TaskType.ANALYSIS
                    and task.game_id in self._app._pending_automatic_games
                ):
                    tracker = self._app._update_tracker
                    record = tracker.get(task.game_id) if tracker is not None else None
                    if record is None or not self._app._start_automatic_compression(record):
                        self._app._pending_automatic_games.discard(task.game_id)
                self._app._emit_toast(f"{presented['name']} completed", "success")
            elif status == TaskStatus.FAILED.value:
                self._app._pending_automatic_games.discard(task.game_id)
                logger.error(
                    "Task %s failed: %s", task_id, presented.get("error") or "unknown"
                )
                self._app._emit_toast(f"{presented['name']} failed", "error")
            elif status == TaskStatus.CANCELLED.value:
                self._app._pending_automatic_games.discard(task.game_id)
            self._app.taskFinished.emit(task_id, status)
        if self._app._updates_dirty:
            self._app._reload_selected_history()
            self._app._reload_updates()
            self._app._reload_system_info()

    def _remember_analysis_report(self, task: Task) -> None:
        if task.task_type is not TaskType.ANALYSIS or not task.result:
            return
        report = qml_value(task.result)
        if isinstance(report, Mapping):
            self._app._analysis_reports[task.game_id] = dict(report)
            self._app._reload_games()

    def _change_task_state(self, action: str, task_id: str) -> bool:
        optiscaler_job = self._app._optiscaler_jobs.get(str(task_id))
        if optiscaler_job is not None:
            if action != "cancel":
                return False
            future, cancelled, _game_id = optiscaler_job
            cancelled.set()
            future.cancel()
            task = self._app._operational_tasks.get(str(task_id))
            if task is not None:
                task["stage"] = "Cancelling"
                task["updatedAt"] = datetime.now(UTC).isoformat()
            self._app._reload_tasks()
            return True
        operation = getattr(self._app._task_service, action)
        try:
            task = operation(task_id)
            self._app._reload_tasks()
        except Exception as error:
            self._app._report_error(f"trying to {action} task {task_id}", error)
            return False

        logger.info("Task %s changed via %s to %s", task.id, action, task.status.value)
        self._app._emit_toast(f"Task {task.status.value}", "info")
        return True
