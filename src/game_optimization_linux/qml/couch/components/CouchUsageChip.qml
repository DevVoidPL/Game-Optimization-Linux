import QtQuick
import QtQuick.Controls
import "../.." as App

// One header metric ("CPU 23%"). Styled like the top-bar input chip so it
// follows the light and dark Couch themes. Strings come from the owner.
Rectangle {
    id: chip

    property real couchScale: 1.0
    property string label: ""
    property string value: ""

    implicitHeight: 44 * couchScale
    implicitWidth: row.implicitWidth + 2 * App.Theme.couchSpaceM * couchScale
    radius: App.Theme.couchRadiusSmall * couchScale
    color: Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                   App.Theme.surfaceRaised.b, 0.82)
    border.width: 1
    border.color: App.Theme.border
    Accessible.role: Accessible.StaticText
    Accessible.name: label + " " + value

    Row {
        id: row
        anchors.centerIn: parent
        spacing: App.Theme.couchSpaceS * chip.couchScale
        Label {
            anchors.verticalCenter: parent.verticalCenter
            text: chip.label
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.couchCaptionSize * chip.couchScale
            font.weight: Font.DemiBold
        }
        Label {
            objectName: "couchUsageChipValue"
            anchors.verticalCenter: parent.verticalCenter
            text: chip.value
            color: App.Theme.text
            font.pixelSize: App.Theme.couchLabelSize * chip.couchScale
            font.weight: Font.DemiBold
        }
    }
}
