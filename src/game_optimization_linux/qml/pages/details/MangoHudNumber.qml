pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

ColumnLayout {
    id: field
    property bool largeControls: false
    property string title
    property string unit: ""
    property real value: 0
    property real minimum: 0
    property real maximum: 100
    property real step: 1
    property int decimals: 0
    property string controlName: ""
    signal edited(real number)
    spacing: 5
    Layout.fillWidth: true
    Label { font.pixelSize:field.largeControls ? 17 : App.Theme.fontBody; Layout.fillWidth: true; text: field.title; color: App.Theme.text; wrapMode: Text.WordWrap }
    RowLayout {
        Layout.fillWidth: true; spacing: 8
        AppSlider {
            id: numberSlider
            Rectangle { anchors.fill: parent; anchors.margins: -3; z: -1; radius: 8; color: "transparent"; border.width: 2; border.color: App.Theme.accent; visible: numberSlider.activeFocus }
            objectName: field.controlName + "Slider"
            property string mangoActionType: "slider"
            Layout.fillWidth: true; Layout.maximumWidth: 340; Layout.minimumWidth: 70
            from: field.minimum; to: field.maximum; stepSize: field.step; value: field.value
            Accessible.name: field.title
            onMoved: field.edited(Number(value.toFixed(field.decimals)))
        }
        AppTextField {
            objectName: field.controlName + "Value"
            property string mangoActionType: "text"
            function mangoCommit(text) {
                var number=Number(text)
                if (String(text).trim().length && isFinite(number) && number>=field.minimum && number<=field.maximum)
                    field.edited(Number(number.toFixed(field.decimals)))
            }
            font.pixelSize:field.largeControls ? 17 : App.Theme.fontBody
            Layout.preferredWidth: 76; Layout.minimumWidth: 62
            text: field.value.toFixed(field.decimals); selectByMouse: true
            Accessible.name: field.title
            validator: DoubleValidator { bottom: field.minimum; top: field.maximum; decimals: field.decimals; locale: "C" }
            onEditingFinished: {
                if (acceptableInput) field.edited(Number(text))
                else text = field.value.toFixed(field.decimals)
            }
        }
        Label { font.pixelSize:field.largeControls ? 15 : App.Theme.fontBody; text: field.unit; color: App.Theme.textSecondary; Layout.preferredWidth: 23 }
    }
}
