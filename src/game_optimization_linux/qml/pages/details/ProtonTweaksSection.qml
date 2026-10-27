pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

SurfaceCard {
    id: section
    signal integrationRequested()
    property var controller
    property var gameData: ({})
    property bool couchMode: false
    property bool collapsible: false
    property bool expanded: !collapsible
    readonly property bool detailsOpen: !collapsible || expanded
    readonly property string gameId: String(gameData && gameData.id || "")
    property var entries: []
    property var states: ({})
    property var savedStates: ({})
    property var information: ({})
    property var previewData: ({})
    property int categoryIndex: 0
    property bool advanced: false
    property bool integrationOpen: false
    readonly property bool launcherGame: ["Heroic", "Lutris"].indexOf(String(gameData.launcher || "")) >= 0
    property bool dirty: false
    property bool previewed: false
    property string errorMessage: ""
    property int padIndex: 0
    property var padFocusedItem: null
    readonly property int bodySize: couchMode ? 17 : 13
    readonly property int captionSize: couchMode ? 14 : 12
    readonly property var categories: [qsTr("Display and input"), qsTr("Latency"), qsTr("Diagnostics"), qsTr("Compatibility")]
    padding: couchMode ? 18 : 14

    function labelFor(id, fallback) {
        var labels = {
            "wayland": qsTr("Native Wayland"), "disable_hdr": qsTr("Disable HDR reporting"),
            "prefer_sdl": qsTr("Prefer SDL controller input"), "low_latency": qsTr("Low Latency Layer"),
            "latency_reflex": qsTr("Expose Reflex through the layer"), "no_ntsync": qsTr("Disable ntsync"),
            "use_wined3d": qsTr("Use WineD3D"), "proton_log": qsTr("Proton logging"),
            "no_esync": qsTr("Disable esync"), "no_fsync": qsTr("Disable fsync"),
            "disable_nvapi": qsTr("Disable NVAPI"), "hide_nvidia_gpu": qsTr("Hide NVIDIA GPU"),
            "large_address_aware": qsTr("Force large-address awareness"), "old_gl_string": qsTr("Use old OpenGL string"),
            "steam_deck_spoof": qsTr("Steam Deck spoof"), "fsr4_upgrade": qsTr("FSR 4 upgrade"),
            "rdna3_wmma_workaround": qsTr("RDNA3 WMMA workaround")
        }
        return labels[id] || fallback
    }
    function descriptionFor(id) {
        var help = {
            "wayland": qsTr("Requires a Wayland session and winewayland in the selected runner. Keep Default with Gamescope. Some overlays and launchers may need XWayland."),
            "disable_hdr": qsTr("Workaround for incorrect HDR detection in DXVK/DXGI. Default keeps automatic detection. Configure HDR in the display, compositor and game; this switch cannot add HDR support."),
            "prefer_sdl": qsTr("Use the runner's SDL controller path instead of HIDRAW/Steam Input. Mappings, vibration and special controller features may change. Use for input compatibility."),
            "low_latency": qsTr("Requires the bundled Vulkan layer for the game's architecture and a game using Anti-Lag 2 or Reflex. Enable that option in the game. Layer loading alone does not prove lower latency."),
            "latency_reflex": qsTr("Select Reflex instead of Anti-Lag 2 in Low Latency Layer. Requires the layer, compatible DXVK-NVAPI and game support. No GPU identity spoofing is applied; the game may not expose Reflex."),
            "no_ntsync": qsTr("Synchronization workaround for runners supporting ntsync. Default lets the runner choose. Disabling fsync or esync alone does not disable ntsync; the kernel and /dev/ntsync must also support it."),
            "use_wined3d": qsTr("OpenGL fallback for Direct3D 9/10/11 instead of DXVK. Does not replace the Direct3D 12 renderer. Vulkan layers and external Vulkan frame generation cannot process this path."),
            "proton_log": qsTr("Write steam-APPID.log for the next launch. Logging can reduce performance and logs may contain personal paths. Turn it back to Default after diagnosis."),
            "no_esync": qsTr("Disable esync for compatibility testing. Newer runners may prefer ntsync or fsync, so this override may not affect the chosen path."),
            "no_fsync": qsTr("Disable fsync for compatibility testing. Default keeps the runner's selection; newer runners may use ntsync instead."),
            "disable_nvapi": qsTr("Disable NVAPI for compatibility testing. Conflicts with Reflex through NVAPI and can remove DLSS or other NVIDIA features."),
            "hide_nvidia_gpu": qsTr("Hide NVIDIA identity to work around a game's vendor-specific path. Can hide DLSS/Reflex. Not a general performance setting."),
            "large_address_aware": qsTr("Legacy 32-bit compatibility option. Many Proton versions already enable large-address awareness. Keep Default unless diagnosing a specific game."),
            "old_gl_string": qsTr("Legacy OpenGL version-string workaround for old games. Relevant to WineD3D/OpenGL, not DXVK or Direct3D 12."),
            "steam_deck_spoof": qsTr("Existing experimental override: exposes SteamDeck=1 and can alter a game's interface or graphics choices. Does not turn this computer into a Steam Deck."),
            "fsr4_upgrade": qsTr("Existing experimental profile option. Requires an independently verified runner, upscaler, driver and GPU combination. Configure working upscalers on their dedicated page."),
            "rdna3_wmma_workaround": qsTr("Existing experimental DXIL-SPIRV option. Use only with a documented RDNA3 shader issue and matching components. Other DXIL_SPIRV_CONFIG tokens are preserved.")
        }
        return help[id] || ""
    }
    function category(entry) {
        if (entry.category === "display") return 0
        if (entry.category === "latency") return 1
        if (entry.category === "debug") return 2
        return 3
    }
    function supported(id) { return String(((information.capabilities || {}).support || {})[id] || "unknown") }
    function supportLabel(id) {
        var state = supported(id)
        return state === "present" ? qsTr("Runner support found") : state === "unsupported" ? qsTr("Not found in selected runner") : qsTr("Support not confirmed")
    }
    function conflictText(id) {
        var messages = {
            "wayland_gamescope": qsTr("Native Wine Wayland conflicts with the managed Gamescope path. Keep Wayland at Default or change Gamescope in Optimization."),
            "reflex_requires_layer": qsTr("Reflex mode requires Low Latency Layer to be Enabled."),
            "layer_requires_vulkan": qsTr("Low Latency Layer needs Vulkan. Return WineD3D to Default before enabling it."),
            "reflex_requires_nvapi": qsTr("Reflex mode conflicts with Disable NVAPI."),
            "external_layer_veto": qsTr("The launch environment disables Low Latency Layer. Review DISABLE_LOW_LATENCY_LAYER in the launcher; GameOpti preserves that setting."),
            "latency_stack": qsTr("Another latency layer or external frame generator is selected. Test one latency path at a time before enabling this layer.")
        }
        return messages[id] || id
    }
    function accept(result, replaceDraft) {
        information = result
        entries = result.entries ? Array.from(result.entries) : []
        if (replaceDraft) {
            states = Object.assign({}, result.states || {})
            savedStates = Object.assign({}, result.states || {})
            dirty = false; previewed = false; previewData = ({})
        }
        errorMessage = ""
    }
    function load(force) {
        if (!controller || !controller.getProtonTweaks || !gameId) return
        var result = controller.getProtonTweaks(gameId) || ({})
        if (result.success) accept(result, Boolean(force) || !dirty)
        else errorMessage = String(result.error || qsTr("Proton Tweaks are unavailable"))
    }
    function setState(id, state) {
        var next = Object.assign({}, states)
        if (state === "default") delete next[id]
        else next[id] = state
        states = next; dirty = true; previewed = false; previewData = ({})
    }
    function reset(all) {
        var next = Object.assign({}, states)
        for (var i=0; i<entries.length; i++)
            if (all || category(entries[i]) === categoryIndex) delete next[entries[i].id]
        states = next; dirty = true; previewed = false; previewData = ({})
    }
    function preview() {
        if (!controller || !controller.previewProtonTweaks) return
        var result = controller.previewProtonTweaks(gameId, {"states": states}) || ({})
        if (result.success) { previewData=result; previewed=true; errorMessage="" }
        else errorMessage=String(result.error || qsTr("Could not preview changes"))
    }
    function save() {
        if (!previewed) { preview(); return }
        var result=controller.saveProtonTweaks(gameId, {"states":states}) || ({})
        if (result.success) accept(result, true)
        else errorMessage=result.error === "Resolve the marked conflicts before applying" ? qsTr("Resolve the marked conflicts before applying") : String(result.error || qsTr("Could not save Proton Tweaks"))
    }
    function environmentText() {
        var env=previewed ? previewData.environment || {} : information.environment || {}
        return Object.keys(env).sort().map(function(key) { return key+"="+env[key] }).join("\n") || qsTr("No GameOpti overrides")
    }
    function controls(item, result) {
        if (!item || !item.visible || !item.enabled) return
        if (item.tweakAction !== undefined) result.push(item)
        var children=item.children || []
        for(var i=0;i<children.length;i++) controls(children[i], result)
    }
    function handlePadAction(action) {
        if (action === "Back") return false
        var list=[]; controls(section, list)
        if (!list.length) return false
        var focused=list.findIndex(function(item) { return item.activeFocus })
        if (focused>=0) padIndex=focused
        if (action==="NavigateDown" || action==="NavigateUp") padIndex=Math.max(0, Math.min(list.length-1,padIndex+(action==="NavigateDown" ? 1 : -1)))
        padIndex=Math.min(padIndex,list.length-1)
        var item=list[padIndex]; padFocusedItem=item; item.forceActiveFocus()
        if (item.tweakAction==="combo" && ["NavigateLeft","NavigateRight","Confirm"].indexOf(action)>=0) {
            item.currentIndex=(item.currentIndex+(action==="NavigateLeft" ? -1 : 1)+item.count)%item.count
            item.activated(item.currentIndex)
        } else if (item.tweakAction==="button" && action==="Confirm") item.clicked()
        return true
    }

    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth:true; text:qsTr("Proton Tweaks"); color:App.Theme.text; font.pixelSize:section.couchMode ? 25 : 20; font.bold:true }
            StatusBadge { text:section.dirty ? qsTr("Unsaved changes") : qsTr("Saved settings"); status:section.dirty ? "warning" : "neutral" }
        }
        AppButton {
            property string tweakAction:"button"
            objectName:"protonTweaksToggle"; visible:section.collapsible; Layout.fillWidth:true
            kind:"secondary"; text:(section.detailsOpen ? "▾ " : "▸ ")+qsTr("Per-game tweaks")
            onClicked:section.expanded=!section.expanded
        }
        ColumnLayout {
            objectName:"protonTweaksBody"; visible:section.detailsOpen; Layout.fillWidth:true; spacing:12
            Label { Layout.fillWidth:true; text:qsTr("Default removes only GameOpti's override. Every change requires a fresh game launch. Compatibility workarounds and diagnostics are not general FPS improvements."); color:App.Theme.textSecondary; font.pixelSize:section.bodySize; wrapMode:Text.WordWrap }
            SurfaceCard {
                Layout.fillWidth:true; padding:12
                contentItem:ColumnLayout {
                    spacing:6
                    Label { Layout.fillWidth:true; text:((section.information.capabilities || {}).runner || qsTr("Selected runner unknown"))+" · "+((section.information.capabilities || {}).boundary === "flatpak" ? qsTr("Launcher Flatpak") : qsTr("Host / Steam runtime")); color:App.Theme.text; font.pixelSize:section.bodySize; font.bold:true; wrapMode:Text.WrapAnywhere }
                    Label { Layout.fillWidth:true; text:qsTr("Components found in a runner do not prove that the game loaded them. Driver, architecture, game and sandbox requirements still apply."); color:App.Theme.textMuted; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
                    Label { Layout.fillWidth:true; text:(section.information.activation || {}).state === "active" ? qsTr("Launch integration configured · runtime not verified") : qsTr("Launch integration needs attention. Use Overview → Launch integration before testing."); color:(section.information.activation || {}).state === "active" ? App.Theme.textSecondary : App.Theme.warning; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
                    Flow { Layout.fillWidth:true; spacing:8
                        AppButton { property string tweakAction:"button"; text:qsTr("Refresh"); kind:"secondary"; compact:true; textPixelSize:section.bodySize; onClicked:section.load(false) }
                        AppButton { property string tweakAction:"button"; text:qsTr("Launch integration"); kind:"ghost"; compact:true; textPixelSize:section.bodySize; onClicked:section.couchMode ? section.integrationOpen=!section.integrationOpen : section.integrationRequested() }
                    }
                }
            }
            SurfaceCard {
                Layout.fillWidth:true; visible:section.couchMode && section.integrationOpen; padding:12
                contentItem:ColumnLayout {
                    spacing:8
                    Label { Layout.fillWidth:true; text:qsTr("Apply the saved profile to the launcher after Apply. Unsaved editor changes are not included."); color:App.Theme.textSecondary; font.pixelSize:section.bodySize; wrapMode:Text.WordWrap }
                    Label { Layout.fillWidth:true; visible:section.launcherGame; text:App.I18n.message(String((section.information.activation || {}).message || "")); color:App.Theme.textSecondary; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
                    Label { Layout.fillWidth:true; visible:!section.launcherGame; text:qsTr("Steam → Properties → General → Launch Options. Keep your other launch arguments when configuring the existing runner."); color:App.Theme.textSecondary; font.pixelSize:section.bodySize; wrapMode:Text.WordWrap }
                    TextEdit { id:runnerCommand; objectName:"tweaksRunnerCommand"; Layout.fillWidth:true; visible:!section.launcherGame; text:String(section.information.steamCommand || (section.information.activation || {}).steamLaunchCommand || ""); readOnly:true; selectByMouse:true; color:App.Theme.text; wrapMode:TextEdit.WrapAnywhere; font.pixelSize:section.captionSize; font.family:"monospace" }
                    Label { Layout.fillWidth:true; visible:section.launcherGame; text:Object.keys((section.information.activation || {}).environment || {}).sort().map(function(key) { return key+"="+section.information.activation.environment[key] }).join("\n"); color:App.Theme.textMuted; font.family:"monospace"; font.pixelSize:section.captionSize; wrapMode:Text.WrapAnywhere }
                    Repeater { model:section.launcherGame ? ((section.information.activation || {}).changes || []) : []
                        Label { required property var modelData; Layout.fillWidth:true; text:App.I18n.message(String(modelData)); color:App.Theme.textMuted; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
                    }
                    Flow { Layout.fillWidth:true; spacing:8
                        AppButton { property string tweakAction:"button"; objectName:"tweaksApplyLauncher"; visible:section.launcherGame; enabled:Boolean((section.information.activation || {}).canApply) && !section.dirty; text:qsTr("Apply to launcher"); textPixelSize:section.bodySize; onClicked:{ var result=section.controller.applyLauncherIntegration(section.gameId) || ({}); if(result.success) section.load(false); else section.errorMessage=App.I18n.message(String(result.error || result.message || "")) } }
                        AppButton { property string tweakAction:"button"; visible:!section.launcherGame; enabled:runnerCommand.text.length>0; text:qsTr("Copy runner command"); kind:"secondary"; textPixelSize:section.bodySize; onClicked:{runnerCommand.selectAll(); runnerCommand.copy(); runnerCommand.deselect()} }
                        AppButton { property string tweakAction:"button"; text:qsTr("Refresh"); kind:"secondary"; textPixelSize:section.bodySize; onClicked:section.load(false) }
                    }
                }
            }
            GridLayout {
                Layout.fillWidth:true; columns:section.width<650 ? 2 : 4; columnSpacing:6; rowSpacing:6
                Repeater { model:section.categories
                    AppButton { required property int index; required property string modelData
                        property string tweakAction:"button"; Layout.fillWidth:true; Layout.minimumWidth:0
                        text:modelData; textPixelSize:section.bodySize; compact:true
                        kind:section.categoryIndex===index ? "primary" : "secondary"
                        onClicked:{section.categoryIndex=index; section.padIndex=0}
                    }
                }
            }
            Label {
                Layout.fillWidth:true; visible:section.categoryIndex===1
                text:qsTr("Game architecture: %1 · layer binaries: %2").arg(((section.information.capabilities || {}).components || {}).gameArchitecture || qsTr("Unknown")).arg((((section.information.capabilities || {}).components || {}).lowLatencyArchitectures || []).join(" / ") || qsTr("Unknown"))
                color:App.Theme.textMuted; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap
            }
            AppButton { property string tweakAction:"button"; Layout.fillWidth:true; visible:section.categoryIndex===3; kind:"secondary"; text:(section.advanced ? "▾ " : "▸ ")+qsTr("Advanced compatibility options"); textPixelSize:section.bodySize; onClicked:section.advanced=!section.advanced }
            Repeater {
                model:section.entries
                SurfaceCard {
                    id:row
                    required property var modelData
                    readonly property string tweakId:String(modelData.id)
                    Layout.fillWidth:true; padding:12
                    visible:section.category(modelData)===section.categoryIndex && (section.categoryIndex!==3 || section.advanced)
                    contentItem:ColumnLayout {
                        spacing:7
                        GridLayout { Layout.fillWidth:true; columns:section.width<560 ? 1 : 2; rowSpacing:7
                            Label { Layout.fillWidth:true; Layout.minimumWidth:0; text:section.labelFor(row.tweakId,row.modelData.label); color:App.Theme.text; font.pixelSize:section.bodySize; font.bold:true; wrapMode:Text.WordWrap }
                            AppComboBox {
                                objectName:"tweak-"+row.tweakId; property string tweakAction:"combo"
                                Layout.preferredWidth:section.couchMode ? 220 : 175; Layout.maximumWidth:section.width-60; textPixelSize:section.bodySize
                                model:row.modelData.disabledValue===null ? [qsTr("Default"),qsTr("Enabled")] : [qsTr("Default"),qsTr("Enabled"),qsTr("Disabled")]
                                currentIndex:section.states[row.tweakId]==="enabled" ? 1 : section.states[row.tweakId]==="disabled" ? 2 : 0
                                Accessible.name:section.labelFor(row.tweakId,row.modelData.label)
                                onActivated:function(index) {section.setState(row.tweakId,["default","enabled","disabled"][index])}
                            }
                        }
                        Label { Layout.fillWidth:true; text:section.descriptionFor(row.tweakId); color:App.Theme.textSecondary; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
                        Label { Layout.fillWidth:true; text:section.supportLabel(row.tweakId)+" · "+String(row.modelData.environmentKey); color:section.supported(row.tweakId)==="present" ? App.Theme.textMuted : App.Theme.warning; font.pixelSize:section.captionSize; wrapMode:Text.WrapAnywhere }
                    }
                }
            }
            ColumnLayout { Layout.fillWidth:true; visible:section.categoryIndex===2; spacing:6
                Label { Layout.fillWidth:true; text:qsTr("Proton log folder: %1").arg(section.information.logDirectory || "—"); color:App.Theme.textMuted; font.pixelSize:section.captionSize; wrapMode:Text.WrapAnywhere }
                AppButton { property string tweakAction:"button"; text:qsTr("Open log folder"); kind:"secondary"; textPixelSize:section.bodySize; onClicked:if(section.controller && section.controller.openProtonLogs) section.controller.openProtonLogs(section.gameId) }
            }
            Flow { Layout.fillWidth:true; spacing:8
                AppButton { property string tweakAction:"button"; text:qsTr("Reset this section"); kind:"ghost"; textPixelSize:section.bodySize; onClicked:section.reset(false) }
                AppButton { property string tweakAction:"button"; text:qsTr("Reset all tweaks"); kind:"ghost"; textPixelSize:section.bodySize; onClicked:section.reset(true) }
            }
            Label { Layout.fillWidth:true; visible:section.dirty; text:qsTr("Resets change the editor first. Review and Apply to save. Other integrations and launcher-owned variables are preserved."); color:App.Theme.textMuted; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
            SurfaceCard {
                Layout.fillWidth:true; padding:12
                contentItem:ColumnLayout {
                    spacing:8
                    Label { Layout.fillWidth:true; text:section.previewed ? qsTr("Changes for the next launch") : qsTr("Saved GameOpti overrides"); color:App.Theme.text; font.bold:true; font.pixelSize:section.bodySize }
                    Repeater { model:section.previewData.changes || []
                        Label { required property var modelData; Layout.fillWidth:true; text:modelData.key+": "+(modelData.before===null ? qsTr("Default") : modelData.before)+" → "+(modelData.after===null ? qsTr("Default (remove override)") : modelData.after); color:App.Theme.textSecondary; font.family:"monospace"; font.pixelSize:section.captionSize; wrapMode:Text.WrapAnywhere }
                    }
                    Label { objectName:"tweaksEnvironment"; Layout.fillWidth:true; text:section.environmentText(); color:App.Theme.textMuted; font.family:"monospace"; font.pixelSize:section.captionSize; wrapMode:Text.WrapAnywhere }
                    Repeater { model:Object.keys(section.information.inheritedEnvironment || {})
                        Label { required property string modelData; Layout.fillWidth:true; text:qsTr("Launcher setting: %1=%2. Default preserves it.").arg(modelData).arg(section.information.inheritedEnvironment[modelData]); color:App.Theme.textSecondary; font.pixelSize:section.captionSize; wrapMode:Text.WrapAnywhere }
                    }
                    Repeater { model:section.previewData.conflicts || []
                        Label { required property string modelData; Layout.fillWidth:true; text:section.conflictText(modelData); color:App.Theme.warning; font.pixelSize:section.bodySize; wrapMode:Text.WordWrap }
                    }
                    Label { Layout.fillWidth:true; visible:(section.previewData.unsupported || []).length>0; text:qsTr("These overrides are not supported by the selected runner: %1").arg((section.previewData.unsupported || []).join(", ")); color:App.Theme.warning; font.pixelSize:section.bodySize; wrapMode:Text.WordWrap }
                    Label { Layout.fillWidth:true; text:qsTr("Applying saves configuration; it does not verify runtime operation or a performance benefit. Heroic/Lutris also need Apply to launcher in Overview."); color:App.Theme.textMuted; font.pixelSize:section.captionSize; wrapMode:Text.WordWrap }
                }
            }
            Label { Layout.fillWidth:true; visible:section.errorMessage.length>0; text:section.errorMessage; color:App.Theme.danger; wrapMode:Text.WordWrap; font.pixelSize:section.bodySize }
            Flow { Layout.fillWidth:true; spacing:8
                AppButton { property string tweakAction:"button"; objectName:"previewTweaks"; text:qsTr("Preview changes"); kind:"secondary"; textPixelSize:section.bodySize; enabled:section.dirty; onClicked:section.preview() }
                AppButton { property string tweakAction:"button"; objectName:"applyTweaks"; text:qsTr("Apply"); textPixelSize:section.bodySize; enabled:section.dirty && section.previewed && !(section.previewData.conflicts || []).length && !(section.previewData.unsupported || []).length; onClicked:section.save() }
            }
        }
    }
    Connections { target:section.controller || null; ignoreUnknownSignals:true
        function onProtonTweaksChanged(key) { if(String(key)===String(section.information.appId || "")) section.load(false) }
    }
    onGameIdChanged:{dirty=false; previewed=false; entries=[]; states=({}); savedStates=({}); information=({}); previewData=({}); load(true)}
    Component.onCompleted:load(true)
}
