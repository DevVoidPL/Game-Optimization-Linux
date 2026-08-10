"""Isolated Qt probes used by test_gui_stability.py.

This module is intentionally not named ``test_*``: every probe needs a fresh
QGuiApplication and, for DPI checks, a fresh process environment.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

from PySide6.QtCore import (
    QObject,
    Q_ARG,
    QMetaObject,
    QPoint,
    QPointF,
    Property,
    QRectF,
    QTimer,
    Signal,
    Slot,
    Qt,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QWheelEvent
from PySide6.QtQuick import QQuickItem, QQuickView
from PySide6.QtQml import QQmlApplicationEngine

from game_optimization_linux.controllers.presenters import game_to_qml
from game_optimization_linux.models import FilesystemType, Game, Launcher


ROOT = Path(__file__).resolve().parents[1]
QML_ROOT = Path(
    os.environ.get(
        "GAME_OPTIMIZATION_QML_ROOT",
        str(ROOT / "src" / "game_optimization_linux" / "qml"),
    )
)
MESSAGES: list[str] = []


class StorageProbeController(QObject):
    historyChanged = Signal()
    settingsChanged = Signal()

    def __init__(
        self,
        history: list[dict[str, Any]],
        *,
        default_profile: str = "Auto",
    ) -> None:
        super().__init__()
        self._history = history
        self._settings = {"defaultCompressionProfile": default_profile}
        self.verification_calls: list[str] = []
        self.prepare_result: dict[str, Any] = {}
        self.prepare_calls: list[tuple[str, str, bool]] = []
        self.start_calls: list[str] = []

    @Property("QVariantList", notify=historyChanged)
    def selectedGameCompressionHistory(self) -> list[dict[str, Any]]:
        return self._history

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, Any]:
        return dict(self._settings)

    @Slot(str, result=bool)
    def verifyCompression(self, game_id: str) -> bool:
        self.verification_calls.append(game_id)
        return True

    @Slot(str, str, bool, result="QVariantMap")
    def prepareCompression(
        self,
        game_id: str,
        profile: str,
        changed_only: bool,
    ) -> dict[str, Any]:
        self.prepare_calls.append((game_id, profile, changed_only))
        return dict(self.prepare_result)

    @Slot(str, result=bool)
    def startCompression(self, plan_id: str) -> bool:
        self.start_calls.append(plan_id)
        return True


class UpdatesProbeController(QObject):
    """Small QML-facing controller used only by the isolated Updates probes."""

    updatesChanged = Signal()
    updatesSummaryChanged = Signal()
    applicationUpdateInfoChanged = Signal()

    def __init__(self, updates: list[dict[str, Any]]) -> None:
        super().__init__()
        self._updates = list(updates)
        self._summary = _updates_summary(self._updates)
        self._application_update_info = {
            "version": "0.4.0-test",
            "installationType": "development",
            "message": "Fixture package updates are managed outside this probe.",
        }
        self.prepare_calls: list[tuple[str, str, bool]] = []
        self.start_calls: list[str] = []
        self.analysis_calls: list[str] = []
        self.ignore_calls: list[str] = []
        self.open_calls: list[str] = []

    @Property("QVariantList", notify=updatesChanged)
    def updates(self) -> list[dict[str, Any]]:
        return self._updates

    @Property("QVariantMap", notify=updatesSummaryChanged)
    def updatesSummary(self) -> dict[str, Any]:
        return self._summary

    @Property("QVariantMap", notify=applicationUpdateInfoChanged)
    def applicationUpdateInfo(self) -> dict[str, Any]:
        return self._application_update_info

    def set_updates(self, updates: list[dict[str, Any]]) -> None:
        self._updates = list(updates)
        self._summary = _updates_summary(self._updates)
        self.updatesChanged.emit()
        self.updatesSummaryChanged.emit()

    @Slot(str, str, bool, result="QVariantMap")
    def prepareCompression(
        self, game_id: str, profile: str, changed_only: bool
    ) -> dict[str, Any]:
        self.prepare_calls.append((game_id, profile, changed_only))
        return {
            "planId": "updates-probe-plan",
            "valid": True,
            "canStart": True,
            "blockers": [],
            "gameName": "Compression fixture",
            "profile": profile,
            "plannedFileCount": 7,
            "estimatedSavingsLowBytes": 256 * 1024 * 1024,
            "estimatedSavingsHighBytes": 384 * 1024 * 1024,
            "fullPath": "/fixture/SteamLibrary/steamapps/common/Compression fixture",
        }

    @Slot(str, result=bool)
    def startCompression(self, plan_id: str) -> bool:
        self.start_calls.append(plan_id)
        return True

    @Slot(str, result=bool)
    def analyzeChanges(self, game_id: str) -> bool:
        self.analysis_calls.append(game_id)
        return True

    @Slot(str, result=bool)
    def ignoreUpdate(self, game_id: str) -> bool:
        self.ignore_calls.append(game_id)
        return True

    @Slot(str, result=bool)
    def openGame(self, game_id: str) -> bool:
        self.open_calls.append(game_id)
        return True


def _message_handler(_mode: object, _context: object, message: str) -> None:
    MESSAGES.append(str(message))


def _settle(application: QGuiApplication, rounds: int = 12) -> None:
    for _ in range(rounds):
        application.processEvents()
        time.sleep(0.003)


def _view(
    application: QGuiApplication,
    relative_path: str,
    width: int,
    height: int,
) -> tuple[QQuickView, QQuickItem]:
    view = QQuickView()
    view.engine().addImportPath(str(QML_ROOT))
    view.engine().rootContext().setContextProperty(
        "gameOptimizationDebugArtwork",
        os.environ.get("GAME_OPTIMIZATION_DEBUG_ARTWORK", "").strip() == "1",
    )
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(width, height)
    view.setSource((QML_ROOT / relative_path).as_uri())
    if view.status() == QQuickView.Status.Error:
        raise AssertionError("; ".join(error.toString() for error in view.errors()))
    root = view.rootObject()
    if not isinstance(root, QQuickItem):
        raise AssertionError(f"No QQuickItem root for {relative_path}")
    view.show()
    _settle(application)
    return view, root


def _descendants(root: QObject) -> list[QObject]:
    found: list[QObject] = []
    pending = list(root.children())
    if isinstance(root, QQuickItem):
        pending.extend(root.childItems())
    seen: set[int] = set()
    while pending:
        child = pending.pop()
        identity = id(child)
        if identity in seen:
            continue
        seen.add(identity)
        found.append(child)
        pending.extend(child.children())
        if isinstance(child, QQuickItem):
            pending.extend(child.childItems())
    return found


def _named(root: QObject, name: str) -> list[QObject]:
    return [item for item in _descendants(root) if item.objectName() == name]


def _item(root: QObject, name: str) -> QQuickItem:
    matches = _named(root, name)
    if not matches or not isinstance(matches[0], QQuickItem):
        names = sorted({item.objectName() for item in _descendants(root) if item.objectName()})
        raise AssertionError(f"Missing QQuickItem {name}; known names: {names}; messages: {MESSAGES}")
    return matches[0]


def _font_pixel_size(item: QQuickItem) -> int:
    font = item.property("font")
    pixel_size = getattr(font, "pixelSize", None)
    return int(pixel_size()) if callable(pixel_size) else -1


def _rect_in(item: QQuickItem, ancestor: QQuickItem) -> QRectF:
    point = item.mapToItem(ancestor, QPointF(0.0, 0.0))
    return QRectF(point.x(), point.y(), item.width(), item.height())


def _assert_inside(item: QQuickItem, ancestor: QQuickItem, tolerance: float = 1.5) -> None:
    rect = _rect_in(item, ancestor)
    if (
        rect.x() < -tolerance
        or rect.y() < -tolerance
        or rect.right() > ancestor.width() + tolerance
        or rect.bottom() > ancestor.height() + tolerance
        or rect.width() < 0
        or rect.height() < 0
    ):
        raise AssertionError(
            f"{item.objectName()} outside {ancestor.objectName()}: "
            f"{rect.getRect()} vs {(ancestor.width(), ancestor.height())}"
        )


def _assert_finite_nonnegative_geometry(root: QQuickItem) -> None:
    items = [root, *[item for item in _descendants(root) if isinstance(item, QQuickItem)]]
    for item in items:
        geometry = (item.x(), item.y(), item.width(), item.height())
        if not all(math.isfinite(value) for value in geometry):
            raise AssertionError(
                f"Non-finite geometry for {item.objectName() or type(item).__name__}: {geometry}"
            )
        if item.width() < 0 or item.height() < 0:
            raise AssertionError(
                f"Negative geometry for {item.objectName() or type(item).__name__}: {geometry}"
            )


def _variant(value: Any) -> Any:
    converter = getattr(value, "toVariant", None)
    return converter() if callable(converter) else value


def _invoke_qml(item: QObject, function_name: str, *arguments: Any) -> Any:
    q_arguments = []
    for argument in arguments:
        if isinstance(argument, str):
            q_arguments.append(Q_ARG("QVariant", argument))
        elif isinstance(argument, int):
            q_arguments.append(Q_ARG("QVariant", argument))
        else:
            q_arguments.append(Q_ARG("QVariant", argument))
    if not QMetaObject.invokeMethod(item, function_name, Qt.DirectConnection, *q_arguments):
        raise AssertionError(f"QML function {function_name} could not be invoked")
    return None


def _update_row(
    index: int,
    *,
    state: str = "Update detected",
    section: str = "game_updates",
    available: bool = True,
    can_analyze: bool = True,
    can_compress: bool = False,
    can_ignore: bool = True,
    error: str = "",
    long_text: bool = False,
) -> dict[str, Any]:
    suffix = (
        " - a deliberately long title that verifies elision and bounded update-card geometry"
        if long_text
        else ""
    )
    return {
        "rowId": f"{section}:steam-update-{index}:fixture-{index}",
        "sectionKey": section,
        "gameId": f"steam-update-{index}",
        "name": f"Update fixture game {index}{suffix}",
        "launcher": "Steam",
        "buildId": f"20260726{index:03d}",
        "detectedAt": "2026-07-26T10:15:00+00:00",
        "lastCompressionAt": "2026-07-25T08:00:00+00:00",
        "compressionState": state,
        "changedBytes": (index + 1) * 128 * 1024 * 1024,
        "changedFileCount": index + 3,
        "libraryAvailable": available,
        "filesystem": "Btrfs" if available else "Unknown",
        "canAnalyze": can_analyze and available,
        "canCompress": can_compress and available,
        "canIgnore": can_ignore,
        "activeTaskId": f"task-{index}" if state in {"Compressing", "Analyzing"} else "",
        "error": error,
        "recommendedProfile": "Auto",
        "portraitArtwork": "",
        "path": (
            "/run/media/fixture/SteamLibrary/steamapps/common/"
            f"Update fixture game {index}{suffix}"
        ),
    }


def _updates_fixture(scenario: str) -> list[dict[str, Any]]:
    if scenario == "empty":
        return []
    if scenario == "one":
        return [_update_row(0)]
    if scenario == "long":
        return [
            _update_row(
                0,
                state="Analysis required",
                section="compression_pending",
                can_analyze=False,
                can_compress=True,
                long_text=True,
            )
        ]
    if scenario == "disconnected":
        return [
            _update_row(
                0,
                state="Drive disconnected",
                available=False,
                can_analyze=False,
                can_compress=False,
            )
        ]
    if scenario == "active":
        return [
            _update_row(
                0,
                state="Compressing",
                section="compression_pending",
                can_analyze=False,
                can_compress=False,
                can_ignore=False,
            )
        ]
    if scenario == "error":
        return [
            _update_row(
                0,
                state="Failed",
                section="compression_pending",
                can_analyze=True,
                error=(
                    "The fixture failed while checking a very long path; this message "
                    "must stay inside the update row without changing its width."
                ),
            )
        ]

    states = (
        ("Update detected", "game_updates", True, False),
        ("Waiting for launcher", "game_updates", False, False),
        ("Analysis required", "compression_pending", True, False),
        ("Compression pending", "compression_pending", False, True),
        ("Optimized", "recently_optimized", False, False),
        ("Verification required", "compression_pending", True, False),
    )
    rows: list[dict[str, Any]] = []
    for index in range(36):
        state, section, can_analyze, can_compress = states[index % len(states)]
        rows.append(
            _update_row(
                index,
                state=state,
                section=section,
                can_analyze=can_analyze,
                can_compress=can_compress,
                can_ignore=state != "Optimized",
                long_text=index % 9 == 0,
            )
        )
    return rows


def _updates_summary(updates: list[dict[str, Any]]) -> dict[str, Any]:
    attention_states = {
        "Update detected",
        "Waiting for launcher",
        "Analysis required",
        "Verification required",
        "Failed",
        "Drive disconnected",
    }
    pending_states = {"Compression pending", "Compressing", "Analyzing", "Queued"}
    optimized = sum(
        str(update.get("compressionState", "")) == "Optimized" for update in updates
    )
    return {
        "needsCheckCount": sum(
            str(update.get("compressionState", "")) in attention_states for update in updates
        ),
        "pendingCount": sum(
            str(update.get("compressionState", "")) in pending_states for update in updates
        ),
        "recentlyOptimizedCount": optimized,
        "recentRecoveredBytes": optimized * 512 * 1024 * 1024,
    }


def _analysis_report(*, filesystem: str = "Btrfs", path_ok: bool = True) -> dict[str, Any]:
    is_btrfs = filesystem.casefold() == "btrfs"
    profile = {
        "estimated_size_low_bytes": 800,
        "estimated_size_high_bytes": 900,
        "estimated_savings_low_bytes": 100,
        "estimated_savings_high_bytes": 200,
        "estimated_time_low_seconds": 10,
        "estimated_time_high_seconds": 20,
        "cpu_usage": "Moderate",
    }
    return {
        "game_id": "steam-test",
        "created_at": "2026-07-22T10:00:00+00:00",
        "path_exists": path_ok,
        "path_is_directory": path_ok,
        "filesystem": filesystem,
        "is_btrfs": is_btrfs,
        "scan_complete": True,
        "profiles_unlocked": is_btrfs and path_ok,
        "profiles": {name: dict(profile) for name in ("Fast", "Balanced", "Maximum", "Auto")},
        "warnings": [],
        "game_running": False,
        "writable": True,
        "compression_eligible": is_btrfs and path_ok,
        "btrfs_du": {
            "available": is_btrfs and path_ok,
            "state": "not_detected" if is_btrfs and path_ok else "unknown",
            "total_bytes": 1000,
            "exclusive_bytes": 1000,
            "set_shared_bytes": 0,
            "estimated_growth_bytes": 0,
        },
        "compsize": {"available": False, "message": "compsize not installed"},
        "logical_bytes": 1000,
        "physical_bytes": 1000,
        "available_bytes": 10000,
        "file_count": 2,
        "directory_count": 1,
        "symlink_count": 0,
        "permission_errors": [],
        "sampled_bytes": 100,
        "sampling_codec": "zstd",
    }


def _game_data(report: dict[str, Any] | None = None, *, available: bool = True) -> dict[str, Any]:
    report = dict(report or {})
    report_ready = bool(report)
    return {
        "id": "steam-test",
        "name": "Test Game",
        "sizeBytes": 10 * 1024 * 1024 * 1024,
        "libraryAvailable": available,
        "analysisAllowed": available,
        "analysisReport": report,
        "analysisReportAvailable": report_ready,
        "analysisPathAvailable": bool(
            report_ready and report.get("path_exists") is True and report.get("path_is_directory") is True
        ),
        "analysisIsBtrfs": bool(report_ready and report.get("is_btrfs") is True),
        "analysisScanComplete": bool(report_ready and report.get("scan_complete") is True),
        "analysisProfilesUnlocked": bool(report_ready and report.get("profiles_unlocked") is True),
        "analysisGameRunning": bool(report_ready and report.get("game_running") is True),
        "analysisHasWarnings": bool(report_ready and report.get("warnings")),
    }


def probe_storage(application: QGuiApplication) -> dict[str, Any]:
    view, root = _view(application, "pages/details/StorageTab.qml", 1050, 900)
    default_profile_controller = StorageProbeController([], default_profile="Balanced")
    root.setProperty("controller", default_profile_controller)
    _settle(application)
    if root.property("selectedMode") != "Balanced":
        raise AssertionError("Storage ignored the persisted default compression profile")
    valid = _analysis_report()
    states = (
        ("no_report", _game_data(), [], False, False),
        ("queued", _game_data(), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "queued"}], False, True),
        ("running", _game_data(), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "analyzing"}], False, True),
        ("cancelled", _game_data(valid), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "cancelled"}], False, False),
        ("failed", _game_data(valid), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "failed", "error": "fixture"}], False, False),
        ("ext4", _game_data(_analysis_report(filesystem="ext4")), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "completed"}], False, False),
        ("missing_path", _game_data(_analysis_report(path_ok=False)), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "completed"}], False, False),
        ("completed", _game_data(valid), [{"id": "a", "gameId": "steam-test", "operation": "Analysis", "status": "completed"}], True, False),
        ("unavailable", _game_data(available=False), [], False, False),
    )
    bool_properties = (
        "hasAnalysisTask",
        "analysisQueued",
        "analysisRunning",
        "analysisActive",
        "analysisCancelled",
        "analysisFailed",
        "reportReady",
        "pathAvailable",
        "confirmedBtrfs",
        "scanSucceeded",
        "analysisSucceeded",
        "analysisComplete",
        "hasSelectedProfile",
        "profilesEnabled",
        "showRunningWarning",
        "hasWarnings",
        "analysisAllowed",
    )
    results: dict[str, Any] = {}
    for name, game, tasks, profiles_expected, active_expected in states:
        root.setProperty("gameData", game)
        root.setProperty("tasksData", tasks)
        _settle(application)
        observed = {key: root.property(key) for key in bool_properties}
        if not all(type(value) is bool for value in observed.values()):
            raise AssertionError(f"Non-boolean Storage state in {name}: {observed}")
        if observed["profilesEnabled"] is not profiles_expected:
            raise AssertionError(f"Wrong profile state for {name}: {observed}")
        if observed["analysisActive"] is not active_expected:
            raise AssertionError(f"Wrong active state for {name}: {observed}")
        for profile in ("Fast", "Balanced", "Maximum", "Auto"):
            button = _item(root, "compressionProfileButton_" + profile)
            if bool(button.property("enabled")) is not profiles_expected:
                raise AssertionError(f"Wrong {profile} enabled state for {name}")
        results[name] = observed

    root.setProperty("selectedMode", "")
    _settle(application)
    if root.property("hasSelectedProfile") is not False:
        raise AssertionError("An empty compression profile was treated as selected")
    estimated_game = _game_data(valid)
    estimated_game.update(
        {
            "compressionClassificationKey": "moderately_compressed",
            "lastOperationReclaimedAvailable": True,
            "lastOperationReclaimedBytes": 256 * 1024 * 1024,
        }
    )
    estimated_game["benchmarkEstimate"] = {
        "available": True,
        "appId": "4242",
        "buildId": "100",
        "baselineBytes": 10 * 1024 * 1024 * 1024,
        "zstd3PotentialBytes": 1258291200,
        "zstd9PotentialBytes": 1468006400,
        "zstd3EstimatedSizeBytes": 8589934592,
        "zstd9EstimatedSizeBytes": 8388608000,
        "projections": {
            "3": {
                "available": True,
                "level": 3,
                "source": "benchmark_estimate",
                "currentSavingBytes": 2 * 1024 * 1024 * 1024,
                "estimatedTotalPotentialBytes": 2.5 * 1024 * 1024 * 1024,
                "estimatedAdditionalSavingBytes": 0.5 * 1024 * 1024 * 1024,
                "estimatedPhysicalBytes": 7.5 * 1024 * 1024 * 1024,
                "lowBenefit": True,
                "additionalConfirmationRequired": True,
            }
        },
    }
    root.setProperty("gameData", estimated_game)
    root.setProperty("tasksData", [])
    root.setProperty("selectedMode", "Balanced")
    _settle(application)
    estimate_probe = {
        "available": root.property("benchmarkEstimateAvailable"),
        "totalLabel": _item(root, "benchmarkTotalPotential").property("label"),
        "totalValue": _item(root, "benchmarkTotalPotential").property("value"),
        "additionalLabel": _item(
            root, "benchmarkAdditionalSaving"
        ).property("label"),
        "additionalValue": _item(
            root, "benchmarkAdditionalSaving"
        ).property("value"),
        "sizeLabel": _item(root, "benchmarkEstimateSize").property("label"),
        "sizeValue": _item(root, "benchmarkEstimateSize").property("value"),
        "sourceValue": _item(root, "benchmarkEstimateSource").property("value"),
        "lowBenefitWarning": bool(
            _item(root, "lowBenefitWarning").property("visible")
        ),
        "classification": _item(
            root, "compressionClassification"
        ).property("value"),
        "lastOperationReclaimed": _item(
            root, "lastGameOptimizationOperationReclaimed"
        ).property("value"),
        "profitability": _item(root, "profitabilityMetric").property("value"),
        "rewrite": _item(root, "estimatedRewriteMetric").property("value"),
    }
    if estimate_probe["available"] is not True:
        raise AssertionError(f"Benchmark estimate was not exposed: {estimate_probe}")
    results["benchmark_estimate"] = estimate_probe
    failed_measurement_controller = StorageProbeController([])
    root.setProperty("controller", failed_measurement_controller)
    root.setProperty("gameData", estimated_game)
    root.setProperty(
        "tasksData",
        [
            {
                "id": "verification-failed",
                "gameId": "steam-test",
                "operation": "Verification",
                "status": "failed",
                "error": "compsize exited with status 1: No files.",
            }
        ],
    )
    _settle(application)
    failed_status = _item(root, "compressionMeasurementStatus").property("text")
    failed_message = _item(root, "measurementFailureMessage")
    failure_probe = {
        "status": failed_status,
        "message": failed_message.property("text"),
        "messageVisible": bool(failed_message.property("visible")),
    }
    if failed_status != "Measurement failed":
        raise AssertionError(f"Failed compsize was shown as measured: {failure_probe}")
    if (
        not failure_probe["messageVisible"]
        or "No files" not in str(failure_probe["message"])
    ):
        raise AssertionError(f"Failed compsize detail was not visible: {failure_probe}")
    results["measurement_failure"] = failure_probe

    root.setProperty(
        "tasksData",
        [
            {
                "id": "verification-basic",
                "gameId": "steam-test",
                "operation": "Verification",
                "status": "completed",
                "result": {
                    "logical_bytes": 186 * 1024 * 1024,
                    "exclusive_bytes": 160 * 1024 * 1024,
                    "shared_bytes": 6 * 1024 * 1024,
                    "compsize_disk_bytes": None,
                    "compsize_uncompressed_bytes": None,
                    "compsize_referenced_bytes": None,
                    "measurement_source": "basic_btrfs",
                    "measurement_error": (
                        "Exact compsize measurement is unavailable because the "
                        "optional privileged host component is not installed"
                    ),
                },
            }
        ],
    )
    _settle(application)
    unavailable_message = _item(root, "measurementFailureMessage")
    unavailable_probe = {
        "badge": _item(root, "compressionMeasurementStatus").property("text"),
        "source": _item(root, "measuredCurrentStatus").property("value"),
        "status": _item(root, "measuredCurrentStatusValue").property("value"),
        "physical": _item(root, "measuredCurrentPhysical").property("value"),
        "saving": _item(root, "measuredCurrentEffect").property("value"),
        "exclusive": _item(root, "basicBtrfsExclusive").property("value"),
        "shared": _item(root, "basicBtrfsShared").property("value"),
        "message": unavailable_message.property("text"),
        "messageVisible": bool(unavailable_message.property("visible")),
    }
    if unavailable_probe["badge"] != "Exact measurement unavailable":
        raise AssertionError(f"Unavailable compsize was shown as failed: {unavailable_probe}")
    if unavailable_probe["source"] != "Basic Btrfs verification":
        raise AssertionError(f"Basic Btrfs source was hidden: {unavailable_probe}")
    if unavailable_probe["physical"] != "Not available" or unavailable_probe["saving"] != "Not available":
        raise AssertionError(f"Basic data was presented as compsize saving: {unavailable_probe}")
    if not unavailable_probe["messageVisible"] or "may still be working correctly" not in str(unavailable_probe["message"]):
        raise AssertionError(f"Unavailable explanation was not visible: {unavailable_probe}")
    results["measurement_unavailable"] = unavailable_probe

    gib = 1024 * 1024 * 1024
    current_verification_controller = StorageProbeController([])
    current_verification_controller.prepare_result = {
        "planId": "low-benefit-plan",
        "id": "low-benefit-plan",
        "valid": True,
        "eligible": True,
        "canStart": True,
        "blockers": [],
        "warnings": [],
        "gameName": "Test Game",
        "profile": "Balanced",
        "persistentCompressionAlgorithm": "zstd",
        "oneTimeRecompressionLevel": 3,
        "totalFiles": 2,
        "totalBytes": 10 * gib,
        "requiredFreeBytes": gib,
        "sharedExtentState": "not_detected",
        "profitabilityAvailable": True,
        "profitability": {
            "currentSavingBytes": 20 * 1024 * 1024,
        },
        "estimatedAdditionalSavingBytes": 512 * 1024 * 1024,
        "estimatedPhysicalBytes": int(7.5 * gib),
        "lowBenefit": True,
        "additionalConfirmationRequired": True,
    }
    verification_game = dict(estimated_game)
    verification_game.update(
        {
            "analysisReport": {},
            "analysisReportAvailable": False,
            "analysisPathAvailable": False,
            "analysisIsBtrfs": False,
            "analysisScanComplete": False,
            "analysisProfilesUnlocked": False,
        }
    )
    root.setProperty("controller", current_verification_controller)
    root.setProperty("gameData", verification_game)
    root.setProperty(
        "tasksData",
        [
            {
                "id": "verification-current",
                "gameId": "steam-test",
                "operation": "Verification",
                "status": "completed",
                "updatedAt": "2026-07-30T12:00:00+00:00",
                "result": {
                    # Exercise the real compatibility case: the helper result
                    # has compsize data but no logical_bytes field.
                    "compsize_disk_bytes": 166 * 1024 * 1024,
                    "compsize_uncompressed_bytes": 186 * 1024 * 1024,
                    "compsize_referenced_bytes": 186 * 1024 * 1024,
                    "measurement_source": "polkit_compsize",
                },
            },
            {
                "id": "verification-old-failure",
                "gameId": "steam-test",
                "operation": "Verification",
                "status": "failed",
                "updatedAt": "2026-07-30T11:00:00+00:00",
                "error": "compsize exited with status 1: No files.",
            },
        ],
    )
    _settle(application)
    current_status = _item(root, "compressionMeasurementStatus").property("text")
    current_logical = _item(root, "measuredLogicalSize")
    current_physical = _item(root, "measuredCurrentPhysical")
    current_effect = _item(root, "measuredCurrentEffect")
    current_ratio = _item(root, "measuredCurrentRatio")
    current_measurement_status = _item(root, "measuredCurrentStatus")
    current_status_value = _item(root, "measuredCurrentStatusValue")
    old_failure_message = _item(root, "measurementFailureMessage")
    verification_probe = {
        "badge": current_status,
        "logicalLabel": current_logical.property("label"),
        "logicalValue": current_logical.property("value"),
        "physicalLabel": current_physical.property("label"),
        "physicalValue": current_physical.property("value"),
        "effectLabel": current_effect.property("label"),
        "effectValue": current_effect.property("value"),
        "ratioValue": current_ratio.property("value"),
        "statusLabel": current_measurement_status.property("label"),
        "statusValue": current_measurement_status.property("value"),
        "measurementStatusLabel": current_status_value.property("label"),
        "measurementStatusValue": current_status_value.property("value"),
        "currentVisible": bool(current_physical.property("visible"))
        and bool(current_effect.property("visible"))
        and bool(current_measurement_status.property("visible")),
        "beforeVisible": bool(
            _item(root, "measuredPhysicalBefore").property("visible")
        ),
        "afterVisible": bool(
            _item(root, "measuredPhysicalAfter").property("visible")
        ),
        "operationVisible": bool(
            _item(root, "measuredOperationReclaimed").property("visible")
        ),
        "oldFailureVisible": bool(old_failure_message.property("visible")),
    }
    if (
        verification_probe["badge"] != "Measured"
        or verification_probe["logicalValue"] in {"0 B", "Not available"}
        or verification_probe["physicalValue"] == "Not available"
        or verification_probe["effectValue"] == "Not available"
        or verification_probe["ratioValue"] == "Not available"
        or verification_probe["statusValue"]
        != "Exact compsize / polkit_compsize"
        or verification_probe["measurementStatusValue"]
        != "Exact measurement completed"
        or not verification_probe["currentVisible"]
        or verification_probe["beforeVisible"]
        or verification_probe["afterVisible"]
        or verification_probe["operationVisible"]
        or verification_probe["oldFailureVisible"]
    ):
        raise AssertionError(
            "Verification-only state mixed old or before/after data: "
            f"{verification_probe}"
        )
    results["current_verification"] = verification_probe

    root.setProperty("gameData", estimated_game)
    _settle(application)
    compress_button = _item(root, "compressGameButton")
    if not bool(compress_button.property("enabled")):
        raise AssertionError("Low estimated benefit incorrectly blocked manual compression")
    QMetaObject.invokeMethod(
        root,
        "prepareCompressionPlan",
        Qt.ConnectionType.DirectConnection,
    )
    _settle(application)
    profitability_matches = _named(root, "profitabilityConfirmDialog")
    final_matches = _named(root, "compressionConfirmDialog")
    if not profitability_matches or not final_matches:
        raise AssertionError("Compression confirmation dialogs were not created")
    profitability_dialog = profitability_matches[0]
    final_dialog = final_matches[0]
    confirmation_probe = {
        "manualEnabled": bool(compress_button.property("enabled")),
        "extraConfirmationVisible": bool(
            profitability_dialog.property("visible")
        ),
        "finalConfirmationVisible": bool(final_dialog.property("visible")),
        "prepareCalls": len(current_verification_controller.prepare_calls),
        "startCalls": len(current_verification_controller.start_calls),
    }
    if (
        not confirmation_probe["manualEnabled"]
        or not confirmation_probe["extraConfirmationVisible"]
        or confirmation_probe["finalConfirmationVisible"]
        or confirmation_probe["prepareCalls"] != 1
        or confirmation_probe["startCalls"] != 0
    ):
        raise AssertionError(
            "Low-benefit manual guard did not require a separate confirmation: "
            f"{confirmation_probe}"
        )
    QMetaObject.invokeMethod(
        profitability_dialog,
        "close",
        Qt.ConnectionType.DirectConnection,
    )
    results["low_benefit_confirmation"] = confirmation_probe

    measured_controller = StorageProbeController(
        [
            {
                "measurement_authoritative": True,
                "before": {
                    "logical_bytes": 10 * gib,
                    "compsize_disk_bytes": 8 * gib,
                    "measurement_source": "polkit_helper",
                },
                "after": {
                    "logical_bytes": 10 * gib,
                    "compsize_disk_bytes": int(7.5 * gib),
                    "compsize_uncompressed_bytes": 10 * gib,
                    "compsize_referenced_bytes": 10 * gib,
                    "measurement_source": "polkit_helper",
                },
                "actual_saved_bytes": int(0.5 * gib),
                "active_files_compression_effect_bytes": int(2.5 * gib),
                "filesystem_free_delta_bytes": int(0.4 * gib),
            }
        ]
    )
    root.setProperty("controller", measured_controller)
    root.setProperty("gameData", estimated_game)
    root.setProperty("tasksData", [])
    _settle(application)
    measurement_probe = {
        "logical": {
            "label": _item(root, "measuredLogicalSize").property("label"),
            "value": _item(root, "measuredLogicalSize").property("value"),
        },
        "physicalBefore": {
            "label": _item(root, "measuredPhysicalBefore").property("label"),
            "value": _item(root, "measuredPhysicalBefore").property("value"),
        },
        "physicalAfter": {
            "label": _item(root, "measuredPhysicalAfter").property("label"),
            "value": _item(root, "measuredPhysicalAfter").property("value"),
        },
        "activeEffect": {
            "label": _item(root, "measuredActiveEffect").property("label"),
            "value": _item(root, "measuredActiveEffect").property("value"),
        },
        "operationReclaimed": {
            "label": _item(root, "measuredOperationReclaimed").property("label"),
            "value": _item(root, "measuredOperationReclaimed").property("value"),
        },
        "filesystemDelta": {
            "label": _item(root, "measuredFilesystemDelta").property("label"),
            "value": _item(root, "measuredFilesystemDelta").property("value"),
        },
    }
    measurement_values = [entry["value"] for entry in measurement_probe.values()]
    if len(set(measurement_values)) != len(measurement_values):
        raise AssertionError(
            f"Compression measurements were not presented separately: {measurement_probe}"
        )
    results["measured_compression"] = measurement_probe
    view.close()

    details_view, details_root = _view(
        application,
        "pages/GameDetailsPage.qml",
        1200,
        900,
    )
    details_game = dict(estimated_game)
    details_game.update(
        {
            "physicalSize": "166 MiB",
            "savedSpace": "20.0 MiB",
            "launchAllowed": True,
            "analysisAllowed": True,
            "status": "Ready",
            "filesystem": "btrfs",
        }
    )
    details_root.setProperty("gameData", details_game)
    details_root.setProperty(
        "tasksData",
        [
            {
                "id": "verification-current-details",
                "gameId": "steam-test",
                "operation": "Verification",
                "status": "completed",
                "updatedAt": "2026-07-30T12:30:00+00:00",
                "result": {
                    "compsize_disk_bytes": 166 * 1024 * 1024,
                    "compsize_uncompressed_bytes": 186 * 1024 * 1024,
                    "compsize_referenced_bytes": 186 * 1024 * 1024,
                    "measurement_source": "polkit_helper",
                },
            }
        ],
    )
    _item(details_root, "detailsTabBar").setProperty("currentIndex", 1)
    _settle(application, 24)
    consistency_probe = {
        "headerPhysical": _item(
            details_root, "detailsHeaderPhysicalSize"
        ).property("value"),
        "cardPhysical": _item(
            details_root, "measuredCurrentPhysical"
        ).property("value"),
        "headerSaving": _item(
            details_root, "detailsHeaderSavedSpace"
        ).property("value"),
        "cardSaving": _item(
            details_root, "measuredCurrentEffect"
        ).property("value"),
    }
    if (
        consistency_probe["headerPhysical"] != consistency_probe["cardPhysical"]
        or consistency_probe["headerSaving"] != consistency_probe["cardSaving"]
    ):
        raise AssertionError(
            "The details header and Storage card used different compsize data: "
            f"{consistency_probe}"
        )
    results["header_card_consistency"] = consistency_probe
    details_view.close()
    return results


def _card_game(index: int) -> dict[str, Any]:
    disconnected = index == 1
    return {
        "id": f"steam-{index}",
        "name": "A very long game title used to verify two-line layout " + str(index),
        "launcher": "Steam",
        "dataSource": "Steam",
        "size": "123.4 GB",
        "filesystem": "ntfs3" if disconnected else "Btrfs" if index % 2 == 0 else "ext4",
        "path": "/run/media/example/SteamLibrary/steamapps/common/A very long directory name/" + str(index),
        "status": "Drive disconnected" if disconnected else "Ready",
        "libraryAvailable": not disconnected,
        "availabilityStatus": "Library unavailable" if disconnected else "",
        "compressionAvailable": index % 2 == 0 and not disconnected,
        "compressionClassificationKey": (
            "strongly_compressed" if index % 2 == 0 else "measurement_unavailable"
        ),
        "portraitArtwork": "",
        "headerArtwork": "",
    }


def _overlap_area(first: QRectF, second: QRectF) -> float:
    intersection = first.intersected(second)
    return max(0.0, intersection.width()) * max(0.0, intersection.height())


def _wheel_over(
    application: QGuiApplication,
    view: QQuickView,
    item: QQuickItem,
    *,
    steps: int = 3,
    angle_delta_y: int = -120,
) -> None:
    local = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    local = QPointF(
        min(max(2.0, local.x()), max(2.0, view.width() - 2.0)),
        min(max(2.0, local.y()), max(2.0, view.height() - 2.0)),
    )
    global_point = view.mapToGlobal(QPoint(round(local.x()), round(local.y())))
    for _ in range(steps):
        event = QWheelEvent(
            local,
            QPointF(global_point),
            QPoint(),
            QPoint(0, angle_delta_y),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
            Qt.MouseEventSource.MouseEventNotSynthesized,
        )
        application.sendEvent(view, event)
        _settle(application, 3)


def probe_cards(
    application: QGuiApplication,
    width: int,
    height: int,
    theme_mode: str,
) -> dict[str, Any]:
    view, root = _view(application, "pages/GamesPage.qml", width, height)
    theme = view.engine().singletonInstance("Game Optimization", "Theme")
    if isinstance(theme, QObject):
        theme.setProperty("mode", theme_mode)
    root.setProperty("gamesData", [_card_game(index) for index in range(24)])
    root.setProperty(
        "libraryCompressionData",
        [
            {
                "libraryPath": "/synthetic/Complete Steam Library",
                "gameCount": 10,
                "measuredGameCount": 10,
                "uncompressedBytes": 45.25 * 1024**3,
                "diskUsageBytes": 44.21 * 1024**3,
                "currentSavingBytes": 1.04 * 1024**3,
                "savingPercent": 2.30,
                "fullyMeasured": True,
                "lastFullMeasurementAt": "2026-07-31T12:00:00+00:00",
                "source": "compsize",
            },
            {
                "libraryPath": "/synthetic/Partial Steam Library",
                "gameCount": 14,
                "measuredGameCount": 7,
                "uncompressedBytes": 362.40 * 1024**3,
                "diskUsageBytes": 350.58 * 1024**3,
                "currentSavingBytes": 11.82 * 1024**3,
                "savingPercent": 3.26,
                "fullyMeasured": False,
                "lastFullMeasurementAt": "",
                "source": "compsize",
            },
        ],
    )
    root.setProperty("gridMode", True)
    _settle(application, 24)

    grid = _item(root, "gamesGridView")
    page_scroll = _item(root, "gamesPageFlickable")
    cards = [item for item in _named(root, "gameGridCard") if isinstance(item, QQuickItem)]
    if not cards:
        raise AssertionError(
            "GridView created no visible cards; "
            f"games={root.property('gamesData')!r} filtered={root.property('filteredGames')!r} "
            f"grid={grid.width()}x{grid.height()} names="
            f"{sorted({item.objectName() for item in _descendants(root) if item.objectName()})} "
            f"messages={MESSAGES}"
        )
    card_rects: list[QRectF] = []
    for card in cards:
        if card.width() < 232 - 1.5 or card.width() > 280 + 1.5:
            raise AssertionError(f"Unreasonable card width: {card.width()}")
        _assert_inside(card, card.parentItem())
        cover = _item(card, "gridCardCoverSection")
        info = _item(card, "gridCardInformationSection")
        _assert_inside(cover, card)
        _assert_inside(info, card)
        cover_rect = _rect_in(cover, card)
        info_rect = _rect_in(info, card)
        if cover_rect.bottom() > info_rect.top() + 1.5:
            raise AssertionError("Cover overlaps the information section")
        for name in (
            "gridCardTitle",
            "gridCardStatus",
            "gridCardFilesystem",
            "gridCardPath",
            "gridCardFooterRow",
            "gridCardFooterDescription",
            "gridCardDetailsButton",
        ):
            _assert_inside(_item(card, name), card)
        path_rect = _rect_in(_item(card, "gridCardPath"), card)
        footer_rect = _rect_in(_item(card, "gridCardFooterRow"), card)
        if path_rect.bottom() > footer_rect.top() + 1.5:
            raise AssertionError("Path overlaps the card footer")
        card_rects.append(_rect_in(card, grid))

    for index, first in enumerate(card_rects):
        for second in card_rects[index + 1 :]:
            if _overlap_area(first, second) > 1.0:
                raise AssertionError(f"Grid cards overlap: {first.getRect()} / {second.getRect()}")

    viewport_height = float(page_scroll.property("height"))
    content_height = float(page_scroll.property("contentHeight"))
    compact_summary = _item(root, "compactLibrarySummary")
    collapsed_summary_height = float(
        _item(root, "libraryCompressionSummary").property("height")
    )
    if root.property("librariesExpanded") is not False or not compact_summary.isVisible():
        raise AssertionError("Library storage summary is not compact by default")
    if content_height <= viewport_height:
        raise AssertionError(
            f"Games content is not scrollable: {content_height} <= {viewport_height}"
        )
    scroll_bar = _item(root, "gamesPageScrollBar")
    if not scroll_bar.isVisible() or float(scroll_bar.property("size")) >= 1.0:
        raise AssertionError("The Games vertical scrollbar did not expose overflow")

    wheel_results: dict[str, float] = {}
    first_card = min(
        cards,
        key=lambda item: (_rect_in(item, grid).top(), _rect_in(item, grid).left()),
    )
    for label, target in (
        ("summary", _item(root, "libraryCompressionSummary")),
        ("filters", _item(root, "gamesFilterCard")),
        ("card", first_card),
    ):
        target_position = target.mapToItem(page_scroll, QPointF(0, 0))
        current_y = float(page_scroll.property("contentY"))
        content_target_y = current_y + target_position.y()
        maximum_y = max(0.0, content_height - viewport_height)
        page_scroll.setProperty(
            "contentY",
            min(maximum_y, max(0.0, content_target_y - viewport_height * 0.25)),
        )
        _settle(application, 5)
        before_wheel = float(page_scroll.property("contentY"))
        _wheel_over(application, view, target)
        after_wheel = float(page_scroll.property("contentY"))
        if after_wheel <= before_wheel and before_wheel < maximum_y - 1:
            raise AssertionError(
                f"Mouse wheel was intercepted over {label}: "
                f"{before_wheel} -> {after_wheel}"
            )
        wheel_results[label] = after_wheel - before_wheel

    maximum_y = max(
        0.0,
        float(page_scroll.property("contentHeight"))
        - float(page_scroll.property("height")),
    )
    page_scroll.setProperty("contentY", maximum_y)
    _settle(application, 8)
    last_card = max(cards, key=lambda item: (_rect_in(item, grid).bottom(), _rect_in(item, grid).right()))
    last_card_viewport = _rect_in(last_card, page_scroll)
    bottom_margin = viewport_height - last_card_viewport.bottom()
    if last_card_viewport.bottom() > viewport_height + 1.5 or bottom_margin < 8:
        raise AssertionError(
            "The last grid card or bottom margin is not reachable: "
            f"card={last_card_viewport.getRect()} margin={bottom_margin}"
        )
    empty_space = _item(root, "gamesBottomSpacer")
    before_empty_wheel = float(page_scroll.property("contentY"))
    _wheel_over(
        application,
        view,
        empty_space,
        angle_delta_y=120,
    )
    after_empty_wheel = float(page_scroll.property("contentY"))
    if after_empty_wheel >= before_empty_wheel:
        raise AssertionError("Mouse wheel was intercepted over empty page space")
    wheel_results["emptySpace"] = before_empty_wheel - after_empty_wheel

    from game_optimization_linux.translations import TranslationManager

    translation_manager = TranslationManager(application)
    translation_manager.attach_engine(view.engine())
    language_heights: dict[str, float] = {}
    for language in ("pl", "es", "en"):
        if not translation_manager.set_language(language):
            raise AssertionError(f"Could not install {language} in cards probe")
        _settle(application, 8)
        translated_height = float(page_scroll.property("contentHeight"))
        translated_maximum = max(0.0, translated_height - viewport_height)
        current_offset = float(page_scroll.property("contentY"))
        if translated_height <= viewport_height or current_offset > translated_maximum + 1.5:
            raise AssertionError(
                f"Language {language} broke content geometry: "
                f"height={translated_height}, offset={current_offset}, max={translated_maximum}"
            )
        language_heights[language] = translated_height

    root.setProperty("gridMode", False)
    _settle(application, 24)
    rows = [item for item in _named(root, "gameListRow") if isinstance(item, QQuickItem)]
    if not rows:
        raise AssertionError("ListView created no visible rows")
    for row in rows:
        if row.width() < 0 or row.height() <= 0:
            raise AssertionError("Invalid list row geometry")
        _assert_inside(_item(row, "listRowCover"), row)
        _assert_inside(_item(row, "listRowTitle"), row)
        _assert_inside(_item(row, "listRowPath"), row)
    list_maximum = max(
        0.0,
        float(page_scroll.property("contentHeight"))
        - float(page_scroll.property("height")),
    )
    page_scroll.setProperty("contentY", list_maximum)
    _settle(application, 8)
    last_row = rows[-1]
    last_row_viewport = _rect_in(last_row, page_scroll)
    if last_row_viewport.bottom() > viewport_height + 1.5:
        raise AssertionError("The last list row is not reachable")

    page_scroll.setProperty("contentY", float(page_scroll.property("contentHeight")) * 2)
    root.setProperty("visible", False)
    _settle(application, 3)
    root.setProperty("visible", True)
    _settle(application, 8)
    restored_maximum = max(
        0.0,
        float(page_scroll.property("contentHeight"))
        - float(page_scroll.property("height")),
    )
    restored_offset = float(page_scroll.property("contentY"))
    if restored_offset < 0 or restored_offset > restored_maximum + 1.5:
        raise AssertionError(
            f"Returning to Games kept an invalid offset: {restored_offset} / {restored_maximum}"
        )
    page_scroll.setProperty("contentY", 0)
    _settle(application, 3)
    _wheel_over(application, view, _item(root, "libraryCompressionSummary"))
    return_wheel_offset = float(page_scroll.property("contentY"))
    if return_wheel_offset <= 0:
        raise AssertionError("Wheel events remained blocked after returning to Games")

    root.setProperty("librariesExpanded", True)
    _settle(application, 10)
    expanded_summary_height = float(
        _item(root, "libraryCompressionSummary").property("height")
    )
    if expanded_summary_height <= collapsed_summary_height:
        raise AssertionError("Opening library details did not expand the compact summary")

    summary_scopes = {
        "full": str(_item(root, "fullLibraryScope").property("text")),
        "partial": str(_item(root, "partialLibraryScope").property("text")),
    }
    summary_statuses = {
        "full": str(_item(root, "fullLibraryStatus").property("text")),
        "partial": str(_item(root, "partialLibraryStatus").property("text")),
    }
    full_date = _item(root, "fullLibraryLastMeasurement")
    partial_date = _item(root, "partialLibraryLastMeasurement")
    raw_filesystem_options = root.property("filesystemOptions")
    filesystem_options = (
        raw_filesystem_options.toVariant()
        if hasattr(raw_filesystem_options, "toVariant")
        else raw_filesystem_options
    )
    view.close()
    return {
        "resolution": [width, height],
        "theme": theme_mode,
        "cards": len(cards),
        "rows": len(rows),
        "columns": int(grid.property("columnCount")),
        "contentHeight": content_height,
        "viewportHeight": viewport_height,
        "scrollBarVisible": scroll_bar.isVisible(),
        "lastCardVisibleAtEnd": last_card_viewport.bottom() <= viewport_height + 1.5,
        "bottomMargin": bottom_margin,
        "wheelDeltas": wheel_results,
        "languageContentHeights": language_heights,
        "returnOffsetValid": restored_offset <= restored_maximum + 1.5,
        "returnWheelOffset": return_wheel_offset,
        "counters": {
            "visible": _item(root, "visibleGamesCount").property("text"),
            "available": _item(root, "availableGamesCount").property("text"),
            "cached": _item(root, "cachedUnavailableGamesCount").property("text"),
        },
        "summaryScopes": summary_scopes,
        "summaryStatuses": summary_statuses,
        "fullDateVisible": bool(full_date.property("visible")),
        "fullDateValue": str(full_date.property("value")),
        "partialDateVisible": bool(partial_date.property("visible")),
        "libraryCompressionSummary": {
            "logical": _item(root, "librarySummaryLogical").property("value"),
            "physical": _item(root, "librarySummaryPhysical").property("value"),
            "saving": _item(root, "librarySummarySaving").property("value"),
            "effect": _item(root, "librarySummaryEffect").property("value"),
            "measuredGames": _item(
                root, "librarySummaryMeasuredGames"
            ).property("value"),
        },
        "compactLibrarySummary": {
            "defaultCollapsed": True,
            "collapsedHeight": collapsed_summary_height,
            "expandedHeight": expanded_summary_height,
        },
        "filesystemOptions": list(filesystem_options or []),
    }


def probe_game_popups(application: QGuiApplication) -> dict[str, Any]:
    view, root = _view(application, "pages/GamesPage.qml", 1280, 720)
    root.setProperty("gamesData", [_card_game(index) for index in range(16)])
    _settle(application, 20)
    page_scroll = _item(root, "gamesPageFlickable")
    launcher = _item(root, "launcherFilter")
    filesystem = _item(root, "filesystemFilter")
    QMetaObject.invokeMethod(launcher, "openMenu", Qt.ConnectionType.DirectConnection)
    _settle(application, 8)
    first_opened = bool(launcher.property("menuVisible"))
    overlay_parent = bool(launcher.property("menuUsesWindowOverlay"))
    if not first_opened or not overlay_parent:
        raise AssertionError("The first filter popup was not moved to the window overlay")

    def attached_geometry(combo: QQuickItem) -> dict[str, float | bool]:
        mapped = combo.mapToScene(QPointF(0, combo.height()))
        mapped_x = float(mapped.x())
        mapped_y = float(mapped.y())
        popup_x = float(combo.property("menuX"))
        popup_y = float(combo.property("menuY"))
        popup_width = float(combo.property("menuWidth"))
        attached = abs(popup_x - mapped_x) <= 1.5 and abs(
            popup_y - (mapped_y + 5.0)
        ) <= 1.5
        if not attached:
            raise AssertionError(
                "Popup is not attached below its ComboBox in overlay coordinates: "
                f"mapped=({mapped_x}, {mapped_y}) popup=({popup_x}, {popup_y})"
            )
        if popup_width + 0.5 < combo.width():
            raise AssertionError(
                f"Popup width {popup_width} is below ComboBox width {combo.width()}"
            )
        if popup_x < -0.5 or popup_x + popup_width > view.width() + 0.5:
            raise AssertionError("Popup escaped the horizontal window bounds")
        return {
            "attached": attached,
            "mappedX": mapped_x,
            "mappedY": mapped_y,
            "popupX": popup_x,
            "popupY": popup_y,
            "popupWidth": popup_width,
        }

    initial_geometry = attached_geometry(launcher)

    QMetaObject.invokeMethod(filesystem, "openMenu", Qt.ConnectionType.DirectConnection)
    _settle(application, 8)
    second_opened = bool(filesystem.property("menuVisible"))
    first_closed_by_second = not bool(launcher.property("menuVisible"))
    if not second_opened or not first_closed_by_second:
        raise AssertionError("Opening a second filter did not close the first popup")
    second_geometry = attached_geometry(filesystem)

    view.resize(1100, 680)
    _settle(application, 16)
    resized_geometry = attached_geometry(filesystem)

    original_offset = float(page_scroll.property("contentY"))
    page_scroll.setProperty("contentY", original_offset + 32.0)
    _settle(application, 20)
    scrolled_geometry = attached_geometry(filesystem)

    before_wheel = float(page_scroll.property("contentY"))
    _wheel_over(application, view, filesystem)
    after_wheel = float(page_scroll.property("contentY"))
    if abs(after_wheel - before_wheel) > 0.5:
        raise AssertionError("The Games page moved underneath an open filter popup")

    application.sendEvent(
        view,
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    application.sendEvent(
        view,
        QKeyEvent(
            QKeyEvent.Type.KeyRelease,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    _settle(application, 8)
    escaped_closed = not bool(filesystem.property("menuVisible"))
    focus_returned = bool(filesystem.property("activeFocus"))

    outside_close_policy = bool(launcher.property("closesOnOutside"))

    QMetaObject.invokeMethod(launcher, "openMenu", Qt.ConnectionType.DirectConnection)
    _settle(application, 5)
    root.setProperty("visible", False)
    _settle(application, 8)
    page_change_closed = not bool(launcher.property("menuVisible"))

    result = {
        "firstOpened": first_opened,
        "firstClosedBySecond": first_closed_by_second,
        "secondOpened": second_opened,
        "escapeClosed": escaped_closed,
        "outsideClosePolicy": outside_close_policy,
        "focusReturned": focus_returned,
        "pageChangeClosed": page_change_closed,
        "pageStayedStill": abs(after_wheel - before_wheel) <= 0.5,
        "overlayParent": overlay_parent,
        "popupZ": float(launcher.property("menuZ")),
        "positionedBelow": True,
        "positionAfterResize": bool(resized_geometry["attached"]),
        "positionAfterScroll": bool(scrolled_geometry["attached"]),
        "popupAtOrigin": bool(
            initial_geometry["popupX"] < 2.0
            and initial_geometry["popupY"] < 2.0
        ),
        "initialGeometry": initial_geometry,
        "secondGeometry": second_geometry,
    }
    view.close()
    return result


def probe_artwork_reuse(application: QGuiApplication) -> dict[str, Any]:
    lifecycle_message_start = len(MESSAGES)
    with tempfile.TemporaryDirectory(prefix="game-optimization-artwork-reuse-") as raw_dir:
        directory = Path(raw_dir)
        games: list[dict[str, Any]] = []
        for index in range(36):
            path = directory / f"cover-{index}.png"
            image = QImage(32, 48, QImage.Format.Format_ARGB32)
            image.fill(QColor.fromHsv((index * 31) % 360, 220, 220))
            if not image.save(str(path)):
                raise AssertionError(f"Could not create artwork fixture {path}")
            games.append(
                {
                    "id": f"steam-{1000 + index}",
                    "name": f"Artwork fixture {index}",
                    "effectiveArtworkUrl": QUrl.fromLocalFile(str(path)).toString(
                        QUrl.ComponentFormattingOption.FullyEncoded
                    ),
                    "sequence": index,
                }
            )

        view = QQuickView()
        view.engine().addImportPath(str(QML_ROOT))
        view.engine().rootContext().setContextProperty(
            "gameOptimizationDebugArtwork",
            os.environ.get("GAME_OPTIMIZATION_DEBUG_ARTWORK", "").strip() == "1",
        )
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.resize(720, 420)
        view.setSource((ROOT / "tests/fixtures/ArtworkReuse.qml").as_uri())
        if view.status() != QQuickView.Status.Ready:
            raise AssertionError("Artwork reuse fixture did not load")
        root = view.rootObject()
        if not isinstance(root, QQuickItem):
            raise AssertionError("Artwork reuse fixture has no root item")
        view.show()
        root.setProperty("gamesData", games)
        _settle(application, 50)
        grid = _item(root, "artworkReuseGrid")

        expected = {game["id"]: game["effectiveArtworkUrl"] for game in games}

        def visible_assignments() -> dict[str, str]:
            assignments: dict[str, str] = {}
            for item in _named(root, "reusedGameArtwork"):
                if not isinstance(item, QQuickItem) or not item.isVisible():
                    continue
                game_id = str(item.property("gameId") or "")
                raw_source = item.property("artworkSource")
                source = (
                    raw_source.toString()
                    if isinstance(raw_source, QUrl)
                    else str(raw_source or "")
                )
                if not game_id:
                    continue
                if expected.get(game_id) != source:
                    raise AssertionError(
                        f"Artwork source crossed game identity: {game_id} -> {source}"
                    )
                if not bool(item.property("artworkReady")):
                    raise AssertionError(
                        f"Visible artwork did not reach Image.Ready: {game_id}/{source}"
                    )
                displayed_game = str(item.property("displayedGameId") or "")
                displayed_source = str(item.property("displayedArtworkSource") or "")
                if displayed_game != game_id or displayed_source != source:
                    raise AssertionError(
                        "A late Image result was accepted by a reused delegate: "
                        f"bound={game_id}/{source} displayed="
                        f"{displayed_game}/{displayed_source}"
                    )
                assignments[game_id] = source
            return assignments

        initial = visible_assignments()
        first_id = games[0]["id"]
        first_source = games[0]["effectiveArtworkUrl"]
        if initial.get(first_id) != first_source:
            raise AssertionError("The first game did not receive its own artwork")

        maximum = max(0.0, float(grid.property("contentHeight")) - grid.height())
        for _ in range(3):
            grid.setProperty("contentY", maximum)
            _settle(application, 35)
            visible_assignments()
            grid.setProperty("contentY", 0.0)
            _settle(application, 35)
            restored = visible_assignments()
            if restored.get(first_id) != first_source:
                raise AssertionError("Scrolling lost the first game's artwork source")

        root.setProperty("filterParity", 1)
        grid.setProperty("contentY", 0.0)
        _settle(application, 40)
        odd = visible_assignments()
        if odd and any((int(game_id.removeprefix("steam-")) - 1000) % 2 == 0 for game_id in odd):
            raise AssertionError("The filtered GridView retained an even delegate")

        root.setProperty("filterParity", -1)
        grid.setProperty("contentY", 0.0)
        _settle(application, 40)
        restored_after_filter = visible_assignments()
        if restored_after_filter.get(first_id) != first_source:
            raise AssertionError("Restoring the filter changed the first artwork source")

        result = {
            "reuseItems": bool(grid.property("reuseItems")),
            "scrollCycles": 3,
            "firstGameId": first_id,
            "firstSourceStable": restored_after_filter.get(first_id) == first_source,
            "filterRestoreStable": True,
            "visibleAfterRestore": len(restored_after_filter),
            "onPooledLogged": any(
                "delegateState=onPooled" in message
                for message in MESSAGES[lifecycle_message_start:]
            ),
            "onReusedLogged": any(
                "delegateState=onReused" in message
                for message in MESSAGES[lifecycle_message_start:]
            ),
        }
        view.close()
        return result


def probe_artwork_refresh(application: QGuiApplication) -> dict[str, Any]:
    """Exercise the production artwork URL through refresh, scroll and filtering."""
    lifecycle_message_start = len(MESSAGES)
    with tempfile.TemporaryDirectory(prefix="game-optimization-artwork-refresh-") as raw_dir:
        directory = Path(raw_dir) / "Steam artwork with spaces"
        directory.mkdir()
        app_ids = ("242550", "204360", *(str(300000 + index) for index in range(28)))
        games: list[dict[str, Any]] = []
        local_paths: dict[str, str] = {}
        expected_urls: dict[str, str] = {}
        expected_colors: dict[str, QColor] = {}
        for index, app_id in enumerate(app_ids):
            path = directory / f"{app_id}_library_600x900.png"
            image = QImage(48, 72, QImage.Format.Format_ARGB32)
            artwork_color = QColor.fromHsv((index * 43) % 360, 210, 225)
            image.fill(artwork_color)
            if not image.save(str(path)):
                raise AssertionError(f"Could not create artwork fixture {path}")
            game = Game(
                id=f"steam-{app_id}",
                steam_app_id=app_id,
                name="Rayman Legends" if app_id == "242550"
                else "Castle Crashers" if app_id == "204360"
                else f"Refresh fixture {index}",
                launcher=Launcher.STEAM,
                install_path=directory / "games" / app_id,
                library_path=directory,
                logical_size_gb=1.0,
                physical_size_gb=1.0,
                filesystem=FilesystemType.EXT4,
                compression_available=False,
                portrait_artwork_path=path,
            )
            presented = game_to_qml(game)
            url = str(presented["effectiveArtworkUrl"])
            game_id = f"steam-{app_id}"
            local_paths[game_id] = str(presented["portraitArtworkPath"])
            expected_urls[game_id] = url
            expected_colors[game_id] = artwork_color
            games.append(
                {
                    "id": game_id,
                    "appId": app_id,
                    "name": presented["name"],
                    "effectiveArtworkUrl": url,
                    "sequence": index,
                }
            )

        view = QQuickView()
        view.engine().addImportPath(str(QML_ROOT))
        view.engine().rootContext().setContextProperty(
            "gameOptimizationDebugArtwork",
            os.environ.get("GAME_OPTIMIZATION_DEBUG_ARTWORK", "").strip() == "1",
        )
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.resize(720, 420)
        view.setSource((ROOT / "tests/fixtures/ArtworkRefresh.qml").as_uri())
        if view.status() != QQuickView.Status.Ready:
            raise AssertionError("Artwork refresh fixture did not load")
        root = view.rootObject()
        if not isinstance(root, QQuickItem):
            raise AssertionError("Artwork refresh fixture has no root item")
        view.show()
        flick = _item(root, "artworkRefreshFlick")
        watched_ids = ("steam-242550", "steam-204360")

        def encoded_url(value: object) -> str:
            url = value if isinstance(value, QUrl) else QUrl(str(value or ""))
            return url.toString(QUrl.ComponentFormattingOption.FullyEncoded)

        def ready_details() -> dict[str, dict[str, str]]:
            details: dict[str, dict[str, str]] = {}
            for item in _named(root, "refreshGameArtwork"):
                game_id = str(item.property("gameId") or "")
                if game_id not in watched_ids:
                    continue
                bound_source = item.property("artworkSource")
                bound_url = encoded_url(bound_source)
                expected = expected_urls[game_id]
                if bound_url != expected or not bound_url.startswith("file:///"):
                    raise AssertionError(
                        f"Invalid QML artwork URL for {game_id}: {bound_url}"
                    )
                if not bool(item.property("artworkReady")):
                    raise AssertionError(f"{game_id} did not reach Image.Ready")
                displayed = encoded_url(item.property("displayedArtworkSource"))
                if displayed != expected:
                    raise AssertionError(
                        f"Displayed source differs for {game_id}: {displayed}"
                    )
                actual_source = encoded_url(item.property("actualImageSource"))
                actual_status = str(item.property("actualImageStatus") or "")
                if actual_source != expected or actual_status != "Ready":
                    raise AssertionError(
                        "No actual ready QML Image for "
                        f"{game_id}/{expected}: {actual_source}/{actual_status}"
                    )
                details[game_id] = {
                    "effectiveArtworkUrl": bound_url,
                    "qmlImageSource": actual_source,
                    "qmlImageStatus": actual_status,
                }
            if set(details) != set(watched_ids):
                raise AssertionError(
                    f"Missing watched artwork delegates: {sorted(details)}"
                )
            return details

        def watched_artwork(game_id: str) -> QObject:
            for item in _named(root, "refreshGameArtwork"):
                if str(item.property("gameId") or "") == game_id:
                    return item
            raise AssertionError(f"Missing artwork delegate for {game_id}")

        stages: dict[str, dict[str, dict[str, str]]] = {}
        root.setProperty("gamesData", games)
        _settle(application, 60)
        stages["loaded"] = ready_details()
        initial_artwork = watched_artwork("steam-242550")
        grabbed = view.grabWindow()
        scene_center = initial_artwork.mapToScene(
            QPointF(initial_artwork.width() / 2, initial_artwork.height() / 2)
        )
        scale_x = grabbed.width() / max(1.0, float(view.width()))
        scale_y = grabbed.height() / max(1.0, float(view.height()))
        sampled = grabbed.pixelColor(
            round(scene_center.x() * scale_x),
            round(scene_center.y() * scale_y),
        )
        expected_sample = expected_colors["steam-242550"]
        artwork_pixels_visible = all(
            abs(actual - expected) <= 12
            for actual, expected in (
                (sampled.red(), expected_sample.red()),
                (sampled.green(), expected_sample.green()),
                (sampled.blue(), expected_sample.blue()),
            )
        )
        if not artwork_pixels_visible:
            raise AssertionError(
                "Image.Ready did not produce visible artwork pixels: "
                f"sampled={sampled.name(QColor.NameFormat.HexArgb)} "
                f"expected={expected_sample.name(QColor.NameFormat.HexArgb)}"
            )

        root.setProperty("gamesData", [dict(game) for game in games])
        _settle(application, 60)
        stages["modelRefreshed"] = ready_details()

        maximum = max(0.0, float(flick.property("contentHeight")) - flick.height())
        flick.setProperty("contentY", maximum)
        _settle(application, 30)
        stages["scrolledDown"] = ready_details()
        flick.setProperty("contentY", 0.0)
        _settle(application, 30)
        stages["returnedTop"] = ready_details()

        root.setProperty("filtered", True)
        _settle(application, 45)
        stages["filterChanged"] = ready_details()
        root.setProperty("filtered", False)
        _settle(application, 45)
        stages["filterRestored"] = ready_details()

        root.setProperty("listMode", True)
        _settle(application, 60)
        stages["listMode"] = ready_details()
        root.setProperty("listMode", False)
        _settle(application, 60)
        flick = _item(root, "artworkRefreshFlick")
        stages["gridModeRestored"] = ready_details()

        root.setProperty("visible", False)
        _settle(application, 20)
        root.setProperty("visible", True)
        _settle(application, 40)
        stages["gamesUpdatesGames"] = ready_details()

        transient = watched_artwork("steam-242550")
        before_transient = {
            "currentReady": transient.property("currentReady"),
            "pendingReady": transient.property("pendingReady"),
            "committed": transient.property("committedArtworkSource"),
            "pending": transient.property("pendingArtworkSource"),
            "pendingAccepted": transient.property("pendingAccepted"),
        }
        transient.setProperty("artworkSource", "")
        _settle(application, 10)
        if (
            not bool(transient.property("artworkReady"))
            or bool(transient.property("showPlaceholder"))
        ):
            raise AssertionError(
                "A transient empty source replaced an already loaded artwork: "
                f"ready={transient.property('artworkReady')} "
                f"placeholder={transient.property('showPlaceholder')} "
                f"currentReady={transient.property('currentReady')} "
                f"pendingReady={transient.property('pendingReady')} "
                f"committed={transient.property('committedArtworkSource')} "
                f"pending={transient.property('pendingArtworkSource')} "
                f"before={before_transient}"
            )
        transient.setProperty(
            "artworkSource", expected_urls["steam-242550"]
        )
        _settle(application, 25)
        stages["transientSourceGap"] = ready_details()

        # A failed replacement for the same game must leave the already-ready
        # current image visible and must not expose the placeholder.
        missing_url = QUrl.fromLocalFile(
            str(directory / "missing-replacement.png")
        ).toString(QUrl.ComponentFormattingOption.FullyEncoded)
        transient.setProperty("artworkSource", missing_url)
        _settle(application, 35)
        failed_replacement_preserved = bool(
            transient.property("currentReady")
        ) and not bool(transient.property("showPlaceholder"))
        if not failed_replacement_preserved:
            raise AssertionError(
                "A failed candidate removed the valid current artwork: "
                f"currentReady={transient.property('currentReady')} "
                f"placeholder={transient.property('showPlaceholder')} "
                f"displayed={transient.property('displayedArtworkSource')}"
            )
        transient.setProperty("artworkSource", expected_urls["steam-242550"])
        _settle(application, 25)
        stages["failedReplacementRecovered"] = ready_details()

        # A controller refresh replaces the QVariant snapshot with fresh maps.
        root.setProperty("gamesData", [dict(game) for game in games])
        _settle(application, 60)
        stages["fullRefresh"] = ready_details()

        rayman = stages["fullRefresh"]["steam-242550"]
        stale_target = watched_artwork("steam-242550")
        stale_request_id = int(stale_target.property("requestId"))
        stale_game_id = str(stale_target.property("gameId"))
        stale_source = encoded_url(stale_target.property("artworkSource"))
        stale_target.setProperty("gameId", "steam-204360")
        stale_target.setProperty(
            "artworkSource", expected_urls["steam-204360"]
        )
        _settle(application, 8)
        replacement_request_id = int(stale_target.property("requestId"))
        _invoke_qml(
            stale_target,
            "acceptPendingImage",
            stale_request_id,
            stale_game_id,
            stale_source,
        )
        _settle(application, 35)
        # Repeat after the replacement has completed to cover a callback that
        # arrives much later than the original asynchronous request.
        _invoke_qml(
            stale_target,
            "acceptPendingImage",
            stale_request_id,
            stale_game_id,
            stale_source,
        )
        _settle(application, 8)
        stale_callback_ignored = bool(
            replacement_request_id > stale_request_id
            and str(stale_target.property("displayedGameId")) == "steam-204360"
            and encoded_url(stale_target.property("displayedArtworkSource"))
            == expected_urls["steam-204360"]
            and encoded_url(stale_target.property("actualImageSource"))
            == expected_urls["steam-204360"]
            and str(stale_target.property("actualImageStatus")) == "Ready"
        )
        if not stale_callback_ignored:
            raise AssertionError(
                "A stale request token changed the replacement artwork: "
                f"old={stale_request_id}/{stale_game_id}/{stale_source} "
                f"new={replacement_request_id}/"
                f"{stale_target.property('displayedGameId')}/"
                f"{stale_target.property('displayedArtworkSource')}/"
                f"{stale_target.property('actualImageStatus')}"
            )
        view.close()
        _settle(application, 12)

        restart_view = QQuickView()
        restart_view.engine().addImportPath(str(QML_ROOT))
        restart_view.engine().rootContext().setContextProperty(
            "gameOptimizationDebugArtwork",
            os.environ.get("GAME_OPTIMIZATION_DEBUG_ARTWORK", "").strip() == "1",
        )
        restart_view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        restart_view.resize(720, 420)
        restart_view.setSource((ROOT / "tests/fixtures/ArtworkRefresh.qml").as_uri())
        if restart_view.status() != QQuickView.Status.Ready:
            raise AssertionError("Restarted artwork fixture did not load")
        restarted_root = restart_view.rootObject()
        if not isinstance(restarted_root, QQuickItem):
            raise AssertionError("Restarted artwork fixture has no root item")
        root = restarted_root
        restart_view.show()
        root.setProperty("gamesData", [dict(game) for game in games])
        _settle(application, 60)
        stages["restart"] = ready_details()

        lifecycle_messages = [
            message
            for message in MESSAGES[lifecycle_message_start:]
            if "Game Optimization GameArtwork lifecycle" in message
            and (
                "gameId=steam-242550 " in message
                or "gameId=steam-204360 " in message
            )
        ]
        if not lifecycle_messages:
            raise AssertionError("No GameArtwork lifecycle diagnostics were emitted")
        if not all(
            "effectiveArtworkUrl=file:///" in message
            for message in lifecycle_messages
            if "delegateState=destroyed" not in message
        ):
            raise AssertionError(
                "A live delegate diagnostic lost effectiveArtworkUrl: "
                + " | ".join(lifecycle_messages)
            )
        result = {
            "stages": stages,
            "lifecycleDiagnostics": {
                "events": len(lifecycle_messages),
                "hasVisible": all(" visible=" in message for message in lifecycle_messages),
                "hasGridCurrent": all(
                    " GridView.isCurrentItem=" in message
                    for message in lifecycle_messages
                ),
                "hasImageSource": all(
                    " Image.source=" in message for message in lifecycle_messages
                ),
                "hasImageStatus": all(
                    " Image.status=" in message for message in lifecycle_messages
                ),
                "sawHidden": any(
                    " visible=false" in message for message in lifecycle_messages
                ),
                "sawGrid": any(
                    " view=grid " in message for message in lifecycle_messages
                ),
                "sawList": any(
                    " view=list " in message for message in lifecycle_messages
                ),
                "committedBeforeTransientGap": bool(
                    before_transient["currentReady"]
                ),
                "transientGapProtected": True,
                "failedReplacementPreserved": failed_replacement_preserved,
                "staleCallbackIgnored": stale_callback_ignored,
                "artworkPixelsVisible": artwork_pixels_visible,
            },
            "steam242550": {
                "pythonLocalPath": local_paths["steam-242550"],
                "pythonQUrl": expected_urls["steam-242550"],
                "qmlImageSource": rayman["qmlImageSource"],
                "qmlImageStatus": rayman["qmlImageStatus"],
            },
        }
        restart_view.close()
        return result


def probe_incremental_games_model(application: QGuiApplication) -> dict[str, Any]:
    """Prove unchanged and one-row updates retain existing QML delegates."""
    from game_optimization_linux.controllers.games_model import GamesListModel

    lifecycle_start = len(MESSAGES)
    with tempfile.TemporaryDirectory(prefix="game-optimization-incremental-model-") as raw_dir:
        directory = Path(raw_dir)

        def row(index: int) -> dict[str, Any]:
            path = directory / f"cover-{index}.png"
            if not path.exists():
                image = QImage(32, 48, QImage.Format.Format_ARGB32)
                image.fill(QColor.fromHsv((index * 47) % 360, 210, 220))
                if not image.save(str(path)):
                    raise AssertionError(f"Could not create {path}")
            return {
                "id": f"steam-{1000 + index}",
                "name": f"Incremental fixture {index}",
                "path": str(directory / "games" / str(index)),
                "launcher": "Steam",
                "filesystem": "btrfs",
                "status": "Ready",
                "effectiveArtworkUrl": QUrl.fromLocalFile(str(path)).toString(
                    QUrl.ComponentFormattingOption.FullyEncoded
                ),
            }

        rows = [row(index) for index in range(10)]
        model = GamesListModel(application)
        initial = model.apply_snapshot(rows, reason="initial")
        data_changed_rows: list[tuple[int, int]] = []
        model.dataChanged.connect(
            lambda top, bottom, _roles: data_changed_rows.append(
                (top.row(), bottom.row())
            )
        )
        resets: list[bool] = []
        model.modelReset.connect(lambda: resets.append(True))

        view = QQuickView()
        view.engine().addImportPath(str(QML_ROOT))
        view.engine().rootContext().setContextProperty(
            "gameOptimizationDebugArtwork",
            os.environ.get("GAME_OPTIMIZATION_DEBUG_ARTWORK", "").strip() == "1",
        )
        view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        view.resize(720, 420)
        view.setSource((ROOT / "tests/fixtures/IncrementalGames.qml").as_uri())
        if view.status() != QQuickView.Status.Ready:
            raise AssertionError("Incremental Games fixture did not load")
        root = view.rootObject()
        if not isinstance(root, QQuickItem):
            raise AssertionError("Incremental Games fixture has no root")
        view.show()
        root.setProperty("gamesModel", model)
        _settle(application, 60)

        def serials() -> dict[str, int]:
            result: dict[str, int] = {}
            for delegate in _named(root, "incrementalGameDelegate"):
                data = delegate.property("modelData")
                if hasattr(data, "toVariant"):
                    data = data.toVariant()
                game_id = str(data.get("id") if isinstance(data, dict) else "")
                if game_id:
                    result[game_id] = int(delegate.property("creationSerial"))
            return result

        initial_serials = serials()
        if len(initial_serials) != 10:
            raise AssertionError(f"Missing initial delegates: {initial_serials}")
        initial_requests = sum(
            "event=candidate_source_changed" in message
            and "view=incremental-model-probe" in message
            for message in MESSAGES[lifecycle_start:]
        )

        identical_results = [
            model.apply_snapshot([dict(item) for item in rows], reason=f"identical-{n}")
            for n in range(10)
        ]
        _settle(application, 20)
        after_identical_serials = serials()
        requests_after_identical = sum(
            "event=candidate_source_changed" in message
            and "view=incremental-model-probe" in message
            for message in MESSAGES[lifecycle_start:]
        )

        changed_rows = [dict(item) for item in rows]
        changed_rows[4]["status"] = "Needs attention"
        one_changed = model.apply_snapshot(changed_rows, reason="single-status")
        _settle(application, 20)
        after_change_serials = serials()

        added_rows = [*changed_rows, row(10)]
        one_added = model.apply_snapshot(added_rows, reason="manifest-added")
        _settle(application, 40)
        after_add_serials = serials()
        existing_after_add = {
            game_id: serial
            for game_id, serial in after_add_serials.items()
            if game_id in initial_serials
        }
        destroyed_before_close = sum(
            "event=component_destroyed" in message
            and "view=incremental-model-probe" in message
            for message in MESSAGES[lifecycle_start:]
        )

        result = {
            "initial": initial,
            "identicalChanged": [entry["changed"] for entry in identical_results],
            "serialsStableAfterIdentical": initial_serials == after_identical_serials,
            "imageRequestsInitial": initial_requests,
            "imageRequestsAfterIdentical": requests_after_identical,
            "singleChange": one_changed,
            "dataChangedRows": data_changed_rows,
            "serialsStableAfterChange": initial_serials == after_change_serials,
            "singleAdd": one_added,
            "existingSerialsStableAfterAdd": initial_serials == existing_after_add,
            "destroyedBeforeClose": destroyed_before_close,
            "modelResetCount": model.modelResetCount,
            "modelResetSignals": len(resets),
        }
        view.close()
        return result


def probe_breeze(application: QGuiApplication) -> dict[str, Any]:
    view, root = _view(application, "pages/details/OptimizationTab.qml", 1000, 800)
    root.setProperty(
        "gameData",
        {"id": "steam-test", "name": "Test Game", "optimizationProfile": "Balanced"},
    )
    _settle(application, 20)
    preview = _item(root, "launchPreviewText")
    if not bool(preview.property("readOnly")):
        raise AssertionError("Launch preview is not read-only")
    view.close()
    return {"preview": str(preview.property("text"))}


def probe_signal_shutdown(application: QGuiApplication) -> dict[str, Any]:
    from threading import Event

    from game_optimization_linux.app import _install_termination_handlers, _restore_termination_handlers
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.models import FilesystemType, Game, Launcher
    from game_optimization_linux.services import AnalysisCancelled, BtrfsAnalysisTaskService, SettingsStore

    temporary_root = Path(tempfile.mkdtemp(prefix="game-optimization-signal-probe-"))
    game_path = temporary_root / "game"
    game_path.mkdir()
    game = Game(
        id="steam-signal",
        name="Signal Test",
        launcher=Launcher.STEAM,
        install_path=game_path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
    )
    started = Event()
    cancelled = Event()

    class Analyzer:
        def analyze(self, _game: Game, *, cancel_event: Event, progress_callback: object) -> object:
            del progress_callback
            started.set()
            if cancel_event.wait(3.0):
                cancelled.set()
                raise AnalysisCancelled("signal fixture cancelled")
            raise RuntimeError("signal fixture was not cancelled")

    class Provider:
        def list_games(self) -> tuple[Game, ...]:
            return (game,)

        def get_game(self, game_id: str) -> Game | None:
            return game if game_id == game.id else None

        def refresh(self) -> tuple[Game, ...]:
            return (game,)

    service = BtrfsAnalysisTaskService(analyzer=Analyzer())  # type: ignore[arg-type]
    controller = AppController(
        parent=application,
        game_provider=Provider(),
        task_service=service,
        settings_store=SettingsStore(temporary_root / "settings.json"),
        initial_games=(game,),
        demo_mode=False,
        auto_refresh=False,
    )
    # The offscreen platform cannot receive a host SDL device.  Keep the
    # production reconnect service untouched and expose the actual page focus
    # for this isolated navigation probe.
    controller.couchNavigation.setControllerConnected(True)
    requested, previous = _install_termination_handlers(application)
    application.aboutToQuit.connect(controller.shutdown)
    if not controller.analyzeGame(game.id):
        raise AssertionError("Could not start signal analysis fixture")

    def send_interrupt() -> None:
        if not started.wait(1.0):
            raise AssertionError("Analysis fixture did not start")
        os.kill(os.getpid(), signal.SIGINT)

    QTimer.singleShot(30, send_interrupt)
    started_at = time.monotonic()
    exit_code = application.exec()
    elapsed = time.monotonic() - started_at
    controller.shutdown()
    _restore_termination_handlers(previous)
    tasks = service.list_tasks()
    return {
        "qt_exit_code": exit_code,
        "requested_signal": requested[0],
        "cancelled": cancelled.is_set(),
        "task_status": tasks[0].status.value,
        "timer_active": controller._task_timer.isActive(),
        "elapsed": elapsed,
    }


def probe_close_during_compression(
    application: QGuiApplication,
) -> dict[str, Any]:
    from dataclasses import replace

    from game_optimization_linux.app import _prepare_qml_shutdown
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.models import Task, TaskStatus, TaskType
    from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
    from game_optimization_linux.services import GamepadService, SettingsStore

    temporary_root = Path(tempfile.mkdtemp(prefix="game-optimization-close-probe-"))

    class ActiveCompressionTasks:
        def __init__(self) -> None:
            self.task = Task(
                id="compression-close-probe",
                game_id="batman-arkham-knight",
                game_name="Batman: Arkham Knight",
                task_type=TaskType.COMPRESSION,
                title="Compress Batman: Arkham Knight",
                status=TaskStatus.RUNNING,
                progress=12.0,
                metadata={"cancellable": True, "read_only": False},
            )
            self.cancel_calls = 0
            self.shutdown_calls = 0

        def list_tasks(self) -> tuple[Task, ...]:
            return (self.task,)

        def tick(self, step: float = 1.0) -> tuple[Task, ...]:
            del step
            return self.list_tasks()

        def cancel(self, task_id: str) -> Task:
            if task_id != self.task.id:
                raise KeyError(task_id)
            self.cancel_calls += 1
            self.task = replace(self.task, status=TaskStatus.CANCELLED)
            return self.task

        def shutdown(self, **_kwargs: Any) -> None:
            self.shutdown_calls += 1

    tasks = ActiveCompressionTasks()
    controller = AppController(
        parent=application,
        game_provider=DemoGameProvider(),
        task_service=tasks,  # type: ignore[arg-type]
        settings_store=SettingsStore(temporary_root / "settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider(available=False)),
        demo_mode=True,
        auto_refresh=False,
    )
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_ROOT))
    context = engine.rootContext()
    context.setContextProperty("appController", controller)
    context.setContextProperty("translationManager", QObject())
    engine.load(QUrl.fromLocalFile(str(QML_ROOT / "Main.qml")))
    if not engine.rootObjects():
        raise AssertionError("Main.qml did not create a window")
    window = engine.rootObjects()[0]
    _settle(application, 20)
    if controller.hasActiveCompressionTasks is not True:
        raise AssertionError("Compression fixture was not reported as active")

    window.close()
    _settle(application, 12)
    dialogs = _named(window, "closeCompressionDialog")
    if not dialogs:
        raise AssertionError("Close warning dialog was not created")
    dialog = dialogs[0]
    warning_visible = bool(
        dialog.property("opened") or dialog.property("visible")
    )
    if not warning_visible or not bool(window.property("visible")):
        raise AssertionError("Closing did not keep the window open for confirmation")

    dialog.confirmed.emit(None)
    _settle(application, 20)
    _prepare_qml_shutdown(engine)
    _settle(application, 4)
    loader_states = {
        name: bool(_named(window, name)[0].property("active"))
        for name in (
            "gamesPageLoader",
            "updatesPageLoader",
            "tasksPageLoader",
            "systemPageLoader",
            "settingsPageLoader",
            "gameDetailsPageLoader",
            "couchModeLoader",
        )
        if _named(window, name)
    }
    result = {
        "warning_visible": warning_visible,
        "cancel_calls": tasks.cancel_calls,
        "window_visible_after_confirmation": bool(window.property("visible")),
        "active_after_confirmation": controller.hasActiveCompressionTasks,
        "loaders_active_after_shutdown": loader_states,
    }
    controller.shutdown()
    return result


def probe_tasks_lifecycle(application: QGuiApplication) -> dict[str, Any]:
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
    from game_optimization_linux.services import GamepadService, SettingsStore

    temporary_root = Path(tempfile.mkdtemp(prefix="game-optimization-tasks-probe-"))
    controller = AppController(
        parent=application,
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(temporary_root / "settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider(available=False)),
        auto_refresh=False,
    )
    view = QQuickView()
    view.engine().addImportPath(str(QML_ROOT))
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(1120, 760)
    view.setSource((ROOT / "tests" / "fixtures" / "TasksLifecycle.qml").as_uri())
    if view.status() == QQuickView.Status.Error:
        raise AssertionError("; ".join(error.toString() for error in view.errors()))
    root = view.rootObject()
    if not isinstance(root, QQuickItem):
        raise AssertionError("Tasks lifecycle fixture has no root item")
    view.show()
    root.setProperty("controller", controller)
    controller.openGame("batman-arkham-knight")
    controller.requestCompression("batman-arkham-knight", "Balanced")
    for index in range(32):
        # Games -> Storage -> Tasks -> Games, repeated while the task model changes.
        root.setProperty("currentIndex", (0, 1, 2, 0)[index % 4])
        _settle(application, 2)
    root.setProperty("currentIndex", 2)
    _settle(application, 30)
    tasks_page = _item(root, "lifecycleTasksPage")
    task_list = _item(tasks_page, "taskList")
    task_cards = [
        item
        for item in _named(tasks_page, "taskCard")
        if isinstance(item, QQuickItem) and item.isVisible()
    ]
    if not task_cards:
        raise AssertionError("Tasks page did not create a task card")
    for card in task_cards:
        cover = _item(card, "taskCardCover")
        _assert_inside(cover, card)
        if cover.width() <= 0 or cover.height() <= 0:
            raise AssertionError("Task artwork has invalid geometry")
    result = {
        "tasks": len(controller.tasks),
        "task_list_visible": task_list.isVisible(),
        "task_covers_inside": len(task_cards),
        "page": controller.currentPage,
    }
    controller.shutdown()
    view.close()
    return result


def probe_mangohud_editor(application: QGuiApplication) -> dict[str, Any]:
    """Exercise the rendered Desktop editor against an isolated real controller."""

    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
    from game_optimization_linux.providers.demo import demo_games
    from game_optimization_linux.services import (
        GamepadService,
        MangoHudDetector,
        MangoHudLaunchIntegration,
        MangoHudProfileRepository,
        SettingsStore,
    )

    temporary_root = Path(tempfile.mkdtemp(prefix="game-optimization-mangohud-qml-"))
    install_path = temporary_root / "SteamLibrary" / "steamapps" / "common" / "Spelunky"
    install_path.mkdir(parents=True)
    (install_path / "Spelunky.exe").write_bytes(b"MZ")
    game = replace(
        demo_games()[1],
        name="Spelunky",
        install_path=install_path,
        library_path=temporary_root / "SteamLibrary",
        steam_app_id="239140",
    )
    repository = MangoHudProfileRepository(
        temporary_root / "config" / "games",
        log_root=temporary_root / "state" / "mangohud-logs",
    )
    fake_layer = temporary_root / "MangoHud.x86_64.json"
    fake_layer.write_text("{}\n", encoding="utf-8")
    detector = MangoHudDetector(
        which=lambda name: "/app/bin/mangohud-probe" if name == "mangohud" else None,
        command_runner=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "MangoHud 0.8.0\n", ""
        ),
        layer_paths=(fake_layer,),
    )
    controller = AppController(
        parent=application,
        game_provider=DemoGameProvider((game,)),
        settings_store=SettingsStore(temporary_root / "settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider(available=False)),
        mangohud_repository=repository,
        mangohud_detector=detector,
        mangohud_launch_integration=MangoHudLaunchIntegration(
            repository,
            detector,
            flatpak_config_root=temporary_root / "flatpak-config",
            application_config_root=temporary_root / "MangoHud",
            native_steam_environment=lambda: {"MANGOHUD": "1"},
        ),
        auto_refresh=False,
    )
    view, root = _view(application, "pages/details/MangoHudTab.qml", 1600, 900)
    root.setProperty("controller", controller)
    root.setProperty("gameData", controller.games[0])
    _settle(application, 20)
    if root.property("profileLoaded") is not True:
        raise AssertionError(
            f"Desktop MangoHud profile did not load: {root.property('errorMessage')!r}"
        )
    if root.property("available") is not True:
        raise AssertionError(
            f"Desktop MangoHud detector is unavailable: "
            f"{root.property('availabilityMessage')!r}"
        )
    _invoke_qml(root, "choosePreset", "basic")
    _settle(application, 8)
    if str(root.property("preset")) != "basic" or root.property("dirty") is not True:
        raise AssertionError("Desktop preset selection did not update the rendered editor")
    _invoke_qml(root, "saveProfile")
    _settle(application, 12)
    saved = repository.load("239140")
    if saved.preset != "basic" or not saved.enabled:
        raise AssertionError(f"Desktop editor did not persist its AppID profile: {saved!r}")
    if saved.executable_path != "Spelunky.exe":
        raise AssertionError(f"Desktop editor did not persist the executable: {saved!r}")
    if str(root.property("activationStrategy")) != "per_application_config":
        raise AssertionError(
            f"Desktop editor selected the wrong activation strategy: "
            f"{root.property('activationStrategy')!r}"
        )
    application_config = temporary_root / "MangoHud" / "wine-Spelunky.conf"
    if not application_config.is_file():
        raise AssertionError("Desktop editor did not create the application profile")
    root.setProperty("categoryIndex", 5)
    _settle(application, 10)
    save_button = _item(root, "saveMangoHudProfileButton")
    _assert_inside(save_button, root)
    screenshot = temporary_root / "mangohud-desktop.png"
    view.grabWindow().save(str(screenshot))
    result = {
        "app_id": saved.app_id,
        "preset": saved.preset,
        "enabled": saved.enabled,
        "profile_loaded": bool(root.property("profileLoaded")),
        "available": bool(root.property("available")),
        "config_path": str(repository.config_path(saved.app_id)),
        "config_has_fps": "fps" in repository.config_path(saved.app_id).read_text(
            encoding="utf-8"
        ).splitlines(),
        "selected_executable": saved.executable_path,
        "activation_strategy": str(root.property("activationStrategy")),
        "application_config": str(application_config),
        "application_config_managed": application_config.read_text(
            encoding="utf-8"
        ).startswith("# Managed by Game Optimization Linux\n# Steam AppID: 239140\n"),
        "save_button_inside": True,
        "screenshot_size": screenshot.stat().st_size,
    }
    view.close()
    controller.shutdown()
    return result


def probe_optimization_editor(application: QGuiApplication) -> dict[str, Any]:
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
    from game_optimization_linux.providers.demo import demo_games
    from game_optimization_linux.services import (
        GameOptimizationProfileRepository,
        GamepadService,
        OptiScalerProfileRepository,
        OptiScalerReleaseClient,
        OptiScalerService,
        ProtonTweaksRepository,
        RunnerIntegration,
        RuntimeToolAvailability,
        SettingsStore,
    )

    temporary_root = Path(tempfile.mkdtemp(prefix="game-optimization-optimization-qml-"))
    game_root = temporary_root / "game"
    executable = game_root / "Binaries" / "Win64" / "Probe-Win64-Shipping.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"executable")
    (game_root / "Engine/Build").mkdir(parents=True)
    (game_root / "Engine/Build/Build.version").write_text(
        '{"MajorVersion": 5, "MinorVersion": 4}', encoding="utf-8"
    )
    (game_root / "Probe/Content/Paks").mkdir(parents=True)
    (game_root / "Probe/Content/Paks/probe.pak").write_bytes(b"pak")
    (game_root / "Config").mkdir()
    (game_root / "Config/DefaultEngine.ini").write_text(
        "DefaultGraphicsRHI=DefaultGraphicsRHI_DX12\n", encoding="utf-8"
    )
    archive = temporary_root / "OptiScaler_v0.7.7.7z"
    import py7zr
    with py7zr.SevenZipFile(archive, "w") as handle:
        handle.writestr(b"proxy", "release/OptiScaler.dll")
        handle.writestr(b"[OptiScaler]\n", "release/OptiScaler.ini")
    from hashlib import sha256
    from io import BytesIO

    online_archive = archive.read_bytes()
    online_url = (
        "https://github.com/optiscaler/OptiScaler/releases/download/"
        "v0.7.7/OptiScaler_v0.7.7.7z"
    )
    metadata = json.dumps([
        {
            "tag_name": "v0.7.7",
            "html_url": "https://github.com/optiscaler/OptiScaler/releases/tag/v0.7.7",
            "published_at": "2026-01-01T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "OptiScaler_v0.7.7.7z",
                "browser_download_url": online_url,
                "size": len(online_archive),
                "digest": "sha256:" + sha256(online_archive).hexdigest(),
            }],
        }
    ]).encode()

    class OnlineResponse(BytesIO):
        status = 200
        def __init__(self, payload: bytes, url: str) -> None:
            super().__init__(payload)
            self._url = url
        def geturl(self) -> str:
            return self._url

    def online_open(request, **_kwargs):
        if request.full_url.endswith("/releases"):
            return OnlineResponse(metadata, request.full_url)
        return OnlineResponse(
            online_archive,
            "https://release-assets.githubusercontent.com/optiscaler-probe",
        )

    online_client = OptiScalerReleaseClient(
        temporary_root / "cache" / "optiscaler-online",
        opener=online_open,
    )
    game = replace(
        demo_games()[0], steam_app_id="224760", install_path=game_root
    )
    repository = GameOptimizationProfileRepository(temporary_root / "config" / "games")
    optiscaler_service = OptiScalerService(
        profile_repository=OptiScalerProfileRepository(
            temporary_root / "config" / "games"
        ),
        data_root=temporary_root / "data" / "games",
        process_detector=lambda _path: (),
    )
    proton_repository = ProtonTweaksRepository(
        temporary_root / "config" / "games"
    )

    class ToolDetector:
        def detect(self, *, refresh: bool = False) -> tuple[RuntimeToolAvailability, RuntimeToolAvailability]:
            del refresh
            return (
                RuntimeToolAvailability("GameMode", False),
                RuntimeToolAvailability(
                    "Gamescope", True, "/usr/bin/gamescope",
                    supported_options=("-w", "-h", "-W", "-H", "-r", "-S", "-F", "-f", "-b"),
                ),
            )

    controller = AppController(
        parent=application,
        game_provider=DemoGameProvider((game,)),
        settings_store=SettingsStore(temporary_root / "settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider(available=False)),
        optimization_profile_repository=repository,
        runner_integration=RunnerIntegration(temporary_root / "bin" / "game-optimization-run"),
        runtime_tool_detector=ToolDetector(),  # type: ignore[arg-type]
        optiscaler_service=optiscaler_service,
        optiscaler_release_client=online_client,
        proton_tweaks_repository=proton_repository,
        auto_refresh=False,
    )
    view, root = _view(application, "pages/details/OptimizationTab.qml", 1600, 1000)
    root.setProperty("controller", controller)
    root.setProperty("gameData", controller.games[0])
    _settle(application, 20)
    if str(root.property("appId")) != "224760":
        raise AssertionError(f"Optimization profile did not load: {root.property('errorMessage')!r}")
    started_analysis = controller.analyzeGameOptimization(game.id)
    if not started_analysis.get("success"):
        raise AssertionError(f"Synthetic game analysis did not start: {started_analysis!r}")
    analysis_deadline = time.monotonic() + 3.0
    while time.monotonic() < analysis_deadline and controller._optimization_jobs:
        controller._poll_tasks()
        _settle(application, 1)
    _settle(application, 8)
    analysis = controller.getGameOptimizationAnalysis(game.id)
    if analysis.get("status") != "completed":
        raise AssertionError(f"Synthetic game analysis did not finish: {analysis!r}")
    if analysis["fingerprint"]["engine"]["value"] != "Unreal Engine":
        raise AssertionError(f"Synthetic fingerprint is incorrect: {analysis!r}")
    rendered_analysis = _variant(root.property("gameAnalysis")) or {}
    if rendered_analysis.get("status") != "completed":
        raise AssertionError(f"Optimization QML did not refresh its analysis: {rendered_analysis!r}")
    _invoke_qml(root, "choosePreset", "quiet")
    _settle(application, 8)
    _invoke_qml(root, "saveProfile")
    _settle(application, 10)
    saved = repository.load("224760")
    if saved.preset != "quiet" or saved.target_fps != 45:
        raise AssertionError(f"Desktop optimization profile was not saved: {saved!r}")
    gamescope_preview = controller.previewOptimizationProfile(game.id, {
        "gamescopeEnabled": True,
        "gamescopeMode": "native",
        "targetFpsMode": "manual",
        "targetFps": 72,
    })
    _invoke_qml(root, "applyResult", gamescope_preview)
    _settle(application, 6)
    optiscaler_section = _item(root, "optiScalerSection")
    if not controller.refreshOptiScalerRelease(game.id, True):
        raise AssertionError("Online OptiScaler check did not start")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and controller._optiscaler_jobs:
        controller._poll_tasks()
        _settle(application, 1)
    _settle(application, 8)
    _invoke_qml(optiscaler_section, "inspectOnline")
    _settle(application, 8)
    online_plan = _variant(optiscaler_section.property("planData")) or {}
    optiscaler_section.setProperty("archiveUrl", QUrl.fromLocalFile(str(archive)).toString())
    _invoke_qml(optiscaler_section, "inspectArchive")
    _settle(application, 8)
    optiscaler_plan = _variant(optiscaler_section.property("planData")) or {}
    optiscaler_picker_filters = _variant(
        optiscaler_section.property("archiveNameFilters")
    ) or []
    proton_saved = controller.saveProtonTweaks(
        game.id,
        {
            "toggles": {"proton_log": True, "no_fsync": True},
            "optiscalerFsr4Update": False,
        },
    )
    _settle(application, 8)
    gamescope_preview = controller.previewOptimizationProfile(game.id, {
        "gamescopeEnabled": True,
        "gamescopeMode": "native",
        "targetFpsMode": "manual",
        "targetFps": 72,
    })
    _invoke_qml(root, "applyResult", gamescope_preview)
    _settle(application, 6)
    proton_section = _item(root, "protonTweaksSection")
    scroll = _item(root, "optimizationScroll")
    flickable = scroll.property("contentItem")
    if isinstance(flickable, QQuickItem):
        flickable.setProperty(
            "contentY",
            max(0.0, float(flickable.property("contentHeight") or 0) - flickable.height()),
        )
        _settle(application, 8)
    save_button = _item(root, "saveOptimizationProfileButton")
    _assert_inside(save_button, root)
    screenshot = temporary_root / "optimization-desktop.png"
    view.grabWindow().save(str(screenshot))
    result = {
        "app_id": saved.app_id,
        "preset": saved.preset,
        "target_fps": saved.target_fps,
        "display_count": len(_variant(root.property("displays")) or []),
        "steam_command": str(root.property("steamLaunchCommand")),
        "plan": str(root.property("launchPlanText")),
        "fps_limit_owner": str(root.property("fpsLimitOwner")),
        "fps_owner_label": str(_item(root, "fpsLimitOwnerLabel").property("text")),
        "gamescope_r_count": list(gamescope_preview["launchPlan"]["command"]).count("-r"),
        "has_legacy_framerate_limit": "--framerate-limit" in gamescope_preview["launchPlan"]["command"],
        "optiscaler_plan_ready": bool(optiscaler_plan.get("canInstall")),
        "optiscaler_archive_format": str(optiscaler_plan.get("archiveFormat", "")),
        "optiscaler_picker_filters": [
            str(item) for item in optiscaler_picker_filters
        ],
        "optiscaler_install_directory": str(optiscaler_plan.get("installDirectory", "")),
        "optiscaler_proxy": str(optiscaler_plan.get("injectionDll", "")),
        "optiscaler_online_ready": bool(online_plan.get("officialRelease")),
        "optiscaler_available_version": str(
            (_variant(optiscaler_section.property("statusData")) or {}).get(
                "availableVersion", ""
            )
        ),
        "proton_environment": dict(proton_saved.get("environment", {})),
        "proton_entries": len(_variant(proton_section.property("entries")) or []),
        "analysis_status": analysis.get("status"),
        "analysis_engine": analysis["fingerprint"]["engine"]["value"],
        "analysis_api": analysis["fingerprint"]["graphicsApi"]["value"],
        "analysis_baseline_available": analysis.get("baselineAvailable"),
        "save_button_inside": True,
        "screenshot": str(screenshot),
        "screenshot_size": screenshot.stat().st_size,
    }
    view.close()
    controller.shutdown()
    return result


def probe_couch(
    application: QGuiApplication,
    width: int,
    height: int,
    scenario: str,
    theme_mode: str,
) -> dict[str, Any]:
    from game_optimization_linux.controllers import AppController
    from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider
    from game_optimization_linux.providers.demo import demo_games
    from game_optimization_linux.services import (
        GamepadService,
        GameOptimizationProfileRepository,
        MangoHudDetector,
        MangoHudLaunchIntegration,
        MangoHudProfileRepository,
        SettingsStore,
    )

    temporary_root = Path(tempfile.mkdtemp(prefix="game-optimization-couch-probe-"))
    mangohud_repository = MangoHudProfileRepository(
        temporary_root / "config" / "games",
        log_root=temporary_root / "state" / "mangohud-logs",
    )
    fake_layer = temporary_root / "MangoHud.x86_64.json"
    fake_layer.write_text("{}\n", encoding="utf-8")
    mangohud_detector = MangoHudDetector(
        which=lambda name: "/app/bin/mangohud-probe" if name == "mangohud" else None,
        command_runner=lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, "MangoHud 0.8.0\n", ""
        ),
        layer_paths=(fake_layer,),
    )
    optimization_repository = GameOptimizationProfileRepository(
        temporary_root / "config" / "games"
    )
    demo_fixture = tuple(
        replace(game, steam_app_id="239140") if game.id == "dying-light" else game
        for game in demo_games()
    )
    controller = AppController(
        parent=application,
        game_provider=DemoGameProvider(demo_fixture),
        settings_store=SettingsStore(temporary_root / "settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider(available=False)),
        mangohud_repository=mangohud_repository,
        mangohud_detector=mangohud_detector,
        mangohud_launch_integration=MangoHudLaunchIntegration(
            mangohud_repository,
            mangohud_detector,
            flatpak_config_root=temporary_root / "flatpak-config",
            application_config_root=temporary_root / "MangoHud",
            native_steam_environment=lambda: None,
        ),
        optimization_profile_repository=optimization_repository,
        auto_refresh=False,
    )
    view, root = _view(application, "couch/CouchMain.qml", width, height)
    theme = view.engine().singletonInstance("Game Optimization", "Theme")
    if isinstance(theme, QObject):
        theme.setProperty("mode", theme_mode)
    root.setProperty("controller", controller)
    _invoke_qml(root, "setSection", "home", "")
    _settle(application, 24)
    home = _item(root, "couchHome")
    base_games = [dict(game) for game in controller.games]
    if scenario == "one":
        fixture_games = base_games[:1]
    elif scenario == "disconnected":
        disconnected = dict(base_games[0])
        disconnected.update(
            {
                "id": "disconnected-fixture",
                "status": "Drive disconnected",
                "libraryAvailable": False,
                "launchAllowed": False,
                "analysisAllowed": False,
                "availabilityStatus": "Library unavailable",
            }
        )
        fixture_games = [disconnected]
    elif scenario == "long":
        long_game = dict(base_games[0])
        long_game["id"] = "long-name-fixture"
        long_game["name"] = "A remarkably long game title that must remain inside a Couch Mode card at every supported resolution"
        fixture_games = [long_game, *base_games]
    else:
        fixture_games = base_games
    home.setProperty("games", fixture_games)
    _settle(application, 20)
    strip = _item(root, "couchGameStrip")
    home_title = _item(root, "couchHomeHeroTitle")
    home_navigation = _item(root, "couchHomeNavigation")
    home_actions = [
        item for item in _named(root, "couchHomeHeroAction")
        if isinstance(item, QQuickItem) and item.isVisible()
    ]
    home_cards = [
        item for item in _named(root, "couchHomeGameCard")
        if isinstance(item, QQuickItem) and item.isVisible()
    ]
    tv_metrics = {
        "homeTitlePx": _font_pixel_size(home_title),
        "homeActionMinHeight": min((item.height() for item in home_actions), default=0),
        "homeCardMaxWidth": max((item.width() * item.scale() for item in home_cards), default=0),
        "homeCardMaxHeight": max((item.height() * item.scale() for item in home_cards), default=0),
        "homeNavigationHeight": home_navigation.height(),
    }
    _assert_inside(strip, home)
    if strip.width() <= 0 or strip.height() <= 0:
        raise AssertionError("Couch game strip has invalid geometry")
    for tile in _named(home, "couchHomeTile"):
        if isinstance(tile, QQuickItem) and tile.isVisible():
            _assert_inside(tile, home)
    retained = ""
    if scenario == "many" and len(fixture_games) > 2:
        strip.setProperty("currentIndex", 1)
        home.setProperty("retainedGameId", str(fixture_games[1]["id"]))
        _settle(application, 5)
        retained = str(home.property("retainedGameId"))
        home.setProperty("games", list(reversed(fixture_games)))
        _settle(application, 12)
        selected = home.property("selectedGame")
        if not isinstance(selected, dict) or str(selected.get("id")) != retained:
            raise AssertionError(
                f"Couch selection was not retained after a model refresh: "
                f"retained={retained!r}, selected={selected!r}, "
                f"index={strip.property('currentIndex')!r}"
            )
    _invoke_qml(root, "openGameFrom", "home", "dying-light")
    _settle(application, 20)
    details = _item(root, "couchGameDetails")
    details_cover = _item(root, "couchDetailsCover")
    details_title = _item(root, "couchDetailsTitle")
    details_content = _item(root, "couchDetailsContent")
    _assert_inside(details, root)
    _assert_inside(details_cover, details)
    _assert_inside(details_content, details)
    tv_metrics.update({
        "detailsCoverWidth": details_cover.width(),
        "detailsCoverHeight": details_cover.height(),
        "detailsTitlePx": _font_pixel_size(details_title),
        "detailsContentHeight": details_content.height(),
    })
    # Overview, Storage, Graphics and Optimization. The former Backups tab was
    # intentionally removed from both detail views.
    for expected_tab in range(1, 4):
        _invoke_qml(root, "handleAction", "PageRight")
        _settle(application, 3)
        if int(details.property("selectedTab")) != expected_tab:
            raise AssertionError(
                f"Couch details did not switch to tab {expected_tab} with PageRight"
            )
    _invoke_qml(root, "handleAction", "PageRight")
    if int(details.property("selectedTab")) != 0:
        raise AssertionError("Couch details tabs did not wrap to Overview")
    details.setProperty("selectedTab", 3)
    details.setProperty("selectedAction", 0)
    previous_profile = str(details.property("optimizationProfile"))
    _invoke_qml(root, "handleAction", "Confirm")
    _settle(application, 8)
    if not bool(details.property("optimizationOverlayOpen")):
        raise AssertionError("Couch optimization overlay did not open")
    _invoke_qml(root, "handleAction", "NavigateRight")
    if str(details.property("optimizationProfile")) == previous_profile:
        raise AssertionError("Couch optimization preset did not react to the controller")
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 6)
    if bool(details.property("optimizationOverlayOpen")):
        raise AssertionError("Back did not close the Couch optimization overlay")
    if optimization_repository.load("239140").preset != "automatic":
        raise AssertionError("Back saved an uncommitted Couch optimization change")
    _invoke_qml(root, "handleAction", "Confirm")
    _invoke_qml(root, "handleAction", "NavigateRight")
    for _ in range(6):
        _invoke_qml(root, "handleAction", "NavigateDown")
    _invoke_qml(root, "handleAction", "Confirm")
    _settle(application, 8)
    if optimization_repository.load("239140").preset != "maximum_performance":
        raise AssertionError("Couch optimization profile was not saved")

    # The per-game MangoHud editor is a true modal layer. Back cancels without
    # saving and returns focus to the tile that opened it.
    details.setProperty("selectedAction", 3)
    _invoke_qml(root, "handleAction", "Confirm")
    _settle(application, 8)
    if not bool(details.property("mangoHudOverlayOpen")):
        raise AssertionError("Couch MangoHud overlay did not open")
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 8)
    if bool(details.property("mangoHudOverlayOpen")):
        raise AssertionError("Back did not close the Couch MangoHud overlay")
    if int(details.property("selectedAction")) != 3:
        raise AssertionError("MangoHud overlay did not restore its opening tile")
    focus_after_mangohud = application.focusObject()
    if (isinstance(focus_after_mangohud, QQuickItem)
            and not focus_after_mangohud.isVisible()):
        raise AssertionError("Closing MangoHud focused an invisible item")
    mangohud_back_focus = True

    # Saving from Couch changes the same AppID profile used by Desktop Mode.
    _invoke_qml(root, "handleAction", "Confirm")
    _invoke_qml(root, "handleAction", "NavigateRight")
    for _ in range(6):
        _invoke_qml(root, "handleAction", "NavigateDown")
    _invoke_qml(root, "handleAction", "Confirm")
    _settle(application, 12)
    if bool(details.property("mangoHudOverlayOpen")):
        details_game = _variant(details.property("game")) or {}
        debug_profile = controller.getMangoHudProfile(str(details_game.get("id", "")))
        raise AssertionError(
            "Saving MangoHud did not close the Couch overlay: "
            f"row={details.property('mangoHudRow')!r}, "
            f"preset={details.property('mangoHudPreset')!r}, "
            f"details_game={details.property('game')!r}, "
            f"controller_game={controller.selectedGame!r}, "
            f"profile={debug_profile!r}"
        )
    details_game = _variant(details.property("game")) or {}
    saved_profile = controller.getMangoHudProfile(str(details_game.get("id", "")))
    if (saved_profile.get("preset") != "fps_only"
            or saved_profile.get("success") is not True):
        raise AssertionError(f"Couch MangoHud profile was not saved: {saved_profile!r}")
    details.setProperty("selectedTab", 0)

    # Details always returns to Library and restores the same stable game ID,
    # even when the details page was opened from Home.
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 12)
    library = _item(root, "couchLibrary")
    library_grid = _item(root, "couchLibraryGrid")
    if str(root.property("section")) != "library":
        raise AssertionError("Back from Couch details did not return to Library")
    filtered_games = _variant(library.property("filteredGames")) or []
    selected_library_index = int(library_grid.property("currentIndex"))
    selected_library_id = (
        str(filtered_games[selected_library_index].get("id", ""))
        if 0 <= selected_library_index < len(filtered_games)
        else ""
    )
    if selected_library_id != "dying-light":
        raise AssertionError(
            f"Back from details restored {selected_library_id!r}, not dying-light"
        )

    focused_after_details = application.focusObject()
    if (isinstance(focused_after_details, QQuickItem)
            and not focused_after_details.isVisible()):
        raise AssertionError("Back from details focused an invisible QML item")

    _invoke_qml(root, "handleAction", "OpenSystemMenu")
    _settle(application, 5)
    system_menu = _item(root, "couchSystemMenu")
    if not system_menu.isVisible() or int(system_menu.property("selectedIndex")) != 0:
        raise AssertionError("Couch system menu did not use Resume as its safe default")
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 5)
    if system_menu.isVisible():
        raise AssertionError("Couch system menu did not return focus after Back")

    focused_after_menu = application.focusObject()
    if not isinstance(focused_after_menu, QQuickItem):
        raise AssertionError("Closing the system menu did not restore QML focus")
    focus_after_menu_name = (
        focused_after_menu.objectName() if isinstance(focused_after_menu, QObject)
        else ""
    )
    focus_after_menu_visible = focused_after_menu.isVisible()
    if (isinstance(focused_after_menu, QQuickItem)
            and not focused_after_menu.isVisible()):
        raise AssertionError("Closing the system menu focused an invisible item")
    if focus_after_menu_name == "couchSystemMenu":
        raise AssertionError("Closing the system menu left focus in the hidden menu")

    # Quit is a protected submenu. Back closes only the confirmation first,
    # then a second Back closes the system menu.
    _invoke_qml(root, "handleAction", "OpenSystemMenu")
    for _ in range(5):
        _invoke_qml(root, "handleAction", "NavigateDown")
    _invoke_qml(root, "handleAction", "Confirm")
    _settle(application, 3)
    if not bool(system_menu.property("quitConfirmationOpen")):
        raise AssertionError("Quit did not open a confirmation in Couch system menu")
    _invoke_qml(root, "handleAction", "Back")
    if bool(system_menu.property("quitConfirmationOpen")) or not system_menu.isVisible():
        raise AssertionError("Back did not return from quit confirmation to system menu")
    _invoke_qml(root, "handleAction", "Back")
    if system_menu.isVisible():
        raise AssertionError("Second Back did not close the Couch system menu")

    _invoke_qml(root, "setSection", "library", "dying-light")
    _settle(application, 20)
    _assert_inside(library_grid, library)
    if library_grid.width() <= 0 or library_grid.height() <= 0:
        raise AssertionError("Couch library grid has invalid geometry")
    initial_library_index = int(library_grid.property("currentIndex"))
    _invoke_qml(library, "handleAction", "NavigateRight")
    _settle(application, 5)
    moved_library_index = int(library_grid.property("currentIndex"))
    if library_grid.property("count") and moved_library_index < initial_library_index:
        raise AssertionError("Couch library directional navigation moved backwards")

    _invoke_qml(library, "handleAction", "ContextMenu")
    _settle(application, 3)
    if not bool(library.property("filterBarFocused")):
        raise AssertionError("Library filter overlay did not open")
    _invoke_qml(root, "handleAction", "OpenSystemMenu")
    if system_menu.isVisible():
        raise AssertionError("System menu opened on top of the Library filter overlay")
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 3)
    if bool(library.property("filterBarFocused")):
        raise AssertionError("Back did not close only the Library filter overlay")
    tv_metrics.update({
        "libraryColumns": int(library_grid.property("columnCount")),
        "libraryCellWidth": float(library_grid.property("cellWidth")),
    })

    # Back on Home opens the same safe system menu instead of closing the app.
    _invoke_qml(root, "setSection", "home", "")
    _invoke_qml(root, "handleAction", "ContextMenu")
    _settle(application, 3)
    if not bool(home.property("contextMenuOpen")):
        raise AssertionError("Home did not open the selected game's context menu")
    _invoke_qml(root, "handleAction", "OpenSystemMenu")
    if system_menu.isVisible():
        raise AssertionError("System menu opened over the game's context menu")
    _invoke_qml(root, "handleAction", "Back")
    if bool(home.property("contextMenuOpen")):
        raise AssertionError("Back did not close the game's context menu")
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 3)
    if not system_menu.isVisible():
        raise AssertionError("Back on Home did not open the Couch system menu")
    _invoke_qml(root, "handleAction", "Back")

    # Settings uses shared history and returns to the page that opened it.
    _invoke_qml(root, "setSection", "tasks", "")
    _invoke_qml(root, "setSection", "settings", "")
    _invoke_qml(root, "handleAction", "Back")
    _settle(application, 5)
    if str(root.property("section")) != "tasks":
        raise AssertionError("Back from Settings did not use shared navigation history")

    _invoke_qml(root, "setSection", "settings", "")
    _settle(application, 20)
    settings = _item(root, "couchSettings")
    _assert_inside(settings, root)
    result = {
        "resolution": [width, height],
        "games": len(controller.games),
        "scale": float(root.property("couchScale")),
        "details_visible": details.isVisible(),
        "settings_visible": settings.isVisible(),
        "scenario": scenario,
        "theme": theme_mode,
        "retained_game": retained,
        "details_tabs": True,
        "optimization_overlay": True,
        "optimization_back_cancelled": True,
        "optimization_saved_preset": optimization_repository.load("239140").preset,
        "mangohud_overlay": True,
        "mangohud_back_focus": mangohud_back_focus,
        "mangohud_saved_preset": saved_profile.get("preset"),
        "system_menu_safe_default": True,
        "system_menu_entries": len(_variant(system_menu.property("entries")) or []),
        "quit_confirmation": True,
        "details_return_game": selected_library_id,
        "focus_after_menu": focus_after_menu_name,
        "focus_after_menu_visible": focus_after_menu_visible,
        "settings_back_target": "tasks",
        "home_back_opens_menu": True,
        "game_context_menu": True,
        "modal_layers_exclusive": True,
        "library_grid_valid": True,
        "library_selection": moved_library_index,
        "tv_metrics": tv_metrics,
    }
    view.close()
    controller.shutdown()
    return result


def _probe_desktop_updates(
    application: QGuiApplication,
    width: int,
    height: int,
    scenario: str,
) -> dict[str, Any]:
    updates = _updates_fixture(scenario)
    controller = UpdatesProbeController(updates)
    view, root = _view(application, "pages/UpdatesPage.qml", width, height)
    root.setProperty("controller", controller)
    _settle(application, 30)
    _assert_finite_nonnegative_geometry(root)

    summary = _item(root, "updatesSummaryGrid")
    updates_list = _item(root, "updatesList")
    application_section = _item(root, "gameOptimizationUpdateSection")
    _assert_inside(summary, root)
    _assert_inside(updates_list, root)
    _assert_inside(application_section, root)

    cards = [
        item
        for item in _named(root, "updatesCard")
        if isinstance(item, QQuickItem) and item.isVisible()
    ]
    empty_state = _item(root, "updatesEmptyState")
    if updates and not cards:
        raise AssertionError(
            f"Desktop Updates created no cards for {scenario}; messages={MESSAGES}"
        )
    if not updates and (cards or not empty_state.isVisible()):
        raise AssertionError("Desktop Updates empty state is inconsistent")

    for card in cards:
        if card.width() <= 0 or card.height() <= 0:
            raise AssertionError(
                f"Invalid desktop update card geometry: {card.width()}x{card.height()}"
            )
        for name in (
            "updatesCardCover",
            "updatesCardInformation",
            "updatesCardActions",
            "analyzeChangesButton",
            "compressChangesButton",
            "ignoreUpdateButton",
            "viewUpdateDetailsButton",
        ):
            _assert_inside(_item(card, name), card)

    reversed_count = len(cards)
    if scenario == "many":
        controller.set_updates(list(reversed(updates)))
        _settle(application, 30)
        _assert_finite_nonnegative_geometry(root)
        reversed_cards = [
            item
            for item in _named(root, "updatesCard")
            if isinstance(item, QQuickItem) and item.isVisible()
        ]
        if not reversed_cards:
            raise AssertionError("Desktop Updates lost all delegates after model reversal")
        reversed_count = len(reversed_cards)
        for card in reversed_cards:
            _assert_inside(_item(card, "updatesCardInformation"), card)

    result = {
        "cards": len(cards),
        "reversedCards": reversed_count,
        "emptyVisible": empty_state.isVisible(),
    }
    view.close()
    _settle(application, 5)
    return result


def _selected_update_id(root: QQuickItem) -> str:
    selected = _variant(root.property("selectedUpdate"))
    if not isinstance(selected, dict):
        raise AssertionError(f"Couch Updates selectedUpdate is not a map: {selected!r}")
    return str(selected.get("gameId") or selected.get("game_id") or selected.get("id") or "")


def _probe_couch_updates(
    application: QGuiApplication,
    width: int,
    height: int,
    scenario: str,
) -> dict[str, Any]:
    updates = _updates_fixture(scenario)
    controller = UpdatesProbeController(updates)
    view, root = _view(application, "couch/CouchUpdates.qml", width, height)
    root.setProperty("couchScale", max(0.82, min(1.8, width / 1920)))
    root.setProperty("controller", controller)
    _settle(application, 30)
    _assert_finite_nonnegative_geometry(root)

    updates_list = _item(root, "couchUpdatesList")
    actions = _item(root, "couchUpdatesActions")
    confirmation = _item(root, "couchCompressionConfirmation")
    _assert_inside(updates_list, root)
    _assert_inside(actions, root)
    _assert_inside(confirmation, root)

    cards = [
        item
        for item in _named(root, "couchUpdateCard")
        if isinstance(item, QQuickItem) and item.isVisible()
    ]
    empty_state = _item(root, "couchUpdatesEmptyState")
    if updates and not cards:
        raise AssertionError(
            f"Couch Updates created no cards for {scenario}; messages={MESSAGES}"
        )
    if not updates and (cards or not empty_state.isVisible()):
        raise AssertionError("Couch Updates empty state is inconsistent")

    for card in cards:
        if card.width() <= 0 or card.height() <= 0:
            raise AssertionError(
                f"Invalid Couch update card geometry: {card.width()}x{card.height()}"
            )
        _assert_inside(_item(card, "couchUpdateCover"), card)
        _assert_inside(_item(card, "couchUpdateInformation"), card)

    retained_game = ""
    confirmation_started = False
    if scenario == "many":
        compression_index = next(
            index for index, update in enumerate(updates) if update["canCompress"]
        )
        _invoke_qml(root, "selectUpdate", compression_index)
        root.setProperty("focusZone", 0)
        _settle(application, 4)
        _invoke_qml(root, "handleAction", "Accept")
        for _ in range(3):
            _invoke_qml(root, "handleAction", "NavigateLeft")
        _invoke_qml(root, "handleAction", "Accept")
        _settle(application, 8)
        if not bool(root.property("confirmationOpen")) or not confirmation.isVisible():
            raise AssertionError(
                "Gamepad flow did not open the Couch compression confirmation: "
                f"index={root.property('selectedIndex')!r}, "
                f"zone={root.property('focusZone')!r}, "
                f"action={root.property('selectedAction')!r}, "
                f"actionModel={_variant(root.property('actionModel'))!r}, "
                f"selected={_variant(root.property('selectedUpdate'))!r}, "
                f"prepare={controller.prepare_calls!r}, messages={MESSAGES!r}"
            )
        if controller.prepare_calls != [
            (str(updates[compression_index]["gameId"]), "Auto", True)
        ]:
            raise AssertionError(
                f"Unexpected prepareCompression calls: {controller.prepare_calls!r}"
            )
        _invoke_qml(root, "handleAction", "NavigateRight")
        _invoke_qml(root, "handleAction", "Accept")
        _settle(application, 8)
        if bool(root.property("confirmationOpen")) or confirmation.isVisible():
            raise AssertionError("Confirmed Couch compression overlay did not close")
        if controller.start_calls != ["updates-probe-plan"]:
            raise AssertionError(f"Unexpected startCompression calls: {controller.start_calls!r}")
        confirmation_started = True

        retained_index = 7
        _invoke_qml(root, "selectUpdate", retained_index)
        _settle(application, 5)
        retained_game = _selected_update_id(root)
        retained_row = str(root.property("retainedRowId"))
        controller.set_updates(list(reversed(updates)))
        _settle(application, 30)
        if _selected_update_id(root) != retained_game:
            raise AssertionError(
                f"Couch selection changed after reversal: retained={retained_game!r}, "
                f"selected={_selected_update_id(root)!r}"
            )
        if str(root.property("retainedRowId")) != retained_row:
            raise AssertionError("Couch retained row identifier changed after model reversal")
        _assert_finite_nonnegative_geometry(root)

    result = {
        "cards": len(cards),
        "emptyVisible": empty_state.isVisible(),
        "retainedGame": retained_game,
        "confirmationStarted": confirmation_started,
        "prepareCalls": len(controller.prepare_calls),
        "startCalls": len(controller.start_calls),
    }
    view.close()
    _settle(application, 5)
    return result


def probe_updates(
    application: QGuiApplication,
    width: int,
    height: int,
    scenario: str,
) -> dict[str, Any]:
    return {
        "resolution": [width, height],
        "scenario": scenario,
        "desktop": _probe_desktop_updates(application, width, height, scenario),
        "couch": _probe_couch_updates(application, width, height, scenario),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "storage",
            "cards",
            "breeze",
            "signal",
            "close",
            "tasks",
            "mangohud",
            "optimization",
            "couch",
            "updates",
            "popups",
            "artwork",
            "artwork_refresh",
            "incremental_games",
        ),
    )
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--theme", choices=("light", "dark"), default="dark")
    parser.add_argument(
        "--scenario",
        choices=("empty", "one", "many", "long", "disconnected", "active", "error"),
        default="many",
    )
    args = parser.parse_args()

    qInstallMessageHandler(_message_handler)
    application = QGuiApplication([sys.argv[0]])
    if args.mode == "storage":
        result = probe_storage(application)
    elif args.mode == "cards":
        result = probe_cards(application, args.width, args.height, args.theme)
    elif args.mode == "breeze":
        result = probe_breeze(application)
    elif args.mode == "signal":
        result = probe_signal_shutdown(application)
    elif args.mode == "close":
        result = probe_close_during_compression(application)
    elif args.mode == "tasks":
        result = probe_tasks_lifecycle(application)
    elif args.mode == "mangohud":
        result = probe_mangohud_editor(application)
    elif args.mode == "optimization":
        result = probe_optimization_editor(application)
    elif args.mode == "couch":
        result = probe_couch(application, args.width, args.height, args.scenario, args.theme)
    elif args.mode == "popups":
        result = probe_game_popups(application)
    elif args.mode == "artwork":
        result = probe_artwork_reuse(application)
    elif args.mode == "artwork_refresh":
        result = probe_artwork_refresh(application)
    elif args.mode == "incremental_games":
        result = probe_incremental_games_model(application)
    else:
        result = probe_updates(application, args.width, args.height, args.scenario)

    bad_messages = [
        message
        for message in MESSAGES
        if "Unable to assign" in message
        or "Binding loop" in message
        or "Layout polish loop" in message
        or "Cannot create delegate" in message
        or "destroyed during incubation" in message
        or "items in the process of being created at engine destruction" in message
        or "ReferenceError" in message
        or "TypeError" in message
    ]
    if bad_messages:
        raise AssertionError("Qt warnings: " + " | ".join(bad_messages))
    print("RESULT:" + json.dumps({"result": result, "messages": MESSAGES}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
