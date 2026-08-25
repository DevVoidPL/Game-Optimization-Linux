pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"
import ".." as App

Popup {
    id: dialog

    property var controller
    property string editingId: ""
    property string errorMessage: ""
    property bool advancedVisible: false
    property alias gameName: nameField.text
    signal saved(string gameId)

    function clearForm() {
        editingId = ""
        errorMessage = ""
        advancedVisible = false
        nameField.text = ""
        executableField.text = ""
        installDirectoryField.text = ""
        workingDirectoryField.text = ""
        argumentsField.text = ""
        runnerField.text = ""
        winePrefixField.text = ""
        portraitField.text = ""
        headerField.text = ""
        preLaunchField.text = ""
        postLaunchField.text = ""
        environmentModel.clear()
    }

    function openForAdd() {
        clearForm()
        open()
        nameField.forceActiveFocus()
    }

    function openForEdit(gameId) {
        clearForm()
        if (!controller || !controller.manualGameConfig) {
            errorMessage = qsTr("Manual game configuration is unavailable.")
            open()
            return
        }
        var result = controller.manualGameConfig(String(gameId || ""))
        if (!result || result.success !== true) {
            errorMessage = String(result && result.error
                                  ? result.error : qsTr("Manual game was not found."))
            open()
            return
        }
        editingId = String(result.id || "")
        nameField.text = String(result.name || "")
        executableField.text = String(result.executable || "")
        installDirectoryField.text = String(result.installDirectory || "")
        workingDirectoryField.text = String(result.workingDirectory || "")
        argumentsField.text = String(result.arguments || "")
        runnerField.text = String(result.runnerCommand || "")
        winePrefixField.text = String(result.winePrefix || "")
        portraitField.text = String(result.portraitArtwork || "")
        headerField.text = String(result.headerArtwork || "")
        preLaunchField.text = String(result.preLaunchCommand || "")
        postLaunchField.text = String(result.postLaunchCommand || "")
        var environment = result.environment || []
        for (var i = 0; i < environment.length; ++i)
            environmentModel.append({"keyText": String(environment[i].key || ""),
                                     "valueText": String(environment[i].value || "")})
        advancedVisible = environment.length > 0
                          || preLaunchField.text.length > 0
                          || postLaunchField.text.length > 0
        open()
        nameField.forceActiveFocus()
    }

    function saveGame() {
        errorMessage = ""
        var environment = []
        for (var i = 0; i < environmentModel.count; ++i) {
            var entry = environmentModel.get(i)
            environment.push({"key": String(entry.keyText || ""),
                              "value": String(entry.valueText || "")})
        }
        var result = controller && controller.saveManualGame
                ? controller.saveManualGame({
                    "id": editingId,
                    "name": nameField.text,
                    "executable": executableField.text,
                    "installDirectory": installDirectoryField.text,
                    "workingDirectory": workingDirectoryField.text,
                    "arguments": argumentsField.text,
                    "environment": environment,
                    "runnerCommand": runnerField.text,
                    "winePrefix": winePrefixField.text,
                    "preLaunchCommand": preLaunchField.text,
                    "postLaunchCommand": postLaunchField.text,
                    "portraitArtwork": portraitField.text,
                    "headerArtwork": headerField.text
                }) : ({"success": false,
                        "error": qsTr("Manual game storage is unavailable.")})
        if (!result || result.success !== true) {
            errorMessage = String(result && result.error
                                  ? result.error : qsTr("The game could not be saved."))
            return
        }
        var savedId = String(result.id || editingId)
        close()
        saved(savedId)
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(760, parent ? parent.width - 36 : 760)
    height: Math.min(820, parent ? parent.height - 36 : 820)
    padding: 20
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
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: dialog.editingId.length > 0 ? qsTr("Edit custom game")
                                                   : qsTr("Add custom game")
                color: App.Theme.text
                font.pixelSize: 21
                font.weight: Font.Bold
            }
            AppButton { text: qsTr("Cancel"); kind: "ghost"; compact: true; onClicked: dialog.close() }
            AppButton {
                objectName: "saveManualGameButton"
                text: dialog.editingId.length > 0 ? qsTr("Save") : qsTr("Add game")
                kind: "primary"
                compact: true
                enabled: nameField.text.trim().length > 0
                         && executableField.text.trim().length > 0
                onClicked: dialog.saveGame()
            }
        }

        Label {
            visible: dialog.errorMessage.length > 0
            Layout.fillWidth: true
            text: dialog.errorMessage
            color: App.Theme.danger
            wrapMode: Text.WordWrap
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: Math.max(1, parent.width - 12)
                spacing: 11

                Label { text: qsTr("Name *"); color: App.Theme.textSecondary }
                AppTextField { id: nameField; objectName: "manualGameNameField"; Layout.fillWidth: true }

                Label { text: qsTr("Executable / command *"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: executableField; objectName: "manualGameExecutableField"; Layout.fillWidth: true }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: executablePicker.open() }
                }

                Label { text: qsTr("Game directory"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: installDirectoryField; Layout.fillWidth: true; placeholderText: qsTr("Defaults to the executable directory") }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: installDirectoryPicker.open() }
                }

                Label { text: qsTr("Working directory"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: workingDirectoryField; Layout.fillWidth: true; placeholderText: qsTr("Defaults to the game directory") }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: workingDirectoryPicker.open() }
                }

                Label { text: qsTr("Arguments"); color: App.Theme.textSecondary }
                AppTextField { id: argumentsField; objectName: "manualGameArgumentsField"; Layout.fillWidth: true; placeholderText: qsTr("Example: --mode \"High quality\"") }

                Label { text: qsTr("Wine / Proton executable and options"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: runnerField; Layout.fillWidth: true; placeholderText: qsTr("Optional; kept separate from the game executable") }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: runnerPicker.open() }
                }

                Label { text: qsTr("Wine prefix"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: winePrefixField; Layout.fillWidth: true }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: winePrefixPicker.open() }
                }

                Label { text: qsTr("Portrait artwork"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: portraitField; Layout.fillWidth: true }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: portraitPicker.open() }
                }

                Label { text: qsTr("Header artwork"); color: App.Theme.textSecondary }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { id: headerField; Layout.fillWidth: true }
                    AppButton { text: qsTr("Browse…"); compact: true; onClicked: headerPicker.open() }
                }

                AppButton {
                    text: dialog.advancedVisible ? qsTr("Hide advanced") : qsTr("Advanced")
                    kind: "ghost"
                    onClicked: dialog.advancedVisible = !dialog.advancedVisible
                }

                ColumnLayout {
                    visible: dialog.advancedVisible
                    Layout.fillWidth: true
                    spacing: 9

                    Label { text: qsTr("Environment variables"); color: App.Theme.text; font.weight: Font.DemiBold }
                    Repeater {
                        model: ListModel { id: environmentModel }
                        delegate: RowLayout {
                            id: environmentRow
                            required property int index
                            required property string keyText
                            required property string valueText
                            Layout.fillWidth: true
                            AppTextField {
                                Layout.preferredWidth: 190
                                text: environmentRow.keyText
                                placeholderText: qsTr("KEY")
                                onTextChanged: environmentModel.setProperty(environmentRow.index, "keyText", text)
                            }
                            Label { text: "="; color: App.Theme.textMuted }
                            AppTextField {
                                Layout.fillWidth: true
                                text: environmentRow.valueText
                                placeholderText: qsTr("Value (may be empty)")
                                onTextChanged: environmentModel.setProperty(environmentRow.index, "valueText", text)
                            }
                            AppButton { text: qsTr("Remove"); compact: true; kind: "ghost"; onClicked: environmentModel.remove(environmentRow.index) }
                        }
                    }
                    AppButton {
                        text: qsTr("Add variable")
                        compact: true
                        kind: "secondary"
                        onClicked: environmentModel.append({"keyText": "", "valueText": ""})
                    }

                    Label { text: qsTr("Pre-launch command"); color: App.Theme.textSecondary }
                    AppTextField { id: preLaunchField; Layout.fillWidth: true; placeholderText: qsTr("Parsed as argv; the game stops if this fails") }

                    Label { text: qsTr("Post-launch command"); color: App.Theme.textSecondary }
                    AppTextField { id: postLaunchField; Layout.fillWidth: true; placeholderText: qsTr("Parsed as argv and run after the game exits") }
                }
            }
        }
    }

    FileDialog { id: executablePicker; title: qsTr("Select game executable"); fileMode: FileDialog.OpenFile; onAccepted: executableField.text = String(selectedFile) }
    FileDialog { id: runnerPicker; title: qsTr("Select Wine or Proton executable"); fileMode: FileDialog.OpenFile; onAccepted: runnerField.text = String(selectedFile) }
    FileDialog { id: portraitPicker; title: qsTr("Select portrait artwork"); fileMode: FileDialog.OpenFile; nameFilters: [qsTr("Images (*.png *.jpg *.jpeg *.webp)")]; onAccepted: portraitField.text = String(selectedFile) }
    FileDialog { id: headerPicker; title: qsTr("Select header artwork"); fileMode: FileDialog.OpenFile; nameFilters: [qsTr("Images (*.png *.jpg *.jpeg *.webp)")]; onAccepted: headerField.text = String(selectedFile) }
    FolderDialog { id: installDirectoryPicker; title: qsTr("Select game directory"); onAccepted: installDirectoryField.text = String(selectedFolder) }
    FolderDialog { id: workingDirectoryPicker; title: qsTr("Select working directory"); onAccepted: workingDirectoryField.text = String(selectedFolder) }
    FolderDialog { id: winePrefixPicker; title: qsTr("Select Wine prefix"); onAccepted: winePrefixField.text = String(selectedFolder) }
}
