from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import (
    BottleneckAnalysis,
    DetectedValue,
    FilesystemType,
    FrameRateAnalysis,
    Game,
    GameFingerprint,
    Launcher,
    MangoHudProfile,
    OptimizationAnalysis,
    PerformanceMeasurement,
    SystemSnapshot,
)
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import (
    BaselineSessionRepository,
    GameSettingsAdvisor,
    MangoHudProfileRepository,
    MockTaskService,
    OptimizationAnalysisRepository,
    OptimizationChangeService,
    SettingsStore,
)


def _state(tmp_path: Path):
    root = tmp_path / "game"
    config = root / "Config/GameUserSettings.ini"
    config.parent.mkdir(parents=True)
    config.write_text(
        "[ScalabilityGroups]\nsg.ShadowQuality=3\nForeign.Mod=keep\n",
        encoding="utf-8",
    )
    executable = root / "Project/Binaries/Win64/Project.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ synthetic")
    game = Game(
        id="steam-440001",
        name="Persistent Synthetic Game",
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=1,
        physical_size_gb=1,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id="440001",
        executable_path="Project/Binaries/Win64/Project.exe",
        data_source="Steam",
    )
    unknown = DetectedValue("Unknown", 0.0, "not detected")
    fingerprint = GameFingerprint(
        game.id,
        "440001",
        game.name,
        "Steam",
        str(root),
        str(executable),
        str(executable.parent),
        DetectedValue("Proton", 0.9, "Steam compatibility data"),
        DetectedValue("64-bit", 1.0, "PE header"),
        DetectedValue("Unreal Engine", 0.95, "filesystem signatures"),
        "5.0",
        DetectedValue("Direct3D 12", 0.8, "renderer configuration"),
        (DetectedValue("Direct3D 12", 0.8, "renderer configuration"),),
        unknown,
        False,
        (str(config.parent),),
        "",
        SystemSnapshot("CPU", "GPU", 8, 16, "Display", 1920, 1080, 60),
    )
    measurement = PerformanceMeasurement(
        str(tmp_path / "baseline.csv"),
        900,
        90,
        60.2,
        55,
        57,
        16.61,
        17.2,
        18.0,
        20,
        65,
        4096,
        5500,
        67,
        quality="high",
        total_samples=900,
        selected_duration_seconds=90,
        representative=True,
    )
    bottleneck = BottleneckAnalysis(
        "balanced", 0.82, ("No saturated resource",), ()
    )
    frame_rate = FrameRateAnalysis(
        "likely_capped", 60, 0.97, ("Stable frame ceiling",), ()
    )
    settings, candidates = GameSettingsAdvisor().analyze(
        game, fingerprint, measurement, bottleneck, frame_rate
    )
    analysis = OptimizationAnalysis(
        fingerprint, measurement, bottleneck, candidates, frame_rate, settings
    )
    return game, config, measurement, analysis


def _controller(
    tmp_path: Path,
    game: Game,
    analyses: OptimizationAnalysisRepository,
    sessions: BaselineSessionRepository,
    changes: OptimizationChangeService,
    mango: MangoHudProfileRepository | None = None,
) -> AppController:
    controller = AppController(
        game_provider=DemoGameProvider((game,)),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        optimization_analysis_repository=analyses,
        mangohud_repository=mango,
        initial_games=(game,),
        auto_refresh=False,
    )
    controller._baseline_sessions = sessions
    controller._optimization_change_service = changes
    return controller


