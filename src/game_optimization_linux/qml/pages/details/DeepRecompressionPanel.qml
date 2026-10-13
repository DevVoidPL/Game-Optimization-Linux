pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

// Stage 2B: controlled per-file Btrfs recompression.  Nothing is selected by
// default and recompression is never presented as reversible.
Item {
    id: panel
    objectName: "deepRecompressionPanel"
    implicitHeight: content.implicitHeight

    property var controller
    property string gameId: ""
    property var recompression: ({})
    property var selectedTypes: ({})
    property int levelChoice: 3
    property bool candidatesExpanded: false
    readonly property var capabilities: recompression.capabilities || ({})
    readonly property var plan: recompression.plan || ({})
    readonly property var counts: plan.counts || ({})
    readonly property var groups: plan.groups || []
    readonly property bool busy: Boolean(recompression.busy)
    readonly property var levels: capabilities.levels || []
    readonly property var selection: {
        var result = { "files": 0, "bytes": 0, "low": 0, "high": 0, "types": [] }
        for (var index = 0; index < groups.length; ++index) {
            var group = groups[index]
            if (!selectedTypes[group.type]) continue
            result.files += Number(group.files || 0)
            result.bytes += Number(group.bytes || 0)
            result.low += Number(group.savingsLow || 0)
            result.high += Number(group.savingsHigh || 0)
            result.types.push(group.type)
        }
        return result
    }

    Layout.fillWidth: true

    function refresh() {
        if (!controller || typeof controller.getDeepRecompression !== "function" || !gameId) {
            recompression = ({})
            return
        }
        recompression = controller.getDeepRecompression(gameId) || ({})
        var next = ({})
        for (var key in selectedTypes)
            if (selectedTypes[key] && groups.some(function(group) { return group.type === key })) next[key] = true
        selectedTypes = next
        if (levels.length && levels.indexOf(levelChoice) < 0) levelChoice = levels.indexOf(3) >= 0 ? 3 : levels[0]
    }
    function formatBytes(raw) {
        var bytes = Number(raw || 0)
        if (!isFinite(bytes) || bytes <= 0) return "0 B"
        if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GiB"
        if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MiB"
        if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KiB"
        return bytes + " B"
    }
    function levelLabel(level) {
        if (level === 1) return qsTr("Fast (zstd:1)")
        if (level === 6) return qsTr("Stronger (zstd:6)")
        return qsTr("Balanced (zstd:3)")
    }
    function analyze() {
        if (controller && typeof controller.analyzeDeepRecompression === "function")
            controller.analyzeDeepRecompression(gameId, levels.length ? levelChoice : -1)
        refresh()
    }
    function setSelected(type, value) {
        var next = Object.assign({}, selectedTypes)
        if (value) next[type] = true
        else delete next[type]
        selectedTypes = next
    }
    function selectRecommended() {
        var next = Object.assign({}, selectedTypes)
        for (var index = 0; index < groups.length; ++index)
            if (groups[index].recommended) next[groups[index].type] = true
        selectedTypes = next
    }
    function start() {
        if (controller && typeof controller.startDeepRecompression === "function")
            controller.startDeepRecompression(gameId, selection.types)
        confirmDialog.close()
        refresh()
    }
    function resultText(result) {
        if (!result || !result.operation) return ""
        var code = String(result.code || "")
        if (result.operation === "analyze")
            return result.success ? "" : codeText(code)
        if (!result.success && code) return codeText(code)
        if (result.state === "completed") return qsTr("Recompression finished.")
        if (result.state === "cancelled") return qsTr("Cancelled after the current file; already processed files stay recompressed.")
        if (result.state === "partial") return qsTr("Stopped: several files of one type gave no gain. Run a new analysis.")
        if (result.state === "failed") return qsTr("Stopped: a file changed during recompression. Run a new analysis.")
        return ""
    }
    function codeText(code) {
        if (code === "plan_stale" || code === "game_root_changed" || code === "shared_extents" || code === "capabilities_changed")
            return qsTr("Game files changed. Run the analysis again")
        if (code === "game_running") return qsTr("Close the game first.")
        if (code === "game_updating") return qsTr("The game is being updated; try again when the launcher is done.")
        if (code === "quarantine_active") return qsTr("Finish or restore the active quarantine first.")
        if (code === "not_btrfs") return qsTr("The game is not on Btrfs.")
        if (code === "filesystem_read_only") return qsTr("The file system is read-only.")
        if (code === "not_enough_space") return qsTr("Not enough free space for the largest file and a safety margin.")
        if (code === "capabilities_missing") return qsTr("The local btrfs-progs cannot recompress files with a per-file shared-extent check.")
        if (code === "deep_plan_required") return qsTr("Run the deep analysis first.")
        if (code === "nothing_selected") return qsTr("Select at least one file type.")
        return qsTr("The operation could not be completed: %1").arg(code)
    }
    function skipCount(name) { return Number(counts[name] || 0) }
    function sourceText(code) {
        if (code === "compression_result") return qsTr("compsize from the compression summary")
        if (code === "analysis_cache") return qsTr("existing compression analysis")
        if (code === "btrfs_du") return "btrfs filesystem du"
        return qsTr("no exact measurement")
    }

    onGameIdChanged: { selectedTypes = ({}); refresh() }
    onControllerChanged: refresh()
    Component.onCompleted: refresh()

    Connections {
        target: panel.controller ? panel.controller : null
        ignoreUnknownSignals: true
        function onDeepRecompressionChanged(changedGameId) {
            if (String(changedGameId) === panel.gameId) panel.refresh()
        }
    }

    Popup {
        id: confirmDialog
        objectName: "deepRecompressionConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, parent ? parent.width - 40 : 520)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: 20
        background: Rectangle { radius: App.Theme.radiusLarge; color: App.Theme.surface; border.width: 1; border.color: App.Theme.borderStrong }
        contentItem: ColumnLayout {
            spacing: 8
            Label { text: qsTr("Start recompression"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
            Label { Layout.fillWidth: true; text: qsTr("%1 files, %2, profile %3").arg(panel.selection.files).arg(panel.formatBytes(panel.selection.bytes)).arg(panel.levels.length ? panel.levelLabel(panel.levelChoice) : "zstd"); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
            Label {
                objectName: "deepRecompressionWarning"
                Layout.fillWidth: true
                text: qsTr("Recompression does not change the logical content of files, but it rebuilds their extents on Btrfs. The operation cannot be undone from the application. Shared extents are skipped.")
                color: App.Theme.warning; wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton { text: qsTr("Cancel"); kind: "secondary"; onClicked: confirmDialog.close() }
                AppButton { objectName: "deepRecompressionConfirmButton"; text: qsTr("Recompress"); kind: "primary"; onClicked: panel.start() }
            }
        }
    }

    ColumnLayout {
        id: content
        width: panel.width
        spacing: 6

        Label { text: qsTr("Recompression"); color: App.Theme.text; font.weight: Font.Bold }
        Label {
            objectName: "deepRecompressionCapabilities"
            Layout.fillWidth: true
            text: panel.recompression.available
                  ? qsTr("btrfs-progs %1: %2, per-file shared-extent check available").arg(String(panel.capabilities.btrfsVersion || "?"))
                        .arg(panel.levels.length ? qsTr("Zstd levels %1").arg(panel.levels.join("/")) : "zstd")
                  : qsTr("Recompression is unavailable: the local btrfs-progs cannot check shared extents per file.")
            color: panel.recompression.available ? App.Theme.textSecondary : App.Theme.warning
            wrapMode: Text.WordWrap
        }
        RowLayout {
            visible: Boolean(panel.recompression.available)
            spacing: 8
            Label { text: qsTr("Profile"); color: App.Theme.textSecondary }
            AppComboBox {
                objectName: "deepRecompressionProfile"
                visible: panel.levels.length > 0
                Layout.preferredWidth: 220
                model: panel.levels.map(function(level) { return panel.levelLabel(level) })
                currentIndex: Math.max(0, panel.levels.indexOf(panel.levelChoice))
                onActivated: function(index) { panel.levelChoice = panel.levels[index] }
            }
            Label { visible: panel.levels.length === 0; text: "zstd"; color: App.Theme.text }
            AppButton { objectName: "deepRecompressionAnalyze"; kind: "secondary"; text: qsTr("Analyze compressibility"); enabled: !panel.busy; onClicked: panel.analyze() }
        }
        Label {
            visible: Boolean(panel.recompression.available)
            Layout.fillWidth: true
            text: qsTr("A higher level can make writing and updating files slower; reading usually stays fast. The gain depends on the data, and already compressed files do not benefit.")
            color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap
        }
        ColumnLayout {
            objectName: "deepRecompressionPlan"
            visible: Boolean(panel.plan.planId)
            Layout.fillWidth: true
            spacing: 3
            Label { Layout.fillWidth: true; text: qsTr("Already compressed: %1 - skipped formats: %2 - shared/reflink: %3 - not worth it: %4 - candidates: %5").arg(panel.skipCount("already_compressed")).arg(panel.skipCount("format_skipped")).arg(panel.skipCount("shared")).arg(panel.skipCount("not_worth")).arg(panel.skipCount("candidate")); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
            Label { objectName: "deepRecompressionEstimate"; Layout.fillWidth: true; text: qsTr("Candidates: %1 logical; estimated savings %2 to %3 (zlib estimate, not a Zstd result)").arg(panel.formatBytes(panel.plan.candidateBytes)).arg(panel.formatBytes(panel.plan.savingsLow)).arg(panel.formatBytes(panel.plan.savingsHigh)); color: App.Theme.text; wrapMode: Text.WordWrap }
            Label { Layout.fillWidth: true; text: qsTr("Measurement source: %1").arg(panel.sourceText(String(panel.plan.measurementSource || "none"))); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
            Label { visible: Boolean(panel.recompression.planStale); Layout.fillWidth: true; text: qsTr("Game files changed. Run the analysis again"); color: App.Theme.danger; wrapMode: Text.WordWrap }
            RowLayout {
                spacing: 8
                AppButton { objectName: "deepRecompressionToggleList"; compact: true; kind: "ghost"; text: (panel.candidatesExpanded ? "\u25BE " : "\u25B8 ") + qsTr("Candidates by file type (%1)").arg(panel.groups.length); onClicked: panel.candidatesExpanded = !panel.candidatesExpanded }
                AppButton { objectName: "deepRecompressionRecommended"; compact: true; kind: "secondary"; text: qsTr("Select recommended"); enabled: !panel.busy && !panel.recompression.planStale; onClicked: panel.selectRecommended() }
            }
            Repeater {
                model: panel.candidatesExpanded ? panel.groups : []
                delegate: RowLayout {
                    id: typeRow
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 8
                    CheckBox {
                        objectName: "deepRecompressionType_" + String(typeRow.modelData.type).replace(".", "_")
                        enabled: !panel.busy && !panel.recompression.planStale
                        checked: Boolean(panel.selectedTypes[typeRow.modelData.type])
                        onToggled: panel.setSelected(String(typeRow.modelData.type), checked)
                    }
                    Label { Layout.fillWidth: true; text: String(typeRow.modelData.type) + " - " + qsTr("%1 files, %2").arg(Number(typeRow.modelData.files)).arg(panel.formatBytes(typeRow.modelData.bytes)) + (typeRow.modelData.recommended ? " - " + qsTr("recommended") : ""); color: App.Theme.text; elide: Text.ElideRight }
                    Label { text: qsTr("estimated %1 to %2").arg(panel.formatBytes(typeRow.modelData.savingsLow)).arg(panel.formatBytes(typeRow.modelData.savingsHigh)); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                }
            }
            Label { objectName: "deepRecompressionSelection"; Layout.fillWidth: true; text: qsTr("Selected: %1 files, %2; estimated savings %3 to %4").arg(panel.selection.files).arg(panel.formatBytes(panel.selection.bytes)).arg(panel.formatBytes(panel.selection.low)).arg(panel.formatBytes(panel.selection.high)); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
            RowLayout {
                spacing: 8
                AppButton { objectName: "deepRecompressionStart"; kind: "primary"; text: qsTr("Start recompression"); enabled: panel.selection.files > 0 && !panel.busy && !panel.recompression.planStale; onClicked: confirmDialog.open() }
                AppButton { objectName: "deepRecompressionCancel"; visible: panel.busy && String(panel.recompression.operation) === "recompress"; kind: "secondary"; text: qsTr("Cancel"); onClicked: if (panel.controller) panel.controller.cancelDeepRecompression(panel.gameId) }
            }
        }
        AppProgressBar {
            objectName: "deepRecompressionProgress"
            visible: panel.busy
            Layout.fillWidth: true
            from: 0; to: Math.max(1, Number((panel.recompression.progress || {}).totalFiles || 1))
            value: Number((panel.recompression.progress || {}).files || 0)
            indeterminateMode: String(panel.recompression.operation) === "analyze"
        }
        Label {
            visible: panel.busy && String(panel.recompression.operation) === "recompress"
            Layout.fillWidth: true
            text: qsTr("%1 of %2 files, %3; current: %4; %5 s").arg(Number((panel.recompression.progress || {}).files || 0)).arg(Number((panel.recompression.progress || {}).totalFiles || 0)).arg(panel.formatBytes((panel.recompression.progress || {}).bytes)).arg(String((panel.recompression.progress || {}).currentFile || "")).arg(Number((panel.recompression.progress || {}).elapsed || 0))
                  + " " + qsTr("Cancel stops after the current file; processed files are not rolled back.")
            color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WrapAnywhere
        }
        Label { objectName: "deepRecompressionResult"; visible: text.length > 0; Layout.fillWidth: true; text: panel.resultText(panel.recompression.result || ({})); color: (panel.recompression.result || {}).success ? App.Theme.success : App.Theme.warning; wrapMode: Text.WordWrap }
        Repeater {
            objectName: "deepRecompressionHistory"
            model: panel.recompression.history || []
            delegate: Label {
                required property var modelData
                objectName: "deepRecompressionHistoryEntry"
                Layout.fillWidth: true
                text: String(modelData.createdAt || "").slice(0, 16).replace("T", " ") + " - "
                      + qsTr("before %1, after %2, freed %3").arg(panel.formatBytes((modelData.before || {}).physicalSize || (modelData.before || {}).allocatedSize))
                          .arg(modelData.after && modelData.after.physicalSize ? panel.formatBytes(modelData.after.physicalSize) : qsTr("not measured"))
                          .arg(modelData.measuredSavedBytes !== null && modelData.measuredSavedBytes !== undefined ? panel.formatBytes(modelData.measuredSavedBytes) : qsTr("not measured"))
                      + " - " + qsTr("%1 recompressed, %2 without gain, %3 skipped, %4 s").arg(Number(modelData.compressed || 0)).arg(Number(modelData.noGain || 0)).arg(Number(modelData.skipped || 0)).arg(Number(modelData.seconds || 0))
                      + " - " + String(modelData.state)
                color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            CheckBox {
                objectName: "deepFutureCompression"
                enabled: Boolean((panel.recompression.futureProperty || {}).available) && !panel.busy
                checked: Boolean((panel.recompression.futureProperty || {}).enabled)
                onToggled: if (panel.controller) { panel.controller.setDeepFutureCompression(panel.gameId, checked); panel.refresh() }
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Also compress future files of this game")
                      + ((panel.recompression.futureProperty || {}).available ? "" : " - " + qsTr("unavailable: the local btrfs-progs does not document how to restore the previous property"))
                color: App.Theme.textSecondary; wrapMode: Text.WordWrap
            }
        }
    }
}
