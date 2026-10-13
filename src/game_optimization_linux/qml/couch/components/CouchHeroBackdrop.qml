import QtQuick
import QtQuick.Window
import "../.." as App

// Cinematic hero background for the selected game.
//
// * `sources` is an ordered list of real artwork URLs for the selected game;
//   the first one that loads is shown, failures fall through to the next.
// * Artwork fills the upper part of the screen and fades into the page at the
//   left (text side) and bottom (carousel side), like the approved reference.
// * Decoded at most 1920 px wide; two layers crossfade; a short debounce skips
//   games the user only scrolls past. No blur or shader effects.
// * With no loadable artwork a neutral theme gradient is shown.
Item {
    id: backdrop
    objectName: "couchHeroBackdrop"

    property var sources: []
    property real artHeightRatio: 0.78
    property int debounceInterval: 90
    property int frontLayer: 0
    property var pendingSources: []
    property int pendingIndex: 0
    readonly property int decodeWidth: Math.min(
        1920, Math.max(640, Math.ceil(width * Screen.devicePixelRatio)))
    readonly property int frontStatus: frontLayer === 0 ? layerA.status : layerB.status
    readonly property url frontSource: frontLayer === 0 ? layerA.source : layerB.source
    readonly property bool showingArtwork: frontStatus === Image.Ready
    readonly property color base: App.Theme.background

    clip: true

    function layerItem(index) { return index === 0 ? layerA : layerB }
    // Ordered real artwork candidates for a game. Steam keeps the wide
    // library hero next to the header in its library cache; if it does not
    // exist the Image errors and the backdrop falls back to the header.
    function sourcesFor(game) {
        if (!game)
            return []
        var header = String(game.headerArtwork || "")
        var list = []
        var nested = header.match(/^(.*\/librarycache\/\d+\/)header\.(jpg|png)$/)
        var flat = header.match(/^(.*\/librarycache\/)(\d+)_header\.(jpg|png)$/)
        if (nested)
            list.push(nested[1] + "library_hero.jpg")
        else if (flat)
            list.push(flat[1] + flat[2] + "_library_hero.jpg")
        list.push(header)
        list.push(String(game.fallbackArtwork || ""))
        list.push(String(game.effectiveArtworkUrl || ""))
        return list
    }
    function cleanSources(list) {
        var result = []
        var values = list ? Array.from(list) : []
        for (var i = 0; i < values.length; ++i) {
            var value = String(values[i] || "")
            if (value.length && result.indexOf(value) < 0)
                result.push(value)
        }
        return result
    }
    function currentCandidate() {
        return pendingIndex < pendingSources.length ? pendingSources[pendingIndex] : ""
    }
    function loadPending() {
        var candidate = currentCandidate()
        var front = layerItem(frontLayer)
        if (candidate.length && String(front.source) === candidate && front.status === Image.Ready)
            return
        var backIndex = 1 - frontLayer
        var back = layerItem(backIndex)
        back.source = candidate
        if (!candidate.length)
            frontLayer = backIndex
        else
            layerSettled(backIndex)
    }
    function layerSettled(index) {
        var item = layerItem(index)
        if (index === frontLayer || String(item.source) !== currentCandidate())
            return
        if (item.status === Image.Ready) {
            frontLayer = index
        } else if (item.status === Image.Error) {
            pendingIndex += 1
            loadPending()
        }
    }

    onSourcesChanged: {
        pendingSources = cleanSources(sources)
        pendingIndex = 0
        debounce.restart()
    }
    Component.onCompleted: {
        pendingSources = cleanSources(sources)
        loadPending()
    }

    Timer {
        id: debounce
        interval: backdrop.debounceInterval
        onTriggered: backdrop.loadPending()
    }

    Rectangle {
        objectName: "couchHeroFallback"
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: App.Theme.background }
            GradientStop { position: 0.65; color: App.Theme.backgroundElevated }
            GradientStop {
                position: 1.0
                color: Qt.rgba(App.Theme.accent.r, App.Theme.accent.g, App.Theme.accent.b,
                               App.Theme.dark ? 0.14 : 0.10)
            }
        }
    }

    Item {
        id: artArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * backdrop.artHeightRatio

        Image {
            id: layerA
            objectName: "couchHeroArtworkA"
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop
            horizontalAlignment: Image.AlignRight
            verticalAlignment: Image.AlignTop
            asynchronous: true
            cache: true
            sourceSize.width: backdrop.decodeWidth
            opacity: backdrop.frontLayer === 0 && status === Image.Ready ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: App.Theme.couchFadeDuration(220) } }
            onStatusChanged: backdrop.layerSettled(0)
        }
        Image {
            id: layerB
            objectName: "couchHeroArtworkB"
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop
            horizontalAlignment: Image.AlignRight
            verticalAlignment: Image.AlignTop
            asynchronous: true
            cache: true
            sourceSize.width: backdrop.decodeWidth
            opacity: backdrop.frontLayer === 1 && status === Image.Ready ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: App.Theme.couchFadeDuration(220) } }
            onStatusChanged: backdrop.layerSettled(1)
        }
        // Light constant dim against very bright artwork.
        Rectangle {
            objectName: "couchHeroDim"
            anchors.fill: parent
            color: backdrop.base
            opacity: App.Theme.dark ? 0.12 : 0.18
        }
        // Text side: dark on the left, art clearly visible on the right.
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Qt.rgba(backdrop.base.r, backdrop.base.g, backdrop.base.b, 0.94) }
                GradientStop { position: 0.30; color: Qt.rgba(backdrop.base.r, backdrop.base.g, backdrop.base.b, 0.78) }
                GradientStop { position: 0.55; color: Qt.rgba(backdrop.base.r, backdrop.base.g, backdrop.base.b, 0.28) }
                GradientStop { position: 1.0; color: Qt.rgba(backdrop.base.r, backdrop.base.g, backdrop.base.b, 0.06) }
            }
        }
        // Fade into the page towards the carousel.
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: parent.height * 0.5
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(backdrop.base.r, backdrop.base.g, backdrop.base.b, 0.0) }
                GradientStop { position: 1.0; color: backdrop.base }
            }
        }
    }
    // Below the art area the page colour continues under the carousel.
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: artArea.bottom
        anchors.bottom: parent.bottom
        color: backdrop.base
    }
}
