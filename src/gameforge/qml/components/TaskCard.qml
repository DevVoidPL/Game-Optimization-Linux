import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

SurfaceCard {
    id: card
    objectName: "taskCard"

    property var taskData: ({})
    signal pauseRequested(string taskId)
    signal resumeRequested(string taskId)
    signal cancelRequested(string taskId, string taskName, bool readOnly)
    signal removeRequested(string taskId)

    readonly property string operationType: String(
                                                 value(["operation", "type"], ""))
                                             .toLowerCase()
    readonly property bool readOnlyAnalysis: operationType === "analysis"
    readonly property bool readOnlyOperation: Boolean(value(["readOnly"], false))
    readonly property bool realCompression: !readOnlyAnalysis
                                            && String(value(["operation", "type"], "")).toLowerCase() === "compression"

    implicitHeight: readOnlyAnalysis || realCompression ? 184 : 154
    padding: 16

    function value(keys, fallback) {
        var source = taskData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function progressValue() {
        var raw = Number(value(["progress", "progressPercent"], 0))
        return raw > 1 ? raw / 100 : raw
    }

    function prettyStatus() {
        return App.I18n.status(value(["status"], "queued"))
    }

    function translatedStage() {
        var raw = String(value(["stage"], ""))
        return App.I18n.status(raw.length > 0 ? raw : "Checking")
    }

    function formatBytes(raw) {
        var bytes = Number(raw || 0)
        if (!isFinite(bytes) || bytes <= 0)
            return "0 " + qsTr("MiB")
        if (bytes >= 1024 * 1024 * 1024)
            return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " " + qsTr("GiB")
        return (bytes / (1024 * 1024)).toFixed(bytes >= 100 * 1024 * 1024 ? 0 : 1) + " " + qsTr("MiB")
    }

    function displayTitle() {
        if (readOnlyAnalysis)
            return qsTr("Analyze %1").arg(value(["gameName"], qsTr("game")))
        if (operationType === "verification")
            return qsTr("Verify compression for %1")
                    .arg(value(["gameName"], qsTr("game")))
        return App.I18n.taskLabel(
                    value(["name", "title", "operation", "type"], qsTr("Task")))
    }

    function isTerminal() {
        var status = String(value(["status"], "")).toLowerCase()
        return status === "completed" || status === "failed"
                || status === "cancelled" || status === "interrupted"
    }

    contentItem: RowLayout {
        spacing: 15

        GameArtwork {
            id: taskCover
            objectName: "taskCardCover"
            Layout.preferredWidth: card.width < 620 ? 70 : 86
            Layout.preferredHeight: card.width < 620 ? 100 : 122
            Layout.alignment: Qt.AlignTop
            compact: false
            gameId: String(card.value(["gameId", "game_id"], ""))
            title: card.value(["gameName"], qsTr("Game"))
            launcher: String(card.value(["launcher"], qsTr("Game")))
            artworkSource: card.value([
                "effectiveArtworkUrl",
                "portraitArtwork", "portrait_artwork", "headerArtwork",
                "header_artwork", "fallbackArtwork", "fallback_artwork"
            ], "")
            artworkFillMode: Image.PreserveAspectCrop
            cornerRadius: App.Theme.radiusSmall
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Label {
                        Layout.fillWidth: true
                        text: card.displayTitle()
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBodyLarge
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                    }

                    Label {
                        Layout.fillWidth: true
                        text: App.I18n.gameLabel(
                                  card.value(["gameName"], qsTr("Unknown game")))
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        elide: Text.ElideRight
                    }
                }

                StatusBadge {
                    text: card.prettyStatus()
                    status: card.value(["status"], "queued")
                }
            }

            AppProgressBar {
                Layout.fillWidth: true
                value: card.progressValue()
                indeterminateMode: (String(card.value(["status"], "")).toLowerCase() === "analyzing"
                                    && card.progressValue() <= 0)
                                   || (card.realCompression
                                       && !Boolean(card.value(["progressDeterminate"], true))
                                       && !card.isTerminal())
                progressColor: App.Theme.statusColor(card.value(["status"], "running"))
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Label {
                    visible: card.readOnlyAnalysis
                    text: card.translatedStage()
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontCaption
                    font.weight: Font.DemiBold
                }

                Label {
                    visible: card.readOnlyAnalysis && card.width >= 590
                    text: qsTr("%1 files scanned").arg(Number(card.value(["scannedFiles"], 0)))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                }

                Label {
                    visible: card.readOnlyAnalysis && card.width >= 720
                    text: qsTr("%1 sampled").arg(card.formatBytes(card.value(["analyzedBytes"], 0)))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                }

                Label {
                    visible: card.readOnlyAnalysis && card.width >= 840
                    text: qsTr("%1 s").arg(Math.round(Number(card.value(["elapsedSeconds"], 0))))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                }

                Label {
                    visible: !card.readOnlyAnalysis
                    text: card.realCompression
                          ? card.translatedStage()
                          : qsTr("%1%").arg(Math.round(card.progressValue() * 100))
                    color: App.Theme.text
                    font.pixelSize: App.Theme.fontCaption
                    font.weight: Font.DemiBold
                }

                Label {
                    visible: card.realCompression && card.width >= 580
                    text: qsTr("%1 / %2 files")
                          .arg(Number(card.value(["processedFiles"], 0)))
                          .arg(Number(card.value(["totalFiles"], 0)))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                }

                Label {
                    visible: card.realCompression && card.width >= 700
                    text: qsTr("%1 / %2")
                          .arg(card.formatBytes(card.value(["processedBytes"], 0)))
                          .arg(card.formatBytes(card.value(["totalBytes"], 0)))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                }

                Label {
                    visible: card.realCompression
                             && card.width >= 820
                             && String(card.value(["currentFile"], "")).length > 0
                    Layout.maximumWidth: Math.max(120, card.width * 0.28)
                    text: String(card.value(["currentFile"], ""))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                }

                Label {
                    visible: !card.readOnlyAnalysis && !card.realCompression
                    text: qsTr("%1%").arg(Math.round(card.progressValue() * 100))
                    color: App.Theme.text
                    font.pixelSize: App.Theme.fontCaption
                }

                Label {
                    visible: String(card.value(["error"], "")).length > 0
                    Layout.fillWidth: true
                    text: App.I18n.message(card.value(["error"], ""))
                    color: App.Theme.danger
                    font.pixelSize: App.Theme.fontCaption
                    elide: Text.ElideRight
                }

                Item { Layout.fillWidth: true }
            }
        }

        RowLayout {
            spacing: 2

            IconButton {
                visible: card.isTerminal()
                symbol: "⌫"
                toolTip: qsTr("Remove finished task")
                onClicked: card.removeRequested(String(card.value(["id"], "")))
            }

            IconButton {
                visible: Boolean(card.value(["cancellable"], true))
                         && String(card.value(["status"], "")).toLowerCase() === "paused"
                symbol: "▶"
                toolTip: qsTr("Resume task")
                onClicked: card.resumeRequested(String(card.value(["id"], "")))
            }

            IconButton {
                visible: Boolean(card.value(["cancellable"], true))
                         && Boolean(card.value(["pausable"], true))
                         && ["running", "analyzing", "queued"].indexOf(String(card.value(["status"], "")).toLowerCase()) >= 0
                symbol: "Ⅱ"
                toolTip: qsTr("Pause task")
                onClicked: card.pauseRequested(String(card.value(["id"], "")))
            }

            IconButton {
                visible: Boolean(card.value(["cancellable"], true))
                enabled: !card.isTerminal()
                danger: true
                symbol: "×"
                toolTip: qsTr("Cancel task")
                onClicked: card.cancelRequested(
                    String(card.value(["id"], "")),
                    App.I18n.taskLabel(
                        card.value(["name", "title", "operation"], qsTr("task"))),
                    card.readOnlyOperation)
            }
        }
    }
}
