"""Anti-cheat files warn, never block, the explicit DLSS Enabler switch.

Owner report 24.10.2026: GTA V Enhanced (BattlEye/ folder) and Jedi: Survivor
(movie assets named *_Ricochet.*) were both blocked with "Anti-cheat files
were detected; automatic installation is blocked".  Disposable folders only.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services.optiscaler import (
    OptiScalerConflictError,
    OptiScalerError,
    anti_cheat_matches,
)

from test_dlss_enabler import AMD, _lite, _snapshot, enabler  # noqa: F401
from test_fsr_variants import _install, env  # noqa: F401


def _game(root: Path, app_id: str, name: str) -> Game:
    exe = root / "Bin" / "Game.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"exe")
    return Game(
        id=f"steam-{app_id}", steam_app_id=app_id, name=name, launcher=Launcher.STEAM,
        install_path=root, logical_size_gb=0.01, physical_size_gb=0.01,
        filesystem=FilesystemType.EXT4, compression_available=False,
    )


def _files(root: Path, *relative: str) -> None:
    for rel in relative:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")


def test_real_layouts_gta_battleye_is_matched_jedi_ricochet_assets_are_not(tmp_path: Path) -> None:
    gta = tmp_path / "Grand Theft Auto V Enhanced"
    _files(gta, "BattlEye/BEClient_x64.dll", "BattlEye/BEClient_x64.cfg",
           "BattlEye/BEService_x64.exe", "GTA5_Enhanced.exe")
    jedi = tmp_path / "Jedi Survivor"
    _files(jedi, "SwGame/Content/Movies/BlasterShot/BlasterShot_Ricochet.uasset",
           "SwGame/Content/Movies/BlasterShot/Shot_Ricochet.bk2",
           "SwGame/Binaries/Win64/JediSurvivor.exe")
    gta_matches = anti_cheat_matches(gta)
    assert {m["rule"] for m in gta_matches} == {"BattlEye"}
    assert {m["path"] for m in gta_matches} == {
        "BattlEye", "BattlEye/BEClient_x64.dll", "BattlEye/BEClient_x64.cfg",
        "BattlEye/BEService_x64.exe",
    }
    assert anti_cheat_matches(jedi) == []


def test_detection_stays_inside_the_game_directory(tmp_path: Path) -> None:
    game = tmp_path / "library" / "Game"
    (game / "Bin").mkdir(parents=True)
    _files(tmp_path / "library", "OtherGame/EasyAntiCheat/EasyAntiCheat_x64.dll",
           "BEService_x64.exe")
    (game / "linked").symlink_to(tmp_path / "library" / "OtherGame", target_is_directory=True)
    matches = anti_cheat_matches(game)
    assert matches == []  # sibling game and symlinked folder are never scanned
    assert anti_cheat_matches(tmp_path / "missing") == []


def test_anticheat_files_warn_but_do_not_block_the_switch(enabler, env, caplog) -> None:  # noqa: F811
    de, _service, _variants, game, bin_dir = enabler
    _lite(env)
    _files(bin_dir, "BattlEye/BEService_x64.exe")
    before = _snapshot(bin_dir)
    status = de.status(game)
    assert status["antiCheat"]["detected"] is True
    assert status["antiCheat"]["names"] == ["BattlEye"]
    with caplog.at_level(logging.WARNING, logger="game_optimization_linux.services.dlss_enabler"):
        result = de.enable(game, gpu=AMD)
    assert result["active"] is True
    assert "Bin/BattlEye/BEService_x64.exe [BattlEye]" in caplog.text
    # Anti-cheat files are never moved, patched or removed.
    assert (bin_dir / "BattlEye" / "BEService_x64.exe").read_bytes() == b"x"
    assert de.load_state("224760")["anti_cheat_warning"][0]["rule"] == "BattlEye"
    de.disable(game)
    assert _snapshot(bin_dir) == before


def test_switching_games_does_not_retain_previous_detection(enabler, tmp_path: Path) -> None:
    de = enabler[0]
    gta = _game(tmp_path / "gta", "3240220", "GTA V Enhanced")
    _files(gta.install_path, "BattlEye/BEClient_x64.dll")
    jedi = _game(tmp_path / "jedi", "1774580", "Jedi Survivor")
    _files(jedi.install_path, "Movies/Shot_Ricochet.bk2")
    first, second, again = de.status(gta), de.status(jedi), de.status(gta)
    assert first["antiCheat"]["detected"] and first["gameId"] == "steam-3240220"
    assert second["gameId"] == "steam-1774580"
    assert second["antiCheat"]["detected"] is False and second["antiCheat"]["matches"] == []
    assert second["antiCheat"]["root"] == str(jedi.install_path.resolve())
    assert again["antiCheat"]["names"] == ["BattlEye"]


def test_existing_protections_still_block(enabler, env) -> None:  # noqa: F811
    de, service, _variants, game, bin_dir = enabler
    _install(env)
    _files(bin_dir, "BattlEye/BEService_x64.exe")
    before = _snapshot(bin_dir)
    service._process_detector = lambda _root: (1234,)
    with pytest.raises(OptiScalerError, match="currently running"):
        de.enable(game, gpu=AMD)
    service._process_detector = lambda _root: ()
    assert _snapshot(bin_dir) == before
    (bin_dir / "dxgi.dll").write_bytes(b"tampered")  # managed OptiScaler proxy
    before = _snapshot(bin_dir)
    with pytest.raises(OptiScalerConflictError):
        de.enable(game, gpu=AMD)
    assert _snapshot(bin_dir) == before
    assert de.status(game)["phase"] == "inactive"


def test_failed_migration_with_anticheat_rolls_back_to_lite(enabler, env) -> None:  # noqa: F811
    de, service, variants, game, bin_dir = enabler
    _lite(env)
    _files(bin_dir, "BattlEye/BEService_x64.exe")
    before = _snapshot(bin_dir)
    profile_before = service.profile_repository.path("224760").read_bytes()
    real = de._run_step
    calls = {"n": 0}

    def flaky(bases, step):
        calls["n"] += 1
        if calls["n"] == 10:
            raise OSError("disk full")
        real(bases, step)

    de._run_step = flaky
    with pytest.raises(OSError):
        de.enable(game, gpu=AMD)
    de._run_step = real
    assert _snapshot(bin_dir) == before
    assert service.profile_repository.path("224760").read_bytes() == profile_before
    assert variants.status(game)["driverInstalled"] is True
    assert de.status(game)["phase"] == "inactive"


def test_tampered_download_is_rejected_before_any_change(enabler, env) -> None:  # noqa: F811
    de, _service, _variants, game, bin_dir = enabler
    _lite(env)
    before = _snapshot(bin_dir)
    url = de.raw_files["enabler_main"].url
    payloads = de.client.opener.payloads
    payloads[url] = b"E" * len(payloads[url])  # same size, wrong SHA-256
    with pytest.raises(OptiScalerError):
        de.enable(game, gpu=AMD)
    assert _snapshot(bin_dir) == before
    assert de.status(game)["phase"] == "inactive"
