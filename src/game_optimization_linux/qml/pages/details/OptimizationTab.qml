pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab
    signal toastRequested(string message, string tone)
    signal optiScalerRequested()
    // Which part of the former single page this instance shows:
    // "performance" | "diagnostics" | "integrations", or "all" (the previous
    // single long page; default when embedded without a section). Cards of
    // other parts are not created (Loader inactive) except the runtime
    // wrappers card, whose text field is used by a page-level copy function.
    property string section: "all"
    // Launcher controls can share the overview's scroll area.
    property bool embedded: false
    implicitHeight: embedded ? optimizationColumn.implicitHeight : 0
    function shows(part) { return tab.section === "all" || tab.section === part }
    // Exposed for the details page to keep the scroll position per tab.
    readonly property var scrollFlickable: optimizationScrollView.contentItem
    function copyText(text) {
        clipboardHelper.text = String(text || "")
        clipboardHelper.selectAll()
        clipboardHelper.copy()
        clipboardHelper.text = ""
    }
    TextEdit { id: clipboardHelper; visible: false }
    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData && gameData.id || "")
    readonly property bool localGame: Boolean(gameData && gameData.localGame)
    readonly property bool launcherGame: ["Heroic", "Lutris"].indexOf(String(gameData && gameData.launcher || "")) >= 0
    readonly property bool lutrisGame: String(gameData && gameData.launcher || "") === "Lutris"
    property var launcherStatus: ({})
    property string launcherMessage: ""
    function refreshLauncher() {
        if (!tab.shows("integrations") || !launcherGame || !controller || !controller.getLauncherIntegration || !gameId) return
        launcherStatus = controller.getLauncherIntegration(gameId) || ({})
        launcherPollCount = launcherStatus.pending ? launcherPollCount + 1 : 0
    }
    property int launcherPollCount: 0
    property int launcherSyncPolls: 0
    // MangoHud config sync runs on a worker: refresh while it is in progress.
    Timer {
        interval: 500
        repeat: true
        running: tab.launcherGame && tab.launcherStatus.state === "syncing" && tab.launcherSyncPolls < 120
        onTriggered: { tab.launcherSyncPolls += 1; tab.refreshLauncher() }
    }
    // Missing launcher-sandbox tools (MangoHud/Gamescope Flatpak extensions).
    property var launcherComponents: ({})
    property bool componentsConfirm: false
    property int componentsPollCount: 0
    function refreshComponents() {
        if (!tab.shows("integrations") || !tab.launcherGame || !tab.controller || !tab.controller.getLauncherComponents || !tab.gameId) return
        var previous = tab.launcherComponents || ({})
        tab.launcherComponents = tab.controller.getLauncherComponents(tab.gameId) || ({})
        tab.componentsPollCount = tab.launcherComponents.pending ? tab.componentsPollCount + 1 : 0
        if (previous.pending && !tab.launcherComponents.pending)
            tab.refreshLauncher()
    }
    function repairComponents() {
        if (!tab.controller || !tab.controller.repairLauncherComponents) return
        tab.componentsConfirm = false
        tab.launcherComponents = tab.controller.repairLauncherComponents(tab.gameId) || ({})
    }
    function componentStateText(item) {
        var state = String(item.state || "")
        if (state === "missing") return qsTr("Missing")
        if (state === "checking") return qsTr("Checking")
        if (state === "installing") return qsTr("Installing")
        if (state === "ready") return qsTr("Installed")
        if (state === "restart_required") return qsTr("Restart the launcher")
        if (state === "unavailable") return qsTr("Not available")
        return qsTr("Error")
    }
    Timer {
        interval: 1000
        repeat: true
        running: tab.launcherGame && Boolean(tab.launcherComponents.pending) && tab.componentsPollCount < 1800
        onTriggered: tab.refreshComponents()
    }
    // The launcher's Flatpak sandbox is checked in the background; poll the
    // cheap status call until the answer arrives (bounded).
    Timer {
        id: launcherPendingTimer
        interval: 1000
        repeat: true
        running: tab.launcherGame && Boolean(tab.launcherStatus.pending) && tab.launcherPollCount < 40
        onTriggered: tab.refreshLauncher()
    }
    function launcherStatusText() {
        var status = launcherStatus || ({})
        var name = String(status.launcher || (gameData && gameData.launcher) || "")
        if (status.launcherRunning) return qsTr("Close %1 first; it can overwrite the game settings").arg(name)
        if (status.state === "applied" && status.configCurrent) return qsTr("Applied - configuration is current")
        if (status.state === "applied") return qsTr("%1 starts this game with the GameOpti settings").arg(name)
        if (status.state === "syncing") return qsTr("Synchronizing...")
        if (status.state === "config_outdated") return App.I18n.message(String(status.message || qsTr("The MangoHud config is outdated")))
        if (status.state === "unavailable") return App.I18n.message(String(status.message || ""))
        if ((status.blockers || []).length > 0 && status.state !== "applied") return App.I18n.message(String(status.message || ""))
        return qsTr("Saved, but not applied to %1").arg(name)
    }
    function changeLauncher(apply) {
        var result = apply ? controller.applyLauncherIntegration(gameId) : controller.restoreLauncherIntegration(gameId)
        launcherStatus = result || ({})
        launcherMessage = result && result.success === false ? String(result.error || "") : ""
    }
    property bool loading: false
    property bool dirty: false
    property int profileRevision: 0
    property bool recommendationDetailsExpanded: false
    property string errorMessage: ""
    property string appId: ""
    property string preset: "automatic"
    property string gameCategory: "unknown"
    property string userGoal: "stable_image"
    property string targetDisplayId: ""
    property string targetFpsMode: "automatic"
    property int targetFps: 60
    property bool gamemodeEnabled: false
    property bool gamescopeEnabled: false
    property string gamescopeMode: "disabled"
    property int inputWidth: 1920
    property int inputHeight: 1080
    property int outputWidth: 1920
    property int outputHeight: 1080
    property int refreshRate: 60
    property bool gamescopeFullscreen: true
    property string gamescopeScaler: "auto"
    property string gamescopeFilter: "linear"
    // Desktop nested Gamescope options; neutral values emit nothing.
    property string gamescopeWindowMode: "fullscreen"
    property int gamescopeSharpnessPercent: -1
    property bool gamescopeAdaptiveSync: false
    property bool gamescopeGrabKeyboard: false
    property bool gamescopeHdr: false
    property bool inputAuto: false
    property bool outputAuto: false
    property bool inputCustom: false
    property bool outputCustom: false
    property int displayIndex: -1
    property bool forceWindowsFullscreen: false
    property string gamescopeBackend: "auto"
    property real maxScale: 0
    property int nestedRefresh: 0
    property int framerateLimit: 0
    property bool framerateCustom: false
    property int unfocusedRefresh: 0
    property bool unfocusedCustom: false
    property int hdrSdrContentNits: 0
    property real sdrGamutWideness: -1
    property bool hdrItm: false
    property int hdrItmSdrNits: 0
    property int hdrItmTargetNits: 0
    property bool forceGrabCursor: false
    property int hideCursorDelay: 0
    property real mouseSensitivity: 0
    property bool exposeWayland: false
    property bool steamIntegration: false
    property var gamescopeArguments: []
    property string gamescopeSteamLaunchOptions: ""
    property var hdrReadiness: ({})
    property bool gamescopeArgumentsExpanded: false
    property bool gamescopeTestRunning: false
    property string gamescopeTestMessage: ""
    property var manualOverrides: ({})
    property var displays: []
    property var recommendation: ({})
    property var presetPlan: ({})
    property var categoryClassification: ({})
    property bool recommendationAnalyzed: false
    property var gamemodeStatus: ({ "available": false })
    property var gamescopeStatus: ({ "available": false })
    property var runnerStatus: ({ "installed": false })
    property string launchPlanText: "%command%"
    property var launchPlan: ({})
    property string steamLaunchCommand: ""
    property string renderingSummary: ""
    property string fpsLimitOwner: "none"
    property var protonOverrides: []
    property bool showSteamInstructions: false
    property var gameAnalysis: ({ "status": "not_analyzed" })
    property var runnerPreflight: ({})
    property var settingPreview: ({})
    property bool automaticPreviewOpen: false

    readonly property var presetValues: ["automatic", "maximum_performance", "balanced", "quiet", "custom"]
    readonly property var presetLabels: [qsTr("Automatic"), qsTr("Maximum Performance"), qsTr("Balanced"), qsTr("Quiet"), qsTr("Custom")]
    readonly property var categoryValues: ["competitive", "fast_action", "cinematic", "platformer_2d", "strategy_simulation", "retro", "unknown", "custom"]
    readonly property var categoryLabels: [qsTr("Competitive"), qsTr("Fast action"), qsTr("Cinematic single-player"), qsTr("Platformer / 2D"), qsTr("Strategy / simulation"), qsTr("Retro"), qsTr("Unknown"), qsTr("Custom")]
    readonly property var goalValues: ["lowest_latency", "stable_image", "best_quality", "low_power", "custom"]
    readonly property var goalLabels: [qsTr("Lowest latency"), qsTr("Stable image"), qsTr("Best quality"), qsTr("Low power"), qsTr("Custom")]
    readonly property var gamescopeModeValues: ["disabled", "automatic", "native", "performance", "quality", "custom"]
    readonly property var gamescopeModeLabels: [qsTr("Disabled"), qsTr("Automatic"), qsTr("Native"), qsTr("Performance"), qsTr("Quality"), qsTr("Custom")]
    readonly property var displayValues: displays.map(function(item) { return String(item.id || "") })
    readonly property var displayLabels: displays.map(function(item) { return String(item.label || item.name || "") })
    readonly property var draft: ({
        "preset": preset, "gameCategory": gameCategory, "userGoal": userGoal,
        "targetDisplayId": targetDisplayId, "targetFpsMode": targetFpsMode,
        "targetFps": targetFps, "gamemodeEnabled": gamemodeEnabled,
        "gamescopeEnabled": gamescopeEnabled, "gamescopeMode": gamescopeMode,
        "gamescopeInputWidth": inputWidth, "gamescopeInputHeight": inputHeight,
        "gamescopeOutputWidth": outputWidth, "gamescopeOutputHeight": outputHeight,
        "gamescopeRefreshRate": refreshRate, "gamescopeFullscreen": gamescopeFullscreen,
        "gamescopeScaler": gamescopeScaler, "gamescopeFilter": gamescopeFilter,
        "gamescopeWindowMode": gamescopeWindowMode,
        "gamescopeSharpnessPercent": gamescopeSharpnessPercent,
        "gamescopeAdaptiveSync": gamescopeAdaptiveSync,
        "gamescopeGrabKeyboard": gamescopeGrabKeyboard, "gamescopeHdr": gamescopeHdr,
        "gamescopeInputAuto": inputAuto, "gamescopeOutputAuto": outputAuto,
        "gamescopeDisplayIndex": displayIndex,
        "gamescopeForceWindowsFullscreen": forceWindowsFullscreen,
        "gamescopeBackend": gamescopeBackend, "gamescopeMaxScale": maxScale,
        "gamescopeNestedRefresh": nestedRefresh, "gamescopeFramerateLimit": framerateLimit,
        "gamescopeUnfocusedRefresh": unfocusedRefresh,
        "gamescopeHdrSdrContentNits": hdrSdrContentNits,
        "gamescopeSdrGamutWideness": sdrGamutWideness, "gamescopeHdrItm": hdrItm,
        "gamescopeHdrItmSdrNits": hdrItmSdrNits, "gamescopeHdrItmTargetNits": hdrItmTargetNits,
        "gamescopeForceGrabCursor": forceGrabCursor, "gamescopeHideCursorDelay": hideCursorDelay,
        "gamescopeMouseSensitivity": mouseSensitivity, "gamescopeExposeWayland": exposeWayland,
        "gamescopeSteamIntegration": steamIntegration,
        "manualOverrides": manualOverrides
    })
    readonly property var resolutionValues: ["auto", "1280x720", "1600x900", "1920x1080", "2560x1440", "3440x1440", "3840x2160", "custom"]
    readonly property var framerateValues: [0, 30, 40, 45, 60, 90, 120, 144, 165, 240, -1]
    readonly property var unfocusedValues: [0, 15, 30, 60, -1]
    readonly property bool gsAdvanced: Boolean(gamescopeStatus.helpVerified)
    readonly property bool gsHdrActive: gamescopeHdr && gsSupports("--hdr-enabled")
    readonly property bool gsItmActive: gsHdrActive && hdrItm && (gsSupports("--hdr-itm-enabled") || gsSupports("--hdr-itm-enable"))
    readonly property var backendValues: ["auto"].concat(["wayland", "sdl"].filter(function(item) {
        return (gamescopeStatus.supportedBackends || []).indexOf(item) >= 0
    }))
    readonly property var filterValues: ["linear", "nearest", "fsr", "nis", "pixel", "sgsr"].filter(function(item) {
        return item === "linear" || (gamescopeStatus.supportedFilters || []).indexOf(item) >= 0
    })
    readonly property var scalerValues: ["auto", "fit", "fill", "stretch", "integer"].filter(function(item) {
        var listed = gamescopeStatus.supportedScalers || []
        return item === "auto" || listed.length === 0 || listed.indexOf(item) >= 0
    })

    function indexOf(values, wanted, fallback) {
        var index = values.indexOf(wanted)
        return index >= 0 ? index : fallback
    }
    function changed(key) {
        if (loading) return
        dirty = true
        if (key) {
            var overrides = Object.assign({}, manualOverrides)
            overrides[key] = true
            manualOverrides = overrides
        }
        previewTimer.restart()
    }
    function choosePreset(value) {
        loading = true
        preset = value
        var preservedOverrides = ({})
        if (manualOverrides.category)
            preservedOverrides.category = true
        if (manualOverrides.display)
            preservedOverrides.display = true
        manualOverrides = preservedOverrides
        loading = false
        dirty = true
        recommendationAnalyzed = false
        previewTimer.restart()
    }
    function selectedDisplay() {
        var index = displayValues.indexOf(targetDisplayId)
        return index >= 0 && index < displays.length ? displays[index] : null
    }
    function fpsOwnerLabel() {
        if (fpsLimitOwner === "gamescope") return "Gamescope"
        if (fpsLimitOwner === "mangohud") return "MangoHud"
        return qsTr("None")
    }
    function frameRateLimitLabel() {
        var result = gameAnalysis.frameRate || ({})
        if (result.state === "likely_capped" && result.estimatedCeilingFps !== null)
            return qsTr("Likely ~%1 FPS").arg(Number(result.estimatedCeilingFps).toFixed(0))
        if (result.state === "not_detected")
            return qsTr("Not detected")
        return qsTr("Unknown")
    }
    function emptyRecommendationMessage() {
        var state = String(gameAnalysis.settingsAnalysis
                           && gameAnalysis.settingsAnalysis.recommendationState || "")
        if (state === "unsupported_engine")
            return qsTr("Automatic graphics settings optimization is not available for this game yet.")
        if (state === "missing_config")
            return qsTr("No supported existing configuration file was found.")
        if (state === "invalid_config")
            return qsTr("Configuration files were invalid or unsupported.")
        if (state === "no_supported_settings")
            return qsTr("No safely modifiable settings were found")
        if (state === "baseline_missing")
            return qsTr("A representative baseline is required before recommending a settings change.")
        if (state === "baseline_unrepresentative")
            return qsTr("The saved baseline is not representative enough for measured settings recommendations.")
        if (state === "baseline_stale")
            return qsTr("The saved baseline predates the current graphics settings. Record a new baseline before using measured Automatic recommendations.")
        if (state === "bottleneck_low_confidence")
            return qsTr("Bottleneck confidence is too low for a conservative settings change.")
        if (state === "capped_with_headroom")
            return qsTr("No graphics reductions are recommended because the game appears frame-limited and the hardware has available headroom.")
        if (state === "balanced")
            return qsTr("No graphics reductions are recommended because the measured workload is balanced.")
        if (state === "no_matching_setting")
            return qsTr("No supported existing setting matches the measured bottleneck.")
        return qsTr("No safe optimization recommendations were found")
    }
    function applyDisplayDefaults() {
        var display = selectedDisplay()
        if (!display) return
        outputWidth = Number(display.width || outputWidth)
        outputHeight = Number(display.height || outputHeight)
        refreshRate = Math.round(Number(display.refreshRate || refreshRate))
    }
    // An option is offered only when the local `gamescope --help` lists it.
    function gsSupports(option) {
        return (gamescopeStatus.supportedOptions || []).indexOf(option) >= 0
    }
    function gsChanged() {
        if (loading) return
        preset = "custom"
        changed("gamescope")
    }
    function resolutionIndex(auto, custom, width, height) {
        if (auto) return 0
        var index = resolutionValues.indexOf(width + "x" + height)
        return custom || index < 0 ? resolutionValues.length - 1 : index
    }
    function chooseResolution(target, value) {
        var parts = String(value).split("x")
        if (target === "input") {
            inputAuto = value === "auto"
            inputCustom = value === "custom"
            if (parts.length === 2) { inputWidth = Number(parts[0]); inputHeight = Number(parts[1]) }
        } else {
            outputAuto = value === "auto"
            outputCustom = value === "custom"
            if (parts.length === 2) { outputWidth = Number(parts[0]); outputHeight = Number(parts[1]) }
        }
        gsChanged()
    }
    function rateIndex(values, value, custom) {
        var index = values.indexOf(value)
        return custom || index < 0 ? values.length - 1 : index
    }
    function sharpnessApplies() {
        return (gamescopeFilter === "fsr" || gamescopeFilter === "nis")
                && (gsSupports("--sharpness") || gsSupports("--fsr-sharpness"))
    }
    function sameRenderAndOutput() {
        return (inputAuto && outputAuto) || (!inputAuto && !outputAuto
                && inputWidth === outputWidth && inputHeight === outputHeight)
    }
    function filterHint(value) {
        if (value === "fsr") return qsTr("FSR: good general-purpose upscaling.")
        if (value === "nis") return qsTr("NIS: an alternative sharpening upscaler.")
        if (value === "nearest" || value === "pixel") return qsTr("Nearest / Pixel: best for pixel-art games.")
        if (value === "sgsr") return qsTr("SGSR: Snapdragon Game Super Resolution with sharpening.")
        return qsTr("Linear: smooth default filtering.")
    }
    function scalerHint(value) {
        if (value === "fit") return qsTr("Fit keeps the aspect ratio and may add black bars.")
        if (value === "fill") return qsTr("Fill keeps the aspect ratio and crops the edges.")
        if (value === "stretch") return qsTr("Stretch fills the screen and changes the aspect ratio.")
        if (value === "integer") return qsTr("Integer scales by whole numbers; best for pixel-art games.")
        return qsTr("Auto lets Gamescope choose.")
    }
    function hdrNote(code) {
        if (code === "wayland_session") return qsTr("Wayland session: the basic requirement is met.")
        if (code === "not_wayland_session") return qsTr("Not a Wayland session: HDR may not work.")
        if (code === "hdr_flag_missing") return qsTr("The installed Gamescope has no --hdr-enabled: HDR is unavailable.")
        if (code === "nvidia_modeset") return qsTr("NVIDIA: HDR requires working DRM kernel modesetting (nvidia-drm.modeset=1).")
        if (code === "wsi_detected") return qsTr("Gamescope WSI layer detected; its variable is set only while HDR is on.")
        if (code === "wsi_checked_at_launch") return qsTr("The Gamescope WSI layer is checked on the host when the game starts.")
        if (code === "wsi_missing") return qsTr("Gamescope WSI layer not found: HDR games may be shown as SDR.")
        return code
    }
    function refreshGamescope() {
        if (!controller || !controller.refreshGamescopeDetection) return
        var result = controller.refreshGamescopeDetection() || ({})
        gamescopeStatus = result
        hdrReadiness = result.hdrReadiness || ({})
        previewTimer.restart()
    }
    function resetGamescope() {
        if (controller && controller.resetGamescopeSettings && gameId) {
            var result = controller.resetGamescopeSettings(gameId)
            if (result && result.success) applyResult(result)
            else errorMessage = String(result && result.error || "")
        }
    }
    function startGamescopeTest(withHdr) {
        gamescopeTestMessage = ""
        if (!controller || !controller.startGamescopeTest || !gameId) return
        var result = controller.startGamescopeTest(gameId, draft, Boolean(withHdr)) || ({})
        gamescopeTestRunning = Boolean(result.running)
        if (!result.success) gamescopeTestMessage = gamescopeTestError(result)
    }
    function gamescopeTestError(result) {
        var code = String(result && result.code || "")
        if (code === "vkcube_missing") return qsTr("The Gamescope test requires the vulkan-tools package")
        if (code === "gamescope_missing") return qsTr("Gamescope is not installed")
        if (code === "already_running") return qsTr("A Gamescope test is already running")
        if (code === "process_failed") return qsTr("The Gamescope test ended with an error: %1").arg(String(result.message || ""))
        return qsTr("Could not start the Gamescope test: %1").arg(String(result && result.message || ""))
    }
    function copyGamescopeLaunchOptions() {
        gamescopeLaunchOptionsText.selectAll()
        gamescopeLaunchOptionsText.copy()
        gamescopeLaunchOptionsText.deselect()
        toastRequested(qsTr("Steam launch options copied"), "success")
    }
    function applyGamescopeResult(result) {
        gamescopeWindowMode = String(result.gamescopeWindowMode || (result.gamescopeFullscreen ? "fullscreen" : "borderless"))
        gamescopeSharpnessPercent = Number(result.gamescopeSharpnessPercent !== undefined ? result.gamescopeSharpnessPercent : -1)
        gamescopeAdaptiveSync = Boolean(result.gamescopeAdaptiveSync)
        gamescopeGrabKeyboard = Boolean(result.gamescopeGrabKeyboard)
        gamescopeHdr = Boolean(result.gamescopeHdr)
        inputAuto = Boolean(result.gamescopeInputAuto)
        outputAuto = Boolean(result.gamescopeOutputAuto)
        inputCustom = !inputAuto && resolutionValues.indexOf(inputWidth + "x" + inputHeight) < 0
        outputCustom = !outputAuto && resolutionValues.indexOf(outputWidth + "x" + outputHeight) < 0
        displayIndex = Number(result.gamescopeDisplayIndex !== undefined ? result.gamescopeDisplayIndex : -1)
        forceWindowsFullscreen = Boolean(result.gamescopeForceWindowsFullscreen)
        gamescopeBackend = String(result.gamescopeBackend || "auto")
        maxScale = Number(result.gamescopeMaxScale || 0)
        nestedRefresh = Number(result.gamescopeNestedRefresh || 0)
        framerateLimit = Number(result.gamescopeFramerateLimit || 0)
        framerateCustom = framerateValues.indexOf(framerateLimit) < 0
        unfocusedRefresh = Number(result.gamescopeUnfocusedRefresh || 0)
        unfocusedCustom = unfocusedValues.indexOf(unfocusedRefresh) < 0
        hdrSdrContentNits = Number(result.gamescopeHdrSdrContentNits || 0)
        sdrGamutWideness = Number(result.gamescopeSdrGamutWideness !== undefined ? result.gamescopeSdrGamutWideness : -1)
        hdrItm = Boolean(result.gamescopeHdrItm)
        hdrItmSdrNits = Number(result.gamescopeHdrItmSdrNits || 0)
        hdrItmTargetNits = Number(result.gamescopeHdrItmTargetNits || 0)
        forceGrabCursor = Boolean(result.gamescopeForceGrabCursor)
        hideCursorDelay = Number(result.gamescopeHideCursorDelay || 0)
        mouseSensitivity = Number(result.gamescopeMouseSensitivity || 0)
        exposeWayland = Boolean(result.gamescopeExposeWayland)
        steamIntegration = Boolean(result.gamescopeSteamIntegration)
        applyGamescopePreview(result)
    }
    function applyGamescopePreview(result) {
        gamescopeArguments = result.gamescopeArguments ? Array.from(result.gamescopeArguments) : []
        gamescopeSteamLaunchOptions = String(result.gamescopeSteamLaunchOptions || "")
        hdrReadiness = result.gamescopeHdrReadiness || ({})
        gamescopeTestRunning = Boolean(result.gamescopeTestRunning)
    }
    function applyGamescopeMode(value) {
        gamescopeMode = value
        gamescopeEnabled = value !== "disabled"
        applyDisplayDefaults()
        if (value === "native" || value === "quality") {
            inputWidth = outputWidth
            inputHeight = outputHeight
        } else if (value === "performance") {
            inputWidth = Math.max(320, Math.round(outputWidth * 0.75 / 2) * 2)
            inputHeight = Math.max(320, Math.round(outputHeight * 0.75 / 2) * 2)
        }
        changed("gamescope")
    }
    function applyResult(result) {
        loading = true
        errorMessage = String(result && result.error || "")
        if (!result || !result.success) { loading = false; return }
        appId = String(result.appId || "")
        preset = String(result.preset || "automatic")
        gameCategory = String(result.gameCategory || "unknown")
        userGoal = String(result.userGoal || "stable_image")
        targetDisplayId = String(result.targetDisplayId || "")
        targetFpsMode = String(result.targetFpsMode || "automatic")
        targetFps = Number(result.targetFps || 60)
        gamemodeEnabled = Boolean(result.gamemodeEnabled)
        gamescopeEnabled = Boolean(result.gamescopeEnabled)
        gamescopeMode = String(result.gamescopeMode || "disabled")
        inputWidth = Number(result.gamescopeInputWidth || 1920); inputHeight = Number(result.gamescopeInputHeight || 1080)
        outputWidth = Number(result.gamescopeOutputWidth || 1920); outputHeight = Number(result.gamescopeOutputHeight || 1080)
        refreshRate = Number(result.gamescopeRefreshRate || 60)
        gamescopeFullscreen = Boolean(result.gamescopeFullscreen)
        gamescopeScaler = String(result.gamescopeScaler || "auto"); gamescopeFilter = String(result.gamescopeFilter || "linear")
        applyGamescopeResult(result)
        manualOverrides = result.manualOverrides || ({})
        displays = result.displays ? Array.from(result.displays) : []
        if (!targetDisplayId && displays.length) {
            var primary = displays.filter(function(item) { return Boolean(item.primary) })
            targetDisplayId = String((primary.length ? primary[0] : displays[0]).id || "")
        }
        recommendation = result.recommendation || ({})
        presetPlan = result.presetPlan || ({})
        categoryClassification = result.categoryClassification || ({})
        gamemodeStatus = result.gamemode || ({ "available": false })
        gamescopeStatus = result.gamescope || ({ "available": false })
        runnerStatus = result.runner || ({ "installed": false })
        launchPlanText = String(result.launchPlanText || "%command%")
        launchPlan = result.launchPlan || ({})
        steamLaunchCommand = String(result.steamLaunchCommand || "")
        renderingSummary = String(result.renderingSummary || "")
        fpsLimitOwner = String(result.fpsLimitOwner || result.launchPlan && result.launchPlan.fpsLimitOwner || "none")
        protonOverrides = result.protonOverrides ? Array.from(result.protonOverrides) : []
        gameAnalysis = result.gameAnalysis || ({ "status": "not_analyzed" })
        recommendationAnalyzed = false
        dirty = false; loading = false
    }
    function loadProfile() {
        if (controller && controller.getOptimizationProfile && gameId)
            applyResult(controller.getOptimizationProfile(gameId))
        refreshLauncher()
        refreshComponents()
    }
    function preview() {
        if (!controller || !controller.previewOptimizationProfile || !gameId) return
        var result = controller.previewOptimizationProfile(gameId, draft)
        if (result && result.success) {
            loading = true
            userGoal = String(result.userGoal || userGoal)
            targetFpsMode = String(result.targetFpsMode || targetFpsMode)
            targetFps = Number(result.targetFps || targetFps)
            gamemodeEnabled = Boolean(result.gamemodeEnabled)
            gamescopeEnabled = Boolean(result.gamescopeEnabled)
            gamescopeMode = String(result.gamescopeMode || gamescopeMode)
            recommendation = result.recommendation || ({})
            presetPlan = result.presetPlan || ({})
            categoryClassification = result.categoryClassification || ({})
            launchPlanText = String(result.launchPlanText || "%command%")
            launchPlan = result.launchPlan || ({})
            renderingSummary = String(result.renderingSummary || "")
            fpsLimitOwner = String(result.fpsLimitOwner || "none")
            protonOverrides = result.protonOverrides ? Array.from(result.protonOverrides) : []
            applyGamescopePreview(result)
            errorMessage = ""
            recommendationAnalyzed = true
            loading = false
        } else errorMessage = String(result && result.error || qsTr("Invalid optimization profile"))
    }
    function saveProfile() {
        if (!controller || !controller.saveOptimizationProfile) return
        var result = controller.saveOptimizationProfile(gameId, draft)
        if (result && result.success) { applyResult(result); profileRevision += 1; refreshLauncher(); refreshComponents() }
        else errorMessage = String(result && result.error || qsTr("Could not save optimization profile"))
    }
    function analyzeGame() {
        if (!controller || !controller.analyzeGameOptimization || !gameId)
            return
        var result = controller.analyzeGameOptimization(gameId)
        if (result && result.success)
            gameAnalysis = result
        else
            errorMessage = String(result && result.error || qsTr("Game analysis could not start"))
    }
    function recordBaseline() {
        if (!gameId) {
            errorMessage = qsTr("Baseline recording could not start")
            console.warn("Record baseline rejected at QML boundary: gameId is empty")
            toastRequested(errorMessage, "error")
            return
        }
        if (!controller || !controller.recordOptimizationBaseline) {
            errorMessage = qsTr("Baseline recording could not start")
            console.warn("Record baseline rejected at QML boundary: controller method is unavailable for gameId=" + gameId)
            toastRequested(errorMessage, "error")
            return
        }
        var result = controller.recordOptimizationBaseline(gameId)
        if (result && result.success) {
            runnerPreflight = ({})
            gameAnalysis = Object.assign({}, gameAnalysis, {
                "baselineSession": result.baselineSession || ({ "status": "waiting_for_steam" })
            })
            toastRequested(qsTr("Baseline session started. Play a representative part of the game, then exit normally."), "info")
        } else {
            runnerPreflight = result && result.runnerPreflight || ({})
            errorMessage = result && result.error ? App.I18n.message(String(result.error))
                                                  : qsTr("Baseline recording could not start")
            console.warn("Record baseline rejected: gameId=" + gameId
                         + " guard=" + String(result && result.code || "unknown")
                         + " reason=" + errorMessage)
            toastRequested(errorMessage, "error")
        }
    }
    function recordComparison() {
        if (!controller || !controller.recordOptimizationComparison || !gameId)
            return
        var result = controller.recordOptimizationComparison(gameId)
        if (result && result.success) {
            gameAnalysis = Object.assign({}, gameAnalysis, {
                "baselineSession": result.baselineSession || ({ "status": "waiting_for_steam", "kind": "comparison" })
            })
        } else {
            errorMessage = String(result && result.error || qsTr("Comparison recording could not start"))
        }
    }
    function baselineStatus() {
        return String(gameAnalysis.baselineSession && gameAnalysis.baselineSession.status || "not_recorded")
    }
    function measurementActive() {
        return ["waiting_for_steam", "waiting_for_runner", "waiting_for_launcher",
                "recording", "waiting_for_game_exit"].indexOf(baselineStatus()) >= 0
    }
    function cancelMeasurement() {
        if (!controller || !controller.cancelOptimizationMeasurement || !gameId)
            return
        var result = controller.cancelOptimizationMeasurement(gameId)
        if (result && result.success) {
            gameAnalysis = Object.assign({}, gameAnalysis, {
                "baselineSession": Object.assign({}, gameAnalysis.baselineSession || ({}),
                                                 { "status": "failed", "error": "Recording cancelled" })
            })
        } else {
            errorMessage = App.I18n.message(String(result && result.error || ""))
        }
    }
    function pendingOptimizationTest() {
        return Boolean(gameAnalysis.appliedChange
                       && gameAnalysis.appliedChange.state === "applied")
    }
    function measurementValue(name, decimals, suffix) {
        var value = gameAnalysis.measurement && gameAnalysis.measurement[name]
        if (value === undefined || value === null || !isFinite(Number(value)))
            return qsTr("Unavailable")
        return Number(value).toFixed(decimals) + suffix
    }
    function comparisonValue(group, name, decimals, suffix) {
        var values = gameAnalysis[group] || ({})
        var value = values[name]
        if (value === undefined || value === null || !isFinite(Number(value)))
            return qsTr("Unavailable")
        return Number(value).toFixed(decimals) + suffix
    }
    function comparisonDelta(name) {
        var before = Number(gameAnalysis.beforeMeasurement
                            && gameAnalysis.beforeMeasurement[name])
        var after = Number(gameAnalysis.afterMeasurement
                           && gameAnalysis.afterMeasurement[name])
        if (!isFinite(before) || !isFinite(after) || before === 0)
            return qsTr("Unavailable")
        var value = (after - before) / before * 100
        return (value >= 0 ? "+" : "") + value.toFixed(1) + "%"
    }
    function comparisonOutcome() {
        var outcome = String(gameAnalysis.comparison
                             && gameAnalysis.comparison.outcome || "")
        if (outcome === "headroom_improved")
            return qsTr("Performance headroom improved, FPS remained capped")
        if (outcome === "insufficient_data")
            return qsTr("Comparison measurement was recorded but was not representative enough")
        if (outcome === "improvement")
            return qsTr("Improved")
        if (outcome === "regression")
            return qsTr("Degraded")
        if (outcome === "no_meaningful_change")
            return qsTr("No meaningful change")
        return outcome
    }
    function automaticState() {
        return gameAnalysis.automaticOptimization || ({})
    }
    function automaticSession() {
        return automaticState().session || ({})
    }
    function automaticProblemLabel() {
        var kind = String(automaticState().problem
                          && automaticState().problem.kind || "insufficient_data")
        if (kind === "gpu_bound") return qsTr("GPU bound")
        if (kind === "cpu_bound") return qsTr("CPU bound")
        if (kind === "frame_pacing") return qsTr("Frame pacing")
        if (kind === "frame_limited") return qsTr("Frame limited")
        if (kind === "balanced") return qsTr("Balanced")
        if (kind === "memory_pressure") return qsTr("Memory pressure")
        return qsTr("Insufficient data")
    }
    function automaticOutcomeLabel(value) {
        var outcome = String(value || "")
        if (outcome === "improved") return qsTr("Improved")
        if (outcome === "degraded") return qsTr("Degraded")
        if (outcome === "marginal") return qsTr("Marginal")
        if (outcome === "headroom_improved") return qsTr("Headroom improved")
        if (outcome === "target_already_met") return qsTr("Target already met")
        if (outcome === "insufficient_data") return qsTr("Insufficient comparable data")
        return qsTr("Not measured")
    }
    function startAutomatic(candidateId) {
        if (!controller || !controller.startAutomaticOptimization)
            return
        var result = controller.startAutomaticOptimization(
            gameId, String(candidateId || ""))
        if (result && result.success) {
            automaticPreviewOpen = false
            toastRequested(qsTr("Runtime optimization applied. Record a comparison before keeping it."), "success")
            loadProfile()
        } else {
            toastRequested(App.I18n.analysisMessage(
                               String(result && result.error
                                      || qsTr("Automatic Optimization could not start"))),
                           "error")
        }
    }
    function activeChangeValue(name) {
        var change = gameAnalysis.appliedChange || ({})
        return String(change[name] || "")
    }
    function previewSetting(instanceId, proposedValue) {
        if (!controller || !controller.previewGameSettingChange)
            return
        var result = controller.previewGameSettingChange(
            gameId, String(instanceId || ""), String(proposedValue || ""))
        if (result && result.success)
            settingPreview = result
        else
            toastRequested(String(result && result.error || qsTr("Setting preview could not be created")), "error")
    }
    function applySettingPreview() {
        if (!controller || !controller.applyGameSettingChange
                || !settingPreview.success)
            return
        var result = controller.applyGameSettingChange(
            gameId,
            String(settingPreview.settingInstanceId || ""),
            String(settingPreview.proposedValue || ""))
        if (result && result.success) {
            settingPreview = ({})
            toastRequested(qsTr("Setting change applied and verified"), "success")
            loadProfile()
        } else {
            toastRequested(String(result && result.error || qsTr("Setting change could not be applied")), "error")
        }
    }
    function revertActiveChange() {
        var result = controller.revertOptimizationChange(
            gameId, String(gameAnalysis.appliedChange && gameAnalysis.appliedChange.id || ""))
        if (result && result.success) {
            settingPreview = ({})
            loadProfile()
        } else {
            toastRequested(String(result && result.error || qsTr("Changes could not be reverted")), "error")
        }
    }
    function keepActiveChange() {
        var result = controller.keepOptimizationChange(
            gameId, String(gameAnalysis.appliedChange && gameAnalysis.appliedChange.id || ""))
        if (result && result.success) {
            settingPreview = ({})
            loadProfile()
        } else {
            toastRequested(String(result && result.error || qsTr("Change could not be kept")), "error")
        }
    }

    Timer { id: previewTimer; interval: 150; repeat: false; onTriggered: tab.preview() }

    ScrollView {
        id: optimizationScrollView
        objectName: "optimizationScroll"
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: tab.embedded ? ScrollBar.AlwaysOff : ScrollBar.AsNeeded
        ColumnLayout {
            id: optimizationColumn
            width: parent.width
            spacing: 14

            Loader {
                Layout.fillWidth: true
                active: tab.shows("performance")
                visible: active
                sourceComponent: GpuMemoryCard { controller: tab.controller; gameData: tab.gameData }
            }

            Loader {
                objectName: "performanceCardsLoader"
                Layout.fillWidth: true
                active: tab.shows("performance")
                visible: active
                sourceComponent: Component {
                ColumnLayout {
                spacing: 14
                SurfaceCard {
                    objectName: "optimizationProfileCard"
                    Layout.fillWidth: true; padding: 18
                    contentItem: ColumnLayout {
                        spacing: 10
                        Label { text: qsTr("Profile"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                        GridLayout {
                            Layout.fillWidth: true; columns: tab.width > 950 ? 5 : 2; columnSpacing: 8; rowSpacing: 8
                            Repeater { model: tab.presetLabels; AppButton { required property int index; required property string modelData; Layout.fillWidth: true; text: modelData; kind: tab.preset === tab.presetValues[index] ? "primary" : "secondary"; onClicked: tab.choosePreset(tab.presetValues[index]) } }
                        }
                    }
                }

                GridLayout {
                    objectName: "optimizationGoalAndFps"
                    Layout.fillWidth: true; columns: tab.width > 900 ? 2 : 1; columnSpacing: 12; rowSpacing: 12
                    SurfaceCard {
                        Layout.fillWidth: true; padding: 18
                        contentItem: ColumnLayout {
                            Label { text: qsTr("Game and user goal"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Game category"); description: qsTr("You can always correct this classification manually")
                                AppComboBox { Layout.preferredWidth: 260; model: tab.categoryLabels; currentIndex: tab.indexOf(tab.categoryValues, tab.gameCategory, 6); onActivated: function(index) { tab.gameCategory = tab.categoryValues[index]; tab.preset = "custom"; tab.changed("category") } }
                            }
                            Label { Layout.fillWidth: true; text: qsTr("Classification source: %1, confidence: %2%").arg(String(tab.categoryClassification.source || qsTr("not detected"))).arg(Math.round(Number(tab.categoryClassification.confidence || 0) * 100)); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                            SettingRow { Layout.fillWidth: true; title: qsTr("User goal")
                                AppComboBox { Layout.preferredWidth: 260; model: tab.goalLabels; currentIndex: tab.indexOf(tab.goalValues, tab.userGoal, 1); onActivated: function(index) { tab.userGoal = tab.goalValues[index]; tab.preset = "custom"; tab.changed("goal") } }
                            }
                        }
                    }
                    SurfaceCard {
                        Layout.fillWidth: true; padding: 18
                        contentItem: ColumnLayout {
                            Label { text: qsTr("Monitor and FPS"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                            SettingRow { Layout.fillWidth: true; title: qsTr("Gaming monitor"); description: tab.displays.length ? qsTr("Detected from active Qt screens") : qsTr("No active screen detected")
                                AppComboBox { Layout.preferredWidth: 300; enabled: tab.displays.length > 0; model: tab.displayLabels; currentIndex: tab.indexOf(tab.displayValues, tab.targetDisplayId, 0); onActivated: function(index) { tab.targetDisplayId = tab.displayValues[index]; tab.applyDisplayDefaults(); tab.changed("display") } }
                            }
                            SettingRow { Layout.fillWidth: true; title: tab.gamescopeEnabled ? qsTr("FPS limit") : qsTr("Target FPS"); description: tab.gamescopeEnabled ? qsTr("Enforced by Gamescope") : qsTr("Recommendation target only; the actual limiter is configured below")
                                RowLayout { AppComboBox { property var values: ["automatic", "manual", "unlimited"]; model: [qsTr("Automatic"), qsTr("Manual"), qsTr("Unlimited")]; currentIndex: tab.indexOf(values, tab.targetFpsMode, 0); onActivated: function(index) { tab.targetFpsMode = values[index]; tab.changed("fps") } }
                                    AppTextField { Layout.preferredWidth: 82; enabled: tab.targetFpsMode !== "unlimited"; text: String(tab.targetFps); validator: IntValidator { bottom: 15; top: 1000 } onTextEdited: { tab.targetFps = Number(text || 60); tab.targetFpsMode = "manual"; tab.changed("fps") } }
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: tab.gamescopeEnabled && (tab.nestedRefresh > 0 || tab.framerateLimit > 0)
                                text: qsTr("Advanced Gamescope settings override this target (%1 requested FPS).").arg(tab.framerateLimit || tab.nestedRefresh)
                                color: App.Theme.warning
                                wrapMode: Text.WordWrap
                            }
                            MangoHudFpsLimitEditor {
                                Layout.fillWidth: true
                                visible: !tab.gamescopeEnabled
                                controller: tab.controller
                                gameId: tab.gameId
                                refreshRevision: tab.profileRevision
                            }

                        }
                    }
                }

                SurfaceCard {
                    Layout.fillWidth: true; padding: 18; selected: gameAnalysis.baselineAvailable === true
                    contentItem: ColumnLayout {
                        spacing: 9
                        Label {
                            text: gameAnalysis.baselineAvailable
                                  ? qsTr("Measured optimization recommendations")
                                  : qsTr("Preliminary settings")
                            color: App.Theme.text
                            font.pixelSize: 19
                            font.weight: Font.Bold
                        }
                        StatusBadge { text: App.I18n.message(String(tab.recommendation.status || qsTr("Preliminary recommendation - game measurement required"))); status: tab.recommendation.preliminary === false ? "available" : "warning" }
                        Label { Layout.fillWidth: true; text: qsTr("Target: %1 FPS").arg(Number(tab.recommendation.targetFps || tab.targetFps)); color: App.Theme.accent; font.pixelSize: 17; font.weight: Font.Bold }
                        AppButton { text: qsTr("Why these settings?"); kind: "ghost"; onClicked: tab.recommendationDetailsExpanded = !tab.recommendationDetailsExpanded }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: tab.recommendationDetailsExpanded
                            Repeater { model: tab.recommendation.reasons || []; Label { required property string modelData; Layout.fillWidth: true; text: "• " + App.I18n.message(modelData); color: App.Theme.textSecondary; wrapMode: Text.WordWrap } }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { text: qsTr("Preview changes"); kind: "secondary"; onClicked: tab.preview() }
                            Label { Layout.fillWidth: true; text: tab.recommendationAnalyzed ? qsTr("Exact changes are shown in the launch plan below") : qsTr("Analyze the current profile before applying it"); color: App.Theme.textMuted; wrapMode: Text.WordWrap }
                        }
                    }
                }

                ProtonTweaksSection {
                    objectName: "protonTweaksSection"
                    Layout.fillWidth: true
                    collapsible: true
                    controller: tab.controller
                    gameData: tab.gameData
                }

                }
                }
            }

            SurfaceCard {
                objectName: "gameModeCard"
                visible: tab.shows("performance")
                Layout.fillWidth: true
                padding: 18
                contentItem: ColumnLayout {
                    SettingRow { Layout.fillWidth: true; title: qsTr("GameMode"); description: App.I18n.message(String(tab.gamemodeStatus.message || ""))
                        AppSwitch { enabled: Boolean(tab.gamemodeStatus.available); checked: tab.gamemodeEnabled; onToggled: { tab.gamemodeEnabled = checked; tab.preset = "custom"; tab.changed("gamemode") } }
                    }
                }
            }

            SurfaceCard {
                objectName: "gamescopeSettingsCard"
                visible: tab.shows("performance")
                Layout.fillWidth: true; padding: 18
                contentItem: ColumnLayout {
                    Label { text: "Gamescope"; color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }

                    SettingRow { Layout.fillWidth: true; title: qsTr("Gamescope"); description: App.I18n.message(String(tab.gamescopeStatus.message || ""))
                        AppSwitch { enabled: Boolean(tab.gamescopeStatus.available); checked: tab.gamescopeEnabled; onToggled: { tab.gamescopeEnabled = checked; if (checked && tab.gamescopeMode === "disabled") tab.gamescopeMode = "native"; tab.preset = "custom"; tab.changed("gamescope") } }
                    }
                    SettingRow { Layout.fillWidth: true; title: qsTr("Gamescope mode"); description: tab.renderingSummary
                        AppComboBox { Layout.preferredWidth: 220; enabled: Boolean(tab.gamescopeStatus.available); model: tab.gamescopeModeLabels; currentIndex: tab.indexOf(tab.gamescopeModeValues, tab.gamescopeMode, 0); onActivated: function(index) { tab.applyGamescopeMode(tab.gamescopeModeValues[index]) } }
                    }
                    Label {
                        objectName: "gamescopeAdvancedUnavailable"
                        Layout.fillWidth: true
                        visible: Boolean(tab.gamescopeStatus.available) && !tab.gsAdvanced
                        text: qsTr("Gamescope --help could not be read. Only the basic options are available; advanced options are disabled.")
                        color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption
                    }

                    GsSection {
                        objectName: "gamescopeSectionWindow"
                        enabled: tab.gamescopeEnabled
                        title: qsTr("Window mode and resolution")
                        expanded: true
                        SettingRow { Layout.fillWidth: true; title: qsTr("Window mode"); description: qsTr("Only one mode is used at a time")
                            AppComboBox { objectName: "gamescopeWindowModeCombo"; Layout.preferredWidth: 220; property var values: ["windowed", "borderless", "fullscreen"]; model: [qsTr("Window"), qsTr("Borderless"), qsTr("Fullscreen")]; currentIndex: tab.indexOf(values, tab.gamescopeWindowMode, 2); onActivated: function(index) { tab.gamescopeWindowMode = values[index]; tab.gamescopeFullscreen = values[index] === "fullscreen"; tab.gsChanged() } }
                        }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Game render resolution"); description: qsTr("-w / -h: the size the game renders at")
                            RowLayout {
                                AppComboBox { objectName: "gamescopeInputResolution"; Layout.preferredWidth: 160; model: [qsTr("Automatic")].concat(tab.resolutionValues.slice(1, -1)).concat([qsTr("Custom")]); currentIndex: tab.resolutionIndex(tab.inputAuto, tab.inputCustom, tab.inputWidth, tab.inputHeight); onActivated: function(index) { tab.chooseResolution("input", tab.resolutionValues[index]) } }
                                AppTextField { visible: tab.inputCustom; Layout.preferredWidth: 80; placeholderText: qsTr("Width"); text: String(tab.inputWidth); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { if (acceptableInput) { tab.inputWidth = Number(text); tab.gsChanged() } } }
                                AppTextField { visible: tab.inputCustom; Layout.preferredWidth: 80; placeholderText: qsTr("Height"); text: String(tab.inputHeight); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { if (acceptableInput) { tab.inputHeight = Number(text); tab.gsChanged() } } }
                            }
                        }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Gamescope output resolution"); description: qsTr("-W / -H: the size of the Gamescope window or screen")
                            RowLayout {
                                AppComboBox { objectName: "gamescopeOutputResolution"; Layout.preferredWidth: 160; model: [qsTr("Native / automatic")].concat(tab.resolutionValues.slice(1, -1)).concat([qsTr("Custom")]); currentIndex: tab.resolutionIndex(tab.outputAuto, tab.outputCustom, tab.outputWidth, tab.outputHeight); onActivated: function(index) { tab.chooseResolution("output", tab.resolutionValues[index]) } }
                                AppTextField { visible: tab.outputCustom; Layout.preferredWidth: 80; placeholderText: qsTr("Width"); text: String(tab.outputWidth); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { if (acceptableInput) { tab.outputWidth = Number(text); tab.gsChanged() } } }
                                AppTextField { visible: tab.outputCustom; Layout.preferredWidth: 80; placeholderText: qsTr("Height"); text: String(tab.outputHeight); validator: IntValidator { bottom: 320; top: 16384 } onTextEdited: { if (acceptableInput) { tab.outputHeight = Number(text); tab.gsChanged() } } }
                            }
                        }
                        SettingRow { visible: tab.gsSupports("--display-index"); Layout.fillWidth: true; title: qsTr("Monitor"); description: qsTr("--display-index; Gamescope's numbering may differ from the list of screens")
                            AppComboBox { objectName: "gamescopeDisplayIndex"; Layout.preferredWidth: 260; model: [qsTr("Automatic")].concat(tab.displayLabels.map(function(label, index) { return index + ": " + label })); currentIndex: Math.max(0, tab.displayIndex + 1); onActivated: function(index) { tab.displayIndex = index - 1; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--force-windows-fullscreen"); Layout.fillWidth: true; title: qsTr("Force full-size game window inside Gamescope"); description: "--force-windows-fullscreen"
                            AppSwitch { objectName: "gamescopeForceWindowsFullscreen"; checked: tab.forceWindowsFullscreen; onToggled: { tab.forceWindowsFullscreen = checked; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--backend") && tab.backendValues.length > 1; Layout.fillWidth: true; title: qsTr("Backend"); description: qsTr("Only nested desktop backends listed by the local Gamescope")
                            AppComboBox { objectName: "gamescopeBackend"; Layout.preferredWidth: 220; model: tab.backendValues.map(function(item) { return item === "auto" ? qsTr("Automatic (recommended)") : item === "wayland" ? "Wayland" : "SDL" }); currentIndex: tab.indexOf(tab.backendValues, tab.gamescopeBackend, 0); onActivated: function(index) { tab.gamescopeBackend = tab.backendValues[index]; tab.gsChanged() } }
                        }
                    }

                    GsSection {
                        objectName: "gamescopeSectionScaling"
                        enabled: tab.gamescopeEnabled
                        title: qsTr("Image scaling")
                        expanded: true
                        SettingRow { Layout.fillWidth: true; title: qsTr("Fit method"); description: tab.scalerHint(tab.gamescopeScaler)
                            AppComboBox { objectName: "gamescopeScalerCombo"; Layout.preferredWidth: 220; model: tab.scalerValues.map(function(item) { return item === "auto" ? qsTr("Auto") : item === "fit" ? qsTr("Fit / keep aspect ratio") : item === "fill" ? qsTr("Fill / crop") : item === "stretch" ? qsTr("Stretch") : qsTr("Integer scaling") }); currentIndex: tab.indexOf(tab.scalerValues, tab.gamescopeScaler, 0); onActivated: function(index) { tab.gamescopeScaler = tab.scalerValues[index]; tab.gsChanged() } }
                        }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Filter"); description: tab.filterHint(tab.gamescopeFilter)
                            AppComboBox { objectName: "gamescopeFilterCombo"; Layout.preferredWidth: 220; model: tab.filterValues.map(function(item) { return item === "linear" ? "Linear" : item === "nearest" ? "Nearest" : item.toUpperCase() === "PIXEL" ? "Pixel" : item.toUpperCase() }); currentIndex: tab.indexOf(tab.filterValues, tab.gamescopeFilter, 0); onActivated: function(index) { tab.gamescopeFilter = tab.filterValues[index]; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("-m"); Layout.fillWidth: true; title: qsTr("Maximum scale"); description: "-m / --max-scale"
                            GsOptionalSlider { objectName: "gamescopeMaxScale"; from: 1; to: 8; stepSize: 0.5; decimals: 1; defaultValue: 2; unit: "x"; value: tab.maxScale; onEdited: function(value) { tab.maxScale = value < 0 ? 0 : value; tab.gsChanged() } }
                        }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Sharpness"); description: tab.sharpnessApplies() ? qsTr("0% = least, 100% = most sharpening") : qsTr("Used only by the FSR and NIS filters")
                            GsOptionalSlider { objectName: "gamescopeSharpness"; enabled: tab.sharpnessApplies(); from: 0; to: 100; stepSize: 5; defaultValue: 50; unit: "%"; value: tab.gamescopeSharpnessPercent; onEdited: function(value) { tab.gamescopeSharpnessPercent = Math.round(value); tab.gsChanged() } }
                        }
                        Label { Layout.fillWidth: true; visible: tab.gamescopeEnabled && tab.sameRenderAndOutput(); text: qsTr("Render and output sizes are equal: the filter only changes the image, it does not increase FPS."); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                    }

                    GsSection {
                        objectName: "gamescopeSectionFps"
                        enabled: tab.gamescopeEnabled
                        title: qsTr("Smoothness and frame limit")
                        expanded: true
                        SettingRow { Layout.fillWidth: true; title: qsTr("Virtual refresh rate (Hz)"); description: qsTr("-r: empty uses the FPS target of this profile")
                            AppTextField { objectName: "gamescopeNestedRefresh"; Layout.preferredWidth: 100; placeholderText: qsTr("Automatic"); text: tab.nestedRefresh > 0 ? String(tab.nestedRefresh) : ""; validator: IntValidator { bottom: 1; top: 1000 } onTextEdited: { if (text === "" || acceptableInput) { tab.nestedRefresh = text === "" ? 0 : Number(text); tab.gsChanged() } } }
                        }
                        SettingRow { visible: tab.gsSupports("--framerate-limit"); Layout.fillWidth: true; title: qsTr("Advanced Gamescope limit override"); description: qsTr("Gamescope may round the limit to a divisor of the refresh rate")
                            RowLayout {
                                AppComboBox { objectName: "gamescopeFramerateLimit"; Layout.preferredWidth: 140; model: tab.framerateValues.map(function(item) { return item === 0 ? qsTr("Off") : item < 0 ? qsTr("Custom") : String(item) }); currentIndex: tab.rateIndex(tab.framerateValues, tab.framerateLimit, tab.framerateCustom); onActivated: function(index) { var value = tab.framerateValues[index]; tab.framerateCustom = value < 0; if (value >= 0) tab.framerateLimit = value; tab.gsChanged() } }
                                AppTextField { visible: tab.framerateCustom; Layout.preferredWidth: 80; text: tab.framerateLimit > 0 ? String(tab.framerateLimit) : ""; validator: IntValidator { bottom: 1; top: 1000 } onTextEdited: { if (acceptableInput) { tab.framerateLimit = Number(text); tab.gsChanged() } } }
                            }
                        }
                        SettingRow { visible: tab.gsSupports("-o"); Layout.fillWidth: true; title: qsTr("Limit when unfocused"); description: "-o / --nested-unfocused-refresh"
                            RowLayout {
                                AppComboBox { objectName: "gamescopeUnfocusedRefresh"; Layout.preferredWidth: 140; model: tab.unfocusedValues.map(function(item) { return item === 0 ? qsTr("Off") : item < 0 ? qsTr("Custom") : String(item) }); currentIndex: tab.rateIndex(tab.unfocusedValues, tab.unfocusedRefresh, tab.unfocusedCustom); onActivated: function(index) { var value = tab.unfocusedValues[index]; tab.unfocusedCustom = value < 0; if (value >= 0) tab.unfocusedRefresh = value; tab.gsChanged() } }
                                AppTextField { visible: tab.unfocusedCustom; Layout.preferredWidth: 80; text: tab.unfocusedRefresh > 0 ? String(tab.unfocusedRefresh) : ""; validator: IntValidator { bottom: 1; top: 1000 } onTextEdited: { if (acceptableInput) { tab.unfocusedRefresh = Number(text); tab.gsChanged() } } }
                            }
                        }
                        SettingRow { visible: tab.gsSupports("--adaptive-sync"); Layout.fillWidth: true; title: qsTr("Adaptive Sync / VRR"); description: qsTr("Requires VRR support in the monitor, the driver and the current session")
                            AppSwitch { objectName: "gamescopeAdaptiveSync"; checked: tab.gamescopeAdaptiveSync; onToggled: { tab.gamescopeAdaptiveSync = checked; tab.gsChanged() } }
                        }
                    }

                    GsSection {
                        objectName: "gamescopeSectionHdr"
                        enabled: tab.gamescopeEnabled
                        title: qsTr("HDR and colors")
                        badge: qsTr("Experimental")
                        visible: tab.gsAdvanced
                        Label { Layout.fillWidth: true; text: qsTr("HDR in Gamescope also depends on the Wayland desktop, the monitor, the driver and the game. If colors look washed out, turn HDR/ITM off."); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption }
                        Repeater {
                            model: tab.hdrReadiness.notes || []
                            delegate: Label { required property string modelData; Layout.fillWidth: true; text: "\u2022 " + tab.hdrNote(modelData); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption }
                        }
                        SettingRow { Layout.fillWidth: true; title: qsTr("Enable HDR"); description: "--hdr-enabled"
                            AppSwitch { objectName: "gamescopeHdr"; enabled: tab.gsSupports("--hdr-enabled"); checked: tab.gamescopeHdr; onToggled: { tab.gamescopeHdr = checked; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--hdr-sdr-content-nits"); Layout.fillWidth: true; title: qsTr("SDR content brightness in HDR"); description: "--hdr-sdr-content-nits"
                            GsOptionalSlider { objectName: "gamescopeHdrSdrContentNits"; enabled: tab.gsHdrActive; from: 80; to: 1000; stepSize: 10; defaultValue: 400; unit: " nits"; value: tab.hdrSdrContentNits; onEdited: function(value) { tab.hdrSdrContentNits = value < 0 ? 0 : Math.round(value); tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--sdr-gamut-wideness"); Layout.fillWidth: true; title: qsTr("SDR gamut wideness"); description: "--sdr-gamut-wideness"
                            GsOptionalSlider { objectName: "gamescopeSdrGamutWideness"; enabled: tab.gsHdrActive; from: 0; to: 1; stepSize: 0.05; decimals: 2; defaultValue: 0.5; value: tab.sdrGamutWideness; onEdited: function(value) { tab.sdrGamutWideness = value; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--hdr-itm-enabled") || tab.gsSupports("--hdr-itm-enable"); Layout.fillWidth: true; title: qsTr("Convert SDR games to HDR (inverse tone mapping)"); description: qsTr("Only for SDR games; never applied to native HDR automatically")
                            AppSwitch { objectName: "gamescopeHdrItm"; enabled: tab.gsHdrActive; checked: tab.hdrItm; onToggled: { tab.hdrItm = checked; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--hdr-itm-sdr-nits"); Layout.fillWidth: true; title: qsTr("SDR source brightness"); description: "--hdr-itm-sdr-nits"
                            GsOptionalSlider { objectName: "gamescopeHdrItmSdrNits"; enabled: tab.gsItmActive; from: 80; to: 1000; stepSize: 10; defaultValue: 100; unit: " nits"; value: tab.hdrItmSdrNits; onEdited: function(value) { tab.hdrItmSdrNits = value < 0 ? 0 : Math.round(value); tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--hdr-itm-target-nits"); Layout.fillWidth: true; title: qsTr("Target HDR brightness"); description: "--hdr-itm-target-nits"
                            GsOptionalSlider { objectName: "gamescopeHdrItmTargetNits"; enabled: tab.gsItmActive; from: 200; to: 10000; stepSize: 50; defaultValue: 1000; unit: " nits"; value: tab.hdrItmTargetNits; onEdited: function(value) { tab.hdrItmTargetNits = value < 0 ? 0 : Math.round(value); tab.gsChanged() } }
                        }
                    }

                    GsSection {
                        objectName: "gamescopeSectionInput"
                        enabled: tab.gamescopeEnabled
                        title: qsTr("Input and window behavior")
                        SettingRow { visible: tab.gsSupports("-g"); Layout.fillWidth: true; title: qsTr("Grab the keyboard"); description: "-g / --grab"
                            AppSwitch { objectName: "gamescopeGrabKeyboard"; checked: tab.gamescopeGrabKeyboard; onToggled: { tab.gamescopeGrabKeyboard = checked; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--force-grab-cursor"); Layout.fillWidth: true; title: qsTr("Always grab the cursor"); description: qsTr("You may need Super+G to release the grab")
                            AppSwitch { objectName: "gamescopeForceGrabCursor"; checked: tab.forceGrabCursor; onToggled: { tab.forceGrabCursor = checked; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("-C"); Layout.fillWidth: true; title: qsTr("Hide the cursor after a delay"); description: "-C / --hide-cursor-delay"
                            GsOptionalSlider { objectName: "gamescopeHideCursorDelay"; from: 100; to: 60000; stepSize: 100; defaultValue: 3000; unit: " ms"; value: tab.hideCursorDelay; onEdited: function(value) { tab.hideCursorDelay = value < 0 ? 0 : Math.round(value); tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("-s"); Layout.fillWidth: true; title: qsTr("Mouse sensitivity"); description: "-s / --mouse-sensitivity"
                            GsOptionalSlider { objectName: "gamescopeMouseSensitivity"; from: 0.1; to: 10; stepSize: 0.1; decimals: 1; defaultValue: 1; unit: "x"; value: tab.mouseSensitivity; onEdited: function(value) { tab.mouseSensitivity = value < 0 ? 0 : value; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("--expose-wayland"); Layout.fillWidth: true; title: qsTr("Native Wayland clients"); description: "--expose-wayland"
                            AppSwitch { objectName: "gamescopeExposeWayland"; checked: tab.exposeWayland; onToggled: { tab.exposeWayland = checked; tab.gsChanged() } }
                        }
                        SettingRow { visible: tab.gsSupports("-e"); Layout.fillWidth: true; title: qsTr("Steam integration (advanced)"); description: qsTr("-e / --steam; never enabled automatically, intended for Steam games")
                            AppSwitch { objectName: "gamescopeSteamIntegration"; checked: tab.steamIntegration; onToggled: { tab.steamIntegration = checked; tab.gsChanged() } }
                        }
                    }

                    GsSection {
                        objectName: "gamescopeSectionDiagnostics"
                        enabled: tab.gamescopeEnabled
                        title: qsTr("Integrations and diagnostics")
                        RowLayout {
                            Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: qsTr("Gamescope %1").arg(String(tab.gamescopeStatus.version || "-")); color: App.Theme.textSecondary }
                            StatusBadge { objectName: "gamescopeAvailabilityBadge"; text: tab.gamescopeStatus.available ? qsTr("Available") : qsTr("Missing"); status: tab.gamescopeStatus.available ? "available" : "warning" }
                            AppButton { objectName: "refreshGamescopeDetection"; compact: true; kind: "secondary"; text: qsTr("Refresh detection"); onClicked: tab.refreshGamescope() }
                        }
                        Label { Layout.fillWidth: true; visible: String(tab.launchPlan.mangoHudActivationOwner || "") === "mangoapp"; text: qsTr("MangoHud is drawn by Gamescope (--mangoapp) with this game's MangoHud profile; the game gets no second overlay."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { objectName: "gamescopeArgumentsToggle"; Layout.fillWidth: true; compact: true; kind: "secondary"; text: (tab.gamescopeArgumentsExpanded ? "\u25BE " : "\u25B8 ") + qsTr("Generated Gamescope arguments"); onClicked: tab.gamescopeArgumentsExpanded = !tab.gamescopeArgumentsExpanded }
                            AppButton { objectName: "copyGamescopeLaunchOptions"; compact: true; kind: "secondary"; enabled: tab.gamescopeSteamLaunchOptions.length > 0; text: qsTr("Copy Steam launch options"); onClicked: tab.copyGamescopeLaunchOptions() }
                        }
                        Rectangle {
                            visible: tab.gamescopeArgumentsExpanded
                            Layout.fillWidth: true; Layout.preferredHeight: 96; radius: App.Theme.radiusMedium; color: App.Theme.input; clip: true
                            ScrollView { anchors.fill: parent; anchors.margins: 10; contentWidth: availableWidth
                                TextEdit { id: gamescopeLaunchOptionsText; objectName: "gamescopeArgumentsText"; width: parent.width; readOnly: true; selectByMouse: true; text: tab.gamescopeSteamLaunchOptions; color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                            }
                        }
                        Label { Layout.fillWidth: true; visible: tab.gamescopeArgumentsExpanded; text: qsTr("Display only. Game launches always use a safe argument list, never this text."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { objectName: "startGamescopeTest"; kind: "secondary"; text: qsTr("Test Gamescope"); enabled: Boolean(tab.gamescopeStatus.available) && !tab.gamescopeTestRunning; onClicked: tab.startGamescopeTest(false) }
                            AppButton { objectName: "startGamescopeHdrTest"; visible: tab.gsHdrActive; kind: "secondary"; text: qsTr("Test with HDR"); enabled: Boolean(tab.gamescopeStatus.available) && !tab.gamescopeTestRunning; onClicked: tab.startGamescopeTest(true) }
                            AppButton { objectName: "stopGamescopeTest"; visible: tab.gamescopeTestRunning; kind: "secondary"; text: qsTr("Close test"); onClicked: if (tab.controller && tab.controller.stopGamescopeTest) tab.controller.stopGamescopeTest() }
                            Item { Layout.fillWidth: true }
                            AppButton { objectName: "resetGamescopeSettings"; kind: "secondary"; text: qsTr("Restore Gamescope settings"); onClicked: tab.resetGamescope() }
                        }
                        Label { Layout.fillWidth: true; text: qsTr("Runs gamescope with the current settings and vkcube. Nothing is saved; HDR is used only in the HDR test."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: App.Theme.fontCaption }
                        Label { objectName: "gamescopeTestMessage"; Layout.fillWidth: true; visible: tab.gamescopeTestMessage.length > 0; text: tab.gamescopeTestMessage; color: App.Theme.danger; wrapMode: Text.WordWrap }
                    }
                }
            }

            Loader {
                objectName: "measuredOptimizationCardsLoader"
                Layout.fillWidth: true
                active: tab.shows("performance")
                visible: active
                sourceComponent: Component {
                    ColumnLayout {
                        spacing: 14
                SurfaceCard {
                    id: automaticOptimizationCard
                    objectName: "automaticOptimizationCard"
                    visible: gameAnalysis.status === "completed"
                    Layout.fillWidth: true
                    padding: 18
                    selected: String(tab.automaticSession().state || "") === "candidate_applied"
                              || String(tab.automaticSession().state || "") === "comparison_recording"
                              || String(tab.automaticSession().state || "") === "result_ready"
                    contentItem: ColumnLayout {
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Automatic Optimization")
                                color: App.Theme.text
                                font.pixelSize: 18
                                font.weight: Font.Bold
                            }
                            StatusBadge {
                                text: tab.automaticSession().state === "candidate_applied"
                                      ? qsTr("Applied - waiting for comparison")
                                      : tab.automaticSession().state === "comparison_recording"
                                        ? qsTr("Comparison recording")
                                      : tab.automaticSession().state === "result_ready"
                                        ? qsTr("Result ready")
                                      : tab.automaticSession().state === "kept"
                                        ? qsTr("Kept")
                                      : tab.automaticSession().state === "reverted"
                                        ? qsTr("Reverted")
                                      : gameAnalysis.baselineAvailable
                                        ? qsTr("Ready") : qsTr("Baseline required")
                                status: tab.automaticSession().state === "candidate_applied"
                                        || tab.automaticSession().state === "comparison_recording"
                                        ? "warning"
                                        : tab.automaticSession().state === "result_ready"
                                          || tab.automaticSession().state === "kept"
                                          ? "available" : "neutral"
                            }
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: tab.width > 880 ? 3 : 1
                            columnSpacing: 12
                            rowSpacing: 8
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Measured problem")
                                value: tab.automaticProblemLabel()
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Confidence")
                                value: Math.round(Number(tab.automaticState().problem
                                                         && tab.automaticState().problem.confidence || 0) * 100) + "%"
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Target")
                                value: App.I18n.analysisMessage(tab.automaticState().problem
                                                               && tab.automaticState().problem.target || "")
                            }
                            LabeledValue {
                                visible: String(tab.automaticState().noOpOutcome || "").length > 0
                                Layout.fillWidth: true
                                label: qsTr("Result")
                                value: tab.automaticOutcomeLabel(
                                           tab.automaticState().noOpOutcome)
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: App.I18n.analysisMessage(tab.automaticState().message || "")
                            color: tab.automaticState().canStart
                                   ? App.Theme.textSecondary : App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            visible: Boolean(tab.automaticState().canStart)
                                     && String(tab.automaticSession().state || "") !== "candidate_applied"
                                     && String(tab.automaticSession().state || "") !== "comparison_recording"
                                     && String(tab.automaticSession().state || "") !== "result_ready"
                            text: qsTr("Available experiments: %1").arg(
                                  Number(tab.automaticState().availableCount || 0))
                            color: App.Theme.text
                            font.weight: Font.Bold
                        }
                        Repeater {
                            model: tab.automaticPreviewOpen
                                   ? (tab.automaticState().availableCandidates || []) : []
                            SurfaceCard {
                                id: automaticCandidateCard
                                objectName: "automaticRuntimeCandidate"
                                required property var modelData
                                Layout.fillWidth: true
                                padding: 13
                                contentItem: ColumnLayout {
                                    spacing: 6
                                    Label {
                                        text: App.I18n.analysisMessage(automaticCandidateCard.modelData.name || "")
                                        color: App.Theme.text
                                        font.weight: Font.Bold
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: qsTr("Goal: %1").arg(
                                              App.I18n.analysisMessage(automaticCandidateCard.modelData.expectedGoal || ""))
                                        color: App.Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: qsTr("Risk: %1").arg(
                                              App.I18n.analysisMessage(automaticCandidateCard.modelData.risk || ""))
                                        color: App.Theme.warning
                                        wrapMode: Text.WordWrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: qsTr("Image quality impact: %1").arg(
                                              App.I18n.analysisMessage(automaticCandidateCard.modelData.qualityImpact || ""))
                                        color: App.Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: qsTr("Activation must be verified in the measured runner plan before the result is judged.")
                                        color: App.Theme.textMuted
                                        wrapMode: Text.WordWrap
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Item { Layout.fillWidth: true }
                                        AppButton {
                                            objectName: "startAutomaticOptimizationButton"
                                            text: qsTr("Apply optimization")
                                            kind: "primary"
                                            onClicked: tab.startAutomatic(
                                                String(automaticCandidateCard.modelData.id || ""))
                                        }
                                    }
                                }
                            }
                        }
                        RowLayout {
                            visible: Boolean(tab.automaticState().canStart)
                                     && String(tab.automaticSession().state || "") !== "candidate_applied"
                                     && String(tab.automaticSession().state || "") !== "comparison_recording"
                                     && String(tab.automaticSession().state || "") !== "result_ready"
                            Layout.fillWidth: true
                            AppButton {
                                objectName: "previewAutomaticPlanButton"
                                text: tab.automaticPreviewOpen
                                      ? qsTr("Hide plan") : qsTr("Preview plan")
                                kind: "secondary"
                                onClicked: tab.automaticPreviewOpen = !tab.automaticPreviewOpen
                            }
                            Item { Layout.fillWidth: true }
                        }
                        SurfaceCard {
                            visible: String(tab.automaticSession().state || "") === "candidate_applied"
                                     || String(tab.automaticSession().state || "") === "comparison_recording"
                            Layout.fillWidth: true
                            padding: 13
                            selected: true
                            contentItem: ColumnLayout {
                                spacing: 7
                                Label {
                                    text: qsTr("Optimization test in progress")
                                    color: App.Theme.warning
                                    font.weight: Font.Bold
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: App.I18n.analysisMessage(tab.automaticSession().candidate
                                                                  && tab.automaticSession().candidate.name || "")
                                    color: App.Theme.text
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: tab.automaticSession().state === "comparison_recording"
                                          ? qsTr("The comparison session is running or being processed.")
                                          : qsTr("The change is applied. Launch and record a representative comparison before deciding whether to keep it.")
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    AppButton {
                                        objectName: "automaticRecordComparisonButton"
                                        text: qsTr("Launch and record comparison")
                                        kind: "primary"
                                        enabled: tab.automaticSession().state === "candidate_applied"
                                                 || ["failed", "recorded_unrepresentative", "completed"].indexOf(tab.baselineStatus()) >= 0
                                        onClicked: tab.recordComparison()
                                    }
                                    AppButton {
                                        text: qsTr("Revert")
                                        kind: "secondary"
                                        onClicked: tab.revertActiveChange()
                                    }
                                }
                            }
                        }
                        SurfaceCard {
                            visible: String(tab.automaticSession().state || "") === "result_ready"
                            Layout.fillWidth: true
                            padding: 13
                            selected: true
                            contentItem: ColumnLayout {
                                spacing: 7
                                Label {
                                    text: qsTr("Measured result: %1").arg(
                                          tab.automaticOutcomeLabel(tab.automaticSession().result
                                                                    && tab.automaticSession().result.outcome))
                                    color: tab.automaticSession().result
                                           && tab.automaticSession().result.recommendRevert
                                           ? App.Theme.danger : App.Theme.text
                                    font.weight: Font.Bold
                                }
                                Repeater {
                                    model: tab.automaticSession().result
                                           && tab.automaticSession().result.evidence || []
                                    Label {
                                        required property string modelData
                                        Layout.fillWidth: true
                                        text: "• " + App.I18n.analysisMessage(modelData)
                                        color: App.Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: App.I18n.analysisMessage(tab.automaticSession().result
                                                                  && tab.automaticSession().result.activationReason || "")
                                    color: tab.automaticSession().result
                                           && tab.automaticSession().result.activationVerified
                                           ? App.Theme.success : App.Theme.warning
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    AppButton {
                                        visible: Boolean(tab.automaticSession().result)
                                                 && tab.automaticSession().result.outcome === "insufficient_data"
                                        text: qsTr("Retry comparison")
                                        kind: "primary"
                                        onClicked: tab.recordComparison()
                                    }
                                    AppButton {
                                        visible: Boolean(tab.automaticSession().result)
                                                 && tab.automaticSession().result.outcome !== "insufficient_data"
                                        text: tab.automaticSession().result
                                              && tab.automaticSession().result.recommendKeep
                                              ? qsTr("Keep recommended") : qsTr("Keep anyway")
                                        kind: tab.automaticSession().result
                                              && tab.automaticSession().result.recommendKeep
                                              ? "primary" : "secondary"
                                        onClicked: tab.keepActiveChange()
                                    }
                                    AppButton {
                                        text: tab.automaticSession().result
                                              && tab.automaticSession().result.recommendRevert
                                              ? qsTr("Revert recommended") : qsTr("Revert")
                                        kind: tab.automaticSession().result
                                              && tab.automaticSession().result.recommendRevert
                                              ? "danger" : "secondary"
                                        onClicked: tab.revertActiveChange()
                                    }
                                }
                            }
                        }
                        ColumnLayout {
                            visible: (tab.automaticState().history || []).length > 0
                            Layout.fillWidth: true
                            spacing: 5
                            Label {
                                text: qsTr("Optimization history")
                                color: App.Theme.text
                                font.weight: Font.Bold
                            }
                            Repeater {
                                model: tab.automaticState().history || []
                                Label {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    text: qsTr("%1 - %2 - %3").arg(
                                          App.I18n.analysisMessage(modelData.candidateName || modelData.candidateId || ""))
                                          .arg(tab.automaticOutcomeLabel(modelData.outcome || ""))
                                          .arg(App.I18n.status(modelData.action || "pending"))
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                }

                SurfaceCard {
                    visible: gameAnalysis.status === "completed"
                    Layout.fillWidth: true
                    padding: 18
                    contentItem: ColumnLayout {
                        spacing: 10
                        Label {
                            text: qsTr("Recommendations")
                            color: App.Theme.text
                            font.pixelSize: 18
                            font.weight: Font.Bold
                        }
                        Label {
                            objectName: "emptyOptimizationRecommendation"
                            visible: gameAnalysis.noSafeRecommendations === true
                            Layout.fillWidth: true
                            text: tab.emptyRecommendationMessage()
                            color: App.Theme.textSecondary
                            wrapMode: Text.WordWrap
                        }
                        Repeater {
                            model: gameAnalysis.candidates || []
                            SurfaceCard {
                                id: candidateCard
                                objectName: "optimizationCandidateCard"
                                required property var modelData
                                property bool previewOpen: false
                                Layout.fillWidth: true
                                padding: 13
                                contentItem: ColumnLayout {
                                    spacing: 6
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            text: App.I18n.analysisMessage(modelData.mechanism || "")
                                            color: App.Theme.text
                                            font.weight: Font.Bold
                                        }
                                        StatusBadge {
                                            text: modelData.reversible ? qsTr("Reversible") : qsTr("Manual rollback")
                                            status: modelData.reversible ? "available" : "warning"
                                        }
                                    }
                                    Label { Layout.fillWidth: true; text: qsTr("Why: %1").arg(App.I18n.joinAnalysis(modelData.evidence || [], "; ")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                    ColumnLayout {
                                        visible: candidateCard.previewOpen
                                        Layout.fillWidth: true
                                        spacing: 5
                                        Label { Layout.fillWidth: true; text: qsTr("Change: %1 → %2").arg(String(modelData.currentValue || "")).arg(String(modelData.proposedValue || "")); color: App.Theme.accent; wrapMode: Text.WordWrap }
                                        Label { Layout.fillWidth: true; text: qsTr("Expected effect: %1").arg(App.I18n.analysisMessage(modelData.expectedEffect || "")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                        Label { Layout.fillWidth: true; text: qsTr("Performance impact: %1").arg(App.I18n.status(modelData.performanceImpact || "unknown")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                        Label { Layout.fillWidth: true; text: qsTr("Quality impact: %1").arg(App.I18n.status(modelData.qualityImpact || "unknown")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                                        Label { Layout.fillWidth: true; text: qsTr("Confidence: %1").arg(App.I18n.status(modelData.confidenceLabel || "unknown")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                        Label { Layout.fillWidth: true; text: qsTr("Risk: %1").arg(App.I18n.analysisMessage(modelData.risk || "")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                                        Label { Layout.fillWidth: true; visible: (modelData.filesToModify || []).length > 0; text: qsTr("File: %1").arg((modelData.filesToModify || []).join(", ")); color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere }
                                        Label { Layout.fillWidth: true; visible: (modelData.filesToModify || []).length > 0; text: qsTr("A verified backup and deterministic revert are available."); color: App.Theme.success; wrapMode: Text.WordWrap }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Item { Layout.fillWidth: true }
                                        AppButton {
                                            objectName: "optimizationCandidatePreviewButton"
                                            text: candidateCard.previewOpen ? qsTr("Hide preview") : qsTr("Preview")
                                            kind: "secondary"
                                            onClicked: candidateCard.previewOpen = !candidateCard.previewOpen
                                        }
                                        AppButton {
                                            objectName: "optimizationCandidateApplyButton"
                                            visible: candidateCard.previewOpen
                                            text: qsTr("Apply and test")
                                            kind: "primary"
                                            onClicked: {
                                                var result = tab.controller.applyOptimizationCandidate(tab.gameId, String(modelData.id || ""))
                                                if (result && result.success)
                                                {
                                                    tab.toastRequested(qsTr("Optimization applied"), "success")
                                                    tab.loadProfile()
                                                }
                                                else
                                                    tab.toastRequested(String(result && result.error || qsTr("Optimization could not be applied")), "error")
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        RowLayout {
                            visible: Boolean(gameAnalysis.appliedChange
                                             && gameAnalysis.appliedChange.state === "applied"
                                             && !gameAnalysis.appliedChange.config_adapter
                                             && !tab.automaticSession().id)
                            Layout.fillWidth: true
                            AppButton {
                                text: qsTr("Record comparison")
                                kind: "primary"
                                onClicked: tab.recordComparison()
                            }
                            AppButton {
                                visible: String(gameAnalysis.comparison
                                                && gameAnalysis.comparison.outcome || "").length > 0
                                         && String(gameAnalysis.comparison.outcome) !== "insufficient_data"
                                text: qsTr("Keep change")
                                kind: "primary"
                                onClicked: {
                                    var result = tab.controller.keepOptimizationChange(
                                        tab.gameId, String(gameAnalysis.appliedChange.id || ""))
                                    if (result && result.success)
                                        tab.loadProfile()
                                    else
                                        tab.toastRequested(String(result && result.error || qsTr("Change could not be kept")), "error")
                                }
                            }
                            AppButton {
                                visible: String(gameAnalysis.appliedChange && gameAnalysis.appliedChange.id || "").length > 0
                                text: qsTr("Revert changes")
                                kind: gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert ? "danger" : "secondary"
                                onClicked: {
                                    var result = tab.controller.revertOptimizationChange(
                                        tab.gameId, String(gameAnalysis.appliedChange.id || ""))
                                    if (result && result.success)
                                        tab.loadProfile()
                                    else
                                        tab.toastRequested(String(result && result.error || qsTr("Changes could not be reverted")), "error")
                                }
                            }
                        }
                        Label {
                            visible: Boolean(gameAnalysis.appliedChange
                                             && gameAnalysis.appliedChange.state === "applied")
                                     && !gameAnalysis.appliedChange.config_adapter
                                     && !tab.automaticSession().id
                                     && String(gameAnalysis.comparison
                                               && gameAnalysis.comparison.outcome || "").length === 0
                            Layout.fillWidth: true
                            text: qsTr("Pending comparison measurement - record a representative comparison before keeping the change.")
                            color: App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                        SurfaceCard {
                            visible: String(gameAnalysis.comparison && gameAnalysis.comparison.outcome || "").length > 0
                            Layout.fillWidth: true
                            padding: 12
                            selected: Boolean(gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert)
                            contentItem: ColumnLayout {
                                Label {
                                    text: qsTr("Before / After: %1").arg(tab.comparisonOutcome())
                                    color: gameAnalysis.comparison && gameAnalysis.comparison.recommendRevert ? App.Theme.danger : App.Theme.text
                                    font.weight: Font.Bold
                                }
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 4
                                    columnSpacing: 10
                                    Label { text: qsTr("Metric"); color: App.Theme.textMuted }
                                    Label { text: qsTr("Before"); color: App.Theme.textMuted }
                                    Label { text: qsTr("After"); color: App.Theme.textMuted }
                                    Label { text: qsTr("Change"); color: App.Theme.textMuted }
                                    Label { text: qsTr("Average FPS"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "averageFps", 1, " FPS"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "averageFps", 1, " FPS"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("averageFps"); color: App.Theme.text }
                                    Label { text: qsTr("1% low"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "onePercentLowFps", 1, " FPS"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "onePercentLowFps", 1, " FPS"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("onePercentLowFps"); color: App.Theme.text }
                                    Label { text: qsTr("Frametime"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "averageFrametimeMs", 2, " ms"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "averageFrametimeMs", 2, " ms"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("averageFrametimeMs"); color: App.Theme.text }
                                    Label { text: qsTr("Frametime p95"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "p95FrametimeMs", 2, " ms"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "p95FrametimeMs", 2, " ms"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("p95FrametimeMs"); color: App.Theme.text }
                                    Label { text: qsTr("Frametime p99"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "p99FrametimeMs", 2, " ms"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "p99FrametimeMs", 2, " ms"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("p99FrametimeMs"); color: App.Theme.text }
                                    Label { text: qsTr("GPU usage"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "gpuUsagePercent", 1, "%"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "gpuUsagePercent", 1, "%"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("gpuUsagePercent"); color: App.Theme.text }
                                    Label { text: qsTr("CPU usage"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "cpuUsagePercent", 1, "%"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "cpuUsagePercent", 1, "%"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("cpuUsagePercent"); color: App.Theme.text }
                                    Label { text: qsTr("VRAM usage"); color: App.Theme.textSecondary }
                                    Label { text: tab.comparisonValue("beforeMeasurement", "vramUsedMb", 0, " MiB"); color: App.Theme.text }
                                    Label { text: tab.comparisonValue("afterMeasurement", "vramUsedMb", 0, " MiB"); color: App.Theme.text }
                                    Label { text: tab.comparisonDelta("vramUsedMb"); color: App.Theme.text }
                                }
                                Repeater {
                                    model: gameAnalysis.comparison && gameAnalysis.comparison.evidence || []
                                    Label { required property string modelData; Layout.fillWidth: true; text: "• " + App.I18n.analysisMessage(modelData); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                }
                            }
                        }
                    }
                }









                    }
                }
            }

            Loader {
                objectName: "launchPlanLoader"
                Layout.fillWidth: true
                active: tab.shows("performance")
                visible: active
                sourceComponent: Component {
                ColumnLayout {
                spacing: 14
                SurfaceCard {
                    Layout.fillWidth: true; padding: 18; selected: true
                    contentItem: ColumnLayout {
                        Label { text: qsTr("Launch plan preview"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                        Label { Layout.fillWidth: true; text: tab.renderingSummary; visible: tab.gamescopeEnabled; color: App.Theme.warning; wrapMode: Text.WordWrap }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 110; radius: App.Theme.radiusMedium; color: App.Theme.input; clip: true
                            ScrollView { anchors.fill: parent; anchors.margins: 10; contentWidth: availableWidth
                                TextEdit { objectName: "launchPreviewText"; width: parent.width; readOnly: true; selectByMouse: true; text: tab.launchPlanText; color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                            }
                        }
                        Label { Layout.fillWidth: true; text: qsTr("Wrapper order: %1").arg((tab.launchPlan.wrappers || []).join(" → ") || qsTr("none")); color: App.Theme.textSecondary }
                        Label { objectName: "fpsLimitOwnerLabel"; Layout.fillWidth: true; text: qsTr("FPS limit owner: %1").arg(tab.fpsOwnerLabel()); color: App.Theme.textSecondary; font.weight: Font.DemiBold }
                        Label { Layout.fillWidth: true; text: qsTr("Final runtime environment: %1").arg(tab.protonOverrides.length ? tab.protonOverrides.join("; ") : qsTr("none")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                        Label { Layout.fillWidth: true; visible: (tab.launchPlan.warnings || []).length > 0; text: qsTr("Warnings: %1").arg((tab.launchPlan.warnings || []).join("; ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                        Label { Layout.fillWidth: true; visible: (tab.launchPlan.environmentConflicts || []).length > 0; text: qsTr("Environment conflicts: %1").arg((tab.launchPlan.environmentConflicts || []).join(", ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                        Label { Layout.fillWidth: true; visible: (tab.presetPlan.sources || []).length > 0; text: qsTr("Decision sources: %1").arg((tab.presetPlan.sources || []).join(", ")); color: App.Theme.textMuted; wrapMode: Text.WordWrap }
                        Label { Layout.fillWidth: true; visible: (tab.presetPlan.conflicts || []).length > 0; text: qsTr("Preset constraints: %1").arg((tab.presetPlan.conflicts || []).join("; ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                    }
                }

                }
                }
            }

            Loader {
                objectName: "diagnosticsCardsLoader"
                Layout.fillWidth: true
                active: tab.shows("diagnostics")
                visible: active
                sourceComponent: Component {
                ColumnLayout {
                spacing: 14
                SurfaceCard {
                    Layout.fillWidth: true
                    padding: 18
                    selected: gameAnalysis.status === "completed"
                    contentItem: ColumnLayout {
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Detected game")
                                color: App.Theme.text
                                font.pixelSize: 19
                                font.weight: Font.Bold
                            }
                            StatusBadge {
                                text: gameAnalysis.status === "running"
                                      ? qsTr("Analyzing")
                                      : gameAnalysis.analysisCacheState === "stale"
                                        ? qsTr("Stale analysis")
                                      : gameAnalysis.analysisCacheState === "cached"
                                        ? qsTr("Cached analysis")
                                      : gameAnalysis.status === "completed"
                                        ? qsTr("Analyzed")
                                        : gameAnalysis.status === "failed"
                                          ? qsTr("Analysis failed")
                                          : qsTr("Not analyzed")
                                status: gameAnalysis.status === "running" ? "analyzing"
                                        : gameAnalysis.analysisCacheState === "stale" ? "warning"
                                        : gameAnalysis.status === "completed" ? "available"
                                        : gameAnalysis.status === "failed" ? "failed" : "not checked"
                            }
                            AppButton {
                                objectName: "analyzeGameOptimizationButton"
                                text: gameAnalysis.status === "running"
                                      ? qsTr("Analyzing…")
                                      : gameAnalysis.status === "completed"
                                        ? qsTr("Re-analyze") : qsTr("Analyze Game")
                                kind: "primary"
                                busy: gameAnalysis.status === "running"
                                enabled: gameAnalysis.status !== "running"
                                onClicked: tab.analyzeGame()
                            }
                        }
                        Label {
                            readonly property var reasons: tab.gameAnalysis.staleReasons || []
                            visible: reasons.length > 0
                            Layout.fillWidth: true
                            text: qsTr("Saved analysis requires refresh: %1").arg(
                                      reasons.length > 0
                                      ? App.I18n.joinAnalysis(reasons, "; ") : "")
                            color: App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                        GridLayout {
                            visible: gameAnalysis.status === "completed"
                            Layout.fillWidth: true
                            columns: tab.width > 1120 ? 3 : 2
                            columnSpacing: 12
                            rowSpacing: 8
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Engine")
                                value: String(gameAnalysis.fingerprint
                                              && gameAnalysis.fingerprint.engine
                                              && gameAnalysis.fingerprint.engine.value || qsTr("Unknown"))
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Graphics API")
                                value: String(gameAnalysis.fingerprint
                                              && gameAnalysis.fingerprint.graphicsApi
                                              && gameAnalysis.fingerprint.graphicsApi.value || qsTr("Unknown"))
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Available graphics APIs")
                                value: gameAnalysis.fingerprint
                                       && gameAnalysis.fingerprint.availableGraphicsApis
                                       && gameAnalysis.fingerprint.availableGraphicsApis.length
                                       ? gameAnalysis.fingerprint.availableGraphicsApis.map(function(api) { return api.value }).join(", ")
                                       : qsTr("Unknown")
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Runtime")
                                value: App.I18n.analysisMessage(gameAnalysis.fingerprint
                                                               && gameAnalysis.fingerprint.runtime
                                                               && gameAnalysis.fingerprint.runtime.value || "Unknown")
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Architecture")
                                value: String(gameAnalysis.fingerprint
                                              && gameAnalysis.fingerprint.architecture
                                              && gameAnalysis.fingerprint.architecture.value || qsTr("Unknown"))
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Category")
                                value: App.I18n.optimizationCategory(gameAnalysis.fingerprint
                                                                    && gameAnalysis.fingerprint.category
                                                                    && gameAnalysis.fingerprint.category.value || "unknown")
                            }
                            LabeledValue {
                                Layout.fillWidth: true
                                label: qsTr("Executable")
                                value: String(gameAnalysis.fingerprint
                                              && gameAnalysis.fingerprint.mainExecutable || qsTr("Unknown"))
                            }
                        }
                        Label {
                            visible: gameAnalysis.status === "completed"
                            Layout.fillWidth: true
                            text: qsTr("Engine confidence: %1% - source: %2")
                                  .arg(Math.round(Number(gameAnalysis.fingerprint
                                                         && gameAnalysis.fingerprint.engine
                                                         && gameAnalysis.fingerprint.engine.confidence || 0) * 100))
                                  .arg(App.I18n.analysisMessage(gameAnalysis.fingerprint
                                                               && gameAnalysis.fingerprint.engine
                                                               && gameAnalysis.fingerprint.engine.source || "not detected"))
                            color: App.Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            visible: gameAnalysis.status === "completed"
                            Layout.fillWidth: true
                            text: qsTr("Category source: %1 - confidence: %2%")
                                  .arg(App.I18n.analysisMessage(gameAnalysis.fingerprint
                                                               && gameAnalysis.fingerprint.category
                                                               && gameAnalysis.fingerprint.category.source || "not detected"))
                                  .arg(Math.round(Number(gameAnalysis.fingerprint
                                                         && gameAnalysis.fingerprint.category
                                                         && gameAnalysis.fingerprint.category.confidence || 0) * 100))
                            color: App.Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            visible: gameAnalysis.status === "completed"
                            text: qsTr("System snapshot")
                            color: App.Theme.text
                            font.weight: Font.Bold
                        }
                        GridLayout {
                            visible: gameAnalysis.status === "completed"
                            Layout.fillWidth: true
                            columns: tab.width > 1120 ? 3 : 2
                            columnSpacing: 12
                            rowSpacing: 8
                            LabeledValue { Layout.fillWidth: true; label: qsTr("CPU"); value: String(gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.cpu || qsTr("Unknown")) }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("GPU"); value: String(gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.gpu || qsTr("Unknown")) }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("RAM"); value: gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.ramGb !== null ? Number(gameAnalysis.fingerprint.system.ramGb).toFixed(1) + " GiB" : qsTr("Unknown") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("VRAM"); value: gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.vramGb !== null ? Number(gameAnalysis.fingerprint.system.vramGb).toFixed(1) + " GiB" : qsTr("Unknown") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Display"); value: String(gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.displayName || qsTr("Unknown")) }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Resolution"); value: gameAnalysis.fingerprint && gameAnalysis.fingerprint.system && gameAnalysis.fingerprint.system.resolutionWidth ? String(gameAnalysis.fingerprint.system.resolutionWidth) + "×" + String(gameAnalysis.fingerprint.system.resolutionHeight) + " @ " + Number(gameAnalysis.fingerprint.system.refreshRate || 0).toFixed(0) + " Hz" : qsTr("Unknown") }
                        }
                        Label {
                            visible: gameAnalysis.status === "failed"
                            Layout.fillWidth: true
                            text: String(gameAnalysis.error || qsTr("Game analysis failed"))
                            color: App.Theme.danger
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                SurfaceCard {
                    visible: gameAnalysis.status === "completed"
                    Layout.fillWidth: true
                    padding: 18
                    contentItem: ColumnLayout {
                        spacing: 10
                        Label {
                            text: qsTr("Performance analysis")
                            color: App.Theme.text
                            font.pixelSize: 18
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Measure FPS, frametimes and resource usage during gameplay, then compare runs. This records performance data, not video.")
                            color: App.Theme.textSecondary
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            Layout.fillWidth: true
                            text: gameAnalysis.baselineAvailable
                                  ? (gameAnalysis.baselineStale
                                     ? qsTr("Saved baseline - new measurement required before measured Automatic recommendations")
                                     : gameAnalysis.analysisCacheState === "cached"
                                       ? qsTr("Saved baseline") : qsTr("Baseline recorded"))
                                  : tab.baselineStatus() === "waiting_for_steam" ? qsTr("Waiting for Steam...")
                                  : tab.baselineStatus() === "waiting_for_runner" ? qsTr("Waiting for runner...")
                                  : tab.baselineStatus() === "waiting_for_launcher" ? qsTr("Waiting for the game to start in %1...").arg(String(tab.gameData.launcher || ""))
                                  : tab.baselineStatus() === "recording" ? qsTr("Recording...")
                                  : tab.baselineStatus() === "waiting_for_game_exit" ? qsTr("Waiting for game exit...")
                                  : tab.baselineStatus() === "processing" ? qsTr("Processing MangoHud log...")
                                  : tab.baselineStatus() === "recorded_unrepresentative"
                                    ? qsTr("Baseline recorded, but the measurement was not representative enough for automatic optimization. Repeat the test during representative gameplay.")
                                  : tab.baselineStatus() === "failed"
                                    ? qsTr("Baseline recording failed: %1").arg(gameAnalysis.baselineSession.error ? App.I18n.message(String(gameAnalysis.baselineSession.error)) : qsTr("No usable log was produced"))
                                    : qsTr("Baseline: Not recorded")
                            color: gameAnalysis.baselineStale ? App.Theme.warning
                                   : gameAnalysis.baselineAvailable ? App.Theme.success
                                   : tab.baselineStatus() === "recorded_unrepresentative" ? App.Theme.warning
                                   : tab.baselineStatus() === "failed" ? App.Theme.danger : App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                objectName: "recordOptimizationBaselineButton"
                                text: tab.measurementActive()
                                      ? qsTr("Recording...") : qsTr("Record baseline")
                                kind: "primary"
                                enabled: !tab.measurementActive()
                                         && tab.baselineStatus() !== "processing"
                                         && !tab.pendingOptimizationTest()
                                         && !tab.lutrisGame
                                toolTip: tab.lutrisGame
                                         ? qsTr("Automatic recording is not available for Lutris games yet")
                                         : tab.launcherGame
                                           ? qsTr("Starts the game through %1 and records until you exit the game").arg(String(tab.gameData.launcher || ""))
                                           : ""
                                onClicked: tab.recordBaseline()
                            }
                            AppButton {
                                objectName: "cancelOptimizationMeasurementButton"
                                visible: tab.measurementActive()
                                text: qsTr("Cancel recording")
                                kind: "secondary"
                                onClicked: tab.cancelMeasurement()
                            }
                            AppButton {
                                objectName: "importMangoHudLogButton"
                                text: qsTr("Import MangoHud log")
                                kind: "secondary"
                                enabled: !tab.measurementActive()
                                         && tab.baselineStatus() !== "processing"
                                         && !tab.pendingOptimizationTest()
                                onClicked: baselineLogDialog.open()
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Recording uses a private one-session MangoHud configuration and does not modify the saved game profile.")
                                color: App.Theme.textMuted
                                wrapMode: Text.WordWrap
                            }
                        }
                        Label {
                            objectName: "launcherMeasurementLimitation"
                            visible: tab.launcherGame
                            Layout.fillWidth: true
                            text: tab.lutrisGame
                                  ? qsTr("Automatic recording is not available for Lutris games yet. Record a MangoHud log in the game (logging key) and use Import MangoHud log")
                                  : qsTr("Close %1 first. GameOpti starts the game through %1 with a private MangoHud logging configuration for this one launch, records until the game exits and then removes that configuration again.").arg(String(tab.gameData.launcher || ""))
                            color: tab.lutrisGame ? App.Theme.warning : App.Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        SurfaceCard {
                            visible: String(tab.runnerPreflight.message || "").length > 0
                            Layout.fillWidth: true
                            padding: 12
                            selected: true
                            contentItem: RowLayout {
                                Label {
                                    Layout.fillWidth: true
                                    text: String(tab.runnerPreflight.message || "")
                                    color: App.Theme.warning
                                    wrapMode: Text.WordWrap
                                }
                                AppButton {
                                    text: qsTr("Copy Launch Option")
                                    kind: "secondary"
                                    onClicked: tab.copyText(tab.steamLaunchCommand)
                                }
                            }
                        }
                        GridLayout {
                            visible: Boolean(gameAnalysis.measurement)
                            Layout.fillWidth: true
                            columns: tab.width > 880 ? 4 : 2
                            columnSpacing: 12
                            rowSpacing: 8
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Duration"); value: tab.measurementValue("durationSeconds", 1, " s") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Average FPS"); value: tab.measurementValue("averageFps", 1, " FPS") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("1% low"); value: tab.measurementValue("onePercentLowFps", 1, " FPS") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Frametime"); value: tab.measurementValue("averageFrametimeMs", 2, " ms") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Frametime p95"); value: tab.measurementValue("p95FrametimeMs", 2, " ms") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Frametime p99"); value: tab.measurementValue("p99FrametimeMs", 2, " ms") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("CPU usage"); value: tab.measurementValue("cpuUsagePercent", 1, "%") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("GPU usage"); value: tab.measurementValue("gpuUsagePercent", 1, "%") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("RAM usage"); value: tab.measurementValue("ramUsedMb", 0, " MiB") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("VRAM usage"); value: tab.measurementValue("vramUsedMb", 0, " MiB") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("GPU temperature"); value: tab.measurementValue("gpuTemperatureC", 1, " °C") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Samples used"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.samples || 0)) }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Total samples"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.totalSamples || 0)) }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Excluded samples"); value: String(Number(gameAnalysis.measurement && gameAnalysis.measurement.excludedSamples || 0)) }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Samples used (%)"); value: Number(gameAnalysis.measurement && gameAnalysis.measurement.usedPercentage || 0).toFixed(1) + "%" }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Selected duration"); value: tab.measurementValue("selectedDurationSeconds", 1, " s") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Measurement quality"); value: App.I18n.status(gameAnalysis.measurement && gameAnalysis.measurement.quality || "low") }
                            LabeledValue { Layout.fillWidth: true; label: qsTr("Representative"); value: Boolean(gameAnalysis.measurement && gameAnalysis.measurement.representative) ? qsTr("Yes") : qsTr("No") }
                        }
                        Label {
                            visible: Boolean(gameAnalysis.measurement) && gameAnalysis.measurement.representative === false
                            Layout.fillWidth: true
                            text: qsTr("The recording contains mixed or unstable rendering regimes. Record another baseline during representative gameplay before using automatic recommendations.")
                            color: App.Theme.warning
                            wrapMode: Text.WordWrap
                        }
                        GridLayout {
                            visible: Boolean(gameAnalysis.measurement)
                            Layout.fillWidth: true
                            columns: tab.width > 880 ? 2 : 1
                            columnSpacing: 12
                            rowSpacing: 8
                            LabeledValue {
                                objectName: "frameRateLimitValue"
                                Layout.fillWidth: true
                                label: qsTr("Frame-rate limit")
                                value: tab.frameRateLimitLabel()
                            }
                            LabeledValue {
                                objectName: "frameRateConfidenceValue"
                                Layout.fillWidth: true
                                label: qsTr("Frame-limit confidence")
                                value: gameAnalysis.frameRate
                                       && gameAnalysis.frameRate.state !== "unknown"
                                       ? Math.round(Number(gameAnalysis.frameRate.confidence || 0) * 100) + "%"
                                       : qsTr("Unavailable")
                            }
                        }
                        Label {
                            visible: Boolean(gameAnalysis.frameRate
                                             && gameAnalysis.frameRate.evidence
                                             && gameAnalysis.frameRate.evidence.length)
                            Layout.fillWidth: true
                            text: qsTr("Frame-limit evidence: %1").arg(
                                      gameAnalysis.frameRate
                                      && gameAnalysis.frameRate.evidence
                                      ? App.I18n.joinAnalysis(gameAnalysis.frameRate.evidence, "; ") : "")
                            color: App.Theme.textSecondary
                            wrapMode: Text.WordWrap
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Bottleneck")
                            value: App.I18n.analysisMessage(gameAnalysis.bottleneck
                                                           && gameAnalysis.bottleneck.conclusion
                                                           || "insufficient_data")
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Confidence")
                            value: gameAnalysis.baselineAvailable
                                   ? Math.round(Number(gameAnalysis.bottleneck && gameAnalysis.bottleneck.confidence || 0) * 100) + "%"
                                   : qsTr("Unavailable")
                        }
                        Repeater {
                            model: gameAnalysis.bottleneck
                                   && gameAnalysis.bottleneck.evidence
                                   ? gameAnalysis.bottleneck.evidence : []
                            Label {
                                required property string modelData
                                Layout.fillWidth: true
                                text: "• " + App.I18n.analysisMessage(modelData)
                                color: App.Theme.textSecondary
                                wrapMode: Text.WordWrap
                            }
                        }
                        Repeater {
                            model: gameAnalysis.bottleneck
                                   && gameAnalysis.bottleneck.limitations
                                   ? gameAnalysis.bottleneck.limitations : []
                            Label {
                                required property string modelData
                                Layout.fillWidth: true
                                text: qsTr("Limitation: %1").arg(App.I18n.analysisMessage(modelData))
                                color: App.Theme.warning
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                SurfaceCard {
                    visible: gameAnalysis.status === "completed"
                    Layout.fillWidth: true
                    padding: 18
                    contentItem: ColumnLayout {
                        spacing: 10
                        Label {
                            text: qsTr("Settings analysis")
                            color: App.Theme.text
                            font.pixelSize: 18
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: gameAnalysis.settingsAnalysis
                                  && gameAnalysis.settingsAnalysis.status === "unsupported"
                                  ? qsTr("Automatic graphics settings optimization is not available for this game yet.")
                                  : gameAnalysis.settingsAnalysis
                                    && gameAnalysis.settingsAnalysis.detected
                                    && gameAnalysis.settingsAnalysis.detected.length
                                    ? qsTr("Supported existing settings were detected.")
                                    : qsTr("No safely modifiable settings were found")
                            color: gameAnalysis.settingsAnalysis
                                   && gameAnalysis.settingsAnalysis.detected
                                   && gameAnalysis.settingsAnalysis.detected.length
                                   ? App.Theme.success : App.Theme.textSecondary
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            visible: Boolean(gameAnalysis.settingsAnalysis
                                             && gameAnalysis.settingsAnalysis.configFiles
                                             && gameAnalysis.settingsAnalysis.configFiles.length)
                            Layout.fillWidth: true
                            text: qsTr("Detected configuration: %1").arg(
                                      gameAnalysis.settingsAnalysis
                                      && gameAnalysis.settingsAnalysis.configFiles
                                      ? gameAnalysis.settingsAnalysis.configFiles.join(", ") : "")
                            color: App.Theme.textMuted
                            wrapMode: Text.WrapAnywhere
                        }
                        Repeater {
                            model: gameAnalysis.settingsAnalysis
                                   && gameAnalysis.settingsAnalysis.detected
                                   ? gameAnalysis.settingsAnalysis.detected : []
                            SurfaceCard {
                                id: detectedSetting
                                required property var modelData
                                property string selectedValue: String(modelData.suggestedValue || "")
                                Layout.fillWidth: true
                                padding: 13
                                contentItem: ColumnLayout {
                                    spacing: 7
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            text: App.I18n.analysisMessage(detectedSetting.modelData.label
                                                                          || detectedSetting.modelData.key || "")
                                            color: App.Theme.text
                                            font.weight: Font.Bold
                                        }
                                        StatusBadge {
                                            text: detectedSetting.modelData.automaticallyRecommended
                                                  ? qsTr("Automatic recommendation")
                                                  : qsTr("Manual test available")
                                            status: detectedSetting.modelData.automaticallyRecommended
                                                    ? "available" : "neutral"
                                        }
                                    }
                                    GridLayout {
                                        Layout.fillWidth: true
                                        columns: tab.width > 820 ? 2 : 1
                                        columnSpacing: 14
                                        rowSpacing: 6
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Current value")
                                            value: String(detectedSetting.modelData.value || "")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Supported values")
                                            value: (detectedSetting.modelData.availableValues || []).join(" / ")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Category")
                                            value: App.I18n.analysisMessage(detectedSetting.modelData.category || "")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Performance relevance")
                                            value: App.I18n.status(detectedSetting.modelData.performanceImpact || "unknown")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Visual impact")
                                            value: App.I18n.status(detectedSetting.modelData.qualityImpact || "unknown")
                                        }
                                        LabeledValue {
                                            Layout.fillWidth: true
                                            label: qsTr("Automatic recommendation")
                                            value: detectedSetting.modelData.automaticallyRecommended
                                                   ? qsTr("Yes") : qsTr("No")
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: App.I18n.analysisMessage(detectedSetting.modelData.automaticReason || "")
                                        color: App.Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                    RowLayout {
                                        visible: Boolean(detectedSetting.modelData.modifiable)
                                        Layout.fillWidth: true
                                        Label {
                                            text: qsTr("Manual test value")
                                            color: App.Theme.textSecondary
                                        }
                                        AppComboBox {
                                            id: manualValue
                                            Layout.preferredWidth: 130
                                            model: detectedSetting.modelData.alternativeValues || []
                                            enabled: !Boolean(gameAnalysis.appliedChange
                                                              && gameAnalysis.appliedChange.state === "applied")
                                            onActivated: function(index) {
                                                detectedSetting.selectedValue = String(model[index])
                                            }
                                        }
                                        Item { Layout.fillWidth: true }
                                        AppButton {
                                            objectName: "manualSettingPreviewButton"
                                            text: qsTr("Preview test")
                                            kind: "secondary"
                                            enabled: detectedSetting.selectedValue.length > 0
                                                     && !Boolean(gameAnalysis.appliedChange
                                                                && gameAnalysis.appliedChange.state === "applied")
                                            onClicked: tab.previewSetting(
                                                detectedSetting.modelData.instanceId,
                                                detectedSetting.selectedValue)
                                        }
                                    }
                                }
                            }
                        }
                        SurfaceCard {
                            visible: Boolean(settingPreview && settingPreview.success)
                            Layout.fillWidth: true
                            padding: 13
                            selected: true
                            contentItem: ColumnLayout {
                                spacing: 6
                                Label {
                                    text: qsTr("Game setting test")
                                    color: App.Theme.text
                                    font.weight: Font.Bold
                                }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("Setting"); value: App.I18n.analysisMessage(settingPreview.settingLabel || "") }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("File"); value: (settingPreview.filesToModify || []).join(", ") }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("Key"); value: String(settingPreview.configKey || "") }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("Current value"); value: String(settingPreview.currentValue || "") }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("New value"); value: String(settingPreview.proposedValue || "") }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("Automatic recommendation"); value: settingPreview.automaticRecommended ? qsTr("Yes") : qsTr("No") }
                                Label { Layout.fillWidth: true; text: App.I18n.analysisMessage(settingPreview.automaticReason || ""); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("Performance relevance"); value: App.I18n.status(settingPreview.performanceImpact || "unknown") }
                                LabeledValue { Layout.fillWidth: true; label: qsTr("Visual impact"); value: App.I18n.status(settingPreview.qualityImpact || "unknown") }
                                Label {
                                    visible: Boolean(settingPreview.aggressive)
                                    Layout.fillWidth: true
                                    text: qsTr("This is more aggressive than the conservative one-step test.")
                                    color: App.Theme.warning
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("A verified backup will be created before the file is changed.")
                                    color: App.Theme.success
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Item { Layout.fillWidth: true }
                                    AppButton { text: qsTr("Cancel"); kind: "secondary"; onClicked: settingPreview = ({}) }
                                    AppButton { text: qsTr("Apply and test"); kind: "primary"; onClicked: tab.applySettingPreview() }
                                }
                            }
                        }
                        SurfaceCard {
                            visible: Boolean(gameAnalysis.appliedChange
                                             && gameAnalysis.appliedChange.state === "applied"
                                             && gameAnalysis.appliedChange.config_adapter)
                            Layout.fillWidth: true
                            padding: 13
                            selected: true
                            contentItem: ColumnLayout {
                                spacing: 7
                                Label { text: qsTr("Pending optimization test"); color: App.Theme.warning; font.weight: Font.Bold }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("%1: %2 -> %3").arg(
                                              App.I18n.analysisMessage(tab.activeChangeValue("setting_label")
                                              || tab.activeChangeValue("config_key")))
                                          .arg(tab.activeChangeValue("current_value"))
                                          .arg(tab.activeChangeValue("proposed_value"))
                                    color: App.Theme.text
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("The config change was applied and verified. Record a representative comparison before keeping it.")
                                    color: App.Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    visible: String(gameAnalysis.comparison
                                                    && gameAnalysis.comparison.outcome || "") === "insufficient_data"
                                    Layout.fillWidth: true
                                    text: qsTr("Comparison measurement was recorded but was not representative enough. The original baseline is unchanged.")
                                    color: App.Theme.warning
                                    wrapMode: Text.WordWrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    AppButton {
                                        text: String(gameAnalysis.comparison
                                                     && gameAnalysis.comparison.outcome || "") === "insufficient_data"
                                              ? qsTr("Record comparison again") : qsTr("Record comparison")
                                        kind: "primary"
                                        onClicked: tab.recordComparison()
                                    }
                                    AppButton {
                                        visible: String(gameAnalysis.comparison
                                                        && gameAnalysis.comparison.outcome || "").length > 0
                                                 && String(gameAnalysis.comparison.outcome) !== "insufficient_data"
                                        text: qsTr("Keep change")
                                        kind: "primary"
                                        onClicked: tab.keepActiveChange()
                                    }
                                    AppButton {
                                        text: qsTr("Revert now")
                                        kind: gameAnalysis.comparison
                                              && gameAnalysis.comparison.recommendRevert
                                              ? "danger" : "secondary"
                                        onClicked: tab.revertActiveChange()
                                    }
                                }
                            }
                        }
                    }
                }

                }
                }
            }

            Loader {
                objectName: "integrationCardsLoader"
                Layout.fillWidth: true
                active: tab.shows("integrations")
                visible: active
                sourceComponent: Component {
                ColumnLayout {
                spacing: 14
                SurfaceCard {
                    objectName: "launcherConnectionCard"
                    visible: tab.launcherGame
                    Layout.fillWidth: true; padding: 18
                    contentItem: ColumnLayout {
                        spacing: 8
                        RowLayout { Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: qsTr("%1 connection").arg(String(tab.launcherStatus.launcher || tab.gameData.launcher || "")); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                            StatusBadge {
                                objectName: "launcherConnectionBadge"
                                text: tab.launcherStatus.state === "applied" ? qsTr("Applied")
                                      : tab.launcherStatus.state === "outdated" ? qsTr("Outdated")
                                      : tab.launcherStatus.state === "modified" ? qsTr("Changed in launcher")
                                      : tab.launcherStatus.state === "unavailable" ? qsTr("Unavailable")
                                      : tab.launcherStatus.state === "syncing" ? qsTr("Synchronizing...")
                                      : tab.launcherStatus.state === "config_outdated" ? qsTr("Configuration outdated") : qsTr("Not applied")
                                status: tab.launcherStatus.state === "applied" ? "available" : "warning"
                            }
                        }
                        Label { objectName: "launcherConnectionMessage"; Layout.fillWidth: true; text: tab.launcherStatusText(); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                        Label { Layout.fillWidth: true; text: qsTr("The saved settings are written into this game's own launcher configuration (environment and wrapper). Nothing else is changed, a backup is kept, and you can restore it here."); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                        Rectangle { visible: String(tab.launcherStatus.preview || "").length > 0; Layout.fillWidth: true; Layout.preferredHeight: 56; radius: App.Theme.radiusMedium; color: App.Theme.input; clip: true
                            TextEdit { objectName: "launcherWrapperPreview"; anchors.fill: parent; anchors.margins: 10; readOnly: true; selectByMouse: true; text: String(tab.launcherStatus.preview || ""); color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                        }
                        Label { Layout.fillWidth: true; visible: (tab.launcherStatus.changes || []).length > 0; text: qsTr("Apply will write:"); color: App.Theme.textSecondary; font.weight: Font.DemiBold }
                        TextEdit { objectName: "launcherChangePreview"; Layout.fillWidth: true; visible: (tab.launcherStatus.changes || []).length > 0; readOnly: true; selectByMouse: true; text: (tab.launcherStatus.changes || []).join("\n"); color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                        Label { objectName: "launcherConflicts"; Layout.fillWidth: true; visible: (tab.launcherStatus.conflicts || []).length > 0; text: (tab.launcherStatus.conflicts || []).join("\n"); color: App.Theme.warning; wrapMode: Text.WordWrap }
                        Label { Layout.fillWidth: true; visible: (tab.launcherStatus.warnings || []).length > 0; text: qsTr("Warnings: %1").arg((tab.launcherStatus.warnings || []).map(function(item) { return App.I18n.message(String(item)) }).join("; ")); color: App.Theme.warning; wrapMode: Text.WordWrap }
                        ColumnLayout {
                            objectName: "launcherMissingComponents"
                            Layout.fillWidth: true
                            spacing: 6
                            visible: (tab.launcherComponents.components || []).length > 0
                            Label { Layout.fillWidth: true; text: qsTr("Missing components"); color: App.Theme.text; font.weight: Font.DemiBold }
                            Repeater {
                                model: tab.launcherComponents.components || []
                                delegate: ColumnLayout {
                                    id: componentRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 1
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label { Layout.fillWidth: true; text: String(componentRow.modelData.label || ""); color: App.Theme.text }
                                        StatusBadge {
                                            text: tab.componentStateText(componentRow.modelData)
                                            status: componentRow.modelData.state === "ready" ? "available" : componentRow.modelData.state === "missing" || componentRow.modelData.state === "installing" || componentRow.modelData.state === "checking" ? "warning" : "error"
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        visible: String(componentRow.modelData.ref || "").length > 0
                                        text: qsTr("%1 · runtime %2 · %3 installation").arg(String(componentRow.modelData.ref || "")).arg(String(componentRow.modelData.branch || "")).arg(String(componentRow.modelData.scope || ""))
                                        color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption; font.family: "monospace"; wrapMode: Text.WrapAnywhere
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        visible: componentRow.modelData.state === "ready" || componentRow.modelData.state === "restart_required"
                                        text: (componentRow.modelData.applied ? qsTr("Applied to the launcher") : qsTr("Not applied to the launcher yet"))
                                              + " · " + qsTr("Active in a game: not verified")
                                        color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        visible: String(componentRow.modelData.message || "").length > 0
                                        text: App.I18n.message(String(componentRow.modelData.message || "")) + (String(componentRow.modelData.progress || "").length > 0 ? " · " + String(componentRow.modelData.progress) : "")
                                        color: componentRow.modelData.state === "error" || componentRow.modelData.state === "unavailable" ? App.Theme.danger : App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap
                                    }
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: tab.componentsConfirm
                                text: qsTr("GameOpti will run:") + "\n" + (tab.launcherComponents.components || []).filter(function(item) { return item.state === "missing" }).map(function(item) { return String(item.argv || "") }).join("\n")
                                      + ((tab.launcherComponents.components || []).some(function(item) { return item.state === "missing" && item.polkit }) ? "\n" + qsTr("The system will ask for administrator authorization.") : "")
                                color: App.Theme.text; font.family: "monospace"; wrapMode: Text.WrapAnywhere
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("After installation, check the result here and then press Apply to launcher. The game profile and the launcher settings are not changed automatically.")
                                color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 10
                                AppButton {
                                    objectName: "repairLauncherComponents"
                                    kind: "primary"
                                    text: tab.componentsConfirm ? qsTr("Install now") : qsTr("Fix missing components")
                                    enabled: Boolean(tab.launcherComponents.canRepair)
                                    onClicked: tab.componentsConfirm ? tab.repairComponents() : (tab.componentsConfirm = true)
                                }
                                AppButton { visible: tab.componentsConfirm; kind: "secondary"; text: qsTr("Cancel"); onClicked: tab.componentsConfirm = false }
                                Item { Layout.fillWidth: true }
                            }
                        }
                        Label { objectName: "launcherConnectionError"; Layout.fillWidth: true; visible: tab.launcherMessage.length > 0; text: tab.launcherMessage; color: App.Theme.danger; wrapMode: Text.WordWrap }
                        RowLayout { Layout.fillWidth: true; spacing: 10
                            AppButton { objectName: "applyLauncherIntegration"; kind: "primary"; text: tab.launcherStatus.state === "outdated" || tab.launcherStatus.state === "modified" ? qsTr("Apply again") : qsTr("Apply to launcher"); enabled: Boolean(tab.launcherStatus.canApply); onClicked: tab.changeLauncher(true) }
                            AppButton { objectName: "syncLauncherMangoHudConfig"; visible: Boolean(tab.launcherStatus.canSyncConfig); kind: "secondary"; text: qsTr("Synchronize again"); onClicked: { tab.launcherStatus = tab.controller.syncLauncherMangoHudConfig(tab.gameId) || ({}); tab.launcherSyncPolls = 0 } }
                            AppButton { objectName: "restoreLauncherIntegration"; kind: "secondary"; text: qsTr("Restore launcher settings"); enabled: Boolean(tab.launcherStatus.canRestore); onClicked: tab.changeLauncher(false) }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }

                SurfaceCard {
                    visible: !tab.localGame && !tab.launcherGame
                    Layout.fillWidth: true; padding: 18
                    contentItem: ColumnLayout {
                        RowLayout { Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: qsTr("Steam connection"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                            StatusBadge { text: tab.runnerStatus.installed ? qsTr("Runner installed") : qsTr("Runner not installed"); status: tab.runnerStatus.installed ? "available" : "warning" }
                        }
                        Label { Layout.fillWidth: true; text: qsTr("Set this command once in the game’s Steam Launch Options. Later profile changes do not change it."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 66; radius: App.Theme.radiusMedium; color: App.Theme.input
                            TextEdit { id: steamCommand; anchors.fill: parent; anchors.margins: 10; readOnly: true; selectByMouse: true; text: tab.steamLaunchCommand; color: App.Theme.text; font.family: "monospace"; wrapMode: TextEdit.WrapAnywhere }
                        }
                        RowLayout {
                            AppButton { text: qsTr("Copy command"); kind: "secondary"; onClicked: { steamCommand.selectAll(); steamCommand.copy(); steamCommand.deselect() } }
                            AppButton { text: qsTr("Show instructions"); kind: "secondary"; onClicked: tab.showSteamInstructions = !tab.showSteamInstructions }
                            AppButton { text: qsTr("Test runner"); kind: "secondary"; onClicked: if (tab.controller && tab.controller.testGameOptimizationRunner) tab.controller.testGameOptimizationRunner(tab.gameId) }
                        }
                        Label { Layout.fillWidth: true; visible: tab.showSteamInstructions; text: qsTr("Steam → Properties → General → Launch Options"); color: App.Theme.textMuted; wrapMode: Text.WordWrap }
                    }
                }

                }
                }
            }

            Label {
                objectName: "integrationsLocalNote"
                visible: tab.shows("integrations") && tab.localGame
                Layout.fillWidth: true
                text: qsTr("This game starts through the GameOpti runner; no launcher integration is needed.")
                color: App.Theme.textSecondary
                wrapMode: Text.WordWrap
            }

            Label { Layout.fillWidth: true; visible: tab.errorMessage.length > 0 && tab.section !== "integrations"; text: tab.errorMessage; color: App.Theme.danger; wrapMode: Text.WordWrap }
            RowLayout { Layout.fillWidth: true; visible: tab.shows("performance"); Item { Layout.fillWidth: true }
                AppButton { objectName: "saveOptimizationProfileButton"; text: qsTr("Apply profile"); iconSource: App.UiIcons.actionApply; enabled: tab.dirty && tab.recommendationAnalyzed; onClicked: tab.saveProfile() }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }

    component GsSection: ColumnLayout {
        id: section
        property string title: ""
        property string badge: ""
        property bool expanded: false
        default property alias body: sectionBody.data
        Layout.fillWidth: true
        spacing: 6
        AppButton {
            Layout.fillWidth: true
            kind: "ghost"
            compact: true
            text: (section.expanded ? "\u25BE " : "\u25B8 ") + section.title + (section.badge.length ? " (" + section.badge + ")" : "")
            onClicked: section.expanded = !section.expanded
        }
        ColumnLayout {
            id: sectionBody
            visible: section.expanded
            Layout.fillWidth: true
            Layout.leftMargin: 8
            spacing: 6
        }
    }

    // A value below ``from`` means "not set": Gamescope keeps its default.
    component GsOptionalSlider: RowLayout {
        id: optionalSlider
        property real from: 0
        property real to: 1
        property real stepSize: 1
        property real value: -1
        property real defaultValue: from
        property int decimals: 0
        property string unit: ""
        readonly property bool overridden: value >= from
        signal edited(real value)
        spacing: 8
        AppSwitch { checked: optionalSlider.overridden; enabled: optionalSlider.enabled; onToggled: optionalSlider.edited(checked ? optionalSlider.defaultValue : -1) }
        AppSlider { Layout.preferredWidth: 170; enabled: optionalSlider.enabled && optionalSlider.overridden; from: optionalSlider.from; to: optionalSlider.to; stepSize: optionalSlider.stepSize; value: optionalSlider.overridden ? optionalSlider.value : optionalSlider.defaultValue; onMoved: optionalSlider.edited(Number(value.toFixed(optionalSlider.decimals))) }
        Label { Layout.preferredWidth: 96; text: optionalSlider.overridden ? Number(optionalSlider.value).toFixed(optionalSlider.decimals) + optionalSlider.unit : qsTr("Default"); color: App.Theme.textSecondary }
    }

    function scheduleProfileLoad() {
        profileLoadTimer.restart()
    }

    Timer {
        id: profileLoadTimer
        interval: 35
        repeat: false
        onTriggered: tab.loadProfile()
    }

    onGameIdChanged: {
        settingPreview = ({})
        scheduleProfileLoad()
    }
    Component.onCompleted: scheduleProfileLoad()

    FileDialog {
        id: baselineLogDialog
        title: qsTr("Import MangoHud log")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("MangoHud logs (*.csv)"), qsTr("All files (*)")]
        onAccepted: {
            var result = tab.controller.importOptimizationBaseline(tab.gameId, selectedFile.toString())
            if (result && result.success) {
                tab.gameAnalysis = Object.assign({}, tab.gameAnalysis, {
                    "baselineSession": result.baselineSession || ({ "status": "processing" })
                })
            } else {
                tab.errorMessage = String(result && result.error || qsTr("MangoHud log could not be imported"))
            }
        }
    }

    Connections {
        target: tab.controller || null
        ignoreUnknownSignals: true
        function onMangoHudProfileChanged(changedAppId) {
            if (String(changedAppId) === tab.appId && tab.shows("performance"))
                previewTimer.restart()
        }
        function onProtonTweaksChanged(appId) {
            if (String(appId) === tab.appId)
                tab.loadProfile()
        }
        function onGamescopeTestChanged(result) {
            tab.gamescopeTestRunning = Boolean(result && result.running)
            tab.gamescopeTestMessage = result && !result.success ? tab.gamescopeTestError(result) : ""
        }
        function onOptimizationAnalysisChanged(changedGameId) {
            if (String(changedGameId) === tab.gameId)
                tab.loadProfile()
        }
    }
}
