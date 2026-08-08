import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
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
                Layout.preferredWidth: page.width < 980 ? 168 : 252
                Layout.preferredHeight: page.width < 980 ? 105 : 142
                title: page.value(["name", "title"], qsTr("Game"))
                launcher: page.value(["launcher"], qsTr("Library"))
                artworkSource: page.value([
                    "effectiveArtworkUrl",
                    "headerArtwork",
                    "headerArtworkUrl",
                    "fallbackArtwork",
                    "portraitArtwork",
                    "cover"
                ], "")
                artworkFillMode: Image.PreserveAspectFit
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
                        text: page.value(["status"], qsTr("Ready"))
                        status: text
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
                        value: page.value(["savedSpace"], "0 GB")
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
                iconText: "▶"
                kind: "primary"
                enabled: Boolean(page.hasSelection && page.launchAllowed)
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
                model: [
                    qsTr("Overview"),
                    qsTr("Storage"),
                    qsTr("Graphics Remaster"),
                    qsTr("Optimization"),
                    qsTr("MangoHud")
                ]

                delegate: TabButton {
                    id: tabButton
                    required property int index
                    required property string modelData
                    width: Math.max(92, (tabBar.width - tabBar.spacing * 4) / 5)
                    height: tabBar.height
                    implicitWidth: 96
                    implicitHeight: height
                    text: modelData
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
                if (tabBar.currentIndex === 2) return graphicsComponent
                if (tabBar.currentIndex === 3) return optimizationComponent
                if (tabBar.currentIndex === 4) return mangoHudComponent
                return overviewComponent
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

    Component {
        id: overviewComponent
        OverviewTab {
            controller: page.controller
            gameData: page.gameData
            tasksData: page.tasksData
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
        id: graphicsComponent
        GraphicsTab {
            controller: page.controller
            gameData: page.gameData
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
        }
    }

    Component {
        id: optimizationComponent
        OptimizationTab {
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
            onToastRequested: function(message, tone) { page.toastRequested(message, tone) }
        }
    }

}
