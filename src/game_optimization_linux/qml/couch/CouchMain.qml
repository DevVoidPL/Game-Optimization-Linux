import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import ".." as App

FocusScope {
    id: couch
    objectName: "couchMain"
    property var controller: null
    readonly property var navigation: controller && controller.couchNavigation ? controller.couchNavigation : null
    readonly property real couchScale: Math.max(0.82, Math.min(1.8, width / 1920))
    readonly property string pageName: controller && controller.currentPage ? String(controller.currentPage) : "games"
    readonly property var hints: controller && controller.gamepadButtonHints ? controller.gamepadButtonHints : ({})
    property string section: "home"
    property string detailsReturnSection: "home"
    property bool cursorVisible: false
    property bool hideCursor: true
    readonly property bool pageModalOpen: homePage.contextMenuOpen
                                          || libraryPage.filterBarFocused
                                          || detailsPage.confirmationOpen
                                          || detailsPage.mangoHudOverlayOpen
                                          || detailsPage.optimizationOverlayOpen
                                          || updatesPage.confirmationOpen
                                          || tasksPage.cancellationOpen
                                          || settingsPage.keyboardOpen
    readonly property var semanticActions: [
        "NavigateLeft", "NavigateRight", "NavigateUp", "NavigateDown",
        "Confirm", "Back", "SecondaryAction", "MoreActions",
        "PreviousTab", "NextTab", "PageUp", "PageDown",
        "OpenSystemMenu", "ContextAction1", "ContextAction2"
    ]

    function normalizeAction(action) {
        var value = String(action || "")
        if (value === "Accept") value = "Confirm"
        else if (value === "Search" || value === "ContextMenu") value = "MoreActions"
        else if (value === "PreviousSection" || value === "PageLeft") value = "PreviousTab"
        else if (value === "NextSection" || value === "PageRight") value = "NextTab"
        else if (value === "OpenMenu") value = "OpenSystemMenu"
        return semanticActions.indexOf(value) >= 0 ? value : ""
    }

    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }

    function setSection(next, preferredId) {
        var normalized = String(next || "home")
        if (normalized !== section)
            closePageModal()
        section = normalized
        if (navigation)
            navigation.enterScreen(section, String(preferredId || ""))
        Qt.callLater(restoreActivePageFocus)
    }

    function closePageModal() {
        if (homePage.contextMenuOpen)
            homePage.closeContextMenu()
        else if (libraryPage.filterBarFocused)
            libraryPage.closeFilters()
        else if (detailsPage.confirmationOpen)
            detailsPage.closeConfirmation()
        else if (detailsPage.mangoHudOverlayOpen)
            detailsPage.closeMangoHudOverlay()
        else if (detailsPage.optimizationOverlayOpen)
            detailsPage.closeOptimizationOverlay()
        else if (updatesPage.confirmationOpen)
            updatesPage.closeConfirmation()
        else if (settingsPage.keyboardOpen)
            settingsPage.closeKeyboard()
        else if (tasksPage.cancellationOpen) {
            tasksPage.cancellationOpen = false
            tasksPage.cancellationChoice = 0
            if (navigation)
                navigation.closeModal()
        }
    }

    function pageForSection(name) {
        return name === "home" ? homePage
             : name === "library" ? libraryPage
             : name === "narrator" ? narratorPage
             : name === "details" ? detailsPage
             : name === "updates" ? updatesPage
             : name === "tasks" ? tasksPage : settingsPage
    }

    function restoreActivePageFocus() {
        forceActiveFocus()
        var target = pageForSection(section)
        if (target && target.restoreActiveFocus)
            target.restoreActiveFocus()
    }

    function synchronizeControllerSection(target) {
        if (!controller)
            return
        if (target === "home" || target === "library")
            controller.backToGames()
        else if (target !== "details")
            controller.navigate(target)
    }

    function returnToPreviousSection() {
        var target = navigation ? String(navigation.previousScreen() || "home")
                                : "home"
        if (["home", "library", "narrator", "updates", "tasks", "settings"].indexOf(target) < 0)
            target = "home"
        section = target
        synchronizeControllerSection(target)
        Qt.callLater(restoreActivePageFocus)
    }

    function openGameFrom(origin, gameId) {
        detailsReturnSection = origin
        if (navigation)
            navigation.rememberFocus("library", String(gameId || ""), -1)
        if (controller && controller.openGame(String(gameId || "")))
            setSection("details", "tab-overview")
    }

    function leaveDetails() {
        var gameId = String(detailsPage.game.id || "")
        if (navigation) {
            navigation.rememberFocus("library", gameId, -1)
            var previous = String(navigation.previousScreen() || "home")
            if (previous !== "library")
                navigation.enterScreen("library", gameId)
        }
        section = "library"
        if (controller) controller.backToGames()
        Qt.callLater(function() {
            libraryPage.restoreSelection()
            couch.restoreActivePageFocus()
        })
    }

    function syncControllerPage() {
        if (pageName === "gameDetails") setSection("details", "tab-overview")
        else if (pageName === "updates") setSection("updates", "")
        else if (pageName === "tasks") setSection("tasks", "")
        else if (pageName === "narrator") setSection("narrator", "")
        else if (pageName === "settings") setSection("settings", "")
        else if (pageName === "games" && ["home", "library"].indexOf(section) < 0) setSection("home", "")
    }

    function handleAction(rawAction) {
        var rawValue = String(rawAction || "")
        if (rawValue === "ToggleMode" || rawValue === "ToggleDesktopCouch") {
            if (controller && controller.toggleInterfaceMode)
                controller.toggleInterfaceMode()
            return
        }
        var action = normalizeAction(rawValue)
        cursorVisible = false
        if (!action.length) return
        if (systemMenu.visible) {
            systemMenu.handleAction(action)
            return
        }
        if (action === "OpenSystemMenu") {
            if (pageModalOpen) {
                playSemanticSound("error")
                return
            }
            systemMenu.open()
            playSemanticSound("open")
            return
        }
        if (action === "Back" && section === "home" && !pageModalOpen) {
            systemMenu.open()
            playSemanticSound("open")
            return
        }
        var target = pageForSection(section)
        if (target && target.handleAction) {
            target.handleAction(action)
            Qt.callLater(restoreActivePageFocus)
        }
    }

    focus: visible
    Component.onCompleted: { if (visible) forceActiveFocus(); syncControllerPage() }
    onVisibleChanged: if (visible) forceActiveFocus()
    onPageNameChanged: syncControllerPage()
    Keys.onPressed: function(event) {
        var action = ""
        if (event.key === Qt.Key_Up) action = "NavigateUp"
        else if (event.key === Qt.Key_Down) action = "NavigateDown"
        else if (event.key === Qt.Key_Left) action = "NavigateLeft"
        else if (event.key === Qt.Key_Right) action = "NavigateRight"
        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) action = "Confirm"
        else if (event.key === Qt.Key_Escape || event.key === Qt.Key_Backspace) action = "Back"
        else if (event.key === Qt.Key_Menu) action = "OpenSystemMenu"
        else if (event.key === Qt.Key_BracketLeft || event.key === Qt.Key_Q) action = "PreviousTab"
        else if (event.key === Qt.Key_BracketRight || event.key === Qt.Key_E) action = "NextTab"
        else if (event.key === Qt.Key_PageUp) action = "PageUp"
        else if (event.key === Qt.Key_PageDown) action = "PageDown"
        else if (event.key === Qt.Key_X) action = "SecondaryAction"
        else if (event.key === Qt.Key_Y) action = "MoreActions"
        else if (event.key === Qt.Key_F11) {
            if (controller && controller.toggleInterfaceMode)
                controller.toggleInterfaceMode()
            event.accepted = true
            return
        }
        if (action.length > 0) {
            if (couch.navigation)
                couch.navigation.setInputModality("keyboard")
            handleAction(action)
            event.accepted = true
        }
    }

    Connections {
        target: couch.navigation
        ignoreUnknownSignals: true
        function onActionRequested(action) { couch.handleAction(String(action)) }
    }

    StackLayout {
        id: contentStack
        objectName: "couchContentStack"
        anchors.fill: parent
        currentIndex: couch.section === "home" ? 0 : couch.section === "library" ? 1
                      : couch.section === "narrator" ? 2 : couch.section === "details" ? 3
                      : couch.section === "updates" ? 4 : couch.section === "tasks" ? 5 : 6
        CouchHome {
            id: homePage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onOpenGame: function(gameId) { couch.openGameFrom("home", gameId) }
            onOpenLibrary: couch.setSection("library", "")
            onOpenNarrator: { if (couch.controller) couch.controller.navigate("narrator"); couch.setSection("narrator", "") }
            onOpenSettings: { if (couch.controller) couch.controller.navigate("settings"); couch.setSection("settings", "") }
        }
        CouchLibrary {
            id: libraryPage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onOpenGame: function(gameId) { couch.openGameFrom("library", gameId) }
            onBackRequested: couch.returnToPreviousSection()
        }
        CouchNarratorPage {
            id: narratorPage
            controller: couch.controller
            navigation: couch.navigation
            couchScale: couch.couchScale
            onBackRequested: couch.returnToPreviousSection()
        }
        CouchGameDetails {
            id: detailsPage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onBackRequested: couch.leaveDetails()
        }
        CouchUpdates {
            id: updatesPage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onBackRequested: couch.returnToPreviousSection()
            onToastRequested: function(message, tone) { if (couch.controller && couch.controller.showToast) couch.controller.showToast(message, tone) }
        }
        CouchTasks {
            id: tasksPage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onBackRequested: couch.returnToPreviousSection()
        }
        CouchSettings {
            id: settingsPage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onBackRequested: couch.returnToPreviousSection()
        }
    }

    Rectangle {
        id: topBar
        visible: !systemMenu.visible && !homePage.contextMenuOpen
                 && !detailsPage.confirmationOpen && !detailsPage.mangoHudOverlayOpen
                 && !detailsPage.optimizationOverlayOpen
                 && !updatesPage.confirmationOpen
                 && !tasksPage.cancellationOpen
                 && !settingsPage.keyboardOpen
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 92 * couch.couchScale; color: App.Theme.dark ? "#B80B1018" : "#C9F3F6FA"; z: 30
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 50 * couch.couchScale; anchors.rightMargin: 50 * couch.couchScale; spacing: 14 * couch.couchScale
            Rectangle {
                Layout.preferredWidth: 48 * couch.couchScale; Layout.preferredHeight: 48 * couch.couchScale; radius: 13 * couch.couchScale; color: App.Theme.surfaceRaised; clip: true
                Image { id: logoImage; anchors.fill: parent; anchors.margins: 4 * couch.couchScale; source: couch.controller ? String(couch.controller.appLogoUrl || "") : ""; fillMode: Image.PreserveAspectFit; visible: status === Image.Ready }
            }
            ColumnLayout {
                spacing: 0
                Label { text: couch.controller ? String(couch.controller.appName || qsTr("Game Optimization Linux")) : qsTr("Game Optimization Linux"); color: App.Theme.text; font.pixelSize: 22 * couch.couchScale; font.weight: Font.Bold }
                Label { text: couch.section === "home" ? qsTr("Home") : couch.section === "library" ? qsTr("Library") : couch.section === "narrator" ? qsTr("Narrator") : couch.section === "details" ? qsTr("Game details") : couch.section === "updates" ? qsTr("Updates") : couch.section === "tasks" ? qsTr("Tasks") : qsTr("Settings"); color: App.Theme.textSecondary; font.pixelSize: 15 * couch.couchScale }
            }
            Item { Layout.fillWidth: true }
            Label { text: couch.controller && couch.controller.activeController.name ? String(couch.controller.activeController.name) : qsTr("Keyboard"); color: App.Theme.text; font.pixelSize: 16 * couch.couchScale; elide: Text.ElideRight; Layout.maximumWidth: 280 * couch.couchScale }
            CouchButton { couchScale: couch.couchScale; implicitWidth: 52 * couch.couchScale; implicitHeight: 52 * couch.couchScale; iconSource: App.UiIcons.sidebarTasks; iconSize: App.Theme.couchIconSizeAction; Accessible.name: qsTr("Tasks"); onClicked: { if (couch.controller) couch.controller.navigate("tasks"); couch.setSection("tasks", "") } }
            CouchButton { couchScale: couch.couchScale; implicitWidth: 52 * couch.couchScale; implicitHeight: 52 * couch.couchScale; iconSource: App.UiIcons.sidebarSettings; iconSize: App.Theme.couchIconSizeAction; Accessible.name: qsTr("Settings"); onClicked: { if (couch.controller) couch.controller.navigate("settings"); couch.setSection("settings", "") } }
            Label { text: Qt.formatTime(new Date(), "HH:mm"); color: App.Theme.text; font.pixelSize: 20 * couch.couchScale; font.weight: Font.DemiBold; Timer { interval: 30000; running: true; repeat: true; onTriggered: parent.text = Qt.formatTime(new Date(), "HH:mm") } }
        }
    }

    CouchHints {
        visible: couch.navigation
                 && couch.navigation.inputModality === "controller"
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.rightMargin: 52 * couch.couchScale; anchors.bottomMargin: 20 * couch.couchScale
        z: 460; couchScale: couch.couchScale; buttonHints: couch.hints
        acceptText: couch.pageModalOpen || systemMenu.visible ? qsTr("Choose")
                    : couch.section === "settings" ? qsTr("Change") : qsTr("Select")
        showBack: couch.section !== "home" || couch.pageModalOpen || systemMenu.visible
        showContext: !couch.pageModalOpen && !systemMenu.visible
                     && (couch.section === "library"
                     || (couch.section === "home" && homePage.selectedGameIndex >= 0)
                     || (couch.section === "details" && Boolean(detailsPage.game.id))
                     || (couch.section === "tasks" && tasksPage.contextAvailable))
        showTabs: !couch.pageModalOpen && !systemMenu.visible
                  && (couch.section === "details" || couch.section === "settings")
        showDirections: couch.pageModalOpen || systemMenu.visible || couch.section === "settings"
        showPages: !couch.pageModalOpen && !systemMenu.visible
                   && ["library", "updates", "tasks", "details", "settings"].indexOf(couch.section) >= 0
        showMenu: !couch.pageModalOpen && !systemMenu.visible
        contextText: couch.section === "library" ? qsTr("Filters") : qsTr("More")
        sectionText: couch.section === "details" ? qsTr("Tabs")
                     : couch.section === "settings" ? qsTr("Categories")
                     : qsTr("Jump")
        directionText: couch.section === "settings" ? qsTr("Adjust") : qsTr("Move")
    }

    Rectangle {
        id: disconnectedOverlay
        objectName: "couchControllerDisconnected"
        // Connection changes use ordinary toasts; keyboard and mouse must remain usable.
        visible: false
        anchors.fill: parent; z: 180; color: "#C5080C12"
        Rectangle {
            anchors.centerIn: parent; width: Math.min(parent.width * 0.64, 920 * couch.couchScale); height: 340 * couch.couchScale
            radius: 26 * couch.couchScale; color: App.Theme.surfaceRaised; border.width: 2; border.color: App.Theme.warning
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 30 * couch.couchScale; spacing: 15 * couch.couchScale
                Label { Layout.fillWidth: true; text: qsTr("Controller disconnected"); color: App.Theme.text; font.pixelSize: 34 * couch.couchScale; font.weight: Font.Bold; horizontalAlignment: Text.AlignHCenter }
                Label { Layout.fillWidth: true; text: qsTr("Reconnect a controller to restore the previous focus. Keyboard and mouse remain available for emergency control."); color: App.Theme.textSecondary; font.pixelSize: 17 * couch.couchScale; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
                Item { Layout.fillHeight: true }
                CouchButton { Layout.alignment: Qt.AlignHCenter; couchScale: couch.couchScale; text: qsTr("Switch to Desktop Mode"); implicitWidth: 360 * couch.couchScale; implicitHeight: 64 * couch.couchScale; onClicked: if (couch.controller) couch.controller.setInterfaceMode("desktop") }
            }
        }
    }

    CouchSystemMenu {
        id: systemMenu
        anchors.fill: parent
        controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
        onLibraryRequested: {
            couch.setSection("library", "")
            couch.synchronizeControllerSection("library")
        }
        onTasksRequested: {
            couch.setSection("tasks", "")
            couch.synchronizeControllerSection("tasks")
        }
        onSettingsRequested: {
            couch.setSection("settings", "")
            couch.synchronizeControllerSection("settings")
        }
        onClosed: Qt.callLater(couch.restoreActivePageFocus)
    }

    MouseArea {
        anchors.fill: parent; z: 500; acceptedButtons: Qt.NoButton; hoverEnabled: true
        cursorShape: !couch.hideCursor || couch.cursorVisible ? Qt.ArrowCursor : Qt.BlankCursor
        onPositionChanged: {
            couch.cursorVisible = true
            if (couch.navigation)
                couch.navigation.setInputModality("mouse")
            if (couch.hideCursor)
                hideCursorTimer.restart()
        }
    }
    Timer { id: hideCursorTimer; interval: 1800; onTriggered: couch.cursorVisible = false }
    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: App.Theme.animationNormal } }
}
