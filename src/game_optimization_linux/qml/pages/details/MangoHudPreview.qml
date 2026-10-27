pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App
SurfaceCard {
    id: card
    property var editor
    property bool compactPreview: false
    padding: 12
    contentItem: ColumnLayout {
        spacing: 8
        Label { Layout.fillWidth: true; text: qsTr("Illustrative preview"); color: App.Theme.text; font.bold: true; font.pixelSize: 18 }
        Label { Layout.fillWidth: true; text: qsTr("Sample values · approximate layout · not measurements"); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: card.editor.captionSize }
                    Rectangle {
                        id: previewSurface
                        objectName: "mangoHudPreviewSurface"
                        readonly property var layout: card.editor.previewLayout || ({})
                        readonly property string corner: String(layout.position || "top-left")
                        readonly property bool horizontalLayout: Boolean(layout.horizontal)
                        readonly property bool compactLayout: Boolean(layout.compact)
                        // The surface stands for a 1280 px wide screen.
                        readonly property real screenScale: width / 1280
                        readonly property real textSize: Math.max(5, Number(layout.fontSize || 24) * Math.max(screenScale, card.compactPreview ? 0.4 : 0.6))
                        readonly property real cellSpacing: Math.max(0, Number(layout.cellPaddingY === undefined ? -0.085 : layout.cellPaddingY) + 0.2) * textSize
                        Layout.fillWidth: true
                        Layout.preferredHeight: card.compactPreview ? 125 : Math.min(300, width * 0.64)
                        radius: App.Theme.radiusMedium
                        color: "#243143"
                        clip: true
                        Rectangle {
                            id: hudPreview
                            objectName: "mangoHudPreviewHud"
                            visible: !Boolean(previewSurface.layout.empty)
                            readonly property real inset: previewSurface.compactLayout ? 3 : 6
                            width: Math.min(parent.width - 8, hudContent.implicitWidth + inset * 2)
                            height: Math.min(parent.height - 8, hudContent.implicitHeight + inset * 2)
                            radius: Math.min(Number(previewSurface.layout.roundCorners || 0) * Math.max(previewSurface.screenScale, 0.3), Math.min(width, height) / 2)
                            color: card.editor.hudBackground(String(previewSurface.layout.backgroundColor || "#020202"), Number(previewSurface.layout.backgroundAlpha !== undefined ? previewSurface.layout.backgroundAlpha : 0.5))
                            // MangoHud has no border option; none is drawn here either.
                            border.width: 0
                            x: Number(previewSurface.layout.offsetX || 0) * previewSurface.screenScale + (previewSurface.corner.indexOf("right") >= 0 ? parent.width - width - 4
                               : previewSurface.corner.indexOf("center") >= 0 ? (parent.width - width) / 2 : 4)
                            y: Number(previewSurface.layout.offsetY || 0) * previewSurface.screenScale + (previewSurface.corner.indexOf("bottom") === 0 ? parent.height - height - 4
                               : previewSurface.corner.indexOf("middle") === 0 ? (parent.height - height) / 2 : 4)
                            clip: true
                            Column {
                                id: hudContent
                                x: hudPreview.inset
                                y: hudPreview.inset
                                spacing: previewSurface.cellSpacing
                                Grid {
                                    objectName: "mangoHudPreviewGrid"
                                    visible: !previewSurface.horizontalLayout
                                    columns: Math.max(1, Number(previewSurface.layout.tableColumns || 3))
                                    columnSpacing: previewSurface.cellSpacing * 2
                                    rowSpacing: previewSurface.compactLayout ? 0 : previewSurface.cellSpacing / 2
                                    Repeater {
                                        model: previewSurface.horizontalLayout ? [] : (previewSurface.layout.cells || [])
                                        delegate: Label {
                                            required property var modelData
                                            text: String(modelData.text || "")
                                            color: String(modelData.color || "transparent")
                                            font.pixelSize: previewSurface.textSize
                                            font.weight: modelData.label ? Font.Bold : Font.Normal
                                        }
                                    }
                                }
                                Row {
                                    objectName: "mangoHudPreviewRow"
                                    visible: previewSurface.horizontalLayout
                                    spacing: previewSurface.cellSpacing * 2
                                    Repeater {
                                        model: previewSurface.horizontalLayout ? (previewSurface.layout.inlineCells || []) : []
                                        delegate: Label {
                                            required property var modelData
                                            text: String(modelData.text || "")
                                            color: String(modelData.color || "transparent")
                                            font.pixelSize: previewSurface.textSize
                                            font.weight: modelData.label ? Font.Bold : Font.Normal
                                        }
                                    }
                                }
                                Row {
                                    objectName: "mangoHudPreviewGraph"
                                    visible: Boolean(previewSurface.layout.frameTimingGraph)
                                    height: Math.max(10, previewSurface.textSize * 2)
                                    spacing: 1
                                    Repeater {
                                        model: [6, 7, 6, 8, 6, 5, 7, 12, 6, 6, 7, 6, 5, 6, 9, 6, 6, 7, 6, 6]
                                        delegate: Rectangle {
                                            required property int modelData
                                            anchors.bottom: parent ? parent.bottom : undefined
                                            width: Math.max(2, previewSurface.textSize * 0.45)
                                            height: parent ? parent.height * modelData / 12 : 0
                                            color: String(previewSurface.layout.frametimeColor || "#00FF00")
                                        }
                                    }
                                }
                                Repeater {
                                    model: previewSurface.layout.graphs || []
                                    delegate: Column {
                                        required property string modelData
                                        spacing: 2
                                        Label { text: modelData + " · " + qsTr("sample"); color: card.editor.textColor ? "#" + card.editor.textColor : App.Theme.text; font.pixelSize: previewSurface.textSize }
                                        Row { spacing: 2
                                            Repeater { model: [3,5,4,7,6,8,5,4,6,7,5,6]
                                                Rectangle { required property int modelData; width: 4; height: modelData * 1.5; color: App.Theme.accent } }
                                        }
                                    }
                                }
                            }
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: Boolean(previewSurface.layout.empty)
                            text: qsTr("MangoHud shows no metrics with these settings")
                            color: App.Theme.textMuted
                            font.pixelSize: card.editor.captionSize
                        }
                    }

        Label { visible: !card.compactPreview && (card.editor.previewLayout.hiddenMetrics || []).length > 0; Layout.fillWidth: true; text: qsTr("Some values need their GPU, CPU, VRAM or FPS row enabled."); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: card.editor.captionSize }
        RowLayout { Layout.fillWidth: true
            AppButton { textPixelSize: card.editor.bodySize; objectName: "mangoHudLivePreviewButton"; property string mangoActionType: "button"; Layout.fillWidth: true; text: qsTr("Real Vulkan preview"); kind: "secondary"; enabled: card.editor.profileLoaded && !card.editor.livePreviewRunning && card.editor.profileEnabled && card.editor.metrics.length > 0; onClicked: card.editor.startLivePreview() }
            AppButton { textPixelSize: card.editor.bodySize; objectName: "mangoHudStopLivePreviewButton"; property string mangoActionType: "button"; visible: card.editor.livePreviewRunning; text: qsTr("Close"); kind: "secondary"; onClicked: card.editor.stopLivePreview() }
        }
        Label { visible: !card.compactPreview; Layout.fillWidth: true; text: qsTr("Opens host MangoHud + vkcube with this draft. No profile is saved; this is separate from the game's launcher runtime."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: card.editor.captionSize }
        Label { objectName: "mangoHudLivePreviewMessage"; visible: card.editor.livePreviewMessage.length > 0; Layout.fillWidth: true; text: card.editor.livePreviewMessage; color: App.Theme.danger; wrapMode: Text.WordWrap; font.pixelSize: card.editor.captionSize }
    }
}
