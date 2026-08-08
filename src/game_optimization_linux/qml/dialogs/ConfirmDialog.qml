import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import ".." as App

Popup {
    id: dialog

    property string title: qsTr("Confirm action")
    property string message: qsTr("Do you want to continue?")
    property string acceptText: qsTr("Continue")
    property bool destructive: false
    property var payload: null
    signal confirmed(var payload)

    function ask(dialogTitle, dialogMessage, buttonText, isDestructive, actionPayload) {
        title = dialogTitle
        message = dialogMessage
        acceptText = buttonText || qsTr("Continue")
        destructive = Boolean(isDestructive)
        payload = actionPayload
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(460, parent ? parent.width - 40 : 460)
    height: contentColumn.implicitHeight + topPadding + bottomPadding
    padding: 22
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape

    Overlay.modal: Rectangle { color: App.Theme.modalScrim }

    background: Rectangle {
        radius: App.Theme.radiusLarge
        color: App.Theme.surface
        border.width: 1
        border.color: App.Theme.borderStrong
    }

    contentItem: ColumnLayout {
        id: contentColumn
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Rectangle {
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                radius: 13
                color: dialog.destructive ? App.Theme.dangerSoft : App.Theme.warningSoft

                Label {
                    anchors.centerIn: parent
                    text: dialog.destructive ? "!" : "?"
                    color: dialog.destructive ? App.Theme.danger : App.Theme.warning
                    font.pixelSize: 20
                    font.weight: Font.Bold
                }
            }

            Label {
                Layout.fillWidth: true
                text: dialog.title
                color: App.Theme.text
                font.pixelSize: 19
                font.weight: Font.Bold
                wrapMode: Text.WordWrap
            }
        }

        Label {
            Layout.fillWidth: true
            text: dialog.message
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.fontBody
            lineHeight: 1.25
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 4
            spacing: 10

            Item { Layout.fillWidth: true }

            AppButton {
                text: qsTr("Cancel")
                kind: "ghost"
                onClicked: dialog.close()
            }

            AppButton {
                text: dialog.acceptText
                kind: dialog.destructive ? "danger" : "primary"
                focus: true
                onClicked: {
                    var value = dialog.payload
                    dialog.close()
                    dialog.confirmed(value)
                }
            }
        }
    }
}
