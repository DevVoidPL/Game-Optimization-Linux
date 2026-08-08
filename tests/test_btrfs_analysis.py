from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import (
    CapabilityStatus,
    FilesystemInfo,
    FilesystemType,
    Game,
    Launcher,
    SessionType,
    SystemInfo,
    TaskStatus,
)
from game_optimization_linux.services import (
    AnalysisCache,
    AnalysisCancelled,
    AnalysisLimits,
    BtrfsAnalysisTaskService,
    BtrfsCompressionAnalyzer,
    SettingsStore,
)


_QT_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _Filesystem:
    def __init__(
        self,
        filesystem: FilesystemType = FilesystemType.BTRFS,
        *,
        options: tuple[str, ...] = ("rw", "compress=zstd:3"),
        writable: bool = True,
    ) -> None:
        self.filesystem = filesystem
        self.options = options
        self.writable = writable

    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=self.filesystem,
            compression_supported=self.filesystem is FilesystemType.BTRFS,
            device="/dev/test",
            mount_options=self.options,
            writable=self.writable,
            filesystem_name=self.filesystem.value.casefold(),
        )

    def list_filesystems(self, **_: object) -> tuple[FilesystemInfo, ...]:
        return ()


def _game(path: Path, filesystem: FilesystemType = FilesystemType.BTRFS) -> Game:
    return Game(
        id="steam-4242",
        name="Temporary Game",
        launcher=Launcher.STEAM,
        install_path=path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=filesystem,
        compression_available=filesystem is FilesystemType.BTRFS,
        steam_app_id="4242",
        filesystem_name=filesystem.value.casefold(),
    )


def _analyzer(
    filesystem: FilesystemType = FilesystemType.BTRFS,
    *,
    compressor: object = lambda data, level: data[:: max(1, level)],
    finder: object = lambda name: None,
    runner: object = subprocess.run,
) -> BtrfsCompressionAnalyzer:
    return BtrfsCompressionAnalyzer(
        _Filesystem(filesystem),
        limits=AnalysisLimits(
            max_sample_bytes=2 * 1024 * 1024,
            max_bytes_per_file=256 * 1024,
            max_sample_candidates_per_group=32,
            timeout_seconds=5.0,
            command_timeout_seconds=1.0,
        ),
        compressor=compressor,  # type: ignore[arg-type]
        executable_finder=finder,  # type: ignore[arg-type]
        command_runner=runner,  # type: ignore[arg-type]
        process_detector=lambda path, cancelled: (),
    )


def test_analyzer_measures_sizes_counts_and_never_follows_symlinks(
    tmp_path: Path,
) -> None:
    game_path = tmp_path / "game"
    outside = tmp_path / "outside"
    (game_path / "data").mkdir(parents=True)
    outside.mkdir()
    first = game_path / "data" / "first.bin"
    second = game_path / "second.txt"
    first.write_bytes(b"a" * 8192)
    second.write_bytes(b"b" * 4096)
    (outside / "not-game.bin").write_bytes(b"x" * 1024 * 1024)
    (game_path / "outside-link").symlink_to(outside, target_is_directory=True)

    report = _analyzer().analyze(_game(game_path))

    expected_physical = sum(
        os.lstat(path).st_blocks * 512 for path in (first, second)
    )
    assert report.is_btrfs
    assert report.scan_complete
    assert report.logical_bytes == 8192 + 4096
    assert report.physical_bytes == expected_physical
    assert report.file_count == 2
    assert report.directory_count == 2
    assert report.symlink_count == 1
    assert report.profiles_unlocked
    assert report.persistent_compression_algorithm == "zstd"
    assert report.mount_compression_level == 3


@pytest.mark.parametrize("filesystem", [FilesystemType.EXT4, FilesystemType.NTFS])
def test_analyzer_rejects_non_btrfs_filesystems(
    tmp_path: Path, filesystem: FilesystemType
) -> None:
    (tmp_path / "payload").write_bytes(b"data")

    report = _analyzer(filesystem).analyze(_game(tmp_path, filesystem))

    assert not report.is_btrfs
    assert not report.profiles_unlocked
    assert not report.compression_eligible
    assert report.sampled_bytes == 0


