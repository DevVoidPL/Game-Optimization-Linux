"""Validated per-AppID runtime optimization profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from .mangohud import validate_app_id


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
GAMESCOPE_FILTERS = ("linear", "nearest", "fsr", "nis", "pixel")


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
    manual_overrides: Mapping[str, bool] = field(default_factory=dict)
    last_recommendation: Mapping[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.schema_version != OPTIMIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported optimization profile schema")
        object.__setattr__(self, "app_id", validate_app_id(self.app_id))
        object.__setattr__(self, "preset", _choice(self.preset, OPTIMIZATION_PRESETS, "preset"))
        object.__setattr__(self, "game_category", _choice(self.game_category, GAME_CATEGORIES, "game_category"))
        object.__setattr__(self, "user_goal", _choice(self.user_goal, USER_GOALS, "user_goal"))
        object.__setattr__(self, "target_fps_mode", _choice(self.target_fps_mode, FPS_MODES, "target_fps_mode"))
        object.__setattr__(self, "gamescope_mode", _choice(self.gamescope_mode, GAMESCOPE_MODES, "gamescope_mode"))
        object.__setattr__(self, "gamescope_scaler", _choice(self.gamescope_scaler, GAMESCOPE_SCALERS, "gamescope_scaler"))
        object.__setattr__(self, "gamescope_filter", _choice(self.gamescope_filter, GAMESCOPE_FILTERS, "gamescope_filter"))
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
        for name in ("gamemode_enabled", "gamescope_enabled", "gamescope_fullscreen"):
            object.__setattr__(self, name, _boolean(getattr(self, name), name))
        overrides = {str(key): bool(value) for key, value in dict(self.manual_overrides).items()}
        object.__setattr__(self, "manual_overrides", overrides)
        object.__setattr__(self, "last_recommendation", dict(self.last_recommendation))
        if self.updated_at.tzinfo is None:
            object.__setattr__(self, "updated_at", self.updated_at.replace(tzinfo=UTC))

    @classmethod
    def default(cls, app_id: object) -> "GameOptimizationProfile":
        return cls(schema_version=OPTIMIZATION_SCHEMA_VERSION, app_id=validate_app_id(app_id))

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
            "manual_overrides": dict(self.manual_overrides),
            "last_recommendation": dict(self.last_recommendation),
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, expected_app_id: object | None = None) -> "GameOptimizationProfile":
        app_id = validate_app_id(data.get("app_id", expected_app_id))
        if expected_app_id is not None and app_id != validate_app_id(expected_app_id):
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
        defaults = cls.default(app_id).to_dict()
        defaults.update({key: value for key, value in migrated.items() if key in defaults})
        defaults["app_id"] = app_id
        raw_updated = defaults.get("updated_at")
        if isinstance(raw_updated, str):
            defaults["updated_at"] = datetime.fromisoformat(raw_updated)
        return cls(**defaults)


__all__ = [
    "FPS_MODES", "GAME_CATEGORIES", "GAMESCOPE_FILTERS", "GAMESCOPE_MODES",
    "GAMESCOPE_SCALERS", "GameOptimizationProfile", "OPTIMIZATION_PRESETS",
    "OPTIMIZATION_SCHEMA_VERSION", "USER_GOALS",
]
