import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

SurfaceCard {
    id: card
    objectName: "gameGridCard"

    property var gameData: ({})
    readonly property real portraitCoverHeight: Math.round(Math.max(1, width) * 1.5)
    readonly property real informationHeight: 196
    signal openRequested(var gameId)

    interactive: true
    padding: 0
    implicitHeight: portraitCoverHeight + informationHeight
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

    function available() {
        return Boolean(value(["compressionAvailable", "compression_available"], false))
    }

    function libraryAvailable() {
        return Boolean(value(["libraryAvailable", "library_available"], true))
    }

    function calculatingSize() {
        return String(value(["sizeScanStatus", "size_scan_status"], "")) === "calculating"
    }

    function sizeScanFailed() {
        return String(value(["sizeScanStatus", "size_scan_status"], "")) === "failed"
    }

    function displayedSize() {
        var manifestSize = value(["size", "logicalSize", "logical_size"], "-")
        if (calculatingSize())
            return qsTr("%1 · Calculating…").arg(manifestSize)
        if (sizeScanFailed())
            return qsTr("%1 · Scan incomplete").arg(manifestSize)
        return manifestSize
    }

    function footerText() {
        if (!libraryAvailable())
            return qsTr("Library unavailable")
        var key = String(value(["compressionClassificationKey"],
                               "measurement_unavailable"))
        var filesystem = String(value(["filesystem", "fileSystem", "file_system"], ""))
                             .toLowerCase()
        if (available() || filesystem === "btrfs")
            return App.I18n.compressionClassification(key)
        return available() ? qsTr("Compression available") : qsTr("Btrfs unavailable")
    }

    Keys.onReturnPressed: openRequested(value(["id"], ""))
    Keys.onEnterPressed: openRequested(value(["id"], ""))
    Keys.onSpacePressed: openRequested(value(["id"], ""))

    contentItem: ColumnLayout {
        spacing: 0

        Item {
            id: coverSection
            objectName: "gridCardCoverSection"
            Layout.fillWidth: true
            Layout.preferredHeight: card.portraitCoverHeight
            clip: true

            GameArtwork {
                objectName: "gridCardCover"
                anchors.fill: parent
                gameId: String(card.value(["id", "gameId"], ""))
                title: card.value(["name", "title"], qsTr("Game"))
                launcher: card.value(["launcher", "provider"], qsTr("Library"))
                diagnosticViewKind: "grid"
                artworkSource: card.value([
                    "effectiveArtworkUrl",
                    "portraitArtwork",
                    "portraitArtworkUrl",
                    "headerArtwork",
                    "fallbackArtwork",
                    "cover"
                ], "")
                artworkFillMode: Image.PreserveAspectCrop
                cornerRadius: App.Theme.radiusLarge
            }
        }

        Item {
            id: informationSection
            objectName: "gridCardInformationSection"
            Layout.fillWidth: true
            Layout.preferredHeight: card.informationHeight
            clip: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 7

                RowLayout {
                    objectName: "gridCardTitleRow"
                    Layout.fillWidth: true
                    spacing: 8

                    Label {
                        id: gameTitle
                        objectName: "gridCardTitle"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: card.value(["name", "title"], qsTr("Untitled game"))
                        color: App.Theme.text
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        maximumLineCount: 2
                        wrapMode: Text.Wrap
                        elide: Text.ElideRight
                        ToolTip.visible: titleHover.hovered
                        ToolTip.text: text
                        HoverHandler { id: titleHover }
                    }

                    StatusBadge {
                        id: gameStatus
                        objectName: "gridCardStatus"
                        Layout.alignment: Qt.AlignTop
                        text: card.value(["status", "lastTaskStatus", "last_task_status"], qsTr("Ready"))
                        status: text
                        showDot: true
                    }
                }

                RowLayout {
                    objectName: "gridCardMetadataRow"
                    Layout.fillWidth: true
                    spacing: 8

                    Label {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: qsTr("%1 · %2")
                              .arg(card.value(["dataSource", "data_source", "launcher", "provider"], qsTr("Manual")))
                              .arg(card.displayedSize())
                        color: card.sizeScanFailed() ? App.Theme.danger
                                                     : card.calculatingSize() ? App.Theme.accent
                                                                              : App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        elide: Text.ElideRight
                        ToolTip.visible: metadataHover.hovered
                        ToolTip.text: text
                        HoverHandler { id: metadataHover }
                    }

                    Label {
                        id: filesystemLabel
                        objectName: "gridCardFilesystem"
                        Layout.maximumWidth: 78
                        text: card.value(["filesystem", "fileSystem", "file_system"], qsTr("Unknown"))
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        font.family: "monospace"
                        elide: Text.ElideRight
                        ToolTip.visible: filesystemHover.hovered
                        ToolTip.text: text
                        HoverHandler { id: filesystemHover }
                    }
                }

                Label {
                    id: gamePath
                    objectName: "gridCardPath"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: card.value(["path", "location", "installPath", "install_path"], qsTr("Location unavailable"))
                    color: App.Theme.textMuted
                    font.pixelSize: 10
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                    ToolTip.visible: pathHover.hovered
                    ToolTip.text: text
                    HoverHandler { id: pathHover }
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: App.Theme.border
                }

                RowLayout {
                    id: footerRow
                    objectName: "gridCardFooterRow"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36
                    spacing: 8

                    Rectangle {
                        Layout.preferredWidth: 30
                        Layout.preferredHeight: 30
                        radius: 9
                        color: card.libraryAvailable()
                               ? (card.available() ? App.Theme.successSoft : App.Theme.dangerSoft)
                               : App.Theme.warningSoft

                        Label {
                            anchors.centerIn: parent
                            text: !card.libraryAvailable() ? "!" : card.available() ? "✓" : "×"
                            color: !card.libraryAvailable() ? App.Theme.warning
                                   : card.available() ? App.Theme.success : App.Theme.danger
                            font.pixelSize: 14
                            font.weight: Font.Bold
                        }
                    }

                    Label {
                        id: footerDescription
                        objectName: "gridCardFooterDescription"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: card.footerText()
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontCaption
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        ToolTip.visible: footerHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: footerHover }
                    }

                    AppButton {
                        objectName: "gridCardDetailsButton"
                        text: qsTr("Details")
                        compact: true
                        kind: "ghost"
                        iconText: "›"
                        onClicked: card.openRequested(card.value(["id"], ""))
                    }
                }
            }
        }
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: card.openRequested(card.value(["id"], ""))
    }
}
