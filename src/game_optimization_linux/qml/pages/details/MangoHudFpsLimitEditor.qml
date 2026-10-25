import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

ColumnLayout {
    id: editor
    objectName: "centralMangoHudFpsEditor"
    property var controller
    property string gameId: ""
    property int refreshRevision: 0
    property var profile: ({})
    property int limit: 0
    property bool dirty: false
    property string error: ""
    readonly property bool canEdit: Boolean(profile.success && profile.available && profile.enabled && profile.fpsLimitOwner !== "gamescope")
    spacing: 6
    function refresh() {
        if (!controller || !controller.getMangoHudProfile || !gameId) return
        profile = controller.getMangoHudProfile(gameId) || ({})
        limit = Number(profile.fpsLimitPreference || profile.fpsLimit || 0)
        dirty = false
    }
    function save() {
        if (!canEdit || !controller || !controller.saveMangoHudProfile) return
        // Partial payload preserves all HUD appearance, metrics and logging settings.
        var result = controller.saveMangoHudProfile(gameId, {"fpsLimit": limit})
        error = result && result.success ? "" : App.I18n.message(String(result && result.error || ""))
        if (result && result.success) refresh()
    }
    Label {
        Layout.fillWidth: true
        text: qsTr("FPS limit · MangoHud")
        color: App.Theme.text
        font.weight: Font.DemiBold
    }
    Label {
        Layout.fillWidth: true
        text: editor.profile.fpsLimitOwner === "gamescope"
              ? qsTr("Apply the Gamescope change first to edit the MangoHud limiter.")
              : editor.canEdit ? qsTr("0 disables the limiter. This setting is saved separately from the optimization profile.")
              : qsTr("Enable MangoHud for this game to use its limiter. The target above does not limit FPS on its own.")
        color: App.Theme.textSecondary
        wrapMode: Text.WordWrap
    }
    RowLayout {
        AppTextField {
            objectName: "centralMangoHudFpsLimit"
            Layout.preferredWidth: 100
            enabled: editor.canEdit
            text: String(editor.limit)
            validator: IntValidator { bottom: 0; top: 1000 }
            onTextEdited: { if (acceptableInput) { editor.limit = Number(text); editor.dirty = true } }
        }
        AppButton {
            objectName: "saveCentralMangoHudFpsLimit"
            text: qsTr("Save FPS limit")
            enabled: editor.canEdit && editor.dirty
            onClicked: editor.save()
        }
    }
    Label { Layout.fillWidth: true; visible: editor.error.length > 0; text: editor.error; color: App.Theme.danger; wrapMode: Text.WordWrap }
    Timer { id: refreshTimer; interval: 35; onTriggered: editor.refresh() }
    onGameIdChanged: refreshTimer.restart()
    onRefreshRevisionChanged: refreshTimer.restart()
    onVisibleChanged: if (visible) refreshTimer.restart()
    Component.onCompleted: refreshTimer.restart()
    Connections {
        target: editor.controller || null
        ignoreUnknownSignals: true
        function onMangoHudProfileChanged() { refreshTimer.restart() }
    }
}
