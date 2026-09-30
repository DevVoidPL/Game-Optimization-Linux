pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab

    property var controller
    property var gameData: ({})
    readonly property string gameId: String(value(["id"], ""))
    property bool loading: false
    property bool profileLoaded: false
    property bool dirty: false
    property string errorMessage: ""
    property bool available: false
    property string availabilityMessage: ""
    property string appId: ""
    property string profilePath: ""
    property string configPath: ""
    property string configPreview: ""
    property string selectedExecutable: ""
    property var executableCandidates: []
    property string activationStrategy: "steam_environment"
    property string strategyStatus: "executable_missing"
    property string strategyMessage: ""
    property string applicationConfigPath: ""
    property string conflictPath: ""
    property bool requiresSteamRestart: false
    property string preset: "disabled"
    property bool profileEnabled: false
    property string position: "top-left"
    property int fontSize: 24
    property real backgroundAlpha: 0.5
    property int roundCorners: 8
    property bool compact: false
    property bool horizontal: false
    property int tableColumns: 3
    property int fpsLimit: 0
    property string fpsLimitOwner: "none"
    property string fpsLimitMethod: ""
    property string vulkanPresentMode: ""
    property int vsync: -2
    property string toggleHudKey: "Shift_R+F12"
    property var metrics: []
    property bool loggingEnabled: false
    property int logDuration: 60
    property real logInterval: 0.1
    property string outputFolder: ""
    property string toggleLoggingKey: "Shift_L+F2"
    property var supportedMetrics: []
    property bool frameTimingGraph: false
    property string textColor: "FFFFFF"
    property string gpuColor: "2E9762"
    property string cpuColor: "2E97CB"
    property string vramColor: "AD64C1"
    property string ramColor: "C26693"
    property string frametimeColor: "00FF00"
    property string backgroundColor: "020202"
    // Preset metric sets and the HUD layout both come from the backend model
    // that also generates MangoHud.conf.
    property var presetMetricSets: ({})
    property var previewLayout: ({})
    property bool configExpanded: false
    property bool livePreviewRunning: false
    property string livePreviewMessage: ""
    property int categoryIndex: 0

    signal toastRequested(string message, string tone)

    readonly property var categoryModel: [
        qsTr("Presets"), qsTr("Appearance"), qsTr("Metrics"),
        qsTr("Performance"), qsTr("Logging"), qsTr("Advanced")
    ]
    readonly property var presetModel: [
        { "id": "disabled", "label": qsTr("Disabled"), "description": qsTr("Do not activate MangoHud for this game.") },
        { "id": "fps_only", "label": qsTr("FPS only"), "description": qsTr("FPS and basic frametime information.") },
        { "id": "basic", "label": qsTr("Basic"), "description": qsTr("FPS, CPU, GPU, temperatures, RAM and VRAM.") },
        { "id": "extended", "label": qsTr("Extended"), "description": qsTr("Adds clocks, power, process and Wine or Proton information.") },
        { "id": "custom", "label": qsTr("Custom"), "description": qsTr("Choose each supported metric yourself.") }
    ]
    readonly property var positionValues: [
        "top-left", "top-center", "top-right", "middle-left", "middle-right",
        "bottom-left", "bottom-center", "bottom-right"
    ]
    readonly property var positionLabels: [
        qsTr("Top left"), qsTr("Top center"), qsTr("Top right"),
        qsTr("Middle left"), qsTr("Middle right"), qsTr("Bottom left"),
        qsTr("Bottom center"), qsTr("Bottom right")
    ]
    readonly property var metricModel: [
        { "id": "fps", "label": qsTr("FPS") },
        { "id": "frametime", "label": qsTr("Frametime") },
        { "id": "gpu_usage", "label": qsTr("GPU usage") },
        { "id": "gpu_temperature", "label": qsTr("GPU temperature") },
        { "id": "gpu_clock", "label": qsTr("GPU clock") },
        { "id": "gpu_power", "label": qsTr("GPU power") },
        { "id": "vram", "label": qsTr("VRAM") },
        { "id": "cpu_usage", "label": qsTr("CPU usage") },
        { "id": "cpu_temperature", "label": qsTr("CPU temperature") },
        { "id": "cpu_clock", "label": qsTr("CPU clock") },
        { "id": "cpu_power", "label": qsTr("CPU power") },
        { "id": "ram", "label": qsTr("RAM") },
        { "id": "process_memory", "label": qsTr("Process memory") },
        { "id": "process_vram", "label": qsTr("Process VRAM") },
        { "id": "resolution", "label": qsTr("Resolution") },
        { "id": "wine_proton", "label": qsTr("Wine / Proton") },
        { "id": "gamemode", "label": qsTr("GameMode status") },
        { "id": "battery", "label": qsTr("Battery") },
        { "id": "network", "label": qsTr("Network") }
    ]

    readonly property var draftMap: ({
        "enabled": profileEnabled,
        "preset": preset,
        "position": position,
        "fontSize": fontSize,
        "backgroundAlpha": backgroundAlpha,
        "roundCorners": roundCorners,
        "compact": compact,
        "horizontal": horizontal,
        "tableColumns": tableColumns,
        "fpsLimit": fpsLimit,
        "fpsLimitMethod": fpsLimitMethod,
        "vulkanPresentMode": vulkanPresentMode,
        "vsync": vsync === -2 ? null : vsync,
        "toggleHudKey": toggleHudKey,
        "metrics": metrics,
        "loggingEnabled": loggingEnabled,
        "logDuration": logDuration,
        "logInterval": logInterval,
        "outputFolder": outputFolder,
        "toggleLoggingKey": toggleLoggingKey,
        "executablePath": selectedExecutable,
        "frameTimingGraph": frameTimingGraph,
        "textColor": textColor,
        "gpuColor": gpuColor,
        "cpuColor": cpuColor,
        "vramColor": vramColor,
        "ramColor": ramColor,
        "frametimeColor": frametimeColor,
        "backgroundColor": backgroundColor
    })
    readonly property var colorModel: [
        { "id": "textColor", "label": qsTr("Text color") },
        { "id": "gpuColor", "label": qsTr("GPU color") },
        { "id": "cpuColor", "label": qsTr("CPU color") },
        { "id": "vramColor", "label": qsTr("VRAM color") },
        { "id": "ramColor", "label": qsTr("RAM color") },
        { "id": "frametimeColor", "label": qsTr("Frametime graph color") },
        { "id": "backgroundColor", "label": qsTr("Background color") }
    ]

    readonly property var executableValues: executableCandidates.map(function(item) {
        return String(item.relativePath || "")
    })
    readonly property var executableLabels: executableCandidates.map(function(item) {
        return String(item.label || item.relativePath || item.name || "")
    })

    function strategyLabel() {
        if (strategyStatus === "application_config_conflict")
            return qsTr("Conflict with an existing MangoHud configuration")
        if (strategyStatus === "executable_missing" || strategyStatus === "saved_invalid")
            return qsTr("Game executable was not determined")
        if (activationStrategy === "per_application_config")
            return qsTr("Application profile - changes apply on the next game launch")
        return requiresSteamRestart
                ? qsTr("Steam environment profile - restart Steam")
                : qsTr("Steam environment profile - already active")
    }

    function value(keys, fallback) {
        var source = gameData || {}
        for (var index = 0; index < keys.length; ++index) {
            var candidate = source[keys[index]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function indexOf(items, wanted, fallback) {
        var found = items.indexOf(wanted)
        return found >= 0 ? found : fallback
    }

    function hasMetric(metric) { return metrics.indexOf(metric) >= 0 }
    function metricSupported(metric) {
        return supportedMetrics.length === 0 || supportedMetrics.indexOf(metric) >= 0
    }

    function sameMetricSet(left, right) {
        if (left.length !== right.length) return false
        for (var index = 0; index < left.length; ++index)
            if (right.indexOf(left[index]) < 0) return false
        return true
    }

    // A manual metric change makes the profile "custom" unless the resulting
    // set is exactly one of the presets (never "disabled").
    function presetForMetrics(values) {
        var candidates = ["fps_only", "basic", "extended"]
        for (var index = 0; index < candidates.length; ++index) {
            var known = presetMetricSets[candidates[index]]
            if (known && sameMetricSet(values, known)) return candidates[index]
        }
        return "custom"
    }

    function setMetric(metric, selected) {
        var next = metrics.slice()
        var index = next.indexOf(metric)
        if (selected && index < 0) next.push(metric)
        else if (!selected && index >= 0) next.splice(index, 1)
        metrics = next
        preset = presetForMetrics(next)
        profileEnabled = true
        changed()
    }

    function presetMetrics(value) {
        if (value === "disabled") return []
        if (value === "custom") return metrics
        var known = presetMetricSets[value]
        return known ? Array.from(known) : metrics
    }

    function hudBackground(hex, alpha) {
        var base = Qt.lighter(hex, 1.0)
        return Qt.rgba(base.r, base.g, base.b, alpha)
    }

    function colorValue(name) { return String(tab[name] || "") }
    function validColor(value) { return /^[0-9A-Fa-f]{6}$/.test(String(value)) }
    function setColor(name, value) {
        var normalized = String(value || "").replace("#", "").toUpperCase()
        if (!validColor(normalized) || tab[name] === normalized) return
        tab[name] = normalized
        changed()
    }

    function startLivePreview() {
        livePreviewMessage = ""
        if (!controller || !controller.startMangoHudLivePreview || gameId.length === 0) return
        var result = controller.startMangoHudLivePreview(gameId, draftMap) || ({})
        livePreviewRunning = Boolean(result.running)
        if (!result.success) livePreviewMessage = livePreviewErrorText(result)
    }

    function stopLivePreview() {
        if (controller && controller.stopMangoHudLivePreview) controller.stopMangoHudLivePreview()
    }

    function livePreviewErrorText(result) {
        var code = String(result && result.code || "")
        if (code === "vkcube_missing") return qsTr("Live preview requires the vulkan-tools package")
        if (code === "mangohud_missing") return App.I18n.message(String(result.message || ""))
        if (code === "already_running") return qsTr("A live preview is already running")
        if (code === "disabled") return qsTr("Select a preset with metrics first")
        if (code === "process_failed")
            return qsTr("The live preview ended with an error: %1").arg(String(result.message || ""))
        return qsTr("Could not start the live preview: %1").arg(String(result && result.message || ""))
    }

    function copyConfig() {
        configTextEdit.selectAll()
        configTextEdit.copy()
        configTextEdit.deselect()
        toastRequested(qsTr("Configuration copied"), "success")
    }

    function choosePreset(value) {
        preset = value
        profileEnabled = value !== "disabled"
        metrics = presetMetrics(value)
        changed()
    }

    function changed() {
        if (loading) return
        dirty = true
        previewTimer.restart()
    }

    function applyProfile(result) {
        loading = true
        profileLoaded = Boolean(result && result.success)
        errorMessage = String(result && result.error || "")
        appId = String(result && result.appId || "")
        available = Boolean(result && result.available)
        availabilityMessage = String(result && result.availabilityMessage || result && result.error || "")
        preset = String(result && result.preset || "disabled")
        profileEnabled = Boolean(result && result.enabled)
        position = String(result && result.position || "top-left")
        fontSize = Number(result && result.fontSize || 24)
        backgroundAlpha = Number(result && result.backgroundAlpha !== undefined ? result.backgroundAlpha : 0.5)
        roundCorners = Number(result && result.roundCorners !== undefined ? result.roundCorners : 8)
        compact = Boolean(result && result.compact)
        horizontal = Boolean(result && result.horizontal)
        tableColumns = Number(result && result.tableColumns || 3)
        fpsLimit = Number(result && result.fpsLimit || 0)
        fpsLimitOwner = String(result && result.fpsLimitOwner || "none")
        fpsLimitMethod = String(result && result.fpsLimitMethod || "")
        vulkanPresentMode = String(result && result.vulkanPresentMode || "")
        vsync = result && result.vsync !== null && result.vsync !== undefined ? Number(result.vsync) : -2
        toggleHudKey = String(result && result.toggleHudKey || "Shift_R+F12")
        metrics = result && result.metrics ? Array.from(result.metrics) : []
        loggingEnabled = Boolean(result && result.loggingEnabled)
        logDuration = Number(result && result.logDuration || 60)
        logInterval = Number(result && result.logInterval !== undefined ? result.logInterval : 0.1)
        outputFolder = String(result && result.outputFolder || "")
        toggleLoggingKey = String(result && result.toggleLoggingKey || "Shift_L+F2")
        supportedMetrics = result && result.supportedMetrics ? Array.from(result.supportedMetrics) : []
        frameTimingGraph = Boolean(result && result.frameTimingGraph)
        textColor = String(result && result.textColor || "FFFFFF")
        gpuColor = String(result && result.gpuColor || "2E9762")
        cpuColor = String(result && result.cpuColor || "2E97CB")
        vramColor = String(result && result.vramColor || "AD64C1")
        ramColor = String(result && result.ramColor || "C26693")
        frametimeColor = String(result && result.frametimeColor || "00FF00")
        backgroundColor = String(result && result.backgroundColor || "020202")
        presetMetricSets = result && result.presetMetrics ? result.presetMetrics : ({})
        previewLayout = result && result.previewLayout ? result.previewLayout : ({})
        livePreviewRunning = Boolean(result && result.livePreviewRunning)
        profilePath = String(result && result.profilePath || "")
        configPath = String(result && result.configPath || "")
        configPreview = String(result && result.configPreview || "")
        selectedExecutable = String(result && result.selectedExecutable || result && result.executablePath || "")
        executableCandidates = result && result.executableCandidates ? Array.from(result.executableCandidates) : []
        activationStrategy = String(result && result.activationStrategy || "steam_environment")
        strategyStatus = String(result && result.strategyStatus || "executable_missing")
        strategyMessage = String(result && result.strategyMessage || "")
        applicationConfigPath = String(result && result.applicationConfigPath || "")
        conflictPath = String(result && result.conflictPath || "")
        requiresSteamRestart = Boolean(result && result.requiresSteamRestart)
        dirty = false
        loading = false
    }

    function loadProfile() {
        if (!controller || !controller.getMangoHudProfile || gameId.length === 0)
            return
        applyProfile(controller.getMangoHudProfile(gameId))
    }

    function refreshPreview() {
        if (!controller || !controller.previewMangoHudProfile || gameId.length === 0)
            return
        var result = controller.previewMangoHudProfile(gameId, draftMap)
        if (result && result.success)
        {
            errorMessage = ""
            configPreview = String(result.configPreview || "")
            previewLayout = result.previewLayout || ({})
            fpsLimit = Number(result.fpsLimit || 0)
            fpsLimitOwner = String(result.fpsLimitOwner || "none")
            activationStrategy = String(result.activationStrategy || activationStrategy)
            strategyStatus = String(result.strategyStatus || strategyStatus)
            strategyMessage = String(result.strategyMessage || strategyMessage)
            applicationConfigPath = String(result.applicationConfigPath || applicationConfigPath)
            conflictPath = String(result.conflictPath || "")
            requiresSteamRestart = Boolean(result.requiresSteamRestart)
        }
        else
            errorMessage = String(result && result.error || qsTr("Invalid MangoHud settings"))
    }

    function saveProfile() {
        if (!controller || !controller.saveMangoHudProfile) return
        var result = controller.saveMangoHudProfile(gameId, draftMap)
        if (result && result.success) applyProfile(result)
        else errorMessage = String(result && result.error || qsTr("Could not save MangoHud profile"))
    }

    // Short debounce: slider drags and typing rebuild only the preview model.
    Timer { id: previewTimer; interval: 180; repeat: false; onTriggered: tab.refreshPreview() }

    Connections {
        target: tab.controller ? tab.controller : null
        ignoreUnknownSignals: true
        function onMangoHudLivePreviewChanged(result) {
            tab.livePreviewRunning = Boolean(result && result.running)
            tab.livePreviewMessage = result && !result.success ? tab.livePreviewErrorText(result) : ""
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: qsTr("Per-game MangoHud profile · Steam AppID %1").arg(tab.appId || "-")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.fontCaption
            }
            StatusBadge {
                text: tab.available ? qsTr("MangoHud detected") : qsTr("MangoHud unavailable")
                status: tab.available ? "available" : "warning"
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: availabilityLabel.implicitHeight + 20
            radius: App.Theme.radiusMedium
            color: tab.available ? App.Theme.successSoft : App.Theme.warningSoft
            Label {
                id: availabilityLabel
                anchors.fill: parent
                anchors.margins: 10
                text: App.I18n.message(tab.availabilityMessage)
                color: tab.available ? App.Theme.success : App.Theme.warning
                wrapMode: Text.WordWrap
                font.pixelSize: App.Theme.fontCaption
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            SurfaceCard {
                Layout.preferredWidth: 174
                Layout.fillHeight: true
                padding: 8
                contentItem: ListView {
                    id: categories
                    clip: true
                    model: tab.categoryModel
                    spacing: 5
                    delegate: ItemDelegate {
                        id: categoryDelegate
                        required property int index
                        required property string modelData
                        width: ListView.view.width
                        height: 48
                        text: modelData
                        highlighted: tab.categoryIndex === index
                        onClicked: tab.categoryIndex = index
                        contentItem: Label {
                            text: categoryDelegate.text
                            color: categoryDelegate.highlighted ? App.Theme.accent : App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontBody
                            font.weight: categoryDelegate.highlighted ? Font.Bold : Font.Normal
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: App.Theme.radiusSmall
                            color: categoryDelegate.highlighted ? App.Theme.accentSoft : categoryDelegate.hovered ? App.Theme.surfaceHover : "transparent"
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 14
                contentItem: ScrollView {
                    id: optionScroll
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical.policy: ScrollBar.AsNeeded

                    ColumnLayout {
                        width: optionScroll.availableWidth
                        spacing: 9

                        ColumnLayout {
                            visible: tab.categoryIndex === 0
                            Layout.fillWidth: true
                            spacing: 8
                            Label { text: qsTr("Presets"); color: App.Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            Repeater {
                                model: tab.presetModel
                                delegate: Button {
                                    id: presetButton
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 70
                                    onClicked: tab.choosePreset(modelData.id)
                                    contentItem: ColumnLayout {
                                        Label { text: presetButton.modelData.label; color: App.Theme.text; font.weight: Font.Bold }
                                        Label { Layout.fillWidth: true; text: presetButton.modelData.description; color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption }
                                    }
                                    background: Rectangle {
                                        radius: App.Theme.radiusMedium
                                        color: tab.preset === presetButton.modelData.id ? App.Theme.accentSoft : App.Theme.surfaceRaised
                                        border.width: tab.preset === presetButton.modelData.id || presetButton.visualFocus ? 2 : 1
                                        border.color: tab.preset === presetButton.modelData.id || presetButton.visualFocus ? App.Theme.accent : App.Theme.border
                                    }
                                }
                            }
                        }

                        ColumnLayout {
                            visible: tab.categoryIndex === 1
                            Layout.fillWidth: true
                            spacing: 10
                            Label { text: qsTr("Appearance"); color: App.Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Position"); description: qsTr("Choose the screen corner or edge for the overlay")
                                AppComboBox { model: tab.positionLabels; currentIndex: tab.indexOf(tab.positionValues, tab.position, 0); onActivated: function(index) { tab.position = tab.positionValues[index]; tab.changed() } }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Font size"); description: qsTr("%1 px").arg(tab.fontSize)
                                AppSlider { from: 8; to: 96; stepSize: 1; value: tab.fontSize; onMoved: { tab.fontSize = Math.round(value); tab.changed() } }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Background opacity"); description: Math.round(tab.backgroundAlpha * 100) + "%"
                                AppSlider { from: 0; to: 1; stepSize: 0.05; value: tab.backgroundAlpha; onMoved: { tab.backgroundAlpha = value; tab.changed() } }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Rounded corners"); description: qsTr("%1 px").arg(tab.roundCorners)
                                AppSlider { from: 0; to: 64; stepSize: 1; value: tab.roundCorners; onMoved: { tab.roundCorners = Math.round(value); tab.changed() } }
                            }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Compact layout"); AppSwitch { checked: tab.compact; onToggled: { tab.compact = checked; tab.changed() } } }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Horizontal layout"); AppSwitch { objectName: "mangoHudHorizontal"; checked: tab.horizontal; onToggled: { tab.horizontal = checked; tab.changed() } } }
                            Repeater {
                                model: tab.colorModel
                                delegate: SettingRow {
                                    id: colorRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    title: modelData.label
                                    description: "#" + tab.colorValue(modelData.id)
                                    RowLayout {
                                        spacing: 8
                                        Rectangle {
                                            Layout.preferredWidth: 26
                                            Layout.preferredHeight: 26
                                            radius: 6
                                            color: "#" + tab.colorValue(colorRow.modelData.id)
                                            border.width: 1
                                            border.color: App.Theme.borderStrong
                                        }
                                        AppTextField {
                                            objectName: "mangoHudColor_" + colorRow.modelData.id
                                            Layout.preferredWidth: 100
                                            text: tab.colorValue(colorRow.modelData.id)
                                            maximumLength: 7
                                            validator: RegularExpressionValidator { regularExpression: /#?[0-9A-Fa-f]{0,6}/ }
                                            onTextEdited: tab.setColor(colorRow.modelData.id, text)
                                        }
                                    }
                                }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Table columns"); description: qsTr("Cells per line in the vertical layout")
                                SpinBox { from: 1; to: 6; value: tab.tableColumns; onValueModified: { tab.tableColumns = value; tab.changed() } }
                            }
                        }

                        ColumnLayout {
                            visible: tab.categoryIndex === 2
                            Layout.fillWidth: true
                            spacing: 5
                            Label { text: qsTr("Metrics"); color: App.Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            Label { Layout.fillWidth: true; text: qsTr("Only options supported by the detected MangoHud configuration are selectable."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                            SettingRow {
                                Layout.fillWidth: true
                                title: qsTr("Frametime graph")
                                description: qsTr("Line graph of recent frame times")
                                AppSwitch {
                                    objectName: "mangoHudFrameTimingGraph"
                                    checked: tab.frameTimingGraph
                                    onToggled: { tab.frameTimingGraph = checked; tab.changed() }
                                }
                            }
                            Repeater {
                                model: tab.metricModel
                                delegate: SettingRow {
                                    id: metricRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    title: modelData.label
                                    description: tab.metricSupported(modelData.id) ? "" : qsTr("Unavailable in this MangoHud version")
                                    AppSwitch {
                                        objectName: "mangoHudMetric_" + metricRow.modelData.id
                                        enabled: tab.metricSupported(metricRow.modelData.id)
                                        checked: tab.hasMetric(metricRow.modelData.id)
                                        onToggled: tab.setMetric(metricRow.modelData.id, checked)
                                    }
                                }
                            }
                        }

                        ColumnLayout {
                            visible: tab.categoryIndex === 3
                            Layout.fillWidth: true
                            spacing: 10
                            Label { text: qsTr("Performance"); color: App.Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("FPS limit"); description: tab.fpsLimitOwner === "gamescope" ? qsTr("Controlled by Gamescope in the optimization profile") : qsTr("0 disables the Game Optimization limit")
                                AppTextField {
                                    Layout.preferredWidth: 100
                                    enabled: tab.fpsLimitOwner !== "gamescope"
                                    text: String(tab.fpsLimit)
                                    validator: IntValidator { bottom: 0; top: 1000 }
                                    onTextEdited: { tab.fpsLimit = Number(text || 0); tab.changed() }
                                }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Limiter method"); description: qsTr("Use only when required by the game")
                                AppComboBox { property var values: ["", "early", "late"]; enabled: tab.fpsLimitOwner !== "gamescope"; model: [qsTr("Default"), qsTr("Early"), qsTr("Late")]; currentIndex: tab.indexOf(values, tab.fpsLimitMethod, 0); onActivated: function(index) { tab.fpsLimitMethod = values[index]; tab.changed() } }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Vulkan present mode"); description: qsTr("Advanced override")
                                AppComboBox { property var values: ["", "immediate", "mailbox", "fifo", "fifo_relaxed"]; model: [qsTr("Application default"), "immediate", "mailbox", "fifo", "fifo_relaxed"]; currentIndex: tab.indexOf(values, tab.vulkanPresentMode, 0); onActivated: function(index) { tab.vulkanPresentMode = values[index]; tab.changed() } }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("VSync override"); description: qsTr("Advanced Vulkan option")
                                AppComboBox { property var values: [-2, -1, 0, 1, 2, 3]; model: [qsTr("Application default"), qsTr("Driver default"), qsTr("Adaptive"), qsTr("Off"), qsTr("Mailbox"), qsTr("On")]; currentIndex: tab.indexOf(values, tab.vsync, 0); onActivated: function(index) { tab.vsync = values[index]; tab.changed() } }
                            }
                        }

                        ColumnLayout {
                            visible: tab.categoryIndex === 4
                            Layout.fillWidth: true
                            spacing: 10
                            Label { text: qsTr("Logging"); color: App.Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Enable logging controls"); description: qsTr("Logs remain local and are never uploaded automatically"); AppSwitch { checked: tab.loggingEnabled; onToggled: { tab.loggingEnabled = checked; tab.changed() } } }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Log duration"); description: qsTr("Seconds")
                                AppTextField {
                                    enabled: tab.loggingEnabled
                                    Layout.preferredWidth: 110
                                    text: String(tab.logDuration)
                                    validator: IntValidator { bottom: 1; top: 86400 }
                                    onTextEdited: { tab.logDuration = Number(text || 1); tab.changed() }
                                }
                            }
                            SettingRow {
                                Layout.fillWidth: true; title: qsTr("Log interval"); description: qsTr("Seconds between samples")
                                AppTextField {
                                    enabled: tab.loggingEnabled
                                    Layout.preferredWidth: 110
                                    text: String(tab.logInterval)
                                    validator: DoubleValidator { bottom: 0; top: 60; decimals: 3 }
                                    onTextEdited: { tab.logInterval = Number(text || 0); tab.changed() }
                                }
                            }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Output folder"); description: qsTr("Game Optimization-owned log directory"); AppTextField { enabled: tab.loggingEnabled; Layout.preferredWidth: 310; text: tab.outputFolder; onTextEdited: { tab.outputFolder = text; tab.changed() } } }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Start logging key"); AppTextField { enabled: tab.loggingEnabled; Layout.preferredWidth: 170; text: tab.toggleLoggingKey; onTextEdited: { tab.toggleLoggingKey = text; tab.changed() } } }
                        }

                        ColumnLayout {
                            visible: tab.categoryIndex === 5
                            Layout.fillWidth: true
                            spacing: 10
                            Label { text: qsTr("Advanced"); color: App.Theme.text; font.pixelSize: 20; font.weight: Font.Bold }
                            SettingRow {
                                Layout.fillWidth: true
                                title: qsTr("Main executable")
                                description: tab.executableCandidates.length === 0
                                             ? qsTr("No supported executable was found in the game directory")
                                             : qsTr("Used for the official MangoHud per-application configuration")
                                AppComboBox {
                                    Layout.preferredWidth: 330
                                    enabled: tab.executableCandidates.length > 0
                                    model: tab.executableLabels
                                    currentIndex: tab.indexOf(tab.executableValues, tab.selectedExecutable, 0)
                                    onActivated: function(index) {
                                        tab.selectedExecutable = tab.executableValues[index]
                                        tab.changed()
                                    }
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: strategyText.implicitHeight + 22
                                radius: App.Theme.radiusMedium
                                color: tab.strategyStatus === "application_config_conflict"
                                       || tab.requiresSteamRestart ? App.Theme.warningSoft : App.Theme.successSoft
                                Label {
                                    id: strategyText
                                    anchors.fill: parent
                                    anchors.margins: 11
                                    text: tab.strategyLabel()
                                    color: tab.strategyStatus === "application_config_conflict"
                                           || tab.requiresSteamRestart ? App.Theme.warning : App.Theme.success
                                    wrapMode: Text.WordWrap
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: tab.applicationConfigPath.length > 0
                                text: qsTr("Application configuration: %1").arg(tab.applicationConfigPath)
                                color: App.Theme.textMuted
                                elide: Text.ElideMiddle
                                ToolTip.visible: applicationConfigHover.hovered
                                ToolTip.text: text
                                HoverHandler { id: applicationConfigHover }
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: tab.conflictPath.length > 0
                                text: qsTr("Existing configuration kept: %1").arg(tab.conflictPath)
                                color: App.Theme.warning
                                elide: Text.ElideMiddle
                                ToolTip.visible: conflictHover.hovered
                                ToolTip.text: text
                                HoverHandler { id: conflictHover }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppButton {
                                    objectName: "mangoHudConfigToggle"
                                    Layout.fillWidth: true
                                    kind: "secondary"
                                    compact: true
                                    text: (tab.configExpanded ? "\u25BE " : "\u25B8 ") + qsTr("Generated configuration")
                                    onClicked: tab.configExpanded = !tab.configExpanded
                                }
                                AppButton {
                                    objectName: "mangoHudConfigCopy"
                                    kind: "secondary"
                                    compact: true
                                    text: qsTr("Copy")
                                    enabled: tab.configPreview.length > 0
                                    onClicked: tab.copyConfig()
                                }
                            }
                            Rectangle {
                                visible: tab.configExpanded
                                Layout.fillWidth: true; Layout.preferredHeight: 260; radius: App.Theme.radiusMedium; color: App.Theme.input; clip: true
                                ScrollView { anchors.fill: parent; anchors.margins: 10; contentWidth: availableWidth; TextEdit { id: configTextEdit; objectName: "mangoHudConfigText"; width: parent.width; readOnly: true; selectByMouse: true; text: tab.configPreview; color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere } }
                            }
                            Label { Layout.fillWidth: true; text: qsTr("Profile: %1").arg(tab.profilePath || "-"); color: App.Theme.textMuted; elide: Text.ElideMiddle; ToolTip.visible: profileHover.hovered; ToolTip.text: text; HoverHandler { id: profileHover } }
                            Label { Layout.fillWidth: true; text: qsTr("Configuration: %1").arg(tab.configPath || "-"); color: App.Theme.textMuted; elide: Text.ElideMiddle; ToolTip.visible: configHover.hovered; ToolTip.text: text; HoverHandler { id: configHover } }
                            AppButton { text: qsTr("Open directory"); iconSource: App.UiIcons.fileFolder; kind: "secondary"; onClicked: if (tab.controller && tab.controller.openMangoHudDirectory) tab.controller.openMangoHudDirectory(tab.gameId) }
                            Label { Layout.fillWidth: true; text: qsTr("No shell commands or global MangoHud configuration are edited."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.preferredWidth: Math.min(330, tab.width * 0.28)
                Layout.fillHeight: true
                padding: 14
                contentItem: ColumnLayout {
                    spacing: 10
                    Label { text: qsTr("Layout preview - sample values"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Label { Layout.fillWidth: true; text: qsTr("Built from the current settings. Values are examples, not measurements, and the look is approximate."); color: App.Theme.warning; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                    Rectangle {
                        id: previewSurface
                        objectName: "mangoHudPreviewSurface"
                        readonly property var layout: tab.previewLayout || ({})
                        readonly property string corner: String(layout.position || "top-left")
                        readonly property bool horizontalLayout: Boolean(layout.horizontal)
                        readonly property bool compactLayout: Boolean(layout.compact)
                        // The surface stands for a 1280 px wide screen.
                        readonly property real screenScale: width / 1280
                        readonly property real textSize: Math.max(7, Number(layout.fontSize || 24) * Math.max(screenScale, 0.3))
                        readonly property real cellSpacing: compactLayout ? 2 : 5
                        Layout.fillWidth: true
                        Layout.preferredHeight: width * 0.62
                        radius: App.Theme.radiusMedium
                        color: "#243143"
                        clip: true
                        Rectangle {
                            id: hudPreview
                            objectName: "mangoHudPreviewHud"
                            visible: !Boolean(previewSurface.layout.empty)
                            readonly property real inset: previewSurface.compactLayout ? 3 : 6
                            width: Math.min(parent.width - 8, hudContent.implicitWidth + inset * 2)
                            height: Math.min(parent.height - 8, hudContent.implicitHeight + inset * 2)
                            radius: Math.min(Number(previewSurface.layout.roundCorners || 0) * Math.max(previewSurface.screenScale, 0.3), Math.min(width, height) / 2)
                            color: tab.hudBackground(String(previewSurface.layout.backgroundColor || "#020202"), Number(previewSurface.layout.backgroundAlpha !== undefined ? previewSurface.layout.backgroundAlpha : 0.5))
                            // MangoHud has no border option; none is drawn here either.
                            border.width: 0
                            x: previewSurface.corner.indexOf("right") >= 0 ? parent.width - width - 4
                               : previewSurface.corner.indexOf("center") >= 0 ? (parent.width - width) / 2 : 4
                            y: previewSurface.corner.indexOf("bottom") === 0 ? parent.height - height - 4
                               : previewSurface.corner.indexOf("middle") === 0 ? (parent.height - height) / 2 : 4
                            clip: true
                            Column {
                                id: hudContent
                                x: hudPreview.inset
                                y: hudPreview.inset
                                spacing: previewSurface.cellSpacing
                                Grid {
                                    objectName: "mangoHudPreviewGrid"
                                    visible: !previewSurface.horizontalLayout
                                    columns: Math.max(1, Number(previewSurface.layout.tableColumns || 3))
                                    columnSpacing: previewSurface.cellSpacing * 2
                                    rowSpacing: previewSurface.compactLayout ? 0 : previewSurface.cellSpacing / 2
                                    Repeater {
                                        model: previewSurface.horizontalLayout ? [] : (previewSurface.layout.cells || [])
                                        delegate: Label {
                                            required property var modelData
                                            text: String(modelData.text || "")
                                            color: String(modelData.color || "transparent")
                                            font.pixelSize: previewSurface.textSize
                                            font.weight: modelData.label ? Font.Bold : Font.Normal
                                        }
                                    }
                                }
                                Row {
                                    objectName: "mangoHudPreviewRow"
                                    visible: previewSurface.horizontalLayout
                                    spacing: previewSurface.cellSpacing * 2
                                    Repeater {
                                        model: previewSurface.horizontalLayout ? (previewSurface.layout.inlineCells || []) : []
                                        delegate: Label {
                                            required property var modelData
                                            text: String(modelData.text || "")
                                            color: String(modelData.color || "transparent")
                                            font.pixelSize: previewSurface.textSize
                                            font.weight: modelData.label ? Font.Bold : Font.Normal
                                        }
                                    }
                                }
                                Row {
                                    objectName: "mangoHudPreviewGraph"
                                    visible: Boolean(previewSurface.layout.frameTimingGraph)
                                    height: Math.max(10, previewSurface.textSize * 2)
                                    spacing: 1
                                    Repeater {
                                        model: [6, 7, 6, 8, 6, 5, 7, 12, 6, 6, 7, 6, 5, 6, 9, 6, 6, 7, 6, 6]
                                        delegate: Rectangle {
                                            required property int modelData
                                            anchors.bottom: parent ? parent.bottom : undefined
                                            width: Math.max(2, previewSurface.textSize * 0.45)
                                            height: parent ? parent.height * modelData / 12 : 0
                                            color: String(previewSurface.layout.frametimeColor || "#00FF00")
                                        }
                                    }
                                }
                            }
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: Boolean(previewSurface.layout.empty)
                            text: qsTr("MangoHud shows no metrics with these settings")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: (tab.previewLayout.hiddenMetrics || []).length > 0
                        text: qsTr("Some GPU, CPU or frametime values are hidden: MangoHud shows them only with GPU usage, CPU usage or FPS enabled.")
                        color: App.Theme.warning
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        AppButton {
                            objectName: "mangoHudLivePreviewButton"
                            Layout.fillWidth: true
                            text: qsTr("Live preview")
                            kind: "secondary"
                            enabled: tab.profileLoaded && !tab.livePreviewRunning && tab.profileEnabled && tab.metrics.length > 0
                            onClicked: tab.startLivePreview()
                        }
                        AppButton {
                            objectName: "mangoHudStopLivePreviewButton"
                            visible: tab.livePreviewRunning
                            text: qsTr("Close preview")
                            kind: "secondary"
                            onClicked: tab.stopLivePreview()
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Runs mangohud vkcube with the current settings. Nothing is saved.")
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        objectName: "mangoHudLivePreviewMessage"
                        Layout.fillWidth: true
                        visible: tab.livePreviewMessage.length > 0
                        text: tab.livePreviewMessage
                        color: App.Theme.danger
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }
                    Label { Layout.fillWidth: true; text: qsTr("Preset: %1").arg(tab.presetModel[tab.indexOf(tab.presetModel.map(function(item) { return item.id }), tab.preset, 0)].label); color: App.Theme.textSecondary; elide: Text.ElideRight }
                    Label { Layout.fillWidth: true; text: qsTr("%1 metrics selected").arg(tab.metrics.length); color: App.Theme.textSecondary }
                    Item { Layout.fillHeight: true }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: tab.errorMessage.length > 0
            text: tab.errorMessage
            color: App.Theme.danger
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: tab.dirty ? qsTr("Unsaved changes") : qsTr("Profile saved"); color: tab.dirty ? App.Theme.warning : App.Theme.textMuted }
            AppButton {
                objectName: "resetMangoHudProfileButton"
                text: qsTr("Reset Game Optimization settings")
                kind: "secondary"
                onClicked: if (tab.controller && tab.controller.resetMangoHudProfile) tab.applyProfile(tab.controller.resetMangoHudProfile(tab.gameId))
            }
            AppButton {
                objectName: "saveMangoHudProfileButton"
                text: qsTr("Save profile")
                kind: "primary"
                enabled: tab.profileLoaded && (!tab.profileEnabled || tab.available)
                onClicked: tab.saveProfile()
            }
        }
    }

    onGameIdChanged: loadProfile()
    Component.onCompleted: loadProfile()
}
