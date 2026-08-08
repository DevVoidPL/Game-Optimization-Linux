import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

Rectangle {
    id: tile

    property string label: qsTr("Metric")
    property string value: "-"
    property string symbol: "◈"
    property color tone: App.Theme.accent

    implicitWidth: 160
    implicitHeight: 88
    radius: App.Theme.radiusMedium
    color: App.Theme.surfaceRaised
    border.width: 1
    border.color: App.Theme.border

    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 11

        Rectangle {
            Layout.preferredWidth: 36
            Layout.preferredHeight: 36
            radius: 10
            color: Qt.rgba(tile.tone.r, tile.tone.g, tile.tone.b, 0.16)

            Label {
                anchors.centerIn: parent
                text: tile.symbol
                color: tile.tone
                font.pixelSize: 17
                font.weight: Font.Bold
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Label {
                text: tile.value
                color: App.Theme.text
                font.pixelSize: App.Theme.fontBodyLarge
                font.weight: Font.Bold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                text: tile.label
                color: App.Theme.textMuted
                font.pixelSize: App.Theme.fontCaption
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }
    }
}
