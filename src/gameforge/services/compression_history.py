"""Atomic XDG-state history and crash markers for compression operations."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from gameforge.models.compression import CompressionPlan, CompressionResult


logger = logging.getLogger(__name__)
COMPRESSION_HISTORY_FORMAT_VERSION = 2
_MAX_HISTORY_PER_GAME = 200


class CompressionHistoryError(RuntimeError):
    """Raised only when an explicitly requested durable write cannot complete."""


class CompressionHistoryStore:
    """Keep history outside games and make interrupted writes discoverable."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._payload = self._load()

    @property
    def path(self) -> Path:
        return self._path

    def begin_operation(self, plan: CompressionPlan) -> None:
        """Durably mark a task before the first mutating command is invoked."""

        with self._lock:
            pending = self._payload.setdefault("pending", {})
            if not isinstance(pending, dict):
                pending = {}
                self._payload["pending"] = pending
            pending[plan.id] = {
                "plan": plan.to_dict(include_files=False),
                "started_at": datetime.now(UTC).isoformat(),
                "state": "running",
            }
            self._write_locked()

    def finish_operation(
        self,
        game_name: str,
        game_path: str,
        result: CompressionResult,
    ) -> dict[str, Any]:
        """Append the verified real outcome and clear its crash marker."""

        entry = {
            "id": f"history-{uuid4().hex}",
            "game_id": result.game_id,
            "game_name": game_name,
            "game_path": game_path,
            **result.to_dict(),
        }
        with self._lock:
            pending = self._payload.setdefault("pending", {})
            if isinstance(pending, dict):
                pending.pop(result.plan_id, None)
            games = self._payload.setdefault("games", {})
            if not isinstance(games, dict):
                games = {}
                self._payload["games"] = games
            raw_game = games.setdefault(result.game_id, {"history": []})
            if not isinstance(raw_game, dict):
                raw_game = {"history": []}
                games[result.game_id] = raw_game
            history = raw_game.setdefault("history", [])
            if not isinstance(history, list):
                history = []
                raw_game["history"] = history
            history.append(entry)
            del history[:-_MAX_HISTORY_PER_GAME]
            raw_game["last_result"] = entry
            self._write_locked()
        return deepcopy(entry)

    def recover_interrupted(self) -> tuple[dict[str, Any], ...]:
        """Convert stale running markers to explicit verification-required history."""

        recovered: list[dict[str, Any]] = []
        with self._lock:
            pending = self._payload.setdefault("pending", {})
            if not isinstance(pending, dict) or not pending:
                return ()
            games = self._payload.setdefault("games", {})
            if not isinstance(games, dict):
                games = {}
                self._payload["games"] = games
            now = datetime.now(UTC).isoformat()
            for plan_id, raw_pending in tuple(pending.items()):
                pending_map = (
                    raw_pending if isinstance(raw_pending, Mapping) else {}
                )
                plan = pending_map.get("plan")
                plan_map = plan if isinstance(plan, Mapping) else {}
                game_id = str(plan_map.get("game_id", ""))
                if not game_id:
                    continue
                entry = {
                    "id": f"history-{uuid4().hex}",
                    "plan_id": str(plan_id),
                    "game_id": game_id,
                    "game_name": str(plan_map.get("game_name", "")),
                    "game_path": str(plan_map.get("game_path", "")),
                    "profile": str(plan_map.get("profile", "Auto")),
                    "status": "verification_required",
                    "started_at": str(pending_map.get("started_at", now)),
                    "completed_at": now,
                    "processed_files": 0,
                    "processed_bytes": 0,
                    "actual_saved_bytes": None,
                    "verification_state": "verification_required",
                    "full_compression": bool(
                        plan_map.get("full_compression", True)
                    ),
                    "after_update": bool(plan_map.get("after_update", False)),
                    "build_id": plan_map.get("build_id"),
                    "command_exit_codes": [],
                    "warnings": [
                        "GameForge stopped before compression could be verified"
                    ],
                    "error": "Compression state requires verification",
                }
                raw_game = games.setdefault(game_id, {"history": []})
                if not isinstance(raw_game, dict):
                    raw_game = {"history": []}
                    games[game_id] = raw_game
                history = raw_game.setdefault("history", [])
                if not isinstance(history, list):
                    history = []
                    raw_game["history"] = history
                history.append(entry)
                del history[:-_MAX_HISTORY_PER_GAME]
                raw_game["last_result"] = entry
                recovered.append(entry)
            pending.clear()
            self._write_locked()
        return tuple(deepcopy(recovered))

    def history(self, game_id: str | None = None) -> tuple[dict[str, Any], ...]:
        with self._lock:
            games = self._payload.get("games", {})
            if not isinstance(games, Mapping):
                return ()
            values: list[dict[str, Any]] = []
            items = (
                ((game_id, games.get(game_id)),)
                if game_id is not None
                else games.items()
            )
            for _, raw_game in items:
                if not isinstance(raw_game, Mapping):
                    continue
                history = raw_game.get("history", ())
                if not isinstance(history, list):
                    continue
                values.extend(item for item in history if isinstance(item, dict))
            values.sort(
                key=lambda item: str(item.get("completed_at", "")), reverse=True
            )
            return tuple(deepcopy(values))

    def pending(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            raw = self._payload.get("pending", {})
            if not isinstance(raw, Mapping):
                return ()
            return tuple(
                deepcopy(value)
                for value in raw.values()
                if isinstance(value, dict)
            )

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty()
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            logger.warning(
                "Ignoring unreadable compression history %s: %s",
                self._path,
                error,
            )
            return self._empty()
        if not isinstance(raw, dict):
            return self._empty()
        version = raw.get("version")
        if version == COMPRESSION_HISTORY_FORMAT_VERSION:
            raw.setdefault("games", {})
            raw.setdefault("pending", {})
            return raw
        if version == 1:
            # Version 1 stored one flat history list and had no crash marker.
            migrated = self._empty()
            legacy = raw.get("history", [])
            if isinstance(legacy, list):
                for entry in legacy:
                    if not isinstance(entry, dict):
                        continue
                    game_id = str(entry.get("game_id", ""))
                    if not game_id:
                        continue
                    game = migrated["games"].setdefault(
                        game_id, {"history": []}
                    )
                    game["history"].append(entry)
            return migrated
        logger.info(
            "Ignoring unsupported compression history format at %s", self._path
        )
        return self._empty()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": COMPRESSION_HISTORY_FORMAT_VERSION,
            "games": {},
            "pending": {},
        }

    def _write_locked(self) -> None:
        self._payload["version"] = COMPRESSION_HISTORY_FORMAT_VERSION
        temporary = self._path.with_name(
            f".{self._path.name}.{uuid4().hex}.tmp"
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(
                self._payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
            try:
                directory_descriptor = os.open(
                    self._path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
            except OSError:
                directory_descriptor = None
            if directory_descriptor is not None:
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except (OSError, TypeError, ValueError) as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.debug(
                    "Could not remove temporary compression history",
                    exc_info=True,
                )
            raise CompressionHistoryError(
                f"could not save compression history to {self._path}: {error}"
            ) from error


__all__ = [
    "COMPRESSION_HISTORY_FORMAT_VERSION",
    "CompressionHistoryError",
    "CompressionHistoryStore",
]
