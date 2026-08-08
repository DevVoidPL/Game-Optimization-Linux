"""Validated per-game Proton and compatibility environment options."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from .mangohud import validate_app_id


PROTON_TWEAKS_SCHEMA_VERSION = 1
PROTON_TWEAK_CATEGORIES = ("recommended", "compatibility", "debug", "experimental")


@dataclass(frozen=True, slots=True)
class ProtonTweakDefinition:
    id: str
    environment_key: str
    value: str
    category: str
    label: str
    description: str
    requirements: str = ""
    official_proton: bool = True
    hardware_dependent: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.environment_key:
            raise ValueError("Proton tweak identifiers cannot be empty")
        if self.category not in PROTON_TWEAK_CATEGORIES:
            raise ValueError("unsupported Proton tweak category")
        if not self.environment_key.replace("_", "A").isalnum():
            raise ValueError("invalid Proton environment key")
        if "\0" in self.value:
            raise ValueError("invalid Proton environment value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "environmentKey": self.environment_key,
            "value": self.value,
            "category": self.category,
            "label": self.label,
            "description": self.description,
            "requirements": self.requirements,
            "officialProton": self.official_proton,
            "hardwareDependent": self.hardware_dependent,
        }


PROTON_TWEAK_REGISTRY: tuple[ProtonTweakDefinition, ...] = (
    ProtonTweakDefinition(
        "use_wined3d", "PROTON_USE_WINED3D", "1", "compatibility",
        "Use WineD3D", "OpenGL compatibility fallback instead of DXVK. It is not a performance boost.",
    ),
    ProtonTweakDefinition(
        "proton_log", "PROTON_LOG", "1", "debug",
        "Proton logging", "Write a Proton debug log for the next game session.",
    ),
    ProtonTweakDefinition(
        "no_esync", "PROTON_NO_ESYNC", "1", "compatibility",
        "Disable esync", "Compatibility and debugging option that disables esync.",
    ),
    ProtonTweakDefinition(
        "no_fsync", "PROTON_NO_FSYNC", "1", "compatibility",
        "Disable fsync", "Compatibility and debugging option that disables fsync.",
    ),
    ProtonTweakDefinition(
        "disable_nvapi", "PROTON_DISABLE_NVAPI", "1", "compatibility",
        "Disable NVAPI", "Disable Proton NVAPI integration for compatibility testing.",
    ),
    ProtonTweakDefinition(
        "hide_nvidia_gpu", "PROTON_HIDE_NVIDIA_GPU", "1", "compatibility",
        "Hide NVIDIA GPU", "Hide the NVIDIA GPU identity from the Windows game.",
    ),
    ProtonTweakDefinition(
        "large_address_aware", "PROTON_FORCE_LARGE_ADDRESS_AWARE", "1", "compatibility",
        "Force large-address awareness", "Enable Proton's large-address-aware compatibility option.",
    ),
    ProtonTweakDefinition(
        "old_gl_string", "PROTON_OLD_GL_STRING", "1", "compatibility",
        "Use old OpenGL string", "Use Proton's older OpenGL version string for compatibility.",
    ),
    ProtonTweakDefinition(
        "steam_deck_spoof", "SteamDeck", "1", "experimental",
        "Steam Deck spoof", "Expose SteamDeck=1 to the game. This is not an official Proton variable.",
        official_proton=False,
    ),
    ProtonTweakDefinition(
        "fsr4_upgrade", "PROTON_FSR4_UPGRADE", "1", "experimental",
        "FSR 4 upgrade", "Request the experimental, hardware-dependent Proton FSR 4 upgrade path.",
        requirements="Requires a compatible Proton build, driver, game and GPU.",
        hardware_dependent=True,
    ),
    ProtonTweakDefinition(
        "rdna3_wmma_workaround", "DXIL_SPIRV_CONFIG", "wmma_rdna3_workaround", "experimental",
        "RDNA3 WMMA workaround", "Enable the DXIL-SPIRV RDNA3 WMMA workaround.",
        requirements="Use only after independently confirming RDNA3 and the required DXIL-SPIRV stack.",
        hardware_dependent=True,
    ),
)
PROTON_TWEAK_BY_ID = {definition.id: definition for definition in PROTON_TWEAK_REGISTRY}


@dataclass(frozen=True, slots=True)
class ProtonTweaksProfile:
    schema_version: int
    app_id: str
    enabled_tweaks: tuple[str, ...] = ()
    optiscaler_fsr4_update: bool = False
    updated_at: datetime = datetime.min.replace(tzinfo=UTC)

    def __post_init__(self) -> None:
        if self.schema_version != PROTON_TWEAKS_SCHEMA_VERSION:
            raise ValueError("unsupported Proton Tweaks profile schema")
        object.__setattr__(self, "app_id", validate_app_id(self.app_id))
        if not isinstance(self.optiscaler_fsr4_update, bool):
            raise ValueError("optiscaler_fsr4_update must be a boolean")
        unknown = set(self.enabled_tweaks).difference(PROTON_TWEAK_BY_ID)
        if unknown:
            raise ValueError("unknown Proton tweak: " + ", ".join(sorted(unknown)))
        ordered = tuple(
            definition.id
            for definition in PROTON_TWEAK_REGISTRY
            if definition.id in set(self.enabled_tweaks)
        )
        object.__setattr__(self, "enabled_tweaks", ordered)
        if self.updated_at.tzinfo is None:
            object.__setattr__(self, "updated_at", self.updated_at.replace(tzinfo=UTC))

    @classmethod
    def default(cls, app_id: object) -> "ProtonTweaksProfile":
        return cls(PROTON_TWEAKS_SCHEMA_VERSION, validate_app_id(app_id))

    def environment(self) -> dict[str, str]:
        enabled = set(self.enabled_tweaks)
        return {
            definition.environment_key: definition.value
            for definition in PROTON_TWEAK_REGISTRY
            if definition.id in enabled
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app_id": self.app_id,
            "enabled_tweaks": list(self.enabled_tweaks),
            "optiscaler_fsr4_update": self.optiscaler_fsr4_update,
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, expected_app_id: object | None = None
    ) -> "ProtonTweaksProfile":
        app_id = validate_app_id(data.get("app_id", expected_app_id))
        if expected_app_id is not None and app_id != validate_app_id(expected_app_id):
            raise ValueError("Proton Tweaks profile AppID does not match its directory")
        raw_enabled = data.get("enabled_tweaks", ())
        if not isinstance(raw_enabled, (list, tuple)) or not all(
            isinstance(value, str) for value in raw_enabled
        ):
            raise ValueError("enabled_tweaks must be a list of registry identifiers")
        updated_raw = str(data.get("updated_at") or "")
        try:
            updated_at = datetime.fromisoformat(updated_raw) if updated_raw else datetime.now(UTC)
        except ValueError as error:
            raise ValueError("invalid Proton Tweaks updated_at") from error
        return cls(
            schema_version=int(data.get("schema_version", PROTON_TWEAKS_SCHEMA_VERSION)),
            app_id=app_id,
            enabled_tweaks=tuple(raw_enabled),
            optiscaler_fsr4_update=data.get("optiscaler_fsr4_update", False),
            updated_at=updated_at,
        )


__all__ = [
    "PROTON_TWEAK_BY_ID", "PROTON_TWEAK_CATEGORIES", "PROTON_TWEAK_REGISTRY",
    "PROTON_TWEAKS_SCHEMA_VERSION", "ProtonTweakDefinition", "ProtonTweaksProfile",
]
