"""Behavioural checks: a saved setting must change what the app actually does.

Every test uses synthetic data in ``tmp_path`` or fake services. Nothing here
reads or writes a real Steam library. Real-Btrfs execution of each profile is
covered by ``test_btrfs_compression_integration.py`` (opt-in, disposable dir).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import (
    AppSettings,
    AutomaticCompressionMode,
    CompressionProfile,
    FilesystemType,
    GamepadAction,
    GamepadEvent,
    TaskStatus,
)
from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
from game_optimization_linux.services import (
    GamepadService,
    GameUpdateStateStore,
    GameUpdateStatus,
    GameUpdateTracker,
    SettingsStore,
)
from tests.test_compression_controller_integration import (
    _analysis_report,
    _CompressionService,
    _FilesystemProvider,
    _game,
    _GameProvider,
    _record,
    _SystemProvider,
    _TaskService,
    _UpdateTracker,
)


def _automatic_controller(
    tmp_path: Path,
    settings: AppSettings,
    *,
    games=None,
    records=None,
    with_report: bool = True,
):
    games = tuple(games or (_game(tmp_path),))
    records = tuple(
        records
        or (_record(game, GameUpdateStatus.ANALYSIS_REQUIRED) for game in games)
    )
    tasks = _TaskService()
    compression = _CompressionService()
    controller = AppController(
        game_provider=_GameProvider(games),
        task_service=tasks,
        settings_store=SettingsStore(
            tmp_path / "settings.json", default_factory=lambda: settings
        ),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=_UpdateTracker(records),  # type: ignore[arg-type]
        initial_games=games,
        demo_mode=False,
        auto_refresh=False,
    )
    if with_report:
        for game in games:
            controller._analysis_reports[game.id] = _analysis_report(game)
    return controller, tasks, compression, games


def _pad_controller(tmp_path: Path):
    service = GamepadService(FakeGamepadProvider())
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        gamepad_service=service,
        auto_refresh=False,
    )
    return controller, service._mapper


# -- Automatic compression ----------------------------------------------------


def test_automatic_profile_is_the_profile_of_the_queued_plan(tmp_path: Path) -> None:
    controller, tasks, compression, _ = _automatic_controller(
        tmp_path,
        AppSettings(
            automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
            automatic_compression_profile=CompressionProfile.MAXIMUM,
            # The manual default must not leak into unattended work.
            default_compression_profile=CompressionProfile.FAST,
            automatic_compression_notify=False,
        ),
    )
    try:
        controller._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 1
        assert tasks.compression_calls[0]["plan"].profile is CompressionProfile.MAXIMUM
        assert tasks.compression_calls[0]["automatic_authorized"] is True
        assert compression.prepare_calls[-1]["profile"] is CompressionProfile.MAXIMUM
    finally:
        controller.shutdown()


def test_switching_automatic_mode_off_stops_new_work(tmp_path: Path) -> None:
    controller, tasks, _, _ = _automatic_controller(
        tmp_path,
        AppSettings(
            automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
            automatic_compression_notify=False,
        ),
        with_report=False,
    )
    try:
        assert controller.saveSetting("automaticCompressionMode", "Off") is True
        controller._queue_eligible_automatic_compression()
        assert tasks.analysis_calls == []
        assert tasks.compression_calls == []
    finally:
        controller.shutdown()


def test_switching_automatic_mode_on_queues_exactly_once(tmp_path: Path) -> None:
    controller, tasks, _, games = _automatic_controller(
        tmp_path, AppSettings(automatic_compression_notify=False), with_report=False
    )
    try:
        assert tasks.analysis_calls == []
        assert controller.saveSetting("automaticCompressionMode", "After game update")
        assert tasks.analysis_calls == [games[0].id]
        # Saving again or re-running the scheduler must not stage a duplicate.
        assert controller.saveSetting("automaticCompressionMode", "After game update")
        controller._queue_eligible_automatic_compression()
        controller._queue_eligible_automatic_compression()
        assert tasks.analysis_calls == [games[0].id]
        assert tasks.compression_calls == []
    finally:
        controller.shutdown()


def test_mode_distinguishes_installation_from_update(tmp_path: Path) -> None:
    game = _game(tmp_path)
    controller, tasks, _, _ = _automatic_controller(
        tmp_path,
        AppSettings(
            automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
            automatic_compression_notify=False,
        ),
        games=(game,),
        records=(_record(game, GameUpdateStatus.ANALYSIS_REQUIRED, installation=True),),
        with_report=False,
    )
    try:
        controller._queue_eligible_automatic_compression()
        assert tasks.analysis_calls == []  # installation, but only updates allowed
        assert controller.saveSetting(
            "automaticCompressionMode", "After new game installation"
        )
        assert tasks.analysis_calls == [game.id]
    finally:
        controller.shutdown()


def test_max_jobs_limits_parallel_automatic_work(tmp_path: Path) -> None:
    games = tuple(_game(tmp_path, app_id=str(100 + index)) for index in range(3))
    controller, tasks, _, _ = _automatic_controller(
        tmp_path,
        AppSettings(
            automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
            automatic_compression_max_jobs=2,
            automatic_compression_notify=False,
        ),
        games=games,
    )
    try:
        controller._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 2
        # Both still active: no third job and no duplicate of the first two.
        controller._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 2
        assert len({call["game"].id for call in tasks.compression_calls}) == 2
        # One finishes -> exactly one more slot, for the game not yet queued.
        tasks.tasks[0].status = TaskStatus.COMPLETED
        controller._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 3
        assert {call["game"].id for call in tasks.compression_calls} == {
            game.id for game in games
        }
    finally:
        controller.shutdown()



def test_skipped_appids_and_library_filter_exclude_games(tmp_path: Path) -> None:
    first = _game(tmp_path, app_id="501")
    second = _game(tmp_path, app_id="502")
    controller, tasks, _, _ = _automatic_controller(
        tmp_path,
        AppSettings(
            automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
            automatic_compression_max_jobs=4,
            automatic_compression_skipped_app_ids=("501",),
            automatic_compression_notify=False,
        ),
        games=(first, second),
    )
    try:
        controller._queue_eligible_automatic_compression()
        assert [call["game"].id for call in tasks.compression_calls] == [second.id]
    finally:
        controller.shutdown()

    third = _game(tmp_path / "other", app_id="503")
    controller, tasks, _, _ = _automatic_controller(
        tmp_path / "other",
        AppSettings(
            automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
            automatic_compression_libraries=(tmp_path / "elsewhere",),
            automatic_compression_notify=False,
        ),
        games=(third,),
    )
    try:
        controller._queue_eligible_automatic_compression()
        assert tasks.compression_calls == [] and tasks.analysis_calls == []
        # Allowing that library is applied immediately.
        assert controller.saveSetting(
            "automaticCompressionLibraries", [str(third.library_path)]
        )
        assert [call["game"].id for call in tasks.compression_calls] == [third.id]
    finally:
        controller.shutdown()


def test_minimum_free_space_reaches_the_plan(tmp_path: Path) -> None:
    controller, _, compression, games = _automatic_controller(
        tmp_path, AppSettings(automatic_compression_min_free_gb=7.5)
    )
    try:
        controller.prepareCompression(games[0].id, "Balanced", False)
        assert compression.prepare_calls[-1]["minimum_free_bytes"] == int(7.5 * 1024**3)
        assert controller.saveSetting("automaticCompressionMinFreeGb", 2.0)
        controller.prepareCompression(games[0].id, "Balanced", False)
        assert compression.prepare_calls[-1]["minimum_free_bytes"] == 2 * 1024**3
    finally:
        controller.shutdown()


def test_notify_setting_controls_automatic_toasts(tmp_path: Path) -> None:
    for notify in (True, False):
        controller, _, _, _ = _automatic_controller(
            tmp_path / str(notify),
            AppSettings(
                automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
                automatic_compression_notify=notify,
            ),
        )
        toasts: list[str] = []
        controller.toastRequested.connect(lambda message, _level: toasts.append(message))
        try:
            controller._queue_eligible_automatic_compression()
            automatic = [text for text in toasts if "Automatic compression" in text]
            assert bool(automatic) is notify, (notify, toasts)
        finally:
            controller.shutdown()


def test_safety_delay_change_applies_to_the_running_tracker(tmp_path: Path) -> None:
    tracker = GameUpdateTracker(
        GameUpdateStateStore(tmp_path / "updates.json"), stability_delay_seconds=300
    )
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        update_tracker=tracker,
        demo_mode=False,
        auto_refresh=False,
    )
    try:
        assert controller.saveSetting("automaticCompressionDelaySeconds", 45) is True
        assert tracker.stability_delay_seconds == 45.0
    finally:
        controller.shutdown()



# -- Automatic compression across an application restart ---------------------


def _restart_fixture(tmp_path: Path):
    """A real tracker + store on synthetic Steam data, one stable update."""

    from tests.test_game_updates import _game as steam_game
    from tests.test_game_updates import _snapshot, _write_manifest

    store_path = tmp_path / "state" / "updates.json"
    game = replace(steam_game(tmp_path / "game"), filesystem=FilesystemType.BTRFS)
    # The fake planner stats this file; the tracker uses explicit snapshots.
    (game.install_path / "payload.bin").write_bytes(b"A" * 4096)
    tracker = GameUpdateTracker(GameUpdateStateStore(store_path), stability_delay_seconds=0)
    baseline = _snapshot(game.install_path, {"payload.bin": (4096, 1, 1)})
    tracker.observe(game, snapshot=baseline)
    tracker.complete_initial_inventory()
    tracker.mark_compression_verified(game.id)
    _write_manifest(game, build_id="101", mtime_ns=2)
    updated_game = replace(
        steam_game(tmp_path / "game", build_id="101", manifest_mtime_ns=2),
        filesystem=FilesystemType.BTRFS,
    )
    changed = _snapshot(
        game.install_path, {"payload.bin": (4096, 1, 1), "new.bin": (8192, 2, 2)}
    )
    record = tracker.observe(updated_game, snapshot=changed)
    assert record.status is GameUpdateStatus.ANALYSIS_REQUIRED
    return store_path, updated_game, changed


def _app_with_store(tmp_path: Path, store_path: Path, game, *, run: int):
    tasks = _TaskService()
    compression = _CompressionService()
    tracker = GameUpdateTracker(GameUpdateStateStore(store_path), stability_delay_seconds=0)
    controller = AppController(
        game_provider=_GameProvider((game,)),
        task_service=tasks,
        settings_store=SettingsStore(
            tmp_path / "settings.json",
            default_factory=lambda: AppSettings(
                automatic_compression_mode=AutomaticCompressionMode.AFTER_UPDATE,
                automatic_compression_notify=False,
            ),
        ),
        system_provider=_SystemProvider(),
        filesystem_provider=_FilesystemProvider(),
        compression_service=compression,  # type: ignore[arg-type]
        update_tracker=tracker,
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    # The analysis cache survives a restart; the report is reloaded with it.
    controller._analysis_reports[game.id] = _analysis_report(game)
    del run
    return controller, tasks, tracker


def test_unverified_automatic_attempt_is_not_retried_after_restart(
    tmp_path: Path,
) -> None:
    store_path, game, changed = _restart_fixture(tmp_path)

    # Session 1: the update is compressed automatically, then the task ends
    # with a warning/failure, so no verified baseline is recorded.
    first, tasks, tracker = _app_with_store(tmp_path, store_path, game, run=1)
    try:
        first._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 1
        tasks.tasks[0].status = TaskStatus.FAILED
        record = tracker.get(game.id)
        assert record is not None
        # Not marked as a success: the update still needs attention.
        assert record.status is GameUpdateStatus.ANALYSIS_REQUIRED
        assert record.automatic_attempt_event
    finally:
        first.shutdown()

    # Session 2 (restart): fresh controller, fresh in-memory state, same files.
    second, tasks, tracker = _app_with_store(tmp_path, store_path, game, run=2)
    try:
        assert second._automatic_handled_events == {}
        # Steam touches the same build again; the record is re-observed.
        tracker.observe(game, snapshot=changed)
        second._queue_eligible_automatic_compression()
        second._queue_eligible_automatic_compression()
        assert tasks.compression_calls == []
        assert tasks.analysis_calls == []
        record = tracker.get(game.id)
        assert record is not None
        assert record.status is GameUpdateStatus.ANALYSIS_REQUIRED

        # A deliberate manual retry is still possible for the same event.
        presented = second.prepareCompression(game.id, "Balanced", True)
        assert presented["valid"] is True
        assert second.startCompression(presented["planId"]) is True
        assert len(tasks.compression_calls) == 1
        assert tasks.compression_calls[0]["automatic_authorized"] is False
    finally:
        second.shutdown()


def test_new_build_after_an_unverified_attempt_is_compressed_automatically(
    tmp_path: Path,
) -> None:
    from tests.test_game_updates import _game as steam_game
    from tests.test_game_updates import _snapshot, _write_manifest

    store_path, game, changed = _restart_fixture(tmp_path)
    first, tasks, _tracker = _app_with_store(tmp_path, store_path, game, run=1)
    try:
        first._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 1
    finally:
        first.shutdown()

    _write_manifest(game, build_id="102", mtime_ns=3)
    newer = replace(
        steam_game(tmp_path / "game", build_id="102", manifest_mtime_ns=3),
        filesystem=FilesystemType.BTRFS,
    )
    second, tasks, tracker = _app_with_store(tmp_path, store_path, newer, run=2)
    try:
        newer_files = _snapshot(
            newer.install_path,
            {"payload.bin": (4096, 1, 1), "new.bin": (8192, 2, 2), "v102.bin": (1, 3, 3)},
        )
        assert tracker.observe(newer, snapshot=newer_files).status is (
            GameUpdateStatus.ANALYSIS_REQUIRED
        )
        second._queue_eligible_automatic_compression()
        assert len(tasks.compression_calls) == 1
        assert tasks.compression_calls[0]["automatic_authorized"] is True
    finally:
        second.shutdown()
    del changed


# -- Controller input (synthetic events; no physical pad needed) -------------


def test_saved_deadzone_changes_which_stick_movement_navigates(tmp_path: Path) -> None:
    controller, mapper = _pad_controller(tmp_path)
    try:
        tilt = GamepadEvent("axis", 1, "left_x", True, 0.30)
        assert controller.saveSetting("analogDeadzone", 0.40)
        assert mapper.process(tilt, now=1.0) == ()
        mapper.reset()
        assert controller.saveSetting("analogDeadzone", 0.20)
        assert mapper.process(tilt, now=2.0) == (GamepadAction.NAVIGATE_RIGHT,)
    finally:
        controller.shutdown()


def test_saved_repeat_delay_and_interval_change_held_navigation(tmp_path: Path) -> None:
    controller, mapper = _pad_controller(tmp_path)
    try:
        assert controller.saveSetting("navigationRepeatDelayMs", 600)
        assert controller.saveSetting("navigationRepeatRateMs", 200)
        down = GamepadEvent("button", 1, "dpad_down", True, 1.0)
        assert mapper.process(down, now=10.0) == (GamepadAction.NAVIGATE_DOWN,)
        assert mapper.poll_repeats(now=10.59) == ()  # inside the 600 ms delay
        assert mapper.poll_repeats(now=10.61) == (GamepadAction.NAVIGATE_DOWN,)
        assert mapper.poll_repeats(now=10.80) == ()  # 200 ms interval not elapsed
        assert mapper.poll_repeats(now=10.82) == (GamepadAction.NAVIGATE_DOWN,)

        mapper.reset()
        assert controller.saveSetting("navigationRepeatDelayMs", 200)
        assert controller.saveSetting("navigationRepeatRateMs", 50)
        assert mapper.process(down, now=20.0) == (GamepadAction.NAVIGATE_DOWN,)
        assert mapper.poll_repeats(now=20.21) == (GamepadAction.NAVIGATE_DOWN,)
        assert mapper.poll_repeats(now=20.27) == (GamepadAction.NAVIGATE_DOWN,)
    finally:
        controller.shutdown()


def test_held_navigation_reaches_the_app_through_the_service(tmp_path: Path) -> None:
    """End-to-end through GamepadService.pollNow: the repeat really is emitted."""

    provider = FakeGamepadProvider()
    service = GamepadService(provider)
    actions: list[str] = []
    service.actionTriggered.connect(actions.append)
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        gamepad_service=service,
        auto_refresh=False,
    )
    try:
        assert controller.saveSetting("navigationRepeatDelayMs", 150)
        assert controller.saveSetting("navigationRepeatRateMs", 50)
        service.start()
        provider.emit(GamepadEvent("button", 1, "dpad_down", True, 1.0))
        service.pollNow()
        assert actions == ["NavigateDown"]
        import time

        time.sleep(0.2)
        service.pollNow()
        assert actions == ["NavigateDown", "NavigateDown"]
        provider.emit(GamepadEvent("button", 1, "dpad_down", False, 0.0))
        service.pollNow()
        time.sleep(0.2)
        service.pollNow()
        assert actions == ["NavigateDown", "NavigateDown"]
    finally:
        service.stop()
        controller.shutdown()


def test_desktop_sliders_cover_the_whole_saved_range() -> None:
    """A slider must not clamp a valid saved value (e.g. set in Couch Mode)."""

    source = (
        Path(__file__).resolve().parents[1]
        / "src/game_optimization_linux/qml/pages/SettingsPage.qml"
    ).read_text(encoding="utf-8")
    expected = {
        "repeatDelaySlider": ("from: 150", "to: 1500"),
        "repeatRateSlider": ("from: 50", "to: 500"),
        "deadzoneSlider": ("from: 5", "to: 75"),
    }
    for slider, bounds in expected.items():
        block = source[source.index(f"id: {slider}"):][:300]
        for bound in bounds:
            assert bound in block, (slider, bound)


def test_unused_fields_survive_other_saves(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save(
        replace(
            AppSettings(),
            cpu_limit_percent=40,
            gpu_limit_percent=55,
            experimental_features=True,
            automatic_updates=False,
            backup_directory=Path("my-backups"),
            quarantine_directory=Path("my-quarantine"),
        )
    )
    controller = AppController(
        game_provider=DemoGameProvider(), settings_store=store, auto_refresh=False
    )
    try:
        assert controller.saveSetting("themeMode", "dark")
    finally:
        controller.shutdown()
    restored = store.load()
    assert restored.cpu_limit_percent == 40
    assert restored.gpu_limit_percent == 55
    assert restored.experimental_features is True
    assert restored.automatic_updates is False
    assert restored.backup_directory == Path("my-backups")
    assert restored.quarantine_directory == Path("my-quarantine")
