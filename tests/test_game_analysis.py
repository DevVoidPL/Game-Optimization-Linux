from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from game_optimization_linux.controllers.optimization_controller import OptimizationController
from game_optimization_linux.models import (
    BottleneckAnalysis,
    FilesystemType,
    Game,
    GameOptimizationProfile,
    Launcher,
    PerformanceMeasurement,
)
from game_optimization_linux.services import (
    BottleneckAnalyzer,
    BaselineSessionRepository,
    GameAnalyzer,
    GameExecutableResolver,
    GameRecommendationEngine,
    MangoHudLogParser,
    OptimizationChangeService,
    compare_measurements,
)


def _pe(path: Path, *, bits: int = 64) -> None:
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x80 + 24, 0x20B if bits == 64 else 0x10B)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _game(root: Path, *, game_id: str = "steam-10", name: str = "Synthetic Game") -> Game:
    return Game(
        id=game_id,
        name=name,
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=1.0,
        physical_size_gb=1.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id=game_id.removeprefix("steam-"),
        data_source="Steam",
    )


def _system() -> dict[str, object]:
    return {"cpu": "Synthetic CPU", "gpu": "Synthetic GPU", "ram_gb": 16.0, "vram_gb": 8.0}


def _unreal_tree(root: Path) -> Game:
    _pe(root / "Synthetic/Binaries/Win64/Synthetic-Win64-Shipping.exe")
    (root / "Engine/Build").mkdir(parents=True)
    (root / "Engine/Build/Build.version").write_text(
        '{"MajorVersion": 5, "MinorVersion": 4}', encoding="utf-8"
    )
    (root / "Synthetic/Content/Paks").mkdir(parents=True)
    (root / "Synthetic/Content/Paks/game.pak").write_bytes(b"pak")
    (root / "Config").mkdir()
    (root / "Config/DefaultEngine.ini").write_text(
        "DefaultGraphicsRHI=DefaultGraphicsRHI_DX12\n"
        "r.Streaming.PoolSize=7600\n",
        encoding="utf-8",
    )
    return _game(root)


def test_unreal_fingerprint_uses_multiple_signatures(tmp_path: Path) -> None:
    game = _unreal_tree(tmp_path / "UnrealGame")
    result = GameAnalyzer(GameExecutableResolver()).analyze(
        game,
        system_info=_system(),
        category="unknown",
    )

    assert result.engine.value == "Unreal Engine"
    assert result.engine.confidence >= 0.9
    assert result.engine_version == "5.4"
    assert len(result.engine.evidence) >= 3
    assert result.graphics_api.value == "Direct3D 12"
    assert result.architecture.value == "64-bit"
    assert result.runtime.value == "Windows game using Steam compatibility layer"


def test_unreal_detector_handles_jedi_like_project_without_shipping_name(
    tmp_path: Path,
) -> None:
    root = tmp_path / "LargeUnrealGame"
    (root / "Engine/Plugins").mkdir(parents=True)
    _pe(root / "SwGame/Binaries/Win64/JediSurvivor.exe")
    paks = root / "SwGame/Content/Paks"
    paks.mkdir(parents=True)
    (paks / "pakchunk0-WindowsNoEditor.pak").write_bytes(b"pak")
    (paks / "pakchunk0-WindowsNoEditor.ucas").write_bytes(b"ucas")
    (paks / "pakchunk0-WindowsNoEditor.utoc").write_bytes(b"utoc")
    for index in range(100):
        path = root / "Engine/Plugins" / f"Plugin{index}.bin"
        path.write_bytes(b"x")

    result = GameAnalyzer(
        GameExecutableResolver(), maximum_files=20
    ).analyze(_game(root), system_info=_system())

    assert result.engine.value == "Unreal Engine"
    assert result.engine.confidence >= 0.8
    assert any("Content/Paks" in item.detail for item in result.engine.evidence)
    assert result.main_executable.endswith("SwGame/Binaries/Win64/JediSurvivor.exe")


