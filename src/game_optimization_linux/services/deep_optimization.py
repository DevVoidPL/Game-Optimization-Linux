"""Read-only deep cleaning/compression analysis of one game directory.

Nothing here writes into a game directory or runs a modifying command.  The
only writes are the atomic plan cache in the application data directory and
an explicit user export to a chosen file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import json
import locale as locale_module
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from threading import Event
import time
from typing import Any

from game_optimization_linux.config import DATA_DIR
from game_optimization_linux.models import FilesystemType, Game
from game_optimization_linux.models.deep_optimization import (
    DEEP_PLAN_SCHEMA_VERSION,
    MAX_SAMPLE_PATHS,
    STEAM_LIBRARY_PLACEHOLDER,
    DeepBtrfsInfo,
    DeepEvidence,
    DeepGroup,
    DeepOptimizationPlan,
)

from .btrfs_analysis import BtrfsCompressionAnalyzer
from .deep_classify import (
    LANGUAGE_NAMES,
    METADATA_MAX_BYTES,
    WWISE_METADATA_NAMES,
    DirectoryContext,
    FileDecision,
    child_context,
    classify_file,
    compressibility,
    parse_wwise_metadata,
    segment_language,
)

logger = logging.getLogger(__name__)

DEEP_ANALYSIS_DIRECTORY = DATA_DIR / "deep-analysis"
NON_BTRFS_MESSAGE = "Deep analysis is available; transparent compression requires Btrfs"
_MANIFEST_LANGUAGE = re.compile(r'"language"\s+"([^"\n]{1,40})"', re.IGNORECASE)


class DeepScanCancelled(RuntimeError):
    """Raised when the user cancels the deep analysis."""


@dataclass(frozen=True, slots=True)
class DeepScanProgress:
    stage: str
    files: int
    bytes: int
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "files": self.files, "bytes": self.bytes, "category": self.category}


@dataclass
class _GroupState:
    decision: FileDecision
    files: int = 0
    bytes: int = 0
    samples: list[str] = field(default_factory=list)
    confidence: float = 1.0
    evidence: dict[str, None] = field(default_factory=dict)
    warnings: dict[str, None] = field(default_factory=dict)

    def add(self, relative: str, size: int, decision: FileDecision) -> None:
        self.files += 1
        self.bytes += size
        self.confidence = min(self.confidence, decision.confidence)
        if len(self.samples) < MAX_SAMPLE_PATHS:
            self.samples.append(relative)
        if decision.evidence and len(self.evidence) < 8:
            self.evidence[decision.evidence] = None
        if decision.warning:
            self.warnings[decision.warning] = None


@dataclass
class _ScanState:
    groups: dict[tuple[str, str], _GroupState] = field(default_factory=dict)
    logical_bytes: int = 0
    allocated_bytes: int = 0
    files: int = 0
    symlinks: int = 0
    hardlinked_files: int = 0
    mounts_skipped: list[str] = field(default_factory=list)
    errors: int = 0
    vanished: int = 0
    seen_inodes: set[tuple[int, int]] = field(default_factory=set)


def language_from_locale(value: str) -> str:
    """Base language code from ``pl_PL.UTF-8``, ``polish`` or ``pt-BR``."""

    text = str(value or "").strip().split(".", 1)[0].split("@", 1)[0]
    if not text or text.upper() in {"C", "POSIX"}:
        return ""
    found = segment_language(text) or segment_language(text.split("_", 1)[0].split("-", 1)[0])
    return found[0] if found else ""


def steam_manifest_language(library: Path | None, app_id: str | None) -> str:
    """Selected game language from the local Steam app manifest, if stated."""

    if library is None or not app_id:
        return ""
    path = Path(library) / "steamapps" / f"appmanifest_{app_id}.acf"
    try:
        if path.stat().st_size > 1024 * 1024:
            return ""
        match = _MANIFEST_LANGUAGE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return LANGUAGE_NAMES.get(match.group(1).strip().casefold(), "") if match else ""


def apply_target_language(plan: DeepOptimizationPlan, language: str) -> DeepOptimizationPlan:
    """Re-evaluate language groups for a target; the selected one is kept."""

    target = str(language or "").strip().casefold()
    groups = []
    for group in plan.groups:
        if group.language and group.key.startswith("lang:"):
            if group.language == target:
                group = replace(group, safety="unsupported", action="keep",
                                reason="Selected target language; always kept")
            else:
                reason = group.reason.replace("Selected target language; always kept", "")
                group = replace(
                    group, safety="review", action="remove_candidate",
                    reason=reason or "Language-specific data for a language other than the target",
                    warnings=tuple(dict.fromkeys((
                        *group.warnings,
                        *(("English is often a fallback language; keep it unless you are sure",)
                          if group.language == "en" else ()),
                    ))),
                )
        groups.append(group)
    return replace(plan, groups=tuple(groups), target_language=target)


class DeepOptimizationAnalyzer:
    """Build a :class:`DeepOptimizationPlan` without changing any file."""

    def __init__(
        self,
        *,
        engine_detector: Callable[[Path], tuple[Any, str]],
        filesystem_provider: object | None = None,
        btrfs_analyzer: BtrfsCompressionAnalyzer | None = None,
        btrfs_report_loader: Callable[[Game, Event | None], Any] | None = None,
        compsize_available: Callable[[], bool] | None = None,
        running_detector: Callable[[Path], Sequence[int]] | None = None,
        installed_app_ids: Callable[[], Iterable[str]] | None = None,
        system_language: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
        progress_interval: float = 0.2,
        allow_native_btrfs_du: bool = True,
    ) -> None:
        self._engine_detector = engine_detector
        self._filesystem_provider = filesystem_provider
        self._btrfs_analyzer = btrfs_analyzer
        self._btrfs_report_loader = btrfs_report_loader
        self._compsize_available = compsize_available or (lambda: bool(shutil.which("compsize")))
        self._running_detector = running_detector
        self._installed_app_ids = installed_app_ids or (lambda: ())
        self._system_language = system_language or (
            lambda: language_from_locale(
                os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                or os.environ.get("LANG") or (locale_module.getlocale()[0] or "")
            )
        )
        self._clock = clock
        self._progress_interval = progress_interval
        self._allow_native_btrfs_du = allow_native_btrfs_du

    # -- public -------------------------------------------------------------

    def analyze(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
        progress_callback: Callable[[DeepScanProgress], None] | None = None,
    ) -> DeepOptimizationPlan:
        root = Path(game.install_path)
        warnings: list[str] = []
        blockers: list[str] = []
        if not root.is_dir() or root.is_symlink():
            return self._failed(game, "The game directory is missing or is not a real directory")
        self._report(progress_callback, "engine", 0, 0, "")
        try:
            engine, engine_version = self._engine_detector(root)
        except (OSError, ValueError) as error:
            engine, engine_version = None, ""
            warnings.append(f"Engine detection failed: {type(error).__name__}")
        engine_name = str(getattr(engine, "value", "Unknown") or "Unknown")
        engine_confidence = float(getattr(engine, "confidence", 0.0) or 0.0)
        engine_evidence = tuple(
            DeepEvidence(str(item.source), str(item.detail), float(item.weight))
            for item in getattr(engine, "evidence", ()) or ()
        )
        self._check(cancel_event)
        launcher = str(getattr(game.launcher, "value", game.launcher))
        launcher_restores = launcher in {"Steam", "Heroic"}
        state = _ScanState()
        base_context = DirectoryContext(engine=engine_name, engine_confidence=engine_confidence)
        self._walk(root, "", base_context, state, launcher_restores=launcher_restores,
                   cancel_event=cancel_event, progress_callback=progress_callback)
        running = self._running(root)
        if running:
            blockers.append("The game is running; files may change until it is closed")
        steam_groups = self._steam_library_groups(game, state, cancel_event, progress_callback)
        filesystem = self._filesystem(game, root, warnings)
        btrfs = self._btrfs(game, root, filesystem, state, cancel_event, warnings)
        if state.mounts_skipped:
            warnings.append(
                f"{len(state.mounts_skipped)} mount point(s) inside the game were not scanned: "
                + ", ".join(state.mounts_skipped[:5])
            )
        if state.symlinks:
            warnings.append(f"{state.symlinks} symbolic link(s) were not followed")
        if state.errors or state.vanished:
            warnings.append(
                f"{state.errors} unreadable entr(y/ies) and {state.vanished} file(s) that disappeared were skipped"
            )
            blockers.append("The scan was not complete; a later stage must rescan before acting")
        if state.hardlinked_files:
            warnings.append(f"{state.hardlinked_files} hard-linked file(s) were counted once")
        groups = self._groups(state, btrfs, complete=not (state.errors or state.vanished))
        groups = (*groups, *steam_groups)
        languages: dict[str, int] = {}
        for group in groups:
            if group.language:
                languages[group.language] = languages.get(group.language, 0) + group.bytes
        target, source = self._target_language(game, set(languages))
        plan = DeepOptimizationPlan(
            game_id=game.id,
            game_name=game.name,
            launcher=launcher,
            status="completed",
            engine=engine_name,
            engine_confidence=max(0.0, min(1.0, engine_confidence)),
            engine_evidence=engine_evidence,
            engine_version=engine_version,
            filesystem=btrfs.filesystem,
            btrfs=btrfs,
            logical_bytes=state.logical_bytes,
            allocated_bytes=state.allocated_bytes,
            file_count=state.files,
            groups=groups,
            languages=languages,
            target_language_source=source,
            warnings=tuple(warnings),
            blockers=tuple(blockers),
            complete=not (state.errors or state.vanished),
        )
        return apply_target_language(plan, target)

    # -- scanning -------------------------------------------------------------

    @staticmethod
    def _check(cancel_event: Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DeepScanCancelled("Deep analysis was cancelled")

    def _report(self, callback, stage: str, files: int, size: int, category: str) -> None:
        if callback is not None:
            callback(DeepScanProgress(stage, files, size, category))

    def _walk(
        self,
        root: Path,
        label: str,
        base_context: DirectoryContext,
        state: _ScanState,
        *,
        launcher_restores: bool,
        cancel_event: Event | None,
        progress_callback,
        decide: Callable[[str, str, os.stat_result], FileDecision | None] | None = None,
    ) -> None:
        """Walk one tree through verified directory descriptors.

        Symlinks are never followed, other filesystems (mount points) are not
        entered, and files are only ``stat``-ed (never opened) except for
        bounded engine metadata such as Wwise SoundbanksInfo.
        """

        try:
            root_stat = os.lstat(root)
        except OSError:
            state.errors += 1
            return
        if not stat.S_ISDIR(root_stat.st_mode):
            return
        root_real = os.path.realpath(root)
        device = root_stat.st_dev
        seen_directories = {(root_stat.st_dev, root_stat.st_ino)}
        pending: list[tuple[Path, str, tuple[int, int], DirectoryContext]] = [
            (root, "", (root_stat.st_dev, root_stat.st_ino), base_context)
        ]
        now = self._clock()
        last_report = 0.0
        while pending:
            self._check(cancel_event)
            current, relative, identity, context = pending.pop()
            descriptor: int | None = None
            try:
                descriptor = BtrfsCompressionAnalyzer._open_verified_directory(
                    current, expected_identity=identity, root_real=root_real
                )
                with os.scandir(descriptor) as iterator:
                    entries = list(iterator)
                names = frozenset(entry.name.casefold() for entry in entries)
                directories: list[str] = []
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(entry.name)
                    except OSError:
                        continue
                sibling_directories = tuple(directories)
                metadata = next((entry.name for entry in entries if entry.name.casefold() in WWISE_METADATA_NAMES), "")
                if metadata:
                    index = self._read_wwise(descriptor, metadata, relative)
                    if index is not None:
                        context = replace(context, wwise=index)
                for entry in entries:
                    self._check(cancel_event)
                    child_relative = f"{relative}/{entry.name}" if relative else entry.name
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        state.vanished += 1
                        continue
                    except OSError:
                        state.errors += 1
                        continue
                    mode = entry_stat.st_mode
                    if stat.S_ISLNK(mode):
                        state.symlinks += 1
                        continue
                    if stat.S_ISDIR(mode):
                        if entry_stat.st_dev != device:
                            state.mounts_skipped.append(f"{label}{child_relative}")
                            continue
                        child_identity = (entry_stat.st_dev, entry_stat.st_ino)
                        if child_identity in seen_directories:
                            continue
                        seen_directories.add(child_identity)
                        pending.append((
                            current / entry.name, child_relative, child_identity,
                            child_context(context, entry.name, sibling_directories),
                        ))
                        continue
                    if not stat.S_ISREG(mode):
                        continue
                    if entry_stat.st_nlink > 1:
                        inode = (entry_stat.st_dev, entry_stat.st_ino)
                        if inode in state.seen_inodes:
                            continue
                        state.seen_inodes.add(inode)
                        state.hardlinked_files += 1
                    size = max(0, entry_stat.st_size)
                    state.files += 1
                    state.logical_bytes += size
                    state.allocated_bytes += max(0, getattr(entry_stat, "st_blocks", 0)) * 512
                    display = f"{label}{child_relative}"
                    decision = (
                        decide(display, entry.name, entry_stat) if decide is not None
                        else classify_file(
                            context, child_relative, entry.name, size=size,
                            age_seconds=max(0.0, now - entry_stat.st_mtime),
                            sibling_names=names, launcher_restores=launcher_restores,
                        )
                    )
                    if decision is not None:
                        key = (decision.category, decision.key)
                        group = state.groups.get(key)
                        if group is None:
                            group = state.groups[key] = _GroupState(decision)
                        group.add(display, size, decision)
                    if decide is None:
                        kind = compressibility(entry.name)
                        btrfs_key = ("btrfs", kind)
                        btrfs_group = state.groups.get(btrfs_key)
                        placeholder = FileDecision("btrfs", kind, "review", "info", 0.8, True, "")
                        if btrfs_group is None:
                            btrfs_group = state.groups[btrfs_key] = _GroupState(placeholder)
                        btrfs_group.add(display, size, placeholder)
                    moment = time.monotonic()
                    if moment - last_report >= self._progress_interval:
                        last_report = moment
                        self._report(progress_callback, "scan", state.files, state.logical_bytes,
                                     decision.category if decision else "")
            except DeepScanCancelled:
                raise
            except FileNotFoundError:
                state.vanished += 1
            except OSError:
                state.errors += 1
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        self._report(progress_callback, "scan", state.files, state.logical_bytes, "")

    @staticmethod
    def _read_wwise(descriptor: int, name: str, relative: str):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            handle = os.open(name, flags, dir_fd=descriptor)
        except OSError:
            return None
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_size > METADATA_MAX_BYTES:
                return None
            chunks: list[bytes] = []
            remaining = info.st_size
            while remaining > 0:
                chunk = os.read(handle, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError:
            return None
        finally:
            os.close(handle)
        return parse_wwise_metadata(b"".join(chunks), name, relative)

    # -- Steam library residues ------------------------------------------------

    def _steam_library_groups(self, game, state, cancel_event, progress_callback) -> tuple[DeepGroup, ...]:
        library = getattr(game, "library_path", None)
        app_id = str(getattr(game, "steam_app_id", "") or "")
        if library is None or not app_id.isdecimal():
            return ()
        steamapps = Path(library) / "steamapps"
        if not steamapps.is_dir():
            return ()
        installed = {str(value) for value in self._installed_app_ids()} | {app_id}
        try:
            for manifest in steamapps.glob("appmanifest_*.acf"):
                installed.add(manifest.name[len("appmanifest_"):-len(".acf")])
        except OSError:
            pass
        library_state = _ScanState()

        def decider(kind: str, owner: str) -> Callable[[str, str, os.stat_result], FileDecision]:
            active = owner in installed
            if kind == "shadercache" and owner == app_id:
                decision = FileDecision("cache", "steam_shadercache_installed", "review", "remove_candidate", 0.9, True,
                                        "Steam shader cache of this installed game", evidence=f"AppID {owner} is installed",
                                        warning="Deleting it causes shader-compilation stutter until it is rebuilt")
            elif kind == "shadercache" and not active:
                decision = FileDecision("cache", "steam_shadercache_uninstalled", "safe", "remove_candidate", 0.9, False,
                                        "Steam shader cache left by a game that is no longer installed",
                                        evidence=f"AppID {owner}: no app manifest in this library and not in the game list")
            elif kind == "compatdata" and owner == app_id:
                decision = FileDecision("cache", "compatdata_active", "unsupported", "keep", 1.0, False,
                                        "Active Proton prefix; it can contain saves and settings",
                                        evidence=f"AppID {owner} is installed")
            elif kind == "compatdata" and not active:
                decision = FileDecision("cache", "compatdata_uninstalled", "review", "remove_candidate", 0.7, False,
                                        "Proton prefix of an uninstalled game; it may still contain saves",
                                        evidence=f"AppID {owner}: no app manifest in this library",
                                        warning="Check for save games before removing a Proton prefix")
            else:
                decision = None  # another installed game: not part of this plan
            return lambda _display, _name, _stat: decision

        for kind in ("shadercache", "compatdata"):
            base = steamapps / kind
            try:
                owners = sorted(entry.name for entry in os.scandir(base)
                                if entry.is_dir(follow_symlinks=False) and entry.name.isdecimal())
            except OSError:
                continue
            for owner in owners:
                if owner != app_id and owner in installed:
                    continue
                self._walk(base / owner, f"{STEAM_LIBRARY_PLACEHOLDER}/steamapps/{kind}/{owner}/",
                           DirectoryContext(), library_state, launcher_restores=False,
                           cancel_event=cancel_event, progress_callback=progress_callback,
                           decide=decider(kind, owner))
        return tuple(self._to_group(group, complete=True) for group in library_state.groups.values())

    # -- filesystem / Btrfs ----------------------------------------------------

    def _filesystem(self, game: Game, root: Path, warnings: list[str]):
        inspect = getattr(self._filesystem_provider, "inspect", None)
        if callable(inspect):
            try:
                return inspect(root)
            except Exception as error:  # read-only probe; never fatal
                warnings.append(f"Filesystem detection failed: {type(error).__name__}")
        return None

    def _btrfs(self, game, root, info, state, cancel_event, warnings) -> DeepBtrfsInfo:
        filesystem = info.filesystem if info is not None else game.filesystem
        name = str(getattr(filesystem, "value", filesystem) or "unknown")
        is_btrfs = filesystem == FilesystemType.BTRFS
        if not is_btrfs:
            return DeepBtrfsInfo(False, name, hardlinked_files=state.hardlinked_files, message=NON_BTRFS_MESSAGE)
        algorithm, level = BtrfsCompressionAnalyzer._mount_compression(tuple(getattr(info, "mount_options", ()) or ()))
        mount = f"{algorithm}:{level}" if algorithm and level is not None else (algorithm or "")
        report = None
        if self._btrfs_report_loader is not None:
            try:
                report = self._btrfs_report_loader(game, cancel_event)
            except Exception as error:
                warnings.append(f"Cached Btrfs analysis could not be read: {type(error).__name__}")
        try:
            compsize = bool(self._compsize_available())
        except Exception:
            compsize = False
        if report is not None:
            du = report.btrfs_du
            return DeepBtrfsInfo(
                True, name, mount_compression=mount,
                persistent_compression=str(report.persistent_compression_algorithm or ""),
                compsize_available=compsize,
                exact_physical_bytes=report.compsize.disk_usage_bytes if report.compsize.available else None,
                shared_extents=du.shared_extents if du.available else report.possible_shared_extents,
                shared_bytes=du.set_shared_bytes if du.available else None,
                hardlinked_files=state.hardlinked_files,
                source="existing compression analysis",
                message="Physical size and shared extents come from the existing compression analysis",
            )
        shared, shared_bytes, source = None, None, ""
        if self._allow_native_btrfs_du and self._btrfs_analyzer is not None and not os.environ.get("FLATPAK_ID"):
            try:
                du = self._btrfs_analyzer._measure_btrfs_du(root, cancel_event, deadline=time.monotonic() + 20.0)
                if du.available:
                    shared, shared_bytes, source = du.shared_extents, du.set_shared_bytes, "btrfs filesystem du"
            except DeepScanCancelled:
                raise
            except Exception as error:
                warnings.append(f"btrfs filesystem du was not available: {type(error).__name__}")
        return DeepBtrfsInfo(
            True, name, mount_compression=mount, compsize_available=compsize,
            shared_extents=shared, shared_bytes=shared_bytes,
            hardlinked_files=state.hardlinked_files, source=source,
            message=(
                "Shared extents were measured with btrfs filesystem du" if source
                else "Run the existing compression analysis to measure physical size and shared extents"
            ),
        )

    def _running(self, root: Path) -> tuple[int, ...]:
        detector = self._running_detector
        if detector is None and self._btrfs_analyzer is not None:
            detector = self._btrfs_analyzer.detect_running_processes
        if detector is None:
            return ()
        try:
            return tuple(detector(root))
        except Exception:
            return ()

    def _target_language(self, game: Game, found: set[str]) -> tuple[str, str]:
        manifest = steam_manifest_language(getattr(game, "library_path", None), game.steam_app_id)
        if manifest:
            return manifest, "Steam app manifest"
        try:
            system = self._system_language()
        except Exception:
            system = ""
        if system:
            return system, "system language"
        return ("en", "default") if "en" in found or not found else (sorted(found)[0], "default")

    # -- aggregation ------------------------------------------------------------

    def _groups(self, state: _ScanState, btrfs: DeepBtrfsInfo, *, complete: bool) -> tuple[DeepGroup, ...]:
        groups: list[DeepGroup] = []
        for (category, key), group_state in sorted(state.groups.items()):
            if category == "btrfs":
                groups.append(self._btrfs_group(key, group_state, btrfs, complete))
            else:
                groups.append(self._to_group(group_state, complete=complete))
        return tuple(groups)

    @staticmethod
    def _to_group(group_state: _GroupState, *, complete: bool) -> DeepGroup:
        decision = group_state.decision
        removable = decision.action == "remove_candidate"
        return DeepGroup(
            category=decision.category, key=decision.key, safety=decision.safety,
            action=decision.action, confidence=round(max(0.0, min(1.0, group_state.confidence)), 3),
            reversible=decision.reversible, reason=decision.reason,
            file_count=group_state.files, bytes=group_state.bytes,
            sample_paths=tuple(group_state.samples), language=decision.language,
            size_exact=complete,
            potential_savings_bytes=group_state.bytes if removable else 0,
            savings_estimated=not complete,
            evidence=tuple(DeepEvidence("file", item) for item in group_state.evidence),
            warnings=tuple(group_state.warnings),
        )

    @staticmethod
    def _btrfs_group(key: str, group_state: _GroupState, btrfs: DeepBtrfsInfo, complete: bool) -> DeepGroup:
        warnings: list[str] = []
        if not btrfs.is_btrfs:
            safety, action, reason = "unsupported", "info", NON_BTRFS_MESSAGE
        elif key == "precompressed":
            safety, action = "unsupported", "info"
            reason = "Already compressed format (archives, media, compressed textures); no Btrfs savings are promised"
        elif key == "compressible":
            safety, action = ("safe" if btrfs.shared_extents is False else "review"), "compress_candidate"
            reason = "Text, executables, libraries and uncompressed audio usually compress well"
        else:
            safety, action = "review", "compress_candidate"
            reason = "Unknown binary format; measure a sample before compressing"
        if btrfs.is_btrfs and btrfs.shared_extents is not False and key != "precompressed":
            warnings.append(
                "Shared extents/reflinks were detected or could not be ruled out; a future executor must skip shared files, "
                "because defragmentation can break sharing and increase disk usage"
                if btrfs.shared_extents else
                "Shared extents were not measured; a future executor must check each file and skip shared ones"
            )
        if btrfs.hardlinked_files:
            warnings.append("Hard-linked files exist; rewriting them can break the link")
        return DeepGroup(
            category="btrfs", key=key, safety=safety, action=action,
            confidence=0.8 if key != "unknown_compressibility" else 0.5,
            reversible=True, reason=reason, file_count=group_state.files,
            bytes=group_state.bytes, sample_paths=tuple(group_state.samples),
            size_exact=complete, potential_savings_bytes=None if key != "precompressed" else 0,
            savings_estimated=True,
            evidence=(DeepEvidence("classification", "file extension"),),
            warnings=tuple(warnings),
        )

    def _failed(self, game: Game, reason: str) -> DeepOptimizationPlan:
        return DeepOptimizationPlan(
            game_id=game.id, game_name=game.name,
            launcher=str(getattr(game.launcher, "value", game.launcher)), status="failed",
            engine="Unknown", engine_confidence=0.0, engine_evidence=(),
            filesystem="unknown", btrfs=DeepBtrfsInfo(False, "unknown"),
            logical_bytes=0, allocated_bytes=0, file_count=0, groups=(),
            blockers=(reason,), complete=False,
        )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def plan_from_dict(raw: Mapping[str, Any]) -> DeepOptimizationPlan:
    if raw.get("schemaVersion") != DEEP_PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported deep analysis schema")
    groups = tuple(
        DeepGroup(
            category=str(item["category"]), key=str(item["key"]), safety=str(item["safety"]),
            action=str(item["action"]), confidence=float(item["confidence"]),
            reversible=bool(item["reversible"]), reason=str(item["reason"]),
            file_count=int(item["fileCount"]), bytes=int(item["bytes"]),
            sample_paths=tuple(str(path) for path in item.get("samplePaths", ())),
            language=str(item.get("language", "")), size_exact=bool(item.get("sizeExact", True)),
            potential_savings_bytes=item.get("potentialSavingsBytes"),
            savings_estimated=bool(item.get("savingsEstimated", False)),
            evidence=tuple(DeepEvidence(str(e["source"]), str(e["detail"]), float(e.get("weight", 0)))
                           for e in item.get("evidence", ())),
            warnings=tuple(str(value) for value in item.get("warnings", ())),
        )
        for item in raw.get("groups", ())
    )
    btrfs_raw = raw.get("btrfs") or {}
    btrfs = DeepBtrfsInfo(
        bool(btrfs_raw.get("isBtrfs")), str(btrfs_raw.get("filesystem", "unknown")),
        str(btrfs_raw.get("mountCompression", "")), str(btrfs_raw.get("persistentCompression", "")),
        bool(btrfs_raw.get("compsizeAvailable")), btrfs_raw.get("exactPhysicalBytes"),
        btrfs_raw.get("sharedExtents"), btrfs_raw.get("sharedBytes"),
        int(btrfs_raw.get("hardlinkedFiles", 0)), str(btrfs_raw.get("source", "")),
        str(btrfs_raw.get("message", "")),
    )
    engine = raw.get("engine") or {}
    return DeepOptimizationPlan(
        game_id=str(raw["gameId"]), game_name=str(raw.get("gameName", "")),
        launcher=str(raw.get("launcher", "")), status=str(raw["status"]),
        engine=str(engine.get("value", "Unknown")), engine_confidence=float(engine.get("confidence", 0)),
        engine_evidence=tuple(DeepEvidence(str(e["source"]), str(e["detail"]), float(e.get("weight", 0)))
                              for e in engine.get("evidence", ())),
        engine_version=str(engine.get("version", "")),
        filesystem=str(raw.get("filesystem", "unknown")), btrfs=btrfs,
        logical_bytes=int(raw.get("logicalBytes", 0)), allocated_bytes=int(raw.get("allocatedBytes", 0)),
        file_count=int(raw.get("fileCount", 0)), groups=groups,
        languages={str(k): int(v) for k, v in (raw.get("languages") or {}).items()},
        target_language=str(raw.get("targetLanguage", "")),
        target_language_source=str(raw.get("targetLanguageSource", "")),
        warnings=tuple(raw.get("warnings", ())), blockers=tuple(raw.get("blockers", ())),
        complete=bool(raw.get("complete", False)),
        created_at=datetime.fromisoformat(str(raw["createdAt"])) if raw.get("createdAt") else datetime.now(UTC),
    )


class DeepAnalysisRepository:
    """Completed plans, atomically stored only in the application data directory."""

    def __init__(self, root: Path = DEEP_ANALYSIS_DIRECTORY) -> None:
        self.root = Path(root)

    def path(self, game_id: str) -> Path:
        digest = hashlib.sha256(str(game_id).encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def save(self, plan: DeepOptimizationPlan) -> Path:
        if plan.status != "completed":
            raise ValueError("only completed deep analyses are stored")
        path = self.path(plan.game_id)
        _atomic_write(path, json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return path

    def load(self, game_id: str) -> DeepOptimizationPlan | None:
        path = self.path(game_id)
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                return None
            return plan_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def export(plan: DeepOptimizationPlan, destination: Path, *, game_root: Path | None = None) -> Path:
        target = Path(destination).expanduser()
        if game_root is not None:
            try:
                inside = os.path.commonpath((os.path.realpath(target), os.path.realpath(game_root))) == os.path.realpath(game_root)
            except ValueError:
                inside = False
            if inside:
                raise ValueError("The report cannot be written into the game directory")
        text = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n"
        home = str(Path.home()).rstrip("/")
        if home and home != "/" and home in text:
            raise ValueError("The report would contain a private path")
        _atomic_write(target, text)
        return target


__all__ = [
    "DEEP_ANALYSIS_DIRECTORY",
    "DeepAnalysisRepository",
    "DeepOptimizationAnalyzer",
    "DeepScanCancelled",
    "DeepScanProgress",
    "NON_BTRFS_MESSAGE",
    "apply_target_language",
    "language_from_locale",
    "plan_from_dict",
    "steam_manifest_language",
]
