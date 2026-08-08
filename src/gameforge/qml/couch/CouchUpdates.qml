import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "../components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchUpdates"

    property var controller
    property var navigation
    property real couchScale: 1.0
    property var updatesData: controller && controller.updates ? controller.updates : []
    property var summaryData: controller && controller.updatesSummary
                              ? controller.updatesSummary : ({})
    property var pendingPlan: ({})
    property int focusZone: 0
    property int selectedAction: 0
    property int confirmSelection: 0
    property string retainedRowId: ""
    property alias selectedIndex: updatesList.currentIndex
    readonly property bool confirmationOpen: confirmationOverlay.visible
    readonly property var selectedUpdate: updatesList.currentIndex >= 0
                                          && updatesList.currentIndex < stableUpdates.count
                                          ? stableUpdates.get(updatesList.currentIndex).updateData
                                          : ({})
    readonly property var actionModel: [
        {
            "symbol": "⌕",
            "title": qsTr("Analyze"),
            "enabled": booleanValue(selectedUpdate, ["canAnalyze", "can_analyze"], false),
            "visible": booleanValue(selectedUpdate, ["canAnalyze", "can_analyze"], false)
        },
        {
            "symbol": "↓",
            "title": qsTr("Compress"),
            "enabled": booleanValue(selectedUpdate, ["canCompress", "can_compress"], false),
            "visible": booleanValue(selectedUpdate, ["canCompress", "can_compress"], false)
        },
        {
            "symbol": "×",
            "title": qsTr("Ignore"),
            "enabled": booleanValue(selectedUpdate, ["canIgnore", "can_ignore"], false),
            "visible": booleanValue(selectedUpdate, ["canIgnore", "can_ignore"], false)
        },
        {
            "symbol": "☷",
            "title": qsTr("View details"),
            "enabled": gameId(selectedUpdate).length > 0,
            "visible": gameId(selectedUpdate).length > 0
        },
        {
            "symbol": "‹",
            "title": qsTr("Back"),
            "enabled": true,
            "visible": true
        }
    ]

    signal backRequested()
    signal toastRequested(string message, string tone)

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible)
                return
            if (page.confirmationOpen)
                (page.confirmSelection === 0
                 ? updateCancelButton : updateConfirmButton).forceActiveFocus()
            else if (page.focusZone === 0)
                updatesList.forceActiveFocus()
            else {
                var tile = actionRepeater.itemAt(page.selectedAction)
                if (tile) tile.forceActiveFocus()
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

    function friendlyMessage(raw) {
        var message = String(raw || "")
        if (message.indexOf("Traceback (most recent call last)") >= 0
                || message.indexOf("File \"") >= 0)
            return qsTr("The update check failed. See the application log for technical details.")
        return message
    }

    function gameId(update) {
        return String(value(update, ["gameId", "game_id", "id"], ""))
    }

    function gameName(update) {
        return String(value(update, ["name", "gameName", "game_name"], qsTr("Unknown game")))
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
            selectedAction = 4
            focusZone = 1
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
            return
        updatesList.currentIndex = index
        retainedRowId = String(stableUpdates.get(index).rowId || "")
        if (navigation)
            navigation.rememberFocus("updates", retainedRowId, index)
        updatesList.positionViewAtIndex(index, ListView.Contain)
        ensureSelectedAction()
    }

    function moveAction(direction) {
        var actions = page.actionModel || []
        var candidate = selectedAction + direction
        while (candidate >= 0 && candidate < actions.length) {
            if (actions[candidate].visible && actions[candidate].enabled) {
                selectedAction = candidate
                return
            }
            candidate += direction
        }
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
            return
        var plan = controller.prepareCompression(id, profileFor(selectedUpdate), true)
        if (!planIsValid(plan)) {
            var message = String(planValue(plan, ["error", "message"],
                                         qsTr("Compression cannot be started.")))
            var blockers = planValue(plan, ["blockers"], [])
            if (blockers && blockers.length > 0)
                message = String(blockers[0])
            showMessage(message, "error")
            return
        }
        pendingPlan = plan
        confirmSelection = 0
        confirmationOverlay.visible = true
        if (navigation)
            navigation.openModal("updates-compression", "cancel")
    }

    function closeConfirmation() {
        confirmationOverlay.visible = false
        pendingPlan = ({})
        confirmSelection = 0
        if (navigation)
            navigation.closeModal()
    }

    function confirmPlan() {
        var planId = String(planValue(pendingPlan, ["planId", "plan_id"], ""))
        if (planId.length > 0 && controller && controller.startCompression)
            controller.startCompression(planId)
        closeConfirmation()
    }

    function activateSelectedAction() {
        var actions = page.actionModel || []
        if (!actions[selectedAction] || !actions[selectedAction].enabled)
            return
        var id = gameId(selectedUpdate)
        if (selectedAction === 0 && id.length > 0 && controller && controller.analyzeChanges)
            controller.analyzeChanges(id)
        else if (selectedAction === 1)
            prepareSelectedCompression()
        else if (selectedAction === 2 && id.length > 0 && controller && controller.ignoreUpdate)
            controller.ignoreUpdate(id)
        else if (selectedAction === 3 && id.length > 0 && controller && controller.openGame)
            controller.openGame(id)
        else if (selectedAction === 4)
            backRequested()
    }

    function handleConfirmationAction(action) {
        if (action === "Back") {
            closeConfirmation()
        } else if (action === "NavigateLeft" || action === "NavigateUp") {
            confirmSelection = 0
        } else if (action === "NavigateRight" || action === "NavigateDown") {
            confirmSelection = 1
        } else if (action === "Confirm") {
            if (confirmSelection === 1)
                confirmPlan()
            else
                closeConfirmation()
        }
    }

    function handleAction(action) {
        if (action === "Accept") action = "Confirm"
        else if (action === "PreviousSection") action = "PageLeft"
        else if (action === "NextSection") action = "PageRight"
        if (confirmationOpen) {
            handleConfirmationAction(action)
            return
        }
        if (action === "Back") {
            backRequested()
        } else if (action === "NavigateUp") {
            if (focusZone === 1 && stableUpdates.count > 0) {
                focusZone = 0
                updatesList.forceActiveFocus()
            } else if (focusZone === 0) {
                selectUpdate(Math.max(0, updatesList.currentIndex - 1))
            }
        } else if (action === "NavigateDown") {
            if (focusZone === 0 && updatesList.currentIndex < stableUpdates.count - 1)
                selectUpdate(updatesList.currentIndex + 1)
            else {
                focusZone = 1
                ensureSelectedAction()
                var tile = actionRepeater.itemAt(selectedAction)
                if (tile)
                    tile.forceActiveFocus()
            }
        } else if (action === "NavigateLeft" && focusZone === 1) {
            moveAction(-1)
        } else if (action === "NavigateRight" && focusZone === 1) {
            moveAction(1)
        } else if (action === "Confirm" && focusZone === 0) {
            focusZone = 1
            ensureSelectedAction()
        } else if (action === "Confirm" && focusZone === 1) {
            activateSelectedAction()
        } else if (action === "PageLeft" && stableUpdates.count > 0) {
            focusZone = 0
            selectUpdate(Math.max(0, updatesList.currentIndex - 5))
        } else if (action === "PageRight" && stableUpdates.count > 0) {
            focusZone = 0
            selectUpdate(Math.min(stableUpdates.count - 1, updatesList.currentIndex + 5))
        }
    }

    onUpdatesDataChanged: synchronizeUpdates()
    onSelectedUpdateChanged: Qt.callLater(page.ensureSelectedAction)
    focus: visible
    Component.onCompleted: {
        synchronizeUpdates()
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) restoreActiveFocus()

    ListModel {
        id: stableUpdates
        dynamicRoles: true
    }

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 72 * page.couchScale
        anchors.rightMargin: 72 * page.couchScale
        anchors.topMargin: 118 * page.couchScale
        anchors.bottomMargin: 90 * page.couchScale
        spacing: 16 * page.couchScale

        RowLayout {
            Layout.fillWidth: true
            spacing: 18 * page.couchScale

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3 * page.couchScale
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Updates")
                    color: App.Theme.text
                    font.pixelSize: 42 * page.couchScale
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Changed Steam files are re-analyzed before compression; current games need no action.")
                    color: App.Theme.textSecondary
                    font.pixelSize: 18 * page.couchScale
                    elide: Text.ElideRight
                }
            }

            Repeater {
                model: [
                    {
                        "label": qsTr("Need attention"),
                        "value": page.numberValue(page.summaryData, [
                            "needsCheckCount", "needs_check_count", "updateCount", "update_count"
                        ], 0)
                    },
                    {
                        "label": qsTr("Queued"),
                        "value": page.numberValue(page.summaryData, [
                            "pendingCount", "pending_count", "queuedCount", "queued_count"
                        ], 0)
                    },
                    {
                        "label": qsTr("Recovered"),
                        "value": page.formatBytes(page.numberValue(page.summaryData, [
                            "recentRecoveredBytes", "recent_recovered_bytes", "recoveredBytes"
                        ], 0))
                    }
                ]

                delegate: Rectangle {
                    required property var modelData
                    Layout.preferredWidth: 205 * page.couchScale
                    Layout.preferredHeight: 78 * page.couchScale
                    radius: 16 * page.couchScale
                    color: App.Theme.surface
                    border.width: 1
                    border.color: App.Theme.border

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 10 * page.couchScale
                        spacing: 1
                        Label {
                            text: String(parent.parent.modelData.value)
                            color: App.Theme.text
                            font.pixelSize: 22 * page.couchScale
                            font.weight: Font.Bold
                        }
                        Label {
                            text: parent.parent.modelData.label
                            color: App.Theme.textMuted
                            font.pixelSize: 15 * page.couchScale
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: updatesList
                objectName: "couchUpdatesList"
                anchors.fill: parent
                visible: stableUpdates.count > 0
                clip: true
                model: stableUpdates
                reuseItems: true
                cacheBuffer: Math.max(height * 2, 1000)
                spacing: 11 * page.couchScale
                boundsBehavior: Flickable.StopAtBounds
                highlightMoveDuration: App.Theme.animationFast
                currentIndex: count > 0 ? 0 : -1

                delegate: Rectangle {
                    id: updateRow
                    objectName: "couchUpdateCard"
                    required property var model
                    property var updateData: model.updateData || ({})
                    property string updateState: page.stateOf(updateData)
                    width: ListView.view.width - 28 * page.couchScale
                    x: 14 * page.couchScale
                    height: 148 * page.couchScale
                    radius: 18 * page.couchScale
                    clip: true
                    color: App.Theme.surface
                    border.width: updatesList.currentIndex === index
                                  && page.focusZone === 0 ? 5 * page.couchScale : 1
                    border.color: updatesList.currentIndex === index
                                  && page.focusZone === 0 ? App.Theme.accent : App.Theme.border
                    scale: updatesList.currentIndex === index
                           && page.focusZone === 0 ? 1.012 : 1.0
                    opacity: updatesList.currentIndex === index
                             || page.focusZone !== 0 ? 1.0 : 0.86
                    Behavior on scale {
                        NumberAnimation { duration: App.Theme.animationFast }
                    }
                    Behavior on opacity {
                        NumberAnimation { duration: App.Theme.animationFast }
                    }

                    required property int index

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            page.focusZone = 0
                            page.selectUpdate(updateRow.index)
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 13 * page.couchScale
                        spacing: 16 * page.couchScale

                        GameCover {
                            objectName: "couchUpdateCover"
                            Layout.preferredWidth: 82 * page.couchScale
                            Layout.preferredHeight: 122 * page.couchScale
                            title: page.gameName(updateRow.updateData)
                            launcher: String(page.value(updateRow.updateData, ["launcher"], qsTr("Steam")))
                            artworkSource: page.value(updateRow.updateData, [
                                "effectiveArtworkUrl", "effective_artwork_url",
                                "portraitArtwork", "portrait_artwork", "cover",
                                "fallbackArtwork", "headerArtwork"
                            ], "")
                            artworkFillMode: Image.PreserveAspectCrop
                            cornerRadius: 10 * page.couchScale
                        }

                        ColumnLayout {
                            objectName: "couchUpdateInformation"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            spacing: 4 * page.couchScale

                            Label {
                                Layout.fillWidth: true
                                text: page.gameName(updateRow.updateData)
                                color: App.Theme.text
                                font.pixelSize: 24 * page.couchScale
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("%1 · Build ID %2 · %3 changed")
                                      .arg(String(page.value(updateRow.updateData, ["launcher"], qsTr("Steam"))))
                                      .arg(String(page.value(updateRow.updateData, [
                                          "buildId", "build_id"
                                      ], qsTr("Unknown"))))
                                      .arg(page.formatBytes(page.numberValue(updateRow.updateData, [
                                          "changedBytes", "changed_bytes"
                                      ], 0)))
                                color: App.Theme.textSecondary
                                font.pixelSize: 16 * page.couchScale
                                elide: Text.ElideRight
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: String(page.value(updateRow.updateData, ["error"], "")).length > 0
                                text: page.friendlyMessage(page.value(
                                          updateRow.updateData, ["error"], ""))
                                color: App.Theme.danger
                                font.pixelSize: 15 * page.couchScale
                                elide: Text.ElideRight
                            }
                        }

                        Rectangle {
                            Layout.preferredWidth: stateLabel.implicitWidth
                                                   + 42 * page.couchScale
                            Layout.preferredHeight: 42 * page.couchScale
                            radius: height / 2
                            color: App.Theme.statusSurface(
                                       page.toneState(updateRow.updateState))

                            Row {
                                anchors.centerIn: parent
                                spacing: 9 * page.couchScale
                                Rectangle {
                                    width: 9 * page.couchScale
                                    height: width
                                    radius: width / 2
                                    color: App.Theme.statusColor(
                                               page.toneState(updateRow.updateState))
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Label {
                                    id: stateLabel
                                    text: page.translatedState(updateRow.updateState)
                                    color: App.Theme.statusColor(
                                               page.toneState(updateRow.updateState))
                                    font.pixelSize: 16 * page.couchScale
                                    font.weight: Font.DemiBold
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                anchors.centerIn: parent
                width: Math.min(parent.width - 40 * page.couchScale, 620 * page.couchScale)
                height: 360 * page.couchScale
                visible: stableUpdates.count === 0
                radius: 28 * page.couchScale
                color: App.Theme.surfaceRaised
                border.width: page.focusZone === 1 ? 4 * page.couchScale : 1
                border.color: page.focusZone === 1 ? App.Theme.accent : App.Theme.borderStrong

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 32 * page.couchScale
                    spacing: 14 * page.couchScale

                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 92 * page.couchScale
                        Layout.preferredHeight: 92 * page.couchScale
                        radius: width / 2
                        color: App.Theme.successSoft
                        border.width: 2 * page.couchScale
                        border.color: App.Theme.success

                        Label {
                            anchors.centerIn: parent
                            text: "✓"
                            color: App.Theme.success
                            font.pixelSize: 52 * page.couchScale
                            font.weight: Font.Bold
                        }
                    }
                    Label {
                        objectName: "couchUpdatesEmptyState"
                        Layout.fillWidth: true
                        text: qsTr("No game updates need attention")
                        color: App.Theme.text
                        font.pixelSize: 30 * page.couchScale
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Game changes and pending compression checks will appear here.")
                        color: App.Theme.textSecondary
                        font.pixelSize: 18 * page.couchScale
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                    CouchTile {
                        Layout.alignment: Qt.AlignHCenter
                        couchScale: page.couchScale
                        implicitWidth: 260 * page.couchScale
                        implicitHeight: 68 * page.couchScale
                        symbol: "‹"
                        text: qsTr("Back")
                        focus: page.focusZone === 1 && page.selectedAction === 4
                        onClicked: page.backRequested()
                    }
                }
            }
        }

        RowLayout {
            id: actionRow
            objectName: "couchUpdatesActions"
            Layout.fillWidth: true
            visible: stableUpdates.count > 0
            spacing: 12 * page.couchScale

            Repeater {
                id: actionRepeater
                model: page.actionModel

                delegate: CouchTile {
                    required property var modelData
                    required property int index
                    couchScale: page.couchScale
                    implicitWidth: 205 * page.couchScale
                    implicitHeight: 86 * page.couchScale
                    visible: modelData.visible
                    symbol: modelData.symbol
                    text: modelData.title
                    enabled: modelData.enabled
                    focus: page.focusZone === 1 && page.selectedAction === index
                    onClicked: {
                        page.focusZone = 1
                        page.selectedAction = index
                        page.activateSelectedAction()
                    }
                }
            }

            Item { Layout.fillWidth: true }
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
                                text: parent.parent.modelData.value
                                color: App.Theme.text
                                font.pixelSize: 18 * page.couchScale
                                font.weight: Font.Bold
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text: parent.parent.modelData.label
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
                    symbol: "‹"
                    text: qsTr("Cancel")
                    subtitle: qsTr("Return without starting")
                    focus: page.confirmationOpen && page.confirmSelection === 0
                    onClicked: page.closeConfirmation()
                }
                CouchTile {
                    id: updateConfirmButton
                    couchScale: page.couchScale
                    implicitWidth: 300 * page.couchScale
                    symbol: "↓"
                    text: qsTr("Start compression")
                    subtitle: qsTr("Use the verified plan")
                    focus: page.confirmationOpen && page.confirmSelection === 1
                    onClicked: page.confirmPlan()
                }
            }
        }
    }
}
