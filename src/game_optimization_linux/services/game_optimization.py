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
        if (
            bottleneck.conclusion == "gpu_bottleneck"
            and gamescope_available
            and fingerprint.system.resolution_width
            and fingerprint.system.resolution_height
        ):
            width = max(640, int(fingerprint.system.resolution_width * 0.75) // 2 * 2)
            height = max(360, int(fingerprint.system.resolution_height * 0.75) // 2 * 2)
            results.append(
                OptimizationCandidate(
                    id="gamescope_gpu_scaling",
                    target="GPU load",
                    mechanism="Gamescope render scaling",
                    source="MangoHud baseline and selected display",
                    evidence=bottleneck.evidence,
                    current_value=f"{fingerprint.system.resolution_width}x{fingerprint.system.resolution_height}",
                    proposed_value=f"{width}x{height}",
                    expected_effect="Reduce the number of pixels rendered by the game",
                    quality_impact="Moderate; image sharpness can decrease",
                    risk="Low; the runner profile can be reverted",
                    reversible=True,
                    requires_measurement=True,
                    engine_support="Engine independent",
                    api_support="Gamescope compatible runtime required",
                    env_changes={"gamescopeMode": "performance"},
                    automatically_selected=profile.preset in {
                        "automatic", "maximum_performance"
                    },
                )
            )
        if (
            bottleneck.conclusion == "cpu_bottleneck"
            and bottleneck.confidence >= 0.60
            and measurement is not None
            and measurement.quality in {"medium", "high"}
            and gamemode_available
        ):
            results.append(
                OptimizationCandidate(
                    id="gamemode_cpu_schedule",
                    target="CPU scheduling",
                    mechanism="GameMode wrapper",
                    source="MangoHud baseline and host GameMode diagnostic",
                    evidence=bottleneck.evidence,
                    current_value="Disabled" if not profile.gamemode_enabled else "Enabled",
                    proposed_value="Enabled",
                    expected_effect="Allow GameMode to apply its configured per-game scheduling policy",
                    quality_impact="None",
                    risk="Low; no global settings are written by Game Optimization",
                    reversible=True,
                    requires_measurement=True,
                    engine_support="Engine independent",
                    api_support="Not graphics-API specific",
                    env_changes={"wrapper": "gamemoderun"},
                    automatically_selected=profile.preset == "automatic",
                )
            )
        if (
            profile.user_goal == "low_power"
            and measurement is not None
            and measurement.average_fps is not None
            and measurement.average_fps > profile.target_fps * 1.10
        ):
            results.append(
                OptimizationCandidate(
                    id="quiet_fps_target",
                    target="GPU and CPU load",
                    mechanism="Existing runner FPS limiter owner",
                    source="User goal and MangoHud baseline",
                    evidence=(
                        f"Measured average was {measurement.average_fps:.1f} FPS",
                        f"Configured quiet target is {profile.target_fps} FPS",
                    ),
                    current_value="Unlimited or above target",
                    proposed_value=f"{profile.target_fps} FPS",
                    expected_effect="Reduce sustained load while keeping the selected target",
                    quality_impact="Motion is limited to the selected frame rate",
                    risk="Low; the per-game profile is reversible",
                    reversible=True,
                    requires_measurement=True,
                    engine_support="Engine independent",
                    api_support="Requires an available configured limiter",
                    env_changes={"fpsLimit": str(profile.target_fps)},
                    automatically_selected=profile.preset in {"automatic", "quiet"},
                )
            )
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
        if candidate.id != "unreal_streaming_pool" or len(candidate.files_to_modify) != 1:
            raise ValueError("This candidate does not modify a supported config file")
        root = game.install_path.resolve(strict=True)
        path = Path(candidate.files_to_modify[0])
        if path.is_symlink():
            raise ValueError("Config symlinks are not modified")
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("Config path is outside the game root") from error
        before = resolved.read_bytes()
        text = before.decode("utf-8")
        matches = list(_UNREAL_POOL.finditer(text))
        if len(matches) != 1 or matches[0].group("value") != candidate.current_value:
            raise RuntimeError("The config changed after analysis")
        if self._process_checker(game) or game.update_in_progress:
            raise RuntimeError("The game started or Steam began updating it")
        match = matches[0]
        updated = (
            text[: match.start("value")]
            + candidate.proposed_value
            + text[match.end("value") :]
        ).encode("utf-8")
        change_id = uuid4().hex
        directory = self._data_root / str(game.steam_app_id or game.id) / change_id
        directory.mkdir(parents=True, exist_ok=False)
        backup = directory / "original"
        backup.write_bytes(before)
        shutil.copystat(resolved, backup, follow_symlinks=False)
        before_hash = hashlib.sha256(before).hexdigest()
        after_hash = hashlib.sha256(updated).hexdigest()
        manifest = {
            "schema_version": 1,
            "id": change_id,
            "game_id": game.id,
            "app_id": str(game.steam_app_id or game.id),
            "candidate_id": candidate.id,
            "game_root": os.fspath(root),
            "relative_path": relative.as_posix(),
            "backup": "original",
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "current_value": candidate.current_value,
            "proposed_value": candidate.proposed_value,
            "created_at": datetime.now(UTC).isoformat(),
            "state": "applied",
        }
        original_mode = resolved.stat().st_mode
        try:
            self._atomic_write(resolved, updated, original_mode)
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != after_hash:
                raise RuntimeError("Config hash verification failed")
            self._atomic_json(directory / "manifest.json", manifest)
        except BaseException:
            self._atomic_write(resolved, before, original_mode)
            raise
        return manifest

    def revert(self, game: Game, change_id: str) -> dict[str, Any]:
        directory = self._data_root / str(game.steam_app_id or game.id) / str(change_id)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("game_id") != game.id or manifest.get("state") != "applied":
            raise ValueError("Optimization change manifest is not applicable")
        root = game.install_path.resolve(strict=True)
        target = (root / str(manifest["relative_path"])).resolve(strict=True)
        target.relative_to(root)
        if hashlib.sha256(target.read_bytes()).hexdigest() != manifest["after_sha256"]:
            raise RuntimeError("The config was changed outside Game Optimization")
        original = (directory / str(manifest["backup"])).read_bytes()
        if hashlib.sha256(original).hexdigest() != manifest["before_sha256"]:
            raise RuntimeError("Optimization backup hash verification failed")
        self._atomic_write(target, original, target.stat().st_mode)
        manifest["state"] = "reverted"
        manifest["reverted_at"] = datetime.now(UTC).isoformat()
        self._atomic_json(manifest_path, manifest)
        return manifest

    def record_runtime_change(
        self,
        game: Game,
        candidate: OptimizationCandidate,
        before: GameOptimizationProfile,
        after: GameOptimizationProfile,
    ) -> dict[str, Any]:
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
