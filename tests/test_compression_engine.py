from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
from typing import Any, Mapping

import pytest

from game_optimization_linux.models import (
    CompressionFile,
    CompressionMeasurement,
    CompressionPlan,
    CompressionProfile,
    CompressionResult,
    CompressionToolCapabilities,
    FilesystemType,
    Game,
    Launcher,
    TaskStatus,
    Task,
    TaskType,
)
from game_optimization_linux.providers import (
    BtrfsCompressionProvider,
    CompressionCancelled,
    CompressionPlanRejected,
    CompressionProviderError,
    FakeCompressionProvider,
    UnavailableCompressionProvider,
)
from game_optimization_linux.services import (
    BtrfsAnalysisReport,
    BtrfsAnalysisTaskService,
    BtrfsCompressionAnalyzer,
    BenchmarkEstimateCatalog,
    CompressionHistoryStore,
    CompressionService,
    TaskHistoryStore,
)


MIB = 1024 * 1024
GIB = 1024 * MIB


def _steam_game(
    tmp_path: Path,
    *,
    filesystem: FilesystemType = FilesystemType.BTRFS,
    library_available: bool = True,
    update_in_progress: bool = False,
) -> Game:
    library = tmp_path / "library"
    game_path = library / "steamapps" / "common" / "Fixture Game"
    game_path.mkdir(parents=True, exist_ok=True)
    manifest = library / "steamapps" / "appmanifest_4242.acf"
    manifest.write_text(
        '"AppState"\n{\n'
        '    "appid" "4242"\n'
        '    "installdir" "Fixture Game"\n'
        '    "buildid" "100"\n'
        '}\n',
        encoding="utf-8",
    )
    manifest_stat = manifest.stat()
    return Game(
        id="steam-4242",
        name="Fixture Game",
        launcher=Launcher.STEAM,
        install_path=game_path,
        logical_size_gb=0.01,
        physical_size_gb=0.01,
        filesystem=filesystem,
        compression_available=filesystem is FilesystemType.BTRFS,
        steam_app_id="4242",
        steam_manifest_path=manifest,
        steam_manifest_mtime_ns=manifest_stat.st_mtime_ns,
        steam_manifest_size_bytes=manifest_stat.st_size,
        library_path=library,
        filesystem_name=filesystem.value.casefold(),
        library_available=library_available,
        update_in_progress=update_in_progress,
        steam_build_id="100",
    )


def _report(
    game: Game,
    *,
    filesystem: str = "btrfs",
    is_btrfs: bool = True,
    writable: bool = True,
    available_bytes: int | None = 8 * GIB,
    logical_bytes: int = 8192,
    physical_bytes: int = 8192,
    scan_complete: bool = True,
    game_running: bool = False,
    shared_state: str = "not_detected",
    shared_total: int | None = 8192,
    shared_exclusive: int | None = 8192,
    set_shared: int | None = 0,
    auto_level: int = 3,
) -> BtrfsAnalysisReport:
    btrfs_du: dict[str, Any]
    if shared_state == "unknown":
        btrfs_du = {
            "available": False,
            "state": "unknown",
            "total_bytes": None,
            "exclusive_bytes": None,
            "set_shared_bytes": None,
            "estimated_growth_bytes": None,
            "message": "shared extent accounting unavailable",
        }
    else:
        btrfs_du = {
            "available": True,
            "state": shared_state,
            "total_bytes": shared_total,
            "exclusive_bytes": shared_exclusive,
            "set_shared_bytes": set_shared,
            "estimated_growth_bytes": (
                None
                if shared_total is None or shared_exclusive is None
                else max(0, shared_total - shared_exclusive)
            ),
            "message": "read-only btrfs filesystem du",
        }
    return BtrfsAnalysisReport.from_dict(
        {
            "analyzer_version": 5,
            "game_id": game.id,
            "app_id": game.steam_app_id,
            "game_name": game.name,
            "path": str(game.install_path),
            "path_exists": True,
            "path_is_directory": True,
            "filesystem": filesystem,
            "is_btrfs": is_btrfs,
            "writable": writable,
            "mount_point": str(game.library_path),
            "filesystem_device": "/dev/test",
            "available_bytes": available_bytes,
            "logical_bytes": logical_bytes,
            "physical_bytes": physical_bytes,
            "file_count": 1,
            "directory_count": 1,
            "symlink_count": 0,
            "hardlink_count": 0,
            "permission_errors": [],
            "scan_complete": scan_complete,
            "existing_compression_state": "none",
            "persistent_compression_algorithm": None,
            "mount_compression_level": None,
            "compsize": {
                "available": False,
                "message": "compsize not installed",
            },
            "btrfs_du": btrfs_du,
            "possible_shared_extents": (
                True
                if shared_state == "detected"
                else False
                if shared_state == "not_detected"
                else None
            ),
            "game_running": game_running,
            "running_process_ids": [222] if game_running else [],
            "sampled_bytes": 0,
            "sampled_files": 0,
            "sampling_codec": "unavailable",
            "sampling_complete": True,
            "selected_auto_level": auto_level,
            "profiles": {},
            "profiles_unlocked": True,
            "compression_eligible": is_btrfs and writable and scan_complete,
            "benefit": "Moderate benefit",
            "warnings": [],
            "created_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": 0.1,
        }
    )


