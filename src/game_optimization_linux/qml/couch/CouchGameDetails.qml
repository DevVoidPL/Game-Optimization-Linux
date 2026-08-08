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
        { "id": "graphics", "title": qsTr("Graphics Remaster") },
        { "id": "optimization", "title": qsTr("Optimization") }
    ]
    readonly property var profileNames: ["Fast", "Balanced", "Maximum", "Auto"]
    readonly property var actionModel: actionsForTab()
    signal backRequested()

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
    function formatBytes(raw) {
        var bytes = Number(raw)
        if (!isFinite(bytes) || bytes < 0) return qsTr("Unavailable")
        var units = [qsTr("B"), qsTr("KiB"), qsTr("MiB"), qsTr("GiB"), qsTr("TiB")]
        var unit = 0
        while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit++ }
        return (unit ? bytes.toFixed(bytes >= 100 ? 0 : 1) : Math.round(bytes)) + " " + units[unit]
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
        if (selectedTab === 3) return [
            { "id": "optimization-profile", "symbol": "◐", "title": qsTr("Profile: %1").arg(optimizationPresetLabel()), "enabled": true },
            { "id": "gamemode", "symbol": "⚡", "title": "GameMode: " + (gameModeEnabled ? qsTr("On") : qsTr("Off")), "enabled": true },
            { "id": "gamescope", "symbol": "▣", "title": "Gamescope: " + (gamescopeEnabled ? qsTr("On") : qsTr("Off")), "enabled": true },
            { "id": "mangohud-profile", "symbol": "◉", "title": qsTr("MangoHud"), "subtitle": mangoHudPresetLabel(), "enabled": true },
            { "id": "optiscaler-launch", "symbol": "◇", "title": qsTr("OptiScaler: %1").arg(optiScalerData.installed ? qsTr("Installed") : qsTr("Not installed")), "subtitle": optiScalerData.installed ? qsTr("Launch with the installed profile") : qsTr("Installation is available in Desktop Mode"), "enabled": Boolean(optiScalerData.installed) },
            { "id": "optiscaler-remove", "symbol": "×", "title": qsTr("Remove OptiScaler"), "subtitle": qsTr("Requires confirmation"), "enabled": Boolean(optiScalerData.manifestId) },
            { "id": "fps", "symbol": "↯", "title": qsTr("FPS: %1").arg(fpsLimit > 0 ? fpsLimit : qsTr("Unlimited")), "enabled": true },
            { "id": "resolution", "symbol": "□", "title": qsTr("Resolution: %1").arg(targetResolution), "enabled": true }
        ]
        return [{ "id": "unavailable", "symbol": "i", "title": qsTr("Unavailable in this version"), "enabled": false }]
    }
    function ensureAction() {
        var actions = actionModel
        if (actions[selectedAction] && actions[selectedAction].enabled) return
        for (var i = 0; i < actions.length; ++i) if (actions[i].enabled) { selectedAction = i; return }
        selectedAction = -1
    }
    function changeTab(delta) {
        selectedTab = (selectedTab + delta + tabs.length) % tabs.length
        selectedAction = 0
        contentFocus = false
        ensureAction()
        if (navigation) navigation.rememberFocus("details", "tab-" + tabs[selectedTab].id, selectedTab)
    }
    function moveAction(delta) {
        var actions = actionModel
        var candidate = selectedAction
        for (var count = 0; count < actions.length; ++count) {
            candidate = Math.max(0, Math.min(actions.length - 1, candidate + delta))
            if (actions[candidate] && actions[candidate].enabled) { selectedAction = candidate; break }
            if (candidate === 0 || candidate === actions.length - 1) break
        }
        if (navigation && selectedAction >= 0) navigation.rememberFocus("details", actions[selectedAction].id, selectedAction)
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
        if (!controller || !controller.getOptiScalerStatus || !game.id) return
        var result = controller.getOptiScalerStatus(String(game.id)) || ({})
        optiScalerData = result.success ? result : ({})
    }
    function loadProtonTweaks() {
        if (!controller || !controller.getProtonTweaks || !game.id) return
        var result = controller.getProtonTweaks(String(game.id)) || ({})
        protonTweaksData = result.success ? result : ({})
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
    }
    function saveCouchOptimization() {
        if (!controller || !controller.saveOptimizationProfile) return
        var source = optimizationData || ({})
        var payload = Object.assign({}, source, {
            "preset": optimizationProfile, "gameCategory": optimizationCategory,
            "targetDisplayId": optimizationDisplayId, "targetFpsMode": "manual",
            "targetFps": fpsLimit, "gamemodeEnabled": gameModeEnabled,
            "gamescopeEnabled": gamescopeEnabled, "gamescopeMode": optimizationGamescopeMode
        })
        var result = controller.saveOptimizationProfile(String(game.id || ""), payload) || ({})
        if (result.success) { optimizationData = result; closeOptimizationOverlay() }
    }
    function handleOptimizationAction(action) {
        if (action === "Back") closeOptimizationOverlay()
        else if (action === "NavigateUp") optimizationRow = Math.max(0, optimizationRow - 1)
        else if (action === "NavigateDown") optimizationRow = Math.min(7, optimizationRow + 1)
        else if (action === "NavigateLeft") adjustOptimization(-1)
        else if (action === "NavigateRight") adjustOptimization(1)
        else if (action === "Confirm") {
            if (optimizationRow < 6) adjustOptimization(1)
            else if (optimizationRow === 6) saveCouchOptimization()
            else closeOptimizationOverlay()
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
        if (!controller || !controller.saveMangoHudProfile) return
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
        }
    }
    function handleMangoHudAction(action) {
        if (action === "Back") {
            closeMangoHudOverlay()
        } else if (action === "NavigateUp") {
            mangoHudRow = Math.max(0, mangoHudRow - 1)
        } else if (action === "NavigateDown") {
            mangoHudRow = Math.min(7, mangoHudRow + 1)
        } else if (action === "NavigateLeft") {
            adjustMangoHud(-1)
        } else if (action === "NavigateRight") {
            adjustMangoHud(1)
        } else if (action === "Confirm") {
            if (mangoHudRow < 6) adjustMangoHud(1)
            else if (mangoHudRow === 6) saveCouchMangoHud()
            else closeMangoHudOverlay()
        }
        restoreActiveFocus()
    }
    function prepareCompression() {
        if (!controller || !controller.prepareCompression || !game.id) return
        var plan = controller.prepareCompression(String(game.id), selectedProfile, true)
        if (!planValid(plan)) return
        pendingPlan = plan
        confirmationChoice = 0
        confirmationKind = "compression"
        confirmationOpen = true
        if (navigation) navigation.openModal("compression-confirmation", "cancel")
    }
    function closeConfirmation() {
        confirmationOpen = false
        confirmationKind = ""
        pendingPlan = ({})
        confirmationChoice = 0
        if (navigation) navigation.closeModal()
    }
    function activateAction() {
        if (selectedAction < 0 || !actionModel[selectedAction] || !actionModel[selectedAction].enabled || !controller) return
        var id = actionModel[selectedAction].id
        if (id === "launch") {
            launchPending = true
            controller.launchGame(String(game.id || ""))
            launchGuard.restart()
        } else if (id === "updates") controller.navigate("updates")
        else if (id === "analyze") controller.analyzeGame(String(game.id || ""))
        else if (id === "verify") controller.verifyCompression(String(game.id || ""))
        else if (id === "profile") {
            var index = profileNames.indexOf(selectedProfile)
            selectedProfile = profileNames[(index + 1) % profileNames.length]
        } else if (id === "compress") prepareCompression()
        else if (id === "optimization-profile" || id === "gamemode" || id === "gamescope" || id === "fps" || id === "resolution") openOptimizationOverlay()
        else if (id === "mangohud-profile") openMangoHudOverlay()
        else if (id === "optiscaler-launch") controller.launchGame(String(game.id || ""))
        else if (id === "optiscaler-remove") {
            confirmationChoice = 0
            confirmationKind = "optiscaler_remove"
            confirmationOpen = true
            if (navigation) navigation.openModal("optiscaler-remove", "cancel")
            restoreActiveFocus()
        }
        
    }
    function handleAction(action) {
        if (optimizationOverlayOpen) {
            handleOptimizationAction(action)
            return
        }
        if (mangoHudOverlayOpen) {
            handleMangoHudAction(action)
            return
        }
        if (confirmationOpen) {
            if (action === "Back") closeConfirmation()
            else if (action === "NavigateLeft" || action === "NavigateUp") confirmationChoice = 0
            else if (action === "NavigateRight" || action === "NavigateDown") confirmationChoice = 1
            else if (action === "Confirm") {
                if (confirmationChoice === 1 && controller) {
                    if (confirmationKind === "optiscaler_remove")
                        controller.removeOptiScaler(String(game.id || ""))
                    else {
                        var planId = String(pendingPlan.planId || pendingPlan.plan_id || "")
                        if (planId.length) controller.startCompression(planId)
                    }
                }
                closeConfirmation()
            }
            return
        }
        if (action === "Back") backRequested()
        else if (action === "PageLeft") changeTab(-1)
        else if (action === "PageRight") changeTab(1)
        else if (action === "NavigateUp" && contentFocus) {
            if (detailsContentFlick.contentY > 0)
                detailsContentFlick.contentY = Math.max(0, detailsContentFlick.contentY - 96 * couchScale)
            else {
                contentFocus = false
                tabFocus = true
            }
        } else if (action === "NavigateDown" && contentFocus) {
            detailsContentFlick.contentY = Math.min(
                Math.max(0, detailsContentFlick.contentHeight - detailsContentFlick.height),
                detailsContentFlick.contentY + 96 * couchScale)
        } else if (action === "NavigateUp" && tabFocus) {
            tabFocus = false
            contentFocus = false
            ensureAction()
        } else if (action === "NavigateDown" && tabFocus) {
            tabFocus = false
            contentFocus = true
        } else if (action === "NavigateDown") {
            tabFocus = true
            contentFocus = false
        } else if (tabFocus && action === "NavigateLeft") changeTab(-1)
        else if (tabFocus && action === "NavigateRight") changeTab(1)
        else if (!contentFocus && action === "NavigateLeft") moveAction(-1)
        else if (!contentFocus && action === "NavigateRight") moveAction(1)
        else if (action === "Confirm" && tabFocus) {
            tabFocus = false
            contentFocus = true
        } else if (action === "Confirm" && !contentFocus) activateAction()
        else if (action === "ContextMenu") { selectedTab = 0; tabFocus = false; contentFocus = false; selectedAction = 0 }
    }
    Timer { id: launchGuard; interval: 1800; onTriggered: page.launchPending = false }
    onSelectedTabChanged: Qt.callLater(ensureAction)
    focus: visible
    Component.onCompleted: { loadMangoHudProfile(); loadOptimizationProfile(); loadOptiScalerStatus(); loadProtonTweaks(); restoreActiveFocus() }
    onVisibleChanged: if (visible) { loadMangoHudProfile(); restoreActiveFocus() }
    onGameChanged: { loadMangoHudProfile(); loadOptimizationProfile(); loadOptiScalerStatus(); loadProtonTweaks() }

    Connections {
        target: page.controller || null
        ignoreUnknownSignals: true
        function onOptiScalerChanged(appId) {
            if (String(appId) === String(page.game.steamAppId || ""))
                page.loadOptiScalerStatus()
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
                    color: page.selectedTab === index
                           ? App.Theme.surfaceSelected : App.Theme.surface
                    border.width: tabButton.activeFocus ? 4 * page.couchScale
                                                        : page.selectedTab === index ? 2 : 1
                    border.color: tabButton.activeFocus ? "white"
                                  : page.selectedTab === index
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
                               ? Math.max(height, detailsContentLoader.item.implicitHeight)
                               : height
                ScrollBar.vertical: ScrollBar {
                    visible: detailsContentFlick.contentHeight
                             > detailsContentFlick.height
                }
                Loader {
                    id: detailsContentLoader
                    width: detailsContentFlick.width
                    height: Math.max(detailsContentFlick.height,
                                     item ? item.implicitHeight : 0)
                sourceComponent: page.selectedTab === 0 ? overviewContent : page.selectedTab === 1 ? storageContent : page.selectedTab === 3 ? optimizationContent : unavailableContent
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
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 78 * page.couchScale
                        radius: App.Theme.couchCardRadius * page.couchScale
                        color: App.Theme.surfaceRaised
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 11 * page.couchScale
                            spacing: 2
                            Label { Layout.fillWidth: true; text: modelData.label; color: App.Theme.textMuted; font.pixelSize: 14 * page.couchScale; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: modelData.value; color: App.Theme.text; font.pixelSize: 19 * page.couchScale; font.weight: Font.Bold; elide: Text.ElideRight }
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
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 86 * page.couchScale
                    radius: 14 * page.couchScale
                    color: App.Theme.surfaceRaised
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 13 * page.couchScale
                        spacing: 3
                        Label { Layout.fillWidth: true; text: modelData.label; color: App.Theme.textSecondary; font.pixelSize: 15 * page.couchScale; elide: Text.ElideRight }
                        Label { Layout.fillWidth: true; text: modelData.value; color: App.Theme.text; font.pixelSize: 24 * page.couchScale; font.weight: Font.Bold; elide: Text.ElideRight }
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
                Label { Layout.fillWidth: true; text: qsTr("Launch configuration preview"); color: App.Theme.text; font.pixelSize: 27 * page.couchScale; font.weight: Font.Bold }
                Label { text: qsTr("Preview only"); color: App.Theme.warning; font.pixelSize: 16 * page.couchScale; font.weight: Font.Bold }
            }
            Label { Layout.fillWidth: true; text: qsTr("MangoHud uses its saved per-game profile. Other launch helpers remain preview-only and no Steam launch options are written."); color: App.Theme.textSecondary; font.pixelSize: 18 * page.couchScale; wrapMode: Text.WordWrap }
            GridLayout {
                Layout.fillWidth: true; columns: 3; columnSpacing: 18 * page.couchScale; rowSpacing: 10 * page.couchScale
                Label { text: "GameMode"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: "Gamescope"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: "MangoHud"; color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale }
                Label { text: page.gameModeEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.gamescopeEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
                Label { text: page.mangoHudEnabled ? qsTr("On") : qsTr("Off"); color: App.Theme.text; font.pixelSize: 20 * page.couchScale; font.weight: Font.Bold }
            }
            Label { Layout.fillWidth: true; text: controller && controller.buildLaunchPreview ? String(controller.buildLaunchPreview(String(page.game.id || ""), { "profile": page.legacyOptimizationProfileLabel(), "gamemode": page.gameModeEnabled, "gamescope": page.gamescopeEnabled, "mangohud": page.mangoHudEnabled, "fpsLimit": page.fpsLimit })) : "%command%"; color: App.Theme.textSecondary; font.family: "monospace"; font.pixelSize: 15 * page.couchScale; elide: Text.ElideMiddle }
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
                Label { Layout.fillWidth: true; text: page.confirmationKind === "optiscaler_remove" ? qsTr("Remove OptiScaler?") : qsTr("Review compression plan"); color: App.Theme.text; font.pixelSize: 29 * page.couchScale; font.weight: Font.Bold }
                Label { Layout.fillWidth: true; text: page.confirmationKind === "optiscaler_remove" ? qsTr("Only files recorded as created by Game Optimization will be removed. Replaced files remain available for restoration in Desktop Mode.") : qsTr("Profile: %1. Review warnings before starting. No operation starts until explicit confirmation.").arg(page.selectedProfile); color: App.Theme.textSecondary; font.pixelSize: 16 * page.couchScale; wrapMode: Text.WordWrap }
                Label { Layout.fillWidth: true; visible: page.confirmationKind !== "optiscaler_remove"; text: page.pendingPlan && page.pendingPlan.warnings && page.pendingPlan.warnings.length ? String(page.pendingPlan.warnings[0]) : qsTr("The estimate does not guarantee the same change in free disk space."); color: App.Theme.warning; font.pixelSize: 15 * page.couchScale; wrapMode: Text.WordWrap }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.fillWidth: true
                    CouchButton { id: detailsCancelButton; Layout.fillWidth: true; couchScale: page.couchScale; text: qsTr("Cancel"); focus: page.confirmationOpen && page.confirmationChoice === 0; onClicked: page.closeConfirmation() }
                    CouchButton { id: detailsConfirmButton; Layout.fillWidth: true; couchScale: page.couchScale; text: qsTr("Start task"); focus: page.confirmationOpen && page.confirmationChoice === 1; onClicked: { page.confirmationChoice = 1; page.handleAction("Confirm") } }
                }
        }
    }
}
