pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "../.." as App

FocusScope {
    id: keyboard
    objectName: "couchOnScreenKeyboard"

    property real couchScale: 1.0
    property var buttonHints: ({})
    property bool opened: false
    property string title: qsTr("Edit text")
    property string initialText: ""
    property string text: ""
    property bool uppercase: false
    property bool symbols: false
    property int selectedIndex: 0
    readonly property int columns: 6
    readonly property var letters: [
        "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "a", "s",
        "d", "f", "g", "h", "j", "k", "l", "z", "x", "c", "v", "b",
        "n", "m", "ą", "ć", "ę", "ł", "ń", "ó", "ś", "ź", "ż"
    ]
    readonly property var symbolCharacters: [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "_",
        "/", "\\", ".", ":", ";", "=", "+", "%", "@", "#", "$", "&",
        "*", "?", "!", "\"", "'", "(", ")", "[", "]", "{", "}"
    ]
    readonly property var keyModel: buildKeys()
    readonly property string selectedAction: keyModel[selectedIndex]
                                               ? String(keyModel[selectedIndex].action)
                                               : ""

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
        // Both layouts reserve exactly six character rows. This keeps every
        // semantic key at the same index when letters and symbols are switched.
        while (result.length < 36)
            result.push({ "label": "", "action": "noop" })
        result.push({ "label": qsTr("Space"), "action": "space" })
        result.push({ "label": "⌫ " + qsTr("Backspace"), "action": "backspace" })
        result.push({ "label": qsTr("Clear"), "action": "clear" })
        result.push({ "label": uppercase ? "abc" : "ABC", "action": "shift" })
        result.push({ "label": symbols ? "abc" : "123", "action": "symbols" })
        result.push({ "label": "", "action": "noop" })
        result.push({ "label": "", "action": "noop" })
        result.push({ "label": "", "action": "noop" })
        result.push({ "label": "", "action": "noop" })
        result.push({ "label": "", "action": "noop" })
        result.push({ "label": "✓ " + qsTr("Confirm"), "action": "confirm" })
        result.push({ "label": "× " + qsTr("Cancel"), "action": "cancel" })
        return result
    }

    function selectable(index) {
        return index >= 0 && index < keyModel.length
                && keyModel[index].action !== "noop"
    }

    function ensureSelected() {
        if (selectable(selectedIndex))
            return
        for (var distance = 1; distance < keyModel.length; ++distance) {
            if (selectable(selectedIndex - distance)) {
                selectedIndex -= distance
                return
            }
            if (selectable(selectedIndex + distance)) {
                selectedIndex += distance
                return
            }
        }
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

    function move(horizontal, vertical) {
        var step = horizontal !== 0 ? horizontal : vertical * columns
        var originRow = Math.floor(selectedIndex / columns)
        var candidate = selectedIndex + step
        while (candidate >= 0 && candidate < keyModel.length) {
            if (horizontal !== 0 && Math.floor(candidate / columns) !== originRow)
                break
            if (selectable(candidate)) {
                selectedIndex = candidate
                focusSelected()
                semanticSound("navigate")
                return
            }
            candidate += step
        }
        semanticSound("error")
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
                    anchors.fill: parent
                    anchors.margins: 16 * keyboard.couchScale
                    text: keyboard.text.length ? keyboard.text : qsTr("Empty")
                    color: keyboard.text.length ? App.Theme.text : App.Theme.textMuted
                    font.pixelSize: 21 * keyboard.couchScale
                    font.family: "monospace"
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideLeft
                }
            }

            GridLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                columns: keyboard.columns
                rowSpacing: 9 * keyboard.couchScale
                columnSpacing: 9 * keyboard.couchScale

                Repeater {
                    id: keyRepeater
                    model: keyboard.keyModel
                    delegate: CouchButton {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        couchScale: keyboard.couchScale
                        text: modelData.label
                        enabled: modelData.action !== "noop"
                        opacity: enabled ? 1 : 0
                        focus: keyboard.opened && keyboard.selectedIndex === index
                        font.pixelSize: 17 * keyboard.couchScale
                        Accessible.name: enabled ? modelData.label : ""
                        onClicked: {
                            keyboard.selectedIndex = index
                            keyboard.activate()
                        }
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                text: qsTr("%1: uppercase · %2: letters/symbols · %3: cancel")
                      .arg(String(keyboard.buttonHints.pageUp || "L2"))
                      .arg(String(keyboard.buttonHints.pageDown || "R2"))
                      .arg(String(keyboard.buttonHints.back || "B"))
                color: App.Theme.textSecondary
                font.pixelSize: 15 * keyboard.couchScale
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
