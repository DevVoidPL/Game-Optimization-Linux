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
    readonly property string steamAppId: String(gameData && gameData.steamAppId || "")
    property var statusData: ({})
    property var planData: ({})
    property string archiveUrl: ""
    property string selectedExecutable: ""
    property string injectionDll: "auto"
    property bool replaceConfirmed: false
    property bool antiCheatConfirmed: false
    property bool showLocalArchive: false
    property string errorMessage: ""
    readonly property var archiveNameFilters: archiveDialog.nameFilters

    readonly property var injectionValues: ["auto", "dxgi.dll", "d3d12.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]
    readonly property var injectionLabels: [qsTr("Automatic"), "dxgi.dll", "d3d12.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]
    readonly property var executableCandidates: statusData.executableCandidates || []
    readonly property var executableLabels: executableCandidates.map(function(item) { return String(item.label || item.relativePath || "") })
    readonly property var executableValues: executableCandidates.map(function(item) { return String(item.relativePath || "") })
    readonly property string displayState: Boolean(planData.requiresConflictConfirmation)
                                                   ? "conflict"
                                                   : String(statusData.onlineState || statusData.installationState || "not_installed")

    padding: 18

    function stateLabel(state) {
        if (state === "installed") return qsTr("Installed")
        if (state === "update_available") return qsTr("Update available")
        if (state === "error") return qsTr("Error")
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

    function inspectOnline() {
        if (!controller || !controller.inspectOnlineOptiScaler)
            return
        var result = controller.inspectOnlineOptiScaler(
                    gameId, selectedExecutable, injectionDll,
                    antiCheatConfirmed) || ({})
        planData = result.success ? result : ({})
        replaceConfirmed = false
        errorMessage = result.success ? "" : String(result.error || qsTr("The official release could not be inspected"))
    }

    function refreshOnline() {
        if (!controller || !controller.refreshOptiScalerRelease)
            return
        if (!controller.refreshOptiScalerRelease(gameId, true))
            errorMessage = qsTr("The official release check could not be started")
    }

    function onlineOperation() {
        if (String(statusData.installationState || "") === "corrupt") return "repair"
        if (String(statusData.onlineState || "") === "update_available") return "update"
        if (Boolean(statusData.installed)) return "reinstall"
        return "install"
    }

    function installLabel() {
        var operation = onlineOperation()
        if (operation === "update") return qsTr("Update OptiScaler")
        if (operation === "repair") return qsTr("Repair OptiScaler")
        if (operation === "reinstall") return qsTr("Reinstall OptiScaler")
        return qsTr("Install OptiScaler")
    }

    function saveExecutable(value) {
        if (!controller || !controller.rememberOptiScalerExecutable || !value)
            return
        var result = controller.rememberOptiScalerExecutable(gameId, value) || ({})
        if (result.success) {
            statusData = result
            selectedExecutable = String(result.executable || "")
            planData = ({})
            errorMessage = ""
        } else {
            errorMessage = String(result.error || qsTr("The selected executable could not be saved"))
        }
    }

    function beginInstall() {
        if (!controller)
            return
        var accepted = false
        if (Boolean(planData.officialRelease) && controller.installOnlineOptiScaler) {
            accepted = controller.installOnlineOptiScaler(
                        gameId, selectedExecutable, injectionDll,
                        onlineOperation(), replaceConfirmed, antiCheatConfirmed)
        } else if (controller.installOptiScaler) {
            accepted = controller.installOptiScaler(
                        gameId, archiveUrl, selectedExecutable, injectionDll,
                        replaceConfirmed)
        }
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
                status: section.displayState === "update_available" ? "warning"
                        : section.statusData.installed ? "available"
                        : section.displayState === "corrupt" ? "error"
                        : section.displayState === "conflict" || section.displayState === "restore_required" ? "warning"
                        : "neutral"
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Game Optimization downloads OptiScaler only from the official GitHub repository, validates the archive, and installs controlled files next to the selected game executable.")
            color: App.Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Do not use OptiScaler in online or anti-cheat protected games unless you understand the account and compatibility risk.")
            color: App.Theme.warning
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Main executable")
            description: qsTr("Resolver confidence: %1").arg(String(section.statusData.executableConfidence || qsTr("Unknown")))
            AppComboBox {
                Layout.preferredWidth: 430
                model: section.executableLabels
                currentIndex: section.executableValues.indexOf(section.selectedExecutable)
                enabled: section.executableValues.length > 0
                onActivated: function(index) {
                    section.saveExecutable(section.executableValues[index])
                }
            }
            AppButton {
                text: qsTr("Choose executable")
                kind: "secondary"
                onClicked: executableDialog.open()
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
            title: qsTr("Official OptiScaler release")
            description: qsTr("Available version: %1 | Installed version: %2")
                         .arg(String(section.statusData.availableVersion || qsTr("Unknown")))
                         .arg(String(section.statusData.installedVersion || qsTr("Not installed")))
            RowLayout {
                AppButton { text: qsTr("Check online"); kind: "secondary"; onClicked: section.refreshOnline() }
                AppButton {
                    text: qsTr("Create installation plan")
                    enabled: Boolean(section.statusData.archiveReady) && section.selectedExecutable.length > 0
                    onClicked: section.inspectOnline()
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: Boolean(section.statusData.onlineError)
            text: String(section.statusData.onlineError || "")
            color: App.Theme.danger
            wrapMode: Text.WordWrap
        }

        AppSwitch {
            text: qsTr("Show local archive fallback")
            checked: section.showLocalArchive
            onToggled: section.showLocalArchive = checked
        }

        SettingRow {
            Layout.fillWidth: true
            visible: section.showLocalArchive
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
            Label { Layout.fillWidth: true; visible: Boolean(section.planData.officialRelease); text: qsTr("Source: official OptiScaler GitHub release"); color: App.Theme.accent }
            Label { Layout.fillWidth: true; visible: Boolean(section.planData.archiveSha256); text: "SHA-256: " + String(section.planData.archiveSha256 || ""); color: App.Theme.textMuted; font.family: "monospace"; elide: Text.ElideMiddle }
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
            Label { Layout.fillWidth: true; text: qsTr("Game Optimization does not run files from the archive."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
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
                          + (modelData.managedByGameOptimization
                             ? qsTr("Managed by Game Optimization")
                             : qsTr("Not managed by Game Optimization"))
                    color: App.Theme.warning
                    font.family: "monospace"
                    elide: Text.ElideRight
                }
            }
            AppSwitch {
                visible: Boolean(section.planData.requiresConflictConfirmation)
                text: qsTr("I understand the conflicts and allow backup and replacement of target files")
                checked: section.replaceConfirmed
                onToggled: section.replaceConfirmed = checked
            }
        }

        AppSwitch {
            visible: (section.planData.blockers || []).join(" ").indexOf("Anti-cheat") >= 0
                     || (section.planData.warnings || []).join(" ").indexOf("anti-cheat") >= 0
            text: qsTr("I understand the anti-cheat risk and want to prepare this installation manually")
            checked: section.antiCheatConfirmed
            onToggled: {
                section.antiCheatConfirmed = checked
                if (Boolean(section.planData.officialRelease))
                    section.inspectOnline()
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
                    text: Boolean(section.planData.officialRelease) ? section.installLabel() : qsTr("Install OptiScaler")
                    enabled: Boolean(section.planData.canInstall)
                             && (!Boolean(section.planData.requiresConflictConfirmation) || section.replaceConfirmed)
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
                AppButton { text: qsTr("Remove OptiScaler"); kind: "danger"; enabled: Boolean(section.statusData.manifestId); onClicked: removeDialog.ask(qsTr("Remove OptiScaler?"), qsTr("Only files recorded as created by Game Optimization will be deleted. Replaced files remain available for restoration."), qsTr("Remove"), true, "remove") }
                AppButton { text: qsTr("Restore previous files"); kind: "secondary"; enabled: (section.statusData.replacedFiles || []).length > 0; onClicked: removeDialog.ask(qsTr("Restore previous files?"), qsTr("Backed-up files will replace the matching Game Optimization-managed OptiScaler files after hash verification."), qsTr("Restore"), true, "restore") }
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

    FileDialog {
        id: executableDialog
        title: qsTr("Choose the main game executable")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Windows game executables (*.exe)")]
        onAccepted: section.saveExecutable(selectedFile.toString())
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
            if (String(appId) === section.steamAppId) {
                section.loadStatus()
                section.planData = ({})
            }
        }
    }

    onGameIdChanged: { selectedExecutable = ""; planData = ({}); antiCheatConfirmed = false; loadStatus() }
    Component.onCompleted: loadStatus()
}