def _capabilities(*, available: bool = True) -> CompressionToolCapabilities:
    return CompressionToolCapabilities(
        btrfs_available=available,
        btrfs_version="btrfs-progs test" if available else "",
        compsize_available=False,
        property_supported=available,
        recompression_supported=available,
        level_supported=available,
        message="Available" if available else "btrfs tools are missing",
    )


def _provider(
    *,
    analyzer: object | None = None,
    runner: object | None = None,
    capabilities: CompressionToolCapabilities | None = None,
    running: bool = False,
    updating: bool = False,
    writer: bool = False,
    measurement_provider: object | None = None,
) -> BtrfsCompressionProvider:
    provider = BtrfsCompressionProvider(
        analyzer=analyzer,  # type: ignore[arg-type]
        executable_finder=lambda name: (
            f"/usr/bin/{name}" if name in {"btrfs", "compsize"} else None
        ),
        command_runner=runner,  # type: ignore[arg-type]
        game_running_checker=lambda game: running,
        steam_update_checker=lambda game: updating,
        write_task_checker=lambda game_id: writer,
        measurement_provider=measurement_provider,  # type: ignore[arg-type]
    )
    provider.capabilities = lambda: capabilities or _capabilities()  # type: ignore[method-assign]
    return provider


def _measurement(*, physical: int = 8192) -> CompressionMeasurement:
    return CompressionMeasurement(
        logical_bytes=8192,
        physical_bytes=physical,
        exclusive_bytes=physical,
        shared_bytes=0,
        compsize_disk_bytes=None,
        compsize_uncompressed_bytes=None,
        compsize_referenced_bytes=None,
        scan_complete=True,
        shared_extent_state="not_detected",
    )


def _plan(game: Game, *, eligible: bool = True) -> CompressionPlan:
    path = game.install_path / "payload.bin"
    if not path.exists():
        path.write_bytes(b"A" * 8192)
    info = path.stat()
    return CompressionPlan(
        id="plan-fixture",
        game_id=game.id,
        app_id=game.steam_app_id or "",
        game_name=game.name,
        game_path=str(game.install_path),
        profile=CompressionProfile.BALANCED,
        persistent_compression_algorithm="zstd",
        one_time_recompression_level=3,
        files=(
            CompressionFile(
                relative_path="payload.bin",
                size_bytes=info.st_size,
                mtime_ns=info.st_mtime_ns,
                ctime_ns=info.st_ctime_ns,
                device=info.st_dev,
                inode=info.st_ino,
            ),
        ),
        skipped_files=(),
        full_compression=True,
        after_update=False,
        build_id=game.steam_build_id,
        estimated_savings_low_bytes=1024,
        estimated_savings_high_bytes=2048,
        estimated_shared_growth_bytes=0,
        available_bytes=8 * GIB,
        required_free_bytes=512 * MIB + info.st_size,
        before=_measurement(),
        eligible=eligible,
        confirmation_required=True,
        blockers=() if eligible else ("blocked fixture",),
    )


def _result(
    game: Game,
    plan: CompressionPlan,
    *,
    status: str = "completed",
    error: str | None = None,
) -> CompressionResult:
    now = datetime.now(UTC)
    after = _measurement(physical=7168)
    return CompressionResult(
        plan_id=plan.id,
        game_id=game.id,
        profile=plan.profile,
        status=status,
        started_at=now,
        completed_at=now,
        processed_files=plan.total_files,
        processed_bytes=plan.total_bytes,
        before=plan.before,
        after=after,
        actual_saved_bytes=1024,
        verification_state=(
            "verified" if status in {"completed", "completed_with_warning"} else "failed"
        ),
        full_compression=plan.full_compression,
        after_update=plan.after_update,
        build_id=plan.build_id,
        command_exit_codes=(0, 0),
        error=error,
    )


class _StaticAnalyzer:
    def __init__(self, report: BtrfsAnalysisReport) -> None:
        self.report = report
        self.calls = 0

    def analyze(self, game: Game, **kwargs: object) -> BtrfsAnalysisReport:
        del game, kwargs
        self.calls += 1
        return self.report


class _PrivilegedMeasurements:
    def __init__(
        self,
        *,
        logical_bytes: int = 16_384,
        on_first_measurement: object | None = None,
    ) -> None:
        self.logical_bytes = logical_bytes
        self.on_first_measurement = on_first_measurement
        self.calls = 0

    def measure(self, game: Game) -> CompressionMeasurement:
        self.calls += 1
        if self.calls == 1 and callable(self.on_first_measurement):
            self.on_first_measurement()
        return CompressionMeasurement(
            logical_bytes=self.logical_bytes,
            physical_bytes=7_000,
            exclusive_bytes=7_000,
            shared_bytes=0,
            compsize_disk_bytes=7_000,
            compsize_uncompressed_bytes=self.logical_bytes,
            compsize_referenced_bytes=self.logical_bytes,
            scan_complete=True,
            shared_extent_state="not_detected",
            measurement_source="polkit_helper",
        )

    def cancel_all(self) -> None:
        return


