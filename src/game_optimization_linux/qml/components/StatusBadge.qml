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
    property url iconSource: ""

    implicitWidth: badgeRow.implicitWidth + leftPadding + rightPadding
    implicitHeight: 26
    leftPadding: 9
    rightPadding: 9

    contentItem: Row {
        id: badgeRow
        spacing: 6
        anchors.centerIn: parent

        Rectangle {
            visible: control.showDot && control.iconSource.toString().length === 0
            width: 6
            height: 6
            radius: 3
            color: control.toneColor
            anchors.verticalCenter: parent.verticalCenter
        }

        Image {
            visible: control.iconSource.toString().length > 0
            width: 16
            height: 16
            sourceSize.width: 16
            sourceSize.height: 16
            source: control.iconSource
            fillMode: Image.PreserveAspectFit
            cache: true
            smooth: true
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
