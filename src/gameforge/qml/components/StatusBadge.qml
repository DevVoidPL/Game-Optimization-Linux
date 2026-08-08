import QtQuick
import QtQuick.Controls
import ".." as App

Control {
    id: control

    property string status: text
    property string text: qsTr("Unknown")
    property color toneColor: App.Theme.statusColor(status)
    property color fillColor: App.Theme.statusSurface(status)
    property bool showDot: true

    implicitWidth: badgeRow.implicitWidth + leftPadding + rightPadding
    implicitHeight: 26
    leftPadding: 9
    rightPadding: 9

    contentItem: Row {
        id: badgeRow
        spacing: 6
        anchors.centerIn: parent

        Rectangle {
            visible: control.showDot
            width: 6
            height: 6
            radius: 3
            color: control.toneColor
            anchors.verticalCenter: parent.verticalCenter
        }

        Label {
            text: App.I18n.status(control.text)
            color: control.toneColor
            font.pixelSize: App.Theme.fontCaption
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    background: Rectangle {
        radius: height / 2
        color: control.fillColor
    }
}
