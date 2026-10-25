"""Read-only deep cleaning/compression analysis (stage 1: plan only)."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from threading import Event
from typing import Any

import pytest

from game_optimization_linux.models import FilesystemInfo, FilesystemType
from game_optimization_linux.models.deep_optimization import DEEP_PLAN_SCHEMA_VERSION
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.services import deep_classify, deep_optimization
from game_optimization_linux.services.btrfs_analysis import BtrfsCompressionAnalyzer
from game_optimization_linux.services.deep_classify import file_name_language, segment_language
from game_optimization_linux.services.deep_optimization import (
    NON_BTRFS_MESSAGE,
    DeepAnalysisRepository,
    DeepOptimizationAnalyzer,
    DeepScanCancelled,
    apply_target_language,
    language_from_locale,
)
from game_optimization_linux.services.game_analysis import GameAnalyzer
from game_optimization_linux.services.game_executable import GameExecutableResolver

OLD = 1_000_000_000  # 2001: "old" logs and crash dumps


def _tree(root: Path, files: dict[str, bytes | str]) -> Path:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def _game(root: Path, *, library: Path | None = None, app_id: str = "4242", filesystem=FilesystemType.EXT4):
    return replace(demo_games()[0], install_path=root, library_path=library, steam_app_id=app_id,
                   filesystem=filesystem)


def _analyzer(**kwargs: Any) -> DeepOptimizationAnalyzer:
    kwargs.setdefault("engine_detector", GameAnalyzer(GameExecutableResolver()).detect_engine)
    kwargs.setdefault("system_language", lambda: "pl")
    kwargs.setdefault("running_detector", lambda _root: ())
    return DeepOptimizationAnalyzer(**kwargs)


def _groups(plan) -> dict[tuple[str, str], Any]:
    return {(group.category, group.key): group for group in plan.groups}


def _snapshot(root: Path) -> dict[str, tuple[int, float, bytes]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime, path.read_bytes())
        for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
    }


FIXTURES = {
    "unreal": {
        "Engine/Binaries/ThirdParty/readme.txt": "x",
        "MyGame/Content/Paks/pakchunk0-WindowsNoEditor.pak": b"P" * 64,
        "MyGame/Content/Paks/global.ucas": b"U" * 32,
        "MyGame/Content/Paks/global.utoc": b"T" * 16,
        "MyGame/Binaries/Win64/MyGame-Win64-Shipping.exe": b"MZ",
    },
    "unity": {
        "UnityPlayer.dll": b"MZ",
        "Game_Data/globalgamemanagers": b"G" * 8,
        "Game_Data/resources.assets": b"R" * 8,
        "Game_Data/sharedassets0.assets": b"S" * 8,
        "Game.exe": b"MZ",
    },
    "source": {
        "hl2.exe": b"MZ",
        "hl2/gameinfo.txt": "GameInfo {}",
        "hl2/hl2_pak_dir.vpk": b"V" * 20,
        "hl2/hl2_pak_000.vpk": b"V" * 40,
    },
    "unknown": {"data/level.bundle": b"B" * 10, "game.bin": b"x"},
}


# A. Engine detection reuses the existing detector ---------------------------------


def test_a_engine_comes_from_the_existing_game_analyzer_api(tmp_path: Path) -> None:
    root = _tree(tmp_path / "Unreal", FIXTURES["unreal"])
    detector = GameAnalyzer(GameExecutableResolver())
    calls: list[Path] = []

    def spy(path: Path):
        calls.append(path)
        return detector.detect_engine(path)

    plan = _analyzer(engine_detector=spy).analyze(_game(root))
    assert calls == [root]
    expected, _version = detector.detect_engine(root)
    assert (plan.engine, plan.engine_confidence) == (expected.value, expected.confidence)
    assert plan.engine == "Unreal Engine" and plan.engine_confidence >= 0.55
    assert plan.engine_evidence
    # The deep analysis has no engine heuristics of its own.
    for module in (deep_classify, deep_optimization):
        text = Path(module.__file__).read_text(encoding="utf-8").casefold()
        assert "unityplayer" not in text and "-win64-shipping" not in text and "build.version" not in text


# B. Archives are opaque core data for every engine --------------------------------


@pytest.mark.parametrize(("name", "engine", "archives"), [
    ("unreal", "Unreal Engine", 3), ("unity", "Unity", 3), ("source", "Unknown", 2), ("unknown", "Unknown", 1),
])
def test_b_archives_are_opaque_core(tmp_path: Path, name: str, engine: str, archives: int) -> None:
    root = _tree(tmp_path / name, FIXTURES[name])
    before = _snapshot(root)
    plan = _analyzer().analyze(_game(root))
    assert plan.engine == engine
    packages = _groups(plan)[("textures", "packages_opaque")]
    assert (packages.file_count, packages.safety, packages.action) == (archives, "unsupported", "keep")
    assert not any(group.safety == "safe" and group.category == "textures" for group in plan.groups)
    assert _snapshot(root) == before  # nothing in the game directory changed


# C. Languages by whole segments ---------------------------------------------------


def test_c_language_segments_and_shared_audio(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {
        "audio/pl_PL/dialogue.pck": b"p" * 10,
        "audio/en_US/dialogue.pck": b"e" * 20,
        "common/dialogue.pck": b"c" * 5,
        "content/enemies/entrance.dat": b"x",
        "content/garden/open_menu.txt": "x",
    })
    plan = _analyzer(system_language=lambda: "de").analyze(_game(root))
    groups = _groups(plan)
    assert groups[("audio", "lang:pl")].sample_paths == ("audio/pl_PL/dialogue.pck",)
    assert groups[("audio", "lang:en")].sample_paths == ("audio/en_US/dialogue.pck",)
    shared = groups[("audio", "shared")]
    assert shared.sample_paths == ("common/dialogue.pck",) and shared.action == "keep"
    assert shared.safety == "unsupported"
    assert set(plan.languages) == {"pl", "en"}
    # Words that merely contain "en" never become English.
    all_samples = {path for group in plan.groups if group.language for path in group.sample_paths}
    assert not {"content/enemies/entrance.dat", "content/garden/open_menu.txt"} & all_samples
    assert file_name_language("entrance.dat", localized_context=False) is None
    assert file_name_language("open_menu.txt", localized_context=True) is None
    assert segment_language("enemies") is None and segment_language("en_US") == ("en", "region")
    # Target language is kept; others are Review, never Safe, never auto-selected.
    polish = apply_target_language(plan, "pl")
    assert _groups(polish)[("audio", "lang:pl")].action == "keep"
    assert _groups(polish)[("audio", "lang:en")].safety == "review"
    assert all(group.safety != "safe" for group in polish.groups if group.language)


def test_c_ambiguous_codes_need_a_localization_context(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {
        "tools/it/readme.txt": "x",          # "it" outside localization
        "Localization/it/strings.txt": "x",  # Italian inside localization
        "maps/map_no.bin": b"x",
    })
    plan = _analyzer().analyze(_game(root))
    samples = {path: group.language for group in plan.groups if group.language for path in group.sample_paths}
    assert samples == {"Localization/it/strings.txt": "it"}


def test_c_target_language_from_steam_manifest(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    root = _tree(library / "steamapps/common/G", {"audio/polish/vo.bnk": b"x", "audio/english/vo.bnk": b"y"})
    (library / "steamapps/appmanifest_4242.acf").write_text(
        '"AppState"\n{\n\t"UserConfig"\n\t{\n\t\t"language"\t\t"polish"\n\t}\n}\n', encoding="utf-8"
    )
    plan = _analyzer(system_language=lambda: "de").analyze(_game(root, library=library))
    assert (plan.target_language, plan.target_language_source) == ("pl", "steam_manifest")
    assert language_from_locale("pl_PL.UTF-8") == "pl" and language_from_locale("C.UTF-8") == ""


# D. Wwise metadata ------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["xml", "json"])
def test_d_wwise_metadata_separates_language_and_shared_banks(tmp_path: Path, fmt: str) -> None:
    files = {
        "Audio/GeneratedSoundBanks/Windows/Init.bnk": b"i" * 3,
        "Audio/GeneratedSoundBanks/Windows/Music.bnk": b"m" * 4,
        "Audio/GeneratedSoundBanks/Windows/Polish(Poland)/VO_Main.bnk": b"v" * 6,
        "Audio/GeneratedSoundBanks/Windows/Polish(Poland)/1234.wem": b"w" * 7,
        "Audio/GeneratedSoundBanks/Windows/English(US)/VO_Main.bnk": b"n" * 5,
    }
    if fmt == "xml":
        files["Audio/GeneratedSoundBanks/Windows/SoundbanksInfo.xml"] = (
            '<?xml version="1.0"?><SoundBanksInfo><SoundBanks>'
            '<SoundBank Id="1" Language="SFX"><ShortName>Init</ShortName><Path>Init.bnk</Path></SoundBank>'
            '<SoundBank Id="2" Language="SFX"><ShortName>Music</ShortName><Path>Music.bnk</Path></SoundBank>'
            '<SoundBank Id="3" Language="Polish(Poland)"><Path>Polish(Poland)\\VO_Main.bnk</Path></SoundBank>'
            '<SoundBank Id="4" Language="English(US)"><Path>English(US)\\VO_Main.bnk</Path></SoundBank>'
            '</SoundBanks><StreamedFiles><File Id="1234" Language="Polish(Poland)"><Path>Polish(Poland)\\1234.wem</Path></File>'
            '</StreamedFiles></SoundBanksInfo>'
        )
    else:
        files["Audio/GeneratedSoundBanks/Windows/SoundbanksInfo.json"] = json.dumps({"SoundBanksInfo": {
            "SoundBanks": [
                {"Language": "SFX", "Path": "Init.bnk"}, {"Language": "SFX", "Path": "Music.bnk"},
                {"Language": "Polish(Poland)", "Path": "Polish(Poland)/VO_Main.bnk"},
                {"Language": "English(US)", "Path": "English(US)/VO_Main.bnk"},
            ],
            "StreamedFiles": [{"Language": "Polish(Poland)", "Path": "Polish(Poland)/1234.wem"}],
        }})
    root = _tree(tmp_path / "G", files)
    groups = _groups(_analyzer(system_language=lambda: "en").analyze(_game(root)))
    shared = groups[("audio", "wwise_shared")]
    assert shared.file_count == 2 and shared.action == "keep"
    polish = groups[("audio", "lang:pl")]
    assert polish.file_count == 2 and polish.confidence >= 0.9 and polish.safety == "review"
    assert polish.role == "other"
    assert groups[("audio", "lang:en")].role == "target"


def test_d_wwise_xml_with_entities_is_ignored() -> None:
    payload = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><SoundBanksInfo/>'
    assert deep_classify.parse_wwise_metadata(payload, "SoundbanksInfo.xml", "") is None


# E. Shader cache and compatdata --------------------------------------------------------


def test_e_shader_cache_and_compatdata_rules(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    root = _tree(library / "steamapps/common/G", {"ShaderCache/cache.bin": b"s" * 3, "Game.exe": b"MZ"})
    (library / "steamapps/appmanifest_4242.acf").write_text('"AppState" {}', encoding="utf-8")
    (library / "steamapps/appmanifest_777.acf").write_text('"AppState" {}', encoding="utf-8")
    _tree(library / "steamapps", {
        "shadercache/4242/fozpipelinesv6/a.foz": b"1" * 4,
        "shadercache/9999/fozpipelinesv6/b.foz": b"2" * 5,   # uninstalled
        "shadercache/777/fozpipelinesv6/c.foz": b"3" * 6,    # another installed game
        "compatdata/4242/pfx/drive_c/users/steamuser/save.sav": b"4",
        "compatdata/5555/pfx/user.reg": b"5",
    })
    before = _snapshot(library)
    groups = _groups(_analyzer().analyze(_game(root, library=library)))
    assert groups[("cache", "shader_cache_installed")].safety == "review"
    assert groups[("cache", "shader_cache_installed")].warnings
    assert groups[("cache", "steam_shadercache_installed")].safety == "review"
    uninstalled = groups[("cache", "steam_shadercache_uninstalled")]
    assert uninstalled.safety == "safe" and uninstalled.sample_paths == (
        "$STEAM_LIBRARY/steamapps/shadercache/9999/fozpipelinesv6/b.foz",)
    active = groups[("cache", "compatdata_active")]
    assert (active.safety, active.action) == ("unsupported", "keep")
    assert groups[("cache", "compatdata_uninstalled")].safety == "review"
    assert not any("/777/" in path for group in groups.values() for path in group.sample_paths)
    assert _snapshot(library) == before


def test_e_logs_crashes_redist_and_backups(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {
        "Logs/old.log": "x", "Logs/new.log": "y", "Crashes/a.dmp": b"d",
        "_CommonRedist/vcredist/vc_redist.x64.exe": b"MZ", "Game.exe": b"MZ", "Game.exe.bak": b"MZ",
        "orphan.bak": b"?", "Saves/slot1.sav.bak": b"s", "Saves/slot1.sav": b"s",
    })
    for relative in ("Logs/old.log", "Crashes/a.dmp"):
        os.utime(root / relative, (OLD, OLD))
    groups = _groups(_analyzer().analyze(_game(root)))
    assert groups[("cache", "logs_old")].safety == "safe"
    assert groups[("cache", "logs_recent")].safety == "review"
    assert groups[("cache", "crash_dumps")].safety == "safe"
    assert groups[("residues", "redistributables")].safety == "review"
    assert groups[("residues", "backup_copies")].sample_paths == ("Game.exe.bak",)
    assert groups[("residues", "protected_backups")].action == "keep"
    assert not any("orphan.bak" in path for group in groups.values() if group.category != "btrfs" for path in group.sample_paths)


# F. Textures -------------------------------------------------------------------------------


def test_f_low_medium_textures_and_mipmaps_are_never_safe(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {
        "Textures/rock_low.dds": b"t", "Textures/rock_medium.ktx": b"t", "Textures/rock_mip3.dds": b"t",
        "DLC/HD_Textures_Pack/hd_rock.dds": b"h" * 9,
    })
    groups = _groups(_analyzer().analyze(_game(root)))
    textures = groups[("textures", "texture_files")]
    assert (textures.file_count, textures.safety, textures.action) == (3, "unsupported", "info")
    hd = groups[("textures", "hd_texture_pack")]
    assert hd.safety == "review" and hd.file_count == 1
    assert all(group.safety != "safe" for group in groups.values() if group.category == "textures")


# G. Scanner containment and cancellation --------------------------------------------------------


def test_g_symlinks_outside_the_game_are_not_followed(tmp_path: Path) -> None:
    outside = _tree(tmp_path / "outside", {"secret/private.log": "x", "big.bin": b"z" * 100})
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ"})
    os.symlink(outside, root / "linked_dir")
    os.symlink(outside / "big.bin", root / "linked_file.bin")
    plan = _analyzer().analyze(_game(root))
    assert plan.file_count == 1 and plan.logical_bytes == 2
    assert not any("secret" in path or "linked" in path for group in plan.groups for path in group.sample_paths)
    assert "symlinks_skipped|2" in plan.warnings


def test_g_mount_points_inside_the_game_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ", "mounted/inner.bin": b"x" * 50})
    original = os.scandir

    class Entry:
        def __init__(self, entry) -> None:
            self._entry = entry
            self.name = entry.name

        def __getattr__(self, name: str):
            return getattr(self._entry, name)

        def stat(self, follow_symlinks: bool = True):
            value = self._entry.stat(follow_symlinks=follow_symlinks)
            if self.name == "mounted":
                fields = list(value)
                fields[2] = value.st_dev + 1  # another filesystem
                return os.stat_result(fields)
            return value

    class Wrapper:
        def __init__(self, target) -> None:
            self._iterator = original(target)

        def __enter__(self):
            return (Entry(entry) for entry in self._iterator.__enter__())

        def __exit__(self, *args):
            return self._iterator.__exit__(*args)

    monkeypatch.setattr(deep_optimization.os, "scandir", Wrapper)
    plan = _analyzer().analyze(_game(root))
    assert plan.file_count == 1
    assert any(warning.startswith("mount_points_skipped|1|") and "mounted" in warning for warning in plan.warnings)


def test_g_scan_can_be_cancelled_and_reports_progress(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {f"data/{index:04}.bin": b"x" for index in range(400)})
    cancel = Event()
    seen: list[Any] = []

    def progress(value) -> None:
        seen.append(value)
        if value.files >= 50:
            cancel.set()

    with pytest.raises(DeepScanCancelled):
        _analyzer(progress_interval=0.0).analyze(_game(root), cancel_event=cancel, progress_callback=progress)
    assert seen and seen[-1].files < 400
    assert {"stage", "files", "bytes", "category"} <= set(seen[-1].to_dict())


def test_g_unreadable_directory_is_reported_not_fatal(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ", "locked/secret.bin": b"x"})
    (root / "locked").chmod(0)
    try:
        plan = _analyzer().analyze(_game(root))
    finally:
        (root / "locked").chmod(0o755)
    assert plan.complete is False and plan.blockers


# H. Report privacy and schema ------------------------------------------------------------------------


def test_h_report_is_private_and_versioned(tmp_path: Path) -> None:
    library = tmp_path / "home-user" / "lib"
    root = _tree(library / "steamapps/common/G", {"audio/pl/vo.bnk": b"x", "Logs/a.log": "x", "save.cfg": "secret=1"})
    (library / "steamapps/appmanifest_4242.acf").write_text('"AppState" {}', encoding="utf-8")
    _tree(library / "steamapps", {"shadercache/9999/x.foz": b"x"})
    plan = _analyzer().analyze(_game(root, library=library))
    repository = DeepAnalysisRepository(tmp_path / "data" / "deep-analysis")
    stored = repository.save(plan)
    exported = repository.export(plan, tmp_path / "export" / "report.json", game_root=root)
    for path in (stored, exported):
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        assert data["schemaVersion"] == DEEP_PLAN_SCHEMA_VERSION == 1
        assert data["installRoot"] == "$GAME_ROOT" and data["readOnly"] is True
        assert str(tmp_path) not in text and str(Path.home()) not in text
        assert "secret=1" not in text
        for group in data["groups"]:
            assert all(not sample.startswith("/") for sample in group["samplePaths"])
            assert {"safety", "confidence", "bytes", "fileCount", "evidence", "reason"} <= set(group)
    assert repository.load(plan.game_id) == plan
    with pytest.raises(ValueError):
        repository.export(plan, root / "report.json", game_root=root)
    assert not (root / "report.json").exists()
    assert not list(stored.parent.glob(".*.tmp"))


def test_h_cancelled_plan_is_never_stored_as_complete(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ"})
    plan = replace(_analyzer().analyze(_game(root)), status="cancelled")
    assert plan.complete is False
    with pytest.raises(ValueError):
        DeepAnalysisRepository(tmp_path / "data").save(plan)


# I. Btrfs capabilities without modifying commands --------------------------------------------------


class _Filesystem:
    def __init__(self, filesystem: FilesystemType, options: tuple[str, ...] = ()) -> None:
        self.filesystem = filesystem
        self.options = options

    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(mount_point=path, filesystem=self.filesystem,
                              compression_supported=self.filesystem == FilesystemType.BTRFS,
                              mount_options=self.options, filesystem_name=self.filesystem.value)


def test_i_btrfs_uses_existing_integration_and_only_read_only_commands(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ" * 100, "data/music.ogg": b"o" * 50, "data/level.xyz": b"?"})
    commands: list[list[str]] = []

    def runner(argv, **kwargs):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "     Total   Exclusive  Set shared  Filename\n  1000  400  600  .\n", "")

    btrfs = BtrfsCompressionAnalyzer(_Filesystem(FilesystemType.BTRFS), command_runner=runner,
                                     executable_finder=lambda name: f"/usr/bin/{name}")
    plan = _analyzer(filesystem_provider=_Filesystem(FilesystemType.BTRFS, ("rw", "compress=zstd:3")),
                     btrfs_analyzer=btrfs, compsize_available=lambda: False).analyze(_game(root))
    assert plan.btrfs.is_btrfs and plan.btrfs.mount_compression == "zstd:3"
    assert plan.btrfs.compsize_available is False and plan.btrfs.shared_extents is True
    assert all(command[1:3] == ["filesystem", "du"] for command in commands)
    assert not any(word in " ".join(command) for command in commands
                   for word in ("defragment", "property set", "balance", "compress"))
    groups = _groups(plan)
    assert groups[("btrfs", "compressible")].safety == "review"  # shared extents -> never safe
    assert "btrfs_shared" in groups[("btrfs", "compressible")].warnings
    precompressed = groups[("btrfs", "precompressed")]
    assert precompressed.potential_savings_bytes == 0 and precompressed.action == "info"


def test_i_non_btrfs_shows_capability_message(tmp_path: Path) -> None:
    root = _tree(tmp_path / "G", {"Game.exe": b"MZ"})
    plan = _analyzer(filesystem_provider=_Filesystem(FilesystemType.EXT4)).analyze(_game(root))
    assert not plan.btrfs.is_btrfs and plan.btrfs.message == "non_btrfs"
    assert all(group.safety == "unsupported" for group in plan.groups if group.category == "btrfs")
    assert NON_BTRFS_MESSAGE == "Deep analysis is available; transparent compression requires Btrfs"
