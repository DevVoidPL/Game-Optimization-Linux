pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

// Couch Mode Tasks: real work from controller.tasks (presenters.task_to_qml),
// grouped as active, queued and the latest results. Same visual language as
// Updates: header, real summary cards, large rows, bottom navigation.
//
// The model has no filters, so there is no chip row. Focus zones: 0 list,
// 2 bottom navigation. The only existing row action is cancelling a
// cancellable task, behind the existing "Keep task" safe-default dialog.
FocusScope {
    id: page
    objectName: "couchTasks"

    property var controller
    property var navigation
    property real couchScale: 1.0
    readonly property real fitScale: height > 0 ? Math.min(couchScale, height / 1080) : couchScale
    readonly property real edgeMargin: 48 * fitScale
    readonly property real bottomNavMargin: 40 * fitScale
    readonly property real hintsBottomMargin: bottomNavMargin + bottomNav.height + App.Theme.couchSpaceS * fitScale
    readonly property real contentMargin: 96 * fitScale

    property var tasks: controller && controller.tasks ? controller.tasks : []
    // Routine automatic work (library scan, size calculation) is recreated on
    // every start; once it finished normally it is noise on a TV. It stays
    // visible while running or when it failed. Backend history is untouched.
    readonly property var routineOperations: ["Library scan", "Size calculation"]
    readonly property var shownTasks: (tasks || []).filter(function(task) { return !page.routineHidden(task) })
    property var visibleTasks: buildVisibleTasks(shownTasks)
    property string retainedTaskId: ""
    property bool cancellationOpen: false
    property int cancellationChoice: 0
    // 0 list, 2 bottom navigation (no filter zone: the model has none).
    property int focusZone: 0
    property int selectedTile: 1
    readonly property int activeCount: countTasks("active")
    readonly property int queuedCount: countTasks("queued")
    readonly property int recentCount: countTasks("recent")
    readonly property var summarySections: {
        var sections = []
        if (activeCount > 0)
            sections.push({ "section": "active", "title": qsTr("Active"), "value": activeCount,
                            "icon": App.UiIcons.couchGlyphStart, "iconOnLight": App.UiIcons.couchGlyphStartOnLight })
        if (queuedCount > 0)
            sections.push({ "section": "queued", "title": qsTr("Queued"), "value": queuedCount,
                            "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight })
        if (recentCount > 0)
            sections.push({ "section": "recent", "title": qsTr("Recently completed"), "value": recentCount,
                            "icon": App.UiIcons.couchGlyphStatusReady, "iconOnLight": App.UiIcons.couchGlyphStatusReadyOnLight })
        return sections
    }
    // Existing row action, stable id.
    readonly property var taskActions: [{ "id": "cancel-task", "title": qsTr("Cancel task") }]
    // History actions: they remove task *records* only (removeFinishedTask /
    // clearFinishedTasks), never game files, backups or compression results.
    property bool actionMenuOpen: false
    property int actionMenuIndex: 0
    property string deleteConfirm: ""      // "" | "one" | "all"
    property int deleteChoice: 0           // 0 = Cancel (safe default)
    readonly property bool historyModalOpen: actionMenuOpen || deleteConfirm.length > 0
    readonly property bool overlayOpen: cancellationOpen || historyModalOpen
    readonly property int removableCount: recentCount
    readonly property var historyActions: {
        var task = selectedTask()
        var actions = []
        if (task && terminal(task) && gameKnown(task))
            actions.push({ "id": "details", "title": qsTr("Game details"),
                           "icon": App.UiIcons.couchGlyphDetails, "iconOnLight": App.UiIcons.couchGlyphDetailsOnLight })
        if (task && terminal(task))
            actions.push({ "id": "delete", "title": qsTr("Delete"),
                           "icon": App.UiIcons.couchGlyphRemove, "iconOnLight": App.UiIcons.couchGlyphRemoveOnLight })
        return actions
    }
    readonly property bool contextAvailable: focusZone === 0 && !overlayOpen && removableCount > 0
    readonly property var navTiles: [
        { "id": "library", "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight, "title": qsTr("Library"), "subtitle": qsTr("Your games") },
        { "id": "tasks", "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight, "title": qsTr("Tasks"), "subtitle": qsTr("Active and recent tasks") },
        { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "title": qsTr("Updates"), "subtitle": qsTr("File changes and re-analysis") },
        { "id": "settings", "icon": App.UiIcons.couchGlyphSettings, "iconOnLight": App.UiIcons.couchGlyphSettingsOnLight, "title": qsTr("Settings"), "subtitle": qsTr("App configuration") }
    ]

    signal backRequested()
    signal sectionRequested(string section)

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (page.cancellationOpen)
                (page.cancellationChoice === 0
                 ? keepTaskButton : cancelTaskButton).forceActiveFocus()
            else if (page.actionMenuOpen) {
                var entry0 = historyActionRepeater.itemAt(page.actionMenuIndex)
                if (entry0) entry0.forceActiveFocus()
            } else if (page.deleteConfirm.length)
                (page.deleteChoice === 0 ? deleteCancelButton : deleteRunButton).forceActiveFocus()
            else if (page.focusZone === 2) {
                var entry = bottomNav.itemAt(page.selectedTile)
                if (entry) entry.forceActiveFocus()
            } else
                taskList.forceActiveFocus()
        })
    }

    function taskId(task, index) {
        return String(task.id || task.taskId || task.task_id || ("task-" + index))
    }

    function taskStatus(task) {
        return String(task && (task.status || task.state) || "").toLowerCase()
    }

    function terminal(task) {
        return ["completed", "failed", "cancelled", "interrupted"]
                .indexOf(taskStatus(task)) >= 0
    }

    function queued(task) {
        return ["queued", "pending", "waiting"].indexOf(taskStatus(task)) >= 0
    }

    function sectionOf(task) {
        return terminal(task) ? "recent" : queued(task) ? "queued" : "active"
    }

    function routineHidden(task) {
        var operation = String(task && (task.operation || task.type) || "")
        var status = taskStatus(task)
        return routineOperations.indexOf(operation) >= 0
                && (status === "completed" || status === "cancelled")
    }

    // Counts over every shown task (the list shows only the latest four results).
    function countTasks(section) {
        var count = 0
        var source = shownTasks || []
        for (var index = 0; index < source.length; ++index) {
            if (sectionOf(source[index] || {}) === section)
                count++
        }
        return count
    }

    function buildVisibleTasks(source) {
        var active = []
        var waiting = []
        var recent = []
        var sourceTasks = source || []
        for (var index = 0; index < sourceTasks.length; ++index) {
            var task = sourceTasks[index] || {}
            var row = {
                "taskData": task,
                "rowId": taskId(task, index),
                "sourceIndex": index,
                "section": sectionOf(task),
                "sectionStart": false
            }
            if (row.section === "active")
                active.push(row)
            else if (row.section === "queued")
                waiting.push(row)
            else
                recent.push(row)
        }

        // The main TV view intentionally keeps history short.  Full task
        // history remains owned by the shared controller/desktop page.
        recent = recent.slice(0, 4)
        var result = active.concat(waiting).concat(recent)
        var previousSection = ""
        for (var resultIndex = 0; resultIndex < result.length; ++resultIndex) {
            result[resultIndex].sectionStart = result[resultIndex].section !== previousSection
            previousSection = result[resultIndex].section
        }
        return result
    }

    function sectionTitle(section) {
        if (section === "active")
            return qsTr("Active")
        if (section === "queued")
            return qsTr("Queued")
        return qsTr("Recently completed")
    }

    function sectionSubtitle(section) {
        if (section === "active")
            return qsTr("Work in progress")
        if (section === "queued")
            return qsTr("Waiting to start")
        return qsTr("Latest four results")
    }

    function translatedStatus(status) {
        switch (String(status).toLowerCase()) {
        case "running": return qsTr("Running")
        case "analyzing": return qsTr("Analyzing")
        case "compressing": return qsTr("Compressing")
        case "queued": return qsTr("Queued")
        case "pending": return qsTr("Pending")
        case "paused": return qsTr("Paused")
        case "completed": return qsTr("Completed")
        case "failed": return qsTr("Failed")
        case "cancelled": return qsTr("Cancelled")
        case "interrupted": return qsTr("Interrupted")
        default: return String(status || qsTr("Unknown"))
        }
    }

    // CouchStatePill tone (dot + text).
    function statusTone(status) {
        var normalized = String(status).toLowerCase()
        if (normalized === "completed")
            return "success"
        if (normalized === "failed" || normalized === "interrupted")
            return "danger"
        if (normalized === "cancelled")
            return "unavailable"
        if (["queued", "pending", "waiting", "paused"].indexOf(normalized) >= 0)
            return "neutral"
        return "info"
    }

    // Real task types (models.enums.TaskType); unknown types keep their text.
    function typeLabel(task) {
        var type = String(task && (task.operation || task.type || task.taskType) || "")
        switch (type) {
        case "Analysis": return qsTr("Analysis")
        case "Verification": return qsTr("Verification")
        case "Compression": return qsTr("Compression")
        case "Optimization": return qsTr("Optimization")
        case "Texture enhancement": return qsTr("Texture enhancement")
        case "Backup": return qsTr("Backup")
        case "Restore": return qsTr("Restore")
        case "Library scan": return qsTr("Library scan")
        case "Size calculation": return qsTr("Size calculation")
        default: return type.length ? type : qsTr("Task")
        }
    }
    // Icon only where the meaning is unambiguous.
    function typeIcon(task, onLight) {
        var type = String(task && (task.operation || task.type || task.taskType) || "")
        var name = type === "Analysis" ? "Analyze" : type === "Verification" ? "Verify"
                 : type === "Compression" ? "Compress" : type === "Optimization" ? "Optimization"
                 : type === "Library scan" ? "Library" : type === "Size calculation" ? "Storage" : ""
        return name.length ? App.UiIcons["couchGlyph" + name + (onLight ? "OnLight" : "")] : ""
    }

    // Progress is shown only for running work that reports determinate
    // progress; otherwise an honest "No data" instead of 0%.
    function hasProgress(task) {
        return task && !terminal(task) && !queued(task)
                && task.progressDeterminate === true
                && task.progress !== undefined && task.progress !== null
                && isFinite(Number(task.progress))
    }
    function progressValue(task) {
        var raw = Number(task.progress)
        if (raw > 1)
            raw /= 100
        return Math.max(0, Math.min(1, raw))
    }
    function progressText(task) {
        if (hasProgress(task))
            return qsTr("%1%").arg(Math.round(progressValue(task) * 100))
        if (!terminal(task) && !queued(task))
            return qsTr("Progress: no data")
        return ""
    }

    function friendlyMessage(raw) {
        var message = String(raw || "")
        if (message.indexOf("Traceback (most recent call last)") >= 0
                || message.indexOf("File \"") >= 0)
            return qsTr("The task failed. See the application log for technical details.")
        return message
    }

    function messageText(task) {
        if (taskStatus(task) === "failed" || taskStatus(task) === "interrupted")
            return friendlyMessage(task.error || task.message || "")
        return friendlyMessage(task.currentFile || task.message || "")
    }

    function timeLabel(task) {
        var elapsed = Number(task && (task.elapsedSeconds
                                     || task.elapsed_seconds) || 0)
        if (isFinite(elapsed) && elapsed > 0) {
            if (elapsed < 60)
                return qsTr("%1 s").arg(Math.round(elapsed))
            return qsTr("%1 min").arg(Math.round(elapsed / 60))
        }
        var raw = String(task && (task.updatedAt || task.updated_at
                                  || task.createdAt || task.created_at) || "")
        if (!raw.length)
            return qsTr("Time unavailable")
        var parsed = new Date(raw)
        return isNaN(parsed.getTime()) ? qsTr("Time unavailable")
                                        : Qt.formatDateTime(parsed, "dd.MM.yyyy, HH:mm")
    }

    function artworkValue(source) {
        var data = source || {}
        return String(data.effectiveArtworkUrl
                      || data.effective_artwork_url
                      || data.artworkUrl
                      || data.artwork_url
                      || data.artwork
                      || data.portraitArtwork
                      || data.portrait_artwork
                      || data.cover || "")
    }

    function artworkForTask(task) {
        var taskArtwork = artworkValue(task)
        if (taskArtwork.length > 0)
            return taskArtwork

        var wantedGameId = String(task && (task.gameId || task.game_id) || "")
        if (wantedGameId.length === 0)
            return ""

        var games = controller && controller.games ? controller.games : []
        for (var index = 0; index < games.length; ++index) {
            var game = games[index] || {}
            var candidateId = String(game.id || game.gameId || game.game_id || "")
            if (candidateId === wantedGameId)
                return artworkValue(game)
        }
        return ""
    }

    function selectedTask() {
        if (taskList.currentIndex < 0 || taskList.currentIndex >= visibleTasks.length)
            return null
        return visibleTasks[taskList.currentIndex].taskData
    }

    function stableIds() {
        var ids = []
        for (var index = 0; index < visibleTasks.length; ++index)
            ids.push(String(visibleTasks[index].rowId))
        return ids
    }

    function restoreSelection() {
        var ids = stableIds()
        var wanted = navigation ? navigation.reconcileFocus("tasks", ids) : retainedTaskId
        var index = ids.indexOf(wanted)
        taskList.currentIndex = index >= 0 ? index : (ids.length ? 0 : -1)
        rememberSelection()
        if (ids.length === 0 && focusZone === 0) {
            focusZone = 2
            restoreActiveFocus()
        }
    }

    function rememberSelection() {
        if (taskList.currentIndex < 0 || taskList.currentIndex >= visibleTasks.length)
            return
        retainedTaskId = String(visibleTasks[taskList.currentIndex].rowId)
        if (navigation)
            navigation.rememberFocus("tasks", retainedTaskId, taskList.currentIndex)
    }

    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }

    function move(delta) {
        if (!visibleTasks.length)
            return false
        var target = Math.max(0, Math.min(visibleTasks.length - 1,
                                         taskList.currentIndex + delta))
        var changed = taskList.currentIndex !== target
        taskList.currentIndex = target
        rememberSelection()
        return changed
    }

    function gameKnown(task) {
        var wanted = String(task && (task.gameId || task.game_id) || "")
        if (!wanted.length)
            return false
        var games = controller && controller.games ? controller.games : []
        for (var index = 0; index < games.length; ++index) {
            if (String(games[index].id || "") === wanted)
                return true
        }
        return false
    }

    function closeHistoryModal() {
        if (!historyModalOpen)
            return false
        actionMenuOpen = false
        deleteConfirm = ""
        deleteChoice = 0
        if (navigation)
            navigation.closeModal()
        restoreActiveFocus()
        return true
    }

    // Closes whichever overlay is open (used by CouchMain).
    function closeOverlays() {
        if (cancellationOpen) {
            cancellationOpen = false
            cancellationChoice = 0
            if (navigation)
                navigation.closeModal()
            return true
        }
        return closeHistoryModal()
    }

    function openActionMenu() {
        if (!historyActions.length)
            return false
        actionMenuIndex = historyActions.length - 1   // "Delete" never auto-runs
        actionMenuOpen = true
        if (navigation)
            navigation.openModal("task-actions", historyActions[actionMenuIndex].id)
        restoreActiveFocus()
        return true
    }

    function openDeleteConfirm(kind) {
        if (kind === "all" && removableCount === 0)
            return false
        if (navigation && !historyModalOpen)
            navigation.openModal("task-delete", "cancel")
        actionMenuOpen = false
        deleteConfirm = kind
        deleteChoice = 0
        restoreActiveFocus()
        return true
    }

    function runHistoryAction() {
        var action = historyActions[actionMenuIndex]
        if (!action)
            return false
        if (action.id === "delete")
            return openDeleteConfirm("one")
        var task = selectedTask()
        closeHistoryModal()
        return Boolean(controller && controller.openGame
                       && controller.openGame(String(task.gameId || task.game_id || "")))
    }

    // Removes history records only; focus moves to the nearest remaining row.
    function confirmDelete() {
        var kind = deleteConfirm
        var removed = false
        if (kind === "one" && taskList.currentIndex >= 0 && controller && controller.removeFinishedTask) {
            var current = taskList.currentIndex
            var neighbour = visibleTasks[current + 1] || visibleTasks[current - 1]
            var target = String(visibleTasks[current].rowId)
            removed = Boolean(controller.removeFinishedTask(target))
            if (removed && neighbour) {
                retainedTaskId = String(neighbour.rowId)
                if (navigation)
                    navigation.rememberFocus("tasks", retainedTaskId, Math.max(0, current - (visibleTasks[current + 1] ? 0 : 1)))
            }
        } else if (kind === "all" && controller && controller.removeFinishedTask) {
            // Exactly the finished entries counted in the confirmation, never
            // hidden routine rows or active/queued work.
            var ids = []
            for (var index = 0; index < shownTasks.length; ++index) {
                if (terminal(shownTasks[index]))
                    ids.push(taskId(shownTasks[index], index))
            }
            var count = 0
            for (var i = 0; i < ids.length; ++i)
                count += controller.removeFinishedTask(ids[i]) ? 1 : 0
            removed = count > 0
        }
        closeHistoryModal()
        Qt.callLater(restoreSelection)
        return removed
    }

    function activateTile() {
        var tile = navTiles[selectedTile]
        if (!tile)
            return false
        if (tile.id === "tasks") {
            focusZone = visibleTasks.length > 0 ? 0 : 2
            return true
        }
        sectionRequested(tile.id)
        return true
    }

    function handleAction(action) {
        if (action === "ContextMenu" || action === "Search") action = "MoreActions"
        else if (action === "PageLeft" || action === "PreviousSection") action = "PageUp"
        else if (action === "PageRight" || action === "NextSection") action = "PageDown"
        if (cancellationOpen) {
            if (action === "Back") {
                cancellationOpen = false
                cancellationChoice = 0
                if (navigation)
                    navigation.closeModal()
                playSemanticSound("back")
            } else if (action === "NavigateLeft" || action === "NavigateUp"
                       || action === "NavigateRight" || action === "NavigateDown") {
                var previousChoice = cancellationChoice
                cancellationChoice = (action === "NavigateLeft" || action === "NavigateUp") ? 0 : 1
                if (cancellationChoice !== previousChoice)
                    playSemanticSound("navigate")
            } else if (action === "Confirm") {
                var completed = true
                if (cancellationChoice === 1) {
                    var taskToCancel = selectedTask()
                    completed = Boolean(controller && taskToCancel
                        && controller.cancelTask(String(
                            visibleTasks[taskList.currentIndex].rowId)))
                }
                cancellationOpen = false
                cancellationChoice = 0
                if (navigation)
                    navigation.closeModal()
                playSemanticSound(completed ? "confirm" : "error")
            }
            return
        }
        if (actionMenuOpen) {
            if (action === "Back" || action === "MoreActions") {
                closeHistoryModal()
                playSemanticSound("back")
            } else if (action === "NavigateUp" || action === "NavigateDown") {
                var nextAction = Math.max(0, Math.min(historyActions.length - 1,
                                                      actionMenuIndex + (action === "NavigateUp" ? -1 : 1)))
                if (nextAction !== actionMenuIndex) {
                    actionMenuIndex = nextAction
                    playSemanticSound("navigate")
                    restoreActiveFocus()
                }
            } else if (action === "Confirm") {
                playSemanticSound(runHistoryAction() ? "confirm" : "error")
            }
            return
        }
        if (deleteConfirm.length) {
            if (action === "Back") {
                closeHistoryModal()
                playSemanticSound("back")
            } else if (["NavigateLeft", "NavigateRight", "NavigateUp", "NavigateDown"].indexOf(action) >= 0) {
                var choice = (action === "NavigateLeft" || action === "NavigateUp") ? 0 : 1
                if (choice !== deleteChoice) {
                    deleteChoice = choice
                    playSemanticSound("navigate")
                    restoreActiveFocus()
                }
            } else if (action === "Confirm") {
                if (deleteChoice === 1)
                    playSemanticSound(confirmDelete() ? "confirm" : "error")
                else {
                    closeHistoryModal()
                    playSemanticSound("back")
                }
            }
            return
        }
        if (action === "Back") {
            backRequested()
            playSemanticSound("back")
            return
        }
        var previousZone = focusZone
        if (focusZone === 2) {
            if (action === "NavigateLeft" || action === "NavigateRight") {
                var tile = Math.max(0, Math.min(navTiles.length - 1,
                                                selectedTile + (action === "NavigateLeft" ? -1 : 1)))
                if (tile !== selectedTile) {
                    selectedTile = tile
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateUp") {
                // Back to the last selected task.
                if (visibleTasks.length > 0)
                    focusZone = 0
            } else if (action === "Confirm") {
                playSemanticSound(activateTile() ? "confirm" : "error")
            }
        } else if (action === "NavigateUp") {
            if (move(-1)) playSemanticSound("navigate")
        } else if (action === "NavigateDown") {
            if (taskList.currentIndex >= visibleTasks.length - 1)
                focusZone = 2
            else if (move(1))
                playSemanticSound("navigate")
        } else if (action === "PageUp") {
            if (move(-5)) playSemanticSound("navigate")
        } else if (action === "PageDown") {
            if (move(5)) playSemanticSound("navigate")
        } else if (action === "MoreActions") {
            // Y: delete every finished history record (confirmation first).
            playSemanticSound(openDeleteConfirm("all") ? "open" : "error")
        } else if (action === "Confirm") {
            if (taskList.currentIndex < 0) {
                playSemanticSound("error")
                return
            }
            var selected = selectedTask()
            if (selected && terminal(selected)) {
                playSemanticSound(openActionMenu() ? "open" : "error")
            } else if (selected && selected.cancellable === true) {
                cancellationOpen = true
                cancellationChoice = 0
                if (navigation)
                    navigation.openModal("cancel-task", "keep-task")
                playSemanticSound("open")
            } else {
                playSemanticSound("error")
            }
        }
        if (focusZone !== previousZone) {
            playSemanticSound("navigate")
            restoreActiveFocus()
        }
    }

    onTasksChanged: Qt.callLater(restoreSelection)
    onVisibleTasksChanged: Qt.callLater(restoreSelection)
    focus: visible
    Component.onCompleted: {
        Qt.callLater(restoreSelection)
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) {
        selectedTile = 1
        focusZone = visibleTasks.length > 0 ? 0 : 2
        restoreActiveFocus()
    }

    CouchHeroBackdrop {
        anchors.fill: parent
        sources: []
    }

    // ---- Header and real summary ------------------------------------------------
    RowLayout {
        id: header
        objectName: "couchTasksHeader"
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
                objectName: "couchTasksTitle"
                text: qsTr("Tasks")
                color: App.Theme.text
                font.pixelSize: App.Theme.couchTitleSize * page.fitScale
                font.weight: Font.Bold
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Analysis, verification and compression run here one after another. Cancellable work can be stopped safely.")
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.fitScale
                elide: Text.ElideRight
            }
        }
        CouchButton {
            id: deleteAllButton
            objectName: "couchTasksDeleteAll"
            visible: page.removableCount > 0
            Layout.alignment: Qt.AlignVCenter
            couchScale: page.fitScale
            focusPolicy: Qt.NoFocus
            implicitHeight: 58 * page.fitScale
            implicitWidth: contentItem.implicitWidth + 2 * App.Theme.couchSpaceL * page.fitScale
            iconSource: App.UiIcons.couchGlyphRemove
            iconLightSource: App.UiIcons.couchGlyphRemoveOnLight
            iconSize: App.Theme.couchIconSmall
            text: qsTr("Delete all")
            font.pixelSize: 17 * page.fitScale
            Accessible.name: qsTr("Delete all")
            Accessible.description: qsTr("Deletes finished history entries. Does not delete files or active tasks.")
            onClicked: page.playSemanticSound(page.openDeleteConfirm("all") ? "open" : "error")
        }
        Repeater {
            model: page.summarySections
            delegate: Rectangle {
                id: summaryCard
                objectName: "couchTasksSummary-" + modelData.section
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
                            text: String(summaryCard.modelData.value)
                            color: App.Theme.text
                            font.pixelSize: 26 * page.fitScale
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: summaryCard.modelData.title
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.couchCaptionSize * page.fitScale
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }

    // ---- Task list (focus zone 0) ---------------------------------------------------
    ListView {
        id: taskList
        objectName: "couchTaskList"
        readonly property real pad: 14 * page.fitScale
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: footerRow.top
        anchors.leftMargin: page.contentMargin - pad
        anchors.rightMargin: page.contentMargin - pad
        anchors.topMargin: App.Theme.couchSpaceM * page.fitScale
        visible: page.visibleTasks.length > 0
        model: page.visibleTasks
        spacing: App.Theme.couchSpaceM * page.fitScale
        clip: true
        reuseItems: true
        topMargin: pad
        bottomMargin: pad
        boundsBehavior: Flickable.StopAtBounds
        keyNavigationEnabled: false
        cacheBuffer: Math.max(height, 600)
        highlightMoveDuration: App.Theme.couchMotionDuration(180)
        highlightMoveVelocity: -1
        preferredHighlightBegin: pad
        preferredHighlightEnd: height - pad
        highlightRangeMode: ListView.ApplyRange

        delegate: Item {
            id: taskDelegate
            objectName: "couchTaskRow"
            required property var modelData
            required property int index
            property var taskData: modelData.taskData || ({})
            readonly property bool selected: taskList.currentIndex === index
                                             && page.focusZone === 0 && !page.overlayOpen
            readonly property bool cancellable: !page.terminal(taskData) && taskData.cancellable === true
            width: taskList.width - 2 * taskList.pad
            x: taskList.pad
            height: sectionHeader.height + card.height
            z: selected ? 2 : 1

            Row {
                id: sectionHeader
                visible: taskDelegate.modelData.sectionStart
                height: visible ? 44 * page.fitScale : 0
                spacing: App.Theme.couchSpaceM * page.fitScale
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: page.sectionTitle(taskDelegate.modelData.section)
                    color: App.Theme.text
                    font.pixelSize: 22 * page.fitScale
                    font.weight: Font.Bold
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: page.sectionSubtitle(taskDelegate.modelData.section)
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.couchLabelSize * page.fitScale
                }
            }

            Item {
                id: card
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: sectionHeader.bottom
                height: 128 * page.fitScale

                Rectangle {
                    anchors.fill: parent
                    radius: App.Theme.couchCardRadius * page.fitScale
                    color: taskDelegate.selected ? App.Theme.couchFocusSurface
                           : Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b,
                                     App.Theme.dark ? 0.86 : 0.94)
                    border.width: 1
                    border.color: App.Theme.border
                    scale: taskDelegate.selected ? 1.01 : 1.0
                    Behavior on scale { NumberAnimation { duration: App.Theme.couchMotionDuration(150); easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                    CouchFocusFrame {
                        active: taskDelegate.selected
                        radius: parent.radius
                        couchScale: page.fitScale
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        var wasSelected = taskDelegate.selected
                        page.focusZone = 0
                        taskList.currentIndex = taskDelegate.index
                        page.rememberSelection()
                        if (wasSelected)
                            page.handleAction("Confirm")
                        page.restoreActiveFocus()
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12 * page.fitScale
                    anchors.rightMargin: App.Theme.couchSpaceL * page.fitScale
                    spacing: App.Theme.couchSpaceL * page.fitScale

                    // Tasks without a game (e.g. library scan) show their type
                    // icon instead of a placeholder cover with clipped text.
                    Rectangle {
                        objectName: "couchTaskTypeTile"
                        visible: String(taskDelegate.taskData.gameId || taskDelegate.taskData.game_id || "").length === 0
                        Layout.preferredWidth: 72 * page.fitScale
                        Layout.preferredHeight: 104 * page.fitScale
                        radius: 10 * page.fitScale
                        color: App.Theme.surfaceRaised
                        border.width: 1
                        border.color: App.Theme.border
                        CouchIcon {
                            anchors.centerIn: parent
                            size: App.Theme.couchIconMedium
                            couchScale: page.fitScale
                            source: String(page.typeIcon(taskDelegate.taskData, false)).length
                                    ? page.typeIcon(taskDelegate.taskData, false) : App.UiIcons.couchGlyphTasks
                            lightSource: String(page.typeIcon(taskDelegate.taskData, true)).length
                                         ? page.typeIcon(taskDelegate.taskData, true) : App.UiIcons.couchGlyphTasksOnLight
                        }
                    }
                    GameArtwork {
                        visible: String(taskDelegate.taskData.gameId || taskDelegate.taskData.game_id || "").length > 0
                        Layout.preferredWidth: 72 * page.fitScale
                        Layout.preferredHeight: 104 * page.fitScale
                        gameId: String(taskDelegate.taskData.gameId
                                       || taskDelegate.taskData.game_id || "")
                        title: String(taskDelegate.taskData.gameName
                                      || taskDelegate.taskData.name || qsTr("Task"))
                        artworkSource: page.artworkForTask(taskDelegate.taskData)
                        artworkFillMode: Image.PreserveAspectCrop
                        cornerRadius: 10 * page.fitScale
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: 6 * page.fitScale
                        Row {
                            spacing: App.Theme.couchSpaceS * page.fitScale
                            CouchIcon {
                                objectName: "couchTaskTypeIcon"
                                anchors.verticalCenter: parent.verticalCenter
                                size: App.Theme.couchIconSmall
                                couchScale: page.fitScale
                                source: page.typeIcon(taskDelegate.taskData, false)
                                lightSource: page.typeIcon(taskDelegate.taskData, true)
                            }
                            Label {
                                objectName: "couchTaskType"
                                anchors.verticalCenter: parent.verticalCenter
                                text: page.typeLabel(taskDelegate.taskData)
                                color: App.Theme.textSecondary
                                font.pixelSize: 17 * page.fitScale
                                font.weight: Font.DemiBold
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: String(taskDelegate.taskData.gameName
                                         || taskDelegate.taskData.title
                                         || taskDelegate.taskData.name || qsTr("Task"))
                            color: taskDelegate.selected ? App.Theme.couchFocusText : App.Theme.text
                            font.pixelSize: 24 * page.fitScale
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                        }
                        Rectangle {
                            objectName: "couchTaskProgress"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 8 * page.fitScale
                            visible: page.hasProgress(taskDelegate.taskData)
                            radius: height / 2
                            color: Qt.rgba(App.Theme.text.r, App.Theme.text.g, App.Theme.text.b, 0.14)
                            Rectangle {
                                width: parent.width * (parent.visible ? page.progressValue(taskDelegate.taskData) : 0)
                                height: parent.height
                                radius: parent.radius
                                color: App.Theme.accent
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: text.length > 0
                            text: page.messageText(taskDelegate.taskData)
                            color: page.statusTone(taskDelegate.taskData.status) === "danger"
                                   ? App.Theme.couchToneText("danger") : App.Theme.textSecondary
                            font.pixelSize: 15 * page.fitScale
                            elide: Text.ElideMiddle
                        }
                    }

                    ColumnLayout {
                        Layout.alignment: Qt.AlignVCenter
                        spacing: 6 * page.fitScale
                        CouchStatePill {
                            objectName: "couchTaskState"
                            Layout.alignment: Qt.AlignRight
                            couchScale: page.fitScale
                            tone: page.statusTone(taskDelegate.taskData.status)
                            text: page.translatedStatus(taskDelegate.taskData.status)
                        }
                        Label {
                            objectName: "couchTaskProgressText"
                            Layout.alignment: Qt.AlignRight
                            visible: text.length > 0
                            text: page.progressText(taskDelegate.taskData)
                            color: page.hasProgress(taskDelegate.taskData) ? App.Theme.accent : App.Theme.textSecondary
                            font.pixelSize: (page.hasProgress(taskDelegate.taskData) ? 22 : 15) * page.fitScale
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.alignment: Qt.AlignRight
                            text: page.timeLabel(taskDelegate.taskData)
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.couchCaptionSize * page.fitScale
                        }
                    }
                    Row {
                        objectName: "couchTaskAction"
                        Layout.alignment: Qt.AlignVCenter
                        visible: taskDelegate.cancellable
                        spacing: 6 * page.fitScale
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            size: App.Theme.couchIconSmall
                            couchScale: page.fitScale
                            source: App.UiIcons.couchGlyphStop
                            lightSource: App.UiIcons.couchGlyphStopOnLight
                        }
                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            text: page.taskActions[0].title
                            color: taskDelegate.selected ? App.Theme.couchFocusText : App.Theme.textSecondary
                            font.pixelSize: App.Theme.couchLabelSize * page.fitScale
                            font.weight: Font.DemiBold
                        }
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            size: App.Theme.couchIconSmall
                            couchScale: page.fitScale
                            source: App.UiIcons.couchGlyphNext
                            lightSource: App.UiIcons.couchGlyphNextOnLight
                        }
                    }
                    // Finished rows: A opens the history menu (details / delete).
                    CouchIcon {
                        objectName: "couchTaskMenuChevron"
                        Layout.alignment: Qt.AlignVCenter
                        visible: page.terminal(taskDelegate.taskData)
                        size: App.Theme.couchIconSmall
                        couchScale: page.fitScale
                        source: App.UiIcons.couchGlyphMore
                        lightSource: App.UiIcons.couchGlyphMoreOnLight
                    }
                }
            }
        }
    }

    // ---- Empty state -----------------------------------------------------------------
    Rectangle {
        objectName: "couchTasksEmptyState"
        visible: page.visibleTasks.length === 0
        anchors.centerIn: taskList
        width: Math.min(taskList.width - 40 * page.fitScale, 900 * page.fitScale)
        height: Math.min(taskList.height, 300 * page.fitScale)
        radius: App.Theme.couchPanelRadius * page.fitScale
        color: App.Theme.dark ? "#D5151D29" : "#EDFFFFFF"
        border.width: 1
        border.color: App.Theme.borderStrong
        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width - 80 * page.fitScale
            spacing: App.Theme.couchSpaceM * page.fitScale
            CouchIcon {
                objectName: "couchTasksEmptyIcon"
                Layout.alignment: Qt.AlignHCenter
                size: App.Theme.couchIconLarge
                couchScale: page.fitScale
                source: App.UiIcons.couchGlyphTasks
                lightSource: App.UiIcons.couchGlyphTasksOnLight
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("No tasks yet")
                color: App.Theme.text
                font.pixelSize: 30 * page.fitScale
                font.weight: Font.Bold
                horizontalAlignment: Text.AlignHCenter
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Active work and recent results will appear here.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchBodySize * page.fitScale
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
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
            visible: page.visibleTasks.length > 0 && taskList.currentIndex >= 0
            anchors.left: parent.left
            anchors.leftMargin: page.contentMargin
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("%1 / %2").arg(taskList.currentIndex + 1).arg(page.visibleTasks.length)
            color: App.Theme.textSecondary
            font.pixelSize: App.Theme.couchLabelSize * page.fitScale
            font.weight: Font.DemiBold
        }
    }

    // ---- Global section navigation (focus zone 2) ----------------------------------
    CouchBottomNav {
        id: bottomNav
        objectName: "couchTasksNavigation"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin
        anchors.bottomMargin: page.bottomNavMargin
        height: implicitHeight
        couchScale: page.fitScale
        model: page.navTiles
        activeIndex: 1
        currentIndex: page.selectedTile
        navFocused: page.focusZone === 2 && !page.overlayOpen
        onActivated: function(index) {
            page.focusZone = 2
            page.selectedTile = index
            page.playSemanticSound(page.activateTile() ? "confirm" : "error")
        }
    }

    CouchOverlayFrame {
        anchors.fill: parent
        z: 100
        visible: page.cancellationOpen
        couchScale: page.couchScale
        maximumWidth: 720 * page.couchScale
        preferredHeight: 310 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 18 * page.couchScale
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Cancel this task?")
                    color: App.Theme.text
                    font.pixelSize: 32 * page.couchScale
                    font.weight: Font.Bold
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Keep task is the safe default. Press right to cancel.")
                    color: App.Theme.textSecondary
                    font.pixelSize: 18 * page.couchScale
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16 * page.couchScale
                    CouchButton {
                        id: keepTaskButton
                        Layout.fillWidth: true
                        couchScale: page.couchScale
                        Layout.preferredHeight: 66 * page.couchScale
                        text: qsTr("Keep task")
                        focus: page.cancellationOpen && page.cancellationChoice === 0
                        onClicked: {
                            page.cancellationChoice = 0
                            page.handleAction("Confirm")
                        }
                    }
                    CouchButton {
                        id: cancelTaskButton
                        Layout.fillWidth: true
                        couchScale: page.couchScale
                        Layout.preferredHeight: 66 * page.couchScale
                        text: qsTr("Cancel task")
                        focus: page.cancellationOpen && page.cancellationChoice === 1
                        onClicked: {
                            page.cancellationChoice = 1
                            page.handleAction("Confirm")
                        }
                    }
                }
        }
    }

    // ---- History menu for a finished task ----------------------------------------
    CouchOverlayFrame {
        objectName: "couchTaskHistoryMenu"
        anchors.fill: parent
        z: 100
        visible: page.actionMenuOpen
        couchScale: page.couchScale
        maximumWidth: 720 * page.couchScale
        preferredHeight: 380 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 14 * page.couchScale
            Label {
                Layout.fillWidth: true
                text: page.selectedTask() ? String(page.selectedTask().gameName || page.selectedTask().name || qsTr("Task")) : ""
                color: App.Theme.text
                font.pixelSize: 30 * page.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Repeater {
                id: historyActionRepeater
                model: page.historyActions
                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.preferredHeight: 76 * page.couchScale
                    couchScale: page.couchScale
                    compact: true
                    iconSource: modelData.icon
                    iconLightSource: modelData.iconOnLight
                    text: modelData.title
                    showChevron: true
                    focus: page.actionMenuOpen && page.actionMenuIndex === index
                    onClicked: {
                        page.actionMenuIndex = index
                        page.playSemanticSound(page.runHistoryAction() ? "confirm" : "error")
                    }
                }
            }
            Item { Layout.fillHeight: true }
        }
    }

    // ---- Delete confirmation (safe default: Cancel) ---------------------------------
    CouchOverlayFrame {
        objectName: "couchTaskDeleteConfirmation"
        anchors.fill: parent
        z: 110
        visible: page.deleteConfirm.length > 0
        couchScale: page.couchScale
        maximumWidth: 760 * page.couchScale
        preferredHeight: 340 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 18 * page.couchScale
            Label {
                Layout.fillWidth: true
                text: page.deleteConfirm === "all"
                      ? qsTr("Delete %1 finished history entries?").arg(page.removableCount)
                      : qsTr("Delete this history entry?")
                color: App.Theme.text
                font.pixelSize: 32 * page.couchScale
                font.weight: Font.Bold
                wrapMode: Text.WordWrap
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Deletes finished history entries. Does not delete files or active tasks.")
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.couchScale
                wrapMode: Text.WordWrap
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                spacing: 16 * page.couchScale
                CouchButton {
                    id: deleteCancelButton
                    Layout.fillWidth: true
                    Layout.preferredHeight: 66 * page.couchScale
                    couchScale: page.couchScale
                    iconSource: App.UiIcons.couchGlyphPrevious
                    iconLightSource: App.UiIcons.couchGlyphPreviousOnLight
                    text: qsTr("Cancel")
                    focus: page.deleteConfirm.length > 0 && page.deleteChoice === 0
                    onClicked: { page.deleteChoice = 0; page.handleAction("Confirm") }
                }
                CouchButton {
                    id: deleteRunButton
                    Layout.fillWidth: true
                    Layout.preferredHeight: 66 * page.couchScale
                    couchScale: page.couchScale
                    iconSource: App.UiIcons.couchGlyphRemove
                    iconLightSource: App.UiIcons.couchGlyphRemoveOnLight
                    text: page.deleteConfirm === "all" ? qsTr("Delete all") : qsTr("Delete")
                    focus: page.deleteConfirm.length > 0 && page.deleteChoice === 1
                    onClicked: { page.deleteChoice = 1; page.handleAction("Confirm") }
                }
            }
        }
    }
}
