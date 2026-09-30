"""Validated per-AppID runtime optimization profiles."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import math
from datetime import UTC, datetime
from typing import Any, Mapping

from .mangohud import validate_game_key


OPTIMIZATION_SCHEMA_VERSION = 1
OPTIMIZATION_PRESETS = ("automatic", "maximum_performance", "balanced", "quiet", "custom")
GAME_CATEGORIES = (
    "competitive", "fast_action", "cinematic", "platformer_2d",
    "strategy_simulation", "retro", "unknown", "custom",
)
USER_GOALS = ("lowest_latency", "stable_image", "best_quality", "low_power", "custom")
FPS_MODES = ("automatic", "manual", "unlimited")
GAMESCOPE_MODES = ("disabled", "automatic", "native", "performance", "quality", "custom")
GAMESCOPE_SCALERS = ("auto", "integer", "fit", "fill", "stretch")
GAMESCOPE_FILTERS = ("linear", "nearest", "fsr", "nis", "pixel", "sgsr")
# Canonical, language-independent window modes; mutually exclusive flags.
GAMESCOPE_WINDOW_MODES = ("windowed", "borderless", "fullscreen")
# Filters whose output is affected by --sharpness.
GAMESCOPE_SHARPNESS_FILTERS = ("fsr", "nis")
# Gamescope's own scale: 0 = sharpest, 20 = softest. -1 = do not pass it.
GAMESCOPE_SHARPNESS_DEFAULT = -1
GAMESCOPE_SHARPNESS_CLI_MAX = 20
# Nested desktop backends only; DRM, headless and OpenVR are never offered.
GAMESCOPE_BACKENDS = ("auto", "wayland", "sdl")
# 0 / -1 below always mean "not set": the flag is not emitted.
GAMESCOPE_RESOLUTION_RANGE = (320, 16384)
GAMESCOPE_RATE_RANGE = (1, 1000)
GAMESCOPE_SDR_NITS_RANGE = (80, 1000)
GAMESCOPE_ITM_TARGET_NITS_RANGE = (200, 10000)
GAMESCOPE_CURSOR_DELAY_RANGE = (100, 60000)


def sharpness_percent_to_cli(percent: object) -> int:
    """UI sharpness 0 % (least) .. 100 % (most) -> Gamescope 20 .. 0; <0 = unset."""

    value = float(percent)
    if not math.isfinite(value) or value < 0:
        return GAMESCOPE_SHARPNESS_DEFAULT
    value = min(100.0, value)
    return int(round(GAMESCOPE_SHARPNESS_CLI_MAX - value * GAMESCOPE_SHARPNESS_CLI_MAX / 100.0))


def sharpness_cli_to_percent(cli: int) -> int:
    if cli < 0:
        return -1
    return int(round((GAMESCOPE_SHARPNESS_CLI_MAX - cli) * 100 / GAMESCOPE_SHARPNESS_CLI_MAX))


def _choice(value: object, allowed: tuple[str, ...], name: str) -> str:
    result = str(value or "").strip().casefold()
    if result not in allowed:
        raise ValueError(f"unsupported {name}")
    return result


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(value).strip() not in {str(result), f"+{result}"} and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _optional_integer(value: object, name: str, minimum: int, maximum: int, unset: int = 0) -> int:
    """``unset`` keeps Gamescope's own default; other values must be in range."""

    if value in (None, ""):
        return unset
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, float):
        if not (math.isfinite(value) and value.is_integer()):
            raise ValueError(f"{name} must be an integer")
        value = int(value)
    result = _integer(value, name, -(2**31), 2**31)
    if result == unset:
        return unset
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _optional_decimal(value: object, name: str, minimum: float, maximum: float, unset: float) -> float:
    if value in (None, ""):
        return unset
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a number")
    if result == unset:
        return unset
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return round(result, 3)


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class GameOptimizationProfile:
    schema_version: int
    app_id: str
    preset: str = "automatic"
    game_category: str = "unknown"
    user_goal: str = "stable_image"
    target_display_id: str = ""
    target_fps_mode: str = "automatic"
    target_fps: int = 60
    gamemode_enabled: bool = False
    gamescope_enabled: bool = False
    gamescope_mode: str = "disabled"
    gamescope_input_width: int = 1920
    gamescope_input_height: int = 1080
    gamescope_output_width: int = 1920
    gamescope_output_height: int = 1080
    gamescope_refresh_rate: int = 60
    gamescope_fullscreen: bool = True
    gamescope_scaler: str = "auto"
    gamescope_filter: str = "linear"
    gamescope_window_mode: str = "fullscreen"
    gamescope_sharpness: int = GAMESCOPE_SHARPNESS_DEFAULT
    gamescope_adaptive_sync: bool = False
    gamescope_grab_keyboard: bool = False
    gamescope_hdr: bool = False
    # Desktop nested options.  Every default keeps the previous command line.
    gamescope_input_auto: bool = False
    gamescope_output_auto: bool = False
    gamescope_display_index: int = -1
    gamescope_force_windows_fullscreen: bool = False
    gamescope_backend: str = "auto"
    gamescope_max_scale: float = 0.0
    gamescope_nested_refresh: int = 0
    gamescope_framerate_limit: int = 0
    gamescope_unfocused_refresh: int = 0
    gamescope_hdr_sdr_content_nits: int = 0
    gamescope_sdr_gamut_wideness: float = -1.0
    gamescope_hdr_itm: bool = False
    gamescope_hdr_itm_sdr_nits: int = 0
    gamescope_hdr_itm_target_nits: int = 0
    gamescope_force_grab_cursor: bool = False
    gamescope_hide_cursor_delay: int = 0
    gamescope_mouse_sensitivity: float = 0.0
    gamescope_expose_wayland: bool = False
    gamescope_steam_integration: bool = False
    manual_overrides: Mapping[str, bool] = field(default_factory=dict)
    last_recommendation: Mapping[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Keys written by a newer build survive a load/save round-trip.
    extra_fields: Mapping[str, Any] = field(
        default_factory=dict, compare=False, hash=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.schema_version != OPTIMIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported optimization profile schema")
        object.__setattr__(self, "app_id", validate_game_key(self.app_id))
        object.__setattr__(self, "preset", _choice(self.preset, OPTIMIZATION_PRESETS, "preset"))
        object.__setattr__(self, "game_category", _choice(self.game_category, GAME_CATEGORIES, "game_category"))
        object.__setattr__(self, "user_goal", _choice(self.user_goal, USER_GOALS, "user_goal"))
        object.__setattr__(self, "target_fps_mode", _choice(self.target_fps_mode, FPS_MODES, "target_fps_mode"))
        object.__setattr__(self, "gamescope_mode", _choice(self.gamescope_mode, GAMESCOPE_MODES, "gamescope_mode"))
        object.__setattr__(self, "gamescope_scaler", _choice(self.gamescope_scaler, GAMESCOPE_SCALERS, "gamescope_scaler"))
        object.__setattr__(self, "gamescope_filter", _choice(self.gamescope_filter, GAMESCOPE_FILTERS, "gamescope_filter"))
        object.__setattr__(self, "gamescope_window_mode", _choice(self.gamescope_window_mode, GAMESCOPE_WINDOW_MODES, "gamescope_window_mode"))
        sharpness = _integer(self.gamescope_sharpness, "gamescope_sharpness", -1, 20)
        object.__setattr__(self, "gamescope_sharpness", sharpness)
        display_id = str(self.target_display_id or "").strip()
        if "\n" in display_id or "\r" in display_id or len(display_id) > 256:
            raise ValueError("invalid target_display_id")
        object.__setattr__(self, "target_display_id", display_id)
        for name in ("target_fps", "gamescope_refresh_rate"):
            object.__setattr__(self, name, _integer(getattr(self, name), name, 15, 1000))
        for name in (
            "gamescope_input_width", "gamescope_input_height",
            "gamescope_output_width", "gamescope_output_height",
        ):
            object.__setattr__(self, name, _integer(getattr(self, name), name, 320, 16384))
        for name in (
            "gamemode_enabled", "gamescope_enabled", "gamescope_fullscreen",
            "gamescope_adaptive_sync", "gamescope_grab_keyboard", "gamescope_hdr",
            "gamescope_input_auto", "gamescope_output_auto",
            "gamescope_force_windows_fullscreen", "gamescope_hdr_itm",
            "gamescope_force_grab_cursor", "gamescope_expose_wayland",
            "gamescope_steam_integration",
        ):
            object.__setattr__(self, name, _boolean(getattr(self, name), name))
        object.__setattr__(self, "gamescope_backend", _choice(self.gamescope_backend, GAMESCOPE_BACKENDS, "gamescope_backend"))
        object.__setattr__(
            self, "gamescope_display_index",
            _optional_integer(self.gamescope_display_index, "gamescope_display_index", 0, 63, unset=-1),
        )
        for name, (low, high) in (
            ("gamescope_nested_refresh", GAMESCOPE_RATE_RANGE),
            ("gamescope_framerate_limit", GAMESCOPE_RATE_RANGE),
            ("gamescope_unfocused_refresh", GAMESCOPE_RATE_RANGE),
            ("gamescope_hdr_sdr_content_nits", GAMESCOPE_SDR_NITS_RANGE),
            ("gamescope_hdr_itm_sdr_nits", GAMESCOPE_SDR_NITS_RANGE),
            ("gamescope_hdr_itm_target_nits", GAMESCOPE_ITM_TARGET_NITS_RANGE),
            ("gamescope_hide_cursor_delay", GAMESCOPE_CURSOR_DELAY_RANGE),
        ):
            object.__setattr__(self, name, _optional_integer(getattr(self, name), name, low, high))
        object.__setattr__(self, "gamescope_max_scale", _optional_decimal(self.gamescope_max_scale, "gamescope_max_scale", 1.0, 8.0, 0.0))
        object.__setattr__(self, "gamescope_mouse_sensitivity", _optional_decimal(self.gamescope_mouse_sensitivity, "gamescope_mouse_sensitivity", 0.1, 10.0, 0.0))
        object.__setattr__(
            self, "gamescope_sdr_gamut_wideness",
            _optional_decimal(self.gamescope_sdr_gamut_wideness, "gamescope_sdr_gamut_wideness", 0.0, 1.0, -1.0),
        )
        object.__setattr__(self, "extra_fields", dict(self.extra_fields or {}))
        # gamescope_window_mode is canonical; the legacy boolean mirrors it.
        object.__setattr__(self, "gamescope_fullscreen", self.gamescope_window_mode == "fullscreen")
        overrides = {str(key): bool(value) for key, value in dict(self.manual_overrides).items()}
        object.__setattr__(self, "manual_overrides", overrides)
        object.__setattr__(self, "last_recommendation", dict(self.last_recommendation))
        if self.updated_at.tzinfo is None:
            object.__setattr__(self, "updated_at", self.updated_at.replace(tzinfo=UTC))

    @classmethod
    def default(cls, app_id: object) -> "GameOptimizationProfile":
        return cls(schema_version=OPTIMIZATION_SCHEMA_VERSION, app_id=validate_game_key(app_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "app_id": self.app_id,
            "preset": self.preset, "game_category": self.game_category,
            "user_goal": self.user_goal, "target_display_id": self.target_display_id,
            "target_fps_mode": self.target_fps_mode, "target_fps": self.target_fps,
            "gamemode_enabled": self.gamemode_enabled, "gamescope_enabled": self.gamescope_enabled,
            "gamescope_mode": self.gamescope_mode,
            "gamescope_input_width": self.gamescope_input_width,
            "gamescope_input_height": self.gamescope_input_height,
            "gamescope_output_width": self.gamescope_output_width,
            "gamescope_output_height": self.gamescope_output_height,
            "gamescope_refresh_rate": self.gamescope_refresh_rate,
            "gamescope_fullscreen": self.gamescope_fullscreen,
            "gamescope_scaler": self.gamescope_scaler,
            "gamescope_filter": self.gamescope_filter,
            "gamescope_window_mode": self.gamescope_window_mode,
            "gamescope_sharpness": self.gamescope_sharpness,
            "gamescope_adaptive_sync": self.gamescope_adaptive_sync,
            "gamescope_grab_keyboard": self.gamescope_grab_keyboard,
            "gamescope_hdr": self.gamescope_hdr,
            **{name: getattr(self, name) for name in GAMESCOPE_DESKTOP_FIELDS},
            "manual_overrides": dict(self.manual_overrides),
            "last_recommendation": dict(self.last_recommendation),
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
        } | {
            key: value for key, value in self.extra_fields.items()
            if key not in _PROFILE_FIELD_NAMES
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, expected_app_id: object | None = None) -> "GameOptimizationProfile":
        app_id = validate_game_key(data.get("app_id", expected_app_id))
        if expected_app_id is not None and app_id != validate_game_key(expected_app_id):
            raise ValueError("optimization profile AppID does not match its directory")
        migrated = dict(data)
        schema = int(migrated.get("schema_version", 0))
        if schema == 0:
            aliases = {"profile": "preset", "gamemode": "gamemode_enabled", "gamescope": "gamescope_enabled", "fps_limit": "target_fps"}
            for old, new in aliases.items():
                if old in migrated and new not in migrated:
                    migrated[new] = migrated[old]
            preset_aliases = {"maximum performance": "maximum_performance", "balanced": "balanced", "quiet": "quiet", "custom": "custom", "automatic": "automatic"}
            migrated["preset"] = preset_aliases.get(str(migrated.get("preset", "automatic")).casefold(), "automatic")
            migrated["schema_version"] = OPTIMIZATION_SCHEMA_VERSION
        # Profiles saved before the explicit window mode existed only stored
        # the fullscreen boolean (True -> -f, False -> -b); keep that behaviour.
        if "gamescope_window_mode" not in migrated and "gamescope_fullscreen" in migrated:
            migrated["gamescope_window_mode"] = (
                "fullscreen" if migrated.get("gamescope_fullscreen") is not False else "borderless"
            )
        defaults = cls.default(app_id).to_dict()
        defaults.update({key: value for key, value in migrated.items() if key in defaults})
        defaults["app_id"] = app_id
        raw_updated = defaults.get("updated_at")
        if isinstance(raw_updated, str):
            defaults["updated_at"] = datetime.fromisoformat(raw_updated)
        extra = {
            key: value for key, value in migrated.items()
            if key not in _PROFILE_FIELD_NAMES and key != "extra_fields"
        }
        known = {key: value for key, value in defaults.items() if key in _PROFILE_FIELD_NAMES}
        return cls(**known, extra_fields=extra)


GAMESCOPE_DESKTOP_FIELDS = (
    "gamescope_input_auto", "gamescope_output_auto", "gamescope_display_index",
    "gamescope_force_windows_fullscreen", "gamescope_backend", "gamescope_max_scale",
    "gamescope_nested_refresh", "gamescope_framerate_limit", "gamescope_unfocused_refresh",
    "gamescope_hdr_sdr_content_nits", "gamescope_sdr_gamut_wideness", "gamescope_hdr_itm",
    "gamescope_hdr_itm_sdr_nits", "gamescope_hdr_itm_target_nits",
    "gamescope_force_grab_cursor", "gamescope_hide_cursor_delay",
    "gamescope_mouse_sensitivity", "gamescope_expose_wayland", "gamescope_steam_integration",
)
# Every Gamescope field; "Restore" resets exactly these for one game.
GAMESCOPE_PROFILE_FIELDS = tuple(
    item.name for item in fields(GameOptimizationProfile) if item.name.startswith("gamescope_")
)
_PROFILE_FIELD_NAMES = frozenset(
    item.name for item in fields(GameOptimizationProfile) if item.name != "extra_fields"
)


__all__ = [
    "FPS_MODES", "GAME_CATEGORIES", "GAMESCOPE_BACKENDS", "GAMESCOPE_DESKTOP_FIELDS",
    "GAMESCOPE_FILTERS", "GAMESCOPE_MODES", "GAMESCOPE_PROFILE_FIELDS",
    "sharpness_cli_to_percent", "sharpness_percent_to_cli",
    "GAMESCOPE_SCALERS", "GAMESCOPE_SHARPNESS_DEFAULT", "GAMESCOPE_SHARPNESS_FILTERS",
    "GAMESCOPE_WINDOW_MODES", "GameOptimizationProfile", "OPTIMIZATION_PRESETS",
    "OPTIMIZATION_SCHEMA_VERSION", "USER_GOALS",
]