def test_analysis_baseline_bottleneck_and_settings_survive_restart(
    tmp_path: Path,
) -> None:
    game, _config, measurement, analysis = _state(tmp_path)
    analyses = OptimizationAnalysisRepository(tmp_path / "analysis")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    changes = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    analyses.save("440001", analysis)
    sessions.save_measurement("440001", measurement, slot="before")

    restarted = _controller(
        tmp_path / "restart", game, analyses, sessions, changes
    )
    try:
        restored = restarted.getGameOptimizationAnalysis(game.id)
    finally:
        restarted.shutdown()

    assert restored["analysisCacheState"] == "cached"
    assert restored["fingerprint"]["mainExecutable"].endswith("Project.exe")
    assert restored["fingerprint"]["engine"]["confidence"] == 0.95
    assert restored["measurement"]["averageFps"] == 60.2
    assert restored["bottleneck"]["conclusion"] == "balanced"
    assert restored["frameRate"]["estimatedCeilingFps"] == 60
    assert restored["settingsAnalysis"]["detected"][0]["value"] == "3"
    assert restored["baselineStale"] is False


def test_changed_config_refreshes_settings_and_preserves_historical_baseline(
    tmp_path: Path,
) -> None:
    game, config, measurement, analysis = _state(tmp_path)
    analyses = OptimizationAnalysisRepository(tmp_path / "analysis")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    changes = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    analyses.save("440001", analysis)
    sessions.save_measurement("440001", measurement, slot="before")
    config.write_text(
        "[ScalabilityGroups]\nsg.ShadowQuality=2\nForeign.Mod=keep\n",
        encoding="utf-8",
    )

    restarted = _controller(
        tmp_path / "restart", game, analyses, sessions, changes
    )
    try:
        restored = restarted.getGameOptimizationAnalysis(game.id)
        manual = restarted.previewGameSettingChange(
            game.id,
            restored["settingsAnalysis"]["detected"][0]["instanceId"],
            "1",
        )
        apply_result = restarted.applyGameSettingChange(
            game.id,
            restored["settingsAnalysis"]["detected"][0]["instanceId"],
            "1",
        )
    finally:
        restarted.shutdown()

    assert restored["analysisCacheState"] == "stale"
    assert restored["baselineStale"] is True
    assert restored["measurement"]["averageFps"] == 60.2
    assert restored["settingsAnalysis"]["detected"][0]["value"] == "2"
    assert restored["settingsAnalysis"]["recommendationState"] == "baseline_stale"
    assert manual["success"] is True
    assert apply_result["success"] is False
    assert "new representative baseline" in apply_result["error"]
    assert sessions.load_measurement("440001", slot="before") == measurement


def test_user_mangohud_profile_is_independent_of_private_baseline_config(
    tmp_path: Path,
) -> None:
    game, _config, _measurement, analysis = _state(tmp_path)
    analyses = OptimizationAnalysisRepository(tmp_path / "analysis")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    changes = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    mango = MangoHudProfileRepository(
        tmp_path / "mangohud", log_root=tmp_path / "logs"
    )
    mango.save(replace(MangoHudProfile.default("440001"), preset="fps_only"))
    analyses.save("440001", analysis)
    private = sessions.create("440001", game.id)

    restarted = _controller(
        tmp_path / "restart", game, analyses, sessions, changes, mango
    )
    try:
        saved_profile = restarted.getMangoHudProfile(game.id)
    finally:
        restarted.shutdown()

    assert private.config_path.is_file()
    assert saved_profile["preset"] == "fps_only"
    assert mango.load("440001").preset == "fps_only"
    assert mango.config_path("440001") != private.config_path


