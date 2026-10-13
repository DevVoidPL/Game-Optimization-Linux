import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import "../../components"
import "../.." as App

// Portrait cover card for Couch carousels.
//
// One ring only: the selected card gets a single accent ring; the soft glow
// appears only while the carousel itself has focus. Unselected cards have a
// hairline edge and are slightly dimmed. The title sits on a gradient at the
// bottom; the launcher badge shows only when the launcher is known; the play
// marker shows on the selected card only when the game can really launch.
Button {
    id: card

    property string gameId: ""
    property string title: ""
    property string launcher: ""
    // Text shown in the launcher badge; defaults to the raw launcher name.
    property string launcherLabel: App.I18n.launcherName(launcher)
    // Optional real availability problem (e.g. "Drive disconnected"). Empty
    // on Home; the Library sets it only for games that cannot be launched.
    property string stateText: ""
    // "offline" (library drive disconnected), "danger" (files missing) or
    // "warning" (launch blocked for another real reason).
    property string stateTone: "warning"
    readonly property bool stateSevere: stateTone === "offline" || stateTone === "danger"
    property url artworkSource: ""
    property bool selected: false
    property bool carouselFocused: false
    property bool launchable: false
    property real couchScale: 1.0
    readonly property real cardRadius: 16 * couchScale
    readonly property bool glowing: selected && carouselFocused

    padding: 0
    focusPolicy: Qt.NoFocus
    scale: selected ? 1.05 : 1.0
    z: selected ? 2 : 1
    Accessible.name: title
    // Full launcher name even when the badge is elided.
    Accessible.description: launcherLabel
    ToolTip.visible: hovered && launcherLabel.length > 0
    ToolTip.text: launcherLabel
    ToolTip.delay: 600

    Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(160); easing.type: Easing.OutCubic } }

    contentItem: Item {}

    background: Item {
        RectangularShadow {
            objectName: "couchGameCardGlow"
            anchors.fill: parent
            visible: card.glowing
            radius: card.cardRadius
            blur: 26 * card.couchScale
            color: Qt.rgba(App.Theme.accent.r, App.Theme.accent.g, App.Theme.accent.b,
                           App.Theme.dark ? 0.46 : 0.32)
        }
        Rectangle {
            id: clipper
            anchors.fill: parent
            radius: card.cardRadius
            color: App.Theme.surface
            clip: true
            GameArtwork {
                anchors.fill: parent
                gameId: card.gameId
                title: card.title
                launcher: card.launcher.length ? card.launcher : "Steam"
                artworkSource: card.artworkSource
                artworkFillMode: Image.PreserveAspectCrop
                cornerRadius: card.cardRadius
            }
            // Title plate: always dark because it sits on imagery.
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.height * 0.34
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "#000A0E14" }
                    GradientStop { position: 0.55; color: "#C80A0E14" }
                    GradientStop { position: 1.0; color: "#F00A0E14" }
                }
            }
            Rectangle {
                anchors.fill: parent
                color: "#000000"
                opacity: card.selected ? 0 : 0.22
                Behavior on opacity { NumberAnimation { duration: App.Theme.couchFadeDuration(140) } }
            }
        }
        // Launcher badge (only when known).
        Rectangle {
            objectName: "couchGameCardLauncher"
            visible: card.launcher.length > 0
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.margins: 10 * card.couchScale
            // Known launcher: logo only (full name in Accessible.description).
            readonly property url logo: App.UiIcons.launcherLogo(card.launcher)
            readonly property bool hasLogo: String(logo).length > 0
            height: 30 * card.couchScale
            width: hasLogo ? height
                           : Math.min(badgeRow.implicitWidth + 16 * card.couchScale,
                                      card.width - 20 * card.couchScale)
            clip: true
            radius: height / 2
            // Colour logos keep their colours; a light neutral plate gives the
            // dark Steam/Heroic marks contrast on any cover.
            color: hasLogo ? "#F2F3F6FA" : "#D90A0E14"
            border.width: 1
            border.color: hasLogo ? "#33000000" : "#40FFFFFF"
            Row {
                id: badgeRow
                anchors.centerIn: parent
                spacing: 5 * card.couchScale
                CouchIcon {
                    objectName: "couchGameCardLauncherIcon"
                    anchors.verticalCenter: parent.verticalCenter
                    size: 20
                    couchScale: card.couchScale
                    source: parent.parent.hasLogo ? parent.parent.logo : App.UiIcons.couchGlyphSource
                    lightSource: source
                }
                Label {
                    visible: !parent.parent.hasLogo
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, card.width - 60 * card.couchScale)
                    text: card.launcherLabel
                    elide: Text.ElideRight
                    color: "#F2F6FA"
                    font.pixelSize: 13 * card.couchScale
                    font.weight: Font.DemiBold
                }
            }
        }
        // Availability problem, above the title (plate is always dark).
        Rectangle {
            objectName: "couchGameCardState"
            visible: card.stateText.length > 0
            anchors.left: parent.left
            anchors.bottom: titleLabel.top
            anchors.leftMargin: 12 * card.couchScale
            anchors.bottomMargin: 8 * card.couchScale
            width: Math.min(stateRow.implicitWidth + 16 * card.couchScale,
                            parent.width - 24 * card.couchScale)
            height: 30 * card.couchScale
            radius: height / 2
            color: "#E60A0E14"
            border.width: 1
            border.color: card.stateSevere ? "#FF7A86" : "#F2C260"
            Row {
                id: stateRow
                anchors.left: parent.left
                anchors.leftMargin: 8 * card.couchScale
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6 * card.couchScale
                CouchIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    size: 18
                    couchScale: card.couchScale
                    source: card.stateTone === "offline" ? App.UiIcons.couchGlyphLibraryOffline
                            : card.stateTone === "danger" ? App.UiIcons.couchGlyphStatusError
                            : App.UiIcons.couchGlyphStatusWarning
                    lightSource: source
                }
                Label {
                    objectName: "couchGameCardStateLabel"
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth,
                                    card.width - 24 * card.couchScale - 40 * card.couchScale)
                    text: card.stateText
                    color: card.stateSevere ? "#FFB3BA" : "#F7D58E"
                    font.pixelSize: 13 * card.couchScale
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
            }
        }
        Label {
            id: titleLabel
            objectName: "couchGameCardTitle"
            anchors.left: parent.left
            anchors.right: launchMarker.visible ? launchMarker.left : parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 14 * card.couchScale
            anchors.rightMargin: 10 * card.couchScale
            anchors.bottomMargin: 12 * card.couchScale
            text: card.title
            color: "#FFFFFF"
            font.pixelSize: 18 * card.couchScale
            font.weight: Font.Bold
            maximumLineCount: 2
            wrapMode: Text.Wrap
            elide: Text.ElideRight
        }
        CouchIcon {
            id: launchMarker
            objectName: "couchGameCardLaunchMarker"
            visible: card.selected && card.launchable
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 12 * card.couchScale
            size: App.Theme.couchIconSmall
            couchScale: card.couchScale
            source: App.UiIcons.couchGlyphLaunch
            lightSource: App.UiIcons.couchGlyphLaunch
        }
        Rectangle {
            objectName: "couchGameCardSelection"
            anchors.fill: parent
            radius: card.cardRadius
            color: "transparent"
            border.width: card.selected ? 3 * card.couchScale : 1
            border.color: card.selected ? App.Theme.accent
                                        : Qt.rgba(1, 1, 1, App.Theme.dark ? 0.10 : 0.0)
        }
    }
}
