"""QML bridge of the deep-cleanup stage 2B Btrfs recompression.

QML sends a game id, a profile level and file *types*; every path comes from
the stored read-only plan.  One job runs at a time, off the Qt thread.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Event, Lock
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QTimer

from ..models.btrfs_recompression import BtrfsCompressionPlan
from ..services.btrfs_recompression import (
    BtrfsRecompressionExecutor,
    BtrfsRecompressionPlanner,
    FutureCompressionProperty,
    RecompressionError,
)

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


@dataclass
class _Operation:
    kind: str
    future: Future
    cancel: Event
    lock: Lock = field(default_factory=Lock)
    progress: dict[str, Any] = field(default_factory=dict)


class DeepRecompressionController:
    def __init__(
        self,
        app: AppController,
        planner: BtrfsRecompressionPlanner | None,
        executor: BtrfsRecompressionExecutor | None,
        plan_loader: Callable[[str], Any],
        fingerprint: Callable[[Any], str],
    ) -> None:
        self._app = app
        self._planner = planner
        self._executor = executor
        self._property = FutureCompressionProperty(planner) if planner is not None else None
        self._plan_loader = plan_loader
        self._fingerprint = fingerprint
        self._plans: dict[str, BtrfsCompressionPlan] = {}
        self._operations: dict[str, _Operation] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game-optimization-recompression")
        self._timer = QTimer(app)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        if planner is not None:
            try:
                planner.recover()  # never resumes; interrupted jobs become partial
            except Exception as error:
                logger.warning("Recompression recovery failed: %s", error)

    # -- state -------------------------------------------------------------------

    def state(self, game_id: str) -> dict[str, Any]:
        if self._planner is None:
            return {"success": True, "available": False, "reason": "provider_unavailable"}
        operation = self._operations.get(game_id)
        progress: dict[str, Any] = {}
        if operation is not None:
            with operation.lock:
                progress = dict(operation.progress)
        try:
            capabilities = self._planner.capabilities()
        except Exception:
            capabilities = None
        plan = self._plans.get(game_id)
        deep = self._plan_loader(game_id)
        stale = bool(plan is not None and (deep is None or deep.stale
                                           or self._fingerprint(deep) != plan.deep_plan_fingerprint))
        jobs = self._planner.jobs(game_id)
        return {
            "success": True,
            "available": bool(capabilities and capabilities.recompression_available),
            "capabilities": capabilities.to_dict() if capabilities is not None else {},
            "busy": operation is not None,
            "operation": operation.kind if operation is not None else "",
            "progress": progress,
            "result": dict(self._results.get(game_id, {})),
            "plan": self._plan_view(plan) if plan is not None else {},
            "planStale": stale,
            "history": [job.history_entry() for job in jobs[:10]],
            "futureProperty": self._property_view(game_id, capabilities),
        }

    @staticmethod
    def _plan_view(plan: BtrfsCompressionPlan) -> dict[str, Any]:
        groups = plan.type_groups()
        return {
            "planId": plan.plan_id, "level": plan.profile_level, "algorithm": plan.algorithm,
            "measurementSource": plan.measurement_source, "counts": plan.counts(),
            "groups": groups,
            "candidateBytes": sum(group["bytes"] for group in groups),
            "savingsLow": sum(group["savingsLow"] for group in groups),
            "savingsHigh": sum(group["savingsHigh"] for group in groups),
            "createdAt": plan.created_at.isoformat(),
        }

    def _property_view(self, game_id: str, capabilities) -> dict[str, Any]:
        available = bool(capabilities is not None and capabilities.future_property_available)
        state = self._property.state(game_id) if self._property is not None else {}
        return {"available": available, "enabled": bool(state.get("enabled")),
                "previous": str(state.get("previous", "")),
                "reason": "" if available else "restore_not_documented"}

    # -- operations ---------------------------------------------------------------

    def analyze(self, game_id: str, level: Any) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        deep = self._plan_loader(game_id)
        if game is None or self._planner is None:
            return {"success": False, "code": "provider_unavailable"}
        if deep is None:
            return {"success": False, "code": "deep_plan_required"}
        chosen = None if level in (None, "", -1) else int(level)
        fingerprint = self._fingerprint(deep)

        def run(cancel: Event, report) -> BtrfsCompressionPlan:
            return self._planner.plan(game, deep, fingerprint, chosen,
                                      measurement_source=deep.sizes.measurement_source,
                                      cancel=cancel, progress=report)

        return self._submit(game_id, "analyze", run)

    def start(self, game_id: str, types: list[str]) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        plan = self._plans.get(game_id)
        deep = self._plan_loader(game_id)
        if game is None or self._executor is None or plan is None or deep is None:
            return {"success": False, "code": "plan_required"}
        fingerprint = self._fingerprint(deep)
        baseline = deep.sizes.to_dict()
        return self._submit(game_id, "recompress", lambda cancel, report: self._executor.run(
            game, plan, deep, fingerprint, list(types), baseline=baseline, cancel=cancel, progress=report))

    def cancel(self, game_id: str) -> bool:
        operation = self._operations.get(game_id)
        if operation is None:
            return False
        operation.cancel.set()  # honoured between files, never mid-file
        return True

    def set_future_property(self, game_id: str, enabled: bool) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None or self._property is None:
            return {"success": False, "code": "property_unavailable"}
        try:
            result = self._property.enable(game) if enabled else self._property.restore(game)
        except RecompressionError as error:
            return {"success": False, "code": error.code}
        except OSError as error:
            return {"success": False, "code": type(error).__name__}
        self._app.deepRecompressionChanged.emit(game_id)
        return {"success": True, **result}

    def shutdown(self) -> None:
        for operation in self._operations.values():
            operation.cancel.set()
        self._timer.stop()
        self._pool.shutdown(wait=True, cancel_futures=True)

    # -- internals --------------------------------------------------------------------

    def _submit(self, game_id: str, kind: str, run) -> dict[str, Any]:
        if game_id in self._operations:
            return {"success": False, "code": "busy"}
        cancel = Event()
        holder: dict[str, _Operation] = {}

        def report(values: dict[str, Any]) -> None:
            operation = holder.get("operation")
            if operation is not None:
                with operation.lock:
                    operation.progress = dict(values)

        future = self._pool.submit(run, cancel, report)
        operation = _Operation(kind, future, cancel)
        holder["operation"] = operation
        self._operations[game_id] = operation
        self._results.pop(game_id, None)
        if not self._timer.isActive():
            self._timer.start()
        self._app.deepRecompressionChanged.emit(game_id)
        return {"success": True, "status": "running", "operation": kind}

    def _poll(self) -> None:
        for game_id, operation in tuple(self._operations.items()):
            if not operation.future.done():
                self._app.deepRecompressionChanged.emit(game_id)
                continue
            del self._operations[game_id]
            try:
                value = operation.future.result()
            except RecompressionError as error:
                self._results[game_id] = {"operation": operation.kind, "success": False,
                                          "code": error.code, "stale": error.stale}
            except Exception as error:
                logger.exception("Recompression %s failed", operation.kind)
                self._results[game_id] = {"operation": operation.kind, "success": False,
                                          "code": "unexpected_error", "detail": type(error).__name__}
            else:
                if operation.kind == "analyze":
                    self._plans[game_id] = value
                    self._results[game_id] = {"operation": "analyze", "success": True}
                else:
                    self._results[game_id] = {"operation": "recompress",
                                              "success": value.state == "completed",
                                              "state": value.state, "note": value.note,
                                              "job": value.history_entry()}
                    self._plans.pop(game_id, None)  # a new analysis is required
            self._app.deepRecompressionChanged.emit(game_id)
        if not self._operations:
            self._timer.stop()


__all__ = ["DeepRecompressionController"]
