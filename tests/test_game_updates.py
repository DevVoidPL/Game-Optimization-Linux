from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from threading import Event

import pytest

from game_optimization_linux.controllers.presenters import game_to_qml, settings_to_qml
from game_optimization_linux.models.enums import (
    AutomaticCompressionMode,
    CompressionProfile,
    FilesystemType,
    GameStatus,
    Launcher,
)
from game_optimization_linux.models.game import Game
from game_optimization_linux.models.settings import AppSettings
from game_optimization_linux.models.system import FilesystemInfo
from game_optimization_linux.providers.steam import SteamGameProvider
from game_optimization_linux.services.game_updates import (
    UPDATE_STATE_FORMAT_VERSION,
    FileFingerprint,
    FingerprintSnapshot,
    GameFingerprintScanner,
    GameUpdateStateStore,
    GameUpdateStatus,
    GameUpdateTracker,
    UpdateScanCancelled,
    UpdateStateDatabase,
    diff_fingerprints,
)
from game_optimization_linux.services.library_cache import CACHE_FORMAT_VERSION, LibraryCache


class _Filesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            label="test",
            filesystem_name="btrfs",
            writable=True,
        )


def _game(
    path: Path,
    *,
    app_id: str = "42",
    build_id: str = "100",
    manifest_mtime_ns: int = 1,
    update_in_progress: bool = False,
    library_available: bool = True,
) -> Game:
    library = path.parent / "SteamLibrary"
    install_path = library / "steamapps" / "common" / path.name
    install_path.mkdir(parents=True, exist_ok=True)
    manifest = library / "steamapps" / f"appmanifest_{app_id}.acf"
    state_flags = 1028 if update_in_progress else 4
    manifest.write_text(
        '"AppState" {'
        f' "appid" "{app_id}"'
        f' "name" "Game {app_id}"'
        f' "installdir" "{path.name}"'
        f' "SizeOnDisk" "4096" "StateFlags" "{state_flags}"'
        f' "buildid" "{build_id}"'
        " }",
        encoding="utf-8",
    )
    os.utime(manifest, ns=(manifest_mtime_ns, manifest_mtime_ns))
    manifest_stat = manifest.stat()
    return Game(
        id=f"steam-{app_id}",
        name=f"Game {app_id}",
        launcher=Launcher.STEAM,
        install_path=install_path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        status=(
            GameStatus.READY
            if library_available
            else GameStatus.DRIVE_DISCONNECTED
        ),
        steam_app_id=app_id,
        library_path=library,
        steam_build_id=build_id,
        steam_manifest_path=manifest,
        steam_manifest_mtime_ns=manifest_stat.st_mtime_ns,
        steam_manifest_size_bytes=manifest_stat.st_size,
        steam_size_on_disk_bytes=4096,
        state_flags=state_flags,
        update_in_progress=update_in_progress,
        library_available=library_available,
    )


def _write_manifest(
    game: Game,
    *,
    build_id: str = "100",
    state_flags: int = 4,
    size_on_disk: int = 4096,
    app_id: str | None = None,
    install_dir: str | None = None,
    mtime_ns: int | None = None,
) -> None:
    assert game.steam_manifest_path is not None
    game.steam_manifest_path.write_text(
        '"AppState" {'
        f' "appid" "{app_id or game.steam_app_id}"'
        f' "name" "{game.name}"'
        f' "installdir" "{install_dir or game.install_path.name}"'
        f' "SizeOnDisk" "{size_on_disk}" "StateFlags" "{state_flags}"'
        f' "buildid" "{build_id}"'
        " }",
        encoding="utf-8",
    )
    if mtime_ns is not None:
        os.utime(game.steam_manifest_path, ns=(mtime_ns, mtime_ns))


def _snapshot(
    root: Path,
    records: dict[str, tuple[int, int, int]],
) -> FingerprintSnapshot:
    files = tuple(
        FileFingerprint(path, size, mtime, ctime)
        for path, (size, mtime, ctime) in sorted(records.items())
    )
    return FingerprintSnapshot(
        root_path=os.path.abspath(root),
        root_device=1,
        files=files,
        complete=True,
        logical_bytes=sum(item.size for item in files),
    )


