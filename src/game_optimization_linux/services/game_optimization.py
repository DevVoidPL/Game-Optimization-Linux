from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from collections.abc import Callable
from typing import Any, Mapping
from uuid import uuid4

from game_optimization_linux.models import (
    BottleneckAnalysis,
    Game,
    GameFingerprint,
    GameOptimizationProfile,
    OptimizationCandidate,
    PerformanceMeasurement,
)
from .game_settings import (
    is_allowed_config_path,
    parse_assignments,
    replace_existing_setting,
)


_UNREAL_POOL = re.compile(
    r"(?im)^(?P<prefix>\s*r\.Streaming\.PoolSize\s*=\s*)(?P<value>[0-9]+)(?P<suffix>\s*(?:[;#].*)?)$"
)


class GameRecommendationEngine:
    def recommend(
        self,
        fingerprint: GameFingerprint,
        measurement: PerformanceMeasurement | None,
        bottleneck: BottleneckAnalysis,
        profile: GameOptimizationProfile,
        *,
        gamemode_available: bool,
        gamescope_available: bool,
    ) -> tuple[OptimizationCandidate, ...]:
        results: list[OptimizationCandidate] = []
        if (
            fingerprint.engine.value == "Unreal Engine"
            and fingerprint.engine.confidence >= 0.75
            and bottleneck.conclusion == "vram_pressure"
            and bottleneck.confidence >= 0.60
            and measurement is not None
            and measurement.available
            and measurement.quality in {"medium", "high"}
        ):
            candidate = self._unreal_streaming_pool(fingerprint)
            if candidate is not None:
                results.append(replace(
                    candidate,
                    automatically_selected=profile.preset in {
                        "automatic", "maximum_performance"
                    },
                ))
        return tuple(results)

    @staticmethod
    def _unreal_streaming_pool(
        fingerprint: GameFingerprint,
    ) -> OptimizationCandidate | None:
        if not fingerprint.system.vram_gb:
            return None
        root = Path(fingerprint.game_root)
        best: tuple[Path, int] | None = None
        for directory in fingerprint.config_locations:
            folder = Path(directory)
            try:
                folder.relative_to(root)
            except ValueError:
                continue
            for path in folder.glob("*.ini"):
                try:
                    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                match = _UNREAL_POOL.search(text)
                if match:
                    current = int(match.group("value"))
                    if best is None or current > best[1]:
                        best = (path, current)
        if best is None:
            return None
        path, current = best
        safe_pool = max(256, int(fingerprint.system.vram_gb * 1024 * 0.70))
        if current <= safe_pool:
            return None
        return OptimizationCandidate(
            id="unreal_streaming_pool",
            target="VRAM pressure",
            mechanism="Existing Unreal r.Streaming.PoolSize setting",
            source="Detected Unreal config and measured VRAM pressure",
            evidence=(
                f"The existing config sets r.Streaming.PoolSize={current}",
                f"Detected VRAM is {fingerprint.system.vram_gb:.1f} GiB",
            ),
            current_value=str(current),
            proposed_value=str(safe_pool),
            expected_effect="Reduce the configured texture streaming pool to leave VRAM headroom",
            quality_impact="Textures may stream more often or use lower mip levels",
            risk="Moderate; the game may ignore or override this setting",
            reversible=True,
            requires_measurement=True,
            engine_support="Unreal Engine with an existing r.Streaming.PoolSize key",
            api_support="Graphics API independent",
            files_to_modify=(os.fspath(path),),
            automatically_selected=False,
        )


