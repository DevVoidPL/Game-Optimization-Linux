"""Small, side-effect-free adapters from domain models to QML values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QUrl

from ..services.benchmark_estimates import (
    current_compression_saving,
    normalized_benchmark_projection,
)
from ..services.compression_summary import (
    classify_compression_effect,
    reclaimed_by_last_operation,
)

if TYPE_CHECKING:
    from ..models import AppSettings, Backup, Game, SystemInfo, Task


def qml_value(value: Any) -> Any:
    """Recursively turn common Python/domain values into QVariant values."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return qml_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): qml_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [qml_value(item) for item in value]
    return value


def _model_dict(model: Any) -> dict[str, Any]:
    converter = getattr(model, "to_dict", None)
    if callable(converter):
        converted = converter()
    elif is_dataclass(model) and not isinstance(model, type):
        converted = asdict(model)
    elif isinstance(model, Mapping):
        converted = dict(model)
    else:
        raise TypeError(f"Cannot expose {type(model).__name__} to QML")
    return qml_value(converted)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _size_label(value: Any) -> str:
    return f"{_number(value):.1f} GB"


def _byte_size_label(value: int) -> str:
    size = max(0, int(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(size)
    unit = 0
    while amount >= 1024.0 and unit < len(units) - 1:
        amount /= 1024.0
        unit += 1
    if unit == 0:
        digits = 0
    elif amount >= 100:
        digits = 0
    elif amount >= 10:
        digits = 1
    else:
        digits = 2
    return f"{amount:.{digits}f} {units[unit]}"


def _local_asset_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        return ""
    url = QUrl.fromLocalFile(str(path))
    return url.toString(QUrl.ComponentFormattingOption.FullyEncoded)


def game_to_qml(
    game: Game,
    *,
    analysis_report: Mapping[str, Any] | None = None,
    compression_result: Mapping[str, Any] | None = None,
    verification_result: Mapping[str, Any] | None = None,
    benchmark_estimate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Present a game with stable camelCase names used by QML."""

    raw = _model_dict(game)
    normalized_report = dict(analysis_report or {})
    logical_size = _number(raw.get("logical_size_gb"))
    physical_size = _number(raw.get("physical_size_gb"), logical_size)
    size_scan_status = str(raw.get("size_scan_status", "not requested"))
    filesystem_label = str(
        raw.get("filesystem_name") or raw.get("filesystem", "Unknown")
    )
    saved_space = _number(raw.get("saved_space_gb"))
    result = dict(compression_result or {})
    verification = dict(verification_result or {})
    verification_status = str(verification.get("status") or "").casefold()
    verification_raw = verification.get("result")
    verification_measurement = (
        dict(verification_raw) if isinstance(verification_raw, Mapping) else {}
    )

    def positive_measurement_int(
        measurement: Mapping[str, Any],
        name: str,
    ) -> int | None:
        value = measurement.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        ):
            return int(value)
        return None

    verification_disk_bytes = positive_measurement_int(
        verification_measurement,
        "compsize_disk_bytes",
    )
    verification_uncompressed_bytes = positive_measurement_int(
        verification_measurement,
        "compsize_uncompressed_bytes"
    )
    verification_referenced_bytes = positive_measurement_int(
        verification_measurement,
        "compsize_referenced_bytes"
    )
    scanner_logical_bytes = max(0, int(logical_size * 1_000_000_000))
    verification_logical_bytes = positive_measurement_int(
        verification_measurement,
        "logical_bytes",
    )
    verification_complete = bool(
        verification_status == "completed"
        and str(
            verification_measurement.get("measurement_source") or ""
        ).casefold()
        == "polkit_helper"
        and verification_disk_bytes is not None
        and verification_uncompressed_bytes is not None
        and verification_referenced_bytes is not None
        and (scanner_logical_bytes > 0 or verification_logical_bytes is not None)
    )
    result_authoritative = result.get("measurement_authoritative") is True
    result_after = result.get("after")
    after_map = dict(result_after) if isinstance(result_after, Mapping) else {}
    after_disk_bytes = positive_measurement_int(
        after_map,
        "compsize_disk_bytes",
    )
    after_uncompressed_bytes = positive_measurement_int(
        after_map,
        "compsize_uncompressed_bytes",
    )
    after_referenced_bytes = positive_measurement_int(
        after_map,
        "compsize_referenced_bytes",
    )
    after_complete = bool(
        result_authoritative
        and str(after_map.get("measurement_source") or "").casefold()
        == "polkit_helper"
        and after_disk_bytes is not None
        and after_uncompressed_bytes is not None
        and after_referenced_bytes is not None
    )
    if verification_complete:
        current_measurement = verification_measurement
        current_disk_bytes = verification_disk_bytes
        current_uncompressed_bytes = verification_uncompressed_bytes
        current_referenced_bytes = verification_referenced_bytes
        current_measurement_at = str(
            verification_measurement.get("measured_at")
            or verification.get("updated_at")
            or ""
        )
    elif verification:
        current_measurement = {}
        current_disk_bytes = None
        current_uncompressed_bytes = None
        current_referenced_bytes = None
        current_measurement_at = ""
    elif after_complete:
        current_measurement = after_map
        current_disk_bytes = after_disk_bytes
        current_uncompressed_bytes = after_uncompressed_bytes
        current_referenced_bytes = after_referenced_bytes
        current_measurement_at = str(
            after_map.get("measured_at") or result.get("completed_at") or ""
        )
    else:
        current_measurement = {}
        current_disk_bytes = None
        current_uncompressed_bytes = None
        current_referenced_bytes = None
        current_measurement_at = ""
    current_measurement_complete = bool(current_measurement)
    current_saved_bytes = current_compression_saving(
        current_uncompressed_bytes,
        current_disk_bytes,
    )
    measured_saved_raw = result.get("actual_saved_bytes")
    measured_saved_known = (
        result_authoritative
        and isinstance(measured_saved_raw, (int, float))
        and not isinstance(measured_saved_raw, bool)
    )
    if current_measurement_complete:
        measured_saved_bytes = current_saved_bytes
        saved_space = measured_saved_bytes / 1_000_000_000
    elif verification:
        measured_saved_bytes = None
    elif measured_saved_known and filesystem_label.casefold() != "btrfs":
        measured_saved_bytes = int(measured_saved_raw)
        saved_space = measured_saved_bytes / 1_000_000_000
    elif result or (
        filesystem_label.casefold() == "btrfs"
        and str(raw.get("data_source", "")).casefold() != "demo"
    ):
        measured_saved_bytes = None
    else:
        measured_saved_bytes = int(saved_space * 1_000_000_000)
    estimated_size = physical_size
    last_status = raw.get("last_task_status") or "Not run"
    legacy_artwork_path = str(raw.get("cover_asset") or "")
    portrait_artwork_path = str(raw.get("portrait_artwork_path") or "")
    header_artwork_path = str(raw.get("header_artwork_path") or "")
    fallback_artwork_path = str(raw.get("fallback_artwork_path") or "")
    legacy_artwork_url = _local_asset_url(legacy_artwork_path)
    portrait_artwork_url = _local_asset_url(portrait_artwork_path)
    header_artwork_url = _local_asset_url(header_artwork_path)
    fallback_artwork_url = _local_asset_url(fallback_artwork_path)
    preferred_artwork_url = (
        portrait_artwork_url
        or header_artwork_url
        or fallback_artwork_url
        or legacy_artwork_url
    )
    library_available = bool(raw.get("library_available", True))
    game_status = str(raw.get("status", "Ready"))
    if not library_available:
        game_status = "Drive disconnected"
    actionable = bool(
        library_available
        and game_status not in {"Drive disconnected", "Missing files"}
    )
    report_available = bool(
        normalized_report
        and str(normalized_report.get("game_id") or "") == str(raw.get("id", ""))
        and str(normalized_report.get("created_at") or "")
    )
    physical_bytes = current_disk_bytes
    if physical_bytes is None and filesystem_label.casefold() != "btrfs":
        physical_bytes = int(physical_size * 1_000_000_000)
    normalized_benchmark = dict(benchmark_estimate or {})
    normalized_benchmark["projections"] = {
        str(level): normalized_benchmark_projection(
            normalized_benchmark,
            level=level,
            current_uncompressed_bytes=current_uncompressed_bytes,
            current_disk_usage_bytes=current_disk_bytes,
            app_id=str(raw.get("steam_app_id") or ""),
            build_id=str(raw.get("steam_build_id") or ""),
        )
        for level in (1, 3, 6, 9)
    }
    btrfs_du_raw = normalized_report.get("btrfs_du")
    btrfs_du = dict(btrfs_du_raw) if isinstance(btrfs_du_raw, Mapping) else {}
    compression_classification = classify_compression_effect(
        current_uncompressed_bytes,
        current_disk_bytes,
        shared_extent_state=btrfs_du.get("state", "unknown"),
        shared_total_bytes=btrfs_du.get("total_bytes"),
        exclusive_bytes=btrfs_du.get("exclusive_bytes"),
        set_shared_bytes=btrfs_du.get("set_shared_bytes"),
        estimated_shared_growth_bytes=btrfs_du.get("estimated_growth_bytes"),
    )
    last_operation_reclaimed = reclaimed_by_last_operation(result)

    return {
        "id": str(raw.get("id", "")),
        "name": str(raw.get("name", "Unknown game")),
        "title": str(raw.get("name", "Unknown game")),
        "launcher": str(raw.get("launcher", "Unknown")),
        "size": _size_label(logical_size),
        "logicalSize": _size_label(logical_size),
        "logicalSizeGb": logical_size,
        "sizeBytes": scanner_logical_bytes,
        "scannerLogicalBytes": scanner_logical_bytes,
        "compsizeUncompressedBytes": current_uncompressed_bytes,
        "compsizeReferencedBytes": current_referenced_bytes,
        "currentCompressionSavingBytes": current_saved_bytes,
        "currentCompressionMeasurement": qml_value(current_measurement),
        "currentCompressionMeasuredAt": current_measurement_at,
        "compressionClassification": qml_value(compression_classification),
        "compressionClassificationKey": str(
            compression_classification.get("key") or "measurement_unavailable"
        ),
        "compressionEffectPercent": compression_classification.get(
            "savingPercent"
        ),
        "lastOperationReclaimed": qml_value(last_operation_reclaimed),
        "lastOperationReclaimedBytes": last_operation_reclaimed.get("bytes"),
        "lastOperationReclaimedAvailable": (
            last_operation_reclaimed.get("available") is True
        ),
        "physicalSize": (
            _byte_size_label(physical_bytes)
            if current_measurement_complete
            else f"{physical_bytes / 1_000_000_000:.1f} GB"
            if physical_bytes is not None
            else "Measurement unavailable"
        ),
        "physicalSizeBytes": physical_bytes,
        "physicalSizeMeasuredByCompsize": bool(
            physical_bytes is not None and filesystem_label.casefold() == "btrfs"
        ),
        "physicalSizeGb": (
            physical_bytes / 1_000_000_000
            if current_measurement_complete and physical_bytes is not None
            else physical_size
        ),
        "path": str(raw.get("install_path", "")),
        "installPath": str(raw.get("install_path", "")),
        "libraryPath": str(raw.get("library_path") or ""),
        "filesystem": filesystem_label,
        "compressionAvailable": bool(
            library_available and raw.get("compression_available", False)
        ),
        "savedSpace": (
            _byte_size_label(measured_saved_bytes)
            if current_measurement_complete
            else _size_label(saved_space)
            if measured_saved_bytes is not None
            else "Measurement unavailable"
        ),
        "savedSpaceGb": saved_space if measured_saved_bytes is not None else None,
        "savedBytes": measured_saved_bytes,
        "savingsMeasured": measured_saved_bytes is not None,
        "verificationTaskId": str(verification.get("task_id") or ""),
        "verificationStatus": str(verification.get("status") or ""),
        "verificationError": str(verification.get("error") or ""),
        "verificationResult": qml_value(verification_measurement),
        "estimatedCompressedSize": _size_label(estimated_size),
        "estimatedCompressedSizeGb": estimated_size,
        "lastTaskStatus": str(last_status),
        "lastOperation": str(last_status),
        "lastCompression": (
            "Measurement unavailable"
            if result and measured_saved_bytes is None
            else "Previously measured"
            if result or saved_space > 0
            else "Never"
        ),
        "status": game_status,
        "libraryAvailable": library_available,
        "availabilityStatus": "" if library_available else "Library unavailable",
        "launchAllowed": actionable,
        "analysisAllowed": actionable,
        "cover": preferred_artwork_url,
        "effectiveArtworkUrl": preferred_artwork_url,
        "effective_artwork_url": preferred_artwork_url,
        "coverAsset": legacy_artwork_path,
        "portraitArtwork": portrait_artwork_url,
        "portraitArtworkUrl": portrait_artwork_url,
        "portraitArtworkPath": portrait_artwork_path,
        "headerArtwork": header_artwork_url,
        "headerArtworkUrl": header_artwork_url,
        "headerArtworkPath": header_artwork_path,
        "fallbackArtwork": fallback_artwork_url,
        "fallbackArtworkUrl": fallback_artwork_url,
        "fallbackArtworkPath": fallback_artwork_path,
        "optimizationProfile": str(
            raw.get("active_optimization_profile", "Balanced")
        ),
        "optimizationStatus": (
            str(raw.get("active_optimization_profile", "Balanced"))
            if str(raw.get("data_source", "")).casefold() == "demo"
            else "Not configured"
        ),
        "backupStatus": str(raw.get("backup_status", "Not detected")),
        "engineCompatibility": str(
            raw.get("texture_compatibility", "Not checked")
        ),
        "hasAnticheat": bool(raw.get("has_anticheat", False)),
        "analysisReport": qml_value(normalized_report),
        "benchmarkEstimate": qml_value(normalized_benchmark),
        "analysisReportAvailable": report_available,
        "analysisPathAvailable": bool(
            report_available
            and normalized_report.get("path_exists") is True
            and normalized_report.get("path_is_directory") is True
        ),
        "analysisIsBtrfs": bool(
            report_available and normalized_report.get("is_btrfs") is True
        ),
        "analysisScanComplete": bool(
            report_available and normalized_report.get("scan_complete") is True
        ),
        "analysisProfilesUnlocked": bool(
            report_available and normalized_report.get("profiles_unlocked") is True
        ),
        "analysisGameRunning": bool(
            report_available and normalized_report.get("game_running") is True
        ),
        "analysisHasWarnings": bool(
            report_available and normalized_report.get("warnings")
        ),
        "steamAppId": str(raw.get("steam_app_id") or ""),
        "libraryPath": str(raw.get("library_path") or ""),
        "dataSource": str(raw.get("data_source") or raw.get("launcher", "Unknown")),
        "lastScannedAt": str(raw.get("last_scanned_at") or ""),
        "lastUpdatedAt": str(raw.get("last_updated_at") or ""),
        "language": str(raw.get("language") or ""),
        "stateFlags": raw.get("state_flags"),
        "steamBuildId": str(raw.get("steam_build_id") or ""),
        "steamManifestPath": str(raw.get("steam_manifest_path") or ""),
        "steamManifestMtimeNs": raw.get("steam_manifest_mtime_ns"),
        "steamManifestSizeBytes": raw.get("steam_manifest_size_bytes"),
        "steamSizeOnDiskBytes": raw.get("steam_size_on_disk_bytes"),
        "updateInProgress": bool(raw.get("update_in_progress", False)),
        "sizeScanStatus": size_scan_status,
        "sizeScanError": str(raw.get("size_scan_error") or ""),
        "mountPoint": str(raw.get("mount_point") or ""),
        "filesystemDevice": str(raw.get("filesystem_device") or ""),
        "mountOptions": qml_value(raw.get("mount_options") or []),
        "isWritable": raw.get("is_writable"),
        "isSteamTool": bool(raw.get("is_steam_tool", False)),
    }


def task_to_qml(task: Task) -> dict[str, Any]:
    """Present task progress on a normalized 0..1 scale."""

    raw = _model_dict(task)
    raw_progress = min(100.0, max(0.0, _number(raw.get("progress"))))
    operation = str(raw.get("task_type", "Task"))
    speed = raw.get("speed_label") or "-"
    remaining = raw.get("remaining_label") or "-"
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    return {
        "id": str(raw.get("id", "")),
        "gameId": str(raw.get("game_id", "")),
        "gameName": str(raw.get("game_name", "Unknown game")),
        "name": str(raw.get("title", operation)),
        "title": str(raw.get("title", operation)),
        "operation": operation,
        "type": operation,
        "progress": raw_progress / 100.0,
        "progressPercent": raw_progress,
        "speed": str(speed),
        "speedMbps": _number(raw.get("speed_mbps")),
        "remaining": str(remaining),
        "remainingDataGb": _number(raw.get("remaining_data_gb")),
        "status": str(raw.get("status", "queued")),
        "result": qml_value(raw.get("result") or {}),
        "error": str(raw.get("error") or ""),
        "createdAt": str(raw.get("created_at", "")),
        "updatedAt": str(raw.get("updated_at", "")),
        "stage": str(metadata.get("stage", "")),
        "scannedFiles": int(_number(metadata.get("scanned_files"))),
        "analyzedBytes": int(_number(metadata.get("analyzed_bytes"))),
        "elapsedSeconds": _number(metadata.get("elapsed_seconds")),
        "helperExitCode": (
            int(metadata["helper_exit_code"])
            if isinstance(metadata.get("helper_exit_code"), int)
            and not isinstance(metadata.get("helper_exit_code"), bool)
            else None
        ),
        "helperStdout": str(metadata.get("helper_stdout") or ""),
        "helperStderr": str(metadata.get("helper_stderr") or ""),
        "cacheHit": bool(metadata.get("cache_hit", False)),
        "readOnly": bool(metadata.get("read_only", False)),
        "cancellable": bool(metadata.get("cancellable", True)),
        "pausable": bool(metadata.get("pausable", True)),
        "processedFiles": int(_number(metadata.get("processed_files"))),
        "totalFiles": int(_number(metadata.get("total_files"))),
        "processedBytes": int(_number(metadata.get("processed_bytes"))),
        "totalBytes": int(_number(metadata.get("total_bytes"))),
        "currentFile": str(metadata.get("current_file", "")),
        "estimatedRemainingSeconds": (
            _number(metadata.get("estimated_remaining_seconds"))
            if metadata.get("estimated_remaining_seconds") is not None
            else None
        ),
        "beforeBytes": (
            int(_number(metadata.get("before_bytes")))
            if metadata.get("before_bytes") is not None
            else None
        ),
        "afterBytes": (
            int(_number(metadata.get("after_bytes")))
            if metadata.get("after_bytes") is not None
            else None
        ),
        "savedBytes": (
            int(_number(metadata.get("saved_bytes")))
            if metadata.get("saved_bytes") is not None
            else None
        ),
        "progressDeterminate": bool(
            metadata.get("progress_determinate", True)
        ),
        "verificationState": str(
            metadata.get("verification_state", "")
        ),
        "profile": str(metadata.get("profile", "")),
        "fullCompression": bool(metadata.get("full_compression", False)),
        "afterUpdate": bool(metadata.get("after_update", False)),
        "cancellationRequested": bool(
            metadata.get("cancellation_requested", False)
        ),
        "warnings": qml_value(metadata.get("warnings") or []),
        "outcome": str(metadata.get("outcome") or ""),
    }


def backup_to_qml(backup: Backup) -> dict[str, Any]:
    """Present a demo backup without leaking Python date/path objects."""

    raw = _model_dict(backup)
    created_at = str(raw.get("created_at", ""))
    date_label = str(raw.get("created_label") or created_at[:16].replace("T", " "))
    operation = str(raw.get("operation_type", "Backup"))
    size = raw.get("size_label") or _size_label(raw.get("size_gb"))
    return {
        "id": str(raw.get("id", "")),
        "gameId": str(raw.get("game_id", "")),
        "gameName": str(raw.get("game_name", "Unknown game")),
        "createdAt": created_at,
        "date": date_label,
        "operation": operation,
        "type": operation,
        "size": str(size),
        "sizeGb": _number(raw.get("size_gb")),
        "status": str(raw.get("status", "Available")),
    }


def system_info_to_qml(system_info: SystemInfo) -> dict[str, Any]:
    """Return both backend field names and common UI aliases."""

    raw = _model_dict(system_info)
    result = dict(raw)
    aliases = {
        "desktop_environment": "desktopEnvironment",
        "session_type": "sessionType",
        "cpu_cores": "cpuCores",
        "cpu_threads": "cpuThreads",
        "gpu_driver": "gpuDriver",
        "gpu_vendor": "gpuVendor",
        "vulkan_device": "vulkanDevice",
        "diagnostics_source": "diagnosticsSource",
        "capability_details": "capabilityDetails",
        "steam_library_detected": "steamLibraryDetected",
        "steam_executable_detected": "steamExecutableDetected",
        "steam_type": "steamType",
        "host_launch_available": "hostLaunchAvailable",
        "filesystems": "filesystems",
        "capabilities": "capabilities",
    }
    for source, target in aliases.items():
        if source in raw:
            result[target] = raw[source]

    filesystem_aliases = {
        "mount_point": "mountPoint",
        "compression_supported": "compressionSupported",
        "mount_options": "mountOptions",
        "filesystem_name": "filesystemName",
        "size_bytes": "sizeBytes",
        "used_bytes": "usedBytes",
        "available_bytes": "availableBytes",
    }
    filesystem_rows: list[dict[str, Any]] = []
    for filesystem in raw.get("filesystems", []):
        if not isinstance(filesystem, Mapping):
            continue
        row = dict(filesystem)
        for source, target in filesystem_aliases.items():
            if source in filesystem:
                row[target] = filesystem[source]
        filesystem_rows.append(row)
    result["filesystems"] = filesystem_rows
    return qml_value(result)


def settings_to_qml(settings: AppSettings) -> dict[str, Any]:
    """Expose persisted settings using concise camelCase keys."""

    raw = _model_dict(settings)
    result = dict(raw)
    aliases = {
        "theme": "themeMode",
        "automatic_updates": "automaticUpdates",
        "default_compression_profile": "defaultCompressionProfile",
        "automatic_compression_mode": "automaticCompressionMode",
        "automatic_compression_profile": "automaticCompressionProfile",
        "automatic_compression_delay_seconds": "automaticCompressionDelaySeconds",
        "automatic_compression_max_jobs": "automaticCompressionMaxJobs",
        "automatic_compression_min_free_gb": "automaticCompressionMinFreeGb",
        "automatic_compression_notify": "automaticCompressionNotify",
        "automatic_compression_skipped_app_ids": "automaticCompressionSkippedAppIds",
        "automatic_compression_libraries": "automaticCompressionLibraries",
        "cpu_limit_percent": "cpuUsageLimit",
        "gpu_limit_percent": "gpuUsageLimit",
        "backup_directory": "backupDirectory",
        "quarantine_directory": "quarantineDirectory",
        "library_directories": "libraryDirectories",
        "steam_installation_directories": "steamInstallationDirectories",
        "ignored_steam_libraries": "ignoredSteamLibraries",
        "experimental_features": "experimentalFeatures",
        "log_level": "logLevel",
        "show_steam_tools_and_runtimes": "showSteamToolsAndRuntimes",
        "controller_mode": "controllerMode",
        "swap_accept_back": "swapAcceptBack",
        "analog_deadzone": "analogDeadzone",
        "navigation_repeat_delay_ms": "navigationRepeatDelayMs",
        "navigation_repeat_rate_ms": "navigationRepeatRateMs",
        "hide_cursor_in_couch_mode": "hideCursorInCouchMode",
        "start_couch_mode_fullscreen": "startCouchModeFullscreen",
        "post_launch_behavior": "postLaunchBehavior",
        "interface_sounds": "interfaceSounds",
    }
    for source, target in aliases.items():
        if source in raw:
            result[target] = raw[source]
    return qml_value(result)


__all__ = [
    "backup_to_qml",
    "game_to_qml",
    "qml_value",
    "settings_to_qml",
    "system_info_to_qml",
    "task_to_qml",
]
