pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

// Couch Mode Updates: file changes of installed games and the re-analysis /
// compression work they need (not game downloads). Same language as Library:
// header, filter chips, list, bottom navigation.
//
// Focus zones: 1 filters, 0 list, 2 navigation (visual order filters, list,
// navigation). Confirm on a row opens the existing per-row actions; Compress
// keeps the existing verified-plan confirmation.
FocusScope {
    id: page
    objectName: "couchUpdates"

    property var controller
    property var navigation
    property real couchScale: 1.0
    readonly property real fitScale: height > 0 ? Math.min(couchScale, height / 1080) : couchScale
    readonly property real edgeMargin: 48 * fitScale
    readonly property real bottomNavMargin: 40 * fitScale
    readonly property real hintsBottomMargin: bottomNavMargin + bottomNav.height + App.Theme.couchSpaceS * fitScale
    readonly property real contentMargin: 96 * fitScale

    property var updatesData: controller && controller.updates ? controller.updates : []
    property var summaryData: controller && controller.updatesSummary
                              ? controller.updatesSummary : ({})
    property var pendingPlan: ({})
    // 0 list, 1 filters, 2 bottom navigation.
    property int focusZone: 0
    property int selectedAction: 0
    property int confirmSelection: 0
    property int filterIndex: 0
    property int selectedTile: 2
    property string sectionFilter: "all"
    property string retainedRowId: ""
    property bool actionsOpen: false
    property alias selectedIndex: updatesList.currentIndex
    readonly property bool confirmationOpen: confirmationOverlay.visible
    readonly property var selectedUpdate: updatesList.currentIndex >= 0
                                          && updatesList.currentIndex < stableUpdates.count
                                          ? stableUpdates.get(updatesList.currentIndex).updateData
                                          : ({})
    readonly property var artworkProbe: ({
        "gameId": resolvedGameId(selectedUpdate),
        "artworkSource": resolvedArtwork(selectedUpdate)
    })
    readonly property var renderedArtworkProbe: updatesList.currentItem ? ({
        "gameId": String(updatesList.currentItem.renderedCoverGameId || ""),
        "artworkSource": String(updatesList.currentItem.renderedCoverArtwork || "")
    }) : ({ "gameId": "", "artworkSource": "" })
    // Same order and meaning as before; `id` is the stable identifier.
    readonly property var actionModel: [
        {
            "id": "analyze", "icon": App.UiIcons.couchGlyphAnalyze, "iconOnLight": App.UiIcons.couchGlyphAnalyzeOnLight,
            "title": qsTr("Analyze"),
            "enabled": booleanValue(selectedUpdate, ["canAnalyze", "can_analyze"], false),
            "visible": booleanValue(selectedUpdate, ["canAnalyze", "can_analyze"], false)
        },
        {
            "id": "compress", "icon": App.UiIcons.couchGlyphCompress, "iconOnLight": App.UiIcons.couchGlyphCompressOnLight,
            "title": qsTr("Compress"),
            "enabled": booleanValue(selectedUpdate, ["canCompress", "can_compress"], false),
            "visible": booleanValue(selectedUpdate, ["canCompress", "can_compress"], false)
        },
        {
            "id": "ignore", "icon": App.UiIcons.couchGlyphCancel, "iconOnLight": App.UiIcons.couchGlyphCancelOnLight,
            "title": qsTr("Ignore"),
            "enabled": booleanValue(selectedUpdate, ["canIgnore", "can_ignore"], false),
            "visible": booleanValue(selectedUpdate, ["canIgnore", "can_ignore"], false)
        },
        {
            "id": "details", "icon": App.UiIcons.couchGlyphDetails, "iconOnLight": App.UiIcons.couchGlyphDetailsOnLight,
            "title": qsTr("View details"),
            "enabled": gameId(selectedUpdate).length > 0,
            "visible": gameId(selectedUpdate).length > 0
        },
        {
            "id": "back", "icon": App.UiIcons.couchGlyphPrevious, "iconOnLight": App.UiIcons.couchGlyphPreviousOnLight,
            "title": qsTr("Back"),
            "enabled": true,
            "visible": true
        }
    ]
    // Section filters use the backend's stable `sectionKey` values.
    readonly property var sectionOrder: ["compression_pending", "game_updates", "recently_optimized", "unavailable"]
    readonly property var sectionCounts: countSections(updatesData)
    readonly property var filterItems: buildFilterItems()
    readonly property var summaryCards: buildSummaryCards()
    readonly property var navTiles: [
        { "id": "library", "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight, "title": qsTr("Library"), "subtitle": qsTr("Your games") },
        { "id": "tasks", "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight, "title": qsTr("Tasks"), "subtitle": qsTr("Active and recent tasks") },
        { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "title": qsTr("Updates"), "subtitle": qsTr("File changes and re-analysis") },
        { "id": "settings", "icon": App.UiIcons.couchGlyphSettings, "iconOnLight": App.UiIcons.couchGlyphSettingsOnLight, "title": qsTr("Settings"), "subtitle": qsTr("App configuration") }
    ]

    signal backRequested()
    signal toastRequested(string message, string tone)
    signal sectionRequested(string section)

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (page.confirmationOpen)
                (page.confirmSelection === 0
                 ? updateCancelButton : updateConfirmButton).forceActiveFocus()
            else if (page.actionsOpen) {
                var tile = actionRepeater.itemAt(page.selectedAction)
                if (tile) tile.forceActiveFocus()
            } else if (page.focusZone === 1) {
                var chip = filterRepeater.itemAt(page.filterIndex)
                if (chip) chip.forceActiveFocus()
            } else if (page.focusZone === 2) {
                var entry = bottomNav.itemAt(page.selectedTile)
                if (entry) entry.forceActiveFocus()
            } else {
                updatesList.forceActiveFocus()
            }
        })
    }

    function value(source, keys, fallback) {
        var object = source || {}
        for (var index = 0; index < keys.length; ++index) {
            var candidate = object[keys[index]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function booleanValue(source, keys, fallback) {
        return value(source, keys, fallback) === true
    }

    function numberValue(source, keys, fallback) {
        var raw = Number(value(source, keys, fallback))
        return isFinite(raw) && raw >= 0 ? raw : Number(fallback || 0)
    }

    function hasNumber(source, key) {
        var raw = (source || {})[key]
        return raw !== undefined && raw !== null && raw !== "" && isFinite(Number(raw))
    }

    function friendlyMessage(raw) {
        var message = String(raw || "")
        if (message.indexOf("Traceback (most recent call last)") >= 0
                || message.indexOf("File \"") >= 0)
            return qsTr("The update check failed. See the application log for technical details.")
        return message
    }

    function gameId(update) {
        return String(value(update, ["gameId", "game_id"], ""))
    }

    function gameName(update) {
        return String(value(update, ["gameName", "name", "game_name", "title"], qsTr("Unknown game")))
    }

    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }

    function resolvedGameId(update) {
        return gameId(update)
    }

    function resolvedArtwork(update) {
        return String(value(update, ["effectiveArtworkUrl", "artworkUrl"], ""))
    }

    function resolvedLauncher(update) {
        return String(value(update, ["provider", "launcher"], ""))
    }

    function stateOf(update) {
        if (!booleanValue(update, ["libraryAvailable", "library_available"], true))
            return "Drive disconnected"
        if (String(value(update, ["error"], "")).length > 0)
            return "Error"
        return String(value(update, ["compressionState", "compression_state", "status"], "Up to date"))
    }

    function translatedState(raw) {
        switch (String(raw).trim().toLowerCase().replace(/_/g, " ")) {
        case "up to date": return qsTr("Up to date")
        case "update detected": return qsTr("Update detected")
        case "waiting for launcher": return qsTr("Waiting for launcher")
        case "analysis required": return qsTr("Analysis required")
        case "compression pending": return qsTr("Compression pending")
        case "compressing": return qsTr("Compressing")
        case "optimized": return qsTr("Optimized")
        case "verification required": return qsTr("Verification required")
        case "drive disconnected": return qsTr("Drive disconnected")
        case "unsupported filesystem": return qsTr("Unsupported filesystem")
        case "analyzing": return qsTr("Analyzing")
        case "queued": return qsTr("Queued")
        case "failed": return qsTr("Failed")
        case "error": return qsTr("Error")
        default: return String(raw || qsTr("Unknown"))
        }
    }

    function toneState(raw) {
        switch (String(raw).trim().toLowerCase().replace(/_/g, " ")) {
        case "up to date":
        case "optimized":
            return "completed"
        case "waiting for launcher":
        case "compressing":
        case "analyzing":
        case "queued":
            return "running"
        case "update detected":
        case "analysis required":
        case "compression pending":
        case "verification required":
            return "warning"
        case "drive disconnected":
        case "unsupported filesystem":
        case "failed":
        case "error":
            return "failed"
        default:
            return "not checked"
        }
    }

    // CouchStatePill tone for a row state (dot + text, never colour alone).
    function pillTone(raw) {
        if (String(raw).trim().toLowerCase() === "drive disconnected")
            return "unavailable"
        var tone = toneState(raw)
        return tone === "completed" ? "success" : tone === "running" ? "info"
             : tone === "warning" ? "warning" : tone === "failed" ? "danger" : "neutral"
    }

    function formatBytes(raw) {
        var bytes = Number(raw)
        if (!isFinite(bytes) || bytes < 0)
            return qsTr("Not available")
        var units = [qsTr("B"), qsTr("KiB"), qsTr("MiB"), qsTr("GiB"), qsTr("TiB")]
        var unit = 0
        while (bytes >= 1024 && unit < units.length - 1) {
            bytes /= 1024
            unit++
        }
        return (unit === 0 ? Math.round(bytes) : bytes.toFixed(bytes >= 100 ? 0 : 1))
                + " " + units[unit]
    }

    // Measured values only; anything unmeasured reads "Not measured", not "0 B".
    function measured(bytes, reliable) {
        var number = Number(bytes)
        return reliable && isFinite(number) && number > 0 ? formatBytes(number) : qsTr("Not measured")
    }
    function changedText(update) {
        return qsTr("Changed: %1").arg(measured(value(update, ["changedBytes", "changed_bytes"], 0),
                                                booleanValue(update, ["changesReliable"], false)))
    }
    function sizeText(update) {
        if (sectionOf(update) === "recently_optimized")
            return qsTr("Recovered: %1").arg(measured(value(update, ["actual_saved_bytes", "actualSavedBytes"], 0), true))
        return qsTr("On disk: %1").arg(measured(value(update, ["physicalSizeBytes"], 0),
                                                booleanValue(update, ["physicalSizeMeasuredByCompsize"], false)))
    }
    function identityText(update) {
        var parts = []
        var launcher = resolvedLauncher(update)
        if (launcher.length) parts.push(launcher)
        var build = String(value(update, ["buildId", "build_id"], ""))
        if (build.length) parts.push(qsTr("Build ID %1").arg(build))
        return parts.join("  ·  ")
    }

    function sectionOf(update) {
        return String(value(update, ["sectionKey", "section_key"], "game_updates"))
    }
    function matchesFilter(update) {
        if (sectionFilter === "all") return true
        if (sectionFilter === "unavailable")
            return !booleanValue(update, ["libraryAvailable", "library_available"], true)
        return sectionOf(update) === sectionFilter
    }
    function countSections(list) {
        var counts = { "total": 0 }
        var source = list || []
        for (var i = 0; i < source.length; ++i) {
            var update = source[i] || {}
            counts.total += 1
            var section = sectionOf(update)
            counts[section] = (counts[section] || 0) + 1
            if (!booleanValue(update, ["libraryAvailable", "library_available"], true))
                counts.unavailable = (counts.unavailable || 0) + 1
        }
        return counts
    }
    function filterLabel(id) {
        return id === "compression_pending" ? qsTr("To analyze")
             : id === "game_updates" ? qsTr("File changes")
             : id === "recently_optimized" ? qsTr("Optimized")
             : id === "unavailable" ? qsTr("Unavailable") : qsTr("All")
    }
    function buildFilterItems() {
        var items = []
        for (var i = 0; i < sectionOrder.length; ++i) {
            var id = sectionOrder[i]
            var count = sectionCounts[id] || 0
            if ((count > 0 && count < sectionCounts.total) || sectionFilter === id)
                items.push(id)
        }
        if (items.length === 0)
            return []
        var icons = {
            "all": [App.UiIcons.couchGlyphUpdates, App.UiIcons.couchGlyphUpdatesOnLight],
            "compression_pending": [App.UiIcons.couchGlyphAnalyze, App.UiIcons.couchGlyphAnalyzeOnLight],
            "game_updates": [App.UiIcons.couchGlyphRefresh, App.UiIcons.couchGlyphRefreshOnLight],
            "recently_optimized": [App.UiIcons.couchGlyphCompress, App.UiIcons.couchGlyphCompressOnLight],
            "unavailable": [App.UiIcons.couchGlyphLibraryOffline, App.UiIcons.couchGlyphLibraryOfflineOnLight]
        }
        return ["all"].concat(items).map(function(id) {
            return { "id": id, "label": id === "all" ? filterLabel(id)
                                         : qsTr("%1 (%2)").arg(filterLabel(id)).arg(page.sectionCounts[id] || 0),
                     "icon": icons[id][0], "iconOnLight": icons[id][1] }
        })
    }
    // Only values present in controller.updatesSummary are shown.
    function buildSummaryCards() {
        var cards = []
        if (hasNumber(summaryData, "needsCheckCount"))
            cards.push({ "id": "attention", "value": String(Math.floor(Number(summaryData.needsCheckCount))),
                         "label": qsTr("Need attention"),
                         "icon": App.UiIcons.couchGlyphStatusWarning, "iconOnLight": App.UiIcons.couchGlyphStatusWarningOnLight })
        if (hasNumber(summaryData, "pendingCount"))
            cards.push({ "id": "pending", "value": String(Math.floor(Number(summaryData.pendingCount))),
                         "label": qsTr("To analyze or queued"),
                         "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight })
        if (hasNumber(summaryData, "recentRecoveredBytes") && Number(summaryData.recentlyOptimizedCount) > 0)
            cards.push({ "id": "recovered", "value": formatBytes(summaryData.recentRecoveredBytes),
                         "label": qsTr("Recovered"),
                         "icon": App.UiIcons.couchGlyphStorage, "iconOnLight": App.UiIcons.couchGlyphStorageOnLight })
        return cards
    }

    function rowIdentifier(update, index) {
        var explicitId = String(value(update, ["rowId", "row_id"], ""))
        if (explicitId.length > 0)
            return explicitId
        var section = String(value(update, ["sectionKey", "section_key"], "updates"))
        var history = String(value(update, ["historyId", "history_id"], index))
        return section + ":" + gameId(update) + ":" + history
    }

    function synchronizeUpdates() {
        if (updatesList.currentIndex >= 0 && updatesList.currentIndex < stableUpdates.count)
            retainedRowId = String(stableUpdates.get(updatesList.currentIndex).rowId || "")

        var source = updatesData || []
        var wanted = []
        for (var sourceIndex = 0; sourceIndex < source.length; ++sourceIndex) {
            var update = source[sourceIndex] || {}
            if (!matchesFilter(update))
                continue
            wanted.push({
                "rowId": rowIdentifier(update, sourceIndex),
                "updateData": update
            })
        }

        for (var oldIndex = stableUpdates.count - 1; oldIndex >= 0; --oldIndex) {
            var keep = false
            for (var wantedIndex = 0; wantedIndex < wanted.length; ++wantedIndex) {
                if (stableUpdates.get(oldIndex).rowId === wanted[wantedIndex].rowId) {
                    keep = true
                    break
                }
            }
            if (!keep)
                stableUpdates.remove(oldIndex)
        }

        for (var index = 0; index < wanted.length; ++index) {
            var existingIndex = -1
            for (var modelIndex = index; modelIndex < stableUpdates.count; ++modelIndex) {
                if (stableUpdates.get(modelIndex).rowId === wanted[index].rowId) {
                    existingIndex = modelIndex
                    break
                }
            }
            if (existingIndex < 0)
                stableUpdates.insert(index, wanted[index])
            else if (existingIndex !== index)
                stableUpdates.move(existingIndex, index, 1)
            stableUpdates.setProperty(index, "updateData", wanted[index].updateData)
        }

        restoreSelection()
    }

    function restoreSelection() {
        if (stableUpdates.count === 0) {
            updatesList.currentIndex = -1
            if (actionsOpen)
                closeActions()
            // Called from Component.onCompleted, before filterItems is bound.
            if (focusZone === 0)
                focusZone = (filterItems || []).length > 0 ? 1 : 2
            return
        }
        if (retainedRowId.length > 0) {
            for (var index = 0; index < stableUpdates.count; ++index) {
                if (String(stableUpdates.get(index).rowId) === retainedRowId) {
                    updatesList.currentIndex = index
                    ensureSelectedAction()
                    return
                }
            }
        }
        updatesList.currentIndex = Math.max(0, Math.min(
            updatesList.currentIndex, stableUpdates.count - 1))
        retainedRowId = String(stableUpdates.get(updatesList.currentIndex).rowId || "")
        ensureSelectedAction()
    }

    function ensureSelectedAction() {
        var actions = page.actionModel || []
        if (actions[selectedAction] && actions[selectedAction].visible
                && actions[selectedAction].enabled)
            return
        for (var index = 0; index < actions.length; ++index) {
            if (actions[index].visible && actions[index].enabled) {
                selectedAction = index
                return
            }
        }
        selectedAction = 4
    }

    function selectUpdate(index) {
        if (index < 0 || index >= stableUpdates.count)
            return false
        var changed = updatesList.currentIndex !== index
        updatesList.currentIndex = index
        retainedRowId = String(stableUpdates.get(index).rowId || "")
        if (navigation)
            navigation.rememberFocus("updates", retainedRowId, index)
        ensureSelectedAction()
        return changed
    }

    function moveAction(direction) {
        var actions = page.actionModel || []
        var candidate = selectedAction + direction
        while (candidate >= 0 && candidate < actions.length) {
            if (actions[candidate].visible && actions[candidate].enabled) {
                var changed = selectedAction !== candidate
                selectedAction = candidate
                return changed
            }
            candidate += direction
        }
        return false
    }

    function setSectionFilter(id) {
        if (sectionFilter === id)
            return false
        sectionFilter = id
        retainedRowId = ""
        updatesList.currentIndex = -1
        synchronizeUpdates()
        updatesList.positionViewAtBeginning()
        return true
    }

    function openActions() {
        if (updatesList.currentIndex < 0)
            return false
        ensureSelectedAction()
        actionsOpen = true
        if (navigation)
            navigation.openModal("updates-actions", actionModel[selectedAction].id)
        return true
    }

    function closeActions() {
        if (!actionsOpen)
            return false
        actionsOpen = false
        if (navigation)
            navigation.closeModal()
        return true
    }

    function activateTile() {
        var tile = navTiles[selectedTile]
        if (!tile)
            return false
        if (tile.id === "updates") {
            focusZone = stableUpdates.count > 0 ? 0 : (filterItems.length > 0 ? 1 : 2)
            return true
        }
        sectionRequested(tile.id)
        return true
    }

    function profileFor(update) {
        var profile = String(value(update, [
            "recommendedProfile", "recommended_profile", "profile", "defaultProfile"
        ], "Auto"))
        return ["Fast", "Balanced", "Maximum", "Auto"].indexOf(profile) >= 0
                ? profile : "Auto"
    }

    function planValue(plan, keys, fallback) {
        return value(plan || {}, keys, fallback)
    }

    function planIsValid(plan) {
        if (!plan || typeof plan !== "object")
            return false
        var planId = String(planValue(plan, ["planId", "plan_id"], ""))
        if (planId.length === 0)
            return false
        var hasValidityFlag = plan.valid !== undefined
                || plan.canStart !== undefined || plan.can_start !== undefined
        if (hasValidityFlag
                && plan.valid !== true
                && plan.canStart !== true
                && plan.can_start !== true)
            return false
        var blockers = planValue(plan, ["blockers"], [])
        return !blockers || blockers.length === 0
    }

    function estimatedPlanSavings() {
        var low = planValue(pendingPlan, [
            "estimatedSavingsLowBytes", "estimated_savings_low_bytes"
        ], null)
        var high = planValue(pendingPlan, [
            "estimatedSavingsHighBytes", "estimated_savings_high_bytes"
        ], low)
        return low === null || high === null
                ? qsTr("Not estimated")
                : qsTr("%1-%2").arg(formatBytes(low)).arg(formatBytes(high))
    }

    function showMessage(message, tone) {
        toastRequested(String(message), String(tone))
    }

    function prepareSelectedCompression() {
        var id = gameId(selectedUpdate)
        if (id.length === 0 || !controller || !controller.prepareCompression)
            return false
        var plan = controller.prepareCompression(id, profileFor(selectedUpdate), true)
        if (!planIsValid(plan)) {
            var message = String(planValue(plan, ["error", "message"],
                                         qsTr("Compression cannot be started.")))
            var blockers = planValue(plan, ["blockers"], [])
            if (blockers && blockers.length > 0)
                message = String(blockers[0])
            showMessage(message, "error")
            return false
        }
        closeActions()
        pendingPlan = plan
        confirmSelection = 0
        confirmationOverlay.visible = true
        if (navigation)
            navigation.openModal("updates-compression", "cancel")
        return true
    }

    function closeConfirmation() {
        if (!confirmationOverlay.visible)
            return false
        confirmationOverlay.visible = false
        pendingPlan = ({})
        confirmSelection = 0
        if (navigation)
            navigation.closeModal()
        return true
    }

    function confirmPlan() {
        var planId = String(planValue(pendingPlan, ["planId", "plan_id"], ""))
        var started = planId.length > 0 && controller && controller.startCompression
                ? Boolean(controller.startCompression(planId)) : false
        closeConfirmation()
        return started
    }

    function activateSelectedAction() {
        var actions = page.actionModel || []
        if (!actions[selectedAction] || !actions[selectedAction].enabled)
            return "error"
        var id = gameId(selectedUpdate)
        if (selectedAction === 0 && id.length > 0 && controller && controller.analyzeChanges) {
            closeActions()
            return controller.analyzeChanges(id) ? "confirm" : "error"
        }
        if (selectedAction === 1)
            return prepareSelectedCompression() ? "open" : "error"
        if (selectedAction === 2 && id.length > 0 && controller && controller.ignoreUpdate) {
            closeActions()
            return controller.ignoreUpdate(id) ? "confirm" : "error"
        }
        if (selectedAction === 3 && id.length > 0 && controller && controller.openGame) {
            closeActions()
            return controller.openGame(id) ? "confirm" : "error"
        }
        if (selectedAction === 4) {
            closeActions()
            return "back"
        }
        return "error"
    }

    function handleConfirmationAction(action) {
        if (action === "Back") {
            if (closeConfirmation())
                playSemanticSound("back")
        } else if (action === "NavigateLeft" || action === "NavigateUp"
                   || action === "NavigateRight" || action === "NavigateDown") {
            var previousChoice = confirmSelection
            confirmSelection = (action === "NavigateLeft" || action === "NavigateUp") ? 0 : 1
            if (confirmSelection !== previousChoice)
                playSemanticSound("navigate")
        } else if (action === "Confirm") {
            if (confirmSelection === 1)
                playSemanticSound(confirmPlan() ? "confirm" : "error")
            else {
                closeConfirmation()
                playSemanticSound("confirm")
            }
        }
    }

    function handleActionsAction(action) {
        if (action === "Back" || action === "MoreActions") {
            if (closeActions())
                playSemanticSound("back")
        } else if (action === "NavigateUp" || action === "NavigateLeft") {
            if (moveAction(-1)) playSemanticSound("navigate")
        } else if (action === "NavigateDown" || action === "NavigateRight") {
            if (moveAction(1)) playSemanticSound("navigate")
        } else if (action === "Confirm") {
            playSemanticSound(activateSelectedAction())
        }
    }

    function handleAction(action) {
        if (action === "Accept") action = "Confirm"
        else if (action === "ContextMenu" || action === "Search") action = "MoreActions"
        else if (action === "PreviousSection" || action === "PageLeft") action = "PageUp"
        else if (action === "NextSection" || action === "PageRight") action = "PageDown"
        if (confirmationOpen) {
            handleConfirmationAction(action)
            return
        }
        if (actionsOpen) {
            handleActionsAction(action)
            return
        }
        if (action === "Back") {
            backRequested()
            playSemanticSound("back")
            return
        }
        var hasRows = stableUpdates.count > 0
        var hasFilters = filterItems.length > 0
        var previousZone = focusZone
        if (focusZone === 1) {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                var next = Math.max(0, Math.min(filterItems.length - 1,
                                                filterIndex + (action === "NavigateLeft" ? -1 : 1)))
                if (next !== filterIndex) {
                    filterIndex = next
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateDown") {
                focusZone = hasRows ? 0 : 2
            } else if (action === "Confirm") {
                var item = filterItems[filterIndex]
                playSemanticSound(item && (setSectionFilter(item.id) || true) ? "confirm" : "error")
            }
        } else if (focusZone === 0) {
            if (action === "NavigateUp") {
                if (updatesList.currentIndex <= 0) {
                    if (hasFilters) focusZone = 1
                } else if (selectUpdate(updatesList.currentIndex - 1)) {
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateDown") {
                if (updatesList.currentIndex >= stableUpdates.count - 1)
                    focusZone = 2
                else if (selectUpdate(updatesList.currentIndex + 1))
                    playSemanticSound("navigate")
            } else if (action === "PageUp" && hasRows) {
                if (selectUpdate(Math.max(0, updatesList.currentIndex - 5)))
                    playSemanticSound("navigate")
            } else if (action === "PageDown" && hasRows) {
                if (selectUpdate(Math.min(stableUpdates.count - 1, updatesList.currentIndex + 5)))
                    playSemanticSound("navigate")
            } else if (action === "Confirm" || action === "MoreActions") {
                playSemanticSound(openActions() ? "open" : "error")
            }
        } else {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                var tile = Math.max(0, Math.min(navTiles.length - 1,
                                                selectedTile + (action === "NavigateLeft" ? -1 : 1)))
                if (tile !== selectedTile) {
                    selectedTile = tile
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateUp") {
                // Back to the last selected row (or the filters when empty).
                focusZone = hasRows ? 0 : (hasFilters ? 1 : 2)
            } else if (action === "Confirm") {
                playSemanticSound(activateTile() ? "confirm" : "error")
            }
        }
        if (focusZone !== previousZone) {
            playSemanticSound("navigate")
            restoreActiveFocus()
        }
    }

    onUpdatesDataChanged: synchronizeUpdates()
    onFilterItemsChanged: {
        if (sectionFilter !== "all" && (sectionCounts[sectionFilter] || 0) === 0)
            setSectionFilter("all")
        filterIndex = Math.max(0, Math.min(filterIndex, filterItems.length - 1))
    }
    onSelectedUpdateChanged: Qt.callLater(page.ensureSelectedAction)
    focus: visible
    Component.onCompleted: {
        synchronizeUpdates()
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) {
        selectedTile = 2
        focusZone = stableUpdates.count > 0 ? 0 : (filterItems.length > 0 ? 1 : 2)
        restoreActiveFocus()
    }

    ListModel {
        id: stableUpdates
        dynamicRoles: true
    }

    CouchHeroBackdrop {
        anchors.fill: parent
        sources: []
    }

    // ---- Header and real summary ------------------------------------------------
    RowLayout {
        id: header
        objectName: "couchUpdatesHeader"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: page.contentMargin
        anchors.rightMargin: page.contentMargin
        anchors.topMargin: 92 * page.fitScale
        height: 96 * page.fitScale
        spacing: App.Theme.couchSpaceL * page.fitScale

        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 4 * page.fitScale
            Label {
                objectName: "couchUpdatesTitle"
                text: qsTr("Updates")
                color: App.Theme.text
                font.pixelSize: App.Theme.couchTitleSize * page.fitScale
                font.weight: Font.Bold
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Changed game files are re-analyzed before compression. Games are not downloaded here.")
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.fitScale
                elide: Text.ElideRight
            }
        }
        Repeater {
            model: page.summaryCards
            delegate: Rectangle {
                id: summaryCard
                objectName: "couchUpdatesSummary-" + modelData.id
                required property var modelData
                Layout.preferredWidth: 236 * page.fitScale
                Layout.preferredHeight: 84 * page.fitScale
                radius: App.Theme.couchCardRadius * page.fitScale
                color: Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b,
                               App.Theme.dark ? 0.86 : 0.92)
                border.width: 1
                border.color: App.Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: App.Theme.couchSpaceM * page.fitScale
                    anchors.rightMargin: App.Theme.couchSpaceM * page.fitScale
                    spacing: App.Theme.couchSpaceM * page.fitScale
                    Rectangle {
                        Layout.preferredWidth: 52 * page.fitScale
                        Layout.preferredHeight: 52 * page.fitScale
                        radius: App.Theme.couchRadiusSmall * page.fitScale
                        color: App.Theme.surfaceRaised
                        CouchIcon {
                            anchors.centerIn: parent
                            size: App.Theme.couchIconMedium
                            couchScale: page.fitScale
                            source: summaryCard.modelData.icon
                            lightSource: summaryCard.modelData.iconOnLight
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label {
                            text: summaryCard.modelData.value
                            color: App.Theme.text
                            font.pixelSize: 26 * page.fitScale
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: summaryCard.modelData.label
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.couchCaptionSize * page.fitScale
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }

    // ---- Filter chips (focus zone 1) ---------------------------------------------
    Row {
        id: filterRow
        objectName: "couchUpdatesFilters"
        visible: page.filterItems.length > 0
        anchors.left: parent.left
        anchors.leftMargin: page.contentMargin
        anchors.top: header.bottom
        anchors.topMargin: App.Theme.couchSpaceM * page.fitScale
        height: visible ? 54 * page.fitScale : 0
        spacing: App.Theme.couchSpaceM * page.fitScale
        Repeater {
            id: filterRepeater
            model: page.filterItems
            delegate: CouchButton {
                id: chip
                objectName: "couchUpdatesFilterChip"
                required property var modelData
                required property int index
                property string filterId: modelData.id
                readonly property bool active: page.sectionFilter === modelData.id
                couchScale: page.fitScale
                implicitHeight: 54 * page.fitScale
                implicitWidth: contentItem.implicitWidth + 2 * App.Theme.couchSpaceL * page.fitScale
                focusPolicy: Qt.NoFocus
                text: modelData.label
                iconSource: modelData.icon
                iconLightSource: modelData.iconOnLight
                iconSize: App.Theme.couchIconSmall
                font.pixelSize: 18 * page.fitScale
                font.weight: active ? Font.Bold : Font.DemiBold
                focus: page.focusZone === 1 && !page.actionsOpen && !page.confirmationOpen
                       && page.filterIndex === index
                Accessible.checkable: true
                Accessible.checked: active
                onClicked: { page.focusZone = 1; page.filterIndex = index; page.setSectionFilter(modelData.id) }
                background: Rectangle {
                    radius: height / 2
                    color: chip.down ? App.Theme.surfacePressed
                          : chip.focusVisible ? App.Theme.couchFocusSurface
                          : chip.active ? App.Theme.accentSoft
                          : Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                                    App.Theme.surfaceRaised.b, 0.88)
                    border.width: chip.active ? 2 * page.fitScale : 1
                    border.color: chip.active ? App.Theme.accent : App.Theme.borderStrong
                    scale: chip.down ? App.Theme.couchPressScale : chip.focusVisible ? 1.04 : 1.0
                    Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                    CouchFocusFrame {
                        active: chip.focusVisible
                        radius: parent.radius
                        couchScale: page.fitScale
                    }
                }
            }
        }
    }

    // ---- Update list (focus zone 0) ------------------------------------------------
    ListView {
        id: updatesList
        objectName: "couchUpdatesList"
        readonly property real pad: 14 * page.fitScale
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: filterRow.bottom
        anchors.bottom: footerRow.top
        anchors.leftMargin: page.contentMargin - pad
        anchors.rightMargin: page.contentMargin - pad
        anchors.topMargin: App.Theme.couchSpaceS * page.fitScale
        visible: stableUpdates.count > 0
        clip: true
        model: stableUpdates
        reuseItems: true
        cacheBuffer: Math.max(height, 600)
        spacing: App.Theme.couchSpaceM * page.fitScale
        topMargin: pad
        bottomMargin: pad
        boundsBehavior: Flickable.StopAtBounds
        keyNavigationEnabled: false
        highlightMoveDuration: App.Theme.couchMotionDuration(180)
        highlightMoveVelocity: -1
        preferredHighlightBegin: pad
        preferredHighlightEnd: height - pad
        highlightRangeMode: ListView.ApplyRange
        currentIndex: count > 0 ? 0 : -1

        delegate: Item {
            id: updateRow
            objectName: "couchUpdateCard"
            required property var model
            required property int index
            property var updateData: model.updateData || ({})
            property string updateState: page.stateOf(updateData)
            readonly property bool selected: updatesList.currentIndex === index
                                             && page.focusZone === 0 && !page.actionsOpen
                                             && !page.confirmationOpen
            readonly property string renderedCoverGameId: updateCover.gameId
            readonly property string renderedCoverArtwork: String(updateCover.artworkSource)
            width: updatesList.width - 2 * updatesList.pad
            x: updatesList.pad
            height: 124 * page.fitScale
            z: selected ? 2 : 1

            Rectangle {
                id: rowBackground
                anchors.fill: parent
                radius: App.Theme.couchCardRadius * page.fitScale
                color: updateRow.selected ? App.Theme.couchFocusSurface
                       : Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b,
                                 App.Theme.dark ? 0.86 : 0.94)
                border.width: 1
                border.color: App.Theme.border
                scale: updateRow.selected ? 1.01 : 1.0
                Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
                Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                CouchFocusFrame {
                    active: updateRow.selected
                    radius: parent.radius
                    couchScale: page.fitScale
                }
            }
            MouseArea {
                anchors.fill: parent
                onClicked: {
                    var wasSelected = updatesList.currentIndex === updateRow.index && page.focusZone === 0
                    page.focusZone = 0
                    page.selectUpdate(updateRow.index)
                    if (wasSelected)
                        page.openActions()
                    page.restoreActiveFocus()
                }
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12 * page.fitScale
                anchors.rightMargin: App.Theme.couchSpaceL * page.fitScale
                spacing: App.Theme.couchSpaceL * page.fitScale

                GameCover {
                    id: updateCover
                    objectName: "couchUpdateCover"
                    Layout.preferredWidth: 68 * page.fitScale
                    Layout.preferredHeight: 100 * page.fitScale
                    gameId: page.resolvedGameId(updateRow.updateData)
                    title: page.gameName(updateRow.updateData)
                    launcher: page.resolvedLauncher(updateRow.updateData)
                    artworkSource: page.resolvedArtwork(updateRow.updateData)
                    artworkFillMode: Image.PreserveAspectCrop
                    cornerRadius: 10 * page.fitScale
                }

                ColumnLayout {
                    objectName: "couchUpdateInformation"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.minimumWidth: 0
                    spacing: 6 * page.fitScale
                    Label {
                        Layout.fillWidth: true
                        text: page.gameName(updateRow.updateData)
                        color: updateRow.selected ? App.Theme.couchFocusText : App.Theme.text
                        font.pixelSize: 24 * page.fitScale
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                    }
                    Row {
                        visible: identityLabel.text.length > 0
                        spacing: App.Theme.couchSpaceS * page.fitScale
                        CouchIcon {
                            objectName: "couchUpdateLauncherIcon"
                            readonly property url logo: App.UiIcons.launcherLogo(page.resolvedLauncher(updateRow.updateData))
                            anchors.verticalCenter: parent.verticalCenter
                            size: App.Theme.couchIconSmall
                            couchScale: page.fitScale
                            source: String(logo).length ? logo : App.UiIcons.couchGlyphSource
                            lightSource: String(logo).length ? logo : App.UiIcons.couchGlyphSourceOnLight
                        }
                        Label {
                            id: identityLabel
                            anchors.verticalCenter: parent.verticalCenter
                            text: page.identityText(updateRow.updateData)
                            color: App.Theme.textSecondary
                            font.pixelSize: 17 * page.fitScale
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: page.friendlyMessage(page.value(updateRow.updateData, ["error"], ""))
                        color: App.Theme.couchToneText("danger")
                        font.pixelSize: 15 * page.fitScale
                        elide: Text.ElideRight
                    }
                }

                ColumnLayout {
                    objectName: "couchUpdateMeasurements"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 2
                    Layout.minimumWidth: 0
                    spacing: 8 * page.fitScale
                    Repeater {
                        model: [
                            { "text": page.changedText(updateRow.updateData),
                              "icon": App.UiIcons.couchGlyphRefresh, "iconOnLight": App.UiIcons.couchGlyphRefreshOnLight },
                            { "text": page.sizeText(updateRow.updateData),
                              "icon": App.UiIcons.couchGlyphStorage, "iconOnLight": App.UiIcons.couchGlyphStorageOnLight }
                        ]
                        delegate: RowLayout {
                            id: measurement
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: App.Theme.couchSpaceS * page.fitScale
                            CouchIcon {
                                size: App.Theme.couchIconSmall
                                couchScale: page.fitScale
                                source: measurement.modelData.icon
                                lightSource: measurement.modelData.iconOnLight
                            }
                            Label {
                                objectName: "couchUpdateMeasurement"
                                Layout.fillWidth: true
                                text: measurement.modelData.text
                                color: App.Theme.textSecondary
                                font.pixelSize: 17 * page.fitScale
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                CouchStatePill {
                    objectName: "couchUpdateState"
                    Layout.alignment: Qt.AlignVCenter
                    couchScale: page.fitScale
                    tone: page.pillTone(updateRow.updateState)
                    text: page.translatedState(updateRow.updateState)
                    maximumTextWidth: 300 * page.fitScale
                }
                CouchIcon {
                    Layout.alignment: Qt.AlignVCenter
                    size: App.Theme.couchIconSmall
                    couchScale: page.fitScale
                    source: App.UiIcons.couchGlyphNext
                    lightSource: App.UiIcons.couchGlyphNextOnLight
                }
            }
        }
    }

    // ---- Empty state ---------------------------------------------------------------
    Rectangle {
        objectName: "couchUpdatesEmptyPanel"
        visible: stableUpdates.count === 0
        anchors.centerIn: updatesList
        width: Math.min(updatesList.width - 40 * page.fitScale, 900 * page.fitScale)
        height: Math.min(updatesList.height, 300 * page.fitScale)
        radius: App.Theme.couchPanelRadius * page.fitScale
        color: App.Theme.dark ? "#D5151D29" : "#EDFFFFFF"
        border.width: 1
        border.color: App.Theme.borderStrong
        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width - 80 * page.fitScale
            spacing: App.Theme.couchSpaceM * page.fitScale
            CouchIcon {
                objectName: "couchUpdatesEmptyIcon"
                Layout.alignment: Qt.AlignHCenter
                size: App.Theme.couchIconLarge
                couchScale: page.fitScale
                source: App.UiIcons.couchGlyphStatusReady
                lightSource: App.UiIcons.couchGlyphStatusReadyOnLight
            }
            Label {
                objectName: "couchUpdatesEmptyState"
                Layout.fillWidth: true
                text: page.sectionFilter === "all" ? qsTr("No changes detected")
                                                   : qsTr("No entries in this filter")
                color: App.Theme.text
                font.pixelSize: 30 * page.fitScale
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Changed game files and pending re-analysis will appear here.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchBodySize * page.fitScale
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    Item {
        id: footerRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: bottomNav.top
        anchors.bottomMargin: App.Theme.couchSpaceS * page.fitScale
        height: 52 * page.fitScale
        Label {
            visible: stableUpdates.count > 0
            anchors.left: parent.left
            anchors.leftMargin: page.contentMargin
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("%1 / %2").arg(updatesList.currentIndex + 1).arg(stableUpdates.count)
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.couchLabelSize * page.fitScale
            font.weight: Font.DemiBold
        }
    }

    // ---- Global section navigation (focus zone 2) ----------------------------------
    CouchBottomNav {
        id: bottomNav
        objectName: "couchUpdatesNavigation"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin
        anchors.bottomMargin: page.bottomNavMargin
        height: implicitHeight
        couchScale: page.fitScale
        model: page.navTiles
        activeIndex: 2
        currentIndex: page.selectedTile
        navFocused: page.focusZone === 2 && !page.actionsOpen && !page.confirmationOpen
        onActivated: function(index) {
            page.focusZone = 2
            page.selectedTile = index
            page.playSemanticSound(page.activateTile() ? "confirm" : "error")
        }
    }

    // ---- Existing per-row actions ------------------------------------------------------
    CouchOverlayFrame {
        id: actionsOverlay
        objectName: "couchUpdatesActionsOverlay"
        anchors.fill: parent
        visible: page.actionsOpen
        z: 90
        couchScale: page.couchScale
        maximumWidth: 760 * page.couchScale
        preferredHeight: 600 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 14 * page.couchScale
            Label {
                Layout.fillWidth: true
                text: page.gameName(page.selectedUpdate)
                color: App.Theme.text
                font.pixelSize: 32 * page.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                text: page.translatedState(page.stateOf(page.selectedUpdate))
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchHelperSize * page.couchScale
            }
            ColumnLayout {
                id: actionRow
                objectName: "couchUpdatesActions"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10 * page.couchScale
                Repeater {
                    id: actionRepeater
                    model: page.actionModel
                    delegate: CouchTile {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 76 * page.couchScale
                        couchScale: page.couchScale
                        compact: true
                        visible: modelData.visible
                        iconSource: modelData.icon
                        iconLightSource: modelData.iconOnLight
                        text: modelData.title
                        enabled: modelData.enabled
                        showChevron: modelData.id !== "back"
                        focus: page.actionsOpen && page.selectedAction === index
                        onClicked: {
                            page.selectedAction = index
                            page.playSemanticSound(page.activateSelectedAction())
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
    }

    Rectangle {
        id: confirmationOverlay
        objectName: "couchCompressionConfirmation"
        anchors.fill: parent
        visible: false
        z: 100
        color: App.Theme.dark ? "#F20B1018" : "#F7F3F6FA"
        focus: visible

        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 160 * page.couchScale, 1180 * page.couchScale)
            spacing: 22 * page.couchScale

            Label {
                Layout.fillWidth: true
                text: qsTr("Confirm compression")
                color: App.Theme.text
                font.pixelSize: 44 * page.couchScale
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }

            Label {
                Layout.fillWidth: true
                text: String(page.planValue(page.pendingPlan, [
                    "gameName", "game_name"
                ], qsTr("Unknown game")))
                color: App.Theme.text
                font.pixelSize: 28 * page.couchScale
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }

            GridLayout {
                Layout.alignment: Qt.AlignHCenter
                columns: 3
                rowSpacing: 10 * page.couchScale
                columnSpacing: 14 * page.couchScale

                Repeater {
                    model: [
                        {
                            "label": qsTr("Profile"),
                            "value": String(page.planValue(page.pendingPlan, ["profile"], "Auto"))
                        },
                        {
                            "label": qsTr("Estimated savings"),
                            "value": page.estimatedPlanSavings()
                        },
                        {
                            "label": qsTr("Files"),
                            "value": String(page.planValue(page.pendingPlan, [
                                "plannedFileCount", "planned_file_count"
                            ], 0))
                        }
                    ]

                    delegate: Rectangle {
                        id: planMetric
                        required property var modelData
                        Layout.preferredWidth: 280 * page.couchScale
                        Layout.preferredHeight: 92 * page.couchScale
                        radius: 18 * page.couchScale
                        color: App.Theme.surface
                        border.width: 1
                        border.color: App.Theme.border

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14 * page.couchScale
                            spacing: 3 * page.couchScale
                            Label {
                                Layout.fillWidth: true
                                text: planMetric.modelData.value
                                color: App.Theme.text
                                font.pixelSize: 18 * page.couchScale
                                font.weight: Font.Bold
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text: planMetric.modelData.label
                                color: App.Theme.textMuted
                                font.pixelSize: 12 * page.couchScale
                                horizontalAlignment: Text.AlignHCenter
                            }
                        }
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                text: {
                    var warnings = page.planValue(page.pendingPlan,
                                                  ["warnings"], [])
                    if (warnings && warnings.length > 0)
                        return qsTr("Warning: %1\nOnly continue after reviewing this risk.")
                            .arg(warnings.map(function(item) {
                                return App.I18n.message(String(item))
                            }).join("; "))
                    return qsTr("Only files in the verified plan will be processed. The game must remain closed while compression is running.")
                }
                color: App.Theme.warning
                font.pixelSize: 16 * page.couchScale
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }

            Label {
                Layout.fillWidth: true
                visible: String(page.planValue(page.pendingPlan, [
                    "fullPath", "full_path", "path"
                ], "")).length > 0
                text: String(page.planValue(page.pendingPlan, [
                    "fullPath", "full_path", "path"
                ], ""))
                color: App.Theme.textMuted
                font.pixelSize: 12 * page.couchScale
                font.family: "monospace"
                elide: Text.ElideMiddle
                horizontalAlignment: Text.AlignHCenter
            }

            RowLayout {
                objectName: "couchConfirmationActions"
                Layout.alignment: Qt.AlignHCenter
                spacing: 18 * page.couchScale

                CouchTile {
                    id: updateCancelButton
                    couchScale: page.couchScale
                    implicitWidth: 300 * page.couchScale
                    iconSource: App.UiIcons.couchGlyphPrevious
                    iconLightSource: App.UiIcons.couchGlyphPreviousOnLight
                    text: qsTr("Cancel")
                    subtitle: qsTr("Return without starting")
                    focus: page.confirmationOpen && page.confirmSelection === 0
                    onClicked: page.closeConfirmation()
                }
                CouchTile {
                    id: updateConfirmButton
                    couchScale: page.couchScale
                    implicitWidth: 300 * page.couchScale
                    iconSource: App.UiIcons.couchGlyphCompress
                    iconLightSource: App.UiIcons.couchGlyphCompressOnLight
                    text: qsTr("Start compression")
                    subtitle: qsTr("Use the verified plan")
                    focus: page.confirmationOpen && page.confirmSelection === 1
                    onClicked: page.confirmPlan()
                }
            }
        }
    }
}
