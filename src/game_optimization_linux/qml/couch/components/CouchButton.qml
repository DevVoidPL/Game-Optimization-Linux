import QtQuick
import QtQuick.Controls
import "../.." as App

Button {
    id: control
    property real couchScale: 1.0
    property url iconSource: ""
    property url iconLightSource: ""
    property int iconSize: App.Theme.couchIconSizeButton
    property bool iconContainer: false
    readonly property bool focusVisible: activeFocus || visualFocus || focus

    implicitHeight: App.Theme.couchButtonHeight * couchScale
    font.pixelSize: App.Theme.couchBodySize * couchScale
    font.weight: Font.DemiBold

    contentItem: Item {
        implicitWidth: contentRow.implicitWidth
        implicitHeight: contentRow.implicitHeight
        Row {
            id: contentRow
            anchors.centerIn: parent
            spacing: control.text.length > 0 ? 14 * control.couchScale : 0
        Item {
            visible: control.iconSource
            width: visible ? (control.iconSize + (control.iconContainer ? 14 : 0)) * control.couchScale : 0
            height: visible ? (control.iconSize + (control.iconContainer ? 14 : 0)) * control.couchScale : 0
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                anchors.fill: parent
                visible: control.iconContainer
                radius: 14 * control.couchScale
                color: control.focusVisible ? App.Theme.surfaceSelected : App.Theme.surfaceRaised
                border.width: 1
                border.color: control.focusVisible ? App.Theme.accent : App.Theme.border
            }
            CouchIcon {
                objectName: "couchButtonIcon"
                anchors.centerIn: parent
                source: control.iconSource
                lightSource: control.iconLightSource
                size: control.iconSize
                couchScale: control.couchScale
                muted: !control.enabled
            }
        }
        Label {
            objectName: "couchButtonLabel"
            visible: text.length > 0
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: !control.enabled ? App.Theme.textMuted
                   : control.focusVisible ? App.Theme.couchFocusText : App.Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
        }
    }

    background: Rectangle {
        objectName: "couchButtonBackground"
        radius: App.Theme.couchCardRadius * control.couchScale
        color: control.down ? App.Theme.surfacePressed
                            : control.focusVisible ? App.Theme.couchFocusSurface
                            : App.Theme.surfaceRaised
        border.width: 1
        border.color: App.Theme.borderStrong
        scale: control.down ? App.Theme.couchPressScale
                            : control.focusVisible ? 1.035 : 1.0
        Behavior on scale {
            NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic }
        }
        Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
        CouchFocusFrame {
            active: control.focusVisible
            radius: parent.radius
            couchScale: control.couchScale
        }
    }
}
