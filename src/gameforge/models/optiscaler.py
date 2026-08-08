"""Validated per-AppID OptiScaler installation state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from .mangohud import validate_app_id


OPTISCALER_SCHEMA_VERSION = 1
OPTISCALER_PROXY_DLLS = (
    "dxgi.dll",
    "d3d12.dll",
    "winmm.dll",
    "version.dll",
    "dbghelp.dll",
    "wininet.dll",
    "winhttp.dll",
)
OPTISCALER_STATES = (
    "not_installed",
    "planned",
    "installed",
    "conflict",
    "corrupt",
    "restore_required",
    "removed",
)


def _relative_path(value: object, field_name: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\0" in text:
        raise ValueError(f"invalid {field_name}")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class OptiScalerProfile:
    schema_version: int
    app_id: str
    enabled: bool = False
    executable: str = ""
    install_directory: str = ""
    installed_version: str = ""
    injection_dll: str = "dxgi.dll"
    proton_override: str = ""
    manifest_id: str = ""
    installation_state: str = "not_installed"
    last_verified_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.schema_version != OPTISCALER_SCHEMA_VERSION:
            raise ValueError("unsupported OptiScaler profile schema")
        object.__setattr__(self, "app_id", validate_app_id(self.app_id))
        object.__setattr__(
            self, "executable", _relative_path(self.executable, "executable")
        )
        injection = str(self.injection_dll or "").strip().casefold()
        if injection not in OPTISCALER_PROXY_DLLS:
            raise ValueError("unsupported OptiScaler proxy DLL")
        object.__setattr__(self, "injection_dll", injection)
        state = str(self.installation_state or "").strip().casefold()
        if state not in OPTISCALER_STATES:
            raise ValueError("unsupported OptiScaler installation state")
        object.__setattr__(self, "installation_state", state)
        for name in ("enabled",):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        for name in ("last_verified_at", "updated_at"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                object.__setattr__(self, name, value.replace(tzinfo=UTC))

    @classmethod
    def default(cls, app_id: object) -> "OptiScalerProfile":
        return cls(
            schema_version=OPTISCALER_SCHEMA_VERSION,
            app_id=validate_app_id(app_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app_id": self.app_id,
            "enabled": self.enabled,
            "executable": self.executable,
            "install_directory": self.install_directory,
            "installed_version": self.installed_version,
            "injection_dll": self.injection_dll,
            "proton_override": self.proton_override,
            "manifest_id": self.manifest_id,
            "installation_state": self.installation_state,
            "last_verified_at": (
                self.last_verified_at.astimezone(UTC).isoformat()
                if self.last_verified_at is not None
                else None
            ),
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        expected_app_id: object | None = None,
    ) -> "OptiScalerProfile":
        app_id = validate_app_id(data.get("app_id", expected_app_id))
        if expected_app_id is not None and app_id != validate_app_id(expected_app_id):
            raise ValueError("OptiScaler profile AppID does not match its directory")
        raw = dict(data)
        schema = int(raw.get("schema_version", 0))
        if schema == 0:
            raw["schema_version"] = OPTISCALER_SCHEMA_VERSION
        defaults = cls.default(app_id).to_dict()
        defaults.update({key: value for key, value in raw.items() if key in defaults})
        defaults["app_id"] = app_id
        for name in ("last_verified_at", "updated_at"):
            value = defaults.get(name)
            if isinstance(value, str) and value:
                defaults[name] = datetime.fromisoformat(value)
            elif name == "last_verified_at" and not value:
                defaults[name] = None
        return cls(**defaults)


__all__ = [
    "OPTISCALER_PROXY_DLLS",
    "OPTISCALER_SCHEMA_VERSION",
    "OPTISCALER_STATES",
    "OptiScalerProfile",
]