def test_steam_manifest_metadata_round_trips_through_provider_cache_and_presenter(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Steam"
    game_path = root / "steamapps" / "common" / "Fixture"
    game_path.mkdir(parents=True)
    manifest = root / "steamapps" / "appmanifest_42.acf"
    manifest.write_text(
        '"AppState" {'
        ' "appid" "42" "name" "Fixture" "installdir" "Fixture"'
        ' "SizeOnDisk" "4096" "StateFlags" "1028" "buildid" "9001"'
        " }",
        encoding="utf-8",
    )
    provider = SteamGameProvider(_Filesystem(), roots=[root])

    game = provider.refresh()[0]

    assert game.steam_build_id == "9001"
    assert game.steam_manifest_path == manifest.absolute()
    assert game.steam_manifest_mtime_ns == manifest.stat().st_mtime_ns
    assert game.steam_manifest_size_bytes == manifest.stat().st_size
    assert game.steam_size_on_disk_bytes == 4096
    assert game.update_in_progress is True

    cache = LibraryCache(tmp_path / "library.json")
    cache.save((game,))
    restored = cache.load()[0]
    assert restored.steam_build_id == "9001"
    assert restored.steam_manifest_mtime_ns == game.steam_manifest_mtime_ns
    assert restored.update_in_progress is True
    presented = game_to_qml(restored)
    assert presented["steamBuildId"] == "9001"
    assert presented["steamSizeOnDiskBytes"] == 4096
    assert presented["updateInProgress"] is True


def test_library_cache_migrates_v1_game_with_safe_update_defaults(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    raw = game.to_dict()
    for key in (
        "steam_build_id",
        "steam_manifest_path",
        "steam_manifest_mtime_ns",
        "steam_manifest_size_bytes",
        "steam_size_on_disk_bytes",
        "update_in_progress",
    ):
        raw.pop(key)
    raw["state_flags"] = 1028
    path = tmp_path / "library.json"
    path.write_text(
        json.dumps({"version": 1, "games": [raw]}),
        encoding="utf-8",
    )

    restored = LibraryCache(path).load()[0]

    assert CACHE_FORMAT_VERSION == 2
    assert restored.steam_build_id is None
    assert restored.update_in_progress is True


def test_manifest_replaced_during_read_is_not_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Steam"
    game_path = root / "steamapps" / "common" / "Fixture"
    game_path.mkdir(parents=True)
    manifest = root / "steamapps" / "appmanifest_42.acf"
    manifest.write_text(
        '"AppState" { "appid" "42" "name" "Fixture"'
        ' "installdir" "Fixture" "StateFlags" "4" }',
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement.acf"
    replacement.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    real_read = os.read
    replaced = False

    def replacing_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = real_read(descriptor, size)
        if chunk and not replaced:
            replaced = True
            replacement.replace(manifest)
        return chunk

    monkeypatch.setattr("game_optimization_linux.providers.steam.os.read", replacing_read)
    provider = SteamGameProvider(_Filesystem(), roots=[root])

    assert provider.refresh() == ()
    assert provider.last_report.invalid_manifests == 1


def test_fingerprint_scanner_never_follows_symlinks(tmp_path: Path) -> None:
    game = tmp_path / "game"
    outside = tmp_path / "outside"
    (game / "nested").mkdir(parents=True)
    outside.mkdir()
    (game / "one.bin").write_bytes(b"one")
    (game / "nested" / "two.bin").write_bytes(b"two")
    (outside / "secret.bin").write_bytes(b"secret")
    (game / "outside-link").symlink_to(outside, target_is_directory=True)

    snapshot = GameFingerprintScanner().scan(game)

    assert snapshot.complete
    assert tuple(item.relative_path for item in snapshot.files) == (
        "nested/two.bin",
        "one.bin",
    )
    assert snapshot.symlink_count == 1
    assert snapshot.logical_bytes == 6


def test_fingerprint_scanner_rejects_root_symlink_and_honors_cancel(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    scanner = GameFingerprintScanner()

    rejected = scanner.scan(link)
    assert rejected.complete is False
    assert rejected.errors == ("root-is-not-a-real-directory",)

    cancelled = Event()
    cancelled.set()
    with pytest.raises(UpdateScanCancelled):
        scanner.scan(actual, cancel_event=cancelled)


def test_diff_reports_new_modified_deleted_and_no_change(tmp_path: Path) -> None:
    before = _snapshot(
        tmp_path,
        {
            "deleted.bin": (2, 1, 1),
            "modified.bin": (3, 1, 1),
            "same.bin": (4, 1, 1),
        },
    )
    after = _snapshot(
        tmp_path,
        {
            "modified.bin": (5, 2, 2),
            "new.bin": (6, 1, 1),
            "same.bin": (4, 1, 1),
        },
    )

    changes = diff_fingerprints(before, after)

    assert changes.new_files == ("new.bin",)
    assert changes.modified_files == ("modified.bin",)
    assert changes.deleted_files == ("deleted.bin",)
    assert changes.unchanged_files == 1
    assert changes.changed_bytes == 11
    assert changes.reliable
    assert diff_fingerprints(after, after).has_changes is False


def test_tracker_first_scan_is_inventory_then_stabilizes_an_update(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock_value = [now]
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "state" / "updates.json"),
        stability_delay_seconds=60,
        clock=lambda: clock_value[0],
    )
    game = _game(tmp_path / "game")
    before = _snapshot(game.install_path, {"data.bin": (10, 1, 1)})

    inventory = tracker.observe(game, snapshot=before)
    assert inventory.status is GameUpdateStatus.INVENTORY
    tracker.complete_initial_inventory()
    assert tracker.observe(game, snapshot=before).status is GameUpdateStatus.UP_TO_DATE

    _write_manifest(game, build_id="101", mtime_ns=2)
    after = _snapshot(game.install_path, {"data.bin": (12, 2, 2)})
    waiting = tracker.observe(game, snapshot=after)
    assert waiting.status is GameUpdateStatus.WAITING_FOR_STABILITY

    clock_value[0] += timedelta(seconds=59)
    assert (
        tracker.observe(game, snapshot=after).status
        is GameUpdateStatus.WAITING_FOR_STABILITY
    )
    clock_value[0] += timedelta(seconds=1)
    detected = tracker.observe(game, snapshot=after)
    assert detected.status is GameUpdateStatus.ANALYSIS_REQUIRED
    assert detected.changes.modified_files == ("data.bin",)
    assert detected.requires_full_analysis is True
    assert detected.installation_detected is False


def test_tracker_restarts_safety_delay_when_files_change_again(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock_value = [now]
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"),
        stability_delay_seconds=60,
        clock=lambda: clock_value[0],
    )
    game = _game(tmp_path / "game")
    baseline = _snapshot(game.install_path, {"data.bin": (10, 1, 1)})
    tracker.observe(game, snapshot=baseline)
    tracker.complete_initial_inventory()
    tracker.observe(game, snapshot=baseline)
    _write_manifest(game, build_id="101", mtime_ns=2)

    first_change = _snapshot(game.install_path, {"data.bin": (12, 2, 2)})
    assert tracker.observe(game, snapshot=first_change).status is GameUpdateStatus.WAITING_FOR_STABILITY
    clock_value[0] += timedelta(seconds=50)
    second_change = _snapshot(game.install_path, {"data.bin": (14, 3, 3)})
    assert tracker.observe(game, snapshot=second_change).status is GameUpdateStatus.WAITING_FOR_STABILITY
    clock_value[0] += timedelta(seconds=59)
    assert tracker.observe(game, snapshot=second_change).status is GameUpdateStatus.WAITING_FOR_STABILITY
    clock_value[0] += timedelta(seconds=1)
    assert tracker.observe(game, snapshot=second_change).status is GameUpdateStatus.ANALYSIS_REQUIRED


def test_tracker_detects_new_install_only_after_initial_inventory(
    tmp_path: Path,
) -> None:
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"),
        stability_delay_seconds=0,
    )
    tracker.complete_initial_inventory()
    game = _game(tmp_path / "new-game", app_id="77")
    snapshot = _snapshot(game.install_path, {"payload.bin": (99, 1, 1)})

    detected = tracker.observe(game, snapshot=snapshot)

    assert detected.status is GameUpdateStatus.ANALYSIS_REQUIRED
    assert detected.installation_detected is True
    assert detected.requires_full_analysis is True
    assert detected.changes.new_files == ("payload.bin",)


def test_manifest_change_without_file_change_still_requires_analysis(
    tmp_path: Path,
) -> None:
    store = GameUpdateStateStore(tmp_path / "updates.json")
    tracker = GameUpdateTracker(store, stability_delay_seconds=0)
    game = _game(tmp_path / "game")
    snapshot = _snapshot(game.install_path, {"payload.bin": (99, 1, 1)})
    tracker.observe(game, snapshot=snapshot)
    tracker.complete_initial_inventory()
    tracker.mark_compression_verified(game.id)

    _write_manifest(game, build_id="101", size_on_disk=8192, mtime_ns=2)
    detected = tracker.observe(game, snapshot=snapshot)

    assert detected.status is GameUpdateStatus.ANALYSIS_REQUIRED
    assert game.steam_build_id == "100"
    assert detected.current_observation is not None
    assert detected.current_observation.build_id == "101"
    assert detected.current_observation.install_size_bytes == 8192
    assert detected.changes.has_changes is False
    assert detected.requires_full_analysis is False

    restarted = GameUpdateTracker(store, stability_delay_seconds=0)
    assert restarted.get(game.id) == detected


@pytest.mark.parametrize(
    ("manifest_kwargs", "error_fragment"),
    (
        ({"app_id": "77"}, "AppID"),
        ({"install_dir": "AnotherGame"}, "installdir"),
    ),
)
def test_tracker_rejects_manifest_for_another_installation(
    tmp_path: Path,
    manifest_kwargs: dict[str, str],
    error_fragment: str,
) -> None:
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"),
        stability_delay_seconds=0,
    )
    game = _game(tmp_path / "game")
    _write_manifest(game, **manifest_kwargs)
    snapshot = _snapshot(game.install_path, {"payload.bin": (1, 1, 1)})

    rejected = tracker.observe(game, snapshot=snapshot)

    assert rejected.status is GameUpdateStatus.ERROR
    assert error_fragment in rejected.last_error
    assert rejected.pending_observation is not None
    assert rejected.pending_observation.manifest_error == rejected.last_error


def test_tracker_never_follows_an_appmanifest_symlink(tmp_path: Path) -> None:
    store = GameUpdateStateStore(tmp_path / "updates.json")
    tracker = GameUpdateTracker(store, stability_delay_seconds=0)
    game = _game(tmp_path / "game")
    assert game.steam_manifest_path is not None
    target = tmp_path / "untrusted.acf"
    target.write_bytes(game.steam_manifest_path.read_bytes())
    game.steam_manifest_path.unlink()
    game.steam_manifest_path.symlink_to(target)
    snapshot = _snapshot(game.install_path, {"payload.bin": (1, 1, 1)})

    rejected = tracker.observe(game, snapshot=snapshot)

    assert rejected.status is GameUpdateStatus.ERROR
    assert "could not be verified" in rejected.last_error
    restored = GameUpdateTracker(store, stability_delay_seconds=0).get(game.id)
    assert restored == rejected


def test_tracker_rejects_manifest_replaced_while_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"),
        stability_delay_seconds=0,
    )
    game = _game(tmp_path / "game")
    assert game.steam_manifest_path is not None
    replacement = tmp_path / "replacement.acf"
    replacement.write_bytes(game.steam_manifest_path.read_bytes())
    real_read = os.read
    replaced = False

    def replacing_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = real_read(descriptor, size)
        if chunk and not replaced:
            replaced = True
            replacement.replace(game.steam_manifest_path)
        return chunk

    monkeypatch.setattr(
        "game_optimization_linux.services.game_updates.os.read",
        replacing_read,
    )
    snapshot = _snapshot(game.install_path, {"payload.bin": (1, 1, 1)})

    rejected = tracker.observe(game, snapshot=snapshot)

    assert rejected.status is GameUpdateStatus.ERROR
    assert "changed while it was read" in rejected.last_error


