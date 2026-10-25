pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

SurfaceCard {
    id: card
    required property var effect
    required property var parameters
    property string displayName: String(effect.label)
    property int position: -1
    property int effectCount: 0
    property bool expanded: false
    property var labelForParameter
    signal toggled(bool value)
    signal reordered(int direction)
    signal edited(string key, real value)
    signal resetRequested()
    padding: width < 500 ? 12 : 16
    implicitWidth: 320
    implicitHeight: body.implicitHeight + topPadding + bottomPadding
    function description() {
        var descriptions = {
            "cas": qsTr("Adaptive sharpening; can duplicate upscaler sharpening."),
            "dls": qsTr("Luminance sharpening with noise suppression."),
            "fxaa": qsTr("Fast edge smoothing; may soften fine detail."),
            "smaa": qsTr("Colour-edge antialiasing; no depth access required."),
            "lut": qsTr("Colour grading from your selected PNG/CUBE LUT."),
            "vibrance": qsTr("Adjust colour intensity while protecting already saturated colours."),
            "tonemap": qsTr("Exposure, gamma and colour adjustment. Useful for a visible A/B test."),
            "curves": qsTr("Adjust contrast with a colour curve."),
            "lumasharpen": qsTr("Fine-detail sharpening with a configurable clamp.")}
        return descriptions[effect.id] || ""
    }
    contentItem: ColumnLayout {
        id: body
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.text
                    font.weight: Font.DemiBold; text: (card.selected ? String(card.position + 1) + ". " : "") + card.displayName }
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontCaption; text: card.description() }
            }
            AppSwitch { property string postActionType: "check"; objectName: "postEffectSwitch_" + card.effect.id
                checked: card.selected; Accessible.name: card.displayName
                onToggled: card.toggled(checked) }
        }
        Flow {
            Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 6
            AppButton { property string postActionType: "button"; compact: true; implicitWidth: 36; text: "↑"
                enabled: card.selected && card.position > 0; Accessible.name: qsTr("Move effect up")
                onClicked: card.reordered(-1) }
            AppButton { property string postActionType: "button"; compact: true; implicitWidth: 36; text: "↓"
                enabled: card.selected && card.position < card.effectCount - 1; Accessible.name: qsTr("Move effect down")
                onClicked: card.reordered(1) }
            AppButton { property string postActionType: "button"; compact: true
                visible: card.effect.parameters.length > 0
                text: (card.expanded ? "▾ " : "▸ ") + qsTr("Parameters")
                onClicked: card.expanded = !card.expanded }
        }
        ColumnLayout {
            visible: card.expanded
            Layout.fillWidth: true; spacing: 8
            Label { visible: !card.selected; Layout.fillWidth: true; color: App.Theme.textMuted; wrapMode: Text.WordWrap
                text: qsTr("Enable this effect to adjust its parameters.") }
            Repeater {
                model: card.effect.parameters
                delegate: ColumnLayout {
                    id: parameter
                    required property var modelData
                    Layout.fillWidth: true; spacing: 2
                    readonly property real currentValue: card.parameters[modelData.key] !== undefined ? card.parameters[modelData.key] : modelData.default
                    Label { Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WordWrap
                        text: card.labelForParameter ? card.labelForParameter(String(parameter.modelData.label)) : String(parameter.modelData.label) }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        AppSlider { property string postActionType: "slider"; objectName: "postParameter_" + parameter.modelData.key
                            Layout.fillWidth: true; Layout.maximumWidth: 360; enabled: card.selected
                            from: parameter.modelData.minimum; to: parameter.modelData.maximum
                            stepSize: parameter.modelData.integer ? 1 : (to - from) / 100
                            value: parameter.currentValue
                            Accessible.name: String(parameter.modelData.label)
                            onMoved: card.edited(String(parameter.modelData.key), parameter.modelData.integer ? Math.round(value) : Number(value.toFixed(5))) }
                        Label { Layout.preferredWidth: 64; color: App.Theme.text; horizontalAlignment: Text.AlignRight
                            text: parameter.currentValue.toFixed(parameter.modelData.integer ? 0 : parameter.modelData.maximum < 0.1 ? 4 : 3) }
                        Item { Layout.fillWidth: true; visible: card.width > 520 }
                    }
                }
            }
            AppButton { property string postActionType: "button"; compact: true
                text: qsTr("Reset this effect's parameters"); onClicked: card.resetRequested() }
        }
    }
}
