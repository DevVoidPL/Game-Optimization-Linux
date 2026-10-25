pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

Item {
    id: tab
    property var controller
    property var gameData: ({})
    property var frameGenerationDraft: ({})
    signal toastRequested(string message, string tone)
    // All loaded panels expose this contract; Loader types its item as QObject.
    // qmllint disable missing-property
    readonly property var scrollFlickable: content.item ? content.item.scrollFlickable : null
    // qmllint enable missing-property

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        TabBar {
            id: methods
            objectName: "scalingMethodsBar"
            readonly property var titles: ["OptiScaler / FSR", "DLSS Enabler", qsTr("Frame generation"), qsTr("Post-processing")]
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            implicitWidth: 0
            spacing: 3
            background: Rectangle {
                radius: App.Theme.radiusSmall
                color: App.Theme.surface
                border.color: App.Theme.border
            }
            Repeater {
                model: 4
                TabButton {
                    id: methodButton
                    required property int index
                    width: (methods.width - methods.spacing * 3) / 4
                    height: methods.height
                    implicitWidth: 96
                    implicitHeight: height
                    text: methods.titles[index]
                    contentItem: Label {
                        text: methodButton.text
                        color: methodButton.checked ? App.Theme.text : App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        // Long translations (ES) shrink slightly before eliding.
                        fontSizeMode: Text.HorizontalFit
                        minimumPixelSize: Math.max(10, App.Theme.fontBody - 3)
                        elide: Text.ElideRight
                        ToolTip.visible: hovered.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: hovered }
                    }
                    background: Rectangle {
                        radius: App.Theme.radiusSmall
                        color: methodButton.checked ? App.Theme.accentSoft
                              : methodButton.hovered ? App.Theme.surfaceHover : "transparent"
                        border.width: methodButton.visualFocus ? 2 : 0
                        border.color: App.Theme.accent
                    }
                }
            }
        }

        Loader {
            id: content
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: methods.currentIndex === 0 ? optiScaler
                             : methods.currentIndex === 1 ? dlssEnabler
                             : methods.currentIndex === 3 ? postProcessing : frameGeneration
        }
    }

    Component {
        id: dlssEnabler
        DlssEnablerPanel {
            controller: tab.controller
            gameData: tab.gameData
        }
    }

    Component {
        id: optiScaler
        OptiScalerTab {
            controller: tab.controller
            gameData: tab.gameData
            onToastRequested: function(message, tone) { tab.toastRequested(message, tone) }
        }
    }

    Component {
        id: postProcessing
        PostProcessingPanel {
            controller: tab.controller
            gameData: tab.gameData
        }
    }

    Component {
        id: frameGeneration
        FrameGenerationPanel {
            controller: tab.controller
            gameData: tab.gameData
            retainedDraft: tab.frameGenerationDraft
            onDraftChanged: function(draft) { tab.frameGenerationDraft = draft }
            onOptiScalerRequested: methods.currentIndex = 0
        }
    }
}
