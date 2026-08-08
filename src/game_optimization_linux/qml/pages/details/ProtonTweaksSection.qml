pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

SurfaceCard {
    id: section

    property var controller
    property var gameData: ({})
    readonly property string gameId: String(gameData && gameData.id || "")
    readonly property string steamAppId: String(gameData && gameData.steamAppId || "")
    property var entries: []
    property var toggles: ({})
    property var environment: ({})
    property bool optiscalerFsr4Update: false
    property bool dirty: false
    property string errorMessage: ""

    padding: 18

    function labelFor(id, fallback) {
        if (id === "use_wined3d") return qsTr("Use WineD3D")
        if (id === "proton_log") return qsTr("Proton logging")
        if (id === "no_esync") return qsTr("Disable esync")
        if (id === "no_fsync") return qsTr("Disable fsync")
        if (id === "disable_nvapi") return qsTr("Disable NVAPI")
        if (id === "hide_nvidia_gpu") return qsTr("Hide NVIDIA GPU")
        if (id === "large_address_aware") return qsTr("Force large-address awareness")
        if (id === "old_gl_string") return qsTr("Use old OpenGL string")
        if (id === "steam_deck_spoof") return qsTr("Steam Deck spoof")
        if (id === "fsr4_upgrade") return qsTr("FSR 4 upgrade")
        if (id === "rdna3_wmma_workaround") return qsTr("RDNA3 WMMA workaround")
        return fallback
    }

    function descriptionFor(id, fallback) {
        if (id === "use_wined3d") return qsTr("OpenGL compatibility fallback instead of DXVK. This is not an automatic performance boost.")
        if (id === "proton_log") return qsTr("Writes a Proton debug log for the next game session.")
        if (id === "no_esync") return qsTr("Compatibility and debugging option that disables esync.")
        if (id === "no_fsync") return qsTr("Compatibility and debugging option that disables fsync.")
        if (id === "disable_nvapi") return qsTr("Disables Proton NVAPI integration for compatibility testing.")
        if (id === "hide_nvidia_gpu") return qsTr("Hides the NVIDIA GPU identity from the Windows game.")
        if (id === "large_address_aware") return qsTr("Enables Proton's large-address-aware compatibility option.")
        if (id === "old_gl_string") return qsTr("Uses Proton's older OpenGL version string for compatibility.")
        if (id === "steam_deck_spoof") return qsTr("Exposes SteamDeck=1. This is not an official Proton variable.")
        if (id === "fsr4_upgrade") return qsTr("Experimental and hardware-dependent Proton FSR 4 upgrade request.")
        if (id === "rdna3_wmma_workaround") return qsTr("Enable only after independently confirming RDNA3 and the required DXIL-SPIRV stack.")
        return fallback
    }

    function categoryLabel(value) {
        if (value === "recommended") return qsTr("Recommended")
        if (value === "compatibility") return qsTr("Compatibility")
        if (value === "debug") return qsTr("Debug")
        return qsTr("Experimental")
    }

    function load() {
        if (!controller || !controller.getProtonTweaks || !gameId)
            return
        var result = controller.getProtonTweaks(gameId) || ({})
        if (!result.success) {
            errorMessage = String(result.error || qsTr("Proton Tweaks are unavailable"))
            return
        }
        entries = result.entries ? Array.from(result.entries) : []
        var next = ({})
        for (var index = 0; index < entries.length; ++index)
            next[String(entries[index].id || "")] = Boolean(entries[index].enabled)
        toggles = next
        environment = result.environment || ({})
        optiscalerFsr4Update = Boolean(result.optiscalerFsr4Update)
        dirty = false
        errorMessage = ""
    }

    function setEnabled(id, enabled) {
        var next = Object.assign({}, toggles)
        next[id] = Boolean(enabled)
        toggles = next
        dirty = true
    }

    function draftEnvironmentText() {
        var values = []
        for (var index = 0; index < entries.length; ++index) {
            var entry = entries[index] || ({})
            if (Boolean(toggles[String(entry.id || "")]))
                values.push(String(entry.environmentKey || "") + "=" + String(entry.value || ""))
        }
        values.sort()
        return values.join("; ")
    }

    function save() {
        if (!controller || !controller.saveProtonTweaks)
            return
        var result = controller.saveProtonTweaks(gameId, {
            "toggles": toggles,
            "optiscalerFsr4Update": optiscalerFsr4Update
        }) || ({})
        if (result.success)
            load()
        else
            errorMessage = String(result.error || qsTr("Could not save Proton Tweaks"))
    }

    contentItem: ColumnLayout {
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: qsTr("Proton Tweaks"); color: App.Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
            StatusBadge { text: qsTr("Per game"); status: "neutral" }
        }
        Label {
            Layout.fillWidth: true
            text: qsTr("All options are off by default. Compatibility and debug options are not general performance boosts.")
            color: App.Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: section.entries
            ColumnLayout {
                id: tweakRow
                required property var modelData
                readonly property string tweakId: String(modelData.id || "")
                Layout.fillWidth: true
                spacing: 4

                SettingRow {
                    Layout.fillWidth: true
                    title: section.labelFor(tweakRow.tweakId, String(tweakRow.modelData.label || ""))
                    description: section.descriptionFor(tweakRow.tweakId, String(tweakRow.modelData.description || ""))
                    RowLayout {
                        spacing: 4
                        StatusBadge {
                            text: section.categoryLabel(String(tweakRow.modelData.category || "experimental"))
                            status: String(tweakRow.modelData.category || "") === "experimental" ? "warning" : "neutral"
                        }
                        AppSwitch {
                            checked: Boolean(section.toggles[tweakRow.tweakId])
                            onToggled: section.setEnabled(tweakRow.tweakId, checked)
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: String(tweakRow.modelData.hardwareState || "") === "unsupported"
                             || String(tweakRow.modelData.hardwareState || "") === "manual_verification_required"
                    text: String(tweakRow.modelData.hardwareState || "") === "unsupported"
                          ? qsTr("The detected GPU vendor does not match this option.")
                          : qsTr("Hardware compatibility must be verified manually; the GPU name alone is not sufficient.")
                    color: App.Theme.warning
                    wrapMode: Text.WordWrap
                }
                Divider { Layout.fillWidth: true }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            title: qsTr("OptiScaler FSR 4 update")
            description: qsTr("Writes Fsr4Update=true only to an intact OptiScaler.ini managed for this game. Experimental and hardware dependent.")
            RowLayout {
                StatusBadge { text: qsTr("Experimental"); status: "warning" }
                AppSwitch {
                    checked: section.optiscalerFsr4Update
                    onToggled: { section.optiscalerFsr4Update = checked; section.dirty = true }
                }
            }
        }

        Label { Layout.fillWidth: true; text: qsTr("Final Proton environment: %1").arg(section.draftEnvironmentText() || qsTr("none")); color: App.Theme.textMuted; font.family: "monospace"; wrapMode: Text.WordWrap }
        Label { Layout.fillWidth: true; visible: section.errorMessage.length > 0; text: section.errorMessage; color: App.Theme.danger; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton { text: qsTr("Save Proton Tweaks"); enabled: section.dirty; onClicked: section.save() }
        }
    }

    Connections {
        target: section.controller || null
        ignoreUnknownSignals: true
        function onProtonTweaksChanged(appId) {
            if (String(appId) === section.steamAppId)
                section.load()
        }
    }

    onGameIdChanged: load()
    Component.onCompleted: load()
}
