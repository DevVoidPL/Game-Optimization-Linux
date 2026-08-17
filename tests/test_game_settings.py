from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from game_optimization_linux.models import (
    BottleneckAnalysis,
    DetectedValue,
    FilesystemType,
    FrameRateAnalysis,
    Game,
    GameFingerprint,
    Launcher,
    PerformanceMeasurement,
    SystemSnapshot,
)
from game_optimization_linux.services import (
    GameSettingsAdvisor,
    OptimizationChangeService,
    compare_measurements,
)


def _game(root: Path, game_id: str = "steam-10") -> Game:
    root.mkdir(parents=True, exist_ok=True)
    return Game(
        id=game_id,
        name="Synthetic Game",
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=1,
        physical_size_gb=1,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id=game_id.removeprefix("steam-"),
        data_source="Steam",
    )


def _fingerprint(
    game: Game, engine: str, config_directories: tuple[Path, ...]
) -> GameFingerprint:
    detected = DetectedValue(engine, 0.95, "synthetic signatures")
    unknown = DetectedValue("Unknown", 0.0, "not detected")
    return GameFingerprint(
        game.id,
        str(game.steam_app_id),
        game.name,
        "Steam",
        str(game.install_path),
        "Game.exe",
        str(game.install_path),
        DetectedValue("Proton", 0.9, "test"),
        DetectedValue("64-bit", 1.0, "PE header"),
        detected,
        "",
        unknown,
        (),
        unknown,
        False,
        tuple(str(path) for path in config_directories),
        "",
        SystemSnapshot(vram_gb=8, ram_gb=16, refresh_rate=144),
    )


def _measurement(*, representative: bool = True) -> PerformanceMeasurement:
    return PerformanceMeasurement(
        "baseline.csv",
        600,
        60,
        70,
        55,
        58,
        14.3,
        16,
        18,
        45,
        98,
        4096,
        6144,
        70,
        quality="high" if representative else "low",
        total_samples=600,
        selected_duration_seconds=60,
        representative=representative,
    )


def _analysis(
    game: Game,
    fingerprint: GameFingerprint,
    *,
    bottleneck: str = "gpu_bottleneck",
    representative: bool = True,
    frame_state: str = "not_detected",
):
    measurement = _measurement(representative=representative)
    if frame_state == "likely_capped":
        measurement = replace(
            measurement,
            cpu_usage_percent=10,
            gpu_usage_percent=10,
        )
    result = GameSettingsAdvisor().analyze(
        game,
        fingerprint,
        measurement,
        BottleneckAnalysis(
            bottleneck,
            0.88 if bottleneck != "insufficient_data" else 0,
            ("Representative GPU evidence",),
            (),
        ),
        FrameRateAnalysis(frame_state, 60 if frame_state == "likely_capped" else None, 0.9, (), ()),
    )
    return measurement, result


def _unreal_config(directory: Path, text: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "GameUserSettings.ini"
    path.write_text(
        text
        or "[ScalabilityGroups]\n"
        "sg.ShadowQuality=4\n"
        "sg.EffectsQuality=3\n"
        "Custom.ModKey=keep-me\n",
        encoding="utf-8",
    )
    return path


def test_unreal_existing_scalability_settings_are_recommended_for_gpu_bound(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(game.install_path / "Config")
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))

    _measurement_value, (settings, candidates) = _analysis(game, fingerprint)

    assert settings.status == "available"
    assert {item.key for item in settings.detected} == {
        "sg.ShadowQuality", "sg.EffectsQuality"
    }
    shadow = next(item for item in candidates if item.setting_id == "unreal_shadow_quality")
    assert shadow.current_value == "4"
    assert shadow.proposed_value == "3"
    assert shadow.performance_impact == "medium"
    assert shadow.config_sha256 == hashlib.sha256(config.read_bytes()).hexdigest()
    assert "+" not in shadow.expected_effect
    assert "%" not in shadow.expected_effect


def test_unreal_without_supported_settings_preserves_unrelated_keys(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(
        game.install_path / "Config",
        "[ScalabilityGroups]\nCustom.ModKey=keep-me\n",
    )
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))

    _measurement_value, (settings, candidates) = _analysis(game, fingerprint)

    assert settings.detected == ()
    assert candidates == ()
    assert config.read_text(encoding="utf-8").endswith("Custom.ModKey=keep-me\n")


def test_redengine_existing_visual_preference_is_detected_without_performance_claim(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "red")
    config_dir = game.install_path / "r6/config"
    config_dir.mkdir(parents=True)
    config = config_dir / "user.settings"
    config.write_text(
        "[Rendering]\nAllowMotionBlur=true\nForeignMod=enabled\n",
        encoding="utf-8",
    )
    fingerprint = _fingerprint(game, "REDengine", (config_dir,))

    _measurement_value, (settings, candidates) = _analysis(game, fingerprint)

    assert settings.status == "available"
    assert settings.detected[0].id == "red_motion_blur"
    assert settings.detected[0].value == "true"
    assert candidates == ()


