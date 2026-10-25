"""DLSS Enabler backend: reversible switch with a managed OptiScaler v10 + Lite.

Fixtures only (no real game, no network).  Pinned hashes are replaced by
fixture payloads with the same file layout as DLSS Enabler v0.9.4.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.services import fsr_variants as fv
from game_optimization_linux.services.dlss_enabler import DlssEnablerService
from game_optimization_linux.services.dlss_enabler_pins import (
    RAW_FILES,
    UPSCALER_ARTIFACT,
)
from game_optimization_linux.services.fsr_variants import PinnedArchive, PinnedFile
from game_optimization_linux.services.optimization_runtime import (
    OptimizationLaunchPlanner,
    RuntimeToolAvailability,
)
from game_optimization_linux.services.optiscaler import (
    OptiScalerConflictError,
    OptiScalerError,
)

from test_fsr_variants import _artifact_zip, _configure, _h, _install, env  # noqa: F401

AMD = "AMD Radeon RX 6650 XT"
UP = {"OptiScaler.dll": b"opti 0.9.4", "OptiScaler.ini": b"[Upscalers]\n",
      "libxess.dll": b"xess", "amd_fidelityfx_dx12.dll": b"ffx12",
      "amd_fidelityfx_vk.dll": b"ffxvk"}


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
    }


@pytest.fixture
def enabler(env, tmp_path: Path):  # noqa: F811
    service, variants, game, bin_dir, opener, _tmp = env
    native_dlss = game.install_path / "Engine/Plugins/Runtime/Nvidia/DLSS/Binaries/ThirdParty/Win64/nvngx_dlss.dll"
    native_dlss.parent.mkdir(parents=True, exist_ok=True)
    native_dlss.write_bytes(b"game native DLSS 3.1.13")
    raw: dict[str, PinnedArchive] = {}
    for key in RAW_FILES:
        data = f"enabler {key}".encode()
        url = f"https://example.invalid/de/{key}"
        raw[key] = PinnedArchive(key, url, len(data), _h(data))
        opener.payloads[url] = data
    up_zip = _artifact_zip({f"OptiScaler-0.9.4/{n}": d for n, d in UP.items()})
    up_url = "https://example.invalid/opti094.zip"
    opener.payloads[up_url] = up_zip
    upscaler = replace(
        UPSCALER_ARTIFACT,
        archive=PinnedArchive("o94", up_url, len(up_zip), _h(up_zip)),
        files=tuple(PinnedFile(n, _h(d), len(d)) for n, d in UP.items()),
    )
    de = DlssEnablerService(
        service, fv.FsrArtifactClient(tmp_path / "de-cache", opener=opener),
        raw_files=raw, upscaler=upscaler,
    )
    return de, service, variants, game, bin_dir


def _lite(env) -> None:  # noqa: F811
    service, variants, game, _bin, _o, _t = env
    _install(env)
    variants.install_driver(game)
    _configure(service, game, "lite", sharpness="0.40")


def test_lite_to_enabler_to_lite_restores_files_settings_and_mods(enabler, env) -> None:  # noqa: F811
    de, service, variants, game, bin_dir = enabler
    _lite(env)
    (bin_dir / "ReShade.ini").write_text("user mod")
    (bin_dir / "version.dll").write_bytes(b"other mod loader")
    # The foreign version.dll is reported by OptiScaler as another loader.
    verify_before = service.verify(game).state
    files_before = _snapshot(bin_dir)
    profile_path = service.profile_repository.path("224760")
    profile_before = profile_path.read_bytes()

    status = de.enable(game, gpu=AMD)
    assert status["active"] and status["amdIntel"] and status["parkedOptiScaler"]
    assert (bin_dir / "version.dll").read_bytes() == b"other mod loader"
    assert (bin_dir / "dlss-enabler.dll").read_bytes() == b"enabler enabler_main"
    assert (bin_dir / "dlss-enabler-upscaler.dll").read_bytes() == UP["OptiScaler.dll"]
    assert (bin_dir / "dxgi.dll").read_bytes() == b"enabler nv_dxgi"
    assert (bin_dir / "_nvngx.dll").read_bytes() == b"game native DLSS 3.1.13"
    assert (bin_dir / "dlss-finder.exe").read_bytes() == b"enabler dlss_finder"
    assert not (bin_dir / "OptiScaler" / "amdvkffx64.dll").exists()
    assert (bin_dir / "ReShade.ini").read_text() == "user mod"
    profile = service.profile_repository.load("224760")
    assert profile.backend == "dlss_enabler"
    assert profile.proton_override == "dxgi=n,b"
    assert status["installed"] and status["selected"]
    assert not status["runtimeVerified"] and status["statusState"] == "selected"
    assert status["moduleVersion"] == "3.00.000.0"
    assert status["bundledOptiScalerVersion"] == "0.9.4"
    plan = OptimizationLaunchPlanner().build(
        GameOptimizationProfile.default("224760"), ["game"],
        gamemode=RuntimeToolAvailability("GameMode", False),
        gamescope=RuntimeToolAvailability("Gamescope", False),
        optiscaler_override=profile.proton_override,
        existing_wine_overrides="d3dcompiler_47=n",
    )
    assert plan.environment["WINEDLLOVERRIDES"] == "d3dcompiler_47=n;dxgi=n,b"
    with pytest.raises(OptiScalerError):
        service.set_backend(game, "none")

    (bin_dir / "nvngx.ini").write_text("[Upscalers]\nedited=1\n")
    de.disable(game)
    assert _snapshot(bin_dir) == files_before
    assert profile_path.read_bytes() == profile_before
    assert service.verify(game).state == verify_before
    assert variants.status(game)["driverInstalled"] is True
    kept = de._root("224760") / "preserved" / "nvngx.ini"
    assert kept.read_text().endswith("edited=1\n")
    assert de.status(game)["phase"] == "inactive"


@pytest.mark.parametrize("crash_at", [1, 4, 9, 14, 19])
def test_failed_switch_rolls_back_to_lite(enabler, env, crash_at: int) -> None:  # noqa: F811
    """Interrupt at a park move, an original restore or an Enabler copy."""

    de, service, variants, game, bin_dir = enabler
    _lite(env)
    files_before = _snapshot(bin_dir)
    profile_before = service.profile_repository.path("224760").read_bytes()
    real_step = de._run_step
    calls = {"n": 0}

    def flaky(bases, step) -> None:
        calls["n"] += 1
        if calls["n"] == crash_at:
            raise KeyboardInterrupt("interrupted")
        real_step(bases, step)

    de._run_step = flaky
    with pytest.raises(KeyboardInterrupt):
        de.enable(game, gpu=AMD)
    de._run_step = real_step
    assert calls["n"] == crash_at
    assert _snapshot(bin_dir) == files_before
    assert service.profile_repository.path("224760").read_bytes() == profile_before
    assert de.status(game)["phase"] == "inactive"
    assert variants.status(game)["driverInstalled"] is True


def test_hard_crash_mid_switch_is_recovered_from_journal(enabler, env) -> None:  # noqa: F811
    """Process killed mid-switch: no in-process rollback ran; recover() does it."""

    de, service, _variants, game, bin_dir = enabler
    _lite(env)
    files_before = _snapshot(bin_dir)
    profile_before = service.profile_repository.path("224760").read_bytes()
    real_mover, real_finish = de._mover, de._finish_undo
    calls = {"n": 0}

    def crash(source: Path, target: Path) -> None:
        calls["n"] += 1
        real_mover(source, target)
        if calls["n"] == 4:
            raise SystemExit("killed")

    def no_rollback(*_args):
        raise SystemExit("killed before rollback")

    de._mover, de._finish_undo = crash, no_rollback
    with pytest.raises(SystemExit):
        de.enable(game, gpu=AMD)
    de._mover, de._finish_undo = real_mover, real_finish
    journal = json.loads(de._state_path("224760").read_text())
    assert journal["phase"] in {"switching_to_enabler", "rollback_required"}
    assert de.status(game)["active"] is False
    assert de.recover(game) is True
    assert _snapshot(bin_dir) == files_before
    assert service.profile_repository.path("224760").read_bytes() == profile_before
    assert service.verify(game).state == "verified"


def test_interrupted_switch_back_is_finished_by_recover(enabler, env) -> None:  # noqa: F811
    de, service, _variants, game, bin_dir = enabler
    _lite(env)
    files_before = _snapshot(bin_dir)
    de.enable(game, gpu=AMD)
    real = de._mover
    calls = {"n": 0}

    def crash(source: Path, target: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise SystemExit("killed")
        real(source, target)

    de._mover = crash
    with pytest.raises(SystemExit):
        de.disable(game)
    de._mover = real
    assert de.status(game)["phase"] == "switching_to_optiscaler"
    assert de.recover(game) is True
    assert _snapshot(bin_dir) == files_before
    assert service.verify(game).state == "verified"


def test_modified_managed_file_blocks_switch_without_changes(enabler, env) -> None:  # noqa: F811
    de, _service, _variants, game, bin_dir = enabler
    _install(env)
    (bin_dir / "dxgi.dll").write_bytes(b"tampered")  # managed OptiScaler proxy
    before = _snapshot(bin_dir)
    with pytest.raises(OptiScalerConflictError):
        de.enable(game, gpu=AMD)
    assert _snapshot(bin_dir) == before
    assert de.status(game)["phase"] == "inactive"


def test_changed_enabler_binary_blocks_switch_back(enabler, env) -> None:  # noqa: F811
    de, _service, _variants, game, bin_dir = enabler
    _lite(env)
    de.enable(game, gpu=AMD)
    (bin_dir / "dlss-enabler.dll").write_bytes(b"replaced by user")
    assert "dlss-enabler.dll" in de.status(game)["drift"]
    with pytest.raises(OptiScalerConflictError):
        de.disable(game)
    assert de.status(game)["active"] is True


def test_nvidia_without_optiscaler_installs_and_removes_cleanly(enabler) -> None:
    de, service, _variants, game, bin_dir = enabler
    service.remember_executable(game, "Bin/Game.exe")
    before = _snapshot(bin_dir)
    status = de.enable(game, gpu="NVIDIA GeForce RTX 4070")
    assert status["active"] and not status["amdIntel"]
    assert not (bin_dir / "dxgi.dll").exists()
    assert service.profile_repository.load("224760").proton_override == "version=n,b"
    de.disable(game)
    assert _snapshot(bin_dir) == before
    assert not (bin_dir / "licenses").exists()
    assert not service.profile_repository.path("224760").read_text().count("dlss_enabler")


def test_pinned_layout_matches_upstream_installer() -> None:
    from game_optimization_linux.services.dlss_enabler_pins import build_enabler_files

    files = {item.target: item for item in build_enabler_files(RAW_FILES, UPSCALER_ARTIFACT)}
    assert files["version.dll"].sha256.startswith("a478031d")
    assert files["dlss-enabler-upscaler.dll"].sha256.startswith("fbfb6676")
    assert files["nvngx.ini"].sha256.startswith("6d7ed07e")
    assert files["dxgi.dll"].amd_intel_only and files["dlss-finder.exe"].amd_intel_only
    assert "_nvngx.dll" not in files  # resolved from the game's nvngx_dlss.dll at install time
    assert all(url.url.startswith("https://raw.githubusercontent.com/artur-graniszewski/DLSS-Enabler/c67224e4")
               for url in RAW_FILES.values())


def test_amd_install_requires_and_copies_the_games_native_dlss(enabler) -> None:
    de, _service, _variants, game, bin_dir = enabler
    native = next(game.install_path.rglob("nvngx_dlss.dll"))
    native.unlink()
    before = _snapshot(game.install_path)
    with pytest.raises(OptiScalerError, match="native nvngx_dlss.dll"):
        de.enable(game, gpu=AMD)
    assert _snapshot(game.install_path) == before
    assert not (bin_dir / "_nvngx.dll").exists()


def test_different_native_dlss_candidates_are_rejected(enabler) -> None:
    de, _service, _variants, game, _bin_dir = enabler
    second = game.install_path / "Other/nvngx_dlss.dll"
    second.parent.mkdir(parents=True)
    second.write_bytes(b"different DLSS runtime")
    with pytest.raises(OptiScalerError, match="multiple different"):
        de.enable(game, gpu=AMD)
    assert de.status(game)["phase"] == "inactive"


def test_configuration_and_runtime_verification_are_per_game(enabler) -> None:
    de, _service, _variants, game, bin_dir = enabler
    status = de.enable(game, gpu=AMD)
    assert status["statusState"] == "selected"
    text = (bin_dir / "nvngx.ini").read_text()
    assert "[FrameGeneration]" in text
    assert text.count("[FrameGeneration]") == 1
    assert "LoggingEnabled=true" in text and "LogToFile=true" in text
    configured = de.configure(game, {
        "dx12Upscaler": "fsr31", "frameGenerator": "fsr3",
        "reflex": "boost", "framerateLimit": "90", "logging": True,
    })
    assert configured["configuration"]["dx12Upscaler"] == "fsr31"
    assert "Dx12Upscaler=fsr31" in (bin_dir / "nvngx.ini").read_text()
    assert (bin_dir / "nvngx.ini").read_text().count("[FrameGeneration]") == 1
    # Repair the adjacent duplicate headers produced by the superseded build.
    ini = bin_dir / "nvngx.ini"
    ini.write_text(ini.read_text().replace("Reflex=boost", "[FrameGeneration]\nReflex=boost"))
    de.configure(game, {})
    assert ini.read_text().count("[FrameGeneration]") == 1
    (bin_dir / "dlss-enabler.log").write_text("unrelated fresh text\n")
    assert de.status(game)["runtimeVerified"] is False
    (bin_dir / "dlss-enabler.log").write_text("[DLSS] Upscaler backend loaded successfully\n")
    verified = de.status(game)
    assert verified["runtimeVerified"] is True
    assert verified["statusState"] == "runtime_verified"


def test_loaded_amd_modules_verify_runtime_and_persist(enabler, tmp_path: Path) -> None:
    de, service, _variants, game, bin_dir = enabler
    de.enable(game, gpu=AMD)
    process_id = 4321
    maps = tmp_path / "proc" / str(process_id) / "maps"
    maps.parent.mkdir(parents=True)
    maps.write_text(
        f"1000-2000 r--p 0 00:00 1 {bin_dir / 'dlss-enabler.dll'}\n"
        f"2000-3000 r--p 0 00:00 2 {bin_dir / 'dlss-enabler-upscaler.dll'}\n"
    )
    de.proc_root = tmp_path / "proc"
    service._process_detector = lambda _root: (process_id,)
    status = de.status(game)
    assert status["runtimeVerified"] is True
    assert status["runtime"]["method"] == "process_maps"
    assert status["runtime"]["modules"] == ["dlss-enabler.dll", "dlss-enabler-upscaler.dll"]
    service._process_detector = lambda _root: ()
    persisted = de.status(game)
    assert persisted["runtimeVerified"] is True
    assert persisted["runtime"]["processId"] == process_id


def test_flatpak_requires_main_and_upscaler_in_the_same_game_process(enabler) -> None:
    de, _service, _variants, game, bin_dir = enabler
    de.enable(game, gpu=AMD)
    de._host_process_modules = lambda _name: ({"processId": 21, "modules": [
        str(bin_dir / "dxgi.dll"), str(bin_dir / "_nvngx.dll")
    ]},)
    assert de.status(game)["runtimeVerified"] is False
    de._host_process_modules = lambda _name: ({"processId": 22, "modules": [
        str(bin_dir / "dlss-enabler.dll"), str(bin_dir / "dlss-enabler-upscaler.dll")
    ]},)
    status = de.status(game)
    assert status["runtimeVerified"] is True
    assert status["runtime"]["method"] == "process_maps"
    assert status["runtime"]["scope"] == "modules_loaded"
    assert status["runtime"]["processId"] == 22


def test_configuration_invalidates_old_evidence_and_rolls_back_if_state_save_fails(enabler) -> None:
    de, _service, _variants, game, bin_dir = enabler
    de.enable(game, gpu=AMD)
    log = bin_dir / "dlss-enabler.log"
    log.write_text("[DLSS] Upscaler backend loaded successfully\n")
    assert de.status(game)["runtimeVerified"]
    de.configure(game, {"dx12Upscaler": "fsr31"})
    assert not de.status(game)["runtimeVerified"]
    ini = bin_dir / "nvngx.ini"
    before = ini.read_bytes()
    saved_state = de._state_path("224760").read_bytes()
    original_save = de._save_state

    def fail_save(*_args):
        raise OSError("disk full")

    de._save_state = fail_save
    with pytest.raises(OSError, match="disk full"):
        de.configure(game, {"dx12Upscaler": "xess"})
    de._save_state = original_save
    assert ini.read_bytes() == before
    assert de._state_path("224760").read_bytes() == saved_state


def test_failure_after_native_runtime_copy_rolls_back_everything(enabler, env) -> None:
    de, service, _variants, game, bin_dir = enabler
    _lite(env)
    before = _snapshot(bin_dir)
    profile = service.profile_repository.path("224760").read_bytes()
    original_run = de._run_step

    def fail_runtime(bases, step):
        original_run(bases, step)
        if step["kind"] == "copy_runtime":
            raise OSError("interrupted after native DLL copy")

    de._run_step = fail_runtime
    with pytest.raises(OSError, match="native DLL copy"):
        de.enable(game, gpu=AMD)
    assert _snapshot(bin_dir) == before
    assert service.profile_repository.path("224760").read_bytes() == profile


def test_flatpak_host_modules_block_mutations_while_game_runs(enabler) -> None:
    de, _service, _variants, game, bin_dir = enabler
    de.enable(game, gpu=AMD)
    de._host_process_modules = lambda _name: ({"processId": 23, "modules": [
        str(bin_dir / "dlss-enabler.dll")
    ]},)
    before = _snapshot(bin_dir)
    with pytest.raises(OptiScalerError, match="currently running"):
        de.disable(game)
    with pytest.raises(OptiScalerError, match="currently running"):
        de.configure(game, {"logging": False})
    assert _snapshot(bin_dir) == before
