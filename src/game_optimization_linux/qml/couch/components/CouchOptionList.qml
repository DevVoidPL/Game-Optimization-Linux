pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

FocusScope {
    id: menu
    property real couchScale: 1
    property string title: ""
    property string status: ""
    property var rows: []
    property int currentIndex: 0
    property bool focusSuspended: false
    readonly property var currentRow: rows[currentIndex] || ({})
    readonly property var padFocusedItem: options.currentItem

    function syncRows() {
        const selected = currentIndex >= 0 && currentIndex < entries.count
            ? entries.get(currentIndex).key : ""
        const keys = rows.map(function(row, index) { return String(row.id || index) })
        for (let i = 0; i < keys.length; ++i) {
            if (i < entries.count && entries.get(i).key === keys[i]) continue
            let found = -1
            for (let j = i + 1; j < entries.count; ++j)
                if (entries.get(j).key === keys[i]) { found = j; break }
            if (found >= 0) entries.move(found, i, 1)
            else entries.insert(i, {key: keys[i]})
        }
        if (entries.count > keys.length) entries.remove(keys.length, entries.count - keys.length)
        const retained = keys.indexOf(selected)
        currentIndex = retained >= 0 ? retained : Math.max(0, Math.min(currentIndex, keys.length - 1))
    }
    function rowIcon(id) {
        if (id === "refresh") return App.UiIcons.actionRefresh
        if (id === "apply" || id === "confirm") return App.UiIcons.actionApply
        if (id === "cancel") return App.UiIcons.couchGlyphCancel
        if (id === "executable" || id === "dll") return App.UiIcons.fileFile
        if (id === "install" || id === "prepare") return App.UiIcons.actionInstall
        if (id === "remove") return App.UiIcons.actionRemove
        if (id === "reset" || id === "restore") return App.UiIcons.actionRestore
        if (id === "diagnostics") return App.UiIcons.statusInformation
        return App.UiIcons.couchGlyphTune
    }

    function focusCurrent() {
        if (focusSuspended) return
        currentIndex = Math.max(0, Math.min(currentIndex, rows.length - 1))
        options.positionViewAtIndex(currentIndex, ListView.Contain)
        if (visible && options.currentItem) options.currentItem.forceActiveFocus()
    }
    function handlePadAction(action) {
        if (action === "Back") return false
        if (action === "NavigateUp" || action === "NavigateDown") {
            currentIndex = Math.max(0, Math.min(rows.length - 1,
                currentIndex + (action === "NavigateUp" ? -1 : 1)))
        } else if (currentRow.enabled !== false) {
            if ((action === "NavigateLeft" || action === "NavigateRight") && currentRow.adjust)
                currentRow.adjust(action === "NavigateLeft" ? -1 : 1)
            else if (action === "Confirm") {
                if (currentRow.trigger) currentRow.trigger()
                else if (currentRow.adjust) currentRow.adjust(1)
            }
        }
        Qt.callLater(focusCurrent)
        return true
    }
    onRowsChanged: syncRows()
    Component.onCompleted: syncRows()
    ListModel { id: entries }
    ColumnLayout {
        anchors.fill: parent
        spacing: 12 * menu.couchScale
        Label {
            Layout.fillWidth: true; text: menu.title; color: App.Theme.text
            font.pixelSize: 30 * menu.couchScale; font.bold: true; wrapMode: Text.WordWrap
        }
        Label {
            Layout.fillWidth: true; visible: text.length > 0; text: menu.status
            color: App.Theme.textSecondary; font.pixelSize: 18 * menu.couchScale
            wrapMode: Text.WordWrap; maximumLineCount: 4; elide: Text.ElideRight
        }
        ListView {
            id: options
            objectName: "couchOptionList"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8 * menu.couchScale
            model: entries; currentIndex: menu.currentIndex
            keyNavigationEnabled: false
            highlightMoveDuration: 0
            delegate: CouchTile {
                required property int index
                readonly property var row: menu.rows[index] || ({})
                objectName: "couchOption-" + String(row.id || index)
                width: ListView.view.width; height: 76 * menu.couchScale
                compact: true; couchScale: menu.couchScale
                text: String(row.title || "")
                subtitle: String(row.value || "")
                iconSource: row.icon || menu.rowIcon(String(row.id || ""))
                iconLightSource: row.iconOnLight || (row.id === "cancel" ? App.UiIcons.couchGlyphCancelOnLight
                    : menu.rowIcon(String(row.id || "")) === App.UiIcons.couchGlyphTune
                    ? App.UiIcons.couchGlyphTuneOnLight : iconSource)
                showChevron: !!row.adjust
                enabled: true
                opacity: row.enabled === false ? 0.55 : 1
                focus: menu.activeFocus && menu.currentIndex === index
                onClicked: { menu.currentIndex = index; menu.handlePadAction("Confirm") }
            }
        }
        Label {
            Layout.fillWidth: true; Layout.minimumHeight: 54 * menu.couchScale
            text: String(menu.currentRow.description || "")
            color: menu.currentRow.enabled === false ? App.Theme.warning : App.Theme.textSecondary
            font.pixelSize: 18 * menu.couchScale; wrapMode: Text.WordWrap
            maximumLineCount: 4; elide: Text.ElideRight
        }
        Label {
            Layout.fillWidth: true
            text: qsTr("D-pad: choose / adjust · A: confirm · B: back")
            color: App.Theme.textMuted; font.pixelSize: 16 * menu.couchScale; wrapMode: Text.WordWrap
        }
    }
}