"""Safe local-archive OptiScaler installation for Proton games."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import re
from threading import Event
from typing import Any, BinaryIO
from uuid import uuid4

from game_optimization_linux.config import GAMES_CONFIG_DIR, OPTISCALER_DATA_DIR
from game_optimization_linux.models import (
    Game,
    OptiScalerProfile,
    OPTISCALER_PROXY_DLLS,
    OPTISCALER_SCHEMA_VERSION,
)

from .btrfs_analysis import BtrfsCompressionAnalyzer
from .archive_reader import ArchiveEntry, ArchiveReadError, open_archive
from .game_executable import ExecutableCandidate, GameExecutableResolver
from .mangohud import _atomic_write
from .optiscaler_fsr4 import (
    OptiScalerIniCapabilities,
    inspect_optiscaler_ini,
    inspect_optiscaler_ini_state,
    managed_ini_differences,
    managed_ini_updates,
    update_ini_text,
)


PROFILE_FILE_NAME = "optiscaler.json"
# Version 2 adds lifecycle lineage (previous manifest, operation, displaced
# and reconciled files) while remaining able to read version 1 manifests.
MANIFEST_FORMAT_VERSION = 2
INSTALL_OPERATIONS = ("auto", "install", "update", "repair", "reinstall")
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
MUTABLE_MANAGED_FILES = frozenset({"optiscaler.ini"})


def _is_mutable_configuration(relative_path: object) -> bool:
    return Path(str(relative_path or "")).name.casefold() in MUTABLE_MANAGED_FILES


class OptiScalerError(RuntimeError):
    pass


class OptiScalerCancelled(OptiScalerError):
    pass


class OptiScalerConflictError(OptiScalerError):
    pass


@dataclass(frozen=True, slots=True)
class OptiScalerVerificationResult:
    """Completed verification report while retaining the installed profile."""

    profile: OptiScalerProfile
    state: str
    summary: str
    payload_valid: bool | None
    configuration_drift: bool
    managed_configuration_drift: bool
    backup_valid: bool | None
    issues: tuple[dict[str, Any], ...] = ()

    @property
    def app_id(self) -> str:
        return self.profile.app_id

    @property
    def installation_state(self) -> str:
        return self.profile.installation_state

    def to_dict(self) -> dict[str, Any]:
        data = self.profile.to_dict()
        data.update(
            {
                "installationVerificationState": self.state,
                "installationVerificationSummary": self.summary,
                "installationVerificationCompleted": self.state
                != "verification_error",
                "installationPayloadValid": self.payload_valid,
                "configurationDrift": self.configuration_drift,
                "managedConfigurationDrift": self.managed_configuration_drift,
                "backupValid": self.backup_valid,
                "verificationIssues": list(self.issues),
            }
        )
        return data


@dataclass(frozen=True, slots=True)
class OptiScalerConflict:
    relative_path: str
    sha256: str
    kind: str
    managed_by_game_optimization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "relativePath": self.relative_path,
            "sha256": self.sha256,
            "kind": self.kind,
            "managedByGameOptimization": self.managed_by_game_optimization,
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

    @property
    def requires_conflict_confirmation(self) -> bool:
        """Return whether installing would replace an unmanaged file.

        Files belonging to the active Game Optimization installation are not
        user conflicts.  Update and repair may replace them without asking the
        user to confirm the same files on every release.
        """

        return any(
            not conflict.managed_by_game_optimization
            for conflict in self.conflicts
        )

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
            "requiresConflictConfirmation": self.requires_conflict_confirmation,
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

    def include(raw: str, *, replace_existing: bool = False) -> None:
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
            target_index = index_by_name[normalized_name]
            if replace_existing:
                # The managed proxy must keep its required native-first order.
                # Preserve unrelated user entries, but do not turn an existing
                # ``dxgi=b`` into ``dxgi=b,n`` because that defeats OptiScaler.
                entries[target_index] = (
                    entries[target_index][0],
                    list(dict.fromkeys(modes)),
                )
                return
            target_modes = entries[target_index][1]
            for mode in modes:
                if mode not in target_modes:
                    target_modes.append(mode)
            return
        index_by_name[normalized_name] = len(entries)
        entries.append((name.strip(), list(dict.fromkeys(modes))))

    for token in str(existing or "").split(";"):
        include(token)
    include(addition, replace_existing=True)
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
    def game_key(game: Game) -> str:
        if game.steam_app_id:
            return str(game.steam_app_id)
        if game.data_source.casefold() == "local" and game.id.startswith("local-"):
            return game.id
        raise OptiScalerError("OptiScaler requires a Steam or configured local game")

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
                prefix="game-optimization-optiscaler-plan-"
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

    def archive_ini_capabilities(
        self, archive_path: Path
    ) -> OptiScalerIniCapabilities:
        """Inspect the shipped INI without executing or installing the archive."""

        try:
            reader = open_archive(Path(archive_path))
            with tempfile.TemporaryDirectory(
                prefix="game-optimization-optiscaler-capabilities-"
            ) as temporary_directory:
                root = Path(temporary_directory).resolve(strict=True)
                reader.extract_to(root)
                dlls = [
                    PurePosixPath(entry.relative_path)
                    for entry in reader.entries
                    if not entry.is_directory
                    and PurePosixPath(entry.relative_path).name.casefold()
                    == "optiscaler.dll"
                ]
                if len(dlls) != 1:
                    raise OptiScalerError(
                        "archive must contain exactly one OptiScaler.dll"
                    )
                ini_entries = [
                    PurePosixPath(entry.relative_path)
                    for entry in reader.entries
                    if not entry.is_directory
                    and PurePosixPath(entry.relative_path).parent == dlls[0].parent
                    and PurePosixPath(entry.relative_path).name.casefold()
                    == "optiscaler.ini"
                ]
                if len(ini_entries) != 1:
                    raise OptiScalerError(
                        "archive must contain exactly one OptiScaler.ini"
                    )
                target = self._target(root, ini_entries[0].as_posix())
                capabilities = inspect_optiscaler_ini(
                    target.read_text(encoding="utf-8-sig")
                )
                has_agility_payload = any(
                    "d3d12_optiscaler" in {
                        part.casefold()
                        for part in PurePosixPath(entry.relative_path).parts[:-1]
                    }
                    for entry in reader.entries
                    if not entry.is_directory
                )
                if capabilities.agility_sdk_upgrade and not has_agility_payload:
                    capabilities = replace(
                        capabilities, agility_sdk_upgrade=False
                    )
                return capabilities
        except ArchiveReadError as error:
            raise OptiScalerError(str(error)) from error
        except (OSError, UnicodeError) as error:
            raise OptiScalerError(
                "archive OptiScaler.ini could not be inspected"
            ) from error

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

    def remember_executable(self, game: Game, executable: str) -> OptiScalerProfile:
        """Persist one validated executable choice for this game."""

        game_key = self.game_key(game)
        selected = self.executable_resolver.validate_selected(game, executable)
        if selected is None:
            raise OptiScalerError(
                "selected executable must be a regular game executable inside the game directory"
            )
        current = self.profile_repository.load(game_key)
        now = datetime.now(UTC)
        updated = replace(
            current,
            executable=selected.relative_path,
            updated_at=now,
        )
        self.profile_repository.save(updated)
        return updated

    def set_channel(self, game: Game, channel: str) -> OptiScalerProfile:
        """Select an official release channel without changing installed files."""

        profile = self.profile_repository.load(self.game_key(game))
        now = datetime.now(UTC)
        try:
            updated = replace(
                profile,
                channel=str(channel or "").strip().casefold(),
                updated_at=now,
            )
        except ValueError as error:
            raise OptiScalerError(str(error)) from error
        self.profile_repository.save(updated)
        return updated

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
        allow_anticheat_risk: bool = False,
        version_override: str = "",
    ) -> OptiScalerInstallPlan:
        game_key = self.game_key(game)
        root = self._canonical_game_root(game)
        profile = self.profile_repository.load(game_key)
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
        if version_override:
            selected_version = str(version_override).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}", selected_version):
                raise OptiScalerError("invalid OptiScaler release version")
            version = selected_version
        active_manifest: dict[str, Any] = {}
        if profile.manifest_id:
            try:
                active_manifest = self._load_manifest(profile)
            except OptiScalerError:
                pass
        managed_hashes = {
            str(item.get("relative_path", "")).casefold(): str(
                item.get("after_sha256", "")
            )
            for item in active_manifest.get("installed_files", [])
            if isinstance(item, Mapping)
        }
        existing_by_name = {
            item.name.casefold(): item
            for item in install_directory.iterdir()
        }
        files: list[OptiScalerFilePlan] = []
        conflicts: list[OptiScalerConflict] = []
        for entry, relative, digest in archive_files:
            relative_path = PurePosixPath(relative)
            existing_root_entry = (
                existing_by_name.get(relative_path.name.casefold())
                if relative_path.parent == PurePosixPath(".")
                else None
            )
            if existing_root_entry is not None:
                relative = existing_root_entry.name
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
                        bool(
                            relative.casefold() in managed_hashes
                            and (
                                managed_hashes.get(relative.casefold())
                                == existing_hash
                                or _is_mutable_configuration(relative)
                            )
                        ),
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
                        managed_hashes.get(target.name.casefold())
                        == self._hash_file(target),
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
        if (game.has_anticheat or anti_cheat) and not allow_anticheat_risk:
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
            warnings.append(
                "OptiScaler can trigger anti-cheat systems; use it only after an explicit risk confirmation"
            )
        return OptiScalerInstallPlan(
            app_id=game_key,
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
                / game_key
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

    @staticmethod
    def _directory_identity(path: Path) -> tuple[int, int]:
        try:
            metadata = path.stat()
        except OSError as error:
            raise OptiScalerError("game installation directory is unavailable") from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise OptiScalerError("game installation path is no longer a directory")
        return int(metadata.st_dev), int(metadata.st_ino)

    def _assert_mutation_safe(
        self,
        game: Game,
        *,
        expected_game_root: Path,
        expected_game_identity: tuple[int, int],
        expected_install_root: Path,
        expected_install_identity: tuple[int, int],
    ) -> None:
        """Revalidate identity and live blockers immediately before mutation."""

        current_game_root = self._canonical_game_root(game)
        if (
            current_game_root != expected_game_root
            or self._directory_identity(current_game_root) != expected_game_identity
        ):
            raise OptiScalerError("the game directory changed before installation")
        try:
            current_install_root = expected_install_root.resolve(strict=True)
            current_install_root.relative_to(current_game_root)
        except (OSError, ValueError) as error:
            raise OptiScalerError(
                "the executable directory changed before installation"
            ) from error
        if (
            current_install_root != expected_install_root
            or self._directory_identity(current_install_root)
            != expected_install_identity
        ):
            raise OptiScalerError("the executable directory changed before installation")
        if game.update_in_progress:
            raise OptiScalerError("A Steam update is currently active")
        try:
            if self._process_detector(current_game_root):
                raise OptiScalerError("The game is currently running")
        except OptiScalerError:
            raise
        except OSError as error:
            raise OptiScalerError(
                "The running-game state could not be verified"
            ) from error

    def install(
        self,
        game: Game,
        archive_path: Path,
        *,
        executable: str = "",
        injection_dll: str = "auto",
        operation: str = "auto",
        allow_replace_conflicts: bool = False,
        allow_anticheat_risk: bool = False,
        cancel_event: Event | None = None,
        progress: Callable[[str, float], None] | None = None,
        expected_archive_sha256: str = "",
        source_identity: str = "local_archive",
        channel: str = "stable",
        fidelityfx_upscaler_version: str = "",
        release_version: str = "",
    ) -> OptiScalerProfile:
        """Install from a local archive or a verified private snapshot.

        Online callers bind the operation to the SHA-256 recorded when the
        official release was downloaded.  Copying it to a private temporary
        file before planning closes the cache validation/extraction TOCTOU
        window without changing the local-archive workflow.
        """

        expected_hash = str(expected_archive_sha256 or "").strip().casefold()
        if not expected_hash:
            return self._install_from_archive(
                game,
                Path(archive_path),
                executable=executable,
                injection_dll=injection_dll,
                operation=operation,
                allow_replace_conflicts=allow_replace_conflicts,
                allow_anticheat_risk=allow_anticheat_risk,
                cancel_event=cancel_event,
                progress=progress,
                source_identity=source_identity,
                channel=channel,
                fidelityfx_upscaler_version=fidelityfx_upscaler_version,
                release_version=release_version,
            )
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise OptiScalerError("invalid expected OptiScaler archive SHA-256")
        requested_path = Path(archive_path)
        if requested_path.is_symlink():
            raise OptiScalerError("verified OptiScaler archive must not be a symbolic link")
        try:
            source_archive = requested_path.resolve(strict=True)
        except OSError as error:
            raise OptiScalerError("verified OptiScaler archive is unavailable") from error
        if not source_archive.is_file():
            raise OptiScalerError("verified OptiScaler archive is not a regular file")
        with tempfile.TemporaryDirectory(
            prefix="game-optimization-optiscaler-verified-"
        ) as temporary_directory:
            snapshot = Path(temporary_directory) / source_archive.name
            self._copy_atomic(source_archive, snapshot)
            actual_hash = self._hash_file(snapshot)
            if actual_hash != expected_hash:
                raise OptiScalerError(
                    "verified OptiScaler archive changed before installation"
                )
            return self._install_from_archive(
                game,
                snapshot,
                executable=executable,
                injection_dll=injection_dll,
                operation=operation,
                allow_replace_conflicts=allow_replace_conflicts,
                allow_anticheat_risk=allow_anticheat_risk,
                cancel_event=cancel_event,
                progress=progress,
                archive_path_for_manifest=source_archive,
                archive_sha256=actual_hash,
                source_identity=source_identity,
                channel=channel,
                fidelityfx_upscaler_version=fidelityfx_upscaler_version,
                release_version=release_version,
            )

    def _install_from_archive(
        self,
        game: Game,
        archive_path: Path,
        *,
        executable: str = "",
        injection_dll: str = "auto",
        operation: str = "auto",
        allow_replace_conflicts: bool = False,
        allow_anticheat_risk: bool = False,
        cancel_event: Event | None = None,
        progress: Callable[[str, float], None] | None = None,
        archive_path_for_manifest: Path | None = None,
        archive_sha256: str = "",
        source_identity: str = "local_archive",
        channel: str = "stable",
        fidelityfx_upscaler_version: str = "",
        release_version: str = "",
    ) -> OptiScalerProfile:
        emit = progress or (lambda _stage, _value: None)
        emit("Validation", 0.05)
        plan = self.plan(
            game,
            archive_path,
            executable=executable,
            injection_dll=injection_dll,
            allow_anticheat_risk=allow_anticheat_risk,
            version_override=release_version,
        )
        self._check_cancel(cancel_event)
        if plan.blockers:
            raise OptiScalerError("; ".join(plan.blockers))
        if plan.requires_conflict_confirmation and not allow_replace_conflicts:
            raise OptiScalerConflictError("conflicting files require confirmation")

        requested_operation = str(operation or "auto").strip().casefold()
        if requested_operation not in INSTALL_OPERATIONS:
            raise OptiScalerError("unsupported OptiScaler install operation")
        previous_profile = self.profile_repository.load(plan.app_id)
        previous_manifest: dict[str, Any] = {}
        has_active_installation = bool(
            previous_profile.enabled
            and previous_profile.manifest_id
            and previous_profile.installation_state
            in {"installed", "corrupt", "partial"}
        )
        if previous_profile.installation_state == "restore_required":
            raise OptiScalerError(
                "restore the previously replaced files before installing OptiScaler again"
            )
        if has_active_installation:
            previous_manifest = self._load_manifest(previous_profile)
        if requested_operation == "auto":
            if not has_active_installation:
                effective_operation = "install"
            elif previous_profile.installed_version == plan.version:
                effective_operation = "repair"
            else:
                effective_operation = "update"
        else:
            effective_operation = requested_operation
        if effective_operation == "install" and has_active_installation:
            raise OptiScalerError(
                "OptiScaler is already installed; use update, repair, or reinstall"
            )
        if effective_operation in {"update", "repair", "reinstall"} and not has_active_installation:
            raise OptiScalerError(
                f"cannot {effective_operation} OptiScaler before it is installed"
            )

        manifest_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
        backup_root = self.backup_root(plan.app_id, manifest_id)
        game_root = self._canonical_game_root(game)
        install_root = Path(plan.install_directory).resolve(strict=True)
        try:
            install_root.relative_to(game_root)
        except ValueError as error:
            raise OptiScalerError(
                "the executable directory is outside the current game directory"
            ) from error
        game_root_identity = self._directory_identity(game_root)
        install_root_identity = self._directory_identity(install_root)
        previous_backup_root: Path | None = None
        previous_installed: dict[str, Mapping[str, Any]] = {}
        previous_created: dict[str, Mapping[str, Any]] = {}
        previous_replaced: dict[str, Mapping[str, Any]] = {}
        if previous_manifest:
            previous_install_root = Path(
                str(previous_manifest.get("install_directory", ""))
            ).resolve(strict=False)
            if previous_install_root != install_root:
                raise OptiScalerError(
                    "the selected executable directory changed; remove and restore the existing installation first"
                )
            previous_backup_root = self.backup_root(
                plan.app_id, previous_profile.manifest_id
            )

            def manifest_items(name: str) -> dict[str, Mapping[str, Any]]:
                result: dict[str, Mapping[str, Any]] = {}
                for raw in previous_manifest.get(name, []):
                    if not isinstance(raw, Mapping):
                        raise OptiScalerError(
                            f"existing OptiScaler manifest contains an invalid {name} entry"
                        )
                    relative = str(raw.get("relative_path", ""))
                    # _target validates traversal and malformed empty paths.
                    self._target(install_root, relative)
                    if not relative or relative in result:
                        raise OptiScalerError(
                            f"existing OptiScaler manifest contains an invalid {name} path"
                        )
                    result[relative] = raw
                return result

            previous_installed = manifest_items("installed_files")
            previous_created = manifest_items("created_files")
            previous_replaced = manifest_items("replaced_files")
            for relative in previous_installed:
                if relative not in previous_created and relative not in previous_replaced:
                    raise OptiScalerError(
                        f"existing OptiScaler manifest has no restore provenance for {relative}"
                    )

        created: list[dict[str, Any]] = []
        replaced_files: list[dict[str, Any]] = []
        displaced_files: list[dict[str, Any]] = []
        installed_files: list[dict[str, Any]] = []
        reconciled_files: list[dict[str, Any]] = []
        # target, exact pre-operation snapshot, expected resulting hash (None
        # means that the operation removed the file).
        changed: list[tuple[Path, Path | None, str | None]] = []
        rollback_root = backup_root / ".rollback"
        manifest_path = self.manifest_path(plan.app_id, manifest_id)
        emit("Preparing manifest", 0.15)
        try:
            # Stage only the already validated payload in a private temporary
            # directory. Nothing from the release archive is executed and no
            # archive path is ever used directly as a destination path.
            with tempfile.TemporaryDirectory(
                prefix="game-optimization-optiscaler-"
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

                self._assert_mutation_safe(
                    game,
                    expected_game_root=game_root,
                    expected_game_identity=game_root_identity,
                    expected_install_root=install_root,
                    expected_install_identity=install_root_identity,
                )
                for index, (item, staged) in enumerate(staged_files):
                    self._check_cancel(cancel_event)
                    target = self._target(install_root, item.target_relative_path)
                    # TOCTOU: compare the current target to the inspected plan.
                    current_exists = target.exists() or target.is_symlink()
                    if current_exists != item.replaces_existing:
                        raise OptiScalerConflictError(
                            f"target changed before installation: {item.target_relative_path}"
                        )
                    current_hash = ""
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

                    # Keep an exact rollback snapshot separate from the
                    # persistent user backup.  A managed file from the prior
                    # release must never become the "original" restored on
                    # uninstall after an update.
                    rollback: Path | None = None
                    if current_exists:
                        rollback = self._target(
                            rollback_root, item.target_relative_path
                        )
                        rollback.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, rollback, follow_symlinks=False)
                        if self._hash_file(rollback) != current_hash:
                            raise OptiScalerError(
                                f"rollback snapshot verification failed: {item.target_relative_path}"
                            )
                    changed.append((target, rollback, item.sha256))

                    previous_item = previous_installed.get(item.target_relative_path)
                    previous_expected = (
                        str(previous_item.get("after_sha256", ""))
                        if previous_item is not None else ""
                    )
                    previous_is_intact = bool(
                        previous_item is not None
                        and current_exists
                        and previous_expected
                        and current_hash == previous_expected
                    )
                    previous_is_mutable_configuration = bool(
                        previous_item is not None
                        and _is_mutable_configuration(item.target_relative_path)
                    )
                    carry_created = bool(
                        previous_item is not None
                        and item.target_relative_path in previous_created
                        and (
                            previous_is_intact
                            or previous_is_mutable_configuration
                            or not current_exists
                        )
                    )
                    carry_replaced = bool(
                        previous_item is not None
                        and item.target_relative_path in previous_replaced
                    )
                    if carry_created:
                        created.append(
                            {
                                "relative_path": item.target_relative_path,
                                "after_sha256": item.sha256,
                            }
                        )
                    elif carry_replaced:
                        if previous_backup_root is None:
                            raise OptiScalerError("existing OptiScaler backup is unavailable")
                        old = previous_replaced[item.target_relative_path]
                        backup_relative = str(
                            old.get("backup_relative_path", item.target_relative_path)
                        )
                        old_backup = self._target(previous_backup_root, backup_relative)
                        before_hash = str(old.get("before_sha256", ""))
                        if (
                            not before_hash
                            or not old_backup.is_file()
                            or self._hash_file(old_backup) != before_hash
                        ):
                            raise OptiScalerError(
                                f"existing OptiScaler backup hash mismatch: {item.target_relative_path}"
                            )
                        backup = self._target(backup_root, item.target_relative_path)
                        self._copy_atomic(old_backup, backup)
                        if self._hash_file(backup) != before_hash:
                            raise OptiScalerError(
                                f"backup lineage verification failed: {item.target_relative_path}"
                            )
                        replaced_files.append(
                            {
                                "relative_path": item.target_relative_path,
                                "before_sha256": before_hash,
                                "after_sha256": item.sha256,
                                "backup_relative_path": item.target_relative_path,
                                "mode": int(old.get("mode", 0)),
                                "mtime_ns": int(old.get("mtime_ns", 0)),
                            }
                        )
                        if current_exists and not previous_is_intact:
                            displaced = self._target(
                                backup_root / ".displaced",
                                item.target_relative_path,
                            )
                            self._copy_atomic(target, displaced)
                            if self._hash_file(displaced) != current_hash:
                                raise OptiScalerError(
                                    f"displaced file backup failed: {item.target_relative_path}"
                                )
                            displaced_files.append(
                                {
                                    "relative_path": item.target_relative_path,
                                    "sha256": current_hash,
                                    "backup_relative_path": (
                                        PurePosixPath(".displaced")
                                        / PurePosixPath(item.target_relative_path)
                                    ).as_posix(),
                                    "reason": "changed_after_previous_installation",
                                }
                            )
                    elif current_exists:
                        # This is either a first install over a user file or a
                        # user-modified file replacing a formerly managed one.
                        # Explicit confirmation makes that exact current file
                        # the new restore point, so it is never lost.
                        backup = self._target(backup_root, item.target_relative_path)
                        self._copy_atomic(target, backup)
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
                    else:
                        created.append(
                            {
                                "relative_path": item.target_relative_path,
                                "after_sha256": item.sha256,
                            }
                        )
                    self._assert_mutation_safe(
                        game,
                        expected_game_root=game_root,
                        expected_game_identity=game_root_identity,
                        expected_install_root=install_root,
                        expected_install_identity=install_root_identity,
                    )
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

                # Reconcile files managed by the previous release but no
                # longer present in the new payload.  Intact created files are
                # removed; intact replacements are restored from the original
                # first-install backup.  A file changed by somebody else is
                # deliberately left untouched and recorded for audit.
                planned_relatives = {
                    item.target_relative_path for item, _staged in staged_files
                }
                obsolete = sorted(set(previous_installed) - planned_relatives)
                for relative in obsolete:
                    self._check_cancel(cancel_event)
                    target = self._target(install_root, relative)
                    old_installed = previous_installed[relative]
                    expected = str(old_installed.get("after_sha256", ""))
                    exists = target.exists() or target.is_symlink()
                    if exists and (target.is_symlink() or not target.is_file()):
                        raise OptiScalerError(
                            f"previously managed target is no longer a regular file: {relative}"
                        )
                    current_hash = self._hash_file(target) if exists else ""
                    mutable_configuration = _is_mutable_configuration(relative)
                    if (
                        exists
                        and current_hash != expected
                        and not mutable_configuration
                    ):
                        reconciled_files.append(
                            {"relative_path": relative, "action": "preserved_foreign_change"}
                        )
                        continue
                    rollback: Path | None = None
                    if exists:
                        rollback = self._target(rollback_root, relative)
                        rollback.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, rollback, follow_symlinks=False)
                    if relative in previous_created:
                        if exists:
                            self._assert_mutation_safe(
                                game,
                                expected_game_root=game_root,
                                expected_game_identity=game_root_identity,
                                expected_install_root=install_root,
                                expected_install_identity=install_root_identity,
                            )
                            target.unlink()
                            changed.append((target, rollback, None))
                        reconciled_files.append(
                            {"relative_path": relative, "action": "removed_obsolete_managed_file"}
                        )
                        continue
                    if relative not in previous_replaced or previous_backup_root is None:
                        raise OptiScalerError(
                            f"existing OptiScaler manifest has no restore provenance for {relative}"
                        )
                    old = previous_replaced[relative]
                    backup_relative = str(old.get("backup_relative_path", relative))
                    old_backup = self._target(previous_backup_root, backup_relative)
                    before_hash = str(old.get("before_sha256", ""))
                    if (
                        not before_hash
                        or not old_backup.is_file()
                        or self._hash_file(old_backup) != before_hash
                    ):
                        raise OptiScalerError(
                            f"existing OptiScaler backup hash mismatch: {relative}"
                        )
                    changed.append((target, rollback, before_hash))
                    self._assert_mutation_safe(
                        game,
                        expected_game_root=game_root,
                        expected_game_identity=game_root_identity,
                        expected_install_root=install_root,
                        expected_install_identity=install_root_identity,
                    )
                    self._copy_atomic(old_backup, target)
                    if self._hash_file(target) != before_hash:
                        raise OptiScalerError(
                            f"restored obsolete file hash mismatch: {relative}"
                        )
                    reconciled_files.append(
                        {"relative_path": relative, "action": "restored_obsolete_replacement"}
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
                "archive_path": str(
                    archive_path_for_manifest.resolve(strict=False)
                    if archive_path_for_manifest is not None
                    else Path(plan.archive_path).resolve(strict=False)
                ),
                "archive_sha256": str(archive_sha256 or ""),
                "source_identity": str(source_identity or "local_archive"),
                "channel": str(channel or "stable"),
                "fidelityfx_upscaler_version": str(
                    fidelityfx_upscaler_version or ""
                ),
                "backup_directory": str(backup_root),
                "installed_at": installed_at.isoformat(),
                "operation": effective_operation,
                "previous_manifest_id": previous_profile.manifest_id if previous_manifest else "",
                "installed_files": installed_files,
                "created_files": created,
                "replaced_files": replaced_files,
                "displaced_files": displaced_files,
                "reconciled_files": reconciled_files,
                "detected_conflicts": [item.to_dict() for item in plan.conflicts],
            }
            _atomic_write(
                manifest_path,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            profile = OptiScalerProfile(
                schema_version=OPTISCALER_SCHEMA_VERSION,
                app_id=plan.app_id,
                enabled=True,
                executable=plan.executable,
                install_directory=plan.install_directory,
                installed_version=plan.version,
                channel=str(channel or "stable"),
                source_identity=str(source_identity or "local_archive"),
                fidelityfx_upscaler_version=str(
                    fidelityfx_upscaler_version or ""
                ),
                injection_dll=plan.injection_dll,
                proton_override=plan.proton_override,
                fsr4_mode=previous_profile.fsr4_mode,
                effective_fsr4_mode="disabled",
                automatic_reason="",
                fsr_agility_sdk_upgrade=(
                    previous_profile.fsr_agility_sdk_upgrade
                ),
                fsr4_watermark=previous_profile.fsr4_watermark,
                dx11_upscaler=previous_profile.dx11_upscaler,
                dx12_upscaler=previous_profile.dx12_upscaler,
                vulkan_upscaler=previous_profile.vulkan_upscaler,
                configuration_applied=False,
                runtime_verification_status="not_verified",
                manifest_id=manifest_id,
                installation_state="installed",
                last_verified_at=installed_at,
                updated_at=installed_at,
            )
            self.profile_repository.save(profile)
            shutil.rmtree(rollback_root, ignore_errors=True)
            emit("Completed", 1.0)
            return profile
        except Exception:
            for target, rollback, expected_result in reversed(changed):
                try:
                    current_exists = target.exists() or target.is_symlink()
                    if rollback is not None and rollback.is_file():
                        # The operation had already taken ownership of this
                        # target after a TOCTOU check.  Restore the exact
                        # snapshot even when a failed write produced a hash
                        # different from the planned result.
                        self._copy_atomic(rollback, target)
                        continue
                    safe_to_remove = bool(
                        current_exists
                        and expected_result is not None
                        and target.is_file()
                        and not target.is_symlink()
                        and self._hash_file(target) == expected_result
                    )
                    if safe_to_remove:
                        target.unlink()
                except OSError:
                    pass
            manifest_path.unlink(missing_ok=True)
            shutil.rmtree(backup_root, ignore_errors=True)
            raise

    def _assert_mutation_allowed(self, game: Game) -> None:
        root = self._canonical_game_root(game)
        if game.update_in_progress:
            raise OptiScalerError("A Steam update is currently active")
        if self._process_detector(root):
            raise OptiScalerError("The game is currently running")

    def verify(self, game: Game) -> OptiScalerVerificationResult:
        profile = self.profile_repository.load(self.game_key(game))
        if not profile.manifest_id:
            return OptiScalerVerificationResult(
                profile,
                "not_verified",
                "OptiScaler is not installed",
                None,
                False,
                False,
                None,
            )
        manifest = self._load_manifest(profile)
        install_root = Path(str(manifest["install_directory"])).resolve(strict=False)
        expected_root = self._canonical_game_root(game)
        try:
            install_root.relative_to(expected_root)
        except ValueError as error:
            raise OptiScalerError("manifest install directory is outside this game") from error
        issues: list[dict[str, Any]] = []
        installed_relatives: set[str] = set()
        payload_missing = False
        payload_corrupt = False
        payload_partial = False
        verification_error = False
        configuration_changed = False
        managed_configuration_drift = False
        replacement_before_hashes = {
            str(item.get("relative_path", "")).casefold(): str(
                item.get("before_sha256", "")
            )
            for item in manifest.get("replaced_files", [])
            if isinstance(item, Mapping)
        }
        for item in manifest.get("installed_files", []):
            if not isinstance(item, Mapping):
                verification_error = True
                issues.append(
                    {
                        "kind": "verification_error",
                        "path": "",
                        "message": "The manifest contains an invalid managed file entry",
                    }
                )
                continue
            relative = str(item.get("relative_path", ""))
            installed_relatives.add(relative.casefold())
            is_ini = Path(relative).name.casefold() == "optiscaler.ini"
            try:
                target = self._target(install_root, relative)
            except OptiScalerError as error:
                verification_error = True
                issues.append(
                    {
                        "kind": "verification_error",
                        "path": relative,
                        "message": str(error),
                    }
                )
                continue
            if target.is_symlink():
                payload_corrupt = True
                issues.append(
                    {
                        "kind": "unsafe_managed_path",
                        "path": relative,
                        "message": f"Managed file is a symbolic link: {relative}",
                    }
                )
                continue
            if not target.is_file():
                if is_ini:
                    managed_configuration_drift = True
                    issues.append(
                        {
                            "kind": "configuration_missing",
                            "path": relative,
                            "message": "Managed OptiScaler.ini is missing",
                        }
                    )
                else:
                    payload_missing = True
                    issues.append(
                        {
                            "kind": "managed_file_missing",
                            "path": relative,
                            "message": f"Managed payload file is missing: {relative}",
                        }
                    )
                continue
            try:
                observed_hash = self._hash_file(target)
            except OSError as error:
                verification_error = True
                issues.append(
                    {
                        "kind": "verification_error",
                        "path": relative,
                        "message": f"Could not read {relative}: {error}",
                    }
                )
                continue
            expected_hash = str(item.get("after_sha256", ""))
            if not expected_hash or observed_hash == expected_hash:
                continue
            if is_ini:
                try:
                    ini_text = target.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as error:
                    verification_error = True
                    issues.append(
                        {
                            "kind": "verification_error",
                            "path": relative,
                            "message": f"Could not inspect OptiScaler.ini: {error}",
                        }
                    )
                    continue
                settings = manifest.get("managed_settings", {})
                differences = managed_ini_differences(
                    ini_text, settings if isinstance(settings, Mapping) else {}
                )
                if differences:
                    managed_configuration_drift = True
                    for difference in differences:
                        issues.append(
                            {
                                "kind": "managed_configuration_drift",
                                "path": relative,
                                "message": (
                                    f"{difference['key']} is {difference['actual']}; "
                                    f"expected {difference['expected']}"
                                ),
                                **difference,
                            }
                        )
                else:
                    configuration_changed = True
                    issues.append(
                        {
                            "kind": "configuration_changed",
                            "path": relative,
                            "message": (
                                "OptiScaler.ini changed after installation, but managed "
                                "settings still match"
                            ),
                        }
                    )
                continue
            before_hash = replacement_before_hashes.get(relative.casefold(), "")
            if before_hash and observed_hash == before_hash:
                payload_partial = True
                issues.append(
                    {
                        "kind": "original_file_already_restored",
                        "path": relative,
                        "message": (
                            "The verified original game file is already restored: "
                            f"{relative}"
                        ),
                        "expectedSha256": expected_hash,
                        "observedSha256": observed_hash,
                    }
                )
                continue
            payload_corrupt = True
            issues.append(
                {
                    "kind": "managed_file_hash_mismatch",
                    "path": relative,
                    "message": f"Managed payload hash mismatch: {relative}",
                    "expectedSha256": expected_hash,
                    "observedSha256": observed_hash,
                }
            )

        backup_invalid = False
        backup_root = self.backup_root(profile.app_id, profile.manifest_id)
        for item in manifest.get("replaced_files", []):
            if not isinstance(item, Mapping):
                backup_invalid = True
                issues.append(
                    {
                        "kind": "backup_invalid",
                        "path": "",
                        "message": "The manifest contains invalid restore metadata",
                    }
                )
                continue
            relative = str(item.get("relative_path", ""))
            backup_relative = str(item.get("backup_relative_path", relative))
            expected_hash = str(item.get("before_sha256", ""))
            try:
                backup = self._target(backup_root, backup_relative)
                valid = bool(
                    expected_hash
                    and not backup.is_symlink()
                    and backup.is_file()
                    and self._hash_file(backup) == expected_hash
                )
            except (OSError, OptiScalerError):
                valid = False
            if not valid:
                backup_invalid = True
                issues.append(
                    {
                        "kind": "backup_invalid",
                        "path": relative,
                        "message": f"Original-file backup is missing or corrupt: {relative}",
                    }
                )

        unmanaged_conflicts: list[str] = []
        for proxy_name in OPTISCALER_PROXY_DLLS:
            if proxy_name.casefold() in installed_relatives:
                continue
            candidate = install_root / proxy_name
            if candidate.exists() or candidate.is_symlink():
                unmanaged_conflicts.append(proxy_name)
                issues.append(
                    {
                        "kind": "unmanaged_conflict",
                        "path": proxy_name,
                        "message": f"Unmanaged injection proxy is also present: {proxy_name}",
                    }
                )

        if verification_error:
            verification_state = "verification_error"
            summary = "Verification could not inspect every managed file"
        elif payload_missing:
            verification_state = "missing_files"
            summary = "Managed OptiScaler payload files are missing"
        elif payload_corrupt:
            verification_state = "corrupt_files"
            summary = "Managed OptiScaler payload files failed hash verification"
        elif payload_partial:
            verification_state = "partial"
            summary = "OptiScaler is partially removed; original game files are safe"
        elif backup_invalid:
            verification_state = "backup_invalid"
            summary = "Installation payload is valid, but restore backups are unavailable"
        elif managed_configuration_drift:
            verification_state = "managed_configuration_drift"
            summary = "Installation verified; managed configuration changed"
        elif configuration_changed:
            verification_state = "configuration_changed"
            summary = "Installation verified; OptiScaler.ini was changed by the user or runtime"
        elif unmanaged_conflicts:
            verification_state = "unmanaged_conflict"
            summary = "Installation verified; another unmanaged injection proxy is present"
        else:
            verification_state = "verified"
            summary = "Installation verified"

        now = datetime.now(UTC)
        payload_valid = not (
            payload_missing or payload_corrupt or payload_partial or verification_error
        )
        state = "installed" if payload_valid and profile.enabled else profile.installation_state
        if profile.enabled and payload_partial and not (
            payload_missing or payload_corrupt or verification_error
        ):
            state = "partial"
        elif profile.enabled and not payload_valid and not verification_error:
            state = "corrupt"
        manifest["last_verification"] = {
            "state": verification_state,
            "summary": summary,
            "completed": not verification_error,
            "payload_valid": payload_valid,
            "configuration_drift": configuration_changed
            or managed_configuration_drift,
            "managed_configuration_drift": managed_configuration_drift,
            "backup_valid": not backup_invalid,
            "unmanaged_conflicts": unmanaged_conflicts,
            "issues": issues,
            "verified_at": now.isoformat(),
        }
        try:
            _atomic_write(
                self.manifest_path(profile.app_id, profile.manifest_id),
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
        except OSError as error:
            raise OptiScalerError(
                f"verification completed but its result could not be saved: {error}"
            ) from error
        updated = replace(
            profile,
            installation_state=state,
            last_verified_at=now,
            updated_at=now,
        )
        self.profile_repository.save(updated)
        return OptiScalerVerificationResult(
            updated,
            verification_state,
            summary,
            payload_valid,
            configuration_changed or managed_configuration_drift,
            managed_configuration_drift,
            not backup_invalid,
            tuple(issues),
        )

    def _managed_ini_context(
        self, game: Game
    ) -> tuple[OptiScalerProfile, dict[str, Any], Path, str, bytes, str]:
        profile = self.profile_repository.load(self.game_key(game))
        if not profile.enabled or profile.installation_state != "installed":
            raise OptiScalerError("OptiScaler is not installed for this game")
        manifest = self._load_manifest(profile)
        install_root = Path(str(manifest.get("install_directory", ""))).resolve(
            strict=True
        )
        expected_root = self._canonical_game_root(game)
        try:
            install_root.relative_to(expected_root)
        except ValueError as error:
            raise OptiScalerError(
                "manifest install directory is outside this game"
            ) from error
        installed_entries = [
            item
            for item in manifest.get("installed_files", [])
            if isinstance(item, Mapping)
            and Path(str(item.get("relative_path", ""))).name.casefold()
            == "optiscaler.ini"
        ]
        if len(installed_entries) != 1:
            raise OptiScalerError("the managed OptiScaler.ini entry is unavailable")
        relative = str(installed_entries[0].get("relative_path", ""))
        target = self._target(install_root, relative)
        if target.is_symlink() or not target.is_file():
            raise OptiScalerError("the managed OptiScaler.ini is unavailable")
        try:
            original = target.read_bytes()
            text = original.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise OptiScalerError("OptiScaler.ini is not valid UTF-8") from error
        except OSError as error:
            raise OptiScalerError("the managed OptiScaler.ini is unavailable") from error
        return profile, manifest, target, relative, original, text

    def ini_capabilities(self, game: Game) -> OptiScalerIniCapabilities:
        """Inspect features advertised by the installed OptiScaler INI."""

        _profile, _manifest, _target, _relative, _original, text = (
            self._managed_ini_context(game)
        )
        return inspect_optiscaler_ini(text)

    def _apply_managed_ini(
        self,
        game: Game,
        *,
        updates: Mapping[tuple[str, str], str],
        profile_changes: Mapping[str, Any],
        settings_record: Mapping[str, Any],
    ) -> OptiScalerProfile:
        """Atomically update owned keys while preserving every other INI line."""

        self._assert_mutation_allowed(game)
        profile, manifest, target, relative, original, text = (
            self._managed_ini_context(game)
        )
        had_bom = original.startswith(b"\xef\xbb\xbf")
        rendered = update_ini_text(text, updates)
        updated_bytes = rendered.encode("utf-8")
        if had_bom:
            updated_bytes = b"\xef\xbb\xbf" + updated_bytes
        updated_hash = sha256(updated_bytes).hexdigest()
        original_manifest_path = self.manifest_path(
            profile.app_id, profile.manifest_id
        )
        try:
            original_manifest = original_manifest_path.read_bytes()
        except OSError as error:
            raise OptiScalerError("OptiScaler installation manifest is unavailable") from error
        original_hash = sha256(original).hexdigest()
        expected_hash = ""
        for item in manifest.get("installed_files", []):
            if (
                isinstance(item, Mapping)
                and str(item.get("relative_path", "")) == relative
            ):
                expected_hash = str(item.get("after_sha256", ""))
                break
        if expected_hash and expected_hash != original_hash:
            history = manifest.setdefault("managed_ini_history", [])
            if not isinstance(history, list):
                history = []
                manifest["managed_ini_history"] = history
            history.append(
                {
                    "observed_sha256": original_hash,
                    "previous_managed_sha256": expected_hash,
                    "preserved_external_settings": True,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )
            del history[:-20]
        for collection_name in (
            "installed_files", "created_files", "replaced_files"
        ):
            for item in manifest.get(collection_name, []):
                if (
                    isinstance(item, dict)
                    and str(item.get("relative_path", "")) == relative
                ):
                    item["after_sha256"] = updated_hash
        settings = manifest.setdefault("managed_settings", {})
        if not isinstance(settings, dict):
            settings = {}
            manifest["managed_settings"] = settings
        settings.update(dict(settings_record))
        manifest["managed_settings_updated_at"] = datetime.now(UTC).isoformat()
        now = datetime.now(UTC)
        updated_profile = replace(
            profile,
            **dict(profile_changes),
            configuration_applied=True,
            runtime_verification_status="not_verified",
            last_verified_at=now,
            updated_at=now,
        )
        try:
            if updated_bytes != original:
                self._copy_atomic(BytesIO(updated_bytes), target)
                if self._hash_file(target) != updated_hash:
                    raise OptiScalerError("OptiScaler.ini hash verification failed")
            _atomic_write(
                original_manifest_path,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            self.profile_repository.save(updated_profile)
        except Exception:
            try:
                self._copy_atomic(BytesIO(original), target)
                self._copy_atomic(BytesIO(original_manifest), original_manifest_path)
            except OSError:
                pass
            raise
        return updated_profile

    def configure_upscaling(
        self,
        game: Game,
        *,
        fsr4_mode: str,
        effective_fsr4_mode: str | None = None,
        automatic_reason: str = "",
        fsr_agility_sdk_upgrade: bool,
        fsr4_watermark: bool,
        dx11_upscaler: str,
        dx12_upscaler: str,
        vulkan_upscaler: str,
    ) -> OptiScalerProfile:
        """Apply the user-selected keys supported by the installed release."""

        if not isinstance(fsr_agility_sdk_upgrade, bool):
            raise OptiScalerError("FsrAgilitySDKUpgrade must be a boolean")
        if not isinstance(fsr4_watermark, bool):
            raise OptiScalerError("Fsr4EnableWatermark must be a boolean")
        _profile, manifest, _target, _relative, _original, text = (
            self._managed_ini_context(game)
        )
        capabilities = inspect_optiscaler_ini(text)
        if fsr_agility_sdk_upgrade and not any(
            "d3d12_optiscaler" in {
                part.casefold()
                for part in PurePosixPath(
                    str(item.get("relative_path", ""))
                ).parts[:-1]
            }
            for item in manifest.get("installed_files", [])
            if isinstance(item, Mapping)
        ):
            raise OptiScalerError(
                "FsrAgilitySDKUpgrade requires the D3D12_OptiScaler payload"
            )
        try:
            applied_mode = str(effective_fsr4_mode or fsr4_mode).casefold()
            updates = managed_ini_updates(
                fsr4_mode=applied_mode,
                agility_sdk_upgrade=fsr_agility_sdk_upgrade,
                watermark=fsr4_watermark,
                dx11_upscaler=dx11_upscaler,
                dx12_upscaler=dx12_upscaler,
                vulkan_upscaler=vulkan_upscaler,
                capabilities=capabilities,
            )
        except ValueError as error:
            raise OptiScalerError(str(error)) from error
        mode = str(fsr4_mode).casefold()
        profile_changes = {
            "fsr4_mode": mode,
            "effective_fsr4_mode": applied_mode,
            "automatic_reason": str(automatic_reason or ""),
            "fsr_agility_sdk_upgrade": fsr_agility_sdk_upgrade,
            "fsr4_watermark": fsr4_watermark,
            "dx11_upscaler": str(dx11_upscaler).casefold(),
            "dx12_upscaler": str(dx12_upscaler).casefold(),
            "vulkan_upscaler": str(vulkan_upscaler).casefold(),
        }
        record: dict[str, Any] = {
            "fsr4Mode": mode,
            "effectiveFsr4Mode": applied_mode,
            "automaticReason": str(automatic_reason or ""),
            "FsrAgilitySDKUpgrade": fsr_agility_sdk_upgrade,
            "Fsr4EnableWatermark": fsr4_watermark,
            "Dx11Upscaler": str(dx11_upscaler).casefold(),
            "Dx12Upscaler": str(dx12_upscaler).casefold(),
            "VulkanUpscaler": str(vulkan_upscaler).casefold(),
        }
        record.update({key: value for (_section, key), value in updates.items()})
        return self._apply_managed_ini(
            game,
            updates=updates,
            profile_changes=profile_changes,
            settings_record=record,
        )

    def configure_fsr4_update(
        self, game: Game, enabled: bool
    ) -> OptiScalerProfile:
        """Compatibility API for the former single managed FSR4 switch."""

        if not isinstance(enabled, bool):
            raise OptiScalerError("Fsr4Update must be a boolean")
        return self._apply_managed_ini(
            game,
            updates={("FSR", "Fsr4Update"): "true" if enabled else "false"},
            profile_changes={
                "fsr4_mode": "normal" if enabled else "disabled",
                "effective_fsr4_mode": "normal" if enabled else "disabled",
            },
            settings_record={"Fsr4Update": enabled},
        )

    def remove(
        self,
        game: Game,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, float], None] | None = None,
    ) -> OptiScalerProfile:
        self._assert_mutation_allowed(game)
        emit = progress or (lambda _stage, _value: None)
        profile = self.profile_repository.load(self.game_key(game))
        manifest = self._load_manifest(profile)
        install_root = Path(str(manifest["install_directory"])).resolve(strict=True)
        game_root = self._canonical_game_root(game)
        try:
            install_root.relative_to(game_root)
        except ValueError as error:
            raise OptiScalerError(
                "manifest install directory is outside this game"
            ) from error
        backup_root = self.backup_root(profile.app_id, profile.manifest_id)
        manifest_path = self.manifest_path(profile.app_id, profile.manifest_id)
        try:
            original_manifest = manifest_path.read_bytes()
        except OSError as error:
            raise OptiScalerError(
                f"could not snapshot the active OptiScaler manifest: {error}"
            ) from error
        created = [
            item
            for item in manifest.get("created_files", [])
            if isinstance(item, Mapping)
        ]
        replacements = [
            item
            for item in manifest.get("replaced_files", [])
            if isinstance(item, Mapping)
        ]
        # Preflight every blocking conflict and every restore backup before
        # mutating the game directory. Mutable OptiScaler.ini content is
        # deliberately allowed to drift; executable payloads are not.
        actions: list[dict[str, Any]] = []
        emit("Checking removal safety", 0.08)
        for item in created:
            self._check_cancel(cancel_event)
            relative = str(item.get("relative_path", ""))
            target = self._target(install_root, relative)
            exists = target.exists() or target.is_symlink()
            if exists and (target.is_symlink() or not target.is_file()):
                raise OptiScalerConflictError(
                    f"managed path is not a regular file and was not removed: {relative}"
                )
            observed_hash = self._hash_file(target) if exists else ""
            expected_hash = str(item.get("after_sha256", ""))
            mutable = _is_mutable_configuration(relative)
            if exists and observed_hash != expected_hash and not mutable:
                raise OptiScalerConflictError(
                    f"modified immutable managed file blocks removal: {relative}"
                )
            actions.append(
                {
                    "action": "remove",
                    "relative": relative,
                    "target": target,
                    "observed_hash": observed_hash,
                    "mutable": mutable,
                    "preserve": bool(
                        mutable and exists and observed_hash != expected_hash
                    ),
                }
            )
        for item in replacements:
            self._check_cancel(cancel_event)
            relative = str(item.get("relative_path", ""))
            target = self._target(install_root, relative)
            exists = target.exists() or target.is_symlink()
            if exists and (target.is_symlink() or not target.is_file()):
                raise OptiScalerConflictError(
                    f"managed path is not a regular file and was not restored: {relative}"
                )
            observed_hash = self._hash_file(target) if exists else ""
            expected_hash = str(item.get("after_sha256", ""))
            mutable = _is_mutable_configuration(relative)
            backup_relative = str(item.get("backup_relative_path", relative))
            backup = self._target(backup_root, backup_relative)
            before_hash = str(item.get("before_sha256", ""))
            if (
                not before_hash
                or backup.is_symlink()
                or not backup.is_file()
                or self._hash_file(backup) != before_hash
            ):
                raise OptiScalerError(
                    f"original-file backup is missing or corrupt: {relative}"
                )
            already_restored = bool(
                exists and before_hash and observed_hash == before_hash
            )
            if (
                exists
                and observed_hash != expected_hash
                and not mutable
                and not already_restored
            ):
                raise OptiScalerConflictError(
                    f"modified immutable managed file blocks restoration: {relative}"
                )
            actions.append(
                {
                    "action": "already_restored" if already_restored else "restore",
                    "relative": relative,
                    "target": target,
                    "backup": backup,
                    "before_hash": before_hash,
                    "observed_hash": observed_hash,
                    "mutable": mutable,
                    "preserve": bool(
                        mutable and exists and observed_hash != expected_hash
                    ),
                }
            )

        self._check_cancel(cancel_event)
        preserved_records: list[dict[str, str]] = []
        preservation_root = (
            backup_root
            / ".preserved-configurations"
            / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        )
        rollback_errors: list[str] = []
        total = max(1, len(actions))
        backup_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".remove-rollback-", dir=backup_root
        ) as rollback_directory:
            rollback_root = Path(rollback_directory)
            snapshots: list[tuple[Path, Path | None]] = []
            try:
                emit("Preparing removal rollback", 0.15)
                for action in actions:
                    target = action["target"]
                    relative = str(action["relative"])
                    exists = target.exists() or target.is_symlink()
                    observed_hash = str(action["observed_hash"])
                    if exists != bool(observed_hash):
                        raise OptiScalerConflictError(
                            f"managed file changed during removal preflight: {relative}"
                        )
                    snapshot: Path | None = None
                    if exists:
                        if (
                            target.is_symlink()
                            or not target.is_file()
                            or self._hash_file(target) != observed_hash
                        ):
                            raise OptiScalerConflictError(
                                f"managed file changed during removal preflight: {relative}"
                            )
                        snapshot = self._target(rollback_root, relative)
                        self._copy_atomic(target, snapshot)
                        if self._hash_file(snapshot) != observed_hash:
                            raise OptiScalerError(
                                f"removal rollback snapshot failed: {relative}"
                            )
                        if bool(action["preserve"]):
                            preserved = self._target(preservation_root, relative)
                            self._copy_atomic(target, preserved)
                            preserved_records.append(
                                {
                                    "relative_path": relative,
                                    "sha256": observed_hash,
                                    "preserved_path": str(preserved),
                                }
                            )
                    snapshots.append((target, snapshot))

                emit("Removing OptiScaler", 0.3)
                for index, action in enumerate(actions):
                    self._check_cancel(cancel_event)
                    target = action["target"]
                    relative = str(action["relative"])
                    if action["action"] == "remove":
                        if target.exists():
                            target.unlink()
                    elif action["action"] == "restore":
                        self._copy_atomic(action["backup"], target)
                        if self._hash_file(target) != action["before_hash"]:
                            raise OptiScalerError(
                                f"restored original file hash mismatch: {relative}"
                            )
                    emit(
                        "Removing OptiScaler",
                        0.3 + 0.55 * (index + 1) / total,
                    )

                now = datetime.now(UTC)
                manifest["last_removal"] = {
                    "state": "removed",
                    "removed_at": now.isoformat(),
                    "preserved_configurations": preserved_records,
                }
                if preserved_records:
                    history = manifest.setdefault("preserved_configurations", [])
                    if isinstance(history, list):
                        history.extend(preserved_records)
                        del history[:-20]
                _atomic_write(
                    manifest_path,
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                )
                updated = replace(
                    profile,
                    enabled=False,
                    installation_state="removed",
                    configuration_applied=False,
                    last_verified_at=now,
                    updated_at=now,
                )
                self.profile_repository.save(updated)
            except Exception as error:
                for target, snapshot in reversed(snapshots):
                    try:
                        if snapshot is None:
                            if target.exists() or target.is_symlink():
                                if target.is_symlink() or not target.is_file():
                                    raise OSError("rollback target is not a regular file")
                                target.unlink()
                        else:
                            self._copy_atomic(snapshot, target)
                    except Exception as rollback_error:
                        rollback_errors.append(
                            f"{target.name}: {rollback_error or type(rollback_error).__name__}"
                        )
                try:
                    self._copy_atomic(BytesIO(original_manifest), manifest_path)
                except Exception as rollback_error:
                    rollback_errors.append(f"manifest: {rollback_error}")
                if rollback_errors:
                    partial_now = datetime.now(UTC)
                    manifest["last_removal"] = {
                        "state": "partial",
                        "failed_at": partial_now.isoformat(),
                        "error": str(error) or type(error).__name__,
                        "rollback_errors": rollback_errors,
                    }
                    try:
                        _atomic_write(
                            manifest_path,
                            json.dumps(
                                manifest,
                                ensure_ascii=False,
                                indent=2,
                                sort_keys=True,
                            )
                            + "\n",
                        )
                        self.profile_repository.save(
                            replace(
                                profile,
                                enabled=True,
                                installation_state="partial",
                                updated_at=partial_now,
                            )
                        )
                    except Exception:
                        pass
                    raise OptiScalerError(
                        "OptiScaler removal failed and rollback was incomplete: "
                        f"{str(error) or type(error).__name__}; "
                        + "; ".join(rollback_errors)
                    ) from error
                shutil.rmtree(preservation_root, ignore_errors=True)
                raise
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
        emit = progress or (lambda _stage, _value: None)
        profile = self.profile_repository.load(self.game_key(game))
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
        try:
            game_key = self.game_key(game)
        except OptiScalerError as error:
            return {"success": False, "error": str(error)}
        profile = self.profile_repository.load(game_key)
        resolution = self.executable_resolver.resolve(game, profile.executable)
        selected = resolution.selected
        manifest: dict[str, Any] = {}
        manifest_error = ""
        if profile.manifest_id:
            try:
                manifest = self._load_manifest(profile)
            except OptiScalerError as error:
                manifest_error = str(error)
        legacy_settings = manifest.get("managed_settings", {})
        if (
            not profile.configuration_applied
            and isinstance(legacy_settings, Mapping)
            and isinstance(legacy_settings.get("Fsr4Update"), bool)
        ):
            legacy_enabled = bool(legacy_settings["Fsr4Update"])
            profile = replace(
                profile,
                fsr4_mode="normal" if legacy_enabled else "disabled",
                effective_fsr4_mode=(
                    "normal" if legacy_enabled else "disabled"
                ),
                configuration_applied=True,
            )
            self.profile_repository.save(profile)
        empty_capabilities = OptiScalerIniCapabilities(
            False, "none", False, False, (), (), ()
        )
        capabilities = empty_capabilities
        ini_state = inspect_optiscaler_ini_state("")
        ini_error = ""
        if profile.enabled and manifest:
            try:
                install_root = Path(
                    str(manifest.get("install_directory", ""))
                ).resolve(strict=True)
                ini_entries = [
                    item
                    for item in manifest.get("installed_files", [])
                    if isinstance(item, Mapping)
                    and Path(str(item.get("relative_path", ""))).name.casefold()
                    == "optiscaler.ini"
                ]
                if len(ini_entries) == 1:
                    ini_target = self._target(
                        install_root, str(ini_entries[0].get("relative_path", ""))
                    )
                    ini_text = ini_target.read_text(encoding="utf-8-sig")
                    capabilities = inspect_optiscaler_ini(ini_text)
                    ini_state = inspect_optiscaler_ini_state(ini_text)
            except (OSError, UnicodeError, OptiScalerError) as error:
                ini_error = str(error)
        installed_files = list(manifest.get("installed_files", []))
        verification = manifest.get("last_verification", {})
        if not isinstance(verification, Mapping):
            verification = {}
        if manifest_error:
            verification = {
                "state": "verification_error",
                "summary": f"Verification unavailable: {manifest_error}",
                "completed": False,
                "issues": [
                    {
                        "kind": "verification_error",
                        "path": "",
                        "message": manifest_error,
                    }
                ],
            }
        fidelityfx_assets = [
            str(item.get("relative_path", ""))
            for item in installed_files
            if isinstance(item, Mapping)
            and Path(str(item.get("relative_path", ""))).name.casefold()
            in {
                "amd_fidelityfx_upscaler_dx12.dll",
                "amd_fidelityfx_dx12.dll",
                "amd_fidelityfx_framegeneration_dx12.dll",
            }
        ]
        has_agility_payload = any(
            "d3d12_optiscaler" in {
                part.casefold()
                for part in PurePosixPath(
                    str(item.get("relative_path", ""))
                ).parts[:-1]
            }
            for item in installed_files
            if isinstance(item, Mapping)
        )
        if capabilities.agility_sdk_upgrade and not has_agility_payload:
            capabilities = replace(capabilities, agility_sdk_upgrade=False)
        # No upstream log parser is implemented here.  Profile/config state is
        # never accepted as runtime evidence; the watermark must be checked in
        # the running game.
        runtime_verification_status = "not_verified"
        verification_labels = {
            "not_verified": "Runtime verification required",
            "verified_fsr4": "Verified FSR4",
            "verified_fsr4_int8": "Verified FSR4-I8",
            "verified_fsr3_fallback": "Verified FSR3 fallback",
        }
        installed_source_identity = (
            profile.source_identity
            or str(manifest.get("source_identity", ""))
        )
        configured_mode = ini_state.fsr4_mode
        has_effective_configuration = bool(
            profile.enabled
            and manifest
            and configured_mode != "unknown"
        )
        requested_mode = profile.fsr4_mode
        expected_mode = (
            profile.effective_fsr4_mode
            if profile.configuration_applied
            and requested_mode == "automatic"
            and profile.effective_fsr4_mode != "disabled"
            else requested_mode
        )
        configuration_matches_requested = bool(
            has_effective_configuration
            and (
                configured_mode == expected_mode
                or requested_mode == "automatic"
                and configured_mode == "automatic"
            )
        )
        data = profile.to_dict()
        data.update(
            {
                "success": True,
                "appId": profile.app_id,
                "installationState": profile.installation_state,
                "installed": bool(
                    profile.enabled
                    and profile.manifest_id
                    and profile.installation_state
                    in {"installed", "partial"}
                ),
                "installedVersion": profile.installed_version,
                "injectionDll": profile.injection_dll,
                "protonOverride": profile.proton_override,
                "manifestId": profile.manifest_id,
                "manifestPath": (
                    str(self.manifest_path(profile.app_id, profile.manifest_id))
                    if profile.manifest_id else ""
                ),
                "manifestError": manifest_error,
                "executable": profile.executable,
                "installDirectory": profile.install_directory,
                "executableStatus": resolution.status,
                "executableConfidence": resolution.status,
                "selectedExecutable": selected.to_dict() if selected else {},
                "executableCandidates": [item.to_dict() for item in resolution.candidates],
                "executableMessage": resolution.message,
                "installedFiles": installed_files,
                "replacedFiles": list(manifest.get("replaced_files", [])),
                "createdFiles": list(manifest.get("created_files", [])),
                "displacedFiles": list(manifest.get("displaced_files", [])),
                "reconciledFiles": list(manifest.get("reconciled_files", [])),
                "installOperation": str(manifest.get("operation", "")),
                "channel": profile.channel,
                "installedChannel": str(
                    manifest.get("channel", profile.channel)
                ),
                "sourceIdentity": installed_source_identity,
                "sourceLabel": (
                    "Official optiscaler/OptiScaler release"
                    if installed_source_identity == "official_optiscaler"
                    else "Local archive"
                    if installed_source_identity == "local_archive"
                    else "Unknown"
                ),
                "fidelityFxUpscalerVersion": (
                    profile.fidelityfx_upscaler_version
                    or str(manifest.get("fidelityfx_upscaler_version", ""))
                ),
                "fidelityFxAssets": fidelityfx_assets,
                "fsr4AssetsInstalled": any(
                    Path(path).name.casefold()
                    == "amd_fidelityfx_upscaler_dx12.dll"
                    for path in fidelityfx_assets
                ),
                "fsr4Mode": (
                    ini_state.fsr4_mode
                    if ini_state.fsr4_mode != "unknown"
                    else profile.fsr4_mode
                ),
                "requestedFsr4Mode": requested_mode,
                "effectiveConfiguredFsr4Mode": configured_mode,
                "effectiveFsr4Mode": (
                    ini_state.fsr4_mode
                    if ini_state.fsr4_mode != "unknown"
                    else profile.effective_fsr4_mode
                ),
                "automaticReason": profile.automatic_reason,
                "fsrAgilitySdkUpgrade": (
                    ini_state.agility_sdk_upgrade == "true"
                    if ini_state.agility_sdk_upgrade != "unknown"
                    else profile.fsr_agility_sdk_upgrade
                ),
                "fsrAgilitySdkUpgradeState": ini_state.agility_sdk_upgrade,
                "fsr4Watermark": (
                    ini_state.watermark == "true"
                    if ini_state.watermark != "unknown"
                    else profile.fsr4_watermark
                ),
                "requestedFsr4Watermark": profile.fsr4_watermark,
                "fsr4WatermarkState": ini_state.watermark,
                "dx11Upscaler": ini_state.dx11_upscaler or profile.dx11_upscaler,
                "dx12Upscaler": ini_state.dx12_upscaler or profile.dx12_upscaler,
                "vulkanUpscaler": (
                    ini_state.vulkan_upscaler or profile.vulkan_upscaler
                ),
                "configurationApplied": has_effective_configuration,
                "profileConfigurationApplied": profile.configuration_applied,
                "configurationMatchesRequested": (
                    configuration_matches_requested
                ),
                "runtimeVerificationStatus": (
                    runtime_verification_status
                ),
                "runtimeVerificationLabel": verification_labels[
                    runtime_verification_status
                ],
                "runtimeVerified": runtime_verification_status
                in {"verified_fsr4", "verified_fsr4_int8"},
                "iniCapabilities": capabilities.to_dict(),
                "effectiveIniState": ini_state.to_dict(),
                "iniCapabilityError": ini_error,
                "managedSettings": dict(
                    manifest.get("managed_settings", {})
                    if isinstance(manifest.get("managed_settings", {}), Mapping)
                    else {}
                ),
                "installationVerificationState": str(
                    verification.get("state", "not_verified")
                ),
                "installationVerificationSummary": str(
                    verification.get("summary", "Installation has not been verified")
                ),
                "installationVerificationCompleted": bool(
                    verification.get("completed", False)
                ),
                "installationPayloadValid": (
                    bool(verification.get("payload_valid"))
                    if "payload_valid" in verification else None
                ),
                "configurationDrift": bool(
                    verification.get("configuration_drift", False)
                ),
                "managedConfigurationDrift": bool(
                    verification.get("managed_configuration_drift", False)
                ),
                "backupValid": (
                    bool(verification.get("backup_valid"))
                    if "backup_valid" in verification else None
                ),
                "verificationIssues": list(
                    verification.get("issues", [])
                    if isinstance(verification.get("issues", []), list)
                    else []
                ),
                "lastVerifiedAt": (
                    profile.last_verified_at.astimezone(UTC).isoformat()
                    if profile.last_verified_at else ""
                ),
            }
        )
        return data


__all__ = [
    "INSTALL_OPERATIONS",
    "MANIFEST_FORMAT_VERSION",
    "OptiScalerCancelled",
    "OptiScalerConflict",
    "OptiScalerConflictError",
    "OptiScalerError",
    "OptiScalerVerificationResult",
    "OptiScalerFilePlan",
    "OptiScalerInstallPlan",
    "OptiScalerProfileRepository",
    "OptiScalerService",
    "merge_wine_dll_overrides",
]
