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
    property bool recompressionMode: false
    property string confirmation: ""
    property string textTarget: ""
    readonly property alias analysisEditor: analysis
    readonly property alias recompressionEditor: recompression
    readonly property var padFocusedItem: keyboard.opened ? keyboard : menu.padFocusedItem
    function refresh() { analysis.refresh(); recompression.refresh() }
    function handlePadAction(action) {
        if (keyboard.opened) { keyboard.handleAction(action); return true }
        if (action === "Back" && confirmation) { confirmation = ""; menu.currentIndex = 0; return true }
        return menu.handlePadAction(action)
    }
    function ask(action) { confirmation = action; menu.currentIndex = 0 }
    function rows() {
        if (confirmation) return [
            {id:"cancel", title:qsTr("Cancel"), trigger:function() { root.confirmation = "" }},
            {id:"confirm", title:qsTr("Confirm"),
                enabled:confirmation === "recompress" ? !recompression.busy && !recompression.recompression.planStale && recompression.selection.files > 0 : !analysis.cleanupBusy && !analysis.planStale && analysis.selectionSummary.files > 0,
                description:confirmation === "recompress"
                    ? qsTr("Btrfs extents will be rebuilt. This operation cannot be undone from the application. Shared extents are skipped.")
                    : qsTr("Only selected groups are moved to reversible quarantine. Start and check the game before permanently removing the quarantine."),
                trigger:function() {
                    var action = root.confirmation; root.confirmation = ""
                    if (action === "recompress") recompression.start()
                    else analysis.startQuarantine()
                }}]
        var result = [{id:"mode", title:qsTr("Storage tools"), value:recompressionMode ? qsTr("Recompression") : qsTr("Deep analysis"),
            trigger:function() { root.recompressionMode = !root.recompressionMode; menu.currentIndex = 0 }}]
        if (recompressionMode) {
            var available = Boolean(recompression.recompression.available)
            var idle = !recompression.busy
            if (recompression.levels.length) result.push({id:"level", title:qsTr("Compression profile"), value:recompression.levelLabel(recompression.levelChoice), enabled:idle,
                adjust:function(d) {
                    var levels = recompression.levels
                    recompression.levelChoice = levels[(Math.max(0, levels.indexOf(recompression.levelChoice)) + d + levels.length) % levels.length]
                }})
            result.push({id:"analyze", title:qsTr("Analyze compressibility"), enabled:available && idle,
                description:qsTr("Requires Btrfs and a local btrfs-progs that can check shared extents per file."), trigger:function() { recompression.analyze() }})
            recompression.groups.forEach(function(group) {
                result.push({id:group.type, title:String(group.type), enabled:idle && !recompression.recompression.planStale,
                    value:(recompression.selectedTypes[group.type] ? qsTr("Selected") : qsTr("Not selected")) + " · " + String(group.files) + " · " + recompression.formatBytes(group.bytes),
                    description:qsTr("Estimated saving: %1 - %2. The result depends on the data.").arg(recompression.formatBytes(group.savingsLow)).arg(recompression.formatBytes(group.savingsHigh)),
                    trigger:function() { recompression.setSelected(group.type, !recompression.selectedTypes[group.type]) }})
            })
            result.push({id:"recommended", title:qsTr("Select recommended"), enabled:idle && available && !recompression.recompression.planStale, trigger:function() { recompression.selectRecommended() }})
            result.push({id:"clear", title:qsTr("Clear selection"), enabled:idle, trigger:function() { recompression.selectedTypes = ({}) }})
            result.push({id:"start", title:qsTr("Recompress selected files"), value:String(recompression.selection.files),
                enabled:available && idle && recompression.selection.files > 0 && !recompression.recompression.planStale,
                trigger:function() { root.ask("recompress") }})
            if (recompression.busy) result.push({id:"cancel", title:qsTr("Cancel"), trigger:function() { root.controller.cancelDeepRecompression(String(root.gameData.id)) }})
        } else {
            result.push({id:"analyze", title:qsTr("Deep analysis"), enabled:!analysis.running && !analysis.cleanupBusy,
                description:qsTr("Read-only analysis. Nothing is selected or removed automatically."), trigger:function() { analysis.start() }})
            if (analysis.running) result.push({id:"cancel", title:qsTr("Cancel analysis"), trigger:function() { analysis.cancel() }})
            if (analysis.analysis.hasPlan) {
                result.push({id:"sizes", title:qsTr("Storage measurements"),
                    value:qsTr("Logical: %1 · allocated estimate: %2").arg(analysis.formatBytes(analysis.sizes.logicalSize)).arg(analysis.formatBytes(analysis.sizes.allocatedSize)),
                    description:analysis.sizeStates.physical === "exact"
                        ? qsTr("Actual compressed size: %1").arg(analysis.formatBytes(analysis.sizes.physicalSize))
                        : qsTr("Actual compressed size not measured. Allocated blocks are only an estimate.")})
                result.push({id:"savings", title:qsTr("Potential and protected data"),
                    value:qsTr("Quarantine: %1 · protected: %2").arg(analysis.formatBytes(analysis.savings.selectableBytes)).arg(analysis.formatBytes(analysis.savings.protectedBytes)),
                    description:qsTr("Btrfs eligible: %1. Savings depend on the data; nothing is removed automatically.").arg(analysis.formatBytes(analysis.savings.btrfsEligible))})
                ;(analysis.summary.warnings || []).concat(analysis.summary.blockers || []).forEach(function(warning, index) {
                    result.push({id:"warning-" + index, title:qsTr("Analysis warning"), description:analysis.codeText(String(warning))})
                })
            }
            if (analysis.languageCodes.length) result.push({id:"language", title:qsTr("Target language"), value:String(analysis.summary.targetLanguage || ""),
                enabled:!analysis.running && !analysis.cleanupBusy && !analysis.hasActive,
                adjust:function(d) {
                    var codes = analysis.languageCodes
                    analysis.chooseLanguage(codes[(Math.max(0, codes.indexOf(String(analysis.summary.targetLanguage || ""))) + d + codes.length) % codes.length])
                }})
            result.push({id:"filter", title:qsTr("Safety filter"), value:analysis.safetyFilter,
                adjust:function(d) { var values = ["all","safe","review","unsupported"]; analysis.setFilter(values[(values.indexOf(analysis.safetyFilter) + d + values.length) % values.length]) }})
            var categories = analysis.analysis.categories || []
            categories.forEach(function(category) {
                ;(category.groups || []).forEach(function(group) {
                    result.push({id:String(group.groupId), title:String(group.label || group.title || group.key || group.groupId),
                        value:(analysis.selectedGroups[group.groupId] ? qsTr("Selected") + " · " : "") + analysis.actionabilityLabel(group) + " · " + analysis.formatBytes(group.bytes),
                        description:analysis.reasonText(group) + "\n" + (group.paths || []).slice(0, 4).map(function(p) { return typeof p === "string" ? p : String(p.path || p.relativePath || "") }).join("\n"),
                        enabled:analysis.isSelectable(group), trigger:function() { analysis.setSelected(group.groupId, !analysis.selectedGroups[group.groupId]) }})
                })
            })
            result.push({id:"safe", title:qsTr("Select all safe"), enabled:!analysis.cleanupBusy && !analysis.planStale && !analysis.hasActive,
                trigger:function() { analysis.selectAllSafe() }})
            result.push({id:"clear", title:qsTr("Clear selection"), trigger:function() { analysis.selectedGroups = ({}) }})
            result.push({id:"quarantine", title:qsTr("Move selection to quarantine"), value:String(analysis.selectionSummary.files),
                enabled:!analysis.cleanupBusy && !analysis.planStale && !analysis.hasActive && analysis.selectionSummary.files > 0,
                trigger:function() { root.ask("quarantine") }})
            if (analysis.cleanupBusy) result.push({id:"cancel-cleanup", title:qsTr("Cancel cleanup"), trigger:function() { root.controller.cancelDeepCleanup(String(root.gameData.id)) }})
            if (analysis.hasActive) {
                result.push({id:"launch", title:qsTr("Start the game and check"), enabled:!analysis.cleanupBusy && analysis.activeTransaction.canFinalize === true, trigger:function() { analysis.launchGame() }})
                result.push({id:"restore", title:qsTr("Restore quarantine"), enabled:!analysis.cleanupBusy && analysis.activeTransaction.canRestore === true, trigger:function() { analysis.restoreQuarantine() }})
                result.push({id:"finalize", title:qsTr("Delete quarantine and free space"), enabled:!analysis.cleanupBusy && analysis.activeTransaction.canFinalize === true,
                    description:qsTr("Permanent deletion requires typing the confirmation word. Keep the quarantine until you have tested the game."),
                    trigger:function() { root.textTarget = "finalize"; keyboard.open("", qsTr("Type DELETE to permanently remove quarantine")) }})
            }
            result.push({id:"export", title:qsTr("Export JSON report"), enabled:Boolean(analysis.analysis.hasPlan) && !analysis.running,
                trigger:function() { root.textTarget = "export"; keyboard.open("", qsTr("Report file path")) }})
            analysis.globalGroups.forEach(function(group, index) {
                result.push({id:"global-" + index, title:qsTr("Global Steam leftover"), value:analysis.formatBytes(group.bytes),
                    description:analysis.reasonText(group) + "\n" + String(group.label || group.key || "")})
            })
        }
        result.push({id:"refresh", title:qsTr("Refresh"), trigger:root.refresh})
        return result
    }
    Details.DeepAnalysisSection {
        id: analysis; visible: false; width: 1
        controller: root.controller; gameId: String(root.gameData.id || "")
    }
    Details.DeepRecompressionPanel {
        id: recompression; visible: false; width: 1
        controller: root.controller; gameId: String(root.gameData.id || "")
    }
    CouchOptionList {
        id: menu; anchors.fill: parent; couchScale: root.couchScale
        focusSuspended: keyboard.opened
        title: root.confirmation ? qsTr("Review storage operation") : root.recompressionMode ? qsTr("Recompression") : qsTr("Deep analysis")
        status: root.recompressionMode ? [recompression.busy ? qsTr("Working…") : recompression.recompression.available ? qsTr("Btrfs recompression available") : qsTr("Recompression unavailable on this filesystem or host"),
            recompression.resultText(recompression.recompression.result || ({}))].join(" · ")
            : [analysis.running ? qsTr("Analyzing: %1 files").arg(Number(analysis.progress.files || 0))
                : analysis.status === "failed" ? analysis.codeText(String(analysis.analysis.error || ""))
                : analysis.status === "completed" || analysis.status === "complete" ? qsTr("Analysis complete")
                : analysis.status === "cancelled" ? qsTr("Analysis cancelled") : qsTr("Ready for analysis"),
                analysis.cleanupResultText(analysis.cleanup.result || ({})), analysis.exportMessage,
                analysis.planStale ? qsTr("Game files changed. Analyze again before applying a plan.") : ""].filter(function(s) { return s.length }).join(" · ")
        rows: root.rows()
    }
    Timer { interval: 1500; repeat: true; running: root.visible; onTriggered: root.refresh() }
    CouchOnScreenKeyboard {
        id: keyboard; anchors.fill: parent; couchScale: root.couchScale
        onAccepted: function(value) {
            if (root.textTarget === "finalize") {
                // The same backend validates the exact word and transaction.
                root.controller.finalizeDeepQuarantine(String(root.gameData.id), String(analysis.activeTransaction.transactionId), value)
                analysis.refreshCleanup()
            } else analysis.exportReport(value)
            menu.focusCurrent()
        }
        onCancelled: menu.focusCurrent()
    }
    onGameIdChanged: {
        keyboard.close(false)
        confirmation = ""; textTarget = ""; menu.currentIndex = 0
        analysis.selectedGroups = ({})
        analysis.finalizeTyped = ""; analysis.steamVerifyHint = false; analysis.exportMessage = ""
    }
}