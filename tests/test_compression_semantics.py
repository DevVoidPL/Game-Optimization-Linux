from __future__ import annotations

from pathlib import Path

from game_optimization_linux.controllers.presenters import game_to_qml
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services import (
    current_compression_saving,
    normalized_benchmark_projection,
)


def _game(tmp_path: Path, *, build_id: str = "100") -> Game:
    return Game(
        id="steam-4242",
        name="Synthetic compression fixture",
        launcher=Launcher.STEAM,
        install_path=tmp_path / "game",
        logical_size_gb=12.0,
        physical_size_gb=12.0,
        filesystem=FilesystemType.BTRFS,
        filesystem_name="btrfs",
        compression_available=True,
        steam_app_id="4242",
        steam_build_id=build_id,
    )


def _benchmark() -> dict[str, object]:
    return {
        "available": True,
        "appId": "4242",
        "buildId": "100",
        "baselineBytes": 100_000,
        "levels": {
            "3": {
                "level": 3,
                "estimatedPhysicalBytes": 60_000,
                "potentialFromBenchmarkBaselineBytes": 40_000,
            },
            "9": {
                "level": 9,
                "estimatedPhysicalBytes": 50_000,
                "potentialFromBenchmarkBaselineBytes": 50_000,
            },
        },
    }


def test_scanner_size_and_compsize_extent_sizes_remain_separate(
    tmp_path: Path,
) -> None:
    gib = 1024**3
    game = _game(tmp_path)
    presented = game_to_qml(
        game,
        verification_result={
            "task_id": "verification-current",
            "status": "completed",
            "result": {
                "logical_bytes": 99 * gib,
                "compsize_uncompressed_bytes": 10 * gib,
                "compsize_disk_bytes": 8 * gib,
                "compsize_referenced_bytes": 10 * gib,
                "measurement_source": "polkit_helper",
            },
        },
        benchmark_estimate=_benchmark(),
    )

    assert presented["scannerLogicalBytes"] == 12_000_000_000
    assert presented["compsizeUncompressedBytes"] == 10 * gib
    assert presented["physicalSizeBytes"] == 8 * gib
    assert presented["currentCompressionSavingBytes"] == 2 * gib
    assert presented["savedBytes"] == 2 * gib
    assert presented["physicalSize"] == "8.00 GiB"
    assert presented["savedSpace"] == "2.00 GiB"


def test_current_saving_uses_only_compsize_and_never_becomes_negative() -> None:
    assert current_compression_saving(10_000, 7_500) == 2_500
    assert current_compression_saving(7_500, 10_000) == 0
    assert current_compression_saving(None, 10_000) is None


def test_partial_compression_reports_only_normalized_additional_potential() -> None:
    projection = normalized_benchmark_projection(
        _benchmark(),
        level=3,
        current_uncompressed_bytes=200_000,
        current_disk_usage_bytes=150_000,
        app_id="4242",
        build_id="100",
    )

    # The benchmark predicts 60% physical usage. Its 100k baseline is not
    # subtracted directly from this 200k installation.
    assert projection["predictedPhysicalRatio"] == 0.6
    assert projection["estimatedPhysicalBytes"] == 120_000
    assert projection["estimatedTotalPotentialBytes"] == 80_000
    assert projection["currentSavingBytes"] == 50_000
    assert projection["estimatedAdditionalSavingBytes"] == 30_000


def test_build_mismatch_makes_additional_projection_unavailable() -> None:
    projection = normalized_benchmark_projection(
        _benchmark(),
        level=3,
        current_uncompressed_bytes=200_000,
        current_disk_usage_bytes=150_000,
        app_id="4242",
        build_id="101",
    )

    assert projection["available"] is False
    assert projection["estimatedAdditionalSavingBytes"] is None
    assert projection["estimatedPhysicalBytes"] is None


def test_incomplete_compsize_makes_additional_projection_unavailable() -> None:
    projection = normalized_benchmark_projection(
        _benchmark(),
        level=3,
        current_uncompressed_bytes=200_000,
        current_disk_usage_bytes=None,
        app_id="4242",
        build_id="100",
    )

    assert projection["available"] is False
    assert projection["estimatedAdditionalSavingBytes"] is None


def test_profitability_guard_also_warns_below_five_percent() -> None:
    gib = 1024**3
    benchmark = {
        "available": True,
        "appId": "4242",
        "buildId": "100",
        "baselineBytes": 100 * gib,
        "levels": {
            "3": {
                "estimatedPhysicalBytes": 96 * gib,
            }
        },
    }
    projection = normalized_benchmark_projection(
        benchmark,
        level=3,
        current_uncompressed_bytes=100 * gib,
        current_disk_usage_bytes=100 * gib,
        app_id="4242",
        build_id="100",
    )

    assert projection["estimatedAdditionalSavingBytes"] == 4 * gib
    assert projection["lowBenefit"] is True
    assert projection["additionalConfirmationRequired"] is True
    assert projection["automaticSkipRecommended"] is True
