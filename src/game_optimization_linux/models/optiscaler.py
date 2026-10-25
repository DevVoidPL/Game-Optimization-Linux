"""Validated per-AppID OptiScaler installation state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from .mangohud import validate_app_id, validate_game_key


OPTISCALER_SCHEMA_VERSION = 3
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
    "partial",
    "restore_required",
    "removed",
)
OPTISCALER_CHANNELS = ("stable", "edge")
OPTISCALER_BACKENDS = ("none", "optiscaler", "dlss_enabler")
OPTISCALER_COMPATIBILITY_STATES = ("supported", "unsupported", "unknown", "warning")
OPTISCALER_FSR4_MODES = ("automatic", "normal", "force_int8", "lite", "disabled")
# Upscaler DLL variant placed over OptiScaler's amd_fidelityfx_upscaler_dx12.dll.
OPTISCALER_FSR_VARIANTS = ("official", "cyboman_exact", "cyboman_lossy2")
OPTISCALER_LOG_MODES = ("auto", "info", "debug")
OPTISCALER_FSR4_PRESETS = ("auto", "0", "1", "2", "3", "4", "5")
OPTISCALER_OVERLAY_SHORTCUTS = ("auto", "0x2D", "0x24", "0x23", "0x79", "0x7B")
OPTISCALER_SOURCE_IDENTITIES = ("", "official_optiscaler", "local_archive")
OPTISCALER_RUNTIME_VERIFICATION_STATES = (
    "not_verified",
    "verified_fsr4",
    "verified_fsr4_int8",
    "verified_fsr4_int8_lite",
    "verified_fsr3_fallback",
)
# These are the values used by supported upstream OptiScaler INI generations.
# The UI further narrows them to values advertised by the installed INI.
OPTISCALER_UPSCALER_VALUES = (
    "auto",
    "dlss",
    "xess",
    "xess_12",
    "fsr21",
    "fsr22",
    "fsr31",
    "fsr21_12",
    "fsr22_12",
    "fsr31_12",
    "ffx",
    "ffx_12",
)
_LOCAL_GAME_ID = re.compile(r"^local-[0-9a-f]{24}$")


def validate_optiscaler_game_id(value: object) -> str:
    """Steam AppID, ``local-<hash>`` or a Heroic/Lutris game ID (file-name safe)."""
    text = str(value or "").strip()
    if _LOCAL_GAME_ID.fullmatch(text):
        return text
    if text.startswith(("heroic-", "lutris-")):
        return validate_game_key(text)
    return validate_app_id(text)


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
    backend: str = "optiscaler"
    executable: str = ""
    install_directory: str = ""
    installed_version: str = ""
    channel: str = "stable"
    source_identity: str = ""
    fidelityfx_upscaler_version: str = ""
    injection_dll: str = "dxgi.dll"
    proton_override: str = ""
    fsr4_mode: str = "automatic"
    effective_fsr4_mode: str = "disabled"
    automatic_reason: str = ""
    fsr_agility_sdk_upgrade: bool = False
    fsr4_watermark: bool = False
    dx11_upscaler: str = "auto"
    dx12_upscaler: str = "auto"
    vulkan_upscaler: str = "auto"
    optipatcher_enabled: bool = False
    fake_nvapi_mode: str = "auto"
    dxgi_spoofing_mode: str = "auto"
    reflex_emulation: bool = False
    optipatcher_compatibility: str = "unknown"
    fsr_variant: str = "official"
    fsr4_preset: str = "auto"
    sharpness: str = "auto"
    log_mode: str = "auto"
    overlay_shortcut: str = "auto"
    configuration_applied: bool = False
    runtime_verification_status: str = "not_verified"
    manifest_id: str = ""
    installation_state: str = "not_installed"
    last_verified_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.schema_version != OPTISCALER_SCHEMA_VERSION:
            raise ValueError("unsupported OptiScaler profile schema")
        object.__setattr__(self, "app_id", validate_optiscaler_game_id(self.app_id))
        backend = str(self.backend or "optiscaler").strip().casefold()
        if backend not in OPTISCALER_BACKENDS:
            raise ValueError("unsupported OptiScaler backend")
        object.__setattr__(self, "backend", backend)
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
        channel = str(self.channel or "").strip().casefold()
        if channel not in OPTISCALER_CHANNELS:
            raise ValueError("unsupported OptiScaler channel")
        object.__setattr__(self, "channel", channel)
        source = str(self.source_identity or "").strip().casefold()
        if source not in OPTISCALER_SOURCE_IDENTITIES:
            raise ValueError("unsupported OptiScaler source identity")
        object.__setattr__(self, "source_identity", source)
        mode = str(self.fsr4_mode or "").strip().casefold()
        if mode not in OPTISCALER_FSR4_MODES:
            raise ValueError("unsupported OptiScaler FSR4 mode")
        object.__setattr__(self, "fsr4_mode", mode)
        effective_mode = str(self.effective_fsr4_mode or "").strip().casefold()
        if effective_mode not in OPTISCALER_FSR4_MODES:
            raise ValueError("unsupported effective OptiScaler FSR4 mode")
        object.__setattr__(self, "effective_fsr4_mode", effective_mode)
        for name in ("dx11_upscaler", "dx12_upscaler", "vulkan_upscaler"):
            value = str(getattr(self, name) or "").strip().casefold()
            if value not in OPTISCALER_UPSCALER_VALUES:
                raise ValueError(f"unsupported OptiScaler {name}")
            object.__setattr__(self, name, value)
        verification = str(self.runtime_verification_status or "").strip().casefold()
        if verification not in OPTISCALER_RUNTIME_VERIFICATION_STATES:
            raise ValueError("unsupported OptiScaler runtime verification state")
        object.__setattr__(self, "runtime_verification_status", verification)
        compatibility = str(self.optipatcher_compatibility or "unknown").strip().casefold()
        if compatibility not in OPTISCALER_COMPATIBILITY_STATES:
            raise ValueError("unsupported OptiPatcher compatibility state")
        object.__setattr__(self, "optipatcher_compatibility", compatibility)
        for name, allowed in (
            ("fsr_variant", OPTISCALER_FSR_VARIANTS),
            ("fsr4_preset", OPTISCALER_FSR4_PRESETS),
            ("log_mode", OPTISCALER_LOG_MODES),
        ):
            value = str(getattr(self, name) or "").strip().casefold()
            if value not in allowed:
                raise ValueError(f"unsupported OptiScaler {name}")
            object.__setattr__(self, name, value)
        shortcut = str(self.overlay_shortcut or "auto").strip()
        if shortcut.casefold() == "auto":
            shortcut = "auto"
        if shortcut not in OPTISCALER_OVERLAY_SHORTCUTS:
            raise ValueError("unsupported OptiScaler overlay shortcut")
        object.__setattr__(self, "overlay_shortcut", shortcut)
        sharpness = str(self.sharpness or "auto").strip().casefold()
        if sharpness != "auto":
            try:
                number = float(sharpness)
            except ValueError as error:
                raise ValueError("unsupported OptiScaler sharpness") from error
            if not 0.0 <= number <= 1.0:
                raise ValueError("unsupported OptiScaler sharpness")
            sharpness = f"{number:.2f}"
        object.__setattr__(self, "sharpness", sharpness)
        for name in ("fake_nvapi_mode", "dxgi_spoofing_mode"):
            value = str(getattr(self, name) or "auto").strip().casefold()
            if value not in {"auto", "enabled", "disabled"}:
                raise ValueError(f"unsupported {name}")
            object.__setattr__(self, name, value)
        for name in (
            "enabled",
            "fsr_agility_sdk_upgrade",
            "fsr4_watermark",
            "configuration_applied",
            "optipatcher_enabled",
            "reflex_emulation",
        ):
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
            app_id=validate_optiscaler_game_id(app_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app_id": self.app_id,
            "enabled": self.enabled,
            "backend": self.backend,
            "executable": self.executable,
            "install_directory": self.install_directory,
            "installed_version": self.installed_version,
            "channel": self.channel,
            "source_identity": self.source_identity,
            "fidelityfx_upscaler_version": self.fidelityfx_upscaler_version,
            "injection_dll": self.injection_dll,
            "proton_override": self.proton_override,
            "fsr4_mode": self.fsr4_mode,
            "effective_fsr4_mode": self.effective_fsr4_mode,
            "automatic_reason": self.automatic_reason,
            "fsr_agility_sdk_upgrade": self.fsr_agility_sdk_upgrade,
            "fsr4_watermark": self.fsr4_watermark,
            "dx11_upscaler": self.dx11_upscaler,
            "dx12_upscaler": self.dx12_upscaler,
            "vulkan_upscaler": self.vulkan_upscaler,
            "optipatcher_enabled": self.optipatcher_enabled,
            "fake_nvapi_mode": self.fake_nvapi_mode,
            "dxgi_spoofing_mode": self.dxgi_spoofing_mode,
            "reflex_emulation": self.reflex_emulation,
            "optipatcher_compatibility": self.optipatcher_compatibility,
            "fsr_variant": self.fsr_variant,
            "fsr4_preset": self.fsr4_preset,
            "sharpness": self.sharpness,
            "log_mode": self.log_mode,
            "overlay_shortcut": self.overlay_shortcut,
            "configuration_applied": self.configuration_applied,
            "runtime_verification_status": self.runtime_verification_status,
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
        app_id = validate_optiscaler_game_id(data.get("app_id", expected_app_id))
        if expected_app_id is not None and app_id != validate_optiscaler_game_id(expected_app_id):
            raise ValueError("OptiScaler profile AppID does not match its directory")
        raw = dict(data)
        schema_value = raw.get("schema_version", 0)
        try:
            schema = int(schema_value)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid OptiScaler profile schema") from error
        if schema < 0:
            raise ValueError("invalid OptiScaler profile schema")
        if schema in (0, 1, 2):
            raw["schema_version"] = OPTISCALER_SCHEMA_VERSION
        elif schema != OPTISCALER_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported OptiScaler profile schema: {schema}"
            )
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
    "OPTISCALER_CHANNELS",
    "OPTISCALER_FSR4_MODES",
    "OPTISCALER_FSR_VARIANTS",
    "OPTISCALER_PROXY_DLLS",
    "OPTISCALER_RUNTIME_VERIFICATION_STATES",
    "OPTISCALER_SCHEMA_VERSION",
    "OPTISCALER_SOURCE_IDENTITIES",
    "OPTISCALER_STATES",
    "OPTISCALER_UPSCALER_VALUES",
    "OptiScalerProfile",
]
