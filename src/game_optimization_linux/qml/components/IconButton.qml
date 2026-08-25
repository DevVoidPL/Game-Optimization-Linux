import QtQuick
import QtQuick.Controls
import ".." as App

Button {
    id: control

    property string symbol: "⋯"
    property url iconSource: ""
    property string toolTip: ""
    property bool danger: false

    text: symbol
    implicitWidth: 38
    implicitHeight: 38
    padding: 0
    focusPolicy: Qt.StrongFocus

    contentItem: Item {
        Label {
            visible: control.iconSource.toString().length === 0
            anchors.fill: parent
            text: control.symbol
            color: !control.enabled ? App.Theme.textMuted
                                   : control.danger ? App.Theme.danger : App.Theme.textSecondary
            font.pixelSize: 18
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        UiIcon {
            visible: control.iconSource.toString().length > 0
            anchors.centerIn: parent
            width: 18
            height: 18
            source: control.iconSource
            sourceSize.width: 18
            sourceSize.height: 18
            tintEnabled: !App.Theme.dark
            tintColor: !control.enabled ? App.Theme.textMuted
                                      : control.danger ? App.Theme.danger
                                                       : App.Theme.textSecondary
        }
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
