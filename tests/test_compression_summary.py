from __future__ import annotations

from pathlib import Path

import pytest

from game_optimization_linux.controllers.presenters import game_to_qml
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services.compression_summary import (
    LOW_COMPRESSION_EFFECT_FRACTION,
    MODERATE_COMPRESSION_EFFECT_FRACTION,
    STRONG_COMPRESSION_EFFECT_FRACTION,
    aggregate_library_compression,
    classify_compression_effect,
    reclaimed_by_last_operation,
)


@pytest.mark.parametrize(
    ("name", "disk_usage", "expected_key", "expected_percent"),
    [
        ("House Flipper", 5_998, "strongly_compressed", 40.02),
        ("Detroit", 9_415, "moderately_compressed", 5.85),
        ("Batman Arkham Knight", 9_679, "low_effect", 3.21),
        ("Gotham Knights", 9_898, "low_effect", 1.02),
        ("Jedi Survivor", 10_000, "no_compression", 0.0),
        ("Batman Arkham Origins", 10_000, "no_compression", 0.0),
    ],
)
def test_realistic_results_are_only_synthetic_classification_fixtures(
    name: str,
    disk_usage: int,
    expected_key: str,
    expected_percent: float,
) -> None:
    result = classify_compression_effect(10_000, disk_usage)

    assert name  # documents the synthetic fixture without production coupling
    assert result["key"] == expected_key
    assert result["savingPercent"] == pytest.approx(expected_percent)


def test_classification_thresholds_are_named_and_boundary_stable() -> None:
    assert STRONG_COMPRESSION_EFFECT_FRACTION == 0.15
    assert MODERATE_COMPRESSION_EFFECT_FRACTION == 0.05
    assert LOW_COMPRESSION_EFFECT_FRACTION == 0.01

    assert classify_compression_effect(10_000, 8_500)["key"] == "strongly_compressed"
    assert classify_compression_effect(10_000, 9_500)["key"] == "moderately_compressed"
    assert classify_compression_effect(10_000, 9_900)["key"] == "low_effect"
    assert classify_compression_effect(10_000, 9_901)["key"] == "no_compression"


def test_large_shared_set_with_almost_no_exclusive_data_blocks_automation() -> None:
    gib = 1024**3
    result = classify_compression_effect(
        100 * gib,
        100 * gib,
        shared_extent_state="detected",
        shared_total_bytes=100 * gib,
        exclusive_bytes=128 * 1024**2,
        set_shared_bytes=95 * gib,
        estimated_shared_growth_bytes=99 * gib,
    )

    assert result["key"] == "shared_extents_blocked"
    assert result["sharedExtentRisk"] is True
    assert result["automaticOperationBlocked"] is True
    assert result["estimatedSharedGrowthBytes"] == 99 * gib


def test_library_summary_uses_only_complete_current_compsize_values() -> None:
    games = [
        {
            "libraryPath": "/steam/library",
            "scannerLogicalBytes": 999_999_999,
            "compsizeUncompressedBytes": 100_000,
            "physicalSizeBytes": 80_000,
            "currentCompressionMeasuredAt": "2026-07-31T10:00:00+00:00",
        },
        {
            "libraryPath": "/steam/library",
            "scannerLogicalBytes": 888_888_888,
            "compsizeUncompressedBytes": 200_000,
            "physicalSizeBytes": 190_000,
            "currentCompressionMeasuredAt": "2026-07-31T11:00:00+00:00",
        },
    ]

    summary = aggregate_library_compression(games)[0]

    assert summary["uncompressedBytes"] == 300_000
    assert summary["diskUsageBytes"] == 270_000
    assert summary["currentSavingBytes"] == 30_000
    assert summary["savingPercent"] == pytest.approx(10.0)
    assert summary["measuredGameCount"] == 2
    assert summary["fullyMeasured"] is True
    assert summary["lastFullMeasurementAt"] == "2026-07-31T11:00:00+00:00"
    assert summary["source"] == "compsize"


