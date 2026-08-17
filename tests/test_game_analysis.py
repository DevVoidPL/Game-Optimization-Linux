from __future__ import annotations

from dataclasses import replace
from concurrent.futures import Future
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
    OptimizationAnalysis,
    OptimizationCandidate,
    PerformanceMeasurement,
    SystemSnapshot,
)
from game_optimization_linux.services import (
    BottleneckAnalyzer,
    BaselineSessionRepository,
    FrameRateAnalyzer,
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


def _time_series_log(
    path: Path,
    phases: tuple[tuple[int, float, float, float], ...],
) -> Path:
    rows = ["fps,frametime,cpu_load,gpu_load,ram,vram,gpu_temp,elapsed"]
    index = 0
    for count, fps, cpu, gpu in phases:
        for offset in range(count):
            varied_fps = fps * (1 + ((offset % 5) - 2) * 0.002)
            rows.append(
                f"{varied_fps:.4f},{1000 / varied_fps:.6f},{cpu},{gpu},"
                f"4096,4096,65,{index * 100_000_000}"
            )
            index += 1
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
    assert bottleneck.conclusion == "insufficient_data"
    assert candidates == ()


def test_mangohud_parser_accepts_samples_without_optional_metrics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "essential-only.csv"
    path.write_text(
        "fps,frametime,cpu_load,gpu_load,elapsed\n"
        "60,16.67,40,90,0\n"
        "59,16.95,42,91,100000000\n",
        encoding="utf-8",
    )

    measurement = MangoHudLogParser().parse(path)

    assert measurement.available is False
    assert measurement.samples == 2
    assert measurement.ram_used_mb is None
    assert measurement.vram_used_mb is None
    assert measurement.gpu_temperature_c is None
    assert measurement.duration_seconds == pytest.approx(0.1)
    assert any("RAM utilization" in item for item in measurement.limitations)
    assert any("VRAM utilization" in item for item in measurement.limitations)


@pytest.mark.parametrize("fps", (60.0, 240.0, 30.0))
def test_representative_parser_preserves_stable_low_and_high_fps(
    tmp_path: Path, fps: float
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(tmp_path / f"stable-{fps}.csv", ((400, fps, 45, 90),))
    )

    assert measurement.representative is True
    assert measurement.samples == 400
    assert measurement.excluded_samples == 0
    assert measurement.average_fps == pytest.approx(fps, rel=0.01)


@pytest.mark.parametrize(
    ("fps", "gpu"),
    ((60.0, 7.0), (30.0, 5.0), (240.0, 18.0)),
)
def test_stable_lightweight_gameplay_is_representative_at_low_gpu_load(
    tmp_path: Path, fps: float, gpu: float
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(
            tmp_path / f"lightweight-{fps}.csv",
            ((1050, fps, 9.0, gpu),),
        )
    )

    assert measurement.representative is True
    assert measurement.available is True
    assert measurement.quality == "high"
    assert measurement.samples == 1050
    assert measurement.excluded_samples == 0
    assert measurement.average_fps == pytest.approx(fps, rel=0.01)


@pytest.mark.parametrize("fps", (30.0, 60.0, 117.0, 144.0))
def test_stable_arbitrary_frame_ceiling_is_detected_with_hardware_headroom(
    tmp_path: Path, fps: float
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(
            tmp_path / f"capped-{fps}.csv",
            ((600, fps, 10.0, 8.0),),
        )
    )

    result = FrameRateAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=200.0),
    )
    bottleneck = BottleneckAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=200.0),
    )

    assert result.state == "likely_capped"
    assert result.estimated_ceiling_fps == pytest.approx(fps, abs=1.0)
    assert result.confidence >= 0.80
    assert bottleneck.conclusion == "balanced"


def test_stable_fps_at_saturated_gpu_is_gpu_bound_not_confidently_capped(
    tmp_path: Path,
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(tmp_path / "gpu-bound.csv", ((600, 60.0, 35.0, 99.0),))
    )
    system = SystemSnapshot(refresh_rate=60.0)

    frame_rate = FrameRateAnalyzer().analyze(measurement, system)
    bottleneck = BottleneckAnalyzer().analyze(measurement, system)

    assert frame_rate.state == "not_detected"
    assert frame_rate.estimated_ceiling_fps is None
    assert any("GPU saturation" in item for item in frame_rate.limitations)
    assert bottleneck.conclusion == "gpu_bottleneck"


