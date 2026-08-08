pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../dialogs"
import "../.." as App

SurfaceCard {
    id: section

    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData && gameData.id || "")
    property var statusData: ({})
    property var planData: ({})
    property string archiveUrl: ""
    property string selectedExecutable: ""
    property string injectionDll: "auto"
    property bool replaceConfirmed: false
    property string errorMessage: ""
    readonly property var archiveNameFilters: archiveDialog.nameFilters

    readonly property var injectionValues: ["auto", "dxgi.dll", "d3d12.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]
    readonly property var injectionLabels: [qsTr("Automatic"), "dxgi.dll", "d3d12.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]
    readonly property var executableCandidates: statusData.executableCandidates || []
    readonly property var executableLabels: executableCandidates.map(function(item) { return String(item.label || item.relativePath || "") })
    readonly property var executableValues: executableCandidates.map(function(item) { return String(item.relativePath || "") })
    readonly property string displayState: (planData.conflicts || []).length > 0
                                                   ? "conflict"
                                                   : String(statusData.installationState || "not_installed")

    padding: 18

    function stateLabel(state) {
        if (state === "installed") return qsTr("Installed")
        if (state === "conflict") return qsTr("Conflict")
        if (state === "corrupt") return qsTr("Damaged installation")
        if (state === "restore_required") return qsTr("Previous files require restoration")
        if (state === "removed") return qsTr("Removed")
        return qsTr("Not installed")
    }

    function loadStatus() {
        if (!controller || !controller.getOptiScalerStatus || !gameId)
            return
        var result = controller.getOptiScalerStatus(gameId) || ({})
        if (!result.success) {
            errorMessage = String(result.error || qsTr("OptiScaler status is unavailable"))
            return
        }
        statusData = result
        if (!selectedExecutable) {
            var selected = result.selectedExecutable || ({})
            selectedExecutable = String(result.executable || selected.relativePath || "")
        }
        if (result.injectionDll)
            injectionDll = String(result.injectionDll)
        errorMessage = ""
    }

    function inspectArchive() {
        if (!controller || !controller.inspectOptiScalerArchive || !archiveUrl)
            return
        var result = controller.inspectOptiScalerArchive(
                    gameId, archiveUrl, selectedExecutable, injectionDll) || ({})
        planData = result.success ? result : ({})
        replaceConfirmed = false
        errorMessage = result.success ? "" : String(result.error || qsTr("The archive could not be inspected"))
    }

    function beginInstall() {
        if (!controller || !controller.installOptiScaler)
            return
        var accepted = controller.installOptiScaler(
                    gameId, archiveUrl, selectedExecutable, injectionDll,
                    replaceConfirmed)
        if (!accepted)
            errorMessage = qsTr("The OptiScaler installation task could not be started")
    }

    function verifyInstallation() {
        if (!controller || !controller.verifyOptiScaler)
            return
        var result = controller.verifyOptiScaler(gameId) || ({})
        if (result.success) {
            statusData = result
            errorMessage = ""
        } else {
            errorMessage = String(result.error || qsTr("The OptiScaler installation could not be verified"))
        }
    }

    contentItem: ColumnLayout {
        spacing: 11

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: qsTr("Image scaling") + "  ·  OptiScaler"
                color: App.Theme.text
                font.pixelSize: 18
                font.weight: Font.Bold
            }
            StatusBadge {
                text: section.stateLabel(section.displayState)
                status: section.statusData.installed ? "available"
                        : section.displayState === "corrupt" ? "error"
                        : section.displayState === "conflict" || section.displayState === "restore_required" ? "warning"
                        : "neutral"
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Install OptiScaler from a local release archive next to the selected game executable. No file is downloaded or executed by GameForge.")
            color: App.Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Main executable")
            description: qsTr("Resolver confidence: %1").arg(String(section.statusData.executableConfidence || qsTr("Unknown")))
            AppComboBox {
                Layout.preferredWidth: 430
                model: section.executableLabels
                currentIndex: Math.max(0, section.executableValues.indexOf(section.selectedExecutable))
                enabled: section.executableValues.length > 0
                onActivated: function(index) {
                    section.selectedExecutable = section.executableValues[index]
                    section.planData = ({})
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: String(section.statusData.executableStatus || "") === "ambiguous"
            text: qsTr("The executable result is ambiguous. Choose the main game executable before installation.")
            color: App.Theme.warning
            wrapMode: Text.WordWrap
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Local OptiScaler archive")
            description: section.archiveUrl || qsTr("Choose an OptiScaler archive")
            RowLayout {
                AppButton { text: qsTr("Choose archive"); kind: "secondary"; onClicked: archiveDialog.open() }
                AppButton { text: qsTr("Check compatibility"); enabled: section.archiveUrl.length > 0 && section.selectedExecutable.length > 0; onClicked: section.inspectArchive() }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Proxy DLL")
            description: section.planData.success
                         ? qsTr("OptiScaler.dll will be installed as %1 next to %2.")
                               .arg(String(section.planData.injectionDll || ""))
                               .arg(String(section.planData.executableName || ""))
                         : qsTr("Automatic mode prefers dxgi.dll as a starting point; compatibility is not guaranteed.")
            AppComboBox {
                Layout.preferredWidth: 220
                model: section.injectionLabels
                currentIndex: Math.max(0, section.injectionValues.indexOf(section.injectionDll))
                onActivated: function(index) {
                    section.injectionDll = section.injectionValues[index]
                    section.planData = ({})
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: Boolean(section.planData.success)
            spacing: 7

            Label { text: qsTr("Validated installation plan"); color: App.Theme.text; font.pixelSize: 16; font.weight: Font.Bold }
            Label { Layout.fillWidth: true; text: qsTr("Detected OptiScaler version: %1").arg(String(section.planData.version || qsTr("Unknown"))); color: App.Theme.textSecondary }
            Label { Layout.fillWidth: true; text: qsTr("Archive format: %1").arg(String(section.planData.archiveFormat || "")); color: App.Theme.textSecondary }
            Label { Layout.fillWidth: true; text: qsTr("Selected executable: %1").arg(String(section.planData.executable || "")); color: App.Theme.textSecondary; font.family: "monospace"; elide: Text.ElideMiddle }
            Label { Layout.fillWidth: true; text: qsTr("Target directory: %1").arg(String(section.planData.installDirectory || "")); color: App.Theme.textSecondary; font.family: "monospace"; elide: Text.ElideMiddle }
            Label { Layout.fillWidth: true; text: qsTr("Proxy DLL: %1").arg(String(section.planData.injectionDll || "")); color: App.Theme.textSecondary }
            Label {
                Layout.fillWidth: true
                text: qsTr("OptiScaler.dll will be installed as %1 next to %2.")
                        .arg(String(section.planData.injectionDll || ""))
                        .arg(String(section.planData.executableName || ""))
                color: App.Theme.accent
                wrapMode: Text.WordWrap
            }
            Label { Layout.fillWidth: true; text: "WINEDLLOVERRIDES=" + String(section.planData.protonOverride || ""); color: App.Theme.textSecondary; font.family: "monospace" }
            Label { Layout.fillWidth: true; text: qsTr("Backup location: %1").arg(String(section.planData.backupDirectory || "")); color: App.Theme.textMuted; font.family: "monospace"; elide: Text.ElideMiddle }
            Label { Layout.fillWidth: true; text: qsTr("GameForge does not run files from the archive."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
        }

        Label {
            Layout.fillWidth: true
            visible: Boolean(section.planData.installDirectory || section.statusData.installDirectory)
            text: qsTr("Installation directory: %1").arg(String(section.planData.installDirectory || section.statusData.installDirectory || ""))
            color: App.Theme.textMuted
            font.family: "monospace"
            elide: Text.ElideMiddle
        }

        Label {
            Layout.fillWidth: true
            visible: Boolean(section.statusData.installedVersion)
            text: qsTr("Installed version: %1 · Proxy: %2 · Proton override: %3")
                    .arg(String(section.statusData.installedVersion || qsTr("Unknown")))
                    .arg(String(section.statusData.injectionDll || ""))
                    .arg(String(section.statusData.protonOverride || qsTr("None")))
            color: App.Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: (section.planData.conflicts || []).length > 0
            spacing: 5
            Label { text: qsTr("Detected conflicts"); color: App.Theme.warning; font.weight: Font.Bold }
            Repeater {
                model: section.planData.conflicts || []
                Label {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "• " + String(modelData.relativePath || "") + "  "
                          + String(modelData.sha256 || "").slice(0, 12) + "  "
                          + (modelData.managedByGameForge
                             ? qsTr("Managed by GameForge")
                             : qsTr("Not managed by GameForge"))
                    color: App.Theme.warning
                    font.family: "monospace"
                    elide: Text.ElideRight
                }
            }
            AppSwitch {
                text: qsTr("I understand the conflicts and allow backup and replacement of target files")
                checked: section.replaceConfirmed
                onToggled: section.replaceConfirmed = checked
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: (section.statusData.installedFiles || []).length > 0
            spacing: 5
            Label {
                text: qsTr("Installed files (%1)").arg((section.statusData.installedFiles || []).length)
                color: App.Theme.text
                font.weight: Font.DemiBold
            }
            Repeater {
                model: (section.statusData.installedFiles || []).slice(0, 12)
                Label {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "• " + String(modelData.relative_path || "") + "  " + String(modelData.after_sha256 || "").slice(0, 12)
                    color: App.Theme.textSecondary
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: (section.planData.filesToAdd || []).length > 0
            spacing: 5
            Label { text: qsTr("Files to add (%1)").arg((section.planData.filesToAdd || []).length); color: App.Theme.text; font.weight: Font.DemiBold }
            Repeater {
                model: (section.planData.filesToAdd || []).slice(0, 12)
                Label {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "• " + String(modelData.targetRelativePath || "")
                    color: App.Theme.textSecondary
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: (section.planData.filesToReplace || []).length > 0
            spacing: 5
            Label { text: qsTr("Files to replace (%1)").arg((section.planData.filesToReplace || []).length); color: App.Theme.warning; font.weight: Font.DemiBold }
            Repeater {
                model: (section.planData.filesToReplace || []).slice(0, 12)
                Label {
                    required property var modelData
                    Layout.fillWidth: true
                    text: "• " + String(modelData.targetRelativePath || "") + "  " + String(modelData.existingSha256 || "").slice(0, 12)
                    color: App.Theme.warning
                    font.family: "monospace"
                    elide: Text.ElideMiddle
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: (section.planData.blockers || []).length > 0
            text: (section.planData.blockers || []).join("\n")
            color: App.Theme.danger
            wrapMode: Text.WordWrap
        }

        Label {
            Layout.fillWidth: true
            visible: Boolean(section.statusData.installed)
            text: qsTr("Launch the game and press Insert to check the OptiScaler menu.")
            color: App.Theme.accent
            wrapMode: Text.WordWrap
        }
        Label {
            Layout.fillWidth: true
            visible: Boolean(section.statusData.manifestPath)
            text: qsTr("Manifest: %1").arg(String(section.statusData.manifestPath || ""))
            color: App.Theme.textMuted
            font.family: "monospace"
            elide: Text.ElideMiddle
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    objectName: "installOptiScalerButton"
                    text: qsTr("Install OptiScaler")
                    enabled: Boolean(section.planData.canInstall)
                             && (!(section.planData.conflicts || []).length || section.replaceConfirmed)
                    onClicked: section.beginInstall()
                }
                AppButton { text: qsTr("Launch game"); kind: "secondary"; enabled: Boolean(section.statusData.installed); onClicked: if (section.controller && section.controller.launchGame) section.controller.launchGame(section.gameId) }
                AppButton { text: qsTr("Verify installation"); kind: "secondary"; enabled: Boolean(section.statusData.manifestId); onClicked: section.verifyInstallation() }
                Item { Layout.fillWidth: true }
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton { text: qsTr("Open directory"); kind: "secondary"; enabled: Boolean(section.statusData.installDirectory); onClicked: section.controller.openOptiScalerDirectory(section.gameId) }
                AppButton { text: qsTr("Show manifest"); kind: "secondary"; enabled: Boolean(section.statusData.manifestPath); onClicked: section.controller.openOptiScalerManifest(section.gameId) }
                Item { Layout.fillWidth: true }
                AppButton { text: qsTr("Remove OptiScaler"); kind: "danger"; enabled: Boolean(section.statusData.manifestId); onClicked: removeDialog.ask(qsTr("Remove OptiScaler?"), qsTr("Only files recorded as created by GameForge will be deleted. Replaced files remain available for restoration."), qsTr("Remove"), true, "remove") }
                AppButton { text: qsTr("Restore previous files"); kind: "secondary"; enabled: (section.statusData.replacedFiles || []).length > 0; onClicked: removeDialog.ask(qsTr("Restore previous files?"), qsTr("Backed-up files will replace the matching GameForge-managed OptiScaler files after hash verification."), qsTr("Restore"), true, "restore") }
            }
        }

        Label { Layout.fillWidth: true; visible: section.errorMessage.length > 0; text: section.errorMessage; color: App.Theme.danger; wrapMode: Text.WordWrap }
    }

    FileDialog {
        id: archiveDialog
        objectName: "optiscalerArchiveDialog"
        title: qsTr("Choose an OptiScaler archive")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("OptiScaler archives (*.7z *.zip)")]
        onAccepted: {
            section.archiveUrl = selectedFile.toString()
            section.planData = ({})
            section.errorMessage = ""
        }
    }

    ConfirmDialog {
        id: removeDialog
        onConfirmed: function(action) {
            if (action === "remove" && section.controller && section.controller.removeOptiScaler)
                section.controller.removeOptiScaler(section.gameId)
            else if (action === "restore" && section.controller && section.controller.restoreOptiScalerFiles)
                section.controller.restoreOptiScalerFiles(section.gameId)
        }
    }

    Connections {
        target: section.controller || null
        ignoreUnknownSignals: true
        function onOptiScalerChanged(appId) {
            if (String(appId) === String(section.statusData.appId || "")) {
                section.loadStatus()
                section.planData = ({})
            }
        }
    }

    onGameIdChanged: { selectedExecutable = ""; planData = ({}); loadStatus() }
    Component.onCompleted: loadStatus()
}
