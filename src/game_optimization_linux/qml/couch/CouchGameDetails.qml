pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchGameDetails"
    property var controller
    property var navigation
    property real couchScale: 1.0
    property var game: controller && controller.selectedGame ? controller.selectedGame : ({})
    property int selectedTab: 0
    property int selectedAction: 0
    // Focus areas in visual order: 0 header cards (Launch + features),
    // 1 tabs, 2 actions of the active tab, 3 scrollable tab content.
    property int focusArea: 0
    property int headerIndex: 0
    readonly property bool headerFocus: focusArea === 0
    readonly property bool tabFocus: focusArea === 1
    readonly property bool actionFocus: focusArea === 2
    readonly property bool contentFocus: focusArea === 3
    property bool launchPending: false
    property string selectedProfile: "Auto"
    property string optimizationProfile: "automatic"
    property bool gameModeEnabled: false
    property bool gamescopeEnabled: false
    property bool mangoHudEnabled: false
    property int fpsLimit: 0
    property string targetResolution: qsTr("Native")
    property var pendingPlan: ({})
    property bool confirmationOpen: false
    property string confirmationKind: ""
    property int confirmationChoice: 0
    property bool mangoHudOverlayOpen: false
    property int mangoHudRow: 0
    property var mangoHudProfile: ({})
    property string mangoHudPreset: "disabled"
    property string mangoHudPosition: "top-left"
    property int mangoHudFontSize: 24
    property int mangoHudFpsLimit: 0
    property bool mangoHudTemperatures: false
    property bool mangoHudMemory: false
    property bool optimizationOverlayOpen: false
    property int optimizationRow: 0
    property var optimizationData: ({})
    property string optimizationCategory: "unknown"
    property string optimizationGamescopeMode: "disabled"
    property string optimizationDisplayId: ""
    property var optimizationDisplays: []
    property var optimizationReasons: []
    property var optiScalerData: ({})
    // Conflict digest the user already declined (the dialog is not reopened
    // for the same set of files).
    property string optiScalerDismissedConflict: ""
    readonly property var optiScalerConflict: optiScalerData.operationConflict || ({})
    readonly property bool optiScalerBusy: optiScalerData.loading === true || optiScalerData.refreshing === true
    readonly property string optiScalerErrorText: App.I18n.message(String(
            optiScalerData.error || optiScalerData.onlineError
            || optiScalerData.refreshError || optiScalerData.operationError || ""))
    property var protonTweaksData: ({})
    property var narratorData: ({})
    property var narratorSession: ({})
    readonly property var optimizationPresets: ["automatic", "maximum_performance", "balanced", "quiet", "custom"]
    readonly property var optimizationCategories: ["competitive", "fast_action", "cinematic", "platformer_2d", "strategy_simulation", "retro", "unknown", "custom"]
    readonly property var optimizationFpsValues: [30, 45, 60, 90, 120, 144, 165, 200, 240]
    readonly property var optimizationGamescopeModes: ["disabled", "native", "performance", "quality"]
    readonly property var mangoHudPresets: ["disabled", "fps_only", "basic", "extended", "custom"]
    readonly property var mangoHudPositions: ["top-left", "top-center", "top-right", "middle-left", "middle-right", "bottom-left", "bottom-center", "bottom-right"]
    readonly property var mangoHudFontSizes: [18, 24, 32]
    readonly property var mangoHudFpsLimits: [0, 30, 40, 60, 90, 120, 144, 165, 240]
    readonly property var tabs: [
        { "id": "overview", "icon": App.UiIcons.couchGlyphOverview, "iconOnLight": App.UiIcons.couchGlyphOverviewOnLight, "title": qsTr("Overview") },
        { "id": "storage", "icon": App.UiIcons.couchGlyphStorage, "iconOnLight": App.UiIcons.couchGlyphStorageOnLight, "title": qsTr("Storage") },
        { "id": "optimization", "icon": App.UiIcons.couchGlyphOptimization, "iconOnLight": App.UiIcons.couchGlyphOptimizationOnLight, "title": qsTr("Optimization") },
        { "id": "optiscaler", "icon": App.UiIcons.couchGlyphOptiscaler, "iconOnLight": App.UiIcons.couchGlyphOptiscalerOnLight, "title": qsTr("OptiScaler") },
        { "id": "narrator", "icon": App.UiIcons.couchGlyphNarrator, "iconOnLight": App.UiIcons.couchGlyphNarratorOnLight, "title": qsTr("Lektor") }
    ]
    readonly property var profileNames: ["Fast", "Balanced", "Maximum", "Auto"]
    readonly property var actionModel: actionsForTab()
    readonly property bool launcherIntegrationSupported: {
        var launcher = String(value(["launcher"], ""))
        return launcher !== "Heroic" && launcher !== "Lutris"
    }
    signal backRequested()

    function loadedImplicitHeight(loadedItem) {
        return loadedItem ? Number(loadedItem.implicitHeight || 0) : 0
    }

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            var item = page.gameModeOverlayOpen
                    ? gameModeOptions.itemAtIndex(page.gameModeRow)
                    : page.gamescopeOverlayOpen
                    ? gamescopeOptions.itemAtIndex(page.gamescopeRow)
                    : page.optimizationOverlayOpen
                    ? optimizationOptions.itemAtIndex(page.optimizationRow)
                    : page.mangoHudOverlayOpen
                    ? mangoHudOptions.itemAtIndex(page.mangoHudRow)
                    : page.confirmationOpen
                    ? (page.confirmationChoice === 0
                       ? detailsCancelButton : detailsConfirmButton)
                    : page.contentFocus ? detailsContentFlick
                    : page.tabFocus ? tabBar.itemAtIndex(page.selectedTab)
                    : page.actionFocus ? detailsActionRepeater.itemAt(page.selectedAction)
                    : page.headerCardItem(page.headerIndex)
            if (item)
                item.forceActiveFocus()
        })
    }

    function value(keys, fallback) {
        for (var i = 0; i < keys.length; ++i) {
            var candidate = game[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "") return candidate
        }
        return fallback
    }
    function boolValue(keys, fallback) { return value(keys, fallback) === true }
    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }
    function formatBytes(raw) {
        var bytes = Number(raw)
        if (!isFinite(bytes) || bytes < 0) return qsTr("Unavailable")
        var units = [qsTr("B"), qsTr("KiB"), qsTr("MiB"), qsTr("GiB"), qsTr("TiB")]
        var unit = 0
        while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit++ }
        return (unit ? bytes.toFixed(bytes >= 100 ? 0 : 1) : Math.round(bytes)) + " " + units[unit]
    }
    function narratorVoices() {
        var source = narratorData && narratorData.voices
                ? Array.from(narratorData.voices) : []
        return source.filter(function(voice) {
            return voice && (voice.available === true || voice.installed === true)
        })
    }
    function narratorVoiceLabel() {
        var voices = narratorVoices()
        if (String(narratorData.voiceName || "").length)
            return String(narratorData.voiceName)     // the voice really used
        var selected = String(narratorData.voiceId || "")
        for (var index = 0; index < voices.length; ++index) {
            if (String(voices[index].id || "") === selected)
                return String(voices[index].name || voices[index].id)
        }
        return voices.length ? String(voices[0].name || voices[0].id) : qsTr("No installed voice")
    }
    function narratorLanguageLabel() {
        return String(narratorData.subtitleLanguageMode || "english_to_polish") === "polish"
                ? qsTr("Polish subtitles") : qsTr("English → Polish")
    }
    function narratorSourceLabel() {
        var mode = String(narratorData.sourceMode || "auto")
        return mode === "ocr" ? qsTr("OCR only") : qsTr("Automatic")
    }
    function narratorCaptureLabel() {
        return String(narratorData.captureSource || "window") === "monitor"
                ? qsTr("Monitor") : qsTr("Game window")
    }
    function narratorSessionActive() {
        return ["starting", "selecting_source", "listening", "ocr", "translating",
                "speaking", "stopping"].indexOf(String(narratorSession.status || "idle")) >= 0
    }
    function narratorStatusLabel() {
        var status = String(narratorSession.status || "idle")
        if (status === "starting") return qsTr("Starting")
        if (status === "selecting_source") return qsTr("Select a capture source")
        if (status === "listening") return qsTr("Listening")
        if (status === "ocr") return qsTr("Reading subtitles")
        if (status === "translating") return qsTr("Translating")
        if (status === "speaking") return qsTr("Speaking")
        if (status === "stopping") return qsTr("Stopping")
        if (status === "error") return qsTr("Error")
        return qsTr("Stopped")
    }
    function narratorCaptureStateLabel(state) {
        if (state === "permission_required" || state === "selecting_source")
            return qsTr("Waiting for portal permission or source")
        if (state === "starting")
            return qsTr("Starting capture")
        if (state === "active")
            return qsTr("Active")
        if (state === "cancelled")
            return qsTr("Selection cancelled")
        if (state === "permission_denied")
            return qsTr("Permission denied")
        if (state === "error" || state === "source_lost")
            return qsTr("Capture error")
        if (state === "unavailable")
            return qsTr("Unavailable")
        return qsTr("Stopped")
    }
    function narratorOcrStateLabel(state) {
        if (state === "loading")
            return qsTr("Loading")
        if (state === "processing")
            return qsTr("Processing")
        if (state === "ready")
            return qsTr("Ready")
        if (state === "error")
            return qsTr("OCR error")
        return qsTr("Component missing")
    }
    function narratorInferenceStateLabel(state, processingLabel, errorLabel) {
        if (state === "loading")
            return qsTr("Loading")
        if (state === "processing")
            return processingLabel
        if (state === "ready")
            return qsTr("Ready")
        if (state === "bypassed")
            return qsTr("Disabled")
        if (state === "error")
            return errorLabel
        return qsTr("Component missing")
    }
    function narratorStartMessage() {
        if (narratorSessionActive())
            return qsTr("Stop the current narration session")
        var reason = String(narratorSession.reasonCode || "")
        if (reason === "components_missing") return qsTr("Required local components are missing")
        if (reason === "game_not_running") return qsTr("Launch the game before starting Narrator")
        if (reason === "another_session_active") return qsTr("Narrator is active for another game")
        if (narratorData.enabled !== true) return qsTr("Enable Narrator for this game first")
        if (narratorSession.captureGrantSaved === false)
            return qsTr("The first start asks you to choose the game window once; it is remembered")
        if (narratorSession.cardState === "waiting_for_game")
            return qsTr("Starts automatically with the game; you can also start it now")
        if (narratorSession.cardState === "manual")
            return qsTr("This launcher cannot be detected; start the Narrator manually")
        return qsTr("Start narration for the running game")
    }
    function narratorActions() {
        if (!narratorData || narratorData.success !== true) return [{
            "id": "narrator-reload", "icon": App.UiIcons.couchGlyphRefresh, "iconOnLight": App.UiIcons.couchGlyphRefreshOnLight, "symbol": "↻", "title": qsTr("Load Narrator settings"),
            "subtitle": qsTr("Read the saved per-game configuration"), "enabled": true
        }]
        var active = narratorSessionActive()
        return [
            { "id": "narrator-enabled", "icon": App.UiIcons.couchGlyphNarrator, "iconOnLight": App.UiIcons.couchGlyphNarratorOnLight, "symbol": "N", "title": qsTr("Narrator: %1").arg(narratorData.enabled ? qsTr("On") : qsTr("Off")), "label": qsTr("Lektor"), "value": narratorData.enabled ? qsTr("Enabled") : qsTr("Disabled"), "description": qsTr("Saved only for this game"), "subtitle": qsTr("Saved only for this game"), "enabled": true },
            { "id": "narrator-language", "icon": App.UiIcons.couchGlyphSubtitles, "iconOnLight": App.UiIcons.couchGlyphSubtitlesOnLight, "symbol": "文", "title": narratorLanguageLabel(), "label": qsTr("Language mode"), "value": narratorLanguageLabel(), "description": qsTr("Cycle subtitle language mode"), "subtitle": qsTr("Cycle subtitle language mode"), "enabled": true },
            { "id": "narrator-source", "icon": App.UiIcons.couchGlyphScanSource, "iconOnLight": App.UiIcons.couchGlyphScanSourceOnLight, "symbol": "⌁", "title": qsTr("Source: %1").arg(narratorSourceLabel()), "label": qsTr("Recognition source"), "value": narratorSourceLabel(), "description": qsTr("Automatic detection or OCR capture"), "subtitle": qsTr("Automatic detection or OCR capture"), "enabled": true },
            { "id": "narrator-capture", "icon": App.UiIcons.couchGlyphCapture, "iconOnLight": App.UiIcons.couchGlyphCaptureOnLight, "symbol": "▣", "title": qsTr("Capture: %1").arg(narratorCaptureLabel()), "label": qsTr("Capture"), "value": narratorCaptureLabel(), "description": qsTr("Choose a window or the full monitor"), "subtitle": qsTr("Choose a window or the full monitor"), "enabled": true },
            { "id": "narrator-voice", "icon": App.UiIcons.couchGlyphVoice, "iconOnLight": App.UiIcons.couchGlyphVoiceOnLight, "symbol": "♪", "title": narratorVoiceLabel(), "label": qsTr("Voice"), "value": narratorVoiceLabel(), "description": qsTr("Cycle installed speech voices"), "subtitle": qsTr("Cycle installed speech voices"), "enabled": narratorVoices().length > 0 },
            { "id": "narrator-volume", "icon": App.UiIcons.couchGlyphVolume, "iconOnLight": App.UiIcons.couchGlyphVolumeOnLight, "symbol": "◖", "title": qsTr("Volume: %1%").arg(Math.round(Number(narratorData.volume || 0) * 100)), "label": qsTr("Volume"), "value": qsTr("%1%").arg(Math.round(Number(narratorData.volume || 0) * 100)), "description": qsTr("Press repeatedly to adjust"), "subtitle": qsTr("Press repeatedly to adjust"), "enabled": true },
            { "id": "narrator-rate", "icon": App.UiIcons.couchGlyphSpeechRate, "iconOnLight": App.UiIcons.couchGlyphSpeechRateOnLight, "symbol": "››", "title": qsTr("Speech rate: %1×").arg(Number(narratorData.speechRate || 1).toFixed(1)), "label": qsTr("Speech rate"), "value": qsTr("%1×").arg(Number(narratorData.speechRate || 1).toFixed(1)), "description": qsTr("Press repeatedly to adjust"), "subtitle": qsTr("Press repeatedly to adjust"), "enabled": true },
            { "id": "narrator-region", "icon": App.UiIcons.couchGlyphRegion, "iconOnLight": App.UiIcons.couchGlyphRegionOnLight, "symbol": "⌗", "title": qsTr("Select subtitle region"), "label": qsTr("Subtitle region"), "value": qsTr("Select"), "description": qsTr("Open the native capture selector"), "subtitle": qsTr("Open the native capture selector"), "enabled": Boolean(controller && controller.selectNarratorSubtitleRegion) },
            { "id": active ? "narrator-stop" : "narrator-start", "icon": active ? App.UiIcons.couchGlyphStop : App.UiIcons.couchGlyphStart, "iconOnLight": active ? App.UiIcons.couchGlyphStopOnLight : App.UiIcons.couchGlyphStartOnLight, "symbol": active ? "■" : "▶", "title": active ? qsTr("Stop Narrator") : qsTr("Start Narrator"), "label": active ? qsTr("Stop Narrator") : qsTr("Start Narrator"), "value": active ? qsTr("Active") : App.I18n.narratorState(narratorSession).text, "description": narratorStartMessage(), "subtitle": narratorStartMessage(), "enabled": active || (narratorData.enabled === true && narratorSession.canStart === true) }
        ]
    }
    function optiScalerExecutable() {
        var selected = optiScalerData.selectedExecutable || ({})
        return String(optiScalerData.executable || selected.relativePath || "")
    }
    function optiScalerOperation() {
        var installation = String(optiScalerData.installationState || "")
        if (installation === "corrupt" || installation === "partial") return "repair"
        if (String(optiScalerData.onlineState || "") === "update_available") return "update"
        if (optiScalerData.installed === true) return "reinstall"
        return "install"
    }
    function optiScalerOperationLabel() {
        var operation = optiScalerOperation()
        if (operation === "repair") return qsTr("Repair OptiScaler")
        if (operation === "update") return qsTr("Update OptiScaler")
        if (operation === "reinstall") return qsTr("Reinstall OptiScaler")
        return qsTr("Install OptiScaler")
    }
    function optiScalerModeLabel() {
        var mode = String(optiScalerData.requestedFsr4Mode || optiScalerData.fsr4Mode || "automatic")
        if (mode === "normal") return qsTr("FSR 4.1.1")
        if (mode === "force_int8") return qsTr("FSR 4.1.1 INT8")
        if (mode === "disabled") return qsTr("Disabled")
        return qsTr("Automatic")
    }
    function optimizationActions() {
        return [
            { "id": "optimization-profile", "icon": App.UiIcons.couchGlyphTune, "iconOnLight": App.UiIcons.couchGlyphTuneOnLight, "symbol": "◐", "title": qsTr("Profile: %1").arg(optimizationPresetLabel()), "enabled": true },
            { "id": "gamemode", "icon": App.UiIcons.couchGlyphGamemode, "iconOnLight": App.UiIcons.couchGlyphGamemodeOnLight, "symbol": "⚡", "title": "GameMode: " + (gameModeEnabled ? qsTr("On") : qsTr("Off")), "enabled": true },
            { "id": "gamescope", "icon": App.UiIcons.couchGlyphGamescope, "iconOnLight": App.UiIcons.couchGlyphGamescopeOnLight, "symbol": "▣", "title": "Gamescope: " + (gamescopeEnabled ? qsTr("On") : qsTr("Off")), "enabled": true },
            { "id": "mangohud-profile", "icon": App.UiIcons.couchGlyphMangohud, "iconOnLight": App.UiIcons.couchGlyphMangohudOnLight, "symbol": "◉", "title": qsTr("MangoHud"), "subtitle": mangoHudPresetLabel(), "enabled": true }
        ]
    }
    function optiScalerRefreshSubtitle() {
        if (optiScalerBusy)
            return qsTr("Checking the official release…")
        if (optiScalerErrorText.length > 0)
            return qsTr("Retry: %1").arg(optiScalerErrorText)
        return qsTr("Official release from the internet · confirmation required")
    }
    function optiScalerActions() {
        var actions = []
        var installed = optiScalerData.installed === true
        var needsPackage = !installed
                || String(optiScalerData.onlineState || "") === "update_available"
                || ["corrupt", "partial"].indexOf(String(optiScalerData.installationState || "")) >= 0
        if (optiScalerData.success !== true && !optiScalerBusy) {
            // Status could not be read: show the reason and a retry.
            actions.push({ "id": "optiscaler-retry-status", "icon": App.UiIcons.couchGlyphRefresh, "iconOnLight": App.UiIcons.couchGlyphRefreshOnLight, "title": qsTr("Retry"), "subtitle": optiScalerErrorText || qsTr("Status not loaded"), "enabled": true })
            return actions
        }
        if (needsPackage && optiScalerData.success === true) {
            // One button: download, verify, plan, confirm conflicts, install.
            actions.push({ "id": "optiscaler-install", "icon": App.UiIcons.couchGlyphInstall, "iconOnLight": App.UiIcons.couchGlyphInstallOnLight, "symbol": "◇", "title": optiScalerOperationLabel(), "subtitle": optiScalerRefreshSubtitle(), "enabled": optiScalerExecutable().length > 0 && !optiScalerBusy })
        }
        if (installed) {
            actions.push({ "id": "optiscaler-configure", "icon": App.UiIcons.couchGlyphTune, "iconOnLight": App.UiIcons.couchGlyphTuneOnLight, "symbol": "◈", "title": qsTr("Upscaling: %1").arg(optiScalerModeLabel()), "subtitle": qsTr("Cycle and apply the FSR mode"), "enabled": (optiScalerData.supportedFsr4Modes || []).length > 0 })
            actions.push({ "id": "optiscaler-verify", "icon": App.UiIcons.couchGlyphVerify, "iconOnLight": App.UiIcons.couchGlyphVerifyOnLight, "symbol": "✓", "title": qsTr("Verify OptiScaler"), "subtitle": qsTr("Check managed files without changing them"), "enabled": true })
            actions.push({ "id": "optiscaler-launch", "icon": App.UiIcons.couchGlyphLaunch, "iconOnLight": App.UiIcons.couchGlyphLaunchOnLight, "symbol": "▶", "title": qsTr("Launch with OptiScaler"), "subtitle": qsTr("Use the installed profile"), "enabled": true })
            if (String(optiScalerData.manifestId || "").length > 0)
                actions.push({ "id": "optiscaler-remove", "icon": App.UiIcons.couchGlyphRemove, "iconOnLight": App.UiIcons.couchGlyphRemoveOnLight, "symbol": "×", "title": qsTr("Remove OptiScaler"), "subtitle": qsTr("Requires confirmation"), "enabled": true })
        }
        if (actions.length === 0)
            actions.push({ "id": "unavailable", "icon": App.UiIcons.couchGlyphInfo, "iconOnLight": App.UiIcons.couchGlyphInfoOnLight, "symbol": "i", "title": qsTr("Checking OptiScaler status…"), "enabled": false })
        return actions
    }
    function actionsForTab() {
        if (selectedTab === 0) return [
            { "id": "launch", "icon": App.UiIcons.couchGlyphLaunch, "iconOnLight": App.UiIcons.couchGlyphLaunchOnLight, "symbol": "▶", "title": launchPending ? qsTr("Launching…") : qsTr("Launch"), "subtitle": boolValue(["launchAllowed"], false) ? qsTr("Start the selected game") : String(value(["availabilityStatus", "status"], qsTr("Game is unavailable"))), "enabled": boolValue(["launchAllowed"], false) && !launchPending },
            { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "symbol": "↓", "title": qsTr("Updates"), "subtitle": qsTr("Review detected changes"), "enabled": true }
        ]
        if (selectedTab === 1) return [
            { "id": "analyze", "icon": App.UiIcons.couchGlyphAnalyze, "iconOnLight": App.UiIcons.couchGlyphAnalyzeOnLight, "symbol": "⌕", "title": qsTr("Analyze"), "subtitle": boolValue(["analysisAllowed"], false) ? qsTr("Inspect the current game") : qsTr("Unavailable for this game"), "enabled": boolValue(["analysisAllowed"], false) },
            { "id": "verify", "icon": App.UiIcons.couchGlyphVerifyCompression, "iconOnLight": App.UiIcons.couchGlyphVerifyCompressionOnLight, "symbol": "✓", "title": qsTr("Verify compression"), "subtitle": boolValue(["analysisAllowed"], false) ? qsTr("Read-only measurement") : qsTr("Unavailable for this game"), "enabled": boolValue(["analysisAllowed"], false) },
            { "id": "profile", "icon": App.UiIcons.couchGlyphCompressionProfile, "iconOnLight": App.UiIcons.couchGlyphCompressionProfileOnLight, "symbol": "◈", "title": qsTr("Profile: %1").arg(selectedProfile), "subtitle": boolValue(["analysisProfilesUnlocked"], false) ? qsTr("Choose a planned profile") : qsTr("Analyze the game first"), "enabled": boolValue(["analysisProfilesUnlocked"], false) },
            { "id": "compress", "icon": App.UiIcons.couchGlyphCompress, "iconOnLight": App.UiIcons.couchGlyphCompressOnLight, "symbol": "↓", "title": qsTr("Start compression"), "subtitle": boolValue(["analysisProfilesUnlocked"], false) && boolValue(["compressionAvailable"], false) ? qsTr("Review the verified plan") : qsTr("A verified Btrfs plan is required"), "enabled": boolValue(["analysisProfilesUnlocked"], false) && boolValue(["compressionAvailable"], false) }
        ]
        if ((selectedTab === 2 || selectedTab === 3) && !launcherIntegrationSupported) return [{
                "id": "unavailable", "icon": App.UiIcons.couchGlyphInfo, "iconOnLight": App.UiIcons.couchGlyphInfoOnLight, "symbol": "i",
                "title": qsTr("Launcher integration unavailable"),
                "subtitle": qsTr("Full optimization launch integration for this launcher is planned for a later update"),
                "enabled": false
            }]
        if (selectedTab === 2)
            return optimizationActions()
        if (selectedTab === 3)
            return optiScalerActions()
        if (selectedTab === 4)
            return narratorActions()
        return [{ "id": "unavailable", "icon": App.UiIcons.couchGlyphInfo, "iconOnLight": App.UiIcons.couchGlyphInfoOnLight, "symbol": "i", "title": qsTr("Unavailable in this version"), "enabled": false }]
    }
    // ---- Header cards (Launch + feature summaries) ---------------------------
    // States come only from the loaded per-game data; "Preview" marks features
    // that are saved but not yet applied when the game launches.
    function featureInfo(id) {
        if (id !== "narrator" && !launcherIntegrationSupported)
            return { "tone": "unavailable", "state": qsTr("Unavailable"), "detail": qsTr("Not supported for this launcher"), "preview": false }
        if (id === "gamemode" || id === "gamescope") {
            var tool = optimizationData ? optimizationData[id] : null
            if (!tool)
                return { "tone": "neutral", "state": qsTr("Unknown"), "detail": qsTr("Optimization profile not loaded"), "preview": false }
            if (tool.available !== true)
                return { "tone": "unavailable", "state": qsTr("Not installed"), "detail": App.I18n.message(String(tool.message || "")), "preview": false }
            var on = id === "gamemode" ? gameModeEnabled : gamescopeEnabled
            var applied = launchActivation.state === "active"
            return { "tone": on ? "success" : "neutral",
                     "state": id === "gamescope" && on ? gamescopeSummaryFromData() : (on ? qsTr("On") : qsTr("Off")),
                     "detail": on ? activationText(launchActivation.state) : "", "preview": on && !applied }
        }
        if (id === "mangohud") {
            if (mangoHudProfile.available !== true)
                return { "tone": "unavailable", "state": qsTr("Unavailable"),
                         "detail": App.I18n.message(String(mangoHudProfile.availabilityMessage || "")), "preview": false }
            return { "tone": mangoHudEnabled ? "success" : "neutral", "state": mangoHudEnabled ? qsTr("On") : qsTr("Off"),
                     "detail": qsTr("Preset: %1").arg(mangoHudPresetLabel()), "preview": false }
        }
        if (id === "optiscaler") {
            if (optiScalerData.loading || optiScalerData.refreshing)
                return { "tone": "info", "state": qsTr("Checking…"), "detail": "", "preview": false }
            if (optiScalerData.success !== true)
                return { "tone": "warning", "state": qsTr("Unknown"), "detail": optiScalerErrorText || qsTr("Status not loaded"), "preview": false }
            if (String(optiScalerData.installationState || "") === "inconsistent")
                return { "tone": "warning", "state": qsTr("Inconsistent installation"), "detail": qsTr("Review in Desktop Mode"), "preview": false }
            var installation = String(optiScalerData.installationState || "")
            if (installation === "corrupt" || installation === "partial")
                return { "tone": "warning", "state": qsTr("Needs repair"), "detail": qsTr("Repair from the OptiScaler tab"), "preview": false }
            if (optiScalerData.installed === true)
                return { "tone": "success", "state": qsTr("Installed"), "detail": qsTr("Upscaling: %1").arg(optiScalerModeLabel()), "preview": false }
            return { "tone": "neutral", "state": qsTr("Not installed"), "detail": qsTr("Install from the OptiScaler tab"), "preview": false }
        }
        // Narrator
        if (!narratorData || narratorData.success !== true)
            return { "tone": "neutral", "state": qsTr("Unknown"), "detail": qsTr("Settings not loaded"), "preview": false }
        var narratorSessionState = narratorSession && narratorSession.cardState ? narratorSession
                : { "cardState": narratorData.enabled === true ? "manual" : "disabled" }
        var narratorCard = App.I18n.narratorState(narratorSessionState)
        var voiceName = String(narratorSession.voiceName || narratorData.voiceName || "")
        return { "tone": narratorCard.tone,
                 "state": narratorSessionActive() ? narratorStatusLabel() : narratorCard.text,
                 "detail": voiceName.length ? qsTr("Voice: %1").arg(voiceName) : narratorLanguageLabel(),
                 "preview": false }
    }
    function gamescopeSummaryFromData() {
        var data = optimizationData || ({})
        var outW = Number(data.gamescopeOutputWidth || 0), outH = Number(data.gamescopeOutputHeight || 0)
        var inW = Number(data.gamescopeInputWidth || outW), inH = Number(data.gamescopeInputHeight || outH)
        return inW === outW && inH === outH ? outW + "×" + outH : inW + "×" + inH + " → " + outW + "×" + outH
    }
    function launchSubtitle() {
        if (launchPending)
            return ""
        if (!boolValue(["launchAllowed"], false))
            return App.I18n.message(String(value(["launchUnavailableReason", "availabilityStatus", "status"], qsTr("Game is unavailable"))))
        if (launcherIntegrationSupported && optimizationData && optimizationData.success === true)
            return qsTr("Profile: %1").arg(optimizationPresetLabel())
        return ""
    }
    readonly property var headerCards: [
        { "id": "launch", "icon": App.UiIcons.couchGlyphLaunch, "iconOnLight": App.UiIcons.couchGlyphLaunchOnLight,
          "title": launchPending ? qsTr("Launching…") : qsTr("Launch game"), "info": ({ "tone": "neutral", "state": "", "detail": launchSubtitle(), "preview": false }),
          "enabled": boolValue(["launchAllowed"], false) && !launchPending, "primary": true },
        { "id": "gamemode", "icon": App.UiIcons.couchGlyphGamemode, "iconOnLight": App.UiIcons.couchGlyphGamemodeOnLight,
          "title": "GameMode", "info": featureInfo("gamemode"), "enabled": launcherIntegrationSupported, "primary": false },
        { "id": "gamescope", "icon": App.UiIcons.couchGlyphGamescope, "iconOnLight": App.UiIcons.couchGlyphGamescopeOnLight,
          "title": "Gamescope", "info": featureInfo("gamescope"), "enabled": launcherIntegrationSupported, "primary": false },
        { "id": "mangohud", "icon": App.UiIcons.couchGlyphMangohud, "iconOnLight": App.UiIcons.couchGlyphMangohudOnLight,
          "title": "MangoHud", "info": featureInfo("mangohud"), "enabled": launcherIntegrationSupported, "primary": false },
        { "id": "optiscaler", "icon": App.UiIcons.couchGlyphOptiscaler, "iconOnLight": App.UiIcons.couchGlyphOptiscalerOnLight,
          "title": "OptiScaler", "info": featureInfo("optiscaler"), "enabled": launcherIntegrationSupported, "primary": false },
        { "id": "narrator", "icon": App.UiIcons.couchGlyphNarrator, "iconOnLight": App.UiIcons.couchGlyphNarratorOnLight,
          "title": qsTr("Lektor"), "info": featureInfo("narrator"), "enabled": true, "primary": false }
    ]
    function headerCardItem(index) {
        return index < 3 ? headerRowTop.itemAt(index) : headerRowBottom.itemAt(index - 3)
    }
    function ensureHeaderCard() {
        if (headerCards[headerIndex] && headerCards[headerIndex].enabled)
            return
        for (var i = 0; i < headerCards.length; ++i)
            if (headerCards[i].enabled) { headerIndex = i; return }
    }
    function moveHeader(dx, dy) {
        var row = headerIndex < 3 ? 0 : 1
        var column = headerIndex % 3
        if (dy !== 0) {
            var targetRow = row + dy
            if (targetRow < 0 || targetRow > 1)
                return false
            row = targetRow
        } else {
            var targetColumn = column + dx
            if (targetColumn < 0 || targetColumn > 2)
                return false
            column = targetColumn
        }
        var candidate = row * 3 + column
        if (!headerCards[candidate] || !headerCards[candidate].enabled)
            return false
        headerIndex = candidate
        if (navigation) navigation.rememberFocus("details", "card-" + headerCards[candidate].id, candidate)
        return true
    }
    function openFeatureTab(tabIndex) {
        selectedTab = tabIndex
        selectedAction = 0
        focusArea = 2
        Qt.callLater(ensureAction)
        return "navigate"
    }
    function launchSelectedGame() {
        if (!controller || !boolValue(["launchAllowed"], false) || launchPending)
            return "error"
        var launched = Boolean(controller.launchGame(String(game.id || "")))
        launchPending = launched
        if (launched) launchGuard.restart()
        return launched ? "confirm" : "error"
    }
    function activateHeader() {
        var card = headerCards[headerIndex]
        if (!card || !card.enabled)
            return "error"
        if (card.id === "launch")
            return launchSelectedGame()
        if (card.id === "gamemode") {
            openGameModeOverlay()
            return "open"
        }
        if (card.id === "gamescope") {
            openGamescopeOverlay()
            return "open"
        }
        if (card.id === "mangohud") {
            openMangoHudOverlay()
            return "open"
        }
        if (card.id === "optiscaler")
            return openFeatureTab(3)
        return openFeatureTab(4)
    }
    function ensureAction() {
        var actions = actionModel
        if (actions[selectedAction] && actions[selectedAction].enabled) return
        for (var i = 0; i < actions.length; ++i) if (actions[i].enabled) { selectedAction = i; return }
        selectedAction = -1
    }
    function changeTab(delta) {
        var previousTab = selectedTab
        selectedTab = (selectedTab + delta + tabs.length) % tabs.length
        selectedAction = 0
        if (focusArea === 3)
            focusArea = 2
        ensureAction()
        if (navigation) navigation.rememberFocus("details", "tab-" + tabs[selectedTab].id, selectedTab)
        return selectedTab !== previousTab
    }
    function moveAction(delta) {
        var actions = actionModel
        var previousAction = selectedAction
        var candidate = selectedAction
        for (var count = 0; count < actions.length; ++count) {
            candidate = Math.max(0, Math.min(actions.length - 1, candidate + delta))
            if (actions[candidate] && actions[candidate].enabled) { selectedAction = candidate; break }
            if (candidate === 0 || candidate === actions.length - 1) break
        }
        if (navigation && selectedAction >= 0) navigation.rememberFocus("details", actions[selectedAction].id, selectedAction)
        return selectedAction !== previousAction
    }
    // Vertical move inside the action grid: same column in the next/previous
    // row (clamped to a shorter last row); the nearest enabled tile in that
    // row wins. Returns false at the grid edge so focus can leave it.
    function moveActionRow(direction) {
        var actions = actionModel
        var columns = Math.max(1, detailsActionsViewport.columns)
        var row = Math.floor(Math.max(0, selectedAction) / columns) + direction
        var lastRow = Math.floor((actions.length - 1) / columns)
        while (row >= 0 && row <= lastRow) {
            var start = row * columns
            var end = Math.min(actions.length - 1, start + columns - 1)
            var wanted = Math.min(end, start + Math.max(0, selectedAction) % columns)
            for (var distance = 0; distance < columns; ++distance) {
                var candidates = [wanted - distance, wanted + distance]
                for (var c = 0; c < candidates.length; ++c) {
                    var candidate = candidates[c]
                    if (candidate >= start && candidate <= end && actions[candidate] && actions[candidate].enabled) {
                        selectedAction = candidate
                        if (navigation) navigation.rememberFocus("details", actions[candidate].id, candidate)
                        return true
                    }
                }
            }
            row += direction
        }
        return false
    }
    function planValid(plan) {
        return plan && typeof plan === "object" && String(plan.planId || plan.plan_id || "").length > 0
                && plan.valid !== false && plan.canStart !== false && plan.can_start !== false
                && (!plan.blockers || plan.blockers.length === 0)
    }
    function selectedProjection() {
        var report = game.benchmarkEstimate || {}
        var projections = report.projections || {}
        var level = selectedProfile === "Fast" ? "1" : selectedProfile === "Maximum" ? "9" : "3"
        return projections[level] || ({})
    }
    function classificationLabel() {
        var classification = game.compressionClassification || {}
        return String(classification.label || classification.status || qsTr("Measurement unavailable"))
    }
    function mangoHudPresetLabel() {
        if (mangoHudPreset === "fps_only") return qsTr("FPS only")
        if (mangoHudPreset === "basic") return qsTr("Basic")
        if (mangoHudPreset === "extended") return qsTr("Extended")
        if (mangoHudPreset === "custom") return qsTr("Custom")
        return qsTr("Disabled")
    }
    function optimizationPresetLabel() {
        var labels = [qsTr("Automatic"), qsTr("Maximum Performance"), qsTr("Balanced"), qsTr("Quiet"), qsTr("Custom")]
        var index = optimizationPresets.indexOf(optimizationProfile)
        return labels[index >= 0 ? index : 0]
    }
    // Internal value for the display-only launch preview. The backend parses
    // it into OptimizationProfile (Maximum Performance/Balanced/Quiet/Custom),
    // so it must never be a translated label. The newer "automatic" preset has
    // no legacy equivalent and keeps the historical Balanced mapping (the
    // preview flags come from the separate gamemode/gamescope/mangohud keys).
    function legacyOptimizationProfileValue() {
        if (optimizationProfile === "maximum_performance") return "Maximum Performance"
        if (optimizationProfile === "quiet") return "Quiet"
        if (optimizationProfile === "custom") return "Custom"
        return "Balanced"
    }
    function optimizationCategoryLabel() {
        var labels = [qsTr("Competitive"), qsTr("Fast action"), qsTr("Cinematic single-player"), qsTr("Platformer / 2D"), qsTr("Strategy / simulation"), qsTr("Retro"), qsTr("Unknown"), qsTr("Custom")]
        var index = optimizationCategories.indexOf(optimizationCategory)
        return labels[index >= 0 ? index : 6]
    }
    function optimizationGamescopeLabel() {
        var labels = [qsTr("Disabled"), qsTr("Native"), qsTr("Performance"), qsTr("Quality")]
        var index = optimizationGamescopeModes.indexOf(optimizationGamescopeMode)
        return labels[index >= 0 ? index : 0]
    }
    function optimizationDisplayLabel() {
        for (var i = 0; i < optimizationDisplays.length; ++i)
            if (String(optimizationDisplays[i].id || "") === optimizationDisplayId)
                return String(optimizationDisplays[i].name || optimizationDisplays[i].label || qsTr("Monitor"))
        return optimizationDisplays.length ? String(optimizationDisplays[0].name || qsTr("Monitor")) : qsTr("Unavailable")
    }
    function loadOptimizationProfile() {
        if (!launcherIntegrationSupported) { optimizationData = ({}); return }
        if (!controller || !controller.getOptimizationProfile || !game.id) return
        var result = controller.getOptimizationProfile(String(game.id)) || ({})
        if (!result.success) return
        optimizationData = result
        optimizationProfile = String(result.preset || "automatic")
        optimizationCategory = String(result.gameCategory || "unknown")
        fpsLimit = Number(result.targetFps || 60)
        gameModeEnabled = Boolean(result.gamemodeEnabled)
        gamescopeEnabled = Boolean(result.gamescopeEnabled)
        optimizationGamescopeMode = String(result.gamescopeMode || "disabled")
        optimizationDisplayId = String(result.targetDisplayId || "")
        optimizationDisplays = result.displays ? Array.from(result.displays) : []
        optimizationReasons = result.recommendation && result.recommendation.reasons ? Array.from(result.recommendation.reasons) : []
        if (!optimizationDisplayId && optimizationDisplays.length)
            optimizationDisplayId = String(optimizationDisplays[0].id || "")
    }
    function loadOptiScalerStatus() {
        if (!launcherIntegrationSupported) { optiScalerData = ({}); return }
        if (!controller || !game.id) return
        var result = controller.requestOptiScalerStatus
                ? controller.requestOptiScalerStatus(String(game.id), false) || ({})
                : controller.getOptiScalerStatus(String(game.id)) || ({})
        applyOptiScalerStatus(result)
    }
    // Accepts success and failure alike, so "Checking…" always ends.
    function applyOptiScalerStatus(result) {
        var data = result || ({})
        if (data.success !== true)
            data = { "success": false, "loading": false, "refreshing": false,
                     "error": String(data.error || data.refreshError || "") }
        optiScalerData = data
        if (optiScalerBusy)
            optiScalerStatusTimeout.restart()
        else
            optiScalerStatusTimeout.stop()
        var conflict = data.operationConflict || ({})
        if (String(conflict.kind || "") === "confirmation_required"
                && String(conflict.digest || "") !== optiScalerDismissedConflict
                && !confirmationOpen && visible) {
            confirmationChoice = 0            // safe default: Cancel
            confirmationKind = "optiscaler_conflict"
            confirmationOpen = true
            if (navigation) navigation.openModal("optiscaler-conflict", "cancel")
            restoreActiveFocus()
        }
    }
    function loadProtonTweaks() {
        if (!launcherIntegrationSupported) { protonTweaksData = ({}); return }
        if (!controller || !controller.getProtonTweaks || !game.id) return
        var result = controller.getProtonTweaks(String(game.id)) || ({})
        protonTweaksData = result.success ? result : ({})
    }
    function loadNarrator() {
        if (!controller || !game.id) {
            narratorData = ({})
            narratorSession = ({})
            return
        }
        if (controller.getNarratorGameSettings)
            narratorData = controller.getNarratorGameSettings(String(game.id)) || ({})
        if (controller.getNarratorSessionState)
            narratorSession = controller.getNarratorSessionState(String(game.id)) || ({})
    }
    function saveNarratorValue(key, value) {
        if (!controller || !controller.saveNarratorGameSettings || !game.id)
            return false
        var payload = ({})
        payload[key] = value
        var saved = Boolean(controller.saveNarratorGameSettings(String(game.id), payload))
        if (saved)
            loadNarrator()
        return saved
    }
    function activateNarratorAction(id) {
        if (id === "narrator-reload") {
            loadNarrator()
            return narratorData.success === true
        }
        if (id === "narrator-enabled")
            return saveNarratorValue("enabled", narratorData.enabled !== true)
        if (id === "narrator-language")
            return saveNarratorValue("subtitleLanguageMode",
                    String(narratorData.subtitleLanguageMode || "english_to_polish") === "polish"
                    ? "english_to_polish" : "polish")
        if (id === "narrator-source")
            return saveNarratorValue("sourceMode",
                    String(narratorData.sourceMode || "auto") === "ocr" ? "auto" : "ocr")
        if (id === "narrator-capture")
            return saveNarratorValue("captureSource",
                    String(narratorData.captureSource || "window") === "monitor" ? "window" : "monitor")
        if (id === "narrator-voice") {
            var voices = narratorVoices()
            if (!voices.length)
                return false
            var selected = String(narratorData.voiceId || "")
            var voiceIndex = -1
            for (var index = 0; index < voices.length; ++index) {
                if (String(voices[index].id || "") === selected) {
                    voiceIndex = index
                    break
                }
            }
            return saveNarratorValue("voiceId",
                    String(voices[(voiceIndex + 1) % voices.length].id || ""))
        }
        if (id === "narrator-volume") {
            var volume = Math.round((Number(narratorData.volume || 0) + 0.05) * 100) / 100
            return saveNarratorValue("volume", volume > 1 ? 0 : volume)
        }
        if (id === "narrator-rate") {
            var rate = Math.round((Number(narratorData.speechRate || 1) + 0.1) * 10) / 10
            return saveNarratorValue("speechRate", rate > 2 ? 0.5 : rate)
        }
        if (id === "narrator-region")
            return Boolean(controller.selectNarratorSubtitleRegion
                    && controller.selectNarratorSubtitleRegion(
                        String(game.id), narratorData.subtitleRegion || ({})))
        if (id === "narrator-start")
            return Boolean(controller.startNarrator
                    && controller.startNarrator(String(game.id)))
        if (id === "narrator-stop")
            return Boolean(controller.stopNarrator && controller.stopNarrator())
        return false
    }
    function refreshCouchOptiScaler() {
        return Boolean(controller && controller.refreshOptiScalerRelease
                && controller.refreshOptiScalerRelease(String(game.id || ""), true))
    }
    function openOptiScalerConfirmation(operation) {
        confirmationChoice = 0
        confirmationKind = "optiscaler_" + String(operation)
        confirmationOpen = true
        if (navigation) navigation.openModal("optiscaler-operation", "cancel")
        restoreActiveFocus()
        return true
    }
    // Same backend path as Desktop "Install". A conflict stops the task before
    // any file is written; the conflict dialog then re-runs it with the digest
    // of exactly the files the user confirmed.
    function startCouchOptiScalerInstall(operation, confirmedDigest) {
        if (!controller || !controller.startOptiScalerInstall)
            return false
        var executable = optiScalerExecutable()
        if (!executable.length)
            return false
        return Boolean(controller.startOptiScalerInstall(
                    String(game.id || ""), executable,
                    String(optiScalerData.injectionDll || "auto"), String(operation),
                    String(confirmedDigest || ""), true, ({})))
    }
    function beginCouchOptiScalerOperation() {
        if (confirmationKind === "optiscaler_conflict") {
            var conflict = optiScalerConflict
            optiScalerDismissedConflict = String(conflict.digest || "")
            return startCouchOptiScalerInstall(String(conflict.operation || "auto"),
                                               String(conflict.digest || ""))
        }
        return startCouchOptiScalerInstall(String(confirmationKind).replace("optiscaler_", ""), "")
    }
    function configureCouchOptiScaler() {
        if (!controller || !controller.configureOptiScalerUpscaling)
            return false
        var modes = Array.from(optiScalerData.supportedFsr4Modes || [])
        if (!modes.length)
            return false
        var current = String(optiScalerData.requestedFsr4Mode
                             || optiScalerData.fsr4Mode || "automatic")
        var currentIndex = modes.indexOf(current)
        var requested = String(modes[(currentIndex + 1 + modes.length) % modes.length])
        var recommendation = optiScalerData.recommendation || ({})
        var effective = requested === "automatic"
                ? String(recommendation.recommendedMode || "disabled") : requested
        if (["normal", "force_int8", "disabled"].indexOf(effective) < 0)
            effective = "disabled"
        var result = controller.configureOptiScalerUpscaling(String(game.id || ""), {
            "fsr4Mode": requested,
            "effectiveFsr4Mode": effective,
            "automaticReason": requested === "automatic" ? String(recommendation.reason || "") : "",
            "fsrAgilitySdkUpgrade": Boolean(optiScalerData.fsrAgilitySdkUpgrade),
            "fsr4Watermark": Boolean(optiScalerData.fsr4Watermark),
            "dx11Upscaler": String(optiScalerData.dx11Upscaler || "auto"),
            "dx12Upscaler": String(optiScalerData.dx12Upscaler || "auto"),
            "vulkanUpscaler": String(optiScalerData.vulkanUpscaler || "auto")
        }) || ({})
        if (result.success === true)
            loadOptiScalerStatus()
        return result.success === true
    }
    function openOptimizationOverlay() {
        loadOptimizationProfile()
        optimizationRow = 0
        optimizationOverlayOpen = true
        if (navigation) navigation.openModal("optimization", "optimization-profile")
        restoreActiveFocus()
    }
    function closeOptimizationOverlay() {
        optimizationOverlayOpen = false
        optimizationRow = 0
        if (navigation) navigation.closeModal()
        restoreActiveFocus()
    }
    function adjustOptimization(delta) {
        var before = [optimizationProfile, optimizationCategory, fpsLimit,
                      gameModeEnabled, gamescopeEnabled,
                      optimizationGamescopeMode, optimizationDisplayId].join("|")
        if (optimizationRow === 0) optimizationProfile = cycleValue(optimizationPresets, optimizationProfile, delta)
        else if (optimizationRow === 1) optimizationCategory = cycleValue(optimizationCategories, optimizationCategory, delta)
        else if (optimizationRow === 2) fpsLimit = cycleValue(optimizationFpsValues, fpsLimit, delta)
        else if (optimizationRow === 3 && optimizationDisplays.length) {
            var values = optimizationDisplays.map(function(item) { return String(item.id || "") })
            optimizationDisplayId = cycleValue(values, optimizationDisplayId, delta)
        }
        var after = [optimizationProfile, optimizationCategory, fpsLimit,
                     gameModeEnabled, gamescopeEnabled,
                     optimizationGamescopeMode, optimizationDisplayId].join("|")
        return before !== after
    }
    function saveCouchOptimization() {
        if (!controller || !controller.saveOptimizationProfile) return false
        var source = optimizationData || ({})
        var payload = Object.assign({}, source, {
            "preset": optimizationProfile, "gameCategory": optimizationCategory,
            "targetDisplayId": optimizationDisplayId, "targetFpsMode": "manual",
            "targetFps": fpsLimit, "gamemodeEnabled": gameModeEnabled,
            "gamescopeEnabled": gamescopeEnabled, "gamescopeMode": optimizationGamescopeMode
        })
        var result = controller.saveOptimizationProfile(String(game.id || ""), payload) || ({})
        if (result.success) {
            optimizationData = result
            closeOptimizationOverlay()
            return true
        }
        return false
    }
    function moveOverlayRow(view, current, delta, maximum) {
        var candidate = current
        for (var count = 0; count <= maximum; ++count) {
            candidate += delta
            if (candidate < 0 || candidate > maximum)
                return current
            var entry = view.model[candidate]
            if (!entry || entry.enabled !== false)
                return candidate
        }
        return current
    }

    function handleOptimizationAction(action) {
        if (action === "Back") {
            closeOptimizationOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateUp" || action === "NavigateDown") {
            var previousRow = optimizationRow
            optimizationRow = moveOverlayRow(
                optimizationOptions, optimizationRow,
                action === "NavigateUp" ? -1 : 1, 5)
            if (optimizationRow !== previousRow)
                playSemanticSound("navigate")
        } else if (action === "NavigateLeft" || action === "NavigateRight") {
            playSemanticSound(adjustOptimization(action === "NavigateLeft" ? -1 : 1)
                              ? "adjust" : "error")
        } else if (action === "Confirm") {
            if (optimizationRow < 4)
                playSemanticSound(adjustOptimization(1) ? "adjust" : "error")
            else if (optimizationRow === 4)
                playSemanticSound(saveCouchOptimization() ? "confirm" : "error")
            else {
                closeOptimizationOverlay()
                playSemanticSound("back")
            }
        }
        restoreActiveFocus()
    }
    // ---- MangoHud menu ---------------------------------------------------------
    // Draft values mirror the saved profile; ids are the model's canonical
    // values ("fps_only", "top-left", 24, ...). Nothing is saved until Save.
    property bool mangoHudDraftEnabled: false
    property var mangoHudDraftMetrics: []
    readonly property var mangoHudMenuPresets: ["fps_only", "basic", "extended", "custom"]
    readonly property var mangoHudMenuPositions: ["top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"]
    readonly property var mangoHudMenuFpsLimits: [0, 30, 40, 60, 90, 120, 144, 165]
    readonly property var mangoHudMetricGroups: [
        { "id": "metric-fps", "metrics": ["fps", "frametime"], "title": qsTr("FPS and frametime"),
          "icon": App.UiIcons.couchGlyphFrametime, "iconOnLight": App.UiIcons.couchGlyphFrametimeOnLight },
        { "id": "metric-usage", "metrics": ["cpu_usage", "gpu_usage"], "title": qsTr("CPU and GPU load"),
          "icon": App.UiIcons.couchGlyphUsage, "iconOnLight": App.UiIcons.couchGlyphUsageOnLight },
        { "id": "metric-temps", "metrics": ["cpu_temperature", "gpu_temperature"], "title": qsTr("CPU and GPU temperatures"),
          "icon": App.UiIcons.couchGlyphTemperature, "iconOnLight": App.UiIcons.couchGlyphTemperatureOnLight },
        { "id": "metric-memory", "metrics": ["ram", "vram"], "title": qsTr("RAM and VRAM"),
          "icon": App.UiIcons.couchGlyphMemory, "iconOnLight": App.UiIcons.couchGlyphMemoryOnLight }
    ]
    readonly property int mangoHudGamescopeLimit: Number(mangoHudProfile.gamescopeFpsLimit || 0)
    readonly property bool mangoHudUsable: launcherIntegrationSupported && mangoHudProfile.available === true

    function mangoHudPositionLabel() {
        var labels = [qsTr("Top left"), qsTr("Top center"), qsTr("Top right"), qsTr("Middle left"), qsTr("Middle right"), qsTr("Bottom left"), qsTr("Bottom center"), qsTr("Bottom right")]
        var index = mangoHudPositions.indexOf(mangoHudPosition)
        return labels[index >= 0 ? index : 0]
    }
    function mangoHudSizeLabel() {
        return mangoHudFontSize <= 18 ? qsTr("Small") : mangoHudFontSize >= 32 ? qsTr("Large") : qsTr("Medium")
    }
    // Mirrors models/mangohud.py PRESET_METRICS for the short preset summary.
    function mangoHudPresetSummary(preset) {
        if (preset === "fps_only") return qsTr("FPS and frametime")
        if (preset === "basic") return qsTr("FPS, frametime, CPU/GPU load, temperatures, RAM/VRAM")
        if (preset === "extended") return qsTr("Basic plus clocks, power, process memory, Proton, resolution and GameMode")
        return qsTr("Choose the metrics below")
    }
    function mangoHudPresetMetrics(preset) {
        if (preset === "fps_only") return ["fps", "frametime"]
        if (preset === "basic" || preset === "extended")
            return ["fps", "frametime", "gpu_usage", "cpu_usage", "gpu_temperature", "cpu_temperature", "vram", "ram"]
        return []
    }
    function loadMangoHudProfile() {
        if (!launcherIntegrationSupported) { mangoHudProfile = ({}); return }
        if (!controller || !controller.getMangoHudProfile || !game.id) return
        var result = controller.getMangoHudProfile(String(game.id)) || ({})
        mangoHudProfile = result
        mangoHudPreset = String(result.preset || "disabled")
        mangoHudPosition = String(result.position || "top-left")
        mangoHudFontSize = Number(result.fontSize || 24)
        // Edit the stored MangoHud preference, not the effective value that is
        // blank while Gamescope owns the limit.
        mangoHudFpsLimit = Number(result.fpsLimitPreference !== undefined ? result.fpsLimitPreference : (result.fpsLimit || 0))
        mangoHudDraftMetrics = result.metrics ? Array.from(result.metrics) : []
        mangoHudDraftEnabled = result.enabled === true && mangoHudPreset !== "disabled"
        mangoHudEnabled = Boolean(result.activationEnabled)
    }
    function openMangoHudOverlay() {
        loadMangoHudProfile()
        mangoHudRow = mangoHudUsable ? 0 : mangoHudRows.length - 1
        mangoHudOverlayOpen = true
        if (navigation) navigation.openModal("mangohud-profile", "mangohud-profile")
        restoreActiveFocus()
    }
    function closeMangoHudOverlay() {
        mangoHudOverlayOpen = false
        mangoHudRow = 0
        if (navigation) navigation.closeModal()
        restoreActiveFocus()
    }
    function cycleValue(values, current, delta) {
        var index = values.indexOf(current)
        if (index < 0) index = 0
        return values[(index + delta + values.length) % values.length]
    }
    function mangoHudGroupOn(group) {
        return group.metrics.every(function(metric) { return mangoHudDraftMetrics.indexOf(metric) >= 0 })
    }
    readonly property var mangoHudRows: {
        var on = mangoHudUsable && mangoHudDraftEnabled
        var rows = [
            { "id": "enable", "icon": mangoHudDraftEnabled ? App.UiIcons.couchGlyphToggleOn : App.UiIcons.couchGlyphToggleOff,
              "iconOnLight": mangoHudDraftEnabled ? App.UiIcons.couchGlyphToggleOnOnLight : App.UiIcons.couchGlyphToggleOffOnLight,
              "title": qsTr("Use MangoHud"),
              "value": !launcherIntegrationSupported ? qsTr("Unsupported for this launch method")
                       : mangoHudProfile.available !== true ? qsTr("Unavailable")
                       : mangoHudDraftEnabled ? qsTr("On") : qsTr("Off"),
              "detail": "", "enabled": mangoHudUsable },
            { "id": "preset", "icon": App.UiIcons.couchGlyphPreset, "iconOnLight": App.UiIcons.couchGlyphPresetOnLight,
              "title": qsTr("Preset"), "value": mangoHudPresetLabel(), "detail": mangoHudPresetSummary(mangoHudPreset), "enabled": on },
            { "id": "position", "icon": App.UiIcons.couchGlyphPosition, "iconOnLight": App.UiIcons.couchGlyphPositionOnLight,
              "title": qsTr("Position"), "value": mangoHudPositionLabel(), "detail": "", "enabled": on },
            { "id": "size", "icon": App.UiIcons.couchGlyphTextSize, "iconOnLight": App.UiIcons.couchGlyphTextSizeOnLight,
              "title": qsTr("Interface size"), "value": mangoHudSizeLabel(), "detail": "", "enabled": on },
            { "id": "fps", "icon": App.UiIcons.couchGlyphFpsLimit, "iconOnLight": App.UiIcons.couchGlyphFpsLimitOnLight,
              "title": qsTr("FPS limit in MangoHud"),
              "value": mangoHudGamescopeLimit > 0 ? qsTr("Controlled by Gamescope: %1 FPS").arg(mangoHudGamescopeLimit)
                       : mangoHudFpsLimit > 0 ? qsTr("%1 FPS").arg(mangoHudFpsLimit) : qsTr("No limit"),
              "detail": mangoHudGamescopeLimit > 0 && mangoHudFpsLimit > 0
                        ? qsTr("Your %1 FPS limit is kept for later").arg(mangoHudFpsLimit) : "",
              "enabled": on && mangoHudGamescopeLimit <= 0 }
        ]
        if (mangoHudPreset === "custom") {
            mangoHudMetricGroups.forEach(function(group) {
                rows.push({ "id": group.id, "icon": group.icon, "iconOnLight": group.iconOnLight, "title": group.title,
                            "value": mangoHudGroupOn(group) ? qsTr("On") : qsTr("Off"), "detail": "", "enabled": on })
            })
        }
        rows.push({ "id": "save", "icon": App.UiIcons.couchGlyphSave, "iconOnLight": App.UiIcons.couchGlyphSaveOnLight,
                    "title": qsTr("Save"), "value": "", "detail": "", "enabled": mangoHudUsable || !mangoHudDraftEnabled })
        rows.push({ "id": "cancel", "icon": App.UiIcons.couchGlyphCancel, "iconOnLight": App.UiIcons.couchGlyphCancelOnLight,
                    "title": qsTr("Cancel"), "value": "", "detail": "", "enabled": true })
        return rows
    }
    function mangoHudActivationText() {
        if (!launcherIntegrationSupported) return qsTr("Saved, but not used by this launch method")
        if (mangoHudProfile.available !== true)
            return App.I18n.message(String(mangoHudProfile.availabilityMessage || qsTr("MangoHud is unavailable.")))
        var status = String(mangoHudProfile.strategyStatus || "")
        if (status === "executable_missing") return qsTr("The game executable has not been determined yet")
        if (status === "application_config_conflict") return qsTr("An existing MangoHud configuration is not managed by GameOpti")
        if (mangoHudProfile.requiresSteamRestart === true) return qsTr("Saved; restart Steam to apply it")
        return mangoHudDraftEnabled ? qsTr("MangoHud will be enabled for this game") : qsTr("MangoHud is off for this game")
    }
    function mangoHudLimiterText() {
        if (mangoHudGamescopeLimit > 0) return qsTr("FPS limiter: Gamescope (%1 FPS)").arg(mangoHudGamescopeLimit)
        if (mangoHudDraftEnabled && mangoHudFpsLimit > 0) return qsTr("FPS limiter: MangoHud (%1 FPS)").arg(mangoHudFpsLimit)
        return qsTr("FPS limiter: none")
    }
    function adjustMangoHud(key, delta) {
        if (key === "enable") {
            mangoHudDraftEnabled = !mangoHudDraftEnabled
            if (mangoHudDraftEnabled && mangoHudPreset === "disabled")
                mangoHudPreset = "basic"
        } else if (key === "preset") {
            var next = cycleValue(mangoHudMenuPresets, mangoHudPreset, delta)
            if (next === "custom" && mangoHudPreset !== "custom")
                mangoHudDraftMetrics = mangoHudPresetMetrics(mangoHudPreset)
            mangoHudPreset = next
        } else if (key === "position") {
            var positions = mangoHudMenuPositions.slice()
            if (positions.indexOf(mangoHudPosition) < 0) positions.push(mangoHudPosition)
            mangoHudPosition = cycleValue(positions, mangoHudPosition, delta)
        } else if (key === "size") {
            mangoHudFontSize = cycleValue(mangoHudFontSizes, mangoHudFontSize, delta)
        } else if (key === "fps") {
            var limits = mangoHudMenuFpsLimits.slice()
            if (limits.indexOf(mangoHudFpsLimit) < 0) limits.push(mangoHudFpsLimit)
            mangoHudFpsLimit = cycleValue(limits, mangoHudFpsLimit, delta)
        } else {
            var group = mangoHudMetricGroups.filter(function(g) { return g.id === key })[0]
            if (!group) return false
            var turnOn = !mangoHudGroupOn(group)
            var metrics = mangoHudDraftMetrics.filter(function(m) { return group.metrics.indexOf(m) < 0 })
            if (turnOn) metrics = metrics.concat(group.metrics)
            mangoHudDraftMetrics = metrics
        }
        return true
    }
    function couchMangoHudMetrics() {
        return mangoHudPreset === "custom" ? mangoHudDraftMetrics : (mangoHudProfile.metrics || [])
    }
    function saveCouchMangoHud() {
        if (!controller || !controller.saveMangoHudProfile) return false
        var source = mangoHudProfile || ({})
        var preset = mangoHudPreset === "disabled" && mangoHudDraftEnabled ? "basic" : mangoHudPreset
        var payload = {
            "enabled": mangoHudDraftEnabled,
            "preset": preset,
            "position": mangoHudPosition,
            "fontSize": mangoHudFontSize,
            "backgroundAlpha": Number(source.backgroundAlpha !== undefined ? source.backgroundAlpha : 0.5),
            "roundCorners": Number(source.roundCorners !== undefined ? source.roundCorners : 8),
            "compact": Boolean(source.compact),
            "horizontal": Boolean(source.horizontal),
            "tableColumns": Number(source.tableColumns || 3),
            // While Gamescope owns the limit the stored MangoHud value is kept
            // by the controller; 0 never erases it.
            "fpsLimit": mangoHudGamescopeLimit > 0 ? 0 : mangoHudFpsLimit,
            "toggleHudKey": String(source.toggleHudKey || "Shift_R+F12"),
            "metrics": couchMangoHudMetrics(),
            "loggingEnabled": Boolean(source.loggingEnabled),
            "logDuration": Number(source.logDuration || 60),
            "logInterval": Number(source.logInterval !== undefined ? source.logInterval : 0.1),
            "outputFolder": String(source.outputFolder || ""),
            "toggleLoggingKey": String(source.toggleLoggingKey || "Shift_L+F2")
        }
        var result = controller.saveMangoHudProfile(String(game.id || ""), payload) || ({})
        if (result.success) {
            loadMangoHudProfile()
            loadOptimizationProfile()
            closeMangoHudOverlay()
            return true
        }
        return false
    }
    function handleMangoHudAction(action) {
        var row = mangoHudRows[mangoHudRow] || ({})
        if (action === "Back") {
            closeMangoHudOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateUp" || action === "NavigateDown") {
            var previousRow = mangoHudRow
            mangoHudRow = moveOverlayRow(mangoHudOptions, mangoHudRow,
                                         action === "NavigateUp" ? -1 : 1, mangoHudRows.length - 1)
            if (mangoHudRow !== previousRow)
                playSemanticSound("navigate")
        } else if (row.id === "save" && action === "Confirm") {
            playSemanticSound(saveCouchMangoHud() ? "confirm" : "error")
        } else if (row.id === "cancel" && action === "Confirm") {
            closeMangoHudOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateLeft" || action === "NavigateRight" || action === "Confirm") {
            if (!row.enabled || row.id === "save" || row.id === "cancel") { playSemanticSound("error"); return }
            playSemanticSound(adjustMangoHud(row.id, action === "NavigateLeft" ? -1 : 1) ? "adjust" : "error")
        }
        restoreActiveFocus()
    }
    // ---- Dedicated GameMode and Gamescope menus ------------------------------
    // Values are canonical ids (e.g. "fullscreen", "fsr", "1920x1080"); labels
    // are translated only for display. Nothing is saved until Save.
    property bool gameModeOverlayOpen: false
    property int gameModeRow: 0
    property bool gameModeDraft: false
    property bool gamescopeOverlayOpen: false
    property int gamescopeRow: 0
    property var gamescopeDraft: ({})
    property var setupPreview: ({})
    readonly property var launchActivation: optimizationData && optimizationData.launchActivation
                                            ? optimizationData.launchActivation : ({ "state": "unknown", "message": "" })
    readonly property var gamescopeTool: optimizationData && optimizationData.gamescope ? optimizationData.gamescope : ({})
    readonly property var gameModeTool: optimizationData && optimizationData.gamemode ? optimizationData.gamemode : ({})

    function activationText(state) {
        if (state === "active") return qsTr("Applied when this game starts")
        if (state === "runner_not_configured") return qsTr("Saved, but not applied: Steam does not start this game through the GameOpti runner")
        if (state === "unsupported") return qsTr("Not applied for this launch method")
        return qsTr("Launch integration could not be verified")
    }
    function mergedOverrides(key) {
        var result = Object.assign({}, (optimizationData && optimizationData.manualOverrides) || {})
        result[key] = true
        return result
    }
    function refreshSetupPreview(payload) {
        if (!controller || !controller.previewOptimizationProfile || !game.id) {
            setupPreview = ({})
            return
        }
        setupPreview = controller.previewOptimizationProfile(String(game.id), payload) || ({})
    }
    function saveSetup(payload) {
        if (!controller || !controller.saveOptimizationProfile || !game.id)
            return false
        var result = controller.saveOptimizationProfile(String(game.id), payload) || ({})
        if (result.success !== true)
            return false
        loadOptimizationProfile()
        return true
    }

    // GameMode --------------------------------------------------------------
    function gameModeStatus() {
        if (launchActivation.state === "unsupported" || !launcherIntegrationSupported)
            return { "tone": "unavailable", "text": qsTr("Unavailable for this launch method") }
        if (gameModeTool.available === true)
            return { "tone": "success", "text": qsTr("Available") }
        return { "tone": "warning", "text": qsTr("Not installed") }
    }
    function gameModePayload() {
        return { "preset": String(optimizationData.preset || "automatic"),
                 "manualOverrides": mergedOverrides("gamemode"), "gamemodeEnabled": gameModeDraft }
    }
    function gameModeApplication() {
        if (!gameModeDraft)
            return { "tone": "neutral", "text": qsTr("The game starts without gamemoderun") }
        var plan = setupPreview && setupPreview.launchPlan ? setupPreview.launchPlan : ({})
        var wrapped = (plan.gameModeWrapper || []).length > 0
        if (!wrapped)
            return { "tone": "warning", "text": qsTr("gamemoderun would not be added: %1").arg(App.I18n.message(String(gameModeTool.message || ""))) }
        if (launchActivation.state !== "active")
            return { "tone": "warning", "text": activationText(launchActivation.state) }
        return { "tone": "success", "text": qsTr("The next launch receives gamemoderun") }
    }
    readonly property var gameModeRows: [
        { "id": "enable", "icon": gameModeDraft ? App.UiIcons.couchGlyphToggleOn : App.UiIcons.couchGlyphToggleOff,
          "iconOnLight": gameModeDraft ? App.UiIcons.couchGlyphToggleOnOnLight : App.UiIcons.couchGlyphToggleOffOnLight,
          "title": qsTr("GameMode for this game"), "value": gameModeDraft ? qsTr("On") : qsTr("Off"),
          "enabled": gameModeTool.available === true && gameModeStatus().tone !== "unavailable" },
        { "id": "save", "icon": App.UiIcons.couchGlyphSave, "iconOnLight": App.UiIcons.couchGlyphSaveOnLight,
          "title": qsTr("Save"), "value": "", "enabled": gameModeStatus().tone !== "unavailable" },
        { "id": "cancel", "icon": App.UiIcons.couchGlyphCancel, "iconOnLight": App.UiIcons.couchGlyphCancelOnLight,
          "title": qsTr("Cancel"), "value": "", "enabled": true }
    ]
    function openGameModeOverlay() {
        loadOptimizationProfile()
        gameModeDraft = Boolean(optimizationData.gamemodeEnabled)
        gameModeRow = gameModeRows[0].enabled ? 0 : 2
        gameModeOverlayOpen = true
        refreshSetupPreview(gameModePayload())
        if (navigation) navigation.openModal("gamemode", "gamemode-enable")
        restoreActiveFocus()
    }
    function closeGameModeOverlay() {
        gameModeOverlayOpen = false
        gameModeRow = 0
        setupPreview = ({})
        if (navigation) navigation.closeModal()
        restoreActiveFocus()
    }
    function handleGameModeAction(action) {
        var row = gameModeRows[gameModeRow] || ({})
        if (action === "Back") {
            closeGameModeOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateUp" || action === "NavigateDown") {
            var previous = gameModeRow
            gameModeRow = moveOverlayRow(gameModeOptions, gameModeRow, action === "NavigateUp" ? -1 : 1, gameModeRows.length - 1)
            if (gameModeRow !== previous) playSemanticSound("navigate")
        } else if ((action === "NavigateLeft" || action === "NavigateRight" || action === "Confirm") && row.id === "enable") {
            if (!row.enabled) { playSemanticSound("error"); return }
            gameModeDraft = !gameModeDraft
            refreshSetupPreview(gameModePayload())
            playSemanticSound("adjust")
        } else if (action === "Confirm" && row.id === "save") {
            var saved = saveSetup(gameModePayload())
            if (saved) closeGameModeOverlay()
            playSemanticSound(saved ? "confirm" : "error")
        } else if (action === "Confirm" && row.id === "cancel") {
            closeGameModeOverlay()
            playSemanticSound("back")
        }
        restoreActiveFocus()
    }

    // Gamescope -------------------------------------------------------------
    function gsSupports(flag) { return (gamescopeTool.supportedOptions || []).indexOf(flag) >= 0 }
    function resolutionLabel(value) {
        if (value === "auto") return qsTr("Automatic (same as output)")
        if (String(value).indexOf("display:") === 0) return qsTr("Monitor (%1)").arg(String(value).slice(8).replace("x", "×"))
        return String(value).replace("x", "×")
    }
    function monitorResolution() {
        var displays = optimizationDisplays || []
        var chosen = null
        for (var i = 0; i < displays.length; ++i)
            if (String(displays[i].id || "") === optimizationDisplayId || (!chosen && displays[i].primary))
                chosen = displays[i]
        if (!chosen && displays.length) chosen = displays[0]
        if (!chosen) return ""
        var dpr = Number(chosen.devicePixelRatio || 1)
        var w = Math.round(Number(chosen.width || 0) * dpr), h = Math.round(Number(chosen.height || 0) * dpr)
        return w > 0 && h > 0 ? w + "x" + h : ""
    }
    function gsOptions(key) {
        var draft = gamescopeDraft || ({})
        if (key === "input") {
            var inputs = ["auto", "1280x720", "1600x900", "1920x1080", "2560x1440"]
            if (inputs.indexOf(draft.input) < 0) inputs.push(draft.input)
            return inputs
        }
        if (key === "output") {
            var outputs = []
            var monitor = monitorResolution()
            if (monitor.length) outputs.push("display:" + monitor)
            ;["1920x1080", "2560x1440", "3840x2160"].forEach(function(v) { if (v !== monitor) outputs.push(v) })
            if (outputs.indexOf(draft.output) < 0) outputs.push(draft.output)
            return outputs
        }
        if (key === "scaler")
            return ["auto", "fit", "fill", "stretch", "integer"].filter(function(v) {
                return v === "auto" || (gamescopeTool.supportedScalers || []).indexOf(v) >= 0 })
        if (key === "filter")
            return ["linear", "nearest", "fsr", "nis", "sgsr"].filter(function(v) {
                return v === "linear" || (gamescopeTool.supportedFilters || []).indexOf(v) >= 0 })
        if (key === "sharpness") return [-1, 0, 5, 10, 15, 20]
        if (key === "fps") return ["unlimited", 30, 40, 60, 90, 120, 144, 165]
        if (key === "window") return ["windowed", "borderless", "fullscreen"]
        return [false, true]
    }
    function gsValueLabel(key, value) {
        if (key === "input" || key === "output") return resolutionLabel(value)
        if (key === "scaler") return ({ "auto": qsTr("Automatic"), "fit": qsTr("Fit"), "fill": qsTr("Fill"), "stretch": qsTr("Stretch"), "integer": qsTr("Integer") })[value] || value
        if (key === "filter") return ({ "linear": qsTr("Linear"), "nearest": qsTr("Nearest"), "fsr": "FSR", "nis": "NIS", "sgsr": "SGSR" })[value] || value
        if (key === "sharpness") return value < 0 ? qsTr("Gamescope default") : value === 0 ? qsTr("Maximum") : value <= 5 ? qsTr("High")
                                        : value <= 10 ? qsTr("Medium") : value <= 15 ? qsTr("Low") : qsTr("Minimum")
        if (key === "fps") return value === "unlimited" ? qsTr("No limit") : qsTr("%1 FPS").arg(value)
        if (key === "window") return ({ "windowed": qsTr("Window"), "borderless": qsTr("Borderless"), "fullscreen": qsTr("Fullscreen") })[value] || value
        return value ? qsTr("On") : qsTr("Off")
    }
    function gsSize(value) {
        var text = String(value)
        if (text.indexOf("display:") === 0) text = text.slice(8)
        var parts = text.split("x")
        return { "w": Number(parts[0]), "h": Number(parts[1]) }
    }
    function gamescopeDraftFromData() {
        var d = optimizationData || ({})
        var outW = Number(d.gamescopeOutputWidth || 1920), outH = Number(d.gamescopeOutputHeight || 1080)
        var inW = Number(d.gamescopeInputWidth || outW), inH = Number(d.gamescopeInputHeight || outH)
        var output = outW + "x" + outH
        if (output === monitorResolution()) output = "display:" + output
        return {
            "enabled": Boolean(d.gamescopeEnabled) && String(d.gamescopeMode || "disabled") !== "disabled",
            "input": inW === outW && inH === outH ? "auto" : inW + "x" + inH,
            "output": output,
            "scaler": String(d.gamescopeScaler || "auto"),
            "filter": String(d.gamescopeFilter || "linear"),
            "sharpness": Number(d.gamescopeSharpness === undefined ? -1 : d.gamescopeSharpness),
            "fps": String(d.targetFpsMode || "automatic") === "unlimited" ? "unlimited" : Number(d.targetFps || 60),
            "window": String(d.gamescopeWindowMode || (d.gamescopeFullscreen === false ? "borderless" : "fullscreen")),
            "vrr": Boolean(d.gamescopeAdaptiveSync),
            "grab": Boolean(d.gamescopeGrabKeyboard),
            "hdr": Boolean(d.gamescopeHdr)
        }
    }
    function gamescopePayload() {
        var draft = gamescopeDraft || ({})
        var out = gsSize(draft.output)
        var input = draft.input === "auto" ? out : gsSize(draft.input)
        return {
            "preset": String(optimizationData.preset || "automatic"),
            "manualOverrides": mergedOverrides("gamescope"),
            "gamescopeEnabled": Boolean(draft.enabled),
            "gamescopeMode": draft.enabled ? "custom" : String(optimizationData.gamescopeMode || "disabled"),
            "gamescopeInputWidth": input.w, "gamescopeInputHeight": input.h,
            "gamescopeOutputWidth": out.w, "gamescopeOutputHeight": out.h,
            "gamescopeScaler": draft.scaler, "gamescopeFilter": draft.filter,
            "gamescopeSharpness": (draft.filter === "fsr" || draft.filter === "nis") ? draft.sharpness : -1,
            "targetFpsMode": draft.fps === "unlimited" ? "unlimited" : "manual",
            "targetFps": draft.fps === "unlimited" ? Number(optimizationData.targetFps || 60) : Number(draft.fps),
            "gamescopeWindowMode": draft.window,
            "gamescopeAdaptiveSync": Boolean(draft.vrr) && gsSupports("--adaptive-sync"),
            "gamescopeGrabKeyboard": Boolean(draft.grab) && gsSupports("-g"),
            "gamescopeHdr": Boolean(draft.hdr) && gsSupports("--hdr-enabled")
        }
    }
    function gamescopeSummary() {
        var draft = gamescopeDraft || ({})
        if (!draft.enabled) return qsTr("Gamescope is off for this game")
        var out = gsSize(draft.output)
        var input = draft.input === "auto" ? out : gsSize(draft.input)
        var parts = [input.w + "×" + input.h + " → " + out.w + "×" + out.h]
        if (draft.filter !== "linear") parts.push(gsValueLabel("filter", draft.filter))
        parts.push(draft.fps === "unlimited" ? qsTr("No FPS limit") : qsTr("%1 FPS").arg(draft.fps))
        parts.push(gsValueLabel("window", draft.window))
        return parts.join(" · ")
    }
    readonly property bool gamescopeUsable: gamescopeTool.available === true && launcherIntegrationSupported
                                            && launchActivation.state !== "unsupported"
    readonly property var gamescopeRows: {
        var draft = gamescopeDraft || ({})
        var on = gamescopeUsable && Boolean(draft.enabled)
        function row(key, icon, iconOnLight, title, detail, enabled) {
            return { "id": key, "icon": icon, "iconOnLight": iconOnLight, "title": title,
                     "value": gsValueLabel(key, draft[key]), "detail": detail || "", "enabled": enabled }
        }
        var rows = [
            { "id": "enabled", "icon": draft.enabled ? App.UiIcons.couchGlyphToggleOn : App.UiIcons.couchGlyphToggleOff,
              "iconOnLight": draft.enabled ? App.UiIcons.couchGlyphToggleOnOnLight : App.UiIcons.couchGlyphToggleOffOnLight,
              "title": qsTr("Use Gamescope"), "value": draft.enabled ? qsTr("On") : qsTr("Off"), "detail": "", "enabled": gamescopeUsable },
            row("input", App.UiIcons.couchGlyphResolutionGame, App.UiIcons.couchGlyphResolutionGameOnLight, qsTr("Game render resolution"), "", on),
            row("output", App.UiIcons.couchGlyphResolutionOutput, App.UiIcons.couchGlyphResolutionOutputOnLight, qsTr("Output resolution"), "", on),
            row("scaler", App.UiIcons.couchGlyphFit, App.UiIcons.couchGlyphFitOnLight, qsTr("Image fit"), "", on && gsSupports("-S")),
            row("filter", App.UiIcons.couchGlyphFilter, App.UiIcons.couchGlyphFilterOnLight, qsTr("Scaling filter"), "", on && gsSupports("-F"))
        ]
        if ((draft.filter === "fsr" || draft.filter === "nis") && (gsSupports("--sharpness") || gsSupports("--fsr-sharpness")))
            rows.push(row("sharpness", App.UiIcons.couchGlyphSharpness, App.UiIcons.couchGlyphSharpnessOnLight, qsTr("Sharpness"),
                          qsTr("Higher sharpness looks crisper but can add halos"), on))
        rows.push(row("fps", App.UiIcons.couchGlyphFps, App.UiIcons.couchGlyphFpsOnLight, qsTr("Target FPS in Gamescope"),
                      qsTr("The game's refresh inside Gamescope"), on && gsSupports("-r")))
        if (gsSupports("--adaptive-sync"))
            rows.push(row("vrr", App.UiIcons.couchGlyphVrr, App.UiIcons.couchGlyphVrrOnLight, qsTr("VRR / Adaptive Sync"),
                          draft.vrr ? qsTr("Display support not verified") : "", on))
        var windowIcon = draft.window === "fullscreen" ? "Fullscreen" : draft.window === "borderless" ? "Borderless" : "Windowed"
        rows.push(row("window", App.UiIcons["couchGlyph" + windowIcon], App.UiIcons["couchGlyph" + windowIcon + "OnLight"], qsTr("Window mode"), "", on))
        if (gsSupports("-g"))
            rows.push(row("grab", App.UiIcons.couchGlyphGrab, App.UiIcons.couchGlyphGrabOnLight, qsTr("Capture keyboard"),
                          qsTr("Gamescope takes over keyboard shortcuts"), on))
        if (gsSupports("--hdr-enabled"))
            rows.push(row("hdr", App.UiIcons.couchGlyphHdr, App.UiIcons.couchGlyphHdrOnLight, qsTr("HDR (experimental)"),
                          qsTr("Needs a compatible display, driver and Gamescope WSI; may not work in every game or session"), on))
        rows.push({ "id": "save", "icon": App.UiIcons.couchGlyphSave, "iconOnLight": App.UiIcons.couchGlyphSaveOnLight,
                    "title": qsTr("Save"), "value": "", "detail": "", "enabled": gamescopeUsable })
        rows.push({ "id": "cancel", "icon": App.UiIcons.couchGlyphCancel, "iconOnLight": App.UiIcons.couchGlyphCancelOnLight,
                    "title": qsTr("Cancel"), "value": "", "detail": "", "enabled": true })
        return rows
    }
    function gamescopeUnavailableReason() {
        if (!launcherIntegrationSupported || launchActivation.state === "unsupported")
            return qsTr("Unavailable for this launch method")
        if (gamescopeTool.available !== true)
            return App.I18n.message(String(gamescopeTool.message || qsTr("Gamescope is not installed")))
        return ""
    }
    function openGamescopeOverlay() {
        loadOptimizationProfile()
        gamescopeDraft = gamescopeDraftFromData()
        gamescopeRow = gamescopeUsable ? 0 : gamescopeRows.length - 1
        gamescopeOverlayOpen = true
        refreshSetupPreview(gamescopePayload())
        if (navigation) navigation.openModal("gamescope", "gamescope-enabled")
        restoreActiveFocus()
    }
    function closeGamescopeOverlay() {
        gamescopeOverlayOpen = false
        gamescopeRow = 0
        setupPreview = ({})
        if (navigation) navigation.closeModal()
        restoreActiveFocus()
    }
    function adjustGamescope(key, delta) {
        var draft = Object.assign({}, gamescopeDraft)
        if (key === "enabled") draft.enabled = !draft.enabled
        else {
            var options = gsOptions(key)
            var index = options.indexOf(draft[key])
            draft[key] = options[((index < 0 ? 0 : index) + delta + options.length) % options.length]
        }
        gamescopeDraft = draft
        refreshSetupPreview(gamescopePayload())
    }
    function handleGamescopeAction(action) {
        var row = gamescopeRows[gamescopeRow] || ({})
        if (action === "Back") {
            closeGamescopeOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateUp" || action === "NavigateDown") {
            var previous = gamescopeRow
            gamescopeRow = moveOverlayRow(gamescopeOptions, gamescopeRow, action === "NavigateUp" ? -1 : 1, gamescopeRows.length - 1)
            if (gamescopeRow !== previous) playSemanticSound("navigate")
        } else if (row.id === "save" && action === "Confirm") {
            var saved = saveSetup(gamescopePayload())
            if (saved) closeGamescopeOverlay()
            playSemanticSound(saved ? "confirm" : "error")
        } else if (row.id === "cancel" && action === "Confirm") {
            closeGamescopeOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateLeft" || action === "NavigateRight" || action === "Confirm") {
            if (!row.enabled || row.id === "save" || row.id === "cancel") { playSemanticSound("error"); return }
            adjustGamescope(row.id, action === "NavigateLeft" ? -1 : 1)
            playSemanticSound("adjust")
        }
        restoreActiveFocus()
    }

    function prepareCompression() {
        if (!controller || !controller.prepareCompression || !game.id) return false
        var plan = controller.prepareCompression(String(game.id), selectedProfile, true)
        if (!planValid(plan)) return false
        pendingPlan = plan
        confirmationChoice = 0
        confirmationKind = "compression"
        confirmationOpen = true
        if (navigation) navigation.openModal("compression-confirmation", "cancel")
        return true
    }
    function closeConfirmation() {
        if (!confirmationOpen) return false
        if (confirmationKind === "optiscaler_conflict")
            optiScalerDismissedConflict = String(optiScalerConflict.digest || "")
        confirmationOpen = false
        confirmationKind = ""
        pendingPlan = ({})
        confirmationChoice = 0
        if (navigation) navigation.closeModal()
        return true
    }
    function confirmationTitle() {
        if (confirmationKind === "optiscaler_remove") return qsTr("Remove OptiScaler?")
        if (confirmationKind === "optiscaler_update") return qsTr("Update OptiScaler?")
        if (confirmationKind === "optiscaler_repair") return qsTr("Repair OptiScaler?")
        if (confirmationKind === "optiscaler_reinstall") return qsTr("Reinstall OptiScaler?")
        if (confirmationKind === "optiscaler_install") return qsTr("Install OptiScaler?")
        if (confirmationKind === "optiscaler_conflict") return qsTr("Replace existing files?")
        return qsTr("Review compression plan")
    }
    function confirmationDescription() {
        if (confirmationKind === "optiscaler_remove")
            return qsTr("Only files recorded as created by GameOpti will be removed. Replaced files remain available for restoration in Desktop Mode.")
        if (confirmationKind === "optiscaler_conflict") {
            var files = (optiScalerConflict.files || []).slice(0, 5).map(function(item) {
                return "• " + String(item.relativePath || "")
            })
            var more = Number(optiScalerConflict.fileCount || 0) - files.length
            return qsTr("Installing would replace %1 existing file(s) that Game Optimization did not create:")
                       .arg(Number(optiScalerConflict.fileCount || 0))
                   + "\n" + files.join("\n")
                   + (more > 0 ? "\n" + qsTr("…and %1 more").arg(more) : "")
                   + "\n" + qsTr("Each replaced file is backed up first and can be restored later.")
        }
        if (confirmationKind.indexOf("optiscaler_") === 0)
            return qsTr("Use the verified official release. Existing target files are backed up before replacement. Do not use injection in online or anti-cheat protected games unless you accept the compatibility and account risk.")
        return qsTr("Profile: %1. Review warnings before starting. No operation starts until explicit confirmation.").arg(selectedProfile)
    }
    function confirmationButtonLabel() {
        if (confirmationKind === "optiscaler_remove") return qsTr("Remove")
        if (confirmationKind === "optiscaler_update") return qsTr("Update")
        if (confirmationKind === "optiscaler_repair") return qsTr("Repair")
        if (confirmationKind === "optiscaler_reinstall") return qsTr("Reinstall")
        if (confirmationKind === "optiscaler_install") return qsTr("Install")
        if (confirmationKind === "optiscaler_conflict") return qsTr("Back up and replace")
        return qsTr("Start task")
    }
    function activateAction() {
        if (selectedAction < 0 || !actionModel[selectedAction]
                || !actionModel[selectedAction].enabled || !controller)
            return "error"
        var id = actionModel[selectedAction].id
        if (id === "launch") {
            var launched = Boolean(controller.launchGame(String(game.id || "")))
            launchPending = launched
            if (launched) launchGuard.restart()
            return launched ? "confirm" : "error"
        }
        if (id === "updates") {
            controller.navigate("updates")
            return "confirm"
        }
        if (id === "analyze")
            return controller.analyzeGame(String(game.id || "")) ? "confirm" : "error"
        if (id === "verify")
            return controller.verifyCompression(String(game.id || "")) ? "confirm" : "error"
        if (id === "profile") {
            var index = profileNames.indexOf(selectedProfile)
            selectedProfile = profileNames[(index + 1) % profileNames.length]
            return "adjust"
        }
        if (id === "compress")
            return prepareCompression() ? "open" : "error"
        if (id.indexOf("narrator-") === 0) {
            var narratorResult = activateNarratorAction(id)
            return narratorResult ? (id === "narrator-region" ? "open" : "confirm") : "error"
        }
        if (id === "gamemode") {
            openGameModeOverlay()
            return "open"
        }
        if (id === "gamescope") {
            openGamescopeOverlay()
            return "open"
        }
        if (id === "optimization-profile" || id === "fps" || id === "resolution") {
            openOptimizationOverlay()
            return "open"
        }
        if (id === "mangohud-profile") {
            openMangoHudOverlay()
            return "open"
        }
        if (id === "optiscaler-refresh")
            return refreshCouchOptiScaler() ? "confirm" : "error"
        if (id === "optiscaler-retry-status") {
            if (controller && controller.requestOptiScalerStatus)
                applyOptiScalerStatus(controller.requestOptiScalerStatus(String(game.id || ""), true) || ({}))
            return "confirm"
        }
        if (id === "optiscaler-install")
            return openOptiScalerConfirmation(optiScalerOperation()) ? "open" : "error"
        if (id === "optiscaler-configure")
            return configureCouchOptiScaler() ? "adjust" : "error"
        if (id === "optiscaler-verify")
            return controller.verifyOptiScaler
                    && controller.verifyOptiScaler(String(game.id || "")) ? "confirm" : "error"
        if (id === "optiscaler-launch")
            return controller.launchGame(String(game.id || "")) ? "confirm" : "error"
        if (id === "optiscaler-remove") {
            confirmationChoice = 0
            confirmationKind = "optiscaler_remove"
            confirmationOpen = true
            if (navigation) navigation.openModal("optiscaler-remove", "cancel")
            restoreActiveFocus()
            return "open"
        }
        return "error"
    }
    function handleAction(action) {
        if (action === "ContextMenu" || action === "Search") action = "MoreActions"
        else if (action === "PageLeft" || action === "PreviousSection") action = "PreviousTab"
        else if (action === "PageRight" || action === "NextSection") action = "NextTab"
        if (gameModeOverlayOpen) {
            handleGameModeAction(action)
            return
        }
        if (gamescopeOverlayOpen) {
            handleGamescopeAction(action)
            return
        }
        if (optimizationOverlayOpen) {
            handleOptimizationAction(action)
            return
        }
        if (mangoHudOverlayOpen) {
            handleMangoHudAction(action)
            return
        }
        if (confirmationOpen) {
            if (action === "Back") {
                closeConfirmation()
                playSemanticSound("back")
            } else if (action === "NavigateLeft" || action === "NavigateUp"
                       || action === "NavigateRight" || action === "NavigateDown") {
                var previousConfirmationChoice = confirmationChoice
                confirmationChoice = (action === "NavigateLeft" || action === "NavigateUp") ? 0 : 1
                if (confirmationChoice !== previousConfirmationChoice)
                    playSemanticSound("navigate")
            } else if (action === "Confirm") {
                var completed = true
                if (confirmationChoice === 1 && controller) {
                    if (confirmationKind === "optiscaler_remove")
                        completed = Boolean(controller.removeOptiScaler(String(game.id || "")))
                    else if (confirmationKind.indexOf("optiscaler_") === 0)
                        completed = beginCouchOptiScalerOperation()
                    else {
                        var planId = String(pendingPlan.planId || pendingPlan.plan_id || "")
                        completed = Boolean(planId.length && controller.startCompression(planId))
                    }
                }
                closeConfirmation()
                playSemanticSound(completed ? "confirm" : "error")
            }
            return
        }
        if (action === "Back") {
            backRequested()
            playSemanticSound("back")
        } else if (action === "PreviousTab" || action === "NextTab") {
            if (changeTab(action === "PreviousTab" ? -1 : 1))
                playSemanticSound("navigate")
        } else if (action === "PageUp" || action === "PageDown") {
            var previousArea = focusArea
            var previousPageY = detailsContentFlick.contentY
            focusArea = 3
            if (action === "PageUp")
                detailsContentFlick.contentY = Math.max(0, detailsContentFlick.contentY - detailsContentFlick.height * 0.72)
            else
                detailsContentFlick.contentY = Math.min(
                    Math.max(0, detailsContentFlick.contentHeight - detailsContentFlick.height),
                    detailsContentFlick.contentY + detailsContentFlick.height * 0.72)
            if (focusArea !== previousArea
                    || detailsContentFlick.contentY !== previousPageY)
                playSemanticSound("navigate")
        } else if (focusArea === 0) {
            // Header cards: 2 rows x 3 columns.
            if (action === "NavigateLeft" || action === "NavigateRight") {
                if (moveHeader(action === "NavigateLeft" ? -1 : 1, 0))
                    playSemanticSound("navigate")
            } else if (action === "NavigateUp") {
                if (moveHeader(0, -1))
                    playSemanticSound("navigate")
            } else if (action === "NavigateDown") {
                if (!moveHeader(0, 1))
                    focusArea = 1
                playSemanticSound("navigate")
            } else if (action === "Confirm") {
                playSemanticSound(activateHeader())
            }
        } else if (focusArea === 1) {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                if (changeTab(action === "NavigateLeft" ? -1 : 1))
                    playSemanticSound("navigate")
            } else if (action === "NavigateUp") {
                focusArea = 0
                if (headerIndex < 3 && headerCards[headerIndex + 3] && headerCards[headerIndex + 3].enabled)
                    headerIndex += 3
                ensureHeaderCard()
                playSemanticSound("navigate")
            } else if (action === "NavigateDown" || action === "Confirm") {
                focusArea = actionModel.some(function(entry) { return entry.enabled }) ? 2 : 3
                ensureAction()
                playSemanticSound("navigate")
            }
        } else if (focusArea === 2) {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                if (moveAction(action === "NavigateLeft" ? -1 : 1))
                    playSemanticSound("navigate")
            } else if (action === "NavigateUp") {
                if (!moveActionRow(-1))
                    focusArea = 1
                playSemanticSound("navigate")
            } else if (action === "NavigateDown") {
                if (!moveActionRow(1))
                    focusArea = 3
                playSemanticSound("navigate")
            } else if (action === "Confirm") {
                playSemanticSound(activateAction())
            }
        } else if (action === "NavigateUp") {
            // Scrollable content: scroll first, then return to the actions.
            var previousUpY = detailsContentFlick.contentY
            if (detailsContentFlick.contentY > 0)
                detailsContentFlick.contentY = Math.max(0, detailsContentFlick.contentY - 96 * couchScale)
            else
                focusArea = actionModel.some(function(entry) { return entry.enabled }) ? 2 : 1
            if (detailsContentFlick.contentY !== previousUpY || focusArea !== 3)
                playSemanticSound("navigate")
        } else if (action === "NavigateDown") {
            var previousDownY = detailsContentFlick.contentY
            detailsContentFlick.contentY = Math.min(
                Math.max(0, detailsContentFlick.contentHeight - detailsContentFlick.height),
                detailsContentFlick.contentY + 96 * couchScale)
            if (detailsContentFlick.contentY !== previousDownY)
                playSemanticSound("navigate")
        }
        if (action === "MoreActions") {
            var changed = selectedTab !== 0 || focusArea !== 0 || headerIndex !== 0
            selectedTab = 0
            focusArea = 0
            headerIndex = 0
            selectedAction = 0
            if (changed)
                playSemanticSound("navigate")
        }
    }
    Timer { id: launchGuard; interval: 1800; onTriggered: page.launchPending = false }
    onSelectedTabChanged: Qt.callLater(ensureAction)
    focus: visible
    Component.onCompleted: { loadMangoHudProfile(); loadOptimizationProfile(); loadOptiScalerStatus(); loadProtonTweaks(); loadNarrator(); restoreActiveFocus() }
    onVisibleChanged: if (visible) { loadMangoHudProfile(); loadNarrator(); restoreActiveFocus() }
    onGameChanged: { focusArea = 0; headerIndex = 0; loadMangoHudProfile(); loadOptimizationProfile(); loadOptiScalerStatus(); loadProtonTweaks(); loadNarrator(); Qt.callLater(ensureHeaderCard) }

    Connections {
        target: page.controller || null
        ignoreUnknownSignals: true
        function onOptiScalerChanged(appId) {
            if (String(appId) === String(page.game.steamAppId || ""))
                page.loadOptiScalerStatus()
        }
        function onOptiScalerStatusChanged(changedGameId, result) {
            if (String(changedGameId) === String(page.game.id || ""))
                page.applyOptiScalerStatus(result)
        }
        function onNarratorChanged(changedGameId) {
            if (String(changedGameId) === String(page.game.id || ""))
                page.loadNarrator()
        }
        function onNarratorRegionSelectionChanged(changedGameId, region) {
            if (String(changedGameId) === String(page.game.id || ""))
                page.loadNarrator()
        }
        function onProtonTweaksChanged(appId) {
            if (String(appId) === String(page.game.steamAppId || ""))
                page.loadProtonTweaks()
        }
    }

    CouchHeroBackdrop {
        id: detailsBackdrop
        anchors.fill: parent
        artHeightRatio: 0.62
        sources: detailsBackdrop.sourcesFor(page.game)
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 64 * page.couchScale; anchors.rightMargin: 64 * page.couchScale
        anchors.topMargin: 100 * page.couchScale; anchors.bottomMargin: 84 * page.couchScale
        spacing: App.Theme.couchSpaceL * page.couchScale
        RowLayout {
            id: detailsHeader
            Layout.fillWidth: true
            // Portrait cover height; the card grid on the right must fit it.
            readonly property real contentHeight:
                Math.max(410 * page.couchScale, detailsInfo.implicitHeight)
            Layout.minimumHeight: contentHeight
            Layout.preferredHeight: contentHeight
            Layout.maximumHeight: contentHeight
            spacing: 30 * page.couchScale
            Rectangle {
                objectName: "couchDetailsCover"
                Layout.preferredWidth: 276 * page.couchScale
                Layout.fillHeight: true
                radius: App.Theme.couchCardRadius * page.couchScale
                color: App.Theme.surface
                clip: true
                GameArtwork {
                    anchors.fill: parent
                    gameId: String(page.game.id || "")
                    title: String(page.game.name || qsTr("Game"))
                    launcher: String(page.game.launcher || "Steam")
                    artworkSource: page.game.portraitArtwork
                                   || page.game.effectiveArtworkUrl
                                   || page.game.headerArtwork
                                   || page.game.fallbackArtwork || ""
                    artworkFillMode: Image.PreserveAspectCrop
                    cornerRadius: App.Theme.couchCardRadius * page.couchScale
                }
                Rectangle {
                    anchors.fill: parent
                    radius: parent.radius
                    color: "transparent"
                    border.width: 1
                    border.color: App.Theme.borderStrong
                }
            }
            ColumnLayout {
                id: detailsInfo
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: App.Theme.couchSpaceM * page.couchScale
                Label {
                    objectName: "couchDetailsTitle"
                    Layout.fillWidth: true
                    text: String(page.game.name || qsTr("Game details"))
                    color: App.Theme.text
                    font.pixelSize: 50 * page.couchScale
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                }
                // Identity: launcher, real readiness, runtime only when recorded.
                Row {
                    objectName: "couchDetailsIdentity"
                    spacing: App.Theme.couchSpaceL * page.couchScale
                    Row {
                        visible: String(page.game.launcher || "").length > 0
                        spacing: App.Theme.couchSpaceS * page.couchScale
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            size: App.Theme.couchIconSmall; couchScale: page.couchScale
                            // Launcher logo (untouched on both themes) or the neutral glyph.
                            readonly property url logo: App.UiIcons.launcherLogo(page.game.launcher)
                            objectName: "couchDetailsLauncherIcon"
                            source: String(logo).length ? logo : App.UiIcons.couchGlyphSource
                            lightSource: String(logo).length ? logo : App.UiIcons.couchGlyphSourceOnLight
                        }
                        Label {
                            objectName: "couchDetailsLauncher"
                            anchors.verticalCenter: parent.verticalCenter
                            text: App.I18n.launcherName(page.game.launcher || "")
                            color: App.Theme.text
                            font.pixelSize: 19 * page.couchScale; font.weight: Font.DemiBold
                        }
                    }
                    Row {
                        id: detailsReadiness
                        objectName: "couchDetailsReadiness"
                        readonly property string tone: page.boolValue(["launchAllowed"], false) ? "success"
                                                       : (page.game.libraryAvailable === false
                                                          || ["Drive disconnected", "Missing files"].indexOf(String(page.game.status || "")) >= 0)
                                                         ? "danger" : "warning"
                        readonly property string text: tone === "success" ? qsTr("Ready to launch")
                                                       : tone === "danger" ? App.I18n.status(String(page.game.status || page.game.availabilityStatus || ""))
                                                       : qsTr("Launch unavailable")
                        spacing: App.Theme.couchSpaceS * page.couchScale
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            size: App.Theme.couchIconSmall; couchScale: page.couchScale
                            source: detailsReadiness.tone === "success" ? App.UiIcons.couchGlyphStatusReady
                                    : detailsReadiness.tone === "danger" ? App.UiIcons.couchGlyphStatusError : App.UiIcons.couchGlyphStatusWarning
                            lightSource: detailsReadiness.tone === "success" ? App.UiIcons.couchGlyphStatusReadyOnLight
                                         : detailsReadiness.tone === "danger" ? App.UiIcons.couchGlyphStatusErrorOnLight : App.UiIcons.couchGlyphStatusWarningOnLight
                        }
                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            text: detailsReadiness.text
                            color: App.Theme.couchToneText(detailsReadiness.tone)
                            font.pixelSize: 19 * page.couchScale; font.weight: Font.Bold
                        }
                    }
                    Label {
                        objectName: "couchDetailsRuntime"
                        readonly property string runner: String(page.game.runner || "").trim()
                        visible: runner.length > 0
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Runtime: %1").arg(runner.split("/").pop())
                        color: App.Theme.textSecondary
                        font.pixelSize: 18 * page.couchScale
                    }
                }
                Item { Layout.preferredHeight: 2 * page.couchScale }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: App.Theme.couchSpaceM * page.couchScale
                    Repeater {
                        id: headerRowTop
                        model: page.headerCards.slice(0, 3)
                        delegate: CouchFeatureCard {
                            required property var modelData
                            required property int index
                            objectName: "couchDetailsCard-" + modelData.id
                            Layout.fillWidth: true
                            Layout.preferredWidth: modelData.primary ? 1.45 : 1
                            Layout.preferredHeight: 108 * page.couchScale
                            couchScale: page.couchScale
                            primary: modelData.primary
                            iconSource: modelData.icon; iconLightSource: modelData.iconOnLight
                            text: modelData.title
                            stateText: modelData.info.state
                            stateTone: modelData.info.tone
                            subtitle: modelData.info.detail
                            badgeText: modelData.info.preview ? qsTr("Preview") : ""
                            enabled: modelData.enabled
                            focus: page.headerFocus && page.headerIndex === index
                            onClicked: { page.focusArea = 0; page.headerIndex = index; page.playSemanticSound(page.activateHeader()) }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: App.Theme.couchSpaceM * page.couchScale
                    Repeater {
                        id: headerRowBottom
                        model: page.headerCards.slice(3, 6)
                        delegate: CouchFeatureCard {
                            required property var modelData
                            required property int index
                            objectName: "couchDetailsCard-" + modelData.id
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.preferredHeight: 108 * page.couchScale
                            couchScale: page.couchScale
                            iconSource: modelData.icon; iconLightSource: modelData.iconOnLight
                            text: modelData.title
                            stateText: modelData.info.state
                            stateTone: modelData.info.tone
                            subtitle: modelData.info.detail
                            badgeText: modelData.info.preview ? qsTr("Preview") : ""
                            enabled: modelData.enabled
                            focus: page.headerFocus && page.headerIndex === index + 3
                            onClicked: { page.focusArea = 0; page.headerIndex = index + 3; page.playSemanticSound(page.activateHeader()) }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 68 * page.couchScale
            radius: App.Theme.couchCardRadius * page.couchScale
            color: Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b, App.Theme.dark ? 0.82 : 0.9)
            border.width: 1
            border.color: App.Theme.border
            ListView {
                id: tabBar
                objectName: "couchDetailsTabs"
                anchors.fill: parent
                anchors.margins: 6 * page.couchScale
                orientation: ListView.Horizontal
                spacing: 6 * page.couchScale
                interactive: false
                currentIndex: page.selectedTab
                model: page.tabs
                delegate: CouchButton {
                    id: tabButton
                    required property var modelData
                    required property int index
                    readonly property bool active: page.selectedTab === index
                    couchScale: page.couchScale
                    width: (tabBar.width - (page.tabs.length - 1) * tabBar.spacing) / page.tabs.length
                    height: tabBar.height
                    text: modelData.title
                    iconSource: modelData.icon || ""
                    iconLightSource: modelData.iconOnLight || ""
                    iconSize: App.Theme.couchIconSmall
                    font.pixelSize: 19 * page.couchScale
                    font.weight: active ? Font.Bold : Font.DemiBold
                    focus: page.tabFocus && active
                    onClicked: { page.selectedTab = index; page.focusArea = 2; page.ensureAction() }
                    background: Rectangle {
                        radius: (App.Theme.couchCardRadius - 4) * page.couchScale
                        color: tabButton.active ? App.Theme.accentSoft
                               : tabButton.hovered ? App.Theme.surfaceHover : "transparent"
                        border.width: tabButton.active ? 1 : 0
                        border.color: App.Theme.accent
                        Rectangle {
                            visible: tabButton.active
                            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                            anchors.leftMargin: 18 * page.couchScale; anchors.rightMargin: 18 * page.couchScale
                            height: 3 * page.couchScale
                            radius: height / 2
                            color: App.Theme.accent
                        }
                        CouchFocusFrame {
                            active: page.tabFocus && tabButton.active
                            radius: parent.radius
                            couchScale: page.couchScale
                            glow: false
                        }
                    }
                }
            }
        }
        Rectangle {
            objectName: "couchDetailsContent"
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: App.Theme.couchPanelRadius * page.couchScale
            color: App.Theme.dark ? "#E516202C" : "#ECFFFFFF"
            border.width: 1
            border.color: App.Theme.border
            CouchFocusFrame {
                active: page.contentFocus
                radius: parent.radius
                couchScale: page.couchScale
                glow: false
            }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20 * page.couchScale
                spacing: App.Theme.couchSpaceM * page.couchScale
                // Balanced action grid: columns follow the readable minimum tile
                // width and the item count (4 -> 2x2, 9 -> 3x3); at most two rows
                // are visible, the focused tile is scrolled into view.
                Flickable {
                    id: detailsActionsViewport
                    objectName: "couchDetailsActionsViewport"
                    // Equals the panel's inner margin: the viewport reaches the
                    // panel edge so the focused tile's ring and glow are not clipped.
                    readonly property real pad: 20 * page.couchScale
                    // CouchTile scales its focused card by 4 %: a 24 px gap keeps
                    // it off its neighbours, and gap >= pad keeps a partial third
                    // row from peeking into the two-row viewport.
                    readonly property real gap: 24 * page.couchScale
                    readonly property real tileHeight: 88 * page.couchScale
                    readonly property int count: page.actionModel.length
                    readonly property int maxColumns: Math.max(1, Math.floor((width - 2 * pad + gap)
                                                                             / (320 * page.couchScale + gap)))
                    readonly property int columns: count <= 3 ? Math.max(1, Math.min(count, maxColumns))
                                                   : Math.max(1, Math.min(maxColumns, Math.ceil(Math.sqrt(count))))
                    readonly property int rows: Math.max(1, Math.ceil(count / columns))
                    readonly property int visibleRows: Math.min(rows, 2)
                    readonly property real tileWidth: Math.floor((width - 2 * pad - (columns - 1) * gap) / columns)
                    Layout.fillWidth: true
                    Layout.leftMargin: -pad
                    Layout.rightMargin: -pad
                    Layout.topMargin: -pad
                    Layout.preferredHeight: visibleRows * tileHeight + (visibleRows - 1) * gap + 2 * pad
                    contentWidth: width
                    contentHeight: rows * tileHeight + (rows - 1) * gap + 2 * pad
                    clip: true
                    interactive: contentHeight > height
                    boundsBehavior: Flickable.StopAtBounds
                    Behavior on contentY { NumberAnimation { duration: App.Theme.couchMotionDuration(160); easing.type: Easing.OutCubic } }

                    function ensureVisible() {
                        if (page.selectedAction < 0 || columns <= 0)
                            return
                        var row = Math.floor(page.selectedAction / columns)
                        var top = row * (tileHeight + gap)
                        if (top < contentY)
                            contentY = top
                        else if (top + tileHeight + 2 * pad > contentY + height)
                            contentY = Math.min(contentHeight - height, top + tileHeight + 2 * pad - height)
                    }
                    Connections {
                        target: page
                        function onSelectedActionChanged() { detailsActionsViewport.ensureVisible() }
                        function onSelectedTabChanged() { detailsActionsViewport.contentY = 0 }
                    }

                GridLayout {
                    id: detailsActions
                    objectName: "couchDetailsActions"
                    x: detailsActionsViewport.pad
                    y: detailsActionsViewport.pad
                    width: detailsActionsViewport.width - 2 * detailsActionsViewport.pad
                    columns: detailsActionsViewport.columns
                    rowSpacing: detailsActionsViewport.gap
                    columnSpacing: detailsActionsViewport.gap
                    Repeater {
                        id: detailsActionRepeater
                        model: page.actionModel
                        delegate: CouchTile {
                            objectName: "couchDetailsActionTile"
                            required property var modelData
                            required property int index
                            Layout.preferredWidth: detailsActionsViewport.tileWidth
                            Layout.maximumWidth: detailsActionsViewport.tileWidth
                            Layout.preferredHeight: detailsActionsViewport.tileHeight
                            compact: true
                            couchScale: page.couchScale
                            iconSource: modelData.icon || ""
                            iconLightSource: modelData.iconOnLight || ""
                            // Short name + current value; the longer description
                            // is shown once, in the help bar below.
                            text: String(modelData.label || modelData.title)
                            subtitle: modelData.value !== undefined ? String(modelData.value)
                                                                    : String(modelData.subtitle || "")
                            primary: modelData.id === "launch"
                            showChevron: true
                            enabled: modelData.enabled
                            focus: page.actionFocus && page.selectedAction === index
                            onClicked: {
                                page.focusArea = 2
                                page.selectedAction = index
                                page.playSemanticSound(page.activateAction())
                            }
                        }
                    }
                }
                }
                Label {
                    objectName: "couchDetailsActionHelp"
                    readonly property var action: page.actionModel[page.selectedAction] || ({})
                    Layout.fillWidth: true
                    visible: String(action.description || "").length > 0
                    text: String(action.description || "")
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.couchHelperSize * page.couchScale
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
                Flickable {
                    id: detailsContentFlick
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    contentWidth: width
                    contentHeight: detailsContentLoader.item
                                   ? Math.max(height, page.loadedImplicitHeight(detailsContentLoader.item))
                                   : height
                    ScrollBar.vertical: ScrollBar {
                        visible: detailsContentFlick.contentHeight
                                 > detailsContentFlick.height
                    }
                    Loader {
                        id: detailsContentLoader
                        width: detailsContentFlick.width
                        height: Math.max(detailsContentFlick.height,
                                         page.loadedImplicitHeight(item))
                        sourceComponent: page.selectedTab === 0 ? overviewContent
                                         : page.selectedTab === 1 ? storageContent
                                         : page.selectedTab === 2 ? optimizationContent
                                         : page.selectedTab === 3 ? optiScalerContent
                                         : page.selectedTab === 4 ? narratorContent
                                         : unavailableContent
                    }
                }
            }
        }
    }

    Component {
        id: overviewContent
        ColumnLayout {
            spacing: 12 * page.couchScale
            Label { text: qsTr("Overview"); color: App.Theme.text; font.pixelSize: 27 * page.couchScale; font.weight: Font.Bold }
            GridLayout {
                Layout.fillWidth: true
                columns: 4
                columnSpacing: 12 * page.couchScale
                Repeater {
                    model: [
                        { "label": qsTr("Status"), "value": String(page.value(["availabilityStatus", "status"], qsTr("Unknown"))) },
                        { "label": qsTr("Filesystem"), "value": String(page.value(["filesystem"], qsTr("Unknown"))) },
                        { "label": qsTr("Scanner size"), "value": String(page.value(["logicalSize"], page.formatBytes(page.value(["scannerLogicalBytes"], -1)))) },
                        { "label": qsTr("Compression"), "value": page.classificationLabel() }
                    ]
                    delegate: Rectangle {
                        id: overviewMetric
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 78 * page.couchScale
                        radius: App.Theme.couchCardRadius * page.couchScale
                        color: App.Theme.surfaceRaised
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 11 * page.couchScale
                            spacing: 2
                            Label { Layout.fillWidth: true; text: overviewMetric.modelData.label; color: App.Theme.textMuted; font.pixelSize: 14 * page.couchScale; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: overviewMetric.modelData.value; color: App.Theme.text; font.pixelSize: 19 * page.couchScale; font.weight: Font.Bold; elide: Text.ElideRight }
                        }
                    }
                }
            }
            Label { Layout.fillWidth: true; text: qsTr("Library: %1").arg(String(page.game.libraryPath || page.game.library || qsTr("Unavailable"))); color: App.Theme.textSecondary; font.pixelSize: 18 * page.couchScale; elide: Text.ElideMiddle }
            Label { Layout.fillWidth: true; text: String(page.game.installPath || qsTr("Path unavailable")); color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale; elide: Text.ElideMiddle }
            Label { Layout.fillWidth: true; text: qsTr("Tasks and updates use the same data as Desktop Mode."); color: App.Theme.textSecondary; font.pixelSize: 18 * page.couchScale; wrapMode: Text.WordWrap }
            Item { Layout.fillHeight: true }
        }
    }
    Component {
        id: storageContent
        GridLayout {
            columns: 3
            rowSpacing: 12 * page.couchScale
            columnSpacing: 12 * page.couchScale
            Repeater {
                model: [
                    { "label": qsTr("Logical size"), "value": String(page.value(["logicalSize"], page.formatBytes(page.value(["scannerLogicalBytes"], -1)))) },
                    { "label": qsTr("Current physical usage"), "value": String(page.value(["physicalSize"], qsTr("Measurement unavailable"))) },
                    { "label": qsTr("Current saving"), "value": String(page.value(["savedSpace"], qsTr("Measurement unavailable"))) },
                    { "label": qsTr("Compression effect"), "value": page.value(["compressionEffectPercent"], null) === null ? qsTr("Unavailable") : Number(page.value(["compressionEffectPercent"], 0)).toFixed(2) + "%" },
                    { "label": qsTr("Classification"), "value": page.classificationLabel() },
                    { "label": qsTr("Additional potential"), "value": page.selectedProjection().available === true ? page.formatBytes(page.selectedProjection().estimatedAdditionalSavingBytes) : qsTr("Unavailable") }
                ]
                delegate: Rectangle {
                    id: storageMetric
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 86 * page.couchScale
                    radius: 14 * page.couchScale
                    color: App.Theme.surfaceRaised
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 13 * page.couchScale
                        spacing: 3
                        Label { Layout.fillWidth: true; text: storageMetric.modelData.label; color: App.Theme.textSecondary; font.pixelSize: 15 * page.couchScale; elide: Text.ElideRight }
                        Label { Layout.fillWidth: true; text: storageMetric.modelData.value; color: App.Theme.text; font.pixelSize: 24 * page.couchScale; font.weight: Font.Bold; elide: Text.ElideRight }
                    }
                }
            }
            Label { Layout.columnSpan: 3; Layout.fillWidth: true; text: String(page.value(["sharedExtentWarning", "compressionWarning"], qsTr("Shared extents and snapshot risk are checked before a write operation."))); color: App.Theme.warning; font.pixelSize: 16 * page.couchScale; wrapMode: Text.WordWrap }
        }
    }
    Component {
        id: optimizationContent
        ColumnLayout {
            spacing: 12 * page.couchScale
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: qsTr("Launch configuration"); color: App.Theme.text; font.pixelSize: 27 * page.couchScale; font.weight: Font.Bold }
            }
            Label { Layout.fillWidth: true; text: page.activationText(String(page.launchActivation.state || "")); color: App.Theme.textSecondary; font.pixelSize: 18 * page.couchScale; wrapMode: Text.WordWrap }
            GridLayout {
                Layout.fillWidth: true; columns: 3; columnSpacing: 18 * page.couchScale; rowSpacing: 10 * page.couchScale
                Label { text: "GameMode"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: "Gamescope"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: "MangoHud"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: page.gameModeEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.gamescopeEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.mangoHudEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
            }
            Label { Layout.fillWidth: true; text: String(page.optimizationData.launchPlanText || "%command%"); color: App.Theme.textSecondary; font.family: "monospace"; font.pixelSize: 15 * page.couchScale; elide: Text.ElideMiddle }
            Item { Layout.fillHeight: true }
        }
    }
    Component {
        id: optiScalerContent
        ColumnLayout {
            spacing: 12 * page.couchScale
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: qsTr("OptiScaler upscaling"); color: App.Theme.text; font.pixelSize: 27 * page.couchScale; font.weight: Font.Bold }
                Rectangle {
                    implicitWidth: optiScalerStateLabel.implicitWidth + 28 * page.couchScale
                    implicitHeight: 38 * page.couchScale
                    radius: height / 2
                    color: page.optiScalerData.installed === true ? App.Theme.successSoft : App.Theme.surfaceRaised
                    border.width: 1
                    border.color: page.optiScalerData.installed === true ? App.Theme.success : App.Theme.border
                    Label {
                        id: optiScalerStateLabel
                        anchors.centerIn: parent
                        text: page.optiScalerData.installed === true ? qsTr("Installed") : qsTr("Not installed")
                        color: page.optiScalerData.installed === true ? App.Theme.success : App.Theme.textSecondary
                        font.pixelSize: 15 * page.couchScale
                        font.weight: Font.Bold
                    }
                }
            }
            Label { Layout.fillWidth: true; text: qsTr("Install, update and configure OptiScaler for this game with the controller. Use the action cards below."); color: App.Theme.textSecondary; font.pixelSize: 18 * page.couchScale; wrapMode: Text.WordWrap }
            GridLayout {
                Layout.fillWidth: true; columns: 2; columnSpacing: 18 * page.couchScale; rowSpacing: 10 * page.couchScale
                Label { text: qsTr("Upscaling mode"); color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: qsTr("Executable"); color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: page.optiScalerModeLabel(); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.optiScalerExecutable().length > 0 ? page.optiScalerExecutable() : qsTr("Not detected"); color: App.Theme.text; font.pixelSize: 18 * page.couchScale; elide: Text.ElideMiddle }
            }
            Item { Layout.fillHeight: true }
        }
    }
    Component {
        id: narratorContent
        ColumnLayout {
            spacing: 14 * page.couchScale
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Per-game Narrator")
                    color: App.Theme.text
                    font.pixelSize: 27 * page.couchScale
                    font.weight: Font.Bold
                }
                Rectangle {
                    implicitWidth: narratorStateLabel.implicitWidth + 28 * page.couchScale
                    implicitHeight: 38 * page.couchScale
                    radius: height / 2
                    color: page.narratorSessionActive() ? App.Theme.successSoft : App.Theme.surfaceRaised
                    border.width: 1
                    border.color: page.narratorSessionActive() ? App.Theme.success : App.Theme.border
                    Label {
                        id: narratorStateLabel
                        anchors.centerIn: parent
                        text: page.narratorStatusLabel()
                        color: page.narratorSessionActive() ? App.Theme.success : App.Theme.textSecondary
                        font.pixelSize: 15 * page.couchScale
                        font.weight: Font.Bold
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("These settings are saved only for %1. Use the action cards above to configure and start narration with the controller.").arg(String(page.game.name || qsTr("this game")))
                color: App.Theme.textSecondary
                font.pixelSize: 17 * page.couchScale
                wrapMode: Text.WordWrap
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 4
                columnSpacing: 10 * page.couchScale
                Repeater {
                    model: [
                        { "label": qsTr("Capture"), "value": page.narratorCaptureStateLabel(String(page.narratorSession.captureState || "stopped")) },
                        { "label": qsTr("OCR"), "value": page.narratorOcrStateLabel(String(page.narratorSession.ocrStatus || "component_missing")) },
                        { "label": qsTr("Translation"), "value": page.narratorInferenceStateLabel(String(page.narratorSession.translationStatus || "component_missing"), qsTr("Translating"), qsTr("Translation error")) },
                        { "label": qsTr("Speech"), "value": page.narratorInferenceStateLabel(String(page.narratorSession.ttsStatus || "component_missing"), qsTr("Generating speech"), qsTr("Speech error")) }
                    ]
                    delegate: Rectangle {
                        id: narratorMetric
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 74 * page.couchScale
                        radius: 14 * page.couchScale
                        color: App.Theme.surfaceRaised
                        border.width: 1
                        border.color: App.Theme.border
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 11 * page.couchScale
                            spacing: 2 * page.couchScale
                            Label { Layout.fillWidth: true; text: narratorMetric.modelData.label; color: App.Theme.textMuted; font.pixelSize: 13 * page.couchScale; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: narratorMetric.modelData.value; color: App.Theme.text; font.pixelSize: 17 * page.couchScale; font.weight: Font.Bold; elide: Text.ElideRight }
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: narratorSummary.implicitHeight + 24 * page.couchScale
                radius: 14 * page.couchScale
                color: App.Theme.surfaceRaised
                border.width: 1
                border.color: (page.narratorSession.missingRequirements || []).length
                              ? App.Theme.warning : App.Theme.border
                ColumnLayout {
                    id: narratorSummary
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 12 * page.couchScale
                    spacing: 5 * page.couchScale
                    Label {
                        Layout.fillWidth: true
                        text: (page.narratorSession.missingRequirements || []).length
                              ? qsTr("%1 required component(s) are missing").arg((page.narratorSession.missingRequirements || []).length)
                              : qsTr("Local Narrator components are ready")
                        color: (page.narratorSession.missingRequirements || []).length
                               ? App.Theme.warning : App.Theme.success
                        font.pixelSize: 16 * page.couchScale
                        font.weight: Font.Bold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Language: %1 · Voice: %2 · Volume: %3% · Speech: %4×")
                              .arg(page.narratorLanguageLabel())
                              .arg(page.narratorVoiceLabel())
                              .arg(Math.round(Number(page.narratorData.volume || 0) * 100))
                              .arg(Number(page.narratorData.speechRate || 1).toFixed(1))
                        color: App.Theme.textSecondary
                        font.pixelSize: 15 * page.couchScale
                        elide: Text.ElideRight
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                visible: String(page.narratorSession.lastDetectedText || "").length > 0
                text: qsTr("Last subtitle: %1").arg(String(page.narratorSession.lastDetectedText || ""))
                color: App.Theme.textSecondary
                font.pixelSize: 16 * page.couchScale
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                visible: String(page.narratorSession.lastTranslation || "").length > 0
                text: qsTr("Last translation: %1").arg(String(page.narratorSession.lastTranslation || ""))
                color: App.Theme.textSecondary
                font.pixelSize: 16 * page.couchScale
                elide: Text.ElideRight
            }
            Item { Layout.fillHeight: true }
        }
    }
    Component {
        id: unavailableContent
        ColumnLayout {
            Label { text: page.tabs[page.selectedTab].title; color: App.Theme.text; font.pixelSize: 28 * page.couchScale; font.weight: Font.Bold }
            Label { Layout.fillWidth: true; text: qsTr("This section shows the current shared backend state. No implementation is simulated when the backend is unavailable."); color: App.Theme.textSecondary; font.pixelSize: 19 * page.couchScale; wrapMode: Text.WordWrap }
            Item { Layout.fillHeight: true }
        }
    }

    CouchOverlayFrame {
        anchors.fill: parent
        z: 215
        visible: page.optimizationOverlayOpen
        couchScale: page.couchScale
        maximumWidth: 940 * page.couchScale
        preferredHeight: 850 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 10 * page.couchScale
            Label { Layout.fillWidth: true; text: qsTr("Optimization"); color: App.Theme.text; font.pixelSize: 30 * page.couchScale; font.weight: Font.Bold }
            Label { Layout.fillWidth: true; text: page.optimizationData.recommendation ? App.I18n.message(String(page.optimizationData.recommendation.status || "")) : qsTr("Preliminary recommendation - game measurement required"); color: App.Theme.warning; font.pixelSize: 16 * page.couchScale; wrapMode: Text.WordWrap }
            ListView {
                id: optimizationOptions
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 7 * page.couchScale
                model: [
                    { "symbol": "◐", "title": qsTr("Profile"), "value": page.optimizationPresetLabel(), "enabled": true },
                    { "symbol": "◆", "title": qsTr("Game category"), "value": page.optimizationCategoryLabel(), "enabled": true },
                    { "symbol": "↯", "title": qsTr("Target FPS"), "value": String(page.fpsLimit), "enabled": true },
                    { "symbol": "□", "title": qsTr("Monitor"), "value": page.optimizationDisplayLabel(), "enabled": page.optimizationDisplays.length > 0 },
                    { "symbol": "✓", "title": qsTr("Save profile"), "value": "", "enabled": true },
                    { "symbol": "‹", "title": qsTr("Cancel"), "value": "", "enabled": true }
                ]
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 62 * page.couchScale
                    couchScale: page.couchScale
                    text: modelData.title
                    subtitle: modelData.value
                    enabled: modelData.enabled
                    focus: page.optimizationOverlayOpen && page.optimizationRow === index
                    onClicked: { page.optimizationRow = index; page.handleOptimizationAction("Confirm") }
                }
            }
            Label { Layout.fillWidth: true; text: page.optimizationReasons.length ? App.I18n.message(String(page.optimizationReasons[0])) : qsTr("No saved session measurements"); color: App.Theme.textSecondary; font.pixelSize: 15 * page.couchScale; wrapMode: Text.WordWrap }
            Label { Layout.fillWidth: true; text: qsTr("Proton Tweaks: %1 active - edit advanced options in Desktop Mode").arg((page.protonTweaksData.enabledTweaks || []).length); color: App.Theme.textSecondary; font.pixelSize: 15 * page.couchScale; wrapMode: Text.WordWrap }
            Label { Layout.fillWidth: true; text: qsTr("Advanced Gamescope options remain available in Desktop Mode."); color: App.Theme.textMuted; font.pixelSize: 14 * page.couchScale; wrapMode: Text.WordWrap }
        }
    }

    CouchOverlayFrame {
        anchors.fill: parent
        z: 210
        visible: page.mangoHudOverlayOpen
        couchScale: page.couchScale
        maximumWidth: 1000 * page.couchScale
        preferredHeight: 880 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: App.Theme.couchSpaceM * page.couchScale
            RowLayout {
                Layout.fillWidth: true
                spacing: App.Theme.couchSpaceM * page.couchScale
                CouchIcon {
                    size: App.Theme.couchIconMedium; couchScale: page.couchScale
                    source: App.UiIcons.couchGlyphMangohud; lightSource: App.UiIcons.couchGlyphMangohudOnLight
                }
                Label { Layout.fillWidth: true; text: "MangoHud"; color: App.Theme.text; font.pixelSize: 30 * page.couchScale; font.weight: Font.Bold }
                CouchStatePill {
                    objectName: "couchMangoHudStatus"
                    couchScale: page.couchScale
                    tone: page.mangoHudUsable ? (page.mangoHudDraftEnabled ? "success" : "neutral") : "unavailable"
                    text: page.mangoHudUsable
                          ? (String(page.mangoHudProfile.version || "").length
                             ? qsTr("Available · %1").arg(String(page.mangoHudProfile.version)) : qsTr("Available"))
                          : (page.launcherIntegrationSupported ? qsTr("Unavailable") : qsTr("Unsupported for this launch method"))
                    maximumTextWidth: 420 * page.couchScale
                }
            }
            Label {
                objectName: "couchMangoHudActivation"
                Layout.fillWidth: true
                text: page.mangoHudActivationText()
                color: page.mangoHudUsable && String(page.mangoHudProfile.strategyStatus || "") !== "executable_missing"
                       && String(page.mangoHudProfile.strategyStatus || "") !== "application_config_conflict"
                       ? App.Theme.textSecondary : App.Theme.warning
                font.pixelSize: 16 * page.couchScale
                wrapMode: Text.WordWrap
            }
            ListView {
                id: mangoHudOptions
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8 * page.couchScale
                model: page.mangoHudRows
                currentIndex: page.mangoHudRow
                highlightMoveDuration: App.Theme.couchMotionDuration(120)
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 66 * page.couchScale
                    compact: true
                    couchScale: page.couchScale
                    iconSource: modelData.icon
                    iconLightSource: modelData.iconOnLight
                    text: modelData.title
                    subtitle: [modelData.value, modelData.detail].filter(function(v) { return String(v).length }).join(" · ")
                    enabled: modelData.enabled
                    focus: page.mangoHudOverlayOpen && page.mangoHudRow === index
                    onClicked: { page.mangoHudRow = index; page.handleMangoHudAction("Confirm") }
                }
            }
            Label {
                objectName: "couchMangoHudLimiter"
                Layout.fillWidth: true
                text: page.mangoHudLimiterText()
                color: App.Theme.text
                font.pixelSize: 18 * page.couchScale
                font.weight: Font.Bold
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("More appearance, metrics, advanced and logging settings are available in Desktop Mode.")
                color: App.Theme.textMuted
                font.pixelSize: 14 * page.couchScale
                wrapMode: Text.WordWrap
            }
        }
    }

    // UI-side guard: never show "Checking…" forever if no result arrives.
    Timer {
        id: optiScalerStatusTimeout
        interval: 20000
        repeat: false
        onTriggered: {
            if (!page.optiScalerBusy)
                return
            var data = Object.assign({}, page.optiScalerData)
            data.loading = false
            data.refreshing = false
            if (data.success !== true || !data.installationState)
                data = { "success": false, "error": "Checking OptiScaler took too long" }
            else
                data.refreshError = "Checking OptiScaler took too long"
            page.optiScalerData = data
        }
    }

    CouchOverlayFrame {
        anchors.fill: parent
        z: 200
        visible: page.confirmationOpen
        couchScale: page.couchScale
        maximumWidth: 800 * page.couchScale
        preferredHeight: 360 * page.couchScale

        ColumnLayout {
                anchors.fill: parent; spacing: 14 * page.couchScale
                Label { Layout.fillWidth: true; text: page.confirmationTitle(); color: App.Theme.text; font.pixelSize: 29 * page.couchScale; font.weight: Font.Bold }
                Label { Layout.fillWidth: true; text: page.confirmationDescription(); color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale; wrapMode: Text.WordWrap }
                Label { Layout.fillWidth: true; visible: page.confirmationKind === "compression"; text: page.pendingPlan && page.pendingPlan.warnings && page.pendingPlan.warnings.length ? String(page.pendingPlan.warnings[0]) : qsTr("The estimate does not guarantee the same change in free disk space."); color: App.Theme.warning; font.pixelSize: 15 * page.couchScale; wrapMode: Text.WordWrap }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.fillWidth: true
                    CouchButton { id: detailsCancelButton; Layout.fillWidth: true; couchScale: page.couchScale; text: qsTr("Cancel"); focus: page.confirmationOpen && page.confirmationChoice === 0; onClicked: page.closeConfirmation() }
                    CouchButton { id: detailsConfirmButton; Layout.fillWidth: true; couchScale: page.couchScale; text: page.confirmationButtonLabel(); focus: page.confirmationOpen && page.confirmationChoice === 1; onClicked: { page.confirmationChoice = 1; page.handleAction("Confirm") } }
                }
        }
    }

    // ---- GameMode menu -------------------------------------------------------
    CouchOverlayFrame {
        anchors.fill: parent
        z: 216
        visible: page.gameModeOverlayOpen
        couchScale: page.couchScale
        maximumWidth: 900 * page.couchScale
        preferredHeight: 700 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: App.Theme.couchSpaceM * page.couchScale
            RowLayout {
                Layout.fillWidth: true
                spacing: App.Theme.couchSpaceM * page.couchScale
                CouchIcon {
                    size: App.Theme.couchIconMedium; couchScale: page.couchScale
                    source: App.UiIcons.couchGlyphGamemode; lightSource: App.UiIcons.couchGlyphGamemodeOnLight
                }
                Label { Layout.fillWidth: true; text: "GameMode"; color: App.Theme.text; font.pixelSize: 30 * page.couchScale; font.weight: Font.Bold }
                CouchStatePill {
                    objectName: "couchGameModeStatus"
                    couchScale: page.couchScale
                    tone: page.gameModeStatus().tone
                    text: page.gameModeStatus().text
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4 * page.couchScale
                Repeater {
                    model: [
                        qsTr("GameOpti uses gamemoderun when this game starts."),
                        qsTr("GameMode applies the system gamemode.ini configuration."),
                        qsTr("GameOpti does not promise a specific FPS gain.")
                    ]
                    delegate: Label {
                        required property string modelData
                        Layout.fillWidth: true
                        text: modelData
                        color: App.Theme.textSecondary
                        font.pixelSize: 16 * page.couchScale
                        wrapMode: Text.WordWrap
                    }
                }
            }
            ListView {
                id: gameModeOptions
                Layout.fillWidth: true
                Layout.preferredHeight: page.gameModeRows.length * 78 * page.couchScale
                interactive: false
                spacing: 8 * page.couchScale
                model: page.gameModeRows
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 70 * page.couchScale
                    compact: true
                    couchScale: page.couchScale
                    iconSource: modelData.icon
                    iconLightSource: modelData.iconOnLight
                    text: modelData.title
                    subtitle: modelData.value
                    enabled: modelData.enabled
                    focus: page.gameModeOverlayOpen && page.gameModeRow === index
                    onClicked: { page.gameModeRow = index; page.handleGameModeAction("Confirm") }
                }
            }
            CouchStatePill {
                objectName: "couchGameModeApplication"
                Layout.alignment: Qt.AlignLeft
                couchScale: page.couchScale
                maximumTextWidth: 780 * page.couchScale
                tone: page.gameModeApplication().tone
                text: page.gameModeApplication().text
            }
            Item { Layout.fillHeight: true }
        }
    }

    // ---- Gamescope menu ------------------------------------------------------
    CouchOverlayFrame {
        anchors.fill: parent
        z: 217
        visible: page.gamescopeOverlayOpen
        couchScale: page.couchScale
        maximumWidth: 1040 * page.couchScale
        preferredHeight: 900 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: App.Theme.couchSpaceM * page.couchScale
            RowLayout {
                Layout.fillWidth: true
                spacing: App.Theme.couchSpaceM * page.couchScale
                CouchIcon {
                    size: App.Theme.couchIconMedium; couchScale: page.couchScale
                    source: App.UiIcons.couchGlyphGamescope; lightSource: App.UiIcons.couchGlyphGamescopeOnLight
                }
                Label { Layout.fillWidth: true; text: "Gamescope"; color: App.Theme.text; font.pixelSize: 30 * page.couchScale; font.weight: Font.Bold }
                CouchStatePill {
                    objectName: "couchGamescopeStatus"
                    couchScale: page.couchScale
                    tone: page.gamescopeUsable ? "success" : "unavailable"
                    text: page.gamescopeUsable
                          ? qsTr("Available · %1").arg(String(page.gamescopeTool.version || ""))
                          : page.gamescopeUnavailableReason()
                    maximumTextWidth: 460 * page.couchScale
                }
            }
            Label {
                Layout.fillWidth: true
                text: page.activationText(String(page.launchActivation.state || ""))
                color: String(page.launchActivation.state || "") === "active" ? App.Theme.textSecondary : App.Theme.warning
                font.pixelSize: 15 * page.couchScale
                wrapMode: Text.WordWrap
            }
            ListView {
                id: gamescopeOptions
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8 * page.couchScale
                model: page.gamescopeRows
                currentIndex: page.gamescopeRow
                highlightMoveDuration: App.Theme.couchMotionDuration(120)
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 66 * page.couchScale
                    compact: true
                    couchScale: page.couchScale
                    iconSource: modelData.icon
                    iconLightSource: modelData.iconOnLight
                    text: modelData.title
                    subtitle: [modelData.value, modelData.detail].filter(function(v) { return String(v).length }).join(" · ")
                    enabled: modelData.enabled
                    focus: page.gamescopeOverlayOpen && page.gamescopeRow === index
                    onClicked: { page.gamescopeRow = index; page.handleGamescopeAction("Confirm") }
                }
            }
            Label {
                objectName: "couchGamescopeSummary"
                Layout.fillWidth: true
                text: page.gamescopeSummary()
                color: App.Theme.text
                font.pixelSize: 18 * page.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Label {
                objectName: "couchGamescopeCommand"
                Layout.fillWidth: true
                text: page.setupPreview && page.setupPreview.success === false
                      ? App.I18n.message(String(page.setupPreview.error || ""))
                      : String((page.setupPreview && page.setupPreview.launchPlanText) || "")
                color: page.setupPreview && page.setupPreview.success === false ? App.Theme.warning : App.Theme.textMuted
                font.family: "monospace"
                font.pixelSize: 14 * page.couchScale
                elide: Text.ElideMiddle
            }
        }
    }
}
