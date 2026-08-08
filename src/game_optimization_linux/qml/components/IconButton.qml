import QtQuick
import QtQuick.Controls
import ".." as App

Button {
    id: control

    property string symbol: "⋯"
    property string toolTip: ""
    property bool danger: false

    text: symbol
    implicitWidth: 38
    implicitHeight: 38
    padding: 0
    focusPolicy: Qt.StrongFocus

    contentItem: Label {
        text: control.symbol
        color: !control.enabled ? App.Theme.textMuted
                               : control.danger ? App.Theme.danger : App.Theme.textSecondary
        font.pixelSize: 18
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        radius: App.Theme.radiusSmall
        color: control.down ? App.Theme.surfacePressed
                            : control.hovered ? App.Theme.surfaceHover : "transparent"
        border.width: control.visualFocus ? 2 : 0
        border.color: App.Theme.accent
        Behavior on color { ColorAnimation { duration: App.Theme.animationFast } }
    }

    ToolTip.visible: toolTip.length > 0 && hovered
    ToolTip.text: toolTip
    ToolTip.delay: 450
}