def test_pending_change_before_after_and_revert_survive_restart(
    tmp_path: Path,
) -> None:
    game, config, measurement, analysis = _state(tmp_path)
    analyses = OptimizationAnalysisRepository(tmp_path / "analysis")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    changes = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    analyses.save("440001", analysis)
    sessions.save_measurement("440001", measurement, slot="before")
    first = _controller(tmp_path / "first", game, analyses, sessions, changes)
    try:
        loaded = first.getGameOptimizationAnalysis(game.id)
        setting = loaded["settingsAnalysis"]["detected"][0]
        applied = first.applyGameSettingChange(
            game.id, setting["instanceId"], "2"
        )
    finally:
        first.shutdown()
    assert applied["success"] is True
    assert "sg.ShadowQuality=2" in config.read_text(encoding="utf-8")

    after = replace(measurement, average_fps=62.0, gpu_usage_percent=58.0)
    sessions.save_measurement("440001", after, slot="after")
    restarted = _controller(
        tmp_path / "restart", game, analyses, sessions, changes
    )
    try:
        pending = restarted.getGameOptimizationAnalysis(game.id)
        reverted = restarted.revertOptimizationChange(
            game.id, pending["appliedChange"]["id"]
        )
    finally:
        restarted.shutdown()

    assert pending["appliedChange"]["state"] == "applied"
    assert pending["beforeMeasurement"]["averageFps"] == 60.2
    assert pending["afterMeasurement"]["averageFps"] == 62.0
    assert pending["comparison"]["outcome"]
    assert reverted["success"] is True
    assert "sg.ShadowQuality=3" in config.read_text(encoding="utf-8")
    assert sessions.load_measurement("440001", slot="before") == measurement
    assert sessions.load_measurement("440001", slot="after") is None


def test_keep_after_restart_preserves_history_and_promotes_comparison(
    tmp_path: Path,
) -> None:
    game, config, measurement, analysis = _state(tmp_path)
    analyses = OptimizationAnalysisRepository(tmp_path / "analysis")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    changes = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    analyses.save("440001", analysis)
    sessions.save_measurement("440001", measurement, slot="before")
    first = _controller(tmp_path / "first", game, analyses, sessions, changes)
    try:
        loaded = first.getGameOptimizationAnalysis(game.id)
        setting = loaded["settingsAnalysis"]["detected"][0]
        applied = first.applyGameSettingChange(
            game.id, setting["instanceId"], "2"
        )
    finally:
        first.shutdown()
    after = replace(measurement, average_fps=63.0, gpu_usage_percent=55.0)
    sessions.save_measurement("440001", after, slot="after")

    restarted = _controller(
        tmp_path / "restart", game, analyses, sessions, changes
    )
    try:
        pending = restarted.getGameOptimizationAnalysis(game.id)
        kept = restarted.keepOptimizationChange(
            game.id, applied["change"]["id"]
        )
    finally:
        restarted.shutdown()

    assert kept["success"] is True
    assert kept["change"]["state"] == "kept"
    assert kept["change"]["comparison"] == pending["comparison"]
    assert kept["change"]["before_measurement"]["averageFps"] == 60.2
    assert kept["change"]["after_measurement"]["averageFps"] == 63.0
    assert "sg.ShadowQuality=2" in config.read_text(encoding="utf-8")
    assert sessions.load_measurement("440001", slot="before") == after
    assert sessions.load_measurement("440001", slot="after") is None


def test_corrupt_game_state_and_old_schema_fail_independently(tmp_path: Path) -> None:
    game, _config, _measurement, analysis = _state(tmp_path)
    repository = OptimizationAnalysisRepository(tmp_path / "analysis")
    repository.save("440001", analysis)
    corrupt = repository.path("440002")
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not-json", encoding="utf-8")

    assert repository.load("440002") is None
    assert repository.load("440001") == analysis

    payload = {
        "app_id": "440003",
        "game_id": game.id,
        "analysis": analysis.to_dict(),
    }
    payload["analysis"].pop("baselineStale", None)
    payload["analysis"].pop("staleReasons", None)
    payload["analysis"]["settingsAnalysis"].pop("analyzedAt", None)
    for setting in payload["analysis"]["settingsAnalysis"]["detected"]:
        setting.pop("availableValues", None)
        setting.pop("alternativeValues", None)
    old_path = repository.path("440003")
    old_path.parent.mkdir(parents=True)
    old_path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = repository.load("440003")
    assert migrated is not None
    assert migrated.baseline_stale is False
    assert migrated.settings.detected[0].available_values == ()
