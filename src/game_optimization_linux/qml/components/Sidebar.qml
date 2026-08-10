pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

Rectangle {
    id: sidebar

    property bool collapsed: false
    property string currentPage: "games"
    property string appName: qsTr("Application")
    property url logoSource: ""
    property int updatesPendingCount: 0
    readonly property int logoExtent: collapsed ? 42 : 54
    signal navigateRequested(string page)
    signal collapseRequested()

    implicitWidth: collapsed ? App.Theme.sidebarCollapsed : App.Theme.sidebarExpanded
    color: App.Theme.sidebar
    border.width: 0

    Behavior on implicitWidth {
        NumberAnimation { duration: App.Theme.animationNormal; easing.type: Easing.OutCubic }
    }

    readonly property var destinations: [
        { "page": "games", "label": qsTr("Games"), "icon": Qt.resolvedUrl("../resources/sidebar-games.svg") },
        { "page": "updates", "label": qsTr("Updates"), "icon": Qt.resolvedUrl("../resources/sidebar-updates.svg"),
          "count": Math.max(0, sidebar.updatesPendingCount) },
        { "page": "tasks", "label": qsTr("Tasks"), "icon": Qt.resolvedUrl("../resources/sidebar-tasks.svg") },
        { "page": "system", "label": qsTr("System"), "icon": Qt.resolvedUrl("../resources/sidebar-system.svg") },
        { "page": "settings", "label": qsTr("Settings"), "icon": Qt.resolvedUrl("../resources/sidebar-settings.svg") }
    ]

    function isSelected(page) {
        if (page === "games")
            return currentPage === "games" || currentPage === "game" || currentPage === "details" || currentPage === "gameDetails"
        return currentPage === page
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 64

            RowLayout {
                anchors.fill: parent
                spacing: 11

                Rectangle {
                    Layout.preferredWidth: sidebar.logoExtent
                    Layout.preferredHeight: sidebar.logoExtent
                    Layout.alignment: Qt.AlignVCenter
                    radius: sidebar.collapsed ? 12 : 15
                    color: App.Theme.accent
                    clip: true

                    Image {
                        id: appLogo
                        anchors.fill: parent
                        anchors.margins: 1
                        source: sidebar.logoSource
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        cache: true
                        smooth: true
                        mipmap: true
                        visible: status === Image.Ready
                    }

                    Label {
                        anchors.centerIn: parent
                        text: "GF"
                        visible: appLogo.status !== Image.Ready
                        color: App.Theme.textOnAccent
                        font.pixelSize: sidebar.collapsed ? 13 : 16
                        font.weight: Font.Black
                    }
                }

                ColumnLayout {
                    visible: !sidebar.collapsed
                    Layout.fillWidth: true
                    spacing: 0

                    Label {
                        text: sidebar.appName
                        color: App.Theme.text
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Label {
                        text: qsTr("DESKTOP")
                        color: App.Theme.accent
                        font.pixelSize: 9
                        font.weight: Font.Bold
                        font.letterSpacing: 1.6
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            Repeater {
                model: sidebar.destinations

                delegate: Button {
                    id: navButton
                    required property var modelData

                    Layout.fillWidth: true
                    Layout.preferredHeight: 46
                    padding: 0
                    focusPolicy: Qt.StrongFocus
                    Accessible.name: modelData.label
                    ToolTip.visible: sidebar.collapsed && hovered
                    ToolTip.text: modelData.label
                    ToolTip.delay: 450
                    onClicked: sidebar.navigateRequested(modelData.page)

                    contentItem: RowLayout {
                        spacing: 12

                        Image {
                            Layout.preferredWidth: 46
                            Layout.preferredHeight: 22
                            source: navButton.modelData.icon
                            sourceSize.width: 22
                            sourceSize.height: 22
                            fillMode: Image.PreserveAspectFit
                            smooth: true
                            opacity: sidebar.isSelected(navButton.modelData.page) ? 1.0 : 0.72
                        }

                        Label {
                            visible: !sidebar.collapsed
                            Layout.fillWidth: true
                            text: navButton.modelData.label
                            color: sidebar.isSelected(navButton.modelData.page) ? App.Theme.text : App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontBody
                            font.weight: sidebar.isSelected(navButton.modelData.page) ? Font.DemiBold : Font.Normal
                            elide: Text.ElideRight
                        }

                        Rectangle {
                            visible: !sidebar.collapsed
                                     && Number(navButton.modelData.count || 0) > 0
                            Layout.preferredWidth: Math.max(22, pendingCountLabel.implicitWidth + 10)
                            Layout.preferredHeight: 22
                            Layout.rightMargin: sidebar.collapsed ? 5 : 9
                            radius: 11
                            color: App.Theme.warningSoft

                            Label {
                                id: pendingCountLabel
                                anchors.centerIn: parent
                                text: String(Math.min(99, Number(navButton.modelData.count || 0)))
                                color: App.Theme.warning
                                font.pixelSize: 10
                                font.weight: Font.Bold
                            }
                        }
                    }

                    background: Rectangle {
                        radius: App.Theme.radiusMedium
                        color: sidebar.isSelected(navButton.modelData.page)
                               ? App.Theme.accentSoft
                               : navButton.hovered ? App.Theme.surfaceHover : "transparent"
                        border.width: navButton.visualFocus ? 2 : 0
                        border.color: App.Theme.accent
                        Behavior on color { ColorAnimation { duration: App.Theme.animationFast } }
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }

        Button {
            id: collapseButton
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            padding: 0
            focusPolicy: Qt.StrongFocus
            Accessible.name: sidebar.collapsed ? qsTr("Expand sidebar") : qsTr("Collapse sidebar")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            ToolTip.delay: 500
            onClicked: sidebar.collapseRequested()

            contentItem: RowLayout {
                spacing: 12

                Label {
                    Layout.preferredWidth: 46
                    text: sidebar.collapsed ? "›" : "‹"
                    color: App.Theme.textSecondary
                    font.pixelSize: 24
                    horizontalAlignment: Text.AlignHCenter
                }

                Label {
                    visible: !sidebar.collapsed
                    Layout.fillWidth: true
                    text: qsTr("Collapse sidebar")
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontCaption
                }
            }

            background: Rectangle {
                radius: App.Theme.radiusSmall
                color: collapseButton.hovered ? App.Theme.surfaceHover : "transparent"
                border.width: collapseButton.visualFocus ? 2 : 0
                border.color: App.Theme.accent
            }
        }
    }

    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: App.Theme.border
    }
}
