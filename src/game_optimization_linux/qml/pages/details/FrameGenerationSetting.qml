pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

ColumnLayout {
    id: row
    required property var specification
    required property var value
    property bool couchMode: false
    signal edited(var newValue)
    Layout.fillWidth: true
    spacing: 6
    RowLayout {
        Layout.fillWidth: true
        Label { Layout.fillWidth: true; text: row.specification.title; color: App.Theme.text
            font.pixelSize: row.couchMode ? 21 : App.Theme.fontBody; wrapMode: Text.WordWrap }
        AppSwitch {
            visible: row.specification.type === "bool"
            property string fgActionType: "check"
            checked: Boolean(row.value)
            onToggled: row.edited(checked)
        }
        Label { objectName: "fgValue-" + row.specification.key; visible: row.specification.type === "number"
            text: (Number(row.value) * (row.specification.displayScale || 1)).toFixed(row.specification.displayScale ? 0 : row.specification.step < 1 ? 2 : 0) + (row.specification.unit ? " " + row.specification.unit : "")
            color: App.Theme.accent; font.pixelSize: row.couchMode ? 21 : App.Theme.fontBody; Layout.minimumWidth: 52 }
    }
    AppComboBox {
        visible: row.specification.type === "choice"
        property string fgActionType: "combo"
        Layout.fillWidth: true
        textPixelSize: row.couchMode ? 21 : App.Theme.fontBody
        model: row.specification.labels || []
        currentIndex: Math.max(0, (row.specification.choices || []).indexOf(row.value))
        onActivated: row.edited(row.specification.choices[currentIndex])
    }
    Slider {
        visible: row.specification.type === "number"
        property string fgActionType: "slider"
        Layout.fillWidth: true
        Layout.maximumWidth: row.couchMode ? 700 : 560
        from: row.specification.min || 0; to: row.specification.max || 1; stepSize: row.specification.step || 1
        value: Number(row.value)
        focusPolicy: Qt.StrongFocus
        Accessible.name: row.specification.title + ": " + String(row.value) + " " + String(row.specification.unit || "")
        onMoved: row.edited(value)
    }
    Label { Layout.fillWidth: true; text: row.specification.description; color: App.Theme.textSecondary
        font.pixelSize: row.couchMode ? 18 : (App.Theme.fontBody - 2); wrapMode: Text.WordWrap }
}
