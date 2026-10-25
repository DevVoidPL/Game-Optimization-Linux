"""Per-game DLSS Enabler backend, switched reversibly with OptiScaler.

DLSS Enabler v0.9.4 bundles its own OptiScaler 0.9.4 and claims
``version.dll`` (and ``dxgi.dll`` on AMD/Intel), so it cannot run next to a
managed OptiScaler v10 / FSR 4 INT8 Lite installation.  The backend is
exclusive:

* **enable** parks every file recorded in the active OptiScaler manifest
  (Lite / Cyboman files included), puts back game files OptiScaler had
  replaced, moves aside unmanaged files that have an Enabler name, copies the
  pinned Enabler files and saves the OptiScaler profile byte for byte;
* **disable** undoes exactly those steps in reverse and restores the profile.

Every step is journaled before it runs, so an interrupted switch in either
direction is recovered by finishing the same undo.  Pins: dlss_enabler_pins.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import errno
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Callable, Mapping, Sequence

from ..models import Game
from .dlss_enabler_pins import (
    DLSS_ENABLER_MODULE_VERSION,
    DLSS_ENABLER_OPTISCALER_VERSION,
    DLSS_ENABLER_SOURCE_URL,
    DLSS_ENABLER_VERSION,
    RAW_FILES,
    UPSCALER_ARTIFACT,
    EnablerFile,
    build_enabler_files,
    needs_amd_intel_support,
    wine_overrides,
)
from .fsr_variants import FsrArtifact, FsrArtifactClient, PinnedArchive, _hash_file
from .mangohud import _atomic_write
from .optiscaler_fsr4 import update_ini_text
from .optiscaler import (
    OptiScalerConflictError,
    OptiScalerError,
    OptiScalerService,
    _is_mutable_configuration,
    anti_cheat_matches,
)

logger = logging.getLogger(__name__)
STATE_FORMAT_VERSION = 1
MUTABLE_ENABLER_FILES = frozenset({"nvngx.ini"})
DEFAULT_CONFIGURATION = {
    "dx12Upscaler": "auto",
    "frameGenerator": "auto",
    "reflex": "on",
    "reflexEmulation": "auto",
    "framerateLimit": "off",
    "frameGenerationMode": "auto",
    "logging": True,
    "logLevel": "2",
}


class DlssEnablerError(OptiScalerError):
    pass


def move_file(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise DlssEnablerError(f"switch target already exists: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        temporary = target.with_name(f".{target.name}.gameopti-move")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
        source.unlink()


def _is_mutable(relative: str) -> bool:
    name = PurePosixPath(relative).name.casefold()
    return name in MUTABLE_ENABLER_FILES or _is_mutable_configuration(relative)


class DlssEnablerService:
    def __init__(
        self,
        optiscaler: OptiScalerService,
        client: FsrArtifactClient,
        *,
        raw_files: Mapping[str, PinnedArchive] | None = None,
        upscaler: FsrArtifact | None = None,
        mover: Callable[[Path, Path], None] = move_file,
        proc_root: Path = Path("/proc"),
        host_process_modules: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self.optiscaler = optiscaler
        self.client = client
        self.raw_files = dict(raw_files or RAW_FILES)
        self.upscaler = upscaler or UPSCALER_ARTIFACT
        self.files: tuple[EnablerFile, ...] = build_enabler_files(self.raw_files, self.upscaler)
        self._mover = mover
        self.proc_root = Path(proc_root)
        self._host_process_modules = host_process_modules

    # -- storage ----------------------------------------------------------
    def _root(self, app_id: str) -> Path:
        return self.optiscaler.data_root / app_id / "dlss-enabler"

    def _state_path(self, app_id: str) -> Path:
        return self._root(app_id) / "state.json"

    def load_state(self, app_id: str) -> dict[str, Any] | None:
        path = self._state_path(app_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DlssEnablerError(f"could not read the DLSS Enabler state: {error}") from error
        if not isinstance(raw, dict) or raw.get("format") != STATE_FORMAT_VERSION:
            raise DlssEnablerError("unsupported DLSS Enabler state file")
        return raw

    def _save_state(self, app_id: str, state: Mapping[str, Any]) -> None:
        _atomic_write(
            self._state_path(app_id),
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def _bases(self, app_id: str, install_dir: Path) -> dict[str, Path]:
        root = self._root(app_id)
        return {
            "game": install_dir,
            "park": root / "parked-optiscaler",
            "displaced": root / "displaced",
            "preserved": root / "preserved",
        }

    def _path(self, bases: Mapping[str, Path], tagged: str) -> Path:
        base, _sep, relative = str(tagged).partition("/")
        if base not in bases or not relative:
            raise DlssEnablerError("invalid DLSS Enabler journal entry")
        return self.optiscaler._target(bases[base], relative)

    # -- artifacts --------------------------------------------------------
    def ensure_artifacts(
        self, progress: Callable[[str, float], None] | None = None
    ) -> dict[str, Path]:
        emit = progress or (lambda _stage, _value: None)
        sources: dict[str, Path] = {}
        emit("Downloading DLSS Enabler files", 0.05)
        for key, pinned in self.raw_files.items():
            sources[f"raw:{key}"] = self.client._download(pinned)
        emit("Downloading OptiScaler 0.9.4 for DLSS Enabler", 0.2)
        for name, path in self.client.ensure(self.upscaler).items():
            sources[f"up:{name}"] = path
        for item in self.files:
            path = sources.get(item.source_key)
            if path is None or path.stat().st_size != item.size or _hash_file(path) != item.sha256:
                raise DlssEnablerError(
                    f"pinned DLSS Enabler file failed verification: {item.target}"
                )
        return sources

    # -- planning (read-only) ---------------------------------------------
    def _install_dir(self, game: Game, profile: Any) -> tuple[Path, dict[str, Any] | None]:
        """Directory of the selected executable and the active OptiScaler manifest."""

        service = self.optiscaler
        if (
            profile.enabled
            and profile.manifest_id
            and profile.installation_state in {"installed", "partial", "corrupt"}
        ):
            manifest = service._load_manifest(profile)
            return service._install_root_for_change(game, manifest), manifest
        root = service._canonical_game_root(game)
        candidate, _confidence = service._selected_candidate(
            game, service.executable_resolver, profile.executable
        )
        return service._target(root, candidate.relative_path).parent, None

    def _plan_optiscaler(
        self, profile: Any, manifest: Mapping[str, Any], install_dir: Path
    ) -> list[dict[str, Any]]:
        """Park every recorded OptiScaler file; put replaced originals back.

        A managed executable whose hash no longer matches blocks the switch,
        so an unknown modification is never parked silently.
        """

        service = self.optiscaler
        steps: list[dict[str, Any]] = []
        for item in manifest.get("installed_files", []):
            if not isinstance(item, Mapping):
                continue
            relative = str(item.get("relative_path", ""))
            target = service._target(install_dir, relative)
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or not target.is_file():
                raise OptiScalerConflictError(f"managed path is not a regular file: {relative}")
            digest = _hash_file(target)
            if digest != str(item.get("after_sha256", "")) and not _is_mutable(relative):
                raise OptiScalerConflictError(
                    f"modified managed OptiScaler file blocks the switch: {relative}"
                )
            steps.append({"kind": "move", "from": f"game/{relative}",
                          "to": f"park/{relative}", "sha256": digest})
        backup_root = service.backup_root(profile.app_id, profile.manifest_id)
        for item in manifest.get("replaced_files", []):
            if not isinstance(item, Mapping):
                continue
            relative = str(item.get("relative_path", ""))
            backup = service._target(backup_root, str(item.get("backup_relative_path", relative)))
            before = str(item.get("before_sha256", ""))
            if not backup.is_file() or _hash_file(backup) != before:
                raise OptiScalerError(f"original-file backup is missing or corrupt: {relative}")
            steps.append({"kind": "copy_original", "source": str(backup),
                          "to": f"game/{relative}", "sha256": before})
        return steps

    def _plan_enabler(
        self, install_dir: Path, amd_intel: bool, sources: Mapping[str, Path],
        planned: list[dict[str, Any]], native_dlss: Path | None = None,
    ) -> list[dict[str, Any]]:
        service = self.optiscaler
        freed = {
            str(step["from"]).casefold() for step in planned if step["kind"] == "move"
        }
        restored = {str(step["to"]).casefold() for step in planned if step["kind"] == "copy_original"}
        steps: list[dict[str, Any]] = []
        for item in self.files:
            if item.amd_intel_only and not amd_intel:
                continue
            # Upstream's proxy loaders are exclusive.  Loading the same main
            # module as both version.dll and dlss-enabler.dll would hook twice.
            if amd_intel and item.target == "version.dll":
                continue
            tagged = f"game/{item.target}"
            target = service._target(install_dir, item.target)
            exists = target.exists() or target.is_symlink()
            if (exists and tagged.casefold() not in freed) or tagged.casefold() in restored:
                if exists and (target.is_symlink() or not target.is_file()):
                    raise OptiScalerConflictError(f"existing path blocks DLSS Enabler: {item.target}")
                # An unmanaged file with an Enabler name (another mod, or a
                # game original): kept aside and put back on switching back.
                steps.append({"kind": "move", "from": tagged,
                              "to": f"displaced/{item.target}", "sha256": "",
                              "after_restore": tagged.casefold() in restored})
            steps.append({"kind": "copy_enabler", "source": str(sources[item.source_key]),
                          "to": tagged, "sha256": item.sha256})
        if amd_intel:
            if native_dlss is None:
                raise DlssEnablerError("the game's native nvngx_dlss.dll was not found")
            runtime_hash = _hash_file(native_dlss)
            tagged = "game/_nvngx.dll"
            target = service._target(install_dir, "_nvngx.dll")
            exists = target.exists() or target.is_symlink()
            if (exists and tagged.casefold() not in freed) or tagged.casefold() in restored:
                if exists and (target.is_symlink() or not target.is_file()):
                    raise OptiScalerConflictError("existing path blocks DLSS Enabler: _nvngx.dll")
                steps.append({"kind": "move", "from": tagged,
                              "to": "displaced/_nvngx.dll", "sha256": "",
                              "after_restore": tagged.casefold() in restored})
            # The official 0.9.4 installer first writes a small placeholder and
            # then dlss-finder.exe replaces it with the game's real DLSS DLL.
            # Copying the already resolved file is equivalent, deterministic
            # and keeps the operation inside the normal rollback journal.
            steps.append({"kind": "copy_runtime", "source": str(native_dlss),
                          "to": tagged, "sha256": runtime_hash})
        return steps

    def _native_dlss(self, game: Game) -> Path:
        """Resolve the game-owned NVIDIA DLSS runtime without following links."""

        root = self.optiscaler._canonical_game_root(game)
        candidates: list[Path] = []
        for directory, names, files in os.walk(root, followlinks=False):
            base = Path(directory)
            names[:] = [name for name in names if not (base / name).is_symlink()]
            for name in files:
                if name.casefold() != "nvngx_dlss.dll":
                    continue
                path = base / name
                if path.is_file() and not path.is_symlink():
                    candidates.append(path)
        if not candidates:
            raise DlssEnablerError(
                "DLSS Enabler AMD/Intel support requires the game's native nvngx_dlss.dll"
            )
        by_hash: dict[str, list[Path]] = {}
        for candidate in candidates:
            by_hash.setdefault(_hash_file(candidate), []).append(candidate)
        if len(by_hash) > 1:
            relative = ", ".join(
                str(path.relative_to(root)) for path in sorted(candidates)
            )
            raise DlssEnablerError(
                f"multiple different native nvngx_dlss.dll files were found: {relative}"
            )
        return sorted(candidates, key=lambda path: (len(path.parts), str(path)))[0]

    @staticmethod
    def _configuration_updates(values: Mapping[str, Any]) -> dict[tuple[str, str], str]:
        return {
            ("Upscalers", "Dx12Upscaler"): str(values["dx12Upscaler"]),
            ("FrameGeneration", "Generator"): str(values["frameGenerator"]),
            ("FrameGeneration", "Reflex"): str(values["reflex"]),
            ("FrameGeneration", "ReflexEmulation"): str(values["reflexEmulation"]),
            ("FrameGeneration", "FramerateLimit"): str(values["framerateLimit"]),
            ("FrameGeneration", "FrameGenerationMode"): str(values["frameGenerationMode"]),
            ("Log", "LoggingEnabled"): "true" if values["logging"] else "false",
            ("Log", "LogToFile"): "true" if values["logging"] else "false",
            ("Log", "LogLevel"): str(values["logLevel"]),
        }

    @staticmethod
    def _validated_configuration(values: Mapping[str, Any]) -> dict[str, Any]:
        merged = {**DEFAULT_CONFIGURATION, **dict(values)}
        allowed = {
            "dx12Upscaler": {"auto", "fsr22", "fsr31", "xess"},
            "frameGenerator": {"auto", "fsr3"},
            "reflex": {"on", "boost", "off"},
            "reflexEmulation": {"auto", "on", "off"},
            "frameGenerationMode": {"auto", "dynamic"},
            "logLevel": {"0", "1", "2", "3", "4"},
        }
        normalized: dict[str, Any] = {}
        for key, choices in allowed.items():
            value = str(merged.get(key, "")).strip().casefold()
            if value not in choices:
                raise DlssEnablerError(f"unsupported DLSS Enabler setting: {key}={value}")
            normalized[key] = value
        logging_value = merged.get("logging")
        if not isinstance(logging_value, bool):
            raise DlssEnablerError("DLSS Enabler logging setting must be a boolean")
        normalized["logging"] = logging_value
        limit = str(merged.get("framerateLimit", "off")).strip().casefold()
        if limit not in {"off", "vsync"}:
            try:
                numeric_limit = int(limit)
            except ValueError as error:
                raise DlssEnablerError("frame-rate limit must be off, vsync or 20-360") from error
            if not 20 <= numeric_limit <= 360:
                raise DlssEnablerError("frame-rate limit must be off, vsync or 20-360")
            limit = str(numeric_limit)
        normalized["framerateLimit"] = limit
        return normalized

    def _write_configuration(
        self, app_id: str, state: dict[str, Any], values: Mapping[str, Any]
    ) -> dict[str, Any]:
        configuration = self._validated_configuration({**state.get("configuration", {}), **values})
        ini = self.optiscaler._target(Path(state["install_dir"]), "nvngx.ini")
        if not ini.is_file() or ini.is_symlink():
            raise DlssEnablerError("DLSS Enabler nvngx.ini is unavailable")
        original = ini.read_bytes().decode("utf-8")
        text = original.removeprefix("\ufeff")
        # update_ini_text preserves unknown content, but when a whole section
        # is absent it appends one section per missing key.  Seed the one
        # Enabler-only section as a unit.  Also coalesce the adjacent duplicate
        # headers written by builds prior to this fix without dropping values.
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        has_frame_generation = False
        normalized_lines: list[str] = []
        current_section = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().casefold()
                if section == "framegeneration":
                    has_frame_generation = True
                    if current_section == section:
                        continue
                current_section = section
            normalized_lines.append(line)
        text = newline.join(normalized_lines) + (newline if text.endswith(("\n", "\r")) else "")
        if not has_frame_generation:
            if text and not text.endswith(newline):
                text += newline
            text += newline + "[FrameGeneration]" + newline
        updates = self._configuration_updates(configuration)
        # Seed absent sections once so all keys land under the same header.
        present = {line.strip().casefold() for line in text.splitlines()
                   if line.strip().startswith("[")}
        for section in dict.fromkeys(section for section, _key in updates):
            if f"[{section}]".casefold() not in present:
                text += newline + f"[{section}]" + newline
        rendered = update_ini_text(text, updates)
        if original.startswith("\ufeff"):
            rendered = "\ufeff" + rendered
        updated_state = {**state, "configuration": configuration,
                         "configuration_updated_at": datetime.now(UTC).isoformat()}
        try:
            _atomic_write(ini, rendered)
            self._save_state(app_id, updated_state)
        except BaseException:
            _atomic_write(ini, original)
            raise
        state.update(updated_state)
        return configuration

    # -- journal ----------------------------------------------------------
    # ``done`` is advanced *before* a step runs and every undo is tolerant of
    # a step that never ran, so a crash at any point is recoverable.
    def _run_step(self, bases: Mapping[str, Path], step: Mapping[str, Any]) -> None:
        if step["kind"] == "move":
            self._mover(self._path(bases, step["from"]), self._path(bases, step["to"]))
            return
        target = self._path(bases, step["to"])
        if target.exists() or target.is_symlink():
            raise DlssEnablerError(f"switch target already exists: {target.name}")
        self.optiscaler._copy_atomic(Path(step["source"]), target)
        if _hash_file(target) != step["sha256"]:
            raise DlssEnablerError(f"copied file hash mismatch: {target.name}")

    def _undo_step(self, bases: Mapping[str, Path], step: Mapping[str, Any]) -> None:
        if step["kind"] == "move":
            source = self._path(bases, step["from"])
            moved = self._path(bases, step["to"])
            if moved.exists() or moved.is_symlink():
                if source.exists() or source.is_symlink():
                    raise OptiScalerConflictError(
                        f"file changed outside GameOpti blocks the switch: {step['from'].partition('/')[2]}"
                    )
                self._mover(moved, source)
            return
        target = self._path(bases, step["to"])
        if not target.exists() and not target.is_symlink():
            return
        relative = str(step["to"]).partition("/")[2]
        if target.is_symlink() or not target.is_file():
            raise OptiScalerConflictError(f"managed path is not a regular file: {relative}")
        if _hash_file(target) != step["sha256"]:
            if step["kind"] == "copy_enabler" and _is_mutable(relative):
                # An edited nvngx.ini is kept for the user, never discarded.
                kept = self._path(bases, f"preserved/{relative}")
                kept.unlink(missing_ok=True)
                self._mover(target, kept)
                return
            raise OptiScalerConflictError(
                f"file changed outside GameOpti blocks the switch: {relative}"
            )
        target.unlink()

    def _undo(self, app_id: str, state: dict[str, Any]) -> None:
        bases = self._bases(app_id, Path(state["install_dir"]))
        steps = state.get("steps", [])
        for index in range(int(state.get("done", 0)) - 1, -1, -1):
            self._undo_step(bases, steps[index])
            state["done"] = index
            self._save_state(app_id, state)

    def _finish_undo(self, app_id: str, state: dict[str, Any]) -> None:
        """Restore the OptiScaler profile byte for byte and drop the journal."""

        self._undo(app_id, state)
        profile_path = self.optiscaler.profile_repository.path(app_id)
        saved = state.get("optiscaler_profile")
        if saved is None:
            profile_path.unlink(missing_ok=True)
        else:
            _atomic_write(profile_path, str(saved))
        install_dir = Path(state["install_dir"])
        for step in state.get("steps", []):
            if step["kind"] in {"copy_enabler", "copy_runtime"}:
                parent = self.optiscaler._target(
                    install_dir, str(step["to"]).partition("/")[2]
                ).parent
                while parent != install_dir and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
        root = self._root(app_id)
        for name in ("parked-optiscaler", "displaced"):
            shutil.rmtree(root / name, ignore_errors=True)
        self._state_path(app_id).unlink(missing_ok=True)

    # -- public API -------------------------------------------------------
    def _assert_safe(self, game: Game) -> None:
        service = self.optiscaler
        root = service._canonical_game_root(game)
        if game.update_in_progress:
            raise OptiScalerError("A Steam update is currently active")
        if service._process_detector(root):
            raise OptiScalerError("The game is currently running")
        if self._host_process_modules is not None:
            profile = service.profile_repository.load(service.game_key(game))
            executable = Path(profile.executable).name
            if executable:
                for process in self._host_process_modules(executable):
                    if any(str(module).startswith(str(root) + "/")
                           for module in process.get("modules", [])):
                        raise OptiScalerError("The game is currently running")

    def recover(self, game: Game) -> bool:
        """Finish an interrupted switch by returning to the previous backend."""

        app_id = self.optiscaler.game_key(game)
        state = self.load_state(app_id)
        if state is None or state.get("phase") == "active":
            return False
        self._assert_safe(game)
        logger.warning("Recovering an interrupted DLSS Enabler switch for %s", app_id)
        self._finish_undo(app_id, state)
        return True

    def disable(
        self, game: Game, *, progress: Callable[[str, float], None] | None = None
    ) -> dict[str, Any]:
        """Remove the Enabler and restore OptiScaler files and settings exactly."""

        emit = progress or (lambda _stage, _value: None)
        app_id = self.optiscaler.game_key(game)
        state = self.load_state(app_id)
        if state is None:
            return self.status(game)
        self._assert_safe(game)
        # Preflight: nothing moves if an Enabler binary changed outside GameOpti.
        bases = self._bases(app_id, Path(state["install_dir"]))
        for step in state.get("steps", [])[: int(state.get("done", 0))]:
            if step["kind"] not in {"copy_enabler", "copy_runtime"}:
                continue
            target = self._path(bases, step["to"])
            relative = str(step["to"]).partition("/")[2]
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise OptiScalerConflictError(f"managed path is not a regular file: {relative}")
            if target.is_file() and not _is_mutable(relative) and _hash_file(target) != step["sha256"]:
                raise OptiScalerConflictError(
                    f"file changed outside GameOpti blocks the switch: {relative}"
                )
        state["phase"] = "switching_to_optiscaler"
        self._save_state(app_id, state)
        emit("Restoring OptiScaler", 0.3)
        self._finish_undo(app_id, state)
        emit("Completed", 1.0)
        return self.status(game)

    def enable(
        self,
        game: Game,
        *,
        gpu: str = "",
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        emit = progress or (lambda _stage, _value: None)
        service = self.optiscaler
        app_id = service.game_key(game)
        self.recover(game)
        if self.load_state(app_id) is not None:
            return self.status(game)
        # Anti-cheat files are a warning, never a block: the user confirmed a
        # single-player switch.  Presence of files is not proof of online play.
        anti_cheat = self.anti_cheat(game)
        if anti_cheat["detected"]:
            logger.warning(
                "DLSS Enabler for %s: anti-cheat files present (single-player warning shown): %s",
                app_id,
                "; ".join(f"{m['path']} [{m['rule']}]" for m in anti_cheat["matches"]),
            )
        self._assert_safe(game)
        profile = service.profile_repository.load(app_id)
        install_dir, manifest = self._install_dir(game, profile)
        executable = profile.executable
        if not executable:
            candidate, _confidence = service._selected_candidate(
                game, service.executable_resolver, ""
            )
            executable = candidate.relative_path
        amd_intel = needs_amd_intel_support(gpu)
        native_dlss = self._native_dlss(game) if amd_intel else None
        steps = self._plan_optiscaler(profile, manifest, install_dir) if manifest else []
        sources = self.ensure_artifacts(emit)
        steps += self._plan_enabler(install_dir, amd_intel, sources, steps, native_dlss)
        profile_path = service.profile_repository.path(app_id)
        state: dict[str, Any] = {
            "format": STATE_FORMAT_VERSION,
            "phase": "switching_to_enabler",
            "app_id": app_id,
            "install_dir": str(install_dir),
            "amd_intel": amd_intel,
            "version": DLSS_ENABLER_VERSION,
            "module_version": DLSS_ENABLER_MODULE_VERSION,
            "bundled_optiscaler_version": DLSS_ENABLER_OPTISCALER_VERSION,
            "source_url": DLSS_ENABLER_SOURCE_URL,
            "native_dlss": ({
                "path": str(native_dlss),
                "sha256": _hash_file(native_dlss),
                "size": native_dlss.stat().st_size,
            } if native_dlss is not None else None),
            "executable_name": Path(executable).name,
            "optiscaler_profile": (
                profile_path.read_text(encoding="utf-8") if profile_path.is_file() else None
            ),
            "optiscaler_manifest_id": profile.manifest_id,
            "anti_cheat_warning": anti_cheat["matches"],
            "steps": steps,
            "done": 0,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._save_state(app_id, state)
        bases = self._bases(app_id, install_dir)
        try:
            self._assert_safe(game)
            for index, step in enumerate(steps):
                state["done"] = index + 1
                self._save_state(app_id, state)
                self._run_step(bases, step)
                emit("Switching to DLSS Enabler", 0.4 + 0.5 * (index + 1) / max(1, len(steps)))
            self._write_configuration(app_id, state, DEFAULT_CONFIGURATION)
            now = datetime.now(UTC)
            service.profile_repository.save(replace(
                profile,
                enabled=True,
                backend="dlss_enabler",
                executable=executable,
                install_directory=str(install_dir),
                manifest_id="",
                installation_state="installed",
                injection_dll="dxgi.dll" if amd_intel else "version.dll",
                proton_override=wine_overrides(amd_intel),
                configuration_applied=False,
                runtime_verification_status="not_verified",
                updated_at=now,
            ))
            state["phase"] = "active"
            state["activated_at"] = now.isoformat()
            self._save_state(app_id, state)
        except BaseException:
            logger.exception("DLSS Enabler switch failed for %s; rolling back", app_id)
            try:
                self._finish_undo(app_id, state)
            except Exception:
                logger.exception("DLSS Enabler rollback incomplete for %s", app_id)
                state["phase"] = "rollback_required"
                self._save_state(app_id, state)
            raise
        emit("Completed", 1.0)
        return self.status(game)

    def configure(self, game: Game, values: Mapping[str, Any]) -> dict[str, Any]:
        """Apply settings understood by the exact 0.9.4/3.00.000.0 package."""

        app_id = self.optiscaler.game_key(game)
        state = self.load_state(app_id)
        if state is None or state.get("phase") != "active":
            raise DlssEnablerError("DLSS Enabler is not selected for this game")
        self._assert_safe(game)
        status = self.status(game, include_anti_cheat=False)
        if status.get("drift"):
            raise OptiScalerConflictError("DLSS Enabler files changed outside GameOpti")
        self._write_configuration(app_id, state, values)
        return self.status(game)

    def _process_runtime_evidence(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        """Require the main Enabler and its bundled upscaler in one game PID."""

        install_dir = Path(str(state.get("install_dir", "")))
        executable = str(state.get("executable_name", ""))
        if not executable:
            try:
                executable = Path(json.loads(str(state.get("optiscaler_profile") or "{}"))
                                  .get("executable", "")).name
            except (ValueError, TypeError):
                return None
        observations: list[Mapping[str, Any]] = []
        try:
            process_ids = self.optiscaler._process_detector(install_dir)
        except Exception:
            process_ids = ()
        for process_id in process_ids:
            try:
                maps = (self.proc_root / str(process_id) / "maps").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
            modules = [parts[5] for line in maps.splitlines()
                       if len(parts := line.split(None, 5)) == 6]
            observations.append({"processId": process_id, "modules": modules})
        if self._host_process_modules is not None and executable:
            try:
                observations.extend(self._host_process_modules(executable))
            except Exception as error:
                logger.debug("Host module diagnostics unavailable: %s", error)
        for observation in observations:
            mapped = set(observation.get("modules") or [])
            main = next((name for name in ("dlss-enabler.dll", "version.dll", "winmm.dll")
                         if str(install_dir / name) in mapped), "")
            upscaler = "dlss-enabler-upscaler.dll"
            if main and str(install_dir / upscaler) in mapped:
                return {
                    "method": "process_maps", "processId": int(observation["processId"]),
                    "modules": [main, upscaler], "scope": "modules_loaded",
                    "verifiedAt": datetime.now(UTC).isoformat(),
                    "configurationAt": str(state.get("configuration_updated_at", "")),
                }
        return None

    def _record_runtime(self, app_id: str, state: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
        # Diagnostic polling must never rewrite the transaction journal: a
        # status read can overlap a background switch or configuration save.
        _atomic_write(self._root(app_id) / "runtime-verification.json", json.dumps({
            **dict(evidence), "activatedAt": state.get("activated_at", ""),
            "configurationAt": state.get("configuration_updated_at", ""),
        }, indent=2, sort_keys=True) + "\n")

    def _runtime_diagnostics(
        self, app_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        install_dir = Path(str(state.get("install_dir", "")))
        log = install_dir / "dlss-enabler.log"
        result: dict[str, Any] = {
            "status": "not_run", "verified": False, "logFound": log.is_file(),
            "logPath": str(log), "errors": [], "warnings": [],
        }
        if log.is_file() and not log.is_symlink():
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
                modified = datetime.fromtimestamp(log.stat().st_mtime, UTC)
                activated = datetime.fromisoformat(str(
                    state.get("configuration_updated_at") or state.get("activated_at", "")
                ))
            except (OSError, ValueError):
                result["status"] = "unreadable"
            else:
                result["modifiedAt"] = modified.isoformat()
                if modified < activated or not text.strip() or text.startswith("This log is still empty"):
                    result["status"] = "stale" if modified < activated else "not_run"
                else:
                    lowered = text.casefold()
                    markers = (
                        "fatal error", "unhandled exception", "validation failed",
                        "failed to initialize", "installation of this mod failed",
                        "software upscaler failed to initialize", "frame generation backend failed to load",
                    )
                    result["warnings"] = [line.strip() for line in text.splitlines()
                                          if "misconfigured" in line.casefold()
                                          or "ngx runtime library is configured but file is missing" in line.casefold()]
                    errors = [marker for marker in markers if marker in lowered]
                    result["errors"] = errors
                    if errors:
                        result["status"] = "error"
                        return result
                    if "[dlss] upscaler backend loaded successfully" in lowered:
                        result.update({"status": "verified", "verified": True,
                                       "method": "runtime_log", "scope": "backend_loaded"})
        process_evidence = self._process_runtime_evidence(state)
        if process_evidence is not None:
            self._record_runtime(app_id, state, process_evidence)
            result.update({"status": "verified", "verified": True, **process_evidence})
            return result
        if result["verified"]:
            self._record_runtime(app_id, state, {
                "method": "runtime_log", "verifiedAt": result.get("modifiedAt", ""),
                "scope": "backend_loaded",
                "configurationAt": str(state.get("configuration_updated_at", "")),
            })
            return result
        try:
            previous = json.loads((self._root(app_id) / "runtime-verification.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if (isinstance(previous, Mapping) and previous.get("verifiedAt")
                and previous.get("activatedAt") == state.get("activated_at", "")
                and previous.get("configurationAt") == state.get("configuration_updated_at", "")
                and previous.get("scope") in {"modules_loaded", "backend_loaded"}):
            result.update({"status": "verified", "verified": True, **dict(previous)})
        return result

    def anti_cheat(self, game: Game) -> dict[str, Any]:
        """Anti-cheat files in *this* game's directory only (no cache).

        Scanned fresh per call, rooted at the game's own install path, so a
        previous game's result can never be reported for another game.
        """

        try:
            root = self.optiscaler._canonical_game_root(game)
        except OptiScalerError:
            return {"detected": bool(game.has_anticheat), "names": [], "matches": []}
        matches = anti_cheat_matches(root)
        names = sorted({item["rule"] for item in matches})
        return {
            "detected": bool(matches) or bool(game.has_anticheat),
            "names": names,
            "matches": matches,
            "root": str(root),
        }

    def status(self, game: Game, *, include_anti_cheat: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": True, "version": DLSS_ENABLER_VERSION,
            "moduleVersion": DLSS_ENABLER_MODULE_VERSION,
            "bundledOptiScalerVersion": DLSS_ENABLER_OPTISCALER_VERSION,
            "sourceUrl": DLSS_ENABLER_SOURCE_URL, "active": False,
            "phase": "inactive", "drift": [], "parkedOptiScaler": False,
            "installed": False, "selected": False, "runtimeVerified": False,
            "statusState": "inactive", "configuration": dict(DEFAULT_CONFIGURATION),
            "supportComponents": [],
            "gameId": str(getattr(game, "id", "")),
        }
        if include_anti_cheat:
            result["antiCheat"] = self.anti_cheat(game)
        try:
            app_id = self.optiscaler.game_key(game)
            state = self.load_state(app_id)
        except OptiScalerError as error:
            return {**result, "available": False, "statusState": "error", "error": str(error)}
        if state is None:
            return result
        phase = str(state.get("phase", ""))
        result.update({
            "active": phase == "active",
            "phase": phase,
            "amdIntel": bool(state.get("amd_intel")),
            "installDirectory": str(state.get("install_dir", "")),
            "parkedOptiScaler": bool(state.get("optiscaler_manifest_id")),
            "activatedAt": str(state.get("activated_at", "")),
            "configuration": dict(state.get("configuration") or DEFAULT_CONFIGURATION),
            "nativeDlss": state.get("native_dlss") or {},
            "supportComponents": ([
                "dxgi.dll", "dlss-enabler.dll", "nvapi64-proxy.dll", "dlss-finder.exe", "_nvngx.dll"
            ] if state.get("amd_intel") else []),
        })
        if phase == "active":
            bases = self._bases(app_id, Path(state["install_dir"]))
            for step in state.get("steps", []):
                if step["kind"] not in {"copy_enabler", "copy_runtime"}:
                    continue
                relative = str(step["to"]).partition("/")[2]
                target = self._path(bases, step["to"])
                if (target.is_symlink() or not target.is_file()
                        or (not _is_mutable(relative) and _hash_file(target) != step["sha256"])):
                    result["drift"].append(relative)
            result["installed"] = not result["drift"]
            try:
                profile = self.optiscaler.profile_repository.load(app_id)
                expected_override = wine_overrides(bool(state.get("amd_intel")))
                result["selected"] = bool(
                    profile.enabled and profile.backend == "dlss_enabler"
                    and profile.proton_override == expected_override
                )
            except OptiScalerError:
                result["selected"] = False
            runtime = self._runtime_diagnostics(app_id, state)
            result["runtime"] = runtime
            result["runtimeVerified"] = bool(runtime["verified"] and result["installed"] and result["selected"])
            if result["drift"] or not result["installed"]:
                result["statusState"] = "error"
            elif not result["selected"]:
                result["statusState"] = "installed"
            elif runtime["status"] == "error":
                result["statusState"] = "error"
            elif runtime["verified"]:
                result["statusState"] = "runtime_verified"
            else:
                result["statusState"] = "selected"
        elif phase != "inactive":
            result["statusState"] = "switch_interrupted"
        return result


__all__ = ["DlssEnablerError", "DlssEnablerService", "move_file"]
