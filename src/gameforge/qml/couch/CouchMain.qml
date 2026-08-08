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
                                          || updatesPage.confirmationOpen
                                          || tasksPage.cancellationOpen

    function normalizeAction(action) {
        var value = String(action || "")
        if (value === "Accept") return "Confirm"
        if (value === "Search") return "ContextMenu"
        if (value === "PreviousSection") return "PageLeft"
        if (value === "NextSection") return "PageRight"
        if (value === "OpenMenu") return "OpenSystemMenu"
        if (value === "ToggleMode") return "ToggleDesktopCouch"
        return value
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
        else if (updatesPage.confirmationOpen)
            updatesPage.closeConfirmation()
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
        if (["home", "library", "updates", "tasks", "settings"].indexOf(target) < 0)
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
        else if (pageName === "settings") setSection("settings", "")
        else if (pageName === "games" && ["home", "library"].indexOf(section) < 0) setSection("home", "")
    }

    function handleAction(rawAction) {
        var action = normalizeAction(rawAction)
        cursorVisible = false
        if (!action.length) return
        if (action === "ToggleDesktopCouch") {
            if (controller) controller.setInterfaceMode("desktop")
            return
        }
        if (systemMenu.visible) {
            systemMenu.handleAction(action)
            return
        }
        if (action === "OpenSystemMenu") {
            if (pageModalOpen)
                return
            systemMenu.open()
            return
        }
        if (action === "Back" && section === "home" && !pageModalOpen) {
            systemMenu.open()
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
        else if (event.key === Qt.Key_BracketLeft) action = "PageLeft"
        else if (event.key === Qt.Key_BracketRight) action = "PageRight"
        else if (event.key === Qt.Key_F11) action = "ToggleDesktopCouch"
        if (action.length > 0) { handleAction(action); event.accepted = true }
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
                      : couch.section === "details" ? 2 : couch.section === "updates" ? 3
                      : couch.section === "tasks" ? 4 : 5
        CouchHome {
            id: homePage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onOpenGame: function(gameId) { couch.openGameFrom("home", gameId) }
            onOpenLibrary: couch.setSection("library", "")
            onOpenSettings: { if (couch.controller) couch.controller.navigate("settings"); couch.setSection("settings", "") }
        }
        CouchLibrary {
            id: libraryPage
            controller: couch.controller; navigation: couch.navigation; couchScale: couch.couchScale
            onOpenGame: function(gameId) { couch.openGameFrom("library", gameId) }
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
                 && !updatesPage.confirmationOpen
                 && !tasksPage.cancellationOpen
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 92 * couch.couchScale; color: App.Theme.dark ? "#B80B1018" : "#C9F3F6FA"; z: 30
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 50 * couch.couchScale; anchors.rightMargin: 50 * couch.couchScale; spacing: 14 * couch.couchScale
            Rectangle {
                Layout.preferredWidth: 48 * couch.couchScale; Layout.preferredHeight: 48 * couch.couchScale; radius: 13 * couch.couchScale; color: App.Theme.surfaceRaised; clip: true
                Image { id: logoImage; anchors.fill: parent; anchors.margins: 4 * couch.couchScale; source: couch.controller ? String(couch.controller.appLogoUrl || "") : ""; fillMode: Image.PreserveAspectFit; visible: status === Image.Ready }
                Label { anchors.centerIn: parent; text: "GF"; color: App.Theme.accent; font.pixelSize: 16 * couch.couchScale; font.weight: Font.Bold; visible: !logoImage.visible }
            }
            ColumnLayout {
                spacing: 0
                Label { text: couch.controller ? String(couch.controller.appName || qsTr("GameForge Linux")) : qsTr("GameForge Linux"); color: App.Theme.text; font.pixelSize: 22 * couch.couchScale; font.weight: Font.Bold }
                Label { text: couch.section === "home" ? qsTr("Home") : couch.section === "library" ? qsTr("Library") : couch.section === "details" ? qsTr("Game details") : couch.section === "updates" ? qsTr("Updates") : couch.section === "tasks" ? qsTr("Tasks") : qsTr("Settings"); color: App.Theme.textSecondary; font.pixelSize: 15 * couch.couchScale }
            }
            Item { Layout.fillWidth: true }
            Label { text: couch.controller && couch.controller.activeController.name ? String(couch.controller.activeController.name) : qsTr("Keyboard"); color: App.Theme.text; font.pixelSize: 16 * couch.couchScale; elide: Text.ElideRight; Layout.maximumWidth: 280 * couch.couchScale }
            CouchButton { couchScale: couch.couchScale; implicitWidth: 52 * couch.couchScale; implicitHeight: 52 * couch.couchScale; font.pixelSize: 22 * couch.couchScale; text: "↻"; Accessible.name: qsTr("Tasks"); onClicked: { if (couch.controller) couch.controller.navigate("tasks"); couch.setSection("tasks", "") } }
            CouchButton { couchScale: couch.couchScale; implicitWidth: 52 * couch.couchScale; implicitHeight: 52 * couch.couchScale; font.pixelSize: 22 * couch.couchScale; text: "⚙"; Accessible.name: qsTr("Settings"); onClicked: { if (couch.controller) couch.controller.navigate("settings"); couch.setSection("settings", "") } }
            Label { text: Qt.formatTime(new Date(), "HH:mm"); color: App.Theme.text; font.pixelSize: 20 * couch.couchScale; font.weight: Font.DemiBold; Timer { interval: 30000; running: true; repeat: true; onTriggered: parent.text = Qt.formatTime(new Date(), "HH:mm") } }
        }
    }

    CouchHints {
        visible: topBar.visible
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.rightMargin: 52 * couch.couchScale; anchors.bottomMargin: 20 * couch.couchScale
        z: 40; couchScale: couch.couchScale; buttonHints: couch.hints
        showBack: couch.section !== "home"
        showContext: couch.section === "library"
                     || (couch.section === "home" && homePage.selectedGameIndex >= 0)
                     || (couch.section === "details" && Boolean(detailsPage.game.id))
                     || (couch.section === "tasks" && tasksPage.contextAvailable)
        showTabs: true
        contextText: couch.section === "library" ? qsTr("Filters") : qsTr("More")
        sectionText: couch.section === "details" ? qsTr("Tabs")
                     : couch.section === "settings" ? qsTr("Categories")
                     : qsTr("Jump")
    }

    Rectangle {
        id: disconnectedOverlay
        objectName: "couchControllerDisconnected"
        visible: couch.navigation && !couch.navigation.controllerConnected && !systemMenu.visible
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
        onPositionChanged: { couch.cursorVisible = true; if (couch.hideCursor) hideCursorTimer.restart() }
    }
    Timer { id: hideCursorTimer; interval: 1800; onTriggered: couch.cursorVisible = false }
    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: App.Theme.animationNormal } }
}
