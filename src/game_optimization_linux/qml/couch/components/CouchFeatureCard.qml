import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

// Card used in the Couch game-details header: the primary Launch card and the
// GameMode / Gamescope / MangoHud / OptiScaler / Narrator feature cards.
// A card never toggles anything by itself; Confirm opens the existing
// configuration (overlay or tab), so the state shown is always the saved one.
Button {
    id: card

    property real couchScale: 1.0
    property url iconSource: ""
    property url iconLightSource: ""
    property string subtitle: ""
    property string stateText: ""
    // success | warning | danger | info | neutral | unavailable
    property string stateTone: "neutral"
    property string badgeText: ""
    property bool primary: false
    readonly property bool focusVisible: activeFocus || visualFocus || focus

    implicitHeight: 104 * couchScale
    implicitWidth: 300 * couchScale
    focusPolicy: Qt.StrongFocus
    padding: 0
    Accessible.name: text
    Accessible.description: [stateText, subtitle].filter(function(v) { return v.length }).join(", ")

    contentItem: Item {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: App.Theme.couchSpaceL * card.couchScale
            anchors.rightMargin: App.Theme.couchSpaceM * card.couchScale
            spacing: App.Theme.couchSpaceM * card.couchScale

            Rectangle {
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredWidth: (card.primary ? 64 : 56) * card.couchScale
                Layout.preferredHeight: Layout.preferredWidth
                radius: card.primary ? width / 2 : App.Theme.couchRadiusSmall * card.couchScale
                color: card.primary && card.enabled
                       ? Qt.rgba(App.Theme.accent.r, App.Theme.accent.g, App.Theme.accent.b, App.Theme.dark ? 0.18 : 0.14)
                       : Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                                 App.Theme.surfaceRaised.b, 0.95)
                border.width: card.primary ? 2 * card.couchScale : 1
                border.color: card.primary && card.enabled ? App.Theme.accent : App.Theme.border
                CouchIcon {
                    anchors.centerIn: parent
                    anchors.horizontalCenterOffset: card.primary ? 2 * card.couchScale : 0
                    size: App.Theme.couchIconMedium
                    couchScale: card.couchScale
                    source: card.iconSource
                    lightSource: card.iconLightSource
                    muted: !card.enabled
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 4 * card.couchScale
                RowLayout {
                    Layout.fillWidth: true
                    spacing: App.Theme.couchSpaceS * card.couchScale
                    Label {
                        objectName: "couchFeatureCardTitle"
                        Layout.fillWidth: true
                        text: card.text
                        color: !card.enabled ? App.Theme.textMuted
                               : card.focusVisible ? App.Theme.couchFocusText : App.Theme.text
                        font.pixelSize: (card.primary ? 24 : 20) * card.couchScale
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                    }
                    Rectangle {
                        objectName: "couchFeatureCardBadge"
                        visible: card.badgeText.length > 0
                        Layout.alignment: Qt.AlignVCenter
                        implicitHeight: 24 * card.couchScale
                        implicitWidth: badgeLabel.implicitWidth + 14 * card.couchScale
                        radius: height / 2
                        color: App.Theme.infoSoft
                        border.width: 1
                        border.color: App.Theme.info
                        Label {
                            id: badgeLabel
                            anchors.centerIn: parent
                            text: card.badgeText
                            color: App.Theme.couchToneText("info")
                            font.pixelSize: 13 * card.couchScale
                            font.weight: Font.Bold
                        }
                    }
                }
                Row {
                    visible: card.stateText.length > 0
                    spacing: 7 * card.couchScale
                    Rectangle {
                        objectName: "couchFeatureCardStateDot"
                        anchors.verticalCenter: parent.verticalCenter
                        width: 10 * card.couchScale
                        height: width
                        radius: width / 2
                        readonly property bool hollow: card.stateTone === "unavailable" || card.stateTone === "neutral"
                        color: hollow ? "transparent" : App.Theme.couchToneColor(card.stateTone)
                        border.width: hollow ? Math.max(2, 2 * card.couchScale) : 0
                        border.color: App.Theme.couchToneColor(card.stateTone)
                    }
                    Label {
                        objectName: "couchFeatureCardState"
                        anchors.verticalCenter: parent.verticalCenter
                        text: card.stateText
                        color: App.Theme.couchToneText(card.stateTone)
                        font.pixelSize: 16 * card.couchScale
                        font.weight: Font.DemiBold
                    }
                }
                Label {
                    objectName: "couchFeatureCardSubtitle"
                    visible: text.length > 0
                    Layout.fillWidth: true
                    text: card.subtitle
                    color: card.primary && card.enabled ? App.Theme.accent : App.Theme.textSecondary
                    font.pixelSize: (card.primary ? 17 : 15) * card.couchScale
                    elide: Text.ElideRight
                }
            }
            CouchIcon {
                Layout.alignment: Qt.AlignVCenter
                size: App.Theme.couchIconSmall
                couchScale: card.couchScale
                source: App.UiIcons.couchGlyphNext
                lightSource: App.UiIcons.couchGlyphNextOnLight
                muted: !card.enabled
            }
        }
    }

    background: Rectangle {
        objectName: "couchFeatureCardBackground"
        radius: App.Theme.couchCardRadius * card.couchScale
        color: card.down ? App.Theme.surfacePressed
              : card.primary && card.enabled ? App.Theme.accentSoft
              : card.focusVisible ? App.Theme.couchFocusSurface
              : Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b, 0.9)
        border.width: card.primary && card.enabled ? 2 * card.couchScale : 1
        border.color: card.primary && card.enabled ? App.Theme.accent : App.Theme.border
        scale: card.down ? App.Theme.couchPressScale : card.focusVisible ? 1.03 : 1.0
        Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
        CouchFocusFrame {
            active: card.focusVisible
            radius: parent.radius
            couchScale: card.couchScale
            glow: false
        }
    }
}
