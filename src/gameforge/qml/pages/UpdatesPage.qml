pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import "../dialogs"
import ".." as App

Item {
    id: page
    objectName: "updatesPage"

    property var controller
    property var updatesData: controller && controller.updates ? controller.updates : []
    property var summaryData: controller && controller.updatesSummary
                              ? controller.updatesSummary : ({})
    property var applicationData: controller && controller.applicationUpdateInfo
                                  ? controller.applicationUpdateInfo : ({})
    property var pendingPlan: ({})

    signal toastRequested(string message, string tone)

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
        var raw = value(source, keys, fallback)
        return raw === true
    }

    function numberValue(source, keys, fallback) {
        var raw = Number(value(source, keys, fallback))
        return isFinite(raw) && raw >= 0 ? raw : Number(fallback || 0)
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

    function stateSymbol(raw) {
        switch (String(raw).trim().toLowerCase().replace(/_/g, " ")) {
        case "update detected": return "↓"
        case "waiting for launcher":
        case "compressing":
        case "analyzing":
        case "queued": return "↻"
        case "optimized": return "✓"
        case "drive disconnected": return "⏏"
        case "failed":
        case "error": return "!"
        default: return "•"
        }
    }

    function sectionKey(update) {
        var explicitKey = String(value(update, ["sectionKey", "section_key", "section"], ""))
                                  .trim().toLowerCase().replace(/[- ]/g, "_")
        if (["game_updates", "compression_pending", "recently_optimized"].indexOf(explicitKey) >= 0)
            return explicitKey

        var normalizedState = stateOf(update).trim().toLowerCase()
        if (normalizedState === "optimized" || booleanValue(update, ["recentlyOptimized"], false))
            return "recently_optimized"
        if (["analysis required", "compression pending", "compressing", "verification required"]
                .indexOf(normalizedState) >= 0)
            return "compression_pending"
        return "game_updates"
    }

    function sectionRank(key) {
        if (key === "game_updates")
            return 0
        if (key === "compression_pending")
            return 1
        return 2
    }

    function sectionTitle(key) {
        if (key === "game_updates")
            return qsTr("Game updates")
        if (key === "compression_pending")
            return qsTr("Compression pending")
        return qsTr("Recently optimized")
    }

    function rowIdentifier(update, index) {
        var explicitId = String(value(update, ["rowId", "row_id"], ""))
        if (explicitId.length > 0)
            return explicitId
        var historyId = String(value(update, ["historyId", "history_id"], ""))
        return sectionKey(update) + ":" + gameId(update) + ":"
                + (historyId.length > 0 ? historyId : String(index))
    }

    function synchronizeUpdates() {
        var source = updatesData || []
        var wanted = []
        for (var sourceIndex = 0; sourceIndex < source.length; ++sourceIndex) {
            var update = source[sourceIndex] || {}
            wanted.push({
                "rowId": rowIdentifier(update, sourceIndex),
                "sectionKey": sectionKey(update),
                "updateData": update
            })
        }
        wanted.sort(function(first, second) {
            var rankDifference = sectionRank(first.sectionKey) - sectionRank(second.sectionKey)
            return rankDifference !== 0 ? rankDifference : first.rowId.localeCompare(second.rowId)
        })

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
            stableUpdates.setProperty(index, "sectionKey", wanted[index].sectionKey)
            stableUpdates.setProperty(index, "updateData", wanted[index].updateData)
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

    function formatDate(raw) {
        var text = String(raw || "")
        if (text.length === 0)
            return qsTr("Not available")
        var date = new Date(text)
        return isNaN(date.getTime()) ? text : Qt.formatDateTime(date, "dd.MM.yyyy, HH:mm")
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

    function prepareCompression(update) {
        var id = gameId(update)
        if (id.length === 0 || !controller || !controller.prepareCompression)
            return
        var plan = controller.prepareCompression(id, profileFor(update), true)
        if (!planIsValid(plan)) {
            var message = String(planValue(plan, ["error", "message"], qsTr("Compression cannot be started.")))
            var blockers = planValue(plan, ["blockers"], [])
            if (blockers && blockers.length > 0)
                message = String(blockers[0])
            toastRequested(message, "error")
            return
        }

        pendingPlan = plan
        var low = planValue(plan, [
            "estimatedSavingsLowBytes", "estimated_savings_low_bytes"
        ], null)
        var high = planValue(plan, [
            "estimatedSavingsHighBytes", "estimated_savings_high_bytes"
        ], low)
        var savings = low === null || high === null
                ? qsTr("Not estimated")
                : qsTr("%1-%2").arg(formatBytes(low)).arg(formatBytes(high))
        var warnings = planValue(plan, ["warnings"], [])
        var warningText = warnings && warnings.length > 0
                ? "\n" + qsTr("Warnings: %1").arg(
                      warnings.map(function(item) {
                          return App.I18n.message(String(item))
                      }).join("; "))
                : ""
        compressionConfirm.ask(
            qsTr("Compress changed files?"),
            qsTr("%1\nProfile: %2\nEstimated savings: %3\nOnly files included in the verified plan will be processed.")
                .arg(String(planValue(plan, ["gameName", "game_name"], gameName(update))))
                .arg(String(planValue(plan, ["profile"], profileFor(update))))
                .arg(savings) + warningText,
            qsTr("Start compression"),
            false,
            String(planValue(plan, ["planId", "plan_id"], "")))
    }

    function installationLabel(raw) {
        switch (String(raw || "").trim().toLowerCase()) {
        case "development": return qsTr("Development")
        case "flatpak": return qsTr("Flatpak")
        case "system package": return qsTr("System package")
        default: return String(raw || qsTr("Unknown"))
        }
    }

    function applicationUpdateMessage(data) {
        var key = String(value(data, ["messageKey", "message_key"], ""))
        if (key === "flatpak")
            return qsTr("Application updates are delivered through Flatpak.")
        if (key === "system")
            return qsTr("Application updates are delivered through the package manager.")
        if (key === "development")
            return qsTr("This development checkout does not update itself; use the repository workflow.")
        return qsTr("Application updates will be delivered later through Flatpak or the system package manager.")
    }

    onUpdatesDataChanged: synchronizeUpdates()
    Component.onCompleted: synchronizeUpdates()

    ListModel {
        id: stableUpdates
        dynamicRoles: true
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: App.Theme.contentPadding
        spacing: App.Theme.spacingMedium

        PageHeader {
            Layout.fillWidth: true
            title: qsTr("Updates")
            subtitle: qsTr("Local Steam changes, pending compression, and recent results")

            AppButton {
                text: qsTr("Clear finished")
                kind: "secondary"
                compact: true
                onClicked: {
                    if (page.controller && page.controller.clearFinishedUpdates) {
                        var removed = page.controller.clearFinishedUpdates()
                        if (removed > 0)
                            page.toastRequested(qsTr("Finished entries cleared"), "success")
                    }
                }
            }

            AppButton {
                text: qsTr("Clear unavailable")
                kind: "ghost"
                compact: true
                onClicked: {
                    if (page.controller && page.controller.clearUnavailableUpdates) {
                        var removed = page.controller.clearUnavailableUpdates()
                        if (removed > 0)
                            page.toastRequested(qsTr("Unavailable library entries cleared"), "success")
                    }
                }
            }
        }

        GridLayout {
            id: summaryGrid
            objectName: "updatesSummaryGrid"
            Layout.fillWidth: true
            columns: page.width >= 1000 ? 4 : 2
            rowSpacing: 10
            columnSpacing: 10

            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Need attention")
                value: String(page.numberValue(page.summaryData, [
                    "needsCheckCount", "needs_check_count", "updateCount", "update_count"
                ], 0))
                symbol: "!"
                tone: App.Theme.warning
            }
            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Queued")
                value: String(page.numberValue(page.summaryData, [
                    "pendingCount", "pending_count", "queuedCount", "queued_count"
                ], 0))
                symbol: "↻"
                tone: App.Theme.info
            }
            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Recently optimized")
                value: String(page.numberValue(page.summaryData, [
                    "recentlyOptimizedCount", "recently_optimized_count"
                ], 0))
                symbol: "✓"
                tone: App.Theme.success
            }
            MetricTile {
                Layout.fillWidth: true
                label: qsTr("Recently recovered")
                value: page.formatBytes(page.numberValue(page.summaryData, [
                    "recentRecoveredBytes", "recent_recovered_bytes", "recoveredBytes"
                ], 0))
                symbol: "↓"
                tone: App.Theme.accent
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180

            ListView {
                id: updatesList
                objectName: "updatesList"
                anchors.fill: parent
                visible: stableUpdates.count > 0
                clip: true
                reuseItems: true
                cacheBuffer: Math.max(height * 2, 900)
                spacing: 10
                model: stableUpdates
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                section.property: "sectionKey"
                section.criteria: ViewSection.FullString

                section.delegate: Item {
                    required property string section
                    width: ListView.view.width
                    height: 42

                    Label {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 8
                        text: page.sectionTitle(parent.section)
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                    }
                }

                delegate: SurfaceCard {
                    id: updateCard
                    objectName: "updatesCard"
                    required property var model
                    property var updateData: model.updateData || ({})
                    property string updateState: page.stateOf(updateData)
                    width: Math.max(0, ListView.view.width - 10)
                    height: implicitHeight
                    implicitHeight: Math.max(152, cardBody.implicitHeight + topPadding + bottomPadding)
                    padding: 14
                    clip: true

                    contentItem: RowLayout {
                        id: cardBody
                        spacing: 14

                        GameArtwork {
                            objectName: "updatesCardCover"
                            Layout.preferredWidth: 76
                            Layout.preferredHeight: 108
                            Layout.alignment: Qt.AlignTop
                            gameId: String(page.value(updateCard.updateData,
                                                      ["gameId", "game_id"], ""))
                            title: page.gameName(updateCard.updateData)
                            launcher: String(page.value(updateCard.updateData, ["launcher"], qsTr("Steam")))
                            artworkSource: page.value(updateCard.updateData, [
                                "effectiveArtworkUrl",
                                "portraitArtwork", "portrait_artwork", "cover",
                                "fallbackArtwork", "headerArtwork"
                            ], "")
                            artworkFillMode: Image.PreserveAspectCrop
                            cornerRadius: App.Theme.radiusSmall
                        }

                        ColumnLayout {
                            objectName: "updatesCardInformation"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            spacing: 6

                            Label {
                                Layout.fillWidth: true
                                text: page.gameName(updateCard.updateData)
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBodyLarge
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                                ToolTip.visible: titleHover.hovered && truncated
                                ToolTip.text: text
                                HoverHandler { id: titleHover }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Label {
                                    text: String(page.value(updateCard.updateData, ["launcher"], qsTr("Steam")))
                                    color: App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                }
                                Label {
                                    visible: updateCard.width >= 650
                                    Layout.fillWidth: true
                                    text: qsTr("Build ID: %1").arg(String(page.value(
                                        updateCard.updateData, ["buildId", "build_id"], qsTr("Unknown"))))
                                    color: App.Theme.textMuted
                                    font.pixelSize: App.Theme.fontCaption
                                    elide: Text.ElideRight
                                }
                                Item { Layout.fillWidth: updateCard.width < 650 }
                                Rectangle {
                                    Layout.preferredWidth: 24
                                    Layout.preferredHeight: 24
                                    radius: 8
                                    color: App.Theme.statusSurface(
                                               page.toneState(updateCard.updateState))

                                    Label {
                                        anchors.centerIn: parent
                                        text: page.stateSymbol(updateCard.updateState)
                                        color: App.Theme.statusColor(
                                                   page.toneState(updateCard.updateState))
                                        font.pixelSize: 12
                                        font.weight: Font.Bold
                                    }
                                }
                                StatusBadge {
                                    text: page.translatedState(updateCard.updateState)
                                    status: page.toneState(updateCard.updateState)
                                }
                                IconButton {
                                    visible: page.booleanValue(
                                                 updateCard.updateData,
                                                 ["canDismiss", "can_dismiss"],
                                                 false)
                                    symbol: "⌫"
                                    toolTip: qsTr("Remove this entry")
                                    onClicked: {
                                        var rowId = String(page.value(
                                            updateCard.updateData,
                                            ["rowId", "row_id"], ""))
                                        if (rowId.length > 0 && page.controller
                                                && page.controller.dismissUpdate)
                                            page.controller.dismissUpdate(rowId)
                                    }
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Detected %1 · %2 changed")
                                      .arg(page.formatDate(page.value(updateCard.updateData, [
                                          "detectedAt", "detected_at"
                                      ], "")))
                                      .arg(page.formatBytes(page.numberValue(updateCard.updateData, [
                                          "changedBytes", "changed_bytes"
                                      ], 0)))
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                elide: Text.ElideRight
                            }

                            Label {
                                Layout.fillWidth: true
                                visible: String(page.value(updateCard.updateData, ["error"], "")).length > 0
                                text: String(page.value(updateCard.updateData, ["error"], ""))
                                color: App.Theme.danger
                                font.pixelSize: App.Theme.fontCaption
                                elide: Text.ElideRight
                                ToolTip.visible: errorHover.hovered && truncated
                                ToolTip.text: text
                                HoverHandler { id: errorHover }
                            }

                            Flow {
                                objectName: "updatesCardActions"
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.max(36, childrenRect.height)
                                spacing: 8

                                AppButton {
                                    objectName: "analyzeChangesButton"
                                    compact: true
                                    text: qsTr("Analyze changes")
                                    iconText: "⌕"
                                    enabled: page.booleanValue(updateCard.updateData, [
                                        "canAnalyze", "can_analyze"
                                    ], false)
                                    onClicked: {
                                        var id = page.gameId(updateCard.updateData)
                                        if (id.length > 0 && page.controller && page.controller.analyzeChanges)
                                            page.controller.analyzeChanges(id)
                                    }
                                }
                                AppButton {
                                    objectName: "compressChangesButton"
                                    compact: true
                                    text: qsTr("Compress changes")
                                    iconText: "↓"
                                    kind: "primary"
                                    busy: ["compressing", "queued"].indexOf(
                                        updateCard.updateState.toLowerCase()) >= 0
                                    enabled: page.booleanValue(updateCard.updateData, [
                                        "canCompress", "can_compress"
                                    ], false)
                                    onClicked: page.prepareCompression(updateCard.updateData)
                                }
                                AppButton {
                                    objectName: "ignoreUpdateButton"
                                    compact: true
                                    text: qsTr("Ignore this update")
                                    kind: "ghost"
                                    enabled: page.booleanValue(updateCard.updateData, [
                                        "canIgnore", "can_ignore"
                                    ], false)
                                    onClicked: {
                                        var id = page.gameId(updateCard.updateData)
                                        if (id.length > 0 && page.controller && page.controller.ignoreUpdate)
                                            page.controller.ignoreUpdate(id)
                                    }
                                }
                                AppButton {
                                    objectName: "viewUpdateDetailsButton"
                                    compact: true
                                    text: qsTr("Details")
                                    kind: "ghost"
                                    enabled: page.gameId(updateCard.updateData).length > 0
                                    onClicked: {
                                        if (page.controller && page.controller.openGame)
                                            page.controller.openGame(page.gameId(updateCard.updateData))
                                    }
                                }
                            }
                        }
                    }
                }
            }

            EmptyState {
                id: emptyUpdates
                objectName: "updatesEmptyState"
                anchors.fill: parent
                visible: stableUpdates.count === 0
                symbol: "✓"
                title: qsTr("No game updates need attention")
                message: qsTr("Game changes and pending compression checks will appear here.")
            }
        }

        SurfaceCard {
            id: gameForgeSection
            objectName: "gameForgeUpdateSection"
            Layout.fillWidth: true
            padding: 15

            contentItem: RowLayout {
                spacing: 14

                Rectangle {
                    Layout.preferredWidth: 42
                    Layout.preferredHeight: 42
                    radius: 13
                    color: App.Theme.accentSoft
                    Label {
                        anchors.centerIn: parent
                        text: "GF"
                        color: App.Theme.accent
                        font.pixelSize: 13
                        font.weight: Font.Black
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: 2
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("GameForge")
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBodyLarge
                        font.weight: Font.Bold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Version %1 · %2").arg(String(page.value(
                            page.applicationData, ["version"],
                            page.controller && page.controller.appVersion
                            ? page.controller.appVersion : "-")))
                              .arg(page.installationLabel(page.value(
                                  page.applicationData,
                                  ["installationType", "installation_type"],
                                  "development")))
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        elide: Text.ElideRight
                    }
                    Label {
                        Layout.fillWidth: true
                        text: page.applicationUpdateMessage(page.applicationData)
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                        elide: Text.ElideRight
                        ToolTip.visible: applicationMessageHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: applicationMessageHover }
                    }
                }
            }
        }
    }

    ConfirmDialog {
        id: compressionConfirm
        objectName: "updatesCompressionConfirmDialog"
        onConfirmed: function(planId) {
            var normalizedId = String(planId || "")
            if (normalizedId.length > 0 && page.controller && page.controller.startCompression)
                page.controller.startCompression(normalizedId)
            page.pendingPlan = ({})
        }
        onClosed: page.pendingPlan = ({})
    }
}
