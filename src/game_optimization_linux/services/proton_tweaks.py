"""Atomic per-AppID Proton Tweaks persistence and registry presentation."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Mapping

from game_optimization_linux.config import GAMES_CONFIG_DIR
from game_optimization_linux.models.proton_tweaks import (
    PROTON_TWEAK_REGISTRY,
    ProtonTweaksProfile,
)

from .mangohud import _atomic_write


PROTON_TWEAKS_FILE_NAME = "proton-tweaks.json"


class ProtonTweaksError(RuntimeError):
    pass


class ProtonTweaksRepository:
    def __init__(self, root: Path = GAMES_CONFIG_DIR) -> None:
        self.root = Path(root)

    def path(self, app_id: object) -> Path:
        return self.root / str(app_id) / PROTON_TWEAKS_FILE_NAME

    def load(self, app_id: object) -> ProtonTweaksProfile:
        default = ProtonTweaksProfile.default(app_id)
        path = self.path(default.app_id)
        if not path.exists():
            return default
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProtonTweaksError(f"could not read Proton Tweaks profile: {error}") from error
        if not isinstance(raw, Mapping):
            raise ProtonTweaksError("Proton Tweaks profile must be a JSON object")
        try:
            return ProtonTweaksProfile.from_dict(raw, expected_app_id=default.app_id)
        except (TypeError, ValueError) as error:
            raise ProtonTweaksError(str(error)) from error

    def save(self, profile: ProtonTweaksProfile) -> Path:
        path = self.path(profile.app_id)
        _atomic_write(
            path,
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        return path

    def from_payload(
        self, app_id: object, payload: Mapping[str, Any]
    ) -> ProtonTweaksProfile:
        current = self.load(app_id)
        enabled: list[str]
        toggles = payload.get("toggles")
        if isinstance(toggles, Mapping):
            enabled = [
                definition.id
                for definition in PROTON_TWEAK_REGISTRY
                if toggles.get(definition.id) is True
            ]
        else:
            raw_enabled = payload.get("enabledTweaks", current.enabled_tweaks)
            if not isinstance(raw_enabled, (list, tuple)) or not all(
                isinstance(value, str) for value in raw_enabled
            ):
                raise ProtonTweaksError("enabledTweaks must be a list")
            enabled = list(raw_enabled)
        fsr4 = payload.get(
            "optiscalerFsr4Update", current.optiscaler_fsr4_update
        )
        try:
            return ProtonTweaksProfile(
                schema_version=current.schema_version,
                app_id=current.app_id,
                enabled_tweaks=tuple(enabled),
                optiscaler_fsr4_update=fsr4,
                updated_at=datetime.now(UTC),
            )
        except (TypeError, ValueError) as error:
            raise ProtonTweaksError(str(error)) from error

    @staticmethod
    def hardware_state(definition_id: str, gpu_vendor: str = "") -> str:
        if definition_id not in {"fsr4_upgrade", "rdna3_wmma_workaround"}:
            return "not_applicable"
        vendor = str(gpu_vendor or "").casefold()
        if not vendor:
            return "unknown"
        if "amd" not in vendor and "advanced micro devices" not in vendor:
            return "unsupported"
        # A vendor or marketing name alone is not sufficient proof of RDNA3,
        # driver, Proton and game compatibility. Keep the decision manual.
        return "manual_verification_required"

    def to_qml(
        self, profile: ProtonTweaksProfile, *, gpu_vendor: str = ""
    ) -> dict[str, Any]:
        enabled = set(profile.enabled_tweaks)
        entries: list[dict[str, Any]] = []
        for definition in PROTON_TWEAK_REGISTRY:
            entry = definition.to_dict()
            entry.update(
                {
                    "enabled": definition.id in enabled,
                    "hardwareState": self.hardware_state(
                        definition.id, gpu_vendor
                    ),
                }
            )
            entries.append(entry)
        return {
            "success": True,
            "appId": profile.app_id,
            "schemaVersion": profile.schema_version,
            "enabledTweaks": list(profile.enabled_tweaks),
            "optiscalerFsr4Update": profile.optiscaler_fsr4_update,
            "entries": entries,
            "environment": profile.environment(),
            "profilePath": str(self.path(profile.app_id)),
            "updatedAt": profile.updated_at.astimezone(UTC).isoformat(),
        }


__all__ = [
    "PROTON_TWEAKS_FILE_NAME", "ProtonTweaksError", "ProtonTweaksRepository",
]
