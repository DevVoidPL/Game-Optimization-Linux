import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import ".." as App

CouchOverlayFrame {
    id: menu
    objectName: "couchSystemMenu"

    property var controller
    property var navigation
    property int selectedIndex: 0
    property bool quitConfirmationOpen: false
    property int quitChoice: 0
    readonly property var entries: [
        { "id": "resume", "symbol": "▶", "title": qsTr("Resume") },
        { "id": "library", "symbol": "▦", "title": qsTr("Library") },
        { "id": "tasks", "symbol": "↻", "title": qsTr("Tasks") },
        { "id": "settings", "symbol": "⚙", "title": qsTr("Settings") },
        { "id": "desktop", "symbol": "▣", "title": qsTr("Switch to Desktop Mode") },
        { "id": "quit", "symbol": "×", "title": qsTr("Quit GameForge") }
    ]

    signal resumeRequested()
    signal libraryRequested()
    signal tasksRequested()
    signal settingsRequested()
    signal closed()

    maximumWidth: 760 * couchScale
    preferredHeight: (quitConfirmationOpen ? 360 : 790) * couchScale

    function focusSelected() {
        Qt.callLater(function() {
            var item = quitConfirmationOpen
                    ? (quitChoice === 0 ? keepRunningButton : quitButton)
                    : menuRepeater.itemAt(selectedIndex)
            if (item)
                item.forceActiveFocus()
        })
    }

    function open() {
        selectedIndex = 0
        quitConfirmationOpen = false
        quitChoice = 0
        visible = true
        forceActiveFocus()
        if (navigation)
            navigation.openModal("system-menu", "resume")
        focusSelected()
    }

    function close() {
        if (!visible)
            return
        visible = false
        quitConfirmationOpen = false
        quitChoice = 0
        if (navigation)
            navigation.closeModal()
        resumeRequested()
        closed()
    }

    function closeForNavigation() {
        visible = false
        if (navigation)
            navigation.closeModal()
        closed()
    }

    function activate() {
        var entry = entries[selectedIndex]
        if (!entry)
            return
        if (entry.id === "resume") {
            close()
        } else if (entry.id === "library") {
            closeForNavigation()
            libraryRequested()
        } else if (entry.id === "tasks") {
            closeForNavigation()
            tasksRequested()
        } else if (entry.id === "settings") {
            closeForNavigation()
            settingsRequested()
        } else if (entry.id === "desktop") {
            visible = false
            if (navigation)
                navigation.closeModal()
            if (controller)
                controller.setInterfaceMode("desktop")
            closed()
        } else if (entry.id === "quit") {
            quitConfirmationOpen = true
            quitChoice = 0
            if (navigation)
                navigation.rememberFocus("system-menu", "keep-running", 0)
            focusSelected()
        }
    }

    function handleAction(action) {
        if (quitConfirmationOpen) {
            if (action === "Back") {
                quitConfirmationOpen = false
                focusSelected()
            } else if (action === "NavigateLeft" || action === "NavigateUp") {
                quitChoice = 0
                focusSelected()
            } else if (action === "NavigateRight" || action === "NavigateDown") {
                quitChoice = 1
                focusSelected()
            } else if (action === "Confirm") {
                if (quitChoice === 0) {
                    quitConfirmationOpen = false
                    focusSelected()
                } else if (controller) {
                    controller.requestWindowAction("close")
                }
            }
            return
        }

        if (action === "Back" || action === "OpenSystemMenu") {
            close()
        } else if (action === "NavigateUp") {
            selectedIndex = Math.max(0, selectedIndex - 1)
            focusSelected()
        } else if (action === "NavigateDown") {
            selectedIndex = Math.min(entries.length - 1, selectedIndex + 1)
            focusSelected()
        } else if (action === "Confirm") {
            activate()
        }
        if (navigation && visible && !quitConfirmationOpen)
            navigation.rememberFocus("system-menu", entries[selectedIndex].id, selectedIndex)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 14 * menu.couchScale

        Label {
            Layout.fillWidth: true
            text: menu.quitConfirmationOpen ? qsTr("Quit GameForge?")
                                                : qsTr("System menu")
            color: App.Theme.text
            font.pixelSize: 34 * menu.couchScale
            font.weight: Font.Bold
            horizontalAlignment: menu.quitConfirmationOpen
                                 ? Text.AlignHCenter : Text.AlignLeft
        }

        Label {
            Layout.fillWidth: true
            text: menu.quitConfirmationOpen
                  ? qsTr("Active tasks will be interrupted safely. Are you sure you want to close the application?")
                  : qsTr("Choose where to go, or resume your game library.")
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.couchHelperSize * menu.couchScale
            wrapMode: Text.WordWrap
            horizontalAlignment: menu.quitConfirmationOpen
                                 ? Text.AlignHCenter : Text.AlignLeft
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !menu.quitConfirmationOpen
            spacing: 10 * menu.couchScale

            Repeater {
                id: menuRepeater
                model: menu.entries
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    couchScale: menu.couchScale
                    symbol: modelData.symbol
                    text: modelData.title
                    focus: menu.visible && !menu.quitConfirmationOpen
                           && menu.selectedIndex === index
                    onClicked: {
                        menu.selectedIndex = index
                        menu.activate()
                    }
                }
            }
        }

        Item {
            Layout.fillHeight: true
            visible: menu.quitConfirmationOpen
        }

        RowLayout {
            Layout.fillWidth: true
            visible: menu.quitConfirmationOpen
            spacing: 14 * menu.couchScale

            CouchButton {
                id: keepRunningButton
                Layout.fillWidth: true
                couchScale: menu.couchScale
                text: qsTr("Keep running")
                focus: menu.visible && menu.quitConfirmationOpen
                       && menu.quitChoice === 0
                onClicked: {
                    menu.quitChoice = 0
                    menu.quitConfirmationOpen = false
                    menu.focusSelected()
                }
            }
            CouchButton {
                id: quitButton
                Layout.fillWidth: true
                couchScale: menu.couchScale
                text: qsTr("Quit GameForge")
                focus: menu.visible && menu.quitConfirmationOpen
                       && menu.quitChoice === 1
                onClicked: if (menu.controller)
                    menu.controller.requestWindowAction("close")
            }
        }
    }
}
