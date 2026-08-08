pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../dialogs"
import ".." as App

Item {
    id: page

    property var controller
    property var gamesData: controller && controller.games ? controller.games : []
    readonly property var incrementalGamesModel: controller
                                                 && controller.gamesModel
                                               ? controller.gamesModel : null
    property var libraryCompressionData: controller
                                             && controller.compressionLibrarySummaries
                                           ? controller.compressionLibrarySummaries : []
    property bool gridMode: true
    property bool librariesExpanded: false
    property string pendingForgetPath: ""
    readonly property bool filterPopupOpen: launcherFilter.menuVisible
                                              || filesystemFilter.menuVisible
                                              || sortFilter.menuVisible
    readonly property bool demoMode: Boolean(controller && controller.demoMode)
    readonly property bool isScanning: Boolean(controller && controller.isScanning)
    readonly property bool steamFound: Boolean(controller && controller.steamFound)
    readonly property string scanStatus: controller && controller.libraryScanStatus
                                                 ? String(controller.libraryScanStatus) : "idle"
    readonly property string scanMessage: controller && controller.libraryScanMessage
                                                  ? String(controller.libraryScanMessage) : ""
    readonly property int visibleGameCount: gamesData ? gamesData.length : 0
    readonly property int renderedGameCount: incrementalGamesModel
                                             ? Number(incrementalGamesModel.count)
                                             : filteredGames.length
    readonly property int availableVisibleGameCount: {
        var count = 0
        var source = gamesData || []
        for (var i = 0; i < source.length; ++i) {
            if (Boolean(gameValue(source[i], ["libraryAvailable", "library_available"], true)))
                count++
        }
        return count
    }
    readonly property int cachedUnavailableGameCount: Math.max(
                                                          0,
                                                          visibleGameCount
                                                          - availableVisibleGameCount)
    signal toastRequested(string message, string tone)

    function logGamesLifecycle(event, details) {
        console.info("GameForge Games lifecycle"
                     + " event=" + String(event)
                     + " visible=" + (visible ? "true" : "false")
                     + " mode=" + (gridMode ? "grid" : "list")
                     + " games=" + visibleGameCount
                     + " filtered=" + filteredGames.length
                     + (details ? " " + String(details) : ""))
    }

    function syncIncrementalFilter() {
        if (!incrementalGamesModel || !incrementalGamesModel.setFilters)
            return
        incrementalGamesModel.setFilters(
                    searchField.text,
                    launcherFilter.currentIndex > 0
                        ? launcherFilter.currentText : "",
                    filesystemFilter.currentIndex > 0
                        ? filesystemFilter.currentText : "",
                    sortFilter.currentIndex)
    }

    WheelHandler {
        id: pageWheelHandler
        objectName: "gamesPageWheelHandler"
        target: null
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: function(event) {
            if (page.filterPopupOpen || App.PopupCoordinator.popupOpen) {
                event.accepted = true
                return
            }
            var rawDelta = event.pixelDelta.y !== 0
                           ? event.pixelDelta.y : event.angleDelta.y
            var maximum = Math.max(0, pageScroll.contentHeight - pageScroll.height)
            pageScroll.contentY = Math.max(
                                      0,
                                      Math.min(maximum,
                                               pageScroll.contentY - rawDelta))
            event.accepted = true
        }
    }

    onVisibleChanged: {
        if (!visible)
            App.PopupCoordinator.closeActive()
        logGamesLifecycle(visible ? "page_shown" : "page_hidden", "")
    }
    onGridModeChanged: logGamesLifecycle("view_mode_changed", "")
    onFilteredGamesChanged: logGamesLifecycle("filter_result_changed", "")
    Component.onCompleted: {
        syncIncrementalFilter()
        logGamesLifecycle("page_started", "")
    }
    Component.onDestruction: App.PopupCoordinator.closeActive()

    Connections {
        target: page.controller || null
        ignoreUnknownSignals: true

        function onGamesModelRefreshed(generation, reason, count) {
            page.logGamesLifecycle(
                        "full_model_refresh",
                        "generation=" + Number(generation)
                        + " reason=" + String(reason)
                        + " count=" + Number(count))
        }
    }

    function gameValue(game, keys, fallback) {
        var source = game || {}
        for (var i = 0; i < keys.length; ++i) {
            var value = source[keys[i]]
            if (value !== undefined && value !== null && value !== "")
                return value
        }
        return fallback
    }

    function buildOptions(keys, availableOnly) {
        var result = [qsTr("All")]
        var known = ({})
        var source = gamesData || []
        for (var i = 0; i < source.length; ++i) {
            if (availableOnly === true
                    && !Boolean(gameValue(source[i],
                                          ["libraryAvailable",
                                           "library_available"], true)))
                continue
            var value = String(gameValue(source[i], keys, ""))
            if (value.length > 0 && !known[value]) {
                known[value] = true
                result.push(value)
            }
        }
        return result
    }

    readonly property var launcherOptions: buildOptions(["launcher", "provider"], false)
    // A disconnected cached installation is useful in the game list, but its
    // stale filesystem must not advertise a currently usable filter.
    readonly property var filesystemOptions: buildOptions(
                                                 ["filesystem", "fileSystem",
                                                  "file_system"], true)
    readonly property var filteredGames: {
        var query = searchField.text.trim().toLowerCase()
        var launcher = launcherFilter.currentText
        var filesystem = filesystemFilter.currentText
        var result = []
        var source = gamesData || []

        for (var i = 0; i < source.length; ++i) {
            var game = source[i]
            var name = String(gameValue(game, ["name", "title"], "")).toLowerCase()
            var path = String(gameValue(game, ["path", "location", "installPath", "install_path"], "")).toLowerCase()
            var gameLauncher = String(gameValue(game, ["launcher", "provider"], ""))
            var gameFs = String(gameValue(game, ["filesystem", "fileSystem", "file_system"], ""))
            if (query.length > 0 && name.indexOf(query) < 0 && path.indexOf(query) < 0)
                continue
            if (launcherFilter.currentIndex > 0 && gameLauncher !== launcher)
                continue
            if (filesystemFilter.currentIndex > 0 && gameFs !== filesystem)
                continue
            result.push(game)
        }

        var sortMode = sortFilter.currentIndex
        result.sort(function(a, b) {
            var aName = String(gameValue(a, ["name", "title"], ""))
            var bName = String(gameValue(b, ["name", "title"], ""))
            if (sortMode === 1)
                return bName.localeCompare(aName)
            if (sortMode === 2) {
                var aBytes = Number(gameValue(a, ["sizeBytes", "size_bytes", "logicalSizeGb", "logical_size_gb"], 0))
                var bBytes = Number(gameValue(b, ["sizeBytes", "size_bytes", "logicalSizeGb", "logical_size_gb"], 0))
                return bBytes - aBytes
            }
            if (sortMode === 3) {
                var aSaved = Number(gameValue(a, ["savedBytes", "saved_bytes", "savedSpaceGb", "saved_space_gb"], 0))
                var bSaved = Number(gameValue(b, ["savedBytes", "saved_bytes", "savedSpaceGb", "saved_space_gb"], 0))
                return bSaved - aSaved
            }
            return aName.localeCompare(bName)
        })
        return result
    }

    function openGame(gameId) {
        if (controller && controller.openGame)
            controller.openGame(gameId)
    }

    function formatBytes(raw) {
        if (raw === undefined || raw === null || raw === "")
            return qsTr("Not available")
        var bytes = Number(raw)
        if (!isFinite(bytes) || bytes < 0)
            return qsTr("Not available")
        var units = [qsTr("B"), qsTr("KiB"), qsTr("MiB"), qsTr("GiB"), qsTr("TiB")]
        var unit = 0
        while (bytes >= 1024 && unit < units.length - 1) {
            bytes /= 1024
            unit++
        }
        return bytes.toFixed(unit === 0 ? 0 : 2) + " " + units[unit]
    }

    function formatMeasurementDate(raw) {
        if (!raw)
            return qsTr("Not available")
        var parsed = new Date(String(raw))
        return isNaN(parsed.getTime()) ? qsTr("Not available")
                                        : Qt.formatDateTime(parsed,
                                                            "dd.MM.yyyy, HH:mm")
    }

    function discoveryStatusText() {
        if (page.isScanning)
            return qsTr("Scanning local Steam libraries…")
        if (page.scanStatus === "error")
            return App.I18n.scanMessage(page.scanMessage)
        if (page.scanStatus === "ready")
            return qsTr("Steam library scan complete")
        return page.scanMessage.length > 0
               ? App.I18n.scanMessage(page.scanMessage)
               : qsTr("Local Steam discovery is ready")
    }

    function hasPartialLibraryMeasurement() {
        var source = libraryCompressionData || []
        for (var i = 0; i < source.length; ++i) {
            if (source[i].fullyMeasured !== true)
                return true
        }
        return false
    }

    Flickable {
        id: pageScroll
        objectName: "gamesPageFlickable"
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: pageContent.implicitHeight + 2 * App.Theme.contentPadding
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: !page.filterPopupOpen
        ScrollBar.vertical: ScrollBar {
            objectName: "gamesPageScrollBar"
            policy: ScrollBar.AsNeeded
        }

        function clampOffset() {
            var maximum = Math.max(0, contentHeight - height)
            contentY = Math.max(0, Math.min(contentY, maximum))
        }

        onContentHeightChanged: Qt.callLater(clampOffset)
        onHeightChanged: Qt.callLater(clampOffset)
        onVisibleChanged: if (visible) Qt.callLater(clampOffset)

        ColumnLayout {
            id: pageContent
            objectName: "gamesPageContent"
            x: App.Theme.contentPadding
            y: App.Theme.contentPadding
            width: Math.max(1, pageScroll.width - 2 * App.Theme.contentPadding)
            spacing: App.Theme.spacingLarge

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Games")
            subtitle: qsTr("Manage storage, graphics, and launch profiles for your library")

            AppButton {
                text: page.isScanning ? qsTr("Scanning…") : qsTr("Refresh")
                iconText: "↻"
                kind: "secondary"
                compact: page.width < 1040
                enabled: !page.isScanning
                onClicked: {
                    if (page.controller && page.controller.requestLibraryScan)
                        page.controller.requestLibraryScan(
                                    "games_header", "", "manual")
                    else if (page.controller && page.controller.refreshGames)
                        page.controller.refreshGames()
                }
            }

            AppButton {
                text: qsTr("Add game")
                iconText: "+"
                kind: "primary"
                visible: page.demoMode
                onClicked: {
                    if (page.controller && page.controller.addManualGame)
                        page.controller.addManualGame()
                }
            }
        }

        SurfaceCard {
            visible: page.isScanning || !page.demoMode || page.scanStatus === "error"
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 54 : 0
            padding: 12

            contentItem: RowLayout {
                spacing: 10

                BusyIndicator {
                    visible: page.isScanning
                    running: visible
                    Layout.preferredWidth: 24
                    Layout.preferredHeight: 24
                }

                Label {
                    text: page.isScanning ? "↻" : page.scanStatus === "error" ? "!" : "●"
                    visible: !page.isScanning
                    color: page.scanStatus === "error" ? App.Theme.danger
                                                         : page.scanStatus === "ready" ? App.Theme.success
                                                                                       : App.Theme.textMuted
                    font.pixelSize: 15
                    font.weight: Font.Bold
                }

                Label {
                    objectName: "gamesDiscoveryStatus"
                    Layout.fillWidth: true
                    text: page.discoveryStatusText()
                    color: page.scanStatus === "error" ? App.Theme.danger : App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontCaption
                    elide: Text.ElideRight
                }

                StatusBadge {
                    visible: !page.demoMode && page.steamFound
                    text: qsTr("Steam detected")
                    status: "Ready"
                }

                StatusBadge {
                    visible: page.demoMode
                    text: qsTr("Demo")
                    status: "Not checked"
                }
            }
        }

        SurfaceCard {
            objectName: "libraryCompressionSummary"
            visible: page.libraryCompressionData
                     && page.libraryCompressionData.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: visible
                                    ? libraryCompressionColumn.implicitHeight + 24 : 0
            padding: 12

            contentItem: ColumnLayout {
                id: libraryCompressionColumn
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Library storage status")
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBodyLarge
                        font.weight: Font.Bold
                    }
                    StatusBadge {
                        text: page.hasPartialLibraryMeasurement()
                              ? qsTr("Partial measurement")
                              : qsTr("Full measurement")
                        status: page.hasPartialLibraryMeasurement()
                                ? "warning" : "completed"
                    }
                    AppButton {
                        objectName: "toggleLibraryDetailsButton"
                        compact: true
                        kind: "ghost"
                        text: page.librariesExpanded
                              ? qsTr("Hide libraries")
                              : qsTr("View libraries")
                        onClicked: page.librariesExpanded = !page.librariesExpanded
                    }
                }

                RowLayout {
                    objectName: "compactLibrarySummary"
                    visible: !page.librariesExpanded
                    Layout.fillWidth: true
                    spacing: 8

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("%1 Steam libraries · source: compsize")
                              .arg((page.libraryCompressionData || []).length)
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        elide: Text.ElideRight
                    }
                    Label {
                        text: qsTr("Open for measured totals")
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                    }
                }

                Repeater {
                    model: page.libraryCompressionData || []

                    delegate: ColumnLayout {
                        id: librarySummaryRow
                        required property var modelData
                        Layout.fillWidth: true
                        visible: page.librariesExpanded
                        spacing: 6

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            color: App.Theme.border
                        }

                        Label {
                            Layout.fillWidth: true
                            text: String(librarySummaryRow.modelData.libraryPath || "")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            font.family: "monospace"
                            elide: Text.ElideMiddle
                            ToolTip.visible: libraryPathHover.hovered && truncated
                            ToolTip.text: text
                            HoverHandler { id: libraryPathHover }
                        }

                        RowLayout {
                            Layout.fillWidth: true

                            Label {
                                objectName: librarySummaryRow.modelData.fullyMeasured === true
                                            ? "fullLibraryScope"
                                            : "partialLibraryScope"
                                Layout.fillWidth: true
                                text: librarySummaryRow.modelData.fullyMeasured === true
                                      ? qsTr("Whole library")
                                      : qsTr("Measured games total")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.DemiBold
                            }

                            StatusBadge {
                                objectName: librarySummaryRow.modelData.fullyMeasured === true
                                            ? "fullLibraryStatus"
                                            : "partialLibraryStatus"
                                text: librarySummaryRow.modelData.fullyMeasured === true
                                      ? qsTr("Full measurement")
                                      : qsTr("Partial measurement")
                                status: librarySummaryRow.modelData.fullyMeasured === true
                                        ? "completed" : "warning"
                            }

                            AppButton {
                                visible: librarySummaryRow.modelData.canIgnoreLibrary === true
                                         || librarySummaryRow.modelData.canForgetLibrary === true
                                compact: true
                                kind: "ghost"
                                text: qsTr("Forget in GameForge")
                                onClicked: {
                                    page.pendingForgetPath = String(
                                        librarySummaryRow.modelData.libraryPath || "")
                                    forgetLibraryDialog.ask(
                                        qsTr("Forget this library in GameForge?"),
                                        qsTr("Games and updates from this path will be hidden only in GameForge. Steam configuration and user files will not be changed. You can restore the library in Settings.\n\n%1")
                                            .arg(page.pendingForgetPath),
                                        qsTr("Forget in GameForge"),
                                        true,
                                        page.pendingForgetPath)
                                }
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.width >= 1240 ? 6
                                   : page.width >= 780 ? 3 : 2
                            rowSpacing: 8
                            columnSpacing: 12

                            LabeledValue {
                                objectName: "librarySummaryLogical"
                                Layout.fillWidth: true
                                label: qsTr("Logical (compsize)")
                                value: page.formatBytes(
                                           librarySummaryRow.modelData.uncompressedBytes)
                            }
                            LabeledValue {
                                objectName: "librarySummaryPhysical"
                                Layout.fillWidth: true
                                label: qsTr("Physical (compsize)")
                                value: page.formatBytes(
                                           librarySummaryRow.modelData.diskUsageBytes)
                            }
                            LabeledValue {
                                objectName: "librarySummarySaving"
                                Layout.fillWidth: true
                                label: qsTr("Current saving")
                                value: page.formatBytes(
                                           librarySummaryRow.modelData.currentSavingBytes)
                            }
                            LabeledValue {
                                objectName: "librarySummaryEffect"
                                Layout.fillWidth: true
                                label: qsTr("Compression effect")
                                value: librarySummaryRow.modelData.savingPercent === null
                                       || librarySummaryRow.modelData.savingPercent === undefined
                                       ? qsTr("Not available")
                                       : Number(librarySummaryRow.modelData.savingPercent).toFixed(2) + "%"
                            }
                            LabeledValue {
                                objectName: "librarySummaryMeasuredGames"
                                Layout.fillWidth: true
                                label: qsTr("Measured games")
                                value: qsTr("%1 of %2")
                                       .arg(Number(librarySummaryRow.modelData.measuredGameCount || 0))
                                       .arg(Number(librarySummaryRow.modelData.gameCount || 0))
                            }
                            LabeledValue {
                                objectName: librarySummaryRow.modelData.fullyMeasured === true
                                            ? "fullLibraryLastMeasurement"
                                            : "partialLibraryLastMeasurement"
                                visible: librarySummaryRow.modelData.fullyMeasured === true
                                Layout.fillWidth: true
                                label: qsTr("Last full measurement")
                                value: page.formatMeasurementDate(
                                           librarySummaryRow.modelData.lastFullMeasurementAt)
                            }
                        }
                    }
                }

                Label {
                    visible: page.librariesExpanded
                    Layout.fillWidth: true
                    text: qsTr("Compression effect uses only compsize Uncompressed and Disk Usage; scanner and Steam sizes are excluded.")
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                    wrapMode: Text.WordWrap
                }
            }
        }

        SurfaceCard {
            objectName: "gamesFilterCard"
            Layout.fillWidth: true
            Layout.preferredHeight: filterFlow.implicitHeight + 24
            padding: 12
            elevated: true

            contentItem: Flow {
                id: filterFlow
                width: parent.width
                spacing: 10

                AppTextField {
                    id: searchField
                    width: page.width < 920 ? Math.max(220, filterFlow.width - 2) : Math.max(240, Math.min(360, filterFlow.width * 0.3))
                    placeholderText: qsTr("Search games or locations…")
                    leadingSymbol: "⌕"
                    Accessible.name: qsTr("Search games")
                    onTextChanged: Qt.callLater(page.syncIncrementalFilter)
                }

                AppComboBox {
                    id: launcherFilter
                    objectName: "launcherFilter"
                    menuObjectName: "launcherFilterPopup"
                    width: 146
                    model: page.launcherOptions
                    Accessible.name: qsTr("Filter by launcher")
                    onCurrentIndexChanged: Qt.callLater(page.syncIncrementalFilter)
                }

                AppComboBox {
                    id: filesystemFilter
                    objectName: "filesystemFilter"
                    menuObjectName: "filesystemFilterPopup"
                    width: 150
                    model: page.filesystemOptions
                    Accessible.name: qsTr("Filter by filesystem")
                    onCurrentIndexChanged: Qt.callLater(page.syncIncrementalFilter)
                }

                AppComboBox {
                    id: sortFilter
                    objectName: "sortFilter"
                    menuObjectName: "sortFilterPopup"
                    width: 156
                    model: [qsTr("Name A-Z"), qsTr("Name Z-A"), qsTr("Largest first"), qsTr("Most saved")]
                    Accessible.name: qsTr("Sort games")
                    onCurrentIndexChanged: Qt.callLater(page.syncIncrementalFilter)
                }

                Rectangle {
                    width: 86
                    height: App.Theme.controlHeight
                    radius: App.Theme.radiusSmall
                    color: App.Theme.input
                    border.width: 1
                    border.color: App.Theme.border

                    Row {
                        anchors.fill: parent
                        anchors.margins: 3
                        spacing: 2

                        Button {
                            id: gridViewButton
                            width: 39
                            height: 36
                            text: "▦"
                            focusPolicy: Qt.StrongFocus
                            Accessible.name: qsTr("Grid view")
                            onClicked: page.gridMode = true
                            contentItem: Label {
                                text: gridViewButton.text
                                color: page.gridMode ? App.Theme.accent : App.Theme.textMuted
                                font.pixelSize: 18
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 6
                                color: page.gridMode ? App.Theme.accentSoft : gridViewButton.hovered ? App.Theme.surfaceHover : "transparent"
                                border.width: gridViewButton.visualFocus ? 2 : 0
                                border.color: App.Theme.accent
                            }
                        }

                        Button {
                            id: listViewButton
                            width: 39
                            height: 36
                            text: "☷"
                            focusPolicy: Qt.StrongFocus
                            Accessible.name: qsTr("List view")
                            onClicked: page.gridMode = false
                            contentItem: Label {
                                text: listViewButton.text
                                color: !page.gridMode ? App.Theme.accent : App.Theme.textMuted
                                font.pixelSize: 18
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 6
                                color: !page.gridMode ? App.Theme.accentSoft : listViewButton.hovered ? App.Theme.surfaceHover : "transparent"
                                border.width: listViewButton.visualFocus ? 2 : 0
                                border.color: App.Theme.accent
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                objectName: "visibleGamesCount"
                text: qsTr("Visible games: %1").arg(page.visibleGameCount)
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.fontCaption
                font.weight: Font.DemiBold
            }

            Label {
                objectName: "availableGamesCount"
                text: qsTr("Games in available libraries: %1").arg(
                          page.availableVisibleGameCount)
                color: App.Theme.textMuted
                font.pixelSize: App.Theme.fontCaption
            }

            Label {
                objectName: "cachedUnavailableGamesCount"
                visible: page.cachedUnavailableGameCount > 0
                text: qsTr("Cached from disconnected libraries: %1").arg(
                          page.cachedUnavailableGameCount)
                color: App.Theme.warning
                font.pixelSize: App.Theme.fontCaption
            }

            Item { Layout.fillWidth: true }

            Label {
                visible: searchField.text.length > 0 || launcherFilter.currentIndex > 0 || filesystemFilter.currentIndex > 0
                text: qsTr("Matching filters: %1").arg(page.filteredGames.length)
                color: App.Theme.accent
                font.pixelSize: App.Theme.fontCaption
            }
        }

        Loader {
            id: gamesLayoutLoader
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            sourceComponent: page.renderedGameCount === 0 ? emptyComponent
                                                                  : page.gridMode ? gridComponent : listComponent
        }

        Item {
            objectName: "gamesBottomSpacer"
            Layout.fillWidth: true
            Layout.preferredHeight: 12
        }
        }
    }

    Component {
        id: gridComponent

        Item {
            implicitHeight: grid.implicitHeight

        Grid {
            id: grid
            objectName: "gamesGridView"
            width: parent.width
            property real minimumCardWidth: 232
            property real maximumCardWidth: 280
            property real horizontalGap: 16
            property real verticalGap: 16
            property int columnCount: Math.max(
                                          1,
                                          Math.floor(width
                                                     / (minimumCardWidth + horizontalGap)))
            property real cellWidth: width / columnCount
            property real cardWidth: Math.max(
                                         1,
                                         Math.min(maximumCardWidth,
                                                  cellWidth - horizontalGap))
            property real cellHeight: Math.ceil(cardWidth * 1.5 + 196)
            columns: columnCount
            columnSpacing: 0
            rowSpacing: verticalGap

            Repeater {
                model: page.incrementalGamesModel || page.filteredGames

            delegate: Item {
                id: gridCell
                objectName: "gamesGridCell"
                required property var modelData
                width: grid.cellWidth
                height: grid.cellHeight
                clip: true

                GameGridCard {
                    width: grid.cardWidth
                    height: implicitHeight
                    anchors.top: parent.top
                    anchors.horizontalCenter: parent.horizontalCenter
                    gameData: gridCell.modelData
                    onOpenRequested: function(gameId) { page.openGame(gameId) }
                }
            }
            }
        }
        }
    }

    Component {
        id: listComponent

        Item {
            implicitHeight: listColumn.implicitHeight

        Column {
            id: listColumn
            objectName: "gamesListView"
            width: parent.width
            spacing: 10

            Repeater {
                model: page.incrementalGamesModel || page.filteredGames

            delegate: GameListRow {
                id: listRowDelegate
                required property var modelData
                width: listColumn.width
                gameData: listRowDelegate.modelData
                onOpenRequested: function(gameId) { page.openGame(gameId) }
            }
            }
        }
        }
    }

    Component {
        id: emptyComponent

        EmptyState {
            implicitHeight: 280
            property bool filtersHideGames: page.gamesData
                                            && page.gamesData.length > 0
            title: filtersHideGames ? qsTr("No games match the filters")
                   : page.isScanning ? qsTr("Scanning Steam libraries…")
                   : page.scanStatus === "error" ? qsTr("Library scan failed")
                   : !page.demoMode && !page.steamFound && page.scanStatus === "steam-not-found"
                     ? qsTr("Steam was not found")
                   : !page.demoMode && page.steamFound ? qsTr("No installed Steam games found")
                   : page.demoMode ? qsTr("Your demo library is empty")
                                   : qsTr("Preparing the Steam scan")
            message: filtersHideGames
                     ? qsTr("Try a different search, launcher, or filesystem filter.")
                     : page.scanStatus === "error" ? App.I18n.scanMessage(page.scanMessage)
                     : page.isScanning ? qsTr("Games appear here as soon as local metadata is ready.")
                     : App.I18n.scanMessage(page.scanMessage)
            symbol: page.scanStatus === "error" ? "!" : page.isScanning ? "↻" : "▦"
            actionText: filtersHideGames || page.isScanning ? ""
                        : page.demoMode ? qsTr("Add a game") : qsTr("Refresh")
            onActionTriggered: {
                if (page.demoMode && page.controller && page.controller.addManualGame)
                    page.controller.addManualGame()
                else if (page.controller && page.controller.requestLibraryScan)
                    page.controller.requestLibraryScan(
                                "games_empty_state", "", "manual")
                else if (page.controller && page.controller.refreshGames)
                    page.controller.refreshGames()
            }
        }
    }

    ConfirmDialog {
        id: forgetLibraryDialog
        objectName: "forgetLibraryDialog"
        onConfirmed: function(libraryPath) {
            if (page.controller && page.controller.ignoreLibrary)
                page.controller.ignoreLibrary(String(libraryPath || ""))
            page.pendingForgetPath = ""
        }
        onClosed: page.pendingForgetPath = ""
    }
}
