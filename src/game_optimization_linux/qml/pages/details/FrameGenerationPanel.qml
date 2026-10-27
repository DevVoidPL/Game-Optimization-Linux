pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../../components"
import "../.." as App

ScrollView {
    id: panel
    objectName: "frameGenerationPanel"
    property var controller
    property var gameData: ({})
    property bool couchMode: false
    property var retainedDraft: ({})
    signal draftChanged(var draft)
    signal optiScalerRequested()
    readonly property string gameId: String(gameData.id || "")
    readonly property var scrollFlickable: contentItem
    readonly property int bodySize: couchMode ? 21 : App.Theme.fontBody
    property var statusData: ({})
    property string backend: "off"
    property string api: "auto"
    property bool internalFgOff: false
    property var settings: ({})
    property string dllPath: ""
    property var draftPaths: ({})
    property string confirmation: ""
    property string error: ""
    property bool advanced: false
    property bool diagnostics: false
    property bool dirty: false
    property bool loaded: false
    property int revision: 0
    property int submittedRevision: -1
    property int awaitingOperation: 0
    property string submittedAction: ""
    property int padIndex: 0
    readonly property var backendIds: ["off", "lsfg", "mako", "afmf"]
    readonly property var backendNames: [qsTr("Off"), "lsfg-vk", "MAKO Renderer", "afmf-linux"]
    readonly property var component: (statusData.components || {})[backend] || ({})
    readonly property var currentSettings: settings[backend] || ({})
    readonly property var blockers: component.blockers || []
    readonly property bool needsLossless: backend === "lsfg" || backend === "mako"
    readonly property bool optiConflict: backend === "afmf" && Boolean(component.optiScalerConflict)
    readonly property bool modelsReady: !needsLossless || (Boolean(component.modelsValidated)
        && localPath(dllPath) === localPath(String((statusData.dlls || {})[backend] || "")))
    readonly property bool apiReady: api !== "auto" || ["unknown", "opengl", ""].indexOf(String((statusData.binary || {}).api || "")) < 0
    readonly property bool environmentReady: Boolean((component.environmentCheck || {}).available)
    readonly property bool hardwareReady: {
        var caps = statusData.capabilities || ({})
        return Boolean(caps.available && caps.compute && caps.storageImages)
            && (caps.apiVersions || []).some(function(v) { var m = String(v).match(/^(\d+)\.(\d+)/); return m && (Number(m[1]) > 1 || Number(m[1]) === 1 && Number(m[2]) >= (backend === "afmf" ? 1 : 2)) })
    }
    readonly property var otherBlockers: blockers.filter(function(value) { return !(panel.optiConflict && String(value).indexOf("OptiScaler") >= 0) })
    readonly property bool dependenciesReady: backend === "off" || (component.installed && modelsReady && apiReady && environmentReady && hardwareReady && !optiConflict && !otherBlockers.length)
    readonly property bool canApply: loaded && !statusData.busy && (backend === "off" || dependenciesReady && !(statusData.steamUpdate || {}).blocked)
    readonly property bool applyAllowed: canApply && (!needsLossless || !statusData.optiScalerPresent || internalFgOff)
    function savedBackendName() { return backendNames[Math.max(0, backendIds.indexOf(String(statusData.backend || "off")))] }
    // The launch plan is always built from the saved profile, never from this draft.
    function draftStateText() {
        var text = qsTr("Not saved: %1 is only selected in this editor. The launch plan and the next game start still use the saved profile (%2).")
            .arg(backendNames[Math.max(0, backendIds.indexOf(backend))]).arg(savedBackendName())
        if (applyAllowed) return text
        var update = statusData.steamUpdate || ({})
        if (update.blocked) return text + "\n" + qsTr("Apply is unavailable while Steam is writing this game's files (Steam state: %1).").arg(String(update.state || "unknown"))
        if (statusData.busy) return text + "\n" + qsTr("Apply is unavailable while another operation runs.")
        if (!loaded) return text + "\n" + qsTr("Apply is unavailable until the status check finishes.")
        if (String(operationError()).indexOf("Steam") >= 0) return text + "\n" + qsTr("The last operation needed Steam to be closed and changed nothing. Close Steam completely, then try again.")
        return text + "\n" + qsTr("Apply is unavailable until the requirements in step 3 are met.")
    }
    readonly property var summaries: [
        {label: qsTr("Installation"), value: backend === "off" ? qsTr("Not required") : !loaded ? qsTr("Checking…") : component.installed ? String(component.version) : qsTr("Missing")},
        {label: qsTr("Requirements"), value: !loaded ? qsTr("Checking…") : dependenciesReady ? qsTr("Ready") : qsTr("Action needed")},
        {label: qsTr("Saved settings"), value: !statusData.configured ? qsTr("Not saved") : backendNames[backendIds.indexOf(String(statusData.backend || "off"))]},
        {label: qsTr("Runtime of saved profile"), value: statusData.runtimeState === "error" ? qsTr("Runtime error — inspect diagnostics") : statusData.runtimeState === "loaded" ? qsTr("Layer loaded in PID %1").arg((statusData.runtimePids || []).join(", ")) : qsTr("Not verified")}
    ]
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    function publishDraft() { draftChanged({gameId: gameId, backend: backend, api: api, settings: settings, internalFgOff: internalFgOff, dllPath: dllPath, draftPaths: draftPaths, dirty: dirty}) }
    function touch() { dirty = true; revision++; error = ""; confirmation = ""; publishDraft() }
    function localPath(value) { try { return decodeURIComponent(String(value).replace(/^file:\/\//, "")) } catch (_) { return String(value) } }
    function message(value) { return String(value || "").split("\n").map(function(line) { return qsTranslate("FrameGenerationMessages", line) }).join("\n") }
    function phase(value) {
        if (value === "download" || value === "install") return qsTr("Download and install")
        if (value === "apply") return qsTr("Apply")
        if (value === "validate") return qsTr("Validate models")
        if (value === "remove") return qsTr("Remove component")
        if (value === "setup-runner") return qsTr("Set up runner")
        if (value === "restore-runner") return qsTr("Restore runner settings")
        return value === "complete" ? "100%" : String(value || "")
    }
    function edit(key, value) {
        var copy = JSON.parse(JSON.stringify(settings)); copy[backend][key] = value; settings = copy; touch()
    }
    function loadEditor(result) {
        backend = String(result.backend || "off"); api = String(result.api || "auto")
        settings = JSON.parse(JSON.stringify(result.settings || result.defaults || {}))
        internalFgOff = Boolean(result.internalFgOff)
        dllPath = String((result.dlls || {})[backend] || "")
        draftPaths = Object.assign({}, result.dlls || {})
        if (!loaded) padIndex = backendIds.indexOf(backend)
        dirty = false; loaded = true
    }
    function refresh() {
        if (!controller || !controller.frameGenerationStatus || !gameId) return
        var result = controller.frameGenerationStatus(gameId) || ({})
        statusData = result
        if (awaitingOperation && Number(result.completedOperationId || 0) >= awaitingOperation) {
            awaitingOperation = 0
            if (result.operationError) error = String(result.operationError)
            else if (revision === submittedRevision && submittedAction === "apply") { loadEditor(result); draftChanged(({})) }
        } else if (!dirty && !awaitingOperation && result.success && !result.busy) {
            var restore = !loaded && retainedDraft.gameId === gameId && retainedDraft.dirty ? retainedDraft : null
            loadEditor(result)
            if (restore) {
                backend = restore.backend; api = restore.api; settings = JSON.parse(JSON.stringify(restore.settings))
                internalFgOff = restore.internalFgOff; dllPath = restore.dllPath; draftPaths = Object.assign({}, restore.draftPaths || {})
                dirty = true; padIndex = backendIds.indexOf(backend)
            }
        }
    }
    function recheck() {
        error = ""
        if (controller && controller.refreshFrameGeneration && gameId) controller.refreshFrameGeneration(gameId)
        refresh()
    }
    function operationError() {
        var value = String(error || statusData.operationError || "")
        if ((statusData.steamUpdate || {}).blocked && value === "A Steam update is currently active") return ""
        if (blockers.indexOf(value) >= 0 || value === String(component.error || "")) return ""
        return message(value)
    }
    function run(action) {
        confirmation = ""
        var payload = {backend: backend, api: api, settings: settings, internalFgOff: internalFgOff, path: dllPath}
        if (controller && controller.frameGenerationAction && controller.frameGenerationAction(gameId, action, payload)) {
            submittedRevision = revision; submittedAction = action
            var result = controller.frameGenerationStatus(gameId) || ({})
            awaitingOperation = Number(result.operationId || 0); statusData = result; error = ""
        } else error = qsTr("The operation could not be started")
    }
    function chooseBackend(index) {
        var paths = Object.assign({}, draftPaths); if (needsLossless) paths[backend] = dllPath
        draftPaths = paths; backend = backendIds[index]
        dllPath = String(paths[backend] || (statusData.dlls || {})[backend] || ((statusData.dllCandidates || {})[backend] || [""])[0] || "")
        advanced = false; padIndex = index; touch()
    }
    function reset() {
        var copy = JSON.parse(JSON.stringify(settings)); copy[backend] = Object.assign({}, (statusData.defaults || {})[backend])
        settings = copy; touch()
    }
    function ask(action) {
        confirmation = action
        Qt.callLater(function() {
            var items = []; collect(panel, items); padIndex = Math.max(0, items.indexOf(confirmButton))
            confirmButton.forceActiveFocus()
            var flick = scrollFlickable
            if (flick && flick.contentItem) flick.contentY = Math.max(0, Math.min(flick.contentHeight - height, confirmationCard.mapToItem(flick.contentItem, 0, 0).y - 12))
        })
    }
    function collect(item, items) {
        if (!item || !item.visible || !item.enabled) return
        if (item.fgActionType !== undefined) items.push(item)
        var children = item.children || []; for (var i = 0; i < children.length; ++i) collect(children[i], items)
    }
    function handlePadAction(action) {
        var items = []; collect(panel, items); if (!items.length) return
        if (action === "NavigateDown" || action === "NavigateUp") padIndex += action === "NavigateDown" ? 1 : -1
        padIndex = Math.max(0, Math.min(items.length - 1, padIndex)); var item = items[padIndex]; item.forceActiveFocus()
        var direction = action === "NavigateLeft" ? -1 : 1
        if (["Confirm", "NavigateLeft", "NavigateRight"].indexOf(action) >= 0) {
            if (item.fgActionType === "combo") {
                item.currentIndex = (item.currentIndex + direction + item.count) % item.count; item.activated(item.currentIndex)
            } else if (item.fgActionType === "backend" && action !== "Confirm") {
                chooseBackend((backendIds.indexOf(backend) + direction + backendIds.length) % backendIds.length)
                padIndex = items.findIndex(function(control) { return control.objectName === "fgBackend-" + panel.backend })
                if (padIndex >= 0) { item = items[padIndex]; item.forceActiveFocus() }
            } else if (item.fgActionType === "slider") { item.value = Math.max(item.from, Math.min(item.to, item.value + direction * item.stepSize)); item.moved() }
            else if (action === "Confirm") { if (item.fgActionType === "check") item.toggle(); item.clicked() }
        }
        var flick = scrollFlickable
        if (flick && flick.contentItem) flick.contentY = Math.max(0, Math.min(flick.contentHeight - height, item.mapToItem(flick.contentItem, 0, 0).y - height / 3))
    }
    readonly property var specifications: {
        var fields = []
        if (backend === "lsfg" || backend === "mako") {
            fields.push({key:"multiplier", title:qsTr("Output multiplier"), type:"number", min:2, max:5, step:1, unit:"×",
                description:qsTr("Start at 2×. Extra generated frames do not improve the game's input rate.")})
            fields.push({key:"performance_mode", title:qsTr("Performance model"), type:"bool",
                description:qsTr("Lighter interpolation model; lower GPU cost with possible image-quality loss.")})
            fields.push({key:"flow_scale", title:qsTr("Motion-estimation scale"), type:"number", min:0.25, max:1, step:0.05, displayScale:100, unit:"%",
                description:qsTr("Lower values reduce cost and may increase motion artifacts.")})
            if (backend === "mako") {
                fields.push({key:"base_fps_cap", title:qsTr("Base FPS cap (0 = off)"), type:"number", min:0, max:240, step:1, unit:"FPS",
                    description:qsTr("Caps real rendered frames. Start with a stable base rate and leave GPU headroom.")})
                fields.push({key:"adaptive", title:qsTr("Adaptive generation"), type:"bool",
                    description:qsTr("MAKO adjusts generated work towards the selected output target.")})
                if (currentSettings.adaptive) {
                    fields.push({key:"target_fps", title:qsTr("Adaptive output target"), type:"number", min:10, max:240, step:1, unit:"FPS",
                        description:qsTr("This is an output target, not a guaranteed real frame rate.")})
                    fields.push({key:"adaptive_max_multiplier", title:qsTr("Maximum adaptive multiplier"), type:"number", min:2, max:5, step:1, unit:"×",
                        description:qsTr("Higher limits can increase interpolation artifacts and latency.")})
                    fields.push({key:"adaptive_auto_base_fps_cap",advanced:true, title:qsTr("Steady base cap"), type:"bool",
                        description:qsTr("Use MAKO's adaptive base-cap policy.")})
                }
            }
            if (advanced) {
                fields.push({key:"allow_fp16",advanced:true, title:qsTr("Allow FP16"), type:"bool",
                    description:qsTr("Use half precision where the selected GPU and model support it. Restart required.")})
                fields.push({key:backend === "mako" ? "swapchain_image_count_compatibility" : "preserve_swapchain_image_count",
                    title:qsTr("Preserve swapchain image count"), type:"bool", advanced:true,
                    description:qsTr("Compatibility option for games that fail to start; may worsen pacing. Restart required.")})
            }
        } else if (backend === "afmf") {
            fields = [
                {key:"performance_mode", title:qsTr("Optical-flow quality"), type:"choice", choices:["auto","quality","performance"], labels:[qsTr("Automatic"),qsTr("Quality"),qsTr("Performance")],
                 description:qsTr("Automatic uses half-resolution flow from 2560×1440; Quality uses full resolution.")},
                {key:"search_mode",advanced:true, title:qsTr("Motion search"), type:"choice", choices:["auto","standard","high"], labels:[qsTr("Automatic"),qsTr("Standard"),qsTr("High")],
                 description:qsTr("High searches larger motion ranges at greater GPU cost.")},
                {key:"pacing", title:qsTr("Frame pacing"), type:"bool", description:qsTr("Space generated and real frames evenly. Recommended on.")},
                {key:"min_fps", title:qsTr("Minimum real FPS"), type:"number", min:0,max:120,step:1,unit:"FPS", description:qsTr("Generation pauses below this real frame rate; 0 removes the floor.")}
            ]
            if (advanced) fields = fields.concat([
                {key:"governor",advanced:true,title:qsTr("GPU contention governor"),type:"bool",description:qsTr("Reduce generated work when the GPU cannot finish it in time.")},
                {key:"hud_detect",advanced:true,title:qsTr("Static HUD detection"),type:"bool",description:qsTr("Keep matching static overlays instead of warping them.")},
                {key:"fast_motion_response",advanced:true,title:qsTr("Unreliable motion fallback"),type:"choice",choices:["blend","repeat"],labels:[qsTr("Blend frames"),qsTr("Repeat previous frame")],description:qsTr("Choose how uncertain motion regions are displayed.")}
            ])
        }
        return fields
    }
    onGameIdChanged: { loaded = false; dirty = false; settings = ({}); statusData = ({}); awaitingOperation = 0; error = ""; confirmation = ""; dllPath = ""; draftPaths = ({}); internalFgOff = false; api = "auto"; backend = "off"; refresh() }
    Component.onCompleted: { if (!dirty && retainedDraft.gameId === gameId && retainedDraft.dirty) loaded = false; recheck() }
    Timer { interval: 1500; repeat: true; running: panel.visible; onTriggered: panel.refresh() }
    FileDialog { id: dllDialog; title: qsTr("Select the required Lossless Scaling DLL"); nameFilters: ["DLL (*.dll)"]
        onAccepted: { panel.dllPath = selectedFile.toString(); panel.touch() } }

    ColumnLayout {
        width: panel.availableWidth
        spacing: 12
        SurfaceCard {
            Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                spacing: 10
                Label { Layout.fillWidth: true; text: qsTr("1 · Choose a generator"); color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize + 3; wrapMode: Text.WordWrap }
                Label { Layout.fillWidth: true; text: qsTr("Choose → Install → Resolve requirements → Configure → Apply"); color: App.Theme.accent; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                GridLayout {
                    Layout.fillWidth: true; columns: width >= 960 ? 4 : width >= 420 ? 2 : 1; rowSpacing: 8; columnSpacing: 8
                    Repeater {
                        model: panel.backendIds
                        Button {
                            id: backendCard
                            required property string modelData
                            required property int index
                            property string fgActionType: "backend"
                            objectName: "fgBackend-" + modelData
                            Layout.fillWidth: true; Layout.minimumWidth: 0; implicitWidth: 0
                            Layout.preferredHeight: Math.max(panel.couchMode ? 110 : 86, backendContent.implicitHeight + 24)
                            padding: 12; focusPolicy: Qt.StrongFocus; enabled: panel.loaded && !panel.statusData.busy
                            Accessible.name: panel.backendNames[index] + ". " + backendState.text
                            background: Rectangle { radius: App.Theme.radiusSmall; color: App.Theme.surfaceRaised
                                border.color: backendCard.activeFocus || panel.backend === backendCard.modelData ? App.Theme.accent : App.Theme.border
                                border.width: backendCard.activeFocus ? 3 : panel.backend === backendCard.modelData ? 2 : 1 }
                            contentItem: ColumnLayout {
                                id: backendContent
                                spacing: 4
                                Label { Layout.fillWidth: true; text: panel.backendNames[backendCard.index]; color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; text: backendCard.modelData === "off" ? qsTr("No managed generator") : backendCard.modelData === "lsfg" ? qsTr("Fixed multiplier · Lossless Scaling") : backendCard.modelData === "mako" ? qsTr("Fixed / adaptive · Lossless Scaling") : qsTr("Independent Vulkan layer · no purchase"); color: App.Theme.textSecondary; font.pixelSize: panel.couchMode ? 17 : App.Theme.fontCaption; wrapMode: Text.WordWrap }
                                Label { id: backendState; Layout.fillWidth: true; text: backendCard.modelData === "off" ? qsTr("Off") : !panel.loaded ? qsTr("Checking…") : !((panel.statusData.components || {})[backendCard.modelData] || {}).installed ? qsTr("Component missing") : ((panel.statusData.components || {})[backendCard.modelData] || {}).optiScalerConflict ? qsTr("OptiScaler conflict") : backendCard.modelData !== "afmf" && !((panel.statusData.components || {})[backendCard.modelData] || {}).modelsValidated ? qsTr("Lossless Scaling required") : String(panel.statusData.backend || "off") === backendCard.modelData ? qsTr("Installed · saved for this game") : qsTr("Installed · not applied to this game"); color: App.Theme.textMuted; font.pixelSize: panel.couchMode ? 17 : App.Theme.fontCaption; wrapMode: Text.WordWrap }
                            }
                            onClicked: panel.chooseBackend(index)
                        }
                    }
                }
                GridLayout {
                    Layout.fillWidth: true; columns: width >= 700 ? 2 : 1; rowSpacing: 6; columnSpacing: 18
                    Repeater { model: panel.summaries
                        RowLayout { id: statusSummary; required property var modelData; Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: statusSummary.modelData.label; color: App.Theme.textMuted; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption; wrapMode: Text.WordWrap }
                            Label { Layout.maximumWidth: panel.width * 0.52; text: statusSummary.modelData.value; color: App.Theme.text; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption; wrapMode: Text.WordWrap }
                        }
                    }
                }
            }
        }
        SurfaceCard {
            visible: panel.backend !== "off"; Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                spacing: 8
                Label { Layout.fillWidth: true; text: qsTr("2 · Install the shared component"); color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                Label { Layout.fillWidth: true; objectName: "fgComponentState"; text: panel.component.installed ? (String(panel.statusData.backend || "off") === panel.backend ? qsTr("Already installed: %1. Nothing to download. This game's saved profile uses it.").arg(String(panel.component.version)) : qsTr("Already installed: %1. Nothing to download; use Apply below to use it for this game.").arg(String(panel.component.version))) : qsTr("Available version: %1 · Download: %2 MiB. Installation does not activate any game or global layer.").arg(String(panel.component.availableVersion || "—")).arg((Number(panel.component.downloadSize || 0) / 1048576).toFixed(2)); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Flow { Layout.fillWidth: true; spacing: 8
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); visible: !panel.component.installed; property string fgActionType: "button"; text: qsTr("Download and install"); textPixelSize: panel.bodySize; enabled: !panel.statusData.busy; onClicked: panel.ask("install") }
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); visible: Boolean(panel.statusData.busy) && ["install", "download"].indexOf(String((panel.statusData.progress || {}).phase || "")) >= 0; property string fgActionType: "button"; text: qsTr("Cancel download"); textPixelSize: panel.bodySize; onClicked: panel.controller.cancelFrameGeneration() }
                }
                Label { visible: Boolean(panel.statusData.busy); Layout.fillWidth: true; text: qsTr("Operation: %1 · %2% ").arg(panel.phase((panel.statusData.progress || {}).phase)).arg(Math.round(Number((panel.statusData.progress || {}).value || 0) * 100)); color: App.Theme.accent; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                ProgressBar { visible: Boolean(panel.statusData.busy); Layout.fillWidth: true; value: Number((panel.statusData.progress || {}).value || 0) }
                Label { visible: Boolean(panel.component.error); Layout.fillWidth: true; text: panel.message(panel.component.error); color: App.Theme.warning; wrapMode: Text.WrapAnywhere; font.pixelSize: panel.bodySize }
            }
        }
        SurfaceCard {
            id: requirementsCard
            objectName: "fgRequirements"
            visible: panel.backend !== "off"; Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                spacing: 10
                Label { Layout.fillWidth: true; text: qsTr("3 · Resolve requirements"); color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                ColumnLayout {
                    visible: panel.optiConflict; Layout.fillWidth: true
                    Label { objectName: "fgOptiConflict"; Layout.fillWidth: true; text: qsTr("afmf-linux cannot be used with the installed OptiScaler. Restore/remove it in its existing panel, or choose another generator. Nothing is removed automatically."); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                    AppButton { Layout.maximumWidth: panel.availableWidth - 32; objectName: "fgOpenOptiScaler"; property string fgActionType: "button"; text: qsTr("Open OptiScaler"); textPixelSize: panel.bodySize; onClicked: { panel.publishDraft(); panel.optiScalerRequested() } }
                }
                ColumnLayout {
                    visible: panel.needsLossless; Layout.fillWidth: true; spacing: 8
                    Label { objectName: "fgPurchaseRequirement"; Layout.fillWidth: true; text: qsTr("Frame generation requires separately purchased Lossless Scaling. The free backend download does not include it. Generation with lsfg-vk / MAKO has not been tested here."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                    Label { Layout.fillWidth: true; text: panel.backend === "lsfg" ? qsTr("lsfg-vk 2.0 needs lsfg-vk.dll from Lossless Scaling's lsfg-vk Steam branch. Only VSync pacing is supported.") : qsTr("MAKO Renderer 4.6 needs Lossless.dll from the public Lossless Scaling release. Decky is not required."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                    Label { Layout.fillWidth: true; text: qsTr("GameOpti never changes the Steam branch. File validation is separate for each backend and is not proof of generated frames."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                    TextField { objectName: "fgLosslessFile"; Layout.fillWidth: true; text: panel.dllPath; placeholderText: panel.backend === "lsfg" ? "lsfg-vk.dll" : "Lossless.dll"; color: App.Theme.text; font.pixelSize: panel.bodySize; selectByMouse: true; onTextEdited: { panel.dllPath = text; panel.touch() } }
                    Flow { Layout.fillWidth: true; spacing: 8
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); objectName: "fgOpenLosslessSteam"; property string fgActionType: "button"; text: qsTr("Lossless Scaling on Steam"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: Qt.openUrlExternally("https://store.steampowered.com/app/993090/Lossless_Scaling/") }
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); objectName: "fgDiscover"; property string fgActionType: "button"; text: qsTr("Discover"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: { panel.dllPath = String(((panel.statusData.dllCandidates || {})[panel.backend] || [""])[0] || ""); panel.touch() } }
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); objectName: "fgBrowse"; property string fgActionType: "button"; text: qsTr("Browse…"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: dllDialog.open() }
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); objectName: "fgValidate"; property string fgActionType: "button"; text: panel.modelsReady ? qsTr("Validate again") : qsTr("Validate models"); textPixelSize: panel.bodySize; kind: "secondary"; enabled: Boolean(panel.component.installed) && !!panel.dllPath && !panel.statusData.busy; onClicked: panel.run("validate") }
                    }
                    Label { Layout.fillWidth: true; text: panel.modelsReady ? qsTr("Models validated · file version: %1 · component: %2").arg(String(((panel.statusData.validation || {})[panel.backend] || {}).fileVersion || "unknown")).arg(String(panel.component.version || "—")) : qsTr("Models not validated for this backend"); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                }
                Label { visible: !panel.needsLossless && !panel.optiConflict; Layout.fillWidth: true; text: qsTr("afmf-linux needs no Lossless Scaling purchase. It is an independent project: fixed 2×, 64-bit applications using Vulkan 1.1 or newer."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { Layout.fillWidth: true; text: qsTr("Executable: %1 · %2-bit · detected API: %3").arg(String(panel.statusData.executable || "—").split("/").pop()).arg(Number((panel.statusData.binary || {}).bits || 0)).arg(String((panel.statusData.binary || {}).api || "unknown")); color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                AppComboBox { objectName: "fgGraphicsPath"; property string fgActionType: "combo"; Layout.fillWidth: true; textPixelSize: panel.bodySize
                    model: [qsTr("Detect graphics path"), "Vulkan", "DXVK (D3D9/10/11)", "VKD3D-Proton (D3D12)"]
                    currentIndex: ["auto","vulkan","dxvk","vkd3d"].indexOf(panel.api)
                    onActivated: { panel.api = ["auto","vulkan","dxvk","vkd3d"][currentIndex]; panel.touch() } }
                Label { visible: !panel.apiReady; Layout.fillWidth: true; text: qsTr("Detection is inconclusive. Select the actual Vulkan / DXVK / VKD3D-Proton path above."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: panel.loaded && !panel.hardwareReady; Layout.fillWidth: true; text: qsTr("GPU requirements are not confirmed. Recheck and inspect the Vulkan probe in diagnostics."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: Boolean(panel.component.installed) && !panel.environmentReady; Layout.fillWidth: true; text: qsTr("The launching environment cannot read the component or its dependencies. Set up/repair the runner, then recheck; details are in diagnostics."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: panel.otherBlockers.length > 0; Layout.fillWidth: true; text: panel.message(panel.otherBlockers[0]); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { objectName: "fgSteamUpdate"; Layout.fillWidth: true; visible: Boolean((panel.statusData.steamUpdate || {}).blocked) || (panel.statusData.steamUpdate || {}).state === "paused"
                    text: (panel.statusData.steamUpdate || {}).blocked ? (panel.statusData.steamUpdate || {}).state === "unknown" ? qsTr("This game's Steam manifest cannot be verified. Check that its library is available, then recheck.") : qsTr("Steam is updating this game's files. Finish or pause the operation in Steam, then recheck.") : qsTr("Steam reports a paused update, not an active file write.")
                    color: (panel.statusData.steamUpdate || {}).blocked ? App.Theme.warning : App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { Layout.fillWidth: true; text: (panel.statusData.runner || {}).configured === true ? qsTr("Runner configured. Apply saves settings for the next launch.") : qsTr("Runner not confirmed. Set it up before starting the game."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Flow { Layout.fillWidth: true; spacing: 8
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); objectName: "fgRecheck"; property string fgActionType: "button"; text: qsTr("Recheck requirements"); textPixelSize: panel.bodySize; kind: "secondary"; enabled: !panel.statusData.busy && !panel.statusData.checking; onClicked: panel.recheck() }
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); objectName: "fgRunnerSetup"; property string fgActionType: "button"; text: (panel.statusData.runner || {}).configured === true ? qsTr("Repair runner setup") : qsTr("Set up runner"); textPixelSize: panel.bodySize; kind: "secondary"; enabled: !panel.statusData.busy; onClicked: panel.ask("setup-runner") }
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); visible: Boolean((panel.statusData.steamUpdate || {}).blocked); property string fgActionType: "button"; text: qsTr("Open Steam downloads"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: Qt.openUrlExternally("steam://open/downloads") }
                }
                Label { visible: Boolean(panel.statusData.checking); Layout.fillWidth: true; text: qsTr("Checking…"); color: App.Theme.textMuted; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
            }
        }
        SurfaceCard {
            visible: panel.backend !== "off"; Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                spacing: 12
                Label { Layout.fillWidth: true; text: qsTr("4 · Configure"); color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize }
                Label { visible: !panel.component.installed; Layout.fillWidth: true; text: qsTr("Install the component above to edit its settings. Requirements remain visible while controls are unavailable."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                ColumnLayout {
                    visible: Boolean(panel.component.installed); enabled: !panel.statusData.busy; Layout.fillWidth: true; spacing: 14
                    Repeater { model: panel.specifications.filter(function(field) { return !field.advanced })
                        FrameGenerationSetting { required property var modelData; specification: modelData; value: panel.currentSettings[modelData.key]; couchMode: panel.couchMode; onEdited: function(newValue) { panel.edit(modelData.key, newValue) } }
                    }
                    AppButton { Layout.maximumWidth: panel.availableWidth - 32; objectName: "fgAdvanced"; property string fgActionType: "button"; text: panel.advanced ? qsTr("Hide advanced") : qsTr("Advanced"); textPixelSize: panel.bodySize; kind: "ghost"; onClicked: panel.advanced = !panel.advanced }
                    Repeater { model: panel.advanced ? panel.specifications.filter(function(field) { return field.advanced }) : []
                        FrameGenerationSetting { required property var modelData; specification: modelData; value: panel.currentSettings[modelData.key]; couchMode: panel.couchMode; onEdited: function(newValue) { panel.edit(modelData.key, newValue) } }
                    }
                    AppButton { Layout.maximumWidth: panel.availableWidth - 32; property string fgActionType: "button"; text: qsTr("Reset backend defaults"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: panel.reset() }
                    Label { Layout.fillWidth: true; text: qsTr("Reset changes this editor only. Apply saves it; other backends, proprietary files and upscalers are preserved."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                    Label { visible: panel.backend !== "mako"; Layout.fillWidth: true; text: qsTr("Cap real FPS in the game. This backend has no separate base-cap control. A Gamescope output limit is not a base-FPS cap."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                }
            }
        }
        SurfaceCard {
            Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                spacing: 10
                Label { Layout.fillWidth: true; text: qsTr("5 · Apply saved settings"); color: App.Theme.text; font.bold: true; font.pixelSize: panel.bodySize }
                Label { Layout.fillWidth: true; text: qsTr("Close the game before Apply. Saving does not start frame generation. Restart through the configured runner to test it."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                RowLayout { visible: panel.needsLossless && Boolean(panel.statusData.optiScalerPresent); Layout.fillWidth: true
                    AppSwitch { property string fgActionType: "check"; checked: panel.internalFgOff; onToggled: { panel.internalFgOff = checked; panel.touch() } }
                    Label { Layout.fillWidth: true; text: qsTr("I disabled in-game / OptiScaler / DLSS Enabler frame generation. Keep the working upscaler."); color: App.Theme.textSecondary; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                }
                Label { visible: panel.backend === "afmf" && !panel.optiConflict; Layout.fillWidth: true; text: qsTr("Before testing afmf-linux, turn off the game's own frame generation."); color: App.Theme.textSecondary; font.pixelSize: panel.bodySize; wrapMode: Text.WordWrap }
                AppButton { Layout.maximumWidth: panel.availableWidth - 32; objectName: "applyFrameGeneration"; property string fgActionType: "button"; text: panel.dirty ? qsTr("Apply changes") : qsTr("Apply"); textPixelSize: panel.bodySize; kind: "primary"; enabled: panel.applyAllowed; onClicked: panel.run("apply") }
                Label { objectName: "fgDraftState"; visible: panel.dirty; Layout.fillWidth: true; text: panel.draftStateText(); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: panel.backend !== "off" && !panel.dependenciesReady; Layout.fillWidth: true; text: qsTr("Resolve the requirements in step 3 before Apply."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                Label { visible: panel.backend === "off"; Layout.fillWidth: true; text: qsTr("Off requests no generator. A guard is added only when the inherited environment concretely activates a generator. Installed components and each backend's saved values are kept."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: !!panel.operationError(); Layout.fillWidth: true; text: panel.operationError(); color: App.Theme.danger; wrapMode: Text.WrapAnywhere; font.pixelSize: panel.bodySize }
                Label { visible: panel.backend !== "off"; Layout.fillWidth: true; text: qsTr("Injection may conflict with anti-cheat or game policies. Use offline testing; GameOpti does not bypass anti-cheat."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
            }
        }
        SurfaceCard {
            id: confirmationCard
            visible: !!panel.confirmation; Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                Label { Layout.fillWidth: true; text: panel.confirmation === "install" ? qsTr("Download and install %1 %2 (%3 MiB) from the official source? Only this shared component is installed; no game is activated.").arg(panel.backendNames[panel.backendIds.indexOf(panel.backend)]).arg(String(panel.component.availableVersion || "")).arg((Number(panel.component.downloadSize || 0) / 1048576).toFixed(2))
                        : panel.confirmation === "remove" ? qsTr("Remove this shared component? Games selecting it will require reinstallation or Off. Per-game settings and Lossless Scaling files are kept.")
                        : panel.confirmation === "setup-runner" ? qsTr("Set up the existing GameOpti runner for this game? Close Steam/Heroic/Lutris first. Existing launch arguments are preserved with a reversible backup.")
                        : qsTr("Restore the launch settings saved before GameOpti runner setup? Independent later changes stop automatic restoration."); color: App.Theme.text; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: panel.confirmation === "install"; Layout.fillWidth: true; text: String(panel.component.source || ""); color: App.Theme.textMuted; wrapMode: Text.WrapAnywhere; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                Label { visible: panel.confirmation === "setup-runner" && Boolean(panel.statusData.launcherFlatpak); Layout.fillWidth: true; text: qsTr("Runner setup also grants this launcher Flatpak read-only access to the selected component and access to this game's private configuration/log folder. Restore removes only GameOpti's grants; shared grants remain while another game needs them."); color: App.Theme.textSecondary; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Label { visible: panel.confirmation === "setup-runner" && Boolean(panel.statusData.launcherFlatpak) && String(panel.gameData.launcher || "") === "Steam"; Layout.fillWidth: true; text: qsTr("Steam Flatpak also needs the existing GameOpti runner folder and the org.freedesktop.Flatpak host bridge. Setup adds these per-user permissions only after confirmation; Restore preserves prior independent permissions."); color: App.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: panel.bodySize }
                Flow { Layout.fillWidth: true; spacing: 8
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); id: confirmButton; property string fgActionType: "button"; text: qsTr("Confirm"); textPixelSize: panel.bodySize; onClicked: panel.run(panel.confirmation) }
                    AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); property string fgActionType: "button"; text: qsTr("Cancel"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: panel.confirmation = "" }
                }
            }
        }
        SurfaceCard {
            Layout.fillWidth: true; padding: 16
            contentItem: ColumnLayout {
                AppButton { Layout.maximumWidth: panel.availableWidth - 32; property string fgActionType: "button"; text: qsTr("Diagnostics and compatibility"); textPixelSize: panel.bodySize; kind: "ghost"; onClicked: panel.diagnostics = !panel.diagnostics }
                ColumnLayout {
                    visible: panel.diagnostics; Layout.fillWidth: true; spacing: 8
                    Label { Layout.fillWidth: true; color: App.Theme.textSecondary; wrapMode: Text.WrapAnywhere; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption
                        text: qsTr("Host Vulkan probe: %1. This does not prove loading inside Steam Linux Runtime or a launcher Flatpak.").arg(((panel.statusData.capabilities || {}).devices || []).join(", "))
                            + "\n" + String(panel.statusData.executable || "") + " · " + String(panel.statusData.environment || "")
                            + "\n" + qsTr("Selected runtime dependencies: %1").arg(String((panel.component.environmentCheck || {}).details || qsTr("Not checked")))
                            + "\n" + qsTr("Independent installations (kept): %1").arg(JSON.stringify(panel.statusData.externalInstallations || []))
                            + "\n" + qsTr("Game/session: %1 · Saved: %2 · Loaded PID: %3").arg(panel.gameId).arg(String(panel.statusData.configuredAt || "—")).arg((panel.statusData.runtimePids || []).join(", ") || "—")
                            + "\nSteam AppID=" + String((panel.statusData.steamUpdate || {}).appId || "—") + " · StateFlags=" + String((panel.statusData.steamUpdate || {}).stateFlags || 0)
                            + "\n" + String((panel.statusData.steamUpdate || {}).manifest || "") + "\n" + String((panel.statusData.steamUpdate || {}).error || "") }
                    Label { Layout.fillWidth: true; text: panel.statusData.generationVerified ? qsTr("The matching session log confirms initialized interpolation and submitted generated frames. This does not measure image quality or input latency.") : qsTr("Generated frames: not confirmed. Overlay FPS alone is not proof; real/output FPS are unavailable without a source that distinguishes them."); color: App.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                    Label { Layout.fillWidth: true; text: String((panel.statusData.runtimeEvidence || {}).sessionAt || "") + "\n" + String((panel.statusData.runtimeEvidence || {}).log || "") + "\n" + ((panel.statusData.runtimeEvidence || {}).errors || []).join("\n"); color: App.Theme.warning; wrapMode: Text.WrapAnywhere; font.pixelSize: panel.couchMode ? 18 : App.Theme.fontCaption }
                    Flow { Layout.fillWidth: true; spacing: 8
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); property string fgActionType: "button"; text: qsTr("Open configuration / logs"); textPixelSize: panel.bodySize; kind: "secondary"; onClicked: Qt.openUrlExternally("file://" + String(panel.statusData.profileDirectory || "")) }
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); visible: panel.backend !== "off"; property string fgActionType: "button"; text: qsTr("Update / reinstall"); textPixelSize: panel.bodySize; kind: "secondary"; enabled: !panel.statusData.busy; onClicked: panel.ask("install") }
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); visible: Boolean(panel.component.managed); property string fgActionType: "button"; text: qsTr("Remove component"); textPixelSize: panel.bodySize; kind: "secondary"; enabled: !panel.statusData.busy; onClicked: panel.ask("remove") }
                        AppButton { width: Math.min(implicitWidth, panel.availableWidth - 32); visible: Boolean(panel.statusData.runnerBackup); property string fgActionType: "button"; text: qsTr("Restore runner settings"); textPixelSize: panel.bodySize; kind: "secondary"; enabled: !panel.statusData.busy; onClicked: panel.ask("restore-runner") }
                    }
                }
            }
        }
    }
}