def test_unity_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "UnityGame"
    _pe(root / "UnityGame.exe")
    (root / "UnityPlayer.dll").write_bytes(b"dll")
    data = root / "UnityGame_Data"
    data.mkdir()
    (data / "globalgamemanagers").write_bytes(b"managers")
    (data / "resources.assets").write_bytes(b"assets")
    (data / "boot.config").write_text("force-vulkan=1\n", encoding="utf-8")

    result = GameAnalyzer(GameExecutableResolver()).analyze(
        _game(root), system_info=_system()
    )

    assert result.engine.value == "Unity"
    assert result.engine.confidence >= 0.75
    assert result.graphics_api.value == "Vulkan"


def test_redengine_fingerprint_requires_combined_layout(tmp_path: Path) -> None:
    root = tmp_path / "RedGame"
    _pe(root / "bin/x64/RedGame.exe")
    (root / "r6/config").mkdir(parents=True)
    (root / "r6/config/settings.json").write_text("{}", encoding="utf-8")
    (root / "archive/pc/content").mkdir(parents=True)
    (root / "archive/pc/content/base.archive").write_bytes(b"archive")

    result = GameAnalyzer(GameExecutableResolver()).analyze(
        _game(root), system_info=_system()
    )

    assert result.engine.value == "REDengine"
    assert result.engine.confidence >= 0.7
    assert result.engine_version == "4"
    assert result.graphics_api.value == "Unknown"


