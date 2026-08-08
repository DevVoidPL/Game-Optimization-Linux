import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

RowLayout {
    id: header

    property string title: qsTr("Page")
    property string subtitle: ""
    default property alias actions: actionArea.data

    spacing: App.Theme.spacingMedium

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 3

        Label {
            text: header.title
            color: App.Theme.text
            font.pixelSize: App.Theme.fontDisplay
            font.weight: Font.Bold
            Layout.fillWidth: true
            elide: Text.ElideRight
        }

        Label {
            visible: header.subtitle.length > 0
            text: header.subtitle
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.fontBody
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }

    RowLayout {
        id: actionArea
        spacing: App.Theme.spacingSmall
    }
}
