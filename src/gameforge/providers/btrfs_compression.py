"""Guarded Btrfs compression provider.

This module contains the only production path that may invoke mutating Btrfs
commands.  Planning and every preflight measurement remain read-only.  The
provider never invokes a shell.  Only the narrowly scoped read-only measurement
helper may be authorized through Polkit; mutating commands always run as the
desktop user.  Files are processed one at a time instead of recursively
defragmenting an untrusted directory tree.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
from threading import Event, RLock
import time
from typing import Any, Protocol
from uuid import uuid4
import zlib

from gameforge.models.compression import (
    CompressionCancelled,
    CompressionFile,
    CompressionMeasurement,
    CompressionPlan,
    CompressionPlanRejected,
    CompressionProviderError,
    CompressionResult,
    CompressionToolCapabilities,
)
from gameforge.models.enums import CompressionProfile, FilesystemType, Launcher
from gameforge.models.game import Game
from gameforge.providers.keyvalues import VDFParseError, parse_keyvalues
from gameforge.services.btrfs_analysis import (
    BtrfsAnalysisReport,
    BtrfsCompressionAnalyzer,
)
from gameforge.services.privileged_measurement import (
    PrivilegedMeasurementClient,
    PrivilegedMeasurementError,
    measurement_delta,
)


logger = logging.getLogger(__name__)
_MIB = 1024 * 1024
_MINIMUM_SAFETY_SPACE = 512 * _MIB
_MAX_MANIFEST_BYTES = 2 * _MIB
# Steam keeps stable installation/runtime flags in the low byte.  Higher bits
# cover update, validation, staging and commit phases.  Treating every higher
# bit as busy is intentionally conservative: a false positive only postpones
# a write operation.
_STEAM_ACTIVE_WRITE_STATE_MASK = ~0xFF
_PROCESS_POLL_INTERVAL_SECONDS = 1.0
_COMPRESSED_EXTENSIONS = frozenset(
    {
        ".7z",
        ".avi",
        ".bz2",
        ".flac",
        ".gz",
        ".jpg",
        ".jpeg",
        ".m4a",
        ".mkv",
        ".mp3",
        ".mp4",
        ".ogg",
        ".pak",
        ".png",
        ".rar",
        ".vpk",
        ".webm",
        ".wem",
        ".xz",
        ".zip",
    }
)


def _short_process_text(value: Any, *, limit: int = 512) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


class CompressionProgressCallback(Protocol):
    def __call__(self, values: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class _RootIdentity:
    path: Path
    device: int
    inode: int
    canonical_path: str = ""
    app_id: str = ""
    build_id: str = ""
    manifest_device: int | None = None
    manifest_inode: int | None = None


class BtrfsCompressionProvider:
    """Create plans and recompress verified files with Btrfs user tools."""

    def __init__(
        self,
        analyzer: BtrfsCompressionAnalyzer | None = None,
        *,
        executable_finder: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        game_running_checker: Callable[[Game], bool] | None = None,
        steam_update_checker: Callable[[Game], bool] | None = None,
        write_task_checker: Callable[[str], bool] | None = None,
        measurement_provider: PrivilegedMeasurementClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._analyzer = analyzer or BtrfsCompressionAnalyzer()
        self._executable_finder = executable_finder
        self._command_runner = command_runner
        self._game_running_checker = game_running_checker
        self._steam_update_checker = steam_update_checker
        self._write_task_checker = write_task_checker
        self._measurement_provider = measurement_provider
        self._clock = clock
        self._children: set[subprocess.Popen[str]] = set()
        self._children_lock = RLock()
        self._cancel_all = Event()
        self._process_check_lock = RLock()
        self._process_checks: dict[str, tuple[str, float, bool]] = {}

    @property
    def active_child_count(self) -> int:
        """Return the number of direct helpers still owned by the provider."""

        with self._children_lock:
            return len(self._children)

    def capabilities(self) -> CompressionToolCapabilities:
        """Inspect versions/help only; no filesystem object is modified."""

        btrfs = self._executable_finder("btrfs")
        compsize = self._executable_finder("compsize")
        if not btrfs:
            return CompressionToolCapabilities(
                btrfs_available=False,
                compsize_available=bool(compsize),
                compsize_version=self._tool_version(compsize),
                message="btrfs tools are missing",
            )

        version = self._tool_version(btrfs)
        property_help = self._read_help(
            [btrfs, "property", "set", "--help"]
        )
        defrag_help = self._read_help(
            [btrfs, "filesystem", "defragment", "--help"]
        )
        property_supported = "compression" in property_help.casefold() or bool(
            property_help
        )
        recompression_supported = (
            "defragment" in defrag_help.casefold()
            and ("--compress" in defrag_help or "-c" in defrag_help)
        )
        level_supported = "--level" in defrag_help or "-L" in defrag_help
        missing: list[str] = []
        if not property_supported:
            missing.append("per-inode compression property")
        if not recompression_supported:
            missing.append("Btrfs recompression")
        if not level_supported:
            missing.append("one-time compression level")
        return CompressionToolCapabilities(
            btrfs_available=True,
            btrfs_version=version,
            compsize_available=bool(compsize),
            compsize_version=self._tool_version(compsize),
            property_supported=property_supported,
            recompression_supported=recompression_supported,
            level_supported=level_supported,
            message=(
                "Available"
                if not missing
                else "Missing support for " + ", ".join(missing)
            ),
        )

    def create_plan(
        self,
        game: Game,
        report: BtrfsAnalysisReport,
        profile: CompressionProfile,
        *,
        previous_fingerprint: Mapping[str, Mapping[str, Any]] | None = None,
        after_update: bool = False,
        confirmation_required: bool = True,
        minimum_free_bytes: int = 0,
    ) -> CompressionPlan:
        """Build a deterministic plan using only metadata and bounded samples."""

        if not isinstance(game, Game):
            raise TypeError("game must be a Game")
        if not isinstance(report, BtrfsAnalysisReport):
            raise TypeError("report must be a BtrfsAnalysisReport")
        if not isinstance(profile, CompressionProfile):
            profile = CompressionProfile(str(profile))
        if isinstance(minimum_free_bytes, bool) or int(minimum_free_bytes) < 0:
            raise ValueError("minimum_free_bytes must be non-negative")

        blockers: list[str] = []
        warnings: list[str] = []
        root_identity = self._validate_game_root(game, blockers)
        capabilities = self.capabilities()
        if not capabilities.compression_available:
            blockers.append(capabilities.message or "Btrfs compression tools unavailable")

        expected_path = os.path.abspath(os.fspath(game.install_path))
        if report.game_id != game.id or os.path.abspath(report.path) != expected_path:
            blockers.append("The analysis report does not match this game path")
        if not report.path_exists or not report.path_is_directory:
            blockers.append("The analyzed game path is unavailable")
        if not report.scan_complete:
            blockers.append("A complete analysis is required")
        if not report.is_btrfs or report.filesystem.casefold() != "btrfs":
            blockers.append("The game is not on a verified Btrfs filesystem")
        if not report.writable:
            blockers.append("The game directory is not writable")
        if report.game_running or self._is_game_running(game):
            blockers.append("The game is currently running")
        if self._steam_is_updating(game):
            blockers.append("Steam is currently installing or updating this game")
        if self._has_other_writer(game.id):
            blockers.append("Another write task is active for this game")

        shared_state, shared_bytes, shared_growth, shared_message = (
            self._shared_extent_values(report)
        )
        if shared_state == "detected":
            warnings.append(
                "Shared Btrfs extents were detected. Recompression will break "
                "sharing for processed extents, so snapshots may retain the "
                "old data and physical usage may temporarily increase"
            )
            if shared_growth is not None:
                warnings.append(
                    "Breaking shared extents could increase physical usage by "
                    f"up to {shared_growth} bytes"
                )
        elif shared_state == "unknown":
            blockers.append(
                "Shared-extent risk could not be measured reliably; operation "
                "is blocked (fail closed)"
            )
        if shared_message and shared_state != "not_detected":
            warnings.append(shared_message)

        files: tuple[CompressionFile, ...] = ()
        skipped: tuple[str, ...] = ()
        if root_identity is not None:
            files, skipped, scan_errors = self._collect_candidates(
                root_identity,
                previous_fingerprint=previous_fingerprint,
            )
            warnings.extend(scan_errors)
            if scan_errors:
                blockers.append("The file plan could not be built completely")
            if not files:
                blockers.append(
                    "No new or changed files require recompression"
                    if previous_fingerprint is not None
                    else "No eligible regular files require recompression"
                )

        full_compression = previous_fingerprint is None
        largest_file = max((item.size_bytes for item in files), default=0)
        required_free = max(
            int(minimum_free_bytes),
            _MINIMUM_SAFETY_SPACE
            + largest_file
            + max(0, int(shared_growth or 0)),
        )
        if report.available_bytes is None:
            blockers.append("Available Btrfs space could not be measured")
        elif report.available_bytes < required_free:
            blockers.append(
                f"Insufficient free space: {required_free} bytes are required"
            )

        selected_level = self._profile_level(profile, report)
        estimate = report.profiles.get(profile.value)
        estimate_low = (
            estimate.estimated_savings_low_bytes if estimate is not None else None
        )
        estimate_high = (
            estimate.estimated_savings_high_bytes if estimate is not None else None
        )
        before = self.measurement_from_report(report)
        blockers = list(dict.fromkeys(blockers))
        warnings = list(dict.fromkeys(warnings))
        return CompressionPlan(
            id=f"compression-plan-{uuid4().hex}",
            game_id=game.id,
            app_id=str(game.steam_app_id or ""),
            game_name=game.name,
            game_path=expected_path,
            profile=profile,
            persistent_compression_algorithm="zstd",
            one_time_recompression_level=selected_level,
            files=files,
            skipped_files=skipped,
            full_compression=full_compression,
            after_update=bool(after_update),
            build_id=getattr(game, "steam_build_id", None),
            estimated_savings_low_bytes=estimate_low,
            estimated_savings_high_bytes=estimate_high,
            estimated_shared_growth_bytes=shared_growth,
            available_bytes=report.available_bytes,
            required_free_bytes=required_free,
            before=before,
            eligible=not blockers,
            confirmation_required=bool(confirmation_required),
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    def execute_plan(
        self,
        game: Game,
        plan: CompressionPlan,
        *,
        confirmed: bool,
        automatic_authorized: bool = False,
        cancel_event: Event | None = None,
        progress_callback: CompressionProgressCallback | None = None,
    ) -> CompressionResult:
        """Execute a freshly revalidated plan and perform read-only verification."""

        started_at = datetime.now(UTC)
        started_clock = self._clock()
        exit_codes: list[int] = []
        warnings = list(plan.warnings)
        processed_files = 0
        processed_bytes = 0

        if not plan.eligible:
            raise CompressionPlanRejected("; ".join(plan.blockers))
        if plan.game_id != game.id:
            raise CompressionPlanRejected("The plan belongs to another game")
        if plan.confirmation_required and not (confirmed or automatic_authorized):
            raise CompressionPlanRejected("Explicit confirmation is required")

        root_identity = self._require_game_root(game)
        if os.path.abspath(os.fspath(root_identity.path)) != os.path.abspath(
            plan.game_path
        ):
            raise CompressionPlanRejected("The game path changed after planning")

        self._emit(
            progress_callback,
            stage="Preparing",
            processed_files=0,
            total_files=plan.total_files,
            processed_bytes=0,
            total_bytes=plan.total_bytes,
            current_file="",
            elapsed_seconds=0.0,
        )
        fresh = self._analyzer.analyze(
            game,
            cancel_event=cancel_event,
            sample_files=False,
            measure_compsize=self._measurement_provider is None,
        )
        if fresh.logical_bytes != plan.before.logical_bytes:
            raise CompressionPlanRejected(
                "The game directory changed after planning; analyze it again"
            )
        preflight = self.create_plan(
            game,
            fresh,
            plan.profile,
            previous_fingerprint=(
                {
                    item.relative_path: {
                        "size": item.size_bytes,
                        "mtime_ns": item.mtime_ns,
                    }
                    for item in plan.files
                }
                if not plan.full_compression
                else None
            ),
            after_update=plan.after_update,
            confirmation_required=plan.confirmation_required,
            minimum_free_bytes=max(
                0, plan.required_free_bytes - _MINIMUM_SAFETY_SPACE
            ),
        )
        # Rebuilding an incremental fingerprint from planned files would yield
        # an empty delta.  Safety decisions come from the new preflight, while
        # the original immutable file list remains the user's reviewed scope.
        preflight_blockers = tuple(
            blocker
            for blocker in preflight.blockers
            if blocker != "No new or changed files require recompression"
        )
        if preflight_blockers:
            raise CompressionPlanRejected("; ".join(preflight_blockers))
        if (
            plan.before.shared_extent_state != "detected"
            and preflight.before.shared_extent_state == "detected"
        ):
            raise CompressionPlanRejected(
                "Shared extents appeared after confirmation; review a new plan"
            )
        if (
            plan.estimated_shared_growth_bytes is not None
            and preflight.estimated_shared_growth_bytes is not None
            and preflight.estimated_shared_growth_bytes
            > plan.estimated_shared_growth_bytes
        ):
            raise CompressionPlanRejected(
                "The estimated shared-extent allocation risk increased; "
                "review a new plan"
            )
        execution_before = self._measurement_or_fallback(
            game,
            preflight.before,
            warnings,
        )
        root_after_baseline = self._require_game_root(game)
        if not self._same_root_identity(root_identity, root_after_baseline):
            raise CompressionPlanRejected(
                "The game directory changed during the privileged baseline measurement"
            )
        self._validate_planned_files(root_identity, plan.files)
        self._check_runtime_guards(game, cancel_event)

        btrfs = self._executable_finder("btrfs")
        if not btrfs:
            raise CompressionPlanRejected("btrfs tools disappeared after planning")

        try:
            property_code = self._set_directory_compression(
                btrfs,
                root_identity,
                cancel_event,
            )
            exit_codes.append(property_code)
            if property_code != 0:
                raise CompressionProviderError(
                    f"btrfs property set exited with status {property_code}"
                )

            for item in plan.files:
                self._check_runtime_guards(game, cancel_event)
                current_file = item.relative_path
                self._emit(
                    progress_callback,
                    stage="Compressing",
                    processed_files=processed_files,
                    total_files=plan.total_files,
                    processed_bytes=processed_bytes,
                    total_bytes=plan.total_bytes,
                    current_file=current_file,
                    elapsed_seconds=max(0.0, self._clock() - started_clock),
                )
                code = self._recompress_file(
                    btrfs,
                    root_identity,
                    item,
                    plan.one_time_recompression_level,
                    cancel_event,
                    allow_shared_extents=bool(
                        plan.before.shared_extent_state == "detected"
                        and confirmed
                        and not automatic_authorized
                    ),
                )
                exit_codes.append(code)
                if code != 0:
                    raise CompressionProviderError(
                        f"Btrfs recompression failed for {current_file} "
                        f"with status {code}"
                    )
                processed_files += 1
                processed_bytes += item.size_bytes
            sync_code = self._sync_filesystem(
                btrfs,
                root_identity,
                cancel_event,
            )
            exit_codes.append(sync_code)
            if sync_code != 0:
                raise CompressionProviderError(
                    f"btrfs filesystem sync exited with status {sync_code}"
                )
        except CompressionCancelled:
            return self._cancelled_result(
                game,
                plan,
                started_at,
                processed_files,
                processed_bytes,
                exit_codes,
                warnings,
                execution_before,
            )
        except Exception as error:
            after = self._verification_measurement(game, warnings)
            return CompressionResult(
                plan_id=plan.id,
                game_id=game.id,
                profile=plan.profile,
                status="failed",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                processed_files=processed_files,
                processed_bytes=processed_bytes,
                before=execution_before,
                after=after,
                actual_saved_bytes=self._actual_savings(execution_before, after),
                verification_state=(
                    "verified_partial" if after is not None else "verification_required"
                ),
                full_compression=plan.full_compression,
                after_update=plan.after_update,
                build_id=plan.build_id,
                command_exit_codes=tuple(exit_codes),
                warnings=tuple(dict.fromkeys(warnings)),
                error=str(error) or type(error).__name__,
            )

        self._emit(
            progress_callback,
            stage="Verifying",
            processed_files=processed_files,
            total_files=plan.total_files,
            processed_bytes=processed_bytes,
            total_bytes=plan.total_bytes,
            current_file="",
            elapsed_seconds=max(0.0, self._clock() - started_clock),
        )
        after_report = self._analyzer.analyze(
            game,
            cancel_event=cancel_event,
            sample_files=False,
            measure_compsize=self._measurement_provider is None,
        )
        after = self._measurement_or_fallback(
            game,
            self.measurement_from_report(after_report),
            warnings,
        )
        verification_errors = self._verify_after(
            preflight.before.logical_bytes,
            after_report,
        )
        actual_saved = self._actual_savings(execution_before, after)
        measurement_unavailable = actual_saved is None
        if measurement_unavailable:
            warnings.append(
                "Compression completed, but savings could not be measured"
            )
        if actual_saved is not None and actual_saved < 0:
            warnings.append(
                f"Physical usage increased by {-actual_saved} bytes"
            )
        if (
            actual_saved is not None
            and plan.estimated_savings_low_bytes is not None
            and actual_saved < plan.estimated_savings_low_bytes
        ):
            warnings.append(
                "Actual savings were lower than the estimated range"
            )
        warnings.extend(verification_errors)
        if verification_errors:
            status = "verification_required"
            verification_state = "failed"
        elif measurement_unavailable:
            status = "completed_with_warning"
            verification_state = "measurement_unavailable"
        elif warnings:
            status = "completed_with_warning"
            verification_state = "verified"
        else:
            status = "completed"
            verification_state = "verified"
        self._emit(
            progress_callback,
            stage="Completed",
            processed_files=processed_files,
            total_files=plan.total_files,
            processed_bytes=processed_bytes,
            total_bytes=plan.total_bytes,
            current_file="",
            elapsed_seconds=max(0.0, self._clock() - started_clock),
        )
        return CompressionResult(
            plan_id=plan.id,
            game_id=game.id,
            profile=plan.profile,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            processed_files=processed_files,
            processed_bytes=processed_bytes,
            before=execution_before,
            after=after,
            actual_saved_bytes=actual_saved,
            verification_state=verification_state,
            full_compression=plan.full_compression,
            after_update=plan.after_update,
            build_id=plan.build_id,
            command_exit_codes=tuple(exit_codes),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def cancel_all(self) -> None:
        """Terminate and reap all active direct children."""

        self._cancel_all.set()
        measurement_provider = self._measurement_provider
        if measurement_provider is not None:
            measurement_provider.cancel_all()
        with self._children_lock:
            children = tuple(self._children)
        for process in children:
            self._terminate_process(process)

    def measure_current(self, game: Game) -> CompressionMeasurement:
        """Authenticate and measure without changing the game or filesystem."""

        provider = self._measurement_provider
        if provider is None:
            raise PrivilegedMeasurementError(
                "The privileged compression measurement helper is unavailable"
            )
        measurement = provider.measure(game)
        if measurement.compsize_disk_bytes is None:
            raise PrivilegedMeasurementError(
                measurement.measurement_error
                or "Privileged compsize measurement is unavailable"
            )
        return measurement

    @staticmethod
    def measurement_from_report(
        report: BtrfsAnalysisReport,
    ) -> CompressionMeasurement:
        btrfs_du = getattr(report, "btrfs_du", None)
        shared_state = str(getattr(btrfs_du, "state", "unknown"))
        if shared_state not in {"detected", "not_detected", "unknown"}:
            shared_state = "unknown"
        exclusive = getattr(btrfs_du, "exclusive_bytes", None)
        shared = getattr(btrfs_du, "set_shared_bytes", None)
        compsize = report.compsize
        physical = compsize.disk_usage_bytes or 0
        return CompressionMeasurement(
            logical_bytes=report.logical_bytes,
            physical_bytes=max(0, physical),
            exclusive_bytes=exclusive,
            shared_bytes=shared,
            compsize_disk_bytes=compsize.disk_usage_bytes,
            compsize_uncompressed_bytes=compsize.uncompressed_bytes,
            compsize_referenced_bytes=compsize.referenced_bytes,
            scan_complete=bool(report.scan_complete),
            shared_extent_state=shared_state,
            filesystem_available_bytes=report.available_bytes,
            measurement_source="unprivileged",
            measurement_error=(
                None if compsize.disk_usage_bytes is not None else compsize.message
            ),
        )

    def _measurement_or_fallback(
        self,
        game: Game,
        fallback: CompressionMeasurement,
        warnings: list[str],
    ) -> CompressionMeasurement:
        provider = self._measurement_provider
        if provider is None:
            return fallback
        try:
            measurement = provider.measure(game)
        except PrivilegedMeasurementError as error:
            warnings.append(
                f"Authoritative compression measurement unavailable: {error}"
            )
            return fallback
        if measurement.measurement_error or measurement.compsize_disk_bytes is None:
            warnings.append(
                "Authoritative compression measurement unavailable: "
                + (
                    measurement.measurement_error
                    or "compsize did not return physical usage"
                )
            )
        return measurement

    @staticmethod
    def _same_root_identity(
        expected: _RootIdentity,
        observed: _RootIdentity,
    ) -> bool:
        return bool(
            (
                expected.canonical_path,
                expected.device,
                expected.inode,
                expected.app_id,
                expected.build_id,
                expected.manifest_device,
                expected.manifest_inode,
            )
            == (
                observed.canonical_path,
                observed.device,
                observed.inode,
                observed.app_id,
                observed.build_id,
                observed.manifest_device,
                observed.manifest_inode,
            )
        )

    @staticmethod
    def _shared_extent_values(
        report: BtrfsAnalysisReport,
    ) -> tuple[str, int | None, int | None, str]:
        value = getattr(report, "btrfs_du", None)
        if value is None:
            return "unknown", None, None, "Btrfs shared extents were not measured"
        state = str(getattr(value, "state", "unknown"))
        if state not in {"detected", "not_detected", "unknown"}:
            state = "unknown"
        return (
            state,
            getattr(value, "set_shared_bytes", None),
            getattr(value, "estimated_growth_bytes", None),
            str(getattr(value, "message", "")),
        )

    def _validate_game_root(
        self, game: Game, blockers: list[str]
    ) -> _RootIdentity | None:
        manifest_identity: tuple[int, int] | None = None
        if game.launcher is not Launcher.STEAM or not game.steam_app_id:
            blockers.append("Only verified Steam installations are supported")
        if not game.library_available:
            blockers.append("The Steam library is unavailable")
        if game.filesystem is not FilesystemType.BTRFS and (
            game.filesystem_name.casefold() != "btrfs"
        ):
            blockers.append("The game model is not on Btrfs")
        root = Path(game.install_path)
        if not root.is_absolute():
            blockers.append("The game path must be absolute")
            return None
        try:
            info = os.lstat(root)
        except OSError as error:
            blockers.append(f"The game path cannot be inspected: {error}")
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            blockers.append("The game root must be a real directory, not a symlink")
            return None
        if game.library_path is None:
            blockers.append("The Steam library path is unknown")
        else:
            expected_parent = os.path.realpath(
                os.fspath(Path(game.library_path) / "steamapps" / "common")
            )
            absolute_root = os.path.realpath(os.fspath(root))
            try:
                contained = (
                    os.path.commonpath((absolute_root, expected_parent))
                    == expected_parent
                )
            except ValueError:
                contained = False
            if not contained or absolute_root == expected_parent:
                blockers.append(
                    "The path is not a game directory below steamapps/common"
                )
            manifest_identity = self._validate_steam_manifest(game, blockers)
        return _RootIdentity(
            path=root,
            canonical_path=os.path.realpath(os.fspath(root)),
            device=max(0, int(info.st_dev)),
            inode=max(0, int(info.st_ino)),
            app_id=str(game.steam_app_id or ""),
            build_id=str(game.steam_build_id or ""),
            manifest_device=(
                manifest_identity[0] if manifest_identity is not None else None
            ),
            manifest_inode=(
                manifest_identity[1] if manifest_identity is not None else None
            ),
        )

    @staticmethod
    def _validate_steam_manifest(
        game: Game,
        blockers: list[str],
    ) -> tuple[int, int] | None:
        """Tie the candidate path to a regular local Steam appmanifest."""

        if game.library_path is None or not game.steam_app_id:
            return None
        expected = (
            Path(game.library_path)
            / "steamapps"
            / f"appmanifest_{game.steam_app_id}.acf"
        )
        expected_absolute = os.path.abspath(os.fspath(expected))
        configured = game.steam_manifest_path
        if configured is not None and os.path.abspath(os.fspath(configured)) != (
            expected_absolute
        ):
            blockers.append("The Steam manifest path does not match this library")
            return None
        if (
            configured is None
            or game.steam_manifest_mtime_ns is None
            or game.steam_manifest_size_bytes is None
        ):
            blockers.append(
                "Steam manifest metadata is incomplete; refresh the library"
            )
            return None
        try:
            manifest_stat, normalized = (
                BtrfsCompressionProvider._read_verified_steam_manifest(game)
            )
            if (
                manifest_stat.st_mtime_ns != game.steam_manifest_mtime_ns
                or manifest_stat.st_size != game.steam_manifest_size_bytes
            ):
                raise OSError(
                    "manifest metadata changed; refresh the Steam library"
                )
            current_build = str(normalized.get("buildid", "")).strip() or None
            if (
                game.steam_build_id is not None
                and current_build != game.steam_build_id
            ):
                raise OSError("manifest Build ID changed")
            return (
                max(0, int(manifest_stat.st_dev)),
                max(0, int(manifest_stat.st_ino)),
            )
        except (OSError, UnicodeError, VDFParseError) as error:
            blockers.append(f"The Steam appmanifest could not be verified: {error}")
            return None

    @staticmethod
    def _read_verified_steam_manifest(
        game: Game,
    ) -> tuple[os.stat_result, dict[str, Any]]:
        """Read the expected manifest without following or racing a symlink."""

        if game.library_path is None or not game.steam_app_id:
            raise OSError("Steam manifest location is unknown")
        expected = (
            Path(game.library_path)
            / "steamapps"
            / f"appmanifest_{game.steam_app_id}.acf"
        )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor: int | None = None
        try:
            manifest_stat = os.lstat(expected)
            if stat.S_ISLNK(manifest_stat.st_mode) or not stat.S_ISREG(
                manifest_stat.st_mode
            ):
                raise OSError("manifest is not a regular no-follow file")
            descriptor = os.open(expected, flags)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino)
                != (manifest_stat.st_dev, manifest_stat.st_ino)
            ):
                raise OSError("manifest changed while it was opened")
            chunks: list[bytes] = []
            remaining = _MAX_MANIFEST_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise OSError("manifest exceeds the safety size limit")
            after = os.fstat(descriptor)
            if (
                (after.st_dev, after.st_ino)
                != (manifest_stat.st_dev, manifest_stat.st_ino)
                or after.st_size != manifest_stat.st_size
                or after.st_mtime_ns != manifest_stat.st_mtime_ns
                or after.st_ctime_ns != manifest_stat.st_ctime_ns
            ):
                raise OSError("manifest changed while it was read")
            parsed = parse_keyvalues(payload.decode("utf-8", errors="replace"))
            app_state = next(
                (
                    value
                    for key, value in parsed.items()
                    if key.casefold() == "appstate" and isinstance(value, dict)
                ),
                None,
            )
            if not isinstance(app_state, dict):
                raise OSError("manifest has no AppState object")
            normalized = {key.casefold(): value for key, value in app_state.items()}
            app_id = str(normalized.get("appid", "")).strip()
            install_directory = str(normalized.get("installdir", "")).strip()
            if app_id != str(game.steam_app_id):
                raise OSError("manifest AppID does not match the game")
            if not install_directory or install_directory != game.install_path.name:
                raise OSError("manifest installdir does not match the game path")
            return after, normalized
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _require_game_root(self, game: Game) -> _RootIdentity:
        blockers: list[str] = []
        root = self._validate_game_root(game, blockers)
        if root is None or blockers:
            raise CompressionPlanRejected("; ".join(blockers))
        return root

    def _collect_candidates(
        self,
        root: _RootIdentity,
        *,
        previous_fingerprint: Mapping[str, Mapping[str, Any]] | None,
    ) -> tuple[tuple[CompressionFile, ...], tuple[str, ...], tuple[str, ...]]:
        files: list[CompressionFile] = []
        skipped: list[str] = []
        errors: list[str] = []
        pending: list[tuple[int, PurePosixPath]] = []
        seen_directories = {(root.device, root.inode)}
        try:
            pending.append((self._open_verified_root(root), PurePosixPath()))
            while pending:
                directory_fd, relative_directory = pending.pop()
                try:
                    with os.scandir(directory_fd) as entries:
                        for entry in entries:
                            relative_path = relative_directory / entry.name
                            relative = relative_path.as_posix()
                            try:
                                info = entry.stat(follow_symlinks=False)
                            except OSError as error:
                                errors.append(f"{relative}: {error}")
                                continue
                            mode = info.st_mode
                            if stat.S_ISLNK(mode):
                                continue
                            if info.st_dev != root.device:
                                continue
                            if stat.S_ISDIR(mode):
                                identity = (info.st_dev, info.st_ino)
                                if identity in seen_directories:
                                    continue
                                try:
                                    child_fd = self._open_child_directory(
                                        directory_fd,
                                        entry.name,
                                        expected_identity=identity,
                                        root_device=root.device,
                                    )
                                except OSError as error:
                                    errors.append(f"{relative}: {error}")
                                    continue
                                seen_directories.add(identity)
                                pending.append((child_fd, relative_path))
                                continue
                            if not stat.S_ISREG(mode):
                                continue
                            prior = (
                                previous_fingerprint.get(relative)
                                if previous_fingerprint is not None
                                else None
                            )
                            unchanged = False
                            if prior is not None:
                                try:
                                    unchanged = (
                                        int(prior.get("size", -1)) == info.st_size
                                        and int(prior.get("mtime_ns", -1))
                                        == info.st_mtime_ns
                                    )
                                    if unchanged and "ctime_ns" in prior:
                                        unchanged = (
                                            int(prior.get("ctime_ns", -1))
                                            == info.st_ctime_ns
                                        )
                                except (TypeError, ValueError):
                                    unchanged = False
                            if unchanged:
                                continue
                            if self._not_worth_recompressing_at(
                                directory_fd,
                                entry.name,
                                relative_path,
                                info,
                            ):
                                skipped.append(relative)
                                continue
                            files.append(
                                CompressionFile(
                                    relative_path=relative,
                                    size_bytes=max(0, int(info.st_size)),
                                    mtime_ns=max(0, int(info.st_mtime_ns)),
                                    ctime_ns=max(0, int(info.st_ctime_ns)),
                                    device=max(0, int(info.st_dev)),
                                    inode=max(0, int(info.st_ino)),
                                )
                            )
                except OSError as error:
                    label = relative_directory.as_posix() or "."
                    errors.append(f"{label}: {error}")
                finally:
                    os.close(directory_fd)
        except OSError as error:
            errors.append(f".: {error}")
        finally:
            for descriptor, _relative in pending:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        files.sort(key=lambda item: item.relative_path)
        skipped.sort()
        return tuple(files), tuple(skipped), tuple(errors)

    @staticmethod
    def _not_worth_recompressing_at(
        directory_fd: int,
        name: str,
        relative_path: PurePosixPath,
        info: os.stat_result,
    ) -> bool:
        """Use an extension only to select a bounded read-only sample."""

        if (
            relative_path.suffix.casefold() not in _COMPRESSED_EXTENSIONS
            or info.st_size < 4096
        ):
            return False
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != info.st_dev
                or opened.st_ino != info.st_ino
            ):
                return False
            sample = os.read(descriptor, min(256 * 1024, max(0, info.st_size)))
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(sample) < 4096:
            return False
        return len(zlib.compress(sample, level=1)) / len(sample) >= 0.98

    @staticmethod
    def _open_verified_root(root: _RootIdentity) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(root.path, flags)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or (info.st_dev, info.st_ino) != (root.device, root.inode)
            ):
                raise OSError("the game root changed during traversal")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _open_child_directory(
        parent_fd: int,
        name: str,
        *,
        expected_identity: tuple[int, int],
        root_device: int,
    ) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_dev != root_device
                or (info.st_dev, info.st_ino) != expected_identity
            ):
                raise OSError("directory changed during traversal")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _profile_level(
        profile: CompressionProfile, report: BtrfsAnalysisReport
    ) -> int:
        if profile is CompressionProfile.FAST:
            return 1
        if profile is CompressionProfile.BALANCED:
            return 3
        if profile is CompressionProfile.MAXIMUM:
            return 9
        return (
            report.selected_auto_level
            if report.selected_auto_level in {1, 3, 6, 9}
            else 3
        )

    def _validate_planned_files(
        self, root: _RootIdentity, files: Sequence[CompressionFile]
    ) -> None:
        for item in files:
            try:
                descriptor = self._open_planned_file(root, item)
            except OSError as error:
                raise CompressionPlanRejected(
                    f"Planned file is unavailable: {item.relative_path}: {error}"
                ) from error
            else:
                os.close(descriptor)

    @staticmethod
    def _path_for(root: Path, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
            raise CompressionPlanRejected("A plan contains an unsafe path")
        candidate = root.joinpath(*pure.parts)
        root_absolute = os.path.abspath(os.fspath(root))
        candidate_absolute = os.path.abspath(os.fspath(candidate))
        try:
            contained = (
                os.path.commonpath((candidate_absolute, root_absolute))
                == root_absolute
            )
        except ValueError:
            contained = False
        if not contained:
            raise CompressionPlanRejected("A plan path escapes the game directory")
        return candidate

    def _open_planned_file(
        self,
        root: _RootIdentity,
        item: CompressionFile,
    ) -> int:
        """Open a plan entry through a descriptor-only, no-symlink walk."""

        pure = PurePosixPath(item.relative_path)
        if (
            pure.is_absolute()
            or len(pure.parts) < 1
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise CompressionPlanRejected("A plan contains an unsafe path")
        descriptors: list[int] = []
        try:
            current_fd = self._open_verified_root(root)
            descriptors.append(current_fd)
            for component in pure.parts[:-1]:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                child_fd = os.open(component, flags, dir_fd=current_fd)
                child_info = os.fstat(child_fd)
                if (
                    not stat.S_ISDIR(child_info.st_mode)
                    or child_info.st_dev != root.device
                ):
                    os.close(child_fd)
                    raise OSError("planned directory left the game filesystem")
                descriptors.append(child_fd)
                current_fd = child_fd
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            descriptor = os.open(pure.parts[-1], flags, dir_fd=current_fd)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_dev != root.device
                or info.st_dev != item.device
                or info.st_ino != item.inode
                or info.st_size != item.size_bytes
                or info.st_mtime_ns != item.mtime_ns
                or info.st_ctime_ns != item.ctime_ns
            ):
                os.close(descriptor)
                raise CompressionPlanRejected(
                    f"Planned file changed: {item.relative_path}"
                )
            return descriptor
        finally:
            for directory_fd in reversed(descriptors):
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

    def _set_directory_compression(
        self,
        btrfs: str,
        root: _RootIdentity,
        cancel_event: Event | None,
    ) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(root.path, flags)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (root.device, root.inode):
                raise CompressionPlanRejected("The game root changed during preflight")
            descriptor_path = f"/proc/self/fd/{descriptor}"
            completed = self._run_command(
                [
                    btrfs,
                    "property",
                    "set",
                    "-t",
                    "inode",
                    descriptor_path,
                    "compression",
                    "zstd",
                ],
                cancel_event=cancel_event,
                pass_fds=(descriptor,),
            )
            return completed.returncode
        finally:
            os.close(descriptor)

    def _sync_filesystem(
        self,
        btrfs: str,
        root: _RootIdentity,
        cancel_event: Event | None,
    ) -> int:
        """Flush the affected Btrfs before the final space measurements."""

        descriptor = self._open_verified_root(root)
        try:
            completed = self._run_command(
                [
                    btrfs,
                    "filesystem",
                    "sync",
                    f"/proc/self/fd/{descriptor}",
                ],
                cancel_event=cancel_event,
                pass_fds=(descriptor,),
                timeout=120.0,
            )
            return completed.returncode
        finally:
            os.close(descriptor)

    def _recompress_file(
        self,
        btrfs: str,
        root: _RootIdentity,
        item: CompressionFile,
        level: int,
        cancel_event: Event | None,
        *,
        allow_shared_extents: bool = False,
    ) -> int:
        descriptor = self._open_planned_file(root, item)
        try:
            self._assert_file_has_no_shared_extents(
                btrfs,
                descriptor,
                item,
                cancel_event,
                allow_shared_extents=allow_shared_extents,
            )
            completed = self._run_command(
                [
                    btrfs,
                    "filesystem",
                    "defragment",
                    "-f",
                    "-czstd",
                    "--level",
                    str(level),
                    f"/proc/self/fd/{descriptor}",
                ],
                cancel_event=cancel_event,
                pass_fds=(descriptor,),
            )
            return completed.returncode
        finally:
            os.close(descriptor)

    def _assert_file_has_no_shared_extents(
        self,
        btrfs: str,
        descriptor: int,
        item: CompressionFile,
        cancel_event: Event | None,
        *,
        allow_shared_extents: bool,
    ) -> None:
        """Fail closed on the last read-only FIEMAP check before defrag.

        A reflink may be created after the directory-wide analysis without
        changing this inode's size, mtime or ctime.  Holding the verified file
        descriptor and asking ``btrfs filesystem du`` about that exact inode
        closes that otherwise invisible planning gap as far as the public
        userspace API allows.
        """

        descriptor_path = f"/proc/self/fd/{descriptor}"
        completed = self._run_command(
            [
                btrfs,
                "filesystem",
                "du",
                "--raw",
                "--summarize",
                descriptor_path,
            ],
            cancel_event=cancel_event,
            pass_fds=(descriptor,),
            timeout=60.0,
        )
        if completed.returncode != 0:
            detail = str(completed.stderr or "").strip()
            suffix = f": {detail}" if detail else ""
            raise CompressionPlanRejected(
                "Shared-extent recheck failed for "
                f"{item.relative_path} (status {completed.returncode}){suffix}"
            )
        result = BtrfsCompressionAnalyzer.parse_btrfs_du(
            str(completed.stdout or "")
        )
        if result.state == "detected":
            growth = result.estimated_growth_bytes
            suffix = (
                f"; possible physical growth up to {growth} bytes"
                if growth is not None
                else ""
            )
            if not allow_shared_extents:
                raise CompressionPlanRejected(
                    "Shared Btrfs extents appeared before recompression of "
                    f"{item.relative_path}{suffix}"
                )
            logger.warning(
                "Proceeding with explicitly confirmed shared-extent risk for "
                "%s%s",
                item.relative_path,
                suffix,
            )
            return
        if result.state != "not_detected":
            raise CompressionPlanRejected(
                "Shared-extent risk could not be rechecked reliably for "
                f"{item.relative_path}; operation stopped fail closed"
            )

    def _run_command(
        self,
        command: Sequence[str],
        *,
        cancel_event: Event | None,
        pass_fds: tuple[int, ...] = (),
        timeout: float = 3600.0,
    ) -> subprocess.CompletedProcess[str]:
        self._check_cancel(cancel_event)
        argv = [os.fspath(item) for item in command]
        forbidden = {"sudo", "pkexec", "chattr", "inspect-internal", "dump-tree"}
        if any(item.casefold() in forbidden for item in argv):
            raise CompressionProviderError("A forbidden command was rejected")
        logger.info("Starting compression helper argv=%r", argv)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C",
            "LC_ALL": "C",
        }
        if self._command_runner is not None:
            completed = self._command_runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=timeout,
                env=environment,
                pass_fds=pass_fds,
            )
            self._check_cancel(cancel_event)
            logger.info(
                "Compression helper finished argv=%r exit_code=%d stderr=%r",
                argv,
                int(completed.returncode),
                _short_process_text(completed.stderr),
            )
            return completed

        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=environment,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        with self._children_lock:
            self._children.add(process)
        deadline = self._clock() + max(0.1, timeout)
        try:
            while True:
                if self._cancelled(cancel_event):
                    self._terminate_process(process)
                    raise CompressionCancelled("Compression was cancelled")
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._terminate_process(process)
                    raise CompressionProviderError(
                        f"Command timed out: {argv[0]} {argv[1]}"
                    )
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(0.1, remaining)
                    )
                except subprocess.TimeoutExpired:
                    continue
                completed = subprocess.CompletedProcess(
                    argv, process.returncode, stdout, stderr
                )
                logger.info(
                    "Compression helper finished argv=%r exit_code=%d stderr=%r",
                    argv,
                    int(completed.returncode),
                    _short_process_text(completed.stderr),
                )
                return completed
        finally:
            with self._children_lock:
                self._children.discard(process)
            if process.poll() is None:
                self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, 15)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, 9)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass
            process.communicate()

    def _tool_version(self, executable: str | None) -> str:
        if not executable:
            return ""
        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
                shell=False,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "LANG": "C",
                    "LC_ALL": "C",
                },
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return str(result.stdout or result.stderr or "").strip().splitlines()[0:1][0] if (
            str(result.stdout or result.stderr or "").strip().splitlines()
        ) else ""

    def _read_help(self, command: Sequence[str]) -> str:
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
                shell=False,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "LANG": "C",
                    "LC_ALL": "C",
                },
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return f"{result.stdout or ''}\n{result.stderr or ''}".strip()

    def _is_game_running(self, game: Game) -> bool:
        if self._game_running_checker is not None:
            return bool(self._game_running_checker(game))
        detector = getattr(self._analyzer, "detect_running_processes", None)
        if not callable(detector):
            return False
        game_path = os.path.abspath(os.fspath(game.install_path))
        now = self._clock()
        with self._process_check_lock:
            cached = self._process_checks.get(game.id)
            if (
                cached is not None
                and cached[0] == game_path
                and now - cached[1] < _PROCESS_POLL_INTERVAL_SECONDS
            ):
                return cached[2]
        try:
            running = bool(detector(Path(game_path)))
        except Exception:
            logger.warning(
                "Running-game polling failed for %s; blocking compression",
                game.id,
                exc_info=True,
            )
            running = True
        with self._process_check_lock:
            self._process_checks[game.id] = (game_path, now, running)
        return running

    def _steam_is_updating(self, game: Game) -> bool:
        if bool(
            getattr(game, "update_in_progress", False)
            or getattr(game, "steam_update_in_progress", False)
        ):
            return True
        if self._steam_update_checker is not None:
            try:
                if self._steam_update_checker(game):
                    return True
            except Exception:
                logger.warning(
                    "Steam update checker failed for %s; blocking compression",
                    game.id,
                    exc_info=True,
                )
                return True
        if game.library_path is None or not game.steam_app_id:
            return False
        try:
            manifest_stat, app_state = self._read_verified_steam_manifest(game)
            if (
                game.steam_manifest_mtime_ns is None
                or game.steam_manifest_size_bytes is None
                or manifest_stat.st_mtime_ns != game.steam_manifest_mtime_ns
                or manifest_stat.st_size != game.steam_manifest_size_bytes
            ):
                return True
            state_flags = int(str(app_state.get("stateflags", "0")).strip() or "0")
            if state_flags & _STEAM_ACTIVE_WRITE_STATE_MASK:
                return True
        except (OSError, UnicodeError, ValueError, VDFParseError):
            logger.warning(
                "Steam manifest could not be revalidated for %s; "
                "blocking compression",
                game.id,
                exc_info=True,
            )
            return True
        downloading = (
            Path(game.library_path) / "steamapps" / "downloading" / game.steam_app_id
        )
        try:
            return downloading.exists()
        except OSError:
            return True

    def _has_other_writer(self, game_id: str) -> bool:
        return bool(
            self._write_task_checker is not None
            and self._write_task_checker(game_id)
        )

    def _check_runtime_guards(
        self, game: Game, cancel_event: Event | None
    ) -> None:
        self._check_cancel(cancel_event)
        if self._is_game_running(game):
            raise CompressionProviderError(
                "The game started while compression was waiting"
            )
        if self._steam_is_updating(game):
            raise CompressionProviderError(
                "Steam started writing this game; compression stopped"
            )

    def _check_cancel(self, cancel_event: Event | None) -> None:
        if self._cancelled(cancel_event):
            raise CompressionCancelled("Compression was cancelled")

    def _cancelled(self, cancel_event: Event | None) -> bool:
        return self._cancel_all.is_set() or bool(
            cancel_event is not None and cancel_event.is_set()
        )

    def _verification_measurement(
        self, game: Game, warnings: list[str]
    ) -> CompressionMeasurement | None:
        try:
            report = self._analyzer.analyze(
                game,
                sample_files=False,
                measure_compsize=self._measurement_provider is None,
            )
        except Exception as error:
            warnings.append(f"Final verification failed: {error}")
            return None
        return self._measurement_or_fallback(
            game,
            self.measurement_from_report(report),
            warnings,
        )

    @staticmethod
    def _verify_after(
        expected_logical_bytes: int,
        report: BtrfsAnalysisReport,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if not report.path_exists or not report.path_is_directory:
            errors.append("The game path disappeared during compression")
        if not report.is_btrfs:
            errors.append("The path is no longer verified as Btrfs")
        if not report.scan_complete:
            errors.append("The final directory scan was incomplete")
        if report.logical_bytes != expected_logical_bytes:
            errors.append(
                "Logical size changed during compression; the state requires verification"
            )
        state, _, _, _ = BtrfsCompressionProvider._shared_extent_values(report)
        if state == "unknown":
            errors.append("Shared extents could not be verified after compression")
        return tuple(errors)

    @staticmethod
    def _actual_savings(
        before: CompressionMeasurement,
        after: CompressionMeasurement | None,
    ) -> int | None:
        # Only the two per-game compsize values are comparable here.  Neither
        # st_blocks, btrfs filesystem du nor the noisy whole-filesystem
        # statvfs counter is a substitute for this compression measurement.
        if (
            after is None
            or before.measurement_source != "polkit_helper"
            or after.measurement_source != "polkit_helper"
            or before.compsize_disk_bytes is None
            or after.compsize_disk_bytes is None
        ):
            return None
        return measurement_delta(
            before.compsize_disk_bytes,
            after.compsize_disk_bytes,
        )

    def _cancelled_result(
        self,
        game: Game,
        plan: CompressionPlan,
        started_at: datetime,
        processed_files: int,
        processed_bytes: int,
        exit_codes: Sequence[int],
        warnings: list[str],
        before: CompressionMeasurement,
    ) -> CompressionResult:
        # Cancellation is part of shutdown.  A full directory analysis here
        # could outlive the GUI by the analyzer timeout and cannot produce a
        # trustworthy before/after result for a partial operation anyway.
        warnings.append(
            "Final measurement was skipped because compression was cancelled"
        )
        return CompressionResult(
            plan_id=plan.id,
            game_id=game.id,
            profile=plan.profile,
            status="cancelled",
            started_at=started_at,
            completed_at=datetime.now(UTC),
            processed_files=processed_files,
            processed_bytes=processed_bytes,
            before=before,
            after=None,
            actual_saved_bytes=None,
            verification_state="verification_required",
            full_compression=plan.full_compression,
            after_update=plan.after_update,
            build_id=plan.build_id,
            command_exit_codes=tuple(exit_codes),
            warnings=tuple(dict.fromkeys(warnings)),
            error="Compression was cancelled before all planned files completed",
        )

    @staticmethod
    def _emit(
        callback: CompressionProgressCallback | None,
        **values: Any,
    ) -> None:
        if callback is None:
            return
        try:
            callback(values)
        except Exception:
            logger.debug("Compression progress callback failed", exc_info=True)


class UnavailableCompressionProvider:
    """Explicit provider used when the Btrfs execution backend cannot run."""

    def __init__(self, reason: str = "Btrfs compression is unavailable") -> None:
        self.reason = reason

    def capabilities(self) -> CompressionToolCapabilities:
        return CompressionToolCapabilities(
            btrfs_available=False,
            message=self.reason,
        )

    def create_plan(self, *_args: Any, **_kwargs: Any) -> CompressionPlan:
        raise CompressionPlanRejected(self.reason)

    def execute_plan(self, *_args: Any, **_kwargs: Any) -> CompressionResult:
        raise CompressionPlanRejected(self.reason)

    def cancel_all(self) -> None:
        return None


class FakeCompressionProvider:
    """Deterministic non-writing provider for service/controller tests."""

    def __init__(
        self,
        *,
        plan: CompressionPlan | None = None,
        result: CompressionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.plan = plan
        self.result = result
        self.error = error
        self.executions: list[str] = []
        self.cancelled = False

    def capabilities(self) -> CompressionToolCapabilities:
        return CompressionToolCapabilities(
            btrfs_available=True,
            btrfs_version="fake",
            compsize_available=True,
            compsize_version="fake",
            property_supported=True,
            recompression_supported=True,
            level_supported=True,
            message="Available (fake)",
        )

    def create_plan(self, *_args: Any, **_kwargs: Any) -> CompressionPlan:
        if self.error is not None:
            raise self.error
        if self.plan is None:
            raise CompressionPlanRejected("No fake plan configured")
        return self.plan

    def execute_plan(
        self, _game: Game, plan: CompressionPlan, **_kwargs: Any
    ) -> CompressionResult:
        self.executions.append(plan.id)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise CompressionProviderError("No fake result configured")
        return self.result

    def cancel_all(self) -> None:
        self.cancelled = True


__all__ = [
    "BtrfsCompressionProvider",
    "CompressionCancelled",
    "CompressionPlanRejected",
    "CompressionProviderError",
    "FakeCompressionProvider",
    "UnavailableCompressionProvider",
]
