from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest

from game_optimization_linux.models import FilesystemInfo, FilesystemType, Game, Launcher
from game_optimization_linux.services import (
    ANALYZER_VERSION,
    AnalysisCache,
    AnalysisCancelled,
    AnalysisLimits,
    BtrfsAnalysisReport,
    BtrfsCompressionAnalyzer,
    BtrfsDuResult,
)


class _BtrfsFilesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            device="/dev/test",
            mount_options=("rw",),
            writable=True,
            filesystem_name="btrfs",
        )


def _game(path: Path) -> Game:
    return Game(
        id="steam-shared-extents",
        name="Shared Extent Fixture",
        launcher=Launcher.STEAM,
        install_path=path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id="919191",
        filesystem_name="btrfs",
    )


def _limits() -> AnalysisLimits:
    return AnalysisLimits(
        max_sample_bytes=1024 * 1024,
        max_bytes_per_file=256 * 1024,
        max_sample_candidates_per_group=8,
        timeout_seconds=5.0,
        command_timeout_seconds=1.0,
    )


def _analyzer(
    *,
    finder: object,
    runner: object = subprocess.run,
) -> BtrfsCompressionAnalyzer:
    return BtrfsCompressionAnalyzer(
        _BtrfsFilesystem(),
        limits=_limits(),
        command_runner=runner,  # type: ignore[arg-type]
        executable_finder=finder,  # type: ignore[arg-type]
        compressor=lambda data, level: data,
        process_detector=lambda path, cancelled: (),
    )


def test_btrfs_du_parser_detects_and_serializes_shared_extents() -> None:
    output = """
     Total   Exclusive  Set shared  Filename
     12288        4096        4096  /games/Game With Spaces
    """

    result = BtrfsCompressionAnalyzer.parse_btrfs_du(output)

    assert result.available is True
    assert result.state == "detected"
    assert result.shared_extents is True
    assert result.total_bytes == 12288
    assert result.exclusive_bytes == 4096
    assert result.set_shared_bytes == 4096
    assert result.estimated_growth_bytes == 8192
    assert BtrfsDuResult.from_dict(result.to_dict()) == result


def test_btrfs_du_parser_reports_confirmed_absence() -> None:
    result = BtrfsCompressionAnalyzer.parse_btrfs_du(
        "     Total Exclusive Set shared Filename\n"
        "      8192      8192          0 /games/Unshared\n"
    )

    assert result.available is True
    assert result.state == "not_detected"
    assert result.shared_extents is False
    assert result.estimated_growth_bytes == 0


def test_btrfs_du_parser_detects_sharing_outside_single_path_set() -> None:
    result = BtrfsCompressionAnalyzer.parse_btrfs_du(
        "8192 4096 0 /games/one-reflink"
    )

    assert result.available is True
    assert result.state == "detected"
    assert result.set_shared_bytes == 0
    assert result.estimated_growth_bytes == 4096
    assert BtrfsDuResult.from_dict(result.to_dict()) == result


def test_btrfs_du_cache_state_mismatch_fails_closed() -> None:
    result = BtrfsDuResult.from_dict(
        {
            "available": True,
            "state": "not_detected",
            "total_bytes": 8192,
            "exclusive_bytes": 0,
            "set_shared_bytes": 4096,
            "estimated_growth_bytes": 8192,
            "message": "tampered fixture",
        }
    )

    assert result.available is False
    assert result.state == "unknown"
    assert result.shared_extents is None


@pytest.mark.parametrize(
    "output",
    (
        "",
        "Total Exclusive Set shared Filename\nnot-a-report",
        "4096 8192 0 /exclusive-larger-than-total",
        "8192 8192 0 /one\n4096 4096 0 /two",
    ),
)
def test_btrfs_du_parser_fails_closed_on_untrusted_output(output: str) -> None:
    result = BtrfsCompressionAnalyzer.parse_btrfs_du(output)

    assert result.available is False
    assert result.state == "unknown"
    assert result.shared_extents is None
    assert result.total_bytes is None
    assert result.estimated_growth_bytes is None


