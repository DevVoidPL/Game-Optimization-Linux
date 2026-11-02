pragma ComponentBehavior: Bound
import QtQuick

FocusScope {
    id: root
    objectName: "couchExecutablePicker"
    property var controller
    property string gameId: ""
    property real couchScale: 1
    property bool opened: false
    property var candidates: []
    property string error: ""
    readonly property var padFocusedItem: keyboard.opened ? keyboard : menu.padFocusedItem
    signal selected(string path)
    signal cancelled()

    visible: opened
    function open() {
        error = ""
        const status = controller && controller.getOptiScalerStatus
            ? controller.getOptiScalerStatus(gameId) || ({}) : ({})
        candidates = status.executableCandidates || []
        menu.currentIndex = 0
        opened = true
        Qt.callLater(menu.focusCurrent)
    }
    function close() { keyboard.close(false); opened = false }
    function save(path) {
        if (!controller || !controller.rememberOptiScalerExecutable) return
        const result = controller.rememberOptiScalerExecutable(gameId, path) || ({})
        if (!result.success) { error = String(result.error || ""); return }
        close()
        selected(path)
    }
    function handlePadAction(action) {
        if (keyboard.opened) { keyboard.handleAction(action); return true }
        if (action === "Back") { close(); cancelled(); return true }
        return menu.handlePadAction(action)
    }
    function rows() {
        const result = candidates.map(function(candidate) {
            const path = String(candidate.relativePath || "")
            return {id: path, title: path, value: String(candidate.fullPath || ""),
                description: (candidate.reasons || []).join(", "),
                trigger: function() { root.save(path) }}
        })
        result.push({id:"manual", title:qsTr("Enter executable path"),
            description:qsTr("Enter the main game executable, relative to the game folder."),
            trigger:function() { keyboard.open("", qsTr("Main executable")) }})
        result.push({id:"cancel", title:qsTr("Cancel"), trigger:function() { root.close(); root.cancelled() }})
        return result
    }
    CouchOptionList {
        id: menu; anchors.fill: parent; couchScale: root.couchScale
        title: qsTr("Choose the main game executable")
        status: root.error
        rows: root.rows()
        focusSuspended: keyboard.opened
    }
    CouchOnScreenKeyboard {
        id: keyboard; anchors.fill: parent; couchScale: root.couchScale
        onAccepted: function(path) { root.save(path) }
        onCancelled: menu.focusCurrent()
    }
    onGameIdChanged: close()
}