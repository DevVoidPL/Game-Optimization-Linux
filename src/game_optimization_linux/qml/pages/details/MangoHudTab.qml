pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

Item {
    id: tab
    signal optimizationRequested()
    signal textInputRequested(var field, string heading)

    property var controller
    property var gameData: ({})
    readonly property string gameId: String(value(["id"], ""))
    property bool loading: false
    property bool profileLoaded: false
    property bool dirty: false
    property string errorMessage: ""
    property bool available: false
    property string availabilityMessage: ""
    property string appId: ""
    property string profilePath: ""
    property string configPath: ""
    property string configPreview: ""
    property string selectedExecutable: ""
    property var executableCandidates: []
    property string activationStrategy: "steam_environment"
    property string strategyStatus: "executable_missing"
    property string strategyMessage: ""
    property string applicationConfigPath: ""
    property string conflictPath: ""
    property bool requiresSteamRestart: false
    property string preset: "disabled"
    property bool profileEnabled: false
    property string position: "top-left"
    property int fontSize: 24
    property real backgroundAlpha: 0.5
    property int roundCorners: 8
    property bool compact: false
    property bool horizontal: false
    property int tableColumns: 3
    property int fpsLimit: 0
    property string fpsLimitOwner: "none"
    property string fpsLimitMethod: ""
    property string vulkanPresentMode: ""
    property int vsync: -2
    property string toggleHudKey: "Shift_R+F12"
    property var metrics: []
    property bool loggingEnabled: false
    property int logDuration: 60
    property real logInterval: 0.1
    property string outputFolder: ""
    property string toggleLoggingKey: "Shift_L+F2"
    property var supportedMetrics: []
    property bool frameTimingGraph: false
    property string textColor: "FFFFFF"
    property string gpuColor: "2E9762"
    property string cpuColor: "2E97CB"
    property string vramColor: "AD64C1"
    property string ramColor: "C26693"
    property string frametimeColor: "00FF00"
    property string backgroundColor: "020202"
    // Preset metric sets and the HUD layout both come from the backend model
    // that also generates MangoHud.conf.
    property var presetMetricSets: ({})
    property var previewLayout: ({})
    property bool configExpanded: false
    property bool livePreviewRunning: false
    property string livePreviewMessage: ""
    property int categoryIndex: 0
    property bool couchMode: false
    readonly property int bodySize: couchMode ? 17 : App.Theme.fontBody
    readonly property int captionSize: couchMode ? 14 : App.Theme.fontCaption
    property int padIndex: 0
    property var padFocusedItem: null
    property string loadedGameId: ""
    property bool appearanceAdvanced: false
    property bool advancedExpanded: false
    property int metricGroup: 0
    property int offsetX: 0
    property int offsetY: 0
    property real cellPaddingY: -0.085
    property var graphs: []
    property var presetAppearance: ({})
    property var editorDefaults: ({})
    property var supportedSettings: []
    property var runtimeInfo: ({})
    property bool mangoapp: false
    property string colorTarget: "gpuColor"
    readonly property bool wide: width >= 920
    readonly property var appearanceKeys: ["position", "fontSize", "backgroundAlpha", "roundCorners", "compact", "horizontal", "tableColumns", "offsetX", "offsetY", "cellPaddingY", "textColor", "gpuColor", "cpuColor", "vramColor", "ramColor", "frametimeColor", "backgroundColor"]
    readonly property var fieldAliases: ({font_size:"fontSize", background_alpha:"backgroundAlpha", round_corners:"roundCorners", table_columns:"tableColumns", offset_x:"offsetX", offset_y:"offsetY", cellpadding_y:"cellPaddingY", frame_timing_graph:"frameTimingGraph", text_color:"textColor", gpu_color:"gpuColor", cpu_color:"cpuColor", vram_color:"vramColor", ram_color:"ramColor", frametime_color:"frametimeColor", background_color:"backgroundColor", logging_enabled:"loggingEnabled", log_duration:"logDuration", log_interval:"logInterval", output_folder:"outputFolder", toggle_logging_key:"toggleLoggingKey", toggle_hud_key:"toggleHudKey", enabled:"profileEnabled"})

    signal toastRequested(string message, string tone)
    readonly property var scrollFlickable: optionScroll.contentItem

    readonly property var categoryModel: [
        qsTr("Presets"), qsTr("Appearance"), qsTr("Metrics"),
        qsTr("Recording")
    ]
    readonly property var presetModel: [
        { "id": "gameopti", "label": qsTr("GameOpti green"), "description": qsTr("Mint accents, balanced telemetry and FPS statistics.") },
        { "id": "minimal", "label": qsTr("Minimal HUD"), "description": qsTr("FPS and frametime with a light background.") },
        { "id": "diagnostic", "label": qsTr("Diagnostic"), "description": qsTr("Clocks, memory, graphs and rendering information.") },
        { "id": "disabled", "label": qsTr("Disabled"), "description": qsTr("Do not activate MangoHud for this game.") },
        { "id": "fps_only", "label": qsTr("FPS only"), "description": qsTr("FPS and basic frametime information.") },
        { "id": "basic", "label": qsTr("Basic"), "description": qsTr("FPS, CPU, GPU, temperatures, RAM and VRAM.") },
        { "id": "extended", "label": qsTr("Extended"), "description": qsTr("Adds clocks, power, process and Wine or Proton information.") },
        { "id": "custom", "label": qsTr("Custom"), "description": qsTr("Choose each supported metric yourself.") }
    ]
    readonly property var positionValues: [
        "top-left", "top-center", "top-right", "middle-left", "middle-right",
        "bottom-left", "bottom-center", "bottom-right"
    ]
    readonly property var positionLabels: [
        qsTr("Top left"), qsTr("Top center"), qsTr("Top right"),
        qsTr("Middle left"), qsTr("Middle right"), qsTr("Bottom left"),
        qsTr("Bottom center"), qsTr("Bottom right")
    ]
    readonly property var metricModel: [
        { "id": "fps", "label": qsTr("FPS") },
        { "id": "frametime", "label": qsTr("Frametime") },
        { "id": "gpu_usage", "label": qsTr("GPU usage") },
        { "id": "gpu_temperature", "label": qsTr("GPU temperature") },
        { "id": "gpu_clock", "label": qsTr("GPU clock") },
        { "id": "gpu_power", "label": qsTr("GPU power") },
        { "id": "vram", "label": qsTr("VRAM") },
        { "id": "cpu_usage", "label": qsTr("CPU usage") },
        { "id": "cpu_temperature", "label": qsTr("CPU temperature") },
        { "id": "cpu_clock", "label": qsTr("CPU clock") },
        { "id": "cpu_power", "label": qsTr("CPU power") },
        { "id": "ram", "label": qsTr("RAM") },
        { "id": "process_memory", "label": qsTr("Process memory") },
        { "id": "process_vram", "label": qsTr("Process VRAM") },
        { "id": "resolution", "label": qsTr("Resolution") },
        { "id": "wine_proton", "label": qsTr("Wine / Proton") },
        { "id": "gamemode", "label": qsTr("GameMode status") },
        { "id": "battery", "label": qsTr("Battery") },
        { "id": "network", "label": qsTr("Network"), "group": 3 },
        { "id": "avg_fps", "label": qsTr("Average FPS"), "group": 0, "description": qsTr("Rolling statistics from MangoHud samples.") },
        { "id": "fps_1_low", "label": qsTr("1% low (MangoHud)"), "group": 0, "description": qsTr("MangoHud's documented 1% metric; needs enough samples.") },
        { "id": "frametime_detailed", "label": qsTr("Detailed frametime"), "group": 0, "description": qsTr("Minimum, average and maximum on the frametime graph.") },
        { "id": "gpu_memory_clock", "label": qsTr("GPU memory clock"), "group": 1, "description": qsTr("Needs the VRAM row and driver support.") },
        { "id": "throttling", "label": qsTr("GPU throttling"), "group": 1, "description": qsTr("Shown only while a limit is active. No indicator does not mean measured zero.") },
        { "id": "disk_io", "label": qsTr("Disk I/O"), "group": 3, "description": qsTr("Reads and writes of the monitored process, not the whole disk.") },
        { "id": "swap", "label": qsTr("Swap usage"), "group": 3, "description": qsTr("System swap allocation.") },
        { "id": "graphics_api", "label": qsTr("Graphics API / version"), "group": 4 },
        { "id": "vulkan_driver", "label": qsTr("Vulkan driver"), "group": 4 },
        { "id": "wine_sync", "label": qsTr("Wine synchronization"), "group": 4, "description": qsTr("Wine synchronization method, not display VSync.") },
        { "id": "present_mode", "label": qsTr("Presentation mode"), "group": 4, "description": qsTr("Swapchain mode; not input latency.") },
        { "id": "vkbasalt", "label": qsTr("vkBasalt status"), "group": 4, "description": qsTr("Loaded-layer status, not proof of a visible effect.") }
    ]

    readonly property var graphModel: [{id:"gpu_load", label:qsTr("GPU load")}, {id:"cpu_load", label:qsTr("CPU load")}, {id:"vram", label:qsTr("VRAM")}, {id:"ram", label:qsTr("RAM")}]
    readonly property var visibleMetrics: metricModel.filter(function(item) {
        var group = item.group !== undefined ? item.group : item.id.indexOf("gpu_") === 0 || item.id === "vram" || item.id === "process_vram" ? 1 : item.id.indexOf("cpu_") === 0 ? 2 : item.id === "fps" || item.id === "frametime" ? 0 : item.id === "resolution" || item.id === "wine_proton" || item.id === "gamemode" ? 4 : 3
        return group === metricGroup
    })
    readonly property var draftMap: ({
        "offsetX": offsetX, "offsetY": offsetY, "cellPaddingY": cellPaddingY,
        "graphs": graphs,
        "enabled": profileEnabled,
        "preset": preset,
        "position": position,
        "fontSize": fontSize,
        "backgroundAlpha": backgroundAlpha,
        "roundCorners": roundCorners,
        "compact": compact,
        "horizontal": horizontal,
        "tableColumns": tableColumns,
        "fpsLimit": fpsLimit,
        "fpsLimitMethod": fpsLimitMethod,
        "vulkanPresentMode": vulkanPresentMode,
        "vsync": vsync === -2 ? null : vsync,
        "toggleHudKey": toggleHudKey,
        "metrics": metrics,
        "loggingEnabled": loggingEnabled,
        "logDuration": logDuration,
        "logInterval": logInterval,
        "outputFolder": outputFolder,
        "toggleLoggingKey": toggleLoggingKey,
        "executablePath": selectedExecutable,
        "frameTimingGraph": frameTimingGraph,
        "textColor": textColor,
        "gpuColor": gpuColor,
        "cpuColor": cpuColor,
        "vramColor": vramColor,
        "ramColor": ramColor,
        "frametimeColor": frametimeColor,
        "backgroundColor": backgroundColor
    })
    readonly property var colorModel: [
        { "id": "textColor", "label": qsTr("Text color") },
        { "id": "gpuColor", "label": qsTr("GPU color") },
        { "id": "cpuColor", "label": qsTr("CPU color") },
        { "id": "vramColor", "label": qsTr("VRAM color") },
        { "id": "ramColor", "label": qsTr("RAM color") },
        { "id": "frametimeColor", "label": qsTr("Frametime graph color") },
        { "id": "backgroundColor", "label": qsTr("Background color") }
    ]

    readonly property var executableValues: executableCandidates.map(function(item) {
        return String(item.relativePath || "")
    })
    readonly property var executableLabels: executableCandidates.map(function(item) {
        return String(item.label || item.relativePath || item.name || "")
    })

    function strategyLabel() {
        if (strategyStatus === "application_config_conflict")
            return qsTr("Conflict with an existing MangoHud configuration")
        if (strategyStatus === "executable_missing" || strategyStatus === "saved_invalid")
            return qsTr("Game executable was not determined")
        if (activationStrategy === "per_application_config")
            return qsTr("Application profile - changes apply on the next game launch")
        return requiresSteamRestart
                ? qsTr("Steam environment profile - restart Steam")
                : qsTr("Steam environment profile - already active")
    }

    function value(keys, fallback) {
        var source = gameData || {}
        for (var index = 0; index < keys.length; ++index) {
            var candidate = source[keys[index]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function indexOf(items, wanted, fallback) {
        var found = items.indexOf(wanted)
        return found >= 0 ? found : fallback
    }

    function hasMetric(metric) { return metrics.indexOf(metric) >= 0 }
    function metricSupported(metric) {
        return supportedMetrics.indexOf(metric) >= 0
    }

    function sameMetricSet(left, right) {
        if (left.length !== right.length) return false
        for (var index = 0; index < left.length; ++index)
            if (right.indexOf(left[index]) < 0) return false
        return true
    }

    // A manual metric change makes the profile "custom" unless the resulting
    // set is exactly one of the presets (never "disabled").
    function presetForMetrics(values) {
        var candidates = ["fps_only", "basic", "extended"]
        for (var index = 0; index < candidates.length; ++index) {
            var known = presetMetricSets[candidates[index]]
            if (known && sameMetricSet(values, known)) return candidates[index]
        }
        return "custom"
    }

    function setMetric(metric, selected) {
        var next = metrics.slice()
        var index = next.indexOf(metric)
        if (selected && index < 0) next.push(metric)
        else if (!selected && index >= 0) next.splice(index, 1)
        if (selected && metric === "gpu_memory_clock" && next.indexOf("vram") < 0) next.push("vram")
        if (selected && metric === "frametime_detailed") frameTimingGraph=true
        metrics = next
        preset = presetForMetrics(next)
        profileEnabled = true
        changed()
    }

    function presetMetrics(value) {
        if (value === "disabled") return []
        if (value === "custom") return metrics
        var known = presetMetricSets[value]
        return known ? Array.from(known) : metrics
    }

    function hudBackground(hex, alpha) {
        var base = Qt.lighter(hex, 1.0)
        return Qt.rgba(base.r, base.g, base.b, alpha)
    }

    function colorValue(name) { return String(tab[name] || "") }
    function validColor(value) { return /^[0-9A-Fa-f]{6}$/.test(String(value)) }
    function setColor(name, value) {
        var normalized = String(value || "").replace("#", "").toUpperCase()
        if (!validColor(normalized) || tab[name] === normalized) return
        tab[name] = normalized
        changed()
    }

    function startLivePreview() {
        livePreviewMessage = ""
        if (!controller || !controller.startMangoHudLivePreview || gameId.length === 0) return
        var result = controller.startMangoHudLivePreview(gameId, draftMap) || ({})
        livePreviewRunning = Boolean(result.running)
        if (!result.success) livePreviewMessage = livePreviewErrorText(result)
    }

    function stopLivePreview() {
        if (controller && controller.stopMangoHudLivePreview) controller.stopMangoHudLivePreview()
    }

    function livePreviewErrorText(result) {
        var code = String(result && result.code || "")
        if (code === "vkcube_missing") return qsTr("Live preview requires the vulkan-tools package")
        if (code === "mangohud_missing") return App.I18n.message(String(result.message || ""))
        if (code === "already_running") return qsTr("A live preview is already running")
        if (code === "disabled") return qsTr("Select a preset with metrics first")
        if (code === "process_failed")
            return qsTr("The live preview ended with an error: %1").arg(String(result.message || ""))
        return qsTr("Could not start the live preview: %1").arg(String(result && result.message || ""))
    }

    function copyConfig() {
        configTextEdit.selectAll()
        configTextEdit.copy()
        configTextEdit.deselect()
        toastRequested(qsTr("Configuration copied"), "success")
    }

    function choosePreset(value) {
        preset = value
        profileEnabled = value !== "disabled"
        metrics = presetMetrics(value)
        assignEditor(presetAppearance[value] || ({}))
        changed()
    }

    function changed() {
        if (loading) return
        dirty = true
        previewTimer.restart()
    }

    function applyProfile(result) {
        loading = true
        syncAvailability(result)
        offsetX = Number(result && result.offsetX || 0)
        offsetY = Number(result && result.offsetY || 0)
        cellPaddingY = Number(result && result.cellPaddingY !== undefined ? result.cellPaddingY : -0.085)
        graphs = result && result.graphs ? Array.from(result.graphs) : []
        profileLoaded = Boolean(result && result.success)
        errorMessage = String(result && result.error || "")
        appId = String(result && result.appId || "")
        available = Boolean(result && result.available)
        availabilityMessage = String(result && result.availabilityMessage || result && result.error || "")
        preset = String(result && result.preset || "disabled")
        profileEnabled = Boolean(result && result.enabled)
        position = String(result && result.position || "top-left")
        fontSize = Number(result && result.fontSize || 24)
        backgroundAlpha = Number(result && result.backgroundAlpha !== undefined ? result.backgroundAlpha : 0.5)
        roundCorners = Number(result && result.roundCorners !== undefined ? result.roundCorners : 8)
        compact = Boolean(result && result.compact)
        horizontal = Boolean(result && result.horizontal)
        tableColumns = Number(result && result.tableColumns || 3)
        fpsLimit = Number(result && result.fpsLimit || 0)
        fpsLimitOwner = String(result && result.fpsLimitOwner || "none")
        fpsLimitMethod = String(result && result.fpsLimitMethod || "")
        vulkanPresentMode = String(result && result.vulkanPresentMode || "")
        vsync = result && result.vsync !== null && result.vsync !== undefined ? Number(result.vsync) : -2
        toggleHudKey = String(result && result.toggleHudKey || "Shift_R+F12")
        metrics = result && result.metrics ? Array.from(result.metrics) : []
        loggingEnabled = Boolean(result && result.loggingEnabled)
        logDuration = Number(result && result.logDuration || 60)
        logInterval = Number(result && result.logInterval !== undefined ? result.logInterval : 0.1)
        outputFolder = String(result && result.outputFolder || "")
        toggleLoggingKey = String(result && result.toggleLoggingKey || "Shift_L+F2")
        supportedMetrics = result && result.supportedMetrics ? Array.from(result.supportedMetrics) : []
        frameTimingGraph = Boolean(result && result.frameTimingGraph)
        textColor = String(result && result.textColor || "FFFFFF")
        gpuColor = String(result && result.gpuColor || "2E9762")
        cpuColor = String(result && result.cpuColor || "2E97CB")
        vramColor = String(result && result.vramColor || "AD64C1")
        ramColor = String(result && result.ramColor || "C26693")
        frametimeColor = String(result && result.frametimeColor || "00FF00")
        backgroundColor = String(result && result.backgroundColor || "020202")
        presetMetricSets = result && result.presetMetrics ? result.presetMetrics : ({})
        previewLayout = result && result.previewLayout ? result.previewLayout : ({})
        livePreviewRunning = Boolean(result && result.livePreviewRunning)
        profilePath = String(result && result.profilePath || "")
        configPath = String(result && result.configPath || "")
        configPreview = String(result && result.configPreview || "")
        selectedExecutable = String(result && result.selectedExecutable || result && result.executablePath || "")
        executableCandidates = result && result.executableCandidates ? Array.from(result.executableCandidates) : []
        activationStrategy = String(result && result.activationStrategy || "steam_environment")
        strategyStatus = String(result && result.strategyStatus || "executable_missing")
        strategyMessage = String(result && result.strategyMessage || "")
        applicationConfigPath = String(result && result.applicationConfigPath || "")
        conflictPath = String(result && result.conflictPath || "")
        requiresSteamRestart = Boolean(result && result.requiresSteamRestart)
        dirty = false
        loading = false
    }

    function loadProfile() {
        if (!controller || !controller.getMangoHudProfile || gameId.length === 0)
            return
        previewTimer.stop()
        var result = controller.getMangoHudProfile(gameId)
        if (dirty && loadedGameId === gameId) {
            syncAvailability(result)
            refreshPreview()
            return
        }
        applyProfile(result)
        loadedGameId = gameId
    }

    function refreshPreview() {
        if (!controller || !controller.previewMangoHudProfile || gameId.length === 0)
            return
        var result = controller.previewMangoHudProfile(gameId, draftMap)
        if (result && result.success)
        {
            errorMessage = ""
            syncAvailability(result)
            configPreview = String(result.configPreview || "")
            previewLayout = result.previewLayout || ({})
            fpsLimit = Number(result.fpsLimit || 0)
            fpsLimitOwner = String(result.fpsLimitOwner || "none")
            activationStrategy = String(result.activationStrategy || activationStrategy)
            strategyStatus = String(result.strategyStatus || strategyStatus)
            strategyMessage = String(result.strategyMessage || strategyMessage)
            applicationConfigPath = String(result.applicationConfigPath || applicationConfigPath)
            conflictPath = String(result.conflictPath || "")
            requiresSteamRestart = Boolean(result.requiresSteamRestart)
        }
        else
            errorMessage = String(result && result.error || qsTr("Invalid MangoHud settings"))
    }

    function saveProfile() {
        if (!controller || !controller.saveMangoHudProfile) return
        var result = controller.saveMangoHudProfile(gameId, draftMap)
        if (result && result.success) {
            applyProfile(result)
            var sync = result.launcherSync || ({})
            launcherSyncText = sync.state === "not_applied" ? qsTr("Profile saved - apply it to the launcher")
                             : sync.state === "syncing" ? qsTr("Profile saved - the launcher config is being updated; it takes effect at the next game start")
                             : sync.state === "refused" || sync.state === "error" ? App.I18n.message(String(sync.message || ""))
                             : ""
        }
        else errorMessage = String(result && result.error || qsTr("Could not save MangoHud profile"))
    }
    property string launcherSyncText: ""

    // Short debounce: slider drags and typing rebuild only the preview model.
    Timer { id: previewTimer; interval: 180; repeat: false; onTriggered: tab.refreshPreview() }

    Connections {
        target: tab.controller ? tab.controller : null
        ignoreUnknownSignals: true
        function onMangoHudProfileChanged(key) {
            if (String(key) === tab.appId) tab.loadProfile()
        }
        function onMangoHudLivePreviewChanged(result) {
            tab.livePreviewRunning = Boolean(result && result.running)
            tab.livePreviewMessage = result && !result.success ? tab.livePreviewErrorText(result) : ""
        }
    }


    function syncAvailability(result) {
        if (!result || !result.success) return
        runtimeInfo = result.runtimeInfo || ({})
        mangoapp = Boolean(result.mangoapp)
        available = Boolean(result.available)
        availabilityMessage = String(result.availabilityMessage || "")
        supportedMetrics = result.supportedMetrics ? Array.from(result.supportedMetrics) : []
        supportedSettings = result.supportedKeys ? Array.from(result.supportedKeys) : []
        presetAppearance = result.presetAppearance || ({})
        editorDefaults = result.editorDefaults || ({})
    }
    function settingSupported(key) { return supportedSettings.indexOf(key) >= 0 }
    function assignEditor(values, keys) {
        var previous = loading; loading = true
        Object.keys(values).forEach(function(key) {
            var property = fieldAliases[key] || key
            if (keys && keys.indexOf(property) < 0) return
            if (tab[property] !== undefined && ["schema_version","app_id","updated_at","extra_fields"].indexOf(key) < 0)
                tab[property] = values[key] instanceof Array ? values[key].slice() : values[key]
        })
        loading = previous
    }
    function resetSection() {
        var keys = categoryIndex === 0 ? ["preset","profileEnabled","metrics"] : categoryIndex === 1 ? appearanceKeys : categoryIndex === 2 ? ["metrics","graphs","frameTimingGraph"] : ["loggingEnabled","logDuration","logInterval","outputFolder","toggleLoggingKey"]
        assignEditor(editorDefaults, keys)
        if (categoryIndex === 2) preset = "custom"
        changed()
    }
    function resetEditor() {
        assignEditor(editorDefaults, appearanceKeys.concat(["preset","profileEnabled","metrics","graphs","frameTimingGraph","loggingEnabled","logDuration","logInterval","outputFolder","toggleLoggingKey","toggleHudKey"]))
        changed()
    }
    function setGraph(key, on) {
        var next = graphs.filter(function(g) { return g !== key })
        if (on) next.push(key)
        graphs = next; changed()
    }
    function controls(item, result) {
        if (!item || !item.visible || !item.enabled) return
        if (item.mangoActionType !== undefined) result.push(item)
        var children = item.children || []
        for (var i=0;i<children.length;i++) controls(children[i], result)
    }
    function handlePadAction(action) {
        if (action === "Back") {
            if (colorPicker.visible) { colorPicker.close(); padIndex = 0; return true }
            return false
        }
        var list=[]; controls(colorPicker.visible ? colorPicker.contentItem : tab, list)
        if (!list.length) return false
        var focused = list.findIndex(function(item) { return item.activeFocus })
        if (focused >= 0) padIndex = focused
        if (action === "NavigateDown" || action === "NavigateUp") padIndex = Math.max(0, Math.min(list.length-1, padIndex + (action === "NavigateDown" ? 1 : -1)))
        padIndex = Math.min(padIndex, list.length-1)
        var item=list[padIndex]; padFocusedItem=item; item.forceActiveFocus()
        if (action === "NavigateLeft" || action === "NavigateRight" || action === "Confirm") {
            var direction=action === "NavigateLeft" ? -1 : 1
            if (item.mangoActionType === "slider") { item.value=Math.max(item.from, Math.min(item.to, item.value+direction*item.stepSize)); item.moved() }
            else if (item.mangoActionType === "combo") { item.currentIndex=(item.currentIndex+direction+item.count)%item.count; item.activated(item.currentIndex) }
            else if (item.mangoActionType === "check" && action === "Confirm") { item.toggle(); item.toggled() }
            else if (item.mangoActionType === "button" && action === "Confirm") item.clicked()
            else if (item.mangoActionType === "text" && action === "Confirm" && couchMode) {
                if (colorPicker.visible) colorPicker.close()
                textInputRequested(item, item.Accessible.name || qsTr("Edit value"))
            }
        }
        if (!colorPicker.visible) revealControl(item)
        return true
    }
    function commitPadText(item, text) {
        if (item.mangoCommit) item.mangoCommit(text)
        else if (item.mangoColor) setColor(item.mangoColor, text)
        else if (item.mangoProperty) { tab[item.mangoProperty]=text; changed() }
    }
    function revealControl(item) {
        var flick=optionScroll.contentItem
        if (!flick || !flick.contentItem || !item) return
        var ancestor=item
        while (ancestor && ancestor!==flick.contentItem) ancestor=ancestor.parent
        if (ancestor!==flick.contentItem) return
        var y=item.mapToItem(flick.contentItem,0,0).y
        if (y<flick.contentY || y+item.height>flick.contentY+flick.height)
            flick.contentY=Math.max(0,Math.min(Math.max(0,flick.contentHeight-flick.height),y-flick.height/3))
    }
    function openColor(name) { colorTarget=name; padIndex=0; colorPicker.open() }
    function colorChannel(index) { return parseInt(colorValue(colorTarget).slice(index*2,index*2+2),16) }
    function setChannel(index, number) {
        var parts=[colorChannel(0),colorChannel(1),colorChannel(2)]; parts[index]=Math.round(number)
        setColor(colorTarget, parts.map(function(v){return v.toString(16).padStart(2,"0")}).join(""))
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Image { objectName: "mangoHudBrand"; source: App.UiIcons.brandMangoHud; Layout.preferredWidth: 32; Layout.preferredHeight: 32; sourceSize.width: 32; sourceSize.height: 32; fillMode: Image.PreserveAspectFit; Accessible.name: "MangoHud" }
            ColumnLayout { Layout.fillWidth: true; spacing: 2
                Label { text: qsTr("MangoHud editor"); color: App.Theme.text; font.bold: true; font.pixelSize: 20; Layout.fillWidth: true }
                Label { Layout.fillWidth: true; text: (tab.runtimeInfo.environment || qsTr("Runtime not checked")) + " · " + (tab.runtimeInfo.version || qsTr("Version unknown")); color: App.Theme.textMuted; font.pixelSize: tab.captionSize; wrapMode: Text.WrapAnywhere }
            }
            AppButton { textPixelSize:tab.bodySize; objectName: "refreshMangoHudProfileButton"; property string mangoActionType: "button"; text: qsTr("Refresh"); kind: "secondary"; compact: true; onClicked: tab.loadProfile() }
        }
        Label { Layout.fillWidth: true; visible: tab.available; text: qsTr("Components detected · game runtime loading not verified"); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: tab.captionSize }
        Label { Layout.fillWidth: true; visible: !tab.available; text: App.I18n.message(tab.availabilityMessage); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: tab.captionSize }
        Label { Layout.fillWidth: true; visible: ["Heroic","Lutris"].indexOf(String(tab.gameData.launcher || ""))>=0; text: qsTr("Apply the saved profile to the launcher from Overview."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: tab.captionSize }
        GridLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; columns: tab.wide ? 2 : 1; rowSpacing: 10; columnSpacing: 12
            SurfaceCard {
                Layout.row: tab.wide ? 0 : 1; Layout.column: 0
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredWidth: tab.wide ? tab.width * 0.57 : tab.width
                Layout.minimumHeight: 190; padding: 12
                contentItem: ColumnLayout {
                    spacing: 10
                    GridLayout { Layout.fillWidth: true; columns: 4; columnSpacing: 4
                        Repeater { model: tab.categoryModel
                            AppButton { required property int index; required property string modelData
                                property string mangoActionType: "button"
                                Layout.fillWidth: true; Layout.minimumWidth: 0; compact: true
                                text: modelData; kind: tab.categoryIndex===index ? "primary" : "secondary"
                                textPixelSize: tab.couchMode ? 16 : tab.width < 550 ? 11 : App.Theme.fontCaption
                                onClicked: { tab.categoryIndex=index; optionScroll.contentItem.contentY=0 }
                            }
                        }
                    }
                    ScrollView {
                        id: optionScroll; Layout.fillWidth: true; Layout.fillHeight: true
                        contentWidth: availableWidth; clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ColumnLayout {
                            width: optionScroll.availableWidth; spacing: 12
                            ColumnLayout {
                                visible: tab.categoryIndex===0; Layout.fillWidth: true; spacing: 10
                                AppSwitch { textPixelSize:tab.bodySize; objectName: "mangoHudEnabled"; property string mangoActionType: "check"; text: qsTr("Enable MangoHud for this game"); checked: tab.profileEnabled; onToggled: { tab.profileEnabled=checked; tab.changed() } }
                                Label { font.pixelSize:tab.bodySize; Layout.fillWidth: true; text: qsTr("Choose a starting style, then adjust it. Nothing changes on disk before Apply."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                Repeater { model: tab.presetModel.slice(0,3)
                                    AppButton { textPixelSize:tab.bodySize; required property var modelData
                                        property string mangoActionType: "button"
                                        Layout.fillWidth: true; Layout.preferredHeight: Math.max(64, presetText.implicitHeight+20)
                                        kind: tab.preset===modelData.id ? "primary" : "secondary"
                                        onClicked: tab.choosePreset(modelData.id)
                                        contentItem: ColumnLayout { id: presetText; spacing: 3
                                            Label { font.pixelSize:tab.bodySize; text: modelData.label; color: tab.preset===modelData.id ? App.Theme.textOnAccent : App.Theme.text; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                            Label { text: modelData.description; color: tab.preset===modelData.id ? App.Theme.textOnAccent : App.Theme.textSecondary; font.pixelSize: tab.captionSize; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                        }
                                    }
                                }
                                Label { font.pixelSize:tab.bodySize; text: qsTr("Other presets"); color: App.Theme.textSecondary }
                                AppComboBox { textPixelSize:tab.bodySize; property string mangoActionType: "combo"; Layout.fillWidth: true; model: tab.presetModel.map(function(p){return p.label}); currentIndex: tab.indexOf(tab.presetModel.map(function(p){return p.id}),tab.preset,0); onActivated: function(index){ tab.choosePreset(tab.presetModel[index].id) } }
                            }
                            ColumnLayout {
                                visible: tab.categoryIndex===1; Layout.fillWidth: true; spacing: 12
                                RowLayout { Layout.fillWidth: true; spacing: 14
                                    GridLayout { columns: 3; rowSpacing: 4; columnSpacing: 4
                                        Repeater { model: ["top-left","top-center","top-right","middle-left","","middle-right","bottom-left","bottom-center","bottom-right"]
                                            AppButton { textPixelSize:tab.bodySize; required property string modelData; required property int index
                                                objectName: "mangoHudPosition_"+modelData; property string mangoActionType: "button"
                                                Layout.preferredWidth: 44; Layout.preferredHeight: 34; enabled: modelData!==""
                                                Accessible.name: modelData ? tab.positionLabels[tab.positionValues.indexOf(modelData)] : ""
                                                text: ["↖","↑","↗","←","·","→","↙","↓","↘"][index]
                                                kind: tab.position===modelData ? "primary" : "secondary"
                                                onClicked: { tab.position=modelData; tab.changed() }
                                            }
                                        }
                                    }
                                    ColumnLayout { Layout.fillWidth: true
                                        Label { font.pixelSize:tab.bodySize; text: qsTr("Position"); color: App.Theme.text; font.bold: true }
                                        Label { font.pixelSize:tab.bodySize; text: tab.positionLabels[tab.indexOf(tab.positionValues,tab.position,0)]; color: App.Theme.textSecondary; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                        Label { text: qsTr("Offsets are relative to this anchor."); color: App.Theme.textMuted; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: tab.captionSize }
                                    }
                                }
                                GridLayout { Layout.fillWidth: true; columns: optionScroll.availableWidth>=590 ? 2 : 1
                                    MangoHudNumber { largeControls:tab.couchMode; title: qsTr("Horizontal offset"); unit: "px"; controlName: "mangoHudOffsetX"; minimum:-4096; maximum:4096; value:tab.offsetX; enabled:tab.settingSupported("offset_x"); onEdited: function(number){tab.offsetX=number;tab.changed()} }
                                    MangoHudNumber { largeControls:tab.couchMode; title: qsTr("Vertical offset"); unit: "px"; controlName: "mangoHudOffsetY"; minimum:-4096; maximum:4096; value:tab.offsetY; enabled:tab.settingSupported("offset_y"); onEdited: function(number){tab.offsetY=number;tab.changed()} }
                                }
                                MangoHudNumber { largeControls:tab.couchMode; title: qsTr("Font size"); unit:"px"; controlName:"mangoHudFontSize"; minimum:8; maximum:96; value:tab.fontSize; onEdited:function(number){tab.fontSize=number;tab.changed()} }
                                MangoHudNumber { largeControls:tab.couchMode; title: qsTr("Background opacity"); unit:"%"; controlName:"mangoHudOpacity"; value:Math.round(tab.backgroundAlpha*100); onEdited:function(number){tab.backgroundAlpha=number/100;tab.changed()} }
                                MangoHudNumber { largeControls:tab.couchMode; title: qsTr("Rounded corners"); unit:"px"; controlName:"mangoHudCorners"; maximum:64; value:tab.roundCorners; onEdited:function(number){tab.roundCorners=number;tab.changed()} }
                                GridLayout { Layout.fillWidth:true; columns: optionScroll.availableWidth>=540 ? 2 : 1; columnSpacing:12; rowSpacing:8
                                    Repeater { model:tab.colorModel
                                        ColumnLayout { required property var modelData; Layout.fillWidth:true; spacing:3
                                            Label { Layout.fillWidth:true; text:modelData.label; color:App.Theme.textSecondary; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
                                            RowLayout { Layout.fillWidth:true; spacing:6
                                                AppButton { textPixelSize:tab.bodySize; property string mangoActionType:"button"; Layout.preferredWidth:44; Layout.preferredHeight:34; leftPadding:6; rightPadding:6; topPadding:6; bottomPadding:6; Accessible.name:modelData.label; onClicked:tab.openColor(modelData.id)
                                                    contentItem:Rectangle { color:"#"+tab.colorValue(modelData.id); radius:4; border.color:App.Theme.borderStrong; border.width:1 }
                                                }
                                                AppTextField { font.pixelSize:tab.bodySize; objectName:"mangoHudColor_"+modelData.id; property string mangoActionType:"text"; property string mangoColor:modelData.id; Layout.fillWidth:true; Layout.maximumWidth:140; text:tab.colorValue(modelData.id); maximumLength:7; Accessible.name:modelData.label; validator:RegularExpressionValidator {regularExpression:/#?[0-9A-Fa-f]{0,6}/}  onTextEdited:tab.setColor(modelData.id,text) }
                                            }
                                        }
                                    }
                                }
                                AppButton { textPixelSize:tab.bodySize; property string mangoActionType:"button"; objectName:"mangoHudLayoutAdvanced"; text:(tab.appearanceAdvanced ? "▾ " : "▸ ")+qsTr("Layout details"); kind:"secondary"; Layout.fillWidth:true; onClicked:tab.appearanceAdvanced=!tab.appearanceAdvanced }
                                ColumnLayout { visible:tab.appearanceAdvanced; Layout.fillWidth:true; spacing:10
                                    AppSwitch { textPixelSize:tab.bodySize; property string mangoActionType:"check"; text:qsTr("Compact layout"); checked:tab.compact; onToggled:{tab.compact=checked;tab.changed()} }
                                    AppSwitch { textPixelSize:tab.bodySize; objectName:"mangoHudHorizontal"; property string mangoActionType:"check"; text:qsTr("Horizontal layout"); checked:tab.horizontal; onToggled:{tab.horizontal=checked;tab.changed()} }
                                    MangoHudNumber { largeControls:tab.couchMode; title:qsTr("Table columns"); controlName:"mangoHudColumns"; minimum:1; maximum:6; value:tab.tableColumns; onEdited:function(number){tab.tableColumns=number;tab.changed()} }
                                    MangoHudNumber { largeControls:tab.couchMode; title:qsTr("Row spacing"); unit:"em"; controlName:"mangoHudSpacing"; minimum:-0.2; maximum:1; step:0.01; decimals:3; value:tab.cellPaddingY; enabled:tab.settingSupported("cellpadding_y"); onEdited:function(number){tab.cellPaddingY=number;tab.changed()} }
                                }
                            }
                            ColumnLayout {
                                visible:tab.categoryIndex===2; Layout.fillWidth:true; spacing:10
                                Label { Layout.fillWidth:true; text:tab.mangoapp ? qsTr("Gamescope mangoapp monitors the compositor. Game API, driver, presentation, per-process memory/I/O and vkBasalt status are unavailable as game telemetry here; saved selections are kept.") : qsTr("In-game telemetry depends on driver and process access. Missing counters are unavailable, not measured zero. An FPS counter does not identify generated frames or input latency."); color:App.Theme.textSecondary; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
                                AppComboBox { textPixelSize:tab.bodySize; objectName:"mangoHudMetricGroup"; property string mangoActionType:"combo"; Layout.fillWidth:true; model:[qsTr("Frame statistics"),qsTr("GPU"),qsTr("CPU"),qsTr("Memory and I/O"),qsTr("API and status")]; currentIndex:tab.metricGroup; onActivated:function(index){tab.metricGroup=index} }
                                AppSwitch { textPixelSize:tab.bodySize; objectName:"mangoHudFrameTimingGraph"; property string mangoActionType:"check"; text:qsTr("Frametime graph"); checked:tab.frameTimingGraph || tab.hasMetric("frametime_detailed"); onToggled:{var on=checked;if(!on && tab.hasMetric("frametime_detailed"))tab.setMetric("frametime_detailed",false);tab.frameTimingGraph=on;tab.changed()} }
                                Repeater { model:tab.visibleMetrics
                                    RowLayout { required property var modelData; Layout.fillWidth:true; spacing:10
                                        ColumnLayout { Layout.fillWidth:true; spacing:2
                                            Label { font.pixelSize:tab.bodySize; text:modelData.label; color:App.Theme.text; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                            Label { Layout.fillWidth:true; visible:text.length>0; text:tab.metricSupported(modelData.id) ? String(modelData.description||"") : qsTr("Unavailable in this runtime or MangoHud version. Your saved selection is preserved."); color:App.Theme.textMuted; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
                                        }
                                        AppSwitch { textPixelSize:tab.bodySize; objectName:"mangoHudMetric_"+modelData.id; property string mangoActionType:"check"; enabled:tab.metricSupported(modelData.id); checked:tab.hasMetric(modelData.id); Accessible.name:modelData.label; onToggled:tab.setMetric(modelData.id,checked) }
                                    }
                                }
                                Label { font.pixelSize:tab.bodySize; text:qsTr("Monitoring graphs"); color:App.Theme.text; font.bold:true }
                                GridLayout { Layout.fillWidth:true; columns:optionScroll.availableWidth>460 ? 2 : 1
                                    Repeater { model:tab.graphModel
                                        AppSwitch { textPixelSize:tab.bodySize; required property var modelData; property string mangoActionType:"check"; objectName:"mangoHudGraph_"+modelData.id; text:modelData.label; enabled:tab.settingSupported("graphs"); checked:tab.graphs.indexOf(modelData.id)>=0; onToggled:tab.setGraph(modelData.id,checked) }
                                    }
                                }
                            }
                            ColumnLayout {
                                visible:tab.categoryIndex===3; Layout.fillWidth:true; spacing:12
                                AppSwitch { textPixelSize:tab.bodySize; property string mangoActionType:"check"; objectName:"mangoHudLogging"; text:qsTr("Enable recording controls"); checked:tab.loggingEnabled; onToggled:{tab.loggingEnabled=checked;tab.changed()} }
                                Label { font.pixelSize:tab.bodySize; Layout.fillWidth:true; text:qsTr("Recordings stay local and work with Performance Analysis. Start logging with the shortcut; enabling controls does not start a recording."); color:App.Theme.textSecondary; wrapMode:Text.WordWrap }
                                MangoHudNumber { largeControls:tab.couchMode; enabled:tab.loggingEnabled; title:qsTr("Recording duration"); unit:"s"; controlName:"mangoHudLogDuration"; minimum:1; maximum:86400; value:tab.logDuration; onEdited:function(number){tab.logDuration=number;tab.changed()} }
                                MangoHudNumber { largeControls:tab.couchMode; enabled:tab.loggingEnabled; title:qsTr("Sample interval"); unit:"s"; controlName:"mangoHudLogInterval"; minimum:0; maximum:60; decimals:3; step:0.1; value:tab.logInterval; onEdited:function(number){tab.logInterval=number;tab.changed()} }
                                Label { font.pixelSize:tab.bodySize; text:qsTr("Logging shortcut"); color:App.Theme.textSecondary }
                                AppTextField { font.pixelSize:tab.bodySize; property string mangoActionType:"text"; property string mangoProperty:"toggleLoggingKey"; Accessible.name:qsTr("Logging shortcut"); enabled:tab.loggingEnabled; Layout.fillWidth:true; Layout.maximumWidth:280; text:tab.toggleLoggingKey; onTextEdited:{tab.toggleLoggingKey=text;tab.changed()} }
                                Label { font.pixelSize:tab.bodySize; text:qsTr("Output folder"); color:App.Theme.textSecondary }
                                AppTextField { font.pixelSize:tab.bodySize; property string mangoActionType:"text"; property string mangoProperty:"outputFolder"; Accessible.name:qsTr("Output folder"); enabled:tab.loggingEnabled; Layout.fillWidth:true; text:tab.outputFolder; selectByMouse:true; onTextEdited:{tab.outputFolder=text;tab.changed()} }
                            }
                            AppButton { textPixelSize:tab.bodySize; objectName:"mangoHudAdvancedToggle"; property string mangoActionType:"button"; Layout.fillWidth:true; kind:"secondary"; text:(tab.advancedExpanded ? "▾ " : "▸ ")+qsTr("Advanced / configuration"); onClicked:tab.advancedExpanded=!tab.advancedExpanded }
                            ColumnLayout { visible:tab.advancedExpanded; Layout.fillWidth:true; spacing:8
                                Label { font.pixelSize:tab.bodySize; Layout.fillWidth:true; text:qsTr("FPS limiting is managed in Optimization. Existing limiter and presentation preferences are preserved."); color:App.Theme.textSecondary; wrapMode:Text.WordWrap }
                                AppButton { textPixelSize:tab.bodySize; property string mangoActionType:"button"; text:qsTr("Open Optimization"); kind:"secondary"; onClicked:tab.optimizationRequested() }
                                Label { font.pixelSize:tab.bodySize; text:qsTr("HUD shortcut"); color:App.Theme.textSecondary }
                                AppTextField { font.pixelSize:tab.bodySize; property string mangoActionType:"text"; property string mangoProperty:"toggleHudKey"; Accessible.name:qsTr("HUD shortcut"); Layout.fillWidth:true; Layout.maximumWidth:280; text:tab.toggleHudKey; onTextEdited:{tab.toggleHudKey=text;tab.changed()} }
                                Label { font.pixelSize:tab.bodySize; text:qsTr("Main executable"); color:App.Theme.textSecondary }
                                AppComboBox { textPixelSize:tab.bodySize; property string mangoActionType:"combo"; Layout.fillWidth:true; enabled:tab.executableCandidates.length>0; model:tab.executableLabels; currentIndex:tab.indexOf(tab.executableValues,tab.selectedExecutable,0); onActivated:function(index){tab.selectedExecutable=tab.executableValues[index];tab.changed()} }
                                Label { font.pixelSize:tab.bodySize; Layout.fillWidth:true; text:tab.strategyLabel(); color:App.Theme.textMuted; wrapMode:Text.WordWrap }
                                Label { Layout.fillWidth:true; visible:!tab.mangoapp && tab.runtimeInfo.layer32===false; text:qsTr("32-bit MangoHud layer not detected. A 32-bit game needs the matching layer in its launcher runtime."); color:App.Theme.warning; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
                                Label { Layout.fillWidth:true; visible:!Boolean(tab.runtimeInfo.versionVerified); text:qsTr("The new monitoring controls require verified MangoHud 0.8.4 or newer. Update the launcher environment, then refresh."); color:App.Theme.warning; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
                                AppButton { textPixelSize:tab.bodySize; objectName:"mangoHudConfigToggle"; property string mangoActionType:"button"; Layout.fillWidth:true; kind:"secondary"; compact:true; text:qsTr("Generated configuration"); onClicked:tab.configExpanded=!tab.configExpanded }
                                Rectangle { visible:tab.configExpanded; Layout.fillWidth:true; Layout.preferredHeight:200; color:App.Theme.input; radius:8; clip:true
                                    ScrollView { anchors.fill:parent; anchors.margins:8; contentWidth:availableWidth
                                        TextEdit { id:configTextEdit; objectName:"mangoHudConfigText"; width:parent.width; readOnly:true; selectByMouse:true; color:App.Theme.text; text:tab.configPreview; font.family:"monospace"; font.pixelSize:12; wrapMode:TextEdit.WrapAnywhere }
                                    }
                                }
                                RowLayout { Layout.fillWidth:true
                                    AppButton { textPixelSize:tab.bodySize; objectName:"mangoHudConfigCopy"; property string mangoActionType:"button"; text:qsTr("Copy"); kind:"secondary"; onClicked:tab.copyConfig() }
                                    AppButton { textPixelSize:tab.bodySize; property string mangoActionType:"button"; text:qsTr("Open directory"); kind:"secondary"; onClicked:if(tab.controller && tab.controller.openMangoHudDirectory)tab.controller.openMangoHudDirectory(tab.gameId) }
                                }
                            }
                        }
                    }
                    AppButton { textPixelSize:tab.bodySize; objectName:"resetMangoHudSectionButton"; property string mangoActionType:"button"; Layout.fillWidth:true; compact:true; kind:"secondary"; text:qsTr("Reset this section"); onClicked:tab.resetSection() }
                }
            }
            MangoHudPreview { editor:tab; compactPreview:!tab.wide; Layout.row:0; Layout.column:tab.wide ? 1 : 0; Layout.fillWidth:true; Layout.preferredWidth:tab.wide ? tab.width*0.4 : tab.width; Layout.alignment:Qt.AlignTop }
        }
        Label { font.pixelSize:tab.bodySize; Layout.fillWidth:true; visible:tab.errorMessage.length>0; text:App.I18n.message(tab.errorMessage); color:App.Theme.danger; wrapMode:Text.WordWrap }
        Label { Layout.fillWidth:true; visible:tab.launcherSyncText.length>0; text:tab.launcherSyncText; color:App.Theme.textSecondary; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
        RowLayout { Layout.fillWidth:true; spacing:8
            Label { Layout.fillWidth:true; text:tab.dirty ? qsTr("Unsaved · preview only") : qsTr("Profile saved · restart game to apply"); color:tab.dirty ? App.Theme.warning : App.Theme.textMuted; wrapMode:Text.WordWrap; font.pixelSize:tab.captionSize }
            AppButton { textPixelSize:tab.bodySize; objectName:"resetMangoHudProfileButton"; property string mangoActionType:"button"; text:qsTr("Reset editor"); compact:true; kind:"secondary"; onClicked:tab.resetEditor() }
            AppButton { textPixelSize:tab.bodySize; objectName:"saveMangoHudProfileButton"; property string mangoActionType:"button"; text:qsTr("Apply"); compact:true; kind:"primary"; enabled:tab.profileLoaded && (!tab.profileEnabled || tab.available); onClicked:tab.saveProfile() }
        }
    }
    Dialog {
        id:colorPicker; objectName:"mangoHudColorPicker"
        parent:Overlay.overlay; anchors.centerIn:parent
        width:Math.min(400,tab.width-16); modal:true
        title:qsTr("Colour picker")
        contentItem:ColumnLayout { spacing:10
            Rectangle { Layout.fillWidth:true; Layout.preferredHeight:42; radius:8; color:"#"+tab.colorValue(tab.colorTarget) }
            GridLayout { Layout.fillWidth:true; columns:6
                Repeater { model:["4FE3B8","8FD7EA","BAA6FF","EDB583","FFFFFF","020202","2E9762","2E97CB","AD64C1","C26693","FF6B75","F5D76E"]
                    AppButton { textPixelSize:tab.bodySize; required property string modelData; property string mangoActionType:"button"; Layout.preferredWidth:42; Layout.preferredHeight:32; leftPadding:6; rightPadding:6; topPadding:6; bottomPadding:6; Accessible.name:"#"+modelData; onClicked:tab.setColor(tab.colorTarget,modelData)
                        contentItem:Rectangle { color:"#"+modelData; radius:4 }
                    }
                }
            }
            Repeater { model:["R","G","B"]
                MangoHudNumber { largeControls:tab.couchMode; required property string modelData; required property int index; title:modelData; maximum:255; value:tab.colorChannel(index); onEdited:function(number){tab.setChannel(index,number)} }
            }
            AppTextField { font.pixelSize:tab.bodySize; property string mangoActionType:"text"; property string mangoColor:tab.colorTarget; Layout.fillWidth:true; text:tab.colorValue(tab.colorTarget); maximumLength:7; Accessible.name:qsTr("Hex colour"); validator:RegularExpressionValidator{regularExpression:/#?[0-9A-Fa-f]{0,6}/}  onTextEdited:tab.setColor(tab.colorTarget,text) }
            AppButton { textPixelSize:tab.bodySize; property string mangoActionType:"button"; text:qsTr("Done"); onClicked:colorPicker.close() }
        }
    }
    onGameIdChanged: { profileLoaded=false; loadProfile() }
    Component.onCompleted: loadProfile()
}
