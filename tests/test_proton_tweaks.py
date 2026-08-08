from __future__ import annotations

from pathlib import Path

import pytest

from game_optimization_linux.models import (
    PROTON_TWEAK_REGISTRY,
    ProtonTweaksProfile,
)
from game_optimization_linux.services import (
    ProtonTweaksError,
    ProtonTweaksRepository,
)


def test_registry_contains_required_official_and_experimental_options() -> None:
    by_key = {item.environment_key: item for item in PROTON_TWEAK_REGISTRY}
    assert {
        "PROTON_USE_WINED3D", "PROTON_LOG", "PROTON_NO_ESYNC",
        "PROTON_NO_FSYNC", "PROTON_DISABLE_NVAPI", "PROTON_HIDE_NVIDIA_GPU",
        "PROTON_FORCE_LARGE_ADDRESS_AWARE", "PROTON_OLD_GL_STRING",
        "SteamDeck", "PROTON_FSR4_UPGRADE", "DXIL_SPIRV_CONFIG",
    } <= set(by_key)
    assert by_key["PROTON_USE_WINED3D"].category == "compatibility"
    assert by_key["SteamDeck"].official_proton is False
    assert by_key["PROTON_FSR4_UPGRADE"].hardware_dependent is True


def test_all_tweaks_are_disabled_by_default() -> None:
    profile = ProtonTweaksProfile.default("224760")
    assert profile.enabled_tweaks == ()
    assert profile.environment() == {}
    assert profile.optiscaler_fsr4_update is False


def test_repository_round_trip_is_per_appid_and_atomic(tmp_path: Path) -> None:
    repository = ProtonTweaksRepository(tmp_path / "games")
    first = repository.from_payload(
        "224760",
        {
            "toggles": {"proton_log": True, "no_fsync": True},
            "optiscalerFsr4Update": True,
        },
    )
    repository.save(first)

    loaded = repository.load("224760")
    other = repository.load("239350")

    assert loaded.enabled_tweaks == ("proton_log", "no_fsync")
    assert loaded.environment() == {"PROTON_LOG": "1", "PROTON_NO_FSYNC": "1"}
    assert loaded.optiscaler_fsr4_update is True
    assert other.environment() == {}
    assert not list(repository.path("224760").parent.glob("*.tmp"))


def test_unknown_tweak_is_rejected_instead_of_becoming_arbitrary_environment(
    tmp_path: Path,
) -> None:
    repository = ProtonTweaksRepository(tmp_path / "games")
    with pytest.raises(ProtonTweaksError, match="unknown Proton tweak"):
        repository.from_payload("224760", {"enabledTweaks": ["run_anything"]})


def test_hardware_dependent_options_never_assume_rdna3_from_gpu_name(
    tmp_path: Path,
) -> None:
    repository = ProtonTweaksRepository(tmp_path / "games")
    assert repository.hardware_state("fsr4_upgrade", "AMD Radeon RX 7900 XTX") == (
        "manual_verification_required"
    )
    assert repository.hardware_state("fsr4_upgrade", "NVIDIA") == "unsupported"
    assert repository.hardware_state("fsr4_upgrade", "") == "unknown"


def test_profile_rejects_non_boolean_optiscaler_setting() -> None:
    with pytest.raises(ValueError, match="boolean"):
        ProtonTweaksProfile.from_dict(
            {
                "schema_version": 1,
                "app_id": "224760",
                "enabled_tweaks": [],
                "optiscaler_fsr4_update": 1,
            }
        )