class OptimizationChangeService:
    def __init__(
        self,
        data_root: Path,
        *,
        process_checker: Callable[[Game], bool] | None = None,
    ) -> None:
        self._data_root = Path(data_root)
        self._process_checker = process_checker or self._game_process_running

    def apply(
        self,
        game: Game,
        candidate: OptimizationCandidate,
        *,
        game_running: bool = False,
    ) -> dict[str, Any]:
        if game_running or self._process_checker(game):
            raise RuntimeError("The game is running")
        if game.update_in_progress:
            raise RuntimeError("Steam is updating the game")
        if self.active_change(game) is not None:
            raise RuntimeError(
                "Finish the current comparison cycle before applying another change"
            )
        settings_change = bool(candidate.config_adapter and candidate.setting_id)
        if (
            not settings_change
            and candidate.id != "unreal_streaming_pool"
        ) or len(candidate.files_to_modify) != 1:
            raise ValueError("This candidate does not modify a supported config file")
        root = game.install_path.resolve(strict=True)
        path = Path(candidate.files_to_modify[0])
        if path.is_symlink():
            raise ValueError("Config symlinks are not modified")
        resolved = path.resolve(strict=True)
        if settings_change:
            if not is_allowed_config_path(game, resolved):
                raise ValueError("Config path is outside the game or its Proton prefix")
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                relative = ""
        else:
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError as error:
                raise ValueError("Config path is outside the game root") from error
        before = resolved.read_bytes()
        before_hash = hashlib.sha256(before).hexdigest()
        if settings_change:
            if not candidate.config_sha256 or before_hash != candidate.config_sha256:
                raise RuntimeError("The config changed after analysis")
            updated = replace_existing_setting(
                before,
                section=candidate.config_section,
                key=candidate.config_key,
                current=candidate.current_value,
                proposed=candidate.proposed_value,
            )
        else:
            text = before.decode("utf-8")
            matches = list(_UNREAL_POOL.finditer(text))
            if len(matches) != 1 or matches[0].group("value") != candidate.current_value:
                raise RuntimeError("The config changed after analysis")
            match = matches[0]
            updated = (
                text[: match.start("value")]
                + candidate.proposed_value
                + text[match.end("value") :]
            ).encode("utf-8")
        if self._process_checker(game) or game.update_in_progress:
            raise RuntimeError("The game started or Steam began updating it")
        change_id = uuid4().hex
        directory = self._data_root / str(game.steam_app_id or game.id) / change_id
        directory.mkdir(parents=True, exist_ok=False)
        backup = directory / "original"
        backup.write_bytes(before)
        shutil.copystat(resolved, backup, follow_symlinks=False)
        after_hash = hashlib.sha256(updated).hexdigest()
        manifest = {
            "schema_version": 1,
            "id": change_id,
            "game_id": game.id,
            "app_id": str(game.steam_app_id or game.id),
            "candidate_id": candidate.id,
            "game_root": os.fspath(root),
            "relative_path": relative,
            "target_path": os.fspath(resolved) if settings_change else "",
            "backup": "original",
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "current_value": candidate.current_value,
            "proposed_value": candidate.proposed_value,
            "setting_id": candidate.setting_id,
            "setting_label": candidate.setting_label,
            "config_section": candidate.config_section,
            "config_key": candidate.config_key,
            "config_adapter": candidate.config_adapter,
            "created_at": datetime.now(UTC).isoformat(),
            "state": "applied",
        }
        original_mode = resolved.stat().st_mode
        try:
            self._atomic_write(resolved, updated, original_mode)
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != after_hash:
                raise RuntimeError("Config hash verification failed")
            if settings_change and self._read_setting_value(
                resolved, candidate.config_section, candidate.config_key
            ) != candidate.proposed_value:
                raise RuntimeError("The modified setting failed read-back verification")
            self._atomic_json(directory / "manifest.json", manifest)
        except BaseException:
            self._atomic_write(resolved, before, original_mode)
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != before_hash:
                raise RuntimeError("The original config could not be restored")
            raise
        return manifest

    def revert(self, game: Game, change_id: str) -> dict[str, Any]:
        directory = self._data_root / str(game.steam_app_id or game.id) / str(change_id)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("game_id") != game.id or manifest.get("state") != "applied":
            raise ValueError("Optimization change manifest is not applicable")
        root = game.install_path.resolve(strict=True)
        if manifest.get("config_adapter"):
            target = Path(str(manifest.get("target_path") or "")).resolve(strict=True)
            if not is_allowed_config_path(game, target):
                raise ValueError("Config path is outside the game or its Proton prefix")
        else:
            target = (root / str(manifest["relative_path"])).resolve(strict=True)
            target.relative_to(root)
        if hashlib.sha256(target.read_bytes()).hexdigest() != manifest["after_sha256"]:
            raise RuntimeError("The config was changed outside Game Optimization")
        original = (directory / str(manifest["backup"])).read_bytes()
        if hashlib.sha256(original).hexdigest() != manifest["before_sha256"]:
            raise RuntimeError("Optimization backup hash verification failed")
        self._atomic_write(target, original, target.stat().st_mode)
        if hashlib.sha256(target.read_bytes()).hexdigest() != manifest["before_sha256"]:
            raise RuntimeError("Restored config hash verification failed")
        if manifest.get("config_adapter") and self._read_setting_value(
            target,
            str(manifest.get("config_section") or ""),
            str(manifest.get("config_key") or ""),
        ) != str(manifest.get("current_value") or ""):
            raise RuntimeError("The restored setting failed read-back verification")
        manifest["state"] = "reverted"
        manifest["reverted_at"] = datetime.now(UTC).isoformat()
        self._atomic_json(manifest_path, manifest)
        return manifest

    def keep(
        self,
        game: Game,
        change_id: str,
        *,
        comparison: Mapping[str, Any] | None = None,
        before_measurement: Mapping[str, Any] | None = None,
        after_measurement: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        directory = self._data_root / str(game.steam_app_id or game.id) / str(change_id)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("game_id") != game.id or manifest.get("state") != "applied":
            raise ValueError("Optimization change manifest is not applicable")
        manifest["state"] = "kept"
        manifest["kept_at"] = datetime.now(UTC).isoformat()
        if comparison:
            manifest["comparison"] = dict(comparison)
        if before_measurement:
            manifest["before_measurement"] = dict(before_measurement)
        if after_measurement:
            manifest["after_measurement"] = dict(after_measurement)
        self._atomic_json(manifest_path, manifest)
        return manifest

    def active_change(self, game: Game) -> dict[str, Any] | None:
        root = self._data_root / str(game.steam_app_id or game.id)
        try:
            manifests = tuple(root.glob("*/manifest.json"))
        except OSError:
            return None
        for path in sorted(manifests, key=lambda item: item.parent.name, reverse=True):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if value.get("game_id") == game.id and value.get("state") == "applied":
                return value
        return None

    @staticmethod
    def _read_setting_value(path: Path, section: str, key: str) -> str | None:
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeError):
            return None
        matches = [
            value
            for value in parse_assignments(text)
            if value[0].casefold() == section.casefold()
            and value[1].casefold() == key.casefold()
        ]
        return matches[0][2] if len(matches) == 1 else None

    def record_runtime_change(
        self,
        game: Game,
        candidate: OptimizationCandidate,
        before: GameOptimizationProfile,
        after: GameOptimizationProfile,
    ) -> dict[str, Any]:
        if self._process_checker(game):
            raise RuntimeError("The game is running")
        if game.update_in_progress:
            raise RuntimeError("Steam is updating the game")
        if self.active_change(game) is not None:
            raise RuntimeError(
                "Finish the current comparison cycle before applying another change"
            )
        change_id = uuid4().hex
        directory = self._data_root / str(game.steam_app_id or game.id) / change_id
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schema_version": 1,
            "id": change_id,
            "game_id": game.id,
            "app_id": str(game.steam_app_id or game.id),
            "candidate_id": candidate.id,
            "kind": "runtime_profile",
            "before_profile": before.to_dict(),
            "after_profile": after.to_dict(),
            "created_at": datetime.now(UTC).isoformat(),
            "state": "applied",
        }
        self._atomic_json(directory / "manifest.json", manifest)
        return manifest

    def runtime_change(self, game: Game, change_id: str) -> dict[str, Any]:
        path = (
            self._data_root
            / str(game.steam_app_id or game.id)
            / str(change_id)
            / "manifest.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value.get("game_id") != game.id
            or value.get("kind") != "runtime_profile"
            or value.get("state") != "applied"
        ):
            raise ValueError("Runtime optimization change is not applicable")
        return value

    def mark_runtime_reverted(
        self, game: Game, change_id: str
    ) -> dict[str, Any]:
        manifest = self.runtime_change(game, change_id)
        manifest["state"] = "reverted"
        manifest["reverted_at"] = datetime.now(UTC).isoformat()
        path = (
            self._data_root
            / str(game.steam_app_id or game.id)
            / str(change_id)
            / "manifest.json"
        )
        self._atomic_json(path, manifest)
        return manifest

    @staticmethod
    def _atomic_write(path: Path, data: bytes, mode: int) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @classmethod
    def _atomic_json(cls, path: Path, payload: Mapping[str, Any]) -> None:
        cls._atomic_write(
            path,
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            0o600,
        )

    @staticmethod
    def _game_process_running(game: Game) -> bool:
        try:
            root = game.install_path.resolve(strict=True)
        except OSError:
            return False
        proc = Path("/proc")
        try:
            processes = tuple(proc.iterdir())
        except OSError:
            return False
        for process in processes:
            if not process.name.isdecimal():
                continue
            try:
                executable = (process / "exe").resolve(strict=True)
                executable.relative_to(root)
                return True
            except (OSError, ValueError):
                continue
        return False


__all__ = ["GameRecommendationEngine", "OptimizationChangeService"]