def test_unreadable_directory_makes_scan_incomplete(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (tmp_path / "readable.bin").write_bytes(b"ok")
    analyzer = _analyzer()
    original = analyzer._open_verified_directory

    def open_directory(
        path: Path, *, expected_identity: tuple[int, int], root_real: str
    ) -> int:
        if path.name == "blocked":
            raise PermissionError("fixture permission denied")
        return original(
            path,
            expected_identity=expected_identity,
            root_real=root_real,
        )

    analyzer._open_verified_directory = open_directory  # type: ignore[method-assign]
    report = analyzer.analyze(_game(tmp_path))

    assert not report.scan_complete
    assert not report.profiles_unlocked
    assert any("permission denied" in error for error in report.permission_errors)


def test_root_symlink_is_rejected_without_traversal(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "payload").write_bytes(b"data")
    link = tmp_path / "game-link"
    link.symlink_to(actual, target_is_directory=True)

    report = _analyzer().analyze(_game(link))

    assert report.path_exists
    assert not report.path_is_directory
    assert not report.scan_complete
    assert report.logical_bytes == 0


def test_sample_parent_swap_to_outside_symlink_is_rejected(tmp_path: Path) -> None:
    game_path = tmp_path / "game"
    data_path = game_path / "data"
    outside = tmp_path / "outside"
    data_path.mkdir(parents=True)
    outside.mkdir()
    (data_path / "payload.bin").write_bytes(b"inside game data" * 100)
    (outside / "payload.bin").write_bytes(b"outside secret" * 100)
    compressed_inputs: list[bytes] = []
    swapped = False

    def compressor(data: bytes, level: int) -> bytes:
        del level
        compressed_inputs.append(data)
        return data

    def progress(update: object) -> None:
        nonlocal swapped
        if getattr(update, "stage", "") != "Measuring existing compression" or swapped:
            return
        swapped = True
        data_path.rename(game_path / "original-data")
        data_path.symlink_to(outside, target_is_directory=True)

    report = _analyzer(compressor=compressor).analyze(
        _game(game_path),
        progress_callback=progress,
    )

    assert swapped
    assert report.scan_complete
    assert report.sampled_bytes == 0
    assert not report.sampling_complete
    assert compressed_inputs == []
    assert any("data" in warning for warning in report.warnings)


def test_live_filesystem_detection_failure_is_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "payload").write_bytes(b"data")
    game = _game(tmp_path, FilesystemType.BTRFS)

    class BrokenFilesystem:
        def inspect(self, path: Path) -> FilesystemInfo:
            del path
            raise RuntimeError("fixture findmnt failure")

    analyzer = BtrfsCompressionAnalyzer(
        BrokenFilesystem(),
        limits=AnalysisLimits(
            max_sample_bytes=1024,
            max_bytes_per_file=1024,
            timeout_seconds=5,
            command_timeout_seconds=1,
        ),
        compressor=lambda data, level: data,
        executable_finder=lambda name: "/usr/bin/compsize",
        command_runner=lambda *args, **kwargs: pytest.fail(
            f"unexpected command after failed filesystem detection: {args!r}"
        ),
        process_detector=lambda path, cancelled: (),
    )

    report = analyzer.analyze(game)

    assert report.filesystem == FilesystemType.UNKNOWN.value
    assert not report.is_btrfs
    assert not report.profiles_unlocked
    assert not report.compression_eligible
    assert report.sampled_bytes == 0
    assert report.compsize.message == "Not run on a non-Btrfs filesystem"
    assert any("fixture findmnt failure" in warning for warning in report.warnings)


