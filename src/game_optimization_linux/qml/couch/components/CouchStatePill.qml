import QtQuick
import QtQuick.Controls
import "../.." as App

// Semantic state label. A state is always communicated by text plus a shape,
// never by colour alone: a filled dot for success/warning/danger/info/neutral,
// a hollow ring for "unavailable". An optional icon replaces the dot.
Rectangle {
    id: pill

    property string text: ""
    // success | warning | danger | info | neutral | unavailable
    property string tone: "neutral"
    property real couchScale: 1.0
    property url iconSource: ""
    property url iconLightSource: ""
    property real maximumTextWidth: 420 * couchScale

    readonly property bool hollow: tone === "unavailable"
    readonly property color toneColor: App.Theme.couchToneColor(tone)
    readonly property color labelColor: App.Theme.couchToneText(tone)
    readonly property bool hasIcon: String(iconSource).length > 0

    implicitHeight: Math.round(38 * couchScale)
    implicitWidth: content.implicitWidth + Math.round(2 * App.Theme.couchSpaceM * couchScale)
    radius: height / 2
    color: App.Theme.couchToneSurface(tone)
    border.width: Math.max(1, Math.round(couchScale))
    border.color: tone === "neutral" || tone === "unavailable" ? App.Theme.border : toneColor
    Accessible.role: Accessible.StaticText
    Accessible.name: text

    Behavior on color { ColorAnimation { duration: App.Theme.couchFadeFast } }

    Row {
        id: content
        anchors.centerIn: parent
        spacing: Math.round(9 * pill.couchScale)

        Rectangle {
            objectName: "couchStatePillDot"
            visible: !pill.hasIcon
            anchors.verticalCenter: parent.verticalCenter
            width: Math.round(12 * pill.couchScale)
            height: width
            radius: width / 2
            color: pill.hollow ? "transparent" : pill.toneColor
            border.width: pill.hollow ? Math.max(2, Math.round(2 * pill.couchScale)) : 0
            border.color: pill.toneColor
        }
        CouchIcon {
            visible: pill.hasIcon
            anchors.verticalCenter: parent.verticalCenter
            source: pill.iconSource
            lightSource: pill.iconLightSource
            size: App.Theme.couchIconSmall
            couchScale: pill.couchScale
            muted: pill.tone === "unavailable"
        }
        Label {
            objectName: "couchStatePillLabel"
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(implicitWidth, pill.maximumTextWidth)
            text: pill.text
            color: pill.labelColor
            font.pixelSize: Math.round(App.Theme.couchLabelSize * pill.couchScale)
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
    }
}
