pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

SurfaceCard {
    id: card

    signal openRequested()
    property var controller
    property var gameData: ({})
    property var statusData: ({ "loading": true })
    property bool forceRefresh: false
    readonly property string gameId: String(gameData && gameData.id || "")
    readonly property string steamAppId: String(gameData && gameData.steamAppId || "")

    padding: 18

    function configuredLabel() {
        var mode = String(statusData.effectiveFsr4Mode || "")
        if (mode === "force_int8") return qsTr("FSR4 INT8 Experimental")
        if (mode === "normal") return qsTr("FSR 4.1.1")
        if (mode === "disabled") return qsTr("Disabled")
        return qsTr("Automatic")
    }

    function stateLabel() {
        var state = String(statusData.snapshotState || "unknown")
        if (state === "installed") return qsTr("Installed")
        if (state === "corrupt") return qsTr("Damaged")
        if (state === "partial") return qsTr("Partial")
        if (state === "not_installed") return qsTr("Not installed")
        return qsTr("Unknown")
    }

    function scheduleRefresh(force) {
        forceRefresh = forceRefresh || Boolean(force)
        refreshTimer.restart()
    }

    function loadStatus() {
        if (!controller || !gameId)
            return
        var result = ({})
        if (controller.requestOptiScalerStatus)
            result = controller.requestOptiScalerStatus(gameId, forceRefresh) || ({})
        else if (controller.getOptiScalerStatus)
            result = controller.getOptiScalerStatus(gameId) || ({})
        forceRefresh = false
        if (result.success) {
            statusData = result
            if (result.loading || result.refreshing)
                refreshTimer.restart()
        }
    }

    contentItem: ColumnLayout {
        spacing: 9

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: qsTr("OptiScaler")
                color: App.Theme.text
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            StatusBadge {
                text: card.statusData.loading ? qsTr("Detecting…") : card.stateLabel()
                status: String(card.statusData.snapshotState || "") === "installed" ? "available"
                      : ["corrupt", "partial"].indexOf(String(card.statusData.snapshotState || "")) >= 0 ? "warning"
                      : "neutral"
            }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 18
            rowSpacing: 4
            Label { text: qsTr("Installed"); color: App.Theme.textMuted }
            Label {
                Layout.fillWidth: true
                text: String(card.statusData.installedVersion
                             || (String(card.statusData.snapshotState || "unknown") === "not_installed"
                                 ? qsTr("No") : qsTr("Unknown")))
                color: App.Theme.text
            }
            Label { text: qsTr("FSR"); color: App.Theme.textMuted }
            Label { Layout.fillWidth: true; text: String(card.statusData.fidelityFxUpscalerVersion || card.statusData.availableFidelityFxUpscalerVersion || qsTr("Unknown")); color: App.Theme.text }
            Label { text: qsTr("Configured"); color: App.Theme.textMuted }
            Label { Layout.fillWidth: true; text: card.configuredLabel(); color: App.Theme.text }
        }

        Label {
            Layout.fillWidth: true
            visible: String(card.statusData.refreshError || card.statusData.operationError || "").length > 0
            text: String(card.statusData.refreshError || card.statusData.operationError || "")
            color: App.Theme.warning
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton {
                objectName: "openOptiScalerButton"
                text: qsTr("Open OptiScaler")
                kind: "primary"
                onClicked: card.openRequested()
            }
        }
    }

    Timer {
        id: refreshTimer
        interval: 35
        repeat: false
        onTriggered: card.loadStatus()
    }

    Connections {
        target: card.controller || null
        ignoreUnknownSignals: true
        function onOptiScalerStatusChanged(changedGameId, result) {
            if (String(changedGameId) === card.gameId && result && result.success)
                card.statusData = result
        }
        function onOptiScalerChanged(appId) {
            if (String(appId) === card.steamAppId)
                card.scheduleRefresh(false)
        }
    }

    onGameIdChanged: scheduleRefresh(false)
    Component.onCompleted: scheduleRefresh(false)
}
