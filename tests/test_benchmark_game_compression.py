from __future__ import annotations

import io
import importlib.util
import json
import errno
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "benchmark_game_compression.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "gameforge_benchmark_game_compression",
    _TOOL_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = benchmark
_SPEC.loader.exec_module(benchmark)


def _capabilities(
    *,
    zstd: bool = False,
    zlib: bool = True,
) -> benchmark.CapabilityReport:
    return benchmark.CapabilityReport(
        btrfs_path="/usr/bin/btrfs",
        btrfs_version="btrfs-progs v7.1",
        btrfs_help="fixture",
        kernel_release="test-kernel",
        btrfs_zstd=zstd,
        btrfs_zlib=zlib,
        btrfs_level_syntax=True,
        btrfs_zstd_level_range=(-15, 15) if zstd else None,
        btrfs_zlib_level_range=(1, 9) if zlib else None,
        btrfs_zstd_levels=(1, 3) if zstd else (),
        btrfs_zlib_levels=(1, 6) if zlib else (),
        zstd_path=None,
        zstd_version=None,
        zstd_help="",
        xz_path=None,
        xz_version=None,
        evidence=("fixture",),
    )


def test_scan_is_read_only_groups_extensions_and_skips_symlinks(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Game"
    (game / "Content").mkdir(parents=True)
    (game / "Config").mkdir()
    payload = game / "Content" / "one.PAK"
    payload.write_bytes(b"A" * 4096)
    (game / "Content" / "two.pak").write_bytes(b"B" * 1024)
    (game / "Config" / "settings.ini").write_text("quality=high")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    (game / "outside-link").symlink_to(outside)
    (game / "directory-link").symlink_to(tmp_path, target_is_directory=True)
    before = {
        path.relative_to(game): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in (payload, game / "Content" / "two.pak", game / "Config" / "settings.ini")
    }

    result = benchmark.scan_game(game)
    groups = benchmark.group_files(result)

    assert result.logical_bytes == 4096 + 1024 + len("quality=high")
    assert len(result.files) == 3
    assert result.symlinks_skipped == 2
    assert {(item.directory, item.extension) for item in groups} == {
        ("Content", ".pak"),
        ("Config", ".ini"),
    }
    after = {
        path.relative_to(game): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in (payload, game / "Content" / "two.pak", game / "Config" / "settings.ini")
    }
    assert after == before


def test_root_symlink_is_rejected(tmp_path: Path) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(game, target_is_directory=True)

    with pytest.raises(OSError):
        benchmark.scan_game(alias)


def test_descriptor_walk_blocks_directory_swapped_for_symlink(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Game"
    original = game / "Content"
    original.mkdir(parents=True)
    (original / "asset.pak").write_bytes(b"original")
    scan = benchmark.scan_game(game)
    plan = benchmark.build_sampling_plan(
        scan,
        sample_limit=128 * 1024,
        window_size=128 * 1024,
    )
    moved = game / "Content-old"
    original.rename(moved)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "asset.pak").write_bytes(b"external-secret")
    original.symlink_to(outside, target_is_directory=True)

    with tempfile.TemporaryFile() as destination:
        with pytest.raises(OSError):
            benchmark.materialize_sample(scan, plan, destination)


def test_sampling_is_capped_stratified_and_uses_multiple_offsets(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Game"
    (game / "Paks").mkdir(parents=True)
    (game / "Media").mkdir()
    (game / "Paks" / "huge.pak").write_bytes(
        bytes(range(256)) * 4096
    )
    (game / "Media" / "medium.bin").write_bytes(b"M" * (512 * 1024))
    for index in range(16):
        (game / "Media" / f"small-{index}.txt").write_bytes(
            bytes([index]) * (32 * 1024)
        )
    scan = benchmark.scan_game(game)

    plan = benchmark.build_sampling_plan(
        scan,
        sample_limit=1024 * 1024,
        window_size=128 * 1024,
    )

    assert plan.sampled_bytes <= 1024 * 1024
    assert plan.sampled_bytes >= 896 * 1024
    assert {item.file.extension for item in plan.slices} >= {
        ".pak",
        ".bin",
        ".txt",
    }
    huge_offsets = {
        item.offset
        for item in plan.slices
        if item.file.relative_path == "Paks/huge.pak"
    }
    assert len(huge_offsets) >= 3
    assert max(huge_offsets) >= 512 * 1024


def test_materialization_reads_in_bounded_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    (game / "large.bin").write_bytes(b"X" * (3 * 1024 * 1024))
    scan = benchmark.scan_game(game)
    plan = benchmark.build_sampling_plan(
        scan,
        sample_limit=3 * 1024 * 1024,
        window_size=2 * 1024 * 1024,
    )
    requested: list[int] = []
    real_pread = os.pread

    def tracking_pread(fd: int, length: int, offset: int) -> bytes:
        requested.append(length)
        return real_pread(fd, length, offset)

    monkeypatch.setattr(benchmark.os, "pread", tracking_pread)
    with tempfile.TemporaryFile() as destination:
        written, _groups, segments = benchmark.materialize_sample(
            scan,
            plan,
            destination,
        )

    assert written == plan.sampled_bytes
    assert sum(item.length for item in segments) == written
    assert requested
    assert max(requested) <= benchmark.READ_CHUNK


def test_btrfs_compression_resets_context_at_128_kib_boundaries() -> None:
    data = b"A" * (2 * benchmark.BTRFS_EXTENT_CHUNK + 17)
    source = io.BytesIO(data)
    segments = [
        benchmark.MaterializedSegment(0, len(data), ("Content", ".pak"))
    ]

    class FakeZstd:
        def __init__(self) -> None:
            self.lengths: list[int] = []

        def compress(self, chunk: bytes, level: int) -> int:
            assert level == 3
            self.lengths.append(len(chunk))
            return len(chunk) // 2

    fake = FakeZstd()
    result = benchmark._compress_btrfs_blocks(
        source,
        "zstd",
        3,
        segments=segments,
        byte_limit=len(data),
        cancel=benchmark.CancellationToken(),
        sectorsize=1,
        zstd_library=fake,  # type: ignore[arg-type]
    )

    assert fake.lengths == [
        benchmark.BTRFS_EXTENT_CHUNK,
        benchmark.BTRFS_EXTENT_CHUNK,
        17,
    ]
    assert result == sum(length // 2 for length in fake.lengths)


def test_btrfs_incompressible_chunks_never_report_negative_savings() -> None:
    data = b"A" * benchmark.BTRFS_EXTENT_CHUNK

    class GrowingCompressor:
        @staticmethod
        def compress(chunk: bytes, _level: int) -> int:
            return len(chunk) + 500

    result = benchmark._compress_btrfs_blocks(
        io.BytesIO(data),
        "zstd",
        3,
        segments=[
            benchmark.MaterializedSegment(0, len(data), (".", ".bin"))
        ],
        byte_limit=len(data),
        cancel=benchmark.CancellationToken(),
        sectorsize=4096,
        zstd_library=GrowingCompressor(),  # type: ignore[arg-type]
    )

    assert result == len(data)


def test_reference_copy_is_stratified_instead_of_prefix_only() -> None:
    source = tempfile.TemporaryFile()
    destination = tempfile.TemporaryFile()
    try:
        source.write(b"A" * 1000)
        source.write(b"B" * 1000)
        source.seek(0)
        written = benchmark._copy_stratified_reference(
            source,
            [
                benchmark.MaterializedSegment(0, 1000, ("A", ".a")),
                benchmark.MaterializedSegment(1000, 1000, ("B", ".b")),
            ],
            200,
            destination,
            cancel=benchmark.CancellationToken(),
        )
        payload = destination.read()
    finally:
        source.close()
        destination.close()

    assert written == 200
    assert payload.count(b"A") == 100
    assert payload.count(b"B") == 100


def test_whole_game_estimate_weights_groups_by_logical_size() -> None:
    huge_incompressible = ("Paks", ".ucas")
    small_compressible = ("Config", ".txt")

    projected = benchmark._weighted_game_compressed_bytes(
        {
            huge_incompressible: 1.0,
            small_compressible: 0.0,
        },
        {
            huge_incompressible: 1000,
            small_compressible: 100,
        },
    )

    # Equal sample budgets would misleadingly suggest a 50% whole-game ratio.
    # Weighting by the installed payload correctly projects 1000 / 1100.
    assert projected == 1000
    result = benchmark._algorithm_result(
        algorithm_id="fixture",
        family="zstd",
        level=3,
        role="btrfs",
        btrfs_compatible=True,
        source_bytes=200,
        compressed_bytes=100,
        total_bytes=1100,
        uncertainty_margin=0.01,
        estimated_game_compressed_bytes=projected,
        group_ratios={
            huge_incompressible: 1.0,
            small_compressible: 0.0,
        },
    )
    assert result["sample_compression_ratio"] == 0.5
    assert result["compression_ratio"] == pytest.approx(1000 / 1100)
    assert (
        result[
            "estimated_total_payload_reduction_from_uncompressed_baseline_bytes"
        ]
        == 100
    )
    assert result["estimated_incremental_disk_savings_bytes"] is None
    assert result["group_weighted"] is True


def test_large_game_pilot_below_512_mib_is_not_recommendation_grade(
    tmp_path: Path,
) -> None:
    key = ("Paks", ".ucas")
    scan = benchmark.ScanResult(
        root=tmp_path,
        root_device=1,
        files=[],
        directory_count=1,
        logical_bytes=1024 * 1024 * 1024,
        symlinks_skipped=0,
        cross_filesystem_skipped=0,
        special_files_skipped=0,
        permission_errors=[],
    )
    plan = benchmark.SamplingPlan(
        slices=[],
        group_logical_bytes={key: scan.logical_bytes},
        group_sampled_bytes={key: 128 * 1024 * 1024},
        sample_limit=128 * 1024 * 1024,
        window_size=4 * 1024 * 1024,
    )

    reliable, reasons = benchmark._is_reliable(
        scan,
        plan,
        {key: (0, 128 * 1024 * 1024)},
    )

    assert reliable is False
    assert "less than the minimum representative byte sample" in reasons


def test_cluster_tail_shortfall_within_half_mib_is_accepted(
    tmp_path: Path,
) -> None:
    keys = (("A", ".a"), ("B", ".b"), ("C", ".c"))
    sampled = benchmark.DEFAULT_SAMPLE_LIMIT - 234_554
    record = benchmark.FileRecord(
        components=("asset.bin",),
        size=sampled,
        device=1,
        inode=1,
        mtime_ns=1,
        extension=".a",
        directory_group="A",
    )
    slice_base, slice_residue = divmod(sampled, 8)
    slices = []
    slice_offset = 0
    for index in range(8):
        slice_length = slice_base + (1 if index < slice_residue else 0)
        slices.append(
            benchmark.SampleSlice(record, slice_offset, slice_length)
        )
        slice_offset += slice_length
    logical = 1024 * 1024 * 1024
    scan = benchmark.ScanResult(
        root=tmp_path,
        root_device=1,
        files=[],
        directory_count=1,
        logical_bytes=logical,
        symlinks_skipped=0,
        cross_filesystem_skipped=0,
        special_files_skipped=0,
        permission_errors=[],
    )
    plan = benchmark.SamplingPlan(
        slices=slices,
        group_logical_bytes={
            keys[0]: logical // 2,
            keys[1]: logical // 4,
            keys[2]: logical - 3 * logical // 4,
        },
        group_sampled_bytes={keys[0]: sampled // 2, keys[1]: sampled // 4, keys[2]: sampled // 4},
        sample_limit=benchmark.DEFAULT_SAMPLE_LIMIT,
        window_size=4 * 1024 * 1024,
        group_size_strata_distribution_tv={key: 0.0 for key in keys},
    )

    reliable, reasons = benchmark._is_reliable(
        scan,
        plan,
        {key: (0, plan.group_sampled_bytes[key]) for key in keys},
    )

    assert reliable is True
    assert "less than the minimum representative byte sample" not in reasons


def test_capabilities_are_derived_from_help_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    responses = {
        ("btrfs", "--version"): ("btrfs-progs v7.1\n", ""),
        (
            "btrfs",
            "filesystem",
            "defragment",
            "--help",
        ): (
            "-c[zlib,lzo,zstd]\n"
            "--level level (zlib: 1..9, zstd: -15..15)\n",
            "",
        ),
        ("zstd", "--version"): ("zstd 1.5.7\n", ""),
        ("zstd", "--help"): ("usage: zstd -#\n", ""),
        ("xz", "--version"): ("xz 5.8.3\n", ""),
    }

    def runner(args: Any) -> subprocess.CompletedProcess[str]:
        normalized = list(args)
        calls.append(normalized)
        stdout, stderr = responses[tuple(normalized)]
        return subprocess.CompletedProcess(normalized, 0, stdout, stderr)

    monkeypatch.setattr(benchmark, "_feature_file_enabled", lambda name: True)
    result = benchmark.detect_capabilities(
        runner=runner,
        which=lambda name: name,
    )

    assert result.btrfs_zstd is True
    assert result.btrfs_zlib is True
    assert result.btrfs_level_syntax is True
    assert result.btrfs_zstd_levels == (1, 3, 6, 9, 15)
    assert result.btrfs_zlib_levels == (1, 3, 6, 9)
    assert ["btrfs", "filesystem", "defragment", "--help"] in calls
    assert benchmark._parse_level_range(
        "zstd: -15..15 and zlib: 1..9", "zstd"
    ) == (-15, 15)


def test_benchmark_produces_json_text_groups_and_methodology(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Fixture Game"
    (game / "Paks").mkdir(parents=True)
    (game / "Config").mkdir()
    (game / "Paks" / "compressible.pak").write_bytes(b"A" * 300_000)
    (game / "Config" / "settings.ini").write_bytes(
        bytes(range(256)) * 400
    )

    report = benchmark.benchmark_game(
        game,
        sample_limit=512 * 1024,
        reference_limit=64 * 1024,
        window_size=128 * 1024,
        capabilities=_capabilities(),
    )

    assert report["game"]["file_count"] == 2
    assert report["safety"]["read_only_source"] is True
    assert report["sampling"]["groups_sampled"] == 2
    assert report["methodology"]["btrfs_extent_chunk_bytes"] == 128 * 1024
    assert all(
        item["measurement_method"]
        == "independent-128-kib-sectorsize-rounded-simulation"
        for item in report["algorithms"]
        if item["id"].startswith("btrfs-")
    )
    assert report["findings"]["well_compressible_groups"]
    assert {
        item["id"] for item in report["algorithms"]
    } == {
        "btrfs-zlib-1",
        "btrfs-zlib-6",
        "reference-zstd-19",
        "reference-xz-9",
    }
    assert all(
        not item["available"]
        for item in report["algorithms"]
        if item["role"] == "reference"
    )
    text = benchmark.format_text_report(report)
    assert "128 KiB" in text
    assert "not LZX" in text

    json_path, text_path = benchmark.write_reports(report, tmp_path / "reports")
    stored = json.loads(json_path.read_text())
    assert stored["schema_version"] == 2
    assert stored["report_type"] == "game-compression-benchmark"
    assert stored["tool"]["methodology_version"] == 2
    assert len(stored["tool"]["source_sha256"]) == 64
    assert "Fixture Game" in text_path.read_text()


def test_external_compressor_uses_no_shell_and_is_terminated_on_cancel() -> None:
    token = benchmark.CancellationToken()
    token.cancel()
    observed: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True
            self.terminated = False

        def poll(self) -> int | None:
            return None if self.running else -15

        def terminate(self) -> None:
            self.terminated = True
            self.running = False

        def wait(self, timeout: float | None = None) -> int:
            return -15

        def kill(self) -> None:
            self.running = False

    process = FakeProcess()

    def factory(args: list[str], **kwargs: Any) -> Any:
        observed["args"] = args
        observed.update(kwargs)
        return process

    with pytest.raises(benchmark.BenchmarkCancelled):
        benchmark._compress_external(
            ["zstd", "-q", "-c", "-3", "sample"],
            cancel=token,
            popen_factory=factory,
        )

    assert observed["args"] == ["zstd", "-q", "-c", "-3", "sample"]
    assert observed["shell"] is False
    assert process.terminated is True


def test_external_groups_share_one_absolute_deadline() -> None:
    token = benchmark.CancellationToken()

    class FakeProcess:
        running = True
        terminated = False

        def poll(self) -> int | None:
            return None if self.running else -15

        def terminate(self) -> None:
            self.terminated = True
            self.running = False

        def wait(self, timeout: float | None = None) -> int:
            return -15

        def kill(self) -> None:
            self.running = False

    process = FakeProcess()

    with pytest.raises(TimeoutError):
        benchmark._compress_external(
            ["xz", "-T1", "-q", "-c", "-9", "sample"],
            cancel=token,
            popen_factory=lambda *_args, **_kwargs: process,  # type: ignore[arg-type]
            timeout_seconds=60,
            deadline_monotonic=benchmark.time.monotonic() - 1,
        )

    assert process.terminated is True


def test_reference_commands_limit_threads_and_memory() -> None:
    xz = benchmark._reference_command_args("/usr/bin/xz", "xz", 9, "/tmp/a")
    zstd = benchmark._reference_command_args(
        "/usr/bin/zstd",
        "zstd",
        19,
        "/tmp/a",
    )

    assert "-T1" in xz
    assert "--memlimit-compress=1024MiB" in xz
    assert "--no-adjust" in xz
    assert "-T1" in zstd
    assert "-M1024" in zstd


def test_cancelled_scan_stops_before_reading_files(tmp_path: Path) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    (game / "asset.bin").write_bytes(b"A" * 100)
    token = benchmark.CancellationToken()
    token.cancel()

    with pytest.raises(benchmark.BenchmarkCancelled):
        benchmark.scan_game(game, cancel=token)


def test_cli_mib_arguments_are_converted_to_bytes() -> None:
    args = benchmark.build_argument_parser().parse_args(
        [
            "/tmp/game",
            "--sample-limit-mib",
            "2",
            "--reference-limit-mib",
            "3",
            "--window-mib",
            "1",
        ]
    )

    assert args.sample_limit_mib == 2 * 1024 * 1024
    assert args.reference_limit_mib == 3 * 1024 * 1024
    assert args.window_mib == 1024 * 1024


def test_reports_cannot_be_written_inside_source_game(tmp_path: Path) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    report = {
        "game": {"name": "Game", "path": str(game)},
        "schema_version": 1,
    }

    with pytest.raises(ValueError, match="outside the game tree"):
        benchmark.write_reports(report, game / "reports")


def test_within_group_sampling_is_proportional_to_file_bytes(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Game"
    assets = game / "Assets"
    assets.mkdir(parents=True)
    huge = assets / "huge.bin"
    huge.write_bytes(os.urandom(8 * 1024 * 1024))
    for index in range(512):
        (assets / f"small-{index:03d}.bin").write_bytes(b"A" * (16 * 1024))

    scan = benchmark.scan_game(game)
    plan = benchmark.build_sampling_plan(
        scan,
        sample_limit=4 * 1024 * 1024,
        window_size=128 * 1024,
    )
    key = ("Assets", ".bin")
    huge_sample = plan.file_sampled_bytes[("Assets", "huge.bin")]
    expected_share = 0.5
    actual_share = huge_sample / plan.group_sampled_bytes[key]

    assert actual_share == pytest.approx(expected_share, abs=0.08)
    assert plan.group_size_strata_distribution_tv[key] < 0.08
    assert plan.group_files_sampled[key] > 1


def test_sampling_windows_preserve_btrfs_cluster_alignment(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    (game / "large.bin").write_bytes(b"X" * 1_000_003)
    scan = benchmark.scan_game(game)
    plan = benchmark.build_sampling_plan(
        scan,
        sample_limit=512 * 1024,
        window_size=256 * 1024,
    )

    assert plan.slices
    assert all(
        item.offset % benchmark.BTRFS_EXTENT_CHUNK == 0
        for item in plan.slices
    )
    assert all(
        item.length % benchmark.BTRFS_EXTENT_CHUNK == 0
        or item.offset + item.length == item.file.size
        for item in plan.slices
    )


def test_scan_and_materialization_preserve_source_atime(tmp_path: Path) -> None:
    game = tmp_path / "Game"
    content = game / "Content"
    content.mkdir(parents=True)
    payload = content / "asset.bin"
    payload.write_bytes(b"A" * 128 * 1024)
    old_atime = 946_684_800_000_000_000
    payload_mtime = payload.stat().st_mtime_ns
    directory_mtime = content.stat().st_mtime_ns
    root_mtime = game.stat().st_mtime_ns
    os.utime(payload, ns=(old_atime, payload_mtime))
    os.utime(content, ns=(old_atime, directory_mtime))
    os.utime(game, ns=(old_atime, root_mtime))

    scan = benchmark.scan_game(game)
    plan = benchmark.build_sampling_plan(
        scan,
        sample_limit=128 * 1024,
        window_size=128 * 1024,
    )
    with tempfile.TemporaryFile() as destination:
        benchmark.materialize_sample(scan, plan, destination)

    assert payload.stat().st_atime_ns == old_atime
    assert content.stat().st_atime_ns == old_atime
    assert game.stat().st_atime_ns == old_atime


def test_btrfs_estimate_rounds_each_chunk_to_sectorsize() -> None:
    data = b"A" * 128 * 1024

    class Compressor:
        @staticmethod
        def compress(_chunk: bytes, _level: int) -> int:
            return 4097

    result = benchmark._compress_btrfs_blocks(
        io.BytesIO(data),
        "zstd",
        3,
        segments=[
            benchmark.MaterializedSegment(0, len(data), (".", ".bin"))
        ],
        byte_limit=len(data),
        cancel=benchmark.CancellationToken(),
        sectorsize=4096,
        zstd_library=Compressor(),  # type: ignore[arg-type]
    )

    assert result == 8192


def test_cli_rejects_unbounded_resource_requests() -> None:
    parser = benchmark.build_argument_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "/tmp/game",
                "--sample-limit-mib",
                str(benchmark.MAX_SAMPLE_LIMIT // (1024 * 1024) + 1),
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "/tmp/game",
                "--external-timeout-seconds",
                str(benchmark.MAX_EXTERNAL_TIMEOUT_SECONDS + 1),
            ]
        )


def test_scan_deduplicates_hardlink_inodes(tmp_path: Path) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    first = game / "first.bin"
    second = game / "second.bin"
    first.write_bytes(b"payload")
    os.link(first, second)

    scan = benchmark.scan_game(game)

    assert scan.namespace_file_count == 2
    assert scan.namespace_logical_bytes == 14
    assert len(scan.files) == 1
    assert scan.logical_bytes == 7
    assert scan.hardlinked_files == 1
    assert scan.hardlink_entries_skipped == 1
    assert scan.hardlink_duplicate_bytes == 7


def test_sparse_source_is_rejected_instead_of_counted_as_compressible(
    tmp_path: Path,
) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    sparse = game / "sparse.bin"
    with sparse.open("wb") as stream:
        stream.seek(4 * 1024 * 1024)
        stream.write(b"X")
    if sparse.stat().st_blocks * 512 >= sparse.stat().st_size:
        pytest.skip("temporary filesystem did not create a sparse file")

    with pytest.raises(RuntimeError, match="sparse files"):
        benchmark.benchmark_game(
            game,
            sample_limit=1024 * 1024,
            reference_limit=64 * 1024,
            window_size=128 * 1024,
            capabilities=_capabilities(),
        )


def test_unknown_sparse_state_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = tmp_path / "Game"
    game.mkdir()
    sparse = game / "sparse.bin"
    with sparse.open("wb") as stream:
        stream.seek(4 * 1024 * 1024)
        stream.write(b"X")
    if sparse.stat().st_blocks * 512 >= sparse.stat().st_size:
        pytest.skip("temporary filesystem did not create a sparse file")

    def unsupported_seek(_fd: int, _offset: int, _whence: int) -> int:
        raise OSError(errno.EINVAL, "not supported")

    monkeypatch.setattr(benchmark.os, "lseek", unsupported_seek)
    scan = benchmark.scan_game(game)

    assert scan.sparse_files == 0
    assert scan.sparse_unknown_files == 1
    with pytest.raises(RuntimeError, match="could not be determined"):
        benchmark.benchmark_game(
            game,
            sample_limit=1024 * 1024,
            reference_limit=64 * 1024,
            window_size=128 * 1024,
            capabilities=_capabilities(),
        )


def test_steam_manifest_is_validated_and_recorded(tmp_path: Path) -> None:
    steamapps = tmp_path / "steamapps"
    game = steamapps / "common" / "Fixture Game"
    game.mkdir(parents=True)
    (game / "asset.bin").write_bytes(b"A" * 256 * 1024)
    manifest = steamapps / "appmanifest_42.acf"
    manifest.write_text(
        '"AppState"\n'
        "{\n"
        '  "appid" "42"\n'
        '  "name" "Fixture Display Name"\n'
        '  "StateFlags" "4"\n'
        '  "installdir" "Fixture Game"\n'
        '  "SizeOnDisk" "262144"\n'
        '  "buildid" "123456"\n'
        "}\n",
        encoding="utf-8",
    )

    report = benchmark.benchmark_game(
        game,
        sample_limit=128 * 1024,
        reference_limit=64 * 1024,
        window_size=128 * 1024,
        capabilities=_capabilities(),
        steam_app_id="42",
        steam_manifest=manifest,
    )

    assert report["game"]["name"] == "Fixture Display Name"
    assert report["game"]["steam_app_id"] == "42"
    assert report["game"]["steam_build_id"] == "123456"
    assert report["game"]["steam_manifest_stable"] is True
    assert report["game"]["inventory_stable"] is True


def test_incomplete_steam_install_is_rejected_before_sampling(
    tmp_path: Path,
) -> None:
    steamapps = tmp_path / "steamapps"
    game = steamapps / "common" / "Fixture Game"
    game.mkdir(parents=True)
    manifest = steamapps / "appmanifest_42.acf"
    manifest.write_text(
        '"AppState"\n'
        "{\n"
        '  "appid" "42"\n'
        '  "name" "Fixture"\n'
        '  "StateFlags" "1026"\n'
        '  "installdir" "Fixture Game"\n'
        '  "buildid" "1"\n'
        "}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fully-installed"):
        benchmark.benchmark_game(
            game,
            sample_limit=128 * 1024,
            reference_limit=32 * 1024,
            window_size=128 * 1024,
            capabilities=_capabilities(),
            steam_app_id="42",
            steam_manifest=manifest,
        )
