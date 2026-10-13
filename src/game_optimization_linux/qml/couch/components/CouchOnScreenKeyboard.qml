pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "../.." as App

// Controller keyboard. Character rows follow QWERTY (q-p, a-l, z-m) plus a
// Polish row; the symbol layer uses the same row sizes, so every action key
// keeps its index when layers change. Keys are laid out on one unit grid:
// every character key has exactly the same cell, action keys span whole
// multiples of it. Up/down move to the key whose centre is nearest.
FocusScope {
    id: keyboard
    objectName: "couchOnScreenKeyboard"

    property real couchScale: 1.0
    property var buttonHints: ({})
    // Owners set this like the top bar: true when no controller is active.
    property bool keyboardInput: false
    property bool opened: false
    property string title: qsTr("Edit text")
    property string initialText: ""
    property string text: ""
    property bool uppercase: false
    property bool symbols: false
    property int selectedIndex: 0
    // Widest row, in key units.
    readonly property int columns: 10
    readonly property var rowSizes: [10, 9, 7, 9]
    readonly property var letters: [
        "q", "w", "e", "r", "t", "y", "u", "i", "o", "p",
        "a", "s", "d", "f", "g", "h", "j", "k", "l",
        "z", "x", "c", "v", "b", "n", "m",
        "ą", "ć", "ę", "ł", "ń", "ó", "ś", "ź", "ż"
    ]
    readonly property var symbolCharacters: [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
        "-", "_", "/", "\\", ".", ":", ";", "=", "+",
        "%", "@", "#", "$", "&", "*", "?",
        "!", "\"", "'", "(", ")", "[", "]", "{", "}"
    ]
    readonly property var keyModel: buildKeys()
    readonly property string selectedAction: keyModel[selectedIndex]
                                               ? String(keyModel[selectedIndex].action)
                                               : ""
    // Geometry of every key in key units: { row, x, span }.
    readonly property var keyLayout: buildLayout()

    signal accepted(string value)
    signal cancelled()
    signal semanticSound(string kind)

    function buildKeys() {
        var source = symbols ? symbolCharacters : letters
        var result = []
        for (var index = 0; index < source.length; ++index) {
            var character = String(source[index])
            result.push({
                "label": uppercase && !symbols ? character.toUpperCase() : character,
                "value": uppercase && !symbols ? character.toUpperCase() : character,
                "action": "character"
            })
        }
        // Action row, then a separate row for the two final decisions.
        result.push({ "label": uppercase ? "abc" : "ABC", "action": "shift", "span": 1.5 })
        result.push({ "label": symbols ? "abc" : "123", "action": "symbols", "span": 1.5 })
        result.push({ "label": qsTr("Space"), "action": "space", "span": 3 })
        result.push({ "label": qsTr("Backspace"), "action": "backspace", "span": 2,
                      "icon": App.UiIcons.couchGlyphBackspace, "iconOnLight": App.UiIcons.couchGlyphBackspaceOnLight })
        result.push({ "label": qsTr("Clear"), "action": "clear", "span": 2 })
        result.push({ "label": qsTr("Cancel"), "action": "cancel", "span": 3,
                      "icon": App.UiIcons.couchGlyphCancel, "iconOnLight": App.UiIcons.couchGlyphCancelOnLight })
        result.push({ "label": qsTr("Confirm"), "action": "confirm", "span": 3, "primary": true,
                      "icon": App.UiIcons.couchGlyphSave, "iconOnLight": App.UiIcons.couchGlyphSaveOnLight })
        return result
    }

    function buildLayout() {
        var layout = []
        var start = 0
        // Character rows, centred on the 10-unit width.
        for (var row = 0; row < rowSizes.length; ++row) {
            var size = rowSizes[row]
            var offset = (columns - size) / 2
            for (var column = 0; column < size; ++column)
                layout.push({ "row": row, "x": offset + column, "span": 1 })
            start += size
        }
        // Action row: spans with a key-gap between them, centred.
        var actionRow = []
        var decisionRow = []
        for (var index = start; index < keyModel.length; ++index) {
            var key = keyModel[index]
            if (key.action === "cancel" || key.action === "confirm")
                decisionRow.push(index)
            else
                actionRow.push(index)
        }
        function place(indices, row, separation) {
            var total = 0
            for (var i = 0; i < indices.length; ++i)
                total += Number(keyModel[indices[i]].span || 1)
            total += separation
            var x = (columns - total) / 2
            for (var j = 0; j < indices.length; ++j) {
                var span = Number(keyModel[indices[j]].span || 1)
                layout[indices[j]] = { "row": row, "x": x, "span": span }
                x += span + (j === 0 ? separation : 0)
            }
        }
        place(actionRow, rowSizes.length, 0)
        // Cancel and Confirm are clearly apart: two units between them.
        place(decisionRow, rowSizes.length + 1, 2)
        return layout
    }

    function selectable(index) {
        return index >= 0 && index < keyModel.length
                && keyModel[index].action !== "noop"
    }

    function ensureSelected() {
        if (!selectable(selectedIndex))
            selectedIndex = 0
    }

    function open(value, heading) {
        initialText = String(value || "")
        text = initialText
        title = String(heading || qsTr("Edit text"))
        uppercase = false
        symbols = false
        selectedIndex = 0
        opened = true
        forceActiveFocus()
        focusSelected()
        semanticSound("open")
    }

    function close(acceptChanges) {
        if (!opened)
            return
        opened = false
        if (acceptChanges)
            accepted(text)
        else
            cancelled()
        semanticSound("close")
    }

    function focusSelected() {
        ensureSelected()
        Qt.callLater(function() {
            var item = keyRepeater.itemAt(keyboard.selectedIndex)
            if (item)
                item.forceActiveFocus()
        })
    }

    // Left/right stay in the row; up/down pick the key whose centre is
    // nearest in the adjacent row (rows have different lengths).
    function move(horizontal, vertical) {
        var current = keyLayout[selectedIndex]
        if (!current) {
            semanticSound("error")
            return
        }
        var candidate = -1
        if (horizontal !== 0) {
            var bestX = Infinity
            for (var index = 0; index < keyLayout.length; ++index) {
                var item = keyLayout[index]
                if (item.row !== current.row || index === selectedIndex)
                    continue
                var delta = (item.x - current.x) * horizontal
                if (delta > 0 && delta < bestX) {
                    bestX = delta
                    candidate = index
                }
            }
        } else {
            var targetRow = current.row + vertical
            var centre = current.x + current.span / 2
            var bestDistance = Infinity
            for (var i = 0; i < keyLayout.length; ++i) {
                var other = keyLayout[i]
                if (other.row !== targetRow)
                    continue
                var distance = Math.abs(other.x + other.span / 2 - centre)
                if (distance < bestDistance - 0.001) {
                    bestDistance = distance
                    candidate = i
                }
            }
        }
        if (candidate < 0 || !selectable(candidate)) {
            semanticSound("error")
            return
        }
        selectedIndex = candidate
        focusSelected()
        semanticSound("navigate")
    }

    function activate() {
        var key = keyModel[selectedIndex]
        if (!key || key.action === "noop") {
            semanticSound("error")
            return
        }
        if (key.action === "character")
            text += key.value
        else if (key.action === "space")
            text += " "
        else if (key.action === "backspace")
            text = text.slice(0, Math.max(0, text.length - 1))
        else if (key.action === "clear")
            text = ""
        else if (key.action === "shift")
            uppercase = !uppercase
        else if (key.action === "symbols")
            symbols = !symbols
        else if (key.action === "confirm") {
            close(true)
            return
        } else if (key.action === "cancel") {
            close(false)
            return
        }
        semanticSound(key.action === "character" || key.action === "space"
                      || key.action === "backspace" ? "adjust" : "confirm")
        focusSelected()
    }

    function handleAction(action) {
        if (!opened)
            return false
        if (action === "Back")
            close(false)
        else if (action === "NavigateLeft")
            move(-1, 0)
        else if (action === "NavigateRight")
            move(1, 0)
        else if (action === "NavigateUp")
            move(0, -1)
        else if (action === "NavigateDown")
            move(0, 1)
        else if (action === "PageUp") {
            uppercase = !uppercase
            semanticSound("adjust")
            focusSelected()
        } else if (action === "PageDown") {
            symbols = !symbols
            semanticSound("adjust")
            focusSelected()
        } else if (action === "Confirm")
            activate()
        return true
    }

    onKeyModelChanged: {
        ensureSelected()
        if (opened)
            focusSelected()
    }

    visible: opened
    enabled: opened
    focus: opened
    z: 400

    CouchOverlayFrame {
        anchors.fill: parent
        visible: keyboard.opened
        couchScale: keyboard.couchScale
        maximumWidth: 1120 * keyboard.couchScale
        preferredHeight: 840 * keyboard.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 14 * keyboard.couchScale

            Label {
                Layout.fillWidth: true
                text: keyboard.title
                color: App.Theme.text
                font.pixelSize: 30 * keyboard.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 70 * keyboard.couchScale
                radius: 14 * keyboard.couchScale
                color: App.Theme.input
                border.width: 2
                border.color: App.Theme.accent
                Label {
                    objectName: "couchKeyboardField"
                    anchors.fill: parent
                    anchors.margins: 16 * keyboard.couchScale
                    text: keyboard.text.length ? keyboard.text : qsTr("Enter text")
                    color: keyboard.text.length ? App.Theme.text : App.Theme.textMuted
                    font.pixelSize: 22 * keyboard.couchScale
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideLeft
                }
            }

            // One unit grid. Gap leaves room for the ring and the button's
            // focus scale so a key never covers its neighbour.
            Item {
                id: keyArea
                objectName: "couchKeyboardKeyArea"
                Layout.fillWidth: true
                Layout.fillHeight: true
                readonly property int rowCount: keyboard.rowSizes.length + 2
                readonly property real gap: 16 * keyboard.couchScale
                readonly property real unit: Math.floor((width - (keyboard.columns - 1) * gap) / keyboard.columns)
                readonly property real keyHeight: Math.max(36 * keyboard.couchScale,
                    Math.floor((height - (rowCount - 1) * gap) / rowCount))
                readonly property real gridWidth: keyboard.columns * unit + (keyboard.columns - 1) * gap
                readonly property real originX: Math.floor((width - gridWidth) / 2)

                Repeater {
                    id: keyRepeater
                    objectName: "couchKeyboardKeys"
                    model: keyboard.keyModel
                    delegate: CouchButton {
                        id: keyButton
                        objectName: "couchKeyboardKey"
                        required property var modelData
                        required property int index
                        readonly property var cell: keyboard.keyLayout[index] || ({ "row": 0, "x": 0, "span": 1 })
                        readonly property bool characterKey: modelData.action === "character"
                        readonly property bool hasIcon: String(modelData.icon || "").length > 0
                        x: keyArea.originX + cell.x * (keyArea.unit + keyArea.gap)
                        y: cell.row * (keyArea.keyHeight + keyArea.gap)
                        width: cell.span * keyArea.unit + (cell.span - 1) * keyArea.gap
                        height: keyArea.keyHeight
                        padding: 0
                        couchScale: keyboard.couchScale
                        text: modelData.label
                        enabled: modelData.action !== "noop"
                        focus: keyboard.opened && keyboard.selectedIndex === index
                        font.pixelSize: (characterKey ? 26 : 19) * keyboard.couchScale
                        font.weight: characterKey ? Font.DemiBold : Font.Bold
                        Accessible.name: modelData.label
                        onClicked: {
                            keyboard.selectedIndex = index
                            keyboard.activate()
                        }
                        // Content centred on the key; no space reserved for an
                        // icon that the key does not have.
                        contentItem: Item {
                            Row {
                                anchors.centerIn: parent
                                spacing: keyButton.hasIcon ? 10 * keyboard.couchScale : 0
                                CouchIcon {
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: keyButton.hasIcon
                                    source: keyButton.modelData.icon || ""
                                    lightSource: keyButton.modelData.iconOnLight || ""
                                    size: App.Theme.couchIconSmall
                                    couchScale: keyboard.couchScale
                                }
                                Label {
                                    objectName: "couchKeyboardKeyLabel"
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: keyButton.text
                                    color: keyButton.focusVisible ? App.Theme.couchFocusText : App.Theme.text
                                    font: keyButton.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }
            }

            // Same button names and chips as the global Couch hints.
            CouchHints {
                Layout.alignment: Qt.AlignHCenter
                couchScale: keyboard.couchScale
                buttonHints: keyboard.buttonHints
                keyboardInput: keyboard.keyboardInput
                acceptText: qsTr("Select")
                backText: qsTr("Cancel")
                pageText: qsTr("Uppercase / symbols")
                showContext: false
                showTabs: false
                showDirections: false
                showPages: true
                showMenu: false
            }
        }
    }
}
