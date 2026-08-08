"""Validated per-game MangoHud profile values.

The model deliberately stores UI-independent identifiers.  QML translates
labels, while the config writer owns the mapping to MangoHud keys.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


MANGOHUD_SCHEMA_VERSION = 1
MANGOHUD_PRESETS = ("disabled", "fps_only", "basic", "extended", "custom")
MANGOHUD_POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
MANGOHUD_METRICS = (
    "fps",
    "frametime",
    "gpu_usage",
    "gpu_temperature",
    "gpu_clock",
    "gpu_power",
    "vram",
    "cpu_usage",
    "cpu_temperature",
    "cpu_clock",
    "cpu_power",
    "ram",
    "process_memory",
    "process_vram",
    "resolution",
    "wine_proton",
    "gamemode",
    "battery",
    "network",
)

PRESET_METRICS: dict[str, tuple[str, ...]] = {
    "disabled": (),
    "fps_only": ("fps", "frametime"),
    "basic": (
        "fps",
        "frametime",
        "gpu_usage",
        "cpu_usage",
        "gpu_temperature",
        "cpu_temperature",
        "vram",
        "ram",
    ),
    "extended": (
        "fps",
        "frametime",
        "gpu_usage",
        "cpu_usage",
        "gpu_temperature",
        "cpu_temperature",
        "gpu_clock",
        "cpu_clock",
        "gpu_power",
        "cpu_power",
        "vram",
        "ram",
        "process_memory",
        "process_vram",
        "wine_proton",
        "resolution",
        "gamemode",
    ),
    "custom": (),
}

_APP_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*$")


def validate_app_id(value: object) -> str:
    app_id = str(value or "").strip()
    if not _APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError("app_id must be a positive decimal Steam AppID")
    return app_id


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be an integer")
        normalized = int(value)
        if not minimum <= normalized <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return normalized
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(value).strip() not in {str(normalized), f"+{normalized}"}:
        raise ValueError(f"{name} must be an integer")
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return normalized


def _optional_integer(
    value: object, name: str, minimum: int, maximum: int
) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    return _integer(value, name, minimum, maximum)


def _number(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return normalized


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError(f"{name} must be a boolean")


def _key(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 64 or not _KEY_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} contains an unsupported key binding")
    return normalized


def _metrics(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        requested = [key for key, enabled in value.items() if enabled is True]
    elif isinstance(value, (list, tuple, set)):
        requested = list(value)
    else:
        raise ValueError("metrics must be a list or map")
    normalized = {str(item).strip() for item in requested}
    unknown = normalized.difference(MANGOHUD_METRICS)
    if unknown:
        raise ValueError(f"unsupported MangoHud metrics: {', '.join(sorted(unknown))}")
    return tuple(metric for metric in MANGOHUD_METRICS if metric in normalized)


@dataclass(frozen=True, slots=True)
class MangoHudProfile:
    schema_version: int
    app_id: str
    enabled: bool = False
    preset: str = "disabled"
    position: str = "top-left"
    font_size: int = 24
    background_alpha: float = 0.5
    round_corners: int = 8
    compact: bool = False
    horizontal: bool = False
    table_columns: int = 3
    fps_limit: int | None = None
    fps_limit_method: str = ""
    vulkan_present_mode: str = ""
    vsync: int | None = None
    toggle_hud_key: str = "Shift_R+F12"
    metrics: tuple[str, ...] = ()
    logging_enabled: bool = False
    log_duration: int = 60
    log_interval: float = 0.1
    output_folder: str = ""
    toggle_logging_key: str = "Shift_L+F2"
    executable_path: str = ""
    updated_at: datetime = datetime.min.replace(tzinfo=UTC)

    def __post_init__(self) -> None:
        if self.schema_version != MANGOHUD_SCHEMA_VERSION:
            raise ValueError("unsupported MangoHud profile schema")
        object.__setattr__(self, "app_id", validate_app_id(self.app_id))
        if self.preset not in MANGOHUD_PRESETS:
            raise ValueError("unsupported MangoHud preset")
        if self.position not in MANGOHUD_POSITIONS:
            raise ValueError("unsupported MangoHud position")
        object.__setattr__(self, "enabled", _boolean(self.enabled, "enabled"))
        object.__setattr__(self, "font_size", _integer(self.font_size, "font_size", 8, 96))
        object.__setattr__(
            self,
            "background_alpha",
            _number(self.background_alpha, "background_alpha", 0.0, 1.0),
        )
        object.__setattr__(
            self, "round_corners", _integer(self.round_corners, "round_corners", 0, 64)
        )
        object.__setattr__(self, "compact", _boolean(self.compact, "compact"))
        object.__setattr__(self, "horizontal", _boolean(self.horizontal, "horizontal"))
        object.__setattr__(
            self, "table_columns", _integer(self.table_columns, "table_columns", 1, 6)
        )
        object.__setattr__(
            self, "fps_limit", _optional_integer(self.fps_limit, "fps_limit", 15, 1000)
        )
        if self.fps_limit_method not in {"", "early", "late"}:
            raise ValueError("unsupported fps_limit_method")
        if self.vulkan_present_mode not in {
            "",
            "immediate",
            "mailbox",
            "fifo",
            "fifo_relaxed",
            "shared_demand_refresh",
            "shared_continuous_refresh",
            "fifo_latest_ready",
        }:
            raise ValueError("unsupported vulkan_present_mode")
        if self.vsync is not None:
            object.__setattr__(self, "vsync", _integer(self.vsync, "vsync", -1, 3))
        object.__setattr__(self, "toggle_hud_key", _key(self.toggle_hud_key, "toggle_hud_key"))
        object.__setattr__(self, "metrics", _metrics(self.metrics))
        object.__setattr__(
            self, "logging_enabled", _boolean(self.logging_enabled, "logging_enabled")
        )
        object.__setattr__(
            self, "log_duration", _integer(self.log_duration, "log_duration", 1, 86400)
        )
        object.__setattr__(
            self, "log_interval", _number(self.log_interval, "log_interval", 0.0, 60.0)
        )
        object.__setattr__(
            self,
            "toggle_logging_key",
            _key(self.toggle_logging_key, "toggle_logging_key"),
        )
        if self.output_folder:
            folder = Path(self.output_folder).expanduser()
            if not folder.is_absolute() or "\n" in self.output_folder or "\r" in self.output_folder:
                raise ValueError("output_folder must be an absolute path")
            object.__setattr__(self, "output_folder", str(folder))
        executable_path = str(self.executable_path or "").strip().replace("\\", "/")
        if executable_path:
            relative = PurePosixPath(executable_path)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or executable_path.startswith("/")
                or "\n" in executable_path
                or "\r" in executable_path
                or len(executable_path) > 1024
            ):
                raise ValueError("executable_path must be a safe path relative to the game")
            object.__setattr__(self, "executable_path", relative.as_posix())
        if self.updated_at.tzinfo is None:
            object.__setattr__(self, "updated_at", self.updated_at.replace(tzinfo=UTC))

    @classmethod
    def default(cls, app_id: object, *, output_folder: Path | None = None) -> "MangoHudProfile":
        return cls(
            schema_version=MANGOHUD_SCHEMA_VERSION,
            app_id=validate_app_id(app_id),
            output_folder=str(output_folder) if output_folder is not None else "",
        )

    def apply_preset(self, preset: str) -> "MangoHudProfile":
        if preset not in MANGOHUD_PRESETS:
            raise ValueError("unsupported MangoHud preset")
        metrics = self.metrics if preset == "custom" else PRESET_METRICS[preset]
        return replace(
            self,
            enabled=preset != "disabled",
            preset=preset,
            metrics=metrics,
            updated_at=datetime.now(UTC),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app_id": self.app_id,
            "enabled": self.enabled,
            "preset": self.preset,
            "position": self.position,
            "font_size": self.font_size,
            "background_alpha": self.background_alpha,
            "round_corners": self.round_corners,
            "compact": self.compact,
            "horizontal": self.horizontal,
            "table_columns": self.table_columns,
            "fps_limit": self.fps_limit,
            "fps_limit_method": self.fps_limit_method,
            "vulkan_present_mode": self.vulkan_present_mode,
            "vsync": self.vsync,
            "toggle_hud_key": self.toggle_hud_key,
            "metrics": list(self.metrics),
            "logging_enabled": self.logging_enabled,
            "log_duration": self.log_duration,
            "log_interval": self.log_interval,
            "output_folder": self.output_folder,
            "toggle_logging_key": self.toggle_logging_key,
            "executable_path": self.executable_path,
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        expected_app_id: object | None = None,
        default_output_folder: Path | None = None,
    ) -> "MangoHudProfile":
        app_id = validate_app_id(data.get("app_id", expected_app_id))
        if expected_app_id is not None and app_id != validate_app_id(expected_app_id):
            raise ValueError("MangoHud profile AppID does not match its directory")
        updated_raw = str(data.get("updated_at") or "")
        try:
            updated_at = datetime.fromisoformat(updated_raw) if updated_raw else datetime.now(UTC)
        except ValueError as error:
            raise ValueError("invalid MangoHud profile updated_at") from error
        defaults = cls.default(app_id, output_folder=default_output_folder)
        values = defaults.to_dict()
        values.update(dict(data))
        values["app_id"] = app_id
        values["updated_at"] = updated_at
        if not values.get("output_folder") and default_output_folder is not None:
            values["output_folder"] = str(default_output_folder)
        return cls(**values)


__all__ = [
    "MANGOHUD_METRICS",
    "MANGOHUD_POSITIONS",
    "MANGOHUD_PRESETS",
    "MANGOHUD_SCHEMA_VERSION",
    "MangoHudProfile",
    "PRESET_METRICS",
    "validate_app_id",
]
