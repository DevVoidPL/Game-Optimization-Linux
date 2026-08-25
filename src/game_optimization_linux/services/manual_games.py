"""Atomic persistence for explicitly configured manual games."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Sequence
from uuid import uuid4

from game_optimization_linux.models.manual_game import (
    MANUAL_GAME_SCHEMA_VERSION,
    ManualGameConfig,
)


class ManualGameStoreError(RuntimeError):
    pass


class ManualGameStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> tuple[ManualGameConfig, ...]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return ()
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ManualGameStoreError(f"could not read manual games: {error}") from error
            if not isinstance(raw, Mapping):
                raise ManualGameStoreError("manual game store root is malformed")
            version = raw.get("version")
            if version != MANUAL_GAME_SCHEMA_VERSION:
                raise ManualGameStoreError(f"unsupported manual game store version: {version}")
            rows = raw.get("games", [])
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
                raise ManualGameStoreError("manual game records are malformed")
            games: list[ManualGameConfig] = []
            seen: set[str] = set()
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise ManualGameStoreError(f"manual game record {index} is malformed")
                try:
                    game = ManualGameConfig.from_dict(row)
                except ValueError as error:
                    raise ManualGameStoreError(
                        f"manual game record {index} is invalid: {error}"
                    ) from error
                if game.id in seen:
                    raise ManualGameStoreError(f"duplicate manual game id: {game.id}")
                seen.add(game.id)
                games.append(game)
            return tuple(games)

    def save(self, games: Sequence[ManualGameConfig]) -> None:
        ordered = tuple(sorted(games, key=lambda game: game.id))
        payload = json.dumps(
            {
                "version": MANUAL_GAME_SCHEMA_VERSION,
                "games": [game.to_dict() for game in ordered],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    self.path.parent.chmod(0o700)
                except OSError:
                    pass
                temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(temporary, flags, 0o600)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    temporary.replace(self.path)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            except OSError as error:
                raise ManualGameStoreError(f"could not save manual games: {error}") from error

    def upsert(self, game: ManualGameConfig) -> ManualGameConfig:
        with self._lock:
            games = {item.id: item for item in self.load()}
            games[game.id] = game
            self.save(tuple(games.values()))
        return game

    def remove(self, game_id: str) -> ManualGameConfig:
        with self._lock:
            games = {item.id: item for item in self.load()}
            try:
                removed = games.pop(str(game_id))
            except KeyError as error:
                raise ManualGameStoreError("manual game was not found") from error
            self.save(tuple(games.values()))
        return removed

    def get(self, game_id: str) -> ManualGameConfig | None:
        return next((game for game in self.load() if game.id == str(game_id)), None)


__all__ = ["ManualGameStore", "ManualGameStoreError"]
