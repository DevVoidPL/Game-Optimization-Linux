from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from game_optimization_linux.models import (
    FilesystemType,
    Game,
    Launcher,
    OPTISCALER_SCHEMA_VERSION,
    OptiScalerProfile,
)
from game_optimization_linux.services import (
    OptiScalerIniCapabilities,
    OptiScalerProfileRepository,
    OptiScalerService,
    inspect_optiscaler_ini,
    inspect_optiscaler_ini_state,
    managed_ini_differences,
    managed_ini_updates,
    recommend_fsr4,
    update_ini_text,
)


BOOLEAN_FSR4_INI = """\
[Upscalers]
# Select Upscaler for Dx11 games

# fsr22 (native DX11), fsr31 (native DX11), xess (native DX11, Arc only), xess_12 (dx11on12), fsr21_12 (dx11on12), fsr22_12 (dx11on12), fsr31_12 (dx11on12, FSR4), dlss - Default (auto) is fsr22

Dx11Upscaler=auto

# Select Upscaler for Dx12 games

# xess, fsr21, fsr22, fsr31 (also for FSR4), dlss - Default (auto) is xess

Dx12Upscaler=auto

# Select Upscaler for Vulkan games

# fsr21 (native VK), fsr22 (native VK), fsr31 (native VK), xess (native VK), fsr21_12 (VKon12), fsr31_12 (VKon12, FSR4), dlss - Default (auto) is fsr22

VulkanUpscaler=auto

[FSR]
Fsr4Update=auto
Fsr4ForceEnableInt8=auto
FsrAgilitySDKUpgrade=auto
Fsr4EnableWatermark=auto
"""


MODEL_FSR4_INI = BOOLEAN_FSR4_INI.replace(
    "Fsr4ForceEnableInt8=auto", "Fsr4ForceModel=auto"
)


def test_managed_ini_differences_ignore_unknown_runtime_settings() -> None:
    text = """[FSR]
Fsr4Update=true
Fsr4EnableWatermark=false
RuntimeSetting=changed
"""
    settings = {"Fsr4Update": "true", "Fsr4EnableWatermark": False}

    assert managed_ini_differences(text, settings) == ()

    drifted = managed_ini_differences(
        text.replace("Fsr4EnableWatermark=false", "Fsr4EnableWatermark=true"),
        settings,
    )
    assert drifted == (
        {"key": "Fsr4EnableWatermark", "expected": "false", "actual": "true"},
    )


def _managed_updates(
    ini: str,
    *,
    mode: str,
    watermark: bool = False,
) -> dict[tuple[str, str], str]:
    return managed_ini_updates(
        fsr4_mode=mode,
        agility_sdk_upgrade=False,
        watermark=watermark,
        dx11_upscaler="auto",
        dx12_upscaler="auto",
        vulkan_upscaler="auto",
        capabilities=inspect_optiscaler_ini(ini),
    )


def _fsr4_capabilities(*, force_int8: bool = True) -> OptiScalerIniCapabilities:
    return OptiScalerIniCapabilities(
        fsr4_update=True,
        force_int8_style="boolean" if force_int8 else "none",
        agility_sdk_upgrade=True,
        watermark=True,
        dx11_values=("auto", "fsr31_12"),
        dx12_values=("auto", "fsr31"),
        vulkan_values=("auto", "fsr31_12"),
    )


def test_schema_1_profile_migrates_with_conservative_fsr4_defaults() -> None:
    profile = OptiScalerProfile.from_dict(
        {
            "schema_version": 1,
            "app_id": "224760",
            "enabled": True,
            "executable": "Binaries/Win64/Game.exe",
            "installed_version": "0.9.3",
            "injection_dll": "version.dll",
            "installation_state": "installed",
        }
    )

    assert profile.schema_version == OPTISCALER_SCHEMA_VERSION == 2
    assert profile.channel == "stable"
    assert profile.fsr4_mode == "automatic"
    assert profile.effective_fsr4_mode == "disabled"
    assert profile.fsr_agility_sdk_upgrade is False
    assert profile.fsr4_watermark is False
    assert profile.configuration_applied is False
    assert profile.runtime_verification_status == "not_verified"


def test_new_profile_defaults_do_not_enable_or_claim_fsr4() -> None:
    profile = OptiScalerProfile.default("224760")

    assert profile.fsr4_mode == "automatic"
    assert profile.effective_fsr4_mode == "disabled"
    assert profile.configuration_applied is False
    assert profile.fsr_agility_sdk_upgrade is False
    assert profile.fsr4_watermark is False
    assert profile.runtime_verification_status == "not_verified"


