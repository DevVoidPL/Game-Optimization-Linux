pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

SurfaceCard {
    id: section

    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData && gameData.id || "")
    property var settingsData: ({})
    property var sessionData: ({ "status": "idle" })
    property string errorMessage: ""

    readonly property bool sessionActive: ["starting", "capturing", "translating",
            "speaking", "stopping"].indexOf(String(sessionData.status || "idle")) >= 0
    readonly property bool directPolish: String(settingsData.subtitleLanguageMode || "english_to_polish") === "polish"

    readonly property var voiceValues: (settingsData.voices || []).map(function(voice) { return String(voice.id || "") })
    readonly property var voiceLabels: (settingsData.voices || []).map(function(voice) {
        var installed = Boolean(voice.installed)
        var name = String(voice.name || voice.id || "")
        return installed ? name : qsTr("%1 (not installed)").arg(name)
    })
    readonly property var profileValues: (settingsData.translationProfiles || []).map(function(profile) { return String(profile.id || "") })
    readonly property var profileLabels: (settingsData.translationProfiles || []).map(function(profile) { return String(profile.name || profile.id || "") })

    padding: 18

    function sessionStateLabel(status) {
        if (status === "idle") return qsTr("Idle")
        if (status === "starting") return qsTr("Starting")
        if (status === "capturing") return qsTr("Capturing")
        if (status === "translating") return qsTr("Translating")
        if (status === "speaking") return qsTr("Speaking")
        if (status === "stopping") return qsTr("Stopping")
        if (status === "stopped") return qsTr("Stopped")
        if (status === "error") return qsTr("Error")
        return qsTr("Unknown")
    }

    function sessionStateTone(status) {
        if (["capturing", "translating", "speaking", "starting"].indexOf(status) >= 0)
            return "running"
        if (status === "error")
            return "error"
        if (status === "idle" || status === "stopped")
            return "paused"
        return "checking"
    }

    function componentStateLabel(state) {
        if (state === "ready") return qsTr("Ready")
        if (state === "bypassed") return qsTr("Not needed")
        if (state === "component_missing") return qsTr("Component missing")
        if (state === "unavailable") return qsTr("Unavailable")
        if (state === "stopped") return qsTr("Ready")
        if (state === "running" || state === "active") return qsTr("Active")
        if (state === "error") return qsTr("Error")
        return qsTr("Unknown")
    }

    function componentStateTone(state) {
        if (["ready", "stopped", "active", "running"].indexOf(state) >= 0)
            return "available"
        if (state === "bypassed")
            return "paused"
        if (["component_missing", "unavailable", "error"].indexOf(state) >= 0)
            return "missing"
        return "neutral"
    }

    function loadSettings() {
        if (!controller || !gameId || !controller.getNarratorGameSettings)
            return
        settingsData = controller.getNarratorGameSettings(gameId) || ({})
    }

    function loadSession() {
        if (!controller || !gameId || !controller.getNarratorSessionState)
            return
        sessionData = controller.getNarratorSessionState(gameId) || ({ "status": "idle" })
    }

    function reload() {
        loadSettings()
        loadSession()
    }

    function saveValue(key, value) {
        if (!controller || !controller.saveNarratorGameSettings || !gameId)
            return
        var payload = {}
        payload[key] = value
        var saved = Boolean(controller.saveNarratorGameSettings(gameId, payload))
        if (saved) {
            errorMessage = ""
            reload()
        } else {
            errorMessage = qsTr("Narrator settings could not be saved")
        }
    }

    function startMessage() {
        if (sessionActive)
            return ""
        var reason = String(sessionData.reasonCode || "")
        if (reason === "game_not_running") return qsTr("Launch the game before starting Narrator")
        if (reason === "another_session_active") return qsTr("Narrator is already active for another game")
        if (settingsData.enabled !== true) return qsTr("Enable Narrator for this game first")
        return ""
    }

    function toggleStart() {
        if (!controller)
            return
        if (sessionActive) {
            if (controller.stopNarrator)
                controller.stopNarrator()
        } else if (controller.startNarrator && gameId) {
            controller.startNarrator(gameId)
        }
        reload()
    }

    function selectRegion() {
        if (!controller || !controller.selectNarratorSubtitleRegion || !gameId)
            return
        controller.selectNarratorSubtitleRegion(gameId, settingsData.subtitleRegion || ({}))
    }

    Component.onCompleted: reload()
    onGameIdChanged: reload()

    Connections {
        target: section.controller || null
        ignoreUnknownSignals: true
        function onNarratorChanged(changedGameId) {
            if (!changedGameId || changedGameId === section.gameId)
                section.reload()
        }
    }

    contentItem: ColumnLayout {
        spacing: 11

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: qsTr("Narrator status")
                color: App.Theme.text
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            StatusBadge {
                text: section.sessionStateLabel(String(section.sessionData.status || "idle"))
                status: section.sessionStateTone(String(section.sessionData.status || "idle"))
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Narrator reads on-screen subtitles for this game, translates them and speaks the result. These settings are saved only for this game.")
            color: App.Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Narrator")
            description: qsTr("Enable subtitle capture and speech for this game")
            AppSwitch {
                checked: Boolean(section.settingsData.enabled)
                onToggled: section.saveValue("enabled", checked)
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Subtitle language")
            description: qsTr("Direct Polish reads native Polish subtitles without translation. Otherwise, detected text is translated into Polish.")
            AppComboBox {
                Layout.preferredWidth: 260
                model: [qsTr("English → Polish (translated)"), qsTr("Polish (Direct Polish, no translation)")]
                currentIndex: section.directPolish ? 1 : 0
                onActivated: function(index) {
                    section.saveValue("subtitleLanguageMode", index === 1 ? "polish" : "english_to_polish")
                }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            visible: section.profileValues.length > 0
            enabled: !section.directPolish
            title: qsTr("Translation profile")
            description: section.directPolish
                         ? qsTr("Not used while Direct Polish is active")
                         : qsTr("Choose the translation style used for detected subtitles")
            AppComboBox {
                Layout.preferredWidth: 260
                model: section.profileLabels
                currentIndex: Math.max(0, section.profileValues.indexOf(String(section.settingsData.translationProfileId || "")))
                onActivated: function(index) { section.saveValue("translationProfileId", section.profileValues[index]) }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            visible: section.voiceValues.length > 0
            title: qsTr("Polish voice")
            description: qsTr("Speech voice used to read translated or direct Polish subtitles")
            AppComboBox {
                Layout.preferredWidth: 260
                model: section.voiceLabels
                currentIndex: Math.max(0, section.voiceValues.indexOf(String(section.settingsData.voiceId || "")))
                onActivated: function(index) { section.saveValue("voiceId", section.voiceValues[index]) }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Volume")
            description: qsTr("%1%").arg(Math.round(Number(section.settingsData.volume || 0) * 100))
            AppSlider {
                from: 0
                to: 1
                stepSize: 0.05
                value: Number(section.settingsData.volume || 0)
                onMoved: section.saveValue("volume", value)
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Speech rate")
            description: qsTr("%1×").arg(Number(section.settingsData.speechRate || 1).toFixed(1))
            AppSlider {
                from: 0.5
                to: 2.0
                stepSize: 0.1
                value: Number(section.settingsData.speechRate || 1)
                onMoved: section.saveValue("speechRate", value)
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Subtitle region")
            description: qsTr("Open the native selector to pick the screen area Narrator reads subtitles from")
            AppButton {
                text: qsTr("Select subtitle region")
                iconSource: App.UiIcons.actionScan
                kind: "secondary"
                enabled: Boolean(section.controller && section.controller.selectNarratorSubtitleRegion)
                onClicked: section.selectRegion()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusColumn.implicitHeight + 24
            radius: App.Theme.radiusMedium
            color: App.Theme.surfaceHover
            border.width: 1
            border.color: App.Theme.border

            ColumnLayout {
                id: statusColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                Label {
                    text: qsTr("Component status")
                    color: App.Theme.text
                    font.pixelSize: 16
                    font.weight: Font.Bold
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    StatusBadge {
                        text: qsTr("Capture: %1").arg(section.componentStateLabel(String(section.sessionData.captureState || "unavailable")))
                        status: section.componentStateTone(String(section.sessionData.captureState || "unavailable"))
                    }
                    StatusBadge {
                        text: qsTr("OCR: %1").arg(section.componentStateLabel(String(section.sessionData.ocrStatus || "component_missing")))
                        status: section.componentStateTone(String(section.sessionData.ocrStatus || "component_missing"))
                    }
                    StatusBadge {
                        text: qsTr("Translation: %1").arg(section.componentStateLabel(String(section.sessionData.translationStatus || "component_missing")))
                        status: section.componentStateTone(String(section.sessionData.translationStatus || "component_missing"))
                    }
                    StatusBadge {
                        text: qsTr("Speech: %1").arg(section.componentStateLabel(String(section.sessionData.ttsStatus || "component_missing")))
                        status: section.componentStateTone(String(section.sessionData.ttsStatus || "component_missing"))
                    }
                    Item { Layout.fillWidth: true }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            AppButton {
                objectName: "narratorStartStopButton"
                text: section.sessionActive ? qsTr("Stop Narrator") : qsTr("Start Narrator")
                iconSource: section.sessionActive ? App.UiIcons.actionCancel : App.UiIcons.actionLaunch
                kind: section.sessionActive ? "danger" : "primary"
                enabled: section.sessionActive
                         || (Boolean(section.settingsData.enabled) && Boolean(section.sessionData.canStart))
                onClicked: section.toggleStart()
            }
            Label {
                Layout.fillWidth: true
                visible: section.startMessage().length > 0
                text: section.startMessage()
                color: App.Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }

        Label {
            Layout.fillWidth: true
            visible: section.errorMessage.length > 0
            text: section.errorMessage
            color: App.Theme.danger
            wrapMode: Text.WordWrap
        }
    }
}