def test_tracker_waits_for_steam_and_preserves_disconnected_state(
    tmp_path: Path,
) -> None:
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"),
        stability_delay_seconds=0,
    )
    tracker.complete_initial_inventory()
    busy = _game(tmp_path / "game", update_in_progress=True)
    assert (
        tracker.observe(busy).status
        is GameUpdateStatus.WAITING_FOR_LAUNCHER
    )

    unavailable = replace(
        busy,
        update_in_progress=False,
        library_available=False,
        status=GameStatus.DRIVE_DISCONNECTED,
    )
    record = tracker.observe(unavailable)
    assert record.status is GameUpdateStatus.LIBRARY_UNAVAILABLE


def test_tracker_uses_verified_compression_baseline_and_ignore(
    tmp_path: Path,
) -> None:
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"),
        stability_delay_seconds=0,
    )
    game = _game(tmp_path / "game")
    baseline = _snapshot(game.install_path, {"old.bin": (1, 1, 1)})
    tracker.observe(game, snapshot=baseline)
    tracker.complete_initial_inventory()
    tracker.mark_compression_verified(game.id)

    _write_manifest(game, build_id="101", mtime_ns=2)
    changed = _snapshot(
        game.install_path,
        {
            "old.bin": (1, 1, 1),
            "new.bin": (2, 2, 2),
        },
    )
    detected = tracker.observe(game, snapshot=changed)
    assert detected.requires_full_analysis is False
    assert detected.changes.new_files == ("new.bin",)

    ignored = tracker.ignore(game.id)
    assert ignored.status is GameUpdateStatus.IGNORED
    assert tracker.observe(game, snapshot=changed) == ignored


