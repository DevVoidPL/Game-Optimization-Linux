pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

// Per-game custom launch: environment, arguments, wrapper, before launch, after exit.
// The draft lives only here; Reset changes the draft, Apply saves (and, for
// Heroic/Lutris after confirmation, writes the launcher in one transaction).
SurfaceCard {
    id: section
    property var controller
    property var gameData: ({})
    property bool couchMode: false
    property bool collapsible: false
    property bool expanded: !collapsible
    readonly property bool detailsOpen: !collapsible || expanded
    readonly property string gameId: String(gameData && gameData.id || "")
    property var information: ({})
    property var draft: emptyDraft()
    property var previewData: ({})
    property bool dirty: false
    property bool technicalOpen: false
    property bool confirmOpen: false
    property string errorMessage: ""
    property string statusMessage: ""
    property int padIndex: 0
    property var padFocusedItem: null
    readonly property int bodySize: couchMode ? 17 : 13
    readonly property int captionSize: couchMode ? 14 : 12
    readonly property var resolved: dirty ? (previewData.draftResolved || ({})) : (information.resolved || ({}))
    readonly property var support: resolved.support || (information.resolved || ({})).support || ({})
    readonly property var errors: dirty ? (previewData.errors || []) : []
    readonly property bool launcherWrite: Boolean(information.launcherConfigWrite)
    signal textInputRequested(var field, string heading)
    padding: couchMode ? 18 : 14

    function emptyDraft() { return {"environment": [], "arguments": [], "wrapper": null, "preLaunch": [], "postExit": []} }
    function clone(value) { return JSON.parse(JSON.stringify(value)) }
    function draftFrom(profile) {
        var value = emptyDraft()
        if (!profile) return value
        value.environment = clone(profile.environment || [])
        value.arguments = clone(profile.arguments || [])
        value.wrapper = profile.wrapper ? clone(profile.wrapper) : null
        value.preLaunch = clone(profile.preLaunch || [])
        value.postExit = clone(profile.postExit || [])
        return value
    }
    function load(force) {
        if (!controller || !controller.getCustomLaunch || !gameId) return
        var result = controller.getCustomLaunch(gameId) || ({})
        if (!result.success) { errorMessage = App.I18n.message(String(result.error || "")) || qsTr("Custom launch settings are unavailable"); return }
        information = result
        // A status refresh never replaces unsaved edits.
        if (force || !dirty) { draft = draftFrom(result.profile); dirty = false; previewData = ({}) }
        else schedulePreview()
        errorMessage = ""
    }
    function change(mutator) {
        var next = clone(draft)
        mutator(next)
        draft = next
        dirty = true
        statusMessage = ""
        schedulePreview()
    }
    property int revision: 0
    // Text, switches and timeouts change the draft in place so the edited row
    // keeps focus; only adding or removing rows rebuilds the lists.
    function edit(mutator) {
        mutator(draft)
        revision++
        dirty = true
        statusMessage = ""
        schedulePreview()
    }
    function schedulePreview() { previewTimer.restart() }
    function preview() {
        if (!controller || !controller.previewCustomLaunch || !gameId) return
        var result = controller.previewCustomLaunch(gameId, draft) || ({})
        if (result.success) { previewData = result; errorMessage = "" }
        else errorMessage = App.I18n.message(String(result.error || "")) || qsTr("Could not preview changes")
    }
    function resetDraft() { draft = draftFrom(information.profile); dirty = false; previewData = ({}); confirmOpen = false; statusMessage = "" }
    function apply(confirmed) {
        if (!controller || !controller.saveCustomLaunch) return
        preview()
        if (errors.length) { errorMessage = qsTr("Fix the marked fields before applying"); return }
        if (launcherWrite && !confirmed) { confirmOpen = true; return }
        confirmOpen = false
        var result = controller.saveCustomLaunch(gameId, draft, Boolean(confirmed)) || ({})
        if (result.success) {
            information = result; draft = draftFrom(result.profile); dirty = false; previewData = ({})
            errorMessage = ""; statusMessage = qsTr("Saved. The next launch uses these settings.")
        } else {
            errorMessage = (result.rolledBack ? qsTr("Nothing was changed: ") : "") + String(result.error || qsTr("Could not apply custom launch settings"))
        }
    }
    function errorFor(sectionName, index) {
        for (var i = 0; i < errors.length; i++)
            if (errors[i].section === sectionName && errors[i].index === index) return errorText(String(errors[i].message))
        return ""
    }
    function errorText(message) {
        if (message.indexOf("Invalid environment variable name") >= 0) return qsTr("Use letters, digits and _; the name cannot start with a digit")
        if (message.indexOf("NUL") >= 0) return qsTr("NUL characters are not allowed")
        if (message.indexOf("listed twice") >= 0) return qsTr("This variable is listed twice")
        if (message.indexOf("privileged programs") >= 0) return qsTr("sudo, pkexec, doas, su and run0 are not allowed")
        if (message.indexOf("shell interpreters") >= 0) return qsTr("Shells are not accepted; point GameOpti at your own script file instead")
        if (message.indexOf("absolute path") >= 0) return qsTr("Use an absolute path or a program name from PATH")
        if (message.indexOf("the executable is empty") >= 0) return qsTr("Enter the program to run")
        if (message.indexOf("timeout") >= 0) return qsTr("Timeout must be 1-600 seconds")
        if (message.indexOf("too long") >= 0) return qsTr("The value is too long")
        if (message.indexOf("whitespace") >= 0) return qsTr("The program name cannot contain tabs or line breaks")
        return message
    }
    function whereText(code) {
        var labels = {
            "game_environment": qsTr("Game environment"),
            "game_command": qsTr("Game command line"),
            "host_runner": qsTr("GameOpti runner on the host"),
            "host_gameopti": qsTr("Host, started by GameOpti"),
            "launcher_config": qsTr("Written to the launcher configuration"),
            "launcher_host": qsTr("Launcher wrapper on the host"),
            "launcher_sandbox": qsTr("Inside the launcher Flatpak sandbox"),
            "unsupported": qsTr("Unavailable")
        }
        return labels[code] || code
    }
    function reasonText(row) {
        var code = String(row && row.code || "")
        if (code === "steam_flatpak") return qsTr("Steam Flatpak runs Launch Options inside its sandbox, which cannot reach the GameOpti host runner. Not verified: Steam Flatpak is not installed here.")
        if (code === "local_windows_no_runner") return qsTr("No Proton runner is configured for this Local Windows executable. Add the game as a Custom game with a Wine/Proton runner.")
        if (code === "launcher_config_unavailable") return qsTr("The launcher's game configuration is missing or cannot be read: %1").arg(String(row.detail || ""))
        if (code === "heroic_args_legendary_only") return qsTr("Heroic passes custom game arguments only for Epic (legendary) games in the verified version 2.22.4.")
        if (code === "lutris_args_runner") return qsTr("Lutris passes game arguments only for the wine and linux runners (this game: %1).").arg(String(row.detail || qsTr("unknown")))
        if (code === "hooks_heroic_umu") return qsTr("Heroic Flatpak starts Proton through UMU in a separate container outside the wrapper, so the end of the game cannot be awaited. Available when UMU is off for this game.")
        if (code === "hooks_lutris_flatpak") return qsTr("Lutris Flatpak is not installed here, so its sandbox and process tree were not verified.")
        if (code === "local_flatpak_host_runner") return qsTr("The host runner of the GameOpti Flatpak accepts only Steam AppIDs, so Local games cannot start through it yet.")
        if (code === "not_supported") return qsTr("Custom launch settings are not available for this game.")
        return String(row && row.reason || "")
    }
    function featureLine(name) {
        var row = support[name] || ({})
        if (!row.state) return ""
        return row.state === "supported" ? qsTr("Runs: %1").arg(whereText(row.where)) : qsTr("Unavailable: %1").arg(reasonText(row))
    }
    function stateText(state) {
        return state === "applied" ? qsTr("applied") : state === "conflict" ? qsTr("not applied: already set by GameOpti or the launcher") : qsTr("disabled")
    }
    function quoted(list) { return (list || []).map(function(item) { return JSON.stringify(String(item)) }).join(" ") }
    function summaryText() {
        var parts = []
        void revision
        var env = (draft.environment || []).filter(function(e) { return e.enabled !== false }).length
        var args = (draft.arguments || []).filter(function(e) { return e.enabled !== false }).length
        if (env) parts.push(qsTr("Variables: %1").arg(env))
        if (args) parts.push(qsTr("Arguments: %1").arg(args))
        if (draft.wrapper && draft.wrapper.enabled !== false) parts.push(qsTr("Wrapper"))
        var pre = (draft.preLaunch || []).filter(function(e) { return e.enabled !== false }).length
        var post = (draft.postExit || []).filter(function(e) { return e.enabled !== false }).length
        if (pre) parts.push(qsTr("Before launch: %1").arg(pre))
        if (post) parts.push(qsTr("After exit: %1").arg(post))
        return parts.length ? parts.join(" · ") : qsTr("No custom launch settings")
    }
    function savedText() {
        var at = String(information.savedAt || "")
        if (!at) return qsTr("Saved profile: none")
        return qsTr("Saved profile: %1").arg(new Date(at).toLocaleString(Qt.locale(), Locale.ShortFormat))
    }
    function commitPadText(item, value) { if (item && item.commitText) item.commitText(String(value)) }
    function controls(item, result) {
        if (!item || !item.visible || !item.enabled) return
        if (item.padAction !== undefined) result.push(item)
        var children = item.children || []
        for (var i = 0; i < children.length; i++) controls(children[i], result)
    }
    function handlePadAction(action) {
        if (action === "Back") {
            if (confirmOpen) { confirmOpen = false; return true }
            return false
        }
        var list = []; controls(section, list)
        if (!list.length) return false
        var focused = list.findIndex(function(item) { return item.activeFocus })
        if (focused >= 0) padIndex = focused
        if (action === "NavigateDown" || action === "NavigateUp")
            padIndex = Math.max(0, Math.min(list.length - 1, padIndex + (action === "NavigateDown" ? 1 : -1)))
        padIndex = Math.min(padIndex, list.length - 1)
        // Tab reason: buttons draw their focus border only for keyboard-like focus.
        var item = list[padIndex]; padFocusedItem = item; item.forceActiveFocus(Qt.TabFocusReason)
        if (item.padAction === "spin" && (action === "NavigateLeft" || action === "NavigateRight")) {
            item.value = item.value + (action === "NavigateLeft" ? -item.stepSize : item.stepSize)
            item.valueModified()
        } else if (item.padAction === "toggle" && action === "Confirm") {
            item.toggle(); item.toggled()
        } else if (item.padAction === "text" && action === "Confirm") {
            textInputRequested(item, item.Accessible.name || qsTr("Edit value"))
        } else if (item.padAction === "button" && action === "Confirm") {
            var pressed = item
            item.clicked()
            // Adding/removing rows rebuilds them: keep focus on the same button,
            // or on the same position when that button was removed with its row.
            Qt.callLater(function() {
                if (!section.visible) return
                var again = []; controls(section, again)
                if (!again.length) return
                var same = again.indexOf(pressed)
                padIndex = same >= 0 ? same : Math.min(padIndex, again.length - 1)
                padFocusedItem = again[padIndex]; padFocusedItem.forceActiveFocus(Qt.TabFocusReason)
            })
        }
        return true
    }

    Timer { id: previewTimer; interval: 250; repeat: false; onTriggered: section.preview() }

    component FieldError: Label {
        property string message: ""
        Layout.fillWidth: true; visible: message.length > 0; text: message
        color: App.Theme.danger; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap
    }
    component EditField: AppTextField {
        property string padAction: "text"
        property var onCommit: null
        function commitText(value) { text = value; if (onCommit) onCommit(value) }
        Layout.fillWidth: true; Layout.minimumWidth: 60
        font.pixelSize: section.bodySize
        onTextEdited: if (onCommit) onCommit(text)
    }
    component PadButton: AppButton {
        property string padAction: "button"
        compact: true; textPixelSize: section.bodySize
    }
    component FocusRing: Rectangle {
        anchors.fill: parent; anchors.margins: -3; radius: 6; color: "transparent"
        border.width: 2; border.color: App.Theme.accent
    }
    component PadSwitch: AppSwitch {
        id: padSwitch
        property string padAction: "toggle"
        textPixelSize: section.bodySize
        FocusRing { visible: padSwitch.activeFocus }
    }
    component SectionTitle: Label {
        Layout.fillWidth: true; color: App.Theme.text; font.bold: true
        font.pixelSize: section.couchMode ? 20 : 15; wrapMode: Text.WordWrap
    }
    component SupportLine: Label {
        property string feature: ""
        Layout.fillWidth: true; text: section.featureLine(feature); visible: text.length > 0
        color: ((section.support[feature] || {}).state === "supported") ? App.Theme.textMuted : App.Theme.warning
        font.pixelSize: section.captionSize; wrapMode: Text.WordWrap
    }
    // One command: program plus separate argument fields (boundaries are kept as typed).
    component CommandEditor: ColumnLayout {
        id: commandEditor
        property string sectionName: ""
        property int rowIndex: 0
        property var entry: ({})
        property bool pre: sectionName === "preLaunch"
        property bool wrapper: sectionName === "wrapper"
        readonly property var argv: entry.argv || [""]
        Layout.fillWidth: true; spacing: 6
        function target(next) { return wrapper ? next.wrapper : next[sectionName][rowIndex] }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            PadSwitch {
                checked: commandEditor.entry.enabled !== false
                Accessible.name: qsTr("Enabled")
                onToggled: { var on = checked; section.edit(function(next) { commandEditor.target(next).enabled = on }) }
            }
            EditField {
                objectName: "customProgram-" + commandEditor.sectionName + "-" + commandEditor.rowIndex
                text: String(commandEditor.argv[0] || "")
                placeholderText: qsTr("Program (absolute path or name in PATH)")
                Accessible.name: qsTr("Program")
                onCommit: function(value) { section.edit(function(next) { var t = commandEditor.target(next); t.argv = (t.argv && t.argv.length ? t.argv : [""]); t.argv[0] = value }) }
            }
            PadButton {
                text: commandEditor.wrapper ? qsTr("Remove wrapper") : qsTr("Remove"); kind: "ghost"
                onClicked: section.change(function(next) { if (commandEditor.wrapper) next.wrapper = null; else next[commandEditor.sectionName].splice(commandEditor.rowIndex, 1) })
            }
        }
        Repeater {
            model: Math.max(0, commandEditor.argv.length - 1)
            RowLayout {
                id: argumentRow
                required property int index
                Layout.fillWidth: true; Layout.leftMargin: section.couchMode ? 40 : 30; spacing: 8
                Label { text: qsTr("Argument %1").arg(argumentRow.index + 1); color: App.Theme.textMuted; font.pixelSize: section.captionSize }
                EditField {
                    text: String(commandEditor.argv[argumentRow.index + 1])
                    Accessible.name: qsTr("Argument %1").arg(argumentRow.index + 1)
                    onCommit: function(value) { section.edit(function(next) { commandEditor.target(next).argv[argumentRow.index + 1] = value }) }
                }
                PadButton { text: "×"; kind: "ghost"; Accessible.name: qsTr("Remove argument"); onClicked: section.change(function(next) { commandEditor.target(next).argv.splice(argumentRow.index + 1, 1) }) }
            }
        }
        Flow {
            Layout.fillWidth: true; Layout.leftMargin: section.couchMode ? 40 : 30; spacing: 8
            PadButton { text: qsTr("Add argument"); kind: "secondary"; onClicked: section.change(function(next) { var t = commandEditor.target(next); t.argv = (t.argv && t.argv.length ? t.argv : [""]); t.argv.push("") }) }
            Label { visible: !commandEditor.wrapper; text: qsTr("Timeout (s)"); color: App.Theme.textSecondary; font.pixelSize: section.captionSize }
            SpinBox {
                id: timeoutBox
                property string padAction: "spin"
                FocusRing { visible: timeoutBox.activeFocus }
                visible: !commandEditor.wrapper
                from: 1; to: 600; stepSize: 5; editable: true
                value: Number(commandEditor.entry.timeout || 30)
                Accessible.name: qsTr("Timeout (s)")
                onValueModified: { var seconds = value; section.edit(function(next) { commandEditor.target(next).timeout = seconds }) }
            }
            PadSwitch {
                visible: commandEditor.pre
                text: qsTr("Continue if it fails")
                checked: commandEditor.entry.onFailure === "continue"
                onToggled: { var on = checked; section.edit(function(next) { commandEditor.target(next).onFailure = on ? "continue" : "stop" }) }
            }
            PadSwitch {
                visible: commandEditor.sectionName === "postExit"
                text: qsTr("Also run if the game did not start")
                checked: commandEditor.entry.runIfNotStarted === true
                onToggled: { var on = checked; section.edit(function(next) { commandEditor.target(next).runIfNotStarted = on }) }
            }
        }
        FieldError { message: section.errorFor(commandEditor.sectionName, commandEditor.wrapper ? 0 : commandEditor.rowIndex) }
    }

    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: qsTr("Custom launch"); color: App.Theme.text; font.pixelSize: section.couchMode ? 25 : 20; font.bold: true; wrapMode: Text.WordWrap }
            StatusBadge { objectName: "customLaunchState"; text: section.dirty ? qsTr("Unsaved changes") : qsTr("Saved settings"); status: section.dirty ? "warning" : "neutral" }
        }
        PadButton {
            objectName: "customLaunchToggle"; visible: section.collapsible; Layout.fillWidth: true; compact: false
            kind: "secondary"; text: (section.detailsOpen ? "▾ " : "▸ ") + qsTr("Environment, arguments, wrapper and commands")
            onClicked: section.expanded = !section.expanded
        }
        ColumnLayout {
            objectName: "customLaunchBody"; visible: section.detailsOpen; Layout.fillWidth: true; spacing: 12
            Label { objectName: "customLaunchSummary"; Layout.fillWidth: true; text: section.summaryText(); color: App.Theme.text; font.pixelSize: section.bodySize; wrapMode: Text.WordWrap }
            Label { Layout.fillWidth: true; text: section.savedText() + (section.dirty ? " · " + qsTr("The editor shows an unsaved draft") : ""); color: App.Theme.textMuted; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
            Label {
                objectName: "customLaunchSecurity"; Layout.fillWidth: true
                text: qsTr("Commands run as your user. GameOpti never parses them as shell text and refuses sudo/pkexec, but a script you choose can do anything your account can. This is trusted command execution, not a sandbox. Preview and status never run a command.")
                color: App.Theme.textSecondary; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap
            }

            // Environment
            SurfaceCard { Layout.fillWidth: true; padding: 12
                contentItem: ColumnLayout { spacing: 8
                    SectionTitle { text: qsTr("Environment") }
                    SupportLine { feature: "environment" }
                    Repeater {
                        model: section.draft.environment || []
                        ColumnLayout {
                            id: envRow
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true; spacing: 4
                            GridLayout {
                                Layout.fillWidth: true; columns: section.width < 620 ? 2 : 4; columnSpacing: 8; rowSpacing: 6
                                PadSwitch { checked: envRow.modelData.enabled !== false; Accessible.name: qsTr("Enabled"); onToggled: { var on = checked; section.edit(function(next) { next.environment[envRow.index].enabled = on }) } }
                                EditField { objectName: "customEnvKey-" + envRow.index; text: String(envRow.modelData.key || ""); placeholderText: qsTr("NAME"); Accessible.name: qsTr("Variable name"); onCommit: function(value) { section.edit(function(next) { next.environment[envRow.index].key = value }) } }
                                EditField { objectName: "customEnvValue-" + envRow.index; text: String(envRow.modelData.value || ""); placeholderText: qsTr("value"); Accessible.name: qsTr("Variable value"); onCommit: function(value) { section.edit(function(next) { next.environment[envRow.index].value = value }) } }
                                PadButton { text: qsTr("Remove"); kind: "ghost"; onClicked: section.change(function(next) { next.environment.splice(envRow.index, 1) }) }
                            }
                            FieldError { message: section.errorFor("environment", envRow.index) }
                            Label {
                                readonly property var row: (section.resolved.environment || [])[envRow.index] || ({})
                                Layout.fillWidth: true; visible: row.state !== undefined && section.errorFor("environment", envRow.index) === ""
                                text: section.stateText(row.state); color: row.state === "conflict" ? App.Theme.warning : App.Theme.textMuted
                                font.pixelSize: section.captionSize; wrapMode: Text.WordWrap
                            }
                        }
                    }
                    PadButton { objectName: "customAddEnv"; text: qsTr("Add variable"); kind: "secondary"; onClicked: section.change(function(next) { next.environment.push({"key": "", "value": "", "enabled": true}) }) }
                }
            }

            // Arguments
            SurfaceCard { Layout.fillWidth: true; padding: 12
                contentItem: ColumnLayout { spacing: 8
                    SectionTitle { text: qsTr("Arguments") }
                    Label { Layout.fillWidth: true; text: qsTr("Each field is one argument, passed exactly as typed after the game's own arguments. Quotes, $ and ; are ordinary characters."); color: App.Theme.textSecondary; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
                    SupportLine { feature: "arguments" }
                    Repeater {
                        model: section.draft.arguments || []
                        ColumnLayout {
                            id: argRow
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true; spacing: 4
                            RowLayout { Layout.fillWidth: true; spacing: 8
                                PadSwitch { checked: argRow.modelData.enabled !== false; Accessible.name: qsTr("Enabled"); onToggled: { var on = checked; section.edit(function(next) { next.arguments[argRow.index].enabled = on }) } }
                                EditField { objectName: "customArg-" + argRow.index; text: String(argRow.modelData.value || ""); Accessible.name: qsTr("Argument %1").arg(argRow.index + 1); onCommit: function(value) { section.edit(function(next) { next.arguments[argRow.index].value = value }) } }
                                PadButton { text: qsTr("Remove"); kind: "ghost"; onClicked: section.change(function(next) { next.arguments.splice(argRow.index, 1) }) }
                            }
                            FieldError { message: section.errorFor("arguments", argRow.index) }
                        }
                    }
                    PadButton { objectName: "customAddArg"; text: qsTr("Add argument"); kind: "secondary"; onClicked: section.change(function(next) { next.arguments.push({"value": "", "enabled": true}) }) }
                }
            }

            // Wrapper
            SurfaceCard { Layout.fillWidth: true; padding: 12
                contentItem: ColumnLayout { spacing: 8
                    SectionTitle { text: qsTr("Wrapper") }
                    Label { Layout.fillWidth: true; text: qsTr("Runs the game through your program. Order: Gamescope → GameMode → your wrapper → frame generation → game. Gamescope, GameMode, MangoHud and MAKO are set in their own sections and are refused here."); color: App.Theme.textSecondary; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
                    SupportLine { feature: "wrapper" }
                    CommandEditor { visible: section.draft.wrapper !== null && section.draft.wrapper !== undefined; sectionName: "wrapper"; entry: section.draft.wrapper || ({}) }
                    PadButton { objectName: "customAddWrapper"; visible: !section.draft.wrapper; text: qsTr("Add wrapper"); kind: "secondary"; onClicked: section.change(function(next) { next.wrapper = {"argv": [""], "enabled": true} }) }
                }
            }

            // Before launch / After exit
            Repeater {
                model: [
                    {"name": "preLaunch", "title": qsTr("Before launch"), "help": qsTr("Runs before the game starts and waits until it ends or times out. By default a failure stops the launch.")},
                    {"name": "postExit", "title": qsTr("After exit"), "help": qsTr("Runs after every game process has ended. A failure is reported and never changes the game's exit status. It does not run if the game never started, unless you allow it.")}
                ]
                SurfaceCard {
                    id: hookCard
                    required property var modelData
                    Layout.fillWidth: true; padding: 12
                    contentItem: ColumnLayout { spacing: 8
                        SectionTitle { text: hookCard.modelData.title }
                        Label { Layout.fillWidth: true; text: hookCard.modelData.help; color: App.Theme.textSecondary; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
                        SupportLine { feature: hookCard.modelData.name }
                        Repeater {
                            model: section.draft[hookCard.modelData.name] || []
                            CommandEditor {
                                required property var modelData
                                required property int index
                                sectionName: hookCard.modelData.name; rowIndex: index; entry: modelData
                            }
                        }
                        PadButton {
                            objectName: "customAdd-" + hookCard.modelData.name; text: qsTr("Add command"); kind: "secondary"
                            onClicked: section.change(function(next) {
                                next[hookCard.modelData.name].push(hookCard.modelData.name === "preLaunch"
                                    ? {"argv": [""], "enabled": true, "timeout": 30, "onFailure": "stop"}
                                    : {"argv": [""], "enabled": true, "timeout": 30, "runIfNotStarted": false})
                            })
                        }
                    }
                }
            }

            // Short resolved summary; full detail on request.
            SurfaceCard { Layout.fillWidth: true; padding: 12
                contentItem: ColumnLayout { spacing: 6
                    Label { Layout.fillWidth: true; text: section.dirty ? qsTr("Next launch with this draft") : qsTr("Next launch with the saved profile"); color: App.Theme.text; font.bold: true; font.pixelSize: section.bodySize; wrapMode: Text.WordWrap }
                    Label { objectName: "customLaunchPreviewBlocked"; Layout.fillWidth: true; visible: section.errors.length > 0; text: qsTr("Fix the marked fields to see how the draft resolves."); color: App.Theme.warning; font.pixelSize: section.bodySize; wrapMode: Text.WordWrap }
                    Repeater { model: section.resolved.conflicts || []
                        Label { required property var modelData; Layout.fillWidth: true; text: modelData.code === "env_managed" ? qsTr("%1 is already set by GameOpti or the launcher; your value is not applied.").arg(modelData.key) : qsTr("Launcher conflict: %1").arg(String(modelData.reason)); color: App.Theme.warning; font.pixelSize: section.bodySize; wrapMode: Text.WordWrap }
                    }
                    Repeater { model: section.resolved.unsupported || []
                        Label { required property var modelData; Layout.fillWidth: true; text: qsTr("Not applied: %1").arg(section.reasonText(modelData)); color: App.Theme.warning; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
                    }
                    Label { Layout.fillWidth: true; visible: section.resolved.support !== undefined && !(section.resolved.conflicts || []).length && !(section.resolved.unsupported || []).length; text: qsTr("No conflicts"); color: App.Theme.textMuted; font.pixelSize: section.captionSize }
                    PadButton { objectName: "customTechnicalToggle"; kind: "ghost"; text: (section.technicalOpen ? "▾ " : "▸ ") + qsTr("Technical details"); onClicked: section.technicalOpen = !section.technicalOpen }
                    ColumnLayout {
                        visible: section.technicalOpen; Layout.fillWidth: true; spacing: 6
                        Label { Layout.fillWidth: true; text: qsTr("Exact argv (one quoted value per argument):"); color: App.Theme.textSecondary; font.pixelSize: section.captionSize }
                        TextEdit { objectName: "customArgv"; Layout.fillWidth: true; readOnly: true; selectByMouse: true; text: section.quoted(section.resolved.argv); color: App.Theme.text; wrapMode: TextEdit.WrapAnywhere; font.family: "monospace"; font.pixelSize: section.captionSize }
                        Label { Layout.fillWidth: true; visible: (section.resolved.wrappers || []).length > 0; text: qsTr("Wrapper order: %1").arg((section.resolved.wrappers || []).join(" → ")); color: App.Theme.textMuted; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
                        Repeater { model: section.resolved.environment || []
                            Label { required property var modelData; Layout.fillWidth: true; text: modelData.key + "=" + modelData.value + " · " + section.stateText(modelData.state); color: App.Theme.textMuted; font.family: "monospace"; font.pixelSize: section.captionSize; wrapMode: Text.WrapAnywhere }
                        }
                        Repeater { model: (section.resolved.pre || []).concat(section.resolved.post || [])
                            Label { required property var modelData; Layout.fillWidth: true; text: section.quoted(modelData.argv) + " · " + modelData.timeout + " s · " + section.whereText(modelData.where); color: App.Theme.textMuted; font.family: "monospace"; font.pixelSize: section.captionSize; wrapMode: Text.WrapAnywhere }
                        }
                        Label { Layout.fillWidth: true; visible: section.launcherWrite; text: qsTr("Written to the launcher: wrapper %1").arg(String((section.resolved.launcherWrites || {}).wrapper || "-")); color: App.Theme.textMuted; font.family: "monospace"; font.pixelSize: section.captionSize; wrapMode: Text.WrapAnywhere }
                        Label { Layout.fillWidth: true; visible: (section.resolved.inherited || []).length > 0; text: qsTr("Preserved inherited variables: %1").arg((section.resolved.inherited || []).join(", ")); color: App.Theme.textMuted; font.pixelSize: section.captionSize; wrapMode: Text.WrapAnywhere }
                        Label { Layout.fillWidth: true; text: qsTr("Profile file: %1").arg(String(section.information.profilePath || "-")); color: App.Theme.textMuted; font.pixelSize: section.captionSize; wrapMode: Text.WrapAnywhere }
                        TextEdit { id: technicalText; objectName: "customTechnicalText"; Layout.fillWidth: true; readOnly: true; selectByMouse: true; text: String(section.resolved.technicalText || ""); color: App.Theme.textMuted; wrapMode: TextEdit.WrapAnywhere; font.family: "monospace"; font.pixelSize: section.captionSize }
                        PadButton { text: qsTr("Copy diagnostics"); kind: "secondary"; onClicked: { technicalText.selectAll(); technicalText.copy(); technicalText.deselect() } }
                    }
                }
            }

            SurfaceCard {
                objectName: "customLaunchConfirm"; Layout.fillWidth: true; visible: section.confirmOpen; padding: 12
                contentItem: ColumnLayout { spacing: 8
                    Label { Layout.fillWidth: true; text: qsTr("Write the %1 game configuration?").arg(String(section.information.launcher || "")); color: App.Theme.text; font.bold: true; font.pixelSize: section.bodySize; wrapMode: Text.WordWrap }
                    Label { Layout.fillWidth: true; text: qsTr("GameOpti saves this profile and updates the launcher's settings for this game. Close the launcher first. Restore in Launch integration removes only GameOpti's entries."); color: App.Theme.textSecondary; font.pixelSize: section.captionSize; wrapMode: Text.WordWrap }
                    Flow { Layout.fillWidth: true; spacing: 8
                        PadButton { text: qsTr("Cancel"); kind: "ghost"; onClicked: section.confirmOpen = false }
                        PadButton { objectName: "customLaunchConfirmApply"; text: qsTr("Write and apply"); kind: "primary"; onClicked: section.apply(true) }
                    }
                }
            }
            Label { Layout.fillWidth: true; visible: section.errorMessage.length > 0; text: section.errorMessage; color: App.Theme.danger; wrapMode: Text.WordWrap; font.pixelSize: section.bodySize }
            Label { Layout.fillWidth: true; visible: section.statusMessage.length > 0; text: section.statusMessage; color: App.Theme.success; wrapMode: Text.WordWrap; font.pixelSize: section.bodySize }
            Flow { Layout.fillWidth: true; spacing: 8
                PadButton { objectName: "customLaunchReset"; text: qsTr("Reset changes"); kind: "ghost"; enabled: section.dirty; onClicked: section.resetDraft() }
                PadButton { objectName: "customLaunchRefresh"; text: qsTr("Refresh"); kind: "secondary"; onClicked: section.load(false) }
                PadButton { objectName: "customLaunchApply"; text: qsTr("Apply"); kind: "primary"; enabled: section.dirty && !section.errors.length; onClicked: section.apply(false) }
            }
        }
    }
    onGameIdChanged: { dirty = false; previewData = ({}); information = ({}); draft = emptyDraft(); confirmOpen = false; technicalOpen = false; statusMessage = ""; load(true) }
    Component.onCompleted: load(true)
}
