"""Read-only deep cleaning/compression analysis of one game directory.

Nothing here writes into a game directory or runs a modifying command.  The
only writes are the atomic plan cache in the application data directory and
an explicit user export to a chosen file.

Warnings, notes and blockers are stable codes (``code|arg|arg``) that the UI
translates; the exported JSON keeps them machine readable.
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
from threading import Event, Lock
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
    DeepSizes,
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
GLOBAL_REGISTRY_NAME = "global-steam-leftovers.json"
_MANIFEST_LANGUAGE = re.compile(r'"language"\s+"([^"\n]{1,40})"', re.IGNORECASE)
_SHARED_KEYS = frozenset({"shared", "wwise_shared"})


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


@dataclass(frozen=True, slots=True)
class SizeMeasurement:
    """An existing, trustworthy measurement (compsize and/or btrfs du)."""

    source: str
    physical_bytes: int | None = None
    uncompressed_bytes: int | None = None
    exclusive_bytes: int | None = None
    shared_bytes: int | None = None

    @property
    def savings(self) -> int | None:
        if self.physical_bytes is None or self.uncompressed_bytes is None:
            return None
        return max(0, self.uncompressed_bytes - self.physical_bytes)


def _positive(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def measurement_from_compression_result(raw: Mapping[str, Any] | None) -> SizeMeasurement | None:
    """The authoritative compsize result that the compression header shows."""

    if not raw:
        return None
    disk = _positive(raw.get("compsize_disk_bytes"))
    uncompressed = _positive(raw.get("compsize_uncompressed_bytes"))
    if disk is None or uncompressed is None:
        return None
    shared = raw.get("shared_bytes")
    return SizeMeasurement("compression_result", disk, uncompressed,
                           _positive(raw.get("exclusive_bytes")),
                           shared if isinstance(shared, int) and not isinstance(shared, bool) and shared >= 0 else None)


def measurement_from_analysis_report(raw: Mapping[str, Any] | None) -> SizeMeasurement | None:
    """compsize/btrfs du of the existing compression analysis report."""

    if not raw:
        return None
    compsize = raw.get("compsize") if isinstance(raw.get("compsize"), Mapping) else {}
    du = raw.get("btrfs_du") if isinstance(raw.get("btrfs_du"), Mapping) else {}
    disk = _positive(compsize.get("disk_usage_bytes")) if compsize.get("available") else None
    uncompressed = _positive(compsize.get("uncompressed_bytes")) if compsize.get("available") else None
    exclusive = _positive(du.get("exclusive_bytes")) if du.get("available") else None
    shared = du.get("set_shared_bytes") if du.get("available") and isinstance(du.get("set_shared_bytes"), int) else None
    if disk is None and exclusive is None:
        return None
    return SizeMeasurement("analysis_cache", disk, uncompressed, exclusive, shared)


@dataclass
class _GroupState:
    decision: FileDecision
    scope: str = "game"
    owner: str = ""
    files: int = 0
    bytes: int = 0
    samples: list[str] = field(default_factory=list)
    confidence: float = 1.0
    evidence: dict[tuple[str, str], None] = field(default_factory=dict)
    warnings: dict[str, None] = field(default_factory=dict)

    def add(self, relative: str, size: int, decision: FileDecision) -> None:
        self.files += 1
        self.bytes += size
        self.confidence = min(self.confidence, decision.confidence)
        if len(self.samples) < MAX_SAMPLE_PATHS:
            self.samples.append(relative)
        if decision.evidence[0] and len(self.evidence) < 8:
            self.evidence[decision.evidence] = None
        if decision.warning:
            self.warnings[decision.warning] = None


@dataclass
class _ScanState:
    groups: dict[tuple[str, str, str], _GroupState] = field(default_factory=dict)
    logical_bytes: int = 0
    allocated_bytes: int = 0
    files: int = 0
    symlinks: int = 0
    hardlinked_files: int = 0
    mounts_skipped: list[str] = field(default_factory=list)
    errors: int = 0
    vanished: int = 0
    # Shared by every walk of one analysis: a file (inode) is counted once.
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


def _fallback(languages: set[str], target: str) -> set[str]:
    """Languages that must stay because the target has no own version."""

    if not languages or target in languages:
        return set()
    if "en" in languages:
        return {"en"}
    # The default language cannot be identified: keep every language.
    return set(languages)


def apply_target_language(plan: DeepOptimizationPlan, language: str) -> DeepOptimizationPlan:
    """Assign target/fallback/other/shared roles; only "other" may be Review.

    Audio and text are separate: Polish subtitles never imply Polish voice
    acting.  Without separate target audio the default (English) voice is a
    fallback and is kept.  English text stays as the usual fallback string
    table unless English is the target.
    """

    target = str(language or "").strip().casefold()
    game_groups = [group for group in plan.groups if group.scope == "game"]
    audio = {group.language for group in game_groups if group.category == "audio" and group.language}
    text = {group.language for group in game_groups if group.category == "languages" and group.language}
    audio_fallback = _fallback(audio, target)
    text_fallback = _fallback(text, target) | ({"en"} & text if target != "en" else set())
    notes = [note for note in plan.notes if not note.startswith("no_target_audio")]
    if audio and target not in audio:
        notes.append(f"no_target_audio|{target}|{','.join(sorted(audio_fallback))}")
    groups = []
    for group in plan.groups:
        if group.scope != "game":
            groups.append(group)
            continue
        if group.category == "audio" and group.key in _SHARED_KEYS:
            groups.append(replace(group, role="shared", safety="unsupported", action="keep"))
            continue
        if not (group.language and group.key.startswith("lang:")):
            groups.append(group)
            continue
        fallback = audio_fallback if group.category == "audio" else text_fallback
        if group.language == target:
            groups.append(replace(group, role="target", safety="unsupported", action="keep",
                                  reason="Selected target language; always kept"))
        elif group.language in fallback:
            groups.append(replace(group, role="fallback", safety="unsupported", action="keep",
                                  reason="Fallback/default language for the target; always kept"))
        else:
            groups.append(replace(group, role="other", safety="review", action="remove_candidate",
                                  reason="Language-specific data for a language other than the target"))
    return replace(plan, groups=tuple(groups), target_language=target, notes=tuple(notes))


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
        measurement: SizeMeasurement | None = None,
    ) -> DeepOptimizationPlan:
        """``measurement`` is the same compsize result the compression header uses."""

        root = Path(game.install_path)
        warnings: list[str] = []
        blockers: list[str] = []
        if not root.is_dir() or root.is_symlink():
            return self._failed(game, "game_directory_missing")
        self._report(progress_callback, "engine", 0, 0, "")
        try:
            engine, engine_version = self._engine_detector(root)
        except (OSError, ValueError) as error:
            engine, engine_version = None, ""
            warnings.append(f"engine_detection_failed|{type(error).__name__}")
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
        if self._running(root):
            blockers.append("game_running")
        steam_groups = self._steam_library_groups(game, state, cancel_event, progress_callback)
        filesystem = self._filesystem(game, root, warnings)
        btrfs, sizes = self._btrfs(game, root, filesystem, state, cancel_event, warnings, measurement)
        if state.mounts_skipped:
            warnings.append(f"mount_points_skipped|{len(state.mounts_skipped)}|{', '.join(state.mounts_skipped[:5])}")
        if state.symlinks:
            warnings.append(f"symlinks_skipped|{state.symlinks}")
        complete = not (state.errors or state.vanished)
        if not complete:
            warnings.append(f"entries_skipped|{state.errors}|{state.vanished}")
            blockers.append("scan_incomplete")
        if state.hardlinked_files:
            warnings.append(f"hardlinks_counted_once|{state.hardlinked_files}")
        groups = (*self._groups(state, btrfs, complete=complete), *steam_groups)
        languages: dict[str, int] = {}
        for group in groups:
            if group.language and group.scope == "game":
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
            sizes=sizes,
            file_count=state.files,
            groups=groups,
            languages=languages,
            target_language_source=source,
            warnings=tuple(warnings),
            blockers=tuple(blockers),
            complete=complete,
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
        scope: str = "game",
        owner: str = "",
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
                if metadata and decide is None:
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
                    inode = (entry_stat.st_dev, entry_stat.st_ino)
                    if inode in state.seen_inodes:
                        continue  # hard link already counted (canonical file)
                    if entry_stat.st_nlink > 1:
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
                        key = (decision.category, decision.key, owner)
                        group = state.groups.get(key)
                        if group is None:
                            group = state.groups[key] = _GroupState(decision, scope=scope, owner=owner)
                        group.add(display, size, decision)
                    # Each file is counted in at most one savings view: a
                    # removal candidate is not also counted as compressible.
                    if decide is None and (decision is None or decision.action != "remove_candidate"):
                        kind = compressibility(entry.name)
                        btrfs_key = ("btrfs", kind, "")
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

    # -- Steam library -----------------------------------------------------------

    def _steam_library_groups(self, game, state, cancel_event, progress_callback) -> tuple[DeepGroup, ...]:
        """This AppID's caches (scope=game) and other uninstalled games' leftovers
        (scope=global, one group per canonical library/kind/AppID)."""

        library = getattr(game, "library_path", None)
        app_id = str(getattr(game, "steam_app_id", "") or "")
        if library is None or not app_id.isdecimal():
            return ()
        steamapps = Path(library) / "steamapps"
        if not steamapps.is_dir():
            return ()
        library_id = hashlib.sha256(os.path.realpath(library).encode("utf-8")).hexdigest()[:12]
        installed = {str(value) for value in self._installed_app_ids()} | {app_id}
        try:
            for manifest in steamapps.glob("appmanifest_*.acf"):
                installed.add(manifest.name[len("appmanifest_"):-len(".acf")])
        except OSError:
            pass
        library_state = _ScanState(seen_inodes=state.seen_inodes)

        def decider(kind: str, owner: str) -> Callable[[str, str, os.stat_result], FileDecision | None]:
            if kind == "shadercache" and owner == app_id:
                decision = FileDecision("cache", "steam_shadercache_installed", "review", "remove_candidate", 0.9, True,
                                        "Steam shader cache of this installed game",
                                        evidence=("appid_installed", owner), warning="shader_stutter")
            elif kind == "shadercache":
                decision = FileDecision("cache", "steam_shadercache_uninstalled", "safe", "remove_candidate", 0.9, False,
                                        "Steam shader cache left by a game that is no longer installed",
                                        evidence=("appid_uninstalled", owner))
            elif owner == app_id:
                decision = FileDecision("cache", "compatdata_active", "unsupported", "keep", 1.0, False,
                                        "Active Proton prefix; it can contain saves and settings",
                                        evidence=("appid_installed", owner))
            else:
                decision = FileDecision("cache", "compatdata_uninstalled", "review", "remove_candidate", 0.7, False,
                                        "Proton prefix of an uninstalled game; it may still contain saves",
                                        evidence=("appid_uninstalled", owner), warning="compatdata_saves")
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
                    continue  # another installed game is never part of this plan
                own = owner == app_id
                self._walk(base / owner, f"{STEAM_LIBRARY_PLACEHOLDER}/steamapps/{kind}/{owner}/",
                           DirectoryContext(), library_state, launcher_restores=False,
                           cancel_event=cancel_event, progress_callback=progress_callback,
                           decide=decider(kind, owner), scope="game" if own else "global",
                           owner="" if own else f"{kind}/{owner}@{library_id}")
        return tuple(self._to_group(group, complete=True) for group in library_state.groups.values())

    # -- filesystem / Btrfs ----------------------------------------------------

    def _filesystem(self, game: Game, root: Path, warnings: list[str]):
        inspect = getattr(self._filesystem_provider, "inspect", None)
        if callable(inspect):
            try:
                return inspect(root)
            except Exception as error:  # read-only probe; never fatal
                warnings.append(f"filesystem_detection_failed|{type(error).__name__}")
        return None

    def _btrfs(self, game, root, info, state, cancel_event, warnings, measurement):
        filesystem = info.filesystem if info is not None else game.filesystem
        name = str(getattr(filesystem, "value", filesystem) or "unknown")
        is_btrfs = filesystem == FilesystemType.BTRFS
        if not is_btrfs:
            sizes = DeepSizes(state.logical_bytes, state.allocated_bytes, measurement_source="none")
            return DeepBtrfsInfo(False, name, hardlinked_files=state.hardlinked_files, message="non_btrfs"), sizes
        algorithm, level = BtrfsCompressionAnalyzer._mount_compression(tuple(getattr(info, "mount_options", ()) or ()))
        mount = f"{algorithm}:{level}" if algorithm and level is not None else (algorithm or "")
        try:
            compsize = bool(self._compsize_available())
        except Exception:
            compsize = False
        persistent = ""
        if measurement is None and self._btrfs_report_loader is not None:
            try:
                report = self._btrfs_report_loader(game, cancel_event)
            except Exception as error:
                report = None
                warnings.append(f"btrfs_cache_unreadable|{type(error).__name__}")
            if report is not None:
                persistent = str(report.persistent_compression_algorithm or "")
                measurement = measurement_from_analysis_report(report.to_dict())
        if measurement is None and self._allow_native_btrfs_du and self._btrfs_analyzer is not None \
                and not os.environ.get("FLATPAK_ID"):
            try:
                du = self._btrfs_analyzer._measure_btrfs_du(root, cancel_event, deadline=time.monotonic() + 20.0)
                if du.available:
                    measurement = SizeMeasurement("btrfs_du", None, None, du.exclusive_bytes, du.set_shared_bytes)
            except DeepScanCancelled:
                raise
            except Exception as error:
                warnings.append(f"btrfs_du_unavailable|{type(error).__name__}")
        shared = None
        if measurement is not None and measurement.shared_bytes is not None:
            shared = measurement.shared_bytes > 0
        sizes = DeepSizes(
            logical_size=state.logical_bytes,
            allocated_size=state.allocated_bytes,
            physical_size=measurement.physical_bytes if measurement else None,
            physical_exclusive_size=measurement.exclusive_bytes if measurement else None,
            shared_size=measurement.shared_bytes if measurement else None,
            measured_savings=measurement.savings if measurement else None,
            measurement_source=measurement.source if measurement else "none",
        )
        return DeepBtrfsInfo(True, name, mount_compression=mount, persistent_compression=persistent,
                             compsize_available=compsize, shared_extents=shared,
                             hardlinked_files=state.hardlinked_files,
                             source=measurement.source if measurement else ""), sizes

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
            return manifest, "steam_manifest"
        try:
            system = self._system_language()
        except Exception:
            system = ""
        if system:
            return system, "system_language"
        return ("en", "default") if "en" in found or not found else (sorted(found)[0], "default")

    # -- aggregation ------------------------------------------------------------

    def _groups(self, state: _ScanState, btrfs: DeepBtrfsInfo, *, complete: bool) -> tuple[DeepGroup, ...]:
        groups: list[DeepGroup] = []
        for (category, key, _owner), group_state in sorted(state.groups.items()):
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
            evidence=tuple(DeepEvidence(code, detail) for code, detail in group_state.evidence),
            warnings=tuple(group_state.warnings),
            scope=group_state.scope, owner=group_state.owner,
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
            warnings.append("btrfs_shared" if btrfs.shared_extents else "btrfs_shared_unknown")
        if btrfs.hardlinked_files:
            warnings.append("hardlinks")
        return DeepGroup(
            category="btrfs", key=key, safety=safety, action=action,
            confidence=0.8 if key != "unknown_compressibility" else 0.5,
            reversible=True, reason=reason, file_count=group_state.files,
            bytes=group_state.bytes, sample_paths=tuple(group_state.samples),
            size_exact=complete, potential_savings_bytes=None if key != "precompressed" else 0,
            savings_estimated=True,
            evidence=(DeepEvidence("classification", "extension"),),
            warnings=tuple(warnings),
        )

    def _failed(self, game: Game, reason: str) -> DeepOptimizationPlan:
        return DeepOptimizationPlan(
            game_id=game.id, game_name=game.name,
            launcher=str(getattr(game.launcher, "value", game.launcher)), status="failed",
            engine="Unknown", engine_confidence=0.0, engine_evidence=(),
            filesystem="unknown", btrfs=DeepBtrfsInfo(False, "unknown"),
            sizes=DeepSizes(0, 0), file_count=0, groups=(),
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


def _evidence(raw: Any) -> tuple[DeepEvidence, ...]:
    return tuple(DeepEvidence(str(e["source"]), str(e["detail"]), float(e.get("weight", 0))) for e in raw or ())


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
            evidence=_evidence(item.get("evidence")),
            warnings=tuple(str(value) for value in item.get("warnings", ())),
            scope=str(item.get("scope", "game")), role=str(item.get("role", "")),
            owner=str(item.get("owner", "")),
        )
        for item in raw.get("groups", ())
    )
    btrfs_raw = raw.get("btrfs") or {}
    btrfs = DeepBtrfsInfo(
        bool(btrfs_raw.get("isBtrfs")), str(btrfs_raw.get("filesystem", "unknown")),
        str(btrfs_raw.get("mountCompression", "")), str(btrfs_raw.get("persistentCompression", "")),
        bool(btrfs_raw.get("compsizeAvailable")), btrfs_raw.get("sharedExtents"),
        int(btrfs_raw.get("hardlinkedFiles", 0)), str(btrfs_raw.get("source", "")),
        str(btrfs_raw.get("message", "")),
    )
    sizes_raw = raw.get("sizes") or {}
    sizes = DeepSizes(
        int(sizes_raw.get("logicalSize", 0)), int(sizes_raw.get("allocatedSize", 0)),
        sizes_raw.get("physicalSize"), sizes_raw.get("physicalExclusiveSize"),
        sizes_raw.get("sharedSize"), sizes_raw.get("measuredSavings"),
        str(sizes_raw.get("measurementSource", "none")), dict(sizes_raw.get("states") or {}),
    )
    engine = raw.get("engine") or {}
    return DeepOptimizationPlan(
        game_id=str(raw["gameId"]), game_name=str(raw.get("gameName", "")),
        launcher=str(raw.get("launcher", "")), status=str(raw["status"]),
        engine=str(engine.get("value", "Unknown")), engine_confidence=float(engine.get("confidence", 0)),
        engine_evidence=_evidence(engine.get("evidence")),
        engine_version=str(engine.get("version", "")),
        filesystem=str(raw.get("filesystem", "unknown")), btrfs=btrfs, sizes=sizes,
        file_count=int(raw.get("fileCount", 0)), groups=groups,
        languages={str(k): int(v) for k, v in (raw.get("languages") or {}).items()},
        target_language=str(raw.get("targetLanguage", "")),
        target_language_source=str(raw.get("targetLanguageSource", "")),
        warnings=tuple(raw.get("warnings", ())), blockers=tuple(raw.get("blockers", ())),
        notes=tuple(raw.get("notes", ())),
        complete=bool(raw.get("complete", False)),
        created_at=datetime.fromisoformat(str(raw["createdAt"])) if raw.get("createdAt") else datetime.now(UTC),
    )


class DeepAnalysisRepository:
    """Completed plans, atomically stored only in the application data directory.

    Global Steam leftovers are also merged into one registry keyed by the
    canonical library/kind/AppID, so they are listed once, not per game.
    """

    def __init__(self, root: Path = DEEP_ANALYSIS_DIRECTORY) -> None:
        self.root = Path(root)
        self._lock = Lock()

    def path(self, game_id: str) -> Path:
        digest = hashlib.sha256(str(game_id).encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def save(self, plan: DeepOptimizationPlan) -> Path:
        if plan.status != "completed":
            raise ValueError("only completed deep analyses are stored")
        path = self.path(plan.game_id)
        _atomic_write(path, json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n")
        self._merge_global(plan)
        return path

    def load(self, game_id: str) -> DeepOptimizationPlan | None:
        path = self.path(game_id)
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                return None
            return plan_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def global_leftovers(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads((self.root / GLOBAL_REGISTRY_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = raw.get("entries") if isinstance(raw, Mapping) else None
        if not isinstance(entries, Mapping):
            return []
        return sorted((dict(value) for value in entries.values() if isinstance(value, Mapping)),
                      key=lambda item: -int(item.get("bytes", 0)))

    def _merge_global(self, plan: DeepOptimizationPlan) -> None:
        with self._lock:
            current = {str(item["owner"]): item for item in self.global_leftovers() if item.get("owner")}
            libraries = {group.owner.rsplit("@", 1)[-1] for group in plan.groups if group.scope == "global"}
            current = {owner: item for owner, item in current.items()
                       if owner.rsplit("@", 1)[-1] not in libraries}
            for group in plan.groups:
                if group.scope != "global" or not group.owner:
                    continue
                current[group.owner] = {
                    "owner": group.owner, "key": group.key, "safety": group.safety,
                    "bytes": group.bytes, "fileCount": group.file_count,
                    "appId": group.owner.split("/", 1)[-1].split("@", 1)[0],
                    "analyzedAt": plan.created_at.astimezone(UTC).isoformat(),
                }
            _atomic_write(self.root / GLOBAL_REGISTRY_NAME,
                          json.dumps({"schemaVersion": 1, "entries": current}, indent=2) + "\n")

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
    "SizeMeasurement",
    "apply_target_language",
    "language_from_locale",
    "measurement_from_analysis_report",
    "measurement_from_compression_result",
    "plan_from_dict",
    "steam_manifest_language",
]
