import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab

    property var controller
    property var gameData: ({})
    property string selectedMode: "Classic Enhance"
    property bool previewReady: true
    signal toastRequested(string message, string tone)

    readonly property var modes: [
        { "value": "Classic Enhance", "label": qsTr("Classic Enhance"), "symbol": "◇", "description": qsTr("Conservative sharpening and cleanup with low overhead.") },
        { "value": "AI Lite", "label": qsTr("AI Lite"), "symbol": "✦", "description": qsTr("Lightweight inference for a balanced output preview.") },
        { "value": "AI Quality", "label": qsTr("AI Quality"), "symbol": "◆", "description": qsTr("Highest preview quality with greater VRAM and size estimates.") }
    ]

    function modeLabel(mode) {
        for (var i = 0; i < modes.length; ++i) {
            if (modes[i].value === mode)
                return modes[i].label
        }
        return mode
    }

    function value(keys, fallback) {
        var source = gameData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    readonly property string compatibility: String(value(["engineCompatibility"], qsTr("Not checked")))
    readonly property bool unsupported: compatibility.toLowerCase() === "unsupported"

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 14

            SurfaceCard {
                Layout.fillWidth: true
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 15

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        Rectangle {
                            Layout.preferredWidth: 45
                            Layout.preferredHeight: 45
                            radius: 14
                            color: App.Theme.infoSoft
                            Label {
                                anchors.centerIn: parent
                                text: "✦"
                                color: App.Theme.info
                                font.pixelSize: 21
                                font.weight: Font.Bold
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Graphics Remaster")
                                color: App.Theme.text
                                font.pixelSize: 18
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Configure a visual-only enhancement preview. No texture files are read or changed.")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            spacing: 3
                            Label {
                                text: qsTr("Engine compatibility")
                                color: App.Theme.textMuted
                                font.pixelSize: 10
                                Layout.alignment: Qt.AlignRight
                            }
                            StatusBadge {
                                text: tab.compatibility
                                status: tab.compatibility
                            }
                        }
                    }

                    Rectangle {
                        visible: tab.unsupported
                        Layout.fillWidth: true
                        Layout.preferredHeight: unsupportedText.implicitHeight + 24
                        radius: App.Theme.radiusMedium
                        color: App.Theme.dangerSoft

                        Label {
                            id: unsupportedText
                            anchors.fill: parent
                            anchors.margins: 12
                            text: qsTr("This engine is marked unsupported. The placeholder comparison remains available, but future processing must stay disabled.")
                            color: App.Theme.danger
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: tab.width >= 830 ? 3 : 1
                        rowSpacing: 9
                        columnSpacing: 9

                        Repeater {
                            model: tab.modes

                            delegate: Button {
                                id: modeButton
                                required property var modelData
                                property bool active: tab.selectedMode === modelData.value
                                Layout.fillWidth: true
                                Layout.preferredHeight: 104
                                padding: 13
                                focusPolicy: Qt.StrongFocus
                                onClicked: tab.selectedMode = modelData.value

                                contentItem: ColumnLayout {
                                    spacing: 6
                                    RowLayout {
                                        Label {
                                            text: modeButton.modelData.symbol
                                            color: modeButton.active ? App.Theme.accent : App.Theme.textSecondary
                                            font.pixelSize: 16
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: modeButton.modelData.label
                                            color: App.Theme.text
                                            font.pixelSize: App.Theme.fontBody
                                            font.weight: Font.Bold
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        text: modeButton.modelData.description
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                        wrapMode: Text.WordWrap
                                    }
                                }

                                background: Rectangle {
                                    radius: App.Theme.radiusMedium
                                    color: modeButton.active ? App.Theme.accentSoft
                                                             : modeButton.hovered ? App.Theme.surfaceHover : App.Theme.surfaceRaised
                                    border.width: modeButton.active || modeButton.visualFocus ? 2 : 1
                                    border.color: modeButton.active || modeButton.visualFocus ? App.Theme.accent : App.Theme.border
                                }
                            }
                        }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: tab.width >= 900 ? 2 : 1
                rowSpacing: 12
                columnSpacing: 12

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 352
                    padding: 18

                    contentItem: ColumnLayout {
                        spacing: 6

                        Label {
                            text: qsTr("Output settings")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Scale")
                            description: qsTr("Automatic, 2×, or 4× preview scaling")
                            AppComboBox {
                                Layout.preferredWidth: 125
                                model: [qsTr("Auto"), "2x", "4x"]
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Maximum VRAM")
                            description: qsTr("Budget for a future enhancement worker")
                            RowLayout {
                                AppSlider {
                                    id: vramSlider
                                    Layout.preferredWidth: 145
                                    from: 2
                                    to: 24
                                    stepSize: 1
                                    value: 8
                                }
                                Label {
                                    Layout.preferredWidth: 48
                                    text: qsTr("%1 GB").arg(Math.round(vramSlider.value))
                                    color: App.Theme.text
                                    font.pixelSize: App.Theme.fontCaption
                                    font.weight: Font.Bold
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Maximum output size")
                            description: qsTr("Hard estimate ceiling for generated data")
                            RowLayout {
                                AppSlider {
                                    id: outputSlider
                                    Layout.preferredWidth: 145
                                    from: 5
                                    to: 100
                                    stepSize: 5
                                    value: 20
                                }
                                Label {
                                    Layout.preferredWidth: 52
                                    text: qsTr("%1 GB").arg(Math.round(outputSlider.value))
                                    color: App.Theme.text
                                    font.pixelSize: App.Theme.fontCaption
                                    font.weight: Font.Bold
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Texture scope")
                            description: qsTr("Select which safe texture category would be considered")
                            AppComboBox {
                                Layout.preferredWidth: 215
                                model: [
                                    qsTr("Low-quality textures only"),
                                    qsTr("World textures"),
                                    qsTr("Characters"),
                                    qsTr("Interface"),
                                    qsTr("All safe textures")
                                ]
                            }
                        }
                    }
                }

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 352
                    padding: 18

                    contentItem: ColumnLayout {
                        spacing: 6

                        Label {
                            text: qsTr("Safety and scheduling")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Pause while gaming")
                            description: qsTr("Future work will yield while a game is running")
                            AppSwitch { checked: true }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Automatic backup")
                            description: qsTr("Require a restore point before future changes")
                            AppSwitch { checked: true }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Selected mode")
                            description: qsTr("The mode is used only to style the local preview")
                            StatusBadge {
                                text: tab.modeLabel(tab.selectedMode)
                                status: "available"
                                showDot: false
                            }
                        }

                        Item { Layout.fillHeight: true }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: previewNotice.implicitHeight + 24
                            radius: App.Theme.radiusMedium
                            color: App.Theme.infoSoft
                            Label {
                                id: previewNotice
                                anchors.fill: parent
                                anchors.margins: 12
                                text: qsTr("Preview only - this screen never opens, converts, downloads, or overwrites game assets.")
                                color: App.Theme.info
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                padding: 18

                contentItem: ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Before / after preview")
                                color: App.Theme.text
                                font.pixelSize: 17
                                font.weight: Font.Bold
                            }
                            Label {
                                text: qsTr("Local symbolic artwork generated by QML")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }
                        AppButton {
                            text: qsTr("Refresh preview")
                            iconText: "↻"
                            kind: "secondary"
                            compact: true
                            onClicked: {
                                tab.previewReady = false
                                previewTimer.restart()
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: tab.width >= 760 ? 2 : 1
                        rowSpacing: 10
                        columnSpacing: 10

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 205
                            radius: App.Theme.radiusMedium
                            color: "#263546"
                            clip: true

                            Repeater {
                                model: 12
                                Rectangle {
                                    required property int index
                                    width: parent.width / 4
                                    height: parent.height / 3
                                    x: (index % 4) * width
                                    y: Math.floor(index / 4) * height
                                    color: index % 2 ? "#33475D" : "#2A3B4D"
                                    border.width: 2
                                    border.color: "#18232E"
                                }
                            }

                            Label {
                                anchors.centerIn: parent
                                text: String(tab.value(["name", "title"], "G")).charAt(0)
                                color: "#66FFFFFF"
                                font.pixelSize: 62
                                font.weight: Font.Black
                            }

                            StatusBadge {
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.margins: 12
                                text: qsTr("Before")
                                status: "not checked"
                                showDot: false
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 205
                            radius: App.Theme.radiusMedium
                            gradient: Gradient {
                                GradientStop { position: 0.0; color: "#244A55" }
                                GradientStop { position: 0.52; color: "#356C75" }
                                GradientStop { position: 1.0; color: "#769392" }
                            }
                            clip: true

                            Rectangle {
                                anchors.centerIn: parent
                                width: Math.min(parent.width, parent.height) * 0.62
                                height: width
                                radius: width / 2
                                color: "#22FFFFFF"
                                border.width: 2
                                border.color: "#55FFFFFF"
                            }

                            Label {
                                anchors.centerIn: parent
                                text: String(tab.value(["name", "title"], "G")).charAt(0)
                                color: "#DDFFFFFF"
                                font.pixelSize: 68
                                font.weight: Font.Black
                                opacity: tab.previewReady ? 1 : 0.25
                                Behavior on opacity { NumberAnimation { duration: App.Theme.animationNormal } }
                            }

                            StatusBadge {
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.margins: 12
                                text: qsTr("After · %1").arg(tab.modeLabel(tab.selectedMode))
                                status: "available"
                                showDot: false
                            }

                            BusyIndicator {
                                anchors.centerIn: parent
                                visible: !tab.previewReady
                                running: visible
                            }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: 2 }
        }
    }

    Timer {
        id: previewTimer
        interval: 450
        onTriggered: {
            tab.previewReady = true
            if (tab.controller && tab.controller.showToast)
                tab.controller.showToast(qsTr("Local graphics preview refreshed"))
        }
    }
}
