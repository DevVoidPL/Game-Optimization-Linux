from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ..models import Game, Launcher, MangoHudProfile
from ..services import (
    METRIC_CONFIG_KEYS,
    MangoHudConfigWriter,
    SteamLaunchError,
    uses_flatpak_steam,
)

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class MangoHudController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    @staticmethod
    def _profile_key(game: Game | None) -> str:
        if game is None:
            return ""
        if game.steam_app_id:
            return str(game.steam_app_id)
        if game.launcher is Launcher.MANUAL and game.data_source.casefold() == "local":
            return game.id
        return ""

    def getMangoHudProfile(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return self._app._mangohud_error("Select an available Steam game first")
        app_id = self._profile_key(game)
        if not app_id:
            return self._app._mangohud_error("MangoHud profiles require a supported game")
        try:
            profile = self._app._mangohud_repository.load(app_id)
            return self._app._mangohud_profile_to_qml(game, profile)
        except Exception as error:
            logger.warning("Could not load MangoHud profile for %s: %s", game.id, error)
            return self._app._mangohud_error(str(error), app_id=app_id)

    def previewMangoHudProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return self._app._mangohud_error("MangoHud profiles require a supported game")
        try:
            profile = self._app._mangohud_profile_from_payload(
                app_id, values
            )
            result = self._app._mangohud_profile_to_qml(game, profile)
            result["success"] = True
            return result
        except Exception as error:
            return self._app._mangohud_error(str(error), app_id=app_id)

    def saveMangoHudProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            result = self._app._mangohud_error("MangoHud profiles require a supported game")
            self._app._emit_toast(result["error"], "error")
            return result
        try:
            previous_profile = self._app._mangohud_repository.load(app_id)
            profile = self._app._mangohud_profile_from_payload(app_id, values)
            optimization_profile = self._app._optimization_profile_repository.load(app_id)
            if self._app._gamescope_owns_fps_limit(optimization_profile):
                profile = replace(
                    profile,
                    fps_limit=None,
                    fps_limit_method="",
                    updated_at=datetime.now(UTC),
                )
            resolution = self._app._mangohud_launch_integration.executable_resolver.resolve(
                game, profile.executable_path
            )
            if not profile.executable_path and resolution.reliable and resolution.selected:
                profile = replace(
                    profile, executable_path=resolution.selected.relative_path
                )
            steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
            availability = self._app._mangohud_detector.detect(steam_type)
            if profile.enabled and not availability.available:
                raise ValueError(availability.message)
            profile_path = self._app._mangohud_repository.save(profile)
            config_path = self._app._mangohud_repository.config_path(app_id)
            MangoHudConfigWriter(availability.supported_keys).write(
                profile, config_path
            )
            self._app._mangohud_launch_integration.synchronize(
                game, profile, previous_profile=previous_profile
            )
        except Exception as error:
            logger.warning("Could not save MangoHud profile for %s: %s", game.id, error)
            result = self._app._mangohud_error(str(error), app_id=app_id)
            self._app._emit_toast(f"Could not save MangoHud profile: {error}", "error")
            return result
        logger.info(
            "Saved MangoHud profile appId=%s profile=%s config=%s enabled=%s",
            app_id,
            profile_path,
            config_path,
            profile.enabled,
        )
        self._app.mangoHudProfileChanged.emit(app_id)
        self._app._emit_toast("MangoHud profile saved", "success")
        result = self._app._mangohud_profile_to_qml(game, profile)
        result["success"] = True
        return result

    def resetMangoHudProfile(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return self._app._mangohud_error("MangoHud profiles require a supported game")
        try:
            self._app._mangohud_launch_integration.reset(game)
            profile = self._app._mangohud_repository.reset(app_id)
        except Exception as error:
            result = self._app._mangohud_error(str(error), app_id=app_id)
            self._app._emit_toast(f"Could not reset MangoHud profile: {error}", "error")
            return result
        self._app.mangoHudProfileChanged.emit(profile.app_id)
        self._app._emit_toast("Game Optimization MangoHud settings restored", "success")
        result = self._app._mangohud_profile_to_qml(game, profile)
        result["success"] = True
        return result

    def openMangoHudDirectory(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return False
        directory = self._app._mangohud_repository.game_directory(app_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._app._emit_toast(f"Could not open MangoHud directory: {error}", "error")
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def mangoHudLaunchPlan(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return self._app._mangohud_error("Select an available Steam game first")
        try:
            profile = self._app._mangohud_profile_for_game(game)
            activation = (
                self._app._mangohud_launch_integration.prepare(game, profile)
                if profile is not None
                else None
            )
            build_plan = getattr(self._app._game_launcher, "build_plan", None)
            if not callable(build_plan):
                raise ValueError("The configured Steam launcher cannot build a launch plan")
            plan = build_plan(game, activation)
            result = plan.to_dict()
            result["success"] = True
            return result
        except Exception as error:
            return self._app._mangohud_error(str(error), app_id=str(game.steam_app_id or ""))

    def _mangohud_profile_for_game(self, game: Game) -> MangoHudProfile | None:
        app_id = self._profile_key(game)
        if not app_id:
            return None
        try:
            return self._app._mangohud_repository.load(app_id)
        except Exception as error:
            raise SteamLaunchError(f"Could not load MangoHud profile: {error}") from error

    def _mangohud_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> MangoHudProfile:
        base = self._app._mangohud_repository.load(app_id)
        aliases = {
            "fontSize": "font_size",
            "backgroundAlpha": "background_alpha",
            "roundCorners": "round_corners",
            "tableColumns": "table_columns",
            "fpsLimit": "fps_limit",
            "fpsLimitMethod": "fps_limit_method",
            "vulkanPresentMode": "vulkan_present_mode",
            "toggleHudKey": "toggle_hud_key",
            "loggingEnabled": "logging_enabled",
            "logDuration": "log_duration",
            "logInterval": "log_interval",
            "outputFolder": "output_folder",
            "toggleLoggingKey": "toggle_logging_key",
            "selectedExecutable": "executable_path",
            "executablePath": "executable_path",
        }
        normalized = {aliases.get(str(key), str(key)): value for key, value in values.items()}
        preset = str(normalized.get("preset", base.preset)).strip().lower()
        prepared = base.apply_preset(preset) if preset != base.preset else base
        data = prepared.to_dict()
        allowed = {
            "enabled",
            "preset",
            "position",
            "font_size",
            "background_alpha",
            "round_corners",
            "compact",
            "horizontal",
            "table_columns",
            "fps_limit",
            "fps_limit_method",
            "vulkan_present_mode",
            "vsync",
            "toggle_hud_key",
            "metrics",
            "logging_enabled",
            "log_duration",
            "log_interval",
            "output_folder",
            "toggle_logging_key",
            "executable_path",
        }
        for key, value in normalized.items():
            if key in allowed:
                data[key] = value
        data["schema_version"] = base.schema_version
        data["app_id"] = app_id
        data["preset"] = preset
        if preset == "disabled":
            data["enabled"] = False
            data["metrics"] = []
        elif preset != "custom":
            data["enabled"] = bool(normalized.get("enabled", True))
            data["metrics"] = list(prepared.apply_preset(preset).metrics)
        data["updated_at"] = datetime.now(UTC)
        return MangoHudProfile.from_dict(
            data,
            expected_app_id=app_id,
            default_output_folder=self._app._mangohud_repository.log_root / app_id,
        )

    def _mangohud_profile_to_qml(
        self, game: Game, profile: MangoHudProfile
    ) -> dict[str, Any]:
        optimization_profile = self._app._optimization_profile_repository.load(profile.app_id)
        gamescope_owns_limit = self._app._gamescope_owns_fps_limit(optimization_profile)
        effective_profile = (
            replace(profile, fps_limit=None, fps_limit_method="")
            if gamescope_owns_limit else profile
        )
        steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
        availability = self._app._mangohud_detector.detect(steam_type)
        strategy = self._app._mangohud_launch_integration.status(game, effective_profile)
        data = effective_profile.to_dict()
        data.update(
            {
                "success": True,
                "schemaVersion": profile.schema_version,
                "appId": profile.app_id,
                "fontSize": profile.font_size,
                "backgroundAlpha": profile.background_alpha,
                "roundCorners": profile.round_corners,
                "tableColumns": profile.table_columns,
                "fpsLimit": effective_profile.fps_limit or 0,
                "fpsLimitMethod": effective_profile.fps_limit_method,
                "fpsLimitOwner": (
                    "gamescope" if gamescope_owns_limit
                    else "mangohud" if effective_profile.fps_limit is not None
                    else "none"
                ),
                "vulkanPresentMode": profile.vulkan_present_mode,
                "toggleHudKey": profile.toggle_hud_key,
                "loggingEnabled": profile.logging_enabled,
                "logDuration": profile.log_duration,
                "logInterval": profile.log_interval,
                "outputFolder": profile.output_folder,
                "toggleLoggingKey": profile.toggle_logging_key,
                "executablePath": profile.executable_path,
                "updatedAt": profile.updated_at.astimezone(UTC).isoformat(),
                "profilePath": str(self._app._mangohud_repository.profile_path(profile.app_id)),
                "configPath": str(self._app._mangohud_repository.config_path(profile.app_id)),
                "available": availability.available,
                "activationEnabled": bool(profile.enabled and availability.available),
                "availabilityMessage": availability.message,
                "steamType": steam_type,
                "version": availability.version,
                "supportedMetrics": [
                    metric
                    for metric, key in METRIC_CONFIG_KEYS.items()
                    if key in availability.supported_keys
                ],
            }
        )
        data.update(strategy.to_dict())
        writer = MangoHudConfigWriter(availability.supported_keys)
        data["configPreview"] = writer.render(effective_profile)
        return data

    def _clear_mangohud_fps_limit(self, game: Game) -> None:
        app_id = self._profile_key(game)
        if not app_id:
            return
        profile = self._app._mangohud_repository.load(app_id)
        if profile.fps_limit is None and not profile.fps_limit_method:
            return
        effective = replace(
            profile,
            fps_limit=None,
            fps_limit_method="",
            updated_at=datetime.now(UTC),
        )
        availability = self._app._mangohud_detector.detect(
            "flatpak" if uses_flatpak_steam(game) else "native"
        )
        self._app._mangohud_repository.save(effective)
        MangoHudConfigWriter(availability.supported_keys).write(
            effective, self._app._mangohud_repository.config_path(app_id)
        )
        self._app._mangohud_launch_integration.synchronize(
            game, effective, previous_profile=profile
        )
        self._app.mangoHudProfileChanged.emit(app_id)
