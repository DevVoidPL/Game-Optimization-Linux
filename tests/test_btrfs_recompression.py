"""Stage 2B Btrfs recompression with a mocked provider: no real btrfs command runs."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import random
import subprocess
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from game_optimization_linux.models import FilesystemInfo, FilesystemType, Launcher
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.services import btrfs_recompression as module
from game_optimization_linux.services.btrfs_recompression import (
    SAMPLE_CHUNK,
    BtrfsRecompressionExecutor,
    BtrfsRecompressionPlanner,
    FutureCompressionProperty,
    RecompressionError,
    RecompressionGuards,
    build_defragment_argv,
    parse_capabilities,
)
from game_optimization_linux.services.deep_optimization import DeepAnalysisRepository, DeepOptimizationAnalyzer
from game_optimization_linux.services.game_analysis import GameAnalyzer
from game_optimization_linux.services.game_executable import GameExecutableResolver

FIXTURES = Path(__file__).parent / "fixtures" / "btrfs"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _capabilities(levels: bool = True, reset: bool = False):
    return parse_capabilities(
        _fixture("version-7.1.txt" if levels else "version-legacy.txt"),
        _fixture("defragment-help-7.1.txt" if levels else "defragment-help-legacy.txt"),
        _fixture("du-help-7.1.txt"),
        _fixture("property-help-reset.txt" if reset else "property-help-7.1.txt"),
    )


class Crash(BaseException):
    pass


class FakeProvider:
    """Mocked host commands: ``du`` rows from fds, ``defragment`` marks extents."""

    def __init__(self, *, levels: bool = True, reset: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.encoded: dict[int, float] = {}
        self.shared: dict[str, int] = {}
        self.missing_rows: set[str] = set()
        self.on_defragment = None
        self.property_value = "compression=lzo"
        self._measurement_provider = None
        self.helps = {
            ("--version",): _fixture("version-7.1.txt" if levels else "version-legacy.txt"),
            ("filesystem", "defragment", "--help"): _fixture("defragment-help-7.1.txt" if levels else "defragment-help-legacy.txt"),
            ("filesystem", "du", "--help"): _fixture("du-help-7.1.txt"),
            ("property", "--help"): _fixture("property-help-reset.txt" if reset else "property-help-7.1.txt"),
        }

    def _read_help(self, argv) -> str:
        return self.helps.get(tuple(argv[1:]), "")

    def _run_command(self, argv, *, cancel_event=None, pass_fds=(), timeout=0.0):
        assert isinstance(argv, list) and all(isinstance(item, str) for item in argv)
        self.calls.append(list(argv))
        self.kwargs.append({"pass_fds": tuple(pass_fds), "cancel_event": cancel_event})
        if argv[1:3] == ["filesystem", "du"]:
            rows = ["     Total   Exclusive  Set shared  Filename"]
            for fd in pass_fds:
                path = os.readlink(f"/proc/self/fd/{fd}")
                name = Path(path).name
                if name in self.missing_rows:
                    continue
                size = os.stat(path).st_size
                shared = self.shared.get(name, 0)
                rows.append(f"{size:10d} {size - shared:11d} {shared:11d}  /proc/self/fd/{fd}")
            return subprocess.CompletedProcess(argv, 0, "\n".join(rows) + "\n", "")
        if argv[1:3] == ["filesystem", "defragment"]:
            fd = int(argv[-1].rsplit("/", 1)[-1])
            info = os.fstat(fd)
            self.encoded[info.st_ino] = 1.0
            if self.on_defragment is not None:
                self.on_defragment(os.readlink(f"/proc/self/fd/{fd}"))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["property", "get"]:
            return subprocess.CompletedProcess(argv, 0, self.property_value + "\n", "")
        if argv[1:3] == ["property", "set"]:
            self.property_value = f"compression={argv[-1]}" if argv[-1] else ""
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command {argv}")

    def fiemap(self, descriptor: int, size: int) -> tuple[float, bool]:
        return self.encoded.get(os.fstat(descriptor).st_ino, 0.0), False

    def defragments(self) -> list[list[str]]:
        return [call for call in self.calls if call[1:3] == ["filesystem", "defragment"]]


class BtrfsInspector:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(mount_point=path, filesystem=FilesystemType.BTRFS, compression_supported=True,
                              mount_options=("compress=zstd:3",), filesystem_name="btrfs")


def _text(size: int, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    words = [bytes(rng.choice(b"abcdefgh") for _ in range(6)) for _ in range(200)]
    data = b" ".join(rng.choice(words) for _ in range(size // 6 + 1))
    return data[:size]


def _setup(tmp_path: Path, *, levels: bool = True, reset: bool = False, total_sample_limit=None):
    root = tmp_path / "SteamLibrary/steamapps/common/Game"
    files = {
        "data/level.txt": _text(200_000), "data/table.csv": _text(300_000, 2), "bin/game.dll": _text(150_000, 3),
        "data/random.bin": random.Random(4).randbytes(200_000), "Paks/core.pak": _text(100_000),
        "Textures/a.dds": _text(90_000), "Movies/intro.mp4": _text(90_000), "Audio/music.ogg": _text(90_000),
        "Archives/extra.zip": _text(90_000), "Logs/old.log": _text(90_000), "small.txt": b"tiny",
        "hard/a.txt": _text(100_000, 5), "shared.txt": _text(120_000, 6), "unknown.txt": _text(120_000, 7),
        "already.txt": _text(120_000, 8),
    }
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    os.link(root / "hard/a.txt", root / "hard/b.txt")
    with open(root / "sparse.dat", "wb") as handle:
        handle.truncate(50 * 1024 * 1024)
        handle.write(b"x" * 4096)
    (tmp_path / "outside.txt").write_bytes(_text(100_000))
    os.symlink(tmp_path / "outside.txt", root / "link.txt")
    os.utime(root / "Logs/old.log", (1_000_000_000, 1_000_000_000))
    game = replace(demo_games()[0], id="steam-4242", install_path=root, library_path=tmp_path / "SteamLibrary",
                   steam_app_id="4242", launcher=Launcher.STEAM, filesystem=FilesystemType.BTRFS)
    analyzer = DeepOptimizationAnalyzer(
        engine_detector=GameAnalyzer(GameExecutableResolver()).detect_engine, filesystem_provider=BtrfsInspector(),
        system_language=lambda: "pl", running_detector=lambda _root: (), allow_native_btrfs_du=False,
    )
    repository = DeepAnalysisRepository(tmp_path / "data/deep-analysis")
    repository.save(analyzer.analyze(game))
    deep = repository.load(game.id)
    provider = FakeProvider(levels=levels, reset=reset)
    provider.shared["shared.txt"] = 40_000
    provider.missing_rows.add("unknown.txt")
    provider.encoded[os.stat(root / "already.txt").st_ino] = 1.0
    kwargs = {"total_sample_limit": total_sample_limit} if total_sample_limit else {}
    planner = BtrfsRecompressionPlanner(
        provider, root=tmp_path / "data/deep-recompression",
        executable_finder=lambda name: "/usr/bin/btrfs" if name == "btrfs" else None,
        filesystem_provider=BtrfsInspector(), disk_usage=lambda _path: SimpleNamespace(free=50 * 1024**3),
        statvfs=lambda _fd: SimpleNamespace(f_flag=0), fiemap=provider.fiemap, **kwargs,
    )
    return game, root, deep, repository, provider, planner


def _guards(**overrides) -> RecompressionGuards:
    values = {"game_running": lambda _g: False, "game_updating": lambda _g: False, "quarantine_active": lambda _g: False}
    values.update(overrides)
    return RecompressionGuards(**values)


def _plan(game, deep, repository, planner, level=3):
    return planner.plan(game, deep, repository.fingerprint(deep), level, measurement_source=deep.sizes.measurement_source)


def _by_path(plan) -> dict[str, Any]:
    return {item.relative_path: item for item in plan.candidates}


# A. Capabilities ------------------------------------------------------------------------------


def test_a_capabilities_with_and_without_zstd_levels() -> None:
    modern = _capabilities(levels=True)
    assert modern.btrfs_version == "7.1" and modern.algorithms == ("zlib", "lzo", "zstd")
    assert (modern.level_flag, modern.zstd_level_range, modern.levels) == ("-L", (-15, 15), (1, 3, 6))
    assert modern.flush_flag and modern.du_raw and modern.recompression_available
    assert modern.property_get and modern.property_set and not modern.future_property_available
    legacy = _capabilities(levels=False)
    assert legacy.algorithms == ("zlib", "lzo") and legacy.levels == () and not legacy.recompression_available
    assert _capabilities(reset=True).future_property_available


# B./C./D. Planning ------------------------------------------------------------------------------


def test_bcd_plan_skips_formats_unsafe_files_and_samples_boundedly(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    items = _by_path(plan)
    expected = {
        "Paks/core.pak": ("skipped", "core_archive"), "Textures/a.dds": ("skipped", "required_texture"),
        "Movies/intro.mp4": ("skipped", "format_skipped"), "Audio/music.ogg": ("skipped", "format_skipped"),
        "Archives/extra.zip": ("skipped", "format_skipped"), "Logs/old.log": ("skipped", "selected_for_removal"),
        "small.txt": ("skipped", "too_small"), "hard/a.txt": ("skipped", "hardlink"),
        "hard/b.txt": ("skipped", "hardlink"), "sparse.dat": ("skipped", "sparse"),
        "shared.txt": ("skipped", "shared_extents"), "unknown.txt": ("skipped", "shared_unknown"),
        "already.txt": ("already_compressed", ""), "data/random.bin": ("skipped", "low_compressibility"),
        "data/level.txt": ("candidate", ""), "data/table.csv": ("candidate", ""), "bin/game.dll": ("candidate", ""),
    }
    for relative, (status, reason) in expected.items():
        assert (items[relative].status, items[relative].reason) == (status, reason), relative
    assert "link.txt" not in items  # symlinks are never part of a plan
    for item in plan.candidates:
        assert item.sample_bytes <= 3 * SAMPLE_CHUNK
        if item.status == "candidate":
            assert item.shared_bytes == 0 and item.nlink == 1 and item.estimated_gain >= 0.10
            assert 0 <= item.estimated_savings_low <= item.estimated_savings_high <= item.size
    counts = plan.counts()
    assert counts["candidate"] == 3 and counts["already_compressed"] == 1
    assert provider.defragments() == []  # planning never recompresses
    du_calls = [call for call in provider.calls if call[1:3] == ["filesystem", "du"]]
    assert len(du_calls) == 1 and all(arg.startswith("/proc/self/fd/") for arg in du_calls[0][4:])


def test_c_large_file_is_sampled_not_read_completely(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    (root / "data/huge.txt").write_bytes(_text(12 * 1024 * 1024, 9))
    deep = repository.load(game.id)
    repository.save(DeepOptimizationAnalyzer(
        engine_detector=GameAnalyzer(GameExecutableResolver()).detect_engine, filesystem_provider=BtrfsInspector(),
        system_language=lambda: "pl", running_detector=lambda _r: (), allow_native_btrfs_du=False).analyze(game))
    deep = repository.load(game.id)
    huge = _by_path(_plan(game, deep, repository, planner))["data/huge.txt"]
    assert huge.status == "candidate" and huge.sample_bytes == 3 * SAMPLE_CHUNK < huge.size


# E./F. Generator and preflight -------------------------------------------------------------------------


def test_e_argv_is_single_file_without_recursion_and_uses_detected_syntax() -> None:
    modern = _capabilities(levels=True)
    assert build_defragment_argv("/usr/bin/btrfs", modern, "zstd", 3, "/proc/self/fd/9") == [
        "/usr/bin/btrfs", "filesystem", "defragment", "-f", "-czstd", "-L", "3", "/proc/self/fd/9"]
    without_levels = replace(modern, level_flag="", zstd_level_range=None)
    assert build_defragment_argv("/usr/bin/btrfs", without_levels, "zstd", None, "/proc/self/fd/9")[-2:] == [
        "-czstd", "/proc/self/fd/9"]
    with pytest.raises(RecompressionError):
        build_defragment_argv("/usr/bin/btrfs", without_levels, "zstd", 3, "/x")
    with pytest.raises(RecompressionError):
        build_defragment_argv("/usr/bin/btrfs", _capabilities(levels=False), "zstd", None, "/x")


def test_e_run_executes_each_file_alone_without_shell(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    job = BtrfsRecompressionExecutor(planner, _guards()).run(game, plan, deep, repository.fingerprint(deep), [".txt", ".csv", ".dll"])
    assert job.state == "completed" and job.counts()["compressed"] == 3
    for argv, kwargs in zip(provider.calls, provider.kwargs):
        if argv[1:3] == ["filesystem", "defragment"]:
            assert "-r" not in argv and "--recursive" not in argv and "compress-force" not in " ".join(argv)
            assert argv[-1].startswith("/proc/self/fd/") and len(kwargs["pass_fds"]) == 1
            assert argv[3:7] == ["-f", "-czstd", "-L", "3"]
            assert kwargs["cancel_event"] is None  # a running file is never killed
    assert len(provider.defragments()) == 3


@pytest.mark.parametrize("blocker", ["changed", "running", "updating", "quarantine", "read_only", "space"])
def test_f_preflight_blocks_everything_before_the_first_file(tmp_path: Path, blocker: str) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    guards = _guards()
    if blocker == "changed":
        os.utime(root / "data/table.csv", (1_000_000_000, 1_000_000_000))
    elif blocker == "running":
        guards = _guards(game_running=lambda _g: True)
    elif blocker == "updating":
        guards = _guards(game_updating=lambda _g: True)
    elif blocker == "quarantine":
        guards = _guards(quarantine_active=lambda _g: True)
    elif blocker == "read_only":
        planner._statvfs = lambda _fd: SimpleNamespace(f_flag=os.ST_RDONLY)
    else:
        planner._disk_usage = lambda _path: SimpleNamespace(free=1024)
    with pytest.raises(RecompressionError) as error:
        BtrfsRecompressionExecutor(planner, guards).run(game, plan, deep, repository.fingerprint(deep), [".txt", ".csv", ".dll"])
    assert error.value.code in {"plan_stale", "game_running", "game_updating", "quarantine_active",
                                "filesystem_read_only", "not_enough_space"}
    assert provider.defragments() == [] and planner.jobs(game.id) == []


def test_f_shared_extents_appearing_after_planning_block_the_job(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    provider.shared["level.txt"] = 4096
    with pytest.raises(RecompressionError) as error:
        BtrfsRecompressionExecutor(planner, _guards()).run(game, plan, deep, repository.fingerprint(deep), [".txt"])
    assert error.value.code == "shared_extents" and provider.defragments() == []


# G./H./I./J. Execution -------------------------------------------------------------------------------------


def test_g_cancel_stops_between_files_and_keeps_real_results(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    cancel = Event()
    provider.on_defragment = lambda _path: cancel.set()
    job = BtrfsRecompressionExecutor(planner, _guards()).run(
        game, plan, deep, repository.fingerprint(deep), [".txt", ".csv", ".dll"], cancel=cancel)
    assert (job.state, job.note) == ("cancelled", "cancelled_no_rollback")
    assert len(provider.defragments()) == 1
    assert [entry.status for entry in job.entries] == ["compressed", "pending", "pending"]
    assert job.entries[0].encoded_after == 1.0


def test_h_no_gain_stops_further_files_of_that_type(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    for index in range(5):
        (root / f"more/file{index}.txt").parent.mkdir(exist_ok=True)
        (root / f"more/file{index}.txt").write_bytes(_text(100_000, 20 + index))
    repository.save(DeepOptimizationAnalyzer(
        engine_detector=GameAnalyzer(GameExecutableResolver()).detect_engine, filesystem_provider=BtrfsInspector(),
        system_language=lambda: "pl", running_detector=lambda _r: (), allow_native_btrfs_du=False).analyze(game))
    deep = repository.load(game.id)
    plan = _plan(game, deep, repository, planner)
    original = provider._run_command

    def no_gain(argv, **kwargs):
        if argv[1:3] == ["filesystem", "defragment"]:
            provider.calls.append(list(argv))
            provider.kwargs.append({"pass_fds": tuple(kwargs.get("pass_fds", ())), "cancel_event": None})
            return subprocess.CompletedProcess(argv, 0, "", "")  # extents stay uncompressed
        return original(argv, **kwargs)

    provider._run_command = no_gain
    job = BtrfsRecompressionExecutor(planner, _guards()).run(game, plan, deep, repository.fingerprint(deep), [".txt"])
    statuses = [entry.status for entry in job.entries]
    assert statuses.count("no_gain") == module.NO_GAIN_LIMIT
    assert statuses.count("skipped") == len(statuses) - module.NO_GAIN_LIMIT
    assert (job.state, job.note) == ("partial", "no_gain_limit_new_analysis")
    assert len(provider.defragments()) == module.NO_GAIN_LIMIT


def test_i_content_change_stops_the_whole_job(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)

    def corrupt(path: str) -> None:
        info = os.stat(path)
        with open(path, "r+b") as handle:
            handle.write(b"Z")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))

    provider.on_defragment = corrupt
    job = BtrfsRecompressionExecutor(planner, _guards()).run(game, plan, deep, repository.fingerprint(deep), [".txt", ".csv", ".dll"])
    assert (job.state, job.note) == ("failed", "content_changed")
    assert len(provider.defragments()) == 1
    assert [entry.status for entry in job.entries][1:] == ["pending", "pending"]


def test_j_crash_is_never_resumed_and_requires_a_new_analysis(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    state = {"count": 0}

    def crash(_path: str) -> None:
        state["count"] += 1
        if state["count"] == 2:
            raise Crash()

    provider.on_defragment = crash
    with pytest.raises(Crash):
        BtrfsRecompressionExecutor(planner, _guards()).run(game, plan, deep, repository.fingerprint(deep), [".txt", ".csv", ".dll"])
    assert planner.jobs(game.id)[0].state == "running"
    recovered = planner.recover()
    assert len(recovered) == 1
    job = recovered[0]
    assert (job.state, job.note, job.recovered) == ("partial", "interrupted_new_analysis_required", True)
    assert [entry.status for entry in job.entries] == ["compressed", "interrupted", "pending"]
    assert job.entries[0].encoded_after == 1.0  # finished file was measured again
    assert len(provider.defragments()) == 2 and planner.recover() == []


# K. Future files property ----------------------------------------------------------------------------------


def test_k_future_property_requires_documented_restore(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    with pytest.raises(RecompressionError) as error:
        FutureCompressionProperty(planner).enable(game)
    assert error.value.code == "property_unavailable"
    assert not any(call[1:3] == ["property", "set"] for call in provider.calls)
    game, root, deep, repository, provider, planner = _setup(tmp_path / "reset", reset=True)
    future = FutureCompressionProperty(planner)
    assert future.enable(game) == {"previous": "lzo", "enabled": True}
    assert provider.property_value == "compression=zstd"
    set_calls = [call for call in provider.calls if call[1:3] == ["property", "set"]]
    assert set_calls[0][3:5] == ["-t", "inode"] and set_calls[0][-2:] == ["compression", "zstd"]
    future.restore(game)
    assert provider.property_value == "compression=lzo"


# M. Quarantine executor is unchanged ------------------------------------------------------------------------


def test_m_recompression_never_touches_the_quarantine_journal(tmp_path: Path) -> None:
    game, root, deep, repository, provider, planner = _setup(tmp_path)
    plan = _plan(game, deep, repository, planner)
    BtrfsRecompressionExecutor(planner, _guards()).run(game, plan, deep, repository.fingerprint(deep), [".txt"])
    assert not (tmp_path / "data/deep-cleanup").exists()
    assert (root / "Logs/old.log").exists()
