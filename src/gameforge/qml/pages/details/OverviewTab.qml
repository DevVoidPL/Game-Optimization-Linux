import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab

    property var controller
    property var gameData: ({})
    property var tasksData: []
    readonly property string gameId: String(value(["id"], ""))
    readonly property bool demoMode: Boolean(controller && controller.demoMode)
    signal toastRequested(string message, string tone)

    function value(keys, fallback) {
        var source = gameData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function reportValue(keys, fallback) {
        var source = value(["analysisReport"], ({})) || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function findAnalysisTask() {
        var source = tasksData || []
        for (var i = source.length - 1; i >= 0; --i) {
            var task = source[i]
            if (String(task.gameId || "") === gameId
                    && String(task.operation || task.type || "").toLowerCase().indexOf("analysis") >= 0)
                return task
        }
        return ({})
    }

    readonly property var analysisTask: findAnalysisTask()
    readonly property string analysisStatus: String(analysisTask.status || "")
    readonly property bool analysisActive: ["queued", "analyzing", "running", "paused"].indexOf(analysisStatus.toLowerCase()) >= 0
    readonly property bool reportAvailable: String(reportValue(["summary"], "")).length > 0

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
                columns: tab.width >= 950 ? 4 : 2
                rowSpacing: 10
                columnSpacing: 10

                MetricTile {
                    Layout.fillWidth: true
                    label: qsTr("Logical size")
                    value: tab.value(["logicalSize", "size"], "-")
                    symbol: "□"
                    tone: App.Theme.info
                }

                MetricTile {
                    Layout.fillWidth: true
                    label: qsTr("Physical size")
                    value: tab.value(["physicalSize"], "-")
                    symbol: "▣"
                    tone: App.Theme.secondary
                }

                MetricTile {
                    Layout.fillWidth: true
                    label: qsTr("Space saved")
                    value: tab.value(["savedSpace"], "0 GB")
                    symbol: "↓"
                    tone: App.Theme.success
                }

                MetricTile {
                    Layout.fillWidth: true
                    label: qsTr("Optimization profile")
                    value: App.I18n.profile(
                               tab.value(["optimizationStatus"], qsTr("Not configured")))
                    symbol: "⚡"
                    tone: App.Theme.warning
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: tab.width >= 920 ? 2 : 1
                rowSpacing: 12
                columnSpacing: 12

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 222
                    padding: 18

                    contentItem: ColumnLayout {
                        spacing: 12

                        Label {
                            text: qsTr("Game information")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Launcher")
                            value: tab.value(["launcher"], qsTr("Manual"))
                        }

                        Divider { Layout.fillWidth: true }

                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Installation path")
                            value: tab.value(["path", "installPath"], "-")
                            mono: true
                        }

                        Divider { Layout.fillWidth: true }

                        RowLayout {
                            Layout.fillWidth: true
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Filesystem")
                                value: tab.value(["filesystem"], qsTr("Unknown"))
                                mono: true
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Game status")
                                value: tab.value(["status"], qsTr("Ready"))
                            }
                        }
                    }
                }

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 222
                    padding: 18

                    contentItem: ColumnLayout {
                        spacing: 12

                        Label {
                            text: qsTr("Recent state")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Last operation")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontBody
                            }
                            StatusBadge {
                                text: tab.value(["lastOperation", "lastTaskStatus"], qsTr("Not run"))
                                status: text
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Backup status")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontBody
                            }
                            StatusBadge {
                                text: tab.value(["backupStatus"], qsTr("Not checked"))
                                status: text
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Last compression")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: tab.value(["lastCompression"], qsTr("Never"))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontCaption
                                font.weight: Font.DemiBold
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                padding: 18

                contentItem: ColumnLayout {
                    spacing: 13

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Label {
                                text: qsTr("Feature compatibility")
                                color: App.Theme.text
                                font.pixelSize: 17
                                font.weight: Font.Bold
                            }
                            Label {
                                text: qsTr("Capabilities are reported independently for this game")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: tab.width >= 870 ? 4 : 2
                        rowSpacing: 8
                        columnSpacing: 8

                        Repeater {
                            model: [
                                { "name": qsTr("Btrfs compression"), "status": tab.value(["compressionAvailable"], false) ? qsTr("Available") : qsTr("Unsupported") },
                                { "name": qsTr("Graphics remaster"), "status": tab.value(["engineCompatibility"], qsTr("Not checked")) },
                                { "name": qsTr("Optimization"), "status": qsTr("Optional") }
                            ]

                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 62
                                radius: App.Theme.radiusSmall
                                color: App.Theme.surfaceRaised

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 4
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.name
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.status
                                        color: App.Theme.statusColor(modelData.status)
                                        font.pixelSize: App.Theme.fontCaption
                                        font.weight: Font.Bold
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                padding: 18

                contentItem: ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 14

                        Rectangle {
                            Layout.preferredWidth: 45
                            Layout.preferredHeight: 45
                            radius: 14
                            color: App.Theme.accentSoft
                            Label {
                                anchors.centerIn: parent
                                text: "⌕"
                                color: App.Theme.accent
                                font.pixelSize: 21
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Analyze Game")
                                color: App.Theme.text
                                font.pixelSize: 17
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: tab.demoMode
                                      ? qsTr("Run a safe simulated scan and generate recommendations.")
                                      : qsTr("Per-game analysis is planned for a later stage.")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        AppButton {
                            text: !tab.demoMode ? qsTr("Coming soon")
                                  : tab.analysisActive ? qsTr("In progress") : qsTr("Analyze Game")
                            iconText: "⌕"
                            kind: "primary"
                            busy: tab.analysisActive && tab.analysisStatus.toLowerCase() !== "paused"
                            enabled: tab.demoMode && !tab.analysisActive
                            onClicked: {
                                if (tab.controller && tab.controller.analyzeGame)
                                    tab.controller.analyzeGame(tab.gameId)
                            }
                        }
                    }

                    ColumnLayout {
                        visible: tab.analysisActive
                        Layout.fillWidth: true
                        spacing: 6

                        AppProgressBar {
                            Layout.fillWidth: true
                            value: {
                                var raw = Number(tab.analysisTask.progress || tab.analysisTask.progressPercent || 0)
                                return raw > 1 ? raw / 100 : raw
                            }
                            indeterminateMode: tab.analysisStatus.toLowerCase() === "queued"
                        }

                        Label {
                            text: qsTr("Simulation status: %1").arg(tab.analysisStatus)
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }
                    }
                }
            }

            SurfaceCard {
                visible: tab.reportAvailable
                Layout.fillWidth: true
                padding: 18
                selected: true

                contentItem: ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Latest analysis report")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }
                        StatusBadge { text: qsTr("Completed"); status: "completed" }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: tab.reportValue(["summary"], "")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 22
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Scanned")
                            value: qsTr("%1 GB").arg(tab.reportValue(["scanned_size_gb", "scannedSizeGb"], "-"))
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Estimated saving")
                            value: qsTr("%1 GB").arg(tab.reportValue(["estimated_savings_gb", "estimatedSavingsGb"], "-"))
                        }
                    }

                    Repeater {
                        model: tab.reportValue(["recommendations"], []) || []
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 9
                            Label { text: "✓"; color: App.Theme.success; font.weight: Font.Bold }
                            Label {
                                Layout.fillWidth: true
                                text: String(modelData)
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: 2 }
        }
    }
}
