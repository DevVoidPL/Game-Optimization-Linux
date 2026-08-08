import QtQuick
import QtQuick.Controls
import "../.." as App

FocusScope {
    id: overlay

    default property alias contentData: contentHost.data
    property real couchScale: 1.0
    property real maximumWidth: App.Theme.couchDialogWidth * couchScale
    property real maximumHeight: parent ? parent.height - 96 * couchScale : 900 * couchScale
    property real preferredHeight: 560 * couchScale
    property color panelColor: App.Theme.surfaceRaised

    visible: false
    z: 100
    focus: visible

    Rectangle {
        anchors.fill: parent
        color: App.Theme.modalScrim
    }

    Rectangle {
        id: panel
        anchors.centerIn: parent
        width: Math.min(overlay.width - 2 * App.Theme.couchDialogMargin * overlay.couchScale,
                        overlay.maximumWidth)
        height: Math.min(overlay.preferredHeight, overlay.maximumHeight)
        radius: App.Theme.couchPanelRadius * overlay.couchScale
        color: overlay.panelColor
        border.width: 1
        border.color: App.Theme.borderStrong
        clip: true

        Item {
            id: contentHost
            anchors.fill: parent
            anchors.margins: App.Theme.couchDialogMargin * overlay.couchScale
        }
    }

    opacity: visible ? 1 : 0
    Behavior on opacity {
        NumberAnimation { duration: App.Theme.couchAnimation }
    }
}
