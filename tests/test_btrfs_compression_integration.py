"""Opt-in integration checks against a disposable directory on real Btrfs.

These tests are skipped unless ``GAMEFORGE_BTRFS_TEST_ROOT`` points at an
existing Btrfs directory.  They never inspect or modify a Steam library.  Every
fixture is created below a unique temporary ``library/steamapps/common`` tree
and removed after the test.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from typing import Iterator

import pytest

from gameforge.models import CompressionProfile, FilesystemType, Game, Launcher
from gameforge.providers import BtrfsCompressionProvider, LinuxFilesystemProvider
from gameforge.services import (
    AnalysisLimits,
    BtrfsCompressionAnalyzer,
    CompressionHistoryStore,
    CompressionService,
    GameFingerprintScanner,
)


_MIB = 1024 * 1024


@contextmanager
def _real_btrfs_sandbox() -> Iterator[Path]:
    configured = os.environ.get("GAMEFORGE_BTRFS_TEST_ROOT", "").strip()
    if not configured:
        pytest.skip(
            "set GAMEFORGE_BTRFS_TEST_ROOT to an owned disposable-capable "
            "directory on Btrfs"
        )
    root = Path(configured).expanduser().resolve()
    if not root.is_dir() or root == Path("/"):
        pytest.skip("GAMEFORGE_BTRFS_TEST_ROOT is not a safe existing directory")
    findmnt = shutil.which("findmnt")
    btrfs = shutil.which("btrfs")
    if findmnt is None or btrfs is None:
        pytest.skip("findmnt and btrfs-progs are required")
    mounted = subprocess.run(
        [findmnt, "--noheadings", "--output", "FSTYPE", "--target", str(root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        shell=False,
    )
    if mounted.returncode != 0 or mounted.stdout.strip().casefold() != "btrfs":
        pytest.skip("GAMEFORGE_BTRFS_TEST_ROOT is not on Btrfs")
    with TemporaryDirectory(prefix=".gameforge-btrfs-test-", dir=root) as temporary:
        yield Path(temporary)


def _game(sandbox: Path, name: str) -> Game:
    library = sandbox / "library"
    game_path = library / "steamapps" / "common" / name
    game_path.mkdir(parents=True)
    manifest = library / "steamapps" / "appmanifest_900001.acf"
    manifest.write_text(
        '"AppState"\n{\n'
        '    "appid" "900001"\n'
        f'    "installdir" "{name}"\n'
        '    "buildid" "integration-1"\n'
        '}\n',
        encoding="utf-8",
    )
    manifest_stat = manifest.stat()
    filesystem = LinuxFilesystemProvider().inspect(game_path)
    if filesystem.filesystem is not FilesystemType.BTRFS:
        pytest.skip("the isolated fixture is not visible as Btrfs")
    return Game(
        id=f"steam-integration-{name.casefold()}",
        name=f"Integration {name}",
        launcher=Launcher.STEAM,
        install_path=game_path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id="900001",
        steam_build_id="integration-1",
        steam_manifest_path=manifest,
        steam_manifest_mtime_ns=manifest_stat.st_mtime_ns,
        steam_manifest_size_bytes=manifest_stat.st_size,
        library_path=library,
        data_source="Integration test",
        filesystem_name="btrfs",
        mount_point=filesystem.mount_point,
        filesystem_device=filesystem.device,
        mount_options=filesystem.mount_options,
        is_writable=True,
    )


def _analyzer() -> BtrfsCompressionAnalyzer:
    return BtrfsCompressionAnalyzer(
        LinuxFilesystemProvider(),
        limits=AnalysisLimits(
            max_sample_bytes=8 * _MIB,
            max_bytes_per_file=1 * _MIB,
            max_sample_candidates_per_group=32,
            timeout_seconds=30.0,
            command_timeout_seconds=10.0,
        ),
        process_detector=lambda _path, _cancelled: (),
    )


def _fingerprint(game: Game) -> dict[str, dict[str, int]]:
    snapshot = GameFingerprintScanner().scan(game.install_path)
    assert snapshot.complete, snapshot.errors
    return {
        item.relative_path: {
            "size": item.size,
            "mtime_ns": item.mtime_ns,
        }
        for item in snapshot.files
    }


def _content_digest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            result[path.relative_to(root).as_posix()] = hashlib.file_digest(
                handle, "sha256"
            ).hexdigest()
    return result


def test_real_reflinks_surface_measured_risk_before_defragmentation() -> None:
    with _real_btrfs_sandbox() as sandbox:
        game = _game(sandbox, "SharedGame")
        source = game.install_path / "original.bin"
        clone = game.install_path / "clone.bin"
        source.write_bytes((b"GameForge shared extent fixture\n" * 262_144)[:8 * _MIB])
        copy = shutil.which("cp")
        if copy is None:
            pytest.skip("cp with reflink support is required")
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

        analyzer = _analyzer()
        report = analyzer.analyze(game, sample_files=False)
        assert report.btrfs_du.state == "detected"
        assert (report.btrfs_du.set_shared_bytes or 0) > 0
        initial_content = _content_digest(game.install_path)

        provider = BtrfsCompressionProvider(
            analyzer,
            game_running_checker=lambda _game: False,
            steam_update_checker=lambda _game: False,
        )
        plan = provider.create_plan(
            game,
            report,
            CompressionProfile.BALANCED,
        )
        assert plan.eligible is True
        assert plan.confirmation_required is True
        assert (plan.estimated_shared_growth_bytes or 0) > 0
        assert any("Shared Btrfs extents" in warning for warning in plan.warnings)
        assert not plan.blockers
        after_plan = analyzer.analyze(game, sample_files=False)
        assert after_plan.btrfs_du.state == "detected"
        assert (after_plan.btrfs_du.set_shared_bytes or 0) > 0
        assert _content_digest(game.install_path) == initial_content
        assert provider.active_child_count == 0


def test_real_full_incremental_cancel_and_verification_workflow() -> None:
    with _real_btrfs_sandbox() as sandbox:
        game = _game(sandbox, "WritableGame")
        outside = sandbox / "outside.bin"
        outside.write_bytes(b"outside-must-not-change")
        (game.install_path / "data").mkdir()
        (game.install_path / "compressible.bin").write_bytes(b"A" * (8 * _MIB))
        (game.install_path / "random.bin").write_bytes(os.urandom(2 * _MIB))
        changed = game.install_path / "data" / "changed.bin"
        changed.write_bytes((b"initial-data\n" * 131_072)[:1 * _MIB])
        (game.install_path / "outside-link").symlink_to(outside)
        initial_content = _content_digest(game.install_path)

        analyzer = _analyzer()
        provider = BtrfsCompressionProvider(
            analyzer,
            game_running_checker=lambda _game: False,
            steam_update_checker=lambda _game: False,
        )
        history = CompressionHistoryStore(sandbox / "state" / "compression.json")
        baseline: dict[str, dict[str, int]] | None = None
        service = CompressionService(
            provider,
            history,
            fingerprint_loader=lambda _game: baseline,
        )

        before = analyzer.analyze(game, sample_files=False)
        assert before.btrfs_du.state == "not_detected"
        plan = service.prepare(
            game,
            before,
            CompressionProfile.BALANCED,
            confirmation_required=True,
        )
        assert plan.eligible, plan.blockers
        assert "outside-link" not in {item.relative_path for item in plan.files}
        first = service.execute(plan.id, game, confirmed=True)
        assert first.status in {"completed", "completed_with_warning"}
        if first.actual_saved_bytes is None:
            assert first.status == "completed_with_warning"
            assert first.verification_state == "measurement_unavailable"
            assert any(
                "savings could not be measured" in warning
                for warning in first.warnings
            )
        else:
            assert first.verification_state == "verified"
        assert first.after is not None
        assert first.before.logical_bytes == first.after.logical_bytes
        assert first.before.exclusive_bytes is not None
        assert first.before.shared_bytes == 0
        assert first.after.exclusive_bytes is not None
        assert first.after.shared_bytes == 0
        assert len(first.command_exit_codes) == plan.total_files + 2
        assert set(first.command_exit_codes) == {0}
        assert _content_digest(game.install_path) == initial_content
        assert outside.read_bytes() == b"outside-must-not-change"

        baseline = _fingerprint(game)
        changed.write_bytes((b"updated-data\n" * 131_072)[:1 * _MIB])
        new_file = game.install_path / "data" / "new.bin"
        new_file.write_bytes(b"N" * (2 * _MIB))
        updated_content = _content_digest(game.install_path)
        refreshed = analyzer.analyze(game, sample_files=False)
        incremental = service.prepare(
            game,
            refreshed,
            CompressionProfile.FAST,
            changed_only=True,
            after_update=True,
        )
        planned_paths = {item.relative_path for item in incremental.files}
        assert planned_paths == {"data/changed.bin", "data/new.bin"}
        second = service.execute(incremental.id, game, confirmed=True)
        assert second.status in {"completed", "completed_with_warning"}
        assert second.full_compression is False
        assert second.after_update is True
        assert len(second.command_exit_codes) == incremental.total_files + 2
        assert set(second.command_exit_codes) == {0}
        assert _content_digest(game.install_path) == updated_content

        cancel_report = analyzer.analyze(game, sample_files=False)
        cancel_plan = service.prepare(
            game,
            cancel_report,
            CompressionProfile.MAXIMUM,
        )
        cancelled = Event()

        def request_cancel(progress: object) -> None:
            if isinstance(progress, dict) and progress.get("stage") == "Compressing":
                cancelled.set()

        cancelled_result = service.execute(
            cancel_plan.id,
            game,
            confirmed=True,
            cancel_event=cancelled,
            progress_callback=request_cancel,
        )
        assert cancelled_result.status == "cancelled"
        assert cancelled_result.verification_state in {
            "verified_partial",
            "verification_required",
        }
        assert provider.active_child_count == 0
        assert not history.pending()
        assert len(history.history(game.id)) == 3
        assert _content_digest(game.install_path) == updated_content