def test_incomplete_library_summary_does_not_invent_full_measurement_date() -> None:
    summary = aggregate_library_compression(
        [
            {
                "libraryPath": "/steam/library",
                "compsizeUncompressedBytes": 100,
                "physicalSizeBytes": 80,
                "currentCompressionMeasuredAt": "2026-07-31T10:00:00+00:00",
            },
            {"libraryPath": "/steam/library", "scannerLogicalBytes": 500},
        ]
    )[0]

    assert summary["measuredGameCount"] == 1
    assert summary["gameCount"] == 2
    assert summary["fullyMeasured"] is False
    assert summary["lastFullMeasurementAt"] == ""


def test_last_operation_reclaimed_requires_one_authoritative_before_after_pair() -> None:
    valid = reclaimed_by_last_operation(
        {
            "measurement_authoritative": True,
            "completed_at": "2026-07-31T12:00:00+00:00",
            "before": {
                "measurement_source": "polkit_helper",
                "compsize_disk_bytes": 10_000,
                "compsize_uncompressed_bytes": 12_000,
                "compsize_referenced_bytes": 12_000,
            },
            "after": {
                "measurement_source": "polkit_helper",
                "compsize_disk_bytes": 8_500,
                "compsize_uncompressed_bytes": 12_000,
                "compsize_referenced_bytes": 12_000,
            },
        }
    )
    incomplete = reclaimed_by_last_operation(
        {
            "measurement_authoritative": False,
            "actual_saved_bytes": 1_500,
            "before": {"compsize_disk_bytes": 10_000},
            "after": {"compsize_disk_bytes": 8_500},
        }
    )

    assert valid == {
        "available": True,
        "bytes": 1_500,
        "measuredAt": "2026-07-31T12:00:00+00:00",
        "source": "compsize_before_after",
    }
    assert incomplete["available"] is False
    assert incomplete["bytes"] is None


def test_presenter_exposes_same_current_effect_and_separate_operation_delta(
    tmp_path: Path,
) -> None:
    game = Game(
        id="steam-1",
        name="Synthetic",
        launcher=Launcher.STEAM,
        install_path=tmp_path / "library" / "steamapps" / "common" / "Synthetic",
        library_path=tmp_path / "library",
        logical_size_gb=999.0,
        physical_size_gb=999.0,
        filesystem=FilesystemType.BTRFS,
        filesystem_name="btrfs",
        compression_available=True,
        steam_app_id="1",
        steam_build_id="2",
    )
    measurement = {
        "measurement_source": "polkit_helper",
        "measured_at": "2026-07-31T12:30:00+00:00",
        "compsize_uncompressed_bytes": 10_000,
        "compsize_disk_bytes": 6_000,
        "compsize_referenced_bytes": 10_000,
    }
    presented = game_to_qml(
        game,
        verification_result={
            "status": "completed",
            "updated_at": "2026-07-31T12:30:00+00:00",
            "result": measurement,
        },
        compression_result={
            "measurement_authoritative": True,
            "completed_at": "2026-07-30T12:00:00+00:00",
            "before": {
                "measurement_source": "polkit_helper",
                "compsize_disk_bytes": 7_000,
                "compsize_uncompressed_bytes": 10_000,
                "compsize_referenced_bytes": 10_000,
            },
            "after": {
                "measurement_source": "polkit_helper",
                "compsize_disk_bytes": 6_500,
                "compsize_uncompressed_bytes": 10_000,
                "compsize_referenced_bytes": 10_000,
            },
        },
    )

    assert presented["currentCompressionSavingBytes"] == 4_000
    assert presented["compressionClassificationKey"] == "strongly_compressed"
    assert presented["compressionEffectPercent"] == pytest.approx(40.0)
    assert presented["lastOperationReclaimedBytes"] == 500
    assert presented["lastOperationReclaimedAvailable"] is True
    assert presented["libraryPath"] == str(tmp_path / "library")