def test_inspection_uses_installed_ini_as_capability_authority() -> None:
    capabilities = inspect_optiscaler_ini(BOOLEAN_FSR4_INI)

    assert capabilities.supports_fsr4 is True
    assert capabilities.force_int8_style == "boolean"
    assert capabilities.supports_force_int8 is True
    assert capabilities.agility_sdk_upgrade is True
    assert capabilities.watermark is True
    assert capabilities.dx11_values == (
        "auto",
        "fsr22",
        "fsr31",
        "xess",
        "xess_12",
        "fsr21_12",
        "fsr22_12",
        "fsr31_12",
        "dlss",
    )
    assert capabilities.dx12_values == (
        "auto",
        "xess",
        "fsr21",
        "fsr22",
        "fsr31",
        "dlss",
    )
    assert capabilities.vulkan_values == (
        "auto",
        "fsr21",
        "fsr22",
        "fsr31",
        "xess",
        "fsr21_12",
        "fsr31_12",
        "dlss",
    )


def test_inspection_reads_current_force_model_and_ffx_backend_prose() -> None:
    current_ini = """\
[Upscalers]
; Select Upscaler for Dx11 games

; fsr22 (native DX11), ffx_12 (FSR 2.3; 3.1; 4.x), dlss - Default (auto) is fsr22

Dx11Upscaler=auto
; Select Upscaler for Dx12 games

; xess, ffx (FSR 2.3; 3.1; 4.x), dlss
; Default (auto) is DLSS when capable gpu, FSR4 when capable gpu, XeSS otherwise

Dx12Upscaler=auto
; Select Upscaler for Vulkan games

; fsr21 (native VK), fsr22 (native VK), ffx (native FSR 2.3; 3.1), xess (native VK), ffx_12 (FSR 2.3; 3.1; 4.x), dlss - Default (auto) is fsr22

VulkanUpscaler=auto
[FSR]
Fsr4ForceModel=auto
FsrAgilitySDKUpgrade=auto
Fsr4EnableWatermark=auto
"""

    capabilities = inspect_optiscaler_ini(current_ini)

    assert capabilities.supports_fsr4 is True
    assert capabilities.fsr4_update is False
    assert capabilities.force_int8_style == "model"
    assert capabilities.dx11_values == ("auto", "fsr22", "ffx_12", "dlss")
    assert capabilities.dx12_values == ("auto", "xess", "ffx", "dlss")
    assert capabilities.vulkan_values == (
        "auto",
        "fsr21",
        "fsr22",
        "ffx",
        "xess",
        "ffx_12",
        "dlss",
    )
    updates = managed_ini_updates(
        fsr4_mode="force_int8",
        agility_sdk_upgrade=False,
        watermark=True,
        dx11_upscaler="ffx_12",
        dx12_upscaler="ffx",
        vulkan_upscaler="ffx_12",
        capabilities=capabilities,
    )
    assert ("FSR", "Fsr4Update") not in updates
    assert updates[("FSR", "Fsr4ForceModel")] == "2"
    with pytest.raises(ValueError, match="cannot explicitly disable"):
        managed_ini_updates(
            fsr4_mode="disabled",
            agility_sdk_upgrade=False,
            watermark=False,
            dx11_upscaler="auto",
            dx12_upscaler="auto",
            vulkan_upscaler="auto",
            capabilities=capabilities,
        )


def test_normal_fsr4_writes_current_boolean_generation_keys() -> None:
    updates = _managed_updates(BOOLEAN_FSR4_INI, mode="normal")

    assert updates[("FSR", "Fsr4Update")] == "true"
    assert updates[("FSR", "Fsr4ForceEnableInt8")] == "false"
    assert ("FSR", "Fsr4ForceModel") not in updates


def test_force_int8_writes_current_boolean_generation_keys() -> None:
    updates = _managed_updates(BOOLEAN_FSR4_INI, mode="force_int8")

    assert updates[("FSR", "Fsr4Update")] == "true"
    assert updates[("FSR", "Fsr4ForceEnableInt8")] == "true"


@pytest.mark.parametrize(
    ("mode", "expected_model"),
    (("automatic", "auto"), ("normal", "0"), ("force_int8", "2"), ("disabled", "0")),
)
def test_force_model_generation_uses_upstream_model_values(
    mode: str,
    expected_model: str,
) -> None:
    capabilities = inspect_optiscaler_ini(MODEL_FSR4_INI)
    updates = _managed_updates(MODEL_FSR4_INI, mode=mode)

    assert capabilities.force_int8_style == "model"
    assert updates[("FSR", "Fsr4ForceModel")] == expected_model
    assert ("FSR", "Fsr4ForceEnableInt8") not in updates


@pytest.mark.parametrize(("enabled", "expected"), ((True, "true"), (False, "auto")))
def test_watermark_can_be_enabled_and_disabled(
    enabled: bool,
    expected: str,
) -> None:
    updates = _managed_updates(
        BOOLEAN_FSR4_INI,
        mode="normal",
        watermark=enabled,
    )

    assert updates[("FSR", "Fsr4EnableWatermark")] == expected


def test_effective_ini_state_reads_runtime_modified_watermark_and_backends() -> None:
    state = inspect_optiscaler_ini_state(
        BOOLEAN_FSR4_INI.replace("Fsr4Update=auto", "Fsr4Update=true")
        .replace("Fsr4ForceEnableInt8=auto", "Fsr4ForceEnableInt8=true")
        .replace("Fsr4EnableWatermark=auto", "Fsr4EnableWatermark=true")
        .replace("Dx12Upscaler=auto", "Dx12Upscaler=fsr31")
    )

    assert state.fsr4_mode == "force_int8"
    assert state.watermark == "true"
    assert state.dx12_upscaler == "fsr31"


