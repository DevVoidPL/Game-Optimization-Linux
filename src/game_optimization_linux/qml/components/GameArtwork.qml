pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import ".." as App

Item {
    id: artworkRoot

    property string gameId: ""
    property string title: qsTr("Game")
    property string launcher: qsTr("Library")
    property url artworkSource: ""
    property int artworkFillMode: Image.PreserveAspectFit
    property real cornerRadius: App.Theme.radiusMedium
    property color accentA: paletteFor(title, 0)
    property color accentB: paletteFor(title, 1)
    property bool compact: false
    property string diagnosticViewKind: "unknown"
    property bool diagnosticGridViewIsCurrentItem: false
    property string diagnosticDelegateState: "active"
    property string lastDiagnosticSignature: ""
    property bool shuttingDown: false
    readonly property bool diagnosticLoggingEnabled:
        typeof gameOptimizationDebugArtwork !== "undefined"
        && gameOptimizationDebugArtwork === true

    property int requestId: 0
    property int scheduledRevision: 0
    property int pendingRequestId: 0
    property int committedRequestId: 0
    readonly property int artworkGeneration: requestId
    property string committedGameId: ""
    property url committedArtworkSource: ""
    property string pendingGameId: ""
    property url pendingArtworkSource: ""
    property bool pendingAccepted: false
    readonly property bool currentMatches: committedGameId === gameId
                                                && String(committedArtworkSource).length > 0
    readonly property bool pendingMatches: pendingRequestId > 0
                                                && pendingRequestId === requestId
                                                && pendingGameId === gameId
                                                && String(pendingArtworkSource)
                                                   === String(artworkSource)
    readonly property bool currentReady: currentMatches
                                             && currentImage.requestId
                                                === committedRequestId
                                             && currentImage.requestGameId
                                                === committedGameId
                                             && String(currentImage.source)
                                                === String(committedArtworkSource)
                                             && currentImage.status === Image.Ready
    readonly property bool pendingReady: pendingMatches
                                             && pendingImage.requestId
                                                === pendingRequestId
                                             && pendingImage.requestGameId
                                                === pendingGameId
                                             && String(pendingImage.source)
                                                === String(pendingArtworkSource)
                                             && pendingImage.status === Image.Ready
    readonly property bool artworkReady: currentReady || pendingReady
    readonly property bool artworkLoading: pendingMatches
                                               && pendingImage.status === Image.Loading
    readonly property bool artworkFailed: !currentReady
                                              && pendingMatches
                                              && pendingImage.status === Image.Error
    readonly property int artworkStatus: pendingReady ? Image.Ready
                                          : currentReady ? Image.Ready
                                          : pendingMatches ? pendingImage.status
                                          : Image.Null
    readonly property string displayedGameId: pendingReady ? pendingGameId
                                               : currentReady ? committedGameId : ""
    readonly property string displayedArtworkSource: pendingReady
            ? String(pendingArtworkSource)
            : currentReady ? String(committedArtworkSource) : ""
    readonly property url actualImageSource: pendingReady ? pendingImage.source
                                             : currentReady ? currentImage.source : ""
    readonly property string actualImageStatus: pendingReady
            ? statusName(pendingImage.status)
            : currentReady ? statusName(currentImage.status) : "Null"
    readonly property string placeholderReason: artworkFailed ? "image_error"
            : String(artworkSource).length === 0 && !currentReady ? "missing_source"
            : artworkLoading ? "loading"
            : currentReady ? "none"
            : "not_ready"
    readonly property bool showPlaceholder: !artworkReady
                                                && !artworkLoading
                                                && (artworkFailed
                                                    || String(artworkSource).length === 0)

    implicitWidth: compact ? 72 : 240
    implicitHeight: compact ? 72 : 150
    clip: true

    function statusName(status) {
        if (status === Image.Ready)
            return "Ready"
        if (status === Image.Loading)
            return "Loading"
        if (status === Image.Error)
            return "Error"
        return "Null"
    }

    function diagnosticImage(imageItem) {
        if (imageItem)
            return imageItem
        if (pendingMatches && String(pendingImage.source).length > 0)
            return pendingImage
        if (String(currentImage.source).length > 0)
            return currentImage
        return null
    }

    function logArtworkChange(reason, imageItem) {
        if (!diagnosticLoggingEnabled || shuttingDown)
            return
        var selectedImage = diagnosticImage(imageItem)
        var sourceValue = selectedImage ? String(selectedImage.source) : ""
        var statusValue = selectedImage
                          ? statusName(selectedImage.status) : "Null"
        var signature = [
            String(gameId),
            String(artworkSource),
            sourceValue,
            statusValue,
            visible ? "true" : "false",
            diagnosticGridViewIsCurrentItem ? "true" : "false",
            diagnosticDelegateState,
            diagnosticViewKind
        ].join("|")
        if (signature === lastDiagnosticSignature)
            return
        lastDiagnosticSignature = signature
        console.info("Game Optimization GameArtwork lifecycle"
                     + " event=" + reason
                     + " gameId=" + String(gameId)
                     + " effectiveArtworkUrl=" + String(artworkSource)
                     + " Image.source=" + sourceValue
                     + " Image.status=" + statusValue
                     + " visible=" + (visible ? "true" : "false")
                     + " GridView.isCurrentItem="
                     + (diagnosticGridViewIsCurrentItem ? "true" : "false")
                     + " delegateState=" + diagnosticDelegateState
                     + " view=" + diagnosticViewKind
                     + " requestId=" + requestId
                     + " pendingRequestId=" + pendingRequestId
                     + " committedRequestId=" + committedRequestId)
    }

    function markGridViewPooled(isCurrentItem) {
        diagnosticGridViewIsCurrentItem = Boolean(isCurrentItem)
        diagnosticDelegateState = "onPooled"
        logArtworkChange("onPooled", null)
    }

    function markGridViewReused(isCurrentItem) {
        diagnosticGridViewIsCurrentItem = Boolean(isCurrentItem)
        diagnosticDelegateState = "onReused"
        logArtworkChange("onReused", null)
    }

    function scheduleArtworkReload() {
        scheduledRevision += 1
        var revision = scheduledRevision
        Qt.callLater(function() {
            if (!artworkRoot.shuttingDown
                    && revision === scheduledRevision)
                artworkRoot.reloadArtwork()
        })
    }

    function requestMatches(requestToken, requestedGameId, requestedSource) {
        return requestToken > 0
                && requestToken === requestId
                && requestToken === pendingRequestId
                && String(requestedGameId) === String(gameId)
                && String(requestedGameId) === String(pendingGameId)
                && String(requestedSource) === String(artworkSource)
                && String(requestedSource) === String(pendingArtworkSource)
    }

    function reloadArtwork() {
        var nextGameId = String(gameId || "")
        var nextSource = String(artworkSource || "")
        if (!nextGameId || !nextSource) {
            pendingAccepted = false
            if (nextGameId && currentReady)
                missingSourceTimer.restart()
            else if (!currentReady)
                logArtworkChange(!nextGameId ? "missing_game_id" : "missing_source", null)
            return
        }
        missingSourceTimer.stop()
        if (currentReady && String(committedArtworkSource) === nextSource) {
            return
        }
        if (pendingMatches
                && (pendingImage.status === Image.Loading
                    || pendingImage.status === Image.Ready))
            return
        requestId += 1
        pendingRequestId = requestId
        pendingGameId = nextGameId
        pendingArtworkSource = nextSource
        pendingAccepted = false
        pendingImage.requestId = pendingRequestId
        pendingImage.requestGameId = pendingGameId
        pendingImage.source = pendingArtworkSource
        logArtworkChange("request_started", pendingImage)
        // Cached local images may already be Ready without another status
        // transition.  The explicit request tuple still guards this path.
        if (pendingImage.status === Image.Ready)
            acceptPendingImage(pendingRequestId, pendingGameId,
                               String(pendingArtworkSource))
    }

    function acceptPendingImage(requestToken, requestedGameId, requestedSource) {
        if (!requestMatches(requestToken, requestedGameId, requestedSource)
                || pendingImage.requestId !== requestToken
                || pendingImage.requestGameId !== requestedGameId
                || String(pendingImage.source) !== String(requestedSource)
                || pendingImage.status !== Image.Ready) {
            logArtworkChange("stale_candidate_ready_ignored", pendingImage)
            return
        }
        pendingAccepted = true
        committedRequestId = requestToken
        committedGameId = requestedGameId
        committedArtworkSource = requestedSource
        currentImage.requestId = requestToken
        currentImage.requestGameId = requestedGameId
        currentImage.source = requestedSource
        logArtworkChange("candidate_ready", pendingImage)
        // If the same cached URL was already loaded by currentImage, no status
        // transition is guaranteed after the metadata changes.
        finishCommit(requestToken, requestedGameId, requestedSource)
    }

    function rejectPendingImage(requestToken, requestedGameId, requestedSource) {
        if (!requestMatches(requestToken, requestedGameId, requestedSource)
                || pendingImage.requestId !== requestToken
                || String(pendingImage.source) !== String(requestedSource)
                || pendingImage.status !== Image.Error)
            return
        // A failed replacement must never remove a valid current image for
        // the same game.  Without a current image, showPlaceholder derives the
        // error state from this still-current pending request.
        console.warn("Game Optimization artwork image error"
                     + " gameId=" + String(gameId)
                     + " source=" + String(requestedSource))
        logArtworkChange("image_error", pendingImage)
    }

    function finishCommit(requestToken, requestedGameId, requestedSource) {
        if (!pendingAccepted
                || !requestMatches(requestToken, requestedGameId,
                                   requestedSource)
                || committedRequestId !== requestToken
                || committedGameId !== requestedGameId
                || String(committedArtworkSource) !== String(requestedSource)
                || currentImage.requestId !== requestToken
                || currentImage.requestGameId !== requestedGameId
                || String(currentImage.source) !== String(requestedSource)
                || currentImage.status !== Image.Ready)
            return
        logArtworkChange("committed", currentImage)
        pendingAccepted = false
        pendingImage.requestId = 0
        pendingImage.requestGameId = ""
        pendingImage.source = ""
        pendingRequestId = 0
        pendingGameId = ""
        pendingArtworkSource = ""
    }

    function initialFor(value) {
        var normalized = String(value || "").trim()
        return (normalized.length > 0 ? normalized.charAt(0) : "G").toUpperCase()
    }

    function paletteFor(value, index) {
        var palettes = [
            ["#163A4F", "#4C8FA6"],
            ["#38255B", "#8B6AD2"],
            ["#4A261C", "#C86A43"],
            ["#173D32", "#3EAD85"],
            ["#3B233E", "#B45CAC"],
            ["#24304C", "#617FD2"]
        ]
        var hash = 0
        var text = String(value || "Game")
        for (var i = 0; i < text.length; ++i)
            hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0
        var row = palettes[Math.abs(hash) % palettes.length]
        return row[index]
    }

    onGameIdChanged: scheduleArtworkReload()
    onArtworkSourceChanged: scheduleArtworkReload()
    onVisibleChanged: logArtworkChange("visible_changed", null)
    Component.onCompleted: {
        // Bind the candidate before the first render. Delaying this with
        // Qt.callLater exposed a one-frame placeholder every time a Repeater
        // recreated its delegates during a model refresh.
        reloadArtwork()
        logArtworkChange("component_completed", null)
    }
    Component.onDestruction: {
        shuttingDown = true
        scheduledRevision += 1
        missingSourceTimer.stop()
        diagnosticDelegateState = "destroyed"
    }

    Timer {
        id: missingSourceTimer
        interval: 150
        repeat: false
        onTriggered: {
            if (String(artworkRoot.artworkSource).length === 0
                    && artworkRoot.committedGameId === artworkRoot.gameId) {
                artworkRoot.committedRequestId = 0
                artworkRoot.committedGameId = ""
                artworkRoot.committedArtworkSource = ""
                currentImage.requestId = 0
                currentImage.requestGameId = ""
                currentImage.source = ""
                artworkRoot.logArtworkChange("missing_source", null)
            }
        }
    }

    Item {
        id: artworkLayer
        anchors.fill: parent
        visible: true

        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: artworkRoot.accentA }
                GradientStop { position: 1.0; color: artworkRoot.accentB }
            }
        }

        Image {
            id: currentImage
            objectName: "gameArtworkImage"
            property int requestId: 0
            property string requestGameId: ""
            anchors.fill: parent
            sourceSize.width: Math.min(
                                  1200,
                                  Math.max(
                                      64,
                                      Math.ceil(artworkRoot.width
                                                * Screen.devicePixelRatio)))
            fillMode: artworkRoot.artworkFillMode
            horizontalAlignment: Image.AlignHCenter
            verticalAlignment: Image.AlignVCenter
            asynchronous: true
            cache: true
            smooth: true
            mipmap: true
            visible: artworkRoot.currentReady && !artworkRoot.pendingReady
            onSourceChanged: artworkRoot.logArtworkChange(
                                 "current_source_changed", currentImage)
            onStatusChanged: {
                artworkRoot.finishCommit(
                            currentImage.requestId,
                            currentImage.requestGameId,
                            String(currentImage.source))
                artworkRoot.logArtworkChange(
                            "current_status_changed", currentImage)
            }
        }

        Image {
            id: pendingImage
            objectName: "gameArtworkCandidateImage"
            property int requestId: 0
            property string requestGameId: ""
            anchors.fill: parent
            sourceSize.width: currentImage.sourceSize.width
            fillMode: artworkRoot.artworkFillMode
            horizontalAlignment: Image.AlignHCenter
            verticalAlignment: Image.AlignVCenter
            asynchronous: true
            cache: true
            smooth: true
            mipmap: true
            visible: artworkRoot.pendingReady
            onSourceChanged: artworkRoot.logArtworkChange(
                                 "candidate_source_changed", pendingImage)
            onStatusChanged: {
                if (status === Image.Ready)
                    artworkRoot.acceptPendingImage(
                                pendingImage.requestId,
                                pendingImage.requestGameId,
                                String(pendingImage.source))
                else if (status === Image.Error)
                    artworkRoot.rejectPendingImage(
                                pendingImage.requestId,
                                pendingImage.requestGameId,
                                String(pendingImage.source))
                artworkRoot.logArtworkChange(
                            "candidate_status_changed", pendingImage)
            }
        }

        Rectangle {
            visible: artworkRoot.showPlaceholder
            width: artworkRoot.width * 0.7
            height: width
            radius: width / 2
            x: artworkRoot.width * 0.55
            y: -height * 0.32
            color: "#22FFFFFF"
            rotation: 12
        }

        Rectangle {
            visible: artworkRoot.showPlaceholder
            width: artworkRoot.width * 0.48
            height: width
            radius: width / 2
            x: -width * 0.3
            y: artworkRoot.height * 0.58
            color: "#16000000"
        }

        Label {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: artworkRoot.compact ? -3 : -8
            text: artworkRoot.initialFor(artworkRoot.title)
            color: "#EFFFFFFF"
            font.pixelSize: artworkRoot.compact
                            ? 30 : Math.min(64, artworkRoot.height * 0.44)
            font.weight: Font.Black
            visible: artworkRoot.showPlaceholder
        }

        Label {
            visible: !artworkRoot.compact && artworkRoot.showPlaceholder
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 12
            text: artworkRoot.launcher.toUpperCase()
            color: "#CCFFFFFF"
            font.pixelSize: 9
            font.weight: Font.Bold
            font.letterSpacing: 1.4
            elide: Text.ElideRight
        }

        BusyIndicator {
            anchors.centerIn: parent
            width: artworkRoot.compact ? 24 : 32
            height: width
            running: artworkRoot.artworkLoading
            visible: running
        }

        Rectangle {
            visible: artworkRoot.artworkFailed
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 8
            width: 22
            height: 22
            radius: 11
            color: "#99000000"

            Label {
                anchors.centerIn: parent
                text: "!"
                color: "white"
                font.weight: Font.Bold
            }
        }
    }

}
