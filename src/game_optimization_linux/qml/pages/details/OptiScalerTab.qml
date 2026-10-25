pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab
    signal toastRequested(string message, string tone)
    readonly property var scrollFlickable: optiScalerScroll.contentItem
    property var controller
    property var gameData: ({})

    ScrollView {
        id: optiScalerScroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: parent.width
            spacing: 14

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 44
                Accessible.role: Accessible.StaticText
                Accessible.name: "OptiScaler"
                Image {
                    id: optiScalerBrand
                    objectName: "optiScalerBrand"
                    width: Math.min(280, parent.width)
                    height: parent.height
                    source: App.UiIcons.brandOptiScaler
                    fillMode: Image.PreserveAspectFit
                    horizontalAlignment: Image.AlignLeft
                    smooth: true
                }
                Label {
                    visible: optiScalerBrand.status === Image.Error
                    text: "OptiScaler"
                    color: App.Theme.text
                    font.pixelSize: App.Theme.fontDisplay
                    font.weight: Font.Bold
                }
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Zainstaluj i skonfiguruj OptiScaler dla tej gry. Zaawansowane ustawienia wstrzykiwania i backendu pozostają ukryte, dopóki nie zostaną wywołane.")
                color: App.Theme.textSecondary
                wrapMode: Text.WordWrap
            }

            OptiScalerSection {
                objectName: "optiScalerPageContent"
                Layout.fillWidth: true
                controller: tab.controller
                gameData: tab.gameData
            }

            Item { Layout.preferredHeight: 4 }
        }
    }
}
