"""Persistence owned by the narrator feature."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Lock
from typing import Any, Mapping
import unicodedata

from game_optimization_linux.config import (
    GAMES_CONFIG_DIR,
    NARRATOR_CAPTURE_GRANTS_FILE,
    NARRATOR_TRANSLATION_CACHE_FILE,
)
from game_optimization_linux.models.narrator import NarratorGameSettings
from game_optimization_linux.models.mangohud import validate_game_key


NARRATOR_SETTINGS_FILE_NAME = "narrator.json"
CAPTURE_GRANTS_SCHEMA_VERSION = 1


def _atomic_json_write(path: Path, values: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(values, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class NarratorSettingsRepository:
    def __init__(self, root: Path = GAMES_CONFIG_DIR) -> None:
        self.root = Path(root)

    def path(self, game_key: object) -> Path:
        return self.root / validate_game_key(game_key) / NARRATOR_SETTINGS_FILE_NAME

    def load(self, game_key: object) -> NarratorGameSettings:
        normalized = validate_game_key(game_key)
        path = self.path(normalized)
        if not path.is_file():
            return NarratorGameSettings.default(normalized)
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read narrator settings: {error}") from error
        if not isinstance(values, Mapping):
            raise ValueError("narrator settings must contain a JSON object")
        return NarratorGameSettings.from_dict(values, expected_game_key=normalized)

    def save(self, settings: NarratorGameSettings) -> Path:
        path = self.path(settings.game_key)
        _atomic_json_write(path, settings.to_dict())
        return path


class CaptureGrantRepository:
    """Store portal restore tokens away from ordinary per-game settings."""

    def __init__(
        self,
        path: Path = NARRATOR_CAPTURE_GRANTS_FILE,
    ) -> None:
        self.path = Path(path)

    def load_token(self, game_key: object) -> str:
        values = self._load()
        token = values.get("tokens", {}).get(validate_game_key(game_key), "")
        return str(token) if isinstance(token, str) else ""

    def save_token(self, game_key: object, token: str) -> None:
        normalized = validate_game_key(game_key)
        if not isinstance(token, str) or any(char in token for char in "\r\n\0"):
            raise ValueError("portal restore token must be a single-line string")
        values = self._load()
        tokens = dict(values.get("tokens", {}))
        if token:
            tokens[normalized] = token
        else:
            tokens.pop(normalized, None)
        _atomic_json_write(
            self.path,
            {
                "schema_version": CAPTURE_GRANTS_SCHEMA_VERSION,
                "tokens": tokens,
            },
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema_version": CAPTURE_GRANTS_SCHEMA_VERSION,
                "tokens": {},
            }
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read capture grants: {error}") from error
        if not isinstance(values, Mapping):
            raise ValueError("capture grants must contain a JSON object")
        if values.get("schema_version", 1) != CAPTURE_GRANTS_SCHEMA_VERSION:
            raise ValueError("unsupported capture grants schema version")
        tokens = values.get("tokens", {})
        if not isinstance(tokens, Mapping):
            raise ValueError("capture grants tokens must contain an object")
        return {
            "schema_version": CAPTURE_GRANTS_SCHEMA_VERSION,
            "tokens": {
                str(key): str(value)
                for key, value in tokens.items()
                if isinstance(key, str) and isinstance(value, str)
            },
        }


def normalize_cache_phrase(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).split()).casefold()


class TranslationCache:
    def __init__(
        self,
        path: Path = NARRATOR_TRANSLATION_CACHE_FILE,
    ) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def get(
        self,
        text: str,
        *,
        provider_id: str,
        profile_id: str,
        source_language: str = "en",
        target_language: str = "pl",
    ) -> str | None:
        phrase = normalize_cache_phrase(text)
        if not phrase:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT translated_text FROM translations
                WHERE source_phrase = ? AND provider_id = ? AND profile_id = ?
                  AND source_language = ? AND target_language = ?
                """,
                (
                    phrase,
                    provider_id,
                    profile_id,
                    source_language,
                    target_language,
                ),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE translations SET last_used_at = ?, hit_count = hit_count + 1
                WHERE source_phrase = ? AND provider_id = ? AND profile_id = ?
                  AND source_language = ? AND target_language = ?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    phrase,
                    provider_id,
                    profile_id,
                    source_language,
                    target_language,
                ),
            )
            return str(row[0])

    def put(
        self,
        text: str,
        translated_text: str,
        *,
        provider_id: str,
        profile_id: str,
        source_language: str = "en",
        target_language: str = "pl",
    ) -> None:
        phrase = normalize_cache_phrase(text)
        translation = " ".join(str(translated_text).split())
        if not phrase or not translation:
            raise ValueError("source and translated phrases must not be empty")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO translations (
                    source_phrase, provider_id, profile_id, source_language,
                    target_language, translated_text, created_at, last_used_at,
                    hit_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT (
                    source_phrase, provider_id, profile_id, source_language,
                    target_language
                ) DO UPDATE SET translated_text = excluded.translated_text,
                                last_used_at = excluded.last_used_at
                """,
                (
                    phrase,
                    provider_id,
                    profile_id,
                    source_language,
                    target_language,
                    translation,
                    now,
                    now,
                ),
            )

    def close(self) -> None:
        return

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        connection = sqlite3.connect(self.path, timeout=5.0)
        os.chmod(self.path, 0o600)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                source_phrase TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                source_language TEXT NOT NULL,
                target_language TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    source_phrase, provider_id, profile_id, source_language,
                    target_language
                )
            )
            """
        )
        return connection


__all__ = [
    "CAPTURE_GRANTS_SCHEMA_VERSION",
    "CaptureGrantRepository",
    "NARRATOR_SETTINGS_FILE_NAME",
    "NarratorSettingsRepository",
    "TranslationCache",
    "normalize_cache_phrase",
]