def test_btrfs_du_uses_read_only_argv_without_shell(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def finder(name: str) -> str | None:
        return "/usr/bin/btrfs" if name == "btrfs" else None

    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="4096 4096 0 " + str(tmp_path) + "\n",
            stderr="",
        )

    result = _analyzer(finder=finder, runner=runner)._measure_btrfs_du(
        tmp_path,
        None,
        deadline=time.monotonic() + 5.0,
    )

    assert result.state == "not_detected"
    assert calls == [
        (
            [
                "/usr/bin/btrfs",
                "filesystem",
                "du",
                "--raw",
                "--summarize",
                str(tmp_path),
            ],
            {
                "capture_output": True,
                "text": True,
                "check": False,
                "timeout": 1.0,
                "shell": False,
            },
        )
    ]
    flattened = " ".join(calls[0][0]).casefold()
    for forbidden in (
        "inspect-internal",
        "dump-tree",
        "defrag",
        "property set",
        "chattr",
        "sudo",
        "pkexec",
    ):
        assert forbidden not in flattened


def test_btrfs_du_missing_command_is_unknown_and_runs_nothing(
    tmp_path: Path,
) -> None:
    def unexpected_runner(*args: object, **kwargs: object) -> object:
        pytest.fail(f"unexpected subprocess call: {args!r} {kwargs!r}")

    result = _analyzer(
        finder=lambda name: None,
        runner=unexpected_runner,
    )._measure_btrfs_du(
        tmp_path,
        None,
        deadline=time.monotonic() + 5.0,
    )

    assert result.state == "unknown"
    assert result.available is False
    assert result.message == "btrfs command not installed"


def test_btrfs_du_nonzero_exit_is_unknown(tmp_path: Path) -> None:
    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        del command, kwargs
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR: not a btrfs filesystem",
        )

    result = _analyzer(
        finder=lambda name: "/usr/bin/btrfs" if name == "btrfs" else None,
        runner=runner,
    )._measure_btrfs_du(
        tmp_path,
        None,
        deadline=time.monotonic() + 5.0,
    )

    assert result.state == "unknown"
    assert result.available is False
    assert "status 1" in result.message
    assert "not a btrfs filesystem" in result.message


def test_btrfs_du_helper_is_cancelled_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "slow-btrfs"
    helper.write_text(
        f"#!{sys.executable}\n"
        "import time\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)

    started = Event()
    cancelled = Event()
    errors: list[BaseException] = []
    processes: list[subprocess.Popen[object]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[object]:
        process = real_popen(*args, **kwargs)  # type: ignore[call-overload]
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(
        "game_optimization_linux.services.btrfs_analysis.subprocess.Popen",
        recording_popen,
    )
    analyzer = _analyzer(finder=lambda name: str(helper) if name == "btrfs" else None)

    def measure() -> None:
        try:
            analyzer._measure_btrfs_du(
                tmp_path,
                cancelled,
                deadline=time.monotonic() + 5.0,
            )
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=measure, daemon=True)
    worker.start()
    assert started.wait(1.0)
    cancelled.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], AnalysisCancelled)
    assert processes and all(process.poll() is not None for process in processes)


