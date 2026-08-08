import QtQuick
import QtQuick.Controls
import ".." as App

Switch {
    id: control

    indicator: Rectangle {
        implicitWidth: 42
        implicitHeight: 24
        x: control.leftPadding
        y: parent.height / 2 - height / 2
        radius: height / 2
        color: control.checked ? App.Theme.accent : App.Theme.backgroundElevated
        border.width: 1
        border.color: control.checked ? App.Theme.accent : App.Theme.borderStrong

        Rectangle {
            x: control.checked ? parent.width - width - 3 : 3
            y: 3
            width: 18
            height: 18
            radius: 9
            color: control.checked ? App.Theme.textOnAccent : App.Theme.textSecondary
            Behavior on x { NumberAnimation { duration: App.Theme.animationFast; easing.type: Easing.OutCubic } }
        }

        Behavior on color { ColorAnimation { duration: App.Theme.animationFast } }
    }

    contentItem: Label {
        text: control.text
        color: control.enabled ? App.Theme.text : App.Theme.textMuted
        font.pixelSize: App.Theme.fontBody
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
    }
}

