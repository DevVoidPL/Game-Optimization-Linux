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
    readonly property alias editor: state
    readonly property var padFocusedItem: menu.padFocusedItem
    function refresh() { state.refresh() }
    function handlePadAction(action) {
        if (action === "Back" && confirmation) { confirmation = ""; menu.currentIndex = 0; return true }
        return menu.handlePadAction(action)
    }
    function cycle(values, value, d) { return values[(Math.max(0, values.indexOf(value)) + d + values.length) % values.length] }
    function rows() {
        var idle = state.loaded && !state.statusData.busy
        if (confirmation) return [
            {id:"cancel", title:qsTr("Cancel"), trigger:function() { root.confirmation = "" }},
            {id:"confirm", title:qsTr("Confirm"), enabled:idle,
                description:qsTr("Host setup uses system authentication (Polkit); a password and keyboard may be needed. Restore removes only GameOpti setup, keeping packages and independent services."),
                trigger:function() {
                    var restore = root.confirmation === "restore"; root.confirmation = ""
                    state.operation(restore ? root.controller.restoreGpuMemorySystem(state.gameId) : root.controller.setupGpuMemory(state.gameId), false)
                }}]
        var result = [
            {id:"protection", title:qsTr("Protect this game's GPU memory"), value:state.priority ? qsTr("On") : qsTr("Off"),
                enabled:idle && (state.statusData.ready || state.priority),
                description:qsTr("Protection needs a supported driver, dmem region and host setup. Restart the game after Apply; this is not proof of a performance benefit."),
                trigger:function() { state.priority = !state.priority; state.touch() }},
            {id:"prepare", title:qsTr("Prepare host (Polkit)"), enabled:idle && !state.statusData.ready,
                trigger:function() { root.confirmation = "setup"; menu.currentIndex = 0 }},
            {id:"advanced", title:qsTr("Advanced controls and measurements"), value:state.expanded ? qsTr("On") : qsTr("Off"),
                trigger:function() { state.expanded = !state.expanded }}
        ]
        if (state.expanded) {
            result.push({id:"region", title:qsTr("Memory region"), value:state.region || qsTr("Not available"), enabled:idle && state.regions.length > 1,
                adjust:function(d) { state.region = root.cycle(state.regions, state.region, d); state.touch() }})
            result.push({id:"api", title:qsTr("Graphics path"), value:state.api, enabled:idle,
                adjust:function(d) { state.api = root.cycle(state.apiValues, state.api, d); state.touch() }})
            result.push({id:"pressure", title:qsTr("Memory-pressure profile"), value:state.pressure, enabled:idle,
                description:qsTr("D3D12 UPLOAD in system memory may reduce VRAM usage but slow GPU access. Requires verified vkd3d, discrete GPU and ReBAR. Default allocator remains unchanged."),
                trigger:function() { state.pressure = state.pressure === "default" ? "upload-system-memory" : "default"; state.touch() }})
            result.push({id:"same-scene", title:qsTr("Same scene, duration and graphics settings"), value:state.sameScene ? qsTr("Yes") : qsTr("No"),
                trigger:function() { state.sameScene = !state.sameScene; state.comparison = ({}) }})
            result.push({id:"compare", title:qsTr("Compare GPU-memory recordings"), enabled:idle && state.sameScene,
                description:qsTr("Compare Off and On using Baseline / Comparison recordings. Lower VRAM alone is not evidence of improvement."),
                trigger:function() { state.comparison = root.controller.compareGpuMemory(state.gameId, state.sameScene) }})
            result.push({id:"restore", title:qsTr("Restore GameOpti system setup"), enabled:idle && Boolean(state.system.managedSetup),
                trigger:function() { root.confirmation = "restore"; menu.currentIndex = 0 }})
        }
        result.push({id:"apply", title:qsTr("Apply for next launch"), enabled:idle,
            trigger:function() { state.operation(root.controller.applyGpuMemory(state.gameId, {priority:state.priority, pressure:state.pressure, region:state.region, api:state.api}), true) }})
        result.push({id:"reset", title:qsTr("Reset editor to defaults"), enabled:idle, trigger:function() { state.reset() }})
        result.push({id:"refresh", title:qsTr("Refresh diagnostics"), trigger:function() { root.refresh() }})
        return result
    }
    Details.GpuMemoryCard {
        id: state; visible: false; width: 1
        controller: root.controller; gameData: root.gameData
    }
    CouchOptionList {
        id: menu; anchors.fill: parent; couchScale: root.couchScale
        title: root.confirmation ? qsTr("Review GPU-memory setup") : qsTr("GPU memory")
        status: [state.dirty ? qsTr("Unsaved changes") : qsTr("Saved settings"),
            state.statusData.runtimeProtectionVerified ? qsTr("Runtime protection verified") : qsTr("Runtime protection not verified"),
            state.comparison.measuredImprovement ? qsTr("Measured improvement in this pair; repeat the test") : qsTr("Measured benefit not verified"),
            App.I18n.message(String(state.error || state.statusData.error || ""))].filter(function(s) { return s.length }).join(" · ")
        rows: root.rows()
    }
    Timer { interval: 1500; repeat: true; running: root.visible; onTriggered: root.refresh() }
    onGameIdChanged: { confirmation = ""; menu.currentIndex = 0 }
}