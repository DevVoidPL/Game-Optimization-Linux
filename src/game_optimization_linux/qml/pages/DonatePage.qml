import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import ".." as App

Item {
    id: page
    objectName: "donatePage"

    property var controller

    function openDonation(url) {
        Qt.openUrlExternally(url)
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: 28
        clip: true

        ColumnLayout {
            width: Math.min(parent.width, 760)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 20

            Label {
                Layout.fillWidth: true
                text: qsTr("Wesprzyj GameOpti")
                color: App.Theme.text
                font.pixelSize: 28
                font.weight: Font.Bold
            }

            SurfaceCard {
                Layout.fillWidth: true
                padding: 24

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 14

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("GameOpti tworzę z pasji do Linuksa i gier. Chcę rozwijać program tak, aby był coraz lepszym narzędziem dla graczy korzystających z Linuxa.")
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Sam rozwój projektu wymaga jednak czasu oraz korzystania z różnych narzędzi i usług, które pomagają mi tworzyć, testować i rozwijać GameOpti.")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Jeżeli GameOpti jest dla Ciebie przydatny i chciałbyś wesprzeć jego dalszy rozwój, możesz zrobić to dobrowolnie poprzez jedną z poniższych opcji. Każde wsparcie naprawdę pomaga :)")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("☕ Buy Me a Coffee")
                    kind: "primary"
                    onClicked: page.openDonation("https://buymeacoffee.com/voiddeveloperpl")
                }

                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("PayPal")
                    kind: "secondary"
                    onClicked: page.openDonation("https://www.paypal.com/paypalme/GameOptimation")
                }
            }

            Label {
                Layout.fillWidth: true
                text: qsTr("Wsparcie jest całkowicie dobrowolne. GameOpti pozostanie darmowym projektem.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.fontCaption
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }
        }
    }
}
