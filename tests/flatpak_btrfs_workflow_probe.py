"""Real Flatpak Btrfs workflow probe using only a synthetic Steam fixture."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from gameforge.models import CompressionProfile, FilesystemType, Game, Launcher
from gameforge.providers import BtrfsCompressionProvider, LinuxFilesystemProvider
from gameforge.services import (
    BtrfsAnalysisReport,
    BtrfsCompressionAnalyzer,
    HostServiceClient,
)


def _measurement(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    result = asdict(value)  # type: ignore[arg-type]
    result.pop("measured_at", None)
    return result


class _LocalProbeAnalyzer:
    """Fresh local metadata for exercising the real write provider without Polkit."""

    def analyze(self, game: Game, **_kwargs: object) -> BtrfsAnalysisReport:
        files = tuple(path for path in game.install_path.rglob("*") if path.is_file())
        logical = sum(path.stat().st_size for path in files)
        fs = game.install_path.stat()
        vfs = os.statvfs(game.install_path)
        available = int(vfs.f_bavail * (vfs.f_frsize or vfs.f_bsize))
        return BtrfsAnalysisReport.from_dict({
            "analyzer_version": 5,
            "game_id": game.id,
            "app_id": game.steam_app_id,
            "game_name": game.name,
            "path": str(game.install_path),
            "path_exists": True,
            "path_is_directory": True,
            "filesystem": "btrfs",
            "is_btrfs": True,
            "writable": True,
            "mount_point": str(game.library_path),
            "filesystem_device": str(fs.st_dev),
            "available_bytes": available,
            "logical_bytes": logical,
            "physical_bytes": logical,
            "file_count": len(files),
            "directory_count": sum(1 for path in game.install_path.rglob("*") if path.is_dir()),
            "symlink_count": 0,
            "hardlink_count": 0,
            "permission_errors": [],
            "scan_complete": True,
            "existing_compression_state": "unknown",
            "persistent_compression_algorithm": None,
            "mount_compression_level": None,
            "compsize": {
                "available": True,
                "message": "synthetic provider-boundary measurement",
                "disk_usage_bytes": logical,
                "uncompressed_bytes": logical,
                "referenced_bytes": logical,
                "saved_bytes": 0,
            },
            "btrfs_du": {
                "available": True,
                "state": "not_detected",
                "total_bytes": logical,
                "exclusive_bytes": logical,
                "set_shared_bytes": 0,
                "estimated_growth_bytes": 0,
                "message": "new non-reflink test file",
            },
            "possible_shared_extents": False,
            "game_running": False,
            "running_process_ids": [],
            "sampled_bytes": 0,
            "sampled_files": 0,
            "sampling_codec": "not run",
            "sampling_complete": True,
            "selected_auto_level": 3,
            "profiles": {},
            "profiles_unlocked": True,
            "compression_eligible": True,
            "benefit": "Unknown",
            "warnings": [],
            "created_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": 0.0,
        })


def main(argv: list[str]) -> int:
    provider_only = "--provider-only" in argv[1:]
    positional = [value for value in argv[1:] if value != "--provider-only"]
    if len(positional) != 1:
        print(
            "usage: flatpak_btrfs_workflow_probe.py [--provider-only] BTRFS_PARENT",
            file=sys.stderr,
        )
        return 2
    parent = Path(positional[0]).resolve(strict=True)
    fixture = Path(tempfile.mkdtemp(prefix=".gameforge-flatpak-probe-", dir=parent))
    try:
        library = fixture / "SteamLibrary"
        game_path = library / "steamapps/common/Flatpak Fixture"
        game_path.mkdir(parents=True)
        payload = game_path / "payload.bin"
        with payload.open("wb") as stream:
            block = (b"GAMEFORGE-FLATPAK-BTRFS-PROBE\0" * 4096)[:128 * 1024]
            for _ in range(64):
                stream.write(block)
        manifest = library / "steamapps/appmanifest_424242.acf"
        manifest.write_text(
            '"AppState"\n{\n'
            '  "appid" "424242"\n'
            '  "buildid" "101"\n'
            '  "installdir" "Flatpak Fixture"\n'
            '}\n',
            encoding="utf-8",
        )
        manifest_info = manifest.stat()
        game = Game(
            id="steam-424242",
            name="Flatpak Fixture",
            launcher=Launcher.STEAM,
            install_path=game_path,
            logical_size_gb=payload.stat().st_size / (1024 ** 3),
            physical_size_gb=0.0,
            filesystem=FilesystemType.BTRFS,
            filesystem_name="btrfs",
            compression_available=True,
            steam_app_id="424242",
            steam_build_id="101",
            steam_manifest_path=manifest,
            steam_manifest_mtime_ns=manifest_info.st_mtime_ns,
            steam_manifest_size_bytes=manifest_info.st_size,
            library_path=library,
        )
        host = None if provider_only else HostServiceClient()
        analyzer = (
            _LocalProbeAnalyzer()
            if provider_only
            else BtrfsCompressionAnalyzer(
                filesystem_provider=LinuxFilesystemProvider(),
                host_service=host,
            )
        )
        report = analyzer.analyze(game, sample_files=False)
        provider = BtrfsCompressionProvider(
            analyzer=analyzer,  # type: ignore[arg-type]
            measurement_provider=host,
        )
        plan = provider.create_plan(
            game,
            report,
            CompressionProfile.BALANCED,
            confirmation_required=True,
        )
        if not plan.eligible:
            raise RuntimeError("plan rejected: " + "; ".join(plan.blockers))
        result = provider.execute_plan(game, plan, confirmed=True)
        output = {
            "btrfs_version": provider.capabilities().btrfs_version.splitlines()[0],
            "analysis": {
                "is_btrfs": report.is_btrfs,
                "scan_complete": report.scan_complete,
                "compsize_available": report.compsize.available,
                "profiles_unlocked": report.profiles_unlocked,
                "source": (
                    "provider_boundary"
                    if provider_only
                    else "host_service_with_graceful_fallback"
                ),
            },
            "plan": {
                "eligible": plan.eligible,
                "files": plan.total_files,
                "bytes": plan.total_bytes,
                "level": plan.one_time_recompression_level,
            },
            "result": {
                "status": result.status,
                "verification_state": result.verification_state,
                "processed_files": result.processed_files,
                "processed_bytes": result.processed_bytes,
                "command_exit_codes": list(result.command_exit_codes),
                "before": _measurement(result.before),
                "after": _measurement(result.after),
                "actual_saved_bytes": result.actual_saved_bytes,
                "error": result.error,
            },
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if result.status in {"completed", "completed_with_warning"} else 1
    finally:
        shutil.rmtree(fixture, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
