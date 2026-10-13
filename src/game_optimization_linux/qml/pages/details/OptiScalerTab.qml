pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

Item {
    id: tab
    signal toastRequested(string message, string tone)
    property var controller
    property var gameData: ({})

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: parent.width
            spacing: 14

            Label {
                Layout.fillWidth: true
                text: qsTr("OptiScaler")
                color: App.Theme.text
                font.pixelSize: App.Theme.fontDisplay
                font.weight: Font.Bold
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
