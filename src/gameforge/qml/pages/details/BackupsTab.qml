import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../dialogs"
import "../.." as App

Item {
    id: tab

    property var controller
    property var gameData: ({})
    property var backupsData: []
    signal toastRequested(string message, string tone)

    readonly property string gameId: String(gameValue(["id"], ""))
    readonly property bool demoMode: Boolean(controller && controller.demoMode)
    readonly property var gameBackups: {
        var source = backupsData || []
        var result = []
        for (var i = 0; i < source.length; ++i) {
            if (String(source[i].gameId || "") === gameId)
                result.push(source[i])
        }
        return result
    }

    function gameValue(keys, fallback) {
        var source = gameData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function backupValue(backup, keys, fallback) {
        var source = backup || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function totalSize() {
        var total = 0
        for (var i = 0; i < gameBackups.length; ++i)
            total += Number(backupValue(gameBackups[i], ["sizeGb"], 0))
        return total.toFixed(1) + " GB"
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 14

            GridLayout {
                Layout.fillWidth: true
                columns: tab.width >= 760 ? 3 : 1
                rowSpacing: 10
                columnSpacing: 10

                MetricTile {
                    Layout.fillWidth: true
                    label: qsTr("Restore points")
                    value: String(tab.gameBackups.length)
                    symbol: "↺"
                    tone: App.Theme.info
                }

                MetricTile {
                    Layout.fillWidth: true
                    label: tab.demoMode ? qsTr("Demo storage") : qsTr("Backup storage")
                    value: tab.totalSize()
                    symbol: "▣"
                    tone: App.Theme.secondary
                }

                MetricTile {
                    Layout.fillWidth: true
                    label: qsTr("Backup status")
                    value: tab.gameValue(["backupStatus"], qsTr("Not checked"))
                    symbol: "✓"
                    tone: App.Theme.success
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                padding: 18
                elevated: true

                contentItem: RowLayout {
                    spacing: 12

                    Rectangle {
                        Layout.preferredWidth: 42
                        Layout.preferredHeight: 42
                        radius: 13
                        color: App.Theme.infoSoft
                        Label {
                            anchors.centerIn: parent
                            text: "↺"
                            color: App.Theme.info
                            font.pixelSize: 20
                            font.weight: Font.Bold
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Label {
                            text: tab.demoMode ? qsTr("Safe demo restore points") : qsTr("Backups")
                            color: App.Theme.text
                            font.pixelSize: 16
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: tab.demoMode
                                  ? qsTr("Restore and delete update only the in-memory demo list. No archives or game files exist at these paths.")
                                  : qsTr("Backup creation and restore are not implemented yet. No files are changed.")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }

                    StatusBadge {
                        text: tab.demoMode ? qsTr("Simulation only") : qsTr("Coming soon")
                        status: "not checked"
                        showDot: false
                    }
                }
            }

            RowLayout {
                visible: tab.gameBackups.length > 0
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Available backups")
                    color: App.Theme.text
                    font.pixelSize: 17
                    font.weight: Font.Bold
                }
                Label {
                    text: qsTr("Newest first")
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                }
            }

            Repeater {
                model: tab.gameBackups

                delegate: SurfaceCard {
                    id: backupCard
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: tab.width >= 720 ? 92 : 142
                    padding: 14

                    contentItem: GridLayout {
                        columns: tab.width >= 720 ? 6 : 2
                        rowSpacing: 8
                        columnSpacing: 16

                        Rectangle {
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            radius: 14
                            color: App.Theme.surfaceRaised
                            Label {
                                anchors.centerIn: parent
                                text: "↺"
                                color: App.Theme.secondary
                                font.pixelSize: 20
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 170
                            spacing: 3
                            Label {
                                Layout.fillWidth: true
                                text: tab.backupValue(backupCard.modelData, ["operation", "type"], qsTr("Backup"))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text: tab.backupValue(backupCard.modelData, ["date", "createdAt"], "-")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                                elide: Text.ElideRight
                            }
                        }

                        LabeledValue {
                            Layout.preferredWidth: 82
                            label: qsTr("Size")
                            value: tab.backupValue(backupCard.modelData, ["size"], "-")
                        }

                        StatusBadge {
                            text: tab.backupValue(backupCard.modelData, ["status"], qsTr("Available"))
                            status: text
                        }

                        Item { Layout.fillWidth: true }

                        RowLayout {
                            spacing: 4
                            AppButton {
                                text: qsTr("Restore")
                                iconText: "↺"
                                kind: "secondary"
                                compact: true
                                enabled: String(tab.backupValue(backupCard.modelData, ["status"], "")).toLowerCase() !== "restored"
                                onClicked: backupDialog.ask(
                                    qsTr("Restore this demo backup?"),
                                    qsTr("The backup will only be marked as restored in memory. No game files will be replaced."),
                                    qsTr("Restore"),
                                    false,
                                    { "action": "restore", "id": tab.backupValue(backupCard.modelData, ["id"], "") })
                            }

                            IconButton {
                                symbol: "⌫"
                                danger: true
                                toolTip: qsTr("Delete backup")
                                onClicked: backupDialog.ask(
                                    qsTr("Delete this demo backup?"),
                                    qsTr("This removes only the selected record from the in-memory demo list."),
                                    qsTr("Delete"),
                                    true,
                                    { "action": "delete", "id": tab.backupValue(backupCard.modelData, ["id"], "") })
                            }
                        }
                    }
                }
            }

            EmptyState {
                visible: tab.gameBackups.length === 0
                Layout.fillWidth: true
                Layout.preferredHeight: 260
                symbol: "↺"
                title: qsTr("No backups for this game")
                message: tab.demoMode
                         ? qsTr("Demo restore points created by future simulated operations will appear here.")
                         : qsTr("No backups detected. Backup creation and restore are not implemented yet.")
            }

            Item { Layout.preferredHeight: 2 }
        }
    }

    ConfirmDialog {
        id: backupDialog
        onConfirmed: function(payload) {
            if (!payload || !tab.controller)
                return
            if (payload.action === "restore" && tab.controller.restoreBackup)
                tab.controller.restoreBackup(String(payload.id))
            else if (payload.action === "delete" && tab.controller.deleteBackup)
                tab.controller.deleteBackup(String(payload.id))
        }
    }
}
