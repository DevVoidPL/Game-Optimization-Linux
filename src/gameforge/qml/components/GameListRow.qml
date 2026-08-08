import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

SurfaceCard {
    id: row
    objectName: "gameListRow"

    property var gameData: ({})
    signal openRequested(var gameId)

    interactive: true
    padding: 14
    implicitHeight: 104
    clip: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: qsTr("Open details for %1").arg(value(["name", "title"], qsTr("game")))

    function value(keys, fallback) {
        var source = gameData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function calculatingSize() {
        return String(value(["sizeScanStatus", "size_scan_status"], "")) === "calculating"
    }

    function sizeScanFailed() {
        return String(value(["sizeScanStatus", "size_scan_status"], "")) === "failed"
    }

    Keys.onReturnPressed: openRequested(value(["id"], ""))
    Keys.onEnterPressed: openRequested(value(["id"], ""))
    Keys.onSpacePressed: openRequested(value(["id"], ""))

    contentItem: RowLayout {
        spacing: 14

        GameArtwork {
            objectName: "listRowCover"
            Layout.preferredWidth: 128
            Layout.preferredHeight: 72
            compact: true
            gameId: String(row.value(["id", "gameId"], ""))
            title: row.value(["name", "title"], qsTr("Game"))
            launcher: row.value(["launcher", "provider"], qsTr("Library"))
            diagnosticViewKind: "list"
            artworkSource: row.value([
                "effectiveArtworkUrl",
                "headerArtwork",
                "headerArtworkUrl",
                "fallbackArtwork",
                "portraitArtwork",
                "cover"
            ], "")
            artworkFillMode: Image.PreserveAspectFit
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 150
            spacing: 4

            Label {
                id: gameTitle
                objectName: "listRowTitle"
                Layout.fillWidth: true
                text: row.value(["name", "title"], qsTr("Untitled game"))
                color: App.Theme.text
                font.pixelSize: App.Theme.fontBodyLarge
                font.weight: Font.Bold
                elide: Text.ElideRight
                ToolTip.visible: titleHover.hovered && truncated
                ToolTip.text: text
                HoverHandler { id: titleHover }
            }

            Label {
                id: gamePath
                objectName: "listRowPath"
                Layout.fillWidth: true
                text: row.value(["path", "location", "installPath", "install_path"], qsTr("Location unavailable"))
                color: App.Theme.textMuted
                font.pixelSize: 10
                font.family: "monospace"
                elide: Text.ElideMiddle
                ToolTip.visible: pathHover.hovered && truncated
                ToolTip.text: text
                HoverHandler { id: pathHover }
            }
        }

        LabeledValue {
            visible: row.width >= 760
            Layout.preferredWidth: 100
            label: qsTr("Source")
            value: row.value(["dataSource", "data_source", "launcher", "provider"], qsTr("Manual"))
        }

        LabeledValue {
            visible: row.width >= 650
            Layout.preferredWidth: 85
            label: row.calculatingSize() ? qsTr("Calculating…")
                                         : row.sizeScanFailed() ? qsTr("Scan incomplete")
                                                                : qsTr("Size")
            value: row.value(["size", "logicalSize", "logical_size"], "-")
            ToolTip.visible: sizeScanHover.hovered && row.sizeScanFailed()
            ToolTip.text: App.I18n.message(
                              row.value(["sizeScanError", "size_scan_error"],
                                        qsTr("Exact size scan was incomplete")))

            HoverHandler { id: sizeScanHover }
        }

        LabeledValue {
            visible: row.width >= 560
            Layout.preferredWidth: 75
            label: qsTr("Filesystem")
            value: row.value(["filesystem", "fileSystem", "file_system"], "-")
            mono: true
        }

        ColumnLayout {
            visible: row.width >= 880
            Layout.preferredWidth: 125
            spacing: 4

            Label {
                text: App.I18n.compressionClassification(
                          row.value(["compressionClassificationKey"],
                                    "measurement_unavailable"))
                color: App.Theme.textMuted
                font.pixelSize: App.Theme.fontCaption
                elide: Text.ElideRight
            }

            Label {
                text: row.value(["savedSpace", "saved_space"], qsTr("Not available"))
                color: App.Theme.success
                font.pixelSize: App.Theme.fontBody
                font.weight: Font.Bold
            }
        }

        StatusBadge {
            visible: row.width >= 720
            text: row.value(["status", "lastTaskStatus", "last_task_status"], qsTr("Ready"))
            status: text
        }

        IconButton {
            symbol: "›"
            toolTip: qsTr("Open details")
            onClicked: row.openRequested(row.value(["id"], ""))
        }
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: row.openRequested(row.value(["id"], ""))
    }
}
