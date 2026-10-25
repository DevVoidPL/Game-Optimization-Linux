pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

// FSR model/DLL variant and additional OptiScaler options for one game.
// Every row is shown only when the installed OptiScaler INI exposes the
// underlying key, so nothing is written as an unknown setting.
ColumnLayout {
    id: options

    property var section
    readonly property var status: section ? section.statusData || ({}) : ({})
    readonly property var caps: status.iniCapabilities || ({})
    readonly property var variants: status.fsrVariants || ({})
    readonly property bool installed: Boolean(status.installed)
    readonly property string currentVariant: String(variants.variant || "official")
    readonly property bool liteDriverInstalled: Boolean(variants.driverInstalled)
    readonly property string gpuCompatibility: String(variants.gpuCompatibility || "unknown")

    spacing: 10
    visible: installed

    Divider { Layout.fillWidth: true }

    SettingRow {
        objectName: "fsrVariantRow"
        Layout.fillWidth: true
        visible: Boolean(options.variants.available)
        title: qsTr("FSR 4.1.1 upscaler DLL")
        description: {
            var text = qsTr("Reference is AMD's signed DLL shipped with OptiScaler. Community DLLs come from zgauthier2000/fsr4-shader-performance-overrides (dll-2026-10-19), are verified by SHA-256 and replace only that file; the original is backed up. For Radeon RX 6000/7000 only, never in online games or with anti-cheat. Restart the game after changing it.")
            if (options.currentVariant === "cyboman_lossy2")
                text += " " + qsTr("Lossy2 runs the network on every other frame: expect more smearing or flicker in motion than with Exact.")
            if (options.gpuCompatibility === "unsupported")
                text += " " + qsTr("Unavailable: this GPU is not supported by the community DLLs.")
            if (options.liteDriverInstalled)
                text += " " + qsTr("Unavailable while the FSR 4.1.2 Lite files are installed: the DLLs are built for FSR 4.1.1.")
            if ((options.variants.drift || []).length > 0)
                text += " " + qsTr("Warning: a managed FSR file was changed outside GameOpti.")
            return text
        }
        AppComboBox {
            Layout.preferredWidth: 300
            model: options.section ? options.section.fsrVariantValues.map(function(value) {
                return options.section.fsrVariantLabel(value)
            }) : []
            currentIndex: Math.max(0, options.section ? options.section.fsrVariantValues.indexOf(options.currentVariant) : 0)
            enabled: options.gpuCompatibility !== "unsupported" && !options.liteDriverInstalled
            onActivated: function(index) {
                var value = options.section.fsrVariantValues[index]
                if (value !== options.currentVariant)
                    options.section.startFsrVariant(value)
            }
        }
    }

    SettingRow {
        objectName: "fsrLiteDriverRow"
        Layout.fillWidth: true
        visible: Boolean(options.variants.available) && Boolean(options.caps.lite)
        title: qsTr("FSR 4.1.2 INT8 Lite files")
        description: options.liteDriverInstalled
                     ? qsTr("Installed: AMD-signed amdvkffx64.dll and amdxcffx64.dll (FFX 2.4.0.4370) in the OptiScaler folder. Select \"FSR 4.1.2 INT8 Lite\" as the FSR 4 mode, apply, then restart the game. Remove switches back to the full model files only.")
                     : qsTr("INT8 Lite is AMD's smaller FSR 4 model from driver 26.10.1. It needs OptiScaler v10 (Nightly) and these driver files, downloaded from the Decky OptiScaler v0.0.9 release and verified by SHA-256 (about 108 MB once). Lighter than the full model with somewhat lower quality.")
        AppButton {
            text: options.liteDriverInstalled ? qsTr("Remove Lite files") : qsTr("Install Lite files")
            kind: options.liteDriverInstalled ? "danger" : "secondary"
            iconSource: options.liteDriverInstalled ? App.UiIcons.actionRemove : App.UiIcons.actionDownload
            enabled: options.liteDriverInstalled || options.currentVariant === "official"
            onClicked: options.section.startLiteDriver(!options.liteDriverInstalled)
        }
    }

    Label {
        Layout.fillWidth: true
        visible: Boolean(options.variants.available) && !Boolean(options.caps.lite)
        text: qsTr("FSR 4 INT8 Lite requires OptiScaler v10. Switch the release channel to Nightly and update to use it; the current installation stays unchanged until then.")
        color: App.Theme.textSecondary
        wrapMode: Text.WordWrap
    }

    Divider { Layout.fillWidth: true; visible: Boolean(options.caps.fsr4Preset || options.caps.sharpness || options.caps.log || options.caps.overlayShortcut) }

    Label {
        Layout.fillWidth: true
        visible: Boolean(options.caps.fsr4Preset || options.caps.sharpness || options.caps.log || options.caps.overlayShortcut)
        text: qsTr("Additional OptiScaler settings are saved for this game and written by \"Apply\" above. They take effect at the next game start; Auto keeps OptiScaler's own default.")
        color: App.Theme.textSecondary
        wrapMode: Text.WordWrap
    }

    SettingRow {
        objectName: "fsr4PresetRow"
        Layout.fillWidth: true
        visible: Boolean(options.caps.fsr4Preset)
        title: qsTr("FSR 4 internal preset")
        description: qsTr("Forces the FSR 4 network preset (Fsr4Preset). Auto uses the preset matching the game's quality mode.")
        AppComboBox {
            Layout.preferredWidth: 260
            model: [qsTr("Auto (game's quality mode)"), qsTr("Native AA"), qsTr("Ultra Quality / Quality"),
                    qsTr("Balanced"), qsTr("Performance"), qsTr("DRS"), qsTr("Ultra Performance")]
            currentIndex: Math.max(0, options.section ? options.section.fsr4PresetValues.indexOf(options.section.fsr4Preset) : 0)
            onActivated: function(index) { options.section.fsr4Preset = options.section.fsr4PresetValues[index] }
        }
    }

    SettingRow {
        objectName: "sharpnessRow"
        Layout.fillWidth: true
        visible: Boolean(options.caps.sharpness)
        title: qsTr("Sharpness override")
        description: qsTr("Replaces the game's sharpness with a fixed RCAS strength (OverrideSharpness + Sharpness). Off keeps the game's value.")
        RowLayout {
            AppSwitch {
                id: sharpnessSwitch
                checked: Boolean(options.section) && options.section.sharpness !== "auto"
                onToggled: options.section.sharpness = checked ? Number(sharpnessSlider.value).toFixed(2) : "auto"
            }
            AppSlider {
                id: sharpnessSlider
                Layout.preferredWidth: 145
                from: 0
                to: 1
                stepSize: 0.05
                enabled: sharpnessSwitch.checked
                value: options.section && options.section.sharpness !== "auto" ? Number(options.section.sharpness) : 0.3
                onMoved: options.section.sharpness = Number(value).toFixed(2)
            }
            Label {
                Layout.preferredWidth: 40
                text: sharpnessSwitch.checked ? Number(sharpnessSlider.value).toFixed(2) : qsTr("Auto")
                color: App.Theme.text
                font.pixelSize: App.Theme.fontCaption
            }
        }
    }

    SettingRow {
        objectName: "overlayShortcutRow"
        Layout.fillWidth: true
        visible: Boolean(options.caps.overlayShortcut)
        title: qsTr("OptiScaler overlay key")
        description: qsTr("Key that opens the in-game OptiScaler menu (ShortcutKey). Use it to read the active FSR version and model.")
        AppComboBox {
            Layout.preferredWidth: 200
            model: [qsTr("Auto (Insert)"), "Insert", "Home", "End", "F10", "F12"]
            currentIndex: Math.max(0, options.section ? options.section.overlayShortcutValues.indexOf(options.section.overlayShortcut) : 0)
            onActivated: function(index) { options.section.overlayShortcut = options.section.overlayShortcutValues[index] }
        }
    }

    SettingRow {
        objectName: "optiscalerLogRow"
        Layout.fillWidth: true
        visible: Boolean(options.caps.log)
        title: qsTr("OptiScaler log file")
        description: qsTr("Writes OptiScaler.log next to the game (LogToFile, LogLevel). GameOpti reads it to detect the FSR 4 Lite model or an FSR 3 fallback. Debug may cost performance.")
        AppComboBox {
            Layout.preferredWidth: 200
            model: [qsTr("Off (default)"), qsTr("Info"), qsTr("Debug")]
            currentIndex: Math.max(0, ["auto", "info", "debug"].indexOf(options.section ? options.section.logMode : "auto"))
            onActivated: function(index) { options.section.logMode = ["auto", "info", "debug"][index] }
        }
    }

    Label {
        objectName: "fsrRuntimeEvidence"
        Layout.fillWidth: true
        readonly property var log: options.status.runtimeLog || ({})
        text: {
            var state = String(log.status || "not_verified")
            var evidence = (log.evidence || []).join(", ")
            if (!log.logFound)
                return qsTr("Runtime: not confirmed. Configured is not the same as active; enable the FSR 4 watermark or the log and start the game.")
            if (log.stale)
                return qsTr("Runtime: the last OptiScaler.log is older than the current settings; start the game again.")
            if (state === "verified_fsr3_fallback")
                return qsTr("Runtime: OptiScaler reported an FSR 3 fallback (%1).").arg(evidence)
            if (state === "log_indicates_fsr4_int8_lite")
                return qsTr("Runtime: the log shows FSR 4 INT8 Lite was available (%1). Confirm with the watermark in game.").arg(evidence)
            if (state === "log_indicates_fsr4")
                return qsTr("Runtime: the log shows an FSR 4 upscaler (%1). Confirm with the watermark in game.").arg(evidence)
            return qsTr("Runtime: the log contains no FSR evidence yet.")
        }
        color: String(log.status || "") === "verified_fsr3_fallback" ? App.Theme.warning : App.Theme.textSecondary
        wrapMode: Text.WordWrap
    }
}
