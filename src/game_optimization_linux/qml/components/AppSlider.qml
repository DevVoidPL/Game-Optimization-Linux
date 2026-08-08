import QtQuick
import QtQuick.Controls
import ".." as App

Slider {
    id: control

    implicitWidth: 220
    implicitHeight: 32
    focusPolicy: Qt.StrongFocus

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 200
        implicitHeight: 6
        width: control.availableWidth
        height: implicitHeight
        radius: 3
        color: App.Theme.backgroundElevated

        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: 3
            color: App.Theme.accent
        }
    }

    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: control.pressed ? 20 : 18
        implicitHeight: implicitWidth
        radius: width / 2
        color: App.Theme.accent
        border.width: 3
        border.color: App.Theme.surface

        Behavior on implicitWidth { NumberAnimation { duration: App.Theme.animationFast } }
    }
}