def test_selected_redengine_renderer_executable_reports_available_and_active_apis(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RedGame"
    _pe(root / "bin/x64/witcher3.exe")
    _pe(root / "bin/x64_dx12/witcher3.exe")
    (root / "r6/config").mkdir(parents=True)
    (root / "r6/config/settings.json").write_text("{}", encoding="utf-8")
    (root / "archive/pc/content").mkdir(parents=True)
    (root / "archive/pc/content/base.archive").write_bytes(b"archive")

    result = GameAnalyzer(GameExecutableResolver()).analyze(
        _game(root, name="The Witcher 3"),
        system_info=_system(),
        selected_executable="bin/x64_dx12/witcher3.exe",
    )

    assert result.main_executable == str(root / "bin/x64_dx12/witcher3.exe")
    assert result.architecture.value == "64-bit"
    assert result.graphics_api.value == "Direct3D 12"
    assert result.graphics_api.source == "selected renderer-specific executable"
    assert {item.value for item in result.available_graphics_apis} == {
        "Direct3D 11",
        "Direct3D 12",
    }


def test_optimization_controller_reuses_saved_executable_and_runner_runtime(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RedGame"
    _pe(root / "bin/x64_dx12/witcher3.exe")
    game = _game(root, game_id="steam-292030", name="The Witcher 3")
    report = tmp_path / "292030.json"
    proton = tmp_path / "compatibilitytools.d/GE-Proton11-1/proton"
    report.write_text(
        json.dumps(
            {
                "appId": "292030",
                "steamCommand": [
                    str(proton), "waitforexitandrun", "REDprelauncher.exe"
                ],
            }
        ),
        encoding="utf-8",
    )
    resolver = GameExecutableResolver()
    app = SimpleNamespace(
        _mangohud_launch_integration=SimpleNamespace(executable_resolver=resolver),
        _optiscaler_service=SimpleNamespace(
            profile_repository=SimpleNamespace(
                load=lambda _app_id: SimpleNamespace(
                    executable="bin/x64_dx12/witcher3.exe"
                )
            )
        ),
        _mangohud_repository=SimpleNamespace(
            load=lambda _app_id: SimpleNamespace(executable_path="")
        ),
        _runner_report_path=lambda _app_id: report,
    )
    controller = OptimizationController(app)

    selected = controller._selected_executable(game, "292030")
    runtime = controller._runtime_hint("292030", selected)

    assert selected == "bin/x64_dx12/witcher3.exe"
    assert runtime is not None
    assert runtime.value == "GE-Proton11-1"
    assert runtime.source == "last verified Steam LaunchPlan"


def test_generic_tree_does_not_guess_engine_or_api(tmp_path: Path) -> None:
    root = tmp_path / "Generic"
    _pe(root / "Generic.exe")
    (root / "d3d12.dll").write_bytes(b"an injected DLL is not evidence")

    result = GameAnalyzer(GameExecutableResolver()).analyze(
        _game(root, name="Generic"), system_info=_system()
    )

    assert result.engine.value == "Unknown"
    assert result.graphics_api.value == "Unknown"


def test_ambiguous_engine_returns_unknown(tmp_path: Path) -> None:
    game = _unreal_tree(tmp_path / "Ambiguous")
    root = game.install_path
    (root / "UnityPlayer.dll").write_bytes(b"dll")
    data = root / "Other_Data"
    data.mkdir()
    (data / "globalgamemanagers").write_bytes(b"managers")
    (data / "resources.assets").write_bytes(b"assets")
    (root / "GameAssembly.dll").write_bytes(b"assembly")

    result = GameAnalyzer(GameExecutableResolver()).analyze(game, system_info=_system())

    assert result.engine.value == "Unknown"
    assert result.engine.source == "filesystem signatures"


def test_manual_category_override_is_explicit(tmp_path: Path) -> None:
    root = tmp_path / "Manual"
    _pe(root / "Manual.exe")
    result = GameAnalyzer(GameExecutableResolver()).analyze(
        _game(root, name="Manual"),
        system_info=_system(),
        category="strategy_simulation",
        manual_category_override=True,
    )
    assert result.category.value == "strategy_simulation"
    assert result.category.confidence == 1.0
    assert result.category.source == "manual override"


def _mangohud_log(path: Path, *, gpu: float = 97.0, cpu: float = 44.0) -> Path:
    rows = ["MangoHud v0.8.1", "time,fps,frametime,cpu_load,gpu_load,ram,vram,gpu_temp"]
    for index in range(120):
        fps = 58 + (index % 5)
        frametime = 1000 / fps
        rows.append(
            f"{index / 10:.1f},{fps},{frametime:.3f},{cpu},{gpu},4096,7600,70"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_mangohud_parser_and_gpu_bottleneck(tmp_path: Path) -> None:
    measurement = MangoHudLogParser().parse(_mangohud_log(tmp_path / "game.csv"))
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        _unreal_tree(tmp_path / "game"), system_info=_system()
    )
    result = BottleneckAnalyzer().analyze(
        measurement, fingerprint.system, target_fps=90
    )

    assert measurement.samples == 120
    assert measurement.one_percent_low_fps is not None
    assert measurement.p99_frametime_ms is not None
    assert result.conclusion == "vram_pressure"
    assert result.confidence > 0.8


def test_short_mangohud_session_is_low_quality_and_not_auto_tuned(
    tmp_path: Path,
) -> None:
    path = tmp_path / "short.csv"
    path.write_text(
        "time,fps,frametime,cpu_load,gpu_load,ram,vram\n"
        "0.0,50,20,40,99,4096,7800\n"
        "0.1,49,21,41,99,4096,7800\n",
        encoding="utf-8",
    )
    measurement = MangoHudLogParser().parse(path)
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        _unreal_tree(tmp_path / "short-game"), system_info=_system()
    )
    bottleneck = BottleneckAnalyzer().analyze(
        measurement, fingerprint.system, target_fps=90
    )
    candidates = GameRecommendationEngine().recommend(
        fingerprint,
        measurement,
        bottleneck,
        GameOptimizationProfile.default("10"),
        gamemode_available=True,
        gamescope_available=True,
    )
    assert measurement.quality == "low"
    assert measurement.one_percent_low_fps is None
    assert any("shorter" in item for item in measurement.limitations)
    assert candidates == ()


def test_unreal_vram_candidate_requires_existing_config_and_measurement(
    tmp_path: Path,
) -> None:
    game = _unreal_tree(tmp_path / "game")
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        game, system_info=_system()
    )
    measurement = MangoHudLogParser().parse(_mangohud_log(tmp_path / "game.csv"))
    bottleneck = BottleneckAnalysis("vram_pressure", 0.9, ("VRAM 93%",), ())
    profile = GameOptimizationProfile.default("10")

    candidates = GameRecommendationEngine().recommend(
        fingerprint,
        measurement,
        bottleneck,
        profile,
        gamemode_available=True,
        gamescope_available=True,
    )

    candidate = next(item for item in candidates if item.id == "unreal_streaming_pool")
    assert candidate.current_value == "7600"
    assert int(candidate.proposed_value) < 7600
    assert candidate.files_to_modify == (str(game.install_path / "Config/DefaultEngine.ini"),)


