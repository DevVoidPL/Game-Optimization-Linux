from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gameforge.models import (
    AnalysisReport,
    AppSettings,
    BackupStatus,
    CompressionProfile,
    FilesystemType,
    Game,
    Launcher,
    OptimizationOptions,
    TextureOptions,
    TaskStatus,
    ThemeMode,
)
from gameforge.providers import (
    DemoCompressionProvider,
    DemoFilesystemProvider,
    DemoGameProvider,
    DemoOptimizationProvider,
    DemoSystemProvider,
)
from gameforge.services import (
    DemoBackupService,
    DemoTaskService,
    InvalidTaskTransitionError,
    SettingsStore,
    SettingsStoreError,
    TaskServiceError,
)


def test_demo_library_matches_product_brief() -> None:
    games = DemoGameProvider().list_games()

    assert [game.name for game in games] == [
        "Batman: Arkham Knight",
        "Dying Light",
        "Cyberpunk 2077",
        "Minecraft",
    ]
    assert [(game.launcher, game.logical_size_gb) for game in games] == [
        (Launcher.STEAM, 72.4),
        (Launcher.STEAM, 38.7),
        (Launcher.HEROIC, 86.2),
        (Launcher.MANUAL, 4.8),
    ]
    assert games[0].filesystem is FilesystemType.BTRFS
    assert games[1].saved_space_gb == 6.3
    assert games[2].filesystem is FilesystemType.EXT4
    assert not games[2].compression_available


def test_compression_provider_rejects_non_btrfs_without_side_effects() -> None:
    game = DemoGameProvider().get_game("cyberpunk-2077")
    assert game is not None

    estimate = DemoCompressionProvider().estimate(
        game, CompressionProfile.MAXIMUM
    )

    assert not estimate.compatible
    assert estimate.estimated_savings_gb == 0.0
    assert estimate.estimated_size_gb == game.physical_size_gb


def test_filesystem_provider_rejects_parent_traversal_without_host_lookup() -> None:
    provider = DemoFilesystemProvider()

    valid = provider.inspect(Path("/demo/steam/Games/example"))
    escaped = provider.inspect(Path("/demo/steam/../heroic/example"))

    assert valid.filesystem is FilesystemType.BTRFS
    assert escaped.filesystem is FilesystemType.UNKNOWN
    assert not escaped.compression_supported


def test_task_queue_is_fifo_and_completes_with_demo_report() -> None:
    games = DemoGameProvider()
    batman = games.get_game("batman-arkham-knight")
    dying_light = games.get_game("dying-light")
    assert batman is not None and dying_light is not None
    service = DemoTaskService()

    analysis = service.enqueue_analysis(batman)
    compression = service.enqueue_compression(
        dying_light, CompressionProfile.BALANCED
    )
    service.tick(step=25.0)

    assert service.get_task(analysis.id).status is TaskStatus.ANALYZING  # type: ignore[union-attr]
    assert service.get_task(compression.id).status is TaskStatus.QUEUED  # type: ignore[union-attr]

    for _ in range(3):
        service.tick(step=25.0)
    completed = service.get_task(analysis.id)
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
    assert completed.progress == 100.0
    assert completed.result is not None
    assert completed.result["game_id"] == batman.id

    service.tick(step=10.0)
    next_task = service.get_task(compression.id)
    assert next_task is not None
    assert next_task.status is TaskStatus.RUNNING


def test_task_pause_resume_cancel_transitions() -> None:
    game = DemoGameProvider().list_games()[0]
    service = DemoTaskService()
    task = service.enqueue_analysis(game)
    service.tick()

    assert service.pause(task.id).status is TaskStatus.PAUSED
    paused_progress = service.get_task(task.id).progress  # type: ignore[union-attr]
    service.tick()
    assert service.get_task(task.id).progress == paused_progress  # type: ignore[union-attr]
    assert service.resume(task.id).status is TaskStatus.ANALYZING
    assert service.cancel(task.id).status is TaskStatus.CANCELLED
    with pytest.raises(InvalidTaskTransitionError):
        service.resume(task.id)


def test_task_provider_error_becomes_failed_state_without_escaping_tick() -> None:
    class BrokenCompressionProvider(DemoCompressionProvider):
        def analyze(self, game: Game) -> AnalysisReport:
            raise RuntimeError("demo provider failure")

    game = DemoGameProvider().list_games()[0]
    service = DemoTaskService(BrokenCompressionProvider())
    task = service.enqueue_analysis(game)

    service.tick(step=100.0)

    failed = service.get_task(task.id)
    assert failed is not None
    assert failed.status is TaskStatus.FAILED
    assert failed.result is None
    assert failed.error == "demo provider failure"