def test_long_stable_ceiling_tolerates_sparse_counter_upper_tail() -> None:
    measurement = PerformanceMeasurement(
        source_path="synthetic.csv",
        samples=8583,
        duration_seconds=859.1,
        average_fps=60.27,
        minimum_fps=48.0,
        one_percent_low_fps=54.2,
        average_frametime_ms=16.59,
        p95_frametime_ms=17.51,
        p99_frametime_ms=18.44,
        cpu_usage_percent=29.6,
        gpu_usage_percent=60.8,
        ram_used_mb=None,
        vram_used_mb=None,
        gpu_temperature_c=None,
        quality="high",
        total_samples=8583,
        selected_duration_seconds=859.1,
        representative=True,
        median_fps=60.38,
        p10_fps=57.71,
        p90_fps=63.10,
        p95_fps=64.38,
        p99_fps=69.04,
        median_frametime_ms=16.56,
    )

    result = FrameRateAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=165.0),
    )

    assert result.state == "likely_capped"
    assert result.estimated_ceiling_fps == pytest.approx(60.0, abs=1.0)
    assert result.confidence >= 0.80


def test_average_near_sixty_with_broad_distribution_is_not_a_cap() -> None:
    measurement = PerformanceMeasurement(
        source_path="synthetic.csv",
        samples=1200,
        duration_seconds=120.0,
        average_fps=60.0,
        minimum_fps=30.0,
        one_percent_low_fps=31.0,
        average_frametime_ms=20.0,
        p95_frametime_ms=31.0,
        p99_frametime_ms=33.0,
        cpu_usage_percent=30.0,
        gpu_usage_percent=60.0,
        ram_used_mb=None,
        vram_used_mb=None,
        gpu_temperature_c=None,
        quality="high",
        total_samples=1200,
        selected_duration_seconds=120.0,
        representative=True,
        median_fps=60.0,
        p10_fps=34.0,
        p90_fps=105.0,
        p95_fps=115.0,
        p99_fps=120.0,
        median_frametime_ms=16.67,
    )

    result = FrameRateAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=60.0),
    )

    assert result.state == "not_detected"
    assert result.estimated_ceiling_fps is None


def test_stable_240_fps_is_valid_without_refresh_rate_assumptions(
    tmp_path: Path,
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(tmp_path / "high-fps.csv", ((600, 240.0, 28.0, 55.0),))
    )
    frame_rate = FrameRateAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=60.0),
    )

    assert measurement.representative is True
    assert measurement.available is True
    assert measurement.average_fps == pytest.approx(240.0, rel=0.01)
    assert frame_rate.estimated_ceiling_fps == pytest.approx(240.0, abs=1.0)


def test_fluctuating_fps_near_sixty_is_not_a_confident_cap(
    tmp_path: Path,
) -> None:
    rows = ["fps,frametime,cpu_load,gpu_load,elapsed"]
    values = (30.0, 40.0, 50.0, 60.0, 120.0)
    for index in range(600):
        fps = values[index % len(values)]
        rows.append(f"{fps},{1000 / fps:.6f},20,30,{index * 100_000_000}")
    path = tmp_path / "fluctuating.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    measurement = MangoHudLogParser().parse(path)
    frame_rate = FrameRateAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=60.0),
    )

    assert measurement.representative is False
    assert frame_rate.state == "unknown"
    assert frame_rate.estimated_ceiling_fps is None


def test_relative_frametime_tail_detects_frame_pacing_problem(
    tmp_path: Path,
) -> None:
    rows = ["fps,frametime,cpu_load,gpu_load,elapsed"]
    for index in range(1000):
        fps = 90.0 if index % 50 == 0 else 201.0
        rows.append(f"{fps},{1000 / fps:.6f},27,24,{index * 100_000_000}")
    path = tmp_path / "pacing.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    measurement = MangoHudLogParser().parse(path)
    bottleneck = BottleneckAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=200.0),
    )

    assert measurement.representative is True
    assert bottleneck.conclusion == "frame_pacing_problem"
    assert any("1% low" in item for item in bottleneck.evidence)


def test_total_cpu_load_does_not_create_high_confidence_cpu_bottleneck(
    tmp_path: Path,
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(tmp_path / "cpu.csv", ((600, 60.0, 94.0, 40.0),))
    )

    bottleneck = BottleneckAnalyzer().analyze(
        measurement,
        SystemSnapshot(refresh_rate=120.0),
        target_fps=120,
    )

    assert bottleneck.conclusion == "cpu_bottleneck"
    assert bottleneck.confidence < 0.60
    assert any("Per-thread" in item for item in bottleneck.limitations)


