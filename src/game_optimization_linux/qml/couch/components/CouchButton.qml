import QtQuick
import QtQuick.Controls
import "../.." as App

Button {
    id: control
    property real couchScale: 1.0
    property url iconSource: ""
    property int iconSize: App.Theme.couchIconSizeButton
    readonly property bool focusVisible: activeFocus || visualFocus || focus

    implicitHeight: App.Theme.couchButtonHeight * couchScale
    font.pixelSize: App.Theme.couchBodySize * couchScale
    font.weight: Font.DemiBold

    contentItem: Row {
        spacing: 6 * control.couchScale
        anchors.centerIn: parent
        Image {
            visible: control.iconSource
            source: control.iconSource
            fillMode: Image.PreserveAspectFit
            sourceSize.width: control.iconSize * control.couchScale
            sourceSize.height: control.iconSize * control.couchScale
            width: visible ? control.iconSize * control.couchScale : 0
            height: visible ? control.iconSize * control.couchScale : 0
            anchors.verticalCenter: parent.verticalCenter
        }
        Label {
            text: control.text
            color: !control.enabled ? App.Theme.textMuted
                   : control.focusVisible ? "white" : App.Theme.text
            font: control.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    background: Rectangle {
        radius: App.Theme.couchCardRadius * control.couchScale
        color: control.down ? App.Theme.surfacePressed
                            : control.focusVisible ? App.Theme.accent
                            : App.Theme.surfaceRaised
        border.width: control.focusVisible ? App.Theme.couchFocusWidth * control.couchScale : 1
        border.color: control.focusVisible ? "white" : App.Theme.borderStrong
        scale: control.down ? 0.98 : control.focusVisible ? 1.035 : 1.0
        Behavior on scale {
            NumberAnimation { duration: App.Theme.couchAnimation; easing.type: Easing.OutCubic }
        }
    }
}