def test_managed_ini_update_preserves_unknown_entries_and_removes_duplicates() -> None:
    original = """\
; user comment
[OptiScaler]
UserPluginSetting=keep me

[FSR]
Fsr4Update=auto
UserFsrSetting=also keep me
fsr4update=false
Fsr4EnableWatermark=auto
"""
    updates = {
        ("FSR", "Fsr4Update"): "true",
        ("FSR", "Fsr4EnableWatermark"): "true",
    }

    once = update_ini_text(original, updates)
    twice = update_ini_text(once, updates)

    assert once == twice
    assert "; user comment" in twice
    assert "UserPluginSetting=keep me" in twice
    assert "UserFsrSetting=also keep me" in twice
    assert twice.casefold().count("fsr4update=") == 1
    assert twice.casefold().count("fsr4enablewatermark=") == 1
    assert "Fsr4Update=true" in twice
    assert "Fsr4EnableWatermark=true" in twice


@pytest.mark.parametrize(
    ("gpu", "api", "capability", "mode", "force_available"),
    (
        ("AMD Radeon RX 9070 XT", "DirectX 12", "native", "normal", True),
        ("AMD Radeon RX 7900 XTX", "DirectX 12", "normal", "normal", True),
        ("AMD Radeon RX 6600 XT", "DirectX 12", "force_int8", "force_int8", True),
        ("NVIDIA GeForce RTX 4070", "DirectX 12", "unknown", "automatic", True),
        ("Intel Arc B580", "DirectX 12", "unknown", "automatic", True),
        ("AMD Radeon RX 580", "DirectX 12", "unsupported", "automatic", False),
        ("NVIDIA GeForce GTX 1080", "DirectX 12", "unsupported", "automatic", False),
        ("Unknown", "DirectX 12", "unknown", "automatic", True),
    ),
)
def test_gpu_recommendations_are_conservative(
    gpu: str,
    api: str,
    capability: str,
    mode: str,
    force_available: bool,
) -> None:
    recommendation = recommend_fsr4(gpu, api, _fsr4_capabilities())

    assert recommendation.capability == capability
    assert recommendation.recommended_mode == mode
    assert recommendation.force_int8_available is force_available


@pytest.mark.parametrize(
    "gpu",
    ("AMD Radeon RX 9070 XT", "AMD Radeon RX 7900 XTX", "AMD Radeon RX 6600 XT"),
)
def test_unknown_graphics_api_never_auto_selects_fsr4(gpu: str) -> None:
    recommendation = recommend_fsr4(gpu, "Unknown", _fsr4_capabilities())

    assert recommendation.capability == "unknown"
    assert recommendation.recommended_mode == "automatic"


def test_missing_installed_fsr4_capability_is_reported_unsupported() -> None:
    recommendation = recommend_fsr4(
        "AMD Radeon RX 9070 XT",
        "DirectX 12",
        OptiScalerIniCapabilities(False, "none", False, False, (), (), ()),
    )

    assert recommendation.capability == "unsupported"
    assert recommendation.recommended_mode == "automatic"


def test_installed_fsr4_assets_are_not_runtime_verification(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    executable = game_root / "Binaries" / "Win64" / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic executable")
    game = Game(
        id="steam-224760",
        steam_app_id="224760",
        name="Test Game",
        launcher=Launcher.STEAM,
        install_path=game_root,
        logical_size_gb=0.01,
        physical_size_gb=0.01,
        filesystem=FilesystemType.EXT4,
        compression_available=False,
    )
    archive_path = tmp_path / "OptiScaler_v0.9.4.zip"
    with ZipFile(archive_path, "w") as archive:
        root = "OptiScaler_v0.9.4/"
        archive.writestr(root + "OptiScaler.dll", b"proxy")
        archive.writestr(root + "OptiScaler.ini", BOOLEAN_FSR4_INI)
        archive.writestr(
            root + "amd_fidelityfx_upscaler_dx12.dll",
            b"synthetic FidelityFX SDK payload",
        )
    service = OptiScalerService(
        profile_repository=OptiScalerProfileRepository(tmp_path / "profiles"),
        data_root=tmp_path / "data",
        process_detector=lambda _path: (),
    )

    service.install(
        game,
        archive_path,
        source_identity="official_optiscaler",
        fidelityfx_upscaler_version="4.1.1",
    )
    status = service.status(game)

    assert status["installed"] is True
    assert status["fsr4AssetsInstalled"] is True
    assert status["fidelityFxUpscalerVersion"] == "4.1.1"
    assert status["runtimeVerificationStatus"] == "not_verified"
    assert status["runtimeVerificationLabel"] == "Runtime verification required"
    assert status["runtimeVerified"] is False