def test_compsize_parser_reports_compression_and_shared_extents() -> None:
    output = """
Processed 2 files, 3 regular extents (4 refs), 0 inline.
Type       Perc     Disk Usage   Uncompressed Referenced
TOTAL       50%      512K         1.0M       2.0M
none       100%      128K         128K       128K
zstd        42%      384K         896K       1.9M
"""

    result = BtrfsCompressionAnalyzer.parse_compsize(output)

    assert result.available
    assert result.disk_usage_bytes == 512 * 1024
    assert result.uncompressed_bytes == 1024 * 1024
    assert result.referenced_bytes == 2 * 1024 * 1024
    assert result.current_compression_ratio == 2.0
    assert result.saved_bytes == 512 * 1024
    assert result.compression_types == {"zstd": 384 * 1024}
    assert result.possible_shared_extents is True


def test_compsize_parser_preserves_raw_byte_precision() -> None:
    output = """
Type       Perc     Disk Usage   Uncompressed Referenced
TOTAL       90%      7516192768   8388608000   8388608000
none       100%      1073741824   1073741824   1073741824
zstd        88%      6442450944   7314866176   7314866176
"""

    result = BtrfsCompressionAnalyzer.parse_compsize(output)

    assert result.available
    assert result.disk_usage_bytes == 7516192768
    assert result.uncompressed_bytes == 8388608000
    assert result.referenced_bytes == 8388608000
    assert result.saved_bytes == 872415232
    assert result.compression_types == {"zstd": 6442450944}


