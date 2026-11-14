pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

ColumnLayout {
    id: panel
    property var controller: null
    property string gameId: ""
    property bool couchMode: false
    property var statusData: ({})
    property bool confirmationPending: false
    property string pendingAction: "apply"
    property int selectedRow: 0
    readonly property bool busy: statusData.busy === true
    readonly property string statusText: statusData.error ? String(statusData.error)
        : busy ? qsTr("Checking or updating Steam…")
        : statusData.success === true ? (statusData.lastAction === "restore"
            ? qsTr("Previous Steam Launch Options restored") : qsTr("Steam Launch Options applied and verified"))
        : statusData.kind === "lxb" ? qsTr("Steam is configured to use the GameOpti runner")
        : qsTr("No recognized GameOpti runner in Steam Launch Options")

    function refresh() {
        if (controller && controller.steamIntegrationStatus && gameId)
            statusData = controller.steamIntegrationStatus(gameId) || ({})
    }
    function requestApply() { pendingAction = "apply"; confirmationPending = true; selectedRow = 1 }
    function requestRestore() { pendingAction = "restore"; confirmationPending = true; selectedRow = 1 }
    function confirmChange() {
        if (!confirmationPending || busy || !controller || !controller.changeSteamIntegration) return
        if (controller.changeSteamIntegration(gameId, pendingAction, true)) {
            confirmationPending = false
            statusData = ({busy: true})
            refresh()
        }
    }
    function handlePadAction(action) {
        if (action === "Back") {
            if (confirmationPending) { confirmationPending = false; return true }
            return false
        }
        if (action === "NavigateUp" || action === "NavigateLeft") selectedRow = Math.max(0, selectedRow - 1)
        else if (action === "NavigateDown" || action === "NavigateRight") selectedRow = Math.min(1, selectedRow + 1)
        else if (action === "Confirm") {
            if (confirmationPending) {
                if (selectedRow === 0) confirmChange()
                else confirmationPending = false
            } else if (!busy) {
                if (selectedRow === 0) requestApply()
                else if (statusData.canRestore) requestRestore()
            }
        }
        return true
    }
    onGameIdChanged: { confirmationPending = false; statusData = ({}); refresh() }
    Component.onCompleted: refresh()
    Timer { interval: 600; running: panel.visible && panel.gameId.length > 0; repeat: true; onTriggered: panel.refresh() }

    Label { Layout.fillWidth: true; text: qsTr("Steam connection"); color: App.Theme.text; font.pixelSize: panel.couchMode ? 26 : 18; font.bold: true }
    Label { Layout.fillWidth: true; text: panel.statusText; color: panel.statusData.error ? App.Theme.warning : App.Theme.textSecondary; wrapMode: Text.WordWrap }
    Label { Layout.fillWidth: true; text: String(panel.statusData.raw || panel.statusData.command || ""); color: App.Theme.textMuted; font.family: "monospace"; wrapMode: Text.WrapAnywhere }
    Label {
        Layout.fillWidth: true
        visible: panel.confirmationPending
        text: qsTr("GameOpti will ask Steam to close, then change only this game's Launch Options with a backup. Your arguments and wrappers stay. Running games or downloads block the change. Steam stays closed afterwards; start it again when ready. Continue?")
        color: App.Theme.warning
        wrapMode: Text.WordWrap
    }
    RowLayout {
        Layout.fillWidth: true
        AppButton {
            objectName: "steamConnectionApply"
            Layout.fillWidth: true
            text: panel.confirmationPending ? qsTr("Confirm") : qsTr("Apply to Steam")
            enabled: !panel.busy && (panel.confirmationPending || panel.statusData.runnerInstalled === true)
            kind: panel.couchMode && panel.selectedRow === 0 ? "primary" : "secondary"
            onClicked: panel.confirmationPending ? panel.confirmChange() : panel.requestApply()
        }
        AppButton {
            objectName: "steamConnectionRestore"
            Layout.fillWidth: true
            text: panel.confirmationPending ? qsTr("Cancel") : qsTr("Restore")
            enabled: panel.confirmationPending || (!panel.busy && panel.statusData.canRestore === true)
            kind: panel.couchMode && panel.selectedRow === 1 ? "primary" : "secondary"
            onClicked: panel.confirmationPending ? panel.confirmationPending = false : panel.requestRestore()
        }
    }
}