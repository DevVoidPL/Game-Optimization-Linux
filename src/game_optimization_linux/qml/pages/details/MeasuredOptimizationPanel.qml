pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

// Measured optimization: goal -> fingerprint -> baseline -> one candidate ->
// apply -> record -> comparability -> comparison -> decision -> history.
// Shared by Desktop and Couch. Status reads never write; every change is an
// explicit button. Drafts (goal options, comparability answers) survive refreshes.
SurfaceCard {
    id: panel
    property var controller
    property var gameData: ({})
    property bool couchMode: false
    readonly property string gameId: String(gameData && gameData.id || "")
    property var info: ({})
    property string errorMessage: ""
    property bool technicalOpen: false
    property bool historyOpen: false
    property bool busy: false
    // Draft values, changed only by the user.
    property string goal: ""
    property int fpsFloor: 60
    property bool qualityConsent: false
    property bool explicitExperiments: false
    property string level: ""
    property string visual: "not_checked"
    property string stability: "same"
    property bool unverifiedConfirmed: false
    property bool draftDirty: false
    property int padIndex: 0
    property var padFocusedItem: null
    readonly property int bodySize: couchMode ? 17 : 13
    readonly property int captionSize: couchMode ? 14 : 12
    readonly property int titleSize: couchMode ? 22 : 16
    readonly property bool wide: width > (couchMode ? 1000 : 760)
    readonly property var workflow: info.workflow || ({})
    readonly property string flowState: String(workflow.state || "")
    readonly property var support: info.support || ({})
    readonly property bool supported: Boolean(support.supported)
    readonly property var fingerprint: info.fingerprint || ({})
    readonly property var detectionState: fingerprint.detection || ({})
    readonly property bool detecting: String(detectionState.state || "") === "detecting"
    property string preparedFor: ""
    readonly property var baselineData: workflow.baseline || ({})
    readonly property var comparison: workflow.comparison || ({})
    readonly property var result: workflow.result || ({})
    readonly property var candidate: (flowState === "baseline_ready" ? (info.proposal || ({})) : (workflow.candidate || ({})))
    readonly property bool activeWorkflow: Boolean(info.active)
    readonly property bool recording: flowState === "baseline_recording" || flowState === "candidate_recording"
    readonly property var goalValues: ["maximum_performance", "balanced", "low_power", "lowest_latency", "stable_image", "best_quality"]
    readonly property var floorGoals: ["low_power", "best_quality"]
    padding: couchMode ? 18 : 16

    MeasuredOptimizationText { id: mt }

    function tr(value) { return mt.text(value) }
    function refresh() {
        if (!controller || !controller.getMeasuredOptimization || !gameId) return
        var value = controller.getMeasuredOptimization(gameId) || ({})
        if (value.success === false) { errorMessage = tr(String(value.error || "")); return }
        info = value
        prepareFingerprint(false)
        var saved = (value.workflow || ({})).goal || ({})
        // Only an untouched draft follows the saved workflow.
        if (!draftDirty && saved.id) {
            goal = String(saved.id); fpsFloor = Number(saved.fpsFloor || fpsFloor)
            qualityConsent = Boolean(saved.qualityConsent); explicitExperiments = Boolean(saved.explicitExperiments)
        }
    }
    function edit(name, value) { panel[name] = value; draftDirty = true }
    function call(method, args, done) {
        if (!controller || !controller[method] || !gameId || busy) return false
        busy = true
        var outcome = controller[method].apply(controller, [gameId].concat(args || [])) || ({})
        busy = false
        if (outcome.success === false) {
            errorMessage = tr(String(outcome.error || ""))
        } else {
            errorMessage = ""
            if (done) done(outcome)
        }
        refresh()
        return outcome.success !== false
    }
    function start() {
        return call("startMeasuredOptimization", [goal, {"fpsFloor": fpsFloor, "qualityConsent": qualityConsent,
                                                          "explicitExperiments": explicitExperiments}],
                    function() { draftDirty = false })
    }
    function recordBaseline() { return call("recordMeasuredBaseline", []) }
    function applyCandidate() { return call("applyMeasuredCandidate", [String(candidate.id || "")]) }
    function skipCandidate() { return call("skipMeasuredCandidate", [String(candidate.id || "")]) }
    function finishWithout() { return call("finishMeasuredWithoutCandidate", []) }
    function recordCandidate() { return call("recordMeasuredCandidate", []) }
    function confirmComparison() {
        return call("confirmMeasuredComparison", [level, {"visual": visual, "stability": stability,
                                                          "unverifiedConfirmed": unverifiedConfirmed}],
                    function() { draftDirty = false })
    }
    function keep(qualityDecision) { return call("keepMeasuredResult", [String(qualityDecision || "")], function() { level = ""; visual = "not_checked"; stability = "same"; unverifiedConfirmed = false }) }
    function revert() { return call("revertMeasuredResult", [], function() { level = ""; visual = "not_checked"; stability = "same"; unverifiedConfirmed = false }) }
    function cancel() { return call("cancelMeasuredOptimization", []) }
    function recordQuality(recordId, decision) { return call("recordMeasuredQuality", [String(recordId), String(decision)]) }
    function presetLabel(value) {
        switch (String(value)) {
        case "automatic": return qsTr("Automatic")
        case "custom": return qsTr("Custom")
        }
        return goalLabel(value)
    }
    function performanceLabel(value) {
        if (value === "improved") return qsTr("Improvement measured")
        if (value === "worse") return qsTr("Worse performance measured")
        if (value === "unchanged") return qsTr("No measurable change")
        return qsTr("Not measured")
    }
    function qualityLabel(value) {
        switch (String(value)) {
        case "not_affected": return qsTr("Not affected by this change")
        case "evaluated_same": return qsTr("Same (your check)")
        case "evaluated_better": return qsTr("Better (your check)")
        case "evaluated_worse": return qsTr("Worse (your check)")
        }
        return qsTr("Not evaluated")
    }
    function overallLabel(value) {
        if (value === "verified") return qsTr("Fully verified result")
        if (value === "performance_only") return qsTr("Performance improvement measured; image quality not evaluated")
        return qsTr("Not a verified result")
    }
    // Detection is started automatically once per game when the workflow is
    // shown; it runs in the background and never touches the draft.
    function prepareFingerprint(force) {
        var detection = (info.fingerprint || ({})).detection || ({})
        if (!supported || !controller || !controller.prepareMeasuredFingerprint || !gameId) return false
        if (detection.state !== "not_started" && !(force && detection.state === "failed")) return false
        if (preparedFor === gameId && !force) return false
        preparedFor = gameId
        var outcome = controller.prepareMeasuredFingerprint(gameId) || ({})
        if (outcome.success === false) errorMessage = tr(String(outcome.error || ""))
        else if (outcome.detection) info = Object.assign({}, info, {"fingerprint": Object.assign({}, info.fingerprint || ({}), {"detection": outcome.detection})})
        return outcome.success !== false
    }
    function detectionLine() {
        var detection = detectionState
        if (detection.state === "detecting" || detection.state === "not_started") return qsTr("Detecting the game files in the background…")
        if (detection.state === "failed") return tr(String(detection.reason || "")).arg(tr(String(detection.detail || "")))
        if (detection.state === "unavailable") {
            var text = tr(String(detection.reason || ""))
            if ((detection.candidates || []).length) text += " (" + detection.candidates.join(", ") + ")"
            return qsTr("Not detected: %1").arg(text)
        }
        return ""
    }
    function detectedValue(value) {
        if (fingerprint.available) return textOr(value)
        return detecting || detectionState.state === "not_started" ? qsTr("Detecting…") : qsTr("Not detected")
    }

    // Formatting ---------------------------------------------------------------
    function missing(value) { return value === undefined || value === null }
    function known(value) { return value !== undefined && value !== null && value !== "" && value !== "Unknown" }
    function textOr(value) { return known(value) ? String(value) : qsTr("Unknown") }
    function percent(value) { return Math.round(Number(value || 0) * 100) + "%" }
    function goalLabel(value) {
        switch (String(value)) {
        case "maximum_performance": return qsTr("Maximum Performance")
        case "balanced": return qsTr("Balanced")
        case "low_power": return qsTr("Low Power")
        case "lowest_latency": return qsTr("Lowest Latency")
        case "stable_image": return qsTr("Stable Image")
        case "best_quality": return qsTr("Best Quality")
        }
        return qsTr("Not selected")
    }
    function goalHint(value) {
        switch (String(value)) {
        case "maximum_performance": return qsTr("Rendered FPS and 1% low first; instability or a severe p99 regression rejects the change.")
        case "balanced": return qsTr("Better frame pacing without a large power, FPS or image-quality cost.")
        case "low_power": return qsTr("Lowest measured power while staying above your FPS floor.")
        case "lowest_latency": return qsTr("Rendered FPS and frame pacing; frame generation is penalized. Input latency itself is not measured.")
        case "stable_image": return qsTr("Penalizes generated frames, lossy upscaling and unstable presentation; uses your observations.")
        case "best_quality": return qsTr("Keeps native or chosen high-quality rendering while staying above your minimum FPS.")
        }
        return ""
    }
    function runnerLabel(kind) {
        switch (String(kind)) {
        case "manual_native": case "native": return qsTr("Native Linux")
        case "manual_wine": case "wine": return qsTr("Wine")
        case "manual_proton": case "proton": return qsTr("Proton")
        }
        return qsTr("Unknown")
    }
    function runtimeLine(runtime) {
        var kind = panel.runnerLabel(runtime.kind)
        if (!panel.known(runtime.name)) return kind
        var parts = [kind]
        if (runtime.name !== "Native Linux") parts.push(String(runtime.name))
        if (runtime.version && runtime.version !== runtime.name) parts.push(String(runtime.version))
        return parts.join("  ")
    }
    function baselineState() {
        if (flowState === "baseline_recording") return "recording"
        var metrics = baselineData.metrics
        if (metrics && metrics.sufficient) return "valid"
        if (metrics || (flowState === "goal_selected" && String(workflow.error || "").length)) return "invalid"
        return "not_recorded"
    }
    function baselineLabel(value) {
        if (value === "recording") return qsTr("Recording")
        if (value === "valid") return qsTr("Valid")
        if (value === "invalid") return qsTr("Invalid")
        return qsTr("Not recorded")
    }
    function recordingLabel() {
        var status = String((info.recording || ({})).status || "")
        if (status === "waiting_for_launcher" || status === "waiting_for_steam" || status === "waiting_for_runner")
            return qsTr("Waiting for the game to start")
        if (status === "recording" || status === "waiting_for_game_exit") return qsTr("Recording - play, then exit the game normally")
        if (status === "processing") return qsTr("Analyzing the recording")
        if (status === "failed") return qsTr("The recording failed: %1").arg(tr(String((info.recording || ({})).error || "")))
        return qsTr("Starting")
    }
    function metricLabel(name, kind) {
        switch (String(name)) {
        case "averageFps": return kind === "output" ? qsTr("Average output FPS (includes generated frames)") : qsTr("Average rendered FPS")
        case "onePercentLowFps": return kind === "output" ? qsTr("1% low output FPS") : qsTr("1% low rendered FPS")
        case "p50FrametimeMs": return qsTr("Frame time p50")
        case "p95FrametimeMs": return qsTr("Frame time p95")
        case "p99FrametimeMs": return qsTr("Frame time p99")
        case "stuttersPerMinute": return qsTr("Stutters per minute")
        case "vramMb": return "VRAM"
        case "gttMb": return "GTT"
        case "ramMb": return "RAM"
        case "gpuLoad": return qsTr("GPU load")
        case "cpuLoad": return qsTr("CPU load")
        case "gpuClockMhz": return qsTr("GPU clock")
        case "cpuClockMhz": return qsTr("CPU clock")
        case "gpuTempC": return qsTr("GPU temperature")
        case "cpuTempC": return qsTr("CPU temperature")
        case "gpuPowerW": return qsTr("GPU power")
        case "cpuPowerW": return qsTr("CPU power")
        }
        return String(name)
    }
    function unit(name) {
        var value = String(name)
        if (value.indexOf("Fps") >= 0) return " FPS"
        if (value.indexOf("Ms") >= 0) return " ms"
        if (value.indexOf("Mb") >= 0) return " MB"
        if (value.indexOf("Load") >= 0) return " %"
        if (value.indexOf("Mhz") >= 0) return " MHz"
        if (value.indexOf("TempC") >= 0) return " °C"
        if (value.indexOf("PowerW") >= 0) return " W"
        return ""
    }
    function number(value, name) {
        if (value === undefined || value === null || !isFinite(Number(value))) return qsTr("Not measured")
        return Number(value).toFixed(Math.abs(Number(value)) >= 100 ? 0 : 1) + unit(name)
    }
    function metricLine(row) {
        if (!row.available) {
            if (missing(row.before) && missing(row.after)) return qsTr("Not measured in either session")
            return qsTr("%1 → %2 (not comparable: missing in one session)").arg(number(row.before, row.metric)).arg(number(row.after, row.metric))
        }
        var sign = Number(row.absolute) >= 0 ? "+" : ""
        var pct = missing(row.percent) ? "" : ", " + (Number(row.percent) >= 0 ? "+" : "") + Number(row.percent).toFixed(1) + "%"
        return number(row.before, row.metric) + " → " + number(row.after, row.metric) + " (" + sign + Number(row.absolute).toFixed(1) + pct + ")"
    }
    function directionLabel(value) {
        switch (String(value)) {
        case "up": return qsTr("expected higher")
        case "down": return qsTr("expected lower")
        case "up_or_unchanged": return qsTr("expected higher or unchanged")
        case "down_or_unchanged": return qsTr("expected lower or unchanged")
        case "unchanged_or_down": return qsTr("may be lower")
        case "down_to_limit": return qsTr("limited to the cap")
        }
        return qsTr("unknown effect")
    }
    function costLabel(value) {
        if (value === "lossy_upscaling") return qsTr("Lower render resolution with lossy upscaling (consent required)")
        if (value === "presentation") return qsTr("Presentation goes through Gamescope; compatibility depends on the game")
        return qsTr("No image-quality change expected")
    }
    function changeValue(value) {
        if (value === true) return qsTr("On")
        if (value === false) return qsTr("Off")
        return String(value)
    }
    function reasonText(item) {
        var text = tr(String(item.text || ""))
        var values = item.values || []
        for (var i = 0; i < values.length; ++i) text = text.arg(String(values[i]))
        return text
    }
    function decisionLabel(value) {
        if (value === "keep") return qsTr("Keep")
        if (value === "revert") return qsTr("Revert")
        return qsTr("Inconclusive")
    }
    function levelLabel(value) {
        if (value === "controlled") return qsTr("Controlled - same scene and settings")
        if (value === "approximate") return qsTr("Approximately comparable")
        if (value === "not_comparable") return qsTr("Not comparable")
        return qsTr("Not answered")
    }
    function scopeLabel(value) {
        switch (String(value)) {
        case "current": return qsTr("Valid for this game and system")
        case "hardware_changed": return qsTr("Hardware changed - record a new baseline")
        case "build_changed": return qsTr("Game build changed - record a new baseline")
        case "software_changed": return qsTr("Driver or runtime changed - lower confidence")
        case "configuration_changed": return qsTr("Configuration changed - lower confidence")
        case "unverified_scope": return qsTr("Scope partly unknown")
        }
        return qsTr("Other game")
    }

    // Pad navigation (Couch) ----------------------------------------------------
    function controls(item, list) {
        if (!item || !item.visible || !item.enabled) return
        if (item.padAction !== undefined) list.push(item)
        var children = item.children || []
        for (var i = 0; i < children.length; i++) controls(children[i], list)
    }
    function handlePadAction(action) {
        if (action === "Back") {
            if (technicalOpen) { technicalOpen = false; return true }
            if (historyOpen) { historyOpen = false; return true }
            return false
        }
        var list = []; controls(panel, list)
        if (!list.length) return false
        var focused = list.findIndex(function(item) { return item.activeFocus })
        if (focused >= 0) padIndex = focused
        if (action === "NavigateDown" || action === "NavigateUp")
            padIndex = Math.max(0, Math.min(list.length - 1, padIndex + (action === "NavigateDown" ? 1 : -1)))
        padIndex = Math.min(padIndex, list.length - 1)
        var item = list[padIndex]; padFocusedItem = item; item.forceActiveFocus(Qt.TabFocusReason)
        if (item.padAction === "spin" && (action === "NavigateLeft" || action === "NavigateRight")) {
            item.value = item.value + (action === "NavigateLeft" ? -item.stepSize : item.stepSize)
            item.valueModified()
        } else if (item.padAction === "toggle" && action === "Confirm") {
            item.toggle(); item.toggled()
        } else if (item.padAction === "button" && action === "Confirm") {
            var pressed = item
            item.clicked()
            Qt.callLater(function() {
                if (!panel.visible) return
                var again = []; controls(panel, again)
                if (!again.length) return
                var same = again.indexOf(pressed)
                padIndex = same >= 0 ? same : Math.min(padIndex, again.length - 1)
                padFocusedItem = again[padIndex]; padFocusedItem.forceActiveFocus(Qt.TabFocusReason)
            })
        }
        return true
    }

    Component.onCompleted: refresh()
    onGameIdChanged: { draftDirty = false; info = ({}); refresh() }
    Timer { interval: 2000; repeat: true; running: panel.visible && (panel.recording || panel.detecting || panel.flowState === "baseline_ready"); onTriggered: panel.refresh() }

    component PadButton: AppButton {
        property string padAction: "button"
        compact: !panel.couchMode; textPixelSize: panel.bodySize
    }
    component FocusRing: Rectangle {
        anchors.fill: parent; anchors.margins: -3; radius: 6; color: "transparent"
        border.width: 2; border.color: App.Theme.accent
    }
    component PadSwitch: AppSwitch {
        id: padSwitch
        property string padAction: "toggle"
        textPixelSize: panel.bodySize
        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 0
        contentItem: Label {
            text: padSwitch.text; wrapMode: Text.WordWrap
            color: padSwitch.enabled ? App.Theme.text : App.Theme.textMuted
            font.pixelSize: padSwitch.textPixelSize
            leftPadding: padSwitch.indicator.width + padSwitch.spacing
            verticalAlignment: Text.AlignVCenter
        }
        FocusRing { visible: padSwitch.activeFocus }
    }
    component Choice: AppButton {
        property string padAction: "button"
        property bool chosen: false
        compact: true; textPixelSize: panel.bodySize
        kind: chosen ? "primary" : "secondary"
        Accessible.checked: chosen
    }
    component Heading: Label {
        Layout.fillWidth: true; color: App.Theme.text; font.bold: true
        font.pixelSize: panel.couchMode ? 19 : 14; wrapMode: Text.WordWrap
    }
    component Body: Label {
        Layout.fillWidth: true; color: App.Theme.textSecondary
        font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap
    }
    component Caption: Label {
        Layout.fillWidth: true; color: App.Theme.textMuted
        font.pixelSize: panel.captionSize; wrapMode: Text.WordWrap
    }
    component Field: RowLayout {
        property string name: ""
        property string value: ""
        property color tone: App.Theme.text
        Layout.fillWidth: true; spacing: 10
        Label { text: parent.name; color: App.Theme.textMuted; font.pixelSize: panel.captionSize
                Layout.preferredWidth: panel.wide ? (panel.couchMode ? 300 : 210) : (panel.couchMode ? 220 : 130); wrapMode: Text.WordWrap }
        Label { text: parent.value; color: parent.tone; font.pixelSize: panel.bodySize; Layout.fillWidth: true; wrapMode: Text.Wrap }
    }

    contentItem: ColumnLayout {
        spacing: panel.couchMode ? 14 : 10
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: qsTr("Measured optimization"); color: App.Theme.text
                    font.pixelSize: panel.titleSize; font.weight: Font.Bold; wrapMode: Text.WordWrap }
            StatusBadge { objectName: "measuredStateBadge"; text: panel.recording ? qsTr("Recording") : panel.activeWorkflow ? qsTr("In progress") : qsTr("Ready")
                          status: panel.recording ? "warning" : panel.activeWorkflow ? "available" : "neutral"; visible: panel.supported }
        }
        Caption { text: qsTr("One change at a time, measured against a clean baseline of this game on this system. The result can be that the baseline is already better.") }
        Label { objectName: "measuredUnsupported"; Layout.fillWidth: true; visible: !panel.supported && String(panel.support.reason || "").length > 0
                text: qsTr("Measured optimization is not available: %1").arg(panel.tr(String(panel.support.reason || "")))
                color: App.Theme.warning; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
        Label { objectName: "measuredError"; Layout.fillWidth: true; visible: panel.errorMessage.length > 0; text: panel.errorMessage
                color: App.Theme.danger; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
        Label { objectName: "measuredWorkflowError"; Layout.fillWidth: true; visible: panel.supported && String(panel.workflow.error || "").length > 0
                text: panel.tr(String(panel.workflow.error || "")); color: App.Theme.warning; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
        Label { Layout.fillWidth: true; visible: panel.supported && String(panel.info.busy || "").length > 0
                text: panel.tr(String(panel.info.busy || "")); color: App.Theme.warning; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }

        // 1. Goal
        Heading { text: qsTr("1. Optimization goal"); visible: panel.supported }
        Flow {
            objectName: "measuredGoalChoices"
            Layout.fillWidth: true; spacing: 8
            visible: panel.supported && !panel.activeWorkflow
            Repeater {
                model: panel.goalValues
                Choice {
                    required property string modelData
                    objectName: "measuredGoal_" + modelData
                    text: panel.goalLabel(modelData); chosen: panel.goal === modelData
                    onClicked: panel.edit("goal", modelData)
                }
            }
        }
        Field { name: qsTr("Selected goal"); value: panel.goalLabel(panel.goal); visible: panel.supported; objectName: "measuredGoalValue" }
        Field { objectName: "measuredCurrentConfiguration"; visible: panel.supported && String((panel.info.configuration || ({})).preset || "").length > 0
                name: qsTr("Current configuration"); value: panel.presetLabel((panel.info.configuration || ({})).preset) }
        Caption { objectName: "automaticProfileNote"; visible: panel.supported && String((panel.info.configuration || ({})).preset || "") === "automatic"
                  text: qsTr("Automatic uses only compatible local results that you kept after a measured comparison for this game and system. Without such evidence it stays conservative and adds no runtime tools; it may recommend no change.") }
        Caption { visible: panel.supported && !panel.activeWorkflow
                  text: qsTr("The goal is chosen only here. The current configuration is your saved launch setup; a kept result changes only the tested settings.") }
        Caption { text: panel.goalHint(panel.goal); visible: panel.supported && panel.goal.length > 0 }
        RowLayout {
            Layout.fillWidth: true
            visible: panel.supported && !panel.activeWorkflow && panel.floorGoals.indexOf(panel.goal) >= 0
            Body { text: panel.goal === "low_power" ? qsTr("FPS floor") : qsTr("Minimum FPS") }
            SpinBox {
                id: floorSpin
                objectName: "measuredFpsFloor"
                property string padAction: "spin"
                from: 15; to: 1000; stepSize: 5; editable: false; value: panel.fpsFloor
                onValueModified: panel.edit("fpsFloor", value)
                FocusRing { visible: floorSpin.activeFocus }
            }
        }
        PadSwitch { objectName: "measuredQualityConsent"; visible: panel.supported && !panel.activeWorkflow
                    text: qsTr("Allow candidates that lower image quality (upscaling)"); checked: panel.qualityConsent
                    onToggled: panel.edit("qualityConsent", checked) }
        PadSwitch { objectName: "measuredExplicitExperiments"; visible: panel.supported && !panel.activeWorkflow
                    text: qsTr("Allow explicit experiments (ntsync, GPU-memory priority, no_upload_hvv)"); checked: panel.explicitExperiments
                    onToggled: panel.edit("explicitExperiments", checked) }
        PadButton { objectName: "measuredStart"; visible: panel.supported && !panel.activeWorkflow; kind: "primary"
                    enabled: panel.goal.length > 0 && !panel.busy; text: qsTr("Start with this goal"); onClicked: panel.start() }

        // 2. Fingerprint
        Heading { text: qsTr("2. Game and system"); visible: panel.supported || panel.info.manual !== undefined }
        Field { objectName: "measuredIdentity"; visible: panel.info.manual !== undefined
                name: qsTr("Custom game identity"); value: String((panel.info.manual || ({})).identity || "") }
        Field { visible: panel.info.manual !== undefined; name: qsTr("Executable"); value: String((panel.info.manual || ({})).executable || "") }
        Field { objectName: "measuredRunner"; visible: panel.info.manual !== undefined; name: qsTr("Runner")
                value: (panel.info.manual || ({})).runner ? panel.runnerLabel((panel.info.manual || ({})).runner) + "  " + String((panel.info.manual || ({})).runnerPath || "")
                                                          : qsTr("None configured") }
        Field { objectName: "measuredManualSupport"; visible: panel.info.manual !== undefined; name: qsTr("Measurement")
                value: panel.supported ? qsTr("Supported: %1").arg(panel.tr(String(panel.support.recording || ""))) : panel.tr(String(panel.support.reason || ""))
                tone: panel.supported ? App.Theme.text : App.Theme.warning }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 4; visible: panel.supported
            Field { name: qsTr("Source"); value: String(panel.fingerprint.launcher || "") + "  ·  " + String(panel.info.key || "") }
            Label { objectName: "measuredDetection"; Layout.fillWidth: true; visible: !panel.fingerprint.available && panel.detectionLine().length > 0
                    text: panel.detectionLine(); wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize
                    color: panel.detectionState.state === "unavailable" || panel.detectionState.state === "failed" ? App.Theme.warning : App.Theme.textSecondary }
            Field { visible: panel.info.manual === undefined; name: qsTr("Executable"); value: panel.detectedValue((panel.fingerprint.executable || ({})).path) }
            Field { name: qsTr("Runtime"); value: panel.fingerprint.available || panel.info.manual !== undefined
                                               ? panel.runtimeLine(panel.fingerprint.runtime || ({})) : panel.detectedValue("") }
            Field { objectName: "measuredEngine"; name: qsTr("Engine")
                    value: !panel.fingerprint.available ? panel.detectedValue("")
                           : panel.textOr((panel.fingerprint.engine || ({})).value) + "  ·  " + qsTr("confidence %1").arg(panel.percent((panel.fingerprint.engine || ({})).confidence))
                           + "  ·  " + panel.tr(String((panel.fingerprint.engine || ({})).source || "")) }
            Field { objectName: "measuredApi"; name: qsTr("Rendering API")
                    value: !panel.fingerprint.available ? panel.detectedValue("")
                           : panel.textOr((panel.fingerprint.api || ({})).value) + "  ·  " + qsTr("confidence %1").arg(panel.percent((panel.fingerprint.api || ({})).confidence))
                           + "  ·  " + panel.tr(String((panel.fingerprint.api || ({})).source || "")) }
            Field { name: qsTr("Game build"); value: panel.textOr(panel.fingerprint.build || ((panel.fingerprint.executable || ({})).partialSha256 || "").substring(0, 12)) }
            Field { name: qsTr("Hardware"); value: panel.textOr((panel.fingerprint.hardware || ({})).gpu) + "  ·  " + panel.textOr((panel.fingerprint.hardware || ({})).cpu)
                                                + ((panel.fingerprint.hardware || ({})).vramGb ? "  ·  " + Number(panel.fingerprint.hardware.vramGb).toFixed(0) + " GB VRAM" : "") }
            Field { name: qsTr("Driver"); value: panel.textOr((panel.fingerprint.hardware || ({})).driver) }
            Field { objectName: "measuredDisplay"; name: qsTr("Display"); value: panel.detectedValue((panel.fingerprint.display || ({})).resolution) }
            Caption { visible: !panel.fingerprint.available
                      text: qsTr("Detection only reads the game folder; it never starts or changes the game.") }
            PadButton { objectName: "measuredDetect"; visible: panel.detectionState.state === "failed" && !panel.recording; text: qsTr("Retry detection")
                        onClicked: { panel.prepareFingerprint(true); panel.refresh() } }
        }

        // 3. Baseline
        Heading { text: qsTr("3. Baseline"); visible: panel.supported && panel.activeWorkflow || panel.flowState === "no_candidate" }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 4
            visible: panel.supported && (panel.activeWorkflow || panel.flowState === "no_candidate")
            Field { objectName: "measuredBaselineState"; name: qsTr("Status"); value: panel.baselineLabel(panel.baselineState())
                    tone: panel.baselineState() === "invalid" ? App.Theme.warning : App.Theme.text }
            Field { visible: Boolean(panel.baselineData.metrics); name: qsTr("Gameplay measured")
                    value: qsTr("%1 s after %2 s warm-up, %3 samples").arg(Number((panel.baselineData.metrics || ({})).gameplaySeconds || 0).toFixed(0))
                           .arg(Number((panel.baselineData.metrics || ({})).warmupSeconds || 0).toFixed(0)).arg(Number((panel.baselineData.metrics || ({})).samples || 0)) }
            Field { visible: Boolean(panel.baselineData.context); name: qsTr("Configuration revision")
                    value: String(((panel.baselineData.context || ({})).config || ({})).revision || "") }
            Body { visible: panel.flowState === "baseline_recording"; objectName: "measuredBaselineProgress"; text: panel.recordingLabel() }
            Caption { visible: panel.flowState === "goal_selected"
                      text: qsTr("GameOpti starts the game now with private MangoHud logging and your current settings. Play a representative scene for at least 90 seconds, then exit normally.") }
            PadButton { objectName: "measuredRecordBaseline"; kind: "primary"; visible: panel.flowState === "goal_selected" || panel.flowState === "baseline_ready"
                        enabled: !panel.busy && String(panel.info.busy || "").length === 0
                        text: panel.flowState === "baseline_ready" ? qsTr("Record the baseline again") : qsTr("Launch and record baseline")
                        onClicked: panel.recordBaseline() }
        }

        // 4. Candidate
        Heading { text: qsTr("4. Proposed change"); visible: panel.supported && ["baseline_ready", "candidate_applied", "candidate_recording", "awaiting_confirmation", "result_ready"].indexOf(panel.flowState) >= 0 }
        Body { objectName: "measuredNoCandidate"; visible: panel.supported && panel.flowState === "baseline_ready" && !panel.candidate.id
               text: qsTr("No compatible candidate is available. Keeping the current configuration is a valid result.") }
        ColumnLayout {
            objectName: "measuredCandidate"
            Layout.fillWidth: true; spacing: 4
            visible: panel.supported && Boolean(panel.candidate.id) && panel.flowState !== "goal_selected"
            Label { objectName: "measuredCandidateTitle"; Layout.fillWidth: true; text: panel.tr(String(panel.candidate.title || ""))
                    color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
            Body { text: panel.tr(String(panel.candidate.mechanism || "")) }
            Repeater {
                model: panel.candidate.changes || []
                Field {
                    required property var modelData
                    name: panel.tr(String(modelData.label || ""))
                    value: panel.changeValue(modelData.before) + " → " + panel.changeValue(modelData.after) + "   (" + String(modelData.target) + "." + String(modelData.setting) + ")"
                }
            }
            Field { name: qsTr("Requires"); value: (panel.candidate.prerequisites || []).map(function(item) { return panel.tr(item) }).join("; ") }
            Field { name: qsTr("Conflicts with"); value: (panel.candidate.conflicts || []).map(function(item) { return panel.tr(item) }).join("; ") }
            Field { name: qsTr("Expected"); value: Object.keys(panel.candidate.expected || ({})).map(function(key) {
                        return panel.metricLabel(key, "rendered") + ": " + panel.directionLabel(panel.candidate.expected[key]) }).join("; ") }
            Field { name: qsTr("Cost"); value: panel.costLabel(panel.candidate.imageQuality); tone: panel.candidate.imageQuality === "none" ? App.Theme.text : App.Theme.warning }
            Field { name: qsTr("Restore"); value: panel.tr(String(panel.candidate.restore || "")) }
            Field { name: qsTr("Evidence"); value: panel.tr(String(panel.candidate.evidenceSource || "")) }
            Caption { text: qsTr("Expected directions describe the mechanism, not a promised result. Only the measured comparison decides.") }
        }
        Flow {
            Layout.fillWidth: true; spacing: 8
            visible: panel.supported && panel.flowState === "baseline_ready"
            PadButton { objectName: "measuredApply"; kind: "primary"; visible: Boolean(panel.candidate.id)
                        enabled: !panel.busy && String(panel.info.busy || "").length === 0
                        text: qsTr("Apply with restore point"); onClicked: panel.applyCandidate() }
            PadButton { objectName: "measuredSkip"; visible: Boolean(panel.candidate.id); text: qsTr("Propose another"); onClicked: panel.skipCandidate() }
            PadButton { objectName: "measuredFinishWithout"; text: qsTr("Keep the current configuration"); onClicked: panel.finishWithout() }
        }

        // 5-6. Applied candidate and its recording
        Heading { text: qsTr("5. Candidate session"); visible: panel.supported && ["candidate_applied", "candidate_recording"].indexOf(panel.flowState) >= 0 }
        Body { objectName: "measuredApplied"; visible: panel.flowState === "candidate_applied"
               text: qsTr("The change is applied and a restore point is saved. Record the same scene with the same in-game settings.") }
        Body { objectName: "measuredCandidateProgress"; visible: panel.flowState === "candidate_recording"; text: panel.recordingLabel() }
        PadButton { objectName: "measuredRecordCandidate"; kind: "primary"; visible: panel.flowState === "candidate_applied"
                    enabled: !panel.busy && String(panel.info.busy || "").length === 0
                    text: qsTr("Launch and record candidate session"); onClicked: panel.recordCandidate() }

        // 7. Comparability
        Heading { text: qsTr("6. Was the scene comparable?"); visible: panel.supported && ["awaiting_confirmation", "result_ready"].indexOf(panel.flowState) >= 0 }
        ColumnLayout {
            id: comparabilityBox
            objectName: "measuredComparability"
            Layout.fillWidth: true; spacing: 6
            visible: panel.supported && ["awaiting_confirmation", "result_ready"].indexOf(panel.flowState) >= 0
            readonly property var check: panel.info.comparabilityCheck || ({})
            Caption { text: qsTr("GameOpti cannot prove that you played the same scene. Your answer can only lower the confidence, never raise it.") }
            Repeater {
                model: (comparabilityBox.check.violations || [])
                Label { required property var modelData; Layout.fillWidth: true; color: App.Theme.danger; font.pixelSize: panel.captionSize; wrapMode: Text.WordWrap
                        text: qsTr("%1 differs: %2 → %3").arg(panel.tr(String(modelData.label))).arg(String(modelData.before)).arg(String(modelData.after)) }
            }
            Repeater {
                model: (comparabilityBox.check.issues || [])
                Label { required property string modelData; Layout.fillWidth: true; color: App.Theme.warning; font.pixelSize: panel.captionSize; wrapMode: Text.WordWrap
                        text: panel.tr(modelData) }
            }
            Caption { visible: (comparabilityBox.check.unverified || []).length > 0
                      text: qsTr("Not readable by GameOpti: %1").arg((comparabilityBox.check.unverified || []).map(function(row) { return panel.tr(String(row.label)) }).join(", ")) }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: ["controlled", "approximate", "not_comparable"]
                    Choice { required property string modelData; objectName: "measuredLevel_" + modelData
                             text: panel.levelLabel(modelData); chosen: panel.level === modelData; onClicked: panel.edit("level", modelData) }
                }
            }
            PadSwitch { objectName: "measuredUnverifiedConfirmed"; visible: (comparabilityBox.check.unverified || []).length > 0
                        text: qsTr("I confirm these did not change between the sessions"); checked: panel.unverifiedConfirmed
                        onToggled: panel.edit("unverifiedConfirmed", checked) }
            Body { text: qsTr("Image quality compared with the baseline") }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: ["same", "better", "worse", "not_checked"]
                    Choice { required property string modelData; objectName: "measuredVisual_" + modelData
                             text: modelData === "same" ? qsTr("Same") : modelData === "better" ? qsTr("Better") : modelData === "worse" ? qsTr("Worse") : qsTr("Not checked")
                             chosen: panel.visual === modelData; onClicked: panel.edit("visual", modelData) }
                }
            }
            Body { text: qsTr("Stability compared with the baseline") }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: ["same", "better", "worse", "crash"]
                    Choice { required property string modelData; objectName: "measuredStability_" + modelData
                             text: modelData === "same" ? qsTr("Same") : modelData === "better" ? qsTr("Better") : modelData === "worse" ? qsTr("Worse") : qsTr("Crashed")
                             chosen: panel.stability === modelData; onClicked: panel.edit("stability", modelData) }
                }
            }
            PadButton { objectName: "measuredConfirm"; kind: "primary"; enabled: panel.level.length > 0 && !panel.busy
                        text: panel.flowState === "result_ready" ? qsTr("Update the answer") : qsTr("Compare the sessions"); onClicked: panel.confirmComparison() }
        }

        // 8-9. Comparison and decision
        Heading { text: qsTr("7. Measured comparison"); visible: panel.supported && panel.flowState === "result_ready" }
        ColumnLayout {
            objectName: "measuredResult"
            Layout.fillWidth: true; spacing: 4
            visible: panel.supported && panel.flowState === "result_ready"
            Label { objectName: "measuredDecision"; Layout.fillWidth: true
                    text: qsTr("Recommendation: %1").arg(panel.decisionLabel(panel.result.decision))
                    color: panel.result.decision === "keep" ? App.Theme.success : panel.result.decision === "revert" ? App.Theme.danger : App.Theme.warning
                    font.bold: true; font.pixelSize: panel.titleSize; wrapMode: Text.WordWrap }
            Field { objectName: "measuredConfidence"; name: qsTr("Comparability"); value: panel.levelLabel(panel.result.comparability)
                    + "  ·  " + (panel.result.comparabilityVerified ? qsTr("verified") : qsTr("not verified")) }
            Field { objectName: "measuredPerformanceStatus"; name: qsTr("Performance"); value: panel.result.performanceVerified
                    ? panel.performanceLabel(panel.result.performance) : qsTr("Not verified") }
            Field { objectName: "measuredQualityStatus"; name: qsTr("Image quality"); value: panel.qualityLabel(panel.result.imageQuality)
                    tone: panel.result.imageQuality === "not_evaluated" || panel.result.imageQuality === "evaluated_worse" ? App.Theme.warning : App.Theme.text }
            Field { objectName: "measuredOverall"; name: qsTr("Overall"); value: panel.overallLabel(panel.result.overall)
                    tone: panel.result.overall === "verified" ? App.Theme.success : App.Theme.warning }
            Repeater {
                model: panel.result.reasons || []
                Body { required property var modelData; text: "• " + panel.reasonText(modelData); color: App.Theme.text }
            }
            Repeater {
                model: panel.result.costs || []
                Body { required property var modelData; text: "△ " + panel.reasonText(modelData); color: App.Theme.warning }
            }
            Caption { text: (panel.baselineData.metrics || ({})).fpsKind === "output" || (panel.comparison.metrics || ({})).fpsKind === "output"
                            ? qsTr("Frame generation was active: the FPS values are output FPS, rendered FPS was not measured.")
                            : qsTr("FPS values are rendered frames. Generated/output FPS is not measured separately in these sessions.") }
            Caption { text: qsTr("Baseline: %1 s, %2 samples. Candidate: %3 s, %4 samples. Stutter: %5.")
                            .arg(Number((panel.baselineData.metrics || ({})).gameplaySeconds || 0).toFixed(0)).arg(Number((panel.baselineData.metrics || ({})).samples || 0))
                            .arg(Number((panel.comparison.metrics || ({})).gameplaySeconds || 0).toFixed(0)).arg(Number((panel.comparison.metrics || ({})).samples || 0))
                            .arg(qsTr("frame time at least twice the median and 8 ms above it")) }
            Repeater {
                objectName: "measuredMetrics"
                model: panel.result.metrics || []
                Field {
                    required property var modelData
                    objectName: "measuredMetric_" + modelData.metric
                    name: panel.metricLabel(modelData.metric, (panel.comparison.metrics || ({})).fpsKind)
                    value: panel.metricLine(modelData)
                    tone: modelData.available ? App.Theme.text : App.Theme.textMuted
                }
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                PadButton { objectName: "measuredKeep"; kind: panel.result.decision === "keep" ? "primary" : "secondary"
                            visible: panel.result.overall !== "performance_only"
                            text: qsTr("Keep the change"); enabled: !panel.busy; onClicked: panel.keep("") }
                PadButton { objectName: "measuredKeepPerformanceOnly"; visible: panel.result.overall === "performance_only"
                            text: qsTr("Keep as performance-only result"); enabled: !panel.busy; onClicked: panel.keep("keep_unevaluated") }
                PadButton { objectName: "measuredRevert"; kind: panel.result.decision === "keep" ? "secondary" : "primary"
                            text: qsTr("Revert to the baseline"); enabled: !panel.busy; onClicked: panel.revert() }
            }
            Caption { objectName: "measuredQualityHint"; visible: panel.result.overall === "performance_only"; color: App.Theme.warning
                      text: qsTr("This change lowers the render resolution. Compare the image, answer the image-quality question above and update the answer to store a fully accepted result. A performance-only result is kept, but Automatic never applies it.") }
            Caption { text: qsTr("Keep saves the result for this game, build and system. Revert restores the saved files byte for byte.") }
        }
        PadButton { objectName: "measuredCancel"; visible: panel.supported && panel.activeWorkflow; kind: "danger"
                    text: panel.flowState === "candidate_applied" || panel.flowState === "candidate_recording" || panel.flowState === "awaiting_confirmation" || panel.flowState === "result_ready"
                          ? qsTr("Cancel and restore") : qsTr("Cancel"); enabled: !panel.busy; onClicked: panel.cancel() }

        // 10. History
        PadButton { objectName: "measuredHistoryToggle"; visible: (panel.info.history || []).length > 0
                    text: (panel.historyOpen ? "▾ " : "▸ ") + qsTr("Saved results for this game (%1)").arg((panel.info.history || []).length)
                    onClicked: panel.historyOpen = !panel.historyOpen }
        Repeater {
            objectName: "measuredHistory"
            model: panel.historyOpen ? (panel.info.history || []) : []
            ColumnLayout {
                id: historyRow
                required property var modelData
                Layout.fillWidth: true; spacing: 2
                Label { Layout.fillWidth: true; color: App.Theme.text; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap
                        text: panel.tr(String(historyRow.modelData.candidateTitle || "")) + "  ·  " + panel.goalLabel(historyRow.modelData.goal) + "  ·  "
                              + panel.decisionLabel(historyRow.modelData.decision) + "  ·  " + (historyRow.modelData.userAction === "kept" ? qsTr("kept") : historyRow.modelData.userAction === "reverted" ? qsTr("reverted") : qsTr("cancelled")) }
                Caption { text: String(historyRow.modelData.savedAt || "").substring(0, 16).replace("T", " ") + "  ·  " + panel.levelLabel((historyRow.modelData.comparability || ({})).final)
                                + "  ·  " + panel.scopeLabel((historyRow.modelData.scopeMatch || ({})).state) }
                Caption { objectName: "measuredHistoryStatus"
                          text: panel.overallLabel((historyRow.modelData.status || ({})).overall) + "  ·  " + qsTr("Image quality") + ": "
                                + panel.qualityLabel((historyRow.modelData.status || ({})).imageQuality) }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    visible: historyRow.modelData.userAction === "kept" && (historyRow.modelData.status || ({})).imageQuality === "not_evaluated"
                    PadButton { objectName: "measuredHistoryQualitySame"; text: qsTr("Image quality is acceptable")
                                onClicked: panel.recordQuality(historyRow.modelData.id, "same") }
                    PadButton { objectName: "measuredHistoryQualityWorse"; text: qsTr("Image quality is worse")
                                onClicked: panel.recordQuality(historyRow.modelData.id, "worse") }
                }
            }
        }

        // Technical details
        PadButton { objectName: "measuredTechnicalToggle"; visible: panel.supported || (panel.info.matrix || []).length > 0
                    text: (panel.technicalOpen ? "▾ " : "▸ ") + qsTr("Technical details"); onClicked: panel.technicalOpen = !panel.technicalOpen }
        ColumnLayout {
            objectName: "measuredTechnical"
            Layout.fillWidth: true; spacing: 3; visible: panel.technicalOpen
            Caption { text: qsTr("Other candidates and why they are not proposed:") }
            Repeater {
                model: (panel.info.candidates || []).filter(function(row) { return row.id !== panel.candidate.id })
                Caption { required property var modelData
                          text: "• " + panel.tr(String(modelData.title)) + ": " + (modelData.applicability.available ? qsTr("available") : panel.tr(String(modelData.applicability.reason || ""))) }
            }
            Caption { text: qsTr("Configuration files: %1").arg(Object.keys(((panel.fingerprint.config || ({})).files) || ({})).filter(function(name) {
                          return panel.fingerprint.config.files[name] }).join(", ")) }
            Caption { text: qsTr("Engine evidence: %1").arg(((panel.fingerprint.engine || ({})).evidence || []).join("; ") || qsTr("none")) }
            Caption { text: qsTr("API evidence: %1").arg(((panel.fingerprint.api || ({})).evidence || []).join("; ") || qsTr("none")) }
            Caption { text: qsTr("Support by launch path:") }
            Repeater {
                model: panel.info.matrix || []
                Caption { required property var modelData
                          text: "• " + String(modelData.kind) + ": " + (modelData.supported ? panel.tr(String(modelData.recording)) : panel.tr(String(modelData.reason))) }
            }
        }
    }
}
