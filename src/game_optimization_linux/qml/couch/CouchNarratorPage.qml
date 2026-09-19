pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchNarratorPage"
    property var controller
    property var navigation
    property real couchScale: 1.0
    property string selectedGameId: ""
    property var settingsData: ({})
    property var voices: []
    property var translationProfiles: []
    property int focusIndex: 0
    readonly property var controls: [gameButton, enabledButton, subtitleButton,
        translationButton, profileButton, voiceButton, volumeSlider, speedSlider,
        regionButton, startButton]

    function value(source, keys, fallback) {
        var data = source || {}
        for (var i = 0; i < keys.length; ++i) {
            if (data[keys[i]] !== undefined && data[keys[i]] !== null
                    && data[keys[i]] !== "")
                return data[keys[i]]
        }
        return fallback
    }

    function loadSettings() {
        if (!controller || !selectedGameId
                || typeof controller.getNarratorGameSettings !== "function")
            return
        settingsData = controller.getNarratorGameSettings(selectedGameId) || ({})
        voices = value(settingsData, ["voices"], []) || []
        translationProfiles = value(settingsData, ["translationProfiles"], []) || []
    }

    function selectInitialGame() {
        var games = controller && controller.games ? controller.games : []
        if (!selectedGameId && games.length)
            selectedGameId = String(games[0].id || "")
        loadSettings()
    }

    function save() {
        if (!controller || !selectedGameId
                || typeof controller.saveNarratorGameSettings !== "function")
            return false
        return Boolean(controller.saveNarratorGameSettings(selectedGameId, {
            "enabled": Boolean(value(settingsData, ["enabled"], false)),
            "sourceMode": String(value(settingsData, ["sourceMode", "source_mode"], "auto")),
            "captureSource": String(value(settingsData, ["captureSource", "capture_source"], "window")),
            "subtitleLanguageMode": String(value(settingsData, ["subtitleLanguageMode", "subtitle_language_mode"], "english_to_polish")),
            "translationProfileId": String(value(settingsData, ["translationProfileId", "translation_profile_id"], "")),
            "ttsProviderId": String(value(settingsData, ["ttsProviderId", "tts_provider_id"], "")),
            "voiceId": String(value(settingsData, ["voiceId", "voice_id"], "")),
            "volume": Number(value(settingsData, ["volume"], 0.85)),
            "speechRate": Number(value(settingsData, ["speechRate", "speech_rate"], 1.0))
        }))
    }

    function cycle(key, values) {
        var current = String(value(settingsData, [key], values[0]))
        var index = values.indexOf(current)
        settingsData[key] = values[(index + 1) % values.length]
        settingsData = settingsData
        save()
    }

    function adjustSlider(slider, delta) {
        slider.value = Math.max(slider.from, Math.min(slider.to, slider.value + delta))
        settingsData = settingsData
        save()
    }

    function restoreActiveFocus() {
        var control = controls[Math.max(0, Math.min(focusIndex, controls.length - 1))]
        if (control) control.forceActiveFocus()
    }

    function handleAction(action) {
        if (action === "Back") {
            if (navigation) navigation.previousScreen()
            backRequested()
            return
        }
        if (action === "NavigateUp")
            focusIndex = Math.max(0, focusIndex - 1)
        else if (action === "NavigateDown")
            focusIndex = Math.min(controls.length - 1, focusIndex + 1)
        else if (action === "NavigateLeft") {
            if (focusIndex === 6) adjustSlider(volumeSlider, -0.05)
            else if (focusIndex === 7) adjustSlider(speedSlider, -0.05)
        } else if (action === "NavigateRight") {
            if (focusIndex === 6) adjustSlider(volumeSlider, 0.05)
            else if (focusIndex === 7) adjustSlider(speedSlider, 0.05)
        } else if (action === "Confirm") {
            var control = controls[focusIndex]
            if (control && control.clicked)
                control.clicked()
        }
        restoreActiveFocus()
    }

    signal backRequested()

    Component.onCompleted: {
        selectInitialGame()
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) {
        selectInitialGame()
        restoreActiveFocus()
    }

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 52 * page.couchScale
        spacing: 18 * page.couchScale
        Label {
            text: qsTr("Narrator")
            color: App.Theme.text
            font.pixelSize: 38 * page.couchScale
            font.weight: Font.Bold
        }
        Label {
            Layout.fillWidth: true
            text: qsTr("Configure game narration with the controller.")
            color: App.Theme.textSecondary
            font.pixelSize: 18 * page.couchScale
            wrapMode: Text.WordWrap
        }
        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: width
            contentHeight: form.implicitHeight
            clip: true
            ColumnLayout {
                id: form
                width: parent.width
                spacing: 12 * page.couchScale
                CouchButton {
                    id: gameButton
                    text: qsTr("Game: %1").arg(selectedGameId || qsTr("No game selected"))
                    couchScale: page.couchScale
                    onClicked: {
                        var games = controller && controller.games ? controller.games : []
                        if (games.length) {
                            var index = 0
                            for (var i = 0; i < games.length; ++i)
                                if (String(games[i].id || "") === selectedGameId) index = i
                            selectedGameId = String(games[(index + 1) % games.length].id || "")
                            loadSettings()
                        }
                    }
                }
                CouchButton {
                    id: enabledButton
                    text: qsTr("Narrator: %1").arg(value(settingsData, ["enabled"], false) ? qsTr("Enabled") : qsTr("Disabled"))
                    iconSource: App.UiIcons.sidebarNarrator
                    couchScale: page.couchScale
                    onClicked: {
                        settingsData.enabled = !Boolean(value(settingsData, ["enabled"], false))
                        settingsData = settingsData; save()
                    }
                }
                CouchButton {
                    id: subtitleButton
                    text: qsTr("Subtitle language: %1").arg(String(value(settingsData, ["subtitleLanguageMode", "subtitle_language_mode"], "english_to_polish")) === "polish"
                              ? qsTr("Polish - read without translation")
                              : qsTr("English - translate to Polish"))
                    couchScale: page.couchScale
                    onClicked: cycle("subtitleLanguageMode", ["english_to_polish", "polish"])
                }
                CouchButton {
                    id: translationButton
                    text: qsTr("Translation mode: %1").arg(String(value(settingsData, ["sourceMode", "source_mode"], "auto")))
                    couchScale: page.couchScale
                    onClicked: cycle("sourceMode", ["auto", "window", "monitor"])
                }
                CouchButton {
                    id: profileButton
                    text: qsTr("Translation profile: %1").arg(String(value(settingsData, ["translationProfileId", "translation_profile_id"], qsTr("Default"))))
                    couchScale: page.couchScale
                    enabled: translationProfiles.length > 0
                    onClicked: if (translationProfiles.length) {
                        var ids = []
                        for (var i = 0; i < translationProfiles.length; ++i)
                            ids.push(String(translationProfiles[i].id || translationProfiles[i].value || ""))
                        cycle("translationProfileId", ids)
                    }
                }
                CouchButton {
                    id: voiceButton
                    text: qsTr("Polish voice: %1").arg(String(value(settingsData, ["voiceId", "voice_id"], qsTr("Not selected"))))
                    couchScale: page.couchScale
                    enabled: voices.length > 0
                    onClicked: if (voices.length) {
                        var ids = []
                        for (var i = 0; i < voices.length; ++i)
                            ids.push(String(voices[i].id || ""))
                        cycle("voiceId", ids)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: qsTr("Volume"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale }
                    Slider {
                        id: volumeSlider
                        Layout.preferredWidth: 430 * page.couchScale
                        from: 0; to: 1; value: Number(page.value(settingsData, ["volume"], 0.85))
                        onMoved: { settingsData.volume = value; settingsData = settingsData; save() }
                    }
                    Label { text: qsTr("%1%").arg(Math.round(volumeSlider.value * 100)); color: App.Theme.textSecondary }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: qsTr("Speech rate"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale }
                    Slider {
                        id: speedSlider
                        Layout.preferredWidth: 430 * page.couchScale
                        from: 0.5; to: 2; value: Number(page.value(settingsData, ["speechRate", "speech_rate"], 1))
                        onMoved: { settingsData.speechRate = value; settingsData = settingsData; save() }
                    }
                    Label { text: speedSlider.value.toFixed(2) + "x"; color: App.Theme.textSecondary }
                }
                CouchButton {
                    id: regionButton
                    text: qsTr("Subtitle region: %1").arg(qsTr("Choose the area used for subtitle capture"))
                    couchScale: page.couchScale
                    enabled: Boolean(selectedGameId) && controller
                    onClicked: if (controller && controller.requestNarratorRegionPreview)
                        controller.requestNarratorRegionPreview(selectedGameId)
                }
                CouchButton {
                    id: startButton
                    text: value(settingsData, ["enabled"], false) ? qsTr("Stop Narrator") : qsTr("Start Narrator")
                    iconSource: value(settingsData, ["enabled"], false) ? App.UiIcons.actionCancel : App.UiIcons.actionLaunch
                    couchScale: page.couchScale
                    onClicked: {
                        save()
                        if (value(settingsData, ["enabled"], false)) {
                            if (controller && controller.stopNarrator) controller.stopNarrator()
                        } else if (controller && controller.startNarrator) {
                            controller.startNarrator(selectedGameId)
                        }
                        loadSettings()
                    }
                }
            }
        }
    }
}
