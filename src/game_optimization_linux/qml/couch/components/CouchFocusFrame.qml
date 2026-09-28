import QtQuick
import QtQuick.Effects
import "../.." as App

// Focus cue for Couch surfaces: exactly one ring plus an optional soft glow.
// Place it inside the element's background so it follows the radius and the
// owner's focus scale. Focus never relies on colour alone: the owner also
// scales up, and the ring is 4 px (x couchScale) thick.
//
// The glow is Qt's analytic RectangularShadow (a single cheap SDF pass, no
// blur of the scene); set `glow: false` where even that is not wanted.
Item {
    id: frame

    property bool active: false
    property real radius: App.Theme.couchCardRadius
    property real couchScale: 1.0
    property bool glow: true
    property color color: App.Theme.couchFocusRing
    property real ringWidth: App.Theme.couchFocusWidth * couchScale

    // Drawn beneath the owner and just outside its edge, so the owner's own
    // fill hides the inner part of the glow and the ring never covers content.
    anchors.fill: parent
    anchors.margins: -ringWidth
    z: -1
    opacity: active ? 1 : 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: App.Theme.couchFadeFast } }

    RectangularShadow {
        objectName: "couchFocusGlow"
        visible: frame.glow
        anchors.fill: parent
        radius: frame.radius + frame.ringWidth
        blur: 22 * frame.couchScale
        spread: 0
        color: Qt.rgba(frame.color.r, frame.color.g, frame.color.b, App.Theme.dark ? 0.42 : 0.30)
    }
    Rectangle {
        objectName: "couchFocusRing"
        anchors.fill: parent
        radius: frame.radius + frame.ringWidth
        color: "transparent"
        border.width: frame.ringWidth
        border.color: frame.color
    }
}