def test_unreal_config_is_discovered_in_actual_proton_prefix_tree(
    tmp_path: Path,
) -> None:
    steamapps = tmp_path / "SteamLibrary/steamapps"
    game = _game(steamapps / "common/Synthetic")
    config = (
        steamapps
        / "compatdata/10/pfx/drive_c/users/steamuser/AppData/Local"
        / "Synthetic/Saved/Config/Windows/GameUserSettings.ini"
    )
    config.parent.mkdir(parents=True)
    config.write_text(
        "[ScalabilityGroups]\nsg.PostProcessQuality=3\n",
        encoding="utf-8",
    )
    fingerprint = _fingerprint(game, "Unreal Engine", ())

    _measurement_value, (settings, candidates) = _analysis(game, fingerprint)

    assert settings.config_files == (str(config),)
    assert any(
        item.setting_id == "unreal_post_process_quality" for item in candidates
    )


@pytest.mark.parametrize("engine", ("Unknown", "Unity"))
def test_unsupported_engine_has_no_automatic_settings_edit(
    tmp_path: Path, engine: str
) -> None:
    game = _game(tmp_path / engine)
    fingerprint = _fingerprint(game, engine, ())

    _measurement_value, (settings, candidates) = _analysis(game, fingerprint)

    assert settings.status == "unsupported"
    assert candidates == ()


def test_missing_and_corrupt_config_fail_safely(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    missing = _fingerprint(game, "Unreal Engine", ())
    _measurement_value, (settings, candidates) = _analysis(game, missing)
    assert settings.status == "unavailable"
    assert candidates == ()

    directory = game.install_path / "Config"
    directory.mkdir()
    corrupt = directory / "GameUserSettings.ini"
    corrupt.write_bytes(b"\xff\xfe\x00broken")
    fingerprint = _fingerprint(game, "Unreal Engine", (directory,))
    _measurement_value, (settings, candidates) = _analysis(game, fingerprint)
    assert settings.status == "invalid"
    assert candidates == ()


def test_advisor_filters_balanced_capped_and_unrepresentative_measurements(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(game.install_path / "Config")
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))

    for bottleneck, representative, frame_state, expected_state in (
        ("balanced", True, "not_detected", "balanced"),
        ("balanced", True, "likely_capped", "capped_with_headroom"),
        ("gpu_bottleneck", True, "likely_capped", "capped_with_headroom"),
        ("insufficient_data", False, "unknown", "baseline_unrepresentative"),
    ):
        _measurement_value, (settings, candidates) = _analysis(
            game,
            fingerprint,
            bottleneck=bottleneck,
            representative=representative,
            frame_state=frame_state,
        )
        assert settings.detected
        assert candidates == ()
        assert settings.recommendation_state == expected_state
        if expected_state == "capped_with_headroom":
            assert "frame-limited" in settings.message


def test_balanced_workload_still_exposes_supported_manual_setting_values(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(
        game.install_path / "Config",
        "[ScalabilityGroups]\nsg.ShadowQuality=3\n",
    )
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))
    _measurement_value, (settings, candidates) = _analysis(
        game, fingerprint, bottleneck="balanced", frame_state="likely_capped"
    )

    assert candidates == ()
    shadow = settings.detected[0]
    assert shadow.available_values == ("3", "2", "1", "0")
    assert shadow.alternative_values == ("2", "1", "0")
    assert shadow.suggested_value == "2"
    assert shadow.automatically_recommended is False
    assert "frame-limited" in shadow.automatic_reason

    manual = GameSettingsAdvisor().manual_candidate(
        settings, shadow.instance_id, "2"
    )
    assert manual.current_value == "3"
    assert manual.proposed_value == "2"
    assert manual.files_to_modify == (str(config),)
    assert manual.config_key == "sg.ShadowQuality"
    assert manual.automatically_selected is False

    with pytest.raises(ValueError, match="not supported"):
        GameSettingsAdvisor().manual_candidate(settings, shadow.instance_id, "5")


def test_apply_backup_atomic_write_and_revert_preserve_unrelated_content(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path / "game")
    original = (
        b"\xef\xbb\xbf[ScalabilityGroups]\r\n"
        b"; user comment\r\n"
        b"sg.ShadowQuality=4 ; retained comment\r\n"
        b"Custom.ModKey=keep-me\r\n"
    )
    config = _unreal_config(game.install_path / "Config")
    config.write_bytes(original)
    config.chmod(0o640)
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))
    _measurement_value, (_settings, candidates) = _analysis(game, fingerprint)
    candidate = next(item for item in candidates if item.setting_id == "unreal_shadow_quality")
    service = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )

    manifest = service.apply(game, candidate)

    modified = config.read_bytes()
    assert b"sg.ShadowQuality=3 ; retained comment" in modified
    assert b"Custom.ModKey=keep-me" in modified
    assert modified.startswith(b"\xef\xbb\xbf")
    assert manifest["setting_id"] == "unreal_shadow_quality"
    assert (tmp_path / "changes/10" / manifest["id"] / "original").read_bytes() == original
    assert config.stat().st_mode & 0o777 == 0o640

    restored = service.revert(game, manifest["id"])
    assert restored["state"] == "reverted"
    assert config.read_bytes() == original