def test_no_data_and_unknown_engine_produce_no_candidate(tmp_path: Path) -> None:
    root = tmp_path / "Generic"
    _pe(root / "Generic.exe")
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        _game(root, name="Generic"), system_info=_system()
    )
    bottleneck = BottleneckAnalyzer().analyze(None, fingerprint.system)
    candidates = GameRecommendationEngine().recommend(
        fingerprint,
        None,
        bottleneck,
        GameOptimizationProfile.default("10"),
        gamemode_available=True,
        gamescope_available=True,
    )
    assert bottleneck.conclusion == "insufficient_data"
    assert candidates == ()


def test_config_apply_and_revert_are_hashed_and_per_game(tmp_path: Path) -> None:
    game = _unreal_tree(tmp_path / "game")
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        game, system_info=_system()
    )
    measurement = MangoHudLogParser().parse(_mangohud_log(tmp_path / "game.csv"))
    candidate = GameRecommendationEngine().recommend(
        fingerprint,
        measurement,
        BottleneckAnalysis("vram_pressure", 0.9, ("VRAM pressure",), ()),
        GameOptimizationProfile.default("10"),
        gamemode_available=False,
        gamescope_available=False,
    )[0]
    config = game.install_path / "Config/DefaultEngine.ini"
    original_hash = hashlib.sha256(config.read_bytes()).hexdigest()
    service = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )

    manifest = service.apply(game, candidate)
    assert hashlib.sha256(config.read_bytes()).hexdigest() == manifest["after_sha256"]
    assert manifest["before_sha256"] == original_hash
    restored = service.revert(game, manifest["id"])
    assert restored["state"] == "reverted"
    assert hashlib.sha256(config.read_bytes()).hexdigest() == original_hash
    assert (tmp_path / "changes/10" / manifest["id"] / "manifest.json").is_file()


def test_runtime_candidate_change_has_revertible_profile_manifest(
    tmp_path: Path,
) -> None:
    game = _unreal_tree(tmp_path / "runtime-game")
    before = GameOptimizationProfile.default("10")
    after = replace(before, preset="custom", gamemode_enabled=True)
    candidate = GameRecommendationEngine().recommend(
        GameAnalyzer(GameExecutableResolver()).analyze(game, system_info=_system()),
        PerformanceMeasurement(
            "baseline.csv", 300, 30, 50, 40, 42, 20, 22, 25,
            92, 50, 4096, 4096, 65, quality="high",
        ),
        BottleneckAnalysis("cpu_bottleneck", 0.9, ("CPU saturated",), ()),
        before,
        gamemode_available=True,
        gamescope_available=False,
    )[0]
    service = OptimizationChangeService(tmp_path / "changes")

    manifest = service.record_runtime_change(game, candidate, before, after)
    stored = service.runtime_change(game, manifest["id"])
    reverted = service.mark_runtime_reverted(game, manifest["id"])

    assert stored["before_profile"] == before.to_dict()
    assert stored["after_profile"] == after.to_dict()
    assert reverted["state"] == "reverted"


