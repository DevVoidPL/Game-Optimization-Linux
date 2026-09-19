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
    property bool tabFocus: false
    property bool contentFocus: false
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
        { "id": "overview", "title": qsTr("Overview") },
        { "id": "storage", "title": qsTr("Storage") },
        { "id": "optimization", "title": qsTr("Optimization") },
        { "id": "optiscaler", "title": qsTr("OptiScaler") },
        { "id": "narrator", "title": qsTr("Narrator") }
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
            var item = page.optimizationOverlayOpen
                    ? optimizationOptions.itemAtIndex(page.optimizationRow)
                    : page.mangoHudOverlayOpen
                    ? mangoHudOptions.itemAtIndex(page.mangoHudRow)
                    : page.confirmationOpen
                    ? (page.confirmationChoice === 0
                       ? detailsCancelButton : detailsConfirmButton)
                    : page.contentFocus ? detailsContentFlick
                    : page.tabFocus ? tabBar.itemAtIndex(page.selectedTab)
                                     : detailsActionRepeater.itemAt(page.selectedAction)
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
        return qsTr("Start narration for the running game")
    }
    function narratorActions() {
        if (!narratorData || narratorData.success !== true) return [{
            "id": "narrator-reload", "symbol": "↻", "title": qsTr("Load Narrator settings"),
            "subtitle": qsTr("Read the saved per-game configuration"), "enabled": true
        }]
        var active = narratorSessionActive()
        return [
            { "id": "narrator-enabled", "symbol": "N", "title": qsTr("Narrator: %1").arg(narratorData.enabled ? qsTr("On") : qsTr("Off")), "subtitle": qsTr("Saved only for this game"), "enabled": true },
            { "id": "narrator-language", "symbol": "文", "title": narratorLanguageLabel(), "subtitle": qsTr("Cycle subtitle language mode"), "enabled": true },
            { "id": "narrator-source", "symbol": "⌁", "title": qsTr("Source: %1").arg(narratorSourceLabel()), "subtitle": qsTr("Automatic detection or OCR capture"), "enabled": true },
            { "id": "narrator-capture", "symbol": "▣", "title": qsTr("Capture: %1").arg(narratorCaptureLabel()), "subtitle": qsTr("Choose a window or the full monitor"), "enabled": true },
            { "id": "narrator-voice", "symbol": "♪", "title": narratorVoiceLabel(), "subtitle": qsTr("Cycle installed speech voices"), "enabled": narratorVoices().length > 0 },
            { "id": "narrator-volume", "symbol": "◖", "title": qsTr("Volume: %1%").arg(Math.round(Number(narratorData.volume || 0) * 100)), "subtitle": qsTr("Press repeatedly to adjust"), "enabled": true },
            { "id": "narrator-rate", "symbol": "››", "title": qsTr("Speech rate: %1×").arg(Number(narratorData.speechRate || 1).toFixed(1)), "subtitle": qsTr("Press repeatedly to adjust"), "enabled": true },
            { "id": "narrator-region", "symbol": "⌗", "title": qsTr("Select subtitle region"), "subtitle": qsTr("Open the native capture selector"), "enabled": Boolean(controller && controller.selectNarratorSubtitleRegion) },
            { "id": active ? "narrator-stop" : "narrator-start", "symbol": active ? "■" : "▶", "title": active ? qsTr("Stop Narrator") : qsTr("Start Narrator"), "subtitle": narratorStartMessage(), "enabled": active || (narratorData.enabled === true && narratorSession.canStart === true) }
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
            { "id": "optimization-profile", "symbol": "◐", "title": qsTr("Profile: %1").arg(optimizationPresetLabel()), "enabled": true },
            { "id": "gamemode", "symbol": "⚡", "title": "GameMode: " + (gameModeEnabled ? qsTr("On") : qsTr("Off")), "enabled": true },
            { "id": "gamescope", "symbol": "▣", "title": "Gamescope: " + (gamescopeEnabled ? qsTr("On") : qsTr("Off")), "enabled": true },
            { "id": "mangohud-profile", "symbol": "◉", "title": qsTr("MangoHud"), "subtitle": mangoHudPresetLabel(), "enabled": true }
        ]
    }
    function optiScalerRefreshSubtitle() {
        if (optiScalerData.loading || optiScalerData.refreshing)
            return qsTr("Checking the official release…")
        var onlineError = String(optiScalerData.onlineError || "")
        if (onlineError.length > 0)
            return onlineError
        return qsTr("Prepare a verified Couch installation")
    }
    function optiScalerActions() {
        var actions = []
        var installed = optiScalerData.installed === true
        var needsPackage = !installed
                || String(optiScalerData.onlineState || "") === "update_available"
                || ["corrupt", "partial"].indexOf(String(optiScalerData.installationState || "")) >= 0
        if (needsPackage) {
            if (optiScalerData.archiveReady === true) {
                actions.push({ "id": "optiscaler-install", "symbol": "◇", "title": optiScalerOperationLabel(), "subtitle": qsTr("Official verified release · confirmation required"), "enabled": optiScalerExecutable().length > 0 })
            } else {
                actions.push({ "id": "optiscaler-refresh", "symbol": "↓", "title": qsTr("Download official OptiScaler release"), "subtitle": optiScalerRefreshSubtitle(), "enabled": !(optiScalerData.loading || optiScalerData.refreshing) })
            }
        }
        if (installed) {
            actions.push({ "id": "optiscaler-configure", "symbol": "◈", "title": qsTr("Upscaling: %1").arg(optiScalerModeLabel()), "subtitle": qsTr("Cycle and apply the FSR mode"), "enabled": (optiScalerData.supportedFsr4Modes || []).length > 0 })
            actions.push({ "id": "optiscaler-verify", "symbol": "✓", "title": qsTr("Verify OptiScaler"), "subtitle": qsTr("Check managed files without changing them"), "enabled": true })
            actions.push({ "id": "optiscaler-launch", "symbol": "▶", "title": qsTr("Launch with OptiScaler"), "subtitle": qsTr("Use the installed profile"), "enabled": true })
            if (String(optiScalerData.manifestId || "").length > 0)
                actions.push({ "id": "optiscaler-remove", "symbol": "×", "title": qsTr("Remove OptiScaler"), "subtitle": qsTr("Requires confirmation"), "enabled": true })
        }
        if (actions.length === 0)
            actions.push({ "id": "unavailable", "symbol": "i", "title": qsTr("Checking OptiScaler status…"), "enabled": false })
        return actions
    }
    function actionsForTab() {
        if (selectedTab === 0) return [
            { "id": "launch", "symbol": "▶", "title": launchPending ? qsTr("Launching…") : qsTr("Launch"), "subtitle": boolValue(["launchAllowed"], false) ? qsTr("Start the selected game") : String(value(["availabilityStatus", "status"], qsTr("Game is unavailable"))), "enabled": boolValue(["launchAllowed"], false) && !launchPending },
            { "id": "updates", "symbol": "↓", "title": qsTr("Updates"), "subtitle": qsTr("Review detected changes"), "enabled": true }
        ]
        if (selectedTab === 1) return [
            { "id": "analyze", "symbol": "⌕", "title": qsTr("Analyze"), "subtitle": boolValue(["analysisAllowed"], false) ? qsTr("Inspect the current game") : qsTr("Unavailable for this game"), "enabled": boolValue(["analysisAllowed"], false) },
            { "id": "verify", "symbol": "✓", "title": qsTr("Verify compression"), "subtitle": boolValue(["analysisAllowed"], false) ? qsTr("Read-only measurement") : qsTr("Unavailable for this game"), "enabled": boolValue(["analysisAllowed"], false) },
            { "id": "profile", "symbol": "◈", "title": qsTr("Profile: %1").arg(selectedProfile), "subtitle": boolValue(["analysisProfilesUnlocked"], false) ? qsTr("Choose a planned profile") : qsTr("Analyze the game first"), "enabled": boolValue(["analysisProfilesUnlocked"], false) },
            { "id": "compress", "symbol": "↓", "title": qsTr("Start compression"), "subtitle": boolValue(["analysisProfilesUnlocked"], false) && boolValue(["compressionAvailable"], false) ? qsTr("Review the verified plan") : qsTr("A verified Btrfs plan is required"), "enabled": boolValue(["analysisProfilesUnlocked"], false) && boolValue(["compressionAvailable"], false) }
        ]
        if ((selectedTab === 2 || selectedTab === 3) && !launcherIntegrationSupported) return [{
                "id": "unavailable", "symbol": "i",
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
        return [{ "id": "unavailable", "symbol": "i", "title": qsTr("Unavailable in this version"), "enabled": false }]
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
        contentFocus = false
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
    function legacyOptimizationProfileLabel() {
        if (optimizationProfile === "maximum_performance") return qsTr("Maximum Performance")
        if (optimizationProfile === "quiet") return qsTr("Quiet")
        if (optimizationProfile === "custom") return qsTr("Custom")
        return qsTr("Balanced")
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
        optiScalerData = result.success ? result : ({})
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
    function beginCouchOptiScalerOperation() {
        if (!controller || !controller.inspectOnlineOptiScaler
                || !controller.installOnlineOptiScaler)
            return false
        var executable = optiScalerExecutable()
        if (!executable.length || optiScalerData.archiveReady !== true)
            return false
        var operation = String(confirmationKind).replace("optiscaler_", "")
        var injectionDll = String(optiScalerData.injectionDll || "auto")
        var plan = controller.inspectOnlineOptiScaler(
                    String(game.id || ""), executable, injectionDll, true) || ({})
        if (plan.success !== true || (plan.blockers || []).length > 0)
            return false
        return Boolean(controller.installOnlineOptiScaler(
                    String(game.id || ""), executable, injectionDll, operation,
                    Boolean(plan.requiresConflictConfirmation), true))
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
        else if (optimizationRow === 3 && optimizationData.gamemode && optimizationData.gamemode.available) gameModeEnabled = !gameModeEnabled
        else if (optimizationRow === 4 && optimizationData.gamescope && optimizationData.gamescope.available) {
            optimizationGamescopeMode = cycleValue(optimizationGamescopeModes, optimizationGamescopeMode, delta)
            gamescopeEnabled = optimizationGamescopeMode !== "disabled"
        } else if (optimizationRow === 5 && optimizationDisplays.length) {
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
                action === "NavigateUp" ? -1 : 1, 7)
            if (optimizationRow !== previousRow)
                playSemanticSound("navigate")
        } else if (action === "NavigateLeft" || action === "NavigateRight") {
            playSemanticSound(adjustOptimization(action === "NavigateLeft" ? -1 : 1)
                              ? "adjust" : "error")
        } else if (action === "Confirm") {
            if (optimizationRow < 6)
                playSemanticSound(adjustOptimization(1) ? "adjust" : "error")
            else if (optimizationRow === 6)
                playSemanticSound(saveCouchOptimization() ? "confirm" : "error")
            else {
                closeOptimizationOverlay()
                playSemanticSound("back")
            }
        }
        restoreActiveFocus()
    }
    function mangoHudPositionLabel() {
        var labels = [qsTr("Top left"), qsTr("Top center"), qsTr("Top right"), qsTr("Middle left"), qsTr("Middle right"), qsTr("Bottom left"), qsTr("Bottom center"), qsTr("Bottom right")]
        var index = mangoHudPositions.indexOf(mangoHudPosition)
        return labels[index >= 0 ? index : 0]
    }
    function mangoHudSizeLabel() {
        return mangoHudFontSize <= 18 ? qsTr("Small") : mangoHudFontSize >= 32 ? qsTr("Large") : qsTr("Medium")
    }
    function loadMangoHudProfile() {
        if (!launcherIntegrationSupported) { mangoHudProfile = ({}); return }
        if (!controller || !controller.getMangoHudProfile || !game.id) return
        var result = controller.getMangoHudProfile(String(game.id)) || ({})
        mangoHudProfile = result
        mangoHudPreset = String(result.preset || "disabled")
        mangoHudPosition = String(result.position || "top-left")
        mangoHudFontSize = Number(result.fontSize || 24)
        mangoHudFpsLimit = Number(result.fpsLimit || 0)
        var selected = result.metrics ? Array.from(result.metrics) : []
        mangoHudTemperatures = selected.indexOf("cpu_temperature") >= 0 || selected.indexOf("gpu_temperature") >= 0
        mangoHudMemory = selected.indexOf("ram") >= 0 || selected.indexOf("vram") >= 0
        mangoHudEnabled = Boolean(result.activationEnabled)
    }
    function openMangoHudOverlay() {
        loadMangoHudProfile()
        mangoHudRow = 0
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
    function adjustMangoHud(delta) {
        var before = [mangoHudPreset, mangoHudPosition, mangoHudFontSize,
                      mangoHudFpsLimit, mangoHudTemperatures,
                      mangoHudMemory].join("|")
        if (mangoHudRow === 0) {
            mangoHudPreset = cycleValue(mangoHudPresets, mangoHudPreset, delta)
        } else if (mangoHudRow === 1) {
            mangoHudPosition = cycleValue(mangoHudPositions, mangoHudPosition, delta)
        } else if (mangoHudRow === 2) {
            mangoHudFontSize = cycleValue(mangoHudFontSizes, mangoHudFontSize, delta)
        } else if (mangoHudRow === 3) {
            mangoHudFpsLimit = cycleValue(mangoHudFpsLimits, mangoHudFpsLimit, delta)
        } else if (mangoHudRow === 4) {
            mangoHudTemperatures = !mangoHudTemperatures
            mangoHudPreset = "custom"
        } else if (mangoHudRow === 5) {
            mangoHudMemory = !mangoHudMemory
            mangoHudPreset = "custom"
        }
        var after = [mangoHudPreset, mangoHudPosition, mangoHudFontSize,
                     mangoHudFpsLimit, mangoHudTemperatures,
                     mangoHudMemory].join("|")
        return before !== after
    }
    function couchMangoHudMetrics() {
        if (mangoHudPreset !== "custom")
            return mangoHudProfile.metrics || []
        var selected = mangoHudProfile.metrics ? Array.from(mangoHudProfile.metrics) : ["fps", "frametime", "gpu_usage", "cpu_usage"]
        function set(metric, wanted) {
            var index = selected.indexOf(metric)
            if (wanted && index < 0) selected.push(metric)
            else if (!wanted && index >= 0) selected.splice(index, 1)
        }
        set("cpu_temperature", mangoHudTemperatures)
        set("gpu_temperature", mangoHudTemperatures)
        set("ram", mangoHudMemory)
        set("vram", mangoHudMemory)
        return selected
    }
    function saveCouchMangoHud() {
        if (!controller || !controller.saveMangoHudProfile) return false
        var source = mangoHudProfile || ({})
        var payload = {
            "enabled": mangoHudPreset !== "disabled",
            "preset": mangoHudPreset,
            "position": mangoHudPosition,
            "fontSize": mangoHudFontSize,
            "backgroundAlpha": Number(source.backgroundAlpha !== undefined ? source.backgroundAlpha : 0.5),
            "roundCorners": Number(source.roundCorners !== undefined ? source.roundCorners : 8),
            "compact": Boolean(source.compact),
            "horizontal": Boolean(source.horizontal),
            "tableColumns": Number(source.tableColumns || 3),
            "fpsLimit": mangoHudFpsLimit,
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
            mangoHudProfile = result
            mangoHudEnabled = Boolean(result.activationEnabled)
            closeMangoHudOverlay()
            return true
        }
        return false
    }
    function handleMangoHudAction(action) {
        if (action === "Back") {
            closeMangoHudOverlay()
            playSemanticSound("back")
        } else if (action === "NavigateUp" || action === "NavigateDown") {
            var previousRow = mangoHudRow
            mangoHudRow = moveOverlayRow(
                mangoHudOptions, mangoHudRow,
                action === "NavigateUp" ? -1 : 1, 7)
            if (mangoHudRow !== previousRow)
                playSemanticSound("navigate")
        } else if (action === "NavigateLeft" || action === "NavigateRight") {
            playSemanticSound(adjustMangoHud(action === "NavigateLeft" ? -1 : 1)
                              ? "adjust" : "error")
        } else if (action === "Confirm") {
            if (mangoHudRow < 6)
                playSemanticSound(adjustMangoHud(1) ? "adjust" : "error")
            else if (mangoHudRow === 6)
                playSemanticSound(saveCouchMangoHud() ? "confirm" : "error")
            else {
                closeMangoHudOverlay()
                playSemanticSound("back")
            }
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
        return qsTr("Review compression plan")
    }
    function confirmationDescription() {
        if (confirmationKind === "optiscaler_remove")
            return qsTr("Only files recorded as created by GameOpti will be removed. Replaced files remain available for restoration in Desktop Mode.")
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
        if (id === "optimization-profile" || id === "gamemode"
                || id === "gamescope" || id === "fps" || id === "resolution") {
            openOptimizationOverlay()
            return "open"
        }
        if (id === "mangohud-profile") {
            openMangoHudOverlay()
            return "open"
        }
        if (id === "optiscaler-refresh")
            return refreshCouchOptiScaler() ? "confirm" : "error"
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
            var previousContentFocus = contentFocus
            var previousPageY = detailsContentFlick.contentY
            contentFocus = true
            if (action === "PageUp")
                detailsContentFlick.contentY = Math.max(0, detailsContentFlick.contentY - detailsContentFlick.height * 0.72)
            else
                detailsContentFlick.contentY = Math.min(
                    Math.max(0, detailsContentFlick.contentHeight - detailsContentFlick.height),
                    detailsContentFlick.contentY + detailsContentFlick.height * 0.72)
            if (contentFocus !== previousContentFocus
                    || detailsContentFlick.contentY !== previousPageY)
                playSemanticSound("navigate")
        } else if (action === "NavigateUp" && contentFocus) {
            var previousUpY = detailsContentFlick.contentY
            if (detailsContentFlick.contentY > 0)
                detailsContentFlick.contentY = Math.max(0, detailsContentFlick.contentY - 96 * couchScale)
            else {
                contentFocus = false
                tabFocus = true
            }
            if (detailsContentFlick.contentY !== previousUpY || tabFocus)
                playSemanticSound("navigate")
        } else if (action === "NavigateDown" && contentFocus) {
            var previousDownY = detailsContentFlick.contentY
            detailsContentFlick.contentY = Math.min(
                Math.max(0, detailsContentFlick.contentHeight - detailsContentFlick.height),
                detailsContentFlick.contentY + 96 * couchScale)
            if (detailsContentFlick.contentY !== previousDownY)
                playSemanticSound("navigate")
        } else if (action === "NavigateUp" && tabFocus) {
            tabFocus = false
            contentFocus = false
            ensureAction()
            playSemanticSound("navigate")
        } else if (action === "NavigateDown" && tabFocus) {
            tabFocus = false
            contentFocus = true
            playSemanticSound("navigate")
        } else if (action === "NavigateDown" && !contentFocus) {
            tabFocus = true
            contentFocus = false
            playSemanticSound("navigate")
        } else if (tabFocus && (action === "NavigateLeft" || action === "NavigateRight")) {
            if (changeTab(action === "NavigateLeft" ? -1 : 1))
                playSemanticSound("navigate")
        } else if (!contentFocus && (action === "NavigateLeft" || action === "NavigateRight")) {
            if (moveAction(action === "NavigateLeft" ? -1 : 1))
                playSemanticSound("navigate")
        } else if (action === "Confirm" && tabFocus) {
            tabFocus = false
            contentFocus = true
            playSemanticSound("navigate")
        } else if (action === "Confirm" && !contentFocus) {
            playSemanticSound(activateAction())
        } else if (action === "MoreActions") {
            var changed = selectedTab !== 0 || tabFocus || contentFocus || selectedAction !== 0
            selectedTab = 0
            tabFocus = false
            contentFocus = false
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
    onGameChanged: { loadMangoHudProfile(); loadOptimizationProfile(); loadOptiScalerStatus(); loadProtonTweaks(); loadNarrator() }

    Connections {
        target: page.controller || null
        ignoreUnknownSignals: true
        function onOptiScalerChanged(appId) {
            if (String(appId) === String(page.game.steamAppId || ""))
                page.loadOptiScalerStatus()
        }
        function onOptiScalerStatusChanged(changedGameId, result) {
            if (String(changedGameId) === String(page.game.id || "")
                    && result && result.success)
                page.optiScalerData = result
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

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
        clip: true
        Image {
            anchors.fill: parent
            source: String(page.game.headerArtwork || page.game.effectiveArtworkUrl
                           || page.game.fallbackArtwork || "")
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: true
            opacity: status === Image.Ready ? 0.58 : 0
        }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: App.Theme.dark ? "#E80A0F17" : "#E8EEF3F8" }
                GradientStop { position: 0.56; color: App.Theme.dark ? "#A80A0F17" : "#B5EEF3F8" }
                GradientStop { position: 1.0; color: App.Theme.dark ? "#650A0F17" : "#70EEF3F8" }
            }
        }
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: parent.height * 0.44
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#00000000" }
                GradientStop { position: 1.0; color: App.Theme.dark ? "#F50A0F17" : "#F5EEF3F8" }
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 64 * page.couchScale; anchors.rightMargin: 64 * page.couchScale
        anchors.topMargin: 104 * page.couchScale; anchors.bottomMargin: 90 * page.couchScale
        spacing: 14 * page.couchScale
        RowLayout {
            Layout.fillWidth: true
            // Keep the primary Steam artwork recognisably portrait-shaped on a TV.
            // The former 356 px row flattened a 278 px cover into a dashboard tile.
            Layout.minimumHeight: 416 * page.couchScale
            Layout.preferredHeight: 416 * page.couchScale
            Layout.maximumHeight: 416 * page.couchScale
            spacing: 30 * page.couchScale
            Rectangle {
                objectName: "couchDetailsCover"
                Layout.preferredWidth: 278 * page.couchScale
                Layout.fillHeight: true
                radius: 23 * page.couchScale
                color: Qt.rgba(App.Theme.accent.r, App.Theme.accent.g,
                               App.Theme.accent.b, 0.22)
                GameArtwork {
                    anchors.fill: parent
                    anchors.margins: 7 * page.couchScale
                    gameId: String(page.game.id || "")
                    title: String(page.game.name || qsTr("Game"))
                    artworkSource: page.game.effectiveArtworkUrl
                                   || page.game.portraitArtwork
                                   || page.game.headerArtwork
                                   || page.game.fallbackArtwork || ""
                    artworkFillMode: Image.PreserveAspectCrop
                    cornerRadius: 18 * page.couchScale
                }
                border.width: 2
                border.color: App.Theme.accent
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10 * page.couchScale
                Label {
                    objectName: "couchDetailsTitle"
                    Layout.fillWidth: true
                    text: String(page.game.name || qsTr("Game details"))
                    color: App.Theme.text
                    font.pixelSize: 46 * page.couchScale
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("%1 · %2 · %3").arg(String(page.game.launcher || qsTr("Unknown"))).arg(String(page.game.availabilityStatus || page.game.status || qsTr("Unknown"))).arg(String(page.game.filesystem || qsTr("Unknown")))
                    color: App.Theme.textSecondary
                    font.pixelSize: 20 * page.couchScale
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: String(page.game.libraryPath || page.game.installPath
                                 || qsTr("Path unavailable"))
                    color: App.Theme.textSecondary
                    font.pixelSize: 16 * page.couchScale
                    elide: Text.ElideMiddle
                }
                Item { Layout.preferredHeight: 4 * page.couchScale }
                GridLayout {
                    id: detailsActions
                    objectName: "couchDetailsActions"
                    Layout.fillWidth: true
                    columns: Math.min(3, Math.max(1, page.actionModel.length))
                    readonly property int actionRows: Math.max(
                        1, Math.ceil(page.actionModel.length / columns))
                    readonly property real actionHeight:
                        actionRows * 82 * page.couchScale
                        + (actionRows - 1) * rowSpacing
                    Layout.minimumHeight: actionHeight
                    Layout.preferredHeight: actionHeight
                    Layout.maximumHeight: actionHeight
                    rowSpacing: 10 * page.couchScale
                    columnSpacing: 10 * page.couchScale
                    Repeater {
                        id: detailsActionRepeater
                        model: page.actionModel
                        delegate: CouchTile {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 78 * page.couchScale
                            couchScale: page.couchScale
                            symbol: modelData.symbol
                            text: modelData.title
                            subtitle: String(modelData.subtitle || "")
                            primary: modelData.id === "launch"
                            enabled: modelData.enabled
                            focus: !page.tabFocus && !page.contentFocus
                                   && page.selectedAction === index
                            onClicked: {
                                page.selectedAction = index
                                page.activateAction()
                            }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
        ListView {
            id: tabBar
            objectName: "couchDetailsTabs"
            Layout.fillWidth: true
            Layout.preferredHeight: 70 * page.couchScale
            orientation: ListView.Horizontal
            spacing: 10 * page.couchScale
            model: page.tabs
            clip: true
            delegate: CouchButton {
                id: tabButton
                required property var modelData
                required property int index
                couchScale: page.couchScale
                width: Math.max(190 * page.couchScale,
                                tabBar.width / page.tabs.length
                                - tabBar.spacing)
                height: 64 * page.couchScale
                text: modelData.title
                font.pixelSize: 18 * page.couchScale
                font.weight: Font.Bold
                focus: page.tabFocus && !page.contentFocus
                       && page.selectedTab === index
                onClicked: { page.selectedTab = index; page.tabFocus = false }
                background: Rectangle {
                    radius: 16 * page.couchScale
                    color: page.selectedTab === tabButton.index
                           ? App.Theme.surfaceSelected : App.Theme.surface
                    border.width: tabButton.activeFocus ? 4 * page.couchScale
                                                        : page.selectedTab === tabButton.index ? 2 : 1
                    border.color: tabButton.activeFocus ? "white"
                                  : page.selectedTab === tabButton.index
                                    ? App.Theme.accent : App.Theme.border
                    scale: tabButton.activeFocus ? 1.035 : 1.0
                    Behavior on scale { NumberAnimation { duration: 140 } }
                }
            }
        }
        Rectangle {
            objectName: "couchDetailsContent"
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 22 * page.couchScale
            color: App.Theme.dark ? "#E516202C" : "#ECFFFFFF"
            border.width: page.contentFocus
                          ? App.Theme.couchFocusWidth * page.couchScale : 1
            border.color: page.contentFocus ? App.Theme.accent : App.Theme.border
            Flickable {
                id: detailsContentFlick
                anchors.fill: parent
                anchors.margins: 24 * page.couchScale
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
                Label { text: qsTr("Controller ready"); color: App.Theme.accent; font.pixelSize: 16 * page.couchScale; font.weight: Font.Bold }
            }
            Label { Layout.fillWidth: true; text: qsTr("MangoHud uses its saved per-game profile. GameMode and Gamescope remain previews until launch integration is available."); color: App.Theme.textSecondary; font.pixelSize: 18 * page.couchScale; wrapMode: Text.WordWrap }
            GridLayout {
                Layout.fillWidth: true; columns: 3; columnSpacing: 18 * page.couchScale; rowSpacing: 10 * page.couchScale
                Label { text: "GameMode"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: "Gamescope"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: "MangoHud"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: page.gameModeEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.gamescopeEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.mangoHudEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
            }
            Label { Layout.fillWidth: true; text: page.controller && page.controller.buildLaunchPreview ? String(page.controller.buildLaunchPreview(String(page.game.id || ""), { "profile": page.legacyOptimizationProfileLabel(), "gamemode": page.gameModeEnabled, "gamescope": page.gamescopeEnabled, "mangohud": page.mangoHudEnabled, "fpsLimit": page.fpsLimit })) : "%command%"; color: App.Theme.textSecondary; font.family: "monospace"; font.pixelSize: 15 * page.couchScale; elide: Text.ElideMiddle }
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
                    { "symbol": "⚡", "title": "GameMode", "value": page.gameModeEnabled ? qsTr("On") : qsTr("Off"), "enabled": Boolean(page.optimizationData.gamemode && page.optimizationData.gamemode.available) },
                    { "symbol": "▣", "title": "Gamescope", "value": page.optimizationGamescopeLabel(), "enabled": Boolean(page.optimizationData.gamescope && page.optimizationData.gamescope.available) },
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
                    symbol: modelData.symbol
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
        maximumWidth: 900 * page.couchScale
        preferredHeight: 820 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 10 * page.couchScale
            Label { Layout.fillWidth: true; text: qsTr("MangoHud for %1").arg(String(page.game.name || qsTr("Game"))); color: App.Theme.text; font.pixelSize: 30 * page.couchScale; font.weight: Font.Bold; elide: Text.ElideRight }
            Label { Layout.fillWidth: true; text: page.mangoHudProfile.available === true ? qsTr("Configure the overlay for this Steam AppID.") : App.I18n.message(String(page.mangoHudProfile.availabilityMessage || qsTr("MangoHud is unavailable."))); color: page.mangoHudProfile.available === true ? App.Theme.textSecondary : App.Theme.warning; font.pixelSize: 16 * page.couchScale; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true
                text: page.mangoHudProfile.activationStrategy === "per_application_config"
                      ? qsTr("Application profile - changes apply on the next game launch")
                      : page.mangoHudProfile.strategyStatus === "application_config_conflict"
                        ? qsTr("Conflict with an existing MangoHud configuration")
                        : page.mangoHudProfile.strategyStatus === "executable_missing"
                          ? qsTr("Game executable was not determined")
                          : qsTr("Steam environment profile - restart Steam")
                color: page.mangoHudProfile.activationStrategy === "per_application_config"
                       ? App.Theme.success : App.Theme.warning
                font.pixelSize: 15 * page.couchScale
                wrapMode: Text.WordWrap
            }
            ListView {
                id: mangoHudOptions
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 7 * page.couchScale
                model: [
                    { "symbol": "◉", "title": qsTr("Preset"), "value": page.mangoHudPresetLabel(), "enabled": true },
                    { "symbol": "⌖", "title": qsTr("Position"), "value": page.mangoHudPositionLabel(), "enabled": true },
                    { "symbol": "A", "title": qsTr("Interface size"), "value": page.mangoHudSizeLabel(), "enabled": true },
                    { "symbol": "↯", "title": qsTr("FPS limit"), "value": page.mangoHudFpsLimit > 0 ? String(page.mangoHudFpsLimit) : qsTr("Unlimited"), "enabled": true },
                    { "symbol": "♨", "title": qsTr("CPU and GPU temperatures"), "value": page.mangoHudTemperatures ? qsTr("On") : qsTr("Off"), "enabled": true },
                    { "symbol": "▤", "title": qsTr("RAM and VRAM"), "value": page.mangoHudMemory ? qsTr("On") : qsTr("Off"), "enabled": true },
                    { "symbol": "✓", "title": qsTr("Save profile"), "value": "", "enabled": page.mangoHudPreset === "disabled" || page.mangoHudProfile.available === true },
                    { "symbol": "‹", "title": qsTr("Cancel"), "value": "", "enabled": true }
                ]
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 62 * page.couchScale
                    couchScale: page.couchScale
                    symbol: modelData.symbol
                    text: modelData.title
                    subtitle: modelData.value
                    enabled: modelData.enabled
                    focus: page.mangoHudOverlayOpen && page.mangoHudRow === index
                    onClicked: { page.mangoHudRow = index; page.handleMangoHudAction("Confirm") }
                }
            }
            Label { Layout.fillWidth: true; text: qsTr("More appearance, metrics, advanced and logging settings are available in Desktop Mode."); color: App.Theme.textSecondary; font.pixelSize: 15 * page.couchScale; wrapMode: Text.WordWrap }
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
}
