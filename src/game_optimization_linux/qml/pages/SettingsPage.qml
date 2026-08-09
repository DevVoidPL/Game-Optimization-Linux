import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import ".." as App

Item {
    id: page

    property var controller
    property var settingsData: controller && controller.settings ? controller.settings : ({})
    signal toastRequested(string message, string tone)

    function setting(keys, fallback) {
        var source = settingsData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function save(key, value) {
        if (controller && controller.saveSetting)
            return controller.saveSetting(key, value)
        return false
    }

    function languageIndex(value) {
        var normalized = String(value || "en").toLowerCase().replace("-", "_")
        if (normalized === "pl" || normalized === "pl_pl" || normalized === "polski" || normalized === "polish")
            return 1
        if (normalized === "es" || normalized === "es_es" || normalized === "español" || normalized === "espanol" || normalized === "spanish")
            return 2
        return 0
    }

    function indexOfValue(modelValues, value, fallback) {
        var target = String(value).toLowerCase()
        for (var i = 0; i < modelValues.length; ++i) {
            if (String(modelValues[i]).toLowerCase() === target)
                return i
        }
        return fallback
    }

    readonly property var libraryDirectories: setting(["libraryDirectories", "library_directories"], []) || []
    readonly property var steamInstallationDirectories: setting(["steamInstallationDirectories", "steam_installation_directories"], []) || []
    readonly property var ignoredSteamLibraries: setting(["ignoredSteamLibraries", "ignored_steam_libraries"], []) || []
    readonly property string automaticCompressionMode: String(
                                                           setting(
                                                               ["automaticCompressionMode",
                                                                "automatic_compression_mode"],
                                                               "Off"))
    readonly property bool automaticCompressionEnabled: Boolean(
                                                            automaticCompressionMode !== "Off")

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: scroll.availableWidth
            spacing: App.Theme.spacingLarge

            Item { Layout.preferredHeight: App.Theme.contentPadding - 4 }

            PageHeader {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                title: qsTr("Settings")
                subtitle: qsTr("Preferences are stored locally and restored at startup")
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 4

                    Label {
                        text: qsTr("General")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Language")
                        description: qsTr("Change the interface language immediately")

                        AppComboBox {
                            property var codes: ["en", "pl", "es"]
                            Layout.preferredWidth: 170
                            model: ["English", "Polski", "Español"]
                            currentIndex: page.languageIndex(page.setting(["language"], "en"))
                            onActivated: function(index) {
                                var code = codes[index]
                                if (page.save("language", code)
                                        && translationManager
                                        && translationManager.setLanguage)
                                    translationManager.setLanguage(code)
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Automatic updates")
                        description: qsTr("Check for new releases without installing them automatically")

                        AppSwitch {
                            checked: Boolean(page.setting(["automaticUpdates", "automatic_updates"], true))
                            onToggled: page.save("automaticUpdates", checked)
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Logging level")
                        description: qsTr("Controls how much diagnostic information is recorded")

                        AppComboBox {
                            property var values: ["DEBUG", "INFO", "WARNING", "ERROR"]
                            Layout.preferredWidth: 140
                            model: values
                            currentIndex: page.indexOfValue(values, page.setting(["logLevel", "log_level"], "INFO"), 1)
                            onActivated: function(index) { page.save("logLevel", values[index]) }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Hidden Updates history")
                        description: qsTr("Allow previously dismissed update events to appear again")

                        AppButton {
                            text: qsTr("Clear hidden history")
                            kind: "secondary"
                            onClicked: {
                                if (page.controller
                                        && page.controller.clearHiddenUpdatesHistory)
                                    page.controller.clearHiddenUpdatesHistory()
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Automatic Btrfs compression")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }
                        StatusBadge {
                            text: page.automaticCompressionEnabled
                                  ? qsTr("Opted in") : qsTr("Off")
                            status: page.automaticCompressionEnabled
                                    ? "warning" : "paused"
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Off is the safe default. When enabled, Game Optimization may queue compression only while the application is open and only after every Btrfs safety check succeeds.")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Automatic compression")
                        description: qsTr("Choose which locally detected Steam events may trigger the guarded workflow")

                        AppComboBox {
                            property var values: [
                                "Off",
                                "After new game installation",
                                "After game update",
                                "After installation and update"
                            ]
                            property var labels: [
                                qsTr("Off"),
                                qsTr("After new game installation"),
                                qsTr("After game update"),
                                qsTr("After installation and update")
                            ]
                            Layout.preferredWidth: 240
                            model: labels
                            currentIndex: page.indexOfValue(
                                              values,
                                              page.automaticCompressionMode,
                                              0)
                            onActivated: function(index) {
                                page.save("automaticCompressionMode", values[index])
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        enabled: page.automaticCompressionEnabled
                        opacity: enabled ? 1.0 : 0.55
                        spacing: 4

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Automatic profile")
                            description: qsTr("Auto compares measured levels; Balanced is the predictable fixed choice")

                            AppComboBox {
                                property var values: ["Fast", "Balanced", "Maximum", "Auto"]
                                property var labels: [qsTr("Fast"), qsTr("Balanced"), qsTr("Maximum"), qsTr("Auto")]
                                Layout.preferredWidth: 170
                                model: labels
                                currentIndex: page.indexOfValue(
                                                  values,
                                                  page.setting(
                                                      ["automaticCompressionProfile",
                                                       "automatic_compression_profile"],
                                                      "Auto"),
                                                  3)
                                onActivated: function(index) {
                                    page.save("automaticCompressionProfile", values[index])
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Safety delay")
                            description: qsTr("Wait after Steam becomes stable before starting analysis or compression")

                            RowLayout {
                                AppTextField {
                                    id: automaticDelayField
                                    Layout.preferredWidth: 110
                                    text: String(page.setting(
                                                     ["automaticCompressionDelaySeconds",
                                                      "automatic_compression_delay_seconds"],
                                                     300))
                                    horizontalAlignment: Text.AlignRight
                                    inputMethodHints: Qt.ImhDigitsOnly
                                    validator: IntValidator { bottom: 0; top: 86400 }
                                    onEditingFinished: {
                                        if (acceptableInput)
                                            page.save("automaticCompressionDelaySeconds",
                                                      Number(text))
                                    }
                                }
                                Label {
                                    text: qsTr("seconds")
                                    color: App.Theme.textSecondary
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Minimum free space")
                            description: qsTr("Do not start if the Btrfs filesystem has less free space")

                            RowLayout {
                                AppTextField {
                                    id: minimumFreeSpaceField
                                    Layout.preferredWidth: 110
                                    text: String(page.setting(
                                                     ["automaticCompressionMinFreeGb",
                                                      "automatic_compression_min_free_gb"],
                                                     10))
                                    horizontalAlignment: Text.AlignRight
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    validator: DoubleValidator {
                                        bottom: 0
                                        top: 1000000
                                        decimals: 1
                                        notation: DoubleValidator.StandardNotation
                                    }
                                    onEditingFinished: {
                                        if (acceptableInput)
                                            page.save("automaticCompressionMinFreeGb",
                                                      Number(text))
                                    }
                                }
                                Label {
                                    text: qsTr("GiB")
                                    color: App.Theme.textSecondary
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Completion notifications")
                            description: qsTr("Notify after automatic compression succeeds, fails, or is blocked")

                            AppSwitch {
                                checked: Boolean(page.setting(
                                                     ["automaticCompressionNotify",
                                                      "automatic_compression_notify"],
                                                     true))
                                onToggled: page.save("automaticCompressionNotify", checked)
                            }
                        }
                    }

                    Rectangle {
                        visible: page.automaticCompressionEnabled
                        Layout.fillWidth: true
                        Layout.preferredHeight: automaticSafetyText.implicitHeight + 24
                        radius: App.Theme.radiusMedium
                        color: App.Theme.warningSoft

                        Label {
                            id: automaticSafetyText
                            anchors.fill: parent
                            anchors.margins: 12
                            text: qsTr("Automatic mode never bypasses safeguards: unavailable libraries, running or updating games, insufficient space, shared extents, and unknown shared-extent state all block writes.")
                            color: App.Theme.warning
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Controller")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }
                        StatusBadge {
                            text: page.controller && page.controller.gamepadAvailable
                                  ? qsTr("SDL3 available") : qsTr("SDL3 missing")
                            status: page.controller && page.controller.gamepadAvailable ? "available" : "missing"
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: !(page.controller && page.controller.gamepadAvailable)
                        text: qsTr("Install SDL3 to enable controller detection and Couch Mode input.")
                        color: App.Theme.warning
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        enabled: !!(page.controller && page.controller.gamepadAvailable)
                        opacity: enabled ? 1.0 : 0.55
                        spacing: 4

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Controller mode")
                            description: qsTr("Choose startup and automatic switching behavior; F11 always remains available")
                            AppComboBox {
                                property var values: ["Automatic", "Desktop only", "Couch only"]
                                property var labels: [qsTr("Automatic"), qsTr("Desktop only"), qsTr("Couch only")]
                                Layout.preferredWidth: 170
                                model: labels
                                currentIndex: page.indexOfValue(values, page.setting(["controllerMode", "controller_mode"], "Automatic"), 0)
                                onActivated: function(index) { page.save("controllerMode", values[index]) }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Swap Accept and Back")
                            description: qsTr("Reverse the two primary face-button actions")
                            AppSwitch {
                                checked: Boolean(page.setting(["swapAcceptBack", "swap_accept_back"], false))
                                onToggled: page.save("swapAcceptBack", checked)
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Repeat interval")
                            description: qsTr("Time between repeated navigation steps")
                            RowLayout {
                                AppSlider {
                                    id: repeatRateSlider
                                    Layout.preferredWidth: 190
                                    from: 50
                                    to: 500
                                    stepSize: 10
                                    value: Number(page.setting(["navigationRepeatRateMs", "navigation_repeat_rate_ms"], 110))
                                    onMoved: repeatRateSaveTimer.restart()
                                }
                                Label {
                                    text: qsTr("%1 ms").arg(Math.round(repeatRateSlider.value))
                                    color: App.Theme.text
                                    Layout.preferredWidth: 62
                                    horizontalAlignment: Text.AlignRight
                                }
                                Timer {
                                    id: repeatRateSaveTimer
                                    interval: 250
                                    onTriggered: page.save("navigationRepeatRateMs", Math.round(repeatRateSlider.value))
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Analog dead zone")
                            description: qsTr("Ignore small stick movement around the center")
                            RowLayout {
                                AppSlider {
                                    id: deadzoneSlider
                                    Layout.preferredWidth: 190
                                    from: 5
                                    to: 75
                                    stepSize: 1
                                    value: Number(page.setting(["analogDeadzone", "analog_deadzone"], 0.20)) * 100
                                    onMoved: deadzoneSaveTimer.restart()
                                }
                                Label {
                                    text: qsTr("%1%").arg(Math.round(deadzoneSlider.value))
                                    color: App.Theme.text
                                    Layout.preferredWidth: 42
                                    horizontalAlignment: Text.AlignRight
                                }
                                Timer {
                                    id: deadzoneSaveTimer
                                    interval: 250
                                    onTriggered: page.save("analogDeadzone", deadzoneSlider.value / 100)
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Navigation repeat")
                            description: qsTr("Delay before held directional input starts repeating")
                            RowLayout {
                                AppSlider {
                                    id: repeatDelaySlider
                                    Layout.preferredWidth: 190
                                    from: 150
                                    to: 1000
                                    stepSize: 50
                                    value: Number(page.setting(["navigationRepeatDelayMs", "navigation_repeat_delay_ms"], 350))
                                    onMoved: repeatSaveTimer.restart()
                                }
                                Label {
                                    text: qsTr("%1 ms").arg(Math.round(repeatDelaySlider.value))
                                    color: App.Theme.text
                                    Layout.preferredWidth: 62
                                    horizontalAlignment: Text.AlignRight
                                }
                                Timer {
                                    id: repeatSaveTimer
                                    interval: 250
                                    onTriggered: page.save("navigationRepeatDelayMs", Math.round(repeatDelaySlider.value))
                                }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Hide cursor in Couch Mode")
                            description: qsTr("The cursor returns as soon as the mouse moves")
                            AppSwitch {
                                checked: Boolean(page.setting(["hideCursorInCouchMode", "hide_cursor_in_couch_mode"], true))
                                onToggled: page.save("hideCursorInCouchMode", checked)
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Start Couch Mode fullscreen")
                            description: qsTr("Use the whole display when Couch Mode opens")
                            AppSwitch {
                                checked: Boolean(page.setting(["startCouchModeFullscreen", "start_couch_mode_fullscreen"], true))
                                onToggled: page.save("startCouchModeFullscreen", checked)
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("After launching a game")
                            description: qsTr("Choose what the Game Optimization window should do")
                            AppComboBox {
                                property var values: ["Minimize", "Stay open", "Close launcher"]
                                property var labels: [qsTr("Minimize"), qsTr("Stay open"), qsTr("Close launcher")]
                                Layout.preferredWidth: 170
                                model: labels
                                currentIndex: page.indexOfValue(values, page.setting(["postLaunchBehavior", "post_launch_behavior"], "Minimize"), 0)
                                onActivated: function(index) { page.save("postLaunchBehavior", values[index]) }
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        SettingRow {
                            Layout.fillWidth: true
                            title: qsTr("Interface sounds")
                            description: qsTr("Optional navigation sounds; disabled by default")
                            AppSwitch {
                                checked: Boolean(page.setting(["interfaceSounds", "interface_sounds"], false))
                                onToggled: page.save("interfaceSounds", checked)
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 4

                    Label {
                        text: qsTr("Appearance")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Color mode")
                        description: qsTr("Follow the desktop or choose a fixed light or dark palette")

                        RowLayout {
                            spacing: 8

                            Repeater {
                                model: [
                                    { "label": qsTr("System"), "value": "system", "symbol": "◐" },
                                    { "label": qsTr("Dark"), "value": "dark", "symbol": "●" },
                                    { "label": qsTr("Light"), "value": "light", "symbol": "○" }
                                ]

                                delegate: Button {
                                    id: themeButton
                                    required property var modelData
                                    property bool active: String(page.setting(["themeMode", "theme"], "system")) === modelData.value
                                    implicitWidth: 84
                                    implicitHeight: 42
                                    focusPolicy: Qt.StrongFocus
                                    onClicked: page.save("themeMode", modelData.value)

                                    contentItem: RowLayout {
                                        spacing: 6
                                        Label {
                                            text: themeButton.modelData.symbol
                                            color: themeButton.active ? App.Theme.accent : App.Theme.textMuted
                                            font.pixelSize: 14
                                        }
                                        Label {
                                            text: themeButton.modelData.label
                                            color: themeButton.active ? App.Theme.text : App.Theme.textSecondary
                                            font.pixelSize: App.Theme.fontCaption
                                            font.weight: themeButton.active ? Font.DemiBold : Font.Normal
                                        }
                                    }

                                    background: Rectangle {
                                        radius: App.Theme.radiusSmall
                                        color: themeButton.active ? App.Theme.accentSoft
                                                                  : themeButton.hovered ? App.Theme.surfaceHover : App.Theme.input
                                        border.width: themeButton.visualFocus || themeButton.active ? 2 : 1
                                        border.color: themeButton.active || themeButton.visualFocus ? App.Theme.accent : App.Theme.border
                                    }
                                }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 4

                    Label {
                        text: qsTr("Resource limits")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Default compression profile")
                        description: qsTr("Preselected mode when opening the Storage tab")

                        AppComboBox {
                            property var values: ["Fast", "Balanced", "Maximum", "Auto"]
                            property var labels: [qsTr("Fast"), qsTr("Balanced"), qsTr("Maximum"), qsTr("Auto")]
                            Layout.preferredWidth: 150
                            model: labels
                            currentIndex: page.indexOfValue(values, page.setting(["defaultCompressionProfile", "default_compression_profile"], "Auto"), 3)
                            onActivated: function(index) { page.save("defaultCompressionProfile", values[index]) }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("CPU usage limit")
                        description: qsTr("Maximum CPU share for future background enhancement jobs")

                        RowLayout {
                            spacing: 10
                            AppSlider {
                                id: cpuSlider
                                Layout.preferredWidth: 190
                                from: 10
                                to: 100
                                stepSize: 5
                                value: Number(page.setting(["cpuUsageLimit", "cpu_limit_percent"], 75))
                                onMoved: cpuSaveTimer.restart()
                            }
                            Label {
                                text: qsTr("%1%").arg(Math.round(cpuSlider.value))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.Bold
                                Layout.preferredWidth: 42
                                horizontalAlignment: Text.AlignRight
                            }
                            Timer {
                                id: cpuSaveTimer
                                interval: 250
                                onTriggered: page.save("cpuUsageLimit", Math.round(cpuSlider.value))
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("GPU usage limit")
                        description: qsTr("Maximum GPU share for future texture enhancement jobs")

                        RowLayout {
                            spacing: 10
                            AppSlider {
                                id: gpuSlider
                                Layout.preferredWidth: 190
                                from: 10
                                to: 100
                                stepSize: 5
                                value: Number(page.setting(["gpuUsageLimit", "gpu_limit_percent"], 75))
                                onMoved: gpuSaveTimer.restart()
                            }
                            Label {
                                text: qsTr("%1%").arg(Math.round(gpuSlider.value))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.Bold
                                Layout.preferredWidth: 42
                                horizontalAlignment: Text.AlignRight
                            }
                            Timer {
                                id: gpuSaveTimer
                                interval: 250
                                onTriggered: page.save("gpuUsageLimit", Math.round(gpuSlider.value))
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 10

                    Label {
                        text: qsTr("Storage locations")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Steam discovery")
                        description: page.controller && page.controller.demoMode
                                     ? qsTr("Demo data is active because GAME_OPTIMIZATION_DEMO=1")
                                     : qsTr("Standard and Flatpak locations are scanned read-only")

                        StatusBadge {
                            text: page.controller && page.controller.isScanning
                                  ? qsTr("Scanning")
                                  : page.controller && page.controller.steamFound
                                    ? qsTr("Steam detected") : qsTr("Not detected")
                            status: page.controller && page.controller.steamFound ? "Ready" : "Not checked"
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Show Steam tools and runtimes")
                        description: qsTr("Include Proton, Steam Linux Runtime, SDKs, and dedicated servers on Games")

                        AppSwitch {
                            checked: Boolean(page.setting(["showSteamToolsAndRuntimes", "show_steam_tools_and_runtimes"], false))
                            onToggled: page.save("showSteamToolsAndRuntimes", checked)
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    Label {
                        text: qsTr("Additional Steam locations")
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Optional Steam roots are read during the next library refresh. No files are modified.")
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    Repeater {
                        model: page.steamInstallationDirectories

                        delegate: Rectangle {
                            id: steamDirectoryRow
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 42
                            radius: App.Theme.radiusSmall
                            color: App.Theme.input
                            border.width: 1
                            border.color: App.Theme.border

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 5
                                spacing: 8

                                Label {
                                    Layout.fillWidth: true
                                    text: String(steamDirectoryRow.modelData)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                    font.family: "monospace"
                                    elide: Text.ElideMiddle
                                }

                                IconButton {
                                    symbol: "×"
                                    danger: true
                                    toolTip: qsTr("Remove additional Steam location")
                                    onClicked: {
                                        var updated = []
                                        for (var i = 0; i < page.steamInstallationDirectories.length; ++i) {
                                            if (i !== steamDirectoryRow.index)
                                                updated.push(String(page.steamInstallationDirectories[i]))
                                        }
                                        page.save("steamInstallationDirectories", updated)
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        AppTextField {
                            id: newSteamDirectoryField
                            Layout.fillWidth: true
                            placeholderText: qsTr("/path/to/Steam")
                            font.family: "monospace"
                            onAccepted: addSteamDirectoryButton.clicked()
                        }

                        AppButton {
                            id: addSteamDirectoryButton
                            text: qsTr("Add Steam location")
                            iconText: "+"
                            enabled: newSteamDirectoryField.text.trim().length > 0
                            onClicked: {
                                var updated = []
                                for (var i = 0; i < page.steamInstallationDirectories.length; ++i)
                                    updated.push(String(page.steamInstallationDirectories[i]))
                                updated.push(newSteamDirectoryField.text.trim())
                                page.save("steamInstallationDirectories", updated)
                                newSteamDirectoryField.clear()
                            }
                        }
                    }

                    Label {
                        visible: page.ignoredSteamLibraries.length > 0
                        text: qsTr("Libraries forgotten in Game Optimization")
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: page.ignoredSteamLibraries.length > 0
                        text: qsTr("These paths remain untouched in Steam and can be restored at any time.")
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    Repeater {
                        model: page.ignoredSteamLibraries

                        delegate: Rectangle {
                            id: ignoredLibraryRow
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 42
                            radius: App.Theme.radiusSmall
                            color: App.Theme.input
                            border.width: 1
                            border.color: App.Theme.border

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 5
                                spacing: 8

                                Label {
                                    Layout.fillWidth: true
                                    text: String(ignoredLibraryRow.modelData)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                    font.family: "monospace"
                                    elide: Text.ElideMiddle
                                }

                                AppButton {
                                    compact: true
                                    text: qsTr("Restore")
                                    onClicked: {
                                        if (page.controller
                                                && page.controller.restoreIgnoredLibrary)
                                            page.controller.restoreIgnoredLibrary(
                                                String(ignoredLibraryRow.modelData))
                                    }
                                }
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Backup directory")
                        description: qsTr("Future backups will be placed here; no directory is touched in demo mode")

                        AppTextField {
                            Layout.preferredWidth: Math.min(360, page.width * 0.38)
                            text: String(page.setting(["backupDirectory", "backup_directory"], "backups"))
                            font.family: "monospace"
                            onEditingFinished: page.save("backupDirectory", text)
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Quarantine directory")
                        description: qsTr("Reserved for a future safe-review workflow")

                        AppTextField {
                            Layout.preferredWidth: Math.min(360, page.width * 0.38)
                            text: String(page.setting(["quarantineDirectory", "quarantine_directory"], "quarantine"))
                            font.family: "monospace"
                            onEditingFinished: page.save("quarantineDirectory", text)
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    Label {
                        text: qsTr("Game library directories")
                        color: App.Theme.text
                        font.pixelSize: App.Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Only immediate child folders are checked for local games. Home and filesystem roots are rejected.")
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    Repeater {
                        model: page.libraryDirectories

                        delegate: Rectangle {
                            id: libraryRow
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 42
                            radius: App.Theme.radiusSmall
                            color: App.Theme.input
                            border.width: 1
                            border.color: App.Theme.border

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 5
                                spacing: 8

                                Label {
                                    Layout.fillWidth: true
                                    text: String(libraryRow.modelData)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                    font.family: "monospace"
                                    elide: Text.ElideMiddle
                                }

                                IconButton {
                                    symbol: "×"
                                    danger: true
                                    toolTip: qsTr("Remove library directory")
                                    onClicked: {
                                        var updated = []
                                        for (var i = 0; i < page.libraryDirectories.length; ++i) {
                                            if (i !== libraryRow.index)
                                                updated.push(String(page.libraryDirectories[i]))
                                        }
                                        page.save("libraryDirectories", updated)
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        AppTextField {
                            id: newLibraryField
                            Layout.fillWidth: true
                            placeholderText: qsTr("/path/to/games")
                            font.family: "monospace"
                            onAccepted: addLibraryButton.clicked()
                        }

                        AppButton {
                            id: addLibraryButton
                            text: qsTr("Add")
                            iconText: "+"
                            enabled: newLibraryField.text.trim().length > 0
                            onClicked: {
                                var updated = []
                                for (var i = 0; i < page.libraryDirectories.length; ++i)
                                    updated.push(String(page.libraryDirectories[i]))
                                updated.push(newLibraryField.text.trim())
                                page.save("libraryDirectories", updated)
                                newLibraryField.clear()
                            }
                        }

                        AppButton {
                            text: qsTr("Rescan")
                            enabled: page.libraryDirectories.length > 0
                            onClicked: {
                                if (page.controller && page.controller.requestLibraryScan)
                                    page.controller.requestLibraryScan(
                                        "settings_local_rescan", "", "manual")
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 4

                    Label {
                        text: qsTr("Experimental")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    SettingRow {
                        Layout.fillWidth: true
                        title: qsTr("Experimental features")
                        description: qsTr("Show unfinished capabilities. They remain simulation-only in this build.")

                        AppSwitch {
                            checked: Boolean(page.setting(["experimentalFeatures", "experimental_features"], false))
                            onToggled: page.save("experimentalFeatures", checked)
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: safetyText.implicitHeight + 24
                        radius: App.Theme.radiusMedium
                        color: App.Theme.warningSoft

                        Label {
                            id: safetyText
                            anchors.fill: parent
                            anchors.margins: 12
                            text: page.controller && page.controller.demoMode
                                  ? qsTr("Demo safety: no sudo, deletion, Btrfs property changes, governor changes, downloads, or launcher-file edits are performed.")
                                  : qsTr("Safety: no sudo, deletion, global mount changes, downloads, or launcher-file edits are performed. Btrfs writes require a confirmed plan or explicit automatic opt-in.")
                            color: App.Theme.warning
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: App.Theme.contentPadding }
        }
    }
}