def test_apply_refuses_running_game_or_changed_config(tmp_path: Path) -> None:
    game = _unreal_tree(tmp_path / "game")
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(game, system_info=_system())
    measurement = MangoHudLogParser().parse(_mangohud_log(tmp_path / "game.csv"))
    candidate = GameRecommendationEngine().recommend(
        fingerprint,
        measurement,
        BottleneckAnalysis("vram_pressure", 0.9, (), ()),
        GameOptimizationProfile.default("10"),
        gamemode_available=False,
        gamescope_available=False,
    )[0]
    with pytest.raises(RuntimeError, match="running"):
        OptimizationChangeService(
            tmp_path / "changes", process_checker=lambda _game: True
        ).apply(game, candidate)
    config = game.install_path / "Config/DefaultEngine.ini"
    config.write_text(config.read_text() + "r.Streaming.PoolSize=8000\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        OptimizationChangeService(
            tmp_path / "changes2", process_checker=lambda _game: False
        ).apply(game, candidate)


def test_comparison_reports_regression() -> None:
    base = PerformanceMeasurement(
        "before.csv", 100, 10, 60, 40, 42, 16.7, 20, 25, 50, 90, 4000, 6000, 70
    )
    after = replace(base, source_path="after.csv", average_fps=50, p95_frametime_ms=24)
    result = compare_measurements(base, after)
    assert result.outcome == "regression"
    assert result.recommend_revert is True


def test_baseline_retry_and_game_isolation_ignore_stale_runner(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    first = sessions.create("10", "steam-10")
    sessions.fail("10", "timeout", first.id)
    retry = sessions.create("10", "steam-10")
    other = sessions.create("20", "steam-20")

    sessions.finish("10", 0, first.id)

    assert sessions.load("10").id == retry.id
    assert sessions.load("10").status == "waiting_for_steam"
    assert sessions.load("20").id == other.id


def test_runner_handshake_timeout_fails_only_active_session(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    game = _game(tmp_path / "game")
    game.install_path.mkdir()
    session = sessions.create("10", game.id)
    sessions._save(replace(
        session,
        status="waiting_for_runner",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
    ))

    class Signal:
        def emit(self, *_args: object) -> None:
            pass

    app = SimpleNamespace(
        _active_baseline_games={game.id},
        _baseline_statuses={game.id: "waiting_for_runner"},
        _baseline_sessions=sessions,
        _optimization_jobs={},
        _optimization_analyses={},
        _resolve_game=lambda game_id, show_error=False: game if game_id == game.id else None,
        _emit_toast=lambda *_args: None,
        optimizationAnalysisChanged=Signal(),
    )
    OptimizationController(app)._poll_baseline_sessions()

    assert sessions.load("10").status == "failed"
    assert "did not reach" in sessions.load("10").error


def test_stable_mangohud_log_prevents_infinite_recording_without_completion(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    game = _game(tmp_path / "game")
    game.install_path.mkdir()
    created = sessions.create("10", game.id)
    claimed = sessions.claim("10", runner_pid=101)
    assert claimed is not None
    sessions.mark_process_started(
        "10",
        created.id,
        claimed.runner_token,
        spawned_pid=202,
        process_group=202,
        command_name="steam-proton-wrapper",
    )
    log = created.log_directory / "baseline.csv"
    log.write_text(
        "time,fps,frametime\n0.0,60,16.67\n",
        encoding="utf-8",
    )

    class Signal:
        def emit(self, *_args: object) -> None:
            pass

    app = SimpleNamespace(
        _active_baseline_games={game.id},
        _baseline_statuses={game.id: "recording"},
        _baseline_sessions=sessions,
        _optimization_jobs={},
        _optimization_analyses={},
        _resolve_game=lambda game_id, show_error=False: (
            game if game_id == game.id else None
        ),
        _emit_toast=lambda *_args: None,
        optimizationAnalysisChanged=Signal(),
    )

    controller = OptimizationController(app)
    controller._poll_baseline_sessions()

    waiting = sessions.load("10")
    assert waiting is not None
    assert waiting.status == "waiting_for_game_exit"

    old = datetime.now(UTC).timestamp() - 30
    os.utime(log, (old, old))
    controller._poll_baseline_sessions()

    session = sessions.load("10")
    assert session is not None
    assert session.status == "processing"
    assert session.runner_completed_at is None
    assert "stabilized MangoHud log" in session.lifecycle_reason
