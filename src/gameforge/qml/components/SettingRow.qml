import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

Item {
    id: row

    property string title: qsTr("Setting")
    property string description: ""
    default property alias controlData: controlSlot.data

    implicitHeight: Math.max(58, descriptionColumn.implicitHeight + 18)

    RowLayout {
        anchors.fill: parent
        spacing: 18

        ColumnLayout {
            id: descriptionColumn
            Layout.fillWidth: true
            spacing: 3

            Label {
                text: row.title
                color: App.Theme.text
                font.pixelSize: App.Theme.fontBody
                font.weight: Font.DemiBold
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }

            Label {
                visible: row.description.length > 0
                text: row.description
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.fontCaption
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            id: controlSlot
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            spacing: 8
        }
    }
}
