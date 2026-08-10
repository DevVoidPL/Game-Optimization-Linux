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
        if (!controller || !controller.recordOptimizationBaseline || !gameId)
            return
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
    function measurementValue(name, decimals, suffix) {
        var value = gameAnalysis.measurement && gameAnalysis.measurement[name]
        if (value === undefined || value === null || !isFinite(Number(value)))
            return qsTr("Unavailable")
        return Number(value).toFixed(decimals) + suffix
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
                                  : gameAnalysis.status === "completed"
                                    ? qsTr("Analyzed")
                                    : gameAnalysis.status === "failed"
                                      ? qsTr("Analysis failed")
                                      : qsTr("Not analyzed")
                            status: gameAnalysis.status === "running" ? "analyzing"
                                    : gameAnalysis.status === "completed" ? "available"
                                    : gameAnalysis.status === "failed" ? "failed" : "not checked"
                        }
                        AppButton {
                            objectName: "analyzeGameOptimizationButton"
                            text: gameAnalysis.status === "running"
                                  ? qsTr("Analyzing…") : qsTr("Analyze Game")
                            kind: "primary"
                            busy: gameAnalysis.status === "running"
                            enabled: gameAnalysis.status !== "running"
                            onClicked: tab.analyzeGame()
                        }
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
                            value: String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.runtime
                                          && gameAnalysis.fingerprint.runtime.value || qsTr("Unknown"))
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
                            value: String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.category
                                          && gameAnalysis.fingerprint.category.value || qsTr("Unknown"))
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
                              .arg(String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.engine
                                          && gameAnalysis.fingerprint.engine.source || qsTr("not detected")))
                        color: App.Theme.textMuted
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        visible: gameAnalysis.status === "completed"
                        Layout.fillWidth: true
                        text: qsTr("Category source: %1 - confidence: %2%")
                              .arg(String(gameAnalysis.fingerprint
                                          && gameAnalysis.fingerprint.category
                                          && gameAnalysis.fingerprint.category.source || qsTr("not detected")))
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
                        text: gameAnalysis.baselineAvailable ? qsTr("Baseline recorded")
                              : tab.baselineStatus() === "waiting_for_steam" ? qsTr("Waiting for Steam...")
                              : tab.baselineStatus() === "waiting_for_runner" ? qsTr("Waiting for runner...")
                              : tab.baselineStatus() === "recording" ? qsTr("Recording...")
                              : tab.baselineStatus() === "waiting_for_game_exit" ? qsTr("Waiting for game exit...")
                              : tab.baselineStatus() === "processing" ? qsTr("Processing MangoHud log...")
                              : tab.baselineStatus() === "failed"
                                ? qsTr("Baseline recording failed: %1").arg(String(gameAnalysis.baselineSession.error || qsTr("No usable log was produced")))
                                : qsTr("Baseline: Not recorded")
                        color: gameAnalysis.baselineAvailable ? App.Theme.success
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
                        visible: gameAnalysis.baselineAvailable === true
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
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Samples"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.samples || 0)) }
                        LabeledValue { Layout.fillWidth: true; label: qsTr("Measurement quality"); value: String(gameAnalysis.measurement && gameAnalysis.measurement.quality || qsTr("Low")) }
                    }
                    LabeledValue {
                        Layout.fillWidth: true
                        label: qsTr("Bottleneck")
                        value: String(gameAnalysis.bottleneck
                                      && gameAnalysis.bottleneck.conclusion
                                      || qsTr("Insufficient data"))
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
                            text: "• " + modelData
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
                            text: qsTr("Limitation: %1").arg(modelData)
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
                        text: qsTr("Recommendations")
                        color: App.Theme.text
                        font.pixelSize: 18
                        font.weight: Font.Bold
                    }
                    Label {
                        visible: gameAnalysis.noSafeRecommendations === true
                        Layout.fillWidth: true
                        text: qsTr("No safe optimization recommendations were found")
                        color: App.Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: gameAnalysis.candidates || []
                        SurfaceCard {
                            required property var modelData
                            Layout.fillWidth: true
                            padding: 13
                            contentItem: ColumnLayout {
                                spacing: 6
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: String(modelData.mechanism || "")
                                        color: App.Theme.text
                                        font.weight: Font.Bold
                                    }
                                    StatusBadge {
                                        text: modelData.reversible ? qsTr("Reversible") : qsTr("Manual rollback")
                                        status: modelData.reversible ? "available" : "warning"
                                    }
                                }
                                Label { Layout.fillWidth: true; text: qsTr("Why: %1").arg((modelData.evidence || []).join("; ")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; text: qsTr("Change: %1 → %2").arg(String(modelData.currentValue || "")).arg(String(modelData.proposedValue || "")); color: App.Theme.accent; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; text: qsTr("Expected effect: %1").arg(String(modelData.expectedEffect || "")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; text: qsTr("Quality impact: %1").arg(String(modelData.qualityImpact || "")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; text: qsTr("Risk: %1").arg(String(modelData.risk || "")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; visible: (modelData.filesToModify || []).length > 0; text: qsTr("Files: %1").arg((modelData.filesToModify || []).join(", ")); color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Item { Layout.fillWidth: true }
                                    AppButton {
                                        text: qsTr("Apply")
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
                        visible: Boolean(gameAnalysis.appliedChange && gameAnalysis.appliedChange.state === "applied")
                        Layout.fillWidth: true
                        AppButton {
                            text: qsTr("Record comparison")
                            kind: "primary"
                            onClicked: tab.recordComparison()
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
                    SurfaceCard {
                        visible: String(gameAnalysis.comparison && gameAnalysis.comparison.outcome || "").length > 0
                        Layout.fillWidth: true
                        padding: 12
                        selected: Boolean(gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert)
                        contentItem: ColumnLayout {
                            Label {
                                text: qsTr("Before / After: %1").arg(String(gameAnalysis.comparison && gameAnalysis.comparison.outcome || ""))
                                color: gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert ? App.Theme.danger : App.Theme.text
                                font.weight: Font.Bold
                            }
                            Repeater {
                                model: gameAnalysis.comparison && gameAnalysis.comparison.evidence || []
                                Label { required property string modelData; Layout.fillWidth: true; text: "• " + modelData; color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
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

    onGameIdChanged: loadProfile()
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
