pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

// Couch Mode full library: header, filter strip, fitted cover grid, bottom
// navigation. Focus zones top to bottom: 0 filters, 1 grid, 2 navigation.
//
// Filters use stable ids only (source: all/steam/heroic/lutris/custom, state:
// any/ready/unavailable/attention/btrfs, sort: name-asc/name-desc/size/saved)
// and are evaluated against fields produced by presenters.game_to_qml. A chip
// is shown only when it would really narrow the current library.
FocusScope {
    id: page
    objectName: "couchLibrary"
    property var controller
    property var navigation
    property real couchScale: 1.0
    // Same vertical fit as Home: short screens shrink the layout instead of
    // clipping it.
    readonly property real fitScale: height > 0 ? Math.min(couchScale, height / 1080) : couchScale
    readonly property real edgeMargin: 48 * fitScale
    readonly property real bottomNavMargin: 40 * fitScale
    // Space CouchMain keeps free for controller hints (footer row, as on Home).
    readonly property real hintsBottomMargin: bottomNavMargin + bottomNav.height + App.Theme.couchSpaceS * fitScale

    property var games: controller && controller.games ? controller.games : []
    readonly property bool isScanning: Boolean(controller && controller.isScanning)
    readonly property string scanStatus: controller && controller.libraryScanStatus
                                         ? String(controller.libraryScanStatus) : ""
    readonly property string scanMessage: controller && controller.libraryScanMessage
                                          ? String(controller.libraryScanMessage) : ""

    // ---- Filter state (stable ids) -------------------------------------------
    property string sourceFilter: "all"
    property string stateFilter: "any"
    property string searchQuery: ""
    property string sortMode: "name-asc"
    readonly property var sortModes: ["name-asc", "name-desc", "size", "saved"]
    readonly property var sourceOrder: ["steam", "heroic", "lutris", "custom"]
    readonly property var stateOrder: ["ready", "unavailable", "attention", "btrfs"]
    readonly property var librarySummary: summarize(games)
    readonly property var presentSources: sourceOrder.filter(function(id) {
        return (page.librarySummary.sources[id] || 0) > 0
    })
    readonly property bool sourceCycleAvailable: presentSources.length >= 2
    // Launchers whose data was found by the provider scan (diagnostics roots
    // with state "found"), even if they contributed no games yet.
    readonly property var detectedSources: {
        var rows = controller && controller.libraryProviderDiagnostics
                ? controller.libraryProviderDiagnostics : []
        var result = []
        for (var i = 0; i < rows.length; ++i) {
            var id = String(rows[i].launcher || "").toLowerCase()
            var roots = rows[i].roots || []
            for (var r = 0; r < roots.length; ++r) {
                if (String(roots[r].state || "") === "found" && sourceOrder.indexOf(id) >= 0
                        && result.indexOf(id) < 0)
                    result.push(id)
            }
        }
        return result
    }
    // Detected launchers with zero games get an honest, disabled "X (0)" chip.
    readonly property var emptyDetectedSources: detectedSources.filter(function(id) {
        return (page.librarySummary.sources[id] || 0) === 0
    })
    readonly property var filterItems: buildFilterItems()
    readonly property var filteredGames: filterGames()
    readonly property bool filtersActive: sourceFilter !== "all" || stateFilter !== "any"
    readonly property bool searchActive: searchQuery.length > 0

    // ---- Focus state ---------------------------------------------------------
    property int focusZone: 1
    property int filterIndex: 0
    property string filterFocusKey: "search"
    property int selectedIndex: -1
    property int selectedTile: 0
    property string retainedGameId: ""
    property bool pendingFirstSelection: false
    readonly property bool keyboardOpen: searchKeyboard.opened
    readonly property var selectedGame: selectedIndex >= 0 && selectedIndex < filteredGames.length
                                        ? filteredGames[selectedIndex] : ({})
    // "loading" | "error" | "empty" | "no-results" | "no-matches" | "grid"
    readonly property string viewState: games.length === 0
                                        ? (isScanning ? "loading" : scanStatus === "error" ? "error" : "empty")
                                        : filteredGames.length === 0
                                          ? (searchActive ? "no-results" : "no-matches")
                                          : "grid"

    // ---- Grid geometry (1920x1080 reference, scaled by fitScale) --------------
    readonly property real cardGap: 24 * fitScale
    // Room around the grid for the selected card's scale, ring and glow.
    readonly property real cardPad: 22 * fitScale
    readonly property real viewMargin: cardPad - cardGap / 2
    readonly property int minimumRows: 2
    readonly property real cardHeight: Math.max(150 * fitScale, Math.min(
        300 * fitScale,
        (gridArea.height - 2 * cardPad - (minimumRows - 1) * cardGap) / minimumRows))
    readonly property real cardWidth: cardHeight / 1.46
    readonly property int columns: Math.max(1, Math.floor((gridArea.width - 2 * cardPad + cardGap)
                                                          / (cardWidth + cardGap)))
    readonly property int visibleRows: Math.max(1, Math.floor((gridArea.height - 2 * viewMargin)
                                                              / (cardHeight + cardGap)))

    readonly property var libraryTiles: [
        { "id": "library", "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight, "title": qsTr("Library"), "subtitle": qsTr("Your games") },
        { "id": "tasks", "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight, "title": qsTr("Tasks"), "subtitle": qsTr("Active and recent tasks") },
        { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "title": qsTr("Updates"), "subtitle": qsTr("File changes and re-analysis") },
        { "id": "settings", "icon": App.UiIcons.couchGlyphSettings, "iconOnLight": App.UiIcons.couchGlyphSettingsOnLight, "title": qsTr("Settings"), "subtitle": qsTr("App configuration") }
    ]

    signal openGame(string gameId)
    signal backRequested()
    signal sectionRequested(string section)

    // ---- Data helpers --------------------------------------------------------
    function sourceOf(game) {
        var launcher = String(game.launcher || "").toLowerCase()
        return launcher === "steam" ? "steam" : launcher === "heroic" ? "heroic"
             : launcher === "lutris" ? "lutris" : launcher === "manual" ? "custom" : ""
    }
    function sourceLabel(id) {
        return id === "all" || !id ? qsTr("All") : App.I18n.launcherName(id)
    }
    function stateLabel(id) {
        return id === "ready" ? qsTr("Ready to launch") : id === "unavailable" ? qsTr("Unavailable")
             : id === "attention" ? qsTr("Needs attention") : id === "btrfs" ? "Btrfs" : ""
    }
    function sortLabel(id) {
        return id === "name-desc" ? qsTr("Name Z-A") : id === "size" ? qsTr("Largest first")
             : id === "saved" ? qsTr("Most saved") : qsTr("Name A-Z")
    }
    function matchesState(game, id) {
        if (id === "ready") return game.launchAllowed === true
        if (id === "unavailable") return game.launchAllowed !== true
        if (id === "attention") return String(game.status || "").toLowerCase() === "needs attention"
        if (id === "btrfs") return String(game.filesystem || "").toLowerCase() === "btrfs"
        return true
    }
    function summarize(list) {
        var result = { "total": 0, "offline": 0, "sources": {}, "states": {} }
        var source = list || []
        for (var i = 0; i < source.length; ++i) {
            var game = source[i]
            result.total += 1
            var sourceId = sourceOf(game)
            if (sourceId.length)
                result.sources[sourceId] = (result.sources[sourceId] || 0) + 1
            for (var s = 0; s < stateOrder.length; ++s) {
                if (matchesState(game, stateOrder[s]))
                    result.states[stateOrder[s]] = (result.states[stateOrder[s]] || 0) + 1
            }
            if (game.libraryAvailable === false || String(game.status || "") === "Drive disconnected")
                result.offline += 1
        }
        return result
    }
    function stateChipUseful(id) {
        var count = librarySummary.states[id] || 0
        return count > 0 && count < librarySummary.total
    }
    function filterGames() {
        var query = searchQuery.toLowerCase()
        var result = []
        var source = games || []
        for (var i = 0; i < source.length; ++i) {
            var game = source[i]
            if (sourceFilter !== "all" && sourceOf(game) !== sourceFilter)
                continue
            if (stateFilter !== "any" && !matchesState(game, stateFilter))
                continue
            if (query.length && String(game.name || "").toLowerCase().indexOf(query) < 0)
                continue
            result.push({ "game": game, "order": i })
        }
        var mode = sortMode
        result.sort(function(a, b) {
            var nameA = String(a.game.name || "").toLowerCase()
            var nameB = String(b.game.name || "").toLowerCase()
            var byName = nameA < nameB ? -1 : nameA > nameB ? 1 : a.order - b.order
            if (mode === "name-desc") return -byName
            if (mode === "size" || mode === "saved") {
                var key = mode === "size" ? "sizeBytes" : "savedBytes"
                var diff = (Number(b.game[key]) || 0) - (Number(a.game[key]) || 0)
                return diff !== 0 ? diff : byName
            }
            return byName
        })
        return result.map(function(entry) { return entry.game })
    }
    function buildFilterItems() {
        var shortQuery = searchQuery.length > 22 ? searchQuery.slice(0, 21) + "…" : searchQuery
        var items = [
            { "key": "search", "groupStart": false, "active": searchActive,
              "icon": App.UiIcons.couchGlyphSearch, "iconOnLight": App.UiIcons.couchGlyphSearchOnLight,
              "label": searchActive ? qsTr("Search: %1").arg(shortQuery) : qsTr("Search") },
            { "key": "sort", "groupStart": false, "active": false,
              "icon": App.UiIcons.couchGlyphSort, "iconOnLight": App.UiIcons.couchGlyphSortOnLight,
              "label": qsTr("Sort: %1").arg(sortLabel(sortMode)) }
        ]
        if (presentSources.length >= 2 || emptyDetectedSources.length > 0) {
            items.push({ "key": "source:all", "groupStart": true, "active": sourceFilter === "all",
                         "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight,
                         "label": qsTr("All") })
            for (var i = 0; i < sourceOrder.length; ++i) {
                var sourceId = sourceOrder[i]
                var present = presentSources.indexOf(sourceId) >= 0
                if (!present && emptyDetectedSources.indexOf(sourceId) < 0)
                    continue
                items.push({ "key": "source:" + sourceId, "groupStart": false,
                             "active": sourceFilter === sourceId, "disabled": !present,
                             "icon": String(App.UiIcons.launcherLogo(sourceId)).length ? App.UiIcons.launcherLogo(sourceId) : App.UiIcons.couchGlyphSource,
                             "iconOnLight": String(App.UiIcons.launcherLogo(sourceId)).length ? App.UiIcons.launcherLogo(sourceId) : App.UiIcons.couchGlyphSourceOnLight,
                             "label": present ? sourceLabel(sourceId)
                                              : qsTr("%1 (0)").arg(sourceLabel(sourceId)) })
            }
        }
        var first = true
        for (var s = 0; s < stateOrder.length; ++s) {
            var id = stateOrder[s]
            if (!stateChipUseful(id) && stateFilter !== id)
                continue
            var icon = id === "ready" ? [App.UiIcons.couchGlyphStatusReady, App.UiIcons.couchGlyphStatusReadyOnLight]
                     : id === "unavailable" ? [App.UiIcons.couchGlyphStatusError, App.UiIcons.couchGlyphStatusErrorOnLight]
                     : id === "attention" ? [App.UiIcons.couchGlyphStatusWarning, App.UiIcons.couchGlyphStatusWarningOnLight]
                     : [App.UiIcons.couchGlyphStorage, App.UiIcons.couchGlyphStorageOnLight]
            items.push({ "key": "state:" + id, "groupStart": first, "active": stateFilter === id,
                         "icon": icon[0], "iconOnLight": icon[1], "label": stateLabel(id) })
            first = false
        }
        return items
    }
    function summaryText() {
        var shown = filteredGames.length
        var total = games.length
        var parts = [shown === total ? qsTr("%1 games").arg(total)
                                     : qsTr("%1 of %2 games").arg(shown).arg(total)]
        if (sourceFilter !== "all") parts.push(sourceLabel(sourceFilter))
        if (stateFilter !== "any") parts.push(stateLabel(stateFilter))
        if (searchActive) parts.push(qsTr("Search: %1").arg(searchQuery))
        parts.push(sortLabel(sortMode))
        return parts.join("  ·  ")
    }
    // Card availability line: only for games that really cannot launch.
    function stateTextFor(game) {
        if (game.launchAllowed === true) return ""
        var status = String(game.status || "")
        if (game.libraryAvailable === false || status === "Drive disconnected")
            return App.I18n.status("Drive disconnected")
        if (status === "Missing files")
            return App.I18n.status("Missing files")
        return qsTr("Launch unavailable")
    }
    function stateToneFor(game) {
        var status = String(game.status || "")
        if (game.libraryAvailable === false || status === "Drive disconnected") return "offline"
        if (status === "Missing files") return "danger"
        return "warning"
    }
    function stableIds() {
        var ids = []
        for (var i = 0; i < filteredGames.length; ++i)
            ids.push(String(filteredGames[i].id || ""))
        return ids
    }
    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }

    // ---- Selection and scrolling ------------------------------------------------
    function restoreSelection() {
        var ids = stableIds()
        if (ids.length === 0) {
            selectedIndex = -1
            pendingFirstSelection = false
            if (focusZone === 1) {
                focusZone = 0
                restoreActiveFocus()
            }
            return
        }
        var index = -1
        if (pendingFirstSelection) {
            pendingFirstSelection = false
            index = 0
            if (navigation)
                navigation.rememberFocus("library", ids[0], 0)
        } else {
            var wanted = navigation ? navigation.reconcileFocus("library", ids) : retainedGameId
            index = ids.indexOf(wanted)
            if (index < 0)
                index = Math.max(0, Math.min(selectedIndex, ids.length - 1))
        }
        selectedIndex = index
        retainedGameId = ids[index]
        ensureVisible(index, false)
    }
    function rememberSelection() {
        if (selectedIndex < 0 || selectedIndex >= filteredGames.length)
            return
        retainedGameId = String(filteredGames[selectedIndex].id || "")
        if (navigation)
            navigation.rememberFocus("library", retainedGameId, selectedIndex)
    }
    // Keep the selected row whole inside the view; rows snap to the grid.
    function ensureVisible(index, animate) {
        if (index < 0 || columns <= 0)
            return
        gameGrid.forceLayout()
        var row = Math.floor(index / columns)
        var rowTop = gameGrid.originY + row * gameGrid.cellHeight
        var target = gameGrid.contentY
        if (rowTop < gameGrid.contentY + viewMargin)
            target = rowTop - viewMargin
        else if (rowTop + gameGrid.cellHeight > gameGrid.contentY + gameGrid.height - viewMargin)
            target = rowTop + gameGrid.cellHeight - gameGrid.height + viewMargin
        var minimum = gameGrid.originY - viewMargin
        var maximum = Math.max(minimum, gameGrid.originY + gameGrid.contentHeight
                                        + viewMargin - gameGrid.height)
        target = Math.max(minimum, Math.min(maximum, target))
        if (Math.abs(target - gameGrid.contentY) < 0.5)
            return
        scrollAnimation.stop()
        if (animate && App.Theme.couchMotionDuration(180) > 0) {
            scrollAnimation.to = target
            scrollAnimation.start()
        } else {
            gameGrid.contentY = target
        }
    }
    function selectIndex(index) {
        if (filteredGames.length === 0)
            return false
        var target = Math.max(0, Math.min(filteredGames.length - 1, index))
        var changed = selectedIndex !== target
        selectedIndex = target
        rememberSelection()
        ensureVisible(target, true)
        return changed
    }
    // Used when returning from game details.
    function focusGame(gameId) {
        if (String(gameId || "").length) {
            retainedGameId = String(gameId)
            if (navigation)
                navigation.rememberFocus("library", retainedGameId, -1)
        }
        restoreSelection()
        focusZone = selectedIndex >= 0 ? 1 : 0
        restoreActiveFocus()
    }
    function openSelected() {
        if (selectedIndex < 0)
            return false
        rememberSelection()
        openGame(String(selectedGame.id || ""))
        return true
    }

    // ---- Filters -------------------------------------------------------------
    function filterChanged() {
        pendingFirstSelection = true
        Qt.callLater(restoreSelection)
    }
    function setSourceFilter(id) {
        if (sourceFilter === id)
            return false
        sourceFilter = id
        filterChanged()
        return true
    }
    function setStateFilter(id) {
        if (stateFilter === id)
            return false
        stateFilter = id
        filterChanged()
        return true
    }
    function cycleSort() {
        var index = sortModes.indexOf(sortMode)
        sortMode = sortModes[(index + 1) % sortModes.length]
        filterChanged()
        return true
    }
    function cycleSource(delta) {
        if (!sourceCycleAvailable)
            return false
        var list = ["all"].concat(presentSources)
        var index = Math.max(0, list.indexOf(sourceFilter))
        return setSourceFilter(list[(index + delta + list.length) % list.length])
    }
    function activateFilter(index) {
        var item = filterItems[index]
        if (!item || item.disabled === true)
            return false
        filterIndex = index
        filterFocusKey = item.key
        if (item.key === "search")
            return openSearch()
        if (item.key === "sort")
            return cycleSort()
        if (item.key.indexOf("source:") === 0)
            return setSourceFilter(item.key.slice(7)) || true
        if (item.key.indexOf("state:") === 0) {
            var id = item.key.slice(6)
            return setStateFilter(stateFilter === id ? "any" : id)
        }
        return false
    }
    // Filters that no longer narrow the library (e.g. a drive came back) are
    // cleared; the retained game stays selected.
    function normalizeFilters() {
        if (sourceFilter !== "all" && presentSources.indexOf(sourceFilter) < 0)
            sourceFilter = "all"
        if (stateFilter !== "any" && (librarySummary.states[stateFilter] || 0) === 0)
            stateFilter = "any"
    }
    function syncFilterIndex() {
        for (var i = 0; i < filterItems.length; ++i) {
            if (filterItems[i].key === filterFocusKey) {
                filterIndex = i
                return
            }
        }
        filterIndex = Math.max(0, Math.min(filterIndex, filterItems.length - 1))
        filterFocusKey = filterItems.length ? filterItems[filterIndex].key : "search"
    }
    function openSearch() {
        searchKeyboard.open(searchQuery, qsTr("Search games"))
        if (navigation)
            navigation.openModal("library-search", "keyboard")
        return true
    }
    function closeKeyboard() {
        searchKeyboard.close(false)
    }

    // ---- Bottom navigation ----------------------------------------------------
    function activateTile() {
        if (selectedTile === 0) {
            // Already in the Library: go to the games.
            focusZone = selectedIndex >= 0 ? 1 : 0
            return true
        }
        var tile = libraryTiles[selectedTile]
        if (!tile)
            return false
        sectionRequested(tile.id)
        return true
    }

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (searchKeyboard.opened) {
                searchKeyboard.forceActiveFocus()
                searchKeyboard.focusSelected()
            } else if (page.focusZone === 0) {
                var chip = filterList.itemAtIndex(page.filterIndex)
                if (chip) chip.chip.forceActiveFocus()
                else filterList.forceActiveFocus()
            } else if (page.focusZone === 1) {
                gameGrid.forceActiveFocus()
            } else {
                var tile = bottomNav.itemAt(page.selectedTile)
                if (tile) tile.forceActiveFocus()
            }
        })
    }

    function handleAction(action) {
        if (action === "ContextMenu" || action === "Search") action = "MoreActions"
        else if (action === "PageLeft" || action === "PreviousSection") action = "PreviousTab"
        else if (action === "PageRight" || action === "NextSection") action = "NextTab"
        if (searchKeyboard.opened) {
            searchKeyboard.handleAction(action)
            return
        }
        if (action === "Back") {
            backRequested()
            playSemanticSound("back")
            return
        }
        if (action === "MoreActions") {
            openSearch()
            return
        }
        if (action === "PreviousTab" || action === "NextTab") {
            playSemanticSound(cycleSource(action === "PreviousTab" ? -1 : 1) ? "navigate" : "error")
            return
        }
        var hasGames = filteredGames.length > 0
        var previousZone = focusZone
        if (focusZone === 0) {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                var next = Math.max(0, Math.min(filterItems.length - 1,
                                                filterIndex + (action === "NavigateLeft" ? -1 : 1)))
                if (next !== filterIndex) {
                    filterIndex = next
                    filterFocusKey = filterItems[next].key
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateDown") {
                focusZone = hasGames ? 1 : 2
            } else if (action === "Confirm") {
                playSemanticSound(activateFilter(filterIndex) ? "confirm" : "error")
            }
        } else if (focusZone === 1) {
            var column = selectedIndex % columns
            if (action === "NavigateLeft") {
                if (column > 0 && selectIndex(selectedIndex - 1))
                    playSemanticSound("navigate")
            } else if (action === "NavigateRight") {
                if (column < columns - 1 && selectedIndex + 1 < filteredGames.length
                        && selectIndex(selectedIndex + 1))
                    playSemanticSound("navigate")
            } else if (action === "NavigateUp") {
                if (selectedIndex < columns)
                    focusZone = 0
                else if (selectIndex(selectedIndex - columns))
                    playSemanticSound("navigate")
            } else if (action === "NavigateDown") {
                var lastRow = Math.floor((filteredGames.length - 1) / columns)
                if (Math.floor(selectedIndex / columns) >= lastRow)
                    focusZone = 2
                else if (selectIndex(Math.min(filteredGames.length - 1, selectedIndex + columns)))
                    playSemanticSound("navigate")
            } else if (action === "PageUp" || action === "PageDown") {
                var step = columns * visibleRows * (action === "PageUp" ? -1 : 1)
                if (selectIndex(selectedIndex + step))
                    playSemanticSound("navigate")
            } else if (action === "Confirm") {
                playSemanticSound(openSelected() ? "confirm" : "error")
            }
        } else {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                var tile = Math.max(0, Math.min(libraryTiles.length - 1,
                                                selectedTile + (action === "NavigateLeft" ? -1 : 1)))
                if (tile !== selectedTile) {
                    selectedTile = tile
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateUp") {
                // Back to the previously selected game (or the filters).
                focusZone = hasGames ? 1 : 0
            } else if (action === "Confirm") {
                playSemanticSound(activateTile() ? "confirm" : "error")
            }
        }
        if (focusZone !== previousZone) {
            if (focusZone === 1 && selectedIndex >= 0)
                ensureVisible(selectedIndex, true)
            playSemanticSound("navigate")
            restoreActiveFocus()
        }
    }

    onFilteredGamesChanged: Qt.callLater(restoreSelection)
    onLibrarySummaryChanged: Qt.callLater(normalizeFilters)
    onFilterItemsChanged: syncFilterIndex()
    onColumnsChanged: if (selectedIndex >= 0) Qt.callLater(function() { page.ensureVisible(page.selectedIndex, false) })
    focus: visible
    Component.onCompleted: {
        Qt.callLater(restoreSelection)
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) {
        focusZone = filteredGames.length > 0 ? 1 : 0
        selectedTile = 0
        Qt.callLater(restoreSelection)
        restoreActiveFocus()
    }

    NumberAnimation {
        id: scrollAnimation
        target: gameGrid
        property: "contentY"
        duration: App.Theme.couchMotionDuration(180)
        easing.type: Easing.OutCubic
    }

    // Neutral themed backdrop (same fallback surface as Home/Details).
    CouchHeroBackdrop {
        anchors.fill: parent
        sources: []
    }

    // ---- Header --------------------------------------------------------------
    Item {
        id: header
        objectName: "couchLibraryHeader"
        anchors.left: gridFrame.left
        anchors.right: gridFrame.right
        anchors.leftMargin: page.cardPad
        anchors.rightMargin: page.cardPad
        anchors.top: parent.top
        anchors.topMargin: 92 * page.fitScale
        height: 60 * page.fitScale

        Label {
            id: headerTitle
            objectName: "couchLibraryTitle"
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Library")
            color: App.Theme.text
            font.pixelSize: App.Theme.couchTitleSize * page.fitScale
            font.weight: Font.Bold
        }
        Label {
            objectName: "couchLibrarySummary"
            anchors.left: headerTitle.right
            anchors.leftMargin: App.Theme.couchSpaceL * page.fitScale
            anchors.right: headerStatus.visible ? headerStatus.left : parent.right
            anchors.rightMargin: headerStatus.visible ? App.Theme.couchSpaceL * page.fitScale : 0
            anchors.baseline: headerTitle.baseline
            text: page.summaryText()
            color: App.Theme.textSecondary
            font.pixelSize: 20 * page.fitScale
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
        CouchStatePill {
            id: headerStatus
            objectName: "couchLibraryStatus"
            readonly property string kind: page.isScanning && page.games.length > 0 ? "scanning"
                                           : page.librarySummary.offline > 0 ? "offline" : ""
            visible: kind.length > 0
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            couchScale: page.fitScale
            tone: kind === "offline" ? "warning" : "info"
            iconSource: kind === "offline" ? App.UiIcons.couchGlyphLibraryOffline : App.UiIcons.couchGlyphRefresh
            iconLightSource: kind === "offline" ? App.UiIcons.couchGlyphLibraryOfflineOnLight
                                                : App.UiIcons.couchGlyphRefreshOnLight
            text: kind === "offline" ? qsTr("Games on a disconnected drive: %1").arg(page.librarySummary.offline)
                                     : qsTr("Refreshing library…")
        }
    }

    // ---- Filter strip (focus zone 0) ---------------------------------------------
    ListView {
        id: filterList
        objectName: "couchLibraryFilters"
        readonly property real chipHeight: 54 * page.fitScale
        readonly property real chipPad: 12 * page.fitScale
        anchors.left: gridFrame.left
        anchors.right: gridFrame.right
        anchors.leftMargin: page.cardPad - chipPad
        anchors.rightMargin: page.cardPad - chipPad
        anchors.top: header.bottom
        anchors.topMargin: 6 * page.fitScale
        height: chipHeight + 2 * chipPad
        orientation: ListView.Horizontal
        spacing: App.Theme.couchSpaceM * page.fitScale
        leftMargin: chipPad
        rightMargin: chipPad
        clip: true
        interactive: contentWidth > width
        boundsBehavior: Flickable.StopAtBounds
        keyNavigationEnabled: false
        model: page.filterItems
        currentIndex: page.filterIndex
        highlightMoveDuration: App.Theme.couchMotionDuration(160)
        highlightMoveVelocity: -1
        preferredHighlightBegin: chipPad
        preferredHighlightEnd: width - chipPad
        highlightRangeMode: ListView.ApplyRange

        delegate: Item {
            id: chipCell
            required property var modelData
            required property int index
            property alias chip: chipButton
            readonly property real dividerWidth: modelData.groupStart
                                                 ? App.Theme.couchSpaceM * page.fitScale + 2 : 0
            width: chipButton.width + dividerWidth
            height: filterList.height

            Rectangle {
                visible: chipCell.modelData.groupStart
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(1, Math.round(page.fitScale))
                height: filterList.chipHeight * 0.6
                color: App.Theme.borderStrong
            }
            CouchButton {
                id: chipButton
                objectName: "couchLibraryFilterChip"
                property string filterKey: chipCell.modelData.key
                readonly property bool active: chipCell.modelData.active === true
                x: chipCell.dividerWidth
                anchors.verticalCenter: parent.verticalCenter
                couchScale: page.fitScale
                implicitHeight: filterList.chipHeight
                implicitWidth: contentItem.implicitWidth + 2 * App.Theme.couchSpaceL * page.fitScale
                width: implicitWidth
                height: implicitHeight
                focusPolicy: Qt.NoFocus
                text: chipCell.modelData.label
                // Still focusable (no focus trap), but shown and announced as unavailable.
                opacity: chipCell.modelData.disabled === true ? 0.55 : 1.0
                Accessible.description: chipCell.modelData.disabled === true
                                        ? qsTr("Detected, but no installed games were found") : ""
                iconSource: chipCell.modelData.icon || ""
                iconLightSource: chipCell.modelData.iconOnLight || ""
                iconSize: App.Theme.couchIconSmall
                font.pixelSize: 18 * page.fitScale
                font.weight: active ? Font.Bold : Font.DemiBold
                focus: page.focusZone === 0 && !page.keyboardOpen && page.filterIndex === chipCell.index
                Accessible.checkable: filterKey.indexOf(":") > 0
                Accessible.checked: active
                onClicked: {
                    page.focusZone = 0
                    page.playSemanticSound(page.activateFilter(chipCell.index) ? "confirm" : "error")
                }
                background: Rectangle {
                    radius: height / 2
                    color: chipButton.down ? App.Theme.surfacePressed
                          : chipButton.focusVisible ? App.Theme.couchFocusSurface
                          : chipButton.active ? App.Theme.accentSoft
                          : Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                                    App.Theme.surfaceRaised.b, 0.88)
                    border.width: chipButton.active ? 2 * page.fitScale : 1
                    border.color: chipButton.active ? App.Theme.accent : App.Theme.borderStrong
                    scale: chipButton.down ? App.Theme.couchPressScale
                                           : chipButton.focusVisible ? 1.04 : 1.0
                    Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                    CouchFocusFrame {
                        active: chipButton.focusVisible
                        radius: parent.radius
                        couchScale: page.fitScale
                    }
                }
            }
        }
    }

    // ---- Grid area (focus zone 1) ------------------------------------------------
    Item {
        id: gridArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: filterList.bottom
        anchors.bottom: footerRow.top
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin
    }

    // Exactly `columns` whole covers and `visibleRows` whole rows; the frame
    // clips, the margins keep the scaled, ringed first/last card inside.
    Item {
        id: gridFrame
        objectName: "couchLibraryGridFrame"
        x: gridArea.x + Math.max(0, (gridArea.width - width) / 2)
        y: gridArea.y
        width: page.columns * (page.cardWidth + page.cardGap) + 2 * page.viewMargin
        height: page.visibleRows * (page.cardHeight + page.cardGap) + 2 * page.viewMargin
        clip: true
        visible: page.viewState === "grid"

        GridView {
            id: gameGrid
            objectName: "couchLibraryGrid"
            readonly property int columnCount: page.columns
            readonly property int selectedIndex: page.selectedIndex
            x: page.viewMargin
            width: page.columns * cellWidth
            height: parent.height
            topMargin: page.viewMargin
            bottomMargin: page.viewMargin
            cellWidth: page.cardWidth + page.cardGap
            cellHeight: page.cardHeight + page.cardGap
            model: page.filteredGames
            reuseItems: true
            cacheBuffer: Math.round(cellHeight)
            keyNavigationEnabled: false
            highlightFollowsCurrentItem: false
            currentIndex: -1
            boundsBehavior: Flickable.StopAtBounds
            snapMode: GridView.SnapToRow
            onMovementStarted: scrollAnimation.stop()

            delegate: Item {
                id: cell
                required property var modelData
                required property int index
                readonly property bool selected: page.selectedIndex === index
                                                 && page.focusZone === 1 && !page.keyboardOpen
                width: gameGrid.cellWidth
                height: gameGrid.cellHeight
                z: selected ? 2 : 1

                CouchGameCard {
                    objectName: "couchLibraryCard"
                    anchors.centerIn: parent
                    width: page.cardWidth
                    height: page.cardHeight
                    couchScale: page.fitScale
                    gameId: String(cell.modelData.id || "")
                    title: String(cell.modelData.name || qsTr("Unknown game"))
                    launcher: page.sourceOf(cell.modelData).length ? String(cell.modelData.launcher || "") : ""
                    launcherLabel: page.sourceLabel(page.sourceOf(cell.modelData))
                    launchable: cell.modelData.launchAllowed === true
                    stateText: page.stateTextFor(cell.modelData)
                    stateTone: page.stateToneFor(cell.modelData)
                    artworkSource: cell.modelData.portraitArtwork
                                   || cell.modelData.effectiveArtworkUrl
                                   || cell.modelData.fallbackArtwork
                                   || cell.modelData.headerArtwork || ""
                    selected: cell.selected
                    carouselFocused: true
                    onClicked: {
                        if (cell.selected) {
                            page.openSelected()
                        } else {
                            page.focusZone = 1
                            page.selectIndex(cell.index)
                            page.restoreActiveFocus()
                        }
                    }
                }
            }
        }
    }

    // ---- Empty, loading and error states -------------------------------------------
    Rectangle {
        id: emptyState
        objectName: "couchLibraryEmptyState"
        readonly property string kind: page.viewState
        visible: kind !== "grid"
        anchors.centerIn: gridArea
        width: Math.min(gridArea.width - 2 * page.cardPad, 940 * page.fitScale)
        height: Math.min(gridArea.height - 2 * page.cardPad, 320 * page.fitScale)
        radius: App.Theme.couchPanelRadius * page.fitScale
        color: App.Theme.dark ? "#D5151D29" : "#EDFFFFFF"
        border.width: 1
        border.color: App.Theme.borderStrong

        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width - 80 * page.fitScale
            spacing: App.Theme.couchSpaceM * page.fitScale
            CouchIcon {
                objectName: "couchLibraryEmptyIcon"
                Layout.alignment: Qt.AlignHCenter
                size: App.Theme.couchIconLarge
                couchScale: page.fitScale
                source: emptyState.kind === "loading" ? App.UiIcons.couchGlyphRefresh
                        : emptyState.kind === "error" ? App.UiIcons.couchGlyphStatusError
                        : emptyState.kind === "no-results" ? App.UiIcons.couchGlyphSearch
                        : emptyState.kind === "no-matches" ? App.UiIcons.couchGlyphFilter
                        : App.UiIcons.couchGlyphLibrary
                lightSource: emptyState.kind === "loading" ? App.UiIcons.couchGlyphRefreshOnLight
                             : emptyState.kind === "error" ? App.UiIcons.couchGlyphStatusErrorOnLight
                             : emptyState.kind === "no-results" ? App.UiIcons.couchGlyphSearchOnLight
                             : emptyState.kind === "no-matches" ? App.UiIcons.couchGlyphFilterOnLight
                             : App.UiIcons.couchGlyphLibraryOnLight
            }
            Label {
                objectName: "couchLibraryEmptyTitle"
                Layout.fillWidth: true
                text: emptyState.kind === "loading" ? qsTr("Scanning game libraries…")
                      : emptyState.kind === "error" ? qsTr("Library scan failed")
                      : emptyState.kind === "no-results" ? qsTr("No games match: %1").arg(page.searchQuery)
                      : emptyState.kind === "no-matches" ? qsTr("No games match these filters")
                      : qsTr("No games found")
                color: App.Theme.text
                font.pixelSize: 30 * page.fitScale
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }
            Label {
                objectName: "couchLibraryEmptyMessage"
                Layout.fillWidth: true
                text: emptyState.kind === "loading" ? qsTr("Games appear here as soon as local launcher data is read.")
                      : emptyState.kind === "error" ? App.I18n.scanMessage(page.scanMessage)
                      : emptyState.kind === "no-results" ? qsTr("Check the spelling or search for a shorter part of the name.")
                      : emptyState.kind === "no-matches" ? qsTr("Choose All or turn off a filter to show the remaining games.")
                      : qsTr("No installed Steam, Heroic, Lutris or custom games were detected. Library folders can be added in Settings.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchBodySize * page.fitScale
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // ---- Footer: real position (hints from CouchMain sit at the right) --------------
    Item {
        id: footerRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: bottomNav.top
        anchors.bottomMargin: App.Theme.couchSpaceS * page.fitScale
        height: 52 * page.fitScale
        Label {
            objectName: "couchLibraryPosition"
            visible: page.viewState === "grid" && page.selectedIndex >= 0
            anchors.left: parent.left
            anchors.leftMargin: gridFrame.x + page.cardPad
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("%1 / %2").arg(page.selectedIndex + 1).arg(page.filteredGames.length)
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.couchLabelSize * page.fitScale
            font.weight: Font.DemiBold
        }
    }

    // ---- Global section navigation (focus zone 2) ------------------------------------
    CouchBottomNav {
        id: bottomNav
        objectName: "couchLibraryNavigation"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin
        anchors.bottomMargin: page.bottomNavMargin
        height: implicitHeight
        couchScale: page.fitScale
        model: page.libraryTiles
        activeIndex: 0
        currentIndex: page.selectedTile
        navFocused: page.focusZone === 2 && !page.keyboardOpen
        onActivated: function(index) {
            page.focusZone = 2
            page.selectedTile = index
            page.playSemanticSound(page.activateTile() ? "confirm" : "error")
        }
    }

    // Existing Couch on-screen keyboard, reused for search.
    CouchOnScreenKeyboard {
        id: searchKeyboard
        anchors.fill: parent
        couchScale: page.couchScale
        buttonHints: page.controller && page.controller.gamepadButtonHints
                     ? page.controller.gamepadButtonHints : ({})
        keyboardInput: !(page.controller && page.controller.activeController
                         && page.controller.activeController.name)
        onSemanticSound: function(kind) { page.playSemanticSound(kind) }
        onAccepted: function(value) {
            var query = String(value || "").trim()
            if (query !== page.searchQuery) {
                page.searchQuery = query
                page.filterChanged()
            }
            if (page.navigation)
                page.navigation.closeModal()
            Qt.callLater(page.restoreActiveFocus)
        }
        onCancelled: {
            if (page.navigation)
                page.navigation.closeModal()
            Qt.callLater(page.restoreActiveFocus)
        }
    }
}
