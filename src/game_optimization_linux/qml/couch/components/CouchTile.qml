import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

Button {
    id: control
    property string symbol: ""
    property string subtitle: ""
    property real couchScale: 1.0
    property bool primary: false
    readonly property bool focusVisible: activeFocus || visualFocus || focus

    implicitWidth: 250 * couchScale
    implicitHeight: 118 * couchScale
    focusPolicy: Qt.StrongFocus
    leftPadding: 22 * couchScale
    rightPadding: 22 * couchScale

    contentItem: RowLayout {
        spacing: 16 * control.couchScale
        Label {
            text: control.symbol
            color: control.enabled
                   ? control.focusVisible ? "white" : App.Theme.accent
                   : App.Theme.textMuted
            font.pixelSize: 32 * control.couchScale
            Layout.alignment: Qt.AlignVCenter
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4 * control.couchScale
            Label {
                Layout.fillWidth: true
                text: control.text
                color: control.focusVisible && control.enabled ? "white"
                       : control.primary && control.enabled ? App.Theme.accent
                       : App.Theme.text
                font.pixelSize: 20 * control.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                visible: control.subtitle.length > 0
                text: control.subtitle
                color: control.focusVisible && control.enabled ? "#EAF7F3" : App.Theme.textSecondary
                font.pixelSize: 15 * control.couchScale
                elide: Text.ElideRight
            }
        }
    }

    background: Rectangle {
        radius: App.Theme.couchCardRadius * control.couchScale
        color: control.down ? App.Theme.surfacePressed
                            : control.focusVisible ? App.Theme.accent
                            : control.primary && control.enabled ? App.Theme.accentSoft
                            : control.hovered ? App.Theme.surfaceHover : App.Theme.surface
        border.width: control.focusVisible ? App.Theme.couchFocusWidth * control.couchScale : 1
        border.color: control.focusVisible ? "white"
                      : control.primary && control.enabled ? App.Theme.accent
                      : App.Theme.border
        scale: control.down ? 0.98 : control.focusVisible ? 1.04 : 1.0
        Behavior on scale { NumberAnimation { duration: App.Theme.couchAnimation; easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: App.Theme.couchAnimation } }
    }
}
