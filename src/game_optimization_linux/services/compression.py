"""Application-level orchestration for guarded compression plans and history."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
import logging
from threading import Event, RLock
from typing import Any, Protocol

from game_optimization_linux.models.compression import (
    CompressionCancelled,
    CompressionMeasurement,
    CompressionPlan,
    CompressionPlanRejected,
    CompressionResult,
    CompressionToolCapabilities,
)
from game_optimization_linux.models.enums import CompressionProfile
from game_optimization_linux.models.game import Game
from game_optimization_linux.services.btrfs_analysis import BtrfsAnalysisReport

from .compression_history import CompressionHistoryError, CompressionHistoryStore


logger = logging.getLogger(__name__)


class CompressionExecutionProvider(Protocol):
    def capabilities(self) -> CompressionToolCapabilities: ...

    def create_plan(
        self,
        game: Game,
        report: BtrfsAnalysisReport,
        profile: CompressionProfile,
        **kwargs: Any,
    ) -> CompressionPlan: ...

    def execute_plan(
        self,
        game: Game,
        plan: CompressionPlan,
        **kwargs: Any,
    ) -> CompressionResult: ...

    def cancel_all(self) -> None: ...

    def measure_current(self, game: Game) -> CompressionMeasurement: ...


class CompressionService:
    """Own plans, enforce one writer per game and durably record every outcome."""

    def __init__(
        self,
        provider: CompressionExecutionProvider,
        history: CompressionHistoryStore,
        *,
        fingerprint_loader: Callable[
            [Game], Mapping[str, Mapping[str, Any]] | None
        ]
        | None = None,
        verified_callback: Callable[[Game, CompressionResult], None] | None = None,
    ) -> None:
        self._provider = provider
        self._history = history
        self._fingerprint_loader = fingerprint_loader
        self._verified_callback = verified_callback
        self._plans: dict[str, CompressionPlan] = {}
        self._active_games: set[str] = set()
        self._lock = RLock()
        self._closed = False
        self._last_error = ""

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def capabilities(self) -> CompressionToolCapabilities:
        return self._provider.capabilities()

    def prepare(
        self,
        game: Game,
        report: BtrfsAnalysisReport,
        profile: CompressionProfile,
        *,
        changed_only: bool = False,
        after_update: bool = False,
        confirmation_required: bool = True,
        minimum_free_bytes: int = 0,
    ) -> CompressionPlan:
        with self._lock:
            self._ensure_open()
            active = game.id in self._active_games
        previous = None
        if changed_only and self._fingerprint_loader is not None:
            previous = self._fingerprint_loader(game)
        plan = self._provider.create_plan(
            game,
            report,
            profile,
            previous_fingerprint=previous,
            after_update=after_update,
            confirmation_required=confirmation_required,
            minimum_free_bytes=minimum_free_bytes,
        )
        if active:
            blockers = tuple(
                dict.fromkeys(
                    (*plan.blockers, "Another write task is active for this game")
                )
            )
            plan = replace(plan, eligible=False, blockers=blockers)
        with self._lock:
            self._plans[plan.id] = plan
        return plan

    def get_plan(self, plan_id: str) -> CompressionPlan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def execute(
        self,
        plan_id: str,
        game: Game,
        *,
        confirmed: bool,
        automatic_authorized: bool = False,
        cancel_event: Event | None = None,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> CompressionResult:
        with self._lock:
            self._ensure_open()
            try:
                plan = self._plans[plan_id]
            except KeyError as error:
                raise CompressionPlanRejected("Unknown compression plan") from error
            if plan.game_id != game.id:
                raise CompressionPlanRejected("The plan belongs to another game")
            if game.id in self._active_games:
                raise CompressionPlanRejected(
                    "Another write task is active for this game"
                )
            self._active_games.add(game.id)
            self._last_error = ""

        history_started = False
        history_finished = False
        final_result: CompressionResult | None = None
        try:
            # A durable marker is a precondition: without it a power loss would
            # leave an operation that cannot be surfaced on next launch.
            self._history.begin_operation(
                plan,
                automatic=bool(automatic_authorized),
            )
            history_started = True
            result = self._provider.execute_plan(
                game,
                plan,
                confirmed=confirmed,
                automatic_authorized=automatic_authorized,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
            final_result = result
            try:
                self._history.finish_operation(
                    game.name,
                    plan.game_path,
                    result,
                )
                history_finished = True
            except CompressionHistoryError as error:
                logger.error(
                    "Compression completed but its history could not be saved: %s",
                    error,
                )
                result = replace(
                    result,
                    status=(
                        "verification_required"
                        if result.status in {"completed", "completed_with_warning"}
                        else result.status
                    ),
                    verification_state="verification_required",
                    warnings=tuple(
                        dict.fromkeys(
                            (*result.warnings, "Compression history could not be saved")
                        )
                    ),
                    error=result.error or str(error),
                )
                final_result = result
            if (
                result.status in {"completed", "completed_with_warning"}
                and result.verification_state == "verified"
                and self._verified_callback is not None
            ):
                try:
                    self._verified_callback(game, result)
                except Exception:
                    logger.exception(
                        "Could not update the verified compression fingerprint"
                    )
            with self._lock:
                if result.error:
                    self._last_error = result.error
            return result
        except Exception as error:
            message = str(error) or type(error).__name__
            with self._lock:
                self._last_error = message
            if history_started:
                logger.error(
                    "Compression task %s stopped before a provider result was recorded",
                    plan.id,
                    exc_info=True,
                )
                cancelled = isinstance(error, CompressionCancelled) or bool(
                    cancel_event is not None and cancel_event.is_set()
                )
                now = datetime.now(UTC)
                result = CompressionResult(
                    plan_id=plan.id,
                    game_id=game.id,
                    profile=plan.profile,
                    status="cancelled" if cancelled else "failed",
                    started_at=now,
                    completed_at=now,
                    processed_files=0,
                    processed_bytes=0,
                    before=plan.before,
                    after=None,
                    actual_saved_bytes=None,
                    verification_state="verification_required",
                    full_compression=plan.full_compression,
                    after_update=plan.after_update,
                    build_id=plan.build_id,
                    warnings=(
                        "Compression stopped before final verification completed",
                    ),
                    error=message,
                )
                final_result = result
                try:
                    self._history.finish_operation(
                        game.name,
                        plan.game_path,
                        result,
                    )
                    history_finished = True
                except CompressionHistoryError as history_error:
                    logger.error(
                        "Compression failure history could not be saved: %s",
                        history_error,
                    )
                    result = replace(
                        result,
                        warnings=tuple(
                            dict.fromkeys(
                                (
                                    *result.warnings,
                                    "Compression history could not be saved",
                                )
                            )
                        ),
                        error=f"{message}; {history_error}",
                    )
                    final_result = result
                return result
            raise
        finally:
            # The pending crash marker must never be left behind merely
            # because a post-measurement callback or normal-path history write
            # raised. This is deliberately independent of task presentation.
            if history_started and not history_finished and final_result is not None:
                try:
                    self._history.finish_operation(
                        game.name,
                        plan.game_path,
                        final_result,
                    )
                except CompressionHistoryError as history_error:
                    logger.error(
                        "Final compression history write failed: %s",
                        history_error,
                    )
            with self._lock:
                self._active_games.discard(game.id)
                self._plans.pop(plan_id, None)

    def cancel_all(self) -> None:
        self._provider.cancel_all()

    def verify_measurement(self, game: Game) -> CompressionMeasurement:
        """Perform one authenticated, read-only current-state measurement."""

        with self._lock:
            self._ensure_open()
        return self._provider.measure_current(game)

    def recover_interrupted(self) -> tuple[dict[str, Any], ...]:
        return self._history.recover_interrupted()

    def history(self, game_id: str | None = None) -> tuple[dict[str, Any], ...]:
        return self._history.history(game_id)

    def active_game_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active_games))

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel_all()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("compression service has been shut down")


__all__ = ["CompressionExecutionProvider", "CompressionService"]
