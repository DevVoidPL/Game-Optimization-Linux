import QtQuick
import "../../components"
import "../.." as App

// Couch icon contract
//
// * `source` is the artwork used in the dark theme; `lightSource` is the
//   optional artwork prepared for light surfaces. Two-tone artwork is drawn
//   untouched (no effect layer) whenever a variant exists for the active theme.
// * The shared UiIcon/MultiEffect tint is used only when the artwork cannot be
//   shown faithfully: a muted (disabled/unavailable) icon, an explicit
//   monochrome request, or the light theme without a light variant. In those
//   cases the icon becomes one readable theme colour instead of risking
//   near-white strokes on a light surface.
// * No currentColor: Qt's SVG renderer cannot resolve it.
Item {
    id: root

    property url source: ""
    property url lightSource: ""
    property real size: App.Theme.couchIconSmall
    property real couchScale: 1.0
    property bool muted: false
    property bool monochrome: false
    property color monochromeColor: App.Theme.couchIconMonochrome

    readonly property bool hasSource: String(source).length > 0
    readonly property bool hasLightVariant: String(lightSource).length > 0
    readonly property url effectiveSource: !App.Theme.dark && hasLightVariant
                                           ? lightSource : source
    // "artwork", "monochrome" or "muted".
    readonly property string renderMode: muted ? "muted"
                                         : monochrome ? "monochrome"
                                         : (!App.Theme.dark && !hasLightVariant) ? "monochrome"
                                         : "artwork"
    readonly property color renderColor: renderMode === "muted"
                                         ? App.Theme.couchIconMuted : monochromeColor
    readonly property int imageStatus: image.status
    readonly property real pixelSize: Math.round(size * couchScale)

    implicitWidth: pixelSize
    implicitHeight: pixelSize
    visible: hasSource
    // Muted (disabled/unavailable) icons keep their artwork and are dimmed;
    // this needs no shader, so it renders the same on every scene backend.
    opacity: renderMode === "muted" ? 0.38 : 1.0

    UiIcon {
        id: image
        objectName: "couchIconImage"
        anchors.centerIn: parent
        width: root.pixelSize
        height: root.pixelSize
        sourceSize.width: root.pixelSize
        sourceSize.height: root.pixelSize
        source: root.effectiveSource
        tintEnabled: root.renderMode === "monochrome"
        tintColor: root.renderColor
    }
}
