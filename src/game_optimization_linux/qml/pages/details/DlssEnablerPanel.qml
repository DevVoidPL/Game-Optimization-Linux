pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

// Per-game DLSS Enabler switch. Exclusive with OptiScaler: enabling parks the
// managed OptiScaler installation (FSR 4 Lite included) and restores it
// exactly when switching back.
ScrollView {
    id: panel
    property var controller
    property var gameData: ({})
    readonly property var scrollFlickable: contentItem
    readonly property string gameId: String(gameData && gameData.id || "")
    property var statusData: ({})
    readonly property var enabler: statusData.dlssEnabler || ({})
    readonly property bool active: Boolean(enabler.active)
    readonly property string phase: String(enabler.phase || "inactive")
    readonly property bool interrupted: phase !== "inactive" && phase !== "active"
    property string errorMessage: ""
    property bool confirmOpen: false

    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    readonly property var antiCheat: enabler.antiCheat || ({})
    readonly property bool antiCheatDetected: Boolean(antiCheat.detected)

    function refresh(force) {
        // Never show the previous game's state (or its anti-cheat result).
        statusData = ({})
        confirmOpen = false
        errorMessage = ""
        if (!controller || !gameId || !controller.requestOptiScalerStatus)
            return
        var result = controller.requestOptiScalerStatus(gameId, Boolean(force)) || ({})
        if (result.success)
            statusData = result
    }
    function start(enabled) {
        confirmOpen = false
        if (!controller || !controller.setDlssEnablerActive
                || !controller.setDlssEnablerActive(gameId, enabled))
            errorMessage = qsTr("The DLSS Enabler task could not be started")
        else
            errorMessage = ""
    }
    function configure(changes) {
        if (!controller || !controller.configureDlssEnabler)
            return
        var current = enabler.configuration || ({})
        var values = {
            "dx12Upscaler": String(current.dx12Upscaler || "auto"),
            "frameGenerator": String(current.frameGenerator || "auto"),
            "reflex": String(current.reflex || "on"),
            "reflexEmulation": String(current.reflexEmulation || "auto"),
            "framerateLimit": String(current.framerateLimit || "off"),
            "frameGenerationMode": String(current.frameGenerationMode || "auto"),
            "logging": current.logging !== false,
            "logLevel": String(current.logLevel || "2")
        }
        for (var key in changes)
            values[key] = changes[key]
        var result = controller.configureDlssEnabler(gameId, values) || ({})
        if (result.success) {
            errorMessage = ""
            refresh(false)
        } else {
            errorMessage = App.I18n.message(String(result.error || qsTr("The DLSS Enabler configuration could not be saved")))
        }
    }

    Component.onCompleted: refresh(false)
    onGameIdChanged: refresh(false)
    Connections {
        target: panel.controller || null
        ignoreUnknownSignals: true
        function onOptiScalerStatusChanged(changedGameId, result) {
            if (String(changedGameId) === panel.gameId && result && result.success)
                panel.statusData = result
        }
    }

    ColumnLayout {
        width: panel.availableWidth
        spacing: 14
        // Plain card (not a Pane contentItem): the wrapped labels get their
        // width before the height is computed, so long PL/ES text is not clipped.
        Rectangle {
            objectName: "dlssEnablerCard"
            Layout.fillWidth: true
            implicitHeight: cardContent.implicitHeight + 36
            radius: App.Theme.radiusLarge
            color: App.Theme.surface
            border.width: 1
            border.color: App.Theme.border
            DlssEnablerPanelContent {
                id: cardContent
                panel: panel
                x: 18
                y: 18
                width: parent.width - 36
            }
        }
    }
}