@pytest.mark.parametrize(
    "phases",
    (
        ((200, 2000.0, 10, 10), (400, 60.0, 50, 95)),
        ((400, 60.0, 50, 95), (200, 2000.0, 10, 10)),
    ),
)
def test_representative_parser_selects_gameplay_not_uncapped_menu(
    tmp_path: Path,
    phases: tuple[tuple[int, float, float, float], ...],
) -> None:
    measurement = MangoHudLogParser().parse(
        _time_series_log(tmp_path / "mixed.csv", phases)
    )

    assert measurement.representative is True
    assert measurement.samples == 400
    assert measurement.total_samples == 600
    assert measurement.excluded_samples == 200
    assert measurement.average_fps == pytest.approx(60, rel=0.01)
    assert measurement.p99_frametime_ms is not None
    assert measurement.p99_frametime_ms < 18


def test_loading_spikes_are_excluded_from_stable_measurement(tmp_path: Path) -> None:
    path = _time_series_log(
        tmp_path / "loading.csv",
        ((250, 60.0, 50, 95), (100, 10.0, 20, 15), (250, 60.0, 50, 95)),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in range(251, 351, 2):
        values = lines[index].split(",")
        values[0] = "220"
        values[1] = f"{1000 / 220:.6f}"
        lines[index] = ",".join(values)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    measurement = MangoHudLogParser().parse(path)

    assert measurement.representative is True
    assert measurement.samples < measurement.total_samples
    assert measurement.average_fps == pytest.approx(60, rel=0.01)


def test_menu_dominated_session_is_not_representative_for_bottleneck(
    tmp_path: Path,
) -> None:
    path = _time_series_log(
        tmp_path / "menu-dominated.csv",
        ((600, 2000.0, 8, 8), (50, 60.0, 55, 98)),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in range(1, 601, 2):
        values = lines[index].split(",")
        values[0] = "80"
        values[1] = "12.5"
        lines[index] = ",".join(values)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    measurement = MangoHudLogParser().parse(path)
    bottleneck = BottleneckAnalyzer().analyze(
        measurement, SystemSnapshot(vram_gb=8, ram_gb=16), target_fps=90
    )
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        _unreal_tree(tmp_path / "menu-game"), system_info=_system()
    )
    profile = replace(
        GameOptimizationProfile.default("10"),
        preset="automatic",
        user_goal="low_power",
        target_fps=30,
    )
    candidates = GameRecommendationEngine().recommend(
        fingerprint,
        measurement,
        bottleneck,
        profile,
        gamemode_available=True,
        gamescope_available=True,
    )

    assert measurement.representative is False
    assert measurement.quality == "low"
    assert measurement.samples < measurement.total_samples
    assert bottleneck.conclusion == "insufficient_data"
    assert bottleneck.confidence == 0
    assert candidates == ()


def test_selected_fps_and_frametime_use_the_same_complete_samples(
    tmp_path: Path,
) -> None:
    rows = ["fps,frametime,cpu_load,gpu_load,elapsed"]
    for index in range(400):
        fps = "60"
        frametime = f"{1000 / 60:.6f}"
        if index == 150:
            fps = "3000"
            frametime = ""
        elif index == 151:
            fps = ""
            frametime = f"{1000 / 3000:.6f}"
        rows.append(f"{fps},{frametime},30,70,{index * 100_000_000}")
    path = tmp_path / "paired.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    measurement = MangoHudLogParser().parse(path)

    assert measurement.representative is True
    assert measurement.samples == 398
    assert measurement.average_frametime_ms == pytest.approx(1000 / 60, rel=1e-4)
    assert measurement.average_fps == pytest.approx(
        1000 / measurement.average_frametime_ms, rel=1e-6
    )


def test_short_high_fps_outliers_do_not_dominate_selected_average(
    tmp_path: Path,
) -> None:
    path = _time_series_log(
        tmp_path / "mixed-outliers.csv",
        ((1000, 2000.0, 8.0, 8.0), (200, 80.0, 50.0, 98.0)),
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    for sample_index in range(1050, 1055):
        values = lines[sample_index + 1].split(",")
        values[0] = "3000"
        values[1] = f"{1000 / 3000:.6f}"
        lines[sample_index + 1] = ",".join(values)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    measurement = MangoHudLogParser().parse(path)
    bottleneck = BottleneckAnalyzer().analyze(
        measurement, SystemSnapshot(vram_gb=8, ram_gb=16), target_fps=90
    )

    assert measurement.representative is False
    assert measurement.samples == 200
    assert measurement.total_samples == 1200
    assert measurement.p99_fps is not None and measurement.p99_fps > 1000
    assert measurement.average_fps == pytest.approx(
        1000 / measurement.average_frametime_ms, rel=1e-6
    )
    assert 80 < measurement.average_fps < 90
    assert bottleneck.conclusion == "insufficient_data"


def test_parsed_unrepresentative_baseline_is_recorded_not_failed(
    tmp_path: Path,
) -> None:
    path = _time_series_log(
        tmp_path / "mixed.csv",
        ((200, 2000.0, 10, 10), (400, 60.0, 50, 95)),
    )
    measurement = MangoHudLogParser().parse(path)
    measurement = replace(
        measurement,
        representative=False,
        quality="low",
        limitations=("Only 10.9% of samples formed a stable segment",),
    )
    game = _unreal_tree(tmp_path / "game")
    fingerprint = GameAnalyzer(GameExecutableResolver()).analyze(
        game, system_info=_system()
    )
    analysis = OptimizationAnalysis(
        fingerprint,
        measurement,
        BottleneckAnalyzer().analyze(measurement, fingerprint.system),
        (),
    )
    future: Future[OptimizationAnalysis] = Future()
    future.set_result(analysis)
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    created = sessions.create("10", game.id)
    claimed = sessions.claim("10", runner_pid=101)
    assert claimed is not None
    sessions.finish("10", 0, created.id, claimed.runner_token)
    toasts: list[tuple[str, str]] = []

    class Signal:
        def emit(self, *_args: object) -> None:
            pass

    app = SimpleNamespace(
        _optimization_jobs={game.id: future},
        _optimization_analyses={},
        _optimization_comparisons={},
        _baseline_sessions=sessions,
        _resolve_game=lambda game_id, show_error=False: (
            game if game_id == game.id else None
        ),
        _emit_toast=lambda message, level: toasts.append((message, level)),
        optimizationAnalysisChanged=Signal(),
    )

    OptimizationController(app)._poll_analysis_jobs()

    recorded = sessions.load("10")
    assert recorded is not None
    assert recorded.status == "recorded_unrepresentative"
    assert recorded.error == ""
    assert "not representative enough" in recorded.lifecycle_reason
    assert app._optimization_analyses[game.id] is analysis
    assert toasts == [(recorded.lifecycle_reason, "warning")]


def test_mangohud_parser_reads_real_084_memory_and_elapsed_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "MangoHud-0.8.4.csv"
    path.write_text(
        "os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
        "Linux,CPU,GPU,32768000,kernel,driver,scheduler\n"
        "fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,"
        "gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,"
        "swap_used,process_rss,cpu_mhz,elapsed\n"
        "60,16.67,40,0,90,45,70,2000,1000,1.5,100,12.25,0,2,4000,100000000\n"
        "59,16.95,42,0,91,45,71,2000,1000,1.75,100,12.5,0,2,4000,200000000\n",
        encoding="utf-8",
    )

    measurement = MangoHudLogParser().parse(path)

    assert measurement.duration_seconds == pytest.approx(0.1)
    assert measurement.ram_used_mb == pytest.approx(12.375 * 1024)
    assert measurement.vram_used_mb == pytest.approx(1.625 * 1024)
    assert measurement.gpu_temperature_c == pytest.approx(70.5)


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
    candidate = OptimizationCandidate(
        "gamemode_runtime",
        "CPU consistency",
        "GameMode runtime experiment",
        "Representative baseline",
        ("CPU-side bottleneck",),
        "Disabled",
        "Enabled",
        "Measure CPU-side consistency",
        "None",
        "Low",
        True,
        True,
        "Engine independent",
        "API independent",
        env_changes={"wrapper": "gamemoderun"},
    )
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
    claimed = sessions.claim("10", runner_pid=101)
    assert claimed is not None
    sessions.fail("10", "runner heartbeat stopped", first.id, claimed.runner_token)
    retry = sessions.create("10", "steam-10")
    other = sessions.create("20", "steam-20")

    sessions.heartbeat("10", first.id, claimed.runner_token)
    sessions.finish("10", 0, first.id, claimed.runner_token)

    assert sessions.load("10").id == retry.id
    assert sessions.load("10").status == "waiting_for_steam"
    assert sessions.load("20").id == other.id


def _baseline_controller(
    tmp_path: Path,
    sessions: BaselineSessionRepository,
    *,
    active_change: dict[str, object] | None = None,
    profile: GameOptimizationProfile | None = None,
) -> tuple[OptimizationController, Game, list[str]]:
    game = _game(tmp_path / "game")
    game.install_path.mkdir(exist_ok=True)
    launches: list[str] = []

    class Signal:
        def emit(self, *_args: object) -> None:
            pass

    app = SimpleNamespace(
        _resolve_game=lambda game_id, show_error=False: (
            game if game_id == game.id else None
        ),
        _optimization_applied_changes=(
            {game.id: dict(active_change)} if active_change else {}
        ),
        _optimization_change_service=SimpleNamespace(
            active_change=lambda _game: active_change
        ),
        _runner_integration=SimpleNamespace(
            status=lambda: SimpleNamespace(
                installed=True,
                path=tmp_path / "game-optimization-run",
                sha256="a" * 64,
            )
        ),
        _optimization_profile_repository=SimpleNamespace(
            load=lambda _app_id: profile or GameOptimizationProfile.default("10")
        ),
        _mangohud_detector=SimpleNamespace(
            detect=lambda _steam_type: SimpleNamespace(
                available=True,
                message="MangoHud detected",
            )
        ),
        _baseline_sessions=sessions,
        _optimization_comparisons={},
        _game_launcher=SimpleNamespace(
            launch=lambda launched: launches.append(launched.id)
            or ("steam", "-applaunch", "10")
        ),
        _active_baseline_games=set(),
        _baseline_statuses={},
        _emit_toast=lambda *_args: None,
        optimizationAnalysisChanged=Signal(),
    )
    return OptimizationController(app), game, launches


def test_completed_baseline_allows_a_second_recording(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    first = sessions.create("10", "steam-10")
    sessions.complete("10", first.id)
    controller, game, launches = _baseline_controller(tmp_path, sessions)

    result = controller.recordOptimizationBaseline(game.id)

    current = sessions.load("10")
    assert result["success"] is True, result
    assert current is not None
    assert current.id != first.id
    assert current.status == "waiting_for_runner"
    assert launches == [game.id]


def test_gamescope_profile_does_not_silently_block_baseline_start(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    profile = replace(
        GameOptimizationProfile.default("10"),
        gamescope_enabled=True,
        gamescope_mode="native",
    )
    controller, game, launches = _baseline_controller(
        tmp_path, sessions, profile=profile
    )

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is True, result
    assert launches == [game.id]


def test_duplicate_record_request_does_not_fail_existing_session(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    active = sessions.create("10", "steam-10")
    sessions.mark_waiting_for_runner("10", active.id)
    controller, game, launches = _baseline_controller(tmp_path, sessions)

    result = controller.recordOptimizationBaseline(game.id)

    current = sessions.load("10")
    assert result["success"] is False
    assert result["code"] == "active_measurement_session"
    assert "already active" in result["error"]
    assert current is not None
    assert current.id == active.id
    assert current.status == "waiting_for_runner"
    assert current.error == ""
    assert launches == []


def test_pending_setting_comparison_does_not_replace_completed_baseline(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    completed = sessions.create("10", "steam-10")
    sessions.complete("10", completed.id)
    controller, game, launches = _baseline_controller(
        tmp_path,
        sessions,
        active_change={"id": "pending", "state": "applied"},
    )

    result = controller.recordOptimizationBaseline(game.id)

    current = sessions.load("10")
    assert result == {
        "success": False,
        "code": "pending_settings_comparison",
        "error": "Record a comparison or revert the pending setting test first",
    }
    assert current is not None
    assert current.id == completed.id
    assert current.status == "completed"
    assert launches == []


@pytest.mark.parametrize("terminal_status", ("failed", "recorded_unrepresentative", "cancelled"))
def test_terminal_baseline_allows_retry(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    first = sessions.create("10", "steam-10")
    if terminal_status == "failed":
        sessions.fail("10", "synthetic failure", first.id)
    elif terminal_status == "recorded_unrepresentative":
        sessions.mark_unrepresentative("10", first.id, "synthetic mixed session")
    else:
        sessions._save(replace(first, status=terminal_status))
    controller, game, launches = _baseline_controller(tmp_path, sessions)

    result = controller.recordOptimizationBaseline(game.id)

    current = sessions.load("10")
    assert result["success"] is True, result
    assert current is not None and current.id != first.id
    assert launches == [game.id]


def test_historical_before_measurement_does_not_block_new_baseline(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    measurement = PerformanceMeasurement(
        "before.csv", 100, 10.0, 60.0, 55.0, 56.0,
        16.67, 17.0, 18.0, 30.0, 60.0, 2048.0, 4096.0, 65.0,
    )
    sessions.save_measurement("10", measurement, slot="before")
    controller, game, launches = _baseline_controller(tmp_path, sessions)

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is True, result
    preserved = sessions.load_measurement("10", slot="before")
    assert preserved is not None
    assert preserved.source_path == measurement.source_path
    assert preserved.average_fps == measurement.average_fps
    assert launches == [game.id]


def test_pending_automatic_comparison_blocks_baseline_with_specific_guard(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    controller, game, launches = _baseline_controller(
        tmp_path,
        sessions,
        active_change={
            "id": "automatic-change",
            "kind": "runtime_profile",
            "state": "applied",
        },
    )

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is False
    assert result["code"] == "pending_automatic_comparison"
    assert launches == []


def test_terminal_optimization_change_does_not_block_new_baseline(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    controller, game, launches = _baseline_controller(tmp_path, sessions)
    controller._app._optimization_applied_changes[game.id] = {
        "id": "old-change",
        "kind": "runtime_profile",
        "state": "reverted",
    }

    result = controller.recordOptimizationBaseline(game.id)

    assert result["success"] is True, result
    assert launches == [game.id]


def test_rejected_baseline_guard_is_logged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    active = sessions.create("10", "steam-10")
    sessions.mark_waiting_for_runner("10", active.id)
    controller, game, _launches = _baseline_controller(tmp_path, sessions)

    with caplog.at_level("WARNING"):
        result = controller.recordOptimizationBaseline(game.id)

    assert result["code"] == "active_measurement_session"
    assert "guard=active_measurement_session" in caplog.text
    assert f"gameId={game.id}" in caplog.text


def test_runner_handshake_timeout_fails_only_active_session(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    game = _game(tmp_path / "game")
    game.install_path.mkdir()
    session = sessions.create("10", game.id)
    sessions._save(replace(
        session,
        status="waiting_for_runner",
        created_at=datetime.now(UTC) - timedelta(minutes=3),
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
    assert "did not invoke" in sessions.load("10").error


def test_handshake_timeout_reports_observed_rejected_runner(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    game = _game(tmp_path / "game")
    game.install_path.mkdir()
    session = sessions.create("10", game.id)
    sessions._save(replace(
        session,
        status="waiting_for_runner",
        created_at=datetime.now(UTC) - timedelta(minutes=3),
        runner_invocation_count=1,
        runner_rejection="session token did not match",
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
        _resolve_game=lambda game_id, show_error=False: (
            game if game_id == game.id else None
        ),
        _emit_toast=lambda *_args: None,
        optimizationAnalysisChanged=Signal(),
    )

    OptimizationController(app)._poll_baseline_sessions()

    failed = sessions.load("10")
    assert failed is not None
    assert "invoked but rejected" in failed.error
    assert "token did not match" in failed.error


def test_delayed_runner_handshake_within_session_timeout_succeeds(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    created = sessions.create("10", "steam-10")
    sessions._save(replace(
        created,
        status="waiting_for_runner",
        created_at=datetime.now(UTC) - timedelta(seconds=75),
    ))

    claimed, reason = sessions.claim_with_reason("10", runner_pid=101)

    assert reason == "claimed"
    assert claimed is not None
    assert claimed.status == "recording"
    assert claimed.runner_invocation_count == 1


def test_reentrant_runner_claim_supersedes_only_the_same_session(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    created = sessions.create("10", "steam-10")
    first = sessions.claim("10", runner_pid=101)
    assert first is not None

    second, reason = sessions.claim_with_reason("10", runner_pid=202)

    assert reason == "claimed"
    assert second is not None
    assert second.id == created.id
    assert second.runner_pid == 202
    assert second.runner_token != first.runner_token
    assert second.runner_invocation_count == 2
    sessions.finish("10", 0, created.id, first.runner_token)
    assert sessions.load("10").status == "recording"


def test_runner_cannot_claim_another_app_session(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    created = sessions.create("10", "steam-10")

    claimed, reason = sessions.claim_with_reason("20", runner_pid=202)

    assert claimed is None
    assert "no baseline session" in reason
    assert sessions.load("10").id == created.id
    assert sessions.load("10").status == "waiting_for_steam"


def test_abandoned_baseline_recovery_does_not_trust_reused_pid(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    created = sessions.create("10", "steam-10")
    claimed = sessions.claim("10", runner_pid=os.getpid())
    assert claimed is not None
    sessions._save(replace(
        claimed,
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=2),
    ))

    recovered = sessions.recover_abandoned()

    assert len(recovered) == 1
    assert recovered[0].id == created.id
    assert recovered[0].status == "failed"
    assert "heartbeat stopped" in recovered[0].error
    assert sessions.load("10").status == "failed"
    retry = sessions.create("10", "steam-10")
    assert retry.id != created.id
    assert retry.status == "waiting_for_steam"


def test_fresh_baseline_heartbeat_is_not_recovered(tmp_path: Path) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    sessions.create("10", "steam-10")
    claimed = sessions.claim("10", runner_pid=101)
    assert claimed is not None

    assert sessions.recover_abandoned() == ()
    assert sessions.load("10").status == "recording"


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
    sessions._save(replace(
        waiting,
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=2),
    ))
    controller._poll_baseline_sessions()

    session = sessions.load("10")
    assert session is not None
    assert session.status == "processing"
    assert session.runner_completed_at is None
    assert "stabilized MangoHud log" in session.lifecycle_reason


def test_stable_log_does_not_end_session_while_runner_heartbeat_is_fresh(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    game = _game(tmp_path / "game")
    game.install_path.mkdir()
    created = sessions.create("10", game.id)
    claimed = sessions.claim("10", runner_pid=101)
    assert claimed is not None
    log = created.log_directory / "launcher-stage.csv"
    log.write_text("time,fps,frametime\n0.0,60,16.67\n", encoding="utf-8")
    sessions.mark_waiting_for_game_exit("10", created.id)
    old = datetime.now(UTC).timestamp() - 30
    os.utime(log, (old, old))

    class Signal:
        def emit(self, *_args: object) -> None:
            pass

    app = SimpleNamespace(
        _active_baseline_games={game.id},
        _baseline_statuses={game.id: "waiting_for_game_exit"},
        _baseline_sessions=sessions,
        _optimization_jobs={},
        _optimization_analyses={},
        _resolve_game=lambda game_id, show_error=False: (
            game if game_id == game.id else None
        ),
        _emit_toast=lambda *_args: None,
        optimizationAnalysisChanged=Signal(),
    )

    OptimizationController(app)._poll_baseline_sessions()

    current = sessions.load("10")
    assert current is not None
    assert current.status == "waiting_for_game_exit"


def test_successful_runner_without_mangohud_log_fails_with_session_diagnostics(
    tmp_path: Path,
) -> None:
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    game = _game(tmp_path / "game")
    game.install_path.mkdir()
    created = sessions.create("10", game.id)
    claimed = sessions.claim("10", runner_pid=101)
    assert claimed is not None
    finished = sessions.finish("10", 0, created.id, claimed.runner_token)
    assert finished is not None
    sessions._save(replace(
        finished,
        finished_at=datetime.now(UTC) - timedelta(seconds=10),
    ))

    class Signal:
        def emit(self, *_args: object) -> None:
            pass

    app = SimpleNamespace(
        _active_baseline_games={game.id},
        _baseline_statuses={game.id: "processing"},
        _baseline_sessions=sessions,
        _optimization_jobs={},
        _optimization_analyses={},
        _resolve_game=lambda game_id, show_error=False: (
            game if game_id == game.id else None
        ),
        _emit_toast=lambda *_args: None,
        optimizationAnalysisChanged=Signal(),
    )

    OptimizationController(app)._poll_baseline_sessions()

    failed = sessions.load("10")
    assert failed is not None
    assert failed.status == "failed"
    assert "private session directory" in failed.error
    diagnostics = sessions.artifact_diagnostics("10")
    assert diagnostics["configExists"] is True
    assert diagnostics["outputDirectoryExists"] is True
    assert diagnostics["measurementFile"] == ""
