from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from game_optimization_linux.models import GameStatus, SizeScanStatus
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services.library_cache import (
    CACHE_FORMAT_VERSION,
    LibraryCache,
)


def test_library_cache_round_trip_is_atomic(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache" / "library.json"
    cache = LibraryCache(cache_path)
    games = list(DemoGameProvider().list_games())

    cache.save(games)
    restored = cache.load()

    assert [game.name for game in restored] == [game.name for game in games]
    assert restored[0].install_path == games[0].install_path
    assert json.loads(cache_path.read_text(encoding="utf-8"))["version"] == CACHE_FORMAT_VERSION
    assert not list(cache_path.parent.glob("*.tmp"))
    assert not list(cache_path.parent.glob(".*.tmp"))


def test_library_cache_tolerates_corrupt_or_unknown_format(tmp_path: Path) -> None:
    cache_path = tmp_path / "library.json"
    cache_path.write_text("{broken", encoding="utf-8")
    assert LibraryCache(cache_path).load() == []

    cache_path.write_text('{"version": 999, "games": []}', encoding="utf-8")
    assert LibraryCache(cache_path).load() == []


def test_library_cache_skips_only_invalid_entries(tmp_path: Path) -> None:
    cache_path = tmp_path / "library.json"
    game = DemoGameProvider().list_games()[0]
    raw_game = game.to_dict()
    raw_game["size_scan_status"] = SizeScanStatus.COMPLETED.value
    cache_path.write_text(
        json.dumps(
            {
                "version": CACHE_FORMAT_VERSION,
                "games": [{"name": "missing fields"}, raw_game],
            }
        ),
        encoding="utf-8",
    )

    restored = LibraryCache(cache_path).load()

    assert len(restored) == 1
    assert restored[0].id == game.id
    assert restored[0].size_scan_status is SizeScanStatus.COMPLETED


def test_library_cache_never_persists_live_calculating_state(tmp_path: Path) -> None:
    cache_path = tmp_path / "library.json"
    calculating = replace(
        DemoGameProvider().list_games()[0],
        size_scan_status=SizeScanStatus.CALCULATING,
    )

    LibraryCache(cache_path).save((calculating,))

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["games"][0]["size_scan_status"] == "not requested"
    assert LibraryCache(cache_path).load()[0].size_scan_status is SizeScanStatus.NOT_REQUESTED


def test_library_cache_round_trips_disconnected_library_state(tmp_path: Path) -> None:
    cache_path = tmp_path / "library.json"
    disconnected = replace(
        DemoGameProvider().list_games()[0],
        status=GameStatus.DRIVE_DISCONNECTED,
        library_available=False,
        compression_available=False,
    )

    LibraryCache(cache_path).save((disconnected,))
    restored = LibraryCache(cache_path).load()[0]

    assert restored.status is GameStatus.DRIVE_DISCONNECTED
    assert restored.library_available is False
    assert restored.compression_available is False
