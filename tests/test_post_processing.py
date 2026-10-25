"""Post-processing ownership, rollback and configuration on disposable games."""
from dataclasses import replace
from pathlib import Path
import struct

import pytest

from game_optimization_linux.models import Game, Launcher, FilesystemType
from game_optimization_linux.services.optiscaler import OptiScalerService, OptiScalerProfileRepository
from game_optimization_linux.services.post_processing import PostProcessingService, inspect_binary, load_profile, launch_environment, MANAGED


def pe(bits=64):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, 0x8664 if bits == 64 else 0x14c)
    return data


class Artifacts:
    def shaders(self, target):
        (target / "Shaders/SweetFX").mkdir(parents=True)
        (target / "Textures/SweetFX").mkdir(parents=True)
        (target / "Shaders/SweetFX/Vibrance.fx").write_text("fixture shader")

    def reshade(self, bits, target):
        target.write_bytes(bytes(pe(bits)))

    def vkbasalt(self, bits, target):
        target.write_bytes(b"fixture layer")


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    exe = root / "Game.exe"
    exe.write_bytes(pe())
    game = Game(id="steam-123", steam_app_id="123", name="Game", launcher=Launcher.STEAM,
                install_path=root, executable_path="Game.exe", logical_size_gb=0.01,
                physical_size_gb=0.01, filesystem=FilesystemType.EXT4, compression_available=False)
    opti = OptiScalerService(profile_repository=OptiScalerProfileRepository(tmp_path / "profiles"),
                            data_root=tmp_path / "data", process_detector=lambda _root: [])
    return PostProcessingService(opti, artifacts=Artifacts()), game


@pytest.mark.parametrize("backend", ["reshade", "vkbasalt"])
def test_install_configure_remove_preserves_custom_files(env, backend):
    service, game = env
    unrelated = game.install_path / "custom-mod.bin"
    unrelated.write_bytes(b"keep")
    result = service.apply(game, dict(backend=backend, api="d3d11", effects=["curves", "vibrance"], parameters={"Vibrance": .3}))
    assert result["installed"] and not result["runtimeLoaded"] and not result["effectsVerified"]
    managed = game.install_path / MANAGED
    (managed / "CustomPreset.ini").write_text("custom user data")
    result = service.apply(game, {"effects": ["vibrance", "curves"], "parameters": {"Vibrance": -.5}})
    config = (managed / ("Preset.ini" if backend == "reshade" else "vkBasalt.conf")).read_text()
    assert "Vibrance=-0.5" in config.replace(" ", "")
    if backend == "reshade":
        assert "KeyEffects=36,1,0,0" in (game.install_path / "ReShade.ini").read_text()
    state = service.remove(game)
    assert state["backend"] == "off" and not managed.exists()
    assert (Path(state["preservedDirectory"]) / MANAGED / "CustomPreset.ini").read_text() == "custom user data"
    assert launch_environment(game.profile_key, service.root) == {}
    assert unrelated.read_bytes() == b"keep"


def test_existing_loader_is_never_overwritten(env):
    service, game = env
    loader = game.install_path / "d3d11.dll"
    loader.write_bytes(b"DXVK/custom mod")
    with pytest.raises(ValueError, match="preserved"):
        service.apply(game, dict(backend="reshade", api="d3d11"))
    assert loader.read_bytes() == b"DXVK/custom mod"
    assert not (game.install_path / MANAGED).exists()


def test_failed_second_rename_rolls_back(env, monkeypatch):
    service, game = env
    move = service._move
    def failing(source, target):
        if Path(target).name == "dxgi.dll": raise OSError("fixture rename failure")
        move(source, target)
    monkeypatch.setattr(service, "_move", failing)
    with pytest.raises(OSError, match="fixture"):
        service.apply(game, dict(backend="reshade", api="d3d11"))
    assert load_profile(game.profile_key, service.root)["backend"] == "off"
    assert not (game.install_path / MANAGED).exists()
    assert not service._journal(game.profile_key).exists()


def test_bitness_and_preset_validation(env, tmp_path):
    service, game = env
    (game.install_path / "Game.exe").write_bytes(pe(32))
    assert inspect_binary(game.install_path / "Game.exe")["bits"] == 32
    service.apply(game, dict(backend="reshade", api="d3d9"))
    assert inspect_binary(game.install_path / "d3d9.dll")["bits"] == 32
    preset = tmp_path / "preset.ini"
    preset.write_text("Techniques=Vibrance@Vibrance.fx,Curves@Curves.fx\n[Vibrance.fx]\nVibrance=0.4\n")
    assert service.import_preset(game, preset)["effects"] == ["vibrance", "curves"]
    preset.write_text("Techniques=MXAO@MXAO.fx\n")
    with pytest.raises(ValueError, match="unsupported shader"):
        service.import_preset(game, preset)
    with pytest.raises(ValueError, match="parameter"):
        service.apply(game, {"parameters": {"Vibrance": float("nan")}})
    with pytest.raises(ValueError, match="Turn Off"):
        service.apply(game, {"api": "d3d11"})


def test_tampered_module_blocks_launch_and_removal(env):
    service, game = env
    service.apply(game, dict(backend="reshade", api="d3d11"))
    (game.install_path / "dxgi.dll").write_bytes(b"another mod")
    assert service.status(game)["statusState"] == "error"
    with pytest.raises(ValueError, match="preserved"):
        service.remove(game)


