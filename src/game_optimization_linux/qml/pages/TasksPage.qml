import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../dialogs"
import ".." as App

Item {
    id: page
    objectName: "tasksPage"

    property var controller
    property bool historyMode: false
    property var tasksData: controller
                            ? (historyMode
                               ? (controller.taskHistory || [])
                               : (controller.activeTasks || []))
                            : []
    signal toastRequested(string message, string tone)

    function statusOf(task) {
        return String(task && task.status ? task.status : "queued").toLowerCase()
    }

    function countWhere(group) {
        var count = 0
        var source = tasksData || []
        for (var i = 0; i < source.length; ++i) {
            var status = statusOf(source[i])
            if (group === "active" && ["running", "analyzing"].indexOf(status) >= 0)
                count++
            else if (group === status)
                count++
        }
        return count
    }

    function taskMatchesFilter(task) {
        return true
    }

    function taskIdentifier(task, index) {
        if (task && task.id !== undefined && task.id !== null)
            return String(task.id)
        return "task-" + String(index)
    }

    function synchronizeTaskModel() {
        var wanted = []
        var source = tasksData || []
        for (var sourceIndex = 0; sourceIndex < source.length; ++sourceIndex) {
            if (taskMatchesFilter(source[sourceIndex])) {
                wanted.push({
                    "taskId": taskIdentifier(source[sourceIndex], sourceIndex),
                    "taskData": source[sourceIndex]
                })
            }
        }

        for (var oldIndex = stableTaskModel.count - 1; oldIndex >= 0; --oldIndex) {
            var keep = false
            for (var wantedIndex = 0; wantedIndex < wanted.length; ++wantedIndex) {
                if (stableTaskModel.get(oldIndex).taskId === wanted[wantedIndex].taskId) {
                    keep = true
                    break
                }
            }
            if (!keep)
                stableTaskModel.remove(oldIndex)
        }

        for (var index = 0; index < wanted.length; ++index) {
            var existingIndex = -1
            for (var modelIndex = index; modelIndex < stableTaskModel.count; ++modelIndex) {
                if (stableTaskModel.get(modelIndex).taskId === wanted[index].taskId) {
                    existingIndex = modelIndex
                    break
                }
            }
            if (existingIndex < 0)
                stableTaskModel.insert(index, wanted[index])
            else if (existingIndex !== index)
                stableTaskModel.move(existingIndex, index, 1)
            stableTaskModel.setProperty(index, "taskData", wanted[index].taskData)
        }
    }

    onTasksDataChanged: synchronizeTaskModel()
    Component.onCompleted: synchronizeTaskModel()

    ListModel {
        id: stableTaskModel
        dynamicRoles: true
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: App.Theme.contentPadding
        spacing: App.Theme.spacingLarge

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Tasks")
            subtitle: qsTr("Library scans, size calculations, and read-only analyses run without blocking the interface")

            AppButton {
                visible: page.historyMode && page.tasksData.length > 0
                text: qsTr("Clear finished")
                kind: "secondary"
                onClicked: {
                    if (page.controller && page.controller.clearFinishedTasks)
                        page.controller.clearFinishedTasks()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            AppButton {
                text: qsTr("Active")
                kind: !page.historyMode ? "primary" : "secondary"
                onClicked: page.historyMode = false
            }
            AppButton {
                text: qsTr("History")
                kind: page.historyMode ? "primary" : "secondary"
                onClicked: page.historyMode = true
            }
            Item { Layout.fillWidth: true }
        }

        GridLayout {
            Layout.fillWidth: true
            columns: page.width >= 980 ? 4 : 2
            rowSpacing: 10
            columnSpacing: 10

            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Active")
                value: String(page.countWhere("active"))
                symbol: "↻"
                tone: App.Theme.info
            }

            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Queued")
                value: String(page.countWhere("queued"))
                symbol: "⋯"
                tone: App.Theme.warning
            }

            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Completed")
                value: String(page.countWhere("completed"))
                symbol: "✓"
                tone: App.Theme.success
            }

            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Failed")
                value: String(page.countWhere("failed"))
                symbol: "!"
                tone: App.Theme.danger
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: taskList
                objectName: "taskList"
                anchors.fill: parent
                visible: stableTaskModel.count > 0
                clip: true
                reuseItems: true
                cacheBuffer: Math.max(height * 2, 800)
                spacing: 10
                model: stableTaskModel
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: TaskCard {
                    required property var model
                    width: ListView.view.width - 10
                    taskData: model.taskData
                    onPauseRequested: function(taskId) {
                        if (page.controller && page.controller.pauseTask)
                            page.controller.pauseTask(taskId)
                    }
                    onResumeRequested: function(taskId) {
                        if (page.controller && page.controller.resumeTask)
                            page.controller.resumeTask(taskId)
                    }
                    onCancelRequested: function(taskId, taskName, readOnly) {
                        cancelDialog.ask(
                            qsTr("Cancel task?"),
                            readOnly
                            ? qsTr("“%1” will stop at its current progress. No game files are affected.").arg(taskName)
                            : qsTr("“%1” will stop after the active helper exits. Files already processed stay valid and Game Optimization will verify the partial result.").arg(taskName),
                            qsTr("Cancel task"),
                            true,
                            taskId)
                    }
                    onRemoveRequested: function(taskId) {
                        if (page.controller && page.controller.removeFinishedTask)
                            page.controller.removeFinishedTask(taskId)
                    }
                }
            }

            EmptyState {
                objectName: "emptyTasksState"
                anchors.fill: parent
                visible: stableTaskModel.count === 0
                symbol: "↻"
                title: page.historyMode ? qsTr("No task history") : qsTr("No active tasks")
                message: page.historyMode
                         ? qsTr("Completed, failed, cancelled and interrupted tasks will appear here.")
                         : qsTr("Queued and running tasks will appear here.")
                actionText: qsTr("Browse games")
                onActionTriggered: {
                    if (page.controller && page.controller.navigate)
                        page.controller.navigate("games")
                }
            }
        }
    }

    ConfirmDialog {
        id: cancelDialog
        onConfirmed: function(taskId) {
            if (page.controller && page.controller.cancelTask)
                page.controller.cancelTask(String(taskId))
        }
    }
}
