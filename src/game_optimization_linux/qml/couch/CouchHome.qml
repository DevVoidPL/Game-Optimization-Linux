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
        { "symbol": "▦", "icon": App.UiIcons.sidebarGames, "title": qsTr("Library") },
        { "symbol": "◉", "icon": App.UiIcons.sidebarNarrator, "title": qsTr("Narrator") },
        { "symbol": "↻", "icon": App.UiIcons.sidebarTasks, "title": qsTr("Tasks") },
        { "symbol": "↓", "icon": App.UiIcons.sidebarUpdates, "title": qsTr("Updates") },
        { "symbol": "⚙", "icon": App.UiIcons.sidebarSettings, "title": qsTr("Settings") }
    ]
    readonly property var displayGames: games || []
    property int selectedGameIndex: displayGames.length > 0
            ? Math.min(gameStrip.currentIndex, displayGames.length - 1) : -1
    property var selectedGame: selectedGameIndex >= 0
            ? displayGames[selectedGameIndex] : ({})
    readonly property var heroActions: [
        { "id": "launch", "icon": App.UiIcons.actionLaunch, "title": launchPending ? qsTr("Launching…") : qsTr("Launch"), "enabled": selectedGame.launchAllowed === true && !launchPending },
        { "id": "details", "icon": App.UiIcons.actionAdvancedSettings, "title": qsTr("Details"), "enabled": selectedGameIndex >= 0 },
        { "id": "more", "icon": App.UiIcons.actionSearch, "title": qsTr("More"), "enabled": selectedGameIndex >= 0 }
    ]
    readonly property var contextEntries: [
        { "id": "launch", "icon": App.UiIcons.actionLaunch, "title": qsTr("Launch"), "enabled": selectedGame.launchAllowed === true && !launchPending },
        { "id": "details", "icon": App.UiIcons.actionAdvancedSettings, "title": qsTr("Game details"), "enabled": selectedGameIndex >= 0 },
        { "id": "updates", "icon": App.UiIcons.sidebarUpdates, "title": qsTr("Updates"), "enabled": selectedGameIndex >= 0 },
        { "id": "close", "icon": App.UiIcons.actionCancel, "title": qsTr("Close menu"), "enabled": true }
    ]
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
        var tile = tileRepeater.itemAt(0)
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
                var tile = tileRepeater.itemAt(selectedTile)
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
        gameStrip.currentIndex = index
        gameStrip.positionViewAtIndex(index, ListView.Contain)
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
            controller.navigate("narrator")
            openNarrator()
            return true
        }
        if (selectedTile === 2 && controller) {
            controller.navigate("tasks")
            return true
        }
        if (selectedTile === 3 && controller) {
            controller.navigate("updates")
            return true
        }
        if (selectedTile === 4) {
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
            var previousZone = focusZone
            if (action === "NavigateUp") {
                if (focusZone === 1 || focusZone === 2)
                    focusZone = 0
            } else if (focusZone === 0) {
                focusZone = 2
            } else if (focusZone === 2) {
                focusZone = 1
            }
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

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
        clip: true

        Image {
            id: heroBackground
            anchors.fill: parent
            source: String(page.selectedGame.headerArtwork
                           || page.selectedGame.fallbackArtwork
                           || page.selectedGame.effectiveArtworkUrl || "")
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: true
            opacity: status === Image.Ready ? 0.68 : 0
            Behavior on opacity { NumberAnimation { duration: 160 } }
        }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: App.Theme.dark ? "#F20A0F17" : "#F2EEF3F8" }
                GradientStop { position: 0.48; color: App.Theme.dark ? "#B80A0F17" : "#B8EEF3F8" }
                GradientStop { position: 1.0; color: App.Theme.dark ? "#520A0F17" : "#60EEF3F8" }
            }
        }
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: parent.height * 0.38
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#00000000" }
                GradientStop { position: 1.0; color: App.Theme.dark ? "#F20A0F17" : "#F2EEF3F8" }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 70 * page.couchScale
        anchors.rightMargin: 70 * page.couchScale
        anchors.topMargin: 112 * page.couchScale
        anchors.bottomMargin: 90 * page.couchScale
        spacing: 12 * page.couchScale

        ColumnLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 116 * page.couchScale
            spacing: 6 * page.couchScale
            Label {
                objectName: "couchHomeHeroTitle"
                Layout.fillWidth: true
                text: String(page.selectedGame.name || qsTr("Your games"))
                color: App.Theme.text
                font.pixelSize: 46 * page.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                text: page.selectedGameIndex >= 0
                      ? qsTr("%1 · %2 · %3").arg(String(page.selectedGame.launcher || qsTr("Unknown"))).arg(String(page.selectedGame.status || qsTr("Unknown"))).arg(String(page.selectedGame.filesystem || qsTr("Unknown")))
                      : qsTr("No games were detected")
                color: App.Theme.textSecondary
                font.pixelSize: 20 * page.couchScale
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                text: page.taskSummary()
                color: App.Theme.accent
                font.pixelSize: 16 * page.couchScale
                font.weight: Font.DemiBold
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 66 * page.couchScale
            spacing: 14 * page.couchScale
            Repeater {
                id: heroActionRepeater
                model: page.heroActions
                delegate: CouchButton {
                    id: heroButton
                    objectName: "couchHomeHeroAction"
                    required property var modelData
                    required property int index
                    couchScale: page.couchScale
                    iconSource: modelData.icon || ""
                    text: modelData.title
                    enabled: modelData.enabled
                    focus: page.focusZone === 2 && page.selectedHeroAction === index
                    implicitWidth: index === 0 ? 230 * page.couchScale : 205 * page.couchScale
                    implicitHeight: 62 * page.couchScale
                    font.pixelSize: 19 * page.couchScale
                    font.weight: Font.Bold
                    onClicked: { page.selectedHeroAction = index; page.activateHeroAction() }
                    background: Rectangle {
                        radius: 16 * page.couchScale
                        color: heroButton.down ? App.Theme.surfacePressed
                              : heroButton.activeFocus ? App.Theme.accent
                              : App.Theme.surfaceRaised
                        border.width: heroButton.activeFocus ? 4 * page.couchScale : 1
                        border.color: heroButton.activeFocus ? "white" : App.Theme.borderStrong
                        scale: heroButton.activeFocus ? 1.045 : 1.0
                        Behavior on scale { NumberAnimation { duration: 140 } }
                    }
                }
            }
            Item { Layout.fillWidth: true }
        }

        ListView {
            id: gameStrip
            objectName: "couchGameStrip"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 350 * page.couchScale
            orientation: ListView.Horizontal
            spacing: 6 * page.couchScale
            leftMargin: 18 * page.couchScale
            rightMargin: 18 * page.couchScale
            clip: true
            visible: count > 0
            model: page.displayGames
            currentIndex: count > 0 ? 0 : -1
            onCountChanged: {
                if (count === 0) currentIndex = -1
                else Qt.callLater(page.restoreRetainedSelection)
            }
            highlightMoveDuration: 150
            preferredHighlightBegin: width * 0.07
            preferredHighlightEnd: width * 0.74
            highlightRangeMode: ListView.ApplyRange
            boundsBehavior: Flickable.StopAtBounds

            delegate: Item {
                id: gameCell
                required property var modelData
                required property int index
                width: 258 * page.couchScale
                height: gameStrip.height
                readonly property bool selected: gameStrip.currentIndex === index
                readonly property real probeCardWidth: gameCard.width * gameCard.scale
                readonly property real probeCardHeight: gameCard.height * gameCard.scale

                Rectangle {
                    anchors.centerIn: gameCard
                    width: gameCard.width + 18 * page.couchScale
                    height: gameCard.height + 18 * page.couchScale
                    radius: 25 * page.couchScale
                    color: gameCell.selected ? Qt.rgba(App.Theme.accent.r, App.Theme.accent.g, App.Theme.accent.b, 0.24) : "transparent"
                    visible: gameCell.selected
                }
                Button {
                    id: gameCard
                    objectName: "couchHomeGameCard"
                    anchors.centerIn: parent
                    width: 220 * page.couchScale
                    height: 330 * page.couchScale
                    focus: page.focusZone === 0 && gameCell.selected
                    scale: gameCell.selected ? 1.13 : 0.98
                    opacity: gameCell.selected ? 1.0 : 0.76
                    z: gameCell.selected ? 2 : 1
                    onClicked: {
                        if (gameStrip.currentIndex === gameCell.index)
                            page.openGame(String(gameCell.modelData.id || ""))
                        else
                            page.selectGameIndex(gameCell.index)
                    }
                    Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                    Behavior on opacity { NumberAnimation { duration: 140 } }
                    background: Rectangle {
                        radius: 19 * page.couchScale
                        clip: true
                        color: App.Theme.surface
                        border.width: gameCell.selected ? 4 * page.couchScale : 1
                        border.color: gameCell.selected ? "white" : App.Theme.border
                        GameArtwork {
                            anchors.fill: parent
                            gameId: String(gameCell.modelData.id || "")
                            title: String(gameCell.modelData.name || "")
                            launcher: String(gameCell.modelData.launcher || "Steam")
                            artworkSource: gameCell.modelData.effectiveArtworkUrl
                                           || gameCell.modelData.portraitArtwork
                                           || gameCell.modelData.fallbackArtwork
                                           || gameCell.modelData.headerArtwork || ""
                            artworkFillMode: Image.PreserveAspectCrop
                            cornerRadius: 19 * page.couchScale
                        }
                        Rectangle {
                            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                            height: 70 * page.couchScale
                            color: "#D80A0E14"
                            Label {
                                anchors.fill: parent
                                anchors.margins: 11 * page.couchScale
                                text: String(gameCell.modelData.name || qsTr("Unknown game"))
                                color: "white"
                                font.pixelSize: 16 * page.couchScale
                                font.weight: Font.Bold
                                maximumLineCount: 2
                                wrapMode: Text.Wrap
                                elide: Text.ElideRight
                            }
                        }
                        Rectangle {
                            anchors.fill: parent
                            radius: 19 * page.couchScale
                            color: "transparent"
                            border.width: gameCell.selected ? 4 * page.couchScale : 1
                            border.color: gameCell.selected ? "white" : App.Theme.border
                            z: 5
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.displayGames.length === 0
            objectName: "couchHomeEmptyState"
            radius: App.Theme.couchPanelRadius * page.couchScale
            color: App.Theme.dark ? "#D5151D29" : "#EDFFFFFF"
            border.width: 1
            border.color: App.Theme.borderStrong

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 80 * page.couchScale,
                                720 * page.couchScale)
                spacing: 12 * page.couchScale
                Label {
                    Layout.fillWidth: true
                    text: qsTr("No games found")
                    color: App.Theme.text
                    font.pixelSize: 30 * page.couchScale
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Connect an available Steam library, then refresh the library from Desktop Mode.")
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.couchBodySize * page.couchScale
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }

        Rectangle {
            objectName: "couchHomeNavigation"
            Layout.fillWidth: true
            Layout.preferredHeight: 68 * page.couchScale
            radius: 18 * page.couchScale
            color: App.Theme.dark ? "#B516202C" : "#C9FFFFFF"
            border.width: 1
            border.color: App.Theme.border
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12 * page.couchScale
                anchors.rightMargin: 12 * page.couchScale
                spacing: 8 * page.couchScale
                Repeater {
                    id: tileRepeater
                    model: page.homeTiles
                    delegate: CouchButton {
                        id: navigationButton
                        objectName: "couchHomeTile"
                        required property var modelData
                        required property int index
                        couchScale: page.couchScale
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        iconSource: modelData.icon || ""
                        iconSize: App.Theme.couchIconSizeNav
                        text: modelData.title
                        focus: page.focusZone === 1 && page.selectedTile === index
                        font.pixelSize: 18 * page.couchScale
                        font.weight: Font.DemiBold
                        onClicked: { page.selectedTile = index; page.activateTile() }
                        background: Rectangle {
                            radius: 13 * page.couchScale
                            color: navigationButton.activeFocus ? App.Theme.surfaceSelected : "transparent"
                            border.width: navigationButton.activeFocus ? 4 * page.couchScale : 0
                            border.color: navigationButton.activeFocus ? App.Theme.accent : "transparent"
                            scale: navigationButton.activeFocus ? 1.035 : 1.0
                            Behavior on scale { NumberAnimation { duration: 140 } }
                        }
                    }
                }
            }
        }
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
