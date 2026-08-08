from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
import logging
import os
import shlex
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QGuiApplication

from ..models import (
    Game,
    GameOptimizationProfile,
    Launcher,
    OptimizationOptions,
    OptimizationProfile,
)
from ..services import OptiScalerError, ProtonTweaksError

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class OptimizationController:
    def __init__(self, app: AppController) -> None:
        self._app = app

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
        if game is None or not game.steam_app_id:
            return {"success": False, "error": "Proton Tweaks require a Steam AppID"}
        try:
            return self._app._proton_tweaks_to_qml(str(game.steam_app_id))
        except (OSError, ValueError, ProtonTweaksError) as error:
            return {"success": False, "error": str(error)}

    def saveProtonTweaks(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return {"success": False, "error": "Proton Tweaks require a Steam AppID"}
        app_id = str(game.steam_app_id)
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
        if game is None or game.launcher is not Launcher.STEAM or not game.steam_app_id:
            return {"success": False, "error": "Optimization profiles require a Steam AppID"}
        try:
            profile = self._app._optimization_profile_repository.load(game.steam_app_id)
            return self._app._optimization_profile_to_qml(profile)
        except Exception as error:
            logger.warning("Could not load optimization profile for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def previewOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return {"success": False, "error": "Optimization profiles require a Steam AppID"}
        try:
            profile = self._app._optimization_profile_from_payload(str(game.steam_app_id), values)
            return self._app._optimization_profile_to_qml(profile)
        except Exception as error:
            return {"success": False, "error": str(error)}

    def saveOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None or game.launcher is not Launcher.STEAM or not game.steam_app_id:
            return {"success": False, "error": "Optimization profiles require a Steam AppID"}
        try:
            previous_profile = self._app._optimization_profile_repository.load(
                str(game.steam_app_id)
            )
            profile = self._app._optimization_profile_from_payload(str(game.steam_app_id), values)
            display = self._app._optimization_display_for(profile.target_display_id)
            recommendation = self._app._optimization_advisor.recommend(profile, display)
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
            path = self._app._optimization_profile_repository.save(profile)
            try:
                if self._app._gamescope_owns_fps_limit(profile):
                    self._app._clear_mangohud_fps_limit(game)
            except Exception:
                self._app._optimization_profile_repository.save(previous_profile)
                raise
            result = self._app._optimization_profile_to_qml(profile)
            result.update({"success": True, "profilePath": str(path)})
            self._app._emit_toast("Optimization profile saved", "success")
            return result
        except Exception as error:
            self._app._emit_toast(f"Could not save optimization profile: {error}", "error")
            return {"success": False, "error": str(error)}

    def testGameOptimizationRunner(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None or not game.steam_app_id:
            return {"success": False, "message": "Optimization profiles require a Steam AppID"}
        result = self._app._runner_integration.test(game.steam_app_id)
        self._app._emit_toast(
            str(result.get("message", "Runner test completed")),
            "success" if result.get("success") else "warning",
        )
        return result

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
        return GameOptimizationProfile.from_dict(data, expected_app_id=app_id)

    def _optimization_profile_to_qml(
        self, profile: GameOptimizationProfile
    ) -> dict[str, Any]:
        displays = self._app._optimization_displays()
        display = self._app._optimization_display_for(profile.target_display_id)
        recommendation = self._app._optimization_advisor.recommend(profile, display)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        mangohud_activation_owner = "none"
        try:
            mangohud_profile = self._app._mangohud_repository.load(profile.app_id)
            mangohud_fps_limit = mangohud_profile.fps_limit
            if mangohud_profile.enabled:
                profile_game = next(
                    (
                        item
                        for item in self._app._domain_games.values()
                        if str(item.steam_app_id or "") == profile.app_id
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
            "gamemode": gamemode.to_dict(), "gamescope": gamescope.to_dict(),
            "launchPlan": plan.to_dict(),
            "launchPlanText": shlex.join(plan.command),
            "fpsLimitOwner": plan.fps_limit_owner,
            "steamLaunchCommand": self._app._runner_integration.steam_command(profile.app_id),
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
