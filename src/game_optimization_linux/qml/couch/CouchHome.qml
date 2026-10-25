pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchHome"
    property var controller
    property var navigation
    property real couchScale: 1.0
    // Vertical fit for short screens (720p TVs, 16:10 handhelds). Equal to
    // couchScale on 16:9 screens at or above the 0.82 scale floor.
    readonly property real fitScale: height > 0 ? Math.min(couchScale, height / 1080) : couchScale
    // Layout metrics (1920x1080 reference, scaled by fitScale).
    readonly property real edgeMargin: 48 * fitScale
    readonly property real arrowSize: 48 * fitScale
    readonly property real arrowGap: 12 * fitScale
    readonly property real cardGap: 24 * fitScale
    readonly property real cardPad: 22 * fitScale
    readonly property int visibleCards: 6
    readonly property real cardWidthByWidth: Math.max(120, (width - 2 * edgeMargin - 2 * (arrowSize + arrowGap)
                                                            - 2 * cardPad - (visibleCards - 1) * cardGap) / visibleCards)
    readonly property real cardHeightByHeight: carouselArea.height > 0
            ? (carouselArea.height - 2 * cardPad) / 1.05 : cardWidthByWidth * 1.46
    readonly property real cardHeight: Math.max(160, Math.min(cardWidthByWidth * 1.46, cardHeightByHeight))
    readonly property real cardWidth: cardHeight / 1.46
    // Width available for the strip between the arrows, and the number of
    // whole covers that fit into it; the strip is sized to exactly that many
    // so no cover (and no focus ring) is ever cut at an edge.
    readonly property real stripMaxWidth: width - 2 * edgeMargin - 2 * (arrowSize + arrowGap)
    readonly property int fittedCards: Math.max(1, Math.floor((stripMaxWidth - 2 * cardPad + cardGap)
                                                              / (cardWidth + cardGap)))
    readonly property real stripWidth: fittedCards * cardWidth + (fittedCards - 1) * cardGap + 2 * cardPad
    // Hero text aligns with the first cover's left edge.
    readonly property real contentLeft: edgeMargin + arrowSize + arrowGap + cardPad

    property var games: controller && controller.games ? controller.games : []
    property int focusZone: 0
    property int selectedTile: 0
    property int selectedHeroAction: 0
    property bool launchPending: false
    property bool contextMenuOpen: false
    property int contextMenuIndex: 0
    property string retainedGameId: ""
    property var updatesSummary: controller && controller.updatesSummary
                                 ? controller.updatesSummary : ({})
    readonly property var homeTiles: [
        { "id": "library", "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight, "title": qsTr("Library"), "subtitle": qsTr("Your games") },
        { "id": "tasks", "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight, "title": qsTr("Tasks"), "subtitle": qsTr("Active and recent tasks") },
        { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "title": qsTr("Updates"), "subtitle": qsTr("File changes and re-analysis") },
        { "id": "settings", "icon": App.UiIcons.couchGlyphSettings, "iconOnLight": App.UiIcons.couchGlyphSettingsOnLight, "title": qsTr("Settings"), "subtitle": qsTr("App configuration") }
    ]
    readonly property var displayGames: games || []
    property int selectedGameIndex: displayGames.length > 0
            ? Math.min(gameStrip.currentIndex, displayGames.length - 1) : -1
    property var selectedGame: selectedGameIndex >= 0
            ? displayGames[selectedGameIndex] : ({})
    readonly property var heroActions: [
        { "id": "launch", "icon": App.UiIcons.couchGlyphLaunch, "iconOnLight": App.UiIcons.couchGlyphLaunchOnLight, "title": launchPending ? qsTr("Launching…") : qsTr("Launch"), "enabled": selectedGame.launchAllowed === true && !launchPending },
        { "id": "details", "icon": App.UiIcons.couchGlyphDetails, "iconOnLight": App.UiIcons.couchGlyphDetailsOnLight, "title": qsTr("Details"), "enabled": selectedGameIndex >= 0 },
        { "id": "more", "icon": App.UiIcons.couchGlyphMore, "iconOnLight": App.UiIcons.couchGlyphMoreOnLight, "title": qsTr("More"), "enabled": selectedGameIndex >= 0 }
    ]
    readonly property var contextEntries: [
        { "id": "launch", "icon": App.UiIcons.couchGlyphLaunch, "iconOnLight": App.UiIcons.couchGlyphLaunchOnLight, "title": qsTr("Launch"), "enabled": selectedGame.launchAllowed === true && !launchPending },
        { "id": "details", "icon": App.UiIcons.couchGlyphDetails, "iconOnLight": App.UiIcons.couchGlyphDetailsOnLight, "title": qsTr("Game details"), "enabled": selectedGameIndex >= 0 },
        { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "title": qsTr("Updates"), "enabled": selectedGameIndex >= 0 },
        { "id": "close", "icon": App.UiIcons.couchGlyphPrevious, "iconOnLight": App.UiIcons.couchGlyphPreviousOnLight, "title": qsTr("Close menu"), "enabled": true }
    ]
    // Space CouchMain must keep free at the bottom for controller hints so
    // they sit in the footer row above the navigation, never on top of it.
    readonly property real hintsBottomMargin: (bottomNavMargin + bottomNav.height + App.Theme.couchSpaceS * fitScale)
    readonly property real bottomNavMargin: 40 * fitScale
    property int runtimeProbeSerial: 0
    readonly property var runtimeProbeMetrics: {
        runtimeProbeSerial
        return buildRuntimeProbeMetrics()
    }
    signal openGame(string gameId)
    signal openLibrary()
    signal openSettings()
    signal openNarrator()

    function buildRuntimeProbeMetrics() {
        var minimumActionHeight = 0
        for (var actionIndex = 0; actionIndex < heroActionRepeater.count; ++actionIndex) {
            var action = heroActionRepeater.itemAt(actionIndex)
            if (action && action.visible)
                minimumActionHeight = minimumActionHeight > 0
                        ? Math.min(minimumActionHeight, action.height) : action.height
        }
        var cell = gameStrip.currentItem
        var tile = bottomNav.itemAt(0)
        var tilePoint = tile ? tile.mapToItem(page, 0, 0) : null
        return {
            "homeActionMinHeight": minimumActionHeight,
            "homeCardMaxWidth": cell ? Number(cell.probeCardWidth) : 0,
            "homeCardMaxHeight": cell ? Number(cell.probeCardHeight) : 0,
            "firstTileInside": !tile || (tilePoint.x >= -1.5
                    && tilePoint.y >= -1.5
                    && tilePoint.x + tile.width <= page.width + 1.5
                    && tilePoint.y + tile.height <= page.height + 1.5)
        }
    }

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (page.contextMenuOpen) {
                var contextItem = contextRepeater.itemAt(page.contextMenuIndex)
                if (contextItem) contextItem.forceActiveFocus()
            } else if (focusZone === 0)
                gameStrip.forceActiveFocus()
            else if (focusZone === 1) {
                var tile = bottomNav.itemAt(selectedTile)
                if (tile) tile.forceActiveFocus()
            } else {
                var action = heroActionRepeater.itemAt(selectedHeroAction)
                if (action) action.forceActiveFocus()
            }
        })
    }

    function summaryNumber(keys) {
        var source = updatesSummary || {}
        for (var index = 0; index < keys.length; ++index) {
            var raw = Number(source[keys[index]])
            if (isFinite(raw) && raw >= 0)
                return Math.floor(raw)
        }
        return 0
    }

    function taskSummary() {
        var count = controller && controller.activeTasks ? controller.activeTasks.length : 0
        if (count > 0)
            return qsTr("%1 active tasks").arg(count)
        var attention = summaryNumber(["needsCheckCount", "needs_check_count"])
        return attention > 0 ? qsTr("%1 items need attention").arg(attention)
                             : qsTr("Library ready")
    }

    onGamesChanged: Qt.callLater(restoreRetainedSelection)
    onSelectedGameChanged: Qt.callLater(ensureHeroAction)

    // Readiness is derived only from fields produced by presenters.game_to_qml.
    function readinessTone() {
        if (selectedGameIndex < 0)
            return "neutral"
        if (selectedGame.launchAllowed === true)
            return "success"
        var status = String(selectedGame.status || "")
        if (selectedGame.libraryAvailable === false
                || status === "Drive disconnected" || status === "Missing files")
            return "danger"
        return "warning"
    }
    function readinessText() {
        var tone = readinessTone()
        if (tone === "success")
            return qsTr("Ready to launch")
        if (tone === "danger")
            return App.I18n.status(String(selectedGame.status || "")
                                   || String(selectedGame.availabilityStatus || ""))
        return qsTr("Launch unavailable")
    }
    function readinessReason() {
        if (readinessTone() !== "warning")
            return ""
        return App.I18n.message(String(selectedGame.launchUnavailableReason || ""))
    }
    // Shown only when the game really records a runner (manual/Heroic/Lutris).
    function runtimeText() {
        var runner = String(selectedGame.runner || "").trim()
        if (!runner.length)
            return ""
        var parts = runner.split("/")
        return qsTr("Runtime: %1").arg(parts[parts.length - 1] || runner)
    }

    // Used when returning from game details: keep the game selected and put
    // focus back on its cover in the carousel.
    function focusGame(gameId) {
        if (String(gameId || "").length)
            retainedGameId = String(gameId)
        if (contextMenuOpen)
            closeContextMenu()
        restoreRetainedSelection()
        focusZone = 0
        restoreActiveFocus()
    }

    function restoreRetainedSelection() {
        if (displayGames.length === 0) {
            gameStrip.currentIndex = -1
            return
        }
        for (var index = 0; index < displayGames.length; ++index) {
            if (String(displayGames[index].id || "") === retainedGameId) {
                gameStrip.currentIndex = index
                return
            }
        }
        selectGameIndex(Math.max(0, Math.min(gameStrip.currentIndex,
                                             displayGames.length - 1)))
    }

    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }

    function selectGameIndex(index) {
        if (index < 0 || index >= displayGames.length)
            return false
        var changed = gameStrip.currentIndex !== index
        // The ListView highlight range keeps the selection inside the padded
        // six-cover window; positionViewAtIndex(Contain) ignored that padding.
        gameStrip.currentIndex = index
        retainedGameId = String(displayGames[index].id || "")
        if (navigation)
            navigation.rememberFocus("home", retainedGameId, index)
        return changed
    }

    function activateTile() {
        if (selectedTile === 0) {
            openLibrary()
            return true
        }
        if (selectedTile === 1 && controller) {
            controller.navigate("tasks")
            return true
        }
        if (selectedTile === 2 && controller) {
            controller.navigate("updates")
            return true
        }
        if (selectedTile === 3) {
            openSettings()
            return true
        }
        return false
    }

    function activateHeroAction() {
        if (selectedGameIndex < 0 || !heroActions[selectedHeroAction]
                || !heroActions[selectedHeroAction].enabled)
            return false
        if (selectedHeroAction === 0 && controller) {
            launchPending = true
            var launched = controller.launchGame(String(selectedGame.id || ""))
            if (!launched) {
                launchPending = false
                return false
            }
            launchGuard.restart()
            return true
        }
        if (selectedHeroAction === 1) {
            openGame(String(selectedGame.id || ""))
            return true
        }
        return openContextMenu()
    }

    function openContextMenu() {
        if (selectedGameIndex < 0)
            return false
        contextMenuIndex = selectedGame.launchAllowed === true ? 0 : 1
        contextMenuOpen = true
        if (navigation)
            navigation.openModal("game-context", contextEntries[contextMenuIndex].id)
        restoreActiveFocus()
        return true
    }

    function closeContextMenu() {
        if (!contextMenuOpen)
            return false
        contextMenuOpen = false
        if (navigation)
            navigation.closeModal()
        restoreActiveFocus()
        return true
    }

    function moveEnabled(model, current, delta) {
        var candidate = current
        for (var count = 0; count < model.length; ++count) {
            candidate += delta
            if (candidate < 0 || candidate >= model.length)
                return current
            if (model[candidate].enabled !== false)
                return candidate
        }
        return current
    }

    function ensureHeroAction() {
        if (heroActions[selectedHeroAction] && heroActions[selectedHeroAction].enabled)
            return
        selectedHeroAction = moveEnabled(heroActions, -1, 1)
    }

    function activateContextEntry() {
        var entry = contextEntries[contextMenuIndex]
        if (!entry || !entry.enabled)
            return false
        if (entry.id === "close") {
            return closeContextMenu()
        }
        if (entry.id === "launch") {
            closeContextMenu()
            selectedHeroAction = 0
            return activateHeroAction()
        }
        if (entry.id === "details") {
            closeContextMenu()
            openGame(String(selectedGame.id || ""))
            return true
        }
        if (entry.id === "updates" && controller) {
            closeContextMenu()
            controller.navigate("updates")
            return true
        }
        return false
    }

    function handleAction(action) {
        if (action === "ContextMenu" || action === "Search") action = "MoreActions"
        else if (action === "PageLeft" || action === "PreviousSection") action = "PageUp"
        else if (action === "PageRight" || action === "NextSection") action = "PageDown"
        if (contextMenuOpen) {
            if (action === "Back" || action === "MoreActions") {
                if (closeContextMenu())
                    playSemanticSound("back")
            } else if (action === "NavigateUp" || action === "NavigateDown") {
                var previousContextIndex = contextMenuIndex
                contextMenuIndex = moveEnabled(
                    contextEntries, contextMenuIndex,
                    action === "NavigateUp" ? -1 : 1)
                restoreActiveFocus()
                if (contextMenuIndex !== previousContextIndex)
                    playSemanticSound("navigate")
            } else if (action === "Confirm") {
                var contextEntry = contextEntries[contextMenuIndex]
                if (!contextEntry || !contextEntry.enabled) {
                    playSemanticSound("error")
                } else if (activateContextEntry()) {
                    playSemanticSound(contextEntry.id === "close" ? "back" : "confirm")
                } else {
                    playSemanticSound("error")
                }
            }
            return
        }
        if (action === "Back") {
            return
        } else if (action === "NavigateUp" || action === "NavigateDown") {
            // Visual order, top to bottom: hero actions (2), game carousel (0),
            // navigation tiles (1).
            var previousZone = focusZone
            if (action === "NavigateUp") {
                if (focusZone === 1)
                    focusZone = 0
                else if (focusZone === 0 && selectedGameIndex >= 0)
                    focusZone = 2
            } else if (focusZone === 2) {
                focusZone = 0
            } else if (focusZone === 0) {
                focusZone = 1
            }
            if (focusZone === 2)
                ensureHeroAction()
            if (focusZone !== previousZone)
                playSemanticSound("navigate")
        } else if (action === "NavigateLeft" && focusZone === 0 && gameStrip.count > 0) {
            if (selectGameIndex(Math.max(0, gameStrip.currentIndex - 1)))
                playSemanticSound("navigate")
        } else if (action === "NavigateRight" && focusZone === 0 && gameStrip.count > 0) {
            if (selectGameIndex(Math.min(gameStrip.count - 1, gameStrip.currentIndex + 1)))
                playSemanticSound("navigate")
        } else if ((action === "NavigateLeft" || action === "NavigateRight")
                   && focusZone === 1) {
            var previousTile = selectedTile
            selectedTile = action === "NavigateLeft"
                    ? Math.max(0, selectedTile - 1)
                    : Math.min(homeTiles.length - 1, selectedTile + 1)
            if (selectedTile !== previousTile)
                playSemanticSound("navigate")
        } else if ((action === "NavigateLeft" || action === "NavigateRight")
                   && focusZone === 2) {
            var previousHeroAction = selectedHeroAction
            selectedHeroAction = moveEnabled(
                heroActions, selectedHeroAction,
                action === "NavigateLeft" ? -1 : 1)
            if (selectedHeroAction !== previousHeroAction)
                playSemanticSound("navigate")
        } else if (action === "Confirm" && focusZone === 0) {
            if (selectedGameIndex >= 0) {
                openGame(String(selectedGame.id || ""))
                playSemanticSound("confirm")
            } else {
                playSemanticSound("error")
            }
        } else if (action === "Confirm" && focusZone === 1) {
            playSemanticSound(activateTile() ? "confirm" : "error")
        } else if (action === "Confirm" && focusZone === 2) {
            playSemanticSound(activateHeroAction() ? "confirm" : "error")
        } else if (action === "MoreActions") {
            if (openContextMenu())
                playSemanticSound("open")
            else
                playSemanticSound("error")
        } else if (action === "PageUp" && gameStrip.count > 0) {
            if (selectGameIndex(Math.max(0, gameStrip.currentIndex - 5)))
                playSemanticSound("navigate")
        } else if (action === "PageDown" && gameStrip.count > 0) {
            if (selectGameIndex(Math.min(gameStrip.count - 1,
                                         gameStrip.currentIndex + 5)))
                playSemanticSound("navigate")
        }
    }

    Timer { id: launchGuard; interval: 1800; onTriggered: page.launchPending = false }

    CouchHeroBackdrop {
        id: heroBackdrop
        anchors.fill: parent
        sources: page.selectedGameIndex >= 0 ? heroBackdrop.sourcesFor(page.selectedGame) : []
    }

    // ---- Hero information and actions ------------------------------------
    ColumnLayout {
        id: heroColumn
        objectName: "couchHomeHero"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.leftMargin: page.contentLeft
        anchors.topMargin: 112 * page.fitScale
        width: Math.min(page.width * 0.56, 1080 * page.fitScale)
        spacing: App.Theme.couchSpaceS * page.fitScale

        Row {
            objectName: "couchHomeLauncherChip"
            visible: page.selectedGameIndex >= 0
                     && String(page.selectedGame.launcher || "").length > 0
            spacing: App.Theme.couchSpaceS * page.fitScale
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 38 * page.fitScale
                height: width
                radius: width / 2
                color: Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                               App.Theme.surfaceRaised.b, 0.9)
                border.width: 1
                border.color: App.Theme.border
                CouchIcon {
                    objectName: "couchHomeLauncherIcon"
                    readonly property url logo: App.UiIcons.launcherLogo(page.selectedGame.launcher)
                    anchors.centerIn: parent
                    size: App.Theme.couchIconSmall
                    couchScale: page.fitScale
                    source: String(logo).length ? logo : App.UiIcons.couchGlyphSource
                    lightSource: String(logo).length ? logo : App.UiIcons.couchGlyphSourceOnLight
                }
            }
            Label {
                objectName: "couchHomeLauncherLabel"
                anchors.verticalCenter: parent.verticalCenter
                text: App.I18n.launcherName(page.selectedGame.launcher || "")
                color: App.Theme.text
                font.pixelSize: 20 * page.fitScale
                font.weight: Font.DemiBold
            }
        }
        Label {
            objectName: "couchHomeHeroTitle"
            Layout.fillWidth: true
            text: String(page.selectedGame.name || qsTr("Your games"))
            color: App.Theme.text
            font.pixelSize: 56 * page.fitScale
            font.weight: Font.Bold
            lineHeight: 1.0
            maximumLineCount: 2
            wrapMode: Text.WordWrap
            elide: Text.ElideRight
        }
        Label {
            visible: page.selectedGameIndex < 0
            Layout.fillWidth: true
            text: qsTr("No games were detected")
            color: App.Theme.textSecondary
            font.pixelSize: 20 * page.fitScale
        }
        // Readiness: icon + colour + text, then runtime or the blocking reason.
        Row {
            id: readinessRow
            objectName: "couchHomeReadiness"
            readonly property string tone: page.readinessTone()
            readonly property string text: page.readinessText()
            visible: page.selectedGameIndex >= 0
            spacing: App.Theme.couchSpaceS * page.fitScale
            CouchIcon {
                anchors.verticalCenter: parent.verticalCenter
                size: App.Theme.couchIconSmall
                couchScale: page.fitScale
                source: readinessRow.tone === "success" ? App.UiIcons.couchGlyphStatusReady
                        : readinessRow.tone === "danger" ? App.UiIcons.couchGlyphStatusError
                        : App.UiIcons.couchGlyphStatusWarning
                lightSource: readinessRow.tone === "success" ? App.UiIcons.couchGlyphStatusReadyOnLight
                             : readinessRow.tone === "danger" ? App.UiIcons.couchGlyphStatusErrorOnLight
                             : App.UiIcons.couchGlyphStatusWarningOnLight
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: readinessRow.text
                color: App.Theme.couchToneText(readinessRow.tone)
                font.pixelSize: 20 * page.fitScale
                font.weight: Font.Bold
            }
            Label {
                visible: runtimeLabel.text.length > 0 || reasonLabel.text.length > 0
                anchors.verticalCenter: parent.verticalCenter
                text: "•"
                color: App.Theme.textMuted
                font.pixelSize: 20 * page.fitScale
            }
            Label {
                id: runtimeLabel
                objectName: "couchHomeRuntime"
                visible: text.length > 0
                anchors.verticalCenter: parent.verticalCenter
                text: page.runtimeText()
                color: App.Theme.textSecondary
                font.pixelSize: 19 * page.fitScale
            }
            Label {
                id: reasonLabel
                objectName: "couchHomeReadinessReason"
                visible: text.length > 0
                anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, heroColumn.width * 0.6)
                text: page.readinessReason()
                color: App.Theme.textSecondary
                font.pixelSize: 19 * page.fitScale
                elide: Text.ElideRight
            }
        }

        Row {
            Layout.topMargin: App.Theme.couchSpaceL * page.fitScale
            spacing: App.Theme.couchSpaceM * page.fitScale
            Repeater {
                id: heroActionRepeater
                model: page.heroActions
                delegate: CouchButton {
                    id: heroButton
                    objectName: "couchHomeHeroAction"
                    required property var modelData
                    required property int index
                    readonly property bool primaryAction: modelData.id === "launch"
                    couchScale: page.fitScale
                    iconSource: modelData.icon || ""
                    iconLightSource: modelData.iconOnLight || ""
                    iconSize: App.Theme.couchIconSmall
                    text: modelData.title
                    enabled: modelData.enabled
                    focus: page.focusZone === 2 && page.selectedHeroAction === index
                    implicitWidth: primaryAction ? 232 * page.fitScale : 172 * page.fitScale
                    implicitHeight: 58 * page.fitScale
                    font.pixelSize: (primaryAction ? 21 : 18) * page.fitScale
                    font.weight: primaryAction ? Font.Bold : Font.DemiBold
                    font.capitalization: primaryAction ? Font.AllUppercase : Font.MixedCase
                    font.letterSpacing: primaryAction ? 1.2 * page.fitScale : 0
                    onClicked: { page.selectedHeroAction = index; page.activateHeroAction() }
                    background: Rectangle {
                        radius: 14 * page.fitScale
                        color: heroButton.down ? App.Theme.surfacePressed
                              : heroButton.primaryAction && heroButton.enabled ? App.Theme.accentSoft
                              : heroButton.focusVisible ? App.Theme.couchFocusSurface
                              : Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                                        App.Theme.surfaceRaised.b, 0.88)
                        border.width: heroButton.primaryAction && heroButton.enabled ? 2 * page.fitScale : 1
                        border.color: heroButton.primaryAction && heroButton.enabled
                                      ? App.Theme.accent : App.Theme.borderStrong
                        scale: heroButton.down ? App.Theme.couchPressScale
                                               : heroButton.focusVisible ? 1.04 : 1.0
                        Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
                        Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                        CouchFocusFrame {
                            active: heroButton.focusVisible
                            radius: parent.radius
                            couchScale: page.fitScale
                        }
                    }
                }
            }
        }
    }

    // ---- Carousel ------------------------------------------------------------
    Item {
        id: carouselArea
        objectName: "couchHomeCarousel"
        visible: gameStrip.count > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: heroColumn.bottom
        anchors.bottom: footerRow.top
        anchors.topMargin: App.Theme.couchSpaceL * page.fitScale
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin

        CouchButton {
            objectName: "couchHomePrevious"
            visible: gameStrip.currentIndex > 0
            anchors.left: parent.left
            anchors.verticalCenter: gameStrip.verticalCenter
            couchScale: page.fitScale
            implicitWidth: page.arrowSize
            implicitHeight: page.arrowSize
            focusPolicy: Qt.NoFocus
            iconSource: App.UiIcons.couchGlyphPrevious
            iconLightSource: App.UiIcons.couchGlyphPreviousOnLight
            iconSize: App.Theme.couchIconSmall
            Accessible.name: qsTr("Previous game")
            onClicked: page.selectGameIndex(gameStrip.currentIndex - 1)
            background: Rectangle {
                radius: height / 2
                color: Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g, App.Theme.surfaceRaised.b, 0.86)
                border.width: 1
                border.color: App.Theme.border
            }
        }
        CouchButton {
            objectName: "couchHomeNext"
            visible: gameStrip.currentIndex < gameStrip.count - 1
            anchors.right: parent.right
            anchors.verticalCenter: gameStrip.verticalCenter
            couchScale: page.fitScale
            implicitWidth: page.arrowSize
            implicitHeight: page.arrowSize
            focusPolicy: Qt.NoFocus
            iconSource: App.UiIcons.couchGlyphNext
            iconLightSource: App.UiIcons.couchGlyphNextOnLight
            iconSize: App.Theme.couchIconSmall
            Accessible.name: qsTr("Next game")
            onClicked: page.selectGameIndex(gameStrip.currentIndex + 1)
            background: Rectangle {
                radius: height / 2
                color: Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g, App.Theme.surfaceRaised.b, 0.86)
                border.width: 1
                border.color: App.Theme.border
            }
        }

        ListView {
            id: gameStrip
            objectName: "couchGameStrip"
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.min(page.stripWidth, parent.width - 2 * (page.arrowSize + page.arrowGap))
            anchors.verticalCenter: parent.verticalCenter
            height: Math.min(parent.height, page.cardHeight * 1.05 + 2 * page.cardPad)
            // Exactly `fittedCards` whole covers fit; the edge pad keeps the
            // scaled, ringed first/last card fully inside the view.
            readonly property real availableWidth: width - 2 * page.cardPad
            orientation: ListView.Horizontal
            spacing: page.cardGap
            leftMargin: page.cardPad
            rightMargin: page.cardPad
            clip: true
            model: page.displayGames
            currentIndex: count > 0 ? 0 : -1
            onCountChanged: {
                if (count === 0) currentIndex = -1
                else Qt.callLater(page.restoreRetainedSelection)
            }
            highlightMoveDuration: App.Theme.couchMotionDuration(180)
            highlightMoveVelocity: -1
            preferredHighlightBegin: page.cardPad
            preferredHighlightEnd: page.cardPad + (page.fittedCards - 1) * (page.cardWidth + page.cardGap) + page.cardWidth
            highlightRangeMode: ListView.ApplyRange
            boundsBehavior: Flickable.StopAtBounds
            keyNavigationEnabled: false

            delegate: Item {
                id: gameCell
                required property var modelData
                required property int index
                width: page.cardWidth
                height: gameStrip.height
                z: selected ? 2 : 1
                readonly property bool selected: gameStrip.currentIndex === index
                readonly property real probeCardWidth: gameCard.width * gameCard.scale
                readonly property real probeCardHeight: gameCard.height * gameCard.scale

                CouchGameCard {
                    id: gameCard
                    objectName: "couchHomeGameCard"
                    anchors.centerIn: parent
                    width: page.cardWidth
                    height: page.cardHeight
                    couchScale: page.fitScale
                    gameId: String(gameCell.modelData.id || "")
                    title: String(gameCell.modelData.name || qsTr("Unknown game"))
                    launcher: String(gameCell.modelData.launcher || "")
                    launchable: gameCell.modelData.launchAllowed === true
                    artworkSource: gameCell.modelData.portraitArtwork
                                   || gameCell.modelData.effectiveArtworkUrl
                                   || gameCell.modelData.fallbackArtwork
                                   || gameCell.modelData.headerArtwork || ""
                    selected: gameCell.selected
                    carouselFocused: page.focusZone === 0 && !page.contextMenuOpen
                    focus: page.focusZone === 0 && gameCell.selected
                    onClicked: {
                        if (gameStrip.currentIndex === gameCell.index)
                            page.openGame(String(gameCell.modelData.id || ""))
                        else
                            page.selectGameIndex(gameCell.index)
                    }
                }
            }
        }
    }

    Rectangle {
        objectName: "couchHomeEmptyState"
        visible: page.displayGames.length === 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: heroColumn.bottom
        anchors.bottom: footerRow.top
        anchors.margins: page.edgeMargin
        radius: App.Theme.couchPanelRadius * page.fitScale
        color: App.Theme.dark ? "#D5151D29" : "#EDFFFFFF"
        border.width: 1
        border.color: App.Theme.borderStrong
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 80 * page.fitScale, 720 * page.fitScale)
            spacing: 12 * page.fitScale
            Label {
                Layout.fillWidth: true
                text: qsTr("No games found")
                color: App.Theme.text
                font.pixelSize: 30 * page.fitScale
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Connect an available Steam library, then refresh the library from Desktop Mode.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchBodySize * page.fitScale
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // ---- Footer: real library position (hints from CouchMain sit at right) --
    Item {
        id: footerRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: bottomNav.top
        anchors.bottomMargin: App.Theme.couchSpaceS * page.fitScale
        height: 52 * page.fitScale

        Rectangle {
            id: positionTrack
            objectName: "couchHomePosition"
            // A scroll position indicator, shown only when the library is wider
            // than the visible covers: thumb = visible part, offset = position.
            visible: gameStrip.count > 0 && gameStrip.contentWidth > gameStrip.width + 1
            anchors.centerIn: parent
            width: 200 * page.fitScale
            height: 6 * page.fitScale
            radius: height / 2
            color: Qt.rgba(App.Theme.text.r, App.Theme.text.g, App.Theme.text.b, 0.16)
            Rectangle {
                readonly property real ratio: gameStrip.contentWidth > 0
                                              ? Math.min(1, gameStrip.width / gameStrip.contentWidth) : 1
                readonly property real progress: gameStrip.contentWidth > gameStrip.width
                        ? Math.max(0, Math.min(1, (gameStrip.contentX - gameStrip.originX + gameStrip.leftMargin)
                                                  / (gameStrip.contentWidth - gameStrip.width)))
                        : 0
                width: Math.max(parent.height * 3, parent.width * ratio)
                height: parent.height
                radius: parent.radius
                x: (parent.width - width) * progress
                color: App.Theme.accent
            }
        }
    }

    // ---- Global section navigation ---------------------------------------------
    CouchBottomNav {
        id: bottomNav
        objectName: "couchHomeNavigation"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin
        anchors.bottomMargin: page.bottomNavMargin
        height: implicitHeight
        couchScale: page.fitScale
        model: page.homeTiles
        activeIndex: 0
        currentIndex: page.selectedTile
        navFocused: page.focusZone === 1 && !page.contextMenuOpen
        onActivated: function(index) { page.selectedTile = index; page.activateTile() }
    }

    CouchOverlayFrame {
        anchors.fill: parent
        visible: page.contextMenuOpen
        couchScale: page.couchScale
        maximumWidth: 760 * page.couchScale
        preferredHeight: 560 * page.couchScale
        z: 170

        ColumnLayout {
            anchors.fill: parent
            spacing: 14 * page.couchScale
            Label {
                Layout.fillWidth: true
                text: String(page.selectedGame.name || qsTr("Game"))
                color: App.Theme.text
                font.pixelSize: 32 * page.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Choose an action for the selected game.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchHelperSize * page.couchScale
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10 * page.couchScale
                Repeater {
                    id: contextRepeater
                    model: page.contextEntries
                    delegate: CouchTile {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        couchScale: page.couchScale
                        symbol: modelData.symbol || ""
                        iconSource: modelData.icon || ""
                        text: modelData.title
                        enabled: modelData.enabled
                        primary: modelData.id === "launch"
                        showChevron: true
                        focus: page.contextMenuOpen
                               && page.contextMenuIndex === index
                        onClicked: {
                            page.contextMenuIndex = index
                            page.activateContextEntry()
                        }
                    }
                }
            }
        }
    }

    focus: visible
    Component.onCompleted: restoreActiveFocus()
    onVisibleChanged: if (visible) restoreActiveFocus()
}
