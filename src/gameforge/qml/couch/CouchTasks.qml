import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchTasks"

    property var controller
    property var navigation
    property real couchScale: 1.0
    property var tasks: controller && controller.tasks ? controller.tasks : []
    property var visibleTasks: buildVisibleTasks(tasks)
    property string retainedTaskId: ""
    property bool cancellationOpen: false
    property int cancellationChoice: 0
    readonly property int activeCount: sectionCount("active")
    readonly property int queuedCount: sectionCount("queued")
    readonly property int recentCount: sectionCount("recent")
    readonly property var summarySections: {
        var sections = []
        if (activeCount > 0)
            sections.push({ "section": "active", "title": qsTr("Active"), "symbol": "▶", "value": activeCount })
        if (queuedCount > 0)
            sections.push({ "section": "queued", "title": qsTr("Queued"), "symbol": "…", "value": queuedCount })
        if (recentCount > 0)
            sections.push({ "section": "recent", "title": qsTr("Recently completed"), "symbol": "✓", "value": recentCount })
        return sections
    }
    readonly property bool contextAvailable: {
        var task = selectedTask()
        return task !== null && !terminal(task) && task.cancellable === true
    }

    signal backRequested()

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (page.cancellationOpen)
                (page.cancellationChoice === 0
                 ? keepTaskButton : cancelTaskButton).forceActiveFocus()
            else
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

    function sectionCount(section) {
        var count = 0
        for (var index = 0; index < visibleTasks.length; ++index) {
            if (visibleTasks[index].section === section)
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
                "section": terminal(task) ? "recent" : queued(task) ? "queued" : "active",
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
        case "completed": return qsTr("Completed")
        case "failed": return qsTr("Failed")
        case "cancelled": return qsTr("Cancelled")
        case "interrupted": return qsTr("Interrupted")
        default: return String(status || qsTr("Unknown"))
        }
    }

    function statusTone(status) {
        var normalized = String(status).toLowerCase()
        if (normalized === "completed")
            return "completed"
        if (["failed", "cancelled", "interrupted"].indexOf(normalized) >= 0)
            return "failed"
        if (["queued", "pending", "waiting"].indexOf(normalized) >= 0)
            return "queued"
        return "running"
    }

    function progressValue(task) {
        var raw = Number(task && task.progress !== undefined ? task.progress : 0)
        if (!isFinite(raw))
            return 0
        if (raw > 1)
            raw /= 100
        return Math.max(0, Math.min(1, raw))
    }

    function friendlyMessage(raw) {
        var message = String(raw || "")
        if (message.indexOf("Traceback (most recent call last)") >= 0
                || message.indexOf("File \"") >= 0)
            return qsTr("The task failed. See the application log for technical details.")
        return message
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
    }

    function rememberSelection() {
        if (taskList.currentIndex < 0 || taskList.currentIndex >= visibleTasks.length)
            return
        retainedTaskId = String(visibleTasks[taskList.currentIndex].rowId)
        if (navigation)
            navigation.rememberFocus("tasks", retainedTaskId, taskList.currentIndex)
    }

    function move(delta) {
        if (!visibleTasks.length)
            return
        taskList.currentIndex = Math.max(0, Math.min(visibleTasks.length - 1,
                                                    taskList.currentIndex + delta))
        taskList.positionViewAtIndex(taskList.currentIndex, ListView.Contain)
        rememberSelection()
    }

    function handleAction(action) {
        if (cancellationOpen) {
            if (action === "Back") {
                cancellationOpen = false
                if (navigation)
                    navigation.closeModal()
            } else if (action === "NavigateLeft" || action === "NavigateUp") {
                cancellationChoice = 0
            } else if (action === "NavigateRight" || action === "NavigateDown") {
                cancellationChoice = 1
            } else if (action === "Confirm") {
                if (cancellationChoice === 1) {
                    var taskToCancel = selectedTask()
                    if (controller && taskToCancel)
                        controller.cancelTask(String(
                            visibleTasks[taskList.currentIndex].rowId))
                }
                cancellationOpen = false
                cancellationChoice = 0
                if (navigation)
                    navigation.closeModal()
            }
            return
        }
        if (action === "Back") {
            backRequested()
        } else if (action === "NavigateUp") {
            move(-1)
        } else if (action === "NavigateDown") {
            move(1)
        } else if (action === "PageLeft") {
            move(-5)
        } else if (action === "PageRight") {
            move(5)
        } else if ((action === "Confirm" || action === "ContextMenu")
                   && taskList.currentIndex >= 0) {
            var selected = selectedTask()
            if (selected && !terminal(selected) && selected.cancellable === true) {
                cancellationOpen = true
                cancellationChoice = 0
                if (navigation)
                    navigation.openModal("cancel-task", "keep-task")
            }
        }
    }

    onTasksChanged: Qt.callLater(restoreSelection)
    onVisibleTasksChanged: Qt.callLater(restoreSelection)
    focus: visible
    Component.onCompleted: {
        Qt.callLater(restoreSelection)
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) restoreActiveFocus()

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 68 * page.couchScale
        anchors.rightMargin: 68 * page.couchScale
        anchors.topMargin: 112 * page.couchScale
        anchors.bottomMargin: 90 * page.couchScale
        spacing: 18 * page.couchScale

        RowLayout {
            Layout.fillWidth: true

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3 * page.couchScale
                Label {
                    text: qsTr("Tasks")
                    color: App.Theme.text
                    font.pixelSize: 44 * page.couchScale
                    font.weight: Font.Bold
                }
                Label {
                    text: qsTr("Current work and the latest results")
                    color: App.Theme.textSecondary
                    font.pixelSize: 18 * page.couchScale
                }
            }

            Label {
                text: qsTr("%1 active · %2 queued")
                      .arg(page.activeCount).arg(page.queuedCount)
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.couchScale
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 16 * page.couchScale

            Repeater {
                model: page.summarySections

                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 92 * page.couchScale
                    radius: 18 * page.couchScale
                    color: App.Theme.surface
                    border.width: 1
                    border.color: App.Theme.border

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 18 * page.couchScale
                        spacing: 15 * page.couchScale

                        Label {
                            text: parent.parent.modelData.symbol
                            color: parent.parent.modelData.section === "active"
                                   ? App.Theme.accent : App.Theme.textSecondary
                            font.pixelSize: 28 * page.couchScale
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Label {
                                text: parent.parent.parent.modelData.title
                                color: App.Theme.textSecondary
                                font.pixelSize: 16 * page.couchScale
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Label {
                                text: String(parent.parent.parent.modelData.value)
                                color: App.Theme.text
                                font.pixelSize: 25 * page.couchScale
                                font.weight: Font.Bold
                            }
                        }
                    }
                }
            }
        }

        ListView {
            id: taskList
            objectName: "couchTaskList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: page.visibleTasks
            spacing: 13 * page.couchScale
            clip: true
            reuseItems: true
            boundsBehavior: Flickable.StopAtBounds
            cacheBuffer: Math.max(height * 2, 1000)

            delegate: Item {
                id: taskDelegate
                required property var modelData
                required property int index
                property var taskData: modelData.taskData || ({})
                property real taskProgress: page.progressValue(taskData)
                property bool selected: taskList.currentIndex === index
                width: taskList.width - 28 * page.couchScale
                x: 14 * page.couchScale
                height: (modelData.sectionStart ? 54 : 0) * page.couchScale
                        + 200 * page.couchScale

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8 * page.couchScale

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: taskDelegate.modelData.sectionStart
                                                ? 46 * page.couchScale : 0
                        visible: taskDelegate.modelData.sectionStart

                        Label {
                            text: page.sectionTitle(taskDelegate.modelData.section)
                            color: App.Theme.text
                            font.pixelSize: 24 * page.couchScale
                            font.weight: Font.Bold
                        }
                        Label {
                            text: page.sectionSubtitle(taskDelegate.modelData.section)
                            color: App.Theme.textSecondary
                            font.pixelSize: 16 * page.couchScale
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 20 * page.couchScale
                        color: taskDelegate.selected ? App.Theme.surfaceSelected : App.Theme.surface
                        border.width: taskDelegate.selected ? 5 * page.couchScale : 1
                        border.color: taskDelegate.selected ? App.Theme.accent : App.Theme.border
                        scale: taskDelegate.selected ? 1.012 : 1.0
                        opacity: taskDelegate.selected ? 1.0 : 0.88

                        Behavior on scale {
                            NumberAnimation { duration: App.Theme.animationFast }
                        }
                        Behavior on opacity {
                            NumberAnimation { duration: App.Theme.animationFast }
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                taskList.currentIndex = taskDelegate.index
                                page.rememberSelection()
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14 * page.couchScale
                            spacing: 18 * page.couchScale

                            GameArtwork {
                                Layout.preferredWidth: 96 * page.couchScale
                                Layout.fillHeight: true
                                gameId: String(taskDelegate.taskData.gameId
                                               || taskDelegate.taskData.game_id || "")
                                title: String(taskDelegate.taskData.gameName
                                              || taskDelegate.taskData.name || qsTr("Task"))
                                artworkSource: page.artworkForTask(
                                                   taskDelegate.taskData)
                                artworkFillMode: Image.PreserveAspectCrop
                                cornerRadius: 13 * page.couchScale
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: 7 * page.couchScale

                                Label {
                                    Layout.fillWidth: true
                                    text: String(taskDelegate.taskData.gameName
                                                 || taskDelegate.taskData.title
                                                 || taskDelegate.taskData.name || qsTr("Task"))
                                    color: App.Theme.text
                                    font.pixelSize: 24 * page.couchScale
                                    font.weight: Font.Bold
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: String(taskDelegate.taskData.taskType
                                                 || taskDelegate.taskData.type || qsTr("Operation"))
                                          + " · " + page.translatedStatus(
                                              taskDelegate.taskData.stage
                                              || taskDelegate.taskData.status)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: 17 * page.couchScale
                                    elide: Text.ElideRight
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 12 * page.couchScale
                                    radius: height / 2
                                    color: App.Theme.backgroundElevated

                                    Rectangle {
                                        width: parent.width * taskDelegate.taskProgress
                                        height: parent.height
                                        radius: parent.radius
                                        color: App.Theme.accent
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: page.friendlyMessage(
                                              taskDelegate.taskData.currentFile
                                              || taskDelegate.taskData.message
                                              || taskDelegate.taskData.error || "")
                                    color: page.taskStatus(taskDelegate.taskData) === "failed"
                                           ? App.Theme.danger : App.Theme.textSecondary
                                    font.pixelSize: 15 * page.couchScale
                                    elide: Text.ElideMiddle
                                }
                            }

                            ColumnLayout {
                                Layout.preferredWidth: 190 * page.couchScale
                                Layout.fillHeight: true
                                spacing: 9 * page.couchScale

                                Rectangle {
                                    Layout.alignment: Qt.AlignRight
                                    Layout.preferredWidth: taskStateLabel.implicitWidth
                                                           + 38 * page.couchScale
                                    Layout.preferredHeight: 40 * page.couchScale
                                    radius: height / 2
                                    color: App.Theme.statusSurface(page.statusTone(
                                        taskDelegate.taskData.status))

                                    Row {
                                        anchors.centerIn: parent
                                        spacing: 8 * page.couchScale
                                        Rectangle {
                                            width: 8 * page.couchScale
                                            height: width
                                            radius: width / 2
                                            color: App.Theme.statusColor(page.statusTone(
                                                taskDelegate.taskData.status))
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        Label {
                                            id: taskStateLabel
                                            text: page.translatedStatus(
                                                      taskDelegate.taskData.status)
                                            color: App.Theme.statusColor(page.statusTone(
                                                taskDelegate.taskData.status))
                                            font.pixelSize: 16 * page.couchScale
                                            font.weight: Font.DemiBold
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }
                                Label {
                                    Layout.alignment: Qt.AlignRight
                                    text: Math.round(taskDelegate.taskProgress * 100) + "%"
                                    color: App.Theme.accent
                                    font.pixelSize: 25 * page.couchScale
                                    font.weight: Font.Bold
                                }
                                Label {
                                    Layout.alignment: Qt.AlignRight
                                    text: page.timeLabel(taskDelegate.taskData)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: 14 * page.couchScale
                                }
                                Item { Layout.fillHeight: true }
                                Rectangle {
                                    Layout.alignment: Qt.AlignRight
                                    Layout.preferredWidth: 154 * page.couchScale
                                    Layout.preferredHeight: 48 * page.couchScale
                                    radius: 14 * page.couchScale
                                    color: taskDelegate.selected
                                           ? App.Theme.accentSoft : App.Theme.surfaceRaised
                                    border.width: taskDelegate.selected ? 2 : 1
                                    border.color: taskDelegate.selected
                                                  ? App.Theme.accent : App.Theme.borderStrong

                                    Label {
                                        anchors.centerIn: parent
                                        text: !page.terminal(taskDelegate.taskData)
                                              && taskDelegate.taskData.cancellable === true
                                              ? qsTr("Cancel task")
                                              : qsTr("Result saved")
                                        color: taskDelegate.selected
                                               ? App.Theme.accent : App.Theme.textSecondary
                                        font.pixelSize: 17 * page.couchScale
                                        font.weight: Font.DemiBold
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                anchors.centerIn: parent
                width: Math.min(parent.width - 60 * page.couchScale,
                                680 * page.couchScale)
                height: 290 * page.couchScale
                visible: taskList.count === 0
                radius: 26 * page.couchScale
                color: App.Theme.surfaceRaised
                border.width: 1
                border.color: App.Theme.borderStrong

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 30 * page.couchScale
                    spacing: 14 * page.couchScale
                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "✓"
                        color: App.Theme.accent
                        font.pixelSize: 58 * page.couchScale
                        font.weight: Font.Bold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("No tasks yet")
                        color: App.Theme.text
                        font.pixelSize: 30 * page.couchScale
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Active work and recent results will appear here.")
                        color: App.Theme.textSecondary
                        font.pixelSize: 18 * page.couchScale
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                    }
                }
            }
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
}
