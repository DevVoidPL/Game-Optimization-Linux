pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab
    signal toastRequested(string message, string tone)
    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData && gameData.id || "")
    readonly property bool localGame: Boolean(gameData && gameData.localGame)
    property bool loading: false
    property bool dirty: false
    property string errorMessage: ""
    property string appId: ""
    property string preset: "automatic"
    property string gameCategory: "unknown"
    property string userGoal: "stable_image"
    property string targetDisplayId: ""
    property string targetFpsMode: "automatic"
    property int targetFps: 60
    property bool gamemodeEnabled: false
    property bool gamescopeEnabled: false
    property string gamescopeMode: "disabled"
    property int inputWidth: 1920
    property int inputHeight: 1080
    property int outputWidth: 1920
    property int outputHeight: 1080
    property int refreshRate: 60
    property bool gamescopeFullscreen: true
    property string gamescopeScaler: "auto"
    property string gamescopeFilter: "linear"
    property var manualOverrides: ({})
    property var displays: []
    property var recommendation: ({})
    property var presetPlan: ({})
    property var categoryClassification: ({})
    property bool recommendationAnalyzed: false
    property var gamemodeStatus: ({ "available": false })
    property var gamescopeStatus: ({ "available": false })
    property var runnerStatus: ({ "installed": false })
    property string launchPlanText: "%command%"
    property var launchPlan: ({})
    property string steamLaunchCommand: ""
    property string renderingSummary: ""
    property string fpsLimitOwner: "none"
    property var protonOverrides: []
    property bool showSteamInstructions: false
    property var gameAnalysis: ({ "status": "not_analyzed" })
    property var runnerPreflight: ({})
    property var settingPreview: ({})
    property bool automaticPreviewOpen: false

    readonly property var presetValues: ["automatic", "maximum_performance", "balanced", "quiet", "custom"]
    readonly property var presetLabels: [qsTr("Automatic"), qsTr("Maximum Performance"), qsTr("Balanced"), qsTr("Quiet"), qsTr("Custom")]
    readonly property var categoryValues: ["competitive", "fast_action", "cinematic", "platformer_2d", "strategy_simulation", "retro", "unknown", "custom"]
    readonly property var categoryLabels: [qsTr("Competitive"), qsTr("Fast action"), qsTr("Cinematic single-player"), qsTr("Platformer / 2D"), qsTr("Strategy / simulation"), qsTr("Retro"), qsTr("Unknown"), qsTr("Custom")]
    readonly property var goalValues: ["lowest_latency", "stable_image", "best_quality", "low_power", "custom"]
    readonly property var goalLabels: [qsTr("Lowest latency"), qsTr("Stable image"), qsTr("Best quality"), qsTr("Low power"), qsTr("Custom")]
    readonly property var gamescopeModeValues: ["disabled", "automatic", "native", "performance", "quality", "custom"]
    readonly property var gamescopeModeLabels: [qsTr("Disabled"), qsTr("Automatic"), qsTr("Native"), qsTr("Performance"), qsTr("Quality"), qsTr("Custom")]
    readonly property var displayValues: displays.map(function(item) { return String(item.id || "") })
    readonly property var displayLabels: displays.map(function(item) { return String(item.label || item.name || "") })
    readonly property var draft: ({
        "preset": preset, "gameCategory": gameCategory, "userGoal": userGoal,
        "targetDisplayId": targetDisplayId, "targetFpsMode": targetFpsMode,
        "targetFps": targetFps, "gamemodeEnabled": gamemodeEnabled,
        "gamescopeEnabled": gamescopeEnabled, "gamescopeMode": gamescopeMode,
        "gamescopeInputWidth": inputWidth, "gamescopeInputHeight": inputHeight,
        "gamescopeOutputWidth": outputWidth, "gamescopeOutputHeight": outputHeight,
        "gamescopeRefreshRate": refreshRate, "gamescopeFullscreen": gamescopeFullscreen,
        "gamescopeScaler": gamescopeScaler, "gamescopeFilter": gamescopeFilter,
        "manualOverrides": manualOverrides
    })

    function indexOf(values, wanted, fallback) {
        var index = values.indexOf(wanted)
        return index >= 0 ? index : fallback
    }
    function changed(key) {
        if (loading) return
        dirty = true
        if (key) {
            var overrides = Object.assign({}, manualOverrides)
            overrides[key] = true
            manualOverrides = overrides
        }
        previewTimer.restart()
    }
    function choosePreset(value) {
        loading = true
        preset = value
        var preservedOverrides = ({})
        if (manualOverrides.category)
            preservedOverrides.category = true
        if (manualOverrides.display)
            preservedOverrides.display = true
        manualOverrides = preservedOverrides
        loading = false
        dirty = true
        recommendationAnalyzed = false
        previewTimer.restart()
    }
    function selectedDisplay() {
        var index = displayValues.indexOf(targetDisplayId)
        return index >= 0 && index < displays.length ? displays[index] : null
    }
    function fpsOwnerLabel() {
        if (fpsLimitOwner === "gamescope") return "Gamescope"
        if (fpsLimitOwner === "mangohud") return "MangoHud"
        return qsTr("None")
    }
    function frameRateLimitLabel() {
        var result = gameAnalysis.frameRate || ({})
        if (result.state === "likely_capped" && result.estimatedCeilingFps !== null)
            return qsTr("Likely ~%1 FPS").arg(Number(result.estimatedCeilingFps).toFixed(0))
        if (result.state === "not_detected")
            return qsTr("Not detected")
        return qsTr("Unknown")
    }
    function emptyRecommendationMessage() {
        var state = String(gameAnalysis.settingsAnalysis
                           && gameAnalysis.settingsAnalysis.recommendationState || "")
        if (state === "unsupported_engine")
            return qsTr("Automatic graphics settings optimization is not available for this game yet.")
        if (state === "missing_config")
            return qsTr("No supported existing configuration file was found.")
        if (state === "invalid_config")
            return qsTr("Configuration files were invalid or unsupported.")
        if (state === "no_supported_settings")
            return qsTr("No safely modifiable settings were found")
        if (state === "baseline_missing")
            return qsTr("A representative baseline is required before recommending a settings change.")
        if (state === "baseline_unrepresentative")
            return qsTr("The saved baseline is not representative enough for measured settings recommendations.")
        if (state === "baseline_stale")
            return qsTr("The saved baseline predates the current graphics settings. Record a new baseline before using measured Automatic recommendations.")
        if (state === "bottleneck_low_confidence")
            return qsTr("Bottleneck confidence is too low for a conservative settings change.")
        if (state === "capped_with_headroom")
            return qsTr("No graphics reductions are recommended because the game appears frame-limited and the hardware has available headroom.")
        if (state === "balanced")
            return qsTr("No graphics reductions are recommended because the measured workload is balanced.")
        if (state === "no_matching_setting")
            return qsTr("No supported existing setting matches the measured bottleneck.")
        return qsTr("No safe optimization recommendations were found")
    }
    function applyDisplayDefaults() {
        var display = selectedDisplay()
        if (!display) return
        outputWidth = Number(display.width || outputWidth)
        outputHeight = Number(display.height || outputHeight)
        refreshRate = Math.round(Number(display.refreshRate || refreshRate))
    }
    function applyGamescopeMode(value) {
        gamescopeMode = value
        gamescopeEnabled = value !== "disabled"
        applyDisplayDefaults()
        if (value === "native" || value === "quality") {
            inputWidth = outputWidth
            inputHeight = outputHeight
        } else if (value === "performance") {
            inputWidth = Math.max(320, Math.round(outputWidth * 0.75 / 2) * 2)
            inputHeight = Math.max(320, Math.round(outputHeight * 0.75 / 2) * 2)
        }
        changed("gamescope")
    }
    function applyResult(result) {
        loading = true
        errorMessage = String(result && result.error || "")
        if (!result || !result.success) { loading = false; return }
        appId = String(result.appId || "")
        preset = String(result.preset || "automatic")
        gameCategory = String(result.gameCategory || "unknown")
        userGoal = String(result.userGoal || "stable_image")
        targetDisplayId = String(result.targetDisplayId || "")
        targetFpsMode = String(result.targetFpsMode || "automatic")
        targetFps = Number(result.targetFps || 60)
        gamemodeEnabled = Boolean(result.gamemodeEnabled)
        gamescopeEnabled = Boolean(result.gamescopeEnabled)
        gamescopeMode = String(result.gamescopeMode || "disabled")
        inputWidth = Number(result.gamescopeInputWidth || 1920); inputHeight = Number(result.gamescopeInputHeight || 1080)
        outputWidth = Number(result.gamescopeOutputWidth || 1920); outputHeight = Number(result.gamescopeOutputHeight || 1080)
        refreshRate = Number(result.gamescopeRefreshRate || 60)
        gamescopeFullscreen = Boolean(result.gamescopeFullscreen)
        gamescopeScaler = String(result.gamescopeScaler || "auto"); gamescopeFilter = String(result.gamescopeFilter || "linear")
        manualOverrides = result.manualOverrides || ({})
        displays = result.displays ? Array.from(result.displays) : []
        if (!targetDisplayId && displays.length) {
            var primary = displays.filter(function(item) { return Boolean(item.primary) })
            targetDisplayId = String((primary.length ? primary[0] : displays[0]).id || "")
        }
        recommendation = result.recommendation || ({})
        presetPlan = result.presetPlan || ({})
        categoryClassification = result.categoryClassification || ({})
        gamemodeStatus = result.gamemode || ({ "available": false })
        gamescopeStatus = result.gamescope || ({ "available": false })
        runnerStatus = result.runner || ({ "installed": false })
        launchPlanText = String(result.launchPlanText || "%command%")
        launchPlan = result.launchPlan || ({})
        steamLaunchCommand = String(result.steamLaunchCommand || "")
        renderingSummary = String(result.renderingSummary || "")
        fpsLimitOwner = String(result.fpsLimitOwner || result.launchPlan && result.launchPlan.fpsLimitOwner || "none")
        protonOverrides = result.protonOverrides ? Array.from(result.protonOverrides) : []
        gameAnalysis = result.gameAnalysis || ({ "status": "not_analyzed" })
        recommendationAnalyzed = false
        dirty = false; loading = false
    }
    function loadProfile() {
        if (controller && controller.getOptimizationProfile && gameId)
            applyResult(controller.getOptimizationProfile(gameId))
    }
    function preview() {
        if (!controller || !controller.previewOptimizationProfile || !gameId) return
        var result = controller.previewOptimizationProfile(gameId, draft)
        if (result && result.success) {
            loading = true
            userGoal = String(result.userGoal || userGoal)
            targetFpsMode = String(result.targetFpsMode || targetFpsMode)
            targetFps = Number(result.targetFps || targetFps)
            gamemodeEnabled = Boolean(result.gamemodeEnabled)
            gamescopeEnabled = Boolean(result.gamescopeEnabled)
            gamescopeMode = String(result.gamescopeMode || gamescopeMode)
            recommendation = result.recommendation || ({})
            presetPlan = result.presetPlan || ({})
            categoryClassification = result.categoryClassification || ({})
            launchPlanText = String(result.launchPlanText || "%command%")
            launchPlan = result.launchPlan || ({})
            renderingSummary = String(result.renderingSummary || "")
            fpsLimitOwner = String(result.fpsLimitOwner || "none")
            protonOverrides = result.protonOverrides ? Array.from(result.protonOverrides) : []
            recommendationAnalyzed = true
            loading = false
        } else errorMessage = String(result && result.error || qsTr("Invalid optimization profile"))
    }
    function saveProfile() {
        if (!controller || !controller.saveOptimizationProfile) return
        var result = controller.saveOptimizationProfile(gameId, draft)
        if (result && result.success) applyResult(result)
        else errorMessage = String(result && result.error || qsTr("Could not save optimization profile"))
    }
    function analyzeGame() {
        if (!controller || !controller.analyzeGameOptimization || !gameId)
            return
        var result = controller.analyzeGameOptimization(gameId)
        if (result && result.success)
            gameAnalysis = result
        else
            errorMessage = String(result && result.error || qsTr("Game analysis could not start"))
    }
    function recordBaseline() {
        if (!gameId) {
            errorMessage = qsTr("Baseline recording could not start")
            console.warn("Record baseline rejected at QML boundary: gameId is empty")
            toastRequested(errorMessage, "error")
            return
        }
        if (!controller || !controller.recordOptimizationBaseline) {
            errorMessage = qsTr("Baseline recording could not start")
            console.warn("Record baseline rejected at QML boundary: controller method is unavailable for gameId=" + gameId)
            toastRequested(errorMessage, "error")
            return
        }
        var result = controller.recordOptimizationBaseline(gameId)
        if (result && result.success) {
            runnerPreflight = ({})
            gameAnalysis = Object.assign({}, gameAnalysis, {
                "baselineSession": result.baselineSession || ({ "status": "waiting_for_steam" })
            })
            toastRequested(qsTr("Baseline session started. Play a representative part of the game, then exit normally."), "info")
        } else {
            runnerPreflight = result && result.runnerPreflight || ({})
            errorMessage = String(result && result.error || qsTr("Baseline recording could not start"))
            console.warn("Record baseline rejected: gameId=" + gameId
                         + " guard=" + String(result && result.code || "unknown")
                         + " reason=" + errorMessage)
            toastRequested(errorMessage, "error")
        }
    }
    function recordComparison() {
        if (!controller || !controller.recordOptimizationComparison || !gameId)
            return
        var result = controller.recordOptimizationComparison(gameId)
        if (result && result.success) {
            gameAnalysis = Object.assign({}, gameAnalysis, {
                "baselineSession": result.baselineSession || ({ "status": "waiting_for_steam", "kind": "comparison" })
            })
        } else {
            errorMessage = String(result && result.error || qsTr("Comparison recording could not start"))
        }
    }
    function baselineStatus() {
        return String(gameAnalysis.baselineSession && gameAnalysis.baselineSession.status || "not_recorded")
    }
    function pendingOptimizationTest() {
        return Boolean(gameAnalysis.appliedChange
                       && gameAnalysis.appliedChange.state === "applied")
    }
    function measurementValue(name, decimals, suffix) {
        var value = gameAnalysis.measurement && gameAnalysis.measurement[name]
        if (value === undefined || value === null || !isFinite(Number(value)))
            return qsTr("Unavailable")
        return Number(value).toFixed(decimals) + suffix
    }
    function comparisonValue(group, name, decimals, suffix) {
        var values = gameAnalysis[group] || ({})
        var value = values[name]
        if (value === undefined || value === null || !isFinite(Number(value)))
            return qsTr("Unavailable")
        return Number(value).toFixed(decimals) + suffix
    }
    function comparisonDelta(name) {
        var before = Number(gameAnalysis.beforeMeasurement
                            && gameAnalysis.beforeMeasurement[name])
        var after = Number(gameAnalysis.afterMeasurement
                           && gameAnalysis.afterMeasurement[name])
        if (!isFinite(before) || !isFinite(after) || before === 0)
            return qsTr("Unavailable")
        var value = (after - before) / before * 100
        return (value >= 0 ? "+" : "") + value.toFixed(1) + "%"
    }
    function comparisonOutcome() {
        var outcome = String(gameAnalysis.comparison
                             && gameAnalysis.comparison.outcome || "")
        if (outcome === "headroom_improved")
            return qsTr("Performance headroom improved, FPS remained capped")
        if (outcome === "insufficient_data")
            return qsTr("Comparison measurement was recorded but was not representative enough")
        if (outcome === "improvement")
            return qsTr("Improved")
        if (outcome === "regression")
            return qsTr("Degraded")
        if (outcome === "no_meaningful_change")
            return qsTr("No meaningful change")
        return outcome
    }
    function automaticState() {
        return gameAnalysis.automaticOptimization || ({})
    }
    function automaticSession() {
        return automaticState().session || ({})
    }
    function automaticProblemLabel() {
        var kind = String(automaticState().problem
                          && automaticState().problem.kind || "insufficient_data")
        if (kind === "gpu_bound") return qsTr("GPU bound")
        if (kind === "cpu_bound") return qsTr("CPU bound")
        if (kind === "frame_pacing") return qsTr("Frame pacing")
        if (kind === "frame_limited") return qsTr("Frame limited")
        if (kind === "balanced") return qsTr("Balanced")
        if (kind === "memory_pressure") return qsTr("Memory pressure")
        return qsTr("Insufficient data")
    }
    function automaticOutcomeLabel(value) {
        var outcome = String(value || "")
        if (outcome === "improved") return qsTr("Improved")
        if (outcome === "degraded") return qsTr("Degraded")
        if (outcome === "marginal") return qsTr("Marginal")
        if (outcome === "headroom_improved") return qsTr("Headroom improved")
        if (outcome === "target_already_met") return qsTr("Target already met")
        if (outcome === "insufficient_data") return qsTr("Insufficient comparable data")
        return qsTr("Not measured")
    }
    function startAutomatic(candidateId) {
        if (!controller || !controller.startAutomaticOptimization)
            return
        var result = controller.startAutomaticOptimization(
            gameId, String(candidateId || ""))
        if (result && result.success) {
            automaticPreviewOpen = false
            toastRequested(qsTr("Runtime optimization applied. Record a comparison before keeping it."), "success")
            loadProfile()
        } else {
            toastRequested(App.I18n.analysisMessage(
                               String(result && result.error
                                      || qsTr("Automatic Optimization could not start"))),
                           "error")
        }
    }
    function activeChangeValue(name) {
        var change = gameAnalysis.appliedChange || ({})
        return String(change[name] || "")
    }
    function previewSetting(instanceId, proposedValue) {
        if (!controller || !controller.previewGameSettingChange)
            return
        var result = controller.previewGameSettingChange(
            gameId, String(instanceId || ""), String(proposedValue || ""))
        if (result && result.success)
            settingPreview = result
        else
            toastRequested(String(result && result.error || qsTr("Setting preview could not be created")), "error")
    }
    function applySettingPreview() {
        if (!controller || !controller.applyGameSettingChange
                || !settingPreview.success)
            return
        var result = controller.applyGameSettingChange(
            gameId,
            String(settingPreview.settingInstanceId || ""),
            String(settingPreview.proposedValue || ""))
        if (result && result.success) {
            settingPreview = ({})
            toastRequested(qsTr("Setting change applied and verified"), "success")
            loadProfile()
        } else {
            toastRequested(String(result && result.error || qsTr("Setting change could not be applied")), "error")
        }
    }
    function revertActiveChange() {
        var result = controller.revertOptimizationChange(
            gameId, String(gameAnalysis.appliedChange && gameAnalysis.appliedChange.id || ""))
        if (result && result.success) {
            settingPreview = ({})
            loadProfile()
        } else {
            toastRequested(String(result && result.error || qsTr("Changes could not be reverted")), "error")
        }
    }
    function keepActiveChange() {
        var result = controller.keepOptimizationChange(
            gameId, String(gameAnalysis.appliedChange && gameAnalysis.appliedChange.id || ""))
        if (result && result.success) {
            settingPreview = ({})
            loadProfile()
        } else {
            toastRequested(String(result && result.error || qsTr("Change could not be kept")), "error")
        }
    }

    Timer { id: previewTimer; interval: 150; repeat: false; onTriggered: tab.preview() }

    ScrollView {
        objectName: "optimizationScroll"
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: parent.width
            spacing: 14

            SurfaceCard {
                Layout.fillWidth: true
                padding: 18
                selected: gameAnalysis.status === "completed"
                contentItem: ColumnLayout {
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Detected game")
                            color: App.Theme.text
                            font.pixelSize: 19
                            font.weight: Font.Bold
                        }
                        StatusBadge {
                            text: gameAnalysis.status === "running"
                                  ? qsTr("Analyzing")
                                  : gameAnalysis.analysisCacheState === "stale"
                                    ? qsTr("Stale analysis")
                                  : gameAnalysis.analysisCacheState === "cached"
                                    ? qsTr("Cached analysis")
                                  : gameAnalysis.status === "completed"
                                    ? qsTr("Analyzed")
                                    : gameAnalysis.status === "failed"
                                      ? qsTr("Analysis failed")
                                      : qsTr("Not analyzed")
                            status: gameAnalysis.status === "running" ? "analyzing"
                                    : gameAnalysis.analysisCacheState === "stale" ? "warning"
                                    : gameAnalysis.status === "completed" ? "available"
                                    : gameAnalysis.status === "failed" ? "failed" : "not checked"
                        }
                        AppButton {
                            objectName: "analyzeGameOptimizationButton"
                            text: gameAnalysis.status === "running"
                                  ? qsTr("Analyzing…")
                                  : gameAnalysis.status === "completed"
                                    ? qsTr("Re-analyze") : qsTr("Analyze Game")
                            kind: "primary"
                            busy: gameAnalysis.status === "running"
                            enabled: gameAnalysis.status !== "running"
                            onClicked: tab.analyzeGame()
                        }
                    }
                    Label {
                        readonly property var reasons: tab.gameAnalysis.staleReasons || []
                        visible: reasons.length > 0
                        Layout.fillWidth: true
                        text: qsTr("Saved analysis requires refresh: %1").arg(
                                  reasons.length > 0
                                  ? App.I18n.joinAnalysis(reasons, "; ") : "")
                        color: App.Theme.warning
                        wrapMode: Text.WordWrap
                    }
                    GridLayout {
                        visible: gameAnalysis.status === "completed"
                        Layout.fillWidth: true
                        columns: tab.width > 1120 ? 3 : 2
                        columnSpacing: 12
                        rowSpacing: 8
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Engine")
                            value: String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.engine
                                          && gameAnalysis.fingerprint.engine.value || qsTr("Unknown"))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Graphics API")
                            value: String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.graphicsApi
                                          && gameAnalysis.fingerprint.graphicsApi.value || qsTr("Unknown"))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Available graphics APIs")
                            value: gameAnalysis.fingerprint
                                   && gameAnalysis.fingerprint.availableGraphicsApis
                                   && gameAnalysis.fingerprint.availableGraphicsApis.length
                                   ? gameAnalysis.fingerprint.availableGraphicsApis.map(function(api) { return api.value }).join(", ")
                                   : qsTr("Unknown")
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Runtime")
                            value: App.I18n.analysisMessage(gameAnalysis.fingerprint
                                                           && gameAnalysis.fingerprint.runtime
                                                           && gameAnalysis.fingerprint.runtime.value || "Unknown")
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Architecture")
                            value: String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.architecture
                                          && gameAnalysis.fingerprint.architecture.value || qsTr("Unknown"))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Category")
                            value: App.I18n.optimizationCategory(gameAnalysis.fingerprint
                                                                && gameAnalysis.fingerprint.category
                                                                && gameAnalysis.fingerprint.category.value || "unknown")
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Executable")
                            value: String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.mainExecutable || qsTr("Unknown"))
                        }
                    }
                    Label {
                        visible: gameAnalysis.status === "completed"
                        Layout.fillWidth: true
                        text: qsTr("Engine confidence: %1% - source: %2")
                              .arg(Math.round(Number(gameAnalysis.fingerprint
                                                     && gameAnalysis.fingerprint.engine
                                                     && gameAnalysis.fingerprint.engine.confidence || 0) * 100))
                              .arg(App.I18n.analysisMessage(gameAnalysis.fingerprint
                                                           && gameAnalysis.fingerprint.engine
                                                           && gameAnalysis.fingerprint.engine.source || "not detected"))
                        color: App.Theme.textMuted
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        visible: gameAnalysis.status === "completed"
                        Layout.fillWidth: true
                        text: qsTr("Category source: %1 - confidence: %2%")
                              .arg(App.I18n.analysisMessage(gameAnalysis.fingerprint
                                                           && gameAnalysis.fingerprint.category
                                                           && gameAnalysis.fingerprint.category.source || "not detected"))
                              .arg(Math.round(Number(gameAnalysis.fingerprint
                                                     && gameAnalysis.fingerprint.category
                                                     && gameAnalysis.fingerprint.category.confidence || 0) * 100))
                        color: App.Theme.textMuted
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        visible: gameAnalysis.status === "completed"
                        text: qsTr("System snapshot")
                        color: App.Theme.text
                        font.weight: Font.Bold
                    }
                    GridLayout {
                        visible: gameAnalysis.status === "completed"
                        Layout.fillWidth: true
                        columns: tab.width > 1120 ? 3 : 2
                        columnSpacing: 12
                        rowSpacing: 8
                        LabeledValue { Layout.fillWidth: true; label: qsTr("CPU"); value: String(gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.cpu || qsTr("Unknown")) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("GPU"); value: String(gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.gpu || qsTr("Unknown")) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("RAM"); value: gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.ramGb !== null ? Number(gameAnalysis.fingerprint.system.ramGb).toFixed(1) + " GiB" : qsTr("Unknown") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("VRAM"); value: gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.vramGb !== null ? Number(gameAnalysis.fingerprint.system.vramGb).toFixed(1) + " GiB" : qsTr("Unknown") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Display"); value: String(gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.displayName || qsTr("Unknown")) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Resolution"); value: gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.resolutionWidth ? String(gameAnalysis.fingerprint.system.resolutionWidth) + "×" + String(gameAnalysis.fingerprint.system.resolutionHeight) + " @ " + Number(gameAnalysis.fingerprint.system.refreshRate || 0).toFixed(0) + " Hz" : qsTr("Unknown") }
                    }
                    Label {
                        visible: gameAnalysis.status === "failed"
                        Layout.fillWidth: true
                        text: String(gameAnalysis.error || qsTr("Game analysis failed"))
                        color: App.Theme.danger
                        wrapMode: Text.WordWrap
                    }
                }
            }

            SurfaceCard {
                id: automaticOptimizationCard
                objectName: "automaticOptimizationCard"
                visible: gameAnalysis.status === "completed"
                Layout.fillWidth: true
                padding: 18
                selected: String(tab.automaticSession().state || "") === "candidate_applied"
                          || String(tab.automaticSession().state || "") === "comparison_recording"
                          || String(tab.automaticSession().state || "") === "result_ready"
                contentItem: ColumnLayout {
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Automatic Optimization")
                            color: App.Theme.text
                            font.pixelSize: 18
                            font.weight: Font.Bold
                        }
                        StatusBadge {
                            text: tab.automaticSession().state === "candidate_applied"
                                  ? qsTr("Applied - waiting for comparison")
                                  : tab.automaticSession().state === "comparison_recording"
                                    ? qsTr("Comparison recording")
                                  : tab.automaticSession().state === "result_ready"
                                    ? qsTr("Result ready")
                                  : tab.automaticSession().state === "kept"
                                    ? qsTr("Kept")
                                  : tab.automaticSession().state === "reverted"
                                    ? qsTr("Reverted")
                                  : gameAnalysis.baselineAvailable
                                    ? qsTr("Ready") : qsTr("Baseline required")
                            status: tab.automaticSession().state === "candidate_applied"
                                    || tab.automaticSession().state === "comparison_recording"
                                    ? "warning"
                                    : tab.automaticSession().state === "result_ready"
                                      || tab.automaticSession().state === "kept"
                                      ? "available" : "neutral"
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: tab.width > 880 ? 3 : 1
                        columnSpacing: 12
                        rowSpacing: 8
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Measured problem")
                            value: tab.automaticProblemLabel()
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Confidence")
                            value: Math.round(Number(tab.automaticState().problem
                                                     && tab.automaticState().problem.confidence || 0) * 100) + "%"
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Target")
                            value: App.I18n.analysisMessage(tab.automaticState().problem
                                                           && tab.automaticState().problem.target || "")
                        }
                        LabeledValue {
                            visible: String(tab.automaticState().noOpOutcome || "").length > 0
                            Layout.fillWidth: true
                            label: qsTr("Result")
                            value: tab.automaticOutcomeLabel(
                                       tab.automaticState().noOpOutcome)
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: App.I18n.analysisMessage(tab.automaticState().message || "")
                        color: tab.automaticState().canStart
                               ? App.Theme.textSecondary : App.Theme.warning
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        visible: Boolean(tab.automaticState().canStart)
                                 && String(tab.automaticSession().state || "") !== "candidate_applied"
                                 && String(tab.automaticSession().state || "") !== "comparison_recording"
                                 && String(tab.automaticSession().state || "") !== "result_ready"
                        text: qsTr("Available experiments: %1").arg(
                              Number(tab.automaticState().availableCount || 0))
                        color: App.Theme.text
                        font.weight: Font.Bold
                    }
                    Repeater {
                        model: tab.automaticPreviewOpen
                               ? (tab.automaticState().availableCandidates || []) : []
                        SurfaceCard {
                            id: automaticCandidateCard
                            objectName: "automaticRuntimeCandidate"
                            required property var modelData
                            Layout.fillWidth: true
                            padding: 13
                            contentItem: ColumnLayout {
                                spacing: 6
                                Label {
                                    text: App.I18n.analysisMessage(automaticCandidateCard.modelData.name || "")
                                    color: App.Theme.text
                                    font.weight: Font.Bold
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Goal: %1").arg(
                                          App.I18n.analysisMessage(automaticCandidateCard.modelData.expectedGoal || ""))
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Risk: %1").arg(
                                          App.I18n.analysisMessage(automaticCandidateCard.modelData.risk || ""))
                                    color: App.Theme.warning
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Image quality impact: %1").arg(
                                          App.I18n.analysisMessage(automaticCandidateCard.modelData.qualityImpact || ""))
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Activation must be verified in the measured runner plan before the result is judged.")
                                    color: App.Theme.textMuted
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Item { Layout.fillWidth: true }
                                    AppButton {
                                        objectName: "startAutomaticOptimizationButton"
                                        text: qsTr("Apply optimization")
                                        kind: "primary"
                                        onClicked: tab.startAutomatic(
                                            String(automaticCandidateCard.modelData.id || ""))
                                    }
                                }
                            }
                        }
                    }
                    RowLayout {
                        visible: Boolean(tab.automaticState().canStart)
                                 && String(tab.automaticSession().state || "") !== "candidate_applied"
                                 && String(tab.automaticSession().state || "") !== "comparison_recording"
                                 && String(tab.automaticSession().state || "") !== "result_ready"
                        Layout.fillWidth: true
                        AppButton {
                            objectName: "previewAutomaticPlanButton"
                            text: tab.automaticPreviewOpen
                                  ? qsTr("Hide plan") : qsTr("Preview plan")
                            kind: "secondary"
                            onClicked: tab.automaticPreviewOpen = !tab.automaticPreviewOpen
                        }
                        Item { Layout.fillWidth: true }
                    }
                    SurfaceCard {
                        visible: String(tab.automaticSession().state || "") === "candidate_applied"
                                 || String(tab.automaticSession().state || "") === "comparison_recording"
                        Layout.fillWidth: true
                        padding: 13
                        selected: true
                        contentItem: ColumnLayout {
                            spacing: 7
                            Label {
                                text: qsTr("Optimization test in progress")
                                color: App.Theme.warning
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: App.I18n.analysisMessage(tab.automaticSession().candidate
                                                              && tab.automaticSession().candidate.name || "")
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: tab.automaticSession().state === "comparison_recording"
                                      ? qsTr("The comparison session is running or being processed.")
                                      : qsTr("The change is applied. Launch and record a representative comparison before deciding whether to keep it.")
                                color: App.Theme.textSecondary
                                wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppButton {
                                    objectName: "automaticRecordComparisonButton"
                                    text: qsTr("Launch and record comparison")
                                    kind: "primary"
                                    enabled: tab.automaticSession().state === "candidate_applied"
                                             || ["failed", "recorded_unrepresentative", "completed"].indexOf(tab.baselineStatus()) >= 0
                                    onClicked: tab.recordComparison()
                                }
                                AppButton {
                                    text: qsTr("Revert")
                                    kind: "secondary"
                                    onClicked: tab.revertActiveChange()
                                }
                            }
                        }
                    }
                    SurfaceCard {
                        visible: String(tab.automaticSession().state || "") === "result_ready"
                        Layout.fillWidth: true
                        padding: 13
                        selected: true
                        contentItem: ColumnLayout {
                            spacing: 7
                            Label {
                                text: qsTr("Measured result: %1").arg(
                                      tab.automaticOutcomeLabel(tab.automaticSession().result
                                                                && tab.automaticSession().result.outcome))
                                color: tab.automaticSession().result
                                       && tab.automaticSession().result.recommendRevert
                                       ? App.Theme.danger : App.Theme.text
                                font.weight: Font.Bold
                            }
                            Repeater {
                                model: tab.automaticSession().result
                                       && tab.automaticSession().result.evidence || []
                                Label {
                                    required property string modelData
                                    Layout.fillWidth: true
                                    text: "• " + App.I18n.analysisMessage(modelData)
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: App.I18n.analysisMessage(tab.automaticSession().result
                                                              && tab.automaticSession().result.activationReason || "")
                                color: tab.automaticSession().result
                                       && tab.automaticSession().result.activationVerified
                                       ? App.Theme.success : App.Theme.warning
                                wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppButton {
                                    visible: Boolean(tab.automaticSession().result)
                                             && tab.automaticSession().result.outcome === "insufficient_data"
                                    text: qsTr("Retry comparison")
                                    kind: "primary"
                                    onClicked: tab.recordComparison()
                                }
                                AppButton {
                                    visible: Boolean(tab.automaticSession().result)
                                             && tab.automaticSession().result.outcome !== "insufficient_data"
                                    text: tab.automaticSession().result
                                          && tab.automaticSession().result.recommendKeep
                                          ? qsTr("Keep recommended") : qsTr("Keep anyway")
                                    kind: tab.automaticSession().result
                                          && tab.automaticSession().result.recommendKeep
                                          ? "primary" : "secondary"
                                    onClicked: tab.keepActiveChange()
                                }
                                AppButton {
                                    text: tab.automaticSession().result
                                          && tab.automaticSession().result.recommendRevert
                                          ? qsTr("Revert recommended") : qsTr("Revert")
                                    kind: tab.automaticSession().result
                                          && tab.automaticSession().result.recommendRevert
                                          ? "danger" : "secondary"
                                    onClicked: tab.revertActiveChange()
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        visible: (tab.automaticState().history || []).length > 0
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: qsTr("Optimization history")
                            color: App.Theme.text
                            font.weight: Font.Bold
                        }
                        Repeater {
                            model: tab.automaticState().history || []
                            Label {
                                required property var modelData
                                Layout.fillWidth: true
                                text: qsTr("%1 - %2 - %3").arg(
                                      App.I18n.analysisMessage(modelData.candidateName || modelData.candidateId || ""))
                                      .arg(tab.automaticOutcomeLabel(modelData.outcome || ""))
                                      .arg(App.I18n.status(modelData.action || "pending"))
                                color: App.Theme.textSecondary
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                visible: gameAnalysis.status === "completed"
                Layout.fillWidth: true
                padding: 18
                contentItem: ColumnLayout {
                    spacing: 10
                    Label {
                        text: qsTr("Performance analysis")
                        color: App.Theme.text
                        font.pixelSize: 18
                        font.weight: Font.Bold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: gameAnalysis.baselineAvailable
                              ? (gameAnalysis.baselineStale
                                 ? qsTr("Saved baseline - new measurement required before measured Automatic recommendations")
                                 : gameAnalysis.analysisCacheState === "cached"
                                   ? qsTr("Saved baseline") : qsTr("Baseline recorded"))
                              : tab.baselineStatus() === "waiting_for_steam" ? qsTr("Waiting for Steam...")
                              : tab.baselineStatus() === "waiting_for_runner" ? qsTr("Waiting for runner...")
                              : tab.baselineStatus() === "recording" ? qsTr("Recording...")
                              : tab.baselineStatus() === "waiting_for_game_exit" ? qsTr("Waiting for game exit...")
                              : tab.baselineStatus() === "processing" ? qsTr("Processing MangoHud log...")
                              : tab.baselineStatus() === "recorded_unrepresentative"
                                ? qsTr("Baseline recorded, but the measurement was not representative enough for automatic optimization. Repeat the test during representative gameplay.")
                              : tab.baselineStatus() === "failed"
                                ? qsTr("Baseline recording failed: %1").arg(String(gameAnalysis.baselineSession.error || qsTr("No usable log was produced")))
                                : qsTr("Baseline: Not recorded")
                        color: gameAnalysis.baselineStale ? App.Theme.warning
                               : gameAnalysis.baselineAvailable ? App.Theme.success
                               : tab.baselineStatus() === "recorded_unrepresentative" ? App.Theme.warning
                               : tab.baselineStatus() === "failed" ? App.Theme.danger : App.Theme.warning
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            objectName: "recordOptimizationBaselineButton"
                            text: ["waiting_for_steam", "waiting_for_runner", "recording", "waiting_for_game_exit"].indexOf(tab.baselineStatus()) >= 0
                                  ? qsTr("Recording...") : qsTr("Record baseline")
                            kind: "primary"
                            enabled: tab.baselineStatus() !== "waiting_for_steam"
                                     && tab.baselineStatus() !== "waiting_for_runner"
                                     && tab.baselineStatus() !== "recording"
                                     && tab.baselineStatus() !== "waiting_for_game_exit"
                                     && tab.baselineStatus() !== "processing"
                                     && !tab.pendingOptimizationTest()
                            onClicked: tab.recordBaseline()
                        }
                        AppButton {
                            objectName: "importMangoHudLogButton"
                            text: qsTr("Import MangoHud log")
                            kind: "secondary"
                            enabled: tab.baselineStatus() !== "waiting_for_steam"
                                     && tab.baselineStatus() !== "waiting_for_runner"
                                     && tab.baselineStatus() !== "recording"
                                     && tab.baselineStatus() !== "waiting_for_game_exit"
                                     && tab.baselineStatus() !== "processing"
                                     && !tab.pendingOptimizationTest()
                            onClicked: baselineLogDialog.open()
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Recording uses a private one-session MangoHud configuration and does not modify the saved game profile.")
                            color: App.Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                    }
                    SurfaceCard {
                        visible: String(tab.runnerPreflight.message || "").length > 0
                        Layout.fillWidth: true
                        padding: 12
                        selected: true
                        contentItem: RowLayout {
                            Label {
                                Layout.fillWidth: true
                                text: String(tab.runnerPreflight.message || "")
                                color: App.Theme.warning
                                wrapMode: Text.WordWrap
                            }
                            AppButton {
                                text: qsTr("Copy Launch Option")
                                kind: "secondary"
                                onClicked: {
                                    steamCommand.selectAll()
                                    steamCommand.copy()
                                    steamCommand.deselect()
                                }
                            }
                        }
                    }
                    GridLayout {
                        visible: Boolean(gameAnalysis.measurement)
                        Layout.fillWidth: true
                        columns: tab.width > 880 ? 4 : 2
                        columnSpacing: 12
                        rowSpacing: 8
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Duration"); value: tab.measurementValue("durationSeconds", 1, " s") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Average FPS"); value: tab.measurementValue("averageFps", 1, " FPS") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("1% low"); value: tab.measurementValue("onePercentLowFps", 1, " FPS") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Frametime"); value: tab.measurementValue("averageFrametimeMs", 2, " ms") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Frametime p95"); value: tab.measurementValue("p95FrametimeMs", 2, " ms") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Frametime p99"); value: tab.measurementValue("p99FrametimeMs", 2, " ms") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("CPU usage"); value: tab.measurementValue("cpuUsagePercent", 1, "%") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("GPU usage"); value: tab.measurementValue("gpuUsagePercent", 1, "%") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("RAM usage"); value: tab.measurementValue("ramUsedMb", 0, " MiB") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("VRAM usage"); value: tab.measurementValue("vramUsedMb", 0, " MiB") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("GPU temperature"); value: tab.measurementValue("gpuTemperatureC", 1, " °C") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Samples used"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.samples || 0)) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Total samples"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.totalSamples || 0)) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Excluded samples"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.excludedSamples || 0)) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Samples used (%)"); value: Number(gameAnalysis.measurement && gameAnalysis.measurement.usedPercentage || 0).toFixed(1) + "%" }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Selected duration"); value: tab.measurementValue("selectedDurationSeconds", 1, " s") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Measurement quality"); value: App.I18n.status(gameAnalysis.measurement && gameAnalysis.measurement.quality || "low") }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Representative"); value: Boolean(gameAnalysis.measurement && gameAnalysis.measurement.representative) ? qsTr("Yes") : qsTr("No") }
                    }
                    Label {
                        visible: Boolean(gameAnalysis.measurement) && gameAnalysis.measurement.representative === false
                        Layout.fillWidth: true
                        text: qsTr("The recording contains mixed or unstable rendering regimes. Record another baseline during representative gameplay before using automatic recommendations.")
                        color: App.Theme.warning
                        wrapMode: Text.WordWrap
                    }
                    GridLayout {
                        visible: Boolean(gameAnalysis.measurement)
                        Layout.fillWidth: true
                        columns: tab.width > 880 ? 2 : 1
                        columnSpacing: 12
                        rowSpacing: 8
                        LabeledValue {
                            objectName: "frameRateLimitValue"
                            Layout.fillWidth: true
                            label: qsTr("Frame-rate limit")
                            value: tab.frameRateLimitLabel()
                        }
                        LabeledValue {
                            objectName: "frameRateConfidenceValue"
                            Layout.fillWidth: true
                            label: qsTr("Frame-limit confidence")
                            value: gameAnalysis.frameRate
                                   && gameAnalysis.frameRate.state !== "unknown"
                                   ? Math.round(Number(gameAnalysis.frameRate.confidence || 0) * 100) + "%"
                                   : qsTr("Unavailable")
                        }
                    }
                    Label {
                        visible: Boolean(gameAnalysis.frameRate
                                         && gameAnalysis.frameRate.evidence
                                         && gameAnalysis.frameRate.evidence.length)
                        Layout.fillWidth: true
                        text: qsTr("Frame-limit evidence: %1").arg(
                                  gameAnalysis.frameRate
                                  && gameAnalysis.frameRate.evidence
                                  ? App.I18n.joinAnalysis(gameAnalysis.frameRate.evidence, "; ") : "")
                        color: App.Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }
                    LabeledValue {
                        Layout.fillWidth: true
                        label: qsTr("Bottleneck")
                        value: App.I18n.analysisMessage(gameAnalysis.bottleneck
                                                       && gameAnalysis.bottleneck.conclusion
                                                       || "insufficient_data")
                    }
                    LabeledValue {
                        Layout.fillWidth: true
                        label: qsTr("Confidence")
                        value: gameAnalysis.baselineAvailable
                               ? Math.round(Number(gameAnalysis.bottleneck && gameAnalysis.bottleneck.confidence || 0) * 100) + "%"
                               : qsTr("Unavailable")
                    }
                    Repeater {
                        model: gameAnalysis.bottleneck
                               && gameAnalysis.bottleneck.evidence
                               ? gameAnalysis.bottleneck.evidence : []
                        Label {
                            required property string modelData
                            Layout.fillWidth: true
                            text: "• " + App.I18n.analysisMessage(modelData)
                            color: App.Theme.textSecondary
                            wrapMode: Text.WordWrap
                        }
                    }
                    Repeater {
                        model: gameAnalysis.bottleneck
                               && gameAnalysis.bottleneck.limitations
                               ? gameAnalysis.bottleneck.limitations : []
                        Label {
                            required property string modelData
                            Layout.fillWidth: true
                            text: qsTr("Limitation: %1").arg(App.I18n.analysisMessage(modelData))
                            color: App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            SurfaceCard {
                visible: gameAnalysis.status === "completed"
                Layout.fillWidth: true
                padding: 18
                contentItem: ColumnLayout {
                    spacing: 10
                    Label {
                        text: qsTr("Settings analysis")
                        color: App.Theme.text
                        font.pixelSize: 18
                        font.weight: Font.Bold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: gameAnalysis.settingsAnalysis
                              && gameAnalysis.settingsAnalysis.status === "unsupported"
                              ? qsTr("Automatic graphics settings optimization is not available for this game yet.")
                              : gameAnalysis.settingsAnalysis
                                && gameAnalysis.settingsAnalysis.detected
                                && gameAnalysis.settingsAnalysis.detected.length
                                ? qsTr("Supported existing settings were detected.")
                                : qsTr("No safely modifiable settings were found")
                        color: gameAnalysis.settingsAnalysis
                               && gameAnalysis.settingsAnalysis.detected
                               && gameAnalysis.settingsAnalysis.detected.length
                               ? App.Theme.success : App.Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        visible: Boolean(gameAnalysis.settingsAnalysis
                                         && gameAnalysis.settingsAnalysis.configFiles
                                         && gameAnalysis.settingsAnalysis.configFiles.length)
                        Layout.fillWidth: true
                        text: qsTr("Detected configuration: %1").arg(
                                  gameAnalysis.settingsAnalysis
                                  && gameAnalysis.settingsAnalysis.configFiles
                                  ? gameAnalysis.settingsAnalysis.configFiles.join(", ") : "")
                        color: App.Theme.textMuted
                        wrapMode: Text.WrapAnywhere
                    }
                    Repeater {
                        model: gameAnalysis.settingsAnalysis
                               && gameAnalysis.settingsAnalysis.detected
                               ? gameAnalysis.settingsAnalysis.detected : []
                        SurfaceCard {
                            id: detectedSetting
                            required property var modelData
                            property string selectedValue: String(modelData.suggestedValue || "")
                            Layout.fillWidth: true
                            padding: 13
                            contentItem: ColumnLayout {
                                spacing: 7
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: App.I18n.analysisMessage(detectedSetting.modelData.label
                                                                      || detectedSetting.modelData.key || "")
                                        color: App.Theme.text
                                        font.weight: Font.Bold
                                    }
                                    StatusBadge {
                                        text: detectedSetting.modelData.automaticallyRecommended
                                              ? qsTr("Automatic recommendation")
                                              : qsTr("Manual test available")
                                        status: detectedSetting.modelData.automaticallyRecommended
                                                ? "available" : "neutral"
                                    }
                                }
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: tab.width > 820 ? 2 : 1
                                    columnSpacing: 14
                                    rowSpacing: 6
                                    LabeledValue {
                                        Layout.fillWidth: true
                                        label: qsTr("Current value")
                                        value: String(detectedSetting.modelData.value || "")
                                    }
                                    LabeledValue {
                                        Layout.fillWidth: true
                                        label: qsTr("Supported values")
                                        value: (detectedSetting.modelData.availableValues || []).join(" / ")
                                    }
                                    LabeledValue {
                                        Layout.fillWidth: true
                                        label: qsTr("Category")
                                        value: App.I18n.analysisMessage(detectedSetting.modelData.category || "")
                                    }
                                    LabeledValue {
                                        Layout.fillWidth: true
                                        label: qsTr("Performance relevance")
                                        value: App.I18n.status(detectedSetting.modelData.performanceImpact || "unknown")
                                    }
                                    LabeledValue {
                                        Layout.fillWidth: true
                                        label: qsTr("Visual impact")
                                        value: App.I18n.status(detectedSetting.modelData.qualityImpact || "unknown")
                                    }
                                    LabeledValue {
                                        Layout.fillWidth: true
                                        label: qsTr("Automatic recommendation")
                                        value: detectedSetting.modelData.automaticallyRecommended
                                               ? qsTr("Yes") : qsTr("No")
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: App.I18n.analysisMessage(detectedSetting.modelData.automaticReason || "")
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    visible: Boolean(detectedSetting.modelData.modifiable)
                                    Layout.fillWidth: true
                                    Label {
                                        text: qsTr("Manual test value")
                                        color: App.Theme.textSecondary
                                    }
                                    AppComboBox {
                                        id: manualValue
                                        Layout.preferredWidth: 130
                                        model: detectedSetting.modelData.alternativeValues || []
                                        enabled: !Boolean(gameAnalysis.appliedChange
                                                          && gameAnalysis.appliedChange.state === "applied")
                                        onActivated: function(index) {
                                            detectedSetting.selectedValue = String(model[index])
                                        }
                                    }
                                    Item { Layout.fillWidth: true }
                                    AppButton {
                                        objectName: "manualSettingPreviewButton"
                                        text: qsTr("Preview test")
                                        kind: "secondary"
                                        enabled: detectedSetting.selectedValue.length > 0
                                                 && !Boolean(gameAnalysis.appliedChange
                                                            && gameAnalysis.appliedChange.state === "applied")
                                        onClicked: tab.previewSetting(
                                            detectedSetting.modelData.instanceId,
                                            detectedSetting.selectedValue)
                                    }
                                }
                            }
                        }
                    }
                    SurfaceCard {
                        visible: Boolean(settingPreview && settingPreview.success)
                        Layout.fillWidth: true
                        padding: 13
                        selected: true
                        contentItem: ColumnLayout {
                            spacing: 6
                            Label {
                                text: qsTr("Game setting test")
                                color: App.Theme.text
                                font.weight: Font.Bold
                            }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Setting"); value: App.I18n.analysisMessage(settingPreview.settingLabel || "") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("File"); value: (settingPreview.filesToModify || []).join(", ") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Key"); value: String(settingPreview.configKey || "") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Current value"); value: String(settingPreview.currentValue || "") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("New value"); value: String(settingPreview.proposedValue || "") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Automatic recommendation"); value: settingPreview.automaticRecommended ? qsTr("Yes") : qsTr("No") }
                            Label { Layout.fillWidth: true; text: App.I18n.analysisMessage(settingPreview.automaticReason || ""); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Performance relevance"); value: App.I18n.status(settingPreview.performanceImpact || "unknown") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Visual impact"); value: App.I18n.status(settingPreview.qualityImpact || "unknown") }
                            Label {
                                visible: Boolean(settingPreview.aggressive)
                                Layout.fillWidth: true
                                text: qsTr("This is more aggressive than the conservative one-step test.")
                                color: App.Theme.warning
                                wrapMode: Text.WordWrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("A verified backup will be created before the file is changed.")
                                color: App.Theme.success
                                wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Item { Layout.fillWidth: true }
                                AppButton { text: qsTr("Cancel"); kind: "secondary"; onClicked: settingPreview = ({}) }
                                AppButton { text: qsTr("Apply and test"); kind: "primary"; onClicked: tab.applySettingPreview() }
                            }
                        }
                    }
                    SurfaceCard {
                        visible: Boolean(gameAnalysis.appliedChange
                                         && gameAnalysis.appliedChange.state === "applied"
                                         && gameAnalysis.appliedChange.config_adapter)
                        Layout.fillWidth: true
                        padding: 13
                        selected: true
                        contentItem: ColumnLayout {
                            spacing: 7
                            Label { text: qsTr("Pending optimization test"); color: App.Theme.warning; font.weight: Font.Bold }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("%1: %2 -> %3").arg(
                                          App.I18n.analysisMessage(tab.activeChangeValue("setting_label")
                                          || tab.activeChangeValue("config_key")))
                                      .arg(tab.activeChangeValue("current_value"))
                                      .arg(tab.activeChangeValue("proposed_value"))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("The config change was applied and verified. Record a representative comparison before keeping it.")
                                color: App.Theme.textSecondary
                                wrapMode: Text.WordWrap
                            }
                            Label {
                                visible: String(gameAnalysis.comparison
                                                && gameAnalysis.comparison.outcome || "") === "insufficient_data"
                                Layout.fillWidth: true
                                text: qsTr("Comparison measurement was recorded but was not representative enough. The original baseline is unchanged.")
                                color: App.Theme.warning
                                wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppButton {
                                    text: String(gameAnalysis.comparison
                                                 && gameAnalysis.comparison.outcome || "") === "insufficient_data"
                                          ? qsTr("Record comparison again") : qsTr("Record comparison")
                                    kind: "primary"
                                    onClicked: tab.recordComparison()
                                }
                                AppButton {
                                    visible: String(gameAnalysis.comparison
                                                    && gameAnalysis.comparison.outcome || "").length > 0
                                             && String(gameAnalysis.comparison.outcome) !== "insufficient_data"
                                    text: qsTr("Keep change")
                                    kind: "primary"
                                    onClicked: tab.keepActiveChange()
                                }
                                AppButton {
                                    text: qsTr("Revert now")
                                    kind: gameAnalysis.comparison
                                          && gameAnalysis.comparison.recommendRevert
                                          ? "danger" : "secondary"
                                    onClicked: tab.revertActiveChange()
                                }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                visible: gameAnalysis.status === "completed"
                Layout.fillWidth: true
                padding: 18
                contentItem: ColumnLayout {
                    spacing: 10
                    Label {
                        text: qsTr("Recommendations")
                        color: App.Theme.text
                        font.pixelSize: 18
                        font.weight: Font.Bold
                    }
                    Label {
                        objectName: "emptyOptimizationRecommendation"
                        visible: gameAnalysis.noSafeRecommendations === true
                        Layout.fillWidth: true
                        text: tab.emptyRecommendationMessage()
                        color: App.Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: gameAnalysis.candidates || []
                        SurfaceCard {
                            id: candidateCard
                            objectName: "optimizationCandidateCard"
                            required property var modelData
                            property bool previewOpen: false
                            Layout.fillWidth: true
                            padding: 13
                            contentItem: ColumnLayout {
                                spacing: 6
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: App.I18n.analysisMessage(modelData.mechanism || "")
                                        color: App.Theme.text
                                        font.weight: Font.Bold
                                    }
                                    StatusBadge {
                                        text: modelData.reversible ? qsTr("Reversible") : qsTr("Manual rollback")
                                        status: modelData.reversible ? "available" : "warning"
                                    }
                                }
                                Label { Layout.fillWidth: true; text: qsTr("Why: %1").arg(App.I18n.joinAnalysis(modelData.evidence || [], "; ")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                ColumnLayout {
                                    visible: candidateCard.previewOpen
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Label { Layout.fillWidth: true; text: qsTr("Change: %1 → %2").arg(String(modelData.currentValue || "")).arg(String(modelData.proposedValue || "")); color: App.Theme.accent; wrapMode: Text.WordWrap }
                                    Label { Layout.fillWidth: true; text: qsTr("Expected effect: %1").arg(App.I18n.analysisMessage(modelData.expectedEffect || "")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                    Label { Layout.fillWidth: true; text: qsTr("Performance impact: %1").arg(App.I18n.status(modelData.performanceImpact || "unknown")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                    Label { Layout.fillWidth: true; text: qsTr("Quality impact: %1").arg(App.I18n.status(modelData.qualityImpact || "unknown")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                                    Label { Layout.fillWidth: true; text: qsTr("Confidence: %1").arg(App.I18n.status(modelData.confidenceLabel || "unknown")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                    Label { Layout.fillWidth: true; text: qsTr("Risk: %1").arg(App.I18n.analysisMessage(modelData.risk || "")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                                    Label { Layout.fillWidth: true; visible: (modelData.filesToModify || []).length > 0; text: qsTr("File: %1").arg((modelData.filesToModify || []).join(", ")); color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere }
                                    Label { Layout.fillWidth: true; visible: (modelData.filesToModify || []).length > 0; text: qsTr("A verified backup and deterministic revert are available."); color: App.Theme.success; wrapMode: Text.WordWrap }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Item { Layout.fillWidth: true }
                                    AppButton {
                                        objectName: "optimizationCandidatePreviewButton"
                                        text: candidateCard.previewOpen ? qsTr("Hide preview") : qsTr("Preview")
                                        kind: "secondary"
                                        onClicked: candidateCard.previewOpen = !candidateCard.previewOpen
                                    }
                                    AppButton {
                                        objectName: "optimizationCandidateApplyButton"
                                        visible: candidateCard.previewOpen
                                        text: qsTr("Apply and test")
                                        kind: "primary"
                                        onClicked: {
                                            var result = tab.controller.applyOptimizationCandidate(tab.gameId, String(modelData.id || ""))
                                            if (result && result.success)
                                            {
                                                tab.toastRequested(qsTr("Optimization applied"), "success")
                                                tab.loadProfile()
                                            }
                                            else
                                                tab.toastRequested(String(result && result.error || qsTr("Optimization could not be applied")), "error")
                                        }
                                    }
                                }
                            }
                        }
                    }
                    RowLayout {
                        visible: Boolean(gameAnalysis.appliedChange
                                         && gameAnalysis.appliedChange.state === "applied"
                                         && !gameAnalysis.appliedChange.config_adapter
                                         && !tab.automaticSession().id)
                        Layout.fillWidth: true
                        AppButton {
                            text: qsTr("Record comparison")
                            kind: "primary"
                            onClicked: tab.recordComparison()
                        }
                        AppButton {
                            visible: String(gameAnalysis.comparison
                                            && gameAnalysis.comparison.outcome || "").length > 0
                                     && String(gameAnalysis.comparison.outcome) !== "insufficient_data"
                            text: qsTr("Keep change")
                            kind: "primary"
                            onClicked: {
                                var result = tab.controller.keepOptimizationChange(
                                    tab.gameId, String(gameAnalysis.appliedChange.id || ""))
                                if (result && result.success)
                                    tab.loadProfile()
                                else
                                    tab.toastRequested(String(result && result.error || qsTr("Change could not be kept")), "error")
                            }
                        }
                        AppButton {
                            visible: String(gameAnalysis.appliedChange && gameAnalysis.appliedChange.id || "").length > 0
                            text: qsTr("Revert changes")
                            kind: gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert ? "danger" : "secondary"
                            onClicked: {
                                var result = tab.controller.revertOptimizationChange(
                                    tab.gameId, String(gameAnalysis.appliedChange.id || ""))
                                if (result && result.success)
                                    tab.loadProfile()
                                else
                                    tab.toastRequested(String(result && result.error || qsTr("Changes could not be reverted")), "error")
                            }
                        }
                    }
                    Label {
                        visible: Boolean(gameAnalysis.appliedChange
                                         && gameAnalysis.appliedChange.state === "applied")
                                 && !gameAnalysis.appliedChange.config_adapter
                                 && !tab.automaticSession().id
                                 && String(gameAnalysis.comparison
                                           && gameAnalysis.comparison.outcome || "").length === 0
                        Layout.fillWidth: true
                        text: qsTr("Pending comparison measurement - record a representative comparison before keeping the change.")
                        color: App.Theme.warning
                        wrapMode: Text.WordWrap
                    }
                    SurfaceCard {
                        visible: String(gameAnalysis.comparison && gameAnalysis.comparison.outcome || "").length > 0
                        Layout.fillWidth: true
                        padding: 12
                        selected: Boolean(gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert)
                        contentItem: ColumnLayout {
                            Label {
                                text: qsTr("Before / After: %1").arg(tab.comparisonOutcome())
                                color: gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert ? App.Theme.danger : App.Theme.text
                                font.weight: Font.Bold
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 4
                                columnSpacing: 10
                                Label { text: qsTr("Metric"); color: App.Theme.textMuted }
                                Label { text: qsTr("Before"); color: App.Theme.textMuted }
                                Label { text: qsTr("After"); color: App.Theme.textMuted }
                                Label { text: qsTr("Change"); color: App.Theme.textMuted }
                                Label { text: qsTr("Average FPS"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "averageFps", 1, " FPS"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "averageFps", 1, " FPS"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("averageFps"); color: App.Theme.text }
                                Label { text: qsTr("1% low"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "onePercentLowFps", 1, " FPS"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "onePercentLowFps", 1, " FPS"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("onePercentLowFps"); color: App.Theme.text }
                                Label { text: qsTr("Frametime"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "averageFrametimeMs", 2, " ms"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "averageFrametimeMs", 2, " ms"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("averageFrametimeMs"); color: App.Theme.text }
                                Label { text: qsTr("Frametime p95"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "p95FrametimeMs", 2, " ms"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "p95FrametimeMs", 2, " ms"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("p95FrametimeMs"); color: App.Theme.text }
                                Label { text: qsTr("Frametime p99"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "p99FrametimeMs", 2, " ms"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "p99FrametimeMs", 2, " ms"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("p99FrametimeMs"); color: App.Theme.text }
                                Label { text: qsTr("GPU usage"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "gpuUsagePercent", 1, "%"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "gpuUsagePercent", 1, "%"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("gpuUsagePercent"); color: App.Theme.text }
                                Label { text: qsTr("CPU usage"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "cpuUsagePercent", 1, "%"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "cpuUsagePercent", 1, "%"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("cpuUsagePercent"); color: App.Theme.text }
                                Label { text: qsTr("VRAM usage"); color: App.Theme.textSecondary }
                                Label { text: tab.comparisonValue("beforeMeasurement", "vramUsedMb", 0, " MiB"); color: App.Theme.text }
                                Label { text: tab.comparisonValue("afterMeasurement", "vramUsedMb", 0, " MiB"); color: App.Theme.text }
                                Label { text: tab.comparisonDelta("vramUsedMb"); color: App.Theme.text }
                            }
                            Repeater {
                                model: gameAnalysis.comparison && gameAnalysis.comparison.evidence || []
                                Label { required property string modelData; Layout.fillWidth: true; text: "• " + App.I18n.analysisMessage(modelData); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true; padding: 18; selected: gameAnalysis.baselineAvailable === true
                contentItem: ColumnLayout {
                    spacing: 9
                    Label {
                        text: gameAnalysis.baselineAvailable
                              ? qsTr("Measured optimization recommendations")
                              : qsTr("Preliminary settings")
                        color: App.Theme.text
                        font.pixelSize: 19
                        font.weight: Font.Bold
                    }
                    StatusBadge { text: App.I18n.message(String(tab.recommendation.status || qsTr("Preliminary recommendation - game measurement required"))); status: tab.recommendation.preliminary === false ? "available" : "warning" }
                    Label { Layout.fillWidth: true; text: qsTr("Target: %1 FPS").arg(Number(tab.recommendation.targetFps || tab.targetFps)); color: App.Theme.accent; font.pixelSize: 17; font.weight: Font.Bold }
                    Repeater { model: tab.recommendation.reasons || []; Label { required property string modelData; Layout.fillWidth: true; text: "• " + App.I18n.message(modelData); color: App.Theme.textSecondary; wrapMode: Text.WordWrap } }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton { text: qsTr("Preview changes"); kind: "secondary"; onClicked: tab.preview() }
                        Label { Layout.fillWidth: true; text: tab.recommendationAnalyzed ? qsTr("Exact changes are shown in the launch plan below") : qsTr("Analyze the current profile before applying it"); color: App.Theme.textMuted; wrapMode: Text.WordWrap }
                    }
                }
            }

            OptiScalerSection {
                objectName: "optiScalerSection"
                Layout.fillWidth: true
                controller: tab.controller
                gameData: tab.gameData
            }

            ProtonTweaksSection {
                objectName: "protonTweaksSection"
                Layout.fillWidth: true
                controller: tab.controller
                gameData: tab.gameData
            }

            SurfaceCard {
                Layout.fillWidth: true; padding: 18
                contentItem: ColumnLayout {
                    spacing: 10
                    Label { text: qsTr("Profile"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                    GridLayout {
                        Layout.fillWidth: true; columns: tab.width > 950 ? 5 : 2; columnSpacing: 8; rowSpacing: 8
                        Repeater { model: tab.presetLabels; AppButton { required property int index; required property string modelData; Layout.fillWidth: true; text: modelData; kind: tab.preset === tab.presetValues[index] ? "primary" : "secondary"; onClicked: tab.choosePreset(tab.presetValues[index]) } }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true; columns: tab.width > 900 ? 2 : 1; columnSpacing: 12; rowSpacing: 12
                SurfaceCard {
                    Layout.fillWidth: true; padding: 18
                    contentItem: ColumnLayout {
                        Label { text: qsTr("Game and user goal"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Game category"); description: qsTr("You can always correct this classification manually")
                            AppComboBox { Layout.preferredWidth: 260; model: tab.categoryLabels; currentIndex: tab.indexOf(tab.categoryValues, tab.gameCategory, 6); onActivated: function(index) { tab.gameCategory = tab.categoryValues[index]; tab.preset = "custom"; tab.changed("category") } }
                        }
                        Label { Layout.fillWidth: true; text: qsTr("Classification source: %1, confidence: %2%").arg(String(tab.categoryClassification.source || qsTr("not detected"))).arg(Math.round(Number(tab.categoryClassification.confidence || 0) * 100)); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                        SettingRow { Layout.fillWidth: true; title: qsTr("User goal")
                            AppComboBox { Layout.preferredWidth: 260; model: tab.goalLabels; currentIndex: tab.indexOf(tab.goalValues, tab.userGoal, 1); onActivated: function(index) { tab.userGoal = tab.goalValues[index]; tab.preset = "custom"; tab.changed("goal") } }
                        }
                    }
                }
                SurfaceCard {
                    Layout.fillWidth: true; padding: 18
                    contentItem: ColumnLayout {
                        Label { text: qsTr("Monitor and FPS"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Gaming monitor"); description: tab.displays.length ? qsTr("Detected from active Qt screens") : qsTr("No active screen detected")
                            AppComboBox { Layout.preferredWidth: 300; enabled: tab.displays.length > 0; model: tab.displayLabels; currentIndex: tab.indexOf(tab.displayValues, tab.targetDisplayId, 0); onActivated: function(index) { tab.targetDisplayId = tab.displayValues[index]; tab.applyDisplayDefaults(); tab.changed("display") } }
                        }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Target FPS"); description: tab.gamescopeEnabled ? qsTr("Enforced by Gamescope") : qsTr("Advisory until Gamescope is enabled")
                            RowLayout { AppComboBox { property var values: ["automatic", "manual", "unlimited"]; model: [qsTr("Automatic"), qsTr("Manual"), qsTr("Unlimited")]; currentIndex: tab.indexOf(values, tab.targetFpsMode, 0); onActivated: function(index) { tab.targetFpsMode = values[index]; tab.changed("fps") } }
                                AppTextField { Layout.preferredWidth: 82; enabled: tab.targetFpsMode !== "unlimited"; text: String(tab.targetFps); validator: IntValidator { bottom: 15; top: 1000 } onTextEdited: { tab.targetFps = Number(text || 60); tab.targetFpsMode = "manual"; tab.changed("fps") } }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true; padding: 18
                contentItem: ColumnLayout {
                    Label { text: qsTr("Runtime wrappers"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                    SettingRow { Layout.fillWidth: true; title: qsTr("GameMode"); description: App.I18n.message(String(tab.gamemodeStatus.message || ""))
                        AppSwitch { enabled: Boolean(tab.gamemodeStatus.available); checked: tab.gamemodeEnabled; onToggled: { tab.gamemodeEnabled = checked; tab.preset = "custom"; tab.changed("gamemode") } }
                    }
                    Divider { Layout.fillWidth: true }
                    SettingRow { Layout.fillWidth: true; title: qsTr("Gamescope"); description: App.I18n.message(String(tab.gamescopeStatus.message || ""))
                        AppSwitch { enabled: Boolean(tab.gamescopeStatus.available); checked: tab.gamescopeEnabled; onToggled: { tab.gamescopeEnabled = checked; if (checked && tab.gamescopeMode === "disabled") tab.gamescopeMode = "native"; tab.preset = "custom"; tab.changed("gamescope") } }
                    }
                    SettingRow { Layout.fillWidth: true; title: qsTr("Gamescope mode"); description: tab.renderingSummary
                        AppComboBox { Layout.preferredWidth: 220; enabled: Boolean(tab.gamescopeStatus.available); model: tab.gamescopeModeLabels; currentIndex: tab.indexOf(tab.gamescopeModeValues, tab.gamescopeMode, 0); onActivated: function(index) { tab.applyGamescopeMode(tab.gamescopeModeValues[index]) } }
                    }
                    GridLayout {
                        Layout.fillWidth: true; columns: 4; columnSpacing: 8
                        AppTextField { placeholderText: qsTr("Render width"); text: String(tab.inputWidth); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { tab.inputWidth = Number(text); tab.changed("resolution") } }
                        AppTextField { placeholderText: qsTr("Render height"); text: String(tab.inputHeight); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { tab.inputHeight = Number(text); tab.changed("resolution") } }
                        AppTextField { placeholderText: qsTr("Output width"); text: String(tab.outputWidth); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { tab.outputWidth = Number(text); tab.changed("resolution") } }
                        AppTextField { placeholderText: qsTr("Output height"); text: String(tab.outputHeight); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { tab.outputHeight = Number(text); tab.changed("resolution") } }
                    }
                    RowLayout {
                        AppComboBox { property var values: ["auto", "fit", "fill", "stretch", "integer"]; model: values; currentIndex: tab.indexOf(values, tab.gamescopeScaler, 0); onActivated: function(index) { tab.gamescopeScaler = values[index]; tab.changed("gamescope") } }
                        AppComboBox { property var values: ["linear", "nearest", "fsr", "nis", "pixel"]; model: values; currentIndex: tab.indexOf(values, tab.gamescopeFilter, 0); onActivated: function(index) { tab.gamescopeFilter = values[index]; tab.changed("gamescope") } }
                        AppTextField { Layout.preferredWidth: 100; placeholderText: qsTr("Refresh Hz"); text: String(tab.refreshRate); validator: IntValidator { bottom: 15; top: 1000 } onTextEdited: { tab.refreshRate = Number(text); tab.changed("refresh") } }
                        AppSwitch { checked: tab.gamescopeFullscreen; onToggled: { tab.gamescopeFullscreen = checked; tab.changed("gamescope") } }
                        Label { text: qsTr("Fullscreen"); color: App.Theme.textSecondary }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true; padding: 18; selected: true
                contentItem: ColumnLayout {
                    Label { text: qsTr("Launch plan preview"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                    Label { Layout.fillWidth: true; text: tab.renderingSummary; visible: tab.gamescopeEnabled; color: App.Theme.warning; wrapMode: Text.WordWrap }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 110; radius: App.Theme.radiusMedium; color: App.Theme.input; clip: true
                        ScrollView { anchors.fill: parent; anchors.margins: 10; contentWidth: availableWidth
                            TextEdit { objectName: "launchPreviewText"; width: parent.width; readOnly: true; selectByMouse: true; text: tab.launchPlanText; color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                        }
                    }
                    Label { Layout.fillWidth: true; text: qsTr("Wrapper order: %1").arg((tab.launchPlan.wrappers || []).join(" → ") || qsTr("none")); color: App.Theme.textSecondary }
                    Label { objectName: "fpsLimitOwnerLabel"; Layout.fillWidth: true; text: qsTr("FPS limit owner: %1").arg(tab.fpsOwnerLabel()); color: App.Theme.textSecondary; font.weight: Font.DemiBold }
                    Label { Layout.fillWidth: true; text: qsTr("Final runtime environment: %1").arg(tab.protonOverrides.length ? tab.protonOverrides.join("; ") : qsTr("none")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                    Label { Layout.fillWidth: true; visible: (tab.launchPlan.warnings || []).length > 0; text: qsTr("Warnings: %1").arg((tab.launchPlan.warnings || []).join("; ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                    Label { Layout.fillWidth: true; visible: (tab.launchPlan.environmentConflicts || []).length > 0; text: qsTr("Environment conflicts: %1").arg((tab.launchPlan.environmentConflicts || []).join(", ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                    Label { Layout.fillWidth: true; visible: (tab.presetPlan.sources || []).length > 0; text: qsTr("Decision sources: %1").arg((tab.presetPlan.sources || []).join(", ")); color: App.Theme.textMuted; wrapMode: Text.WordWrap }
                    Label { Layout.fillWidth: true; visible: (tab.presetPlan.conflicts || []).length > 0; text: qsTr("Preset constraints: %1").arg((tab.presetPlan.conflicts || []).join("; ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                }
            }

            SurfaceCard {
                visible: !tab.localGame
                Layout.fillWidth: true; padding: 18
                contentItem: ColumnLayout {
                    RowLayout { Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: qsTr("Steam connection"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                        StatusBadge { text: tab.runnerStatus.installed ? qsTr("Runner installed") : qsTr("Runner not installed"); status: tab.runnerStatus.installed ? "available" : "warning" }
                    }
                    Label { Layout.fillWidth: true; text: qsTr("Set this command once in the game’s Steam Launch Options. Later profile changes do not change it."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 66; radius: App.Theme.radiusMedium; color: App.Theme.input
                        TextEdit { id: steamCommand; anchors.fill: parent; anchors.margins: 10; readOnly: true; selectByMouse: true; text: tab.steamLaunchCommand; color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                    }
                    RowLayout {
                        AppButton { text: qsTr("Copy command"); kind: "secondary"; onClicked: { steamCommand.selectAll(); steamCommand.copy(); steamCommand.deselect() } }
                        AppButton { text: qsTr("Show instructions"); kind: "secondary"; onClicked: tab.showSteamInstructions = !tab.showSteamInstructions }
                        AppButton { text: qsTr("Test runner"); kind: "secondary"; onClicked: if (tab.controller && tab.controller.testGameOptimizationRunner) tab.controller.testGameOptimizationRunner(tab.gameId) }
                    }
                    Label { Layout.fillWidth: true; visible: tab.showSteamInstructions; text: qsTr("Steam → Properties → General → Launch Options"); color: App.Theme.textMuted; wrapMode: Text.WordWrap }
                }
            }

            Label { Layout.fillWidth: true; visible: tab.errorMessage.length > 0; text: tab.errorMessage; color: App.Theme.danger; wrapMode: Text.WordWrap }
            RowLayout { Layout.fillWidth: true; Item { Layout.fillWidth: true }
                AppButton { objectName: "saveOptimizationProfileButton"; text: qsTr("Apply profile"); enabled: tab.dirty && tab.recommendationAnalyzed; onClicked: tab.saveProfile() }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }

    onGameIdChanged: {
        settingPreview = ({})
        loadProfile()
    }
    Component.onCompleted: loadProfile()

    FileDialog {
        id: baselineLogDialog
        title: qsTr("Import MangoHud log")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("MangoHud logs (*.csv)"), qsTr("All files (*)")]
        onAccepted: {
            var result = tab.controller.importOptimizationBaseline(tab.gameId, selectedFile.toString())
            if (result && result.success) {
                tab.gameAnalysis = Object.assign({}, tab.gameAnalysis, {
                    "baselineSession": result.baselineSession || ({ "status": "processing" })
                })
            } else {
                tab.errorMessage = String(result && result.error || qsTr("MangoHud log could not be imported"))
            }
        }
    }

    Connections {
        target: tab.controller || null
        ignoreUnknownSignals: true
        function onProtonTweaksChanged(appId) {
            if (String(appId) === tab.appId)
                tab.loadProfile()
        }
        function onOptimizationAnalysisChanged(changedGameId) {
            if (String(changedGameId) === tab.gameId)
                tab.loadProfile()
        }
    }
}
