"""Safe local-archive OptiScaler installation for Proton games."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from threading import Event
from typing import Any, BinaryIO
from uuid import uuid4

from gameforge.config import GAMES_CONFIG_DIR, OPTISCALER_DATA_DIR
from gameforge.models import (
    Game,
    OptiScalerProfile,
    OPTISCALER_PROXY_DLLS,
)

from .btrfs_analysis import BtrfsCompressionAnalyzer
from .archive_reader import ArchiveEntry, ArchiveReadError, open_archive
from .game_executable import ExecutableCandidate, GameExecutableResolver
from .mangohud import _atomic_write


PROFILE_FILE_NAME = "optiscaler.json"
MANIFEST_FORMAT_VERSION = 1
ANTI_CHEAT_MARKERS = (
    "easyanticheat",
    "easy anti-cheat",
    "eac_launcher",
    "battleye",
    "beclient",
    "beservice",
    "equ8",
    "faceit",
    "ricochet",
    "vgk.sys",
)
LOADER_MARKERS = (
    "nvapi64.dll",
    "nvngx.dll",
    "optiscaler.dll",
    "optiscaler.log",
    "reshade.ini",
    "reshadepreset.ini",
    "reshade-shaders",
    "vkbasalt.dll",
    "vkbasalt.conf",
)


class OptiScalerError(RuntimeError):
    pass


class OptiScalerCancelled(OptiScalerError):
    pass


class OptiScalerConflictError(OptiScalerError):
    pass


@dataclass(frozen=True, slots=True)
class OptiScalerConflict:
    relative_path: str
    sha256: str
    kind: str
    managed_by_gameforge: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "relativePath": self.relative_path,
            "sha256": self.sha256,
            "kind": self.kind,
            "managedByGameForge": self.managed_by_gameforge,
        }


@dataclass(frozen=True, slots=True)
class OptiScalerFilePlan:
    archive_member: str
    target_relative_path: str
    size: int
    sha256: str
    replaces_existing: bool = False
    existing_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "archiveMember": self.archive_member,
            "targetRelativePath": self.target_relative_path,
            "size": self.size,
            "sha256": self.sha256,
            "replacesExisting": self.replaces_existing,
            "existingSha256": self.existing_sha256,
        }


@dataclass(frozen=True, slots=True)
class OptiScalerInstallPlan:
    app_id: str
    archive_path: str
    archive_format: str
    version: str
    executable: str
    install_directory: str
    executable_confidence: str
    injection_dll: str
    proton_override: str
    backup_directory: str
    files: tuple[OptiScalerFilePlan, ...]
    conflicts: tuple[OptiScalerConflict, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def can_install(self) -> bool:
        return not self.blockers and bool(self.files)

    def to_dict(self) -> dict[str, Any]:
        executable_name = Path(self.executable).name
        return {
            "success": True,
            "appId": self.app_id,
            "archivePath": self.archive_path,
            "archiveFormat": self.archive_format,
            "version": self.version,
            "executable": self.executable,
            "executableName": executable_name,
            "installDirectory": self.install_directory,
            "executableConfidence": self.executable_confidence,
            "injectionDll": self.injection_dll,
            "protonOverride": self.proton_override,
            "backupDirectory": self.backup_directory,
            "installationSummary": (
                f"OptiScaler.dll will be installed as {self.injection_dll} "
                f"next to {executable_name}"
            ),
            "files": [item.to_dict() for item in self.files],
            "filesToAdd": [
                item.to_dict() for item in self.files if not item.replaces_existing
            ],
            "filesToReplace": [
                item.to_dict() for item in self.files if item.replaces_existing
            ],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "canInstall": self.can_install,
            "requiresConflictConfirmation": bool(self.conflicts),
        }


class OptiScalerProfileRepository:
    def __init__(self, root: Path = GAMES_CONFIG_DIR) -> None:
        self.root = Path(root)

    def path(self, app_id: object) -> Path:
        return self.root / str(app_id) / PROFILE_FILE_NAME

    def load(self, app_id: object) -> OptiScalerProfile:
        default = OptiScalerProfile.default(app_id)
        path = self.path(default.app_id)
        if not path.exists():
            return default
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OptiScalerError(f"could not read OptiScaler profile: {error}") from error
        if not isinstance(raw, Mapping):
            raise OptiScalerError("OptiScaler profile must be a JSON object")
        return OptiScalerProfile.from_dict(raw, expected_app_id=default.app_id)

    def save(self, profile: OptiScalerProfile) -> Path:
        path = self.path(profile.app_id)
        _atomic_write(
            path,
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        return path


def merge_wine_dll_overrides(existing: str, addition: str) -> str:
    """Merge one ``name=modes`` override without dropping user entries."""

    entries: list[tuple[str, list[str]]] = []
    index_by_name: dict[str, int] = {}

    def include(raw: str) -> None:
        text = str(raw or "").strip()
        if not text or "=" not in text:
            return
        name, raw_modes = text.split("=", 1)
        normalized_name = name.strip().casefold()
        if not normalized_name:
            return
        modes = [
            item.strip().casefold()
            for item in raw_modes.split(",")
            if item.strip()
        ]
        if normalized_name in index_by_name:
            target_modes = entries[index_by_name[normalized_name]][1]
            for mode in modes:
                if mode not in target_modes:
                    target_modes.append(mode)
            return
        index_by_name[normalized_name] = len(entries)
        entries.append((name.strip(), list(dict.fromkeys(modes))))

    for token in str(existing or "").split(";"):
        include(token)
    include(addition)
    return ";".join(
        f"{name}={','.join(modes)}" for name, modes in entries if modes
    )


class OptiScalerService:
    def __init__(
        self,
        *,
        profile_repository: OptiScalerProfileRepository | None = None,
        data_root: Path = OPTISCALER_DATA_DIR,
        executable_resolver: GameExecutableResolver | None = None,
        process_detector: Callable[[Path], Sequence[int]] | None = None,
    ) -> None:
        self.profile_repository = profile_repository or OptiScalerProfileRepository()
        self.data_root = Path(data_root)
        self.executable_resolver = executable_resolver or GameExecutableResolver()
        if process_detector is None:
            analyzer = BtrfsCompressionAnalyzer()
            process_detector = analyzer.detect_running_processes
        self._process_detector = process_detector

    @staticmethod
    def _check_cancel(cancel_event: Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OptiScalerCancelled("OptiScaler operation was cancelled")

    @staticmethod
    def _hash_stream(handle: BinaryIO) -> str:
        digest = sha256()
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _hash_file(path: Path) -> str:
        with path.open("rb") as handle:
            return OptiScalerService._hash_stream(handle)

    def _archive_payload(
        self, archive_path: Path, injection_dll: str
    ) -> tuple[str, str, tuple[tuple[ArchiveEntry, str, str], ...]]:
        try:
            reader = open_archive(archive_path)
            with tempfile.TemporaryDirectory(
                prefix="gameforge-optiscaler-plan-"
            ) as temporary_directory:
                extracted_root = Path(temporary_directory).resolve(strict=True)
                reader.extract_to(extracted_root)
                safe = [
                    (entry, PurePosixPath(entry.relative_path))
                    for entry in reader.entries
                    if not entry.is_directory
                ]
                dlls = [
                    (entry, member)
                    for entry, member in safe
                    if member.name.casefold() == "optiscaler.dll"
                ]
                if len(dlls) != 1:
                    raise OptiScalerError(
                        "archive must contain exactly one OptiScaler.dll"
                    )
                payload_root = dlls[0][1].parent
                ini_present = any(
                    member.parent == payload_root
                    and member.name.casefold() == "optiscaler.ini"
                    for _entry, member in safe
                )
                if not ini_present:
                    raise OptiScalerError("archive does not contain OptiScaler.ini")
                mapped: list[tuple[ArchiveEntry, str, str]] = []
                seen: set[str] = set()
                for entry, member in safe:
                    try:
                        relative = member.relative_to(payload_root)
                    except ValueError:
                        continue
                    target = (
                        PurePosixPath(injection_dll)
                        if relative.name.casefold() == "optiscaler.dll"
                        else relative
                    )
                    target_text = target.as_posix()
                    folded = target_text.casefold()
                    if folded in seen:
                        raise OptiScalerError(
                            f"archive maps multiple files to {target_text}"
                        )
                    seen.add(folded)
                    extracted = self._target(extracted_root, entry.relative_path)
                    if not extracted.is_file():
                        raise OptiScalerError(
                            f"archive member was not extracted: {entry.relative_path}"
                        )
                    mapped.append((entry, target_text, self._hash_file(extracted)))
        except ArchiveReadError as error:
            raise OptiScalerError(str(error)) from error
        if not mapped:
            raise OptiScalerError("OptiScaler archive has no installable payload")
        version = self._detect_version(archive_path)
        return reader.format_name, version, tuple(mapped)

    @staticmethod
    def _detect_version(archive_path: Path) -> str:
        import re

        match = re.search(
            r"(?i)optiscaler[^0-9]*v?(\d+(?:\.\d+){1,3}(?:[-_a-z0-9.]*)?)",
            archive_path.stem,
        )
        return match.group(1).replace("_", "-") if match else "Unknown"

    @staticmethod
    def _canonical_game_root(game: Game) -> Path:
        try:
            root = game.install_path.resolve(strict=True)
        except OSError as error:
            raise OptiScalerError("game directory is unavailable") from error
        if not root.is_dir():
            raise OptiScalerError("game path is not a directory")
        return root

    @staticmethod
    def _selected_candidate(
        game: Game,
        resolver: GameExecutableResolver,
        executable: str,
    ) -> tuple[ExecutableCandidate, str]:
        resolution = resolver.resolve(game, executable)
        if not resolution.reliable or resolution.selected is None:
            raise OptiScalerError(resolution.message or "choose the main executable")
        return resolution.selected, resolution.status

    @staticmethod
    def _target(root: Path, relative: str) -> Path:
        root = root.resolve(strict=False)
        candidate = (root / PurePosixPath(relative)).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise OptiScalerError(f"target escapes the executable directory: {relative}") from error
        return candidate

    def _anti_cheat_markers(self, root: Path) -> tuple[str, ...]:
        found: list[str] = []
        visited = 0
        for directory, names, files in os.walk(root, followlinks=False):
            names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
            for name in (*names, *files):
                visited += 1
                if visited > 50000:
                    return tuple(found)
                folded = name.casefold()
                if any(marker in folded for marker in ANTI_CHEAT_MARKERS):
                    found.append(str((Path(directory) / name).relative_to(root)))
                    if len(found) >= 12:
                        return tuple(found)
        return tuple(found)

    def manifest_path(self, app_id: str, manifest_id: str) -> Path:
        return self.data_root / app_id / "optiscaler" / "manifests" / f"{manifest_id}.json"

    def backup_root(self, app_id: str, manifest_id: str) -> Path:
        return self.data_root / app_id / "optiscaler" / "backups" / manifest_id

    def _load_manifest(self, profile: OptiScalerProfile) -> dict[str, Any]:
        if not profile.manifest_id:
            raise OptiScalerError("OptiScaler installation manifest is unavailable")
        path = self.manifest_path(profile.app_id, profile.manifest_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OptiScalerError(f"could not read OptiScaler manifest: {error}") from error
        if not isinstance(raw, dict) or raw.get("app_id") != profile.app_id:
            raise OptiScalerError("OptiScaler manifest does not match this game")
        return raw

    def plan(
        self,
        game: Game,
        archive_path: Path,
        *,
        executable: str = "",
        injection_dll: str = "auto",
    ) -> OptiScalerInstallPlan:
        if not game.steam_app_id:
            raise OptiScalerError("OptiScaler requires a Steam AppID")
        root = self._canonical_game_root(game)
        profile = self.profile_repository.load(game.steam_app_id)
        selected_path = executable or profile.executable
        candidate, confidence = self._selected_candidate(
            game, self.executable_resolver, selected_path
        )
        exe_path = self._target(root, candidate.relative_path)
        if not exe_path.is_file():
            raise OptiScalerError("selected executable is unavailable")
        install_directory = exe_path.parent
        proxy = "dxgi.dll" if injection_dll == "auto" else injection_dll.casefold()
        if proxy not in OPTISCALER_PROXY_DLLS:
            raise OptiScalerError("unsupported OptiScaler proxy DLL")
        archive_format, version, archive_files = self._archive_payload(
            Path(archive_path), proxy
        )
        active_manifest: dict[str, Any] = {}
        if profile.manifest_id:
            try:
                active_manifest = self._load_manifest(profile)
            except OptiScalerError:
                pass
        managed_hashes = {
            str(item.get("relative_path", "")): str(item.get("after_sha256", ""))
            for item in active_manifest.get("installed_files", [])
            if isinstance(item, Mapping)
        }
        files: list[OptiScalerFilePlan] = []
        conflicts: list[OptiScalerConflict] = []
        for entry, relative, digest in archive_files:
            target = self._target(install_directory, relative)
            existing_hash = ""
            exists = target.exists() or target.is_symlink()
            if exists:
                if target.is_symlink() or not target.is_file():
                    raise OptiScalerError(f"target is not a regular file: {relative}")
                existing_hash = self._hash_file(target)
                conflicts.append(
                    OptiScalerConflict(
                        relative,
                        existing_hash,
                        "existing_proxy" if relative.casefold() == proxy else "existing_file",
                        managed_hashes.get(relative) == existing_hash,
                    )
                )
            files.append(
                OptiScalerFilePlan(
                    entry.relative_path,
                    relative,
                    entry.size,
                    digest,
                    exists,
                    existing_hash,
                )
            )
        planned_targets = {item.target_relative_path.casefold() for item in files}
        existing_by_name = {
            item.name.casefold(): item
            for item in install_directory.iterdir()
        }
        inspected_markers: set[str] = set()
        for name in (*OPTISCALER_PROXY_DLLS, *LOADER_MARKERS):
            folded_name = name.casefold()
            if folded_name in planned_targets or folded_name in inspected_markers:
                continue
            inspected_markers.add(folded_name)
            target = existing_by_name.get(folded_name, install_directory / name)
            if target.is_file():
                conflicts.append(
                    OptiScalerConflict(
                        target.name,
                        self._hash_file(target),
                        "other_loader",
                        managed_hashes.get(target.name) == self._hash_file(target),
                    )
                )
            elif target.is_dir():
                conflicts.append(
                    OptiScalerConflict(target.name, "", "other_loader")
                )
        blockers: list[str] = []
        if not game.library_available:
            blockers.append("The game library is unavailable")
        if game.update_in_progress:
            blockers.append("A Steam update is currently active")
        anti_cheat = self._anti_cheat_markers(root)
        if game.has_anticheat or anti_cheat:
            blockers.append("Anti-cheat files were detected; automatic installation is blocked")
        try:
            if self._process_detector(root):
                blockers.append("The game is currently running")
        except OSError:
            blockers.append("The running-game state could not be verified")
        warnings = [
            "Automatic proxy selection prefers dxgi.dll but cannot guarantee compatibility"
        ] if injection_dll == "auto" else []
        if anti_cheat:
            warnings.append("Detected anti-cheat: " + ", ".join(anti_cheat[:4]))
        return OptiScalerInstallPlan(
            app_id=str(game.steam_app_id),
            archive_path=str(Path(archive_path).resolve()),
            archive_format=archive_format,
            version=version,
            executable=candidate.relative_path,
            install_directory=str(install_directory),
            executable_confidence=confidence,
            injection_dll=proxy,
            proton_override=f"{Path(proxy).stem}=n,b",
            backup_directory=str(
                self.data_root
                / str(game.steam_app_id)
                / "optiscaler"
                / "backups"
            ),
            files=tuple(files),
            conflicts=tuple(dict.fromkeys(conflicts)),
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _copy_atomic(source: BinaryIO | Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                if isinstance(source, Path):
                    with source.open("rb") as handle:
                        shutil.copyfileobj(handle, destination, 1024 * 1024)
                else:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def install(
        self,
        game: Game,
        archive_path: Path,
        *,
        executable: str = "",
        injection_dll: str = "auto",
        allow_replace_conflicts: bool = False,
        cancel_event: Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> OptiScalerProfile:
        emit = progress or (lambda _stage, _value: None)
        emit("Validation", 0.05)
        plan = self.plan(
            game,
            archive_path,
            executable=executable,
            injection_dll=injection_dll,
        )
        self._check_cancel(cancel_event)
        if plan.blockers:
            raise OptiScalerError("; ".join(plan.blockers))
        if plan.conflicts and not allow_replace_conflicts:
            raise OptiScalerConflictError("conflicting files require confirmation")
        manifest_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
        backup_root = self.backup_root(plan.app_id, manifest_id)
        install_root = Path(plan.install_directory).resolve(strict=True)
        created: list[dict[str, Any]] = []
        replaced_files: list[dict[str, Any]] = []
        installed_files: list[dict[str, Any]] = []
        changed: list[tuple[Path, Path | None, str]] = []
        emit("Preparing manifest", 0.15)
        try:
            # Stage only the already validated payload in a private temporary
            # directory. Nothing from the release archive is executed and no
            # archive path is ever used directly as a destination path.
            with tempfile.TemporaryDirectory(
                prefix="gameforge-optiscaler-"
            ) as temporary_directory:
                staging_root = Path(temporary_directory).resolve(strict=True)
                emit("Extracting archive", 0.18)
                try:
                    reader = open_archive(Path(plan.archive_path))
                    reader.extract_to(staging_root)
                except ArchiveReadError as error:
                    raise OptiScalerError(str(error)) from error
                staged_files: list[tuple[OptiScalerFilePlan, Path]] = []
                total = max(1, len(plan.files))
                for index, item in enumerate(plan.files):
                    self._check_cancel(cancel_event)
                    emit("Validating extracted files", 0.22 + 0.08 * index / total)
                    staged = self._target(staging_root, item.archive_member)
                    if not staged.is_file():
                        raise OptiScalerError(
                            f"archive member was not extracted: {item.archive_member}"
                        )
                    if self._hash_file(staged) != item.sha256:
                        raise OptiScalerError(
                            f"archive staging hash mismatch: {item.target_relative_path}"
                        )
                    staged_files.append((item, staged))

                for index, (item, staged) in enumerate(staged_files):
                    self._check_cancel(cancel_event)
                    target = self._target(install_root, item.target_relative_path)
                    # TOCTOU: compare the current target to the inspected plan.
                    current_exists = target.exists() or target.is_symlink()
                    if current_exists:
                        if target.is_symlink() or not target.is_file():
                            raise OptiScalerError(
                                f"target changed before installation: {item.target_relative_path}"
                            )
                        current_hash = self._hash_file(target)
                        if current_hash != item.existing_sha256:
                            raise OptiScalerConflictError(
                                f"target changed before installation: {item.target_relative_path}"
                            )
                        backup = self._target(backup_root, item.target_relative_path)
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup, follow_symlinks=False)
                        if self._hash_file(backup) != current_hash:
                            raise OptiScalerError(
                                f"backup verification failed: {item.target_relative_path}"
                            )
                        replaced_files.append(
                            {
                                "relative_path": item.target_relative_path,
                                "before_sha256": current_hash,
                                "after_sha256": item.sha256,
                                "backup_relative_path": item.target_relative_path,
                                "mode": stat.S_IMODE(target.stat().st_mode),
                                "mtime_ns": target.stat().st_mtime_ns,
                            }
                        )
                        changed.append((target, backup, item.sha256))
                    else:
                        if item.replaces_existing:
                            raise OptiScalerConflictError(
                                f"target disappeared before installation: {item.target_relative_path}"
                            )
                        created.append(
                            {
                                "relative_path": item.target_relative_path,
                                "after_sha256": item.sha256,
                            }
                        )
                        changed.append((target, None, item.sha256))
                    emit("Copying files", 0.32 + 0.48 * index / total)
                    self._copy_atomic(staged, target)
                    actual = self._hash_file(target)
                    if actual != item.sha256:
                        raise OptiScalerError(
                            f"installed file hash mismatch: {item.target_relative_path}"
                        )
                    installed_files.append(
                        {
                            "relative_path": item.target_relative_path,
                            "archive_member": item.archive_member,
                            "size": item.size,
                            "after_sha256": actual,
                        }
                    )
            self._check_cancel(cancel_event)
            emit("Configuring runner", 0.85)
            installed_at = datetime.now(UTC)
            manifest = {
                "format_version": MANIFEST_FORMAT_VERSION,
                "manifest_id": manifest_id,
                "app_id": plan.app_id,
                "executable": plan.executable,
                "install_directory": plan.install_directory,
                "optiscaler_version": plan.version,
                "archive_format": plan.archive_format,
                "injection_dll": plan.injection_dll,
                "proton_override": plan.proton_override,
                "archive_path": plan.archive_path,
                "backup_directory": str(backup_root),
                "installed_at": installed_at.isoformat(),
                "installed_files": installed_files,
                "created_files": created,
                "replaced_files": replaced_files,
                "detected_conflicts": [item.to_dict() for item in plan.conflicts],
            }
            manifest_path = self.manifest_path(plan.app_id, manifest_id)
            _atomic_write(
                manifest_path,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            profile = OptiScalerProfile(
                schema_version=1,
                app_id=plan.app_id,
                enabled=True,
                executable=plan.executable,
                install_directory=plan.install_directory,
                installed_version=plan.version,
                injection_dll=plan.injection_dll,
                proton_override=plan.proton_override,
                manifest_id=manifest_id,
                installation_state="installed",
                last_verified_at=installed_at,
                updated_at=installed_at,
            )
            self.profile_repository.save(profile)
            emit("Completed", 1.0)
            return profile
        except Exception:
            for target, backup, expected_hash in reversed(changed):
                try:
                    if backup is not None and backup.is_file():
                        self._copy_atomic(backup, target)
                    elif target.is_file() and self._hash_file(target) == expected_hash:
                        target.unlink()
                except OSError:
                    pass
            raise

    def _assert_mutation_allowed(self, game: Game) -> None:
        root = self._canonical_game_root(game)
        if game.update_in_progress:
            raise OptiScalerError("A Steam update is currently active")
        if self._process_detector(root):
            raise OptiScalerError("The game is currently running")

    def verify(self, game: Game) -> OptiScalerProfile:
        if not game.steam_app_id:
            raise OptiScalerError("OptiScaler requires a Steam AppID")
        profile = self.profile_repository.load(game.steam_app_id)
        if not profile.manifest_id:
            return profile
        manifest = self._load_manifest(profile)
        install_root = Path(str(manifest["install_directory"])).resolve(strict=False)
        expected_root = self._canonical_game_root(game)
        try:
            install_root.relative_to(expected_root)
        except ValueError as error:
            raise OptiScalerError("manifest install directory is outside this game") from error
        intact = True
        for item in manifest.get("installed_files", []):
            if not isinstance(item, Mapping):
                intact = False
                continue
            target = self._target(install_root, str(item.get("relative_path", "")))
            if not target.is_file() or self._hash_file(target) != item.get("after_sha256"):
                intact = False
        now = datetime.now(UTC)
        state = "installed" if intact and profile.enabled else profile.installation_state
        if profile.enabled and not intact:
            state = "corrupt"
        updated = replace(
            profile,
            installation_state=state,
            last_verified_at=now,
            updated_at=now,
        )
        self.profile_repository.save(updated)
        return updated

    def remove(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> OptiScalerProfile:
        self._assert_mutation_allowed(game)
        if not game.steam_app_id:
            raise OptiScalerError("OptiScaler requires a Steam AppID")
        emit = progress or (lambda _stage, _value: None)
        profile = self.profile_repository.load(game.steam_app_id)
        manifest = self._load_manifest(profile)
        install_root = Path(str(manifest["install_directory"])).resolve(strict=True)
        created = [item for item in manifest.get("created_files", []) if isinstance(item, Mapping)]
        emit("Removing managed files", 0.1)
        for index, item in enumerate(created):
            self._check_cancel(cancel_event)
            target = self._target(install_root, str(item.get("relative_path", "")))
            if not target.exists():
                continue
            if not target.is_file() or self._hash_file(target) != item.get("after_sha256"):
                raise OptiScalerConflictError(
                    f"managed file changed and was not removed: {item.get('relative_path', '')}"
                )
            target.unlink()
            emit("Removing managed files", 0.1 + 0.75 * (index + 1) / max(1, len(created)))
        replacements = [
            item for item in manifest.get("replaced_files", []) if isinstance(item, Mapping)
        ]
        now = datetime.now(UTC)
        updated = replace(
            profile,
            enabled=False,
            installation_state="restore_required" if replacements else "removed",
            last_verified_at=now,
            updated_at=now,
        )
        self.profile_repository.save(updated)
        emit("Completed", 1.0)
        return updated

    def restore(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> OptiScalerProfile:
        self._assert_mutation_allowed(game)
        if not game.steam_app_id:
            raise OptiScalerError("OptiScaler requires a Steam AppID")
        emit = progress or (lambda _stage, _value: None)
        profile = self.profile_repository.load(game.steam_app_id)
        manifest = self._load_manifest(profile)
        install_root = Path(str(manifest["install_directory"])).resolve(strict=True)
        backup_root = self.backup_root(profile.app_id, profile.manifest_id)
        replacements = [
            item for item in manifest.get("replaced_files", []) if isinstance(item, Mapping)
        ]
        emit("Restoring previous files", 0.1)
        for index, item in enumerate(replacements):
            self._check_cancel(cancel_event)
            relative = str(item.get("relative_path", ""))
            target = self._target(install_root, relative)
            if target.exists():
                if not target.is_file() or self._hash_file(target) != item.get("after_sha256"):
                    raise OptiScalerConflictError(
                        f"installed file changed and was not restored: {relative}"
                    )
            backup = self._target(
                backup_root, str(item.get("backup_relative_path", relative))
            )
            if not backup.is_file() or self._hash_file(backup) != item.get("before_sha256"):
                raise OptiScalerError(f"backup hash mismatch: {relative}")
            self._copy_atomic(backup, target)
            if self._hash_file(target) != item.get("before_sha256"):
                raise OptiScalerError(f"restored file hash mismatch: {relative}")
            emit("Restoring previous files", 0.1 + 0.8 * (index + 1) / max(1, len(replacements)))
        now = datetime.now(UTC)
        updated = replace(
            profile,
            enabled=False,
            installation_state="removed",
            last_verified_at=now,
            updated_at=now,
        )
        self.profile_repository.save(updated)
        emit("Completed", 1.0)
        return updated

    def status(self, game: Game) -> dict[str, Any]:
        if not game.steam_app_id:
            return {"success": False, "error": "OptiScaler requires a Steam AppID"}
        profile = self.profile_repository.load(game.steam_app_id)
        resolution = self.executable_resolver.resolve(game, profile.executable)
        selected = resolution.selected
        manifest: dict[str, Any] = {}
        if profile.manifest_id:
            try:
                manifest = self._load_manifest(profile)
            except OptiScalerError:
                pass
        data = profile.to_dict()
        data.update(
            {
                "success": True,
                "appId": profile.app_id,
                "installationState": profile.installation_state,
                "installed": profile.enabled and profile.installation_state == "installed",
                "installedVersion": profile.installed_version,
                "injectionDll": profile.injection_dll,
                "protonOverride": profile.proton_override,
                "manifestId": profile.manifest_id,
                "manifestPath": (
                    str(self.manifest_path(profile.app_id, profile.manifest_id))
                    if profile.manifest_id else ""
                ),
                "executable": profile.executable,
                "installDirectory": profile.install_directory,
                "executableStatus": resolution.status,
                "executableConfidence": resolution.status,
                "selectedExecutable": selected.to_dict() if selected else {},
                "executableCandidates": [item.to_dict() for item in resolution.candidates],
                "executableMessage": resolution.message,
                "installedFiles": list(manifest.get("installed_files", [])),
                "replacedFiles": list(manifest.get("replaced_files", [])),
                "createdFiles": list(manifest.get("created_files", [])),
                "lastVerifiedAt": (
                    profile.last_verified_at.astimezone(UTC).isoformat()
                    if profile.last_verified_at else ""
                ),
            }
        )
        return data


__all__ = [
    "MANIFEST_FORMAT_VERSION",
    "OptiScalerCancelled",
    "OptiScalerConflict",
    "OptiScalerConflictError",
    "OptiScalerError",
    "OptiScalerFilePlan",
    "OptiScalerInstallPlan",
    "OptiScalerProfileRepository",
    "OptiScalerService",
    "merge_wine_dll_overrides",
]
