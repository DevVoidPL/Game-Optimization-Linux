from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import logging
import os
from threading import Event
from typing import TYPE_CHECKING, Any, cast

from ..models import (
    AutomaticCompressionMode,
    FilesystemType,
    Game,
    Launcher,
    Task,
    TaskStatus,
    TaskType,
)
from ..services import GameUpdateRecord, GameUpdateStatus, classify_compression_effect
from .presenters import qml_value

if TYPE_CHECKING:
    from .app_controller import AppController

_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.INTERRUPTED.value,
}

logger = logging.getLogger(__name__)


class UpdatesController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    def ignoreUpdate(self, game_id: str) -> bool:
        tracker = self._app._update_tracker
        if tracker is None:
            return False
        try:
            tracker.ignore(game_id)
        except Exception as error:
            self._app._report_error(f"ignoring update for {game_id}", error)
            return False
        self._app._reload_updates()
        self._app._emit_toast("This game update was ignored", "info")
        return True

    def dismissUpdate(self, row_id: str) -> bool:
        """Hide one finished/inactive Updates row without deleting history."""

        normalized = str(row_id).strip()
        row = next(
            (item for item in self._app._updates if item.get("rowId") == normalized),
            None,
        )
        if row is None or row.get("canDismiss") is not True:
            return False
        game_id = str(row.get("gameId", ""))
        if game_id and self._app._active_task_for_game(game_id) is not None:
            return False
        self._app._dismissed_updates[normalized] = str(
            row.get("displayVersion") or normalized
        )
        self._app._save_update_display_state()
        self._app._reload_updates()
        return True

    def clearFinishedUpdates(self) -> int:
        """Hide completed/error rows while preserving actionable active work."""

        removable_statuses = {
            GameUpdateStatus.INVENTORY.value,
            GameUpdateStatus.UP_TO_DATE.value,
            GameUpdateStatus.IGNORED.value,
            GameUpdateStatus.ERROR.value,
        }
        rows = [
            row
            for row in self._app._updates
            if row.get("canDismiss") is True
            and (
                row.get("sectionKey") == "recently_optimized"
                or str(row.get("updateStatus", "")) in removable_statuses
            )
        ]
        return self._app._dismiss_update_rows(rows)

    def clearUnavailableUpdates(self) -> int:
        """Hide rows belonging to disconnected or already forgotten games."""

        rows = [
            row
            for row in self._app._updates
            if row.get("canDismiss") is True
            and (
                row.get("libraryAvailable") is False
                or str(row.get("gameId", "")) not in self._app._domain_games
            )
        ]
        return self._app._dismiss_update_rows(rows)

    def clearHiddenUpdatesHistory(self) -> int:
        """Forget presentation tombstones so hidden events may be shown again."""

        removed = len(self._app._dismissed_updates)
        if not removed:
            return 0
        self._app._dismissed_updates.clear()
        self._app._save_update_display_state()
        self._app._reload_updates()
        self._app._emit_toast("Hidden Updates history was cleared", "success")
        return removed

    def _dismiss_update_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        removed = 0
        for row in rows:
            row_id = str(row.get("rowId", ""))
            game_id = str(row.get("gameId", ""))
            if (
                not row_id
                or (game_id and self._app._active_task_for_game(game_id) is not None)
            ):
                continue
            self._app._dismissed_updates[row_id] = str(
                row.get("displayVersion") or row_id
            )
            removed += 1
        if removed:
            self._app._save_update_display_state()
            self._app._reload_updates()
        return removed

    def _save_update_display_state(self) -> None:
        store = self._app._update_display_store
        if store is None:
            return
        try:
            store.save(self._app._dismissed_updates)
        except Exception as error:
            logger.warning("Could not save Updates display state: %s", error)

    def _active_task_for_game(self, game_id: str) -> Task | None:
        try:
            tasks = self._app._task_service.list_tasks()
        except Exception:
            return None
        active = [
            task
            for task in tasks
            if task.game_id == game_id
            and task.status.value not in _TERMINAL_STATUSES
        ]
        return active[-1] if active else None

    def _update_record_to_qml(self, record: GameUpdateRecord) -> dict[str, Any]:
        game = self._app._domain_games.get(record.game_id)
        if game is None and record.app_id:
            game = next(
                (
                    candidate
                    for candidate in self._app._domain_games.values()
                    if str(candidate.steam_app_id or "") == record.app_id
                ),
                None,
            )
        presented_game = (
            self._app._present_game(game, analysis_report=self._app._analysis_reports.get(game.id))
            if game is not None
            else {}
        )
        available = bool(
            game is not None
            and game.library_available
            and record.status is not GameUpdateStatus.LIBRARY_UNAVAILABLE
        )
        status_label = self._app._record_status_label(record.status)
        task = self._app._active_task_for_game(record.game_id)
        if task is not None:
            if task.task_type is TaskType.COMPRESSION:
                status_label = (
                    "Queued"
                    if task.status is TaskStatus.QUEUED
                    else "Compressing"
                )
            elif task.task_type is TaskType.ANALYSIS:
                status_label = (
                    "Queued"
                    if task.status is TaskStatus.QUEUED
                    else "Analyzing"
                )
        report = self._app._analysis_reports.get(record.game_id)
        du = report.get("btrfs_du", {}) if isinstance(report, Mapping) else {}
        report_ready = bool(
            isinstance(report, Mapping)
            and report.get("game_id") == record.game_id
            and report.get("scan_complete") is True
            and report.get("is_btrfs") is True
            and report.get("compression_eligible") is True
            and isinstance(du, Mapping)
            and du.get("available") is True
            and du.get("state") == "not_detected"
        )
        attention = record.status in {
            GameUpdateStatus.WAITING_FOR_STABILITY,
            GameUpdateStatus.ANALYSIS_REQUIRED,
        }
        can_analyze = bool(
            available
            and record.status is GameUpdateStatus.ANALYSIS_REQUIRED
            and task is None
        )
        can_compress = bool(
            available
            and record.status is GameUpdateStatus.ANALYSIS_REQUIRED
            and task is None
            and report_ready
        )
        observation = record.current_observation or record.pending_observation
        section = (
            "compression_pending"
            if record.status is GameUpdateStatus.ANALYSIS_REQUIRED or task is not None
            else "game_updates"
        )
        update_identity = self._app._update_event_identity(record, game)
        return {
            **presented_game,
            "rowId": f"update:{record.app_id or record.game_id}:{update_identity}",
            "displayVersion": update_identity,
            "updateIdentity": update_identity,
            "sectionKey": section,
            "gameId": record.game_id,
            "appId": record.app_id,
            "gameKnown": game is not None,
            "name": game.name if game is not None else f"Steam {record.app_id}",
            "gameName": game.name if game is not None else f"Steam {record.app_id}",
            "compressionState": status_label,
            "status": status_label,
            "updateStatus": record.status.value,
            "libraryAvailable": available,
            "canAnalyze": can_analyze,
            "canCompress": can_compress,
            "canIgnore": bool(attention and available and task is None),
            "requiresFullAnalysis": bool(record.requires_full_analysis),
            "installationDetected": bool(record.installation_detected),
            "newFiles": list(record.changes.new_files),
            "modifiedFiles": list(record.changes.modified_files),
            "deletedFiles": list(record.changes.deleted_files),
            "changedFileCount": (
                len(record.changes.new_files)
                + len(record.changes.modified_files)
                + len(record.changes.deleted_files)
            ),
            "changedBytes": int(record.changes.changed_bytes),
            "changesReliable": bool(record.changes.reliable),
            "detectedAt": (
                record.detected_at.isoformat()
                if record.detected_at is not None
                else ""
            ),
            "updatedAt": record.updated_at.isoformat(),
            "buildId": str(observation.build_id or "") if observation else "",
            "recommendedProfile": (
                self._app._settings_model.automatic_compression_profile.value
            ),
            "error": str(record.last_error),
            "ignored": record.status is GameUpdateStatus.IGNORED,
            "canDismiss": task is None,
        }

    def _history_update_rows(self) -> list[dict[str, Any]]:
        service = self._app._compression_service
        if service is None:
            return []
        try:
            history = service.history()
        except Exception as error:
            logger.warning("Could not read recent compression history: %s", error)
            return []
        rows: list[dict[str, Any]] = []
        for entry in history[:20]:
            history_path = str(
                entry.get("library_path")
                or entry.get("libraryPath")
                or entry.get("game_path")
                or entry.get("path")
                or ""
            )
            if history_path and any(
                self._app._path_is_within_library_path(history_path, ignored)
                for ignored in self._app._settings_model.ignored_steam_libraries
            ):
                continue
            game_id = str(entry.get("game_id", ""))
            game = self._app._domain_games.get(game_id)
            app_id = str(entry.get("app_id") or game_id.removeprefix("steam-"))
            if game is None and app_id:
                game = next(
                    (
                        candidate
                        for candidate in self._app._domain_games.values()
                        if str(candidate.steam_app_id or "") == app_id
                    ),
                    None,
                )
            presented_game = self._app._present_game(game) if game is not None else {}
            status = str(entry.get("status", ""))
            state = (
                "Optimized"
                if status in {"completed", "completed_with_warning"}
                else "Verification required"
                if status == "verification_required"
                else "Failed"
                if status == "failed"
                else "Unknown"
            )
            rows.append(
                {
                    **presented_game,
                    **cast(dict[str, Any], qml_value(dict(entry))),
                    "rowId": f"history:{entry.get('id', '')}",
                    "displayVersion": str(
                        entry.get("completed_at")
                        or entry.get("updated_at")
                        or entry.get("id", "")
                    ),
                    "historyId": str(entry.get("id", "")),
                    "sectionKey": "recently_optimized",
                    "gameId": game_id,
                    "name": str(
                        entry.get("game_name")
                        or (game.name if game is not None else "Unknown game")
                    ),
                    "compressionState": state,
                    "libraryAvailable": bool(
                        game is not None and game.library_available
                    ),
                    "canAnalyze": False,
                    "canCompress": False,
                    "canIgnore": False,
                    "canDismiss": self._app._active_task_for_game(game_id) is None,
                    "error": str(entry.get("error") or ""),
                }
            )
        return rows

    def _update_row_is_dismissed(self, row: Mapping[str, Any]) -> bool:
        row_id = str(row.get("rowId", ""))
        if not row_id:
            return False
        return self._app._dismissed_updates.get(row_id) == str(
            row.get("displayVersion") or row_id
        )

    def _reload_updates(self, *, emit_signal: bool = True) -> None:
        tracker = self._app._update_tracker
        records = tracker.list_records() if tracker is not None else ()
        record_rows = [
            self._app._update_record_to_qml(record)
            for record in records
            if not self._app._update_record_is_in_ignored_library(record)
        ]
        history_rows = self._app._history_update_rows()
        record_rows = [
            row
            for row in record_rows
            if not (
                self._app._library_scan_status == "ready"
                and row.get("gameKnown") is not True
                and self._app._active_task_for_game(str(row.get("gameId", ""))) is None
            )
            if not self._app._update_row_is_dismissed(row)
            and not self._app._update_row_is_very_old(row)
        ]
        history_rows = [
            row
            for row in history_rows
            if not self._app._update_row_is_dismissed(row)
            and not self._app._update_row_is_very_old(row)
        ]
        self._app._updates = record_rows + history_rows
        attention_statuses = {
            GameUpdateStatus.WAITING_FOR_STABILITY.value,
            GameUpdateStatus.ANALYSIS_REQUIRED.value,
            GameUpdateStatus.ERROR.value,
            GameUpdateStatus.LIBRARY_UNAVAILABLE.value,
        }
        needs_attention = sum(
            str(row.get("updateStatus", "")) in attention_statuses
            for row in record_rows
        )
        pending_count = sum(
            row.get("compressionState")
            in {"Queued", "Analyzing", "Compressing", "Analysis required"}
            for row in record_rows
        )
        recovered_bytes = sum(
            max(0, int(entry.get("actual_saved_bytes") or 0))
            for entry in history_rows
        )
        self._app._updates_summary = {
            "needsCheckCount": int(needs_attention),
            "updateCount": int(needs_attention),
            "pendingCount": int(pending_count),
            "queuedCount": int(pending_count),
            "recentlyOptimizedCount": len(history_rows),
            "recentRecoveredBytes": int(recovered_bytes),
        }
        self._app._updates_dirty = False
        if emit_signal:
            self._app.updatesChanged.emit()
            self._app.updatesSummaryChanged.emit()

    def _schedule_update_observations(self) -> None:
        if self._app._shutdown_requested:
            return
        tracker = self._app._update_tracker
        executor = self._app._update_executor
        if tracker is None or executor is None:
            return
        scheduled = False
        for game in tuple(self._app._domain_games.values()):
            if (
                game.launcher is not Launcher.STEAM
                or not game.steam_app_id
                or game.is_steam_tool
                or game.id in self._app._update_jobs
            ):
                continue
            cancel_event = Event()
            try:
                future = executor.submit(
                    tracker.observe,
                    game,
                    cancel_event=cancel_event,
                )
            except RuntimeError as error:
                if not self._app._shutdown_requested:
                    logger.warning(
                        "Could not schedule update scan for %s: %s",
                        game.id,
                        error,
                    )
                continue
            self._app._update_jobs[game.id] = (future, cancel_event)
            scheduled = True
        if scheduled:
            self._app._inventory_scan_started = True
            self._app._last_periodic_rescan = datetime.now(UTC)

    def _poll_update_jobs(self) -> None:
        if not self._app._update_jobs:
            return
        changed = False
        for game_id, (future, _event) in tuple(self._app._update_jobs.items()):
            if not future.done():
                continue
            self._app._update_jobs.pop(game_id, None)
            try:
                record = future.result()
            except Exception as error:
                if not self._app._shutdown_requested:
                    logger.warning(
                        "Steam update scan for %s failed: %s",
                        game_id,
                        error,
                    )
            else:
                changed = True
                logger.debug(
                    "Update state for %s is %s",
                    game_id,
                    record.status.value,
                )
        if (
            self._app._inventory_completion_pending
            and self._app._inventory_scan_started
            and not self._app._update_jobs
            and self._app._update_tracker is not None
        ):
            try:
                self._app._update_tracker.complete_initial_inventory()
            except Exception as error:
                logger.warning("Could not finalize update inventory: %s", error)
            else:
                self._app._inventory_completion_pending = False
                changed = True
        if changed:
            self._app._reload_updates()
            self._app._queue_eligible_automatic_compression()

    def _automatic_mode_allows(self, record: GameUpdateRecord) -> bool:
        mode = self._app._settings_model.automatic_compression_mode
        if mode is AutomaticCompressionMode.OFF:
            return False
        if record.installation_detected:
            return bool(mode.allows_installation)
        return bool(mode.allows_update)

    def _automatic_library_allowed(self, game: Game) -> bool:
        configured = self._app._settings_model.automatic_compression_libraries
        if not configured:
            return True
        candidate = game.library_path or game.install_path
        try:
            candidate_path = os.path.abspath(os.fspath(candidate))
        except (OSError, TypeError, ValueError):
            return False
        for library in configured:
            try:
                allowed_path = os.path.abspath(os.fspath(library))
                if os.path.commonpath((candidate_path, allowed_path)) == allowed_path:
                    return True
            except (OSError, TypeError, ValueError):
                continue
        return False

    def _automatic_task_active(self, game_id: str = "") -> bool:
        try:
            tasks = self._app._task_service.list_tasks()
        except Exception:
            return False
        return any(
            task.status.value not in _TERMINAL_STATUSES
            and task.task_type in {TaskType.ANALYSIS, TaskType.COMPRESSION}
            and (not game_id or task.game_id == game_id)
            for task in tasks
        )

    def _queue_eligible_automatic_compression(self) -> None:
        tracker = self._app._update_tracker
        if (
            tracker is None
            or self._app._settings_model.automatic_compression_mode
            is AutomaticCompressionMode.OFF
            or self._app._shutdown_requested
        ):
            return
        try:
            active_compression = sum(
                task.task_type is TaskType.COMPRESSION
                and task.status.value not in _TERMINAL_STATUSES
                for task in self._app._task_service.list_tasks()
            )
        except Exception:
            active_compression = 0
        available_slots = max(
            0,
            self._app._settings_model.automatic_compression_max_jobs
            - active_compression,
        )
        if available_slots <= 0:
            return
        for record in tracker.list_records():
            if available_slots <= 0:
                break
            if (
                record.status is not GameUpdateStatus.ANALYSIS_REQUIRED
                or not self._app._automatic_mode_allows(record)
                or record.app_id
                in self._app._settings_model.automatic_compression_skipped_app_ids
                or record.game_id in self._app._pending_automatic_games
            ):
                continue
            game = self._app._domain_games.get(record.game_id)
            if (
                game is None
                or not self._app._game_actions_allowed(game)
                or game.update_in_progress
                or game.filesystem is not FilesystemType.BTRFS
                or not self._app._automatic_library_allowed(game)
                or self._app._automatic_task_active(game.id)
            ):
                continue
            self._app._pending_automatic_games.add(game.id)
            report = self._app._analysis_reports.get(game.id)
            if isinstance(report, Mapping):
                if self._app._start_automatic_compression(record):
                    available_slots -= 1
                    continue
            try:
                task = self._app._task_service.enqueue_analysis(game)
            except Exception as error:
                self._app._pending_automatic_games.discard(game.id)
                logger.warning(
                    "Could not queue automatic analysis for %s: %s",
                    game.id,
                    error,
                )
                continue
            self._app._reload_tasks()
            if self._app._settings_model.automatic_compression_notify:
                self._app._emit_toast(
                    f"Checking {game.name} before automatic compression",
                    "info",
                )
            logger.info(
                "Queued automatic safety analysis %s for %s",
                task.id,
                game.id,
            )
            # Analysis and compression share one bounded queue.  Waiting for
            # its result prevents a second game from being staged as a writer.
            available_slots -= 1

    def _start_automatic_compression(self, record: GameUpdateRecord) -> bool:
        game = self._app._domain_games.get(record.game_id)
        if (
            game is None
            or not self._app._automatic_mode_allows(record)
            or not self._app._game_actions_allowed(game)
            or game.update_in_progress
            or game.filesystem is not FilesystemType.BTRFS
        ):
            self._app._pending_automatic_games.discard(record.game_id)
            return False
        report = self._app._analysis_reports.get(game.id)
        if (
            not isinstance(report, Mapping)
            or report.get("scan_complete") is not True
            or report.get("is_btrfs") is not True
            or report.get("writable") is not True
            or report.get("game_running") is True
        ):
            self._app._pending_automatic_games.discard(game.id)
            return False
        btrfs_du = (
            report.get("btrfs_du", {})
            if isinstance(report, Mapping) else {}
        )
        if (
            not isinstance(btrfs_du, Mapping)
            or btrfs_du.get("available") is not True
            or btrfs_du.get("state") != "not_detected"
        ):
            # Breaking snapshot/reflink sharing requires a human to review the
            # measured growth warning.  Generic automatic opt-in is not enough.
            self._app._pending_automatic_games.discard(game.id)
            if self._app._settings_model.automatic_compression_notify:
                self._app._emit_toast(
                    f"Automatic compression skipped for {game.name}: "
                    "shared extents require manual confirmation",
                    "warning",
                )
            return False
        current_measurement = self._app._current_authoritative_compsize(game.id)
        current_classification = classify_compression_effect(
            current_measurement.get("compsize_uncompressed_bytes"),
            current_measurement.get("compsize_disk_bytes"),
        )
        if current_classification.get("key") == "no_compression":
            # A zero-effect installation may be a special/snapshot-backed
            # layout.  It remains available manually, but is never selected by
            # the unattended policy without a human reviewing the plan.
            self._app._pending_automatic_games.discard(game.id)
            if self._app._settings_model.automatic_compression_notify:
                self._app._emit_toast(
                    f"Automatic compression skipped for {game.name}: "
                    "the current measurement requires manual review",
                    "warning",
                )
            return False
        presented = self._app.prepareCompression(
            game.id,
            self._app._settings_model.automatic_compression_profile.value,
            not record.requires_full_analysis,
        )
        plan_id = str(presented.get("planId", ""))
        if not bool(presented.get("valid", False)) or not plan_id:
            self._app._pending_automatic_games.discard(game.id)
            return False
        if presented.get("additionalConfirmationRequired") is True:
            self._app._pending_automatic_games.discard(game.id)
            if self._app._settings_model.automatic_compression_notify:
                self._app._emit_toast(
                    f"Automatic compression skipped for {game.name}: "
                    "the estimated additional benefit requires manual confirmation",
                    "warning",
                )
            return False
        return self._app._start_compression_plan(
            plan_id,
            confirmed=False,
            automatic_authorized=True,
        )
