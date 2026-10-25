pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../../components"
import "../.." as App

ScrollView {
    id: panel
    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData.id || "")
    readonly property var scrollFlickable: contentItem
    property var statusData: ({})
    property string backend: "off"
    property string api: "auto"
    property string executable: ""
    property string toggleKey: "Home"
    property var effects: []
    property var parameters: ({})
    property bool dirty: false
    property bool loaded: false
    property bool confirming: false
    property string error: ""
    property int padIndex: 0
    property var catalogue: []
    readonly property var orderedCatalogue: {
        var chosen = effects.map(function(key) { return catalogue.find(function(item) { return item.id === key }) }).filter(function(item) { return !!item })
        return chosen.concat(catalogue.filter(function(item) { return effects.indexOf(item.id) < 0 }))
    }
    property int editorRevision: 0
    property int submittedRevision: -1
    property int awaitingOperation: 0
    property string savedStamp: ""
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    function padControls(item, result) {
        if (!item || !item.visible || !item.enabled) return
        if (item.postActionType !== undefined) result.push(item)
        var children = item.children || []
        for (var i = 0; i < children.length; ++i) padControls(children[i], result)
    }
    function handlePadAction(action) {
        var controls = []; padControls(panel, controls)
        if (!controls.length) return
        if (action === "NavigateDown" || action === "NavigateUp")
            padIndex = Math.max(0, Math.min(controls.length - 1, padIndex + (action === "NavigateDown" ? 1 : -1)))
        padIndex = Math.min(padIndex, controls.length - 1)
        var item = controls[padIndex]
        item.forceActiveFocus()
        if (action === "Confirm" || action === "NavigateLeft" || action === "NavigateRight") {
            var direction = action === "NavigateLeft" ? -1 : 1
            if (item.postActionType === "combo") {
                item.currentIndex = (item.currentIndex + direction + item.count) % item.count
                item.activated(item.currentIndex)
            } else if (item.postActionType === "text") {
                item.forceActiveFocus()
            } else if (item.postActionType === "slider") {
                item.value = Math.max(item.from, Math.min(item.to, item.value + direction * item.stepSize))
                item.moved()
            } else if (action === "Confirm") {
                if (item.postActionType === "check") item.toggle()
                item.clicked()
            }
        }
        var flick = panel.scrollFlickable
        if (flick && flick.contentItem) {
            var y = item.mapToItem(flick.contentItem, 0, 0).y
            flick.contentY = Math.max(0, Math.min(flick.contentHeight - panel.height, y - panel.height / 3))
        }
    }

    function touch() { dirty = true; editorRevision += 1; error = ""; confirming = false }
    function stamp(result) { return JSON.stringify([result.backend, result.configuredAt, result.installedAt, result.phase, result.effects, result.parameters]) }
    function defaults() {
        var values = ({})
        catalogue.forEach(function(effect) { effect.parameters.forEach(function(p) { values[p.key] = p.default }) })
        return values
    }
    function loadEditor(result) {
        backend = String(result.backend || "off"); executable = String(result.executable || "")
        api = String(result.api || "auto"); toggleKey = String(result.toggleKey || "Home")
        effects = (result.effects || []).slice(); parameters = Object.assign(defaults(), result.parameters || {})
        savedStamp = stamp(result); dirty = false; loaded = true
    }
    function refresh() {
        if (!controller || !controller.postProcessingStatus || !gameId) return
        var result = controller.postProcessingStatus(gameId) || ({})
        statusData = result
        if (!catalogue.length && result.catalogue) catalogue = result.catalogue
        if (awaitingOperation && result.completedOperationId >= awaitingOperation) {
            awaitingOperation = 0
            if (result.operationError) error = String(result.operationError)
            else if (editorRevision === submittedRevision) loadEditor(result)
        } else if (!awaitingOperation && !dirty && !result.busy && result.success && (!loaded || savedStamp !== stamp(result))) {
            loadEditor(result)
        }
    }
    function watchOperation(started) {
        if (started) {
            submittedRevision = editorRevision
            var result = controller.postProcessingStatus(gameId) || ({})
            awaitingOperation = Number(result.operationId || 0); statusData = result
            error = ""; refresh()
        } else error = qsTr("The post-processing task could not be started")
    }
    function apply() {
        confirming = false
        var started = controller && controller.applyPostProcessing
                && controller.applyPostProcessing(gameId, {"backend": backend, "api": api,
                    "effects": effects, "parameters": parameters, "toggleKey": toggleKey, "executable": executable})
        watchOperation(started)
    }
    function setEffect(key, enabled) {
        var next = effects.slice()
        var index = next.indexOf(key)
        if (enabled && index < 0) next.push(key)
        if (!enabled && index >= 0) next.splice(index, 1)
        effects = next; touch()
    }
    function moveEffect(key, direction) {
        var next = effects.slice(); var index = next.indexOf(key)
        var target = index + direction
        if (index < 0 || target < 0 || target >= next.length) return
        var other = next[target]; next[target] = key; next[index] = other
        effects = next; touch()
    }
    function preset(name) {
        effects = name === "colour" ? ["vibrance", "curves"] : name === "clarity" ? ["fxaa", "lumasharpen"] : []
        parameters = Object.assign(defaults(), name === "colour" ? {"Vibrance": 0.12, "Contrast": 0.1} : name === "clarity" ? {"sharp_strength": 0.35} : ({}))
        touch()
    }
    function resetEffect(key) {
        var next = Object.assign({}, parameters)
        catalogue.forEach(function(effect) { if (effect.id === key) effect.parameters.forEach(function(p) { next[p.key] = p.default }) })
        parameters = next; touch()
    }
    function resetAll() { preset("neutral"); toggleKey = "Home" }
    function updateParameter(key, value) { var next = Object.assign({}, parameters); next[key] = value; parameters = next; touch() }
    function parameterLabel(key) {
        var labels = {"Sharpness": qsTr("Sharpness"), "Denoise": qsTr("Denoise"),
            "Subpixel filtering": qsTr("Subpixel filtering"), "Edge threshold": qsTr("Edge threshold"),
            "Darkness threshold": qsTr("Darkness threshold"), "Search steps": qsTr("Search steps"),
            "Diagonal steps": qsTr("Diagonal steps"), "Corner rounding": qsTr("Corner rounding"),
            "Vibrance": qsTr("Vibrance"), "Gamma": qsTr("Gamma"), "Exposure": qsTr("Exposure"),
            "Saturation": qsTr("Saturation"), "Bleach": qsTr("Bleach"), "Contrast": qsTr("Contrast"),
            "Sharpening limit": qsTr("Sharpening limit"),
            "Denoised Luma Sharpening": qsTr("Denoised Luma Sharpening"),
            "SMAA (colour edges)": qsTr("SMAA (colour edges)")}
        return labels[key] || key
    }
    Component.onCompleted: refresh()
    onGameIdChanged: { statusData = ({}); dirty = false; loaded = false; confirming = false; awaitingOperation = 0; savedStamp = ""; error = ""; refresh() }
    Timer { interval: 1200; running: panel.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        width: panel.availableWidth
        spacing: 12
        SurfaceCard {
            objectName: "postBackendCard"
            Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: qsTr("Post-processing"); color: App.Theme.text; font.pixelSize: App.Theme.fontTitle; wrapMode: Text.WordWrap }
                    StatusBadge { objectName: "postProcessingState"
                        text: panel.statusData.busy ? qsTr("Working…") : panel.statusData.statusState === "error" ? qsTr("Shader / configuration error")
                              : panel.statusData.runtimeLoaded ? qsTr("Runtime loaded · effects not verified")
                              : panel.statusData.installed ? qsTr("Installed · runtime not verified") : qsTr("Off")
                        status: panel.statusData.statusState === "error" ? "error" : panel.statusData.runtimeLoaded ? "success" : "info" }
                }
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
                    text: qsTr("Optional image effects, not an FPS optimisation. Defaults are neutral. Use one backend per game.") }
                GridLayout {
                    Layout.fillWidth: true; columns: panel.availableWidth > 760 ? 3 : 1; columnSpacing: 12; rowSpacing: 8
                    ColumnLayout { Layout.fillWidth: true
                        Label { text: qsTr("Backend"); color: App.Theme.textSecondary }
                        AppComboBox { property string postActionType: "combo"; objectName: "postProcessingBackend"; Layout.fillWidth: true
                            model: [qsTr("Off"), "vkBasalt", "ReShade"]; currentIndex: ["off", "vkbasalt", "reshade"].indexOf(panel.backend)
                            onActivated: function(index) { panel.backend = ["off", "vkbasalt", "reshade"][index]; panel.effects = []; panel.touch() } } }
                    ColumnLayout { Layout.fillWidth: true
                        Label { text: qsTr("Graphics path"); color: App.Theme.textSecondary }
                        AppComboBox { property string postActionType: "combo"; objectName: "postProcessingApi"; Layout.fillWidth: true
                            readonly property var values: ["auto", "vulkan", "d3d9", "d3d10", "d3d11", "d3d12", "opengl"]
                            model: [qsTr("Automatic"), "Vulkan", "D3D9", "D3D10", "D3D11", "D3D12", "OpenGL"]; currentIndex: values.indexOf(panel.api)
                            onActivated: function(index) { panel.api = values[index]; panel.touch() } } }
                    ColumnLayout { Layout.fillWidth: true
                        Label { text: qsTr("Effect toggle"); color: App.Theme.textSecondary }
                        AppComboBox { property string postActionType: "combo"; Layout.fillWidth: true
                            model: ["Home", "End", "Insert", "F10", "F11"]; currentIndex: model.indexOf(panel.toggleKey)
                            onActivated: function(index) { panel.toggleKey = model[index]; panel.touch() } } }
                }
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
                    text: qsTr("Executable: %1 · %2-bit · API: %3").arg(panel.executable || "?")
                          .arg(String((panel.statusData.detected || {}).bits || "?"))
                          .arg(panel.api === "auto" ? String((panel.statusData.detected || {}).api || "unknown") : panel.api) }
                Label { visible: panel.api === "auto" && panel.statusData.apiDetectionInconclusive === true
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.warning
                    text: qsTr("Automatic API detection is inconclusive. Choose the API used by the game; this does not mean the game is unsupported.") }
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
                    text: qsTr("vkBasalt: Vulkan or DXVK/VKD3D. ReShade: Windows D3D/OpenGL. Auto uses EXE imports; select the actual API if detection is inconclusive.") }
                AppTextField { property string postActionType: "text"; Layout.fillWidth: true; text: panel.executable
                    placeholderText: qsTr("Main executable path relative to the game folder (keyboard)"); Accessible.name: placeholderText
                    onTextEdited: { panel.executable = text; panel.touch() } }
                Label { visible: panel.backend === "reshade" && (panel.statusData.loaderConflicts || []).length > 0
                    Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; color: App.Theme.warning
                    text: qsTr("ReShade loader conflict. Existing files stay intact: %1").arg((panel.statusData.loaderConflicts || []).map(function(c) { return String(c.owner) + ": " + String(c.path) }).join("\n"))
                          + "\n" + qsTr("Keep OptiScaler and use vkBasalt, or restore/remove the upscaler on its own page first. No verified chaining is configured.") }
                Label { visible: panel.backend === "reshade" && panel.statusData.upscalerLoaderMissing === true
                    Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; color: App.Theme.warning
                    text: qsTr("The upscaler profile records a loader which is now missing. Verify its installation before installing ReShade: %1").arg(String(panel.statusData.upscalerLoaderPath || "")) }
                Label { visible: panel.backend === "reshade"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
                    text: qsTr("OptiScaler upstream documents plugin/LoadReshade loading. This managed installation keeps it unchanged until coexistence is verified for the installed release.") }
                Label { visible: panel.dirty; Layout.fillWidth: true; color: App.Theme.warning; text: qsTr("Unsaved changes"); wrapMode: Text.WordWrap }
                Label { Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WordWrap
                    text: qsTr("Apply saves the configuration. Restart the game to load changed effects, order or parameters. The shortcut only toggles the effects already loaded.")
                          + " " + qsTr("Close the game before applying, installing or removing the integration.") }
                Flow { Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 8
                    AppButton { property string postActionType: "button"; objectName: "postApply"; kind: "primary"
                        text: panel.confirming ? qsTr("Confirm") : panel.statusData.installed && panel.backend === panel.statusData.backend ? qsTr("Apply settings") : qsTr("Install / apply")
                        enabled: !panel.statusData.busy && !panel.awaitingOperation
                        onClicked: {
                            if (panel.confirming || panel.backend === "off" || (panel.statusData.installed && panel.backend === panel.statusData.backend)) panel.apply()
                            else panel.confirming = true
                        } }
                    AppButton { property string postActionType: "button"; text: qsTr("Off / restore"); enabled: !panel.statusData.busy && !panel.awaitingOperation
                        onClicked: { panel.backend = "off"; panel.apply() } }
                }
                Label { visible: panel.confirming; Layout.fillWidth: true; color: App.Theme.warning; wrapMode: Text.WordWrap
                    text: qsTr("Install/apply verified components for this game? Existing loader DLLs will not be overwritten. Turning Off preserves custom presets in the game folder.") }
            }
        }
        SurfaceCard {
            objectName: "postPresetsCard"; Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: 8
                Label { text: qsTr("Presets"); color: App.Theme.text; font.weight: Font.DemiBold }
                Flow { Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 8
                    AppButton { property string postActionType: "button"; compact: true; text: qsTr("Neutral"); onClicked: panel.preset("neutral") }
                    AppButton { property string postActionType: "button"; compact: true; text: qsTr("Gentle colour"); onClicked: panel.preset("colour") }
                    AppButton { property string postActionType: "button"; compact: true; text: qsTr("AA + light sharpening"); onClicked: panel.preset("clarity") }
                    AppButton { property string postActionType: "button"; objectName: "postResetAll"; compact: true
                        text: qsTr("Restore default post-processing settings"); onClicked: panel.resetAll() }
                }
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
                    text: qsTr("Reset changes the editor only: no effects, default parameter values and Home shortcut. Backend/API, imported files, backups and upscalers are kept. Press Apply to save.") }
            }
        }
        Label { Layout.fillWidth: true; color: App.Theme.text; wrapMode: Text.WordWrap
            text: qsTr("Effect order: %1").arg(panel.effects.join(" → ") || qsTr("None")) }
        Label { Layout.fillWidth: true; color: App.Theme.warning; wrapMode: Text.WordWrap
            text: qsTr("Sharpening can duplicate the upscaler's sharpening. Depth effects (AO/DoF) are not enabled: depth access must be verified in each game.") }
        Repeater {
            model: panel.orderedCatalogue
            delegate: PostProcessingEffect {
                required property var modelData
                objectName: "postEffect_" + modelData.id
                Layout.fillWidth: true
                visible: panel.backend !== "off" && modelData.backends.indexOf(panel.backend) >= 0
                effect: modelData
                parameters: panel.parameters
                selected: panel.effects.indexOf(String(modelData.id)) >= 0
                position: panel.effects.indexOf(String(modelData.id))
                effectCount: panel.effects.length
                displayName: panel.parameterLabel(String(modelData.label))
                labelForParameter: panel.parameterLabel
                onToggled: function(value) { panel.setEffect(String(modelData.id), value) }
                onReordered: function(direction) { panel.moveEffect(String(modelData.id), direction) }
                onEdited: function(key, value) { panel.updateParameter(key, value) }
                onResetRequested: panel.resetEffect(String(modelData.id))
            }
        }
        SurfaceCard {
            objectName: "postDiagnosticsCard"; Layout.fillWidth: true
            contentItem: ColumnLayout {
                spacing: 8
                Label { text: qsTr("Diagnostics and installation"); color: App.Theme.text; font.weight: Font.DemiBold }
                Label { Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WordWrap
                    text: qsTr("Game: %1 · session: %2").arg(String((panel.statusData.diagnostics || {}).appId || panel.gameId))
                          .arg(String((panel.statusData.diagnostics || {}).sessionAt || qsTr("No runner session"))) }
                Label { visible: panel.statusData.installed === true; Layout.fillWidth: true; color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere
                    text: String(panel.statusData.backend || "") + " " + String(panel.statusData.version || "") + "\n"
                          + String((panel.statusData.diagnostics || {}).configPath || "") }
                Label { visible: panel.statusData.installed === true; Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WordWrap
                    text: qsTr("Saved effects: %1").arg(((panel.statusData.diagnostics || {}).effects || []).join(" → ") || qsTr("None")) }
                Label { visible: !!panel.statusData.runtimeEvidence; Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WordWrap
                    text: qsTr("Module observed in game PID %1 at %2. This alone does not verify the image effects.")
                          .arg(String((panel.statusData.runtimeEvidence || {}).processId || "?"))
                          .arg(String((panel.statusData.runtimeEvidence || {}).observedAt || "")) }
                Label { Layout.fillWidth: true; color: App.Theme.warning; wrapMode: Text.WordWrap
                    visible: panel.statusData.installed === true && !panel.statusData.runtimeLoaded
                    text: panel.effects.length === 0 ? (panel.backend === "vkbasalt" ? qsTr("Neutral configuration: vkBasalt is not activated without selected effects.") : qsTr("No effects selected. The backend stays installed."))
                          : (panel.statusData.steamRunner || {}).configured === false ? qsTr("Steam runner is missing or its launch options are invalid. Copy the command below into Steam launch options.")
                          : qsTr("No fresh runtime evidence for these saved settings. Restart through the configured launcher, then refresh diagnostics.") }
                TextArea { visible: panel.gameData.launcher === "Steam"; Layout.fillWidth: true
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; color: App.Theme.text
                    padding: 12; font.pixelSize: App.Theme.fontCaption
                    background: Rectangle { color: App.Theme.input; radius: App.Theme.radiusSmall; border.color: App.Theme.border }
                    text: String((panel.statusData.steamRunner || {}).command || "") }
                Label { Layout.fillWidth: true; color: App.Theme.danger; wrapMode: Text.WrapAnywhere
                    visible: text.length > 0; text: panel.error || String(panel.statusData.error || panel.statusData.compatibilityError || "") }
                Label { Layout.fillWidth: true; color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere
                    text: String(panel.statusData.logPath || "") + "\n" + (panel.statusData.runtimeErrors || []).join("\n") }
                Label { Layout.fillWidth: true; color: App.Theme.warning; wrapMode: Text.WordWrap
                    visible: (panel.statusData.antiCheat || []).length > 0
                    text: qsTr("Anti-cheat files detected. Single-player/offline only; do not enter online modes with these modifications.") }
                Flow { Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 8
                    AppButton { property string postActionType: "button"; compact: true; text: qsTr("Import ReShade preset")
                        enabled: panel.statusData.backend === "reshade" && !panel.statusData.busy; onClicked: presetDialog.open() }
                    AppButton { property string postActionType: "button"; compact: true; text: qsTr("Select LUT")
                        enabled: panel.statusData.backend === "vkbasalt" && !panel.statusData.busy; onClicked: lutDialog.open() }
                    AppButton { property string postActionType: "button"; compact: true; text: qsTr("Apply launcher profile")
                        visible: panel.gameData.launcher === "Heroic" || panel.gameData.launcher === "Lutris"
                        onClicked: { var result = panel.controller.applyLauncherIntegration(panel.gameId); if (result.success === false) panel.error = String(result.error || result.message || "") } }
                }
                Label { Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WordWrap
                    text: panel.backend === "reshade" ? qsTr("ReShade: Ctrl + selected key toggles effects; Home opens its in-game interface.")
                          : qsTr("vkBasalt uses the selected X11 key. Restart the game after changing configuration; Wayland-native keyboard toggling is not guaranteed.") }
            }
        }
    }
    FileDialog { id: presetDialog; title: qsTr("Import ReShade preset"); nameFilters: ["INI (*.ini)"]
        onAccepted: panel.watchOperation(panel.controller.importPostProcessingPreset(panel.gameId, String(selectedFile))) }
    FileDialog { id: lutDialog; title: qsTr("Select LUT"); nameFilters: ["LUT (*.png *.cube *.CUBE)"]
        onAccepted: panel.watchOperation(panel.controller.importPostProcessingLut(panel.gameId, String(selectedFile))) }
}
