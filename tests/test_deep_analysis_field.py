"""Field-test fixes of the read-only deep analysis (scope, roles, sizes, RAGE)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from game_optimization_linux.controllers.deep_optimization_controller import DeepOptimizationController
from game_optimization_linux.models import FilesystemInfo, FilesystemType
from game_optimization_linux.models.deep_optimization import savings_paths_disjoint
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.services.deep_optimization import (
    DeepAnalysisRepository,
    DeepOptimizationAnalyzer,
    SizeMeasurement,
    apply_target_language,
    measurement_from_analysis_report,
)
from game_optimization_linux.services.game_analysis import GameAnalyzer
from game_optimization_linux.services.game_executable import GameExecutableResolver

APP_ID = "960090"


def _tree(root: Path, files: dict[str, bytes | str]) -> Path:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def _game(root: Path, *, library: Path | None = None, app_id: str = APP_ID,
          filesystem: FilesystemType = FilesystemType.EXT4, game_id: str = ""):
    return replace(demo_games()[0], id=game_id or f"steam-{app_id}", install_path=root,
                   library_path=library, steam_app_id=app_id, filesystem=filesystem)


def _analyzer(**kwargs: Any) -> DeepOptimizationAnalyzer:
    kwargs.setdefault("engine_detector", GameAnalyzer(GameExecutableResolver()).detect_engine)
    kwargs.setdefault("system_language", lambda: "pl")
    kwargs.setdefault("running_detector", lambda _root: ())
    kwargs.setdefault("allow_native_btrfs_du", False)
    return DeepOptimizationAnalyzer(**kwargs)


def _groups(plan) -> dict[tuple[str, str, str], Any]:
    return {(group.category, group.key, group.owner): group for group in plan.groups}


class _Btrfs:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(mount_point=path, filesystem=FilesystemType.BTRFS,
                              compression_supported=True, mount_options=("compress=zstd:3",),
                              filesystem_name="btrfs")


# A. Global leftovers -----------------------------------------------------------------


def _library(tmp_path: Path) -> Path:
    library = tmp_path / "SteamLibrary"
    for app_id in (APP_ID, "960091"):
        _tree(library / f"steamapps/common/G{app_id}", {"Game.exe": b"MZ", "Logs/a.log": "x"})
        (library / f"steamapps/appmanifest_{app_id}.acf").write_text('"AppState" {}', encoding="utf-8")
    _tree(library / "steamapps", {
        "shadercache/111/fozpipelinesv6/a.foz": b"s" * 1000,   # uninstalled
        "compatdata/222/pfx/user.reg": b"p" * 500,             # uninstalled
        f"shadercache/{APP_ID}/fozpipelinesv6/own.foz": b"o" * 30,
    })
    return library


def test_a_global_leftovers_do_not_count_for_the_game_and_are_not_repeated(tmp_path: Path) -> None:
    library = _library(tmp_path)
    plans = [
        _analyzer().analyze(_game(library / f"steamapps/common/G{app_id}", library=library, app_id=app_id))
        for app_id in (APP_ID, "960091")
    ]
    for plan in plans:
        summary = plan.savings_summary()
        global_groups = [group for group in plan.groups if group.scope == "global"]
        assert {group.owner.split("@")[0] for group in global_groups} == {"shadercache/111", "compatdata/222"}
        assert summary["global"] == 1500
        game_savings = summary["safeNow"] + summary["needsConfirmation"]
        assert game_savings < 1000  # the 1.5 KB of other games is not this game's saving
        assert all(group.scope == "game" for group in plan.actionable_groups())
    # The game's own shader cache stays in the game scope.
    own = _groups(plans[0])[("cache", "steam_shadercache_installed", "")]
    assert own.scope == "game" and own.safety == "review"
    repository = DeepAnalysisRepository(tmp_path / "data")
    for plan in plans:
        repository.save(plan)
    leftovers = repository.global_leftovers()
    assert sorted(item["appId"] for item in leftovers) == ["111", "222"]
    assert sum(item["bytes"] for item in leftovers) == 1500
    exported = plans[0].to_dict()
    assert {group["scope"] for group in exported["groups"]} == {"game", "global"}


# B./C. Language roles -------------------------------------------------------------------


def test_b_polish_text_without_polish_audio_keeps_english_fallback(tmp_path: Path) -> None:
    root = _tree(tmp_path / "Bloons", {
        "audio/en_US/vo.pck": b"e" * 40, "audio/de_DE/vo.pck": b"d" * 30,
        "loc/strings_pl.txt": "pl", "loc/strings_en.txt": "en", "loc/strings_fr.txt": "fr",
        "common/sfx.pck": b"s" * 10,
    })
    plan = _analyzer(system_language=lambda: "pl").analyze(_game(root))
    groups = _groups(plan)
    english_audio = groups[("audio", "lang:en", "")]
    assert (english_audio.role, english_audio.safety, english_audio.action) == ("fallback", "unsupported", "keep")
    assert groups[("audio", "lang:de", "")].role == "other"
    assert groups[("languages", "lang:pl", "")].role == "target"
    assert groups[("languages", "lang:en", "")].role == "fallback"
    assert groups[("languages", "lang:fr", "")].role == "other"
    assert "no_target_audio|pl|en" in plan.notes
    # Polish subtitles never imply Polish voice acting.
    assert not any(group.category == "audio" and group.language == "pl" for group in plan.groups)
    summary = plan.savings_summary()
    assert summary["otherLanguages"] == 30 + 2
    # With separate Polish audio, English audio is no longer a fallback.
    with_polish = _tree(tmp_path / "WithPolish", {"audio/pl_PL/vo.pck": b"p", "audio/en_US/vo.pck": b"e"})
    plan = _analyzer(system_language=lambda: "pl").analyze(_game(with_polish))
    assert _groups(plan)[("audio", "lang:en", "")].role == "other"
    assert not any(note.startswith("no_target_audio") for note in plan.notes)


def test_b_unknown_default_voice_keeps_every_audio_language(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"audio/de_DE/vo.pck": b"d", "audio/fr_FR/vo.pck": b"f"})
    plan = _analyzer(system_language=lambda: "pl").analyze(_game(root))
    assert {group.role for group in plan.groups if group.category == "audio" and group.language} == {"fallback"}
    assert all(group.action == "keep" for group in plan.groups if group.category == "audio")


@pytest.mark.parametrize("target", ["pl", "en", "de", "ja"])
def test_c_shared_audio_is_never_a_removal_candidate(tmp_path: Path, target: str) -> None:
    root = _tree(tmp_path / "G", {
        "audio/en_US/vo.pck": b"e", "audio/pl_PL/vo.pck": b"p",
        "audio/common/music.pck": b"m", "sound/ambience.bank": b"a",
    })
    plan = apply_target_language(_analyzer().analyze(_game(root)), target)
    shared = [group for group in plan.groups if group.key in {"shared", "wwise_shared"}]
    assert shared and all((group.role, group.action, group.safety) == ("shared", "keep", "unsupported") for group in shared)
    assert not any(group.key in {"shared", "wwise_shared"} for group in plan.actionable_groups())


# D. Disjoint savings -------------------------------------------------------------------------


def test_d_no_canonical_path_is_counted_twice_in_savings(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {
        "Logs/a.log": "x" * 10, "Crashes/b.dmp": b"d" * 20, "Game.exe": b"MZ" * 50,
        "loc/strings_de.txt": "de", "_CommonRedist/vc.exe": b"MZ",
    })
    (root / "Game.exe.hardlink").hardlink_to(root / "Game.exe")
    plan = _analyzer(filesystem_provider=_Btrfs()).analyze(_game(root, filesystem=FilesystemType.BTRFS))
    assert savings_paths_disjoint(plan.groups)
    assert plan.savings_summary()["disjoint"] is True
    removal = {path for group in plan.groups if group.action == "remove_candidate" for path in group.sample_paths}
    compression = {path for group in plan.groups if group.category == "btrfs" for path in group.sample_paths}
    assert removal and not removal & compression
    summary = plan.savings_summary()
    assert summary["safeNow"] + summary["needsConfirmation"] + summary["btrfsEligible"] <= plan.logical_bytes
    # A hard link is one canonical file, counted once.
    assert plan.file_count == 5 and "hardlinks_counted_once|1" in plan.warnings


# E. One measurement source for both screens ---------------------------------------------------


def test_e_exact_measurement_from_the_compression_header_is_used(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ" * 100})
    header = {"compsize_disk_bytes": 2_362_232_012, "compsize_uncompressed_bytes": 2_792_000_000,
              "compsize_referenced_bytes": 2_792_000_000, "exclusive_bytes": 2_300_000_000,
              "shared_bytes": 0, "measurement_source": "polkit_compsize"}
    app = SimpleNamespace(_current_authoritative_compsize=lambda game_id: header, _analysis_reports={})
    measurement = DeepOptimizationController.measurement_for(SimpleNamespace(_app=app), "steam-1")
    assert measurement is not None and measurement.source == "compression_result"
    plan = _analyzer(filesystem_provider=_Btrfs()).analyze(
        _game(root, filesystem=FilesystemType.BTRFS), measurement=measurement
    )
    sizes = plan.to_dict()["sizes"]
    assert sizes["physicalSize"] == 2_362_232_012
    assert sizes["measuredSavings"] == 2_792_000_000 - 2_362_232_012
    assert sizes["states"]["physical"] == "exact" and sizes["measurementSource"] == "compression_result"
    assert sizes["states"]["allocated"] == "estimated"
    assert plan.btrfs.shared_extents is False


def test_e_analysis_cache_report_is_the_second_source(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ"})
    report = {"compsize": {"available": True, "disk_usage_bytes": 900, "uncompressed_bytes": 1200},
              "btrfs_du": {"available": True, "exclusive_bytes": 800, "set_shared_bytes": 100}}
    app = SimpleNamespace(_current_authoritative_compsize=lambda game_id: {}, _analysis_reports={"g": report})
    measurement = DeepOptimizationController.measurement_for(SimpleNamespace(_app=app), "g")
    assert measurement == measurement_from_analysis_report(report)
    plan = _analyzer(filesystem_provider=_Btrfs()).analyze(
        _game(root, filesystem=FilesystemType.BTRFS), measurement=measurement
    )
    assert plan.sizes.measurement_source == "analysis_cache"
    assert (plan.sizes.physical_size, plan.sizes.shared_size, plan.btrfs.shared_extents) == (900, 100, True)


def test_e_without_measurement_values_are_estimated_or_unavailable(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ" * 10})
    plan = _analyzer(filesystem_provider=_Btrfs()).analyze(_game(root, filesystem=FilesystemType.BTRFS))
    states = plan.sizes.states
    assert plan.sizes.physical_size is None and plan.sizes.measured_savings is None
    assert (states["physical"], states["savings"], states["allocated"]) == ("unavailable", "unavailable", "estimated")
    assert plan.sizes.measurement_source == "none"
    qml = Path("src/game_optimization_linux/qml/pages/details/DeepAnalysisSection.qml").read_text(encoding="utf-8")
    assert 'qsTr("Allocated blocks (estimate)")' in qml and 'qsTr("not measured")' in qml
    assert "allocated blocks, approximate" not in qml


# F. RAGE detection --------------------------------------------------------------------------------


def _rage_root(tmp_path: Path) -> Path:
    files = {f"x64{letter}.rpf": b"R" for letter in "abcdefghijklmnopqrstuvw"}
    files.update({
        "common.rpf": b"C", "update/update.rpf": b"U", "x64/audio/sfx/RESIDENT.rpf": b"A",
        "x64/data/cdimages/scaleform_frontend.rpf": b"S",
        "update/x64/dlcpacks/mpbeach/dlc.rpf": b"D", "GTA5_Enhanced.exe": b"MZ",
    })
    return _tree(tmp_path / "Grand Theft Auto V Enhanced", files)


def test_f_full_rage_layout_is_detected_with_high_confidence(tmp_path: Path) -> None:
    engine, _version = GameAnalyzer(GameExecutableResolver()).detect_engine(_rage_root(tmp_path))
    assert engine.value == "RAGE" and engine.confidence >= 0.9
    assert len(engine.evidence) >= 4


@pytest.mark.parametrize("files", [
    {"data/archive.rpf": b"x", "game.exe": b"MZ"},
    {"x64a.rpf": b"x", "x64b.rpf": b"x", "x64c.rpf": b"x", "common.rpf": b"x"},
    {"update/update.rpf": b"x", "levels/x64a.rpf": b"x"},
])
def test_f_a_random_rpf_layout_is_not_rage(tmp_path: Path, files: dict[str, bytes]) -> None:
    engine, _version = GameAnalyzer(GameExecutableResolver()).detect_engine(_tree(tmp_path / "Game", files))
    assert engine.value == "Unknown"


def test_f_rage_archives_stay_opaque_core(tmp_path: Path) -> None:
    plan = _analyzer().analyze(_game(_rage_root(tmp_path)))
    assert plan.engine == "RAGE"
    packages = _groups(plan)[("textures", "packages_opaque", "")]
    assert packages.action == "keep" and packages.file_count == 28
