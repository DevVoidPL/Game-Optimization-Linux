from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from game_optimization_linux.config import STATE_DIR
from game_optimization_linux.models import OptimizationAnalysis, validate_game_key


logger = logging.getLogger(__name__)
ANALYSIS_STATE_SCHEMA_VERSION = 1
ANALYSIS_STATE_FILE_NAME = "optimization-analysis-v1.json"
_MAX_STATE_BYTES = 2 * 1024 * 1024


class OptimizationAnalysisRepository:
    def __init__(self, root: Path = STATE_DIR / "games") -> None:
        self.root = Path(root)

    def path(self, app_id: object) -> Path:
        return self.root / validate_game_key(app_id) / ANALYSIS_STATE_FILE_NAME

    def save(self, app_id: object, analysis: OptimizationAnalysis) -> Path:
        key = validate_game_key(app_id)
        path = self.path(key)
        payload = {
            "schema_version": ANALYSIS_STATE_SCHEMA_VERSION,
            "app_id": key,
            "game_id": analysis.fingerprint.game_id,
            "saved_at": datetime.now(UTC).isoformat(),
            "analysis": analysis.to_dict(),
        }
        content = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(content) > _MAX_STATE_BYTES:
            raise ValueError("optimization analysis state is too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def load(self, app_id: object) -> OptimizationAnalysis | None:
        key = validate_game_key(app_id)
        path = self.path(key)
        try:
            if path.stat().st_size > _MAX_STATE_BYTES:
                raise ValueError("saved analysis is too large")
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("saved analysis must be an object")
            schema = int(payload.get("schema_version") or 1)
            if schema < 1 or schema > ANALYSIS_STATE_SCHEMA_VERSION:
                raise ValueError(f"unsupported analysis schema {schema}")
            if str(payload.get("app_id") or key) != key:
                raise ValueError("saved analysis belongs to another game")
            analysis = payload.get("analysis")
            if not isinstance(analysis, dict):
                raise ValueError("saved analysis payload is missing")
            return OptimizationAnalysis.from_dict(analysis)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            logger.warning("Ignoring invalid optimization analysis %s: %s", path, error)
            return None


__all__ = [
    "ANALYSIS_STATE_FILE_NAME",
    "ANALYSIS_STATE_SCHEMA_VERSION",
    "OptimizationAnalysisRepository",
]