def test_apply_is_blocked_for_running_changed_or_active_cycle(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(game.install_path / "Config")
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))
    _measurement_value, (_settings, candidates) = _analysis(game, fingerprint)
    candidate = candidates[0]

    with pytest.raises(RuntimeError, match="running"):
        OptimizationChangeService(
            tmp_path / "running", process_checker=lambda _game: True
        ).apply(game, candidate)

    config.write_text(config.read_text(encoding="utf-8") + "External=1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed after analysis"):
        OptimizationChangeService(
            tmp_path / "changed", process_checker=lambda _game: False
        ).apply(game, candidate)

    _measurement_value, (_settings, current) = _analysis(game, fingerprint)
    service = OptimizationChangeService(
        tmp_path / "active", process_checker=lambda _game: False
    )
    first = service.apply(game, current[0])
    with pytest.raises(RuntimeError, match="comparison cycle"):
        service.apply(game, current[1])
    kept = service.keep(game, first["id"])
    assert kept["state"] == "kept"
    assert "sg.ShadowQuality=3" in config.read_text(encoding="utf-8")

    _measurement_value, (_settings, refreshed) = _analysis(game, fingerprint)
    second = next(
        item for item in refreshed if item.setting_id == "unreal_effects_quality"
    )
    assert service.apply(game, second)["state"] == "applied"


def test_write_failure_preserves_original_bytes(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(game.install_path / "Config")
    original = config.read_bytes()
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))
    _measurement_value, (_settings, candidates) = _analysis(game, fingerprint)
    service = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    atomic_write = service._atomic_write
    calls = 0

    def fail_after_replace(path: Path, data: bytes, mode: int) -> None:
        nonlocal calls
        calls += 1
        atomic_write(path, data, mode)
        if calls == 1:
            raise OSError("synthetic write failure")

    service._atomic_write = fail_after_replace  # type: ignore[method-assign]
    with pytest.raises(OSError, match="synthetic"):
        service.apply(game, candidates[0])
    assert config.read_bytes() == original


def test_failed_setting_readback_rolls_back_exact_original(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(
        game.install_path / "Config",
        "[ScalabilityGroups]\nsg.ShadowQuality=3\n",
    )
    original = config.read_bytes()
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))
    _measurement_value, (settings, _candidates) = _analysis(
        game, fingerprint, bottleneck="balanced"
    )
    shadow = settings.detected[0]
    candidate = GameSettingsAdvisor().manual_candidate(
        settings, shadow.instance_id, "2"
    )
    service = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    read_setting = service._read_setting_value

    def reject_applied_value(path: Path, section: str, key: str) -> str | None:
        value = read_setting(path, section, key)
        return "invalid" if value == "2" else value

    service._read_setting_value = reject_applied_value  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="read-back"):
        service.apply(game, candidate)

    assert config.read_bytes() == original
    assert service.active_change(game) is None


def test_revert_refuses_to_overwrite_newer_external_changes(tmp_path: Path) -> None:
    game = _game(tmp_path / "game")
    config = _unreal_config(game.install_path / "Config")
    fingerprint = _fingerprint(game, "Unreal Engine", (config.parent,))
    _measurement_value, (_settings, candidates) = _analysis(game, fingerprint)
    service = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    manifest = service.apply(game, candidates[0])
    config.write_text(
        config.read_text(encoding="utf-8") + "UserChangedAfterApply=1\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="changed outside"):
        service.revert(game, manifest["id"])

    assert "UserChangedAfterApply=1" in config.read_text(encoding="utf-8")


def test_comparison_requires_representative_data_and_checks_tail_regression() -> None:
    before = _measurement()
    unrepresentative = replace(before, representative=False, quality="low")
    assert compare_measurements(before, unrepresentative).outcome == "insufficient_data"

    after = replace(
        before,
        average_fps=75,
        one_percent_low_fps=62,
        p95_frametime_ms=13,
        p99_frametime_ms=22,
    )
    result = compare_measurements(before, after)
    assert result.outcome == "regression"
    assert result.recommend_revert is True

    improved = replace(
        before,
        average_fps=75,
        one_percent_low_fps=64,
        p95_frametime_ms=13,
        p99_frametime_ms=17,
    )
    assert compare_measurements(before, improved).outcome == "improvement"


def test_capped_sessions_can_report_improved_gpu_headroom_without_fps_gain() -> None:
    before = replace(
        _measurement(),
        average_fps=60.0,
        one_percent_low_fps=57.0,
        gpu_usage_percent=85.0,
        p95_frametime_ms=17.0,
        p99_frametime_ms=18.0,
    )
    after = replace(
        before,
        average_fps=60.1,
        one_percent_low_fps=57.2,
        gpu_usage_percent=65.0,
        p95_frametime_ms=16.9,
        p99_frametime_ms=17.9,
    )
    capped = FrameRateAnalysis("likely_capped", 60.0, 0.96, (), ())

    comparison = compare_measurements(
        before,
        after,
        before_frame_rate=capped,
        after_frame_rate=capped,
    )

    assert comparison.outcome == "headroom_improved"
    assert comparison.recommend_revert is False
    assert any("headroom" in item.casefold() for item in comparison.evidence)
