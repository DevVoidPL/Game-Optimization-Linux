"""Atomic persistence for per-AppID runtime optimization profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from game_optimization_linux.config import GAMES_CONFIG_DIR
from game_optimization_linux.models import GameOptimizationProfile, validate_app_id


OPTIMIZATION_FILE_NAME = "optimization.json"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class GameOptimizationProfileRepository:
    def __init__(self, root: Path = GAMES_CONFIG_DIR) -> None:
        self.root = Path(root)

    def path(self, app_id: object) -> Path:
        return self.root / validate_app_id(app_id) / OPTIMIZATION_FILE_NAME

    def default(self, app_id: object) -> GameOptimizationProfile:
        return GameOptimizationProfile.default(app_id)

    def load(self, app_id: object) -> GameOptimizationProfile:
        normalized = validate_app_id(app_id)
        path = self.path(normalized)
        if not path.is_file():
            return self.default(normalized)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read optimization profile: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("optimization profile must contain a JSON object")
        return GameOptimizationProfile.from_dict(payload, expected_app_id=normalized)

    def save(self, profile: GameOptimizationProfile) -> Path:
        path = self.path(profile.app_id)
        _atomic_write(path, json.dumps(profile.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return path


__all__ = ["GameOptimizationProfileRepository", "OPTIMIZATION_FILE_NAME"]
