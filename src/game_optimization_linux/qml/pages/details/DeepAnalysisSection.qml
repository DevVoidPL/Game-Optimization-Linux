pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../.." as App

// Read-only "Compression 2.0" deep analysis.  It never offers an action that
// changes game files; applying a plan belongs to a later stage.
SurfaceCard {
    id: section
    objectName: "deepAnalysisSection"

    property var controller
    property string gameId: ""
    property var analysis: ({})
    property string safetyFilter: "all"
    property var expandedGroups: ({})
    readonly property string status: String(analysis.status || "idle")
    readonly property bool running: status === "running"
    readonly property var summary: analysis.summary || ({})
    readonly property var progress: analysis.progress || ({})
    readonly property var languageCodes: (summary.languages || []).map(function(item) { return String(item.code) })

    Layout.fillWidth: true
    padding: 20
    elevated: true

    function refresh() {
        if (!controller || typeof controller.getDeepAnalysis !== "function" || !gameId) {
            analysis = ({})
            return
        }
        analysis = controller.getDeepAnalysis(gameId, safetyFilter) || ({})
    }
    function start() {
        if (controller && typeof controller.startDeepAnalysis === "function" && gameId)
            controller.startDeepAnalysis(gameId)
        refresh()
    }
    function cancel() {
        if (controller && typeof controller.cancelDeepAnalysis === "function")
            controller.cancelDeepAnalysis(gameId)
    }
    function setFilter(value) {
        safetyFilter = value
        refresh()
    }
    function chooseLanguage(code) {
        if (controller && typeof controller.setDeepAnalysisLanguage === "function")
            analysis = controller.setDeepAnalysisLanguage(gameId, code) || analysis
        refresh()
    }
    function exportReport(fileUrl) {
        if (!controller || typeof controller.exportDeepAnalysis !== "function") return
        var result = controller.exportDeepAnalysis(gameId, String(fileUrl)) || ({})
        exportMessage = result.success ? qsTr("Report exported: %1").arg(String(result.name || ""))
                                       : String(result.error || qsTr("The report could not be exported"))
    }
    property string exportMessage: ""
    function toggleGroup(identifier) {
        var next = Object.assign({}, expandedGroups)
        next[identifier] = !next[identifier]
        expandedGroups = next
    }
    function formatBytes(raw) {
        var bytes = Number(raw || 0)
        if (!isFinite(bytes) || bytes <= 0) return "0 B"
        if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GiB"
        if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MiB"
        if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KiB"
        return bytes + " B"
    }
    function categoryTitle(id) {
        if (id === "languages") return qsTr("Languages")
        if (id === "audio") return qsTr("Audio")
        if (id === "cache") return qsTr("Cache and logs")
        if (id === "residues") return qsTr("Leftovers")
        if (id === "textures") return qsTr("Textures and packages")
        return qsTr("Btrfs compression")
    }
    function languageLabel(code) {
        var names = { "en": qsTr("English"), "pl": qsTr("Polish"), "de": qsTr("German"), "fr": qsTr("French"),
                      "es": qsTr("Spanish"), "it": qsTr("Italian"), "pt": qsTr("Portuguese"), "ru": qsTr("Russian"),
                      "ja": qsTr("Japanese"), "ko": qsTr("Korean"), "zh": qsTr("Chinese"), "cs": qsTr("Czech") }
        return (names[code] || String(code).toUpperCase()) + " (" + code + ")"
    }
    function groupTitle(group) {
        var key = String(group.key || "")
        if (key.indexOf("lang:") === 0) return languageLabel(String(group.language || key.slice(5)))
        var labels = {
            "shared": qsTr("Shared / neutral audio"),
            "wwise_shared": qsTr("Shared Wwise banks (SFX)"),
            "crash_dumps": qsTr("Crash dumps"),
            "logs_old": qsTr("Old logs"),
            "logs_recent": qsTr("Recent logs"),
            "shader_cache_installed": qsTr("Shader cache of this game"),
            "engine_cache": qsTr("Engine cache"),
            "steam_shadercache_installed": qsTr("Steam shader cache of this game"),
            "steam_shadercache_uninstalled": qsTr("Steam shader cache of uninstalled games"),
            "compatdata_active": qsTr("Active Proton prefix (compatdata)"),
            "compatdata_uninstalled": qsTr("Proton prefixes of uninstalled games"),
            "redistributables": qsTr("Redistributable installers"),
            "backup_copies": qsTr("Backup copies"),
            "protected_backups": qsTr("Backups inside save/config folders"),
            "temporary_files": qsTr("Temporary files"),
            "texture_files": qsTr("Texture files (required)"),
            "packages_opaque": qsTr("Engine packages (core, opaque)"),
            "hd_texture_pack": qsTr("Separate HD texture pack"),
            "compressible": qsTr("Likely worth compressing"),
            "precompressed": qsTr("Already compressed formats"),
            "unknown_compressibility": qsTr("Unknown compressibility")
        }
        return labels[key] || key
    }
    function safetyLabel(value) {
        if (value === "safe") return qsTr("Safe")
        if (value === "review") return qsTr("Review")
        return qsTr("Unsupported")
    }
    function safetyTone(value) {
        return value === "safe" ? "available" : value === "review" ? "warning" : "unavailable"
    }
    function statusLabel() {
        if (running) return qsTr("Analyzing")
        if (status === "cancelled") return qsTr("Cancelled")
        if (status === "failed") return qsTr("Failed")
        if (status === "completed") return qsTr("Completed")
        return qsTr("Not analyzed")
    }

    onGameIdChanged: { expandedGroups = ({}); refresh() }
    onControllerChanged: refresh()
    Component.onCompleted: refresh()

    Connections {
        target: section.controller ? section.controller : null
        ignoreUnknownSignals: true
        function onDeepAnalysisChanged(changedGameId) {
            if (String(changedGameId) === section.gameId) section.refresh()
        }
    }

    FileDialog {
        id: exportDialog
        objectName: "deepAnalysisExportDialog"
        title: qsTr("Export deep analysis report")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [qsTr("JSON reports (*.json)")]
        onAccepted: section.exportReport(selectedFile.toString())
    }

    contentItem: ColumnLayout {
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Label { text: qsTr("Compression 2.0: deep analysis"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                Label {
                    objectName: "deepAnalysisReadOnlyNotice"
                    Layout.fillWidth: true
                    text: qsTr("The analysis does not change game files. Review items require manual confirmation in the next stage.")
                    color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap
                }
            }
            StatusBadge { objectName: "deepAnalysisStatus"; text: section.statusLabel(); status: section.running ? "running" : section.status === "completed" ? "available" : section.status === "failed" ? "error" : "paused" }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AppButton { objectName: "deepAnalyzeButton"; text: qsTr("Analyze deeply"); kind: "primary"; enabled: !section.running && section.gameId.length > 0; onClicked: section.start() }
            AppButton { objectName: "deepCancelButton"; visible: section.running; text: qsTr("Cancel"); kind: "secondary"; onClicked: section.cancel() }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "deepExportButton"; text: qsTr("Export JSON report"); kind: "secondary"; enabled: Boolean(section.analysis.hasPlan) && !section.running; onClicked: exportDialog.open() }
            AppButton { objectName: "deepApplyButton"; text: qsTr("Apply plan - available in the next stage"); kind: "secondary"; enabled: false }
        }

        ColumnLayout {
            visible: section.running
            Layout.fillWidth: true
            spacing: 4
            AppProgressBar { objectName: "deepAnalysisProgress"; Layout.fillWidth: true; indeterminateMode: true }
            Label {
                objectName: "deepAnalysisProgressText"
                Layout.fillWidth: true
                text: qsTr("%1 files, %2").arg(Number(section.progress.files || 0)).arg(section.formatBytes(section.progress.bytes))
                      + (section.progress.category ? " - " + section.categoryTitle(String(section.progress.category)) : "")
                color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption
            }
        }
        Label { visible: section.status === "cancelled"; Layout.fillWidth: true; text: qsTr("The analysis was cancelled. No incomplete report was saved."); color: App.Theme.warning; wrapMode: Text.WordWrap }
        Label { visible: section.status === "failed"; Layout.fillWidth: true; text: String(section.analysis.error || ""); color: App.Theme.danger; wrapMode: Text.WordWrap }

        GridLayout {
            visible: Boolean(section.analysis.hasPlan)
            Layout.fillWidth: true
            columns: 2; columnSpacing: 16; rowSpacing: 4
            Label { text: qsTr("Engine"); color: App.Theme.textMuted }
            Label { objectName: "deepEngineLabel"; Layout.fillWidth: true; text: String(section.summary.engine || "Unknown") + (section.summary.engineVersion ? " " + section.summary.engineVersion : "") + qsTr(" (confidence %1%)").arg(Math.round(Number(section.summary.engineConfidence || 0) * 100)); color: App.Theme.text; wrapMode: Text.WordWrap }
            Label { text: qsTr("File system"); color: App.Theme.textMuted }
            Label { Layout.fillWidth: true; text: String(section.summary.filesystem || "-"); color: App.Theme.text }
            Label { text: qsTr("Logical size"); color: App.Theme.textMuted }
            Label { Layout.fillWidth: true; text: section.formatBytes(section.summary.logicalBytes) + qsTr(" in %1 files").arg(Number(section.summary.fileCount || 0)); color: App.Theme.text }
            Label { text: qsTr("Physical size"); color: App.Theme.textMuted }
            Label {
                Layout.fillWidth: true
                readonly property var btrfs: section.summary.btrfs || ({})
                text: btrfs.exactPhysicalBytes !== null && btrfs.exactPhysicalBytes !== undefined
                      ? section.formatBytes(btrfs.exactPhysicalBytes) + qsTr(" (measured with compsize)")
                      : section.formatBytes(section.summary.allocatedBytes) + qsTr(" (allocated blocks, approximate)")
                color: App.Theme.text; wrapMode: Text.WordWrap
            }
        }
        Repeater {
            model: section.summary.engineEvidence || []
            delegate: Label { required property var modelData; Layout.fillWidth: true; text: "\u2022 " + String(modelData.detail) + " (" + String(modelData.source) + ")"; color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
        }
        Label {
            objectName: "deepNonBtrfsNotice"
            visible: Boolean(section.analysis.hasPlan) && !section.summary.isBtrfs
            Layout.fillWidth: true
            text: qsTr("Deep analysis is available; transparent compression requires Btrfs")
            color: App.Theme.warning; wrapMode: Text.WordWrap
        }
        Repeater {
            model: (section.summary.blockers || []).concat(section.summary.warnings || [])
            delegate: Label { required property string modelData; Layout.fillWidth: true; text: "\u2022 " + modelData; color: App.Theme.warning; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
        }

        RowLayout {
            visible: Boolean(section.analysis.hasPlan)
            Layout.fillWidth: true
            spacing: 8
            Label { text: qsTr("Target language"); color: App.Theme.textSecondary }
            AppComboBox {
                objectName: "deepTargetLanguage"
                Layout.preferredWidth: 220
                readonly property var codes: {
                    var list = section.languageCodes.slice()
                    var target = String(section.summary.targetLanguage || "")
                    if (target && list.indexOf(target) < 0) list.unshift(target)
                    return list
                }
                model: codes.map(function(code) { return section.languageLabel(code) })
                currentIndex: Math.max(0, codes.indexOf(String(section.summary.targetLanguage || "")))
                onActivated: function(index) { section.chooseLanguage(codes[index]) }
            }
            Label { Layout.fillWidth: true; text: qsTr("Source: %1. Nothing is selected for removal automatically.").arg(String(section.summary.targetLanguageSource || "-")); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
        }

        RowLayout {
            visible: Boolean(section.analysis.hasPlan)
            spacing: 6
            Repeater {
                model: ["all", "safe", "review", "unsupported"]
                delegate: AppButton {
                    required property string modelData
                    objectName: "deepFilter_" + modelData
                    compact: true
                    kind: section.safetyFilter === modelData ? "primary" : "secondary"
                    text: modelData === "all" ? qsTr("All") : section.safetyLabel(modelData)
                          + " (" + Number((section.analysis.counts || {})[modelData] || 0) + ")"
                    onClicked: section.setFilter(modelData)
                }
            }
        }

        Repeater {
            objectName: "deepCategoryRepeater"
            model: section.analysis.categories || []
            delegate: SurfaceCard {
                id: categoryCard
                required property var modelData
                objectName: "deepCategory_" + String(modelData.id)
                Layout.fillWidth: true
                padding: 14
                contentItem: ColumnLayout {
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: section.categoryTitle(String(categoryCard.modelData.id)); color: App.Theme.text; font.weight: Font.Bold }
                        Label { text: section.formatBytes(categoryCard.modelData.bytes) + qsTr(" in %1 files").arg(Number(categoryCard.modelData.files || 0)); color: App.Theme.textSecondary }
                    }
                    Label { visible: (categoryCard.modelData.groups || []).length === 0; text: qsTr("Nothing found for this filter"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                    Repeater {
                        model: categoryCard.modelData.groups || []
                        delegate: ColumnLayout {
                            id: groupRow
                            required property var modelData
                            readonly property string identifier: String(categoryCard.modelData.id) + "/" + String(modelData.key)
                            readonly property bool expanded: Boolean(section.expandedGroups[identifier])
                            objectName: "deepGroup_" + String(categoryCard.modelData.id) + "_" + String(modelData.key).replace(":", "_")
                            Layout.fillWidth: true
                            spacing: 3
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Label { Layout.fillWidth: true; text: section.groupTitle(groupRow.modelData); color: App.Theme.text; elide: Text.ElideRight }
                                Label { text: section.formatBytes(groupRow.modelData.bytes) + (groupRow.modelData.sizeExact ? "" : qsTr(" (estimated)")); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(Number(groupRow.modelData.confidence || 0) * 100)); color: App.Theme.textMuted }
                                StatusBadge { text: section.safetyLabel(String(groupRow.modelData.safety)); status: section.safetyTone(String(groupRow.modelData.safety)) }
                                AppButton { compact: true; kind: "ghost"; text: (groupRow.expanded ? "\u25BE " : "\u25B8 ") + qsTr("Why detected"); onClicked: section.toggleGroup(groupRow.identifier) }
                            }
                            ColumnLayout {
                                visible: groupRow.expanded
                                Layout.fillWidth: true
                                Layout.leftMargin: 12
                                spacing: 2
                                Label { Layout.fillWidth: true; text: String(groupRow.modelData.reason || ""); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
                                Label { Layout.fillWidth: true; text: groupRow.modelData.reversible ? qsTr("Reversible (restored by the launcher or regenerated)") : qsTr("Not reversible once removed"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                                Repeater {
                                    model: groupRow.expanded ? (groupRow.modelData.evidence || []) : []
                                    delegate: Label { required property var modelData; Layout.fillWidth: true; text: "\u2022 " + String(modelData.detail); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WrapAnywhere }
                                }
                                Repeater {
                                    model: groupRow.expanded ? (groupRow.modelData.warnings || []) : []
                                    delegate: Label { required property string modelData; Layout.fillWidth: true; text: "\u26A0 " + modelData; color: App.Theme.warning; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                                }
                                Repeater {
                                    // At most 100 relative paths per group; the rest is only counted.
                                    model: groupRow.expanded ? (groupRow.modelData.samplePaths || []) : []
                                    delegate: Label { required property string modelData; Layout.fillWidth: true; text: modelData; color: App.Theme.textSecondary; font.family: "monospace"; font.pixelSize: App.Theme.fontCaption; elide: Text.ElideMiddle }
                                }
                                Label { visible: Number(groupRow.modelData.remainingCount || 0) > 0; text: qsTr("and %1 more files").arg(Number(groupRow.modelData.remainingCount || 0)); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            }
                        }
                    }
                }
            }
        }
        Label { objectName: "deepExportMessage"; visible: section.exportMessage.length > 0; Layout.fillWidth: true; text: section.exportMessage; color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
    }
}
