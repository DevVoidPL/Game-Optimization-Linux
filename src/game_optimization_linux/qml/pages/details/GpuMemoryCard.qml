pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

SurfaceCard {
    id: card
    objectName: "gpuMemoryCard"
    property bool couchMode: false
    readonly property real controlTextSize: couchMode ? 20 : App.Theme.fontBody
    font.pixelSize: controlTextSize
    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData.id || "")
    property var statusData: ({})
    property bool priority: false
    property string pressure: "default"
    property string region: ""
    property string api: "auto"
    property bool loaded: false
    property string loadedProfile: ""
    property bool expanded: false
    property bool dirty: false
    property int revision: 0
    property int submittedRevision: -1
    property int awaiting: 0
    property string error: ""
    property string measurementMessage: ""
    property bool sameScene: false
    property var comparison: ({})
    property int padIndex: 0
    property var padFocusedItem: null
    readonly property var system: statusData.system || ({})
    readonly property var regions: Object.keys(system.regions || ({}))
    readonly property var apiValues: ["auto", "d3d12", "d3d11", "vulkan", "opengl"]
    function touch() { dirty = true; revision += 1; error = "" }
    function loadEditor() {
        var p = statusData.profile || ({})
        priority = Boolean(p.priority); pressure = String(p.pressure || "default")
        api = String(p.api || "auto"); region = String(p.region || regions[0] || "")
        loaded = true; dirty = false
        loadedProfile = JSON.stringify(p)
    }
    function refresh() {
        if (!controller || !controller.gpuMemoryStatus || !gameId) return
        statusData = controller.gpuMemoryStatus(gameId) || ({})
        if (awaiting && statusData.completedOperationId >= awaiting) {
            awaiting = 0; error = String(statusData.error || "")
            if (!error && submittedRevision === revision && submittedRevision >= 0) loadEditor()
        } else if (statusData.success && (!loaded || (!dirty && loadedProfile !== JSON.stringify(statusData.profile || ({}))))) loadEditor()
    }
    function operation(started, saving) {
        if (!started) { error = qsTr("The GPU-memory operation could not be started"); return }
        submittedRevision = saving ? revision : -1
        refresh(); awaiting = Number(statusData.operationId || 0)
    }
    function reset() { priority = false; pressure = "default"; api = "auto"; touch() }
    function metric(value, units) { return value === undefined || value === null || !isFinite(Number(value)) ? "—" : Number(value).toFixed(1) + " " + units }
    function runtimeLine(p) {
        var group = (p.chain || [])[0] || ({})
        var key = region || Object.keys(p.regions || ({}))[0] || ""
        var low = (group.low || ({}))[key], current = (group.current || ({}))[key]
        return "PID " + p.pid + " · " + p.command + "\n" + p.cgroup + "\n" + key
            + " · dmem.low " + metric(low === undefined ? null : low / 1048576, "MiB")
            + " · dmem.current " + metric(current === undefined ? null : current / 1048576, "MiB")
    }
    function collectControls(item, result) {
        if (!item || !item.visible || !item.enabled) return
        if (item.gpuAction !== undefined) result.push(item)
        var children = item.children || []
        for (var i = 0; i < children.length; ++i) collectControls(children[i], result)
    }
    function handlePadAction(action) {
        var controls = []; collectControls(card, controls)
        if (!controls.length) return
        if (action === "NavigateDown" || action === "NavigateUp")
            padIndex = Math.max(0, Math.min(controls.length - 1, padIndex + (action === "NavigateDown" ? 1 : -1)))
        padIndex = Math.min(padIndex, controls.length - 1)
        var item = controls[padIndex]; padFocusedItem = item; item.forceActiveFocus()
        if (item.gpuAction === "combo" && ["Confirm", "NavigateLeft", "NavigateRight"].indexOf(action) >= 0) {
            item.currentIndex = (item.currentIndex + (action === "NavigateLeft" ? -1 : 1) + item.count) % item.count
            item.activated(item.currentIndex)
        } else if (action === "Confirm") {
            if (item.gpuAction === "switch" || item.checkable) item.toggle()
            item.clicked()
        }
    }
    Component.onCompleted: refresh()
    onGameIdChanged: { loaded = false; loadedProfile = ""; statusData = ({}); priority = false; pressure = "default"; region = ""; api = "auto"; dirty = false; awaiting = 0; error = ""; sameScene = false; comparison = ({}); refresh() }
    Timer { interval: 1500; repeat: true; running: card.visible; onTriggered: card.refresh() }
    contentItem: ColumnLayout {
        spacing: 10
        Label { Layout.fillWidth: true; text: qsTr("GPU memory"); color: App.Theme.text; font.pixelSize: card.couchMode ? 28 : 20; font.bold: true }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            text: qsTr("Protect the game's GPU allocations under memory pressure. This does not increase physical VRAM or reduce texture quality.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            visible: Boolean((card.system.gpus || []).length)
            text: (card.system.gpus || []).map(function(g) { return String(g.name || g.vendor || "") + " · "
                + (g.vramTotal ? (g.vramTotal / 1073741824).toFixed(2) + " GiB" : "—")
                + (String(g.type || "").indexOf("INTEGRATED") >= 0 ? " · " + qsTr("Shared-memory GPU") : "") }).join("\n") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: card.statusData.ready ? App.Theme.success : App.Theme.warning
            text: !card.statusData.success ? qsTr("Checking system readiness…") : card.statusData.ready ? qsTr("System ready · dmemcg-booster is running") : qsTr("System preparation required for GameOpti protection") }
        Label { objectName: "gpuSavedProfile"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            visible: card.loaded
            text: ((card.statusData.profile || {}).priority ? qsTr("Saved GameOpti protection: On") : qsTr("Saved GameOpti protection: Off"))
                + (card.dirty ? " · " + qsTr("Unsaved editor changes") : "") }
        Label { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; color: App.Theme.textMuted
            visible: card.expanded
            text: qsTr("Running kernel: %1 · memory regions: %2").arg(String(card.system.kernel || "—")).arg(card.regions.join(", ") || "—") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            visible: card.expanded
            text: card.system.ttmProtectionEvidence === "cachyos-backport-btf"
                  ? qsTr("TTM eviction protection: running-module evidence found (CachyOS backport / upstream mechanism).")
                  : qsTr("TTM eviction improvements are unverified in this running build. A kernel version alone is insufficient.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.warning
            visible: card.loaded && !card.regions.length
            text: qsTr("The running driver exposes no dmem region. Protection needs a supported kernel and GPU driver plus dmemcg-booster; GameOpti will not install experimental kernels or drivers.") }
        Flow {
            Layout.fillWidth: true; spacing: 8
            AppButton { property string gpuAction: "button"; textPixelSize: card.controlTextSize; text: qsTr("Prepare host (Polkit)"); enabled: !card.statusData.busy && !card.statusData.ready
                onClicked: card.operation(card.controller.setupGpuMemory(card.gameId), false) }
            AppButton { property string gpuAction: "button"; textPixelSize: card.controlTextSize; text: qsTr("Restore GameOpti system setup"); enabled: !card.statusData.busy && Boolean(card.system.managedSetup)
                visible: card.expanded
                onClicked: card.operation(card.controller.restoreGpuMemorySystem(card.gameId), false) }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: card.expanded
            text: qsTr("Setup uses the distro's dmemcg-booster and systemd. Restore keeps installed packages and independent service settings; it removes only GameOpti's setup changes. Per-game Off does not stop system services.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.warning
            visible: card.loaded && !card.statusData.launchSupported
            text: qsTr("This launcher cannot invoke the host scope. Use native Steam, Heroic, Lutris or a configured manual game. A launcher Flatpak needs its own supported integration; protection is not simulated.") }
        AppSwitch { objectName: "gpuPrioritySwitch"; property string gpuAction: "switch"; textPixelSize: card.controlTextSize; Layout.fillWidth: true; text: qsTr("Protect this game's GPU memory"); checked: card.priority
            enabled: !card.statusData.busy && (card.statusData.ready || card.priority)
            onClicked: { card.priority = checked; card.touch() } }
        AppButton { property string gpuAction: "button"; textPixelSize: card.controlTextSize; Layout.fillWidth: true; text: qsTr("Advanced controls and measurements")
            checkable: true; checked: card.expanded; onClicked: card.expanded = checked }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: card.expanded
            text: qsTr("A private systemd scope is created before game allocations. Plasma/Gamescope may adjust foreground protection. Existing allocations are not moved: restart the game after Apply.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted; visible: card.expanded
            text: qsTr("Without a desktop foreground booster or Gamescope, protection lasts for the game session, including when unfocused.") }
        AppComboBox { property string gpuAction: "combo"; textPixelSize: card.controlTextSize; Layout.fillWidth: true; model: card.regions; currentIndex: Math.max(0, card.regions.indexOf(card.region))
            visible: card.expanded
            enabled: !card.statusData.busy && card.regions.length > 1
            onActivated: function(index) { card.region = card.regions[index]; card.touch() } }
        Label { text: qsTr("Graphics API / launch path"); color: App.Theme.text; visible: card.expanded }
        AppComboBox { property string gpuAction: "combo"; textPixelSize: card.controlTextSize; Layout.fillWidth: true; model: [qsTr("Automatic"), "D3D12 / vkd3d", "D3D11 / DXVK", "Vulkan", "OpenGL"]
            visible: card.expanded
            currentIndex: Math.max(0, card.apiValues.indexOf(card.api)); enabled: !card.statusData.busy
            onActivated: function(index) { card.api = card.apiValues[index]; card.touch() } }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: card.expanded
            text: qsTr("Detected: %1 · vkd3d: %2").arg(String(card.statusData.detectedApi || "—")).arg(String((card.statusData.tool || {}).version || qsTr("not established by an actual launch"))) }
        Label { text: qsTr("Memory-pressure profile (separate from protection)"); color: App.Theme.text; Layout.fillWidth: true; wrapMode: Text.WordWrap; visible: card.expanded }
        AppComboBox { property string gpuAction: "combo"; Layout.fillWidth: true
            textPixelSize: card.controlTextSize
            visible: card.expanded
            model: [qsTr("Default allocator behaviour"), qsTr("D3D12: keep UPLOAD heaps in system memory")]
            currentIndex: card.pressure === "default" ? 0 : 1; enabled: !card.statusData.busy
            onActivated: function(index) { card.pressure = index ? "upload-system-memory" : "default"; card.touch() } }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: card.pressure === "default" ? App.Theme.textMuted : App.Theme.warning
            visible: card.expanded
            text: qsTr("Optional no_upload_hvv needs verified vkd3d support and a discrete GPU with ReBAR. It can free VRAM but slow GPU access. vkd3d already reserves only part of VRAM for UPLOAD. DXVK's automatic defragmentation stays at its default. Reported-VRAM overrides are not memory savings.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.warning
            visible: card.expanded && card.loaded && !card.statusData.pressureSupported
            text: !(card.statusData.tool || {}).noUploadHvv
                ? qsTr("UPLOAD profile unavailable: no actual Proton launch with verified 32/64-bit vkd3d support was found. Launch normally once, then refresh this page.")
                : qsTr("UPLOAD profile unavailable: a discrete GPU with a large visible VRAM BAR was not verified. Shared-memory GPUs keep the default allocator.") }
        Flow { Layout.fillWidth: true; spacing: 8
            AppButton { property string gpuAction: "button"; textPixelSize: card.controlTextSize; text: qsTr("Apply for next launch"); kind: "primary"; enabled: card.loaded && !card.statusData.busy
                onClicked: card.operation(card.controller.applyGpuMemory(card.gameId, {priority: card.priority, pressure: card.pressure, region: card.region, api: card.api}), true) }
            AppButton { property string gpuAction: "button"; textPixelSize: card.controlTextSize; text: qsTr("Reset editor to defaults"); enabled: !card.statusData.busy; onClicked: card.reset() }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            objectName: "gpuRuntimeProtection"
            text: !card.statusData.success ? qsTr("Checking game process…") : card.statusData.runtimeProtectionVerified
                ? (card.statusData.protectionOrigin === "managed_scope"
                    ? qsTr("Game process: protection verified in GameOpti scope")
                    : qsTr("Game process: independent protection verified, including when GameOpti is Off"))
                : card.statusData.runtimeState === "unprotected" ? qsTr("Game process: protection absent in the observed cgroup chain")
                : (card.statusData.gameProcesses || []).length ? qsTr("Game process: protection unverified · incomplete GPU or cgroup evidence")
                : qsTr("No matching game process detected · runtime protection unverified") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: Boolean(card.statusData.observedAt)
            text: qsTr("Observation: %1 · protection is best effort, not a measured benefit").arg(Qt.formatTime(new Date(card.statusData.observedAt), "HH:mm:ss")) }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: card.loaded
            text: qsTr("Measured benefit: unverified until a controlled comparison. Gamescope or desktop protection can remain active in both Off and On.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            visible: Boolean(card.statusData.busy || card.statusData.runtimePressureVerified || card.statusData.failedLaunch)
            text: card.statusData.busy ? qsTr("Applying GPU-memory operation…") : card.statusData.failedLaunch
                ? qsTr("Last launch returned code %1. Inspect its session log; this alone does not establish a GPU-memory fault.").arg(String((card.statusData.session || {}).exitCode))
                : qsTr("vkd3d and no_upload_hvv observed in the game · memory savings still need measurement") }
        Label { Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; color: App.Theme.textMuted
            visible: Boolean((card.statusData.gameProcesses || []).length)
            text: (card.statusData.gameProcesses || []).map(function(p) { return card.runtimeLine(p) }).join("\n") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: Boolean((card.statusData.gameProcesses || []).length)
            text: qsTr("dmem.current covers the whole cgroup, including other processes sharing it; it is not the game's private VRAM usage.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.warning
            visible: Boolean((card.statusData.profile || {}).priority && (card.statusData.gameProcesses || []).length && !card.statusData.runtimeProtectionVerified)
            text: qsTr("Game detected, but protection was not confirmed. Inspect its cgroup and ancestor limits.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.warning
            visible: Boolean(card.statusData.scopeMismatch)
            text: qsTr("The game is outside the managed scope. Any observed protection belongs to its current launcher or desktop integration.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; visible: Boolean(card.error || card.statusData.error); color: App.Theme.danger
            text: card.error || String(card.statusData.error || "") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            visible: card.expanded
            text: qsTr("Measure Off and On with the same scene and settings using the existing Baseline / Comparison recording below. GPU telemetry is saved beside MangoHud logs: VRAM/GTT, DRM residency where exposed, RAM pressure and process RSS. Per-process Vulkan budget can be unavailable. Compare FPS, p95/p99 and stutters; lower VRAM alone is not proof of improvement.") }
        AppSwitch { property string gpuAction: "switch"; Layout.fillWidth: true
            textPixelSize: card.controlTextSize
            visible: card.expanded
            text: qsTr("I compared the same scene, duration and graphics settings")
            checked: card.sameScene; onClicked: { card.sameScene = checked; card.comparison = ({}) } }
        AppButton { property string gpuAction: "button"; textPixelSize: card.controlTextSize; text: qsTr("Compare GPU-memory recordings"); enabled: card.sameScene && !card.statusData.busy
            visible: card.expanded
            onClicked: card.comparison = card.controller.compareGpuMemory(card.gameId, card.sameScene) }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textSecondary
            visible: card.expanded
            text: card.comparison.measuredImprovement ? qsTr("Measured improvement in this pair: FPS ≥ +5%, p99 ≤ −5%, p95 did not regress by more than 5%. Repeat the test; one pair is not a guarantee.")
                : card.comparison.matched ? qsTr("Comparable recordings found; the improvement criteria were not met.")
                : card.comparison.sameProtectionState ? qsTr("Both recordings show the same protection state. An unprotected-versus-protected comparison was not established.")
                : qsTr("Measured improvement unverified · recordings need actual game-process evidence of different protection or allocator settings.") }
        Repeater {
            model: ["before", "after"]
            delegate: Label {
                required property string modelData
                readonly property var sample: card.comparison[modelData] || ({})
                readonly property var memory: sample.memory || ({})
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
                visible: card.expanded && Boolean(sample.source)
                text: (modelData === "before" ? qsTr("Before") : qsTr("After")) + ": "
                    + card.metric(sample.fps, "FPS") + " · p95 " + card.metric(sample.p95, "ms") + " · p99 " + card.metric(sample.p99, "ms")
                    + "\n" + qsTr("Peak device VRAM / GTT: %1 / %2 MiB · process RSS sum: %3 MiB")
                        .arg(card.metric(memory.peakVramDevice === null ? null : memory.peakVramDevice / 1048576, ""))
                        .arg(card.metric(memory.peakGttDevice === null ? null : memory.peakGttDevice / 1048576, ""))
                        .arg(card.metric(memory.peakRssSum === null ? null : memory.peakRssSum / 1048576, ""))
                    + "\n" + String(sample.summaryPath || "")
                    + "\n" + qsTr("Sampled frametime spikes: %1 · RAM PSI full: %2 µs · GPU errors: %3")
                        .arg(sample.sampledHitches ? String(sample.sampledHitches.spikes) : "—")
                        .arg(memory.fullPsiTotalEnd !== undefined && memory.fullPsiTotalEnd !== null ? String(memory.fullPsiTotalEnd - memory.fullPsiTotalStart) : "—")
                        .arg(memory.kernelGpuErrors === undefined || memory.kernelGpuErrors === null ? "—" : String(memory.kernelGpuErrors.length))
            }
        }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted
            visible: card.expanded
            text: qsTr("Device totals include other applications. Summed RSS can count shared pages more than once. Missing metrics are shown as —; GPU budget and inaccessible driver errors are not invented.") }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: App.Theme.textMuted; visible: card.expanded
            text: qsTr("Spikes count logged samples above max(50 ms, 2× median); they are not a count of every rendered frame. RAM PSI is system-wide. Kernel errors cover the recording interval and may come from other applications.") }
    }
}
