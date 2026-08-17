from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication

from ..models import (
    Game,
    DetectedValue,
    DetectionEvidence,
    OptimizationAnalysis,
    OptimizationCandidate,
    PerformanceMeasurement,
    GameOptimizationProfile,
    Launcher,
    OptimizationOptions,
    OptimizationProfile,
)
from ..services import (
    OptiScalerError,
    ProtonTweaksError,
    SessionPerformanceData,
    uses_flatpak_steam,
)
from ..services.performance_analysis import compare_measurements
from ..services.performance_session import RUNNER_HEARTBEAT_STALE_SECONDS
from ..services.automatic_optimization import verify_runtime_activation

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class OptimizationController:
    def __init__(self, app: AppController) -> None:
        self._app = app
        self._analysis_cache_states: dict[str, str] = {}

    @staticmethod
    def _profile_key(game: Game | None) -> str:
        if game is None:
            return ""
        if game.steam_app_id:
            return str(game.steam_app_id)
        if game.launcher is Launcher.MANUAL and game.data_source.casefold() == "local":
            return game.id
        return ""

    def buildLaunchPreview(self, game_id: str, options: Mapping[str, Any]) -> str:
        """Build a display-only Steam launch preview."""

        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return "%command%"
        try:
            normalized_options = self._app._optimization_options(options)
            preview = self._app._provider_launch_preview(game, normalized_options)
        except Exception as error:
            self._app._report_error(f"building launch preview for {game.name}", error)
            return "%command%"

        logger.debug("Generated display-only launch preview for %s", game.id)
        return preview

    def playUiSound(self, kind: str) -> bool:
        if self._app._interface_mode == "couch":
            return False
        return self._app._ui_sound_service.play(kind)

    def optimizationDefaults(self, profile: str) -> dict[str, Any]:
        """Return provider-owned defaults for an optimization profile."""

        try:
            normalized_profile = self._app._coerce_enum(OptimizationProfile, profile)
            options = self._app._optimization_provider.defaults_for(normalized_profile)
        except Exception as error:
            self._app._report_error(f"loading optimization profile {profile}", error)
            return {}

        return {
            "gamemode": options.gamemode,
            "gamescope": options.gamescope,
            "mangohud": options.mangohud,
            "fpsLimit": options.fps_limit,
            "adaptiveSync": options.adaptive_sync,
            "cursorGrab": options.cursor_grab,
            "cpuPerformanceProfile": options.cpu_performance_profile,
            "memoryMonitoring": options.memory_monitoring,
            "optiscaler": options.optiscaler,
        }

    def _proton_tweaks_to_qml(self, app_id: str) -> dict[str, Any]:
        profile = self._app._proton_tweaks_repository.load(app_id)
        gpu_vendor = str(self._app._system_info.get("gpuVendor", ""))
        return self._app._proton_tweaks_repository.to_qml(
            profile, gpu_vendor=gpu_vendor
        )

    def getProtonTweaks(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Proton Tweaks require a supported game"}
        try:
            return self._app._proton_tweaks_to_qml(app_id)
        except (OSError, ValueError, ProtonTweaksError) as error:
            return {"success": False, "error": str(error)}

    def saveProtonTweaks(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "error": "Proton Tweaks require a supported game"}
        try:
            current = self._app._proton_tweaks_repository.load(app_id)
            updated = self._app._proton_tweaks_repository.from_payload(app_id, values)
            if (
                updated.optiscaler_fsr4_update
                != current.optiscaler_fsr4_update
            ):
                optiscaler = self._app._optiscaler_service.profile_repository.load(app_id)
                if optiscaler.enabled and optiscaler.installation_state == "installed":
                    self._app._optiscaler_service.configure_fsr4_update(
                        game, updated.optiscaler_fsr4_update
                    )
            self._app._proton_tweaks_repository.save(updated)
            self._app.protonTweaksChanged.emit(app_id)
            return self._app._proton_tweaks_to_qml(app_id)
        except Exception as error:
            logger.warning("Could not save Proton Tweaks for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def getOptimizationProfile(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            previous_profile = self._app._optimization_profile_repository.load(app_id)
            profile = previous_profile
            result = self._app._optimization_profile_to_qml(profile)
            result["gameAnalysis"] = self.getGameOptimizationAnalysis(game_id)
            return result
        except Exception as error:
            logger.warning("Could not load optimization profile for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def previewOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            profile = self._app._optimization_profile_from_payload(app_id, values)
            return self._app._optimization_profile_to_qml(profile)
        except Exception as error:
            return {"success": False, "error": str(error)}

    def saveOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            previous_profile = self._app._optimization_profile_repository.load(
                app_id
            )
            profile = self._app._optimization_profile_from_payload(app_id, values)
            display = self._app._optimization_display_for(profile.target_display_id)
            recommendation = self._app._optimization_advisor.recommend(
                profile,
                display,
                self._saved_session_performance(app_id),
                system_info=self._app._system_info,
            )
            profile = replace(
                profile,
                target_fps=(
                    recommendation.target_fps
                    if profile.target_fps_mode == "automatic"
                    else profile.target_fps
                ),
                last_recommendation=recommendation.to_dict(),
                updated_at=datetime.now(UTC),
            )
            gamemode, gamescope = self._app._runtime_tool_detector.detect()
            if profile.gamemode_enabled and not gamemode.available:
                raise ValueError(gamemode.message)
            if profile.gamescope_enabled and not gamescope.available:
                raise ValueError(gamescope.message)
            result = self._app._optimization_profile_to_qml(profile)
            path = self._app._optimization_profile_repository.save(profile)
            try:
                if self._app._gamescope_owns_fps_limit(profile):
                    self._app._clear_mangohud_fps_limit(game)
            except Exception:
                self._app._optimization_profile_repository.save(previous_profile)
                raise
            result.update({"success": True, "profilePath": str(path)})
            self._app._emit_toast("Optimization profile saved", "success")
            return result
        except Exception as error:
            self._app._emit_toast(f"Could not save optimization profile: {error}", "error")
            return {"success": False, "error": str(error)}

    def testGameOptimizationRunner(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "message": "Optimization profiles require a supported game"}
        result = self._app._runner_integration.test(app_id)
        self._app._emit_toast(
            str(result.get("message", "Runner test completed")),
            "success" if result.get("success") else "warning",
        )
        return result

    def analyzeGameOptimization(
        self, game_id: str, log_path: str = ""
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "error": "Game analysis requires an available game"}
        if game_id in self._app._optimization_jobs:
            return {"success": True, "status": "running", "gameId": game_id}
        try:
            profile = self._app._optimization_profile_repository.load(app_id)
            display = self._app._optimization_display_for(profile.target_display_id)
            gamemode, gamescope = self._app._runtime_tool_detector.detect()
            selected_log = self._select_mangohud_log(game, app_id, log_path)
            future = self._app._optimization_executor.submit(
                self._analyze_game,
                game,
                profile,
                display,
                dict(self._app._system_info),
                selected_log,
                gamemode.available,
                gamescope.available,
            )
            self._app._optimization_jobs[game_id] = future
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}
        return {
            "success": True,
            "status": "running",
            "gameId": game_id,
            "baselineLog": str(selected_log or ""),
        }

    def recordOptimizationBaseline(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        logger.info(
            "Optimization measurement requested: kind=baseline gameId=%s appId=%s",
            game_id,
            app_id or "unresolved",
        )
        active_change = self._active_optimization_change(game)
        if active_change is not None:
            if active_change.get("kind") == "runtime_profile":
                return self._measurement_rejection(
                    game_id,
                    app_id,
                    "baseline",
                    "pending_automatic_comparison",
                    "Record a comparison or revert the pending Automatic Optimization test first",
                )
            return self._measurement_rejection(
                game_id,
                app_id,
                "baseline",
                "pending_settings_comparison",
                "Record a comparison or revert the pending setting test first",
            )
        return self._record_optimization_measurement(game_id, kind="baseline")

    @staticmethod
    def _measurement_rejection(
        game_id: str,
        app_id: str,
        kind: str,
        guard: str,
        reason: str,
        **details: Any,
    ) -> dict[str, Any]:
        logger.warning(
            "Optimization measurement request rejected: kind=%s gameId=%s "
            "appId=%s guard=%s reason=%s",
            kind,
            game_id,
            app_id or "unresolved",
            guard,
            reason,
        )
        return {
            "success": False,
            "code": guard,
            "error": reason,
            **details,
        }

    def recordOptimizationComparison(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id or self._app._baseline_sessions.load_measurement(
            app_id, slot="before"
        ) is None:
            return {"success": False, "error": "Record a baseline before the comparison"}
        if game is None or self._active_optimization_change(game) is None:
            return {"success": False, "error": "No optimization change is pending comparison"}
        result = self._record_optimization_measurement(game_id, kind="comparison")
        if result.get("success"):
            self._mark_automatic_comparison_started(
                app_id,
                str(result.get("baselineSession", {}).get("id") or ""),
            )
        return result

    def _record_optimization_measurement(
        self, game_id: str, *, kind: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        session = None
        if game is None or not app_id or not game.library_available:
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "game_unavailable",
                "Baseline recording requires an available game",
            )
        current = self._app._baseline_sessions.load(app_id)
        if current is not None and current.status in {
            "waiting_for_steam",
            "waiting_for_runner",
            "recording",
            "waiting_for_game_exit",
            "processing",
        }:
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "active_measurement_session",
                "A baseline recording is already active for this game",
            )
        runner_status = self._app._runner_integration.status()
        if not runner_status.installed:
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "runner_unavailable",
                "Install Game Optimization Runner first",
            )
        try:
            steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
            availability = self._app._mangohud_detector.detect(steam_type)
            if not availability.available:
                return self._measurement_rejection(
                    game_id,
                    app_id,
                    kind,
                    "mangohud_unavailable",
                    availability.message,
                )
            if game.launcher is not Launcher.MANUAL or game.data_source.casefold() != "local":
                preflight_method = getattr(
                    self._app._runner_integration,
                    "steam_launch_option_status",
                    None,
                )
                preflight = preflight_method(game) if callable(preflight_method) else None
                if preflight is not None and preflight.configured is False:
                    return self._measurement_rejection(
                        game_id,
                        app_id,
                        kind,
                        "runner_not_configured",
                        preflight.message,
                        runnerPreflight=preflight.to_dict(),
                    )
            session = self._app._baseline_sessions.create(
                app_id,
                game.id,
                kind=kind,
                expected_runner_path=str(runner_status.path),
                expected_runner_hash=runner_status.sha256,
            )
            if kind == "baseline":
                self._app._optimization_comparisons.pop(game.id, None)
            if game.launcher is Launcher.MANUAL and game.data_source.casefold() == "local":
                self._app._runner_integration.launch_local(game)
            else:
                launch_result = self._app._game_launcher.launch(game)
                self._app._baseline_sessions.mark_steam_launched(
                    app_id, session.id, str(launch_result)
                )
            session = self._app._baseline_sessions.mark_waiting_for_runner(
                app_id, session.id
            ) or session
        except Exception as error:
            if session is not None:
                try:
                    self._app._baseline_sessions.fail(
                        app_id, str(error), session.id
                    )
                except Exception:
                    pass
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "session_start_failed",
                str(error) or type(error).__name__,
            )
        self._app._active_baseline_games.add(game.id)
        self._app._baseline_statuses[game.id] = session.status
        self._app.optimizationAnalysisChanged.emit(game.id)
        self._app._emit_toast(
            "Comparison recording will start with the game"
            if kind == "comparison"
            else "Baseline recording will start with the game",
            "info",
        )
        logger.info(
            "Optimization measurement session started: kind=%s gameId=%s "
            "appId=%s sessionId=%s status=%s",
            kind,
            game_id,
            app_id,
            session.id,
            session.status,
        )
        return {
            "success": True,
            "status": session.status,
            "baselineSession": session.to_dict(),
        }

    def importOptimizationBaseline(
        self, game_id: str, log_path: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "error": "Select an available game"}
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Record a comparison or revert the pending setting test first",
            }
        text = str(log_path or "").strip()
        url = QUrl(text)
        if url.isValid() and url.isLocalFile():
            text = url.toLocalFile()
        try:
            session = self._app._baseline_sessions.import_log(
                app_id, game.id, Path(text)
            )
            log = self._app._baseline_sessions.newest_log(app_id)
            if log is None:
                raise ValueError("The imported MangoHud log is empty")
            self._app._mangohud_log_parser.parse(log)
            result = self.analyzeGameOptimization(game.id, str(log))
            if not result.get("success"):
                raise ValueError(str(result.get("error") or "Could not analyze the imported log"))
        except Exception as error:
            try:
                self._app._baseline_sessions.fail(app_id, str(error))
            except Exception:
                pass
            return {"success": False, "error": str(error) or type(error).__name__}
        self._app._active_baseline_games.add(game.id)
        self._app._baseline_statuses[game.id] = session.status
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {"success": True, "status": "running", "baselineSession": session.to_dict()}

    def _poll_baseline_sessions(self) -> None:
        for game_id in tuple(self._app._active_baseline_games):
            game = self._app._resolve_game(game_id, show_error=False)
            app_id = self._profile_key(game)
            session = self._app._baseline_sessions.load(app_id) if app_id else None
            if game is None or session is None:
                self._app._active_baseline_games.discard(game_id)
                continue
            previous = self._app._baseline_statuses.get(game_id, "")
            if session.status == "processing":
                log = self._app._baseline_sessions.newest_log(app_id)
                if log is not None:
                    result = (
                        {"success": True, "status": "running"}
                        if game_id in self._app._optimization_jobs
                        else self.analyzeGameOptimization(game_id, str(log))
                    )
                    if not result.get("success"):
                        session = self._app._baseline_sessions.fail(
                            app_id,
                            str(result.get("error") or "Baseline analysis failed"),
                            session.id,
                        ) or session
                elif session.finished_at and datetime.now(UTC) - session.finished_at > timedelta(seconds=5):
                    artifacts = self._app._baseline_sessions.artifact_diagnostics(
                        app_id
                    )
                    logger.error(
                        "MangoHud baseline output missing: session=%s appId=%s "
                        "config=%s configExists=%s outputDirectory=%s "
                        "outputDirectoryExists=%s files=%s runnerCompletion=%s",
                        session.id,
                        app_id,
                        artifacts["configPath"],
                        artifacts["configExists"],
                        artifacts["outputDirectory"],
                        artifacts["outputDirectoryExists"],
                        artifacts["files"],
                        session.runner_completed_at is not None,
                    )
                    session = self._app._baseline_sessions.fail(
                        app_id,
                        "MangoHud did not produce a baseline log in the private session directory",
                        session.id,
                    ) or session
            elif session.status == "recording":
                if self._app._baseline_sessions.newest_log(app_id) is not None:
                    session = self._app._baseline_sessions.mark_waiting_for_game_exit(
                        app_id, session.id
                    ) or session
                elif (
                    session.started_at
                    and datetime.now(UTC) - session.started_at > timedelta(hours=4, minutes=5)
                ):
                    session = self._app._baseline_sessions.fail(
                        app_id,
                        "Baseline recording exceeded the four-hour MangoHud limit",
                        session.id,
                    ) or session
            elif session.status == "waiting_for_game_exit":
                log = self._app._baseline_sessions.newest_log(app_id)
                heartbeat_stale = bool(
                    session.last_heartbeat_at is None
                    or datetime.now(UTC) - session.last_heartbeat_at
                    > timedelta(seconds=RUNNER_HEARTBEAT_STALE_SECONDS)
                )
                try:
                    log_stable = bool(
                        log is not None
                        and datetime.now(UTC).timestamp() - log.stat().st_mtime >= 15
                    )
                except OSError:
                    log_stable = False
                if log_stable and heartbeat_stale:
                    session = self._app._baseline_sessions.finish_from_stable_log(
                        app_id, session.id
                    ) or session
            elif (
                session.status in {"waiting_for_steam", "waiting_for_runner"}
                and datetime.now(UTC) - session.created_at
                > timedelta(seconds=session.handshake_timeout_seconds)
            ):
                runner_path = Path(session.expected_runner_path)
                if session.runner_invocation_count:
                    failure = (
                        "Game Optimization Runner was invoked but rejected the baseline "
                        f"session: {session.runner_rejection or 'reason not reported'}"
                    )
                else:
                    failure = (
                        "Steam did not invoke Game Optimization Runner "
                        f"within {session.handshake_timeout_seconds} seconds"
                    )
                logger.error(
                    "Baseline runner handshake timeout: session=%s appId=%s "
                    "createdAt=%s expectedRunner=%s runnerExists=%s runnerHash=%s "
                    "runnerInvocations=%s runnerRejection=%s steamLaunch=%s "
                    "timeoutSeconds=%s activeState=%s",
                    session.id,
                    app_id,
                    session.created_at.isoformat(),
                    session.expected_runner_path or "not recorded",
                    runner_path.is_file() if session.expected_runner_path else False,
                    session.expected_runner_hash or "not recorded",
                    session.runner_invocation_count,
                    session.runner_rejection or "none observed",
                    session.steam_launch_result or "not recorded",
                    session.handshake_timeout_seconds,
                    session.status,
                )
                session = self._app._baseline_sessions.fail(
                    app_id,
                    failure,
                    session.id,
                ) or session
            if session.status != previous:
                logger.info(
                    "Baseline lifecycle: session=%s appId=%s gameId=%s status=%s "
                    "runnerPid=%s spawnedPid=%s processGroup=%s handshakeAt=%s "
                    "runnerCompletion=%s logExists=%s observed=%s reason=%s",
                    session.id,
                    app_id,
                    game_id,
                    session.status,
                    session.runner_pid,
                    session.spawned_pid,
                    session.process_group,
                    session.handshake_at.isoformat() if session.handshake_at else "not received",
                    session.runner_completed_at is not None,
                    self._app._baseline_sessions.newest_log(app_id) is not None,
                    list(session.observed_processes),
                    session.lifecycle_reason or "not reported",
                )
                self._app._baseline_statuses[game_id] = session.status
                self._app.optimizationAnalysisChanged.emit(game_id)
            if session.status in {"completed", "recorded_unrepresentative", "failed"}:
                self._app._active_baseline_games.discard(game_id)
                if session.status == "failed":
                    self._app._emit_toast(session.error or "Baseline recording failed", "error")

    def _analyze_game(
        self,
        game: Game,
        profile: GameOptimizationProfile,
        display: Any,
        system_info: Mapping[str, Any],
        log_path: Path | None,
        gamemode_available: bool,
        gamescope_available: bool,
    ) -> OptimizationAnalysis:
        app_id = self._profile_key(game)
        selected_executable = self._selected_executable(game, app_id)
        fingerprint = self._app._game_analyzer.analyze(
            game,
            system_info=system_info,
            display=display,
            category=profile.game_category,
            manual_category_override=bool(
                profile.manual_overrides.get("category")
            ),
            selected_executable=selected_executable,
            runtime_hint=self._runtime_hint(app_id, selected_executable),
        )
        measurement = (
            self._app._mangohud_log_parser.parse(log_path)
            if log_path is not None
            else self._app._baseline_sessions.load_measurement(
                app_id, slot="before"
            )
        )
        bottleneck = self._app._bottleneck_analyzer.analyze(
            measurement,
            fingerprint.system,
            target_fps=profile.target_fps,
        )
        frame_rate = self._app._frame_rate_analyzer.analyze(
            measurement,
            fingerprint.system,
        )
        candidates = self._app._game_recommendation_engine.recommend(
            fingerprint,
            measurement,
            bottleneck,
            profile,
            gamemode_available=gamemode_available,
            gamescope_available=gamescope_available,
        )
        settings, settings_candidates = self._app._game_settings_advisor.analyze(
            game,
            fingerprint,
            measurement,
            bottleneck,
            frame_rate,
        )
        return OptimizationAnalysis(
            fingerprint,
            measurement,
            bottleneck,
            (*candidates, *settings_candidates),
            frame_rate,
            settings,
        )

    def _selected_executable(self, game: Game, app_id: str) -> str:
        resolver = self._app._mangohud_launch_integration.executable_resolver
        candidates: list[str] = [game.executable_path]
        try:
            candidates.append(
                self._app._optiscaler_service.profile_repository.load(app_id).executable
            )
        except Exception:
            pass
        try:
            candidates.append(
                self._app._mangohud_repository.load(app_id).executable_path
            )
        except (OSError, ValueError):
            pass
        for candidate in candidates:
            if candidate and resolver.validate_selected(game, candidate) is not None:
                return candidate
        resolution = resolver.resolve(game)
        return (
            resolution.selected.relative_path
            if resolution.reliable and resolution.selected is not None
            else ""
        )

    @staticmethod
    def _runtime_label(tool_name: str) -> str:
        folded = tool_name.casefold()
        if "ge-proton" in folded or "proton-ge" in folded:
            return tool_name
        if "cachy" in folded and "proton" in folded:
            return tool_name
        if "proton" in folded:
            return tool_name
        return ""

    def _runtime_hint(
        self, app_id: str, selected_executable: str
    ) -> DetectedValue | None:
        if not app_id or not selected_executable.casefold().endswith(".exe"):
            return None
        report_path = self._app._runner_report_path(app_id)
        try:
            if report_path.stat().st_size > 1024 * 1024:
                return None
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping) or str(payload.get("appId") or "") != app_id:
            return None
        command = payload.get("steamCommand")
        values = command if isinstance(command, list) else []
        for raw in values:
            path = Path(str(raw))
            if path.name.casefold() != "proton":
                continue
            label = self._runtime_label(path.parent.name)
            if label:
                evidence = DetectionEvidence(
                    "runner launch report",
                    f"Steam selected {path.parent.name}",
                    0.98,
                )
                return DetectedValue(
                    label, 0.98, "last verified Steam LaunchPlan", (evidence,)
                )
        return DetectedValue(
            "Windows game using Steam compatibility layer",
            0.85,
            "resolved PE executable",
        )

    def _poll_analysis_jobs(self) -> None:
        for game_id, future in tuple(self._app._optimization_jobs.items()):
            if not future.done():
                continue
            self._app._optimization_jobs.pop(game_id, None)
            try:
                analysis = future.result()
            except Exception as error:
                logger.exception("Game analysis failed for %s", game_id)
                self._app._optimization_analyses[game_id] = {
                    "success": False,
                    "status": "failed",
                    "error": str(error) or type(error).__name__,
                }
                self._app._emit_toast("Game analysis failed", "error")
            else:
                payload = analysis.to_dict()
                payload.update({"success": True, "status": "completed"})
                self._app._optimization_analyses[game_id] = analysis
                game = self._app._resolve_game(game_id, show_error=False)
                app_id = self._profile_key(game)
                session = self._app._baseline_sessions.load(app_id) if app_id else None
                toast_message = "Game analysis completed"
                toast_level = "success"
                if session is not None and session.status == "processing":
                    if analysis.measurement is not None and analysis.measurement.available:
                        if session.kind == "comparison":
                            before = self._app._baseline_sessions.load_measurement(
                                app_id, slot="before"
                            )
                            self._app._baseline_sessions.save_measurement(
                                app_id, analysis.measurement, slot="after"
                            )
                            if before is not None:
                                self._app._optimization_comparisons[game_id] = (
                                    compare_measurements(
                                        before,
                                        analysis.measurement,
                                        before_frame_rate=self._app._frame_rate_analyzer.analyze(
                                            before, analysis.fingerprint.system
                                        ),
                                        after_frame_rate=analysis.frame_rate,
                                    )
                                )
                            self._complete_automatic_experiment(
                                game_id, app_id, session.id, analysis
                            )
                        else:
                            self._app._baseline_sessions.save_measurement(
                                app_id, analysis.measurement, slot="before"
                            )
                            self._app._optimization_comparisons.pop(game_id, None)
                        self._app._baseline_sessions.complete(app_id, session.id)
                        toast_message = "Baseline recorded"
                    elif analysis.measurement is not None and analysis.measurement.samples > 0:
                        if session.kind == "comparison":
                            before = self._app._baseline_sessions.load_measurement(
                                app_id, slot="before"
                            )
                            self._app._baseline_sessions.save_measurement(
                                app_id, analysis.measurement, slot="after"
                            )
                            if before is not None:
                                self._app._optimization_comparisons[game_id] = (
                                    compare_measurements(before, analysis.measurement)
                                )
                            self._complete_automatic_experiment(
                                game_id, app_id, session.id, analysis
                            )
                        reason = (
                            "Baseline recorded, but the measurement was not representative "
                            "enough for automatic optimization. Repeat the test during "
                            "representative gameplay."
                        )
                        self._app._baseline_sessions.mark_unrepresentative(
                            app_id, session.id, reason
                        )
                        toast_message = reason
                        toast_level = "warning"
                    else:
                        self._app._baseline_sessions.fail(
                            app_id,
                            "The MangoHud log did not contain usable performance samples",
                            session.id,
                        )
                        toast_message = (
                            "Baseline recording failed: the MangoHud log contained "
                            "no performance samples"
                        )
                        toast_level = "error"
                if app_id and getattr(
                    self._app, "_optimization_analysis_persistence_enabled", False
                ):
                    try:
                        self._app._optimization_analysis_repository.save(
                            app_id, analysis
                        )
                        self._analysis_cache_states[game_id] = "current"
                    except (OSError, ValueError) as error:
                        logger.warning(
                            "Could not persist optimization analysis for %s: %s",
                            game_id,
                            error,
                        )
                self._app._emit_toast(toast_message, toast_level)
            self._app.optimizationAnalysisChanged.emit(game_id)

    def getGameOptimizationAnalysis(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        session = self._app._baseline_sessions.load(app_id) if app_id else None
        if session is not None and session.status in {
            "waiting_for_steam", "waiting_for_runner", "recording",
            "waiting_for_game_exit", "processing"
        }:
            self._app._active_baseline_games.add(game_id)
            self._app._baseline_statuses.setdefault(game_id, session.status)
        if (
            session is not None
            and session.status == "processing"
            and game_id not in self._app._optimization_jobs
            and not isinstance(
                self._app._optimization_analyses.get(game_id), OptimizationAnalysis
            )
        ):
            log = self._app._baseline_sessions.newest_log(app_id)
            if log is not None:
                self.analyzeGameOptimization(game_id, str(log))
        if game_id in self._app._optimization_jobs:
            return {
                "success": True,
                "status": "running",
                "gameId": game_id,
                "baselineSession": session.to_dict() if session else {},
            }
        value = self._app._optimization_analyses.get(game_id)
        if value is None and game is not None and app_id:
            value = self._restore_persisted_analysis(game, app_id)
        elif (
            isinstance(value, OptimizationAnalysis)
            and not value.baseline_stale
            and game is not None
            and app_id
            and self._app._optimization_analysis_persistence_enabled
            and self._active_optimization_change(game) is None
            and self._cached_settings_changed(value.settings)
        ):
            self._app._optimization_analyses.pop(game_id, None)
            value = self._restore_persisted_analysis(game, app_id)
        if isinstance(value, OptimizationAnalysis):
            result = value.to_dict()
            result.update({"success": True, "status": "completed"})
            result["analysisCacheState"] = self._analysis_cache_states.get(
                game_id, "current"
            )
            result["baselineSession"] = session.to_dict() if session else {}
            before = self._app._baseline_sessions.load_measurement(
                app_id, slot="before"
            ) if app_id else None
            after = self._app._baseline_sessions.load_measurement(
                app_id, slot="after"
            ) if app_id else None
            comparison = self._app._optimization_comparisons.get(game_id)
            if comparison is None and before is not None and after is not None:
                comparison = compare_measurements(
                    before,
                    after,
                    before_frame_rate=self._app._frame_rate_analyzer.analyze(
                        before, value.fingerprint.system
                    ),
                    after_frame_rate=self._app._frame_rate_analyzer.analyze(
                        after, value.fingerprint.system
                    ),
                )
                self._app._optimization_comparisons[game_id] = comparison
            result["beforeMeasurement"] = before.to_dict() if before else {}
            result["afterMeasurement"] = after.to_dict() if after else {}
            result["comparison"] = comparison.to_dict() if comparison else {}
            active = self._active_optimization_change(game)
            result["appliedChange"] = dict(active or {})
            result["automaticOptimization"] = self._automatic_optimization_state(
                game, app_id, value
            )
            return result
        if isinstance(value, Mapping):
            result = dict(value)
            result["baselineSession"] = session.to_dict() if session else {}
            return result
        return {
            "success": True,
            "status": "not_analyzed",
            "gameId": game_id,
            "baselineSession": session.to_dict() if session else {},
        }

    def _automatic_optimization_state(
        self,
        game: Game,
        app_id: str,
        analysis: OptimizationAnalysis,
    ) -> dict[str, Any]:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        registry = getattr(self._app, "_runtime_candidate_registry", None)
        if repository is None or registry is None:
            return {}
        profile = self._app._optimization_profile_repository.load(app_id)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        document = repository.load(app_id)
        plan = registry.plan(
            analysis,
            profile,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            history=tuple(document.get("history", ())),
        ).to_dict()
        plan["session"] = dict(document.get("session") or {})
        plan["history"] = list(document.get("history") or ())
        plan["hasPendingChange"] = bool(self._active_optimization_change(game))
        return plan

    def _mark_automatic_comparison_started(
        self, app_id: str, measurement_session_id: str
    ) -> None:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        if repository is None or not measurement_session_id:
            return
        document = repository.load(app_id)
        session = document.get("session")
        if not isinstance(session, Mapping):
            return
        state = str(session.get("state") or "")
        result = session.get("result")
        retry_inconclusive = bool(
            state == "result_ready"
            and isinstance(result, Mapping)
            and str(result.get("outcome") or "") == "insufficient_data"
        )
        if state not in {"candidate_applied", "comparison_recording"} and not retry_inconclusive:
            return
        repository.mark_comparison_recording(
            app_id,
            str(session.get("id") or ""),
            measurement_session_id,
        )

    def _complete_automatic_experiment(
        self,
        game_id: str,
        app_id: str,
        measurement_session_id: str,
        analysis: OptimizationAnalysis,
    ) -> None:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        evaluator = getattr(self._app, "_automatic_optimization_evaluator", None)
        if repository is None or evaluator is None:
            return
        document = repository.load(app_id)
        session = document.get("session")
        if not isinstance(session, Mapping):
            return
        if (
            str(session.get("state") or "") != "comparison_recording"
            or str(session.get("comparisonSessionId") or "")
            != measurement_session_id
        ):
            return
        before_payload = session.get("originalBaseline")
        candidate = session.get("candidate")
        problem_payload = session.get("problem")
        if not all(
            isinstance(item, Mapping)
            for item in (before_payload, candidate, problem_payload)
        ):
            repository.fail(
                app_id,
                str(session.get("id") or ""),
                "The Automatic Optimization session is incomplete",
            )
            return
        before = PerformanceMeasurement.from_dict(before_payload)
        after = analysis.measurement
        if after is None:
            return
        try:
            report = json.loads(
                self._app._runner_report_path(app_id).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            report = {}
        verified, activation_reason = verify_runtime_activation(
            str(candidate.get("id") or ""),
            report if isinstance(report, Mapping) else {},
            measurement_session_id,
        )
        from ..services.automatic_optimization import OptimizationProblem

        problem = OptimizationProblem(
            str(problem_payload.get("kind") or "insufficient_data"),
            float(problem_payload.get("confidence") or 0.0),
            str(problem_payload.get("target") or ""),
            tuple(str(item) for item in problem_payload.get("evidence", ())),
            tuple(str(item) for item in problem_payload.get("limitations", ())),
        )
        result = evaluator.evaluate(
            problem,
            before,
            after,
            activation_verified=verified,
            activation_reason=activation_reason,
            before_frame_rate=self._app._frame_rate_analyzer.analyze(
                before, analysis.fingerprint.system
            ),
            after_frame_rate=analysis.frame_rate,
        )
        repository.mark_result(
            app_id,
            str(session.get("id") or ""),
            result,
            after,
        )

    def _finish_automatic_session_for_change(
        self, app_id: str, change_id: str, action: str
    ) -> None:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        if repository is None or not app_id:
            return
        document = repository.load(app_id)
        session = document.get("session")
        if not isinstance(session, Mapping):
            return
        if str(session.get("candidateChangeId") or "") != change_id:
            return
        repository.finish(
            app_id,
            str(session.get("id") or ""),
            action=action,
        )

    def _restore_persisted_analysis(
        self, game: Game, app_id: str
    ) -> OptimizationAnalysis | None:
        if not self._app._optimization_analysis_persistence_enabled:
            return None
        cached = self._app._optimization_analysis_repository.load(app_id)
        if cached is None or cached.fingerprint.game_id != game.id:
            return None
        reasons = list(cached.stale_reasons)
        root_valid = True
        try:
            current_root = game.install_path.resolve(strict=True)
            cached_root = Path(cached.fingerprint.game_root).resolve(strict=True)
            if current_root != cached_root:
                reasons.append("The game installation path changed after analysis")
                root_valid = False
        except OSError:
            reasons.append("The analyzed game path is no longer available")
            root_valid = False
        executable = Path(cached.fingerprint.main_executable)
        if cached.fingerprint.main_executable and not executable.is_file():
            reasons.append("The analyzed executable is no longer available")

        active_change = self._active_optimization_change(game)
        settings_changed = (
            active_change is None and self._cached_settings_changed(cached.settings)
        )
        restored = cached
        if settings_changed and root_valid:
            try:
                refreshed_settings, _unused = self._app._game_settings_advisor.analyze(
                    game,
                    cached.fingerprint,
                    None,
                    cached.bottleneck,
                    cached.frame_rate,
                )
                refreshed_settings = replace(
                    refreshed_settings,
                    message=(
                        "The graphics configuration changed after the saved baseline. "
                        "Settings were refreshed; record a new baseline before using "
                        "measured Automatic recommendations."
                    ),
                    recommendation_state="baseline_stale",
                )
                restored = replace(cached, settings=refreshed_settings, candidates=())
            except (OSError, ValueError) as error:
                logger.warning(
                    "Could not refresh stale settings analysis for %s: %s",
                    game.id,
                    error,
                )
            reasons.append("Graphics settings changed after the saved baseline")
        if reasons:
            restored = replace(
                restored,
                candidates=(),
                baseline_stale=True,
                stale_reasons=tuple(dict.fromkeys(reasons)),
            )
            cache_state = "stale"
        else:
            cache_state = "cached"
        self._app._optimization_analyses[game.id] = restored
        self._analysis_cache_states[game.id] = cache_state
        if restored != cached:
            try:
                self._app._optimization_analysis_repository.save(app_id, restored)
            except (OSError, ValueError) as error:
                logger.warning(
                    "Could not update persisted optimization analysis for %s: %s",
                    game.id,
                    error,
                )
        return restored

    @staticmethod
    def _cached_settings_changed(settings: Any) -> bool:
        expected: dict[str, str] = {}
        for item in settings.detected:
            if item.file and item.config_sha256:
                expected[item.file] = item.config_sha256
        for raw_path, digest in expected.items():
            path = Path(raw_path)
            try:
                if path.is_symlink() or not path.is_file():
                    return True
                current = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return True
            if current != digest:
                return True
        return False

    def startAutomaticOptimization(
        self, game_id: str, candidate_id: str = ""
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        analysis = self._app._optimization_analyses.get(game_id)
        if game is None or not app_id or not isinstance(analysis, OptimizationAnalysis):
            return {
                "success": False,
                "error": "Analyze the game and record a representative baseline first",
            }
        if analysis.baseline_stale or analysis.measurement is None or not analysis.measurement.available:
            return {
                "success": False,
                "error": "Automatic Optimization requires a current representative baseline",
            }
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Finish the current comparison cycle before starting Automatic Optimization",
            }
        profile = self._app._optimization_profile_repository.load(app_id)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        document = self._app._automatic_optimization_repository.load(app_id)
        plan = self._app._runtime_candidate_registry.plan(
            analysis,
            profile,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            history=tuple(document.get("history", ())),
        )
        selected = next(
            (
                item for item in plan.available
                if not candidate_id or str(item.get("id") or "") == candidate_id
            ),
            None,
        )
        if selected is None:
            return {"success": False, "error": plan.message}
        before = self._app._baseline_sessions.load_measurement(app_id, slot="before")
        if before is None or not before.available:
            return {
                "success": False,
                "error": "The original representative baseline is unavailable",
            }
        if before.to_dict() != analysis.measurement.to_dict():
            return {
                "success": False,
                "error": "The saved baseline changed after analysis; analyze the game again",
            }
        session: Mapping[str, Any] | None = None
        try:
            session = self._app._automatic_optimization_repository.create(
                app_id,
                game.id,
                plan,
                before,
                selected,
            )
            definition = self._app._runtime_candidate_registry.get(
                str(selected.get("id") or "")
            )
            candidate = definition.as_profile_candidate(profile, plan.problem)
            applied = self._apply_runtime_profile_candidate(
                game, app_id, candidate
            )
            if not applied.get("success"):
                raise RuntimeError(str(applied.get("error") or "Candidate apply failed"))
            change = applied.get("change") or {}
            session = self._app._automatic_optimization_repository.mark_applied(
                app_id,
                str(session.get("id") or ""),
                str(change.get("id") or ""),
            )
        except Exception as error:
            if session is not None:
                try:
                    self._app._automatic_optimization_repository.fail(
                        app_id,
                        str(session.get("id") or ""),
                        str(error) or type(error).__name__,
                    )
                except Exception:
                    pass
            return {"success": False, "error": str(error) or type(error).__name__}
        self._app.optimizationAnalysisChanged.emit(game_id)
        return {"success": True, "automaticSession": dict(session)}

    def _apply_runtime_profile_candidate(
        self,
        game: Game,
        app_id: str,
        candidate: OptimizationCandidate,
    ) -> dict[str, Any]:
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Finish the current comparison cycle before applying another change",
            }
        previous_profile = self._app._optimization_profile_repository.load(app_id)
        profile = previous_profile
        if candidate.id == "gamemode_runtime":
            profile = replace(profile, gamemode_enabled=True)
        elif candidate.id == "gamemode_cpu_schedule":
            profile = replace(profile, preset="custom", gamemode_enabled=True)
        elif candidate.id == "gamescope_gpu_scaling":
            width, height = candidate.proposed_value.split("x", 1)
            profile = replace(
                profile,
                preset="custom",
                gamescope_enabled=True,
                gamescope_mode="performance",
                gamescope_input_width=int(width),
                gamescope_input_height=int(height),
            )
        elif candidate.id == "quiet_fps_target":
            profile = replace(
                profile,
                preset="custom",
                target_fps_mode="manual",
                target_fps=int(candidate.proposed_value.split()[0]),
            )
        else:
            return {"success": False, "error": "Unsupported runtime recommendation"}
        path = self._app._optimization_profile_repository.save(profile)
        try:
            manifest = self._app._optimization_change_service.record_runtime_change(
                game, candidate, previous_profile, profile
            )
        except Exception:
            self._app._optimization_profile_repository.save(previous_profile)
            raise
        self._app._optimization_applied_changes[game.id] = dict(manifest)
        self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        self._app._optimization_comparisons.pop(game.id, None)
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {
            "success": True,
            "profilePath": str(path),
            "change": manifest,
        }

    def applyOptimizationCandidate(
        self, game_id: str, candidate_id: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        analysis = self._app._optimization_analyses.get(game_id)
        if game is None or not app_id or not isinstance(analysis, OptimizationAnalysis):
            return {"success": False, "error": "Analyze the game before applying a recommendation"}
        active = self._active_optimization_change(game)
        if active is not None:
            return {
                "success": False,
                "error": (
                    "Finish the current comparison cycle by keeping or reverting "
                    "the existing change"
                ),
            }
        candidate = next(
            (item for item in analysis.candidates if item.id == candidate_id),
            None,
        )
        if candidate is None:
            return {"success": False, "error": "Optimization candidate is no longer available"}
        try:
            if candidate.files_to_modify:
                return self._apply_file_candidate(game, app_id, candidate)
            return self._apply_runtime_profile_candidate(game, app_id, candidate)
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}

    def previewGameSettingChange(
        self, game_id: str, instance_id: str, proposed_value: str
    ) -> dict[str, Any]:
        analysis = self._app._optimization_analyses.get(game_id)
        if not isinstance(analysis, OptimizationAnalysis):
            return {"success": False, "error": "Analyze the game before previewing a setting test"}
        try:
            candidate = self._app._game_settings_advisor.manual_candidate(
                analysis.settings, instance_id, proposed_value
            )
            setting = next(
                item
                for item in analysis.settings.detected
                if item.instance_id == instance_id
            )
        except (ValueError, StopIteration) as error:
            return {"success": False, "error": str(error) or "Setting is unavailable"}
        result = candidate.to_dict()
        result.update({
            "success": True,
            "settingInstanceId": setting.instance_id,
            "automaticRecommended": setting.automatically_recommended,
            "automaticReason": setting.automatic_reason,
            "manualTestAvailable": True,
            "backupAvailable": True,
            "aggressive": proposed_value != setting.suggested_value,
        })
        return result

    def applyGameSettingChange(
        self, game_id: str, instance_id: str, proposed_value: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        analysis = self._app._optimization_analyses.get(game_id)
        if game is None or not app_id or not isinstance(analysis, OptimizationAnalysis):
            return {"success": False, "error": "Analyze the game before applying a setting test"}
        try:
            candidate = self._app._game_settings_advisor.manual_candidate(
                analysis.settings, instance_id, proposed_value
            )
            return self._apply_file_candidate(game, app_id, candidate)
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}

    def _apply_file_candidate(
        self, game: Game, app_id: str, candidate: OptimizationCandidate
    ) -> dict[str, Any]:
        analysis = self._app._optimization_analyses.get(game.id)
        if isinstance(analysis, OptimizationAnalysis) and analysis.baseline_stale:
            return {
                "success": False,
                "error": "Record a new representative baseline after the graphics settings change",
            }
        before = self._app._baseline_sessions.load_measurement(app_id, slot="before")
        if before is None or not before.available:
            return {
                "success": False,
                "error": "Record a representative baseline before applying a setting test",
            }
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Finish the current comparison cycle before applying another change",
            }
        manifest = self._app._optimization_change_service.apply(game, candidate)
        self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        self._app._optimization_comparisons.pop(game.id, None)
        self._app._optimization_applied_changes[game.id] = dict(manifest)
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {"success": True, "change": manifest}

    def _active_optimization_change(self, game: Game | None) -> dict[str, Any] | None:
        if game is None:
            return None
        cached = self._app._optimization_applied_changes.get(game.id)
        if isinstance(cached, Mapping) and cached.get("state") == "applied":
            return dict(cached)
        active = self._app._optimization_change_service.active_change(game)
        if active is not None:
            self._app._optimization_applied_changes[game.id] = dict(active)
        return active

    def revertOptimizationChange(
        self, game_id: str, change_id: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Game is unavailable"}
        try:
            active = self._active_optimization_change(game) or {}
            if active.get("kind") == "runtime_profile":
                manifest = self._app._optimization_change_service.runtime_change(
                    game, change_id
                )
                previous = GameOptimizationProfile.from_dict(
                    manifest["before_profile"],
                    expected_app_id=self._profile_key(game),
                )
                current = self._app._optimization_profile_repository.load(
                    self._profile_key(game)
                )
                if current.to_dict() != manifest["after_profile"]:
                    raise RuntimeError(
                        "The optimization profile changed outside this recommendation"
                    )
                self._app._optimization_profile_repository.save(previous)
                try:
                    manifest = self._app._optimization_change_service.mark_runtime_reverted(
                        game, change_id
                    )
                except Exception:
                    self._app._optimization_profile_repository.save(current)
                    raise
            else:
                manifest = self._app._optimization_change_service.revert(
                    game, change_id
                )
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}
        self._finish_automatic_session_for_change(
            self._profile_key(game), change_id, "reverted"
        )
        self._app._optimization_applied_changes.pop(game_id, None)
        app_id = self._profile_key(game)
        if app_id:
            self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        self._app._optimization_comparisons.pop(game_id, None)
        self._app.optimizationAnalysisChanged.emit(game_id)
        self.analyzeGameOptimization(game_id)
        return {"success": True, "change": manifest}

    def keepOptimizationChange(
        self, game_id: str, change_id: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Game is unavailable"}
        active = self._active_optimization_change(game) or {}
        if active.get("id") != change_id or active.get("state") != "applied":
            return {"success": False, "error": "No matching active change exists"}
        app_id = self._profile_key(game)
        automatic_repository = getattr(
            self._app, "_automatic_optimization_repository", None
        )
        if automatic_repository is not None and app_id:
            automatic_session = automatic_repository.load(app_id).get("session")
            if (
                isinstance(automatic_session, Mapping)
                and str(automatic_session.get("candidateChangeId") or "")
                == change_id
            ):
                if str(automatic_session.get("state") or "") != "result_ready":
                    return {
                        "success": False,
                        "error": (
                            "Record and evaluate a representative comparison "
                            "before keeping this optimization"
                        ),
                    }
                automatic_result = automatic_session.get("result")
                if (
                    not isinstance(automatic_result, Mapping)
                    or str(automatic_result.get("outcome") or "")
                    == "insufficient_data"
                ):
                    return {
                        "success": False,
                        "error": (
                            "The comparison is inconclusive; retry it or revert "
                            "the optimization"
                        ),
                    }
        try:
            before = self._app._baseline_sessions.load_measurement(
                app_id, slot="before"
            ) if app_id else None
            after = self._app._baseline_sessions.load_measurement(
                app_id, slot="after"
            ) if app_id else None
            comparison = self._app._optimization_comparisons.get(game_id)
            manifest = self._app._optimization_change_service.keep(
                game,
                change_id,
                comparison=comparison.to_dict() if comparison else None,
                before_measurement=before.to_dict() if before else None,
                after_measurement=after.to_dict() if after else None,
            )
            if app_id and after is not None and after.available:
                self._app._baseline_sessions.save_measurement(
                    app_id, after, slot="before"
                )
            if app_id:
                self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}
        self._finish_automatic_session_for_change(app_id, change_id, "kept")
        self._app._optimization_applied_changes.pop(game_id, None)
        self._app._optimization_comparisons.pop(game_id, None)
        self._app.optimizationAnalysisChanged.emit(game_id)
        self.analyzeGameOptimization(game_id)
        return {"success": True, "change": manifest}

    def _select_mangohud_log(
        self, game: Game, app_id: str, explicit: str
    ) -> Path | None:
        if explicit:
            path = Path(explicit).expanduser()
            return path.resolve(strict=True)
        try:
            profile = self._app._mangohud_repository.load(app_id)
        except (OSError, ValueError):
            return None
        if not profile.output_folder:
            return None
        folder = Path(profile.output_folder)
        if not folder.is_dir():
            return None
        tokens = {
            re.sub(r"[^a-z0-9]", "", game.name.casefold()),
            re.sub(r"[^a-z0-9]", "", app_id.casefold()),
            re.sub(r"[^a-z0-9]", "", Path(game.executable_path).stem.casefold()),
        }
        tokens.discard("")
        candidates: list[Path] = []
        for path in folder.glob("*.csv"):
            folded = re.sub(r"[^a-z0-9]", "", path.name.casefold())
            if any(token in folded for token in tokens):
                candidates.append(path)
        return max(candidates, key=lambda item: item.stat().st_mtime, default=None)

    def _optimization_options(self, options: Mapping[str, Any]) -> OptimizationOptions:
        def option(*names: str, default: Any = None) -> Any:
            for name in names:
                if name in options:
                    return options[name]
            return default

        fps_value = option("fpsLimit", "fps_limit")
        fps_limit = int(fps_value) if fps_value not in (None, "", 0, "0") else None
        return OptimizationOptions(
            profile=self._app._coerce_enum(
                OptimizationProfile, str(option("profile", default="Balanced"))
            ),
            gamemode=self._app._coerce_bool(
                option("gamemode", "gameMode", default=True)
            ),
            gamescope=self._app._coerce_bool(option("gamescope", default=False)),
            mangohud=self._app._coerce_bool(
                option("mangohud", "mangoHud", default=False)
            ),
            fps_limit=fps_limit,
            adaptive_sync=self._app._coerce_bool(
                option("adaptiveSync", "adaptive_sync", default=True)
            ),
            cursor_grab=self._app._coerce_bool(
                option("cursorGrab", "cursor_grab", default=False)
            ),
            cpu_performance_profile=self._app._coerce_bool(
                option("cpuPerformanceProfile", "cpu_performance_profile", default=False)
            ),
            memory_monitoring=self._app._coerce_bool(
                option("memoryMonitoring", "memory_monitoring", default=True)
            ),
            optiscaler=self._app._coerce_bool(
                option("optiscaler", "optiScaler", default=False)
            ),
        )

    def _optimization_displays(self) -> list[Any]:
        application = QGuiApplication.instance()
        if application is None:
            return []
        try:
            return list(self._app._display_detector.from_application(application))
        except Exception as error:
            logger.warning("Could not detect displays: %s", error)
            return []

    def _optimization_display_for(self, display_id: str) -> Any | None:
        displays = self._app._optimization_displays()
        return next(
            (display for display in displays if display.display_id == display_id),
            next(
                (display for display in displays if display.primary),
                displays[0] if displays else None,
            ),
        )

    def _optimization_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> GameOptimizationProfile:
        base = self._app._optimization_profile_repository.load(app_id)
        aliases = {
            "gameCategory": "game_category", "userGoal": "user_goal",
            "targetDisplayId": "target_display_id", "targetFpsMode": "target_fps_mode",
            "targetFps": "target_fps", "gamemodeEnabled": "gamemode_enabled",
            "gamescopeEnabled": "gamescope_enabled", "gamescopeMode": "gamescope_mode",
            "gamescopeInputWidth": "gamescope_input_width",
            "gamescopeInputHeight": "gamescope_input_height",
            "gamescopeOutputWidth": "gamescope_output_width",
            "gamescopeOutputHeight": "gamescope_output_height",
            "gamescopeRefreshRate": "gamescope_refresh_rate",
            "gamescopeFullscreen": "gamescope_fullscreen",
            "gamescopeScaler": "gamescope_scaler", "gamescopeFilter": "gamescope_filter",
            "manualOverrides": "manual_overrides", "lastRecommendation": "last_recommendation",
        }
        data = base.to_dict()
        runtime_keys = {
            "targetFpsMode", "target_fps_mode", "targetFps", "target_fps",
            "gamemodeEnabled", "gamemode_enabled", "gamescopeEnabled",
            "gamescope_enabled", "gamescopeMode", "gamescope_mode",
            "gamescopeInputWidth", "gamescope_input_width",
            "gamescopeInputHeight", "gamescope_input_height",
            "gamescopeOutputWidth", "gamescope_output_width",
            "gamescopeOutputHeight", "gamescope_output_height",
            "gamescopeRefreshRate", "gamescope_refresh_rate",
            "gamescopeFullscreen", "gamescope_fullscreen",
            "gamescopeScaler", "gamescope_scaler",
            "gamescopeFilter", "gamescope_filter",
        }
        if "preset" not in values and runtime_keys.intersection(values):
            data["preset"] = "custom"
        for key, value in values.items():
            normalized = aliases.get(str(key), str(key))
            if normalized in data and normalized not in {"schema_version", "app_id", "updated_at"}:
                data[normalized] = value
        data.update(
            {
                "schema_version": base.schema_version,
                "app_id": app_id,
                "updated_at": datetime.now(UTC),
            }
        )
        profile = GameOptimizationProfile.from_dict(data, expected_app_id=app_id)
        display = self._app._optimization_display_for(profile.target_display_id)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        return self._app._optimization_advisor.resolve_preset(
            profile,
            display,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            measurements=self._saved_session_performance(app_id),
            system_info=self._app._system_info,
        ).profile

    def _saved_session_performance(
        self, app_id: str
    ) -> SessionPerformanceData | None:
        if self._app._optimization_analysis_persistence_enabled:
            saved_analysis = self._app._optimization_analysis_repository.load(app_id)
            if saved_analysis is not None and saved_analysis.baseline_stale:
                return None
        measurement = self._app._baseline_sessions.load_measurement(
            app_id, slot="before"
        )
        if measurement is None or not measurement.available:
            return None
        return SessionPerformanceData(
            average_fps=measurement.average_fps,
            one_percent_low_fps=measurement.one_percent_low_fps,
            frametime_ms=measurement.average_frametime_ms,
            gpu_usage_percent=measurement.gpu_usage_percent,
            cpu_usage_percent=measurement.cpu_usage_percent,
            vram_used_mb=measurement.vram_used_mb,
        )

    def _optimization_profile_to_qml(
        self, profile: GameOptimizationProfile
    ) -> dict[str, Any]:
        displays = self._app._optimization_displays()
        display = self._app._optimization_display_for(profile.target_display_id)
        measurements = self._saved_session_performance(profile.app_id)
        recommendation = self._app._optimization_advisor.recommend(
            profile,
            display,
            measurements,
            system_info=self._app._system_info,
        )
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        preset_plan = self._app._optimization_advisor.resolve_preset(
            profile,
            display,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            measurements=measurements,
            system_info=self._app._system_info,
        )
        mangohud_activation_owner = "none"
        try:
            mangohud_profile = self._app._mangohud_repository.load(profile.app_id)
            mangohud_fps_limit = mangohud_profile.fps_limit
            if mangohud_profile.enabled:
                profile_game = next(
                    (
                        item
                        for item in self._app._domain_games.values()
                        if str(item.steam_app_id or item.id) == profile.app_id
                    ),
                    None,
                )
                if profile_game is not None:
                    mangohud_activation_owner = (
                        self._app._mangohud_launch_integration.status(
                            profile_game, mangohud_profile
                        ).strategy
                    )
                else:
                    mangohud_activation_owner = "steam_environment"
        except (OSError, ValueError):
            mangohud_fps_limit = None
        try:
            optiscaler_profile = self._app._optiscaler_service.profile_repository.load(
                profile.app_id
            )
            optiscaler_override = (
                optiscaler_profile.proton_override
                if optiscaler_profile.enabled
                and optiscaler_profile.installation_state == "installed"
                else ""
            )
        except (OSError, ValueError, OptiScalerError):
            optiscaler_override = ""
        proton_tweaks_error = ""
        try:
            proton_environment = self._app._proton_tweaks_repository.load(
                profile.app_id
            ).environment()
        except (OSError, ValueError, ProtonTweaksError) as error:
            proton_environment = {}
            proton_tweaks_error = str(error)
        plan = self._app._optimization_launch_planner.build(
            profile, ["%command%"], gamemode=gamemode, gamescope=gamescope,
            mangohud_fps_limit=mangohud_fps_limit,
            optiscaler_override=optiscaler_override,
            existing_wine_overrides=os.environ.get("WINEDLLOVERRIDES", ""),
            proton_environment=proton_environment,
            existing_environment=os.environ,
            mangohud_activation_owner=mangohud_activation_owner,
            allow_placeholder=True,
        )
        runner = self._app._runner_integration.status()
        data = profile.to_dict()
        data.update({
            "success": True, "schemaVersion": profile.schema_version,
            "appId": profile.app_id, "gameCategory": profile.game_category,
            "userGoal": profile.user_goal, "targetDisplayId": profile.target_display_id,
            "targetFpsMode": profile.target_fps_mode, "targetFps": profile.target_fps,
            "gamemodeEnabled": profile.gamemode_enabled,
            "gamescopeEnabled": profile.gamescope_enabled,
            "gamescopeMode": profile.gamescope_mode,
            "gamescopeInputWidth": profile.gamescope_input_width,
            "gamescopeInputHeight": profile.gamescope_input_height,
            "gamescopeOutputWidth": profile.gamescope_output_width,
            "gamescopeOutputHeight": profile.gamescope_output_height,
            "gamescopeRefreshRate": profile.gamescope_refresh_rate,
            "gamescopeFullscreen": profile.gamescope_fullscreen,
            "gamescopeScaler": profile.gamescope_scaler,
            "gamescopeFilter": profile.gamescope_filter,
            "manualOverrides": dict(profile.manual_overrides),
            "lastRecommendation": dict(profile.last_recommendation),
            "updatedAt": profile.updated_at.astimezone(UTC).isoformat(),
            "displays": [item.to_dict() for item in displays],
            "recommendation": recommendation.to_dict(),
            "presetPlan": preset_plan.to_dict(),
            "categoryClassification": {
                "value": profile.game_category,
                "source": (
                    "manual override"
                    if profile.manual_overrides.get("category")
                    else "saved profile"
                    if profile.game_category != "unknown"
                    else "not detected"
                ),
                "confidence": (
                    1.0
                    if profile.manual_overrides.get("category")
                    else 0.75
                    if profile.game_category != "unknown"
                    else 0.0
                ),
                "manualOverride": bool(profile.manual_overrides.get("category")),
            },
            "gamemode": gamemode.to_dict(), "gamescope": gamescope.to_dict(),
            "launchPlan": plan.to_dict(),
            "launchPlanText": shlex.join(plan.command),
            "fpsLimitOwner": plan.fps_limit_owner,
            "steamLaunchCommand": (
                self._app._runner_integration.steam_command(profile.app_id)
                if profile.app_id.isdecimal()
                else ""
            ),
            "localGame": not profile.app_id.isdecimal(),
            "runner": runner.to_dict(),
            "profilePath": str(self._app._optimization_profile_repository.path(profile.app_id)),
            "protonTweaksError": proton_tweaks_error,
            "renderingSummary": (
                f"The game will render at {profile.gamescope_input_width}×{profile.gamescope_input_height} "
                f"and display at {profile.gamescope_output_width}×{profile.gamescope_output_height}"
            ),
            "protonOverrides": [
                f"{key}={value}"
                for key, value in sorted(plan.environment.items())
            ],
        })
        return data

    def _provider_launch_preview(
        self, game: Game, options: OptimizationOptions
    ) -> str:
        for method_name in (
            "preview_command",
            "generate_command_preview",
            "build_launch_preview",
            "generate_launch_preview",
            "build_launch_command",
            "generate_launch_command",
        ):
            method = getattr(self._app._optimization_provider, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(game, options)
            except TypeError:
                result = method(options)
            if isinstance(result, str):
                return result
            for attribute in ("preview", "command", "command_preview"):
                value = getattr(result, attribute, None)
                if isinstance(value, str):
                    return value
        raise AttributeError("optimization provider cannot build a launch preview")
