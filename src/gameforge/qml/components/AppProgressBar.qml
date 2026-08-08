import QtQuick
import QtQuick.Controls
import ".." as App

ProgressBar {
    id: control

    property bool indeterminateMode: false
    property color progressColor: App.Theme.accent

    from: 0
    to: 1
    indeterminate: indeterminateMode
    implicitHeight: 7

    background: Rectangle {
        implicitHeight: 7
        radius: height / 2
        color: App.Theme.backgroundElevated
    }

    contentItem: Item {
        implicitHeight: 7
        clip: true

        Rectangle {
            visible: !control.indeterminate
            width: parent.width * Math.max(0, Math.min(1, control.visualPosition))
            height: parent.height
            radius: height / 2
            color: control.progressColor

            Behavior on width {
                NumberAnimation { duration: App.Theme.animationNormal; easing.type: Easing.OutCubic }
            }
        }

        Rectangle {
            visible: control.indeterminate
            width: parent.width * 0.35
            height: parent.height
            radius: height / 2
            color: control.progressColor

            SequentialAnimation on x {
                running: control.indeterminate
                loops: Animation.Infinite
                NumberAnimation {
                    from: -control.width * 0.35
                    to: control.width
                    duration: 1100
                    easing.type: Easing.InOutSine
                }
            }
        }
    }
}
