pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import ".." as App

Item {
    id: page

    property var controller
    property var gamesData: controller && controller.games ? controller.games : []
    property var componentsData: controller && controller.narratorComponents
                                 ? controller.narratorComponents : []
    property string selectedGameId: ""
    property var sessionData: ({ "status": "idle" })

    property bool narratorEnabled: false
    property string sourceMode: "auto"
    property string captureSource: "window"
    property string ocrProviderId: ""
    property string translationProviderId: ""
    property string translationProfile: ""
    property var translationProfiles: []
    property string ttsProviderId: ""
    property string voiceId: ""
    property var voices: []
    property real narratorVolume: 0.85
    property real speechRate: 1.0
    property real cropX: 0.05
    property real cropY: 0.62
    property real cropWidth: 0.90
    property real cropHeight: 0.30

    readonly property var gameRows: buildGameRows()
    readonly property string sessionStatus: String(value(
                                                        sessionData,
                                                        ["status"],
                                                        "idle"))
    readonly property bool sessionActive: [
        "starting", "selecting_source", "listening", "ocr",
        "translating", "speaking", "stopping"
    ].indexOf(sessionStatus) >= 0
    readonly property bool componentsReady: requiredComponentsReady()

    signal toastRequested(string message, string tone)

    function value(source, keys, fallback) {
        var values = source || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = values[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function buildGameRows() {
        var rows = []
        var source = gamesData || []
        for (var i = 0; i < source.length; ++i) {
            var game = source[i] || {}
            var gameId = String(value(game, ["gameId", "game_id", "id"], ""))
            if (!gameId.length)
                continue
            rows.push({
                "id": gameId,
                "label": String(value(game, ["name", "title"], gameId))
            })
        }
        rows.sort(function(left, right) {
            return left.label.localeCompare(right.label)
        })
        return rows
    }

    function gameLabels() {
        var labels = []
        for (var i = 0; i < gameRows.length; ++i)
            labels.push(gameRows[i].label)
        return labels
    }

    function selectedGameIndex() {
        for (var i = 0; i < gameRows.length; ++i) {
            if (gameRows[i].id === selectedGameId)
                return i
        }
        return gameRows.length ? 0 : -1
    }

    function indexOfValue(values, wanted, fallback) {
        for (var i = 0; i < values.length; ++i) {
            if (String(values[i]) === String(wanted))
                return i
        }
        return fallback
    }

    function ensureSelection() {
        if (!gameRows.length) {
            selectedGameId = ""
            sessionData = ({ "status": "idle" })
            return
        }
        var index = selectedGameIndex()
        selectGame(gameRows[Math.max(0, index)].id)
    }

    function selectGame(gameId) {
        var normalized = String(gameId || "")
        if (!normalized.length)
            return
        selectedGameId = normalized
        loadSettings()
        refreshSession()
    }

    function loadSettings() {
        if (!selectedGameId.length || !controller
                || typeof controller.getNarratorGameSettings !== "function")
            return
        var settings = controller.getNarratorGameSettings(selectedGameId) || {}
        narratorEnabled = Boolean(value(settings, ["enabled"], false))
        sourceMode = String(value(settings, ["source_mode", "sourceMode"], "auto"))
        captureSource = String(value(settings, ["capture_source", "captureSource"], "window"))
        ocrProviderId = String(value(settings, ["ocr_provider_id", "ocrProviderId"], ""))
        translationProviderId = String(value(settings, ["translation_provider_id", "translationProviderId"], ""))
        translationProfile = String(value(
                                        settings,
                                        ["translation_profile_id", "translationProfileId"],
                                        ""))
        translationProfiles = value(settings, ["translationProfiles"], []) || []
        ttsProviderId = String(value(settings, ["tts_provider_id", "ttsProviderId"], ""))
        voiceId = String(value(settings, ["voice_id", "voiceId"], ""))
        voices = value(settings, ["voices"], []) || []
        narratorVolume = Number(value(settings, ["volume"], 0.85))
        speechRate = Number(value(settings, ["speech_rate", "speechRate"], 1.0))
        var region = value(settings, ["subtitle_region", "subtitleRegion"], ({})) || {}
        cropX = Number(value(region, ["x"], 0.05))
        cropY = Number(value(region, ["y"], 0.62))
        cropWidth = Number(value(region, ["width"], 0.90))
        cropHeight = Number(value(region, ["height"], 0.30))
    }

    function settingsPayload() {
        return {
            "enabled": narratorEnabled,
            "sourceMode": sourceMode,
            "captureSource": captureSource,
            "ocrProviderId": ocrProviderId,
            "translationProviderId": translationProviderId,
            "translationProfileId": translationProfile,
            "ttsProviderId": ttsProviderId,
            "voiceId": voiceId,
            "volume": narratorVolume,
            "speechRate": speechRate,
            "subtitleRegion": {
                "x": cropX,
                "y": cropY,
                "width": cropWidth,
                "height": cropHeight
            }
        }
    }

    function saveSettings() {
        if (!selectedGameId.length || !controller
                || typeof controller.saveNarratorGameSettings !== "function")
            return false
        var saved = Boolean(controller.saveNarratorGameSettings(
                                selectedGameId, settingsPayload()))
        return saved
    }

    function refreshSession() {
        if (!selectedGameId.length || !controller
                || typeof controller.getNarratorSessionState !== "function") {
            sessionData = ({ "status": "idle" })
            return
        }
        sessionData = controller.getNarratorSessionState(selectedGameId)
                      || ({ "status": "idle" })
    }

    function startNarrator() {
        if (!saveSettings() || !controller
                || typeof controller.startNarrator !== "function")
            return
        controller.startNarrator(selectedGameId)
        refreshSession()
    }

    function stopNarrator() {
        if (controller && typeof controller.stopNarrator === "function")
            controller.stopNarrator()
        refreshSession()
    }

    function resetSubtitleRegion() {
        cropX = 0.05
        cropY = 0.62
        cropWidth = 0.90
        cropHeight = 0.30
    }

    function requiredComponentsReady() {
        var required = {
            "capture": false,
            "ocr": false,
            "translation": false,
            "tts": false,
            "audio": false
        }
        var source = componentsData || []
        for (var i = 0; i < source.length; ++i) {
            var kind = String(value(source[i], ["kind"], ""))
            if (required[kind] !== undefined
                    && String(value(source[i], ["state"], "")) === "available")
                required[kind] = true
        }
        for (var key in required) {
            if (!required[key])
                return false
        }
        return true
    }

    function optionLabels(options) {
        var labels = []
        for (var i = 0; i < (options || []).length; ++i)
            labels.push(String(value(options[i], ["name", "id"], "")))
        return labels
    }

    function optionIds(options) {
        var ids = []
        for (var i = 0; i < (options || []).length; ++i)
            ids.push(String(value(options[i], ["id"], "")))
        return ids
    }

    function translationProfileLabels(options) {
        var labels = []
        for (var i = 0; i < (options || []).length; ++i) {
            var profileId = String(value(options[i], ["id"], ""))
            if (profileId === "balanced")
                labels.push(qsTr("Balanced"))
            else if (profileId === "fast")
                labels.push(qsTr("Fast"))
            else
                labels.push(String(value(options[i], ["name", "id"], "")))
        }
        return labels
    }

    function componentName(component) {
        var componentId = String(value(component, ["componentId", "component_id"], ""))
        if (componentId === "capture.portal-pipewire")
            return qsTr("Wayland portal and PipeWire capture")
        if (componentId === "ocr.english-local")
            return qsTr("Local English subtitle OCR")
        if (componentId === "translation.opus-en-pl")
            return qsTr("Local English to Polish translation")
        if (componentId === "tts.polish-voice")
            return qsTr("Local Polish voice")
        if (componentId === "audio.qt-pcm")
            return qsTr("PCM audio output")
        return String(value(component, ["name"], componentId))
    }

    function componentKind(kind) {
        if (kind === "capture")
            return qsTr("Screen capture")
        if (kind === "ocr")
            return qsTr("Text recognition")
        if (kind === "translation")
            return qsTr("Translation")
        if (kind === "tts")
            return qsTr("Polish speech")
        if (kind === "audio")
            return qsTr("Audio output")
        return qsTr("Component")
    }

    function componentDescription(component) {
        var code = String(value(component, ["descriptionCode", "description_code"], ""))
        if (code === "capture_runtime")
            return qsTr("Capture permission is requested through the system portal when a session starts.")
        if (code === "ocr_model_required")
            return qsTr("A verified local English OCR runtime and model are required.")
        if (code === "translation_model_required")
            return qsTr("A verified local English to Polish translation model is required.")
        if (code === "polish_voice_required")
            return qsTr("A Polish voice model with verified licensing is required.")
        if (code === "audio_runtime")
            return qsTr("Generated PCM audio is played through the sandbox audio service.")
        return ""
    }

    function componentStateLabel(state) {
        if (state === "available")
            return qsTr("Available")
        if (state === "not_installed")
            return qsTr("Not installed")
        if (state === "installing")
            return qsTr("Installing")
        if (state === "update_available")
            return qsTr("Update available")
        if (state === "unsupported")
            return qsTr("Unavailable in this build")
        if (state === "error")
            return qsTr("Error")
        return qsTr("Unknown")
    }

    function componentTone(state) {
        if (state === "available")
            return "available"
        if (state === "update_available" || state === "installing")
            return "warning"
        if (state === "error")
            return "failed"
        return "not checked"
    }

    function sessionLabel(state) {
        if (state === "idle")
            return qsTr("Stopped")
        if (state === "starting")
            return qsTr("Starting")
        if (state === "selecting_source")
            return qsTr("Select a screen or window")
        if (state === "listening")
            return qsTr("Waiting for subtitles")
        if (state === "ocr")
            return qsTr("Recognizing text")
        if (state === "translating")
            return qsTr("Translating")
        if (state === "speaking")
            return qsTr("Speaking")
        if (state === "stopping")
            return qsTr("Stopping")
        if (state === "stopped")
            return qsTr("Stopped")
        if (state === "error")
            return qsTr("Error")
        return qsTr("Unknown")
    }

    function sessionTone(state) {
        if (state === "error")
            return "failed"
        if (state === "idle" || state === "stopped")
            return "paused"
        return "available"
    }

    function captureStateLabel(state) {
        if (state === "permission_required" || state === "selecting_source")
            return qsTr("Waiting for portal permission or source")
        if (state === "starting")
            return qsTr("Starting capture")
        if (state === "active")
            return qsTr("Active")
        if (state === "cancelled")
            return qsTr("Selection cancelled")
        if (state === "permission_denied")
            return qsTr("Permission denied")
        if (state === "error" || state === "source_lost")
            return qsTr("Capture error")
        if (state === "unavailable")
            return qsTr("Unavailable")
        return qsTr("Stopped")
    }

    function ocrStateLabel(state) {
        if (state === "loading")
            return qsTr("Loading")
        if (state === "processing")
            return qsTr("Processing")
        if (state === "ready")
            return qsTr("Ready")
        if (state === "error")
            return qsTr("OCR error")
        return qsTr("Component missing")
    }

    function inferenceStateLabel(state, processingLabel, errorLabel) {
        if (state === "loading")
            return qsTr("Loading")
        if (state === "processing")
            return processingLabel
        if (state === "ready")
            return qsTr("Ready")
        if (state === "error")
            return errorLabel
        return qsTr("Component missing")
    }

    function audioStateLabel(state) {
        if (state === "waiting")
            return qsTr("Waiting for audio")
        if (state === "speaking")
            return qsTr("Speaking")
        if (state === "ready")
            return qsTr("Ready")
        if (state === "error")
            return qsTr("Audio error")
        if (state === "stopped")
            return qsTr("Stopped")
        if (state === "unavailable")
            return qsTr("Unavailable")
        return qsTr("Component missing")
    }

    function formatBytes(raw) {
        if (raw === undefined || raw === null || Number(raw) < 0)
            return qsTr("Size not published")
        var amount = Number(raw)
        var units = ["B", "KiB", "MiB", "GiB"]
        var unit = 0
        while (amount >= 1024 && unit < units.length - 1) {
            amount /= 1024
            unit++
        }
        return (unit === 0 ? Math.round(amount) : amount.toFixed(1)) + " " + units[unit]
    }

    function formatLatency(raw) {
        if (raw === undefined || raw === null || !isFinite(Number(raw)))
            return qsTr("Not measured")
        return qsTr("%1 ms").arg(Number(raw).toFixed(0))
    }

    function runComponentAction(component) {
        if (!controller)
            return
        var componentId = String(value(component, ["componentId", "component_id"], ""))
        var state = String(value(component, ["state"], ""))
        if (state === "update_available"
                && typeof controller.updateNarratorComponent === "function")
            controller.updateNarratorComponent(componentId)
        else if (state === "available" && Boolean(value(component, ["managed"], false))
                 && typeof controller.removeNarratorComponent === "function")
            controller.removeNarratorComponent(componentId)
        else if (typeof controller.installNarratorComponent === "function")
            controller.installNarratorComponent(componentId)
    }

    onGameRowsChanged: Qt.callLater(ensureSelection)
    Component.onCompleted: Qt.callLater(ensureSelection)

    Connections {
        target: page.controller || null
        ignoreUnknownSignals: true

        function onNarratorChanged(gameId) {
            if (!gameId || String(gameId) === page.selectedGameId)
                page.refreshSession()
        }

        function onNarratorComponentsChanged() {
            page.loadSettings()
            page.refreshSession()
        }
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: scroll.availableWidth
            spacing: App.Theme.spacingLarge

            Item { Layout.preferredHeight: App.Theme.contentPadding - 4 }

            PageHeader {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                title: qsTr("Narrator")
                subtitle: qsTr("Local English subtitle narration in Polish")

                StatusBadge {
                    text: page.sessionLabel(page.sessionStatus)
                    status: page.sessionTone(page.sessionStatus)
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 12

                    Label {
                        text: qsTr("Game")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Narrator settings are stored separately for each game. Models and voices are shared between games.")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    AppComboBox {
                        id: gameSelector
                        objectName: "narratorGameSelector"
                        Layout.fillWidth: true
                        model: page.gameLabels()
                        currentIndex: page.selectedGameIndex()
                        enabled: page.gameRows.length > 0 && !page.sessionActive
                        onActivated: function(index) {
                            if (index >= 0 && index < page.gameRows.length)
                                page.selectGame(page.gameRows[index].id)
                        }
                    }

                    Label {
                        visible: page.gameRows.length === 0
                        Layout.fillWidth: true
                        text: qsTr("No games are available. Scan or add a game first.")
                        color: App.Theme.warning
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }
                }
            }

            GridLayout {
                id: narratorSettingsGrid
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                columns: page.width >= 1050 ? 2 : 1
                columnSpacing: App.Theme.spacingLarge
                rowSpacing: App.Theme.spacingLarge

                SurfaceCard {
                    Layout.fillWidth: narratorSettingsGrid.columns === 1
                    Layout.fillHeight: true
                    Layout.minimumWidth: narratorSettingsGrid.columns === 2 ? 320 : 0
                    Layout.preferredWidth: narratorSettingsGrid.columns === 2 ? 340 : -1
                    padding: 20

                    contentItem: ColumnLayout {
                        spacing: 10

                        Label {
                            text: qsTr("Narration")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10

                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Enabled for this game")
                                    color: App.Theme.text
                                    font.pixelSize: App.Theme.fontBody
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                }

                                AppSwitch {
                                    checked: page.narratorEnabled
                                    enabled: page.selectedGameId.length > 0 && !page.sessionActive
                                    onToggled: page.narratorEnabled = checked
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("The session processes subtitles only while the selected game is running")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Subtitle source")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.DemiBold
                            }

                            AppComboBox {
                                property var values: ["auto", "ocr"]
                                Layout.fillWidth: true
                                model: [qsTr("Auto"), qsTr("OCR")]
                                currentIndex: page.indexOfValue(values, page.sourceMode, 0)
                                enabled: !page.sessionActive
                                onActivated: function(index) { page.sourceMode = values[index] }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Auto can use a supported adapter later and keeps OCR as the universal fallback")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Capture source")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.DemiBold
                            }

                            AppComboBox {
                                property var values: ["window", "monitor"]
                                Layout.fillWidth: true
                                model: [qsTr("Window"), qsTr("Monitor")]
                                currentIndex: page.indexOfValue(values, page.captureSource, 0)
                                enabled: !page.sessionActive
                                onActivated: function(index) { page.captureSource = values[index] }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("The system portal asks which monitor or window may be captured")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    padding: 20

                    contentItem: ColumnLayout {
                        spacing: 10

                        Label {
                            text: qsTr("Voice and translation")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Translation profile")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }

                        AppComboBox {
                            property var values: page.optionIds(page.translationProfiles)
                            Layout.fillWidth: true
                            model: page.translationProfileLabels(page.translationProfiles)
                            currentIndex: page.indexOfValue(values, page.translationProfile, 0)
                            enabled: values.length > 0 && !page.sessionActive
                            onActivated: function(index) {
                                if (index >= 0 && index < values.length)
                                    page.translationProfile = values[index]
                            }
                        }

                        Label {
                            visible: page.translationProfiles.length === 0
                            Layout.fillWidth: true
                            text: qsTr("Install the verified translation component to enable local English to Polish translation")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Polish voice")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }

                        AppComboBox {
                            property var values: page.optionIds(page.voices)
                            Layout.fillWidth: true
                            model: page.optionLabels(page.voices)
                            currentIndex: page.indexOfValue(values, page.voiceId, 0)
                            enabled: values.length > 0 && !page.sessionActive
                            onActivated: function(index) {
                                if (index >= 0 && index < values.length)
                                    page.voiceId = values[index]
                            }
                        }

                        Label {
                            visible: page.voices.length === 0
                            Layout.fillWidth: true
                            text: qsTr("Install the verified Polish voice component to enable speech")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }

                        Divider { Layout.fillWidth: true }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Volume")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: qsTr("%1%").arg(Math.round(page.narratorVolume * 100))
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }

                        AppSlider {
                            Layout.fillWidth: true
                            from: 0
                            to: 1
                            stepSize: 0.05
                            value: page.narratorVolume
                            enabled: !page.sessionActive
                            onMoved: page.narratorVolume = value
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Speech speed")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: qsTr("%1x").arg(page.speechRate.toFixed(2))
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }

                        AppSlider {
                            Layout.fillWidth: true
                            from: 0.5
                            to: 2.0
                            stepSize: 0.05
                            value: page.speechRate
                            enabled: !page.sessionActive
                            onMoved: page.speechRate = value
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Subtitle region")
                                color: App.Theme.text
                                font.pixelSize: 17
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Only this normalized part of the selected image is sent to OCR")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                        AppButton {
                            text: qsTr("Reset to bottom area")
                            compact: true
                            enabled: !page.sessionActive
                            onClicked: page.resetSubtitleRegion()
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(150, width * 0.22)
                        Layout.maximumHeight: 250
                        radius: App.Theme.radiusMedium
                        color: App.Theme.backgroundElevated
                        border.width: 1
                        border.color: App.Theme.border
                        clip: true

                        Label {
                            anchors.centerIn: parent
                            text: qsTr("Captured screen or window")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontBody
                        }

                        Rectangle {
                            x: page.cropX * parent.width
                            y: page.cropY * parent.height
                            width: page.cropWidth * parent.width
                            height: page.cropHeight * parent.height
                            color: App.Theme.accentSoft
                            border.width: 2
                            border.color: App.Theme.accent

                            Label {
                                anchors.centerIn: parent
                                text: qsTr("OCR region")
                                color: App.Theme.accent
                                font.pixelSize: App.Theme.fontCaption
                                font.weight: Font.Bold
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 900 ? 2 : 1
                        columnSpacing: 22
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Left"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropX * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0
                                to: Math.max(0, 1 - page.cropWidth)
                                stepSize: 0.01
                                value: page.cropX
                                enabled: !page.sessionActive
                                onMoved: page.cropX = value
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Top"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropY * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0
                                to: Math.max(0, 1 - page.cropHeight)
                                stepSize: 0.01
                                value: page.cropY
                                enabled: !page.sessionActive
                                onMoved: page.cropY = value
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Width"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropWidth * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0.05
                                to: Math.max(0.05, 1 - page.cropX)
                                stepSize: 0.01
                                value: page.cropWidth
                                enabled: !page.sessionActive
                                onMoved: page.cropWidth = value
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Height"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropHeight * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0.05
                                to: Math.max(0.05, 1 - page.cropY)
                                stepSize: 0.01
                                value: page.cropHeight
                                enabled: !page.sessionActive
                                onMoved: page.cropHeight = value
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                spacing: 10

                Label {
                    text: qsTr("Optional components")
                    color: App.Theme.text
                    font.pixelSize: 20
                    font.weight: Font.Bold
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("OCR, translation, and voice models are installed in the user data directory and shared by all games. No model is bundled with the base application.")
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontCaption
                    wrapMode: Text.WordWrap
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: page.width >= 1050 ? 2 : 1
                    columnSpacing: 12
                    rowSpacing: 12

                    Repeater {
                        model: page.componentsData || []

                        delegate: SurfaceCard {
                            id: componentCard
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: componentColumn.implicitHeight + 36
                            padding: 18

                            readonly property string componentState: String(
                                                                         page.value(
                                                                             modelData,
                                                                             ["state"],
                                                                             "unknown"))
                            readonly property bool managed: Boolean(page.value(modelData, ["managed"], false))
                            readonly property bool installable: Boolean(page.value(
                                                                            modelData,
                                                                            ["installable", "canInstall"],
                                                                            false))

                            contentItem: ColumnLayout {
                                id: componentColumn
                                spacing: 8

                                RowLayout {
                                    Layout.fillWidth: true
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Label {
                                            Layout.fillWidth: true
                                            text: page.componentName(componentCard.modelData)
                                            color: App.Theme.text
                                            font.pixelSize: App.Theme.fontBodyLarge
                                            font.weight: Font.Bold
                                            wrapMode: Text.WordWrap
                                        }
                                        Label {
                                            text: page.componentKind(String(page.value(componentCard.modelData, ["kind"], "")))
                                            color: App.Theme.textMuted
                                            font.pixelSize: App.Theme.fontCaption
                                        }
                                    }
                                    StatusBadge {
                                        text: page.componentStateLabel(componentCard.componentState)
                                        status: page.componentTone(componentCard.componentState)
                                    }
                                }

                                Label {
                                    visible: page.componentDescription(componentCard.modelData).length > 0
                                    Layout.fillWidth: true
                                    text: page.componentDescription(componentCard.modelData)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                    wrapMode: Text.WordWrap
                                }

                                Label {
                                    visible: String(page.value(componentCard.modelData, ["message"], "")).length > 0
                                    Layout.fillWidth: true
                                    text: App.I18n.message(String(page.value(componentCard.modelData, ["message"], "")))
                                    color: componentCard.componentState === "error"
                                           ? App.Theme.danger : App.Theme.textMuted
                                    font.pixelSize: App.Theme.fontCaption
                                    wrapMode: Text.WordWrap
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 14
                                    Label {
                                        text: qsTr("Download: %1").arg(page.formatBytes(
                                                                          page.value(
                                                                              componentCard.modelData,
                                                                              ["downloadSizeBytes", "download_size_bytes"],
                                                                              null)))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: page.value(componentCard.modelData, ["installedSizeBytes", "installed_size_bytes"], null) !== null
                                        text: qsTr("Installed: %1").arg(page.formatBytes(
                                                                           page.value(
                                                                               componentCard.modelData,
                                                                               ["installedSizeBytes", "installed_size_bytes"],
                                                                               null)))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["version"], "")).length > 0
                                        text: qsTr("Version: %1").arg(page.value(componentCard.modelData, ["version"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["licenseId", "license_id"], "")).length > 0
                                        text: qsTr("License: %1").arg(page.value(componentCard.modelData, ["licenseId", "license_id"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["runtimeLicenseId"], "")).length > 0
                                        text: qsTr("Runtime license: %1").arg(page.value(componentCard.modelData, ["runtimeLicenseId"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["artifactLicenseId"], "")).length > 0
                                        text: qsTr("Model license: %1").arg(page.value(componentCard.modelData, ["artifactLicenseId"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["attribution"], "")).length > 0
                                        text: qsTr("Attribution: %1").arg(page.value(componentCard.modelData, ["attribution"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                }

                                AppButton {
                                    Layout.alignment: Qt.AlignRight
                                    compact: true
                                    text: componentCard.componentState === "update_available"
                                          ? qsTr("Update")
                                          : componentCard.componentState === "available" && componentCard.managed
                                            ? qsTr("Remove") : qsTr("Install")
                                    kind: componentCard.componentState === "available" && componentCard.managed
                                          ? "danger" : "secondary"
                                    enabled: !page.sessionActive
                                             && (componentCard.componentState === "update_available"
                                                 || (componentCard.componentState === "available" && componentCard.managed)
                                                 || componentCard.installable)
                                    toolTip: enabled ? "" : qsTr("No verified download is configured for this component")
                                    onClicked: page.runComponentAction(componentCard.modelData)
                                }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Narrator session")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }
                        StatusBadge {
                            text: page.sessionLabel(page.sessionStatus)
                            status: page.sessionTone(page.sessionStatus)
                        }
                    }

                    Label {
                        visible: String(page.value(page.sessionData, ["message"], "")).length > 0
                        Layout.fillWidth: true
                        text: App.I18n.message(String(page.value(page.sessionData, ["message"], "")))
                        color: page.sessionStatus === "error" ? App.Theme.danger : App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 950 ? 3 : 1
                        columnSpacing: 18
                        rowSpacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Last detected English phrase"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.sessionData, ["lastDetectedText", "last_detected_text"], qsTr("None")))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Polish translation"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.sessionData, ["lastTranslation", "last_translation"], qsTr("None")))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Last spoken phrase"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.sessionData, ["lastSpokenText", "last_spoken_text"], qsTr("None")))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 950 ? 3 : 1
                        columnSpacing: 18
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Capture status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.captureStateLabel(String(page.value(page.sessionData, ["captureState"], "stopped")))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Translation status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.inferenceStateLabel(String(page.value(page.sessionData, ["translationStatus"], "component_missing")), qsTr("Translating"), qsTr("Translation error"))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Speech status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.inferenceStateLabel(String(page.value(page.sessionData, ["ttsStatus"], "component_missing")), qsTr("Generating speech"), qsTr("Speech error"))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Audio status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.audioStateLabel(String(page.value(page.sessionData, ["audioStatus"], "component_missing")))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("OCR status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.ocrStateLabel(String(page.value(page.sessionData, ["ocrStatus"], "component_missing")))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    Flow {
                        Layout.fillWidth: true
                        spacing: 18
                        Label { text: qsTr("Capture: %1").arg(page.formatLatency(page.value(page.sessionData, ["captureMs", "capture_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR: %1").arg(page.formatLatency(page.value(page.sessionData, ["ocrMs", "ocr_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Translation: %1").arg(page.formatLatency(page.value(page.sessionData, ["translationMs", "translation_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Speech: %1").arg(page.formatLatency(page.value(page.sessionData, ["ttsMs", "tts_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Audio start: %1").arg(page.formatLatency(page.value(page.sessionData, ["audioStartMs", "audio_start_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture size: %1x%2").arg(page.value(page.sessionData, ["captureWidth"], 0)).arg(page.value(page.sessionData, ["captureHeight"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture to text: %1").arg(page.formatLatency(page.value(page.sessionData, ["totalCaptureToTextMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Subtitle to speech: %1").arg(page.formatLatency(page.value(page.sessionData, ["totalCaptureToAudioStartMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR runs: %1").arg(page.value(page.sessionData, ["ocrExecutionCount"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Dropped work: %1").arg(page.value(page.sessionData, ["droppedFrames"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label {
                            visible: page.value(page.sessionData, ["lastDetectedAgeSeconds"], null) !== null
                            text: qsTr("Last phrase: %1 s ago").arg(Number(page.value(page.sessionData, ["lastDetectedAgeSeconds"], 0)).toFixed(1))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Item { Layout.fillWidth: true }
                        AppButton {
                            text: qsTr("Save settings")
                            enabled: page.selectedGameId.length > 0 && !page.sessionActive
                            onClicked: page.saveSettings()
                        }
                        AppButton {
                            text: page.sessionActive ? qsTr("Stop narrator") : qsTr("Start narrator")
                            kind: page.sessionActive ? "danger" : "primary"
                            enabled: page.sessionActive
                                     || (page.selectedGameId.length > 0
                                         && page.narratorEnabled
                                         && page.componentsReady)
                            toolTip: enabled ? "" : qsTr("Enable the narrator and install all required local narrator components first")
                            onClicked: page.sessionActive
                                       ? page.stopNarrator() : page.startNarrator()
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: App.Theme.contentPadding }
        }
    }
}