def test_compsize_is_invoked_read_only_without_shell(tmp_path: Path) -> None:
    (tmp_path / "payload").write_bytes(b"compressible" * 100)
    calls: list[tuple[list[str], dict[str, object]]] = []
    output = (
        "Type Perc Disk Usage Uncompressed Referenced\n"
        "TOTAL 50% 512K 1M 1M\n"
        "zstd 50% 512K 1M 1M\n"
    )

    def finder(name: str) -> str | None:
        return "/usr/bin/compsize" if name == "compsize" else None

    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    report = _analyzer(finder=finder, runner=runner).analyze(_game(tmp_path))

    assert report.compsize.available
    assert calls == [
        (
            [
                "/usr/bin/compsize",
                "--bytes",
                "--one-file-system",
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
    assert all("sudo" not in argument for argument in calls[0][0])


def test_preflight_can_skip_unprivileged_compsize_for_privileged_baseline(
    tmp_path: Path,
) -> None:
    (tmp_path / "payload").write_bytes(b"compressible" * 100)
    calls: list[list[str]] = []

    def finder(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in {"btrfs", "compsize"} else None

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        if command[0].endswith("/compsize"):
            raise AssertionError("unprivileged compsize must not run")
        return SimpleNamespace(
            returncode=0,
            stdout=f"1000 1000 0 {tmp_path}",
            stderr="",
        )

    report = _analyzer(finder=finder, runner=runner).analyze(
        _game(tmp_path),
        sample_files=False,
        measure_compsize=False,
    )

    assert not report.compsize.available
    assert report.compsize.message == "Supplied by privileged baseline measurement"
    assert calls
    assert all(not command[0].endswith("/compsize") for command in calls)


def test_missing_compsize_and_zstd_produce_no_invented_estimate(
    tmp_path: Path,
) -> None:
    (tmp_path / "payload").write_bytes(b"compressible" * 1000)
    analyzer = _analyzer(compressor=None, finder=lambda name: None)

    report = analyzer.analyze(_game(tmp_path))

    assert not report.compsize.available
    assert report.compsize.message == "compsize not installed"
    assert report.sampling_codec == "unavailable"
    assert report.sampled_bytes == 0
    assert all(not estimate.estimated for estimate in report.profiles.values())
    assert all(
        estimate.estimated_savings_low_bytes is None
        for estimate in report.profiles.values()
    )


def test_profiles_use_levels_1_3_9_and_auto_avoids_pointless_level_9(
    tmp_path: Path,
) -> None:
    (tmp_path / "payload").write_bytes((b"abcd" * 64 * 1024))
    analyzer = _analyzer(compressor=lambda data, level: data[: len(data) // 2])

    report = analyzer.analyze(_game(tmp_path))

    assert report.profiles["Fast"].one_time_recompression_level == 1
    assert report.profiles["Balanced"].one_time_recompression_level == 3
    assert report.profiles["Maximum"].one_time_recompression_level == 9
    assert report.profiles["Auto"].one_time_recompression_level in {1, 3, 6}
    assert report.profiles["Maximum"].persistent_compression_algorithm == "zstd"


def test_analyzer_cancellation_stops_before_work(tmp_path: Path) -> None:
    (tmp_path / "payload").write_bytes(b"data")
    cancelled = Event()
    cancelled.set()

    with pytest.raises(AnalysisCancelled):
        _analyzer().analyze(_game(tmp_path), cancel_event=cancelled)


def test_analysis_cache_is_atomic_and_invalidates_nested_updates(
    tmp_path: Path,
) -> None:
    game_path = tmp_path / "game"
    nested = game_path / "data"
    nested.mkdir(parents=True)
    payload = nested / "payload.bin"
    payload.write_bytes(b"before")
    game = _game(game_path)
    report = _analyzer().analyze(game)
    cache = AnalysisCache(tmp_path / "cache" / "analysis.json")

    cache.save(game, report)

    restored = cache.load(game)
    assert restored is not None
    assert restored.to_dict() == report.to_dict()
    assert not list(cache.path.parent.glob("*.tmp"))
    assert not list(cache.path.parent.glob(".*.tmp"))

    payload.write_bytes(b"after and a different size")
    assert cache.load(game) is None
    assert cache.load(replace(game, install_path=tmp_path / "moved")) is None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"same-1", b"same-2"),
        (b"short", b"a substantially larger replacement"),
    ],
)
def test_analysis_cache_invalidates_deep_file_updates(
    tmp_path: Path,
    before: bytes,
    after: bytes,
) -> None:
    game_path = tmp_path / "game"
    deep = game_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    payload = deep / "payload.bin"
    payload.write_bytes(before)
    game = _game(game_path)
    report = _analyzer().analyze(game)
    cache = AnalysisCache(tmp_path / "cache" / "analysis.json")
    cache.save(game, report)
    assert cache.load(game) is not None

    original = payload.stat()
    payload.write_bytes(after)
    updated = payload.stat()
    os.utime(
        payload,
        ns=(
            original.st_atime_ns,
            max(original.st_mtime_ns + 1, updated.st_mtime_ns),
        ),
    )

    assert cache.load(game) is None


def test_analysis_cache_signature_never_follows_directory_symlinks(
    tmp_path: Path,
) -> None:
    game_path = tmp_path / "game"
    outside = tmp_path / "outside"
    game_path.mkdir()
    outside.mkdir()
    (game_path / "payload.bin").write_bytes(b"game data")
    outside_payload = outside / "outside.bin"
    outside_payload.write_bytes(b"outside-before")
    (game_path / "outside-link").symlink_to(outside, target_is_directory=True)
    game = _game(game_path)
    report = _analyzer().analyze(game)
    cache = AnalysisCache(tmp_path / "cache" / "analysis.json")
    cache.save(game, report)

    outside_payload.write_bytes(b"outside-after-and-larger")

    assert cache.load(game) is not None


def test_analysis_cache_signature_fails_closed_when_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    (tmp_path / "first.bin").write_bytes(b"one")
    (tmp_path / "second.bin").write_bytes(b"two")

    signature = AnalysisCache._tree_signature(tmp_path, max_entries=1)

    assert signature["complete"] is False
    assert signature["reason"] == "entry-budget-exceeded"


def test_analysis_cache_signature_honors_cancellation(tmp_path: Path) -> None:
    (tmp_path / "payload.bin").write_bytes(b"data")
    cancelled = Event()
    cancelled.set()

    with pytest.raises(AnalysisCancelled):
        AnalysisCache._tree_signature(tmp_path, cancel_event=cancelled)


def test_async_task_can_be_cancelled_and_never_returns_partial_result(
    tmp_path: Path,
) -> None:
    started = Event()

    class BlockingAnalyzer:
        def analyze(
            self,
            game: Game,
            *,
            cancel_event: Event,
            progress_callback: object,
        ) -> object:
            del game, progress_callback
            started.set()
            while not cancel_event.wait(0.005):
                pass
            raise AnalysisCancelled("cancelled fixture")

    cache = AnalysisCache(tmp_path / "cache" / "analysis.json")
    service = BtrfsAnalysisTaskService(
        analyzer=BlockingAnalyzer(),  # type: ignore[arg-type]
        cache=cache,
    )
    task = service.enqueue_analysis(_game(tmp_path))
    assert started.wait(1.0)

    cancelled = service.cancel(task.id)
    finished = service.wait_for(task.id, timeout=1.0)
    service.shutdown()

    assert cancelled.status is TaskStatus.CANCELLED
    assert finished.status is TaskStatus.CANCELLED
    assert finished.result is None
    assert finished.metadata["temporary_data_removed"] is True
    assert not cache.path.exists()
    if cache.path.parent.exists():
        assert not list(cache.path.parent.glob("*.tmp"))


def test_analyzer_opens_game_data_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "payload").write_bytes(b"read-only data" * 100)
    opened_flags: list[int] = []
    real_open = os.open

    def recording_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        opened_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("game_optimization_linux.services.btrfs_analysis.os.open", recording_open)

    report = _analyzer().analyze(_game(tmp_path))

    forbidden = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    assert report.scan_complete
    assert opened_flags
    assert all(flags & forbidden == 0 for flags in opened_flags)
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if nonblocking:
        assert all(flags & nonblocking for flags in opened_flags)


def test_read_only_helper_process_honors_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = _analyzer(compressor=None)
    cancelled = Event()
    started = Event()
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

    def run_helper() -> None:
        try:
            analyzer._run_read_only_command(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                text=True,
                cancel_event=cancelled,
                deadline=time.monotonic() + 10.0,
            )
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=run_helper, daemon=True)
    worker.start()
    assert started.wait(1.0)
    cancelled.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], AnalysisCancelled)
    assert processes and all(process.poll() is not None for process in processes)


