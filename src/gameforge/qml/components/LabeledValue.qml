import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

ColumnLayout {
    id: field

    property string label: qsTr("Label")
    property string value: "-"
    property bool mono: false

    spacing: 4

    Label {
        text: field.label
        color: App.Theme.textMuted
        font.pixelSize: App.Theme.fontCaption
        Layout.fillWidth: true
        elide: Text.ElideRight
    }

    Label {
        text: field.value
        color: App.Theme.text
        font.pixelSize: App.Theme.fontBody
        font.weight: Font.DemiBold
        font.family: field.mono ? "monospace" : Application.font.family
        Layout.fillWidth: true
        elide: Text.ElideMiddle
        ToolTip.visible: hovered && truncated
        ToolTip.text: text

        HoverHandler { id: hoverHandler }
        property bool hovered: hoverHandler.hovered
    }
}
