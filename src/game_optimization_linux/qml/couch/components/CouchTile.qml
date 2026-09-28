import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

Button {
    id: control
    property string symbol: ""
    property url iconSource: ""
    property url iconLightSource: ""
    property string subtitle: ""
    property real couchScale: 1.0
    property bool primary: false
    property bool showChevron: false
    // Compact tiles are used inside content panels (e.g. game-details actions).
    property bool compact: false
    readonly property real iconContainerSize: (compact ? 56 : 76) * couchScale
    readonly property real contentInset: (compact ? 20 : 28) * couchScale
    readonly property bool focusVisible: activeFocus || visualFocus || focus

    implicitWidth: 250 * couchScale
    implicitHeight: (compact ? 88 : 132) * couchScale
    focusPolicy: Qt.StrongFocus
    leftPadding: contentInset
    rightPadding: contentInset

    contentItem: Item {
        RowLayout {
            id: contentGroup
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            height: implicitHeight
            spacing: (control.compact ? 14 : 20) * control.couchScale

            Item {
                id: symbolSlot
                visible: !control.iconSource && control.symbol.length > 0
                Layout.preferredWidth: control.iconContainerSize
                Layout.preferredHeight: control.iconContainerSize
                Layout.alignment: Qt.AlignVCenter
                Label {
                    anchors.centerIn: parent
                    text: control.symbol
                    color: control.enabled
                           ? control.focusVisible ? App.Theme.couchFocusText : App.Theme.accent
                           : App.Theme.textMuted
                    font.pixelSize: 32 * control.couchScale
                }
            }
            Item {
                id: iconSlot
                visible: control.iconSource
                Layout.preferredWidth: control.iconContainerSize
                Layout.preferredHeight: control.iconContainerSize
                Layout.alignment: Qt.AlignVCenter
                Rectangle {
                    anchors.fill: parent
                    radius: 20 * control.couchScale
                    color: control.focusVisible ? App.Theme.surfaceSelected
                                                 : App.Theme.surfaceRaised
                    border.width: control.focusVisible ? 2 * control.couchScale : 1
                    border.color: control.focusVisible ? App.Theme.accent : App.Theme.borderStrong
                }
                CouchIcon {
                    objectName: "couchTileIcon"
                    anchors.centerIn: parent
                    source: control.iconSource
                    lightSource: control.iconLightSource
                    size: control.compact ? App.Theme.couchIconMedium : App.Theme.couchIconSizeTile
                    couchScale: control.couchScale
                    muted: !control.enabled
                }
            }
            ColumnLayout {
                id: textGroup
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 7 * control.couchScale
                Label {
                    objectName: "couchTileTitle"
                    Layout.fillWidth: true
                    text: control.text
                    color: control.focusVisible && control.enabled ? App.Theme.couchFocusText
                           : control.primary && control.enabled ? App.Theme.accent
                           : App.Theme.text
                    font.pixelSize: 22 * control.couchScale
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    visible: control.subtitle.length > 0
                    text: control.subtitle
                    color: control.focusVisible && control.enabled ? App.Theme.couchFocusSubtext : App.Theme.textSecondary
                    font.pixelSize: 16 * control.couchScale
                    elide: Text.ElideRight
                }
            }
            CouchIcon {
                id: actionSlot
                objectName: "couchTileChevron"
                visible: control.showChevron
                Layout.alignment: Qt.AlignVCenter
                size: App.Theme.couchIconSmall
                couchScale: control.couchScale
                source: App.UiIcons.couchGlyphNext
                lightSource: App.UiIcons.couchGlyphNextOnLight
                muted: !control.enabled
            }
        }
    }

    background: Rectangle {
        objectName: "couchTileBackground"
        radius: App.Theme.couchCardRadius * control.couchScale
        color: control.down ? App.Theme.surfacePressed
                            : control.focusVisible ? App.Theme.couchFocusSurface
                            : control.primary && control.enabled ? App.Theme.accentSoft
                            : control.hovered ? App.Theme.surfaceHover : App.Theme.surface
        border.width: 1
        border.color: control.primary && control.enabled ? App.Theme.accent : App.Theme.border
        scale: control.down ? App.Theme.couchPressScale
                            : control.focusVisible ? App.Theme.couchFocusScale : 1.0
        Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
        CouchFocusFrame {
            active: control.focusVisible
            radius: parent.radius
            couchScale: control.couchScale
        }
    }
}
