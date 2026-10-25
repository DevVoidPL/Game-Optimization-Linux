pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../.." as App

ColumnLayout {
    id: content
    property var panel
    readonly property var enabler: panel ? panel.enabler : ({})
    readonly property var configuration: enabler.configuration || ({})
    readonly property string backendState: String(enabler.statusState || "inactive")
    spacing: 12

    Label {
        Layout.fillWidth: true
        text: "DLSS Enabler"
        color: App.Theme.text
        font.pixelSize: App.Theme.fontTitle
        font.weight: Font.Bold
    }
    StatusBadge {
        text: content.backendState === "runtime_verified" ? qsTr("Runtime verified")
              : content.backendState === "selected" ? qsTr("Selected · runtime not verified")
              : content.backendState === "installed" ? qsTr("Installed · not selected")
              : content.backendState === "error" ? qsTr("Error")
              : content.backendState === "switch_interrupted" ? qsTr("Switch interrupted") : qsTr("Off")
        status: content.backendState === "runtime_verified" ? "success"
                : content.backendState === "error" ? "danger"
                : (content.backendState === "selected" || content.backendState === "switch_interrupted") ? "warning" : "neutral"
    }
    Label {
        objectName: "scalingIntegrationStatus"
        Layout.fillWidth: true
        text: qsTr("DLSS Enabler release %1 (module %2) replaces OptiScaler for this game and bundles OptiScaler %3. FSR 4 INT8 Lite is unavailable while this backend is selected. Switching remains fully reversible.")
              .arg(String(content.enabler.version || "0.9.4"))
              .arg(String(content.enabler.moduleVersion || "3.00.000.0"))
              .arg(String(content.enabler.bundledOptiScalerVersion || "0.9.4"))
        color: App.Theme.textSecondary
        wrapMode: Text.WordWrap
    }
    Label {
        Layout.fillWidth: true
        visible: content.panel.active
        text: (content.enabler.amdIntel
               ? qsTr("AMD/Intel uses the DXGI loader with dlss-enabler.dll and the game's native DLSS copied as _nvngx.dll. Proton override: dxgi=n,b.")
               : qsTr("Proton override: version=n,b."))
              + (content.enabler.parkedOptiScaler ? " " + qsTr("OptiScaler is parked and will be restored.") : "")
        color: App.Theme.text
        wrapMode: Text.WordWrap
    }
    Label {
        Layout.fillWidth: true
        visible: content.panel.active
        text: content.enabler.runtimeVerified
              ? qsTr("Enabler and its upscaler were loaded by the game. Frame generation and image quality still require an in-game check.")
              : qsTr("Installed and selected. Runtime operation is not confirmed until a game launch produces fresh runtime evidence.")
        color: content.enabler.runtimeVerified ? App.Theme.success : App.Theme.warning
        wrapMode: Text.WordWrap
    }
    Label {
        Layout.fillWidth: true
        visible: content.panel.active && Boolean((content.enabler.nativeDlss || {}).path)
        text: qsTr("Native game DLSS copied as _nvngx.dll: %1")
              .arg(String((content.enabler.nativeDlss || {}).path || ""))
        color: App.Theme.textMuted
        font.family: "monospace"
        elide: Text.ElideMiddle
    }
    SettingRow {
        Layout.fillWidth: true
        visible: content.panel.active
        title: qsTr("DX12 upscaler")
        description: qsTr("Supported by the installed Enabler module and bundled OptiScaler.")
        AppComboBox {
            readonly property var values: ["auto", "fsr31", "fsr22", "xess"]
            Layout.preferredWidth: 220
            model: [qsTr("Automatic"), "FSR 3.1", "FSR 2.2", "XeSS"]
            currentIndex: Math.max(0, values.indexOf(String(content.configuration.dx12Upscaler || "auto")))
            onActivated: function(index) { content.panel.configure({"dx12Upscaler": values[index]}) }
        }
    }
    SettingRow {
        Layout.fillWidth: true
        visible: content.panel.active
        title: qsTr("Frame generation")
        description: qsTr("Automatic or FSR 3, as supported by Enabler 3.00.000.0.")
        AppComboBox {
            readonly property var values: ["auto", "fsr3"]
            Layout.preferredWidth: 220
            model: [qsTr("Automatic"), "FSR 3"]
            currentIndex: Math.max(0, values.indexOf(String(content.configuration.frameGenerator || "auto")))
            onActivated: function(index) { content.panel.configure({"frameGenerator": values[index]}) }
        }
    }
    SettingRow {
        Layout.fillWidth: true
        visible: content.panel.active
        title: qsTr("Enabler FPS limit")
        description: qsTr("Requires game Reflex support; applies after restarting the game.")
        AppComboBox {
            readonly property var values: ["off", "60", "90", "120", "144", "vsync"]
            Layout.preferredWidth: 220
            model: [qsTr("Off"), "60", "90", "120", "144", "VSync"]
            currentIndex: Math.max(0, values.indexOf(String(content.configuration.framerateLimit || "off")))
            onActivated: function(index) { content.panel.configure({"framerateLimit": values[index]}) }
        }
    }
    SettingRow {
        Layout.fillWidth: true
        visible: content.panel.active
        title: "Reflex"
        AppComboBox {
            readonly property var values: ["on", "boost", "off"]
            Layout.preferredWidth: 220
            model: [qsTr("On"), "Boost", qsTr("Off")]
            currentIndex: Math.max(0, values.indexOf(String(content.configuration.reflex || "on")))
            onActivated: function(index) { content.panel.configure({"reflex": values[index]}) }
        }
    }
    SettingRow {
        Layout.fillWidth: true
        visible: content.panel.active
        title: qsTr("Runtime logging")
        description: qsTr("Keeps local Enabler and OptiScaler logs for verification and diagnostics.")
        AppSwitch {
            checked: content.configuration.logging !== false
            onToggled: content.panel.configure({"logging": checked})
        }
    }
    SettingRow {
        Layout.fillWidth: true
        visible: content.panel.active && content.configuration.logging !== false
        title: qsTr("Log detail")
        AppComboBox {
            readonly property var values: ["2", "1", "0"]
            Layout.preferredWidth: 220
            model: [qsTr("Information"), qsTr("Debug"), qsTr("Trace")]
            currentIndex: Math.max(0, values.indexOf(String(content.configuration.logLevel || "2")))
            onActivated: function(index) { content.panel.configure({"logLevel": values[index]}) }
        }
    }
    Label {
        Layout.fillWidth: true
        visible: content.panel.active
        text: qsTr("Diagnostic log: %1").arg(String((content.enabler.runtime || {}).logPath || ""))
        color: App.Theme.textMuted
        wrapMode: Text.WrapAnywhere
    }
    Label {
        Layout.fillWidth: true
        visible: ((content.enabler.runtime || {}).warnings || []).length > 0
                 || ((content.enabler.runtime || {}).errors || []).length > 0
        text: (((content.enabler.runtime || {}).errors || []).concat((content.enabler.runtime || {}).warnings || [])).join("\n")
        color: App.Theme.warning
        wrapMode: Text.WordWrap
    }
    Label {
        Layout.fillWidth: true
        visible: (content.enabler.drift || []).length > 0
        text: qsTr("Changed outside GameOpti: %1. Switching back is blocked until these files are restored.")
              .arg((content.enabler.drift || []).join(", "))
        color: App.Theme.warning
        wrapMode: Text.WordWrap
    }
    Label {
        Layout.fillWidth: true
        visible: content.panel.interrupted
        text: qsTr("A previous switch was interrupted. Restoring returns the game to the previous state.")
        color: App.Theme.warning
        wrapMode: Text.WordWrap
    }
    Label {
        objectName: "dlssEnablerConfirmText"
        Layout.fillWidth: true
        visible: content.panel.confirmOpen
        text: qsTr("Switch this game to DLSS Enabler? GameOpti will verify installed files and check runtime logs or loaded game modules after launch.")
        color: App.Theme.text
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
    }
    Label {
        objectName: "dlssEnablerAntiCheatWarning"
        Layout.fillWidth: true
        visible: content.panel.confirmOpen && content.panel.antiCheatDetected
        text: qsTr("Anti-cheat files found in the game folder (%1). For single-player/offline use only. Do not enter online modes with these modifications; you may be banned.")
              .arg((content.panel.antiCheat.names || []).join(", ") || qsTr("unknown"))
        color: App.Theme.warning
        wrapMode: Text.WordWrap
    }
    Label {
        readonly property string operationError: String(content.panel.statusData.operationError || "")
        Layout.fillWidth: true
        visible: content.panel.errorMessage.length > 0 || operationError.length > 0 || Boolean(content.enabler.error)
        text: content.panel.errorMessage.length > 0 ? content.panel.errorMessage
              : App.I18n.message(operationError || String(content.enabler.error || ""))
        color: App.Theme.danger
        wrapMode: Text.WordWrap
    }
    Flow {
        Layout.fillWidth: true
        Layout.preferredHeight: childrenRect.height
        spacing: 10
        AppButton {
            objectName: "dlssEnablerEnableButton"
            visible: !content.panel.active && !content.panel.interrupted && !content.panel.confirmOpen
            text: qsTr("Switch to DLSS Enabler")
            iconSource: App.UiIcons.actionInstall
            enabled: content.enabler.available !== false
            onClicked: content.panel.confirmOpen = true
        }
        AppButton {
            visible: content.panel.confirmOpen
            text: qsTr("Confirm switch")
            kind: "primary"
            onClicked: content.panel.start(true)
        }
        AppButton {
            visible: content.panel.confirmOpen
            text: qsTr("Cancel")
            kind: "secondary"
            onClicked: content.panel.confirmOpen = false
        }
        AppButton {
            objectName: "dlssEnablerRestoreButton"
            visible: content.panel.active || content.panel.interrupted
            text: qsTr("Restore OptiScaler")
            iconSource: App.UiIcons.actionRefresh
            kind: "secondary"
            onClicked: content.panel.start(false)
        }
        AppButton {
            text: qsTr("Refresh diagnostics")
            kind: "secondary"
            onClicked: content.panel.refresh(true)
        }
        AppButton {
            visible: content.panel.active
            text: qsTr("Open game folder")
            kind: "secondary"
            onClicked: if (content.panel.controller && content.panel.controller.openOptiScalerDirectory)
                           content.panel.controller.openOptiScalerDirectory(content.panel.gameId)
        }
    }
}