def test_backend_switch_is_explicit_and_metadata_cannot_be_injected(env):
    service, game = env
    result = service.apply(game, dict(backend="vkbasalt", api="vulkan", directory="/tmp", moduleHash="bad"))
    assert result["directory"] == str(game.install_path)
    with pytest.raises(ValueError, match="before switching"):
        service.apply(game, {"backend": "reshade"})


def test_upscaler_and_anticheat_are_preserved(env):
    service, game = env
    previous = service.optiscaler.profile_repository.load(game.profile_key)
    service.optiscaler.profile_repository.save(replace(previous, enabled=True, installation_state="installed"))
    loader = game.install_path / "dxgi.dll"
    loader.write_bytes(b"working upscaler")
    (game.install_path / "EasyAntiCheat").mkdir()
    with pytest.raises(ValueError, match="Loader conflict"):
        service.apply(game, {"backend": "reshade", "api": "d3d11"})
    assert service.apply(game, {"backend": "vkbasalt", "api": "d3d11"})["antiCheat"]
    service.remove(game)
    assert loader.read_bytes() == b"working upscaler"
    assert service.optiscaler.profile_repository.load(game.profile_key).enabled


def test_missing_shader_and_unsafe_lut_are_reported(env, tmp_path):
    service, game = env
    service.apply(game, {"backend": "vkbasalt", "api": "vulkan"})
    bad = tmp_path / "bad.cube"
    bad.write_text("LUT_3D_SIZE 64\n0 0 0\n")
    with pytest.raises(ValueError, match="CUBE"):
        service.import_lut(game, bad)
    (game.install_path / MANAGED / "Shaders/SweetFX/Vibrance.fx").unlink()
    assert service.status(game)["statusState"] == "error"


def test_relocated_game_cannot_modify_the_previous_install_directory(env, tmp_path):
    service, game = env
    service.apply(game, {"backend": "reshade", "api": "d3d11"})
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    (relocated / "Game.exe").write_bytes(pe())
    with pytest.raises(ValueError, match="directory|path"):
        service.remove(replace(game, install_path=relocated))
    assert (game.install_path / "dxgi.dll").is_file()


def test_neutral_vkbasalt_profile_does_not_load_default_cas(env):
    service, game = env
    service.apply(game, {"backend": "vkbasalt", "api": "vulkan", "effects": []})
    assert launch_environment(game.profile_key, service.root) == {"ENABLE_VKBASALT": "0", "DISABLE_VKBASALT": "1"}


def test_status_is_read_only_and_matches_consumed_configuration(env):
    service, game = env
    service.apply(game, {"backend": "vkbasalt", "api": "d3d12", "effects": ["tonemap"]})
    before = {str(p): p.read_bytes() for root in (game.install_path, service.root) for p in root.rglob("*") if p.is_file()}
    state = service.status(game)
    assert state["diagnostics"]["configMatches"]
    assert state["diagnostics"]["configEffects"] == "tonemap"
    assert before == {str(p): p.read_bytes() for root in (game.install_path, service.root) for p in root.rglob("*") if p.is_file()}
    config = game.install_path / MANAGED / "vkBasalt.conf"
    config.write_text(config.read_text().replace("Exposure = 0", "Exposure = -1"))
    state = service.status(game)
    assert state["statusState"] == "error" and "Exposure" in state["error"]


def test_helper_log_truncation_does_not_hide_loaded_game_module(env):
    from types import SimpleNamespace
    service, game = env
    service.apply(game, {"backend": "vkbasalt", "api": "d3d12", "effects": ["tonemap"]})
    (game.install_path / MANAGED / "vkBasalt.log").write_text("vkBasalt info: helper initialisation\n")
    service.host = SimpleNamespace(game_process_modules=lambda name: [dict(processId=321, modules=[str(game.install_path / MANAGED / "libvkbasalt.so")])])
    state = service.status(game)
    assert state["runtimeLoaded"] and not state["effectsVerified"]
    assert state["runtimeEvidence"]["processId"] == 321


def test_api_detection_and_loader_conflicts_are_separate(env):
    service, game = env
    state = service.status(game)
    assert state["apiDetectionInconclusive"] and not state["conflicts"]
    service.apply(game, {"backend": "reshade", "api": "d3d12"})
    assert service.status(game)["installed"]


def test_missing_recorded_upscaler_loader_is_not_reported_as_existing_conflict(env):
    service, game = env
    profile = service.optiscaler.profile_repository.load(game.profile_key)
    service.optiscaler.profile_repository.save(replace(profile, enabled=True, installation_state="installed"))
    state = service.status(game)
    assert state["upscalerLoaderMissing"] and not state["loaderConflicts"]
    with pytest.raises(ValueError, match="requires verification"):
        service.apply(game, {"backend": "reshade", "api": "d3d12"})


def test_smaa_integer_parameters_keep_their_upstream_type(env):
    service, game = env
    state = service.apply(game, {"backend": "vkbasalt", "api": "d3d12", "effects": ["smaa"], "parameters": {"MaxSearchSteps": 23.7}})
    assert state["parameters"]["MaxSearchSteps"] == 24
    assert "smaaMaxSearchSteps = 24\n" in (game.install_path / MANAGED / "vkBasalt.conf").read_text()
