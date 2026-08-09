pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
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
        if (value === "automatic") targetFpsMode = "automatic"
        else if (value === "maximum_performance") { userGoal = "lowest_latency"; targetFpsMode = "automatic"; gamemodeEnabled = Boolean(gamemodeStatus.available) }
        else if (value === "balanced") { userGoal = "stable_image"; targetFpsMode = "automatic" }
        else if (value === "quiet") { userGoal = "low_power"; targetFpsMode = "manual"; targetFps = 45; gamemodeEnabled = false; gamescopeEnabled = false; gamescopeMode = "disabled" }
        loading = false
        dirty = true
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
        gamemodeStatus = result.gamemode || ({ "available": false })
        gamescopeStatus = result.gamescope || ({ "available": false })
        runnerStatus = result.runner || ({ "installed": false })
        launchPlanText = String(result.launchPlanText || "%command%")
        launchPlan = result.launchPlan || ({})
        steamLaunchCommand = String(result.steamLaunchCommand || "")
        renderingSummary = String(result.renderingSummary || "")
        fpsLimitOwner = String(result.fpsLimitOwner || result.launchPlan && result.launchPlan.fpsLimitOwner || "none")
        protonOverrides = result.protonOverrides ? Array.from(result.protonOverrides) : []
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
            recommendation = result.recommendation || ({})
            launchPlanText = String(result.launchPlanText || "%command%")
            launchPlan = result.launchPlan || ({})
            renderingSummary = String(result.renderingSummary || "")
        } else errorMessage = String(result && result.error || qsTr("Invalid optimization profile"))
    }
    function saveProfile() {
        if (!controller || !controller.saveOptimizationProfile) return
        var result = controller.saveOptimizationProfile(gameId, draft)
        if (result && result.success) applyResult(result)
        else errorMessage = String(result && result.error || qsTr("Could not save optimization profile"))
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
                Layout.fillWidth: true; padding: 18; selected: true
                contentItem: ColumnLayout {
                    spacing: 9
                    Label { text: qsTr("Game Optimization recommendation"); color: App.Theme.text; font.pixelSize: 19; font.weight: Font.Bold }
                    StatusBadge { text: App.I18n.message(String(tab.recommendation.status || qsTr("Preliminary recommendation - game measurement required"))); status: tab.recommendation.preliminary === false ? "available" : "warning" }
                    Label { Layout.fillWidth: true; text: qsTr("Target: %1 FPS").arg(Number(tab.recommendation.targetFps || tab.targetFps)); color: App.Theme.accent; font.pixelSize: 17; font.weight: Font.Bold }
                    Repeater { model: tab.recommendation.reasons || []; Label { required property string modelData; Layout.fillWidth: true; text: "• " + App.I18n.message(modelData); color: App.Theme.textSecondary; wrapMode: Text.WordWrap } }
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
                    Label { Layout.fillWidth: true; visible: (tab.launchPlan.environmentConflicts || []).length > 0; text: qsTr("Environment conflicts: %1").arg((tab.launchPlan.environmentConflicts || []).join(", ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
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
                AppButton { objectName: "saveOptimizationProfileButton"; text: qsTr("Save profile"); enabled: tab.dirty; onClicked: tab.saveProfile() }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }

    onGameIdChanged: loadProfile()
    Component.onCompleted: loadProfile()

    Connections {
        target: tab.controller || null
        ignoreUnknownSignals: true
        function onProtonTweaksChanged(appId) {
            if (String(appId) === tab.appId)
                tab.loadProfile()
        }
    }
}
