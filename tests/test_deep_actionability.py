"""Actionability vs. safety in Deep Analysis, and the UE3/REDengine 3 detectors."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from game_optimization_linux.models import Launcher
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.services.deep_cleanup import CleanupError, CleanupJournal, DeepCleanupExecutor
from game_optimization_linux.services.deep_optimization import (
    DeepAnalysisRepository,
    DeepOptimizationAnalyzer,
    plan_from_dict,
)
from game_optimization_linux.services.game_analysis import GameAnalyzer
from game_optimization_linux.services.game_executable import GameExecutableResolver
from tests.test_deep_analysis_field import _rage_root

APP_ID = "4242"


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def _plan(tmp_path: Path):
    library = tmp_path / "SteamLibrary"
    root = _tree(library / "steamapps/common/Game", {
        "loc/strings_de.txt": b"de", "loc/strings_pl.txt": b"pl", "audio/en_US/vo.pck": b"v" * 8,
        "common/music.pck": b"m" * 3, "Crashes/a.dmp": b"dump", "Paks/core.pak": b"k" * 16,
        "Textures/rock_mip2.dds": b"t" * 5, "Game.exe": b"MZ", "data/level.xyz": b"?",
    })
    os.utime(root / "Crashes/a.dmp", (1_000_000_000, 1_000_000_000))
    (library / "steamapps" / f"appmanifest_{APP_ID}.acf").write_text('"AppState" {}', encoding="utf-8")
    _tree(library / "steamapps", {
        f"shadercache/{APP_ID}/fozpipelinesv6/own.foz": b"s" * 100,
        f"compatdata/{APP_ID}/pfx/user.reg": b"p" * 200,
        "shadercache/999/old.foz": b"g" * 300,
    })
    game = replace(demo_games()[0], id=f"steam-{APP_ID}", install_path=root, library_path=library,
                   steam_app_id=APP_ID, launcher=Launcher.STEAM)
    analyzer = DeepOptimizationAnalyzer(
        engine_detector=GameAnalyzer(GameExecutableResolver()).detect_engine,
        system_language=lambda: "pl", running_detector=lambda _root: (), allow_native_btrfs_du=False,
    )
    repository = DeepAnalysisRepository(tmp_path / "data")
    repository.save(analyzer.analyze(game))
    return game, repository, repository.load(game.id)


def _by_id(plan) -> dict[str, object]:
    return {group.group_id if group.scope == "game" else "global/" + group.key: group for group in plan.groups}


def test_actionability_and_reason_codes(tmp_path: Path) -> None:
    _game, _repository, plan = _plan(tmp_path)
    groups = _by_id(plan)
    expected = {
        "cache/crash_dumps": ("selectable", "safe_to_move", "safe"),
        "languages/lang:de": ("selectable", "review_before_moving", "review"),
        "languages/lang:pl": ("protected", "target_language", None),
        "audio/lang:en": ("protected", "fallback_language", None),
        "audio/shared": ("protected", "shared_audio", None),
        "cache/compatdata_active": ("protected", "active_proton_prefix", None),
        "cache/steam_shadercache_installed": ("external", "current_game_shader_cache", "review"),
        "global/steam_shadercache_uninstalled": ("external", "global_leftover", None),
        "textures/packages_opaque": ("protected", "core_archive", None),
        "textures/texture_files": ("protected", "required_texture", None),
        "btrfs/unknown_compressibility": ("unsupported", "unknown_type", None),
    }
    for group_id, (actionability, reason, safety) in expected.items():
        group = groups[group_id]
        assert (group.actionability, group.actionability_reason[1]) == (actionability, reason), group_id
        if safety:
            assert group.safety == safety
        exported = group.to_dict()
        assert (exported["actionability"], exported["actionabilityReason"]) == (actionability, reason)


def test_review_external_is_not_selectable_and_not_counted(tmp_path: Path) -> None:
    game, repository, plan = _plan(tmp_path)
    summary = plan.savings_summary()
    selectable = sum(group.bytes for group in plan.groups if group.actionability == "selectable")
    assert summary["selectableBytes"] == selectable == 4 + 2  # crash dump + German text
    assert summary["safeNow"] + summary["needsConfirmation"] == selectable
    # The game's own Steam shader cache (Review) and its active prefix never add savings.
    assert summary["externalBytes"] >= 100 + 300
    assert summary["protectedBytes"] >= 200 + 16 + 5 + 8 + 3 + 2
    executor = DeepCleanupExecutor(CleanupJournal(tmp_path / "journal"), running_detector=lambda _root: ())
    assert set(executor.selectable_groups(plan)) == {
        group.group_id for group in plan.groups if group.actionability == "selectable"
    }
    for group in plan.groups:
        if group.actionability == "selectable":
            continue
        with pytest.raises(CleanupError) as error:
            executor.prepare(game, plan, repository.fingerprint(plan), [group.group_id])
        assert error.value.code == "not_selectable"


def test_plans_without_actionability_fields_stay_compatible(tmp_path: Path) -> None:
    _game, _repository, plan = _plan(tmp_path)
    raw = json.loads(json.dumps(plan.to_dict()))
    for group in raw["groups"]:
        group.pop("actionability")
        group.pop("actionabilityReason")
        group.pop("removable")
    loaded = plan_from_dict(raw)
    assert [group.actionability for group in loaded.groups] == [group.actionability for group in plan.groups]


# Unreal Engine 3 ---------------------------------------------------------------------


def _batman_origins(tmp_path: Path) -> Path:
    files = {f"SinglePlayer/BMGame/CookedPCConsole/{name}.upk": b"U" for name in ("Startup", "Core", "Engine", "BmGame")}
    files.update({
        "SinglePlayer/BMGame/CookedPCConsole/Textures.tfc": b"T",
        "SinglePlayer/Binaries/Win32/BatmanOrigins.exe": b"MZ",
        "SinglePlayer/Engine/Config/BaseEngine.ini": b"[Core.System]",
        "SinglePlayer/BMGame/Config/DefaultEngine.ini": b"[Engine.Engine]",
        "SinglePlayer/Engine/Localization/INT/Core.int": b"[Core]",
    })
    return _tree(tmp_path / "Batman Arkham Origins", files)


def _batman_knight(tmp_path: Path) -> Path:
    files = {f"BMGame/CookedPCConsole/{name}.upk": b"U" for name in ("Startup", "BmGame", "Batmobile", "City")}
    files.update({
        "BMGame/CookedPCConsole/Textures0.tfc": b"T", "BMGame/CookedPCConsole/Textures1.tfc": b"T",
        "Binaries/Win64/BatmanAK.exe": b"MZ",
        "BMGame/Config/DefaultGame.ini": b"[Engine.GameInfo]",
        "Engine/Config/BaseEngine.ini": b"[Core.System]",
    })
    return _tree(tmp_path / "Batman Arkham Knight", files)


@pytest.mark.parametrize("builder", [_batman_origins, _batman_knight])
def test_unreal_engine_3_batman_layouts(tmp_path: Path, builder) -> None:
    engine, version = GameAnalyzer(GameExecutableResolver()).detect_engine(builder(tmp_path))
    assert engine.value == "Unreal Engine 3" and engine.confidence >= 0.85 and version == "3"
    assert len(engine.evidence) >= 4


@pytest.mark.parametrize("files", [
    {"data/random.upk": b"x", "game.exe": b"MZ"},
    {"Packages/a.upk": b"x", "Packages/b.upk": b"x", "Packages/c.upk": b"x", "tex.tfc": b"x"},
    {"CookedPC/one.upk": b"x", "Binaries/Win64/game.exe": b"MZ"},
])
def test_unreal_engine_3_needs_several_signatures(tmp_path: Path, files: dict[str, bytes]) -> None:
    engine, _version = GameAnalyzer(GameExecutableResolver()).detect_engine(_tree(tmp_path / "Game", files))
    assert engine.value == "Unknown"


# REDengine 3 -----------------------------------------------------------------------------


def _witcher3(tmp_path: Path) -> Path:
    files = {f"content/content0/bundles/{name}.bundle": b"B" for name in ("blob", "buffers", "xml", "startup")}
    files.update({
        "bin/x64/witcher3.exe": b"MZ", "bin/x64_dx12/witcher3.exe": b"MZ",
        "content/content0/metadata.store": b"M", "content/content0/texture.cache": b"C",
        "content/content0/collision.cache": b"C", "content/patch0/bundles/patch.bundle": b"B",
        "dlc/ep1/content/bundles/blob.bundle": b"B", "dlc/bob/content/metadata.store": b"M",
    })
    return _tree(tmp_path / "The Witcher 3", files)


def test_redengine_3_witcher_layout(tmp_path: Path) -> None:
    engine, version = GameAnalyzer(GameExecutableResolver()).detect_engine(_witcher3(tmp_path))
    assert engine.value == "REDengine" and version == "3" and engine.confidence >= 0.85


@pytest.mark.parametrize("files", [
    {"data/level.bundle": b"x", "game.exe": b"MZ"},
    {"bin/x64/witcher3.exe": b"MZ"},
    {"content/content0/one.bundle": b"x", "bin/x64/game.exe": b"MZ"},
])
def test_redengine_3_needs_several_signatures(tmp_path: Path, files: dict[str, bytes]) -> None:
    engine, _version = GameAnalyzer(GameExecutableResolver()).detect_engine(_tree(tmp_path / "Game", files))
    assert engine.value == "Unknown"


# RAGE regression ------------------------------------------------------------------------------


def test_rage_gta_v_enhanced_still_detected(tmp_path: Path) -> None:
    engine, _version = GameAnalyzer(GameExecutableResolver()).detect_engine(_rage_root(tmp_path))
    assert engine.value == "RAGE" and engine.confidence >= 0.9
