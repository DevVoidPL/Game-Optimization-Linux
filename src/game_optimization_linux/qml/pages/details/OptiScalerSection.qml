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
    property bool showAdvanced: false
    property string releaseChannel: "stable"
    property string backend: "optiscaler"
    property string fsr4Mode: "automatic"
    property bool fsrAgilitySdkUpgrade: false
    property bool fsr4Watermark: false
    property string dx11Upscaler: "auto"
    property string dx12Upscaler: "auto"
    property string vulkanUpscaler: "auto"
    property string errorMessage: ""
    property bool forceStatusRefresh: false
    property string dismissedOperationConflict: ""
    readonly property var archiveNameFilters: archiveDialog.nameFilters

    readonly property var injectionValues: ["auto", "dxgi.dll", "d3d12.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]
    readonly property var injectionLabels: [qsTr("Automatic"), "dxgi.dll", "d3d12.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]
    readonly property var fsr4ModeValues: statusData.supportedFsr4Modes || []
    readonly property var fsr4ModeLabels: fsr4ModeValues.map(function(value) {
        if (value === "automatic") return qsTr("Automatic")
        if (value === "normal") return qsTr("FSR 4.1.1")
        if (value === "force_int8") return qsTr("FSR 4.1.1 INT8 - Experimental")
        return qsTr("Disabled")
    })
    readonly property var executableCandidates: statusData.executableCandidates || []
    readonly property var executableLabels: executableCandidates.map(function(item) { return String(item.label || item.relativePath || "") })
    readonly property var executableValues: executableCandidates.map(function(item) { return String(item.relativePath || "") })
    readonly property string displayState: Boolean(planData.requiresConflictConfirmation)
                                                   ? "conflict"
                                                   : String(statusData.onlineState || "") === "update_available"
                                                   ? "update_available"
                                                   : String(statusData.snapshotState || "unknown")

    padding: 18

    function stateLabel(state) {
        if (state === "installed") return qsTr("Installed")
        if (state === "update_available") return qsTr("Update available")
        if (state === "error") return qsTr("Error")
        if (state === "conflict") return qsTr("Conflict")
        if (state === "corrupt") return qsTr("Damaged installation")
        if (state === "partial") return qsTr("Partial installation")
        if (state === "unknown") return qsTr("Unknown")
        if (state === "restore_required") return qsTr("Previous files require restoration")
        if (state === "removed") return qsTr("Removed")
        return qsTr("Not installed")
    }

    function modeLabel(mode) {
        if (mode === "automatic") return qsTr("Automatic")
        if (mode === "normal") return qsTr("FSR 4.1.1")
        if (mode === "force_int8") return qsTr("FSR 4.1.1 INT8 - Experimental")
        if (mode === "disabled") return qsTr("Disabled")
        return qsTr("Unknown")
    }

    function scheduleStatus(force) {
        forceStatusRefresh = forceStatusRefresh || Boolean(force)
        statusRefreshTimer.restart()
    }

    function loadStatus() {
        if (!controller || !gameId)
            return
        var result = ({})
        if (controller.requestOptiScalerStatus)
            result = controller.requestOptiScalerStatus(gameId, forceStatusRefresh) || ({})
        else if (controller.getOptiScalerStatus)
            result = controller.getOptiScalerStatus(gameId) || ({})
        forceStatusRefresh = false
        if (!result.success) {
            errorMessage = qsTr("Status refresh failed: %1. Last known installation information is still shown.")
                           .arg(String(result.error || qsTr("No diagnostic was returned")))
            return
        }
        statusData = result
        if (result.loading || result.refreshing)
            statusRefreshTimer.restart()
        if (result.loading)
            return
        if (!selectedExecutable) {
            var selected = result.selectedExecutable || ({})
            selectedExecutable = String(result.executable || selected.relativePath || "")
        }
        if (result.injectionDll)
            injectionDll = String(result.injectionDll)
        releaseChannel = String(result.channel || "stable")
        backend = String(result.backend || "optiscaler")
        fsr4Mode = String(result.requestedFsr4Mode || result.fsr4Mode || "automatic")
        fsrAgilitySdkUpgrade = Boolean(result.fsrAgilitySdkUpgrade)
        fsr4Watermark = Boolean(result.fsr4Watermark)
        dx11Upscaler = String(result.dx11Upscaler || "auto")
        dx12Upscaler = String(result.dx12Upscaler || "auto")
        vulkanUpscaler = String(result.vulkanUpscaler || "auto")
        if (String(result.refreshError || "").length > 0)
            errorMessage = qsTr("Refresh error: %1. Last known installation information is shown.")
                           .arg(String(result.refreshError))
        else if (String(result.operationError || "").length > 0)
            errorMessage = String(result.operationError)
        else
            errorMessage = ""
    }

    function selectChannel(value) {
        if (!controller || !controller.setOptiScalerChannel)
            return
        var result = controller.setOptiScalerChannel(gameId, value) || ({})
        if (result.success) {
            releaseChannel = String(result.channel || value)
            planData = ({})
            errorMessage = ""
            scheduleStatus(true)
        } else {
            errorMessage = String(result.error || qsTr("The release channel could not be changed"))
        }
    }

    function selectBackend(value) {
        if (!controller || !controller.setOptiScalerBackend)
            return
        var result = controller.setOptiScalerBackend(gameId, value) || ({})
        if (result.success) {
            backend = String(result.backend || value)
            planData = ({})
            errorMessage = ""
            scheduleStatus(true)
        } else {
            errorMessage = String(result.error || qsTr("The backend could not be changed"))
        }
    }

    function applyUpscaling() {
        if (!controller)
            return
        var recommendation = statusData.recommendation || ({})
        var effectiveMode = fsr4Mode === "automatic"
                ? String(recommendation.recommendedMode || "disabled")
                : fsr4Mode
        if (["normal", "force_int8", "disabled"].indexOf(effectiveMode) < 0)
            effectiveMode = "disabled"
        var configuration = {
            "fsr4Mode": fsr4Mode,
            "effectiveFsr4Mode": effectiveMode,
            "automaticReason": fsr4Mode === "automatic"
                               ? String(recommendation.reason || "") : "",
            "fsrAgilitySdkUpgrade": fsrAgilitySdkUpgrade,
            "fsr4Watermark": fsr4Watermark,
            "dx11Upscaler": dx11Upscaler,
            "dx12Upscaler": dx12Upscaler,
            "vulkanUpscaler": vulkanUpscaler
        }
        if (!statusData.installed) {
            if (!statusData.archiveReady) {
                refreshOnline()
                errorMessage = qsTr("The official release check was started. Apply the recommendation when the download is ready.")
                return
            }
            if (!Boolean(planData.success))
                inspectOnline()
            if (!Boolean(planData.success))
                return
            if ((planData.blockers || []).length > 0) {
                errorMessage = (planData.blockers || []).join("\n")
                return
            }
            if (Boolean(planData.requiresConflictConfirmation) && !replaceConfirmed) {
                errorMessage = qsTr("Review the file conflict beside the install action and confirm replacement before continuing.")
                return
            }
            if (!controller.installAndConfigureOnlineOptiScaler)
                return
            var accepted = controller.installAndConfigureOnlineOptiScaler(
                        gameId, selectedExecutable, injectionDll, "install",
                        replaceConfirmed, antiCheatConfirmed, configuration)
            if (!accepted)
                errorMessage = qsTr("The OptiScaler install and configuration task could not be started")
            return
        }
        if (!controller.configureOptiScalerUpscaling)
            return
        var result = controller.configureOptiScalerUpscaling(gameId, configuration) || ({})
        if (result.success) {
            errorMessage = ""
            scheduleStatus(true)
        } else {
            errorMessage = String(result.error || qsTr("The OptiScaler configuration could not be saved"))
        }
    }

    function upscalingActionLabel() {
        if (!statusData.installed)
            return fsr4Mode === "automatic"
                    ? qsTr("Install OptiScaler + apply recommended FSR")
                    : qsTr("Install OptiScaler + apply selected FSR")
        if (fsr4Mode === "force_int8") return qsTr("Request FSR 4.1.1 INT8")
        if (fsr4Mode === "normal") return qsTr("Upgrade to FSR 4")
        if (fsr4Mode === "disabled") return qsTr("Disable FSR4 update")
        return qsTr("Apply automatic recommendation")
    }

    function inspectArchive() {
        if (!controller || !controller.inspectOptiScalerArchive || !archiveUrl)
            return
        var result = controller.inspectOptiScalerArchive(
                    gameId, archiveUrl, selectedExecutable, injectionDll, fsr4Mode) || ({})
        planData = result.success ? result : ({})
        replaceConfirmed = false
        errorMessage = result.success ? "" : String(result.error || qsTr("The archive could not be inspected"))
    }

    function inspectOnline() {
        if (!controller || !controller.inspectOnlineOptiScaler)
            return
        var result = controller.inspectOnlineOptiScaler(
                    gameId, selectedExecutable, injectionDll,
                    antiCheatConfirmed, fsr4Mode) || ({})
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
            selectedExecutable = String(value)
            planData = ({})
            errorMessage = ""
            scheduleStatus(true)
        } else {
            errorMessage = String(result.error || qsTr("The selected executable could not be saved"))
        }
    }

    function beginInstall() {
        if (!controller)
            return
        var recommendation = statusData.recommendation || ({})
        var configuration = {
            "fsr4Mode": fsr4Mode,
            "effectiveFsr4Mode": fsr4Mode === "automatic"
                               ? String(recommendation.recommendedMode || "disabled")
                               : fsr4Mode,
            "automaticReason": fsr4Mode === "automatic"
                               ? String(recommendation.reason || "") : "",
            "fsrAgilitySdkUpgrade": fsrAgilitySdkUpgrade,
            "fsr4Watermark": fsr4Watermark,
            "dx11Upscaler": dx11Upscaler,
            "dx12Upscaler": dx12Upscaler,
            "vulkanUpscaler": vulkanUpscaler
        }
        var accepted = false
        if (Boolean(planData.officialRelease) && controller.installOnlineOptiScaler) {
            accepted = controller.installOnlineOptiScaler(
                        gameId, selectedExecutable, injectionDll,
                        onlineOperation(), replaceConfirmed, antiCheatConfirmed,
                        configuration)
        } else if (controller.installOptiScaler) {
            accepted = controller.installOptiScaler(
                        gameId, archiveUrl, selectedExecutable, injectionDll,
                        replaceConfirmed, configuration)
        }
        if (!accepted)
            errorMessage = qsTr("The OptiScaler installation task could not be started")
    }

    function verifyInstallation() {
        if (!controller || !controller.verifyOptiScaler)
            return
        if (controller.verifyOptiScaler(gameId))
            errorMessage = ""
        else
            errorMessage = qsTr("The OptiScaler verification task could not be started")
    }

    contentItem: ColumnLayout {
        spacing: 11

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: qsTr("OptiScaler status")
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

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: upgradeColumn.implicitHeight + 24
            radius: App.Theme.radiusMedium
            color: App.Theme.surfaceHover
            border.width: 1
            border.color: App.Theme.border

            ColumnLayout {
                id: upgradeColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                Label {
                    text: qsTr("Upscaling upgrade")
                    color: App.Theme.text
                    font.pixelSize: 16
                    font.weight: Font.Bold
                }

                Label {
                    Layout.fillWidth: true
                    visible: String(section.statusData.installationVerificationSummary || "").length > 0
                    text: String(section.statusData.installationVerificationSummary || "")
                    color: ["missing_files", "corrupt_files", "verification_error"]
                           .indexOf(String(section.statusData.installationVerificationState || "")) >= 0
                           ? App.Theme.danger
                           : String(section.statusData.installationVerificationState || "") === "verified"
                           ? App.Theme.success : App.Theme.warning
                    wrapMode: Text.WordWrap
                }

                Repeater {
                    model: section.statusData.verificationIssues || []
                    delegate: Label {
                        required property var modelData
                        Layout.fillWidth: true
                        text: "• " + String(modelData.message || modelData.path || "")
                        color: App.Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    visible: section.showAdvanced
                    columns: 2
                    columnSpacing: 18
                    rowSpacing: 4
                    Label { text: qsTr("Game upscaler"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.gameUpscaler || qsTr("Unknown")); color: App.Theme.text }
                    Label { text: qsTr("Graphics API"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.graphicsApi || qsTr("Unknown")); color: App.Theme.text }
                    Label { text: qsTr("Runtime"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.runtime || qsTr("Unknown")); color: App.Theme.text }
                    Label { text: qsTr("Proton version"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.protonVersion || qsTr("Unknown")); color: App.Theme.text }
                    Label { text: qsTr("Gamescope"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.gamescopeEnabled) ? qsTr("Enabled") : qsTr("Disabled"); color: App.Theme.text }
                    Label { text: qsTr("GameMode"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.gameModeEnabled) ? qsTr("Enabled") : qsTr("Disabled"); color: App.Theme.text }
                    Label { text: qsTr("GPU"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.gpu || qsTr("Unknown")); color: App.Theme.text; elide: Text.ElideRight }
                    Label { text: qsTr("OptiScaler"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.installedVersion || section.statusData.availableVersion || qsTr("Unknown")); color: App.Theme.text }
                    Label { text: qsTr("Installation"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.installedStatus) ? qsTr("Installed") : qsTr("Not installed"); color: App.Theme.text }
                    Label { text: qsTr("Verification"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.verifiedStatus) ? qsTr("Verified") : qsTr("Not verified"); color: App.Theme.text }
                    Label { text: qsTr("Configuration"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.configurationVerifiedStatus) ? qsTr("Verified") : qsTr("Not verified"); color: App.Theme.text }
                    Label { text: qsTr("Source / channel"); color: App.Theme.textMuted }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("%1 · %2")
                              .arg(String(section.statusData.sourceLabel || qsTr("Official release available")))
                              .arg(String(section.statusData.installedChannel || section.statusData.channel || "stable"))
                        color: App.Theme.text
                        wrapMode: Text.WordWrap
                    }
                    Label { text: qsTr("Selected proxy"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.injectionDll || qsTr("Unknown")); color: App.Theme.text }
                    Label { text: qsTr("Managed proxy path"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.managedProxyPath || qsTr("Unknown")); color: App.Theme.text; elide: Text.ElideMiddle }
                    Label { text: qsTr("FidelityFX upscaler"); color: App.Theme.textMuted }
                    Label {
                        Layout.fillWidth: true
                        text: String(section.statusData.fidelityFxUpscalerVersion
                                     || section.statusData.availableFidelityFxUpscalerVersion
                                     || qsTr("Unknown"))
                        color: App.Theme.text
                    }
                    Label { text: qsTr("Recommended"); color: App.Theme.textMuted }
                    Label {
                        Layout.fillWidth: true
                        text: String((section.statusData.recommendation || {}).label || qsTr("Unknown"))
                        color: String((section.statusData.recommendation || {}).recommendedMode || "") === "force_int8"
                               ? App.Theme.warning : App.Theme.accent
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                    }
                }

                Label {
                    Layout.fillWidth: true
                    text: String((section.statusData.recommendation || {}).reason || "")
                    color: App.Theme.textSecondary
                    wrapMode: Text.WordWrap
                }

                Label {
                    Layout.fillWidth: true
                    text: {
                        var caps = section.statusData.availableIniCapabilities
                                   || section.statusData.iniCapabilities || ({})
                        var state = String(caps.forceInt8State || "unknown")
                        if (state === "supported")
                            return qsTr("FSR 4.1.1 INT8: Available in selected OptiScaler release")
                        if (state === "unsupported")
                            return qsTr("FSR 4.1.1 INT8: Disabled - not supported by selected OptiScaler release")
                        return qsTr("FSR 4.1.1 INT8: Unknown until the selected release is inspected")
                    }
                    color: {
                        var caps = section.statusData.availableIniCapabilities
                                   || section.statusData.iniCapabilities || ({})
                        return String(caps.forceInt8State || "unknown") === "unsupported"
                               ? App.Theme.warning : App.Theme.textSecondary
                    }
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    AppComboBox {
                        Layout.preferredWidth: 300
                        model: section.fsr4ModeLabels
                        currentIndex: Math.max(0, section.fsr4ModeValues.indexOf(section.fsr4Mode))
                        enabled: section.fsr4ModeValues.length > 0
                        onActivated: function(index) { section.fsr4Mode = section.fsr4ModeValues[index] }
                    }
                    AppButton {
                        objectName: "applyOptiScalerFsr4Button"
                        text: section.upscalingActionLabel()
                        iconSource: App.UiIcons.actionApply
                        enabled: section.fsr4ModeValues.length > 0
                                 && (Boolean(section.statusData.installed)
                                     || section.selectedExecutable.length > 0)
                        onClicked: section.applyUpscaling()
                    }
                    AppButton {
                        visible: Boolean(section.statusData.installed)
                        text: qsTr("Launch game")
                        iconSource: App.UiIcons.actionLaunch
                        kind: "secondary"
                        onClicked: if (section.controller && section.controller.launchGame) section.controller.launchGame(section.gameId)
                    }
                    Item { Layout.fillWidth: true }
                }

                GridLayout {
                    Layout.fillWidth: true
                    visible: section.showAdvanced
                    columns: 2
                    columnSpacing: 14
                    rowSpacing: 3
                    Label { text: qsTr("Installed"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.fsr4AssetsInstalled) ? qsTr("FSR assets present") : qsTr("Not confirmed"); color: App.Theme.textSecondary }
                    Label { text: qsTr("Requested"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: section.modeLabel(String(section.statusData.requestedFsr4Mode || "unknown")); color: App.Theme.textSecondary }
                    Label { text: qsTr("Configured in INI"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: Boolean(section.statusData.configurationApplied) ? section.modeLabel(String(section.statusData.effectiveConfiguredFsr4Mode || "unknown")) : qsTr("Unknown"); color: App.Theme.textSecondary }
                    Label { text: qsTr("Runtime verified"); color: App.Theme.textMuted }
                    Label { Layout.fillWidth: true; text: String(section.statusData.runtimeVerificationLabel || qsTr("Runtime verification required")); color: App.Theme.textSecondary }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: (section.planData.conflicts || []).length > 0
                    spacing: 5
                    Label { text: qsTr("Installation needs confirmation"); color: App.Theme.warning; font.weight: Font.Bold }
                    Repeater {
                        model: section.planData.conflicts || []
                        Label {
                            required property var modelData
                            Layout.fillWidth: true
                            text: "• " + String(modelData.relativePath || "") + " - "
                                  + (modelData.managedByGameOptimization
                                     ? qsTr("existing GOL-managed file")
                                     : qsTr("existing file will be backed up"))
                            color: App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                    }
                    AppSwitch {
                        visible: Boolean(section.planData.requiresConflictConfirmation)
                        text: qsTr("Back up and replace the listed files")
                        checked: section.replaceConfirmed
                        onToggled: section.replaceConfirmed = checked
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: Boolean((section.statusData.operationConflict || {}).message)
                             && String((section.statusData.operationConflict || {}).message)
                                !== section.dismissedOperationConflict
                    spacing: 5
                    Label { text: qsTr("Removal needs review"); color: App.Theme.warning; font.weight: Font.Bold }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Modified managed file: %1").arg(String((section.statusData.operationConflict || {}).path || qsTr("Unknown")))
                        color: App.Theme.warning
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("The file was preserved because it does not match either the managed payload or a verified original backup.")
                        color: App.Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        AppButton {
                            text: qsTr("Cancel")
                            kind: "secondary"
                            onClicked: section.dismissedOperationConflict = String((section.statusData.operationConflict || {}).message || "")
                        }
                        AppButton {
                            text: qsTr("Review conflict")
                            kind: "secondary"
                            onClicked: if (section.controller && section.controller.openOptiScalerManifest) section.controller.openOptiScalerManifest(section.gameId)
                        }
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: section.fsr4Mode === "force_int8"
                    text: qsTr("Force INT8 is experimental. The game may fail to start or OptiScaler may fall back to FSR3; use the verification watermark.")
                    color: App.Theme.warning
                    wrapMode: Text.WordWrap
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 7

            SettingRow {
                Layout.fillWidth: true
                title: qsTr("Release channel")
                description: qsTr("Stable uses the official stable OptiScaler release. Nightly always fetches the newest qualified official prerelease and is experimental.")
                AppComboBox {
                    Layout.preferredWidth: 220
                    model: [qsTr("Stable"), qsTr("Nightly (experimental)")]
                    currentIndex: section.releaseChannel === "edge" ? 1 : 0
                    onActivated: function(index) { section.selectChannel(index === 1 ? "edge" : "stable") }
                }
            }

            SettingRow {
                Layout.fillWidth: true
                visible: Boolean(section.statusData.optipatcher)
                title: qsTr("OptiPatcher")
                description: qsTr("ASI plugin status is reported from the managed manifest. Compatibility remains unknown unless upstream provides a matching game/build entry.")
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: {
                            var item = section.statusData.optipatcher || ({})
                            if (item.state === "blocked") return qsTr("Unavailable: %1").arg(String(item.error || qsTr("OptiScaler is not installed")))
                            if (item.state === "conflict") return qsTr("Conflict")
                            if (item.installed) return qsTr("Installed · %1").arg(String(item.version || qsTr("Unknown")))
                            return qsTr("Not installed")
                        }
                        color: App.Theme.textSecondary
                    }
                    Label {
                        text: qsTr("Version: %1").arg(String((section.statusData.optipatcher || ({})).version || qsTr("Not installed")))
                        color: App.Theme.textMuted
                    }
                    Label {
                        text: qsTr("Available: %1").arg(String((section.statusData.optipatcher || ({})).availableVersion || qsTr("Unknown")))
                        color: App.Theme.textMuted
                    }
                    AppButton {
                        text: Boolean((section.statusData.optipatcher || ({})).installed) ? qsTr("Update") : qsTr("Install")
                        kind: "secondary"
                        enabled: Boolean(section.controller && section.controller.installOptiPatcher)
                                 && String((section.statusData.optipatcher || ({})).state || "") !== "blocked"
                                 && String((section.statusData.optipatcher || ({})).state || "") !== "conflict"
                        onClicked: {
                            var result = section.controller.installOptiPatcher(section.gameId, true) || ({})
                            if (!result.success)
                                section.errorMessage = String(result.error || qsTr("OptiPatcher installation failed"))
                            section.scheduleStatus(true)
                        }
                    }
                    AppButton {
                        text: qsTr("Remove")
                        kind: "danger"
                        visible: Boolean((section.statusData.optipatcher || ({})).installed)
                        enabled: Boolean(section.controller && section.controller.removeOptiPatcher)
                        onClicked: {
                            var result = section.controller.removeOptiPatcher(section.gameId) || ({})
                            if (!result.success)
                                section.errorMessage = String(result.error || qsTr("OptiPatcher removal failed"))
                            section.scheduleStatus(true)
                        }
                    }
                }
            }
        }

        AppButton {
            text: section.showAdvanced ? qsTr("Hide advanced") : qsTr("Advanced")
            iconSource: App.UiIcons.actionAdvancedSettings
            kind: "secondary"
            onClicked: section.showAdvanced = !section.showAdvanced
        }

        SettingRow {
            Layout.fillWidth: true
            visible: section.showAdvanced || section.selectedExecutable.length === 0
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

        ColumnLayout {
            Layout.fillWidth: true
            visible: section.showAdvanced
            spacing: 7

            Label {
                text: qsTr("Advanced upscaling settings")
                color: App.Theme.text
                font.pixelSize: 16
                font.weight: Font.Bold
            }

            SettingRow {
                Layout.fillWidth: true
                title: qsTr("Runtime backend")
                description: qsTr("Only one proxy backend may be active for a game. DLSS Enabler is detection-only until its installer is verified.")
                AppComboBox {
                    Layout.preferredWidth: 220
                    model: [qsTr("Disabled"), qsTr("OptiScaler"), qsTr("DLSS Enabler (detection only)")]
                    currentIndex: backend === "none" ? 0 : backend === "dlss_enabler" ? 2 : 1
                    onActivated: function(index) {
                        if (index === 2) {
                            errorMessage = qsTr("DLSS Enabler is detected only; no files were changed.")
                            return
                        }
                        section.selectBackend(index === 0 ? "none" : "optiscaler")
                    }
                }
            }

            SettingRow {
                Layout.fillWidth: true
                visible: Boolean((section.statusData.iniCapabilities || {}).agilitySdkUpgrade)
                title: qsTr("FSR Agility SDK upgrade")
                description: qsTr("Requests OptiScaler's D3D12 Agility SDK upgrade. The matching D3D12_OptiScaler payload must be present.")
                AppSwitch {
                    checked: section.fsrAgilitySdkUpgrade
                    onToggled: section.fsrAgilitySdkUpgrade = checked
                }
            }

            SettingRow {
                Layout.fillWidth: true
                visible: Boolean((section.statusData.iniCapabilities || {}).watermark)
                title: qsTr("FSR4 verification watermark")
                description: qsTr("Requested: %1. Effective INI: %2. Runtime overlay: %3.")
                             .arg(String(section.statusData.watermarkRequestedLabel || qsTr("Unknown")))
                             .arg(String(section.statusData.watermarkEffectiveIniLabel || qsTr("Unknown")))
                             .arg(String(section.statusData.runtimeOverlayLabel || qsTr("Unknown")))
                AppSwitch {
                    checked: section.fsr4Watermark
                    onToggled: section.fsr4Watermark = checked
                }
            }

            SettingRow {
                Layout.fillWidth: true
                visible: ((section.statusData.iniCapabilities || {}).dx11Upscalers || []).length > 0
                title: qsTr("Preferred DirectX 11 upscaler")
                AppComboBox {
                    Layout.preferredWidth: 220
                    model: (section.statusData.iniCapabilities || {}).dx11Upscalers || []
                    currentIndex: Math.max(0, model.indexOf(section.dx11Upscaler))
                    onActivated: function(index) { section.dx11Upscaler = model[index] }
                }
            }

            SettingRow {
                Layout.fillWidth: true
                visible: ((section.statusData.iniCapabilities || {}).dx12Upscalers || []).length > 0
                title: qsTr("Preferred DirectX 12 upscaler")
                AppComboBox {
                    Layout.preferredWidth: 220
                    model: (section.statusData.iniCapabilities || {}).dx12Upscalers || []
                    currentIndex: Math.max(0, model.indexOf(section.dx12Upscaler))
                    onActivated: function(index) { section.dx12Upscaler = model[index] }
                }
            }

            SettingRow {
                Layout.fillWidth: true
                visible: ((section.statusData.iniCapabilities || {}).vulkanUpscalers || []).length > 0
                title: qsTr("Preferred Vulkan upscaler")
                description: qsTr("Only values advertised by the installed OptiScaler INI are shown.")
                AppComboBox {
                    Layout.preferredWidth: 220
                    model: (section.statusData.iniCapabilities || {}).vulkanUpscalers || []
                    currentIndex: Math.max(0, model.indexOf(section.vulkanUpscaler))
                    onActivated: function(index) { section.vulkanUpscaler = model[index] }
                }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("Official OptiScaler release")
            description: qsTr("Available version: %1 | Installed version: %2")
                         .arg(String(section.statusData.availableVersion || qsTr("Unknown")))
                         .arg(String(section.statusData.installedVersion || qsTr("Not installed")))
            RowLayout {
                AppButton { text: qsTr("Check online"); iconSource: App.UiIcons.actionRefresh; kind: "secondary"; onClicked: section.refreshOnline() }
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
            visible: section.showAdvanced
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
                    visible: Boolean(section.statusData.installed) || section.showLocalArchive
                    text: Boolean(section.planData.officialRelease) ? section.installLabel() : qsTr("Install OptiScaler")
                    iconSource: App.UiIcons.actionInstall
                    enabled: Boolean(section.planData.canInstall)
                             && (!Boolean(section.planData.requiresConflictConfirmation) || section.replaceConfirmed)
                    onClicked: section.beginInstall()
                }
                AppButton { text: qsTr("Verify installation"); iconSource: App.UiIcons.actionVerify; kind: "secondary"; enabled: Boolean(section.statusData.manifestId); onClicked: section.verifyInstallation() }
                Item { Layout.fillWidth: true }
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton { text: qsTr("Open directory"); iconSource: App.UiIcons.fileFolder; kind: "secondary"; enabled: Boolean(section.statusData.installDirectory); onClicked: section.controller.openOptiScalerDirectory(section.gameId) }
                AppButton { text: qsTr("Show manifest"); iconSource: App.UiIcons.fileFile; kind: "secondary"; enabled: Boolean(section.statusData.manifestPath); onClicked: section.controller.openOptiScalerManifest(section.gameId) }
                Item { Layout.fillWidth: true }
                AppButton { text: qsTr("Remove OptiScaler"); iconSource: App.UiIcons.actionRemove; kind: "danger"; enabled: Boolean(section.statusData.manifestId); onClicked: removeDialog.ask(qsTr("Remove OptiScaler?"), qsTr("GOL-created files will be removed and verified original game files will be restored. Unknown modified binaries are preserved and block removal for review."), qsTr("Remove"), true, "remove") }
                AppButton { text: qsTr("Restore previous files"); iconSource: App.UiIcons.actionRestore; kind: "secondary"; enabled: ["partial", "restore_required"].indexOf(String(section.statusData.installationState || "")) >= 0 && (section.statusData.replacedFiles || []).length > 0; onClicked: removeDialog.ask(qsTr("Restore previous files?"), qsTr("Backed-up files will replace the matching Game Optimization-managed OptiScaler files after hash verification."), qsTr("Restore"), true, "restore") }
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
                section.planData = ({})
                section.scheduleStatus(false)
            }
        }
        function onOptiScalerStatusChanged(changedGameId, result) {
            if (String(changedGameId) !== section.gameId || !result)
                return
            if (!result.success) {
                section.errorMessage = qsTr("Status refresh failed: %1. Last known installation information is still shown.")
                                       .arg(String(result.error || qsTr("No diagnostic was returned")))
                return
            }
            section.statusData = result
            section.loadStatus()
        }
    }

    Timer {
        id: statusRefreshTimer
        interval: 35
        repeat: false
        onTriggered: section.loadStatus()
    }

    onGameIdChanged: { selectedExecutable = ""; planData = ({}); antiCheatConfirmed = false; dismissedOperationConflict = ""; scheduleStatus(false) }
    Component.onCompleted: scheduleStatus(false)
}
