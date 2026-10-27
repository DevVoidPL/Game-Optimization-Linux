"""Guided, measured optimization workflow for Desktop and Couch.

Status and preview are read-only. Only explicit user actions write: start,
record (which launches the game the user asked to launch), apply a
candidate with a restore point, confirm comparability, keep, revert, cancel.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Mapping

from ..models import GameOptimizationProfile, Launcher, OptimizationAnalysis
from ..services import measured_optimization as mo
from ..services.measured_optimization import fingerprint as fpmod
from ..services.measured_optimization import manual as manual_mod
from ..services.measured_optimization.metrics import SessionMetrics
from ..services.performance_session import ACTIVE_SESSION_STATUSES

logger = logging.getLogger(__name__)

TARGET_FILES = {"optimization": "optimization.json", "proton_tweaks": "proton-tweaks.json",
                "gpu_memory": "gpu-memory.json"}
FLOOR_GOALS = ("low_power", "best_quality")
_CACHE_SECONDS = 30.0


def _known(value: Any) -> bool:
    return value not in (None, "", fpmod.UNKNOWN)


class MeasuredOptimizationController:
    def __init__(self, app: Any, store: mo.MeasuredOptimizationStore | None = None) -> None:
        self._app = app
        self.store = store or mo.MeasuredOptimizationStore()
        self._cache: dict[str, tuple[float, str, Any]] = {}

    # Game and environment ---------------------------------------------------
    def _game(self, game_id: str) -> tuple[Any, str]:
        game = self._app._resolve_game(game_id, show_error=False)
        return game, manual_mod.optimization_key(game)

    @property
    def _games_root(self) -> Path:
        return Path(self._app._optimization_profile_repository.root)

    def _manual(self, game: Any) -> dict[str, Any]:
        if not manual_mod.is_manual(game):
            return {}
        return self._app._optimization_controller.manual_measurement_support(game)

    def _kind(self, game: Any) -> tuple[str, str]:
        if manual_mod.is_manual(game):
            support = self._manual(game)
            kind = str((support.get("runner") or {}).get("kind") or "manual")
            return mo.launcher_kind(game, manual=(kind if support.get("supported") else "manual", support.get("code", "")))
        from ..services.steam_launch import uses_flatpak_steam
        flatpak_steam = False
        local_wine = False
        if game.launcher is Launcher.STEAM:
            try:
                flatpak_steam = bool(uses_flatpak_steam(game))
            except Exception:  # noqa: BLE001 - unknown is not verified support
                flatpak_steam = True
        if game.launcher is Launcher.MANUAL and str(game.data_source).casefold() == "local":
            local_wine = str(game.executable_path or "").casefold().endswith(".exe")
        return mo.launcher_kind(game, flatpak_steam=flatpak_steam, in_flatpak=bool(os.environ.get("FLATPAK_ID")),
                                local_wine=local_wine)

    def _analysis(self, game: Any) -> OptimizationAnalysis | None:
        value = self._app._optimization_analyses.get(game.id)
        return value if isinstance(value, OptimizationAnalysis) else None

    DETECTION_REASONS = {
        "library_unavailable": "The game's library is disconnected or unavailable",
        "directory_missing": "The game folder is missing or not readable",
        "executable_missing": "The game executable was not found",
        "executable_ambiguous": "Several executables match; choose the main game executable on the OptiScaler page first",
        "analysis_failed": "Game detection failed: %1",
    }

    def detection(self, game: Any, key: str) -> dict[str, Any]:
        """Whether the fingerprint can be (or is being) detected. Read only."""
        if self._analysis(game) is not None:
            return {"state": "ready", "code": "", "reason": ""}
        if game.id in getattr(self._app, "_optimization_jobs", {}):
            return {"state": "detecting", "code": "", "reason": ""}
        failed = self._app._optimization_analyses.get(game.id)
        if isinstance(failed, Mapping) and failed.get("status") == "failed":
            return {"state": "failed", "code": "analysis_failed",
                    "reason": self.DETECTION_REASONS["analysis_failed"], "detail": str(failed.get("error") or "")}
        code, extra = self._detection_blocker(game, key)
        if code:
            return {"state": "unavailable", "code": code, "reason": self.DETECTION_REASONS[code], **extra}
        return {"state": "not_started", "code": "", "reason": ""}

    def _detection_blocker(self, game: Any, key: str) -> tuple[str, dict[str, Any]]:
        if not getattr(game, "library_available", True):
            return "library_unavailable", {}
        try:
            if not Path(game.install_path).is_dir():
                return "directory_missing", {}
        except (OSError, TypeError):
            return "directory_missing", {}
        if manual_mod.is_manual(game):
            return ("" if Path(str(game.executable_path or "")).is_file() else "executable_missing"), {}
        try:
            if self._app._optimization_controller._selected_executable(game, key):
                return "", {}
            resolution = self._app._mangohud_launch_integration.executable_resolver.resolve(game)
        except Exception:  # noqa: BLE001 - reported as missing
            return "executable_missing", {}
        if resolution.status == "ambiguous":
            return "executable_ambiguous", {"candidates": [item.relative_path for item in resolution.candidates]}
        return "executable_missing", {}

    def prepare_fingerprint(self, game_id: str) -> dict[str, Any]:
        """Start the read-only game analysis in the background when needed."""
        game, key = self._game(game_id)
        if game is None or not key:
            return {"success": False, "error": "This game has no per-game optimization profile"}
        state = self.detection(game, key)
        if state["state"] == "not_started":
            optimization = self._app._optimization_controller
            restore = getattr(optimization, "_restore_persisted_analysis", None)
            if callable(restore) and game.id not in self._app._optimization_analyses:
                try:
                    restore(game, key)
                except Exception as failure:  # noqa: BLE001 - a fresh analysis follows
                    logger.debug("No saved analysis for %s: %s", game.id, failure)
            if self._analysis(game) is None:
                started = optimization.analyzeGameOptimization(game.id)
                if started.get("success") is False:
                    return {"success": False, "error": str(started.get("error") or "")}
            state = self.detection(game, key)
        return {"success": True, "detection": state}

    def _runner(self, game: Any) -> tuple[Path | None, str, Mapping[str, str]]:
        cached = self._cache.get("runner:" + game.id)
        if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
            return cached[2]
        if manual_mod.is_manual(game):
            runner = self._manual(game).get("runner") or {}
            script = Path(runner["script"]) if runner.get("script") else None
            support: dict[str, str] = {}
            if script is not None:
                from ..models.proton_tweaks import PROTON_TWEAK_REGISTRY
                try:
                    text = script.read_text(errors="replace")[:2_000_000]
                except OSError:
                    text = ""
                support = {d.id: ("present" if text and d.environment_key in text else "unsupported" if text else "unknown")
                           for d in PROTON_TWEAK_REGISTRY}
            value = (script, "selected" if runner.get("kind") == "manual_proton" else "not_proton", support)
            self._cache["runner:" + game.id] = (time.monotonic(), "", value)
            return value
        from ..services import tweaks_capabilities
        try:
            capabilities = tweaks_capabilities.runner_capabilities(game)
            script = Path(capabilities["runnerPath"]) if capabilities.get("runnerPath") else None
            value = (script, str(capabilities.get("selection") or "unknown"), dict(capabilities.get("support") or {}))
        except Exception as error:  # noqa: BLE001 - reported as unknown runtime
            logger.debug("Runner capabilities unavailable for %s: %s", game.id, error)
            value = (None, "unknown", {})
        self._cache["runner:" + game.id] = (time.monotonic(), "", value)
        return value

    def _active_tools(self, key: str, profile: GameOptimizationProfile) -> dict[str, Any]:
        root = self._games_root
        result: dict[str, Any] = {
            "gamemode": bool(profile.gamemode_enabled),
            "gamescope": (profile.gamescope_mode if profile.gamescope_enabled and profile.gamescope_mode != "disabled" else "off"),
            "fpsLimit": (profile.target_fps if profile.target_fps_mode != "unlimited" else 0),
        }
        try:
            hud = self._app._mangohud_repository.load(key)
            result["mangohud"] = bool(hud.enabled)
            result["mangohudFpsLimit"] = hud.fps_limit or 0
        except (OSError, ValueError):
            result["mangohud"] = fpmod.UNKNOWN
        result["upscaler"] = "None"
        try:
            opti = self._app._optiscaler_service.profile_repository.load(key)
            if opti.enabled and opti.installation_state == "installed":
                result["upscaler"] = "OptiScaler"
        except Exception:  # noqa: BLE001
            result["upscaler"] = fpmod.UNKNOWN
        if result["upscaler"] == "None" and result["gamescope"] != "off" and profile.gamescope_filter in ("fsr", "nis"):
            result["upscaler"] = f"Gamescope {profile.gamescope_filter.upper()}"
        try:
            from ..services.frame_generation import load_profile as fg_profile, profile_path as fg_path
            result["frameGeneration"] = str(fg_profile(key, root)["backend"]) if fg_path(key, root).exists() else "off"
        except (OSError, ValueError):
            result["frameGeneration"] = fpmod.UNKNOWN
        try:
            post = self._app._optimization_controller._post_environment(key)
            result["postProcessing"] = "vkBasalt" if post.get("ENABLE_VKBASALT") == "1" else "off"
        except Exception:  # noqa: BLE001
            result["postProcessing"] = fpmod.UNKNOWN
        try:
            from ..services.gpu_memory import load_profile as gpu_profile
            gpu = gpu_profile(self._gpu_key(key), root)
            result["gpuMemory"] = {"priority": bool(gpu.get("priority")), "pressure": str(gpu.get("pressure"))}
        except (OSError, ValueError):
            result["gpuMemory"] = fpmod.UNKNOWN
        try:
            result["protonTweaks"] = dict(self._app._proton_tweaks_repository.load(key).states)
        except Exception:  # noqa: BLE001
            result["protonTweaks"] = fpmod.UNKNOWN
        return result

    def _gpu_key(self, key: str) -> str:
        game = next((item for item in self._app._domain_games.values() if manual_mod.optimization_key(item) == key), None)
        if game is not None and manual_mod.is_manual(game):
            from ..services.gpu_memory import game_key
            return game_key(game)
        return key

    def _user_launch(self, game: Any) -> tuple[frozenset[str], tuple[str, ...], dict[str, Any]]:
        """User-owned environment keys, programs and hashes (never modified)."""
        keys: set[str] = set()
        programs: list[str] = []
        files: dict[str, Any] = {}
        try:
            from ..services.custom_launch import CustomLaunchRepository, launch_key, profile_path
            custom_key = launch_key(game)
            custom = CustomLaunchRepository(self._games_root).load(custom_key)
            keys.update(custom.active_environment())
            programs.extend(Path(item).name for item in custom.active_wrapper()[:1])
            path = profile_path(custom_key, self._games_root)
            files["custom-launch.json"] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        except Exception:  # noqa: BLE001 - no custom launch identity
            pass
        support = self._manual(game)
        configuration = support.get("configuration")
        if configuration is not None:
            keys.update(name for name, _value in configuration.environment)
            programs.extend(Path(item).name for item in configuration.runner_command[:1])
            files["manual-launch"] = manual_mod.launch_profile_hash(configuration)
            from ..services.gpu_memory import game_key, profile_path as gpu_path
            gpu = gpu_path(game_key(game), self._games_root)
            files["gpu-memory.json"] = hashlib.sha256(gpu.read_bytes()).hexdigest() if gpu.is_file() else None
        return frozenset(keys), tuple(programs), files

    def fingerprint(self, game: Any, key: str) -> fpmod.RunFingerprint:
        analysis = self._analysis(game)
        profile = self._app._optimization_profile_repository.load(key)
        fp = analysis.fingerprint if analysis else None
        executable = Path(fp.main_executable) if fp and fp.main_executable else None
        imports = None
        if executable is not None:
            marker = f"{executable}:{executable.stat().st_mtime_ns if executable.exists() else 0}"
            cached = self._cache.get("imports:" + str(executable))
            if cached and cached[1] == marker:
                imports = cached[2]
            else:
                imports = fpmod.pe_imports(executable)
                self._cache["imports:" + str(executable)] = (time.monotonic(), marker, imports)
        manual = self._manual(game)
        runner = manual.get("runner") or {}
        native = runner.get("kind") == "manual_native" if manual else bool(fp and fp.runtime.value == "Native Linux")
        script, selection, _support = self._runner(game) if not native else (None, "native", {})
        system = fp.system if fp else None
        width = system.resolution_width if system else None
        height = system.resolution_height if system else None
        refresh = system.refresh_rate if system else None
        display = {"width": width, "height": height, "refresh": refresh,
                   "resolution": f"{width}x{height}@{round(refresh)}" if width and height and refresh else fpmod.UNKNOWN}
        category = {"value": profile.game_category,
                    "source": "manual" if profile.manual_overrides.get("category") else "saved profile",
                    "launcherMetadata": "not available"}
        kind, _code = self._kind(game)
        config = fpmod.config_snapshot(self._games_root, key)
        _keys, _programs, user_files = self._user_launch(game)
        if user_files:
            files = {**dict(config["files"]), **user_files}
            config = {"files": files, "revision": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]}
        runtime = fpmod.proton_runtime(script, selection, native)
        if runner.get("kind") == "manual_wine":
            runtime = {"kind": "wine", "name": runner.get("runnerName", fpmod.UNKNOWN),
                       "version": runner.get("runnerVersion", ""), "steamRuntime": "none", "path": runner.get("runner", "")}
        elif runner.get("kind") == "manual_proton" and not runner.get("script"):
            runtime = {"kind": "proton", "name": fpmod.UNKNOWN, "version": "", "steamRuntime": fpmod.UNKNOWN}
        executable_path = executable
        if executable_path is None and manual.get("configuration") is not None:
            executable_path = Path(manual["configuration"].executable)
        return fpmod.RunFingerprint(
            key=key, launcher=game.launcher.value, variant=kind,
            executable=fpmod.executable_identity(executable_path, Path(fp.game_root) if fp else None),
            architecture=fp.architecture.value if fp else fpmod.UNKNOWN,
            engine=fpmod.engine(imports, fp.engine.to_dict() if fp else None),
            api=fpmod.graphics_api(imports, fp.graphics_api.to_dict() if fp else None),
            runtime=runtime,
            hardware=fpmod.hardware(self._app._system_info or {}),
            display=display, active=self._active_tools(key, profile),
            config=config,
            build=str(game.steam_build_id or ""), category=category)

    def _graphics_settings(self, game: Any) -> str:
        analysis = self._analysis(game)
        files = sorted({item.file for item in analysis.settings.detected if item.file}) if analysis else []
        if not files:
            return fpmod.UNKNOWN
        digest = hashlib.sha256()
        for name in files:
            try:
                digest.update(name.encode() + b"\0" + Path(name).read_bytes())
            except OSError:
                return fpmod.UNKNOWN
        return digest.hexdigest()[:16]

    def run_context(self, game: Any, key: str) -> dict[str, Any]:
        """What must stay equal between the two sessions (captured at launch)."""

        fp = self.fingerprint(game, key)
        profile = self._app._optimization_profile_repository.load(key)
        active = fp.active
        gamescope = active.get("gamescope") != "off"
        render = (f"{profile.gamescope_input_width}x{profile.gamescope_input_height}"
                  if gamescope and not profile.gamescope_input_auto else
                  fpmod.UNKNOWN if gamescope else str(fp.display.get("resolution", fpmod.UNKNOWN)).split("@")[0])
        settings = self._graphics_settings(game)
        identity = fp.executable.get("partialSha256") or fpmod.UNKNOWN
        return {
            "build": fp.build or identity, "executable": identity,
            "displayResolution": fp.display.get("resolution", fpmod.UNKNOWN), "renderResolution": render,
            "api": fp.api.value, "runtime": fp.scope()["runtime"] if fp.runtime.get("name") != fpmod.UNKNOWN else fpmod.UNKNOWN,
            "graphicsSettings": settings,
            # Ray tracing is read from the game's settings files when they are known.
            "rayTracing": f"settings {settings}" if _known(settings) else fpmod.UNKNOWN,
            "upscaler": active.get("upscaler", fpmod.UNKNOWN), "frameGeneration": active.get("frameGeneration", fpmod.UNKNOWN),
            "gpu": fp.hardware.get("gpu"), "driver": fp.hardware.get("driver"),
            "profile": profile.to_dict(), "config": dict(fp.config), "scope": fp.scope(),
            "active": dict(active), "capturedAt": time.time(),
        }

    def _gpu_status(self, game: Any, key: str, workflow: Mapping[str, Any]) -> dict[str, Any]:
        """Only cheap, local facts; the host probe runs on the GPU-memory page."""

        from ..services.gpu_memory import load_profile as gpu_profile
        try:
            profile = gpu_profile(self._gpu_key(key), self._games_root)
        except (OSError, ValueError):
            profile = {}
        controller = getattr(self._app, "_gpu_memory_controller", None)
        cached = dict(getattr(controller, "cache", {}).get(game.id, {})) if controller is not None else {}
        return {"ready": bool(cached.get("ready")), "region": str(profile.get("region") or ""),
                "priority": bool(profile.get("priority")), "pressure": str(profile.get("pressure") or "default"),
                "pressureSupported": bool(cached.get("pressureSupported")),
                "baselineProtection": str((workflow.get("baseline") or {}).get("gpuProtection") or "unknown")}

    def candidate_context(self, game: Any, key: str, workflow: Mapping[str, Any],
                          fp: fpmod.RunFingerprint) -> mo.CandidateContext:
        profile = self._app._optimization_profile_repository.load(key)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        goal = dict(workflow.get("goal") or {})
        baseline = (workflow.get("baseline") or {}).get("metrics")
        try:
            states = dict(self._app._proton_tweaks_repository.load(key).states)
        except Exception:  # noqa: BLE001
            states = {}
        _script, selection, support = self._runner(game)
        kind, _code = self._kind(game)
        integration = True
        if kind in mo.candidates.HEROIC_LAUNCHERS:
            try:
                status = self._app._launcher_integration.status(game)
                integration = status.get("state") in ("applied", "outdated")
            except Exception:  # noqa: BLE001
                integration = False
        return mo.CandidateContext(
            launcher=kind, profile=profile, goal=str(goal.get("id") or ""), tweak_states=states,
            proton=fp.runtime.get("kind") == "proton" or selection == "selected", proton_support=support,
            gamemode_available=bool(gamemode.available), gamescope_available=bool(gamescope.available),
            gamescope_filters=tuple(getattr(gamescope, "supported_filters", ()) or ()),
            gpu_memory=self._gpu_status(game, key, workflow), api=fp.api.value, api_confidence=fp.api.confidence,
            frame_generation_active=fp.active.get("frameGeneration") not in ("off", "", None),
            display={k: fp.display.get(k) for k in ("width", "height", "refresh")},
            baseline=SessionMetrics.from_dict(baseline) if isinstance(baseline, Mapping) else None,
            fps_floor=int(goal.get("fpsFloor") or 0), quality_consent=bool(goal.get("qualityConsent")),
            explicit_experiments=bool(goal.get("explicitExperiments")),
            category=profile.game_category if profile.manual_overrides.get("category") else "unknown",
            launcher_integration_active=integration,
            user_environment=self._user_launch(game)[0], user_programs=self._user_launch(game)[1])

    def busy_reason(self, game: Any, key: str, *, allow_recording: bool = False) -> str:
        session = self._app._baseline_sessions.load(key)
        if not allow_recording and session is not None and session.status in ACTIVE_SESSION_STATUSES:
            return "A recording is active for this game"
        if game.update_in_progress:
            return "Steam is updating the game"
        if manual_mod.is_manual(game):
            if game.id in getattr(self._app, "_manual_launch_jobs", {}) and not allow_recording:
                return "The game is running"
            if self._app._optimization_controller._manual_game_running(game)[0]:
                return "The game is running"
        else:
            detector = self._app._optimization_controller._process_detector()
            if detector is not None and detector.game_process_running(game.id) is True:
                return "The game is running"
        if game.launcher in (Launcher.HEROIC, Launcher.LUTRIS):
            try:
                if self._app._launcher_integration.launcher_running(game) is True:
                    return f"Close {game.launcher.value} first; it can overwrite the game settings"
            except Exception:  # noqa: BLE001
                pass
        return ""

    def accepted_for(self, key: str, goal: str) -> dict[str, Any]:
        """Verified, kept optimization-profile values for this scope and goal."""

        game = next((item for item in self._app._domain_games.values() if manual_mod.optimization_key(item) == key), None)
        if game is None or not goal:
            return {}
        history = self.store.load(key)["history"]
        if not history:
            return {}
        try:
            scope = self.fingerprint(game, key).scope()
        except Exception:  # noqa: BLE001 - no evidence without a scope
            return {}
        values = mo.accepted_results(history, scope, goal)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        if values.get("gamemode_enabled") and not gamemode.available:
            values.pop("gamemode_enabled")
        if values.get("gamescope_enabled") and not gamescope.available:
            for name in [name for name in values if name.startswith("gamescope_")]:
                values.pop(name)
        return values

    # Read-only status -------------------------------------------------------
    def status(self, game_id: str) -> dict[str, Any]:
        game, key = self._game(game_id)
        if game is None or not key:
            kind, code = self._kind(game) if game is not None else ("unknown", "no_profile")
            return {"success": True, "gameId": game_id, "support": mo.support(kind, code or "no_profile"),
                    "workflow": {}, "steps": [], "goals": list(mo.GOALS)}
        kind, code = self._kind(game)
        document = self.store.load(key)
        workflow = document["workflow"]
        state = str(workflow.get("state") or "")
        result: dict[str, Any] = {"success": True, "gameId": game.id, "key": key, "support": mo.support(kind, code),
                                  "goals": list(mo.GOALS), "floorGoals": list(FLOOR_GOALS),
                                  "workflow": workflow, "active": state in mo.ACTIVE,
                                  "matrix": [mo.support(*row) for row in mo.MATRIX]}
        step = mo.step_index(state) if workflow else 0
        result["step"] = step
        result["steps"] = [{"id": name, "status": "done" if index < step else "current" if index == step else "pending"}
                           for index, name in enumerate(mo.STEPS)]
        analysis = self._analysis(game)
        result["analysisAvailable"] = analysis is not None
        try:
            result["configuration"] = {"preset": self._app._optimization_profile_repository.load(key).preset}
        except (OSError, ValueError):
            result["configuration"] = {"preset": ""}
        manual = self._manual(game)
        if manual:
            configuration = manual.get("configuration")
            runner = manual.get("runner") or {}
            result["manual"] = {"identity": game.id, "optimizationKey": key,
                                "executable": str(configuration.executable) if configuration is not None else "",
                                "runner": runner.get("kind", ""), "runnerName": runner.get("runnerName", ""),
                                "runnerPath": runner.get("runner", ""), "supported": bool(manual.get("supported")),
                                "code": manual.get("code", "")}
        try:
            fp = self.fingerprint(game, key)
            result["fingerprint"] = {**fp.to_dict(), "available": analysis is not None,
                                     "detection": self.detection(game, key)}
            scope = fp.scope()
        except Exception as error:  # noqa: BLE001 - shown, never fatal
            logger.warning("Measured optimization fingerprint failed for %s: %s", game.id, error)
            fp, scope = None, {}
            result["fingerprint"] = {"available": False, "error": str(error)}
        result["history"] = [{**record, "scopeMatch": mo.scope_match(record.get("scope") or {}, scope),
                              "status": mo.store.record_status(record)}
                             for record in reversed(document["history"])]
        session = self._app._baseline_sessions.load(key)
        linked = ""
        if state == "baseline_recording":
            linked = str((workflow.get("baseline") or {}).get("sessionId") or "")
        elif state == "candidate_recording":
            linked = str((workflow.get("comparison") or {}).get("sessionId") or "")
        result["recording"] = ({"sessionId": session.id, "status": session.status, "error": session.error}
                               if session is not None and linked and session.id == linked else
                               {"sessionId": linked, "status": "missing" if linked else ""})
        result["busy"] = self.busy_reason(game, key, allow_recording=True) if code == "" else ""
        if fp is not None and code == "" and workflow and state in ("baseline_ready",):
            ctx = self.candidate_context(game, key, workflow, fp)
            tested = mo.tested_candidates(document["history"], scope, ctx.goal)
            skipped = {name: "skipped in this run" for name in workflow.get("skipped") or ()}
            rows = mo.evaluate(ctx, {**skipped, **tested})
            result["candidates"] = rows
            result["proposal"] = mo.proposal(rows) or {}
        if state in ("awaiting_confirmation", "result_ready"):
            result["comparabilityCheck"] = self._automatic_comparability(workflow)
        return result

    def _automatic_comparability(self, workflow: Mapping[str, Any]) -> dict[str, Any]:
        baseline = workflow.get("baseline") or {}
        comparison = workflow.get("comparison") or {}
        candidate = workflow.get("candidate") or {}
        before_metrics = SessionMetrics.from_dict(baseline["metrics"]) if baseline.get("metrics") else None
        after_metrics = SessionMetrics.from_dict(comparison["metrics"]) if comparison.get("metrics") else None
        before, after = baseline.get("context") or {}, comparison.get("context") or {}
        result = mo.check_comparability(before, after, intended=tuple(candidate.get("intendedVariables") or ()),
                                        before_metrics=before_metrics, after_metrics=after_metrics,
                                        config_diff_ok=self._one_variable(before, after, candidate))
        changed = sorted(set(comparison.get("configDuringSession") or ()))
        if changed:
            result["issues"] = [*result["issues"], "configuration_changed_during_session"]
            result["ceiling"] = result["baseCeiling"] = "not_comparable"
        return result

    @staticmethod
    def _one_variable(before: Mapping[str, Any], after: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
        """Only the candidate's files and settings differ between the sessions."""

        allowed_files = {TARGET_FILES[item["target"]] for item in candidate.get("changes") or () if item.get("target") in TARGET_FILES}
        old_files = dict((before.get("config") or {}).get("files") or {})
        new_files = dict((after.get("config") or {}).get("files") or {})
        if {name for name in set(old_files) | set(new_files) if old_files.get(name) != new_files.get(name)} - allowed_files:
            return False
        settings = {item["setting"] for item in candidate.get("changes") or () if item.get("target") == "optimization"}
        old_profile, new_profile = dict(before.get("profile") or {}), dict(after.get("profile") or {})
        differing = {name for name in set(old_profile) | set(new_profile)
                     if old_profile.get(name) != new_profile.get(name)} - {"updated_at", "gamescope_fullscreen"}
        return differing <= settings

    # Actions ------------------------------------------------------------------
    def _supported(self, game_id: str) -> tuple[Any, str, dict[str, Any]]:
        game, key = self._game(game_id)
        if game is None or not key:
            return None, "", {"success": False, "error": "This game has no per-game optimization profile"}
        kind, code = self._kind(game)
        if code:
            return None, "", {"success": False, "error": mo.support(kind, code)["reason"]}
        return game, key, {}

    def _emit(self, game: Any) -> None:
        signal = getattr(self._app, "optimizationAnalysisChanged", None)
        if signal is not None:
            signal.emit(game.id)

    def start(self, game_id: str, goal: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        options = dict(options or {})
        if goal not in mo.GOALS:
            return {"success": False, "error": "Select an optimization goal"}
        try:
            floor = int(float(options.get("fpsFloor") or 0))
        except (TypeError, ValueError):
            return {"success": False, "error": "The FPS value must be a number"}
        if goal in FLOOR_GOALS and not 15 <= floor <= 1000:
            return {"success": False, "error": "Enter an FPS value between 15 and 1000"}
        if self._app._optimization_controller._active_optimization_change(game) is not None:
            return {"success": False, "error": "Finish the current comparison cycle before applying another change"}
        try:
            workflow = self.store.start(key, game.id, {
                "id": goal, "fpsFloor": floor if goal in FLOOR_GOALS else 0,
                "qualityConsent": bool(options.get("qualityConsent")),
                "explicitExperiments": bool(options.get("explicitExperiments"))})
        except RuntimeError as failure:
            return {"success": False, "error": str(failure)}
        self._emit(game)
        return {"success": True, "workflow": workflow}

    def _workflow(self, key: str, *states: str) -> dict[str, Any]:
        workflow = self.store.workflow(key)
        if not workflow or workflow.get("state") not in states:
            raise RuntimeError("This step is not available in the current workflow state")
        return workflow

    def record_baseline(self, game_id: str) -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        try:
            workflow = self._workflow(key, "goal_selected", "baseline_ready", "baseline_recording")
            busy = self.busy_reason(game, key)
            if busy:
                return {"success": False, "error": busy}
            if self._app._optimization_controller._active_optimization_change(game) is not None:
                return {"success": False, "error": "Record a comparison or revert the pending setting test first"}
            if self.detection(game, key)["state"] == "not_started":
                self.prepare_fingerprint(game.id)  # values still Unknown now are filled when the session ends
            context = self.run_context(game, key)
            result = self._app._optimization_controller.recordOptimizationBaseline(game.id)
            if not result.get("success"):
                return result
            session_id = str((result.get("baselineSession") or {}).get("id") or "")
            self.store.update(key, workflow["id"], "baseline_recording", error="",
                              baseline={"sessionId": session_id, "context": context})
        except Exception as failure:  # noqa: BLE001 - reported to the user
            return {"success": False, "error": str(failure) or type(failure).__name__}
        self._emit(game)
        return {"success": True, "sessionId": session_id}

    def _paths(self, key: str, changes: list[Mapping[str, Any]]) -> dict[str, Path]:
        root = self._games_root
        return {TARGET_FILES[item["target"]]: root / (self._gpu_key(key) if item["target"] == "gpu_memory" else key)
                / TARGET_FILES[item["target"]] for item in changes}

    def _write_changes(self, game: Any, key: str, changes: list[Mapping[str, Any]]) -> None:
        optimization = {item["setting"]: item["after"] for item in changes if item["target"] == "optimization"}
        if optimization:
            profile = self._app._optimization_profile_repository.load(key)
            for name, before in ((item["setting"], item["before"]) for item in changes if item["target"] == "optimization"):
                if getattr(profile, name) != before:
                    raise RuntimeError("The optimization profile changed after the candidate was prepared")
            self._app._optimization_profile_repository.save(replace(profile, **optimization))
            saved = self._app._optimization_profile_repository.load(key)
            if any(getattr(saved, name) != value for name, value in optimization.items()):
                raise RuntimeError("The optimization profile failed read-back verification")
        tweaks = {item["setting"]: item["after"] for item in changes if item["target"] == "proton_tweaks"}
        if tweaks:
            profile = self._app._proton_tweaks_repository.load(key)
            states = {**dict(profile.states), **tweaks}
            self._app._proton_tweaks_repository.save(replace(profile, states=states, enabled_tweaks=()))
            if any(self._app._proton_tweaks_repository.load(key).states.get(k, "default") != v for k, v in tweaks.items()):
                raise RuntimeError("Proton Tweaks failed read-back verification")
        gpu = {item["setting"]: item["after"] for item in changes if item["target"] == "gpu_memory"}
        if gpu:
            from ..services.gpu_memory import load_profile as gpu_profile
            current = gpu_profile(self._gpu_key(key), self._games_root)
            values = {"priority": bool(current.get("priority")), "pressure": str(current.get("pressure") or "default"),
                      "api": str(current.get("api") or "auto"), "region": str(current.get("region") or ""), **gpu}
            self._app._gpu_memory_service.apply(game, values)

    def _sync_launcher(self, game: Any) -> None:
        if game.launcher is Launcher.HEROIC:
            self._app._launcher_integration.apply(game)

    def apply_candidate(self, game_id: str, candidate_id: str = "") -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        try:
            workflow = self._workflow(key, "baseline_ready")
        except RuntimeError as failure:
            return {"success": False, "error": str(failure)}
        busy = self.busy_reason(game, key)
        if busy:
            return {"success": False, "error": busy}
        view = self.status(game.id)
        rows = view.get("candidates") or []
        chosen = next((row for row in rows if row["id"] == candidate_id), None) if candidate_id else view.get("proposal")
        if not chosen or not chosen["applicability"]["available"]:
            return {"success": False, "error": "The candidate is not available for this game and system"}
        changes = list(chosen["changes"])
        manifest = None
        entries: list[dict[str, Any]] = []
        try:
            entries = self.store.create_restore_point(key, workflow["id"], self._paths(key, changes))
            manifest = self._app._optimization_change_service.record_measured_change(
                game, candidate_id=chosen["id"], workflow_id=workflow["id"])
            self._write_changes(game, key, changes)
            self._sync_launcher(game)
            sealed = self.store.seal(entries)
            self.store.update(key, workflow["id"], "candidate_applied", error="", candidate=chosen,
                              restorePoint={"changeId": manifest["id"], "files": sealed})
        except Exception as failure:  # noqa: BLE001 - undo, then report
            logger.warning("Measured candidate apply failed for %s: %s", game.id, failure)
            try:
                if entries:
                    self.store.restore(key, workflow["id"], entries, force=True)
                    self._sync_launcher(game)
                if manifest is not None:
                    self._app._optimization_change_service.finish_measured_change(game, manifest["id"], "reverted")
            except Exception as undo:  # noqa: BLE001
                logger.error("Could not undo measured candidate for %s: %s", game.id, undo)
                self.store.update(key, workflow["id"], error=f"Restore failed: {undo}")
                return {"success": False, "error": f"{failure}; restore failed: {undo}"}
            self.store.update(key, workflow["id"], error=str(failure))
            return {"success": False, "error": str(failure) or type(failure).__name__}
        self._app._optimization_applied_changes.pop(game.id, None)
        self._emit(game)
        return {"success": True, "candidate": chosen}

    def skip_candidate(self, game_id: str, candidate_id: str) -> dict[str, Any]:
        """Hide one proposal for this run and propose the next one."""

        game, key, error = self._supported(game_id)
        if error:
            return error
        try:
            workflow = self._workflow(key, "baseline_ready")
            skipped = [*(workflow.get("skipped") or []), str(candidate_id)]
            self.store.update(key, workflow["id"], skipped=list(dict.fromkeys(skipped)))
        except RuntimeError as failure:
            return {"success": False, "error": str(failure)}
        self._emit(game)
        return {"success": True}

    def finish_without_candidate(self, game_id: str) -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        try:
            workflow = self._workflow(key, "baseline_ready")
            self.store.update(key, workflow["id"], "no_candidate")
        except RuntimeError as failure:
            return {"success": False, "error": str(failure)}
        self._emit(game)
        return {"success": True}

    def record_candidate(self, game_id: str) -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        try:
            workflow = self._workflow(key, "candidate_applied", "candidate_recording", "awaiting_confirmation")
            busy = self.busy_reason(game, key)
            if busy:
                return {"success": False, "error": busy}
            context = self.run_context(game, key)
            launcher_status = {}
            if game.launcher is Launcher.HEROIC:
                launcher_status = dict(self._app._launcher_integration.status(game))
            result = self._app._optimization_controller.recordOptimizationComparison(game.id)
            if not result.get("success"):
                return result
            session_id = str((result.get("baselineSession") or {}).get("id") or "")
            next_state = "candidate_applied" if workflow["state"] == "awaiting_confirmation" else None
            if next_state:
                self.store.update(key, workflow["id"], next_state)
            self.store.update(key, workflow["id"], "candidate_recording", error="", comparability={}, result={},
                              comparison={"sessionId": session_id, "context": context,
                                          "launcherStatus": {"state": launcher_status.get("state", "")}})
        except Exception as failure:  # noqa: BLE001
            return {"success": False, "error": str(failure) or type(failure).__name__}
        self._emit(game)
        return {"success": True, "sessionId": session_id}

    def _metrics_for(self, key: str, session: Any, context: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
        log = mo.find_session_log(Path(session.log_directory))
        if log is None:
            return None, "MangoHud did not write a log for this session"
        summary = None
        try:
            report = json.loads(self._app._runner_report_path(key).read_text(encoding="utf-8"))
            metrics_path = report.get("gpuMemoryMetrics") if report.get("baselineSessionId") == session.id else None
            if metrics_path:
                summary = json.loads(Path(metrics_path).with_name("gpu-memory-summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            summary = None
        fg = str((context.get("active") or {}).get("frameGeneration") or "off") not in ("off", "")
        try:
            return mo.session_metrics(log, frame_generation_active=fg, gpu_memory_summary=summary).to_dict(), ""
        except (OSError, ValueError) as failure:
            return None, str(failure)

    def _gpu_protection(self, key: str, session: Any) -> str:
        from ..services import gpu_memory_host
        path = self._games_root / self._gpu_key(key) / "gpu-memory-session.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "unknown"
        if str(record.get("createdAt") or "") < session.created_at.isoformat():
            return "unknown"
        region = str(record.get("region") or "")
        try:
            return str(gpu_memory_host.protection_observation(record, region))
        except Exception:  # noqa: BLE001
            return "unknown"

    def on_measurement_processed(self, game: Any, key: str, session: Any) -> None:
        """Called by the measurement poller after a session was analysed."""

        try:
            workflow = self.store.workflow(key)
            state = workflow.get("state")
            if state == "baseline_recording" and (workflow.get("baseline") or {}).get("sessionId") == session.id:
                baseline = dict(workflow["baseline"])
                metrics, problem = self._metrics_for(key, session, baseline.get("context") or {})
                end = self.run_context(game, key)
                baseline["context"] = self._completed(baseline.get("context") or {}, end)
                baseline.update(metrics=metrics, finishedAt=time.time(), exitCode=session.exit_code,
                                gpuProtection=self._gpu_protection(key, session),
                                configDuringSession=self._changed(baseline.get("context") or {}, end))
                if metrics is None or not metrics.get("sufficient") or baseline["configDuringSession"]:
                    reason = problem or ("The configuration changed during the recording" if baseline["configDuringSession"]
                                         else "The baseline was too short or had too few samples")
                    self.store.update(key, workflow["id"], "goal_selected", baseline=baseline, error=reason)
                else:
                    self.store.update(key, workflow["id"], "baseline_ready", baseline=baseline, error="")
            elif state == "candidate_recording" and (workflow.get("comparison") or {}).get("sessionId") == session.id:
                comparison = dict(workflow["comparison"])
                metrics, problem = self._metrics_for(key, session, comparison.get("context") or {})
                end = self.run_context(game, key)
                try:
                    report = json.loads(self._app._runner_report_path(key).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    report = {}
                summary = None
                if report.get("gpuMemoryMetrics") and report.get("baselineSessionId") == session.id:
                    try:
                        summary = json.loads(Path(report["gpuMemoryMetrics"]).with_name("gpu-memory-summary.json").read_text())
                    except (OSError, ValueError):
                        summary = None
                kind, _code = self._kind(game)
                comparison["context"] = self._completed(comparison.get("context") or {}, end)
                comparison.update(metrics=metrics, finishedAt=time.time(), exitCode=session.exit_code,
                                  configDuringSession=self._changed(comparison.get("context") or {}, end),
                                  activation=mo.verify_activation(
                                      workflow.get("candidate") or {}, launcher=kind, report=report,
                                      session_id=session.id, gpu_summary=summary,
                                      launcher_status=comparison.get("launcherStatus")))
                if metrics is None:
                    self.store.update(key, workflow["id"], "candidate_applied", comparison=comparison, error=problem)
                else:
                    self.store.update(key, workflow["id"], "awaiting_confirmation", comparison=comparison, error="")
        except Exception as failure:  # noqa: BLE001 - the recording itself stays saved
            logger.warning("Measured optimization could not process session %s: %s", session.id, failure)

    @staticmethod
    def _completed(start: Mapping[str, Any], end: Mapping[str, Any]) -> dict[str, Any]:
        """Fill values that were Unknown at launch (display, API from the game
        analysis of this very session) without replacing known ones."""
        result = dict(start)
        for name, value in end.items():
            if name in ("profile", "config", "active", "capturedAt"):
                continue
            if name == "scope":
                scope = dict(start.get("scope") or {})
                for field, item in dict(value or {}).items():
                    if not _known(scope.get(field)) and _known(item):
                        scope[field] = item
                result["scope"] = scope
            elif not _known(result.get(name)) and _known(value):
                result[name] = value
        return result

    @staticmethod
    def _changed(start: Mapping[str, Any], end: Mapping[str, Any]) -> list[str]:
        old = dict((start.get("config") or {}).get("files") or {})
        new = dict((end.get("config") or {}).get("files") or {})
        return sorted(name for name in set(old) | set(new) if old.get(name) != new.get(name))

    def confirm(self, game_id: str, level: str, observations: Mapping[str, Any] | None = None) -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        if level not in mo.comparison.LEVELS:
            return {"success": False, "error": "Choose whether the sessions were comparable"}
        try:
            workflow = self._workflow(key, "awaiting_confirmation", "result_ready")
        except RuntimeError as failure:
            return {"success": False, "error": str(failure)}
        observed = {name: str((observations or {}).get(name) or "not_checked") for name in ("visual", "latency", "stability")}
        confirmed = bool((observations or {}).get("unverifiedConfirmed"))
        automatic = self._automatic_comparability(workflow)
        final = mo.final_level(automatic, level, unverified_confirmed=confirmed)
        baseline, comparison = workflow.get("baseline") or {}, workflow.get("comparison") or {}
        goal = dict(workflow.get("goal") or {})
        fg = tuple(str(((part.get("context") or {}).get("active") or {}).get("frameGeneration") or "off") not in ("off", "")
                   for part in (baseline, comparison))
        result = mo.score(
            str(goal.get("id")), SessionMetrics.from_dict(baseline["metrics"]) if baseline.get("metrics") else None,
            SessionMetrics.from_dict(comparison["metrics"]) if comparison.get("metrics") else None,
            workflow.get("candidate") or {}, level=final, activation=comparison.get("activation") or {},
            observations=observed, fps_floor=int(goal.get("fpsFloor") or 0),
            after_exit_code=comparison.get("exitCode"), frame_generation=(fg[0], fg[1]))
        if workflow["state"] == "result_ready":
            self.store.update(key, workflow["id"], "awaiting_confirmation")
        self.store.update(key, workflow["id"], "result_ready", result=result,
                          comparability={"user": level, "final": final, "automatic": automatic,
                                         "unverifiedConfirmed": confirmed, "observations": observed})
        self._emit(game)
        return {"success": True, "result": result}

    def _evidence(self, key: str, workflow: Mapping[str, Any], action: str, *,
                  quality_decision: str = "") -> dict[str, Any]:
        result = workflow.get("result") or {}
        candidate = workflow.get("candidate") or {}
        baseline_context = (workflow.get("baseline") or {}).get("context") or {}
        return self.store.add_evidence(key, {
            "workflowId": workflow.get("id"), "goal": (workflow.get("goal") or {}).get("id"),
            "goalSettings": dict(workflow.get("goal") or {}), "candidateId": candidate.get("id"),
            "candidateTitle": candidate.get("title"), "changes": list(candidate.get("changes") or []),
            "decision": result.get("decision", "inconclusive"), "verified": bool(result.get("verified")),
            "confidence": result.get("confidence"), "comparability": dict(workflow.get("comparability") or {}),
            "reasons": list(result.get("reasons") or []), "costs": list(result.get("costs") or []),
            "metrics": list(result.get("metrics") or []), "userAction": action,
            **{name: result.get(name) for name in ("comparabilityVerified", "performanceVerified", "performance",
                                                   "imageQuality", "imageQualityImpact", "overall", "fullyVerified")
               if name in result},
            **({"qualityDecision": quality_decision} if quality_decision else {}),
            "scope": dict(baseline_context.get("scope") or {}),
            "baselineSessionId": (workflow.get("baseline") or {}).get("sessionId"),
            "candidateSessionId": (workflow.get("comparison") or {}).get("sessionId")})

    QUALITY_DECISIONS = ("same", "better", "worse", "keep_unevaluated")

    def keep(self, game_id: str, quality_decision: str = "") -> dict[str, Any]:
        """Keep the change. A candidate that lowers image quality needs the
        owner's image-quality answer, or an explicit Keep without it, which is
        stored as a performance-only result and never applied by Automatic."""
        game, key, error = self._supported(game_id)
        if error:
            return error
        if quality_decision and quality_decision not in self.QUALITY_DECISIONS:
            return {"success": False, "error": "Unsupported image-quality decision"}
        try:
            workflow = self._workflow(key, "result_ready")
            result = workflow.get("result") or {}
            if result.get("overall") == "performance_only" and not quality_decision:
                return {"success": False, "code": "quality_decision_required",
                        "error": "Image quality was not evaluated. Compare the image first, or keep it as a performance-only result"}
            change_id = str((workflow.get("restorePoint") or {}).get("changeId") or "")
            self._app._optimization_change_service.finish_measured_change(game, change_id, "kept")
            decision = "" if quality_decision == "keep_unevaluated" else quality_decision
            record = self._evidence(key, workflow, "kept", quality_decision=decision)
            self.store.update(key, workflow["id"], "kept")
        except Exception as failure:  # noqa: BLE001
            return {"success": False, "error": str(failure) or type(failure).__name__}
        self._app._optimization_applied_changes.pop(game.id, None)
        self._emit(game)
        return {"success": True, "evidence": record}

    def record_quality(self, game_id: str, record_id: str, decision: str) -> dict[str, Any]:
        """The owner's later image-quality judgment of a saved result."""
        game, key, error = self._supported(game_id)
        if error:
            return error
        if decision not in ("same", "better", "worse"):
            return {"success": False, "error": "Unsupported image-quality decision"}
        try:
            record = self.store.annotate(key, record_id, qualityDecision=decision, qualityDecidedAt=time.time())
        except RuntimeError as failure:
            return {"success": False, "error": str(failure)}
        self._emit(game)
        return {"success": True, "record": record}

    def _undo(self, game: Any, key: str, workflow: Mapping[str, Any]) -> None:
        point = workflow.get("restorePoint") or {}
        if not point.get("files"):
            return
        busy = self.busy_reason(game, key)
        if busy:
            raise RuntimeError(busy)
        self.store.restore(key, workflow["id"], list(point["files"]))
        self._sync_launcher(game)
        if point.get("changeId"):
            self._app._optimization_change_service.finish_measured_change(game, point["changeId"], "reverted")
        self._app._baseline_sessions.clear_measurement(key, slot="after")
        self._app._optimization_applied_changes.pop(game.id, None)
        self._app._optimization_comparisons.pop(game.id, None)

    def revert(self, game_id: str) -> dict[str, Any]:
        game, key, error = self._supported(game_id)
        if error:
            return error
        try:
            workflow = self._workflow(key, "result_ready")
            self._undo(game, key, workflow)
            record = self._evidence(key, workflow, "reverted")
            self.store.update(key, workflow["id"], "reverted")
        except Exception as failure:  # noqa: BLE001
            return {"success": False, "error": str(failure) or type(failure).__name__}
        self._emit(game)
        return {"success": True, "evidence": record}

    def cancel(self, game_id: str) -> dict[str, Any]:
        game, key = self._game(game_id)
        if game is None or not key:
            return {"success": False, "error": "This game has no per-game optimization profile"}
        workflow = self.store.workflow(key)
        if workflow.get("state") not in mo.ACTIVE:
            return {"success": False, "error": "No measured optimization is in progress"}
        session = self._app._baseline_sessions.load(key)
        linked = {str((workflow.get(part) or {}).get("sessionId") or "") for part in ("baseline", "comparison")}
        try:
            if session is not None and session.id in linked and session.status in ACTIVE_SESSION_STATUSES:
                self._app._optimization_controller.cancelOptimizationMeasurement(game.id)
            self._undo(game, key, workflow)
            if workflow.get("state") in ("result_ready", "awaiting_confirmation") and workflow.get("result"):
                self._evidence(key, self.store.workflow(key), "cancelled")
            self.store.update(key, workflow["id"], "cancelled")
        except Exception as failure:  # noqa: BLE001 - the restore point stays for a retry
            self.store.update(key, workflow["id"], error=str(failure))
            return {"success": False, "error": str(failure) or type(failure).__name__}
        self._emit(game)
        return {"success": True}
