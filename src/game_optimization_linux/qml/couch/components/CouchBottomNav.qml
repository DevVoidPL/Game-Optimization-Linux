import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

// Global Couch Mode section navigation: one shared outer container with four
// equal entries (icon, title, short subtitle).
//
// model entries: { id, title, subtitle, icon, iconOnLight }
// * `activeIndex`  - the section the user is currently in (persistent cue:
//                    accent tint and outline, visible without focus);
// * `currentIndex` - the entry that owns controller/keyboard focus while
//                    `navFocused` is true (focus ring + glow + scale).
Rectangle {
    id: nav
    objectName: "couchBottomNav"

    property var model: []
    property int activeIndex: -1
    property int currentIndex: 0
    property bool navFocused: false
    property real couchScale: 1.0

    signal activated(int index)

    readonly property int count: repeater.count
    function itemAt(index) { return repeater.itemAt(index) }

    implicitHeight: 104 * couchScale
    radius: App.Theme.couchPanelRadius * couchScale
    color: Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b,
                   App.Theme.dark ? 0.86 : 0.92)
    border.width: 1
    border.color: App.Theme.border

    RowLayout {
        anchors.fill: parent
        anchors.margins: App.Theme.couchSpaceM * nav.couchScale
        spacing: App.Theme.couchSpaceM * nav.couchScale

        Repeater {
            id: repeater
            model: nav.model
            delegate: Button {
                id: entry
                objectName: "couchBottomNavItem"
                required property var modelData
                required property int index
                readonly property bool active: nav.activeIndex === index
                readonly property bool focusVisible: nav.navFocused && nav.currentIndex === index
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                focusPolicy: Qt.NoFocus
                focus: focusVisible
                padding: 0
                Accessible.name: String(modelData.title || "")
                Accessible.description: String(modelData.subtitle || "")
                onClicked: nav.activated(index)

                background: Rectangle {
                    radius: App.Theme.couchCardRadius * nav.couchScale
                    color: entry.down ? App.Theme.surfacePressed
                          : entry.active ? App.Theme.accentSoft
                          : entry.hovered ? App.Theme.surfaceHover
                          : "transparent"
                    border.width: entry.active ? 2 * nav.couchScale : 0
                    border.color: App.Theme.accent
                    scale: entry.down ? App.Theme.couchPressScale
                                      : entry.focusVisible ? 1.03 : 1.0
                    Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(140); easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(140) } }
                    CouchFocusFrame {
                        active: entry.focusVisible
                        radius: parent.radius
                        couchScale: nav.couchScale
                    }
                }

                contentItem: Item {
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: App.Theme.couchSpaceL * nav.couchScale
                        anchors.rightMargin: App.Theme.couchSpaceM * nav.couchScale
                        spacing: App.Theme.couchSpaceM * nav.couchScale

                        Rectangle {
                            Layout.alignment: Qt.AlignVCenter
                            Layout.preferredWidth: 52 * nav.couchScale
                            Layout.preferredHeight: 52 * nav.couchScale
                            radius: App.Theme.couchRadiusSmall * nav.couchScale
                            color: entry.active ? Qt.rgba(App.Theme.accent.r, App.Theme.accent.g, App.Theme.accent.b, App.Theme.dark ? 0.16 : 0.14)
                                                : App.Theme.surfaceRaised
                            CouchIcon {
                                anchors.centerIn: parent
                                size: App.Theme.couchIconMedium
                                couchScale: nav.couchScale
                                source: entry.modelData.icon || ""
                                lightSource: entry.modelData.iconOnLight || ""
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 2 * nav.couchScale
                            Label {
                                objectName: "couchBottomNavTitle"
                                Layout.fillWidth: true
                                text: String(entry.modelData.title || "")
                                color: entry.focusVisible ? App.Theme.couchFocusText : App.Theme.text
                                font.pixelSize: 21 * nav.couchScale
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                            }
                            Label {
                                objectName: "couchBottomNavSubtitle"
                                Layout.fillWidth: true
                                text: String(entry.modelData.subtitle || "")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.couchCaptionSize * nav.couchScale
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }
    }
}