def test_privileged_baseline_skips_unprivileged_compsize_and_stable_root_passes(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    report = _report(game)

    class RecordingAnalyzer(_StaticAnalyzer):
        def __init__(self, active_report: BtrfsAnalysisReport) -> None:
            super().__init__(active_report)
            self.options: list[dict[str, object]] = []

        def analyze(self, game: Game, **kwargs: object) -> BtrfsAnalysisReport:
            self.options.append(dict(kwargs))
            return super().analyze(game, **kwargs)

    analyzer = RecordingAnalyzer(report)
    measurements = _PrivilegedMeasurements(logical_bytes=16_384)
    commands: list[list[str]] = []

    def runner(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = (
            f"8192 8192 0 {command[-1]}"
            if command[1:3] == ["filesystem", "du"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    provider = _provider(
        analyzer=analyzer,
        runner=runner,
        measurement_provider=measurements,
    )
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)

    result = provider.execute_plan(game, plan, confirmed=True)

    assert result.before.measurement_source == "polkit_helper"
    assert result.before.logical_bytes == 16_384
    assert result.status in {"completed", "completed_with_warning"}
    assert measurements.calls == 2
    assert analyzer.options
    assert all(option["measure_compsize"] is False for option in analyzer.options)
    assert not any(command[0].endswith("/compsize") for command in commands)


def test_directory_inode_replacement_during_privileged_baseline_is_rejected(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    report = _report(game)
    original_inode = game.install_path.stat().st_ino

    def replace_directory() -> None:
        moved = game.install_path.with_name("Fixture Game replaced")
        game.install_path.rename(moved)
        game.install_path.mkdir()

    measurements = _PrivilegedMeasurements(
        on_first_measurement=replace_directory,
    )
    provider = _provider(
        analyzer=_StaticAnalyzer(report),
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "", ""
        ),
        measurement_provider=measurements,
    )
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)

    with pytest.raises(
        CompressionPlanRejected,
        match="changed during the privileged baseline measurement",
    ):
        provider.execute_plan(game, plan, confirmed=True)

    assert game.install_path.stat().st_ino != original_inode
    assert measurements.calls == 1


@pytest.mark.parametrize("filesystem", [FilesystemType.EXT4, FilesystemType.NTFS])
def test_plan_rejects_non_btrfs_without_running_commands(
    tmp_path: Path, filesystem: FilesystemType
) -> None:
    game = _steam_game(tmp_path, filesystem=filesystem)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    calls: list[object] = []
    provider = _provider(runner=lambda *args, **kwargs: calls.append((args, kwargs)))

    plan = provider.create_plan(
        game,
        _report(game, filesystem=filesystem.value, is_btrfs=False),
        CompressionProfile.BALANCED,
    )

    assert plan.eligible is False
    assert any("Btrfs" in blocker for blocker in plan.blockers)
    assert calls == []


@pytest.mark.parametrize(
    ("game_changes", "report_changes", "provider_changes", "expected"),
    [
        ({"library_available": False}, {}, {}, "library is unavailable"),
        ({}, {"writable": False}, {}, "not writable"),
        ({}, {"game_running": True}, {}, "currently running"),
        ({}, {}, {"running": True}, "currently running"),
        ({}, {}, {"updating": True}, "installing or updating"),
        ({}, {}, {"writer": True}, "write task is active"),
        ({}, {"available_bytes": None}, {}, "could not be measured"),
        ({}, {"available_bytes": 1}, {}, "Insufficient free space"),
        ({}, {"scan_complete": False}, {}, "complete analysis"),
    ],
)
def test_plan_preconditions_fail_closed(
    tmp_path: Path,
    game_changes: Mapping[str, object],
    report_changes: Mapping[str, object],
    provider_changes: Mapping[str, object],
    expected: str,
) -> None:
    game = replace(_steam_game(tmp_path), **game_changes)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    provider = _provider(**provider_changes)  # type: ignore[arg-type]

    plan = provider.create_plan(
        game,
        _report(game, **report_changes),  # type: ignore[arg-type]
        CompressionProfile.BALANCED,
    )

    assert plan.eligible is False
    assert expected.casefold() in " ".join(plan.blockers).casefold()


def test_plan_checks_game_update_in_progress_model_field(tmp_path: Path) -> None:
    game = _steam_game(tmp_path, update_in_progress=True)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)

    plan = _provider().create_plan(
        game,
        _report(game),
        CompressionProfile.BALANCED,
    )

    assert plan.eligible is False
    assert "installing or updating" in " ".join(plan.blockers)


@pytest.mark.parametrize("manifest_state", ("missing", "wrong-appid", "wrong-directory"))
def test_plan_requires_a_matching_regular_steam_manifest(
    tmp_path: Path,
    manifest_state: str,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    assert game.steam_manifest_path is not None
    if manifest_state == "missing":
        game.steam_manifest_path.unlink()
    else:
        app_id = "9999" if manifest_state == "wrong-appid" else "4242"
        directory = "Other Game" if manifest_state == "wrong-directory" else "Fixture Game"
        game.steam_manifest_path.write_text(
            '"AppState"\n{\n'
            f'    "appid" "{app_id}"\n'
            f'    "installdir" "{directory}"\n'
            '}\n',
            encoding="utf-8",
        )

    plan = _provider().create_plan(
        game,
        _report(game),
        CompressionProfile.BALANCED,
    )

    assert plan.eligible is False
    assert any("appmanifest" in blocker for blocker in plan.blockers)


def test_plan_rejects_missing_tools(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    provider = _provider(capabilities=_capabilities(available=False))

    plan = provider.create_plan(game, _report(game), CompressionProfile.BALANCED)

    assert plan.eligible is False
    assert "tools are missing" in " ".join(plan.blockers)


@pytest.mark.parametrize(
    ("profile", "auto_level", "expected_level"),
    [
        (CompressionProfile.FAST, 9, 1),
        (CompressionProfile.BALANCED, 9, 3),
        (CompressionProfile.MAXIMUM, 3, 9),
        (CompressionProfile.AUTO, 6, 6),
        (CompressionProfile.AUTO, 15, 3),
    ],
)
def test_profile_maps_to_supported_one_time_level_and_persistent_algorithm(
    tmp_path: Path,
    profile: CompressionProfile,
    auto_level: int,
    expected_level: int,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)

    plan = _provider().create_plan(
        game,
        _report(game, auto_level=auto_level),
        profile,
    )

    assert plan.eligible is True
    assert plan.profile is profile
    assert plan.persistent_compression_algorithm == "zstd"
    assert plan.one_time_recompression_level == expected_level
    assert plan.one_time_recompression_level != 15


def test_plan_surfaces_measured_shared_extent_risk_for_manual_confirmation(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)

    plan = _provider().create_plan(
        game,
        _report(
            game,
            shared_state="detected",
            shared_total=16384,
            shared_exclusive=4096,
            set_shared=8192,
        ),
        CompressionProfile.BALANCED,
    )

    assert plan.eligible is True
    assert plan.estimated_shared_growth_bytes == 12288
    assert plan.required_free_bytes >= 512 * MIB + 8192 + 12288
    assert "shared btrfs extents" in " ".join(plan.warnings).casefold()
    assert any("12288" in warning for warning in plan.warnings)


def test_plan_blocks_when_shared_extent_measurement_is_unknown(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)

    plan = _provider().create_plan(
        game,
        _report(
            game,
            shared_state="unknown",
            shared_total=None,
            shared_exclusive=None,
            set_shared=None,
        ),
        CompressionProfile.BALANCED,
    )

    assert plan.eligible is False
    assert "fail closed" in " ".join(plan.blockers).casefold()


def test_incremental_plan_contains_only_changed_and_new_regular_files(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    unchanged = game.install_path / "unchanged.bin"
    changed = game.install_path / "changed.bin"
    new = game.install_path / "new.bin"
    unchanged.write_bytes(b"A" * 8192)
    changed.write_bytes(b"B" * 8192)
    unchanged_info = unchanged.stat()
    previous = {
        "unchanged.bin": {
            "size": unchanged_info.st_size,
            "mtime_ns": unchanged_info.st_mtime_ns,
        },
        "changed.bin": {"size": 1, "mtime_ns": 1},
        "removed.bin": {"size": 10, "mtime_ns": 1},
    }
    new.write_bytes(b"C" * 8192)

    plan = _provider().create_plan(
        game,
        _report(game),
        CompressionProfile.AUTO,
        previous_fingerprint=previous,
        after_update=True,
    )

    assert plan.eligible is True
    assert plan.full_compression is False
    assert plan.after_update is True
    assert [item.relative_path for item in plan.files] == [
        "changed.bin",
        "new.bin",
    ]


def test_incremental_plan_with_no_changes_is_not_executable(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    payload = game.install_path / "payload.bin"
    payload.write_bytes(b"A" * 8192)
    info = payload.stat()

    plan = _provider().create_plan(
        game,
        _report(game),
        CompressionProfile.AUTO,
        previous_fingerprint={
            "payload.bin": {"size": info.st_size, "mtime_ns": info.st_mtime_ns}
        },
    )

    assert plan.full_compression is False
    assert plan.files == ()
    assert plan.eligible is False
    assert "No new or changed files" in " ".join(plan.blockers)


def test_incremental_plan_uses_ctime_to_detect_same_size_and_mtime_rewrite(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    payload = game.install_path / "payload.bin"
    payload.write_bytes(b"A" * 8192)
    before = payload.stat()
    payload.write_bytes(b"B" * 8192)
    os.utime(
        payload,
        ns=(before.st_atime_ns, before.st_mtime_ns),
        follow_symlinks=False,
    )
    after = payload.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns

    plan = _provider().create_plan(
        game,
        _report(game),
        CompressionProfile.AUTO,
        previous_fingerprint={
            "payload.bin": {
                "size": before.st_size,
                "mtime_ns": before.st_mtime_ns,
                "ctime_ns": before.st_ctime_ns,
            }
        },
    )

    assert plan.eligible is True
    assert [item.relative_path for item in plan.files] == ["payload.bin"]


def test_symlink_outside_root_is_skipped_and_commands_stay_below_game(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    payload = game.install_path / "payload.bin"
    outside = tmp_path / "outside.bin"
    payload.write_bytes(b"A" * 8192)
    outside.write_bytes(b"DO NOT TOUCH")
    (game.install_path / "outside-link").symlink_to(outside)
    report = _report(game)
    analyzer = _StaticAnalyzer(report)
    resolved_targets: list[Path] = []
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, dict(kwargs)))
        descriptor_path = (
            command[-3] if command[1:3] == ["property", "set"] else command[-1]
        )
        resolved_targets.append(Path(os.readlink(descriptor_path)))
        stdout = (
            f"8192 8192 0 {descriptor_path}"
            if command[1:3] == ["filesystem", "du"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    provider = _provider(analyzer=analyzer, runner=runner)
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)
    result = provider.execute_plan(game, plan, confirmed=True)

    assert result.status in {"completed", "completed_with_warning"}
    assert [item.relative_path for item in plan.files] == ["payload.bin"]
    assert outside.read_bytes() == b"DO NOT TOUCH"
    assert resolved_targets
    root = game.install_path.resolve()
    assert all(
        target.resolve() == root or target.resolve().is_relative_to(root)
        for target in resolved_targets
    )
    assert all(call_kwargs["shell"] is False for _, call_kwargs in calls)
    flattened = " ".join(item for command, _ in calls for item in command)
    assert " -r " not in f" {flattened} "
    assert "inspect-internal" not in flattened
    assert "dump-tree" not in flattened
    assert "sudo" not in flattened
    assert "pkexec" not in flattened


def test_forbidden_helper_command_is_rejected_before_runner(tmp_path: Path) -> None:
    calls: list[object] = []
    provider = _provider(runner=lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(CompressionProviderError, match="forbidden"):
        provider._run_command(  # noqa: SLF001 - direct safety boundary test
            ["sudo", "btrfs", "filesystem", "defragment", str(tmp_path)],
            cancel_event=None,
        )

    assert calls == []


def test_nonzero_recompression_exit_is_failed_not_success(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    report = _report(game)
    analyzer = _StaticAnalyzer(report)
    calls = 0

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del kwargs
        calls += 1
        if command[1:3] == ["filesystem", "du"]:
            return subprocess.CompletedProcess(
                command, 0, f"8192 8192 0 {command[-1]}", ""
            )
        return subprocess.CompletedProcess(
            command,
            0 if command[1:3] == ["property", "set"] else 7,
            "",
            "simulated failure",
        )

    provider = _provider(analyzer=analyzer, runner=runner)
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)

    result = provider.execute_plan(game, plan, confirmed=True)

    assert calls == 3
    assert result.status == "failed"
    assert result.verification_state == "verified_partial"
    assert result.command_exit_codes == (0, 7)
    assert result.error is not None and "status 7" in result.error


def test_new_shared_extent_is_blocked_immediately_before_defrag(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    report = _report(game)
    analyzer = _StaticAnalyzer(report)
    calls: list[list[str]] = []

    def runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["filesystem", "du"]:
            # Set shared may be zero for a one-file input set.  Total greater
            # than Exclusive still proves sharing with an outside reflink.
            return subprocess.CompletedProcess(
                command, 0, f"8192 4096 0 {command[-1]}", ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = _provider(analyzer=analyzer, runner=runner)
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)

    result = provider.execute_plan(game, plan, confirmed=True)

    assert result.status == "failed"
    assert result.processed_files == 0
    assert result.error is not None
    assert "shared btrfs extents appeared" in result.error.casefold()
    assert not any(
        command[1:3] == ["filesystem", "defragment"] for command in calls
    )


def test_same_inode_size_and_mtime_but_changed_ctime_is_rejected(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    payload = game.install_path / "payload.bin"
    payload.write_bytes(b"A" * 8192)
    report = _report(game)
    calls: list[list[str]] = []
    provider = _provider(
        analyzer=_StaticAnalyzer(report),
        runner=lambda command, **_kwargs: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)
    original_mtime = payload.stat().st_mtime_ns
    payload.write_bytes(b"B" * 8192)
    os.utime(payload, ns=(payload.stat().st_atime_ns, original_mtime))
    assert payload.stat().st_ctime_ns != plan.files[0].ctime_ns

    with pytest.raises(CompressionPlanRejected, match="Planned file changed"):
        provider.execute_plan(game, plan, confirmed=True)

    assert calls == []


def test_cancellation_after_helper_returns_a_cancelled_result(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    report = _report(game)
    analyzer = _StaticAnalyzer(report)
    cancelled = Event()

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        cancelled.set()
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = _provider(analyzer=analyzer, runner=runner)
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)

    result = provider.execute_plan(
        game,
        plan,
        confirmed=True,
        cancel_event=cancelled,
    )

    assert result.status == "cancelled"
    assert result.processed_files == 0
    assert result.error is not None


def test_success_without_compsize_is_warning_not_zero_savings(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    planned_report = _report(game, physical_bytes=8192)
    fresh_report = _report(game, physical_bytes=7168)
    analyzer = _StaticAnalyzer(fresh_report)

    def runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        stdout = (
            f"8192 8192 0 {command[-1]}"
            if command[1:3] == ["filesystem", "du"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    provider = _provider(analyzer=analyzer, runner=runner)
    plan = provider.create_plan(
        game,
        planned_report,
        CompressionProfile.BALANCED,
    )

    result = provider.execute_plan(game, plan, confirmed=True)

    assert result.status == "completed_with_warning"
    assert result.verification_state == "measurement_unavailable"
    assert result.actual_saved_bytes is None
    assert result.before.physical_bytes == 0
    assert result.before.compsize_disk_bytes is None
    assert result.before.measurement_error == "compsize not installed"
    assert result.before != plan.before
    assert any(
        "savings could not be measured" in warning
        for warning in result.warnings
    )


def test_compsize_search_v2_failure_finishes_parent_task_bounded(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    unavailable = replace(
        _result(game, plan, status="completed_with_warning"),
        after=None,
        actual_saved_bytes=None,
        verification_state="measurement_unavailable",
        warnings=(
            "compsize exit_code=1: SEARCH_V2: Operation not permitted",
            "Compression completed, but savings could not be measured",
        ),
    )
    def compsize_runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "SEARCH_V2: Operation not permitted",
        )

    analyzer = BtrfsCompressionAnalyzer(
        command_runner=compsize_runner,
        executable_finder=lambda name: "/usr/bin/compsize" if name == "compsize" else None,
    )

    class MeasuringProvider(FakeCompressionProvider):
        def execute_plan(
            self,
            active_game: Game,
            active_plan: CompressionPlan,
            **_kwargs: object,
        ) -> CompressionResult:
            measured = analyzer._measure_compsize(  # noqa: SLF001
                active_game.install_path,
                None,
                deadline=time.monotonic() + 1.0,
            )
            assert measured.available is False
            assert "SEARCH_V2: Operation not permitted" in measured.message
            return replace(
                unavailable,
                plan_id=active_plan.id,
                warnings=(
                    measured.message,
                    "Compression completed, but savings could not be measured",
                ),
            )

    provider = MeasuringProvider(plan=plan, result=unavailable)
    history = CompressionHistoryStore(tmp_path / "compression-history.json")
    compression = CompressionService(provider, history)
    tasks = BtrfsAnalysisTaskService(
        compression_service=compression,
        history_store=TaskHistoryStore(tmp_path / "task-history.json"),
        max_workers=1,
    )
    prepared = compression.prepare(
        game,
        _report(game),
        CompressionProfile.BALANCED,
    )

    queued = tasks.enqueue_compression_plan(game, prepared, confirmed=True)
    started = time.monotonic()
    final = tasks.wait_for(queued.id, timeout=1.0)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert final.status is TaskStatus.COMPLETED
    assert final.result is not None
    assert final.result["verification_state"] == "measurement_unavailable"
    assert final.metadata["stage"] == "Completed"
    assert len(history.history(game.id)) == 1
    assert history.pending() == ()
    assert tasks.shutdown(wait=True, timeout=1.0)


def test_task_history_restores_interrupts_limits_and_clears(
    tmp_path: Path,
) -> None:
    store = TaskHistoryStore(tmp_path / "task-history.json")
    now = datetime.now(UTC)
    tasks = [
        Task(
            id="active-from-previous-session",
            game_id="steam-active",
            game_name="Active",
            task_type=TaskType.COMPRESSION,
            title="Compress Active",
            status=TaskStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    ]
    for index in range(105):
        tasks.append(
            Task(
                id=f"finished-{index}",
                game_id=f"steam-{index}",
                game_name=f"Game {index}",
                task_type=TaskType.ANALYSIS,
                title=f"Analyze Game {index}",
                status=TaskStatus.COMPLETED,
                progress=100.0,
                created_at=now - timedelta(minutes=index + 1),
                updated_at=now - timedelta(minutes=index + 1),
            )
        )
    store.save(tasks)

    service = BtrfsAnalysisTaskService(
        history_store=TaskHistoryStore(store.path),
        max_workers=1,
    )
    restored = service.list_tasks()

    assert len(restored) == 100
    interrupted = next(
        task for task in restored if task.id == "active-from-previous-session"
    )
    assert interrupted.status is TaskStatus.INTERRUPTED
    assert interrupted.metadata["stage"] == "Interrupted"
    assert service.remove_finished("finished-0") is True
    assert len(service.list_tasks()) == 99
    assert service.clear_finished() == 99
    assert service.list_tasks() == ()
    assert TaskHistoryStore(store.path).load() == ()
    assert service.shutdown(wait=True, timeout=1.0)


def test_benchmark_estimate_requires_exact_appid_and_build(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    payload = {
        "report_type": "game-compression-benchmark",
        "schema_version": 2,
        "created_at": "2026-07-27T12:00:00+00:00",
        "cancelled": False,
        "game": {
            "steam_app_id": "4242",
            "steam_build_id": "100",
            "steam_manifest_stable": True,
            "inventory_stable": True,
            "logical_bytes": 10_000,
        },
        "algorithms": [
            {
                "family": "zstd",
                "level": level,
                "available": True,
                "estimated_total_payload_reduction_from_uncompressed_baseline_bytes": reduction,
                "estimated_game_compressed_bytes": 10_000 - reduction,
            }
            for level, reduction in ((3, 2_000), (9, 2_500))
        ],
    }
    (reports / "accepted.json").write_text(json.dumps(payload), encoding="utf-8")
    catalog = BenchmarkEstimateCatalog(reports)

    current = catalog.estimate_for(game)
    stale = catalog.estimate_for(replace(game, steam_build_id="101"))

    assert current["available"] is True
    assert current["zstd3PotentialBytes"] == 2_000
    assert current["zstd9PotentialBytes"] == 2_500
    assert current["zstd3EstimatedSizeBytes"] == 8_000
    assert current["levels"]["3"]["estimatedPhysicalBytes"] == 8_000
    assert current["levels"]["3"]["predictedPhysicalRatio"] == 0.8
    assert stale == {
        "available": False,
        "reason": "No current estimate",
        "appId": "4242",
        "buildId": "101",
    }


def test_active_child_is_terminated_and_reaped_on_cancellation() -> None:
    provider = BtrfsCompressionProvider()
    cancelled = Event()
    errors: list[BaseException] = []

    def run_helper() -> None:
        try:
            provider._run_command(  # noqa: SLF001 - child lifecycle boundary
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cancel_event=cancelled,
            )
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=run_helper, daemon=True)
    worker.start()
    deadline = time.monotonic() + 2.0
    while provider.active_child_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert provider.active_child_count == 1

    cancelled.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CompressionCancelled)
    assert provider.active_child_count == 0


def test_new_file_after_plan_is_rejected_before_first_write(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "payload.bin").write_bytes(b"A" * 8192)
    original = _report(game, logical_bytes=8192)
    changed = _report(game, logical_bytes=16384)
    calls: list[object] = []
    provider = _provider(
        analyzer=_StaticAnalyzer(changed),
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    plan = provider.create_plan(game, original, CompressionProfile.BALANCED)
    (game.install_path / "new-after-plan.bin").write_bytes(b"B" * 8192)

    with pytest.raises(CompressionPlanRejected, match="changed after planning"):
        provider.execute_plan(game, plan, confirmed=True)

    assert calls == []


def test_default_process_poll_stops_before_next_file(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    (game.install_path / "first.bin").write_bytes(b"A" * 8192)
    (game.install_path / "second.bin").write_bytes(b"B" * 8192)
    report = _report(game)
    running = Event()

    class RuntimeAnalyzer(_StaticAnalyzer):
        def detect_running_processes(self, path: Path) -> tuple[int, ...]:
            del path
            return (9876,) if running.is_set() else ()

    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    defrag_calls = 0

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal defrag_calls
        del kwargs
        if command[1:3] == ["filesystem", "defragment"]:
            defrag_calls += 1
            running.set()
            clock.value += 1.1
        stdout = (
            f"8192 8192 0 {command[-1]}"
            if command[1:3] == ["filesystem", "du"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    provider = BtrfsCompressionProvider(
        analyzer=RuntimeAnalyzer(report),  # type: ignore[arg-type]
        executable_finder=lambda name: f"/usr/bin/{name}",
        command_runner=runner,
        clock=clock,
    )
    provider.capabilities = lambda: _capabilities()  # type: ignore[method-assign]
    plan = provider.create_plan(game, report, CompressionProfile.BALANCED)

    result = provider.execute_plan(game, plan, confirmed=True)

    assert result.status == "failed"
    assert result.processed_files == 1
    assert defrag_calls == 1
    assert result.error is not None
    assert "game started" in result.error.casefold()


def test_unavailable_and_fake_providers_are_explicit(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    result = _result(game, plan)
    fake = FakeCompressionProvider(plan=plan, result=result)

    assert fake.capabilities().compression_available is True
    assert fake.create_plan(game, _report(game), CompressionProfile.BALANCED) is plan
    assert fake.execute_plan(game, plan, confirmed=True) is result
    assert fake.executions == [plan.id]
    fake.cancel_all()
    assert fake.cancelled is True

    unavailable = UnavailableCompressionProvider("fixture unavailable")
    assert unavailable.capabilities().compression_available is False
    with pytest.raises(CompressionPlanRejected, match="fixture unavailable"):
        unavailable.create_plan(game, _report(game), CompressionProfile.BALANCED)


def test_service_records_success_and_calls_verified_callback(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    result = _result(game, plan)
    verified: list[str] = []
    history = CompressionHistoryStore(tmp_path / "state" / "history.json")
    service = CompressionService(
        FakeCompressionProvider(plan=plan, result=result),
        history,
        verified_callback=lambda verified_game, verified_result: verified.append(
            f"{verified_game.id}:{verified_result.plan_id}"
        ),
    )

    prepared = service.prepare(
        game,
        _report(game),
        CompressionProfile.BALANCED,
    )
    completed = service.execute(prepared.id, game, confirmed=True)

    assert completed.status == "completed"
    assert verified == [f"{game.id}:{plan.id}"]
    assert service.active_game_ids() == ()
    assert history.pending() == ()
    assert history.history(game.id)[0]["actual_saved_bytes"] == 1024


def test_service_records_normal_provider_exception_as_failed_history(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)

    class ErrorAfterPlanning(FakeCompressionProvider):
        def execute_plan(
            self, _game: Game, _plan: CompressionPlan, **_kwargs: object
        ) -> CompressionResult:
            raise CompressionProviderError("simulated provider error")

    history = CompressionHistoryStore(tmp_path / "history.json")
    service = CompressionService(ErrorAfterPlanning(plan=plan), history)
    prepared = service.prepare(game, _report(game), CompressionProfile.BALANCED)

    result = service.execute(prepared.id, game, confirmed=True)

    assert result.status == "failed"
    assert result.error == "simulated provider error"
    assert history.pending() == ()
    assert history.history(game.id)[0]["status"] == "failed"
    assert service.last_error == "simulated provider error"


def test_service_converts_preflight_cancellation_to_cancelled_history(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    cancelled = Event()
    cancelled.set()

    class CancelledProvider(FakeCompressionProvider):
        def execute_plan(
            self, _game: Game, _plan: CompressionPlan, **_kwargs: object
        ) -> CompressionResult:
            raise CompressionProviderError("cancelled during preflight")

    history = CompressionHistoryStore(tmp_path / "history.json")
    service = CompressionService(CancelledProvider(plan=plan), history)
    prepared = service.prepare(game, _report(game), CompressionProfile.BALANCED)

    result = service.execute(
        prepared.id,
        game,
        confirmed=True,
        cancel_event=cancelled,
    )

    assert result.status == "cancelled"
    assert history.pending() == ()
    assert history.history(game.id)[0]["status"] == "cancelled"


def test_confirmation_rejection_clears_pending_marker_as_failed(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)

    class ConfirmationProvider(FakeCompressionProvider):
        def execute_plan(
            self, _game: Game, _plan: CompressionPlan, **_kwargs: object
        ) -> CompressionResult:
            raise CompressionPlanRejected("Explicit confirmation is required")

    history = CompressionHistoryStore(tmp_path / "history.json")
    service = CompressionService(ConfirmationProvider(plan=plan), history)
    prepared = service.prepare(game, _report(game), CompressionProfile.BALANCED)

    result = service.execute(prepared.id, game, confirmed=False)

    assert result.status == "failed"
    assert result.error == "Explicit confirmation is required"
    assert history.pending() == ()
    assert history.history(game.id)[0]["verification_state"] == (
        "verification_required"
    )


def test_history_migrates_v1_and_persists_v2_atomically(tmp_path: Path) -> None:
    path = tmp_path / "state" / "compression.json"
    path.parent.mkdir()
    legacy = {
        "version": 1,
        "history": [
            {
                "id": "legacy-entry",
                "game_id": "steam-4242",
                "completed_at": "2025-01-01T00:00:00+00:00",
                "status": "completed",
            }
        ],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = CompressionHistoryStore(path)
    game = _steam_game(tmp_path)
    plan = _plan(game)

    assert store.history("steam-4242")[0]["id"] == "legacy-entry"
    store.begin_operation(plan)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["games"]["steam-4242"]["history"][0]["id"] == "legacy-entry"
    assert plan.id in persisted["pending"]
    assert not list(path.parent.glob("*.tmp"))
    assert not list(path.parent.glob(".*.tmp"))


def test_history_recovers_interrupted_operation_as_verification_required(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    path = tmp_path / "state" / "compression.json"
    store = CompressionHistoryStore(path)
    store.begin_operation(plan)

    recovered = CompressionHistoryStore(path).recover_interrupted()

    assert len(recovered) == 1
    assert recovered[0]["plan_id"] == plan.id
    assert recovered[0]["status"] == "verification_required"
    assert recovered[0]["actual_saved_bytes"] is None
    reopened = CompressionHistoryStore(path)
    assert reopened.pending() == ()
    assert reopened.history(game.id)[0]["verification_state"] == (
        "verification_required"
    )


def test_history_records_automatic_compression(tmp_path: Path) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    store = CompressionHistoryStore(tmp_path / "compression-history.json")

    store.begin_operation(plan, automatic=True)
    entry = store.finish_operation(
        game.name,
        str(game.install_path),
        _result(game, plan),
    )

    assert entry["automatic"] is True
    assert store.history(game.id)[0]["automatic"] is True


def test_task_service_shutdown_cancels_and_joins_compression_worker(
    tmp_path: Path,
) -> None:
    game = _steam_game(tmp_path)
    plan = _plan(game)
    entered = Event()
    release = Event()

    class BlockingProvider(FakeCompressionProvider):
        def execute_plan(
            self,
            _game: Game,
            active_plan: CompressionPlan,
            **kwargs: object,
        ) -> CompressionResult:
            entered.set()
            cancel_event = kwargs["cancel_event"]
            assert isinstance(cancel_event, Event)
            assert cancel_event.wait(2.0)
            release.set()
            return _result(
                game,
                active_plan,
                status="cancelled",
                error="cancelled by shutdown",
            )

    provider = BlockingProvider(plan=plan)
    service = CompressionService(
        provider,
        CompressionHistoryStore(tmp_path / "history.json"),
    )
    tasks = BtrfsAnalysisTaskService(
        compression_service=service,
        max_workers=1,
    )
    prepared = service.prepare(game, _report(game), CompressionProfile.BALANCED)
    task = tasks.enqueue_compression_plan(game, prepared, confirmed=True)
    assert entered.wait(1.0)

    stopped = tasks.shutdown(wait=True, timeout=2.0)

    assert stopped is True
    assert release.is_set()
    assert provider.cancelled is True
    final = tasks.get_task(task.id)
    assert final is not None
    assert final.status is TaskStatus.CANCELLED
    assert final.metadata["stage"] == "Cancelled"
    assert service.active_game_ids() == ()


def test_descriptor_walk_blocks_directory_swapped_for_external_symlink(
    tmp_path: Path,
) -> None:
    """A raced ancestor symlink must never reach the Btrfs command runner."""

    from game_optimization_linux.providers import btrfs_compression as provider_module

    game_root = tmp_path / "library" / "steamapps" / "common" / "Fixture"
    original_directory = game_root / "data"
    original_directory.mkdir(parents=True)
    payload = original_directory / "payload.bin"
    payload.write_bytes(b"safe payload")
    root_stat = game_root.stat()
    payload_stat = payload.stat()
    planned = CompressionFile(
        relative_path="data/payload.bin",
        size_bytes=payload_stat.st_size,
        mtime_ns=payload_stat.st_mtime_ns,
        ctime_ns=payload_stat.st_ctime_ns,
        device=payload_stat.st_dev,
        inode=payload_stat.st_ino,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    moved_directory = outside / "moved-data"
    original_directory.rename(moved_directory)
    original_directory.symlink_to(moved_directory, target_is_directory=True)
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = BtrfsCompressionProvider(command_runner=runner)
    identity = provider_module._RootIdentity(
        path=game_root,
        device=root_stat.st_dev,
        inode=root_stat.st_ino,
    )

    with pytest.raises((OSError, CompressionPlanRejected)):
        provider._recompress_file(  # noqa: SLF001 - targeted safety regression
            "/usr/bin/btrfs",
            identity,
            planned,
            3,
            None,
        )

    assert calls == []
