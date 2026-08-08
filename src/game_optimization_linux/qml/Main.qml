import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "dialogs"
import "." as App

ApplicationWindow {
    id: window

    property var controller: appController
    property bool manuallyCollapsed: false
    property bool closeAfterCompressionCancellation: false
    property bool shuttingDown: false
    readonly property bool compactNavigation: width < 1080
    readonly property string activePage: controller && controller.currentPage
                                         ? String(controller.currentPage) : "games"
    readonly property string applicationName: controller && controller.appName
                                              ? String(controller.appName) : qsTr("Application")
    readonly property string interfaceMode: controller && controller.interfaceMode
                                            ? String(controller.interfaceMode) : "desktop"

    width: 1360
    height: 850
    minimumWidth: 820
    minimumHeight: 620
    visible: true
    title: applicationName
    color: App.Theme.background

    palette.window: App.Theme.background
    palette.windowText: App.Theme.text
    palette.base: App.Theme.input
    palette.text: App.Theme.text
    palette.button: App.Theme.surfaceRaised
    palette.buttonText: App.Theme.text
    palette.highlight: App.Theme.accent
    palette.highlightedText: App.Theme.textOnAccent
    palette.toolTipBase: App.Theme.surfaceRaised
    palette.toolTipText: App.Theme.text

    Binding {
        target: App.Theme
        property: "mode"
        value: window.controller && window.controller.themeMode
               ? String(window.controller.themeMode) : "system"
    }

    function navigate(pageName) {
        App.PopupCoordinator.closeActive()
        if (controller && controller.navigate)
            controller.navigate(pageName)
    }

    function setting(name, fallback) {
        var source = controller && controller.settings ? controller.settings : ({})
        var value = source[name]
        return value === undefined || value === null ? fallback : value
    }

    function synchronizeWindowMode() {
        if (shuttingDown)
            return
        if (interfaceMode === "couch" && Boolean(setting("startCouchModeFullscreen", true)))
            showFullScreen()
        else if (visibility === Window.FullScreen)
            showNormal()
    }

    onInterfaceModeChanged: Qt.callLater(synchronizeWindowMode)
    Component.onCompleted: Qt.callLater(synchronizeWindowMode)

    function prepareForShutdown() {
        if (shuttingDown)
            return
        shuttingDown = true
        App.PopupCoordinator.closeActive()
        closeCompressionDialog.close()
        gamesLoader.active = false
        updatesLoader.active = false
        tasksLoader.active = false
        systemLoader.active = false
        settingsLoader.active = false
        detailsLoader.active = false
        couchLoader.active = false
    }

    onClosing: function(close) {
        var compressionActive = window.controller
                && window.controller.hasActiveCompressionTasks === true
        if (!window.closeAfterCompressionCancellation && compressionActive) {
            close.accepted = false
            closeCompressionDialog.ask(
                qsTr("Compression is still running"),
                qsTr("Closing now will cancel the active compression and verify its state on the next start. You can return to the application or cancel the task and close."),
                qsTr("Cancel task and close"),
                true,
                null)
        }
    }

    function pageSource(pageName) {
        if (pageName === "updates")
            return Qt.resolvedUrl("pages/UpdatesPage.qml")
        if (pageName === "tasks")
            return Qt.resolvedUrl("pages/TasksPage.qml")
        if (pageName === "system")
            return Qt.resolvedUrl("pages/SystemPage.qml")
        if (pageName === "settings")
            return Qt.resolvedUrl("pages/SettingsPage.qml")
        if (pageName === "gameDetails" || pageName === "game" || pageName === "details")
            return Qt.resolvedUrl("pages/GameDetailsPage.qml")
        return Qt.resolvedUrl("pages/GamesPage.qml")
    }

    function pageIndex(pageName) {
        if (pageName === "updates")
            return 1
        if (pageName === "tasks")
            return 2
        if (pageName === "system")
            return 3
        if (pageName === "settings")
            return 4
        if (pageName === "gameDetails" || pageName === "game" || pageName === "details")
            return 5
        return 0
    }

    function activeDesktopLoader() {
        var loaders = [
            gamesLoader,
            updatesLoader,
            tasksLoader,
            systemLoader,
            settingsLoader,
            detailsLoader
        ]
        return loaders[desktopPageStack.currentIndex] || gamesLoader
    }

    function updatesPendingCount() {
        var summary = controller && controller.updatesSummary
                      ? controller.updatesSummary : ({})
        var raw = summary.pendingCount
        if (raw === undefined || raw === null)
            raw = summary.pending_count
        if (raw === undefined || raw === null)
            raw = summary.needsCheckCount
        if (raw === undefined || raw === null)
            raw = summary.needs_check_count
        var count = Number(raw)
        return isFinite(count) && count > 0 ? Math.floor(count) : 0
    }

    StackLayout {
        id: interfaceStack
        anchors.fill: parent
        currentIndex: window.interfaceMode === "couch" ? 1 : 0

        Item {
            id: desktopShell

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Sidebar {
            id: sidebar
            Layout.fillHeight: true
            Layout.preferredWidth: implicitWidth
            collapsed: window.compactNavigation || window.manuallyCollapsed
            currentPage: window.activePage
            appName: window.applicationName
            appVersion: window.controller && window.controller.appVersion
                        ? String(window.controller.appVersion) : "0.1.4-alpha"
            logoSource: window.controller && window.controller.appLogoUrl
                        ? String(window.controller.appLogoUrl) : ""
            updatesPendingCount: window.updatesPendingCount()
            updateStatus: window.controller && window.controller.updateStatus
                          ? String(window.controller.updateStatus) : qsTr("Not checked")
            onNavigateRequested: function(page) { window.navigate(page) }
            onCollapseRequested: window.manuallyCollapsed = !window.manuallyCollapsed
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: App.Theme.background

            StackLayout {
                id: desktopPageStack
                objectName: "desktopPageStack"
                anchors.fill: parent
                currentIndex: window.pageIndex(window.activePage)

                Loader {
                    id: gamesLoader
                    objectName: "gamesPageLoader"
                    asynchronous: false
                    source: Qt.resolvedUrl("pages/GamesPage.qml")
                    onLoaded: if (item && item.hasOwnProperty("controller")) item.controller = window.controller
                }

                Loader {
                    id: updatesLoader
                    objectName: "updatesPageLoader"
                    asynchronous: false
                    source: Qt.resolvedUrl("pages/UpdatesPage.qml")
                    onLoaded: if (item && item.hasOwnProperty("controller")) item.controller = window.controller
                }

                Loader {
                    id: tasksLoader
                    objectName: "tasksPageLoader"
                    asynchronous: false
                    source: Qt.resolvedUrl("pages/TasksPage.qml")
                    onLoaded: if (item && item.hasOwnProperty("controller")) item.controller = window.controller
                }

                Loader {
                    id: systemLoader
                    objectName: "systemPageLoader"
                    asynchronous: false
                    source: Qt.resolvedUrl("pages/SystemPage.qml")
                    onLoaded: if (item && item.hasOwnProperty("controller")) item.controller = window.controller
                }

                Loader {
                    id: settingsLoader
                    objectName: "settingsPageLoader"
                    asynchronous: false
                    source: Qt.resolvedUrl("pages/SettingsPage.qml")
                    onLoaded: if (item && item.hasOwnProperty("controller")) item.controller = window.controller
                }

                Loader {
                    id: detailsLoader
                    objectName: "gameDetailsPageLoader"
                    asynchronous: false
                    source: Qt.resolvedUrl("pages/GameDetailsPage.qml")
                    onLoaded: if (item && item.hasOwnProperty("controller")) item.controller = window.controller
                }
            }

            BusyIndicator {
                anchors.centerIn: parent
                running: window.activeDesktopLoader().status === Loader.Loading
                visible: running
                palette.dark: App.Theme.accent
            }

            Rectangle {
                visible: window.activeDesktopLoader().status === Loader.Error
                anchors.centerIn: parent
                width: Math.min(460, parent.width - 50)
                height: errorColumn.implicitHeight + 36
                radius: App.Theme.radiusLarge
                color: App.Theme.dangerSoft
                border.width: 1
                border.color: App.Theme.danger

                ColumnLayout {
                    id: errorColumn
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 8

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("This page could not be opened")
                        color: App.Theme.danger
                        font.pixelSize: 17
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Return to Games and try again.")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        horizontalAlignment: Text.AlignHCenter
                    }

                    AppButton {
                        Layout.alignment: Qt.AlignHCenter
                        text: qsTr("Back to Games")
                        kind: "primary"
                        onClicked: window.navigate("games")
                    }
                }
            }
        }
    }
        }

        Item {
            id: couchShell

            Loader {
                id: couchLoader
                objectName: "couchModeLoader"
                anchors.fill: parent
                active: window.interfaceMode === "couch"
                asynchronous: false
                source: Qt.resolvedUrl("couch/CouchMain.qml")

                onLoaded: {
                    if (!item)
                        return
                    if (item.hasOwnProperty("controller"))
                        item.controller = window.controller
                    if (item.hasOwnProperty("hideCursor"))
                        item.hideCursor = Boolean(window.setting("hideCursorInCouchMode", true))
                }
                onStatusChanged: {
                    if (status === Loader.Error
                            && window.controller
                            && window.controller.setInterfaceMode)
                        window.controller.setInterfaceMode("desktop")
                }
            }
        }
    }

    Connections {
        target: window.activeDesktopLoader().item
        ignoreUnknownSignals: true

        function onToastRequested(message, tone) {
            toastHost.show(message, tone)
        }
    }

    Connections {
        target: window.controller
        ignoreUnknownSignals: true

        function onToastRequested(message, level) {
            if (window.interfaceMode === "couch")
                toastHost.showSingle(message, level, 4200)
            else
                toastHost.show(message, level)
        }

        function onToastDismissRequested(message) {
            toastHost.dismiss(message)
        }

        function onWindowActionRequested(action) {
            if (action === "minimize")
                window.showMinimized()
            else if (action === "close")
                window.close()
        }
    }

    ConfirmDialog {
        id: closeCompressionDialog
        objectName: "closeCompressionDialog"

        onConfirmed: {
            if (window.controller
                    && typeof window.controller.cancelActiveCompressionTasks === "function")
                window.controller.cancelActiveCompressionTasks()
            window.closeAfterCompressionCancellation = true
            Qt.callLater(window.close)
        }
    }

    ToastHost {
        id: toastHost
        parent: Overlay.overlay
        anchors.fill: parent
        z: 1000
    }

    Shortcut { sequence: "Ctrl+1"; onActivated: window.navigate("games") }
    Shortcut { sequence: "Ctrl+2"; onActivated: window.navigate("tasks") }
    Shortcut { sequence: "Ctrl+3"; onActivated: window.navigate("system") }
    Shortcut { sequence: "Ctrl+4"; onActivated: window.navigate("settings") }
    Shortcut { sequence: "Ctrl+5"; onActivated: window.navigate("updates") }
    Shortcut {
        sequence: "F11"
        onActivated: {
            if (window.controller && window.controller.toggleInterfaceMode)
                window.controller.toggleInterfaceMode()
        }
    }
    Shortcut {
        sequences: [StandardKey.Back]
        enabled: window.activePage === "gameDetails"
        onActivated: {
            if (window.controller && window.controller.backToGames)
                window.controller.backToGames()
        }
    }
}
