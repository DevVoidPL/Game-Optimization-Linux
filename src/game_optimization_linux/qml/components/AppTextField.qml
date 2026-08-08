import QtQuick
import QtQuick.Controls
import ".." as App

TextField {
    id: control

    property string leadingSymbol: ""

    implicitHeight: App.Theme.controlHeight
    implicitWidth: 220
    leftPadding: leadingSymbol.length > 0 ? 38 : 13
    rightPadding: 13
    color: App.Theme.text
    placeholderTextColor: App.Theme.textMuted
    selectionColor: App.Theme.accent
    selectedTextColor: App.Theme.textOnAccent
    font.pixelSize: App.Theme.fontBody

    Label {
        visible: control.leadingSymbol.length > 0
        text: control.leadingSymbol
        color: App.Theme.textMuted
        font.pixelSize: 16
        anchors.left: parent.left
        anchors.leftMargin: 13
        anchors.verticalCenter: parent.verticalCenter
    }

    background: Rectangle {
        radius: App.Theme.radiusSmall
        color: App.Theme.input
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? App.Theme.accent : App.Theme.border
        Behavior on border.color { ColorAnimation { duration: App.Theme.animationFast } }
    }
}

