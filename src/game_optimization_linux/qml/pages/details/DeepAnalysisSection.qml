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
    property var pathLimits: ({})
    property string exportMessage: ""
    readonly property string status: String(analysis.status || "idle")
    readonly property bool running: status === "running"
    readonly property var summary: analysis.summary || ({})
    readonly property var sizes: summary.sizes || ({})
    readonly property var sizeStates: sizes.states || ({})
    readonly property var savings: analysis.savings || ({})
    readonly property var progress: analysis.progress || ({})
    readonly property var globalGroups: analysis.globalGroups || []
    readonly property var languageCodes: (summary.languages || []).map(function(item) { return String(item.code) })
    readonly property var coreKeys: ["packages_opaque", "texture_files", "precompressed", "compressible", "unknown_compressibility"]

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
                                       : qsTr("The report could not be exported")
    }
    function openSystemPage() {
        if (controller && typeof controller.navigate === "function") controller.navigate("system")
    }
    function toggleGroup(identifier) {
        var next = Object.assign({}, expandedGroups)
        next[identifier] = !next[identifier]
        expandedGroups = next
    }
    function defaultPathLimit(group) {
        return coreKeys.indexOf(String(group.key || "")) >= 0 ? 0 : 10
    }
    function pathLimit(identifier, group) {
        var value = pathLimits[identifier]
        return value === undefined ? defaultPathLimit(group) : Number(value)
    }
    function setPathLimit(identifier, value) {
        var next = Object.assign({}, pathLimits)
        next[identifier] = value
        pathLimits = next
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
    function languageName(code) {
        var names = { "en": qsTr("English"), "pl": qsTr("Polish"), "de": qsTr("German"), "fr": qsTr("French"),
                      "es": qsTr("Spanish"), "it": qsTr("Italian"), "pt": qsTr("Portuguese"), "ru": qsTr("Russian"),
                      "ja": qsTr("Japanese"), "ko": qsTr("Korean"), "zh": qsTr("Chinese"), "cs": qsTr("Czech") }
        return names[code] || String(code).toUpperCase()
    }
    function languageLabel(code) { return languageName(code) + " (" + code + ")" }
    function roleLabel(role) {
        if (role === "target") return qsTr("target")
        if (role === "fallback") return qsTr("fallback")
        if (role === "shared") return qsTr("shared")
        if (role === "other") return qsTr("other")
        return ""
    }
    function groupTitle(group) {
        var key = String(group.key || "")
        if (key.indexOf("lang:") === 0) {
            var role = roleLabel(String(group.role || ""))
            return languageLabel(String(group.language || key.slice(5))) + (role ? " - " + role : "")
        }
        var labels = {
            "shared": qsTr("Shared / neutral audio"),
            "wwise_shared": qsTr("Shared Wwise banks (SFX)"),
            "crash_dumps": qsTr("Crash dumps"),
            "logs_old": qsTr("Old logs"),
            "logs_recent": qsTr("Recent logs"),
            "shader_cache_installed": qsTr("Shader cache of this game"),
            "engine_cache": qsTr("Engine cache"),
            "steam_shadercache_installed": qsTr("Steam shader cache of this game"),
            "steam_shadercache_uninstalled": qsTr("Steam shader cache of an uninstalled game"),
            "compatdata_active": qsTr("Active Proton prefix (compatdata)"),
            "compatdata_uninstalled": qsTr("Proton prefix of an uninstalled game"),
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
    function whyText(group) {
        var key = String(group.key || "")
        var role = String(group.role || "")
        if (key.indexOf("lang:") === 0) {
            if (role === "target") return qsTr("Selected target language; always kept.")
            if (role === "fallback") return qsTr("Default/fallback language for the target; always kept.")
            return qsTr("Language-specific data for a language other than the target.")
        }
        var texts = {
            "shared": qsTr("Audio without a language marker is treated as shared/neutral and is never removed."),
            "wwise_shared": qsTr("Wwise metadata marks these banks as shared (SFX); never removed."),
            "crash_dumps": qsTr("Crash dumps and crash reports are only used to debug a past crash."),
            "logs_old": qsTr("Old log files in a log folder."),
            "logs_recent": qsTr("Recent or unconfirmed log files; the game may still write them."),
            "shader_cache_installed": qsTr("Shader cache of an installed game; it is rebuilt while playing."),
            "engine_cache": qsTr("Engine derived-data cache; it is regenerated when needed."),
            "steam_shadercache_installed": qsTr("Steam shader cache of this installed game."),
            "steam_shadercache_uninstalled": qsTr("Steam shader cache left by a game that is no longer installed."),
            "compatdata_active": qsTr("Active Proton prefix; it can contain saves and settings."),
            "compatdata_uninstalled": qsTr("Proton prefix of an uninstalled game; it may still contain saves."),
            "redistributables": qsTr("Redistributable installers shipped with the game."),
            "backup_copies": qsTr("Backup copies of files that still exist next to them."),
            "protected_backups": qsTr("Backup copies inside save/config folders may be the only copy of user data."),
            "temporary_files": qsTr("Temporary files in a temp folder."),
            "texture_files": qsTr("Texture data required by the game, including mipmaps and lower quality levels."),
            "packages_opaque": qsTr("Opaque engine archives (core data); never opened or modified."),
            "hd_texture_pack": qsTr("Separate HD texture pack; remove it only through the launcher or DLC settings."),
            "compressible": qsTr("Text, executables, libraries and uncompressed audio usually compress well."),
            "precompressed": qsTr("Already compressed formats; no Btrfs savings are promised."),
            "unknown_compressibility": qsTr("Unknown binary format; measure a sample before compressing.")
        }
        return texts[key] || ""
    }
    function evidenceText(item) {
        var code = String(item.source || "")
        var detail = String(item.detail || "")
        if (code === "path") return detail
        if (code === "language_directory") return qsTr("Language directory: %1").arg(detail)
        if (code === "language_directory_siblings") return qsTr("Language directory next to other languages: %1").arg(detail)
        if (code === "language_directory_localized") return qsTr("Language directory in a localization folder: %1").arg(detail)
        if (code === "language_token") return qsTr("Language name in the file name: %1").arg(detail)
        if (code === "language_locale") return qsTr("Locale in the file name: %1").arg(detail)
        if (code === "language_code") return qsTr("Language code in the file name: %1").arg(detail)
        if (code === "wwise_shared") return qsTr("Wwise SoundbanksInfo: shared (SFX)")
        if (code === "wwise_language") return qsTr("Wwise SoundbanksInfo: language %1").arg(detail)
        if (code === "no_language_marker") return qsTr("No language segment in the path")
        if (code === "original_present") return qsTr("Original file is present: %1").arg(detail)
        if (code === "extension") return qsTr("File type: %1").arg(detail)
        if (code === "appid_installed") return qsTr("Steam AppID %1 is installed").arg(detail)
        if (code === "appid_uninstalled") return qsTr("Steam AppID %1: no app manifest, not in the game list").arg(detail)
        if (code === "classification") return qsTr("Classified by file type")
        return detail
    }
    function codeText(raw) {
        var parts = String(raw || "").split("|")
        var code = parts[0]
        if (code === "shader_stutter") return qsTr("Deleting it causes shader-compilation stutter until it is rebuilt")
        if (code === "engine_cache_rebuild") return qsTr("The next start may be slower while the cache is rebuilt")
        if (code === "redist_reinstall") return qsTr("Some launchers run these installers again on the next start")
        if (code === "hd_dlc") return qsTr("Optional DLC content; never removed automatically")
        if (code === "compatdata_saves") return qsTr("Check for save games before removing a Proton prefix")
        if (code === "btrfs_shared") return qsTr("Shared extents/reflinks were detected: a future executor must skip shared files, because defragmentation can break sharing and increase disk usage")
        if (code === "btrfs_shared_unknown") return qsTr("Shared extents were not measured: a future executor must check each file and skip shared ones")
        if (code === "hardlinks") return qsTr("Hard-linked files exist; rewriting them can break the link")
        if (code === "game_running") return qsTr("The game is running; files may change until it is closed")
        if (code === "scan_incomplete") return qsTr("The scan was not complete; a later stage must rescan before acting")
        if (code === "game_directory_missing") return qsTr("The game directory is missing or is not a real directory")
        if (code === "mount_points_skipped") return qsTr("%1 mount point(s) inside the game were not scanned: %2").arg(parts[1] || "0").arg(parts[2] || "")
        if (code === "symlinks_skipped") return qsTr("%1 symbolic link(s) were not followed").arg(parts[1] || "0")
        if (code === "entries_skipped") return qsTr("%1 unreadable entries and %2 vanished files were skipped").arg(parts[1] || "0").arg(parts[2] || "0")
        if (code === "hardlinks_counted_once") return qsTr("%1 hard-linked file(s) were counted once").arg(parts[1] || "0")
        if (code === "no_target_audio") return noTargetAudioText(parts[1] || "", String(parts[2] || "").split(",").filter(function(item) { return item.length > 0 }))
        return qsTr("Detection detail: %1").arg(code)
    }
    function noTargetAudioText(target, fallbacks) {
        if (target === "pl" && fallbacks.length === 1 && fallbacks[0] === "en")
            return qsTr("No separate Polish voice-over; the default English voice was kept")
        if (fallbacks.length === 0)
            return qsTr("No separate voice-over in the target language (%1); no audio language is removable").arg(languageName(target))
        return qsTr("No separate voice-over in the target language (%1); the default voice was kept: %2")
                .arg(languageName(target)).arg(fallbacks.map(function(code) { return languageName(code) }).join(", "))
    }
    function sourceText(value) {
        if (value === "steam_manifest") return qsTr("Steam app manifest")
        if (value === "system_language") return qsTr("system language")
        return qsTr("default")
    }
    function measurementSourceText(value) {
        if (value === "compression_result") return qsTr("the same compsize measurement as the compression summary")
        if (value === "analysis_cache") return qsTr("the existing compression analysis (compsize/btrfs du)")
        if (value === "btrfs_du") return qsTr("btrfs filesystem du")
        return qsTr("no measurement")
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

    onGameIdChanged: { expandedGroups = ({}); pathLimits = ({}); refresh() }
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

    component GroupRow: ColumnLayout {
        id: groupRow
        required property var group
        required property string identifier
        readonly property bool expanded: Boolean(section.expandedGroups[identifier])
        readonly property var samples: group.samplePaths || []
        readonly property int limit: section.pathLimit(identifier, group)
        readonly property int defaultLimit: section.defaultPathLimit(group)
        Layout.fillWidth: true
        spacing: 3
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label { Layout.fillWidth: true; text: section.groupTitle(groupRow.group); color: App.Theme.text; elide: Text.ElideRight }
            Label { text: section.formatBytes(groupRow.group.bytes) + (groupRow.group.sizeExact ? "" : qsTr(" (estimated)")); color: App.Theme.textSecondary }
            Label { text: qsTr("%1%").arg(Math.round(Number(groupRow.group.confidence || 0) * 100)); color: App.Theme.textMuted }
            StatusBadge { text: section.safetyLabel(String(groupRow.group.safety)); status: section.safetyTone(String(groupRow.group.safety)) }
            AppButton { compact: true; kind: "ghost"; text: (groupRow.expanded ? "\u25BE " : "\u25B8 ") + qsTr("Why detected"); onClicked: section.toggleGroup(groupRow.identifier) }
        }
        ColumnLayout {
            visible: groupRow.expanded
            Layout.fillWidth: true
            Layout.leftMargin: 12
            spacing: 2
            Label { Layout.fillWidth: true; text: section.whyText(groupRow.group); color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
            Label { Layout.fillWidth: true; text: groupRow.group.reversible ? qsTr("Reversible (restored by the launcher or regenerated)") : qsTr("Not reversible once removed"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
            Repeater {
                model: groupRow.expanded ? (groupRow.group.evidence || []) : []
                delegate: Label { required property var modelData; Layout.fillWidth: true; text: "\u2022 " + section.evidenceText(modelData); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WrapAnywhere }
            }
            Repeater {
                model: groupRow.expanded ? (groupRow.group.warnings || []) : []
                delegate: Label { required property string modelData; Layout.fillWidth: true; text: "\u26A0 " + section.codeText(modelData); color: App.Theme.warning; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
            }
        }
        Repeater {
            // Ten paths by default (none for big core/opaque groups), at most 100.
            model: groupRow.samples.slice(0, groupRow.limit)
            delegate: Label { required property string modelData; objectName: "deepPath"; Layout.fillWidth: true; Layout.leftMargin: 12; text: modelData; color: App.Theme.textSecondary; font.family: "monospace"; font.pixelSize: App.Theme.fontCaption; elide: Text.ElideMiddle }
        }
        RowLayout {
            Layout.leftMargin: 12
            spacing: 8
            AppButton {
                objectName: "deepShowMore"
                visible: groupRow.samples.length > groupRow.limit
                compact: true; kind: "ghost"
                text: qsTr("Show more (%1)").arg(groupRow.samples.length - groupRow.limit)
                onClicked: section.setPathLimit(groupRow.identifier, 100)
            }
            AppButton {
                objectName: "deepCollapse"
                visible: groupRow.limit > groupRow.defaultLimit
                compact: true; kind: "ghost"
                text: qsTr("Collapse")
                onClicked: section.setPathLimit(groupRow.identifier, groupRow.defaultLimit)
            }
            Label { visible: groupRow.limit >= groupRow.samples.length && Number(groupRow.group.remainingCount || 0) > 0; text: qsTr("and %1 more files").arg(Number(groupRow.group.remainingCount || 0)); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
        }
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
        Label { visible: section.status === "failed"; Layout.fillWidth: true; text: section.codeText(String(section.analysis.error || "")); color: App.Theme.danger; wrapMode: Text.WordWrap }

        GridLayout {
            objectName: "deepSizes"
            visible: Boolean(section.analysis.hasPlan)
            Layout.fillWidth: true
            columns: 2; columnSpacing: 16; rowSpacing: 4
            Label { text: qsTr("Engine"); color: App.Theme.textMuted }
            Label { objectName: "deepEngineLabel"; Layout.fillWidth: true; text: String(section.summary.engine || "Unknown") + (section.summary.engineVersion ? " " + section.summary.engineVersion : "") + qsTr(" (confidence %1%)").arg(Math.round(Number(section.summary.engineConfidence || 0) * 100)); color: App.Theme.text; wrapMode: Text.WordWrap }
            Label { text: qsTr("File system"); color: App.Theme.textMuted }
            Label { Layout.fillWidth: true; text: String(section.summary.filesystem || "-"); color: App.Theme.text }
            Label { text: qsTr("Logical size"); color: App.Theme.textMuted }
            Label { Layout.fillWidth: true; text: section.formatBytes(section.sizes.logicalSize) + qsTr(" in %1 files").arg(Number(section.summary.fileCount || 0)); color: App.Theme.text }
            Label { text: qsTr("Allocated blocks (estimate)"); color: App.Theme.textMuted }
            Label { objectName: "deepAllocatedSize"; Layout.fillWidth: true; text: section.formatBytes(section.sizes.allocatedSize); color: App.Theme.text }
            Label { text: qsTr("Actual compressed size"); color: App.Theme.textMuted }
            Label {
                objectName: "deepPhysicalSize"
                Layout.fillWidth: true
                text: section.sizeStates.physical === "exact"
                      ? qsTr("%1 (measured: %2)").arg(section.formatBytes(section.sizes.physicalSize)).arg(section.measurementSourceText(String(section.sizes.measurementSource)))
                      : qsTr("not measured")
                color: App.Theme.text; wrapMode: Text.WordWrap
            }
            Label { visible: section.sizeStates.savings === "exact"; text: qsTr("Measured Btrfs savings"); color: App.Theme.textMuted }
            Label { objectName: "deepMeasuredSavings"; visible: section.sizeStates.savings === "exact"; Layout.fillWidth: true; text: section.formatBytes(section.sizes.measuredSavings); color: App.Theme.text }
            Label { visible: section.sizeStates.exclusive === "exact"; text: qsTr("Exclusive / shared"); color: App.Theme.textMuted }
            Label { visible: section.sizeStates.exclusive === "exact"; Layout.fillWidth: true; text: section.formatBytes(section.sizes.physicalExclusiveSize) + " / " + section.formatBytes(section.sizes.sharedSize); color: App.Theme.text }
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
            model: (section.summary.notes || []).concat(section.summary.blockers || []).concat(section.summary.warnings || [])
            delegate: Label { required property string modelData; objectName: "deepNotice"; Layout.fillWidth: true; text: "\u2022 " + section.codeText(modelData); color: App.Theme.warning; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
        }

        SurfaceCard {
            objectName: "deepSavingsSummary"
            visible: Boolean(section.analysis.hasPlan)
            Layout.fillWidth: true
            padding: 14
            contentItem: GridLayout {
                columns: 2; columnSpacing: 16; rowSpacing: 4
                Label { Layout.columnSpan: 2; text: qsTr("Potential savings for this game"); color: App.Theme.text; font.weight: Font.Bold }
                Label { text: qsTr("Safe now"); color: App.Theme.textMuted }
                Label { objectName: "deepSafeNow"; Layout.fillWidth: true; text: section.formatBytes(section.savings.safeNow); color: App.Theme.text }
                Label { text: qsTr("Needs confirmation"); color: App.Theme.textMuted }
                Label { objectName: "deepNeedsConfirmation"; Layout.fillWidth: true; text: qsTr("%1 (not guaranteed)").arg(section.formatBytes(section.savings.needsConfirmation)); color: App.Theme.text }
                Label { text: qsTr("Btrfs compression potential"); color: App.Theme.textMuted }
                Label { Layout.fillWidth: true; text: qsTr("%1 eligible; savings depend on the data").arg(section.formatBytes(section.savings.btrfsEligible)); color: App.Theme.text; wrapMode: Text.WordWrap }
                Label { text: qsTr("Protected / unsupported"); color: App.Theme.textMuted }
                Label { Layout.fillWidth: true; text: section.formatBytes(section.savings.protected); color: App.Theme.text }
                Repeater {
                    model: section.savings.languages || []
                    delegate: Label {
                        required property var modelData
                        Layout.columnSpan: 2
                        text: section.languageLabel(String(modelData.code)) + ": " + section.formatBytes(modelData.bytes)
                              + (modelData.role ? " (" + section.roleLabel(String(modelData.role)) + ")" : "")
                        color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption
                    }
                }
                Label { visible: (section.savings.languages || []).length > 0; text: qsTr("Total other languages"); color: App.Theme.textMuted }
                Label { objectName: "deepOtherLanguages"; visible: (section.savings.languages || []).length > 0; Layout.fillWidth: true; text: section.formatBytes(section.savings.otherLanguages); color: App.Theme.text }
            }
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
            Label { Layout.fillWidth: true; text: qsTr("Source: %1. Nothing is selected for removal automatically.").arg(section.sourceText(String(section.summary.targetLanguageSource || ""))); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
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
                        delegate: GroupRow {
                            required property var modelData
                            objectName: "deepGroup_" + String(categoryCard.modelData.id) + "_" + String(modelData.key).replace(":", "_")
                            group: modelData
                            identifier: String(categoryCard.modelData.id) + "/" + String(modelData.key)
                        }
                    }
                }
            }
        }

        SurfaceCard {
            objectName: "deepGlobalLeftovers"
            visible: Boolean(section.analysis.hasPlan) && section.globalGroups.length > 0
            Layout.fillWidth: true
            padding: 14
            contentItem: ColumnLayout {
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: qsTr("Global Steam leftovers"); color: App.Theme.text; font.weight: Font.Bold }
                    Label { objectName: "deepGlobalTotal"; text: section.formatBytes(section.savings.global); color: App.Theme.textSecondary }
                }
                Label { Layout.fillWidth: true; text: qsTr("Leftovers of other, uninstalled games in this Steam library. They are not counted in this game's savings and cannot be removed through this game's plan."); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption; wrapMode: Text.WordWrap }
                Repeater {
                    model: section.globalGroups
                    delegate: GroupRow {
                        required property var modelData
                        group: modelData
                        identifier: "global/" + String(modelData.owner)
                    }
                }
                AppButton { objectName: "deepOpenSystemPage"; compact: true; kind: "secondary"; text: qsTr("Open system storage overview"); onClicked: section.openSystemPage() }
            }
        }
        Label { objectName: "deepExportMessage"; visible: section.exportMessage.length > 0; Layout.fillWidth: true; text: section.exportMessage; color: App.Theme.textSecondary; wrapMode: Text.WordWrap }
    }
}
