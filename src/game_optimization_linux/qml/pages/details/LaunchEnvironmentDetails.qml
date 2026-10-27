pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

ColumnLayout {
    id: card
    property var plan: ({})
    property var planSource: ({})
    property string commandText: "%command%"
    // "steam" (runner + Launch Options), "local" (GameOpti runner), "Heroic"/"Lutris" (launcher file).
    property string launcherKind: "steam"
    property var launcherPreview: ({})
    readonly property bool launcherFile: launcherKind === "Heroic" || launcherKind === "Lutris"
    // True while the optimization editor shows an unsaved draft preview.
    property bool draftActive: false
    property bool expanded: false
    property bool couchMode: false
    property bool controllerFocused: false
    readonly property int bodySize: couchMode ? 17 : 13
    readonly property var environment: plan.environment || ({})
    readonly property var rows: plan.environmentClassification || []
    readonly property var categories: ["activation", "inherited", "suppression", "boundary", "unresolved"]
    function toggleDetails() { expanded=!expanded }
    function backendName(id) {
        return ({"off": qsTr("Off"), "lsfg": "lsfg-vk", "mako": "MAKO Renderer", "afmf": "afmf-linux",
                 "reshade": "ReShade", "vkbasalt": "vkBasalt"})[String(id || "off")] || String(id)
    }
    // Only selected feature activations; suppression is never listed as a feature.
    function launcherFeaturesText() {
        var part=(card.draftActive && launcherPreview.draft) ? launcherPreview.draft : (launcherPreview.apply || ({}))
        var labels={"gamemode": "GameMode", "gamescope": "Gamescope", "mangohud": "MangoHud", "optiscaler": "OptiScaler",
                    "proton": qsTr("Proton Tweaks"), "gpu_memory": qsTr("GPU memory priority")}
        var values=(part.included || []).map(function(name) { return labels[name] || name })
        var env=part.environment || ({})
        if (env.SteamDeck==="1") values.push(qsTr("Steam Deck mode (requested)"))
        if ((part.settings || {}).disableUMU===true) values.push("disableUMU")
        var builtins=(launcherPreview.current || {}).builtins || ({})
        Object.keys(builtins).filter(function(name) { return builtins[name] }).forEach(function(name) {
            values.push(qsTr("%1 (enabled by %2)").arg(labels[name] || name).arg(launcherKind)) })
        return values.join(" · ") || qsTr("Default launch")
    }
    function featuresText() {
        if (launcherFile) return launcherFeaturesText()
        var values=[]
        var wrappers=plan.wrappers || []
        if (wrappers.indexOf("gamescope")>=0) values.push("Gamescope")
        if (wrappers.indexOf("gamemode")>=0) values.push("GameMode")
        if (plan.mangoHudActivationOwner && plan.mangoHudActivationOwner!=="none") values.push(plan.mangoHudActivationOwner==="mangoapp" ? "Mangoapp" : "MangoHud")
        var fg=String((plan.frameGeneration || {}).backend || "")
        if (fg && fg!=="off") values.push(qsTr("Frame generation: %1").arg(backendName(fg)))
        var activations=rows.filter(function(row) { return row.category==="activation" })
        var owners=activations.map(function(row) { return String(row.owner || "") })
        if (owners.indexOf("ReShade")>=0) values.push("ReShade")
        if (owners.indexOf("vkBasalt")>=0) values.push("vkBasalt")
        if (owners.indexOf("OptiScaler")>=0) values.push("OptiScaler")
        if (activations.some(function(row) { return row.key==="SteamDeck" })) values.push(qsTr("Steam Deck mode (requested)"))
        if (activations.some(function(row) { return row.key==="PROTON_FSR4_UPGRADE" })) values.push(qsTr("Proton FSR4 upgrade (requested)"))
        if (wrappers.indexOf("custom")>=0) values.push(qsTr("Custom wrapper"))
        if ((plan.customArguments || []).length) values.push(qsTr("Custom arguments: %1").arg((plan.customArguments || []).length))
        if ((plan.preLaunch || []).length || (plan.postExit || []).length) values.push(qsTr("Custom commands before/after"))
        return values.join(" · ") || qsTr("Default launch")
    }
    function categoryTitle(id) {
        if (id==="activation") return qsTr("Selected feature activation")
        if (id==="inherited") return qsTr("Inherited value preserved")
        if (id==="suppression") return qsTr("Targeted conflict suppression")
        if (id==="boundary") return qsTr("Launcher/runtime boundary")
        return qsTr("Unresolved conflict")
    }
    function ownerName(owner) { return owner==="Frame generation" ? qsTr("Frame generation") : owner==="Custom launch" ? qsTr("Custom launch") : String(owner || "") }
    function reasonText(row) {
        var d=String(row.detail || "")
        var r
        if (row.code==="inherited_enable") r=qsTr("Inherited activation request: %1").arg(d)
        else if (row.code==="automatic_load") r=qsTr("Layer without an opt-in variable would load automatically: %1").arg(d)
        else if (row.code==="enable_filter") r=qsTr("VK_LOADER_LAYERS_ENABLE forces this layer: %1").arg(d)
        else if (row.code==="instance_layers") r=qsTr("Only this generator entry is removed from VK_INSTANCE_LAYERS: %1").arg(d)
        else if (row.code==="enable_filter_removed") r=qsTr("Only this exact VK_LOADER_LAYERS_ENABLE entry is removed: %1").arg(d)
        else if (row.code==="allow_removed") r=qsTr("Only this exact VK_LOADER_LAYERS_ALLOW entry is removed: %1").arg(d)
        else if (row.code==="disable_filter") r=qsTr("Exact disable filter for a layer without a usable disable variable: %1").arg(d)
        else if (row.code==="fg_selected") r=qsTr("Selected frame generator: %1").arg(backendName(d))
        else if (row.code==="vkbasalt_duplicate") r=qsTr("Prevents a second implicit vkBasalt layer while the per-game layer is requested")
        else if (row.code==="vkbasalt_inherited") r=qsTr("Inherited ENABLE_VKBASALT=1 would activate vkBasalt")
        else if (row.code==="reshade_override") r=qsTr("Native-first DLL override for the ReShade loader")
        else if (row.code==="vkbasalt_layer") r=qsTr("Per-game vkBasalt layer")
        else if (row.code==="feature") r=qsTr("Selected in %1").arg(String(row.owner || d))
        else if (row.code==="inherited") r=qsTr("Inherited value preserved")
        else if (row.code==="boundary_restored") r=qsTr("Kept out of the Gamescope process and restored unchanged for the game")
        else if (row.code==="boundary_removed") r=qsTr("Not passed to the Gamescope process")
        else if (row.code==="boundary_unset") r=qsTr("Compositor-only guard is unset again for the game")
        else if (row.code==="custom_env") r=qsTr("Your custom launch variable")
        else if (row.code==="custom_refused") r=qsTr("Your custom value is not applied: GameOpti or the runtime already manages this variable")
        else r=String(row.reason || "")
        return row.gameOnly==="1" ? r+" · "+qsTr("game only, not Gamescope") : r
    }
    function rowText(row) {
        var target=row.target==="compositor" ? qsTr("Gamescope process") : row.target==="game" ? qsTr("game") : ""
        var head=row.key ? row.key+(row.value ? "="+row.value : "") : qsTr("No change")
        return head+(target ? " · "+target : "")+(row.owner ? " · "+ownerName(row.owner) : "")+"\n"+reasonText(row)
    }
    function valueText(value) {
        return value === null || value === undefined ? qsTr("(not set)") : (typeof value === "string" ? value : JSON.stringify(value))
    }
    function launcherLines(part) {
        var data = part || ({})
        var fields = launcherPreview.fields || ({})
        var lines = []
        var env = data.environment || ({})
        Object.keys(env).sort().forEach(function(key) { lines.push(String(fields.environment || "env") + ": " + key + "=" + card.valueText(env[key])) })
        if (data.wrapper) lines.push(String(fields.wrapper || "wrapper") + ": " + (data.wrapper.exe !== undefined ? card.valueText(data.wrapper.exe) + " " + String(data.wrapper.args || "") : String(data.wrapper.text || card.valueText(data.wrapper))))
        var settings = data.settings || ({})
        Object.keys(settings).sort().forEach(function(key) { lines.push((key === "launcherArgs" || key === "args" ? String(fields.arguments || key) : key) + ": " + card.valueText(settings[key])) })
        var dll = data.dll || ({})
        Object.keys(dll).sort().forEach(function(key) { lines.push("wine.overrides: " + key + "=" + card.valueText(dll[key])) })
        return lines.length ? lines.join("\n") : qsTr("Nothing")
    }
    function launcherSourceText() {
        var preview = launcherPreview || ({})
        var saved = preview.savedAt || ({})
        var restore = preview.restore || ({})
        return [qsTr("%1 game configuration: %2").arg(preview.launcher || launcherKind).arg(preview.configPath || "-"),
                qsTr("Optimization profile saved %1 · Proton Tweaks saved %2").arg(String(saved.optimization || "-")).arg(String(saved.protonTweaks || "-")),
                qsTr("Saved plan revision %1 · applied revision %2 at %3").arg(String((preview.apply || {}).revision || "-")).arg(String(restore.appliedRevision || "-")).arg(String(restore.appliedAt || "-"))].join("\n")
    }
    function sourceText() {
        var s=planSource || ({})
        var saved=s.savedFrameGenerationBackend ? backendName(s.savedFrameGenerationBackend) : qsTr("no profile")
        var lines=[qsTr("Frame generation: saved profile %1, saved %2").arg(saved).arg(String(s.frameGenerationConfiguredAt || "-")),
                   qsTr("Post-processing: saved profile %1").arg(backendName(s.postProcessingBackend)),
                   card.draftActive ? qsTr("Optimization settings: unsaved draft shown in this preview") : qsTr("Optimization settings: saved profile"),
                   qsTr("Plan revision %1 · application %2").arg(String(s.planRevision || "-")).arg(String(s.applicationCommit || qsTr("source checkout")))]
        return lines.join("\n")
    }
    spacing: 8
    Label { objectName:"launchFeatureSummary"; Layout.fillWidth:true; text:qsTr("Selected features: %1").arg(card.featuresText()); color:App.Theme.text; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
    Label { objectName:"launchPreviewSource"; Layout.fillWidth:true; text:card.draftActive ? qsTr("Preview of the unsaved optimization draft. Frame generation and post-processing always come from their saved profiles.") : qsTr("Preview of the saved profiles. Unsaved editor selections on other pages are not included."); color:card.draftActive ? App.Theme.warning : App.Theme.textMuted; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
    AppButton { objectName:"toggleLaunchTechnical"; Layout.fillWidth:true; text:(card.expanded ? "▾ " : "▸ ")+qsTr("Technical launch details"); textPixelSize:card.bodySize; kind:card.controllerFocused ? "primary" : "secondary"; onClicked:card.toggleDetails() }
    ColumnLayout {
        objectName:"launchTechnicalBody"; visible:card.expanded; Layout.fillWidth:true; spacing:8
        Label { objectName:"launchPlanSourceDetails"; Layout.fillWidth:true; text:card.sourceText(); color:App.Theme.textSecondary; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
        Label { objectName:"launchSteamExplanation"; visible:card.launcherKind === "steam"; Layout.fillWidth:true; text:qsTr("The Steam Launch Options entry stays the same. At every launch the runner from the installed GameOpti Flatpak rebuilds this plan from the saved profiles, so a change takes effect after Apply and a fresh game start. This preview is not proof of the actual runtime environment."); color:App.Theme.textMuted; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
        Label { visible:card.launcherKind === "steam"; Layout.fillWidth:true; text:qsTr("This is a template, not Steam's final command. Steam supplies the game command and runtime environment at launch. <preserved> marks values restored for the game."); color:App.Theme.textSecondary; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
        Label { objectName:"launchLocalExplanation"; visible:card.launcherKind === "local"; Layout.fillWidth:true; text:qsTr("GameOpti starts this local game through its own runner and rebuilds this plan from the saved profiles at every start. <game executable> stands for the selected executable. This preview is not proof of the actual runtime environment."); color:App.Theme.textMuted; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
        ColumnLayout {
            objectName:"launcherFilePreview"; visible:card.launcherFile; Layout.fillWidth:true; spacing:6
            Label { Layout.fillWidth:true; text:qsTr("%1 starts this game with its own settings file. GameOpti writes only the entries below with Apply to launcher and removes only its own entries with Restore.").arg(card.launcherKind); color:App.Theme.textMuted; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
            Label { objectName:"launcherPreviewSource"; Layout.fillWidth:true; text:card.launcherSourceText(); color:App.Theme.textSecondary; font.pixelSize:card.bodySize; wrapMode:Text.WrapAnywhere }
            Label { Layout.fillWidth:true; text:card.draftActive ? qsTr("Saved profile (written by Apply to launcher):") : qsTr("Apply to launcher writes:"); color:App.Theme.text; font.bold:true; font.pixelSize:card.bodySize }
            Label { objectName:"launcherApplyLines"; Layout.fillWidth:true; text:card.launcherLines(card.launcherPreview.apply); color:App.Theme.textMuted; font.family:"monospace"; font.pixelSize:card.couchMode ? 14 : 12; wrapMode:Text.WrapAnywhere }
            Label { visible:card.draftActive && card.launcherPreview.draft !== null && card.launcherPreview.draft !== undefined; Layout.fillWidth:true; text:qsTr("Unsaved draft (written only after Save and Apply to launcher):"); color:App.Theme.warning; font.bold:true; font.pixelSize:card.bodySize }
            Label { objectName:"launcherDraftLines"; visible:card.draftActive && card.launcherPreview.draft !== null && card.launcherPreview.draft !== undefined; Layout.fillWidth:true; text:card.launcherLines(card.launcherPreview.draft); color:App.Theme.warning; font.family:"monospace"; font.pixelSize:card.couchMode ? 14 : 12; wrapMode:Text.WrapAnywhere }
            Label { visible:card.draftActive && !card.launcherPreview.draft; Layout.fillWidth:true; text:qsTr("The unsaved draft writes the same launcher entries."); color:App.Theme.textMuted; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
            Label { Layout.fillWidth:true; text:qsTr("Current %1 settings:").arg(card.launcherKind); color:App.Theme.text; font.bold:true; font.pixelSize:card.bodySize }
            Label { objectName:"launcherCurrentLines"; Layout.fillWidth:true; text:card.launcherLines(card.launcherPreview.current); color:App.Theme.textMuted; font.family:"monospace"; font.pixelSize:card.couchMode ? 14 : 12; wrapMode:Text.WrapAnywhere }
            Label { Layout.fillWidth:true; text:qsTr("Restore puts back:"); color:App.Theme.text; font.bold:true; font.pixelSize:card.bodySize }
            Label { objectName:"launcherRestoreLines"; Layout.fillWidth:true; text:card.launcherPreview.restore && card.launcherPreview.restore.appliedAt ? card.launcherLines(card.launcherPreview.restore) + (card.launcherPreview.restore.byteIdentical ? "\n" + qsTr("The file is unchanged since Apply; Restore puts back the original file byte for byte.") : "") : qsTr("Nothing; GameOpti has not changed this game in %1").arg(card.launcherKind); color:App.Theme.textMuted; font.family:"monospace"; font.pixelSize:card.couchMode ? 14 : 12; wrapMode:Text.WrapAnywhere }
            Label { visible:String(card.launcherPreview.error || "").length > 0; Layout.fillWidth:true; text:App.I18n.message(String(card.launcherPreview.error || "")); color:App.Theme.warning; font.pixelSize:card.bodySize; wrapMode:Text.WordWrap }
        }
        Repeater { model:card.launcherFile ? [] : card.categories
            ColumnLayout {
                id: group
                required property string modelData
                readonly property var items: card.rows.filter(function(row) { return row.category===group.modelData })
                visible: items.length>0
                Layout.fillWidth:true; spacing:4
                Label { Layout.fillWidth:true; text:card.categoryTitle(group.modelData)+" ("+group.items.length+")"; color:group.modelData==="unresolved" ? App.Theme.warning : App.Theme.text; font.bold:true; font.pixelSize:card.bodySize }
                Repeater { model:group.items
                    Label { required property var modelData; objectName:"launchEnvironmentRow"; Layout.fillWidth:true; text:card.rowText(modelData); color:App.Theme.textMuted; font.family:"monospace"; font.pixelSize:card.couchMode ? 14 : 12; wrapMode:Text.WrapAnywhere }
                }
            }
        }
        TextEdit { objectName:"launchPreviewText"; visible:!card.launcherFile; Layout.fillWidth:true; readOnly:true; selectByMouse:true; text:card.launcherKind === "local" ? card.commandText.replace("%command%", "<game executable>") : card.commandText; color:App.Theme.text; font.family:"monospace"; font.pixelSize:card.couchMode ? 14 : 12; wrapMode:TextEdit.WrapAnywhere }
    }
}
