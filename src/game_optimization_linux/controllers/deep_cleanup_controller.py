"""QML bridge of the deep-cleanup quarantine executor.

QML only ever sends a game id, group ids (``category/key``) and a typed
confirmation word.  Paths always come from the stored plan and journals.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Event, Lock
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QTimer

from ..models.deep_cleanup import CleanupTransaction
from ..services.deep_cleanup import CleanupError, CleanupProgress, DeepCleanupExecutor
from ..services.deep_optimization import DeepAnalysisRepository

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)
ACTIVE_STATES = frozenset({
    "preparing", "quarantining", "quarantined", "restoring", "finalizing",
    "rolling_back", "failed", "partial",
})


@dataclass
class _Operation:
    kind: str
    future: Future
    cancel: Event
    lock: Lock = field(default_factory=Lock)
    progress: dict[str, Any] = field(default_factory=dict)


class DeepCleanupController:
    def __init__(
        self,
        app: AppController,
        executor: DeepCleanupExecutor,
        repository: DeepAnalysisRepository,
        plan_loader: Callable[[str], Any],
    ) -> None:
        self._app = app
        self._executor = executor
        self._repository = repository
        self._plan_loader = plan_loader
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game-optimization-deep-cleanup")
        self._operations: dict[str, _Operation] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._timer = QTimer(app)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)
        try:
            # Unfinished journals are reconciled, never finalized automatically.
            self._recovered = self._executor.recover()
        except Exception as error:
            logger.warning("Deep cleanup recovery failed: %s", error)
            self._recovered = []

    # -- state --------------------------------------------------------------

    def state(self, game_id: str) -> dict[str, Any]:
        transactions = self._executor.journal.for_game(game_id)
        active = next((item for item in transactions if item.state in ACTIVE_STATES), None)
        operation = self._operations.get(game_id)
        progress: dict[str, Any] = {}
        if operation is not None:
            with operation.lock:
                progress = dict(operation.progress)
        plan = self._plan_loader(game_id)
        return {
            "success": True,
            "busy": operation is not None,
            "operation": operation.kind if operation is not None else "",
            "progress": progress,
            "result": dict(self._results.get(game_id, {})),
            "active": self._transaction_view(active) if active is not None else {},
            "history": [item.history_entry() for item in transactions[:10]],
            "planStale": bool(plan.stale) if plan is not None else False,
            "selectable": sorted(self._executor.selectable_groups(plan)) if plan is not None and active is None else [],
        }

    @staticmethod
    def _transaction_view(transaction: CleanupTransaction) -> dict[str, Any]:
        view = transaction.history_entry()
        quarantined = [entry for entry in transaction.entries if entry.status == "quarantined"]
        view.update({
            "gameName": transaction.game_name,
            "quarantinedBytes": sum(entry.size for entry in quarantined),
            "canRestore": transaction.state in {"quarantined", "partial", "failed"}
            and any(entry.status in {"quarantined", "conflict"} for entry in transaction.entries),
            "canFinalize": transaction.state == "quarantined",
            "needsAttention": transaction.state in {"partial", "failed"},
            "inProgress": transaction.state in {"preparing", "quarantining", "restoring", "finalizing", "rolling_back"},
        })
        return view

    # -- operations ------------------------------------------------------------

    def start_quarantine(self, game_id: str, group_ids: list[str]) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "code": "game_missing"}
        if game_id in self._operations:
            return {"success": False, "code": "busy"}
        if any(item.state in ACTIVE_STATES for item in self._executor.journal.for_game(game_id)):
            return {"success": False, "code": "active_transaction"}
        plan = self._plan_loader(game_id)
        if plan is None:
            return {"success": False, "code": "plan_missing"}
        fingerprint = self._repository.fingerprint(plan)

        def run(cancel: Event, report) -> CleanupTransaction:
            transaction = self._executor.prepare(game, plan, fingerprint, list(group_ids))
            return self._executor.quarantine(transaction, cancel_event=cancel, progress=report)

        return self._submit(game_id, "quarantine", run)

    def restore(self, game_id: str, transaction_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        transaction = self._find(game_id, transaction_id)
        if game is None or transaction is None:
            return {"success": False, "code": "transaction_missing"}
        return self._submit(game_id, "restore",
                            lambda _cancel, report: self._executor.restore(transaction, game, progress=report))

    def finalize(self, game_id: str, transaction_id: str, confirmation: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        transaction = self._find(game_id, transaction_id)
        if game is None or transaction is None:
            return {"success": False, "code": "transaction_missing"}
        return self._submit(game_id, "finalize",
                            lambda _cancel, report: self._executor.finalize(transaction, game, confirmation, progress=report))

    def cancel(self, game_id: str) -> bool:
        operation = self._operations.get(game_id)
        if operation is None or operation.kind != "quarantine":
            return False
        operation.cancel.set()
        return True

    def shutdown(self) -> None:
        for operation in self._operations.values():
            operation.cancel.set()
        self._timer.stop()
        # Let a running rename/rollback finish so the journal stays coherent.
        self._pool.shutdown(wait=True, cancel_futures=True)

    # -- internals ---------------------------------------------------------------

    def _find(self, game_id: str, transaction_id: str) -> CleanupTransaction | None:
        return next((item for item in self._executor.journal.for_game(game_id)
                     if item.transaction_id == str(transaction_id)), None)

    def _submit(self, game_id: str, kind: str, run) -> dict[str, Any]:
        if game_id in self._operations:
            return {"success": False, "code": "busy"}
        cancel = Event()
        holder: dict[str, _Operation] = {}

        def report(value: CleanupProgress) -> None:
            operation = holder.get("operation")
            if operation is not None:
                with operation.lock:
                    operation.progress = value.to_dict()

        future = self._pool.submit(run, cancel, report)
        operation = _Operation(kind, future, cancel)
        holder["operation"] = operation
        self._operations[game_id] = operation
        self._results.pop(game_id, None)
        if not self._timer.isActive():
            self._timer.start()
        self._app.deepCleanupChanged.emit(game_id)
        return {"success": True, "status": "running", "operation": kind}

    def _poll(self) -> None:
        for game_id, operation in tuple(self._operations.items()):
            if not operation.future.done():
                self._app.deepCleanupChanged.emit(game_id)
                continue
            del self._operations[game_id]
            try:
                transaction = operation.future.result()
            except CleanupError as error:
                if error.stale:
                    self._repository.mark_stale(game_id)
                    reload = getattr(self._app, "_deep_optimization_controller", None)
                    if reload is not None:
                        reload.forget(game_id)
                self._results[game_id] = {"operation": operation.kind, "success": False,
                                          "code": error.code, "detail": error.detail}
            except Exception as error:
                logger.exception("Deep cleanup %s failed", operation.kind)
                self._results[game_id] = {"operation": operation.kind, "success": False,
                                          "code": "unexpected_error", "detail": type(error).__name__}
            else:
                self._results[game_id] = {
                    "operation": operation.kind,
                    "success": transaction.state in {"quarantined", "restored", "finalized"}
                    and not (operation.kind == "quarantine" and transaction.state != "quarantined"),
                    "state": transaction.state, "note": transaction.note,
                    "counts": transaction.counts(),
                }
            self._app.deepCleanupChanged.emit(game_id)
            self._app.deepAnalysisChanged.emit(game_id)
        if not self._operations:
            self._timer.stop()


__all__ = ["DeepCleanupController"]
