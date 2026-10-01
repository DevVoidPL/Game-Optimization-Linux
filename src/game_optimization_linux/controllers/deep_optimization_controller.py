"""Deep cleaning/compression analysis for QML: read-only, cancellable, off the Qt thread."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer, QUrl

from ..models.deep_optimization import DEEP_CATEGORIES, SAFETY_LEVELS, DeepOptimizationPlan
from ..services.deep_optimization import (
    DeepAnalysisRepository,
    DeepOptimizationAnalyzer,
    DeepScanCancelled,
    DeepScanProgress,
    apply_target_language,
    measurement_from_analysis_report,
    measurement_from_compression_result,
)

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


@dataclass
class _Job:
    future: Future
    cancel: Event
    lock: Lock = field(default_factory=Lock)
    progress: dict[str, Any] = field(default_factory=lambda: {
        "stage": "queued", "files": 0, "bytes": 0, "category": ""})


class DeepOptimizationController:
    def __init__(
        self,
        app: AppController,
        analyzer: DeepOptimizationAnalyzer,
        repository: DeepAnalysisRepository,
    ) -> None:
        self._app = app
        self._analyzer = analyzer
        self._repository = repository
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game-optimization-deep-analysis")
        self._jobs: dict[str, _Job] = {}
        self._status: dict[str, dict[str, str]] = {}
        self._plans: dict[str, DeepOptimizationPlan] = {}
        # Progress reaches QML at most five times a second, never per file.
        self._timer = QTimer(app)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)

    # -- slots ------------------------------------------------------------------

    def start(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select an available game first"}
        if game_id in self._jobs:
            return {"success": True, "status": "running"}
        job_holder: dict[str, _Job] = {}
        cancel = Event()

        def progress(value: DeepScanProgress) -> None:
            job = job_holder.get("job")
            if job is not None:
                with job.lock:
                    job.progress = value.to_dict()

        # Snapshot (on the Qt thread) the same measurement the compression
        # header shows, so both screens report one physical size.
        measurement = self.measurement_for(game_id)
        future = self._executor.submit(
            self._analyzer.analyze, game, cancel_event=cancel,
            progress_callback=progress, measurement=measurement,
        )
        job = _Job(future, cancel)
        job_holder["job"] = job
        self._jobs[game_id] = job
        self._status[game_id] = {"status": "running", "error": ""}
        if not self._timer.isActive():
            self._timer.start()
        self._app.deepAnalysisChanged.emit(game_id)
        return {"success": True, "status": "running"}

    def measurement_for(self, game_id: str):
        authoritative = getattr(self._app, "_current_authoritative_compsize", None)
        if callable(authoritative):
            try:
                found = measurement_from_compression_result(authoritative(game_id))
            except Exception:
                found = None
            if found is not None:
                return found
        report = getattr(self._app, "_analysis_reports", {}).get(game_id)
        return measurement_from_analysis_report(report) if isinstance(report, dict) else None

    def cancel(self, game_id: str) -> bool:
        job = self._jobs.get(game_id)
        if job is None:
            return False
        job.cancel.set()
        return True

    def state(self, game_id: str, safety_filter: str = "all") -> dict[str, Any]:
        status = dict(self._status.get(game_id, {"status": "idle", "error": ""}))
        job = self._jobs.get(game_id)
        progress: dict[str, Any] = {}
        if job is not None:
            with job.lock:
                progress = dict(job.progress)
        plan = self._plan(game_id)
        if plan is not None and status["status"] == "idle":
            status["status"] = "completed"
        return {
            "success": True,
            **status,
            "progress": progress,
            "hasPlan": plan is not None,
            "summary": self._summary(plan) if plan is not None else {},
            "categories": self._categories(plan, safety_filter) if plan is not None else [],
            "counts": self._counts(plan) if plan is not None else {},
            "savings": plan.savings_summary() if plan is not None else {},
            "globalLeftovers": self._repository.global_leftovers(),
            "globalGroups": [
                group.to_dict() for group in plan.groups if group.scope == "global"
            ] if plan is not None else [],
            "filter": safety_filter if safety_filter in SAFETY_LEVELS else "all",
        }

    def set_language(self, game_id: str, language: str) -> dict[str, Any]:
        plan = self._plan(game_id)
        if plan is not None:
            updated = apply_target_language(plan, language)
            self._plans[game_id] = updated
            try:
                self._repository.save(updated)
            except (OSError, ValueError) as error:
                logger.warning("Could not store the deep analysis language: %s", error)
            self._app.deepAnalysisChanged.emit(game_id)
        return self.state(game_id)

    def export(self, game_id: str, destination: str) -> dict[str, Any]:
        plan = self._plan(game_id)
        if plan is None:
            return {"success": False, "error": "Run the deep analysis first"}
        text = str(destination or "").strip()
        url = QUrl(text)
        if url.scheme():
            if not url.isLocalFile():
                return {"success": False, "error": "Only local files are supported"}
            text = url.toLocalFile()
        if not text:
            return {"success": False, "error": "Choose a file for the report"}
        game = self._app._resolve_game(game_id, show_error=False)
        try:
            path = self._repository.export(
                plan, Path(text), game_root=game.install_path if game is not None else None
            )
        except (OSError, ValueError) as error:
            return {"success": False, "error": str(error)}
        self._app._emit_toast("Deep analysis report exported", "success")
        return {"success": True, "name": path.name}

    def shutdown(self) -> None:
        for job in self._jobs.values():
            job.cancel.set()
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- internals ---------------------------------------------------------------

    def _plan(self, game_id: str) -> DeepOptimizationPlan | None:
        if game_id not in self._plans:
            loaded = self._repository.load(game_id)
            if loaded is None:
                return None
            self._plans[game_id] = loaded
        return self._plans[game_id]

    def _poll(self) -> None:
        for game_id, job in tuple(self._jobs.items()):
            if not job.future.done():
                self._app.deepAnalysisChanged.emit(game_id)
                continue
            del self._jobs[game_id]
            try:
                plan = job.future.result()
            except DeepScanCancelled:
                # A cancelled scan never replaces the last complete plan.
                self._status[game_id] = {"status": "cancelled", "error": ""}
            except Exception as error:
                logger.warning("Deep analysis failed for %s: %s", game_id, error)
                self._status[game_id] = {"status": "failed", "error": str(error) or type(error).__name__}
            else:
                if plan.status == "completed":
                    self._plans[game_id] = plan
                    try:
                        self._repository.save(plan)
                    except (OSError, ValueError) as error:
                        logger.warning("Could not store the deep analysis: %s", error)
                    self._status[game_id] = {"status": "completed", "error": ""}
                else:
                    self._status[game_id] = {"status": "failed", "error": "; ".join(plan.blockers)}
            self._app.deepAnalysisChanged.emit(game_id)
        if not self._jobs:
            self._timer.stop()

    @staticmethod
    def _summary(plan: DeepOptimizationPlan) -> dict[str, Any]:
        data = plan.to_dict()
        return {
            "engine": plan.engine,
            "engineVersion": plan.engine_version,
            "engineConfidence": plan.engine_confidence,
            "engineEvidence": data["engine"]["evidence"],
            "filesystem": plan.filesystem,
            "isBtrfs": plan.btrfs.is_btrfs,
            "btrfs": data["btrfs"],
            "sizes": data["sizes"],
            "fileCount": plan.file_count,
            "languages": [
                {"code": code, "bytes": size}
                for code, size in sorted(plan.languages.items(), key=lambda item: -item[1])
            ],
            "targetLanguage": plan.target_language,
            "targetLanguageSource": plan.target_language_source,
            "warnings": list(plan.warnings),
            "blockers": list(plan.blockers),
            "notes": list(plan.notes),
            "complete": plan.complete,
            "createdAt": data["createdAt"],
        }

    @staticmethod
    def _categories(plan: DeepOptimizationPlan, safety_filter: str) -> list[dict[str, Any]]:
        result = []
        for category in DEEP_CATEGORIES:
            # Global leftovers of other games are shown in their own section.
            groups = [group for group in plan.groups if group.category == category and group.scope == "game"]
            shown = [
                group.to_dict() for group in groups
                if safety_filter not in SAFETY_LEVELS or group.safety == safety_filter
            ]
            result.append({
                "id": category,
                "bytes": sum(group.bytes for group in groups),
                "files": sum(group.file_count for group in groups),
                "groups": sorted(shown, key=lambda item: -item["bytes"]),
            })
        return result

    @staticmethod
    def _counts(plan: DeepOptimizationPlan) -> dict[str, int]:
        return {
            level: sum(1 for group in plan.groups if group.safety == level and group.scope == "game")
            for level in SAFETY_LEVELS
        }


__all__ = ["DeepOptimizationController"]
