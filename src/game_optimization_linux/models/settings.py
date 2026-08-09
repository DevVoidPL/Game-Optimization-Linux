"""Persistable application settings and their validation rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .enums import (
    AutomaticCompressionMode,
    CompressionProfile,
    ControllerMode,
    LogLevel,
    PostLaunchBehavior,
    ThemeMode,
)


_SUPPORTED_LANGUAGES = {
    "en",
    "en_us",
    "en_gb",
    "english",
    "pl",
    "pl_pl",
    "polski",
    "polish",
    "es",
    "es_es",
    "español",
    "espanol",
    "spanish",
}


def _is_supported_language(value: str) -> bool:
    return value.strip().casefold().replace("-", "_") in _SUPPORTED_LANGUAGES


def _read_bool(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _read_percentage(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    if not 1 <= value <= 100:
        raise ValueError(f"{key} must be between 1 and 100")
    return value


def _read_int_range(
    data: Mapping[str, Any], key: str, default: int, minimum: int, maximum: int
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _read_float_range(
    data: Mapping[str, Any], key: str, default: float, minimum: float, maximum: float
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return normalized


@dataclass(frozen=True, slots=True)
class AppSettings:
    """User preferences; all path values remain platform-neutral ``Path`` objects."""

    language: str = "English"
    theme: ThemeMode = ThemeMode.SYSTEM
    automatic_updates: bool = True
    default_compression_profile: CompressionProfile = CompressionProfile.AUTO
    automatic_compression_mode: AutomaticCompressionMode = AutomaticCompressionMode.OFF
    automatic_compression_profile: CompressionProfile = CompressionProfile.AUTO
    automatic_compression_delay_seconds: int = 300
    automatic_compression_max_jobs: int = 1
    automatic_compression_min_free_gb: float = 10.0
    automatic_compression_notify: bool = True
    automatic_compression_skipped_app_ids: tuple[str, ...] = ()
    automatic_compression_libraries: tuple[Path, ...] = ()
    cpu_limit_percent: int = 75
    gpu_limit_percent: int = 75
    backup_directory: Path = Path("backups")
    quarantine_directory: Path = Path("quarantine")
    library_directories: tuple[Path, ...] = ()
    steam_installation_directories: tuple[Path, ...] = ()
    ignored_steam_libraries: tuple[Path, ...] = ()
    experimental_features: bool = False
    log_level: LogLevel = LogLevel.INFO
    show_steam_tools_and_runtimes: bool = False
    controller_mode: ControllerMode = ControllerMode.AUTOMATIC
    swap_accept_back: bool = False
    analog_deadzone: float = 0.20
    navigation_repeat_delay_ms: int = 350
    navigation_repeat_rate_ms: int = 110
    hide_cursor_in_couch_mode: bool = True
    start_couch_mode_fullscreen: bool = True
    post_launch_behavior: PostLaunchBehavior = PostLaunchBehavior.MINIMIZE
    interface_sounds: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.language, str) or not self.language.strip():
            raise ValueError("language cannot be empty")
        if not _is_supported_language(self.language):
            raise ValueError("language must be English, Polski, or Español")
        if not isinstance(self.theme, ThemeMode):
            raise ValueError("theme must be a ThemeMode value")
        if not isinstance(self.automatic_updates, bool):
            raise ValueError("automatic_updates must be a boolean")
        if not isinstance(self.default_compression_profile, CompressionProfile):
            raise ValueError(
                "default_compression_profile must be a CompressionProfile value"
            )
        if not isinstance(self.automatic_compression_mode, AutomaticCompressionMode):
            raise ValueError(
                "automatic_compression_mode must be an AutomaticCompressionMode value"
            )
        if not isinstance(self.automatic_compression_profile, CompressionProfile):
            raise ValueError(
                "automatic_compression_profile must be a CompressionProfile value"
            )
        if (
            isinstance(self.automatic_compression_delay_seconds, bool)
            or not isinstance(self.automatic_compression_delay_seconds, int)
            or not 0 <= self.automatic_compression_delay_seconds <= 86_400
        ):
            raise ValueError(
                "automatic_compression_delay_seconds must be between 0 and 86400"
            )
        if (
            isinstance(self.automatic_compression_max_jobs, bool)
            or not isinstance(self.automatic_compression_max_jobs, int)
            or not 1 <= self.automatic_compression_max_jobs <= 8
        ):
            raise ValueError("automatic_compression_max_jobs must be between 1 and 8")
        if (
            isinstance(self.automatic_compression_min_free_gb, bool)
            or not isinstance(self.automatic_compression_min_free_gb, (int, float))
            or not 0.0 <= float(self.automatic_compression_min_free_gb) <= 1_000_000.0
        ):
            raise ValueError(
                "automatic_compression_min_free_gb must be between 0 and 1000000"
            )
        if not isinstance(self.automatic_compression_notify, bool):
            raise ValueError("automatic_compression_notify must be a boolean")
        if not isinstance(self.automatic_compression_skipped_app_ids, tuple) or not all(
            isinstance(app_id, str)
            and app_id.isascii()
            and app_id.isdecimal()
            and int(app_id) > 0
            for app_id in self.automatic_compression_skipped_app_ids
        ):
            raise ValueError(
                "automatic_compression_skipped_app_ids must contain positive Steam AppIDs"
            )
        if len(set(self.automatic_compression_skipped_app_ids)) != len(
            self.automatic_compression_skipped_app_ids
        ):
            raise ValueError("automatic_compression_skipped_app_ids contains duplicates")
        if not isinstance(self.automatic_compression_libraries, tuple) or not all(
            isinstance(path, Path) for path in self.automatic_compression_libraries
        ):
            raise ValueError("automatic_compression_libraries must be a tuple of Paths")
        for value, field_name in (
            (self.cpu_limit_percent, "cpu_limit_percent"),
            (self.gpu_limit_percent, "gpu_limit_percent"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 100
            ):
                raise ValueError(f"{field_name} must be an integer between 1 and 100")
        if not isinstance(self.backup_directory, Path):
            raise ValueError("backup_directory must be a Path")
        if not isinstance(self.quarantine_directory, Path):
            raise ValueError("quarantine_directory must be a Path")
        for paths, field_name in (
            (self.library_directories, "library_directories"),
            (self.steam_installation_directories, "steam_installation_directories"),
            (self.ignored_steam_libraries, "ignored_steam_libraries"),
        ):
            if not isinstance(paths, tuple) or not all(
                isinstance(path, Path) for path in paths
            ):
                raise ValueError(f"{field_name} must be a tuple of Path values")
        if not isinstance(self.experimental_features, bool):
            raise ValueError("experimental_features must be a boolean")
        if not isinstance(self.log_level, LogLevel):
            raise ValueError("log_level must be a LogLevel value")
        if not isinstance(self.show_steam_tools_and_runtimes, bool):
            raise ValueError("show_steam_tools_and_runtimes must be a boolean")
        if not isinstance(self.controller_mode, ControllerMode):
            raise ValueError("controller_mode must be a ControllerMode value")
        if not isinstance(self.swap_accept_back, bool):
            raise ValueError("swap_accept_back must be a boolean")
        if (
            isinstance(self.analog_deadzone, bool)
            or not isinstance(self.analog_deadzone, (int, float))
            or not 0.05 <= float(self.analog_deadzone) <= 0.75
        ):
            raise ValueError("analog_deadzone must be between 0.05 and 0.75")
        for value, field_name, minimum, maximum in (
            (self.navigation_repeat_delay_ms, "navigation_repeat_delay_ms", 150, 1500),
            (self.navigation_repeat_rate_ms, "navigation_repeat_rate_ms", 50, 500),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
        for value, field_name in (
            (self.hide_cursor_in_couch_mode, "hide_cursor_in_couch_mode"),
            (self.start_couch_mode_fullscreen, "start_couch_mode_fullscreen"),
            (self.interface_sounds, "interface_sounds"),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{field_name} must be a boolean")
        if not isinstance(self.post_launch_behavior, PostLaunchBehavior):
            raise ValueError("post_launch_behavior must be a PostLaunchBehavior value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "theme": self.theme.value,
            "automatic_updates": self.automatic_updates,
            "default_compression_profile": self.default_compression_profile.value,
            "automatic_compression_mode": self.automatic_compression_mode.value,
            "automatic_compression_profile": self.automatic_compression_profile.value,
            "automatic_compression_delay_seconds": self.automatic_compression_delay_seconds,
            "automatic_compression_max_jobs": self.automatic_compression_max_jobs,
            "automatic_compression_min_free_gb": float(
                self.automatic_compression_min_free_gb
            ),
            "automatic_compression_notify": self.automatic_compression_notify,
            "automatic_compression_skipped_app_ids": list(
                self.automatic_compression_skipped_app_ids
            ),
            "automatic_compression_libraries": [
                str(path) for path in self.automatic_compression_libraries
            ],
            "cpu_limit_percent": self.cpu_limit_percent,
            "gpu_limit_percent": self.gpu_limit_percent,
            "backup_directory": str(self.backup_directory),
            "quarantine_directory": str(self.quarantine_directory),
            "library_directories": [str(path) for path in self.library_directories],
            "steam_installation_directories": [
                str(path) for path in self.steam_installation_directories
            ],
            "ignored_steam_libraries": [
                str(path) for path in self.ignored_steam_libraries
            ],
            "experimental_features": self.experimental_features,
            "log_level": self.log_level.value,
            "show_steam_tools_and_runtimes": self.show_steam_tools_and_runtimes,
            "controller_mode": self.controller_mode.value,
            "swap_accept_back": self.swap_accept_back,
            "analog_deadzone": float(self.analog_deadzone),
            "navigation_repeat_delay_ms": self.navigation_repeat_delay_ms,
            "navigation_repeat_rate_ms": self.navigation_repeat_rate_ms,
            "hide_cursor_in_couch_mode": self.hide_cursor_in_couch_mode,
            "start_couch_mode_fullscreen": self.start_couch_mode_fullscreen,
            "post_launch_behavior": self.post_launch_behavior.value,
            "interface_sounds": self.interface_sounds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AppSettings:
        """Build validated settings while tolerating unknown future keys."""

        if not isinstance(data, Mapping):
            raise ValueError("settings root must be a JSON object")

        defaults = cls()
        language = data.get("language", defaults.language)
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language must be a non-empty string")
        if not _is_supported_language(language):
            raise ValueError("language must be English, Polski, or Español")

        backup_directory = data.get("backup_directory", str(defaults.backup_directory))
        quarantine_directory = data.get(
            "quarantine_directory", str(defaults.quarantine_directory)
        )
        if not isinstance(backup_directory, str) or not backup_directory.strip():
            raise ValueError("backup_directory must be a non-empty string")
        if not isinstance(quarantine_directory, str) or not quarantine_directory.strip():
            raise ValueError("quarantine_directory must be a non-empty string")

        raw_libraries = data.get(
            "library_directories",
            [str(path) for path in defaults.library_directories],
        )
        if not isinstance(raw_libraries, list) or not all(
            isinstance(item, str) and bool(item.strip()) for item in raw_libraries
        ):
            raise ValueError(
                "library_directories must be a list of non-empty strings"
            )

        raw_steam_directories = data.get("steam_installation_directories", [])
        if not isinstance(raw_steam_directories, list) or not all(
            isinstance(item, str) and bool(item.strip())
            for item in raw_steam_directories
        ):
            raise ValueError(
                "steam_installation_directories must be a list of non-empty strings"
            )

        raw_ignored_libraries = data.get("ignored_steam_libraries", [])
        if not isinstance(raw_ignored_libraries, list) or not all(
            isinstance(item, str) and bool(item.strip())
            for item in raw_ignored_libraries
        ):
            raise ValueError(
                "ignored_steam_libraries must be a list of non-empty strings"
            )

        raw_skipped_app_ids = data.get("automatic_compression_skipped_app_ids", [])
        if not isinstance(raw_skipped_app_ids, list) or not all(
            isinstance(item, str) and bool(item.strip())
            for item in raw_skipped_app_ids
        ):
            raise ValueError(
                "automatic_compression_skipped_app_ids must be a list of AppID strings"
            )
        normalized_skipped_app_ids = tuple(
            item.strip() for item in raw_skipped_app_ids
        )

        raw_automatic_libraries = data.get("automatic_compression_libraries", [])
        if not isinstance(raw_automatic_libraries, list) or not all(
            isinstance(item, str) and bool(item.strip())
            for item in raw_automatic_libraries
        ):
            raise ValueError(
                "automatic_compression_libraries must be a list of non-empty strings"
            )

        try:
            theme = ThemeMode(data.get("theme", defaults.theme.value))
            compression_profile = CompressionProfile(
                data.get(
                    "default_compression_profile",
                    defaults.default_compression_profile.value,
                )
            )
            automatic_compression_mode = AutomaticCompressionMode(
                data.get(
                    "automatic_compression_mode",
                    defaults.automatic_compression_mode.value,
                )
            )
            automatic_compression_profile = CompressionProfile(
                data.get(
                    "automatic_compression_profile",
                    defaults.automatic_compression_profile.value,
                )
            )
            log_level = LogLevel(data.get("log_level", defaults.log_level.value))
            controller_mode = ControllerMode(
                data.get("controller_mode", defaults.controller_mode.value)
            )
            post_launch_behavior = PostLaunchBehavior(
                data.get("post_launch_behavior", defaults.post_launch_behavior.value)
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid settings enum value: {error}") from error

        return cls(
            language=language,
            theme=theme,
            automatic_updates=_read_bool(
                data, "automatic_updates", defaults.automatic_updates
            ),
            default_compression_profile=compression_profile,
            automatic_compression_mode=automatic_compression_mode,
            automatic_compression_profile=automatic_compression_profile,
            automatic_compression_delay_seconds=_read_int_range(
                data,
                "automatic_compression_delay_seconds",
                defaults.automatic_compression_delay_seconds,
                0,
                86_400,
            ),
            automatic_compression_max_jobs=_read_int_range(
                data,
                "automatic_compression_max_jobs",
                defaults.automatic_compression_max_jobs,
                1,
                8,
            ),
            automatic_compression_min_free_gb=_read_float_range(
                data,
                "automatic_compression_min_free_gb",
                defaults.automatic_compression_min_free_gb,
                0.0,
                1_000_000.0,
            ),
            automatic_compression_notify=_read_bool(
                data,
                "automatic_compression_notify",
                defaults.automatic_compression_notify,
            ),
            automatic_compression_skipped_app_ids=normalized_skipped_app_ids,
            automatic_compression_libraries=tuple(
                Path(item) for item in raw_automatic_libraries
            ),
            cpu_limit_percent=_read_percentage(
                data, "cpu_limit_percent", defaults.cpu_limit_percent
            ),
            gpu_limit_percent=_read_percentage(
                data, "gpu_limit_percent", defaults.gpu_limit_percent
            ),
            backup_directory=Path(backup_directory),
            quarantine_directory=Path(quarantine_directory),
            library_directories=tuple(Path(item) for item in raw_libraries),
            steam_installation_directories=tuple(
                Path(item) for item in raw_steam_directories
            ),
            ignored_steam_libraries=tuple(
                Path(item) for item in raw_ignored_libraries
            ),
            experimental_features=_read_bool(
                data, "experimental_features", defaults.experimental_features
            ),
            log_level=log_level,
            show_steam_tools_and_runtimes=_read_bool(
                data,
                "show_steam_tools_and_runtimes",
                defaults.show_steam_tools_and_runtimes,
            ),
            controller_mode=controller_mode,
            swap_accept_back=_read_bool(
                data, "swap_accept_back", defaults.swap_accept_back
            ),
            analog_deadzone=_read_float_range(
                data, "analog_deadzone", defaults.analog_deadzone, 0.05, 0.75
            ),
            navigation_repeat_delay_ms=_read_int_range(
                data,
                "navigation_repeat_delay_ms",
                defaults.navigation_repeat_delay_ms,
                150,
                1500,
            ),
            navigation_repeat_rate_ms=_read_int_range(
                data,
                "navigation_repeat_rate_ms",
                defaults.navigation_repeat_rate_ms,
                50,
                500,
            ),
            hide_cursor_in_couch_mode=_read_bool(
                data,
                "hide_cursor_in_couch_mode",
                defaults.hide_cursor_in_couch_mode,
            ),
            start_couch_mode_fullscreen=_read_bool(
                data,
                "start_couch_mode_fullscreen",
                defaults.start_couch_mode_fullscreen,
            ),
            post_launch_behavior=post_launch_behavior,
            interface_sounds=_read_bool(
                data, "interface_sounds", defaults.interface_sounds
            ),
        )
