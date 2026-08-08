import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

Item {
    id: state

    property string symbol: "◇"
    property string title: qsTr("Nothing here yet")
    property string message: qsTr("Content will appear here when it is available.")
    property string actionText: ""
    signal actionTriggered()

    implicitHeight: 300

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 440)
        spacing: 10

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 64
            Layout.preferredHeight: 64
            radius: 20
            color: App.Theme.accentSoft

            Label {
                anchors.centerIn: parent
                text: state.symbol
                color: App.Theme.accent
                font.pixelSize: 28
                font.weight: Font.Bold
            }
        }

        Label {
            Layout.fillWidth: true
            text: state.title
            color: App.Theme.text
            font.pixelSize: 18
            font.weight: Font.Bold
            horizontalAlignment: Text.AlignHCenter
        }

        Label {
            Layout.fillWidth: true
            text: state.message
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.fontBody
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        AppButton {
            visible: state.actionText.length > 0
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 8
            text: state.actionText
            kind: "primary"
            onClicked: state.actionTriggered()
        }
    }
}
