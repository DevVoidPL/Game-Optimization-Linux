pragma ComponentBehavior: Bound
import QtQuick
import "components"
import "../pages/details" as Details
import ".." as App

FocusScope {
    id: root
    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData.id || "")
    property real couchScale: 1
    property string confirmation: ""
    property string textTarget: ""
    readonly property var padFocusedItem: executablePicker.opened ? executablePicker.padFocusedItem : keyboard.opened ? keyboard : menu.padFocusedItem
    readonly property alias editor: state
    function refresh() { state.refresh() }
    function cycle(values, value, direction) {
        return values[(Math.max(0, values.indexOf(value)) + direction + values.length) % values.length]
    }
    function ask(action) { confirmation = action; menu.currentIndex = 0 }
    function editText(target, value, title) { textTarget = target; keyboard.open(value, title) }
    function handlePadAction(action) {
        if (executablePicker.opened) return executablePicker.handlePadAction(action)
        if (keyboard.opened) { keyboard.handleAction(action); return true }
        if (action === "Back" && confirmation) { confirmation = ""; menu.currentIndex = 0; return true }
        return menu.handlePadAction(action)
    }
    function rows() {
        var idle = state.loaded && !state.statusData.busy && !state.awaitingOperation
        if (confirmation) return [
            {id:"cancel", title:qsTr("Cancel"), trigger:function() { root.confirmation = "" }},
            {id:"confirm", title:qsTr("Confirm"), enabled:idle,
                description:qsTr("Close the game first. Existing files are backed up. Use offline games only; injection may conflict with anti-cheat."),
                trigger:function() {
                    var action = root.confirmation; root.confirmation = ""
                    if (action === "apply") state.apply()
                    else state.watchOperation(root.controller.applyPostProcessing(state.gameId, {backend:"off"}))
                }}]
        var result = [
            {id:"backend", title:qsTr("Backend"), value:state.backend, enabled:idle,
                description:qsTr("Optional image effects, not an FPS optimisation. Use one backend per game."),
                adjust:function(d) { state.backend = root.cycle(["off","vkbasalt","reshade"], state.backend, d); state.effects = []; state.touch() }},
            {id:"api", title:qsTr("Graphics path"), value:state.api, enabled:idle,
                description:qsTr("Choose the API used by the game if automatic detection is inconclusive."),
                adjust:function(d) { state.api = root.cycle(["auto","vulkan","d3d9","d3d10","d3d11","d3d12","opengl"], state.api, d); state.touch() }},
            {id:"executable", title:qsTr("Main executable"), value:state.executable || qsTr("Not detected"), enabled:idle,
                trigger:function() {
                    if (root.controller && root.controller.getOptiScalerStatus) executablePicker.open()
                    else root.editText("executable", state.executable, qsTr("Main executable"))
                }},
            {id:"toggle", title:qsTr("Effect toggle"), value:state.toggleKey, enabled:idle,
                adjust:function(d) { state.toggleKey = root.cycle(["Home","End","Insert","F10","F11"], state.toggleKey, d); state.touch() }}
        ]
        if (state.backend !== "off") {
            [{id:"neutral", title:qsTr("Neutral")}, {id:"colour", title:qsTr("Gentle colour")}, {id:"clarity", title:qsTr("AA + light sharpening")}].forEach(function(p) {
                result.push({id:p.id, title:p.title, enabled:idle, trigger:function() { state.preset(p.id) }})
            })
            state.orderedCatalogue.forEach(function(effect) {
                if (effect.backends.indexOf(state.backend) < 0) return
                var selected = state.effects.indexOf(effect.id) >= 0
                result.push({id:effect.id, title:state.parameterLabel(effect.label), value:selected ? qsTr("On") : qsTr("Off"), enabled:idle,
                    trigger:function() { state.setEffect(effect.id, !selected) }})
                if (!selected) return
                effect.parameters.forEach(function(p) {
                    result.push({id:p.key, title:state.parameterLabel(p.label || p.key), value:String(state.parameters[p.key]), enabled:idle,
                        adjust:function(d) {
                            var step = p.integer ? 1 : (p.maximum - p.minimum) / 100
                            var value = Number(state.parameters[p.key]) + d * step
                            state.updateParameter(p.key, Number(Math.max(p.minimum, Math.min(p.maximum, value)).toFixed(5)))
                        }})
                })
                result.push({id:effect.id + "-order", title:qsTr("Effect order"), value:String(state.effects.indexOf(effect.id) + 1), enabled:idle,
                    adjust:function(d) { state.moveEffect(effect.id, d) }})
                result.push({id:effect.id + "-reset", title:qsTr("Reset effect"), enabled:idle, trigger:function() { state.resetEffect(effect.id) }})
            })
            result.push({id:"import", title:state.backend === "reshade" ? qsTr("Import ReShade preset") : qsTr("Select LUT"), enabled:idle,
                description:qsTr("Enter a local file path with the on-screen keyboard."),
                trigger:function() { root.editText(state.backend === "reshade" ? "preset" : "lut", "", qsTr("Local file path")) }})
        }
        result.push({id:"reset", title:qsTr("Restore defaults"), enabled:idle, trigger:function() { state.resetAll() }})
        result.push({id:"apply", title:qsTr("Install / apply"), enabled:idle,
            description:qsTr("Saves for the next game launch. Effects and runtime loading are verified separately."), trigger:function() { root.ask("apply") }})
        if (state.statusData.installed) result.push({id:"remove", title:qsTr("Remove integration"), enabled:idle, trigger:function() { root.ask("remove") }})
        result.push({id:"diagnostics", title:qsTr("Runtime diagnostics"),
            value:state.statusData.runtimeLoaded ? qsTr("Runtime loaded · effects not verified") : qsTr("Runtime not verified"),
            description:[String(state.statusData.logPath || ""), (state.statusData.runtimeErrors || []).join("\n"),
                (state.statusData.loaderConflicts || []).map(function(c) { return String(c.owner) + ": " + String(c.path) }).join("\n"),
                String((state.statusData.steamRunner || {}).command || "")].filter(function(s) { return s.length }).join("\n")})
        if ((state.statusData.antiCheat || []).length)
            result.push({id:"anti-cheat", title:qsTr("Anti-cheat detected"), description:qsTr("Single-player/offline only. Do not enter online modes with these modifications.")})
        if (["Heroic","Lutris"].indexOf(String(gameData.launcher || "")) >= 0)
            result.push({id:"launcher", title:qsTr("Apply launcher profile"), enabled:idle, trigger:function() {
                var result = root.controller.applyLauncherIntegration(state.gameId)
                if (!result.success) state.error = String(result.error || result.message || "")
            }})
        result.push({id:"refresh", title:qsTr("Refresh diagnostics"), trigger:function() { root.refresh() }})
        return result
    }
    // The shared editor owns serialization, async revisions and conflict
    // handling. Its desktop controls are not the Couch interaction surface.
    Details.PostProcessingPanel {
        id: state; objectName: "couchPostProcessingState"; visible: false; width: 1; height: 1
        controller: root.controller; gameData: root.gameData
    }
    CouchOptionList {
        id: menu; anchors.fill: parent; couchScale: root.couchScale
        visible: !executablePicker.opened
        focusSuspended: keyboard.opened || executablePicker.opened
        title: root.confirmation ? qsTr("Review post-processing changes") : qsTr("Post-processing")
        status: [state.dirty ? qsTr("Unsaved changes") : qsTr("Saved settings"),
            state.statusData.busy ? qsTr("Working…") : state.statusData.runtimeLoaded ? qsTr("Runtime loaded · effects not verified") : qsTr("Runtime not verified"),
            App.I18n.message(String(state.error || state.statusData.operationError || state.statusData.error || state.statusData.compatibilityError || ""))].filter(function(s) { return s.length }).join(" · ")
        rows: root.rows()
    }
    Timer { interval: 1500; repeat: true; running: root.visible; onTriggered: root.refresh() }
    CouchOnScreenKeyboard {
        id: keyboard; anchors.fill: parent; couchScale: root.couchScale
        onAccepted: function(value) {
            if (root.textTarget === "executable") { state.executable = value; state.touch() }
            else if (root.textTarget === "preset") state.watchOperation(root.controller.importPostProcessingPreset(state.gameId, value))
            else state.watchOperation(root.controller.importPostProcessingLut(state.gameId, value))
            menu.focusCurrent()
        }
        onCancelled: menu.focusCurrent()
    }
    onGameIdChanged: {
        keyboard.close(false)
        confirmation = ""; textTarget = ""; menu.currentIndex = 0
    }
    CouchExecutablePicker {
        id: executablePicker; anchors.fill: parent
        controller: root.controller; gameId: root.gameId; couchScale: root.couchScale
        onSelected: function(path) { state.executable = path; state.touch(); menu.focusCurrent() }
        onCancelled: menu.focusCurrent()
    }
}