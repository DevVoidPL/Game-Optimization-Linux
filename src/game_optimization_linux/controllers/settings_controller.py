from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from enum import Enum
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import SETTINGS_FILE
from ..models import AppSettings, AutomaticCompressionMode, ControllerMode
from .presenters import qml_value, settings_to_qml

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class SettingsController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    def saveSetting(self, key: str, value: Any) -> bool:
        field_name = self._app._setting_field_name(key)
        if field_name is None:
            logger.warning("Refused to save unknown setting key %r", key)
            self._app._emit_toast(f"Unknown setting: {key}", "warning")
            return False

        try:
            current_value = getattr(self._app._settings_model, field_name)
            converted_value = self._app._coerce_setting_value(
                current_value,
                value,
                field_name=field_name,
            )
            updated_model = replace(
                self._app._settings_model, **{field_name: converted_value}
            )
            self._app._settings_store.save(updated_model)
        except Exception as error:
            self._app._report_error(f"saving setting {field_name}", error)
            return False

        self._app._settings_model = updated_model
        self._app._settings = settings_to_qml(updated_model)
        self._app.settingsChanged.emit()
        toast_message = "Setting saved locally"
        toast_level = "success"
        new_theme_mode = self._app._extract_theme_mode(self._app._settings)
        if new_theme_mode != self._app._theme_mode:
            self._app._theme_mode = new_theme_mode
            self._app.themeModeChanged.emit()
        if field_name == "log_level":
            self._app._apply_runtime_log_level(str(qml_value(converted_value)))
        elif field_name == "steam_installation_directories" and not self._app._demo_mode:
            setter = getattr(self._app._game_provider, "set_additional_roots", None)
            try:
                if callable(setter):
                    setter(tuple(converted_value))
                self._app.requestLibraryScan(
                    "settings_steam_paths",
                    "",
                    "settings",
                )
            except Exception as error:
                logger.warning(
                    "Saved Steam locations but could not apply them immediately: %s",
                    error,
                )
                toast_message = "Steam locations were saved and will apply after restart"
                toast_level = "warning"
        elif field_name == "library_directories" and not self._app._demo_mode:
            setter = getattr(self._app._game_provider, "set_local_roots", None)
            try:
                if callable(setter):
                    setter(tuple(converted_value))
                self._app.requestLibraryScan(
                    "settings_local_paths",
                    "",
                    "settings",
                )
            except Exception as error:
                logger.warning(
                    "Saved local game directories but could not apply them immediately: %s",
                    error,
                )
                toast_message = "Local game directories will apply after restart"
                toast_level = "warning"
        elif field_name == "show_steam_tools_and_runtimes":
            self._app._reload_games()
        elif field_name in {
            "swap_accept_back",
            "analog_deadzone",
            "navigation_repeat_delay_ms",
            "navigation_repeat_rate_ms",
        }:
            self._app._configure_gamepad_service()
        elif field_name == "controller_mode":
            if converted_value is ControllerMode.DESKTOP_ONLY:
                self._app._set_interface_mode("desktop")
            elif converted_value is ControllerMode.COUCH_ONLY:
                self._app._set_interface_mode("couch")
        elif field_name == "interface_sounds":
            self._app._ui_sound_service.set_enabled(bool(converted_value))
        elif field_name.startswith("automatic_compression_"):
            self._app._reload_updates()
            if (
                field_name == "automatic_compression_mode"
                and converted_value is not AutomaticCompressionMode.OFF
            ):
                self._app._queue_eligible_automatic_compression()
        logger.info("Saved local setting %s", field_name)
        self._app._emit_toast(toast_message, toast_level)
        return True

    def _load_settings(self) -> AppSettings:
        try:
            settings = self._app._settings_store.load()
        except Exception as error:
            logger.exception(
                "Could not load settings from %s; using in-memory defaults: %s",
                SETTINGS_FILE,
                error,
            )
            self._app._deferred_toasts.append(
                ("Local settings could not be loaded; safe defaults are active", "warning")
            )
            return AppSettings()
        return settings

    def _setting_field_name(self, key: str) -> str | None:
        aliases = {
            "appearance": "theme",
            "themeMode": "theme",
            "automaticUpdates": "automatic_updates",
            "defaultCompressionProfile": "default_compression_profile",
            "automaticCompressionMode": "automatic_compression_mode",
            "automaticCompressionProfile": "automatic_compression_profile",
            "automaticCompressionDelaySeconds": "automatic_compression_delay_seconds",
            "automaticCompressionMaxJobs": "automatic_compression_max_jobs",
            "automaticCompressionMinFreeGb": "automatic_compression_min_free_gb",
            "automaticCompressionNotify": "automatic_compression_notify",
            "automaticCompressionSkippedAppIds": "automatic_compression_skipped_app_ids",
            "automaticCompressionLibraries": "automatic_compression_libraries",
            "cpuLimit": "cpu_limit_percent",
            "cpuUsageLimit": "cpu_limit_percent",
            "gpuLimit": "gpu_limit_percent",
            "gpuUsageLimit": "gpu_limit_percent",
            "backupDirectory": "backup_directory",
            "quarantineDirectory": "quarantine_directory",
            "libraryDirectories": "library_directories",
            "steamInstallationDirectories": "steam_installation_directories",
            "ignoredSteamLibraries": "ignored_steam_libraries",
            "experimentalFeatures": "experimental_features",
            "logLevel": "log_level",
            "showSteamToolsAndRuntimes": "show_steam_tools_and_runtimes",
            "controllerMode": "controller_mode",
            "swapAcceptBack": "swap_accept_back",
            "analogDeadzone": "analog_deadzone",
            "navigationRepeatDelayMs": "navigation_repeat_delay_ms",
            "navigationRepeatRateMs": "navigation_repeat_rate_ms",
            "hideCursorInCouchMode": "hide_cursor_in_couch_mode",
            "startCouchModeFullscreen": "start_couch_mode_fullscreen",
            "postLaunchBehavior": "post_launch_behavior",
            "interfaceSounds": "interface_sounds",
        }
        requested = aliases.get(key, key)
        if not is_dataclass(self._app._settings_model):
            return None
        valid_fields = {field.name for field in fields(self._app._settings_model)}
        return requested if requested in valid_fields else None

    def _coerce_setting_value(
        self,
        current_value: Any,
        value: Any,
        *,
        field_name: str = "",
    ) -> Any:
        if isinstance(current_value, Enum):
            return self._app._coerce_enum(type(current_value), str(value))
        if isinstance(current_value, Path):
            path_text = str(value)
            if not path_text.strip():
                raise ValueError("directory path cannot be empty")
            return Path(path_text).expanduser()
        if isinstance(current_value, bool):
            return self._app._coerce_bool(value)
        if isinstance(current_value, int) and not isinstance(current_value, bool):
            return int(value)
        if isinstance(current_value, float):
            return float(value)
        if isinstance(current_value, tuple):
            if isinstance(value, (str, bytes)):
                items = (str(value),)
            else:
                items = tuple(value)
            if field_name == "automatic_compression_skipped_app_ids":
                normalized = tuple(str(item).strip() for item in items)
                if any(
                    not item.isascii()
                    or not item.isdecimal()
                    or int(item) <= 0
                    for item in normalized
                ):
                    raise ValueError(
                        "skipped AppIDs must contain positive decimal Steam AppIDs"
                    )
                return tuple(dict.fromkeys(normalized))
            path_tuple = field_name in {
                "library_directories",
                "steam_installation_directories",
                "automatic_compression_libraries",
                "ignored_steam_libraries",
            } or bool(current_value and isinstance(current_value[0], Path))
            if path_tuple:
                path_values = tuple(str(item) for item in items)
                if any(not item.strip() for item in path_values):
                    raise ValueError("library directory paths cannot be empty")
                if field_name == "library_directories":
                    normalized: dict[str, Path] = {}
                    home = Path.home().resolve(strict=False)
                    for item in path_values:
                        path = Path(item).expanduser().resolve(strict=True)
                        if not path.is_dir():
                            raise ValueError(f"local game directory is not a directory: {path}")
                        if path == Path(path.anchor) or path == home:
                            raise ValueError(
                                "select a dedicated games directory, not the filesystem root or home directory"
                            )
                        normalized.setdefault(os.path.normcase(os.fspath(path)), path)
                    return tuple(normalized.values())
                return tuple(Path(item).expanduser() for item in path_values)
            return items
        if isinstance(current_value, list):
            if isinstance(value, (str, bytes)):
                return [str(value)]
            return list(value)
        return value