def test_analysis_refresh_and_cache_preserve_fail_closed_extent_state(
    tmp_path: Path,
) -> None:
    game_path = tmp_path / "game"
    game_path.mkdir()
    (game_path / "payload.bin").write_bytes(b"fixture" * 1024)
    outputs = iter(
        (
            "16384 4096 8192 " + str(game_path) + "\n",
            "16384 16384 0 " + str(game_path) + "\n",
        )
    )

    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        del command, kwargs
        return SimpleNamespace(returncode=0, stdout=next(outputs), stderr="")

    analyzer = _analyzer(
        finder=lambda name: "/usr/bin/btrfs" if name == "btrfs" else None,
        runner=runner,
    )
    game = _game(game_path)
    report = analyzer.analyze(game, sample_files=False)

    assert ANALYZER_VERSION >= 2
    assert report.btrfs_du.state == "detected"
    assert report.possible_shared_extents is True
    assert any(
        "recursive defragmentation" in warning
        for warning in report.warnings
    )

    serialized = report.to_dict()
    assert BtrfsAnalysisReport.from_dict(serialized).btrfs_du == report.btrfs_du
    cache = AnalysisCache(tmp_path / "cache" / "analysis.json")
    cache.save(game, report)
    cached = cache.load(game)
    assert cached is not None
    assert cached.btrfs_du == report.btrfs_du

    refreshed = analyzer.refresh_cached_report(game, cached)
    assert refreshed.btrfs_du.state == "not_detected"
    assert refreshed.possible_shared_extents is False
    assert not any(
        "Shared Btrfs extents/reflinks were detected" in warning
        for warning in refreshed.warnings
    )


def test_shared_extent_analysis_never_opens_game_data_for_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_path = tmp_path / "game"
    game_path.mkdir()
    (game_path / "payload.bin").write_bytes(b"read-only fixture" * 512)
    opened_flags: list[int] = []
    real_open = os.open

    def recording_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        opened_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        del command, kwargs
        return SimpleNamespace(
            returncode=0,
            stdout="4096 4096 0 " + str(game_path) + "\n",
            stderr="",
        )

    monkeypatch.setattr(
        "game_optimization_linux.services.btrfs_analysis.os.open",
        recording_open,
    )
    report = _analyzer(
        finder=lambda name: "/usr/bin/btrfs" if name == "btrfs" else None,
        runner=runner,
    ).analyze(_game(game_path), sample_files=False)

    forbidden = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    assert report.btrfs_du.state == "not_detected"
    assert opened_flags
    assert all(flags & forbidden == 0 for flags in opened_flags)
    assert report.to_dict()["read_only"] is True


def test_real_reflink_is_detected_before_any_defragmentation(
    tmp_path: Path,
) -> None:
    findmnt = shutil.which("findmnt")
    copy = shutil.which("cp")
    btrfs = shutil.which("btrfs")
    if findmnt is None or copy is None or btrfs is None:
        pytest.skip("findmnt, cp and btrfs are required for the reflink integration test")
    mounted = subprocess.run(
        [findmnt, "--noheadings", "--output", "FSTYPE", "--target", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        shell=False,
    )
    if mounted.returncode != 0 or mounted.stdout.strip().casefold() != "btrfs":
        pytest.skip("temporary test directory is not on Btrfs")

    game_path = tmp_path / "game"
    game_path.mkdir()
    source = game_path / "original.bin"
    clone = game_path / "reflink.bin"
    with source.open("wb") as handle:
        handle.write((b"Game Optimization reflink integration fixture\n" * 131072)[:4 * 1024 * 1024])
        handle.flush()
        os.fsync(handle.fileno())
    copied = subprocess.run(
        [copy, "--reflink=always", "--", str(source), str(clone)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        shell=False,
    )
    if copied.returncode != 0:
        pytest.skip(f"reflink creation is unavailable: {copied.stderr.strip()}")

    analyzer = BtrfsCompressionAnalyzer(
        limits=_limits(),
        executable_finder=lambda name: btrfs if name == "btrfs" else None,
        compressor=lambda data, level: data,
        process_detector=lambda path, cancelled: (),
    )
    report = analyzer.analyze(_game(game_path), sample_files=False)

    assert report.is_btrfs is True
    assert report.btrfs_du.available is True
    assert report.btrfs_du.state == "detected"
    assert (report.btrfs_du.set_shared_bytes or 0) > 0
    assert report.possible_shared_extents is True
    assert any("must remain blocked" in warning for warning in report.warnings)
