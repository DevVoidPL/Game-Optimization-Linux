pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../dialogs"
import "../.." as App

Item {
    id: tab
    objectName: "storageTab"

    property var controller
    property var gameData: ({})
    property var tasksData: []
    property string selectedMode: "Auto"
    property var preparedPlan: ({})
    property bool preparingPlan: false

    readonly property string gameId: String(value(["id"], ""))
    readonly property var report: value(["analysisReport"], ({}))
    readonly property var benchmarkEstimate: value(["benchmarkEstimate"], ({}))
    readonly property bool benchmarkEstimateAvailable: Boolean(
                                                           benchmarkEstimate
                                                           && benchmarkEstimate.available === true)
    readonly property int selectedBenchmarkLevel: benchmarkLevelForMode(
                                                      selectedMode)
    readonly property var selectedBenchmarkProjection:
        benchmarkProjection(selectedBenchmarkLevel)
    readonly property bool selectedBenchmarkProjectionAvailable: Boolean(
                                                                     selectedBenchmarkProjection
                                                                     && selectedBenchmarkProjection.available === true)
    readonly property bool lowAdditionalBenefit: Boolean(
                                                     selectedBenchmarkProjectionAvailable
                                                     && selectedBenchmarkProjection.lowBenefit === true)
    readonly property var compsize: report && typeof report.compsize === "object"
                                            ? report.compsize : ({})
    readonly property var btrfsDu: report && typeof report.btrfs_du === "object"
                                          ? report.btrfs_du
                                          : report && typeof report.btrfsDu === "object"
                                            ? report.btrfsDu : ({})
    readonly property var analysisTask: findAnalysisTask()
    readonly property var verificationTask: findVerificationTask()
    readonly property bool hasVerificationTask: Boolean(
                                                    verificationTask
                                                    && String(
                                                        verificationTask.id || "").length > 0)
    readonly property string verificationStatus: String(
                                                     verificationTask.status || "")
    readonly property bool verificationActive: ["queued", "running", "analyzing"]
                                               .indexOf(verificationStatus.toLowerCase()) >= 0
    readonly property bool verificationFailed: verificationStatus.toLowerCase()
                                               === "failed"
    readonly property var verificationMeasurement: (
                                                       verificationStatus.toLowerCase() === "completed"
                                                       && verificationTask.result
                                                       && typeof verificationTask.result === "object")
                                                     ? verificationTask.result : ({})
    readonly property bool verificationSucceeded: Boolean(
                                                    verificationStatus.toLowerCase()
                                                    === "completed"
                                                    && hasCompleteCompsizeMeasurement(
                                                        verificationMeasurement))
    readonly property string analysisStatus: String(analysisTask.status || "")
    readonly property string normalizedAnalysisStatus: analysisStatus.toLowerCase()
    readonly property bool hasAnalysisTask: Boolean(analysisTask
                                                     && String(analysisTask.id || "").length > 0)
    readonly property bool analysisQueued: normalizedAnalysisStatus === "queued"
                                                   || normalizedAnalysisStatus === "pending"
    readonly property bool analysisRunning: normalizedAnalysisStatus === "analyzing"
                                                    || normalizedAnalysisStatus === "running"
    readonly property bool analysisActive: Boolean(analysisQueued || analysisRunning)
    readonly property bool analysisCancelled: normalizedAnalysisStatus === "cancelled"
    readonly property bool analysisFailed: normalizedAnalysisStatus === "failed"
    readonly property bool reportReady: Boolean(
                                            value(["analysisReportAvailable"], false)
                                            || (report
                                                && String(report.game_id || "") === gameId
                                                && String(report.created_at || "").length > 0))
    readonly property bool pathAvailable: Boolean(value(
                                                      ["analysisPathAvailable"],
                                                      reportReady
                                                      && report.path_exists === true
                                                      && report.path_is_directory === true))
    readonly property bool confirmedBtrfs: Boolean(value(
                                                       ["analysisIsBtrfs"],
                                                       reportReady
                                                       && report.is_btrfs === true))
    readonly property bool scanSucceeded: Boolean(value(
                                                      ["analysisScanComplete"],
                                                      reportReady
                                                      && report.scan_complete === true))
    readonly property bool analysisSucceeded: Boolean(reportReady
                                                       && !analysisActive
                                                       && !analysisCancelled
                                                       && !analysisFailed)
    readonly property bool analysisComplete: Boolean(analysisSucceeded
                                                      && pathAvailable
                                                      && scanSucceeded)
    readonly property bool hasSelectedProfile: ["Fast", "Balanced", "Maximum", "Auto"]
                                               .indexOf(selectedMode) >= 0
    readonly property bool profilesEnabled: Boolean(analysisComplete
                                                     && confirmedBtrfs
                                                     && value(["analysisProfilesUnlocked"],
                                                              report.profiles_unlocked === true))
    readonly property bool showRunningWarning: Boolean(value(
                                                           ["analysisGameRunning"],
                                                           reportReady
                                                           && report.game_running === true))
    readonly property bool hasWarnings: Boolean(value(
                                                    ["analysisHasWarnings"],
                                                    reportReady
                                                    && Array.isArray(report.warnings)
                                                    && report.warnings.length > 0))
    readonly property bool analysisAllowed: Boolean(value(
                                                        ["analysisAllowed"],
                                                        value(["libraryAvailable"], true)))
    readonly property var selectedEstimate: profileEstimate(selectedMode)
    readonly property string sharedExtentState: String(
                                                     duValue(["state"],
                                                             "")
                                                     || (report.possible_shared_extents === true
                                                         ? "detected"
                                                         : "unknown"))
                                                 .toLowerCase()
    readonly property bool sharedMeasurementAvailable: Boolean(
                                                           duValue(["available"], false) === true
                                                           && (sharedExtentState === "detected"
                                                               || sharedExtentState === "not_detected"))
    readonly property bool sharedExtentsDetected: Boolean(
                                                      sharedMeasurementAvailable
                                                      && sharedExtentState === "detected")
    readonly property bool sharedExtentsClear: Boolean(
                                                   sharedMeasurementAvailable
                                                   && sharedExtentState === "not_detected")
    readonly property bool reportCompressionEligible: Boolean(
                                                          reportReady
                                                          && (report.compression_eligible === true
                                                              || report.compressionEligible === true))
    readonly property bool compressPrerequisitesReady: Boolean(
                                                          profilesEnabled
                                                          && pathAvailable
                                                          && scanSucceeded
                                                          && confirmedBtrfs
                                                          && report.writable === true
                                                          && report.game_running !== true
                                                          && reportCompressionEligible
                                                          && sharedMeasurementAvailable
                                                          && hasSelectedProfile
                                                          && controller !== null
                                                          && controller !== undefined
                                                          && typeof controller.prepareCompression === "function"
                                                          && typeof controller.startCompression === "function")
    readonly property var compressionHistory: controller
                                               && controller.selectedGameCompressionHistory
                                               ? controller.selectedGameCompressionHistory : []
    readonly property var latestCompressionResult: compressionHistory.length > 0
                                                   ? compressionHistory[0] : ({})

    signal toastRequested(string message, string tone)

    readonly property var modes: [
        { "key": "Fast", "name": qsTr("Fast"), "symbol": "⚡", "level": "zstd:1", "description": qsTr("Prioritizes speed with lower CPU use.") },
        { "key": "Balanced", "name": qsTr("Balanced"), "symbol": "◐", "level": "zstd:3", "description": qsTr("Recommended balance of time and estimated savings.") },
        { "key": "Maximum", "name": qsTr("Maximum"), "symbol": "◆", "level": "zstd:9", "description": qsTr("Uses more CPU for a potentially small additional gain.") },
        { "key": "Auto", "name": qsTr("Auto"), "symbol": "✦", "level": qsTr("automatic"), "description": qsTr("Chooses between levels 1, 3, 6 and 9 from measured samples.") }
    ]

    function value(keys, fallback) {
        var source = gameData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function findAnalysisTask() {
        var source = tasksData || []
        for (var i = source.length - 1; i >= 0; --i) {
            var task = source[i]
            var operation = String(task.operation || task.type || "").toLowerCase()
            if (String(task.gameId || "") === gameId && operation === "analysis")
                return task
        }
        return ({})
    }

    function findVerificationTask() {
        var source = tasksData || []
        var newestActive = ({})
        var newestTerminal = ({})
        var activeStamp = ""
        var terminalStamp = ""
        for (var i = 0; i < source.length; ++i) {
            var task = source[i]
            var operation = String(task.operation || task.type || "").toLowerCase()
            if (String(task.gameId || "") !== gameId
                    || operation !== "verification")
                continue
            var status = String(task.status || "").toLowerCase()
            var stamp = String(task.updatedAt || task.createdAt || "")
            var active = ["queued", "running", "analyzing"].indexOf(status) >= 0
            if (active && (!newestActive.id || stamp >= activeStamp)) {
                newestActive = task
                activeStamp = stamp
            } else if (!active
                       && (!newestTerminal.id || stamp >= terminalStamp)) {
                newestTerminal = task
                terminalStamp = stamp
            }
        }
        if (newestActive.id)
            return newestActive
        if (newestTerminal.id)
            return newestTerminal
        var storedId = String(value(["verificationTaskId"], ""))
        if (storedId.length > 0)
            return {
                "id": storedId,
                "gameId": gameId,
                "operation": "Verification",
                "status": String(value(["verificationStatus"], "")),
                "error": String(value(["verificationError"], "")),
                "result": value(["verificationResult"], ({}))
            }
        return ({})
    }

    function measurementValue(measurement, keys, fallback) {
        var source = measurement || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function resultValue(keys, fallback) {
        return historyValue(latestCompressionResult, keys, fallback)
    }

    function resultMeasurement(keys) {
        var measured = resultValue(keys, ({}))
        return measured && typeof measured === "object" ? measured : ({})
    }

    function authoritativeResult() {
        return resultValue(["measurementAuthoritative",
                            "measurement_authoritative"], false) === true
    }

    function displayedAfterMeasurement() {
        if (hasVerificationTask)
            return verificationMeasurement
        return resultMeasurement(["after"])
    }

    function activeCompressionEffect() {
        var current = displayedAfterMeasurement()
        var uncompressed = measurementValue(
                    current, ["compsizeUncompressedBytes",
                              "compsize_uncompressed_bytes"], null)
        var disk = measurementValue(
                    current, ["compsizeDiskBytes",
                              "compsize_disk_bytes"], null)
        if (uncompressed === null || disk === null)
            return null
        return Math.max(0, Number(uncompressed) - Number(disk))
    }

    function hasCompleteCompsizeMeasurement(measurement) {
        var source = measurement && typeof measurement === "object"
                     ? measurement : ({})
        return String(measurementValue(source,
                                       ["measurementSource",
                                        "measurement_source"],
                                       "")).toLowerCase() === "polkit_helper"
                && Number(measurementValue(
                              source,
                              ["compsizeDiskBytes", "compsize_disk_bytes"],
                              0)) > 0
                && Number(measurementValue(
                              source,
                              ["compsizeUncompressedBytes",
                               "compsize_uncompressed_bytes"],
                              0)) > 0
                && Number(measurementValue(
                              source,
                              ["compsizeReferencedBytes",
                               "compsize_referenced_bytes"],
                              0)) > 0
    }

    function scannerLogicalBytes() {
        var known = Number(value(["scannerLogicalBytes", "sizeBytes"], 0))
        if (isFinite(known) && known > 0)
            return known
        var analyzed = Number(report.logical_bytes || 0)
        return isFinite(analyzed) && analyzed > 0 ? analyzed : null
    }

    function benchmarkLevelForMode(mode) {
        if (mode === "Fast")
            return 1
        if (mode === "Maximum")
            return 9
        if (mode === "Auto") {
            var automatic = Number(report.selected_auto_level
                                   || report.selectedAutoLevel || 0)
            if ([1, 3, 6, 9].indexOf(automatic) >= 0)
                return automatic
        }
        return 3
    }

    function benchmarkProjection(level) {
        var projections = benchmarkEstimate
                          && typeof benchmarkEstimate.projections === "object"
                          ? benchmarkEstimate.projections : ({})
        var projection = projections[String(level)]
        return projection && typeof projection === "object" ? projection : ({})
    }

    function profileEstimate(profile) {
        if (!hasSelectedProfile || !report || typeof report.profiles !== "object")
            return ({})
        return report.profiles[profile] || ({})
    }

    function formatBytes(raw) {
        if (raw === undefined || raw === null || raw === "")
            return qsTr("Not available")
        var bytes = Number(raw)
        if (!isFinite(bytes) || bytes < 0)
            return qsTr("Not available")
        var units = [qsTr("B"), qsTr("KiB"), qsTr("MiB"), qsTr("GiB"), qsTr("TiB")]
        var unit = 0
        while (bytes >= 1024 && unit < units.length - 1) {
            bytes /= 1024
            unit++
        }
        var digits = unit === 0 ? 0 : bytes >= 100 ? 0 : bytes >= 10 ? 1 : 2
        return bytes.toFixed(digits) + " " + units[unit]
    }

    function formatRange(low, high) {
        if (low === undefined || low === null || high === undefined || high === null)
            return qsTr("Not estimated")
        return qsTr("%1-%2").arg(formatBytes(low)).arg(formatBytes(high))
    }

    function formatSignedBytes(raw) {
        if (raw === undefined || raw === null || raw === "")
            return qsTr("Not available")
        var bytes = Number(raw)
        if (!isFinite(bytes))
            return qsTr("Not available")
        return bytes < 0 ? "−" + formatBytes(Math.abs(bytes)) : formatBytes(bytes)
    }

    function formatDuration(low, high) {
        if (low === undefined || low === null || high === undefined || high === null)
            return qsTr("Not estimated")
        return qsTr("%1-%2 min").arg(Math.max(1, Math.round(Number(low) / 60)))
                                    .arg(Math.max(1, Math.round(Number(high) / 60)))
    }

    function currentDiskBytes() {
        var current = displayedAfterMeasurement()
        if (hasCompleteCompsizeMeasurement(current))
            return measurementValue(current,
                                    ["compsizeDiskBytes",
                                     "compsize_disk_bytes"],
                                    null)
        return null
    }

    function estimatedRewriteBytes() {
        var planned = Number(preparedPlan && preparedPlan.valid === true
                             ? (preparedPlan.totalBytes
                                || preparedPlan.plannedBytes || 0) : 0)
        if (isFinite(planned) && planned > 0)
            return planned
        var analyzed = Number(report && report.logical_bytes
                              ? report.logical_bytes : 0)
        return isFinite(analyzed) && analyzed > 0 ? analyzed : null
    }

    function translatedBenefit(raw) {
        if (raw === "High benefit")
            return qsTr("High benefit")
        if (raw === "Moderate benefit")
            return qsTr("Moderate benefit")
        if (raw === "Low benefit")
            return qsTr("Low benefit")
        return qsTr("Not estimated")
    }

    function compressionTypes() {
        if (!compsize || !compsize.compression_types)
            return qsTr("None detected")
        var names = Object.keys(compsize.compression_types)
        return names.length > 0 ? names.join(", ") : qsTr("None detected")
    }

    function ratioLabel() {
        var ratio = Number(compsize.current_compression_ratio)
        return isFinite(ratio) && ratio > 0 ? ratio.toFixed(2) + "×" : qsTr("Not available")
    }

    function yesNoUnknown(raw) {
        if (raw === true) return qsTr("Yes")
        if (raw === false) return qsTr("No")
        return qsTr("Unknown")
    }

    function analysisExplanation() {
        if (!analysisAllowed)
            return qsTr("Library unavailable")
        if (analysisQueued)
            return qsTr("Analysis is queued.")
        if (analysisRunning)
            return qsTr("Analysis is running.")
        if (analysisCancelled)
            return qsTr("Analysis was cancelled. Analyze the game again to enable profiles.")
        if (analysisFailed) {
            var failure = String(analysisTask.error || "")
            return failure.length > 0
                    ? qsTr("Analysis failed: %1").arg(App.I18n.message(failure))
                    : qsTr("Analysis failed. Profiles remain disabled.")
        }
        if (!reportReady)
            return qsTr("Analyze the game first")
        if (!pathAvailable)
            return qsTr("Analysis finished, but the game directory could not be read.")
        if (!confirmedBtrfs)
            return qsTr("Analysis finished. Compression profiles are unavailable on %1.")
                    .arg(String(report.filesystem || qsTr("this filesystem")))
        if (!scanSucceeded)
            return qsTr("Analysis finished with read errors. Profiles remain disabled.")
        return qsTr("Analysis complete. Choose a planning profile below.")
    }

    function translatedCpu(raw) {
        if (raw === "Very high")
            return qsTr("Very high")
        if (raw === "High")
            return qsTr("High")
        if (raw === "Moderate")
            return qsTr("Moderate")
        if (raw === "Low")
            return qsTr("Low")
        return qsTr("Not estimated")
    }

    function autoSelectionExplanation() {
        var level = Number(report.selected_auto_level
                           || report.selectedAutoLevel || 0)
        if (level === 1)
            return qsTr("Auto selected ZSTD level 1 because the measured samples favor speed over a small additional space gain.")
        if (level === 3)
            return qsTr("Auto selected ZSTD level 3 because higher levels produced only a minimal measured gain.")
        if (level === 6)
            return qsTr("Auto selected ZSTD level 6 because the measured samples showed a worthwhile gain over the balanced level.")
        if (level === 9)
            return qsTr("Auto selected ZSTD level 9 because the measured samples showed a strong additional space gain.")
        return qsTr("Auto selects deterministically from ZSTD levels 1, 3, 6 and 9 using sample size and elapsed time; it does not use AI.")
    }

    function duValue(keys, fallback) {
        var source = btrfsDu || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function planBlockers(plan) {
        if (!plan || typeof plan !== "object")
            return []
        return Array.isArray(plan.blockers) ? plan.blockers : []
    }

    function planWarnings(plan) {
        if (!plan || typeof plan !== "object")
            return []
        return Array.isArray(plan.warnings) ? plan.warnings : []
    }

    function planId(plan) {
        if (!plan || typeof plan !== "object")
            return ""
        return String(plan.planId || plan.id || "")
    }

    function planCanStart(plan) {
        if (!plan || typeof plan !== "object" || planId(plan).length === 0)
            return false
        var eligible = plan.eligible === true || plan.canStart === true
        var explicitlyInvalid = plan.valid === false || plan.canStart === false
                            || plan.eligible === false
        return Boolean(eligible && !explicitlyInvalid && planBlockers(plan).length === 0)
    }

    function firstPlanBlocker(plan) {
        var blockers = planBlockers(plan)
        if (blockers.length > 0)
            return App.I18n.message(String(blockers[0]))
        var message = plan && (plan.message || plan.error)
        return String(message || qsTr("The compression plan could not be prepared safely."))
    }

    function compressionBlocker() {
        if (!analysisAllowed)
            return qsTr("Library unavailable")
        if (analysisActive)
            return qsTr("Wait until analysis is complete.")
        if (!reportReady)
            return qsTr("Analyze the game first")
        if (!pathAvailable)
            return qsTr("The analyzed game path is unavailable.")
        if (!scanSucceeded)
            return qsTr("A complete analysis is required.")
        if (!confirmedBtrfs)
            return qsTr("The game is not on a verified Btrfs filesystem.")
        if (report.writable !== true)
            return qsTr("The game directory is not writable.")
        if (report.game_running === true)
            return qsTr("Close the game before preparing compression.")
        if (!sharedMeasurementAvailable)
            return qsTr("Shared-extent risk could not be measured reliably. Compression is blocked.")
        if (!reportCompressionEligible)
            return qsTr("The analysis did not confirm that compression can run safely.")
        if (!hasSelectedProfile)
            return qsTr("Choose a compression profile.")
        return qsTr("A safe plan can now be prepared and reviewed.")
    }

    function confirmationMessage(plan) {
        var profile = App.I18n.profile(String(plan.profile || selectedMode))
        var persistent = String(plan.persistentCompressionAlgorithm
                                || plan.persistent_compression_algorithm || "zstd")
        var level = Number(plan.oneTimeRecompressionLevel
                           || plan.one_time_recompression_level || 0)
        var totalFiles = Number(plan.totalFiles || plan.total_files || 0)
        var totalBytes = Number(plan.totalBytes || plan.total_bytes || 0)
        var low = plan.estimatedSavingsLowBytes
        if (low === undefined || low === null)
            low = plan.estimated_savings_low_bytes
        var high = plan.estimatedSavingsHighBytes
        if (high === undefined || high === null)
            high = plan.estimated_savings_high_bytes
        var required = plan.requiredFreeBytes
        if (required === undefined || required === null)
            required = plan.required_free_bytes
        var sharedState = String(plan.sharedExtentState
                                 || plan.shared_extent_state || "unknown")
        var sharedGrowth = plan.estimatedSharedGrowthBytes
        if (sharedGrowth === undefined || sharedGrowth === null)
            sharedGrowth = plan.estimated_shared_growth_bytes
        var sharingLine = sharedState === "detected"
                ? qsTr("Warning: shared extents are present. Recompression breaks sharing for processed data; Snapper snapshots may retain the old extents, so filesystem usage may temporarily grow by up to %1 and free space may not increase until snapshots expire.")
                    .arg(formatBytes(sharedGrowth))
                : qsTr("No shared extents were detected by the analysis. Game Optimization will recheck each file immediately before recompression.")
        var lines = [
            qsTr("Game: %1").arg(String(plan.gameName || plan.game_name
                                       || value(["name"], qsTr("Unknown game")))),
            qsTr("Profile: %1").arg(profile),
            qsTr("Persistent algorithm: %1").arg(persistent),
            qsTr("One-time recompression level: ZSTD %1").arg(level > 0 ? level : "-"),
            qsTr("Planned files: %1 (%2)").arg(totalFiles).arg(formatBytes(totalBytes)),
            plan.profitabilityAvailable === true
                ? qsTr("Current measured saving: %1")
                  .arg(formatBytes(plan.profitability.currentSavingBytes))
                : qsTr("Current measured saving: Not available"),
            plan.profitabilityAvailable === true
                ? qsTr("Estimated additional saving: %1")
                  .arg(formatBytes(plan.estimatedAdditionalSavingBytes))
                : qsTr("Estimated additional saving: Not available"),
            plan.profitabilityAvailable === true
                ? qsTr("Estimated physical size after operation: %1")
                  .arg(formatBytes(plan.estimatedPhysicalBytes))
                : qsTr("Estimated physical size after operation: Not available"),
            qsTr("Required free space: %1").arg(formatBytes(required)),
            "",
            sharingLine,
            qsTr("The operation can take time. Keep Steam and the game closed until final verification is complete.")
        ]
        var warnings = planWarnings(plan)
        if (warnings.length > 0)
            lines.push("", qsTr("Warnings: %1").arg(
                           warnings.map(function(item) {
                               return App.I18n.message(String(item))
                           }).join("; ")))
        return lines.join("\n")
    }

    function openFinalCompressionConfirmation(plan) {
        compressionConfirm.ask(
                    qsTr("Confirm Btrfs compression"),
                    confirmationMessage(plan),
                    qsTr("Start compression"),
                    false,
                    planId(plan))
    }

    function prepareCompressionPlan() {
        if (!compressPrerequisitesReady) {
            toastRequested(compressionBlocker(), "warning")
            return
        }
        preparingPlan = true
        var plan = ({})
        try {
            plan = controller.prepareCompression(gameId, selectedMode, false)
        } catch (error) {
            preparingPlan = false
            toastRequested(qsTr("The compression plan could not be prepared: %1")
                           .arg(String(error)), "error")
            return
        }
        preparingPlan = false
        preparedPlan = plan && typeof plan === "object" ? plan : ({})
        if (!planCanStart(preparedPlan)) {
            toastRequested(firstPlanBlocker(preparedPlan), "error")
            return
        }
        if (preparedPlan.additionalConfirmationRequired === true) {
            profitabilityConfirm.ask(
                        qsTr("Low estimated benefit"),
                        qsTr("The estimated additional saving is below 1 GiB or below 5% of current physical usage. Recompression may rewrite files for a long time for little benefit. Do you want to continue to the final safety confirmation?"),
                        qsTr("Continue to final confirmation"),
                        false,
                        planId(preparedPlan))
            return
        }
        openFinalCompressionConfirmation(preparedPlan)
    }

    function historyValue(entry, keys, fallback) {
        if (!entry || typeof entry !== "object")
            return fallback
        for (var i = 0; i < keys.length; ++i) {
            var candidate = entry[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function formatTimestamp(raw) {
        var value = String(raw || "")
        if (value.length === 0)
            return qsTr("Unknown")
        var date = new Date(value)
        return isNaN(date.getTime())
                ? value
                : Qt.formatDateTime(date, Locale.ShortFormat)
    }

    function measurementSummary(measurement) {
        var data = measurement && typeof measurement === "object"
                   ? measurement : ({})
        return qsTr("logical %1 · compsize disk usage %2 · btrfs du exclusive %3 · btrfs du set shared %4")
                .arg(formatBytes(historyValue(
                                     data, ["logicalBytes", "logical_bytes"], null)))
                .arg(formatBytes(historyValue(
                                     data,
                                     ["compsizeDiskBytes", "compsize_disk_bytes"],
                                     null)))
                .arg(formatBytes(historyValue(
                                     data, ["exclusiveBytes", "exclusive_bytes"], null)))
                .arg(formatBytes(historyValue(
                                     data, ["sharedBytes", "shared_bytes"], null)))
    }

    function historyMessage(entry) {
        var error = String(historyValue(entry, ["error"], "") || "")
        if (error.length > 0)
            return App.I18n.message(error)
        var warnings = historyValue(entry, ["warnings"], [])
        if (!Array.isArray(warnings) || warnings.length === 0)
            return ""
        return warnings.map(function(item) {
            return App.I18n.message(String(item))
        }).join("; ")
    }

    onGameIdChanged: {
        selectedMode = "Auto"
        preparedPlan = ({})
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 14

            SurfaceCard {
                Layout.fillWidth: true
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 15

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        Rectangle {
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            radius: 15
                            color: tab.reportReady ? App.Theme.accentSoft : App.Theme.backgroundElevated
                            Label {
                                anchors.centerIn: parent
                                text: "B"
                                color: tab.reportReady ? App.Theme.accent : App.Theme.textSecondary
                                font.pixelSize: 20
                                font.weight: Font.Black
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Btrfs compression analysis")
                                color: App.Theme.text
                                font.pixelSize: 18
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Analysis is read-only. Compression starts only after a complete safety check and your explicit confirmation.")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        StatusBadge {
                            text: tab.analysisActive ? qsTr("Analyzing")
                                  : tab.reportReady ? qsTr("Analyzed")
                                  : qsTr("Not analyzed")
                            status: tab.analysisActive ? "analyzing"
                                    : tab.reportReady ? "completed" : "not checked"
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        AppButton {
                            objectName: "analyzeCompressionButton"
                            text: tab.analysisActive ? qsTr("Analyzing…") : qsTr("Analyze compression")
                            iconText: "⌕"
                            kind: "primary"
                            busy: tab.analysisActive
                            enabled: Boolean(tab.gameId.length > 0
                                             && tab.analysisAllowed
                                             && !tab.analysisActive)
                            onClicked: {
                                if (tab.controller && tab.controller.analyzeGame)
                                    tab.controller.analyzeGame(tab.gameId)
                            }
                        }

                        AppButton {
                            visible: tab.analysisActive
                            text: qsTr("Cancel analysis")
                            iconText: "×"
                            kind: "danger"
                            onClicked: {
                                if (tab.controller && tab.controller.cancelTask)
                                    tab.controller.cancelTask(String(tab.analysisTask.id || ""))
                            }
                        }

                        AppButton {
                            objectName: "verifyCompressionButton"
                            text: tab.verificationActive
                                  ? qsTr("Verifying…")
                                  : qsTr("Verify compression")
                            iconText: "✓"
                            kind: "secondary"
                            busy: tab.verificationActive
                            enabled: Boolean(tab.gameId.length > 0
                                             && tab.analysisAllowed
                                             && !tab.verificationActive
                                             && (tab.confirmedBtrfs
                                                 || String(tab.value(
                                                               ["filesystem"], ""))
                                                    .toLowerCase() === "btrfs"))
                            onClicked: {
                                if (tab.controller
                                        && tab.controller.verifyCompression)
                                    tab.controller.verifyCompression(tab.gameId)
                            }
                        }

                        Label {
                            Layout.fillWidth: true
                            text: tab.analysisExplanation()
                            color: tab.profilesEnabled ? App.Theme.success
                                  : tab.reportReady && !tab.report.is_btrfs ? App.Theme.warning
                                  : App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }

                    ColumnLayout {
                        visible: tab.analysisActive
                        Layout.fillWidth: true
                        spacing: 6

                        AppProgressBar {
                            Layout.fillWidth: true
                            value: Number(tab.analysisTask.progress || 0)
                        }
                        Label {
                            text: qsTr("%1 files scanned · %2 sampled · %3 s")
                                  .arg(Number(tab.analysisTask.scannedFiles || 0))
                                  .arg(tab.formatBytes(Number(tab.analysisTask.analyzedBytes || 0)))
                                  .arg(Math.round(Number(tab.analysisTask.elapsedSeconds || 0)))
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }
                    }

                    SurfaceCard {
                        id: measuredResultCard
                        objectName: "compressionMeasurementCard"
                        Layout.fillWidth: true
                        padding: 13

                        property var beforeMeasurement: tab.resultMeasurement(["before"])
                        property var afterMeasurement: tab.displayedAfterMeasurement()
                        property bool hasCurrentMeasurement: afterMeasurement
                                                             && Object.keys(
                                                                 afterMeasurement).length > 0
                        property bool completeCurrentMeasurement:
                            tab.hasCompleteCompsizeMeasurement(afterMeasurement)
                        property bool verificationContext: tab.hasVerificationTask

                        contentItem: ColumnLayout {
                            spacing: 9

                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Measured compression result")
                                    color: App.Theme.text
                                    font.weight: Font.Bold
                                }
                                StatusBadge {
                                    objectName: "compressionMeasurementStatus"
                                    text: tab.verificationActive
                                          ? qsTr("Measuring")
                                          : tab.verificationFailed
                                            ? qsTr("Measurement failed")
                                          : measuredResultCard.verificationContext
                                            && !tab.verificationSucceeded
                                            ? qsTr("Not available")
                                          : measuredResultCard.completeCurrentMeasurement
                                            ? qsTr("Measured") : qsTr("Not measured")
                                    status: tab.verificationActive
                                            ? "analyzing"
                                            : tab.verificationFailed
                                              ? "failed"
                                            : measuredResultCard.verificationContext
                                              && !tab.verificationSucceeded
                                              ? "unavailable"
                                            : measuredResultCard.completeCurrentMeasurement
                                              ? "completed" : "not checked"
                                }
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: tab.width >= 900 ? 3 : tab.width >= 620 ? 2 : 1
                                rowSpacing: 9
                                columnSpacing: 12

                                LabeledValue {
                                    objectName: "measuredLogicalSize"
                                    Layout.fillWidth: true
                                    label: qsTr("File size reported by the application scanner")
                                    value: tab.formatBytes(tab.scannerLogicalBytes())
                                }
                                LabeledValue {
                                    objectName: "measuredUncompressedExtents"
                                    Layout.fillWidth: true
                                    label: qsTr("Uncompressed extent size (compsize)")
                                    value: measuredResultCard.completeCurrentMeasurement
                                           ? tab.formatBytes(tab.measurementValue(
                                                                 measuredResultCard.afterMeasurement,
                                                                 ["compsizeUncompressedBytes",
                                                                  "compsize_uncompressed_bytes"],
                                                                 null))
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredCurrentPhysical"
                                    Layout.fillWidth: true
                                    label: qsTr("Current physical usage (compsize)")
                                    value: measuredResultCard.completeCurrentMeasurement
                                           ? tab.formatBytes(tab.measurementValue(
                                                                 measuredResultCard.afterMeasurement,
                                                                 ["compsizeDiskBytes",
                                                                  "compsize_disk_bytes"],
                                                                 null))
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredCurrentEffect"
                                    Layout.fillWidth: true
                                    label: qsTr("Current saving for active files")
                                    value: measuredResultCard.completeCurrentMeasurement
                                           ? tab.formatSignedBytes(
                                                 tab.activeCompressionEffect())
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "compressionClassification"
                                    Layout.fillWidth: true
                                    label: qsTr("Compression classification")
                                    value: App.I18n.compressionClassification(
                                               tab.value([
                                                   "compressionClassificationKey"
                                               ], "measurement_unavailable"))
                                }
                                LabeledValue {
                                    objectName: "lastGameOptimizationOperationReclaimed"
                                    Layout.fillWidth: true
                                    label: qsTr("Reclaimed during the last Game Optimization operation")
                                    value: Boolean(tab.value([
                                                       "lastOperationReclaimedAvailable"
                                                   ], false))
                                           ? tab.formatSignedBytes(tab.value([
                                                 "lastOperationReclaimedBytes"
                                             ], null))
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredCurrentStatus"
                                    Layout.fillWidth: true
                                    label: qsTr("Source")
                                    value: measuredResultCard.completeCurrentMeasurement
                                           ? qsTr("Measurement (compsize)")
                                           : tab.verificationFailed
                                             ? qsTr("Measurement failed")
                                             : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredCurrentStatusValue"
                                    Layout.fillWidth: true
                                    label: qsTr("Measurement status")
                                    value: measuredResultCard.completeCurrentMeasurement
                                           ? qsTr("Measured")
                                           : tab.verificationFailed
                                             ? qsTr("Measurement failed")
                                             : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredPhysicalBefore"
                                    visible: !measuredResultCard.verificationContext
                                    Layout.fillWidth: true
                                    label: qsTr("Physical usage before")
                                    value: tab.authoritativeResult()
                                           ? tab.formatBytes(tab.measurementValue(
                                                                 measuredResultCard.beforeMeasurement,
                                                                 ["compsizeDiskBytes",
                                                                  "compsize_disk_bytes"],
                                                                 null))
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredPhysicalAfter"
                                    visible: !measuredResultCard.verificationContext
                                    Layout.fillWidth: true
                                    label: qsTr("Physical usage after")
                                    value: measuredResultCard.hasCurrentMeasurement
                                           ? tab.formatBytes(tab.measurementValue(
                                                                 measuredResultCard.afterMeasurement,
                                                                 ["compsizeDiskBytes",
                                                                  "compsize_disk_bytes"],
                                                                 null))
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredActiveEffect"
                                    visible: false
                                    Layout.fillWidth: true
                                    label: qsTr("Total compression effect for active files")
                                    value: tab.formatSignedBytes(
                                               tab.activeCompressionEffect())
                                }
                                LabeledValue {
                                    objectName: "measuredOperationReclaimed"
                                    visible: !measuredResultCard.verificationContext
                                    Layout.fillWidth: true
                                    label: qsTr("Actually reclaimed during this operation")
                                    value: tab.authoritativeResult()
                                           ? tab.formatSignedBytes(tab.resultValue(
                                                                      ["actualSavedBytes",
                                                                       "actual_saved_bytes"],
                                                                      null))
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "measuredFilesystemDelta"
                                    visible: !measuredResultCard.verificationContext
                                    Layout.fillWidth: true
                                    label: qsTr("Whole-filesystem free-space change")
                                    value: tab.authoritativeResult()
                                           ? tab.formatSignedBytes(tab.resultValue(
                                                                      ["filesystemFreeDeltaBytes",
                                                                       "filesystem_free_delta_bytes"],
                                                                      null))
                                           : qsTr("Not available")
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: measuredResultCard.verificationContext
                                      ? qsTr("This is a read-only current-state measurement. No before/after operation took place.")
                                      : qsTr("compsize measures the active data of this game. The whole-filesystem statvfs change is auxiliary and may include unrelated writes.")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Current total saving may predate Game Optimization. Only a valid compsize before/after pair is attributed to the last Game Optimization operation.")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }

                            Label {
                                objectName: "measurementFailureMessage"
                                Layout.fillWidth: true
                                visible: tab.verificationFailed
                                         || (!measuredResultCard.verificationContext
                                             && tab.latestCompressionResult
                                          && Object.keys(
                                              tab.latestCompressionResult).length > 0
                                          && !tab.authoritativeResult())
                                text: tab.verificationFailed
                                      ? qsTr("Privileged measurement failed: %1")
                                        .arg(String(tab.verificationTask.error || ""))
                                      : qsTr("The actual saving cannot be claimed because a complete privileged compsize measurement before and after is unavailable.")
                                color: App.Theme.warning
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    SurfaceCard {
                        objectName: "benchmarkEstimateCard"
                        Layout.fillWidth: true
                        padding: 13

                        contentItem: ColumnLayout {
                            spacing: 9

                            Label {
                                Layout.fillWidth: true
                                text: tab.benchmarkEstimateAvailable
                                      ? qsTr("Current build estimate")
                                      : qsTr("No current estimate")
                                color: tab.benchmarkEstimateAvailable
                                       ? App.Theme.text : App.Theme.warning
                                font.weight: Font.Bold
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: tab.width >= 800 ? 3 : 1
                                rowSpacing: 8
                                columnSpacing: 10

                                LabeledValue {
                                    objectName: "benchmarkCurrentSaving"
                                    Layout.fillWidth: true
                                    label: qsTr("Current measured saving")
                                    value: measuredResultCard.completeCurrentMeasurement
                                           ? tab.formatBytes(
                                                 tab.activeCompressionEffect())
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "benchmarkTotalPotential"
                                    Layout.fillWidth: true
                                    label: qsTr("Estimated total potential for ZSTD-%1")
                                           .arg(tab.selectedBenchmarkLevel)
                                    value: tab.selectedBenchmarkProjectionAvailable
                                           ? tab.formatBytes(
                                                 tab.selectedBenchmarkProjection.estimatedTotalPotentialBytes)
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "benchmarkAdditionalSaving"
                                    Layout.fillWidth: true
                                    label: qsTr("Estimated additional saving from the current state")
                                    value: tab.selectedBenchmarkProjectionAvailable
                                           ? tab.formatBytes(
                                                 tab.selectedBenchmarkProjection.estimatedAdditionalSavingBytes)
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "benchmarkEstimateSize"
                                    Layout.fillWidth: true
                                    label: qsTr("Estimated physical size after operation")
                                    value: tab.selectedBenchmarkProjectionAvailable
                                           ? tab.formatBytes(
                                                 tab.selectedBenchmarkProjection.estimatedPhysicalBytes)
                                           : qsTr("Not available")
                                }
                                LabeledValue {
                                    objectName: "benchmarkEstimateSource"
                                    Layout.fillWidth: true
                                    label: qsTr("Source")
                                    value: tab.selectedBenchmarkProjectionAvailable
                                           ? qsTr("Measurement (compsize) + matching-build estimate")
                                           : measuredResultCard.completeCurrentMeasurement
                                             ? qsTr("Measurement (compsize); estimate unavailable")
                                             : qsTr("Not available")
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("The estimate is normalized to the current compsize Uncompressed value. It does not guarantee an identical change in free space for the whole filesystem.")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }

                            Rectangle {
                                objectName: "lowBenefitWarning"
                                visible: tab.lowAdditionalBenefit
                                Layout.fillWidth: true
                                Layout.preferredHeight: lowBenefitText.implicitHeight + 24
                                radius: App.Theme.radiusMedium
                                color: App.Theme.warningSoft
                                border.width: 1
                                border.color: App.Theme.warning

                                Label {
                                    id: lowBenefitText
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    text: qsTr("Low additional benefit: recompression may rewrite files for a long time for little additional space saving. Manual compression remains available, but it requires an extra confirmation.")
                                    color: App.Theme.warning
                                    font.weight: Font.DemiBold
                                    font.pixelSize: App.Theme.fontCaption
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: tab.width >= 880 ? 4 : 2
                        rowSpacing: 9
                        columnSpacing: 9

                        Repeater {
                            model: tab.modes

                            delegate: Button {
                                id: modeButton
                                objectName: "compressionProfileButton_" + modelData.key
                                required property var modelData
                                property bool active: tab.selectedMode === modelData.key
                                Layout.fillWidth: true
                                Layout.preferredHeight: 116
                                padding: 13
                                focusPolicy: Qt.StrongFocus
                                enabled: tab.profilesEnabled
                                Accessible.name: modelData.name
                                onClicked: tab.selectedMode = modelData.key

                                contentItem: ColumnLayout {
                                    spacing: 5
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            text: modeButton.modelData.symbol
                                            color: modeButton.active ? App.Theme.accent : App.Theme.textSecondary
                                            font.pixelSize: 16
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: modeButton.modelData.name
                                            color: modeButton.enabled ? App.Theme.text : App.Theme.textMuted
                                            font.pixelSize: App.Theme.fontBody
                                            font.weight: Font.Bold
                                        }
                                        Label {
                                            text: modeButton.modelData.level
                                            color: App.Theme.textMuted
                                            font.pixelSize: 10
                                            font.family: "monospace"
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        text: modeButton.modelData.description
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }
                                }

                                background: Rectangle {
                                    radius: App.Theme.radiusMedium
                                    color: modeButton.active && modeButton.enabled ? App.Theme.accentSoft
                                          : modeButton.hovered && modeButton.enabled ? App.Theme.surfaceHover
                                          : App.Theme.surfaceRaised
                                    border.width: modeButton.visualFocus || (modeButton.active && modeButton.enabled) ? 2 : 1
                                    border.color: modeButton.active && modeButton.enabled ? App.Theme.accent : App.Theme.border
                                    opacity: modeButton.enabled ? 1.0 : 0.62
                                }
                            }
                        }
                    }

                    Label {
                        visible: tab.reportReady && tab.selectedMode === "Auto"
                        Layout.fillWidth: true
                        text: tab.autoSelectionExplanation()
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    GridLayout {
                        visible: tab.reportReady
                        Layout.fillWidth: true
                        columns: tab.width >= 820 ? 4 : 2
                        rowSpacing: 10
                        columnSpacing: 10

                        MetricTile {
                            Layout.fillWidth: true
                            label: qsTr("Current disk usage")
                            value: tab.formatBytes(tab.currentDiskBytes())
                            symbol: "▣"
                            tone: App.Theme.info
                        }
                        MetricTile {
                            Layout.fillWidth: true
                            label: qsTr("Current compression effect")
                            value: App.I18n.compressionClassification(
                                       tab.value(["compressionClassificationKey"],
                                                 "measurement_unavailable"))
                            symbol: "◉"
                            tone: App.Theme.accent
                        }
                        MetricTile {
                            Layout.fillWidth: true
                            label: qsTr("Estimated physical size after operation")
                            value: tab.selectedBenchmarkProjectionAvailable
                                   ? tab.formatBytes(
                                         tab.selectedBenchmarkProjection.estimatedPhysicalBytes)
                                   : qsTr("Not available")
                            symbol: "▽"
                            tone: App.Theme.secondary
                        }
                        MetricTile {
                            Layout.fillWidth: true
                            label: qsTr("Estimated additional saving")
                            value: tab.selectedBenchmarkProjectionAvailable
                                   ? tab.formatBytes(
                                         tab.selectedBenchmarkProjection.estimatedAdditionalSavingBytes)
                                   : qsTr("Not available")
                            symbol: "↓"
                            tone: App.Theme.success
                        }
                        MetricTile {
                            objectName: "profitabilityMetric"
                            Layout.fillWidth: true
                            label: qsTr("Profitability")
                            value: tab.selectedBenchmarkProjectionAvailable
                                   ? tab.lowAdditionalBenefit
                                     ? qsTr("Low benefit")
                                     : qsTr("Worthwhile")
                                   : qsTr("Not estimated")
                            symbol: "◇"
                            tone: App.Theme.warning
                        }
                        MetricTile {
                            objectName: "estimatedRewriteMetric"
                            Layout.fillWidth: true
                            label: qsTr("Estimated data to rewrite")
                            value: tab.formatBytes(tab.estimatedRewriteBytes())
                            symbol: "↻"
                            tone: App.Theme.secondary
                        }
                    }

                    GridLayout {
                        visible: tab.reportReady
                        Layout.fillWidth: true
                        columns: tab.width >= 860 ? 4 : 2
                        rowSpacing: 10
                        columnSpacing: 14

                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Filesystem")
                            value: String(tab.report.filesystem || qsTr("Unknown"))
                            mono: true
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Mount point")
                            value: String(tab.report.mount_point || "-")
                            mono: true
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Estimated time")
                            value: tab.formatDuration(tab.selectedEstimate.estimated_time_low_seconds,
                                                      tab.selectedEstimate.estimated_time_high_seconds)
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("CPU usage")
                            value: tab.translatedCpu(String(tab.selectedEstimate.cpu_usage || ""))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Files and directories")
                            value: qsTr("%1 files · %2 directories")
                                   .arg(Number(tab.report.file_count || 0))
                                   .arg(Number(tab.report.directory_count || 0))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Symbolic links")
                            value: String(Number(tab.report.symlink_count || 0))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Available space")
                            value: tab.formatBytes(tab.report.available_bytes)
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Compression measurement")
                            value: App.I18n.message(String(
                                       tab.compsize.message || qsTr("Not run")))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("File size reported by the application scanner")
                            value: tab.formatBytes(tab.scannerLogicalBytes())
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Uncompressed extent size (compsize)")
                            value: measuredResultCard.completeCurrentMeasurement
                                   ? tab.formatBytes(tab.measurementValue(
                                                         measuredResultCard.afterMeasurement,
                                                         ["compsizeUncompressedBytes",
                                                          "compsize_uncompressed_bytes"],
                                                         null))
                                   : qsTr("Not available")
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Current physical usage (compsize)")
                            value: tab.formatBytes(tab.currentDiskBytes())
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Writable")
                            value: tab.yesNoUnknown(tab.report.writable)
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Existing compression")
                            value: App.I18n.status(String(tab.report.existing_compression_state || "unknown"))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Shared extents / reflinks")
                            value: App.I18n.status(tab.sharedExtentState)
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Sampled data")
                            value: tab.formatBytes(tab.report.sampled_bytes)
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Sample codec")
                            value: String(tab.report.sampling_codec || qsTr("Not available"))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Read errors")
                            value: String(tab.report.permission_errors
                                          ? tab.report.permission_errors.length : 0)
                        }
                    }

                    Rectangle {
                        visible: tab.reportReady && tab.confirmedBtrfs
                        Layout.fillWidth: true
                        Layout.preferredHeight: sharedDetails.implicitHeight + 28
                        radius: App.Theme.radiusMedium
                        color: tab.sharedExtentsDetected ? App.Theme.dangerSoft
                              : tab.sharedExtentsClear ? App.Theme.backgroundElevated
                              : App.Theme.warningSoft
                        border.width: 1
                        border.color: tab.sharedExtentsDetected ? App.Theme.danger
                                    : tab.sharedExtentsClear ? App.Theme.border
                                    : App.Theme.warning

                        ColumnLayout {
                            id: sharedDetails
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 10

                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Btrfs shared-extent safety check")
                                    color: App.Theme.text
                                    font.weight: Font.Bold
                                }
                                StatusBadge {
                                    text: App.I18n.status(tab.sharedExtentState)
                                    status: tab.sharedExtentsClear ? "available"
                                            : tab.sharedExtentsDetected ? "error"
                                            : "warning"
                                }
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: tab.width >= 820 ? 4 : 2
                                rowSpacing: 8
                                columnSpacing: 14

                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Total")
                                    value: tab.formatBytes(tab.duValue(
                                                               ["total_bytes", "totalBytes"],
                                                               null))
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Exclusive")
                                    value: tab.formatBytes(tab.duValue(
                                                               ["exclusive_bytes", "exclusiveBytes"],
                                                               null))
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Set shared")
                                    value: tab.formatBytes(tab.duValue(
                                                               ["set_shared_bytes", "setSharedBytes"],
                                                               null))
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Possible allocation growth")
                                    value: tab.formatBytes(tab.duValue(
                                                               ["estimated_growth_bytes",
                                                                "estimatedGrowthBytes"],
                                                               null))
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: tab.sharedExtentsDetected
                                      ? qsTr("Shared extents or snapshots were detected. Manual recompression is available only after reviewing the warning; it breaks sharing for processed data and can increase physical usage by up to %1 before old snapshots expire.")
                                          .arg(tab.formatBytes(tab.duValue(
                                                                   ["estimated_growth_bytes",
                                                                    "estimatedGrowthBytes"],
                                                                   null)))
                                      : tab.sharedExtentsClear
                                        ? qsTr("No shared extents were detected by btrfs filesystem du. The result will be checked again immediately before compression.")
                                        : qsTr("Shared-extent risk could not be measured reliably. Compression remains blocked (fail closed). %1")
                                          .arg(App.I18n.message(String(
                                                   tab.duValue(["message"], "")
                                                   || qsTr("Measurement unavailable"))))
                                color: tab.sharedExtentsClear ? App.Theme.textSecondary
                                      : tab.sharedExtentsDetected ? App.Theme.danger
                                      : App.Theme.warning
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Rectangle {
                        visible: !measuredResultCard.completeCurrentMeasurement
                                 && tab.reportReady
                                 && Boolean(tab.compsize.available)
                        Layout.fillWidth: true
                        Layout.preferredHeight: compsizeDetails.implicitHeight + 28
                        radius: App.Theme.radiusMedium
                        color: App.Theme.backgroundElevated
                        border.width: 1
                        border.color: App.Theme.border

                        ColumnLayout {
                            id: compsizeDetails
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 10

                            Label {
                                text: qsTr("Analysis-time compsize snapshot")
                                color: App.Theme.text
                                font.weight: Font.Bold
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: tab.width >= 820 ? 3 : 2
                                rowSpacing: 8
                                columnSpacing: 14

                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Disk usage")
                                    value: tab.formatBytes(tab.compsize.disk_usage_bytes)
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Uncompressed size")
                                    value: tab.formatBytes(tab.compsize.uncompressed_bytes)
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Referenced size")
                                    value: tab.formatBytes(tab.compsize.referenced_bytes)
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Current compression ratio")
                                    value: tab.ratioLabel()
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Compression types")
                                    value: tab.compressionTypes()
                                    mono: true
                                }
                                LabeledValue {
                                    Layout.fillWidth: true
                                    label: qsTr("Space already saved")
                                    value: tab.formatBytes(tab.compsize.saved_bytes)
                                }
                            }
                        }
                    }

                    Rectangle {
                        visible: tab.showRunningWarning
                        Layout.fillWidth: true
                        Layout.preferredHeight: runningWarning.implicitHeight + 24
                        radius: App.Theme.radiusMedium
                        color: App.Theme.warningSoft
                        Label {
                            id: runningWarning
                            anchors.fill: parent
                            anchors.margins: 12
                            text: qsTr("The game appears to be running. Analysis is read-only, but any future operation must wait until it exits.")
                            color: App.Theme.warning
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }

                    ColumnLayout {
                        visible: tab.hasWarnings
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: qsTr("Warnings")
                            color: App.Theme.text
                            font.weight: Font.Bold
                        }
                        Repeater {
                            model: tab.hasWarnings ? tab.report.warnings : []
                            delegate: Label {
                                required property string modelData
                                Layout.fillWidth: true
                                text: "• " + App.I18n.message(modelData)
                                color: App.Theme.warning
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Compress Game")
                                color: App.Theme.text
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: tab.preparingPlan
                                      ? qsTr("Preparing a read-only plan…")
                                      : tab.compressionBlocker()
                                color: tab.compressPrerequisitesReady
                                       ? App.Theme.success : App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                        AppButton {
                            objectName: "compressGameButton"
                            text: qsTr("Compress Game")
                            iconText: "↓"
                            kind: "primary"
                            busy: tab.preparingPlan
                            enabled: Boolean(tab.compressPrerequisitesReady
                                             && !tab.preparingPlan)
                            onClicked: tab.prepareCompressionPlan()
                        }
                    }

                    ColumnLayout {
                        visible: tab.compressionHistory.length > 0
                        Layout.fillWidth: true
                        spacing: 9

                        Divider { Layout.fillWidth: true }

                        Label {
                            text: qsTr("Compression history")
                            color: App.Theme.text
                            font.pixelSize: App.Theme.fontBodyLarge
                            font.weight: Font.Bold
                        }

                        Repeater {
                            model: tab.compressionHistory

                            delegate: Rectangle {
                                id: historyRow
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: historyContent.implicitHeight + 22
                                radius: App.Theme.radiusMedium
                                color: App.Theme.backgroundElevated
                                border.width: 1
                                border.color: App.Theme.border

                                ColumnLayout {
                                    id: historyContent
                                    anchors.fill: parent
                                    anchors.margins: 11
                                    spacing: 7

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8

                                        Label {
                                            Layout.fillWidth: true
                                            text: tab.formatTimestamp(tab.historyValue(
                                                                          historyRow.modelData,
                                                                          ["completedAt", "completed_at",
                                                                           "createdAt", "created_at"], ""))
                                            color: App.Theme.text
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                        }

                                        StatusBadge {
                                            text: App.I18n.status(String(tab.historyValue(
                                                                             historyRow.modelData,
                                                                             ["status"], "unknown")))
                                            status: String(tab.historyValue(
                                                               historyRow.modelData,
                                                               ["status"], "unknown"))
                                                    .toLowerCase()
                                        }
                                    }

                                    GridLayout {
                                        Layout.fillWidth: true
                                        columns: tab.width >= 760 ? 4 : 2
                                        rowSpacing: 7
                                        columnSpacing: 12

                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Profile")
                                            value: App.I18n.profile(String(tab.historyValue(
                                                                              historyRow.modelData,
                                                                              ["profile"], "-")))
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Physical before (compsize)")
                                            value: tab.historyValue(
                                                       historyRow.modelData,
                                                       ["measurementAuthoritative",
                                                        "measurement_authoritative"],
                                                       false) === true
                                                   ? tab.formatBytes(tab.historyValue(
                                                                       tab.historyValue(
                                                                           historyRow.modelData,
                                                                           ["before"], ({})),
                                                                       ["compsizeDiskBytes",
                                                                        "compsize_disk_bytes"],
                                                                       null))
                                                   : qsTr("Not available")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Physical after (compsize)")
                                            value: tab.historyValue(
                                                       historyRow.modelData,
                                                       ["measurementAuthoritative",
                                                        "measurement_authoritative"],
                                                       false) === true
                                                   ? tab.formatBytes(tab.historyValue(
                                                                       tab.historyValue(
                                                                           historyRow.modelData,
                                                                           ["after"], ({})),
                                                                       ["compsizeDiskBytes",
                                                                        "compsize_disk_bytes"],
                                                                       null))
                                                   : qsTr("Not available")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Actual savings")
                                            value: tab.historyValue(
                                                       historyRow.modelData,
                                                       ["measurementAuthoritative",
                                                        "measurement_authoritative"],
                                                       false) === true
                                                   ? tab.formatSignedBytes(
                                                         tab.historyValue(
                                                             historyRow.modelData,
                                                             ["actualSavedBytes",
                                                              "actual_saved_bytes"],
                                                             null))
                                                   : qsTr("Not available")
                                        }
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        visible: tab.historyValue(
                                                     historyRow.modelData,
                                                     ["measurementAuthoritative",
                                                      "measurement_authoritative"],
                                                     false) !== true
                                                 || tab.historyValue(
                                                     historyRow.modelData,
                                                     ["actualSavedBytes",
                                                      "actual_saved_bytes"],
                                                     null) === null
                                        text: qsTr("Actual savings could not be measured")
                                        color: App.Theme.warning
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        text: qsTr("Before: %1").arg(
                                                  tab.measurementSummary(
                                                      tab.historyValue(
                                                          historyRow.modelData,
                                                          ["before"], ({}))))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        text: qsTr("After: %1").arg(
                                                  tab.measurementSummary(
                                                      tab.historyValue(
                                                          historyRow.modelData,
                                                          ["after"], ({}))))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        visible: String(tab.historyValue(
                                                            historyRow.modelData,
                                                            ["verificationState",
                                                             "verification_state"], "")).length > 0
                                        text: qsTr("Verification: %1").arg(
                                                  App.I18n.status(String(
                                                      tab.historyValue(
                                                          historyRow.modelData,
                                                          ["verificationState",
                                                           "verification_state"],
                                                          qsTr("Unknown")))))
                                        color: App.Theme.textMuted
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        visible: tab.historyMessage(
                                                     historyRow.modelData).length > 0
                                        text: tab.historyMessage(
                                                  historyRow.modelData)
                                        color: App.Theme.warning
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                padding: 20
                elevated: true

                contentItem: RowLayout {
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Label {
                            text: qsTr("Deep Optimize")
                            color: App.Theme.text
                            font.pixelSize: 18
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Recursive directory defragmentation remains disabled. Game Optimization processes verified files individually and requires manual confirmation when snapshots or reflinks make data shared.")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }
                    StatusBadge { text: qsTr("Planned"); status: "paused" }
                }
            }

            Item { Layout.preferredHeight: 12 }
        }
    }

    ConfirmDialog {
        id: profitabilityConfirm
        objectName: "profitabilityConfirmDialog"
        onConfirmed: function(planIdentifier) {
            if (String(planIdentifier || "") !== tab.planId(tab.preparedPlan)) {
                tab.toastRequested(qsTr("The compression plan is no longer available."), "error")
                return
            }
            tab.openFinalCompressionConfirmation(tab.preparedPlan)
        }
    }

    ConfirmDialog {
        id: compressionConfirm
        objectName: "compressionConfirmDialog"
        onConfirmed: function(planIdentifier) {
            var identifier = String(planIdentifier || "")
            if (identifier.length === 0
                    || !tab.controller
                    || typeof tab.controller.startCompression !== "function") {
                tab.toastRequested(qsTr("The compression plan is no longer available."), "error")
                return
            }
            if (!tab.controller.startCompression(identifier))
                tab.toastRequested(qsTr("Compression could not be started. Review the latest safety checks."), "error")
        }
    }
}
