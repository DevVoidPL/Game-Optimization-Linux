import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes
import "../components"
import "../dialogs"
import "details"
import ".." as App

Item {
    id: page

    property var controller
    property var gameData: controller && controller.selectedGame ? controller.selectedGame : ({})
    property var tasksData: controller && controller.tasks ? controller.tasks : []
    readonly property bool demoMode: Boolean(controller && controller.demoMode)
    readonly property string gameId: String(value(["id"], ""))
    readonly property bool hasSelection: gameId.length > 0
    readonly property bool launchAllowed: Boolean(value(["launchAllowed"], false))
    readonly property bool analysisAllowed: Boolean(value(["analysisAllowed"], false))
    readonly property bool customManualGame: Boolean(value(["customManualGame"], false))
    // Feature availability from the game's actual data (directory, executable,
    // runner, launcher config), not from its source. Each entry carries a
    // concrete reason when unavailable; the tab stays visible but disabled.
    property var capabilities: ({})
    // Keep compression prominent; launcher setup belongs to the game's overview.
    // Only the selected page is instantiated.
    readonly property var tabCapabilityKeys: ["", "", "optimization", "optiscaler", "mangohud", "optimization"]
    readonly property var tabTitles: [qsTr("Overview"), qsTr("Compression"), qsTr("Optimization"), qsTr("Upscaling & effects"), qsTr("MangoHud"), qsTr("Performance analysis")]
    function refreshCapabilities() {
        page.capabilities = page.controller && page.controller.getGameCapabilities && page.gameId
                            ? (page.controller.getGameCapabilities(page.gameId) || ({})) : ({})
    }
    function tabAvailable(index) {
        var key = page.tabCapabilityKeys[index] || ""
        if (!key) return true
        if (key === "integrations") return !page.customManualGame
        var entry = page.capabilities[key]
        if (entry === undefined) return !page.customManualGame
        return Boolean(entry.available)
    }
    function tabReason(index) {
        var entry = page.capabilities[page.tabCapabilityKeys[index] || ""] || ({})
        return App.I18n.message(String(entry.reason || (page.customManualGame ? qsTr("Optimization is not available for custom games") : "")))
    }
    readonly property var unavailableTabReasons: {
        var lines = []
        var seen = ({})
        for (var i = 1; i < page.tabTitles.length; ++i) {
            var reason = page.tabReason(i)
            if (!page.tabAvailable(i) && reason.length > 0 && !seen[reason]) {
                seen[reason] = true
                lines.push(page.tabTitles[i] + ": " + reason)
            }
        }
        // Tools checked in the environment that starts the game (e.g. the
        // launcher's Flatpak): GameMode / Gamescope inside Optimization.
        var tools = page.capabilities.tools || ({})
        for (var name in tools) {
            var tool = tools[name] || ({})
            if (name !== "mangohud" && !tool.available && String(tool.reason || "").length > 0)
                lines.push(String(tool.label || name) + ": " + App.I18n.message(String(tool.reason)))
        }
        return lines
    }
    // The launcher sandbox is checked in the background; refresh until known.
    property int capabilityPolls: 0
    Timer {
        interval: 1000
        repeat: true
        running: Boolean(page.capabilities.pending) && page.capabilityPolls < 40
        onTriggered: { page.capabilityPolls += 1; page.refreshCapabilities() }
    }
    signal toastRequested(string message, string tone)

    function value(keys, fallback) {
        var source = gameData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function goBack() {
        if (controller && controller.backToGames)
            controller.backToGames()
    }

    onGameIdChanged: { tabBar.currentIndex = 0; page.capabilityPolls = 0; page.scrollPositions = ({}); page.refreshCapabilities() }
    // Scroll position per tab while the same game stays open.
    property var scrollPositions: ({})
    property real pendingScrollRestore: -1
    function restoreScroll() {
        var flick = tabLoader.item && tabLoader.item.scrollFlickable ? tabLoader.item.scrollFlickable : null
        if (!flick || page.pendingScrollRestore < 0) return
        var maxY = Math.max(0, flick.contentHeight - flick.height)
        if (maxY >= page.pendingScrollRestore) {
            flick.contentY = page.pendingScrollRestore
            page.pendingScrollRestore = -1
        }
    }
    onGameDataChanged: page.refreshCapabilities()
    Component.onCompleted: page.refreshCapabilities()

    ColumnLayout {
        visible: page.hasSelection
        anchors.fill: parent
        anchors.margins: App.Theme.contentPadding
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            IconButton {
                Layout.alignment: Qt.AlignTop
                symbol: "‹"
                toolTip: qsTr("Back to Games")
                onClicked: page.goBack()
            }

            GameCover {
                id: headerArtwork
                objectName: "detailsGameArtwork"
                Layout.preferredWidth: page.width < 980 ? 168 : 252
                Layout.preferredHeight: Layout.preferredWidth / (artworkAspectRatio >= 1.4 ? artworkAspectRatio : 16 / 9)
                gameId: page.gameId
                diagnosticViewKind: "details"
                title: page.value(["name", "title"], qsTr("Game"))
                launcher: page.value(["launcher"], qsTr("Library"))
                artworkSource: page.value([
                    "headerArtworkUrl",
                    "headerArtwork",
                    "effectiveArtworkUrl",
                    "fallbackArtwork",
                    "portraitArtwork",
                    "cover"
                ], "")
                artworkFillMode: Image.PreserveAspectFit

                // Cover only the four corner cutouts. Unlike an offscreen shader
                // mask, this also renders with Qt's software backend.
                Shape {
                    id: artworkCorners
                    objectName: "headerArtworkCorners"
                    anchors.fill: parent
                    readonly property real r: App.Theme.radiusMedium
                    ShapePath {
                        strokeColor: "transparent"
                        fillColor: App.Theme.background
                        fillRule: ShapePath.OddEvenFill
                        startX: 0; startY: 0
                        PathLine { x: artworkCorners.width; y: 0 }
                        PathLine { x: artworkCorners.width; y: artworkCorners.height }
                        PathLine { x: 0; y: artworkCorners.height }
                        PathLine { x: 0; y: 0 }
                        PathMove { x: artworkCorners.r; y: 0 }
                        PathLine { x: artworkCorners.width - artworkCorners.r; y: 0 }
                        PathArc { x: artworkCorners.width; y: artworkCorners.r; radiusX: artworkCorners.r; radiusY: artworkCorners.r }
                        PathLine { x: artworkCorners.width; y: artworkCorners.height - artworkCorners.r }
                        PathArc { x: artworkCorners.width - artworkCorners.r; y: artworkCorners.height; radiusX: artworkCorners.r; radiusY: artworkCorners.r }
                        PathLine { x: artworkCorners.r; y: artworkCorners.height }
                        PathArc { x: 0; y: artworkCorners.height - artworkCorners.r; radiusX: artworkCorners.r; radiusY: artworkCorners.r }
                        PathLine { x: 0; y: artworkCorners.r }
                        PathArc { x: artworkCorners.r; y: 0; radiusX: artworkCorners.r; radiusY: artworkCorners.r }
                    }
                }
            }


            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 6

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 9

                    Label {
                        id: detailsGameTitle
                        Layout.fillWidth: true
                        text: page.value(["name", "title"], qsTr("Untitled game"))
                        color: App.Theme.text
                        font.pixelSize: page.width < 980 ? 24 : App.Theme.fontDisplay
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                        ToolTip.visible: nameHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: nameHover }
                    }

                    StatusBadge {
                        text: App.I18n.status(page.value(["status"], "ready"))
                        status: page.value(["status"], "ready")
                    }
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("%1 · %2").arg(page.value(["dataSource", "launcher"], qsTr("Manual")))
                          .arg(page.value(["filesystem"], qsTr("Unknown filesystem")))
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontBody
                    elide: Text.ElideRight
                }

                Label {
                    Layout.fillWidth: true
                    text: page.value(["path", "installPath"], qsTr("Location unavailable"))
                    color: App.Theme.textMuted
                    font.pixelSize: App.Theme.fontCaption
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                    ToolTip.visible: pathHover.hovered && truncated
                    ToolTip.text: text
                    HoverHandler { id: pathHover }
                }

                Label {
                    Layout.fillWidth: true
                    visible: String(page.value(["sizeScanStatus"], "")) === "failed"
                    text: qsTr("Exact size scan incomplete: %1").arg(
                              App.I18n.message(page.value(
                                  ["sizeScanError"], qsTr("some files could not be read"))))
                    color: App.Theme.danger
                    font.pixelSize: App.Theme.fontCaption
                    elide: Text.ElideRight
                    ToolTip.visible: sizeErrorHover.hovered && truncated
                    ToolTip.text: text

                    HoverHandler { id: sizeErrorHover }
                }

                RowLayout {
                    visible: page.width >= 850
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    spacing: 22

                    LabeledValue {
                        objectName: "detailsHeaderScannerSize"
                        Layout.preferredWidth: 105
                        label: qsTr("Scanner file size")
                        value: page.value(["logicalSize", "size"], "-")
                    }

                    LabeledValue {
                        objectName: "detailsHeaderPhysicalSize"
                        Layout.preferredWidth: 105
                        label: qsTr("Current physical usage")
                        value: App.I18n.status(page.value(["physicalSize"], "-"))
                    }

                    LabeledValue {
                        objectName: "detailsHeaderSavedSpace"
                        visible: page.width >= 1080
                        Layout.preferredWidth: 115
                        label: qsTr("Current saving")
                        value: App.I18n.status(page.value(["savedSpace"], "0 GB"))
                    }

                    LabeledValue {
                        visible: page.width >= 1180
                        Layout.preferredWidth: 150
                        label: qsTr("Optimization profile")
                        value: App.I18n.profile(
                                   page.value(["optimizationStatus"], qsTr("Not configured")))
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            AppButton {
                objectName: "launchGameButton"
                Layout.alignment: Qt.AlignTop
                text: page.demoMode ? qsTr("Launch (demo)") : qsTr("Launch")
                iconSource: App.UiIcons.actionLaunch
                kind: "primary"
                enabled: Boolean(page.hasSelection && page.launchAllowed)
                toolTip: enabled ? "" : String(page.value(
                    ["launchUnavailableReason"], qsTr("Launch is unavailable")))
                onClicked: {
                    if (page.controller && page.controller.launchGame)
                        page.controller.launchGame(page.gameId)
                }
            }

            IconButton {
                Layout.alignment: Qt.AlignTop
                symbol: "⋯"
                toolTip: qsTr("More actions")
                onClicked: moreMenu.popup()

                Menu {
                    id: moreMenu

                    MenuItem {
                        text: qsTr("Edit custom game")
                        visible: page.customManualGame
                        onTriggered: manualGameDialog.openForEdit(page.gameId)
                    }

                    MenuItem {
                        text: qsTr("Remove custom game")
                        visible: page.customManualGame
                        onTriggered: removeManualDialog.ask(
                                         qsTr("Remove custom game?"),
                                         qsTr("This removes only the Game Optimization Linux entry. Game files and artwork are not deleted."),
                                         qsTr("Remove"), true, page.gameId)
                    }

                    MenuSeparator { visible: page.customManualGame }

                    MenuItem {
                        text: qsTr("Analyze game")
                        enabled: page.analysisAllowed
                        onTriggered: {
                            if (page.controller && page.controller.analyzeGame)
                                page.controller.analyzeGame(page.gameId)
                        }
                    }

                    MenuItem {
                        text: qsTr("Open task queue")
                        onTriggered: {
                            if (page.controller && page.controller.navigate)
                                page.controller.navigate("tasks")
                        }
                    }
                }
            }
        }

        TabBar {
            id: tabBar
            objectName: "detailsTabBar"
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            implicitWidth: 0
            spacing: 3
            clip: true

            background: Rectangle {
                radius: App.Theme.radiusMedium
                color: App.Theme.surface
                border.width: 1
                border.color: App.Theme.border
            }

            Repeater {
                // A language change updates labels without recreating buttons
                // (and briefly selecting/loading each unrelated page).
                model: page.tabCapabilityKeys.length

                delegate: TabButton {
                    id: tabButton
                    required property int index
                    width: Math.max(92, (tabBar.width - tabBar.spacing * (page.tabTitles.length - 1)) / page.tabTitles.length)
                    height: tabBar.height
                    implicitWidth: 96
                    implicitHeight: height
                    text: page.tabTitles[index]
                    enabled: page.tabAvailable(tabButton.index)
                    ToolTip.visible: !enabled && tabDisabledHover.hovered
                    ToolTip.text: page.tabReason(tabButton.index)
                    Accessible.description: tabButton.enabled ? "" : page.tabReason(tabButton.index)
                    focusPolicy: Qt.StrongFocus

                    contentItem: Label {
                        text: tabButton.text
                        color: tabButton.checked ? App.Theme.text : App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        font.weight: tabButton.checked ? Font.DemiBold : Font.Normal
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        ToolTip.visible: tabHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: tabHover }
                    }

                    HoverHandler { id: tabDisabledHover }

                    background: Rectangle {
                        radius: App.Theme.radiusSmall
                        color: tabButton.checked ? App.Theme.accentSoft
                                                  : tabButton.hovered ? App.Theme.surfaceHover : "transparent"
                        border.width: tabButton.visualFocus ? 2 : 0
                        border.color: App.Theme.accent
                    }
                }
            }
        }

        Loader {
            id: tabLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: {
                if (tabBar.currentIndex === 1) return storageComponent
                if (tabBar.currentIndex === 2) return performanceComponent
                if (tabBar.currentIndex === 3) return optiScalerComponent
                if (tabBar.currentIndex === 4) return mangoHudComponent
                if (tabBar.currentIndex === 5) return diagnosticsComponent
                return overviewComponent
            }
            onLoaded: {
                var saved = page.scrollPositions[String(tabBar.currentIndex)]
                page.pendingScrollRestore = saved === undefined ? -1 : Number(saved)
                page.restoreScroll()
            }
        }
    }

    EmptyState {
        visible: !page.hasSelection
        anchors.fill: parent
        title: qsTr("No game selected")
        message: qsTr("Choose a game from the library to open its details.")
        actionText: qsTr("Back to Games")
        onActionTriggered: page.goBack()
    }

    ManualGameDialog {
        id: manualGameDialog
        objectName: "editManualGameDialog"
        controller: page.controller
    }

    ConfirmDialog {
        id: removeManualDialog
        objectName: "removeManualGameDialog"
        onConfirmed: function(gameId) {
            if (page.controller && page.controller.removeManualGame)
                page.controller.removeManualGame(String(gameId || ""))
        }
    }

    Connections {
        target: tabLoader.item && tabLoader.item.scrollFlickable ? tabLoader.item.scrollFlickable : null
        ignoreUnknownSignals: true
        function onContentYChanged() {
            if (page.pendingScrollRestore >= 0 || !tabLoader.item) return
            var positions = page.scrollPositions
            positions[String(tabBar.currentIndex)] = tabLoader.item.scrollFlickable.contentY
            page.scrollPositions = positions
        }
        function onContentHeightChanged() { page.restoreScroll() }
    }

    Component {
        id: overviewComponent
        OverviewTab {
            controller: page.controller
            gameData: page.gameData
            tasksData: page.tasksData
            capabilities: page.capabilities
            capabilityNotices: page.unavailableTabReasons
            onOptiScalerRequested: tabBar.currentIndex = 3
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
        }
    }

    Component {
        id: storageComponent
        StorageTab {
            controller: page.controller
            gameData: page.gameData
            tasksData: page.tasksData
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
        }
    }

    Component {
        id: performanceComponent
        OptimizationTab {
            section: "performance"
            controller: page.controller
            gameData: page.gameData
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
            onOptiScalerRequested: tabBar.currentIndex = 3
            onIntegrationRequested: tabBar.currentIndex = 0
        }
    }

    Component {
        id: diagnosticsComponent
        OptimizationTab {
            section: "diagnostics"
            controller: page.controller
            gameData: page.gameData
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
            onOptiScalerRequested: tabBar.currentIndex = 3
        }
    }

    Component {
        id: optiScalerComponent
        ScalingEffectsTab {
            controller: page.controller
            gameData: page.gameData
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
        }
    }

    Component {
        id: mangoHudComponent
        MangoHudTab {
            controller: page.controller
            gameData: page.gameData
            onOptimizationRequested: tabBar.currentIndex = 2
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
        }
    }

}
