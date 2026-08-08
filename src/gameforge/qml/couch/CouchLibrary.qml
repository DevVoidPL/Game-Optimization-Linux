import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchLibrary"
    property var controller
    property var navigation
    property real couchScale: 1.0
    property var games: controller && controller.games ? controller.games : []
    property int selectedFilter: 0
    property string retainedGameId: ""
    property bool filterBarFocused: false
    readonly property var filters: [
        { "id": "all", "symbol": "▦", "label": qsTr("All") },
        { "id": "ready", "symbol": "✓", "label": qsTr("Ready") },
        { "id": "attention", "symbol": "!", "label": qsTr("Needs attention") },
        { "id": "btrfs", "symbol": "◈", "label": "Btrfs" },
        { "id": "disconnected", "symbol": "×", "label": qsTr("Disconnected library") },
        { "id": "recent", "symbol": "◷", "label": qsTr("Recently played") }
    ]
    readonly property var filteredGames: filterGames()
    signal openGame(string gameId)
    signal backRequested()

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (page.filterBarFocused) {
                var filter = filterRepeater.itemAt(page.selectedFilter)
                if (filter) filter.forceActiveFocus()
            } else {
                gameGrid.forceActiveFocus()
            }
        })
    }

    function boolValue(value) { return value === true }
    function filterGames() {
        var result = []
        var kind = filters[selectedFilter].id
        for (var i = 0; i < games.length; ++i) {
            var game = games[i]
            var status = String(game.status || "").toLowerCase()
            var include = kind === "all"
                    || (kind === "ready" && boolValue(game.launchAllowed))
                    || (kind === "attention"
                        && (status.indexOf("attention") >= 0
                            || status.indexOf("error") >= 0))
                    || (kind === "btrfs"
                        && String(game.filesystem || "").toLowerCase() === "btrfs")
                    || (kind === "disconnected" && !boolValue(game.libraryAvailable))
                    || (kind === "recent" && (game.lastPlayed || game.last_played))
            if (include)
                result.push(game)
        }
        return result
    }

    function stableIds() {
        var ids = []
        for (var i = 0; i < filteredGames.length; ++i)
            ids.push(String(filteredGames[i].id || ""))
        return ids
    }

    function restoreSelection() {
        var ids = stableIds()
        var wanted = navigation ? navigation.reconcileFocus("library", ids)
                                : retainedGameId
        var index = ids.indexOf(wanted)
        if (index < 0)
            index = ids.length > 0 ? 0 : -1
        gameGrid.currentIndex = index
        if (index >= 0) {
            retainedGameId = ids[index]
            gameGrid.positionViewAtIndex(index, GridView.Contain)
        }
    }

    function rememberSelection() {
        if (gameGrid.currentIndex < 0
                || gameGrid.currentIndex >= filteredGames.length)
            return
        retainedGameId = String(filteredGames[gameGrid.currentIndex].id || "")
        if (navigation)
            navigation.rememberFocus("library", retainedGameId,
                                     gameGrid.currentIndex)
    }

    function selectIndex(index) {
        if (filteredGames.length === 0)
            return
        gameGrid.currentIndex = Math.max(0, Math.min(filteredGames.length - 1,
                                                     index))
        gameGrid.positionViewAtIndex(gameGrid.currentIndex, GridView.Contain)
        rememberSelection()
    }

    function openFilters() {
        filterBarFocused = true
        if (navigation)
            navigation.openModal("library-filters", filters[selectedFilter].id)
        restoreActiveFocus()
    }

    function closeFilters() {
        filterBarFocused = false
        if (navigation)
            navigation.closeModal()
        restoreActiveFocus()
    }

    function handleAction(action) {
        var columns = Math.max(1, Math.floor(gameGrid.width / gameGrid.cellWidth))
        if (filterBarFocused) {
            if (action === "Back") {
                closeFilters()
            } else if (action === "NavigateLeft") {
                selectedFilter = Math.max(0, selectedFilter - 1)
            } else if (action === "NavigateRight") {
                selectedFilter = Math.min(filters.length - 1, selectedFilter + 1)
            } else if (action === "NavigateUp") {
                selectedFilter = Math.max(0, selectedFilter - 2)
            } else if (action === "NavigateDown") {
                selectedFilter = Math.min(filters.length - 1, selectedFilter + 2)
            } else if (action === "Confirm" || action === "ContextMenu") {
                closeFilters()
                Qt.callLater(restoreSelection)
            }
            return
        }

        if (action === "Back") {
            backRequested()
        } else if (action === "ContextMenu") {
            openFilters()
        } else if (action === "NavigateLeft") {
            selectIndex(gameGrid.currentIndex - 1)
        } else if (action === "NavigateRight") {
            selectIndex(gameGrid.currentIndex + 1)
        } else if (action === "NavigateUp") {
            if (gameGrid.currentIndex < columns)
                openFilters()
            else
                selectIndex(gameGrid.currentIndex - columns)
        } else if (action === "NavigateDown") {
            selectIndex(gameGrid.currentIndex + columns)
        } else if (action === "PageLeft") {
            selectIndex(gameGrid.currentIndex - columns * 2)
        } else if (action === "PageRight") {
            selectIndex(gameGrid.currentIndex + columns * 2)
        } else if (action === "Confirm" && gameGrid.currentIndex >= 0) {
            var game = filteredGames[gameGrid.currentIndex]
            if (boolValue(game.libraryAvailable))
                openGame(String(game.id || ""))
        }
    }

    onGamesChanged: Qt.callLater(restoreSelection)
    onSelectedFilterChanged: if (!filterBarFocused) Qt.callLater(restoreSelection)
    focus: visible
    Component.onCompleted: {
        Qt.callLater(restoreSelection)
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) {
        Qt.callLater(restoreSelection)
        restoreActiveFocus()
    }

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            height: 330 * page.couchScale
            gradient: Gradient {
                GradientStop { position: 0.0; color: App.Theme.dark ? "#192838" : "#E2EDF4" }
                GradientStop { position: 1.0; color: App.Theme.background }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 56 * page.couchScale
        anchors.rightMargin: 56 * page.couchScale
        anchors.topMargin: 108 * page.couchScale
        anchors.bottomMargin: 90 * page.couchScale
        spacing: 14 * page.couchScale

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 70 * page.couchScale
            spacing: 20 * page.couchScale
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Label {
                    text: qsTr("Game library")
                    color: App.Theme.text
                    font.pixelSize: 38 * page.couchScale
                    font.weight: Font.Bold
                }
                Label {
                    text: qsTr("%1 games shown").arg(page.filteredGames.length)
                    color: App.Theme.textSecondary
                    font.pixelSize: 17 * page.couchScale
                }
            }
            CouchButton {
                id: filterButton
                objectName: "couchLibraryFilters"
                text: "Y  ·  " + qsTr("Filter: %1").arg(page.filters[page.selectedFilter].label)
                couchScale: page.couchScale
                implicitWidth: 300 * page.couchScale
                implicitHeight: 58 * page.couchScale
                font.pixelSize: 18 * page.couchScale
                font.weight: Font.DemiBold
                onClicked: page.openFilters()
                background: Rectangle {
                    radius: 17 * page.couchScale
                    color: App.Theme.surfaceRaised
                    border.width: 2
                    border.color: App.Theme.borderStrong
                }
            }
        }

        GridView {
            id: gameGrid
            objectName: "couchLibraryGrid"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.filteredGames
            readonly property real desiredCellWidth: 250 * page.couchScale
            readonly property int columnCount: Math.max(3,
                Math.floor(width / desiredCellWidth))
            cellWidth: width / columnCount
            cellHeight: 444 * page.couchScale
            reuseItems: true
            boundsBehavior: Flickable.StopAtBounds
            delegate: Item {
                id: cell
                required property var modelData
                required property int index
                width: gameGrid.cellWidth
                height: gameGrid.cellHeight
                readonly property bool selected: gameGrid.currentIndex === index
                readonly property bool activeSelection: selected
                                                        && !page.filterBarFocused

                Rectangle {
                    anchors.centerIn: card
                    width: card.width + 14 * page.couchScale
                    height: card.height + 14 * page.couchScale
                    radius: 23 * page.couchScale
                    color: cell.activeSelection
                           ? Qt.rgba(App.Theme.accent.r, App.Theme.accent.g,
                                     App.Theme.accent.b, 0.26)
                           : "transparent"
                    visible: cell.activeSelection
                }
                Button {
                    id: card
                    objectName: "couchLibraryCard"
                    anchors.centerIn: parent
                    width: cell.width - 18 * page.couchScale
                    height: 420 * page.couchScale
                    focus: cell.activeSelection
                    scale: cell.activeSelection ? 1.045 : 0.98
                    opacity: cell.activeSelection ? 1.0 : 0.82
                    z: cell.activeSelection ? 2 : 1
                    onClicked: {
                        if (gameGrid.currentIndex === cell.index
                                && page.boolValue(cell.modelData.libraryAvailable))
                            page.openGame(String(cell.modelData.id || ""))
                        else
                            page.selectIndex(cell.index)
                    }
                    Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                    Behavior on opacity { NumberAnimation { duration: 140 } }
                    background: Rectangle {
                        radius: 18 * page.couchScale
                        clip: true
                        color: App.Theme.surface
                        border.width: cell.activeSelection ? 4 * page.couchScale : 1
                        border.color: cell.activeSelection ? "white" : App.Theme.border

                        GameArtwork {
                            id: cover
                            objectName: "couchLibraryCover"
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            height: Math.min(parent.height - 82 * page.couchScale,
                                             width * 1.5)
                            gameId: String(cell.modelData.id || "")
                            title: String(cell.modelData.name || "")
                            launcher: String(cell.modelData.launcher || "Steam")
                            artworkSource: cell.modelData.effectiveArtworkUrl
                                           || cell.modelData.portraitArtwork
                                           || cell.modelData.fallbackArtwork || ""
                            artworkFillMode: Image.PreserveAspectCrop
                            cornerRadius: 18 * page.couchScale
                        }
                        Rectangle {
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.top: cover.bottom; anchors.bottom: parent.bottom
                            color: App.Theme.dark ? "#F0141B24" : "#F7FFFFFF"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 13 * page.couchScale
                                anchors.rightMargin: 13 * page.couchScale
                                anchors.topMargin: 8 * page.couchScale
                                anchors.bottomMargin: 8 * page.couchScale
                                spacing: 3 * page.couchScale
                                Label {
                                    Layout.fillWidth: true
                                    text: String(cell.modelData.name || qsTr("Unknown game"))
                                    color: App.Theme.text
                                    font.pixelSize: 17 * page.couchScale
                                    font.weight: Font.Bold
                                    maximumLineCount: 2
                                    wrapMode: Text.Wrap
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: page.boolValue(cell.modelData.libraryAvailable)
                                          ? String(cell.modelData.status || qsTr("Ready"))
                                          : qsTr("Library unavailable")
                                    color: page.boolValue(cell.modelData.libraryAvailable)
                                           ? App.Theme.textSecondary : App.Theme.warning
                                    font.pixelSize: 14 * page.couchScale
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        Rectangle {
                            anchors.fill: parent
                            radius: 18 * page.couchScale
                            color: "transparent"
                            border.width: cell.activeSelection ? 4 * page.couchScale : 1
                            border.color: cell.activeSelection ? "white" : App.Theme.border
                            z: 5
                        }
                    }
                }
            }
            ColumnLayout {
                anchors.centerIn: parent
                visible: gameGrid.count === 0
                objectName: "couchLibraryEmptyState"
                width: Math.min(parent.width - 80 * page.couchScale,
                                720 * page.couchScale)
                spacing: 10 * page.couchScale
                Label {
                    Layout.fillWidth: true
                    text: page.games.length === 0 ? qsTr("No games found")
                                                  : qsTr("No games match this filter")
                    color: App.Theme.text
                    font.pixelSize: 28 * page.couchScale
                    font.weight: Font.Bold
                    horizontalAlignment: Text.AlignHCenter
                }
                Label {
                    Layout.fillWidth: true
                    text: page.games.length === 0
                          ? qsTr("No available Steam library is currently visible.")
                          : qsTr("Choose another filter to show the remaining games.")
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.couchBodySize * page.couchScale
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }
    }

    CouchOverlayFrame {
        id: filterOverlay
        objectName: "couchLibraryFilterOverlay"
        anchors.fill: parent
        visible: page.filterBarFocused
        z: 160
        couchScale: page.couchScale
        maximumWidth: 940 * page.couchScale
        preferredHeight: 650 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 20 * page.couchScale
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Choose a library filter")
                    color: App.Theme.text
                    font.pixelSize: 34 * page.couchScale
                    font.weight: Font.Bold
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("The game grid updates after you confirm the filter.")
                    color: App.Theme.textSecondary
                    font.pixelSize: 18 * page.couchScale
                }
                GridLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    columns: 2
                    rowSpacing: 14 * page.couchScale
                    columnSpacing: 14 * page.couchScale
                    Repeater {
                        id: filterRepeater
                        model: page.filters
                        delegate: CouchButton {
                            id: filterChoice
                            required property var modelData
                            required property int index
                            couchScale: page.couchScale
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            text: modelData.symbol + "   " + modelData.label
                            focus: page.filterBarFocused
                                   && page.selectedFilter === index
                            font.pixelSize: 21 * page.couchScale
                            font.weight: Font.Bold
                            onClicked: { page.selectedFilter = index; page.closeFilters() }
                            background: Rectangle {
                                radius: 18 * page.couchScale
                                color: page.selectedFilter === index
                                       ? App.Theme.surfaceSelected : App.Theme.surface
                                border.width: page.selectedFilter === index
                                              ? 4 * page.couchScale : 1
                                border.color: page.selectedFilter === index
                                              ? App.Theme.accent : App.Theme.border
                                scale: page.selectedFilter === index ? 1.025 : 1.0
                                Behavior on scale { NumberAnimation { duration: 140 } }
                            }
                        }
                    }
                }
        }
    }
}