def test_read_only_helper_process_honors_global_deadline(tmp_path: Path) -> None:
    analyzer = _analyzer(compressor=None)
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        analyzer._run_read_only_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            text=True,
            cancel_event=None,
            deadline=started + 0.12,
        )

    assert time.monotonic() - started < 2.0


class _Provider:
    def __init__(self, game: Game) -> None:
        self.game = game

    def list_games(self) -> tuple[Game, ...]:
        return (self.game,)

    def get_game(self, game_id: str) -> Game | None:
        return self.game if game_id == self.game.id else None

    def refresh(self) -> tuple[Game, ...]:
        return (self.game,)


class _System:
    def collect(self) -> SystemInfo:
        return SystemInfo(
            distribution="Test Linux",
            kernel="test",
            desktop_environment="test",
            session_type=SessionType.UNKNOWN,
            cpu="test",
            gpu="test",
            ram_gb=1.0,
            vram_gb=0.0,
            capabilities={"Btrfs": CapabilityStatus.AVAILABLE},
        )


def test_controller_polls_real_analysis_and_attaches_report(tmp_path: Path) -> None:
    game_path = tmp_path / "game"
    game_path.mkdir()
    (game_path / "payload").write_bytes(b"payload" * 1000)
    game = _game(game_path)
    service = BtrfsAnalysisTaskService(analyzer=_analyzer())
    controller = AppController(
        game_provider=_Provider(game),
        filesystem_provider=_Filesystem(),
        task_service=service,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        system_provider=_System(),
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.openGame(game.id)
        assert controller.analyzeGame(game.id)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            _QT_APPLICATION.processEvents()
            if controller.selectedGame.get("analysisReport", {}).get("scan_complete"):
                break
            time.sleep(0.01)

        assert controller.tasks[0]["status"] == "completed"
        assert controller.tasks[0]["readOnly"] is True
        assert controller.selectedGame["analysisReport"]["profiles_unlocked"] is True
    finally:
        controller.shutdown()