def test_update_state_store_is_atomic_restartable_and_migrates_v1(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "updates.json"
    store = GameUpdateStateStore(path)
    store.save(UpdateStateDatabase(initial_inventory_complete=True))
    assert store.load().initial_inventory_complete is True
    assert json.loads(path.read_text(encoding="utf-8"))["format_version"] == (
        UPDATE_STATE_FORMAT_VERSION
    )
    assert not list(path.parent.glob("*.tmp"))
    assert not list(path.parent.glob(".*.tmp"))

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "initial_inventory_complete": True,
                "records": [],
            }
        ),
        encoding="utf-8",
    )
    assert store.load().initial_inventory_complete is True

    path.write_text("{broken", encoding="utf-8")
    assert store.load() == UpdateStateDatabase()


@pytest.mark.parametrize("mode", list(AutomaticCompressionMode))
def test_automatic_compression_settings_round_trip_with_safe_defaults(
    mode: AutomaticCompressionMode,
) -> None:
    defaults = AppSettings()
    assert defaults.automatic_updates is True
    assert defaults.automatic_compression_mode is AutomaticCompressionMode.OFF
    assert defaults.automatic_compression_max_jobs == 1

    settings = replace(
        defaults,
        automatic_compression_mode=mode,
        automatic_compression_profile=CompressionProfile.BALANCED,
        automatic_compression_delay_seconds=420,
        automatic_compression_max_jobs=1,
        automatic_compression_min_free_gb=25.0,
        automatic_compression_notify=False,
        automatic_compression_skipped_app_ids=("42", "77"),
        automatic_compression_libraries=(Path("/games"),),
    )

    restored = AppSettings.from_dict(settings.to_dict())
    assert restored == settings
    presented = settings_to_qml(restored)
    assert presented["automaticCompressionMode"] == mode.value
    assert presented["automaticCompressionSkippedAppIds"] == ["42", "77"]