@pytest.mark.parametrize("step", [float("nan"), float("inf"), float("-inf")])
def test_task_queue_rejects_non_finite_steps(step: float) -> None:
    service = DemoTaskService()

    with pytest.raises(ValueError):
        service.tick(step)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_domain_models_reject_non_finite_sizes(value: float) -> None:
    game = DemoGameProvider().list_games()[0]
    system = DemoSystemProvider().collect()

    with pytest.raises(ValueError):
        replace(game, logical_size_gb=value)
    with pytest.raises(ValueError):
        replace(system, ram_gb=value)
    with pytest.raises(ValueError):
        TextureOptions(max_vram_gb=value)


def test_incompatible_compression_is_not_queued() -> None:
    game = DemoGameProvider().get_game("cyberpunk-2077")
    assert game is not None
    service = DemoTaskService()

    with pytest.raises(TaskServiceError):
        service.enqueue_compression(game)
    assert service.list_tasks() == ()


def test_backup_actions_only_change_in_memory_records() -> None:
    game = DemoGameProvider().list_games()[0]
    service = DemoBackupService(backups=())

    backup = service.create_backup(game, "Before demo compression")
    assert service.get_backup(backup.id) == backup
    restored = service.restore_backup(backup.id)
    assert restored.status is BackupStatus.RESTORED
    service.delete_backup(backup.id)
    assert service.list_backups() == ()


def test_settings_store_round_trip_is_atomic(tmp_path) -> None:
    settings_path = tmp_path / "nested" / "settings.json"
    store = SettingsStore(settings_path)
    settings = replace(
        AppSettings(),
        language="Polski",
        theme=ThemeMode.DARK,
        cpu_limit_percent=60,
        library_directories=(tmp_path / "games",),
    )

    store.save(settings)

    assert store.load() == settings
    assert not list(settings_path.parent.glob("*.tmp"))
    assert not list(settings_path.parent.glob(".*.tmp"))


def test_settings_store_reports_bad_json_and_can_recover(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not-json", encoding="utf-8")
    store = SettingsStore(settings_path)

    with pytest.raises(SettingsStoreError):
        store.load()
    assert store.load_or_default() == AppSettings()
    assert store.last_error is not None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("backup_directory", ""),
        ("quarantine_directory", "  "),
        ("library_directories", [""]),
    ],
)
def test_settings_reject_empty_directory_values(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        AppSettings.from_dict({field_name: value})


@pytest.mark.parametrize("field_name", ["cpu_limit_percent", "gpu_limit_percent"])
def test_settings_percentage_rejects_boolean(field_name: str) -> None:
    with pytest.raises(ValueError):
        AppSettings(**{field_name: True})  # type: ignore[arg-type]


def test_settings_store_wraps_invalid_target_path() -> None:
    store = SettingsStore(Path("."))

    with pytest.raises(SettingsStoreError):
        store.save(AppSettings())


def test_optimization_preview_is_display_only_and_warns_for_anticheat() -> None:
    game = DemoGameProvider().get_game("dying-light")
    assert game is not None
    provider = DemoOptimizationProvider()
    options = OptimizationOptions(optiscaler=True, gamescope=True, fps_limit=60)

    preview = provider.preview_command(game, options)
    compatibility = provider.compatibility(game, options)

    assert preview.endswith("%command%")
    assert game.name not in preview
    assert str(game.install_path) not in preview
    assert "optiscaler-preview" not in preview
    assert not compatibility.compatible
    assert any("anti-cheat" in warning for warning in compatibility.warnings)


def test_optiscaler_stays_unverified_without_anticheat() -> None:
    game = DemoGameProvider().get_game("batman-arkham-knight")
    assert game is not None
    provider = DemoOptimizationProvider()
    options = OptimizationOptions(optiscaler=True)

    preview = provider.preview_command(game, options)
    compatibility = provider.compatibility(game, options)

    assert preview.endswith("%command%")
    assert "optiscaler" not in preview.casefold()
    assert not compatibility.compatible
    assert any("separate check" in warning for warning in compatibility.warnings)


@pytest.mark.parametrize("game_id", ["cyberpunk-2077", "minecraft"])
def test_steam_preview_is_unavailable_for_other_launchers(game_id: str) -> None:
    game = DemoGameProvider().get_game(game_id)
    assert game is not None

    preview = DemoOptimizationProvider().preview_command(
        game, OptimizationOptions()
    )

    assert "Steam launch options are unavailable" in preview
    assert "%command%" not in preview


def test_demo_system_information_is_clearly_marked() -> None:
    system = DemoSystemProvider().get_system_info()

    assert system.demo
    assert "Demo" in system.distribution
    assert system.capabilities["GameMode"].value == "Available"
