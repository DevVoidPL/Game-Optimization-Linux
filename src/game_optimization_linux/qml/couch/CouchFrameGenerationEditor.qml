pragma ComponentBehavior: Bound
import QtQuick
import "components"
import "../pages/details" as Details

FocusScope {
    id: root
    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData.id || "")
    property real couchScale: 1
    property string confirmation: ""
    readonly property alias editor: state
    readonly property var padFocusedItem: executablePicker.opened ? executablePicker.padFocusedItem : keyboard.opened ? keyboard : menu.padFocusedItem
    signal optiScalerRequested()
    function refresh() { state.refresh() }
    function recheck() { state.recheck() }
    function cycle(values, value, direction) {
        return values[(Math.max(0, values.indexOf(value)) + direction + values.length) % values.length]
    }
    function ask(action) { confirmation = action; menu.currentIndex = 0 }
    function handlePadAction(action) {
        if (executablePicker.opened) return executablePicker.handlePadAction(action)
        if (keyboard.opened) { keyboard.handleAction(action); return true }
        if (action === "Back" && confirmation) { confirmation = ""; menu.currentIndex = 0; return true }
        return menu.handlePadAction(action)
    }
    function rows() {
        var idle = !state.statusData.busy && !state.awaitingOperation
        if (confirmation) return [
            {id:"cancel", title:qsTr("Cancel"), trigger:function() { root.confirmation = "" }},
            {id:"confirm", title:qsTr("Confirm"), enabled:idle,
                description:confirmation === "install" ? qsTr("Download the shared component from its official source. Installation does not activate any game.")
                    : confirmation === "remove" ? qsTr("Remove the shared component. Games selecting it will need reinstallation or Off. Purchased files are kept.")
                    : confirmation === "setup-empty-steam" ? qsTr("Close Steam completely. This writes only an already empty Launch Options field for this game, with a full backup. Existing commands are never replaced. This is an offline file edit, not a Steam API.")
                    : qsTr("Close the launcher first. Runner setup preserves existing arguments with a reversible backup and can grant the launcher Flatpak access to the component and game configuration."),
                trigger:function() { var action = root.confirmation; root.confirmation = ""; state.run(action, action === "setup-empty-steam" ? {confirmEmptySteamOptions:true} : {}) }}]
        var result = [
            {id:"backend", title:qsTr("Frame generator"), value:state.backendNames[Math.max(0, state.backendIds.indexOf(state.backend))], enabled:idle,
                description:qsTr("Select a generator, resolve requirements, then Apply. Selecting alone does not change the saved launch profile."),
                adjust:function(d) { state.chooseBackend(state.backendIds.indexOf(root.cycle(state.backendIds, state.backend, d))) }},
            {id:"api", title:qsTr("Graphics path"), value:state.api, enabled:idle,
                adjust:function(d) { state.api = root.cycle(["auto","vulkan","dxvk","vkd3d"], state.api, d); state.touch() }}
        ]
        result.push({id:"executable", title:qsTr("Main executable"), value:String(state.statusData.executable || qsTr("Not detected")),
            enabled:idle, trigger:function() { executablePicker.open() }})
        if (state.backend !== "off") {
            result.push({id:"install", title:state.component.installed ? qsTr("Update / reinstall") : qsTr("Download and install"),
                value:String(state.component.version || state.component.availableVersion || ""), enabled:idle,
                description:String(state.component.source || ""), trigger:function() { root.ask("install") }})
            if (state.needsLossless) {
                result.push({id:"dll", title:qsTr("Lossless Scaling file"), value:state.dllPath || qsTr("Not found"), enabled:idle,
                    description:state.backend === "lsfg" ? qsTr("Requires separately purchased Lossless Scaling: lsfg-vk.dll from the lsfg-vk Steam branch.")
                        : qsTr("Requires separately purchased Lossless Scaling: Lossless.dll from the public release."),
                    trigger:function() { keyboard.open(state.dllPath, qsTr("Lossless Scaling file")) }})
                var candidates = (state.statusData.dllCandidates || {})[state.backend] || []
                if (candidates.length) result.push({id:"discover", title:qsTr("Discovered files"), value:state.dllPath, enabled:idle,
                    adjust:function(d) { state.dllPath = root.cycle(candidates, state.dllPath, d); state.touch() }})
                result.push({id:"validate", title:qsTr("Validate models"), enabled:idle && Boolean(state.component.installed) && !!state.dllPath,
                    description:qsTr("File validation is not proof of generated frames."), trigger:function() { state.run("validate") }})
                if (state.statusData.optiScalerPresent) result.push({id:"internal", title:qsTr("In-game frame generation is disabled"),
                    value:state.internalFgOff ? qsTr("Yes") : qsTr("No"), enabled:idle,
                    trigger:function() { state.internalFgOff = !state.internalFgOff; state.touch() }})
            }
            if (state.optiConflict) result.push({id:"optiscaler", title:qsTr("Open OptiScaler"),
                description:qsTr("afmf-linux conflicts with OptiScaler. Nothing is removed automatically."), trigger:function() { root.optiScalerRequested() }})
            result.push({id:"advanced", title:qsTr("Advanced settings"), value:state.advanced ? qsTr("On") : qsTr("Off"),
                trigger:function() { state.advanced = !state.advanced }})
            state.specifications.forEach(function(spec) {
                var current = state.currentSettings[spec.key]
                var value = spec.type === "bool" ? (current ? qsTr("On") : qsTr("Off"))
                    : spec.type === "choice" ? String(spec.labels[Math.max(0, spec.choices.indexOf(current))])
                    : String(Number((Number(current) * Number(spec.displayScale || 1)).toFixed(2))) + " " + String(spec.unit || "")
                result.push({id:spec.key, title:spec.title, value:value, enabled:idle, description:spec.description,
                    adjust:function(d) {
                        var next = spec.type === "bool" ? !current : spec.type === "choice" ? root.cycle(spec.choices, current, d)
                            : Number(Math.max(spec.min, Math.min(spec.max, Number(current) + d * spec.step)).toFixed(4))
                        state.edit(spec.key, next)
                    }})
            })
            result.push({id:"reset", title:qsTr("Reset editor to defaults"), enabled:idle, trigger:function() { state.reset() }})
        }
        result.push({id:"apply", title:qsTr("Apply for next launch"), enabled:idle && state.applyAllowed,
            description:state.blockers.length ? state.message(state.blockers.join("\n")) : qsTr("Restart the game. Runtime loading and generated frames need separate verification."),
            trigger:function() { state.run("apply") }})
        result.push({id:"runner", title:qsTr("Set up runner"), enabled:idle && String(root.gameData.launcher || "") !== "Steam",
            description:String(root.gameData.launcher || "") === "Steam"
                ? qsTr("Use Steam Library > Properties > General > Launch Options. Preserve existing arguments.") + "\n" + String((state.statusData.runner || {}).command || "") : "",
            trigger:function() { root.ask("setup-runner") }})
        if (String(root.gameData.launcher || "") === "Steam" && !state.statusData.launcherFlatpak)
            result.push({id:"empty-steam-runner", title:qsTr("Set up empty Steam Launch Options"), enabled:idle,
                description:qsTr("Controller-only setup after closing Steam. Refuses any existing command; keeps a full backup. AppImage must remain at its current path."),
                trigger:function() { root.ask("setup-empty-steam") }})
        result.push({id:"diagnostics", title:qsTr("Runtime diagnostics"),
            value:state.statusData.runtimeState === "loaded" ? qsTr("Layer loaded") : qsTr("Runtime not verified"),
            description:[state.message(state.component.error), String((state.component.environmentCheck || {}).details || ""),
                qsTr("Runtime PIDs: %1").arg((state.statusData.runtimePids || []).join(", ") || "-"),
                String((state.statusData.runtimeEvidence || {}).log || ""),
                ((state.statusData.runtimeEvidence || {}).errors || []).join("\n")].join("\n")})
        if ((state.statusData.steamUpdate || {}).blocked)
            result.push({id:"steam-update", title:qsTr("Steam update blocks Apply"),
                description:qsTr("Finish or pause the update in Steam, then refresh requirements."),
                trigger:function() { Qt.openUrlExternally("steam://open/downloads") }})
        if (state.statusData.runnerBackup) result.push({id:"restore-runner", title:qsTr("Restore runner settings"), enabled:idle,
            trigger:function() { root.ask("restore-runner") }})
        if (state.component.managed) result.push({id:"remove", title:qsTr("Remove component"), enabled:idle, trigger:function() { root.ask("remove") }})
        if (state.statusData.busy) result.push({id:"cancel-download", title:qsTr("Cancel download"),
            enabled:["install","download"].indexOf(String((state.statusData.progress || {}).phase || "")) >= 0,
            trigger:function() { root.controller.cancelFrameGeneration() }})
        result.push({id:"refresh", title:qsTr("Refresh diagnostics"), trigger:function() { root.recheck() }})
        return result
    }
    Details.FrameGenerationPanel {
        id: state; objectName: "couchFrameGenerationState"; visible: false; width: 1; height: 1
        controller: root.controller; gameData: root.gameData
    }
    CouchOptionList {
        id: menu; anchors.fill: parent; couchScale: root.couchScale
        visible: !executablePicker.opened
        focusSuspended: keyboard.opened || executablePicker.opened
        title: root.confirmation ? qsTr("Review frame-generation changes") : qsTr("Frame generation")
        status: [state.dirty ? qsTr("Unsaved changes") : qsTr("Saved settings"),
            state.statusData.busy ? qsTr("Working…") : state.statusData.generationVerified ? qsTr("Generated frames confirmed in session log") : qsTr("Generated frames not verified"),
            state.operationError(), state.message(state.blockers.join("\n")),
            !state.hardwareReady && state.backend !== "off" ? qsTr("GPU requirements not verified") : "",
            !state.environmentReady && state.backend !== "off" ? qsTr("Launching environment not ready; set up the runner") : ""].filter(function(s) { return s.length }).join(" · ")
        rows: root.rows()
    }
    Timer { interval: 1500; repeat: true; running: root.visible; onTriggered: root.refresh() }
    CouchOnScreenKeyboard {
        id: keyboard; anchors.fill: parent; couchScale: root.couchScale
        onAccepted: function(value) { state.dllPath = value; state.touch(); menu.focusCurrent() }
        onCancelled: menu.focusCurrent()
    }
    CouchExecutablePicker {
        id: executablePicker; anchors.fill: parent
        controller: root.controller; gameId: root.gameId; couchScale: root.couchScale
        onSelected: { root.recheck(); menu.focusCurrent() }
        onCancelled: menu.focusCurrent()
    }
    onGameIdChanged: { keyboard.close(false); confirmation = ""; menu.currentIndex = 0 }
}