import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import ".." as App

Item {
    id: page

    property var controller
    property var systemData: controller && controller.systemInfo ? controller.systemInfo : ({})
    signal toastRequested(string message, string tone)

    function value(keys, fallback) {
        var source = systemData || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function buildCapabilities() {
        var source = value(["capabilities"], ({})) || {}
        var details = value(["capabilityDetails", "capability_details"], ({})) || {}
        var rows = []
        for (var key in source) {
            var detail = details[key] || {}
            rows.push({
                "name": key,
                "status": String(source[key]),
                "source": String(detail.source || ""),
                "version": String(detail.version || ""),
                "message": String(detail.diagnostic_message || "")
            })
        }
        if (rows.length === 0) {
            var defaults = ["GameMode", "Gamescope", "MangoHud", "Btrfs tools", "compsize", "Flatpak", "Steam", "Vulkan", "Heroic", "Lutris", "Bottles", "OptiScaler"]
            for (var i = 0; i < defaults.length; ++i)
                rows.push({ "name": defaults[i], "status": "Not checked" })
        }
        rows.sort(function(a, b) { return a.name.localeCompare(b.name) })
        return rows
    }

    function capabilitiesFor(status) {
        var result = []
        var normalized = String(status).toLowerCase()
        var source = capabilityRows
        for (var i = 0; i < source.length; ++i) {
            if (String(source[i].status).toLowerCase() === normalized)
                result.push(source[i])
        }
        return result
    }

    function formatBytes(value) {
        if (value === undefined || value === null || value === "")
            return "-"
        var amount = Number(value)
        if (!isFinite(amount) || amount < 0)
            return "-"
        var units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        var unit = 0
        while (amount >= 1024 && unit < units.length - 1) {
            amount /= 1024
            ++unit
        }
        return (unit === 0 ? Math.round(amount) : amount.toFixed(1)) + " " + units[unit]
    }

    function compressionValue(keys, fallback) {
        var source = compressionCapabilities || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = source[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function capabilityLabel(raw) {
        if (raw === true)
            return qsTr("Available")
        if (raw === false)
            return qsTr("Missing")
        return qsTr("Unknown")
    }

    function capabilityStatus(raw) {
        if (raw === true)
            return "available"
        if (raw === false)
            return "missing"
        return "not checked"
    }

    readonly property var capabilityRows: buildCapabilities()
    readonly property var filesystems: value(["filesystems"], []) || []
    readonly property var compressionCapabilities: value(
                                                           ["compressionCapabilities",
                                                            "compression_capabilities"],
                                                           ({})) || ({})
    readonly property var rawCompressionAvailable: compressionValue(
                                                       ["compressionAvailable",
                                                        "compression_available"],
                                                       null)
    readonly property bool compressionAvailable: Boolean(
                                                     rawCompressionAvailable === true
                                                     || (compressionValue(
                                                             ["btrfsAvailable",
                                                              "btrfs_available"], null) === true
                                                         && compressionValue(
                                                             ["propertySupported",
                                                              "property_supported"], null) === true
                                                         && compressionValue(
                                                             ["recompressionSupported",
                                                              "recompression_supported"], null) === true
                                                         && compressionValue(
                                                             ["levelSupported",
                                                              "level_supported"], null) === true))
    readonly property bool compressionAvailabilityKnown: Boolean(
                                                              rawCompressionAvailable === true
                                                              || rawCompressionAvailable === false
                                                              || compressionValue(
                                                                  ["btrfsAvailable",
                                                                   "btrfs_available"],
                                                                  null) === true
                                                              || compressionValue(
                                                                  ["btrfsAvailable",
                                                                   "btrfs_available"],
                                                                  null) === false)

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
                title: qsTr("System")
                subtitle: page.value(["demo"], false)
                          ? qsTr("Compatibility snapshot for the demonstration environment")
                          : qsTr("Detected Linux hardware, session, and gaming tools")

                StatusBadge {
                    text: page.value(["demo"], false) ? qsTr("Demo data") : qsTr("Detected")
                    status: page.value(["demo"], false) ? "warning" : "available"
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20
                elevated: true

                contentItem: RowLayout {
                    spacing: 18

                    Rectangle {
                        Layout.preferredWidth: 58
                        Layout.preferredHeight: 58
                        radius: 18
                        color: App.Theme.accentSoft

                        Label {
                            anchors.centerIn: parent
                            text: "◈"
                            color: App.Theme.accent
                            font.pixelSize: 26
                            font.weight: Font.Bold
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Label {
                            Layout.fillWidth: true
                            text: page.value(["distribution"], qsTr("Unknown Linux distribution"))
                            color: App.Theme.text
                            font.pixelSize: 20
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                        }

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("%1 · %2 · Kernel %3").arg(
                                      page.value(["desktopEnvironment", "desktop_environment"], qsTr("Unknown desktop")))
                                  .arg(page.value(["sessionType", "session_type"], qsTr("Unknown session")))
                                  .arg(page.value(["kernel"], "-"))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontBody
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                columns: page.width >= 960 ? 2 : 1
                rowSpacing: 12
                columnSpacing: 12

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 152

                    contentItem: ColumnLayout {
                        spacing: 10

                        Label {
                            text: qsTr("Processor")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                            font.weight: Font.DemiBold
                        }

                        Label {
                            Layout.fillWidth: true
                            text: page.value(["cpu"], qsTr("Not checked"))
                            color: App.Theme.text
                            font.pixelSize: App.Theme.fontBodyLarge
                            font.weight: Font.Bold
                            wrapMode: Text.WordWrap
                        }

                        Label {
                            text: Number(page.value(["ram_gb", "ramGb"], 0)) > 0
                                  ? qsTr("%1 GB RAM · %2 cores / %3 threads")
                                      .arg(page.value(["ram_gb", "ramGb"], "-"))
                                      .arg(page.value(["cpuCores", "cpu_cores"], "-"))
                                      .arg(page.value(["cpuThreads", "cpu_threads"], "-"))
                                  : qsTr("RAM: Unknown")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontBody
                        }
                    }
                }

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 130

                    contentItem: ColumnLayout {
                        spacing: 10

                        Label {
                            text: qsTr("Graphics")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                            font.weight: Font.DemiBold
                        }

                        Label {
                            Layout.fillWidth: true
                            text: page.value(["gpu"], qsTr("Not checked"))
                            color: App.Theme.text
                            font.pixelSize: App.Theme.fontBodyLarge
                            font.weight: Font.Bold
                            wrapMode: Text.WordWrap
                        }

                        Label {
                            text: qsTr("Driver: %1").arg(page.value(["gpuDriver", "gpu_driver"], qsTr("Unknown")))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontBody
                        }

                        Label {
                            Layout.fillWidth: true
                            text: String(page.value(["vulkanDevice", "vulkan_device"], "")).length > 0
                                  ? qsTr("Vulkan device: %1").arg(page.value(["vulkanDevice", "vulkan_device"], ""))
                                  : qsTr("Vulkan device information is partial")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 16

                contentItem: RowLayout {
                    spacing: 18
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Steam library: %1").arg(
                                  page.value(["steamLibraryDetected", "steam_library_detected"], false)
                                  ? qsTr("detected") : qsTr("not detected"))
                        color: App.Theme.text
                    }
                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Steam executable: %1 (%2)")
                              .arg(page.value(["steamExecutableDetected", "steam_executable_detected"], false)
                                   ? qsTr("detected") : qsTr("not detected"))
                              .arg(page.value(["steamType", "steam_type"], qsTr("unavailable")))
                        color: App.Theme.textSecondary
                    }
                    StatusBadge {
                        text: page.value(["hostLaunchAvailable", "host_launch_available"], false)
                              ? qsTr("Host launch available") : qsTr("Host launch unavailable")
                        status: page.value(["hostLaunchAvailable", "host_launch_available"], false)
                                ? "available" : "missing"
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: RowLayout {
                    spacing: 16

                    Rectangle {
                        Layout.preferredWidth: 48
                        Layout.preferredHeight: 48
                        radius: 15
                        color: App.Theme.accentSoft
                        Label {
                            anchors.centerIn: parent
                            text: "◎"
                            color: App.Theme.accent
                            font.pixelSize: 23
                            font.weight: Font.Bold
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Controllers")
                            color: App.Theme.text
                            font.pixelSize: App.Theme.fontBodyLarge
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: Number(page.value(["controllerCount"], 0)) > 0
                                  ? qsTr("%1 connected · active: %2").arg(page.value(["controllerCount"], 0)).arg(page.value(["activeControllerName"], qsTr("none")))
                                  : qsTr("No controllers connected")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontBody
                            elide: Text.ElideRight
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: String(page.value(["activeControllerName"], "")).length > 0
                            text: qsTr("%1 · %2").arg(page.value(["activeControllerType"], qsTr("Unknown"))).arg(page.value(["activeControllerMapping"], qsTr("Mapping unknown")))
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                            elide: Text.ElideRight
                        }
                    }

                    StatusBadge {
                        text: page.value(["sdl3Status"], qsTr("Missing"))
                        status: String(page.value(["sdl3Status"], "Missing")).toLowerCase() === "available" ? "available" : "missing"
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding

                Label {
                    Layout.fillWidth: true
                    text: qsTr("Filesystems")
                    color: App.Theme.text
                    font.pixelSize: 18
                    font.weight: Font.Bold
                }

                Switch {
                    visible: !page.value(["demo"], false)
                    text: qsTr("Show system mounts")
                    checked: !!(page.controller && page.controller.showSystemMounts)
                    onToggled: {
                        if (page.controller)
                            page.controller.setShowSystemMounts(checked)
                    }
                }
            }

            Flow {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                spacing: 10
                Layout.preferredHeight: childrenRect.height

                Repeater {
                    model: page.filesystems

                    delegate: SurfaceCard {
                        id: filesystemCard
                        required property var modelData
                        width: parent.width >= 1120 ? (parent.width - 20) / 3 : parent.width >= 700 ? (parent.width - 10) / 2 : parent.width
                        height: 206
                        padding: 14

                        function fsValue(keys, fallback) {
                            for (var i = 0; i < keys.length; ++i) {
                                var candidate = modelData[keys[i]]
                                if (candidate !== undefined && candidate !== null && candidate !== "")
                                    return candidate
                            }
                            return fallback
                        }

                        contentItem: ColumnLayout {
                            spacing: 6

                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true
                                    text: filesystemCard.fsValue(["mount_point", "mountPoint"], "-")
                                    color: App.Theme.text
                                    font.pixelSize: App.Theme.fontBody
                                    font.weight: Font.Bold
                                    font.family: "monospace"
                                    elide: Text.ElideMiddle
                                }
                                StatusBadge {
                                    text: filesystemCard.fsValue(["filesystem_name", "filesystemName", "filesystem"], qsTr("Unknown"))
                                    status: filesystemCard.fsValue(["compression_supported"], false) ? "available" : "unsupported"
                                    showDot: false
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: filesystemCard.fsValue(["device"], qsTr("Device unknown"))
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                                font.family: "monospace"
                                elide: Text.ElideMiddle
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 3
                                columnSpacing: 12

                                Repeater {
                                    model: [
                                        { "label": qsTr("Size"), "value": page.formatBytes(filesystemCard.fsValue(["size_bytes", "sizeBytes"], null)) },
                                        { "label": qsTr("Used"), "value": page.formatBytes(filesystemCard.fsValue(["used_bytes", "usedBytes"], null)) },
                                        { "label": qsTr("Available"), "value": page.formatBytes(filesystemCard.fsValue(["available_bytes", "availableBytes"], null)) }
                                    ]

                                    delegate: ColumnLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        spacing: 2

                                        Label {
                                            text: modelData.label
                                            color: App.Theme.textMuted
                                            font.pixelSize: App.Theme.fontCaption
                                        }
                                        Label {
                                            text: modelData.value
                                            color: App.Theme.text
                                            font.pixelSize: App.Theme.fontBody
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                }
                            }

                            Divider { Layout.fillWidth: true }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Label {
                                    property var writableValue: filesystemCard.fsValue(["writable"], null)
                                    Layout.fillWidth: true
                                    text: writableValue === true ? qsTr("Read-write")
                                          : writableValue === false ? qsTr("Read-only")
                                          : qsTr("Write status unknown")
                                    color: writableValue === false ? App.Theme.warning : App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                }

                                Label {
                                    property bool supported: filesystemCard.fsValue(["compression_supported", "compressionSupported"], false)
                                    text: supported ? qsTr("GameForge compression supported") : qsTr("Compression unsupported")
                                    color: supported ? App.Theme.success : App.Theme.textMuted
                                    font.pixelSize: App.Theme.fontCaption
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }

            Label {
                visible: page.filesystems.length === 0
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                text: qsTr("Not detected")
                color: App.Theme.textMuted
                font.pixelSize: App.Theme.fontCaption
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Btrfs compression capabilities")
                                color: App.Theme.text
                                font.pixelSize: 18
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Tools are detected read-only. Every game is checked again before a write task starts.")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        StatusBadge {
                            text: page.compressionAvailable ? qsTr("Ready")
                                  : page.compressionAvailabilityKnown
                                    ? qsTr("Unavailable") : qsTr("Not checked")
                            status: page.compressionAvailable ? "available"
                                    : page.compressionAvailabilityKnown
                                      ? "missing" : "not checked"
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 920 ? 4 : 2
                        rowSpacing: 9
                        columnSpacing: 9

                        Repeater {
                            model: [
                                {
                                    "label": qsTr("Btrfs tools"),
                                    "value": page.compressionValue(
                                                 ["btrfsAvailable",
                                                  "btrfs_available"], null),
                                    "detail": String(page.compressionValue(
                                                         ["btrfsVersion",
                                                          "btrfs_version"], ""))
                                },
                                {
                                    "label": qsTr("compsize"),
                                    "value": page.compressionValue(
                                                 ["compsizeAvailable",
                                                  "compsize_available"], null),
                                    "detail": String(page.compressionValue(
                                                         ["compsizeVersion",
                                                          "compsize_version"], ""))
                                },
                                {
                                    "label": qsTr("Compression property"),
                                    "value": page.compressionValue(
                                                 ["propertySupported",
                                                  "property_supported"], null),
                                    "detail": qsTr("Persistent ZSTD algorithm")
                                },
                                {
                                    "label": qsTr("File recompression"),
                                    "value": page.compressionValue(
                                                 ["recompressionSupported",
                                                  "recompression_supported"], null),
                                    "detail": page.compressionValue(
                                                  ["levelSupported",
                                                   "level_supported"], null) === true
                                              ? qsTr("ZSTD levels supported")
                                              : qsTr("ZSTD level support unknown")
                                }
                            ]

                            delegate: Rectangle {
                                id: compressionCapability
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 88
                                radius: App.Theme.radiusMedium
                                color: App.Theme.backgroundElevated
                                border.width: 1
                                border.color: App.Theme.border

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 11
                                    spacing: 5

                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            text: compressionCapability.modelData.label
                                            color: App.Theme.text
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                        }
                                        StatusBadge {
                                            text: page.capabilityLabel(
                                                      compressionCapability.modelData.value)
                                            status: page.capabilityStatus(
                                                        compressionCapability.modelData.value)
                                        }
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        text: compressionCapability.modelData.detail.length > 0
                                              ? compressionCapability.modelData.detail
                                              : qsTr("No version information")
                                        color: App.Theme.textMuted
                                        font.pixelSize: App.Theme.fontCaption
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 760 ? 2 : 1
                        rowSpacing: 8
                        columnSpacing: 16

                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Active compression jobs")
                            value: String(Number(page.compressionValue(
                                                     ["activeJobs", "active_jobs"],
                                                     page.value(
                                                         ["activeCompressionJobs",
                                                          "active_compression_jobs"],
                                                         0))) || 0)
                        }
                        LabeledValue {
                            Layout.fillWidth: true
                            label: qsTr("Last compression error")
                            value: App.I18n.message(String(page.compressionValue(
                                                              ["lastError", "last_error"],
                                                              page.value(
                                                                  ["lastCompressionError",
                                                                   "last_compression_error"],
                                                                  qsTr("None")))))
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        visible: String(page.compressionValue(
                                            ["message"], "")).length > 0
                        text: App.I18n.message(String(page.compressionValue(
                                                         ["message"], "")))
                        color: App.Theme.textMuted
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                text: qsTr("Gaming capabilities")
                color: App.Theme.text
                font.pixelSize: 18
                font.weight: Font.Bold
            }

            GridLayout {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                columns: page.width >= 1160 ? 4 : page.width >= 760 ? 2 : 1
                rowSpacing: 10
                columnSpacing: 10

                Repeater {
                    model: [
                        { "key": "available", "label": qsTr("Available"), "symbol": "✓", "tone": App.Theme.success },
                        { "key": "not detected", "label": qsTr("Not detected"), "symbol": "?", "tone": App.Theme.textMuted },
                        { "key": "game-dependent", "label": qsTr("Game-dependent"), "symbol": "◈", "tone": App.Theme.info },
                        { "key": "missing", "label": qsTr("Missing"), "symbol": "↓", "tone": App.Theme.warning },
                        { "key": "unsupported", "label": qsTr("Unsupported"), "symbol": "×", "tone": App.Theme.danger },
                        { "key": "not checked", "label": qsTr("Not checked"), "symbol": "?", "tone": App.Theme.textMuted }
                    ]

                    delegate: SurfaceCard {
                        id: capabilityGroup
                        required property var modelData
                        property var entries: page.capabilitiesFor(modelData.key)
                        Layout.fillWidth: true
                        Layout.minimumHeight: 150
                        padding: 15

                        contentItem: ColumnLayout {
                            spacing: 9

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Label {
                                    text: capabilityGroup.modelData.symbol
                                    color: capabilityGroup.modelData.tone
                                    font.pixelSize: 16
                                    font.weight: Font.Bold
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: capabilityGroup.modelData.label
                                    color: App.Theme.text
                                    font.pixelSize: App.Theme.fontBody
                                    font.weight: Font.Bold
                                }

                                Label {
                                    text: String(capabilityGroup.entries.length)
                                    color: App.Theme.textMuted
                                    font.pixelSize: App.Theme.fontCaption
                                }
                            }

                            Divider { Layout.fillWidth: true }

                            Repeater {
                                model: capabilityGroup.entries

                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 1

                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.name
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                        elide: Text.ElideRight
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        visible: String(modelData.source || "").length > 0
                                                 || String(modelData.version || "").length > 0
                                        text: [
                                            String(modelData.source || ""),
                                            String(modelData.version || "")
                                        ].filter(function(value) {
                                            return value.length > 0
                                        }).join(" - ")
                                        color: App.Theme.textMuted
                                        font.pixelSize: 10
                                        elide: Text.ElideRight
                                        ToolTip.visible: hovered
                                                             && String(modelData.message || "").length > 0
                                        ToolTip.text: String(modelData.message || "")
                                        property bool hovered: capabilityMouse.containsMouse

                                        MouseArea {
                                            id: capabilityMouse
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            acceptedButtons: Qt.NoButton
                                        }
                                    }
                                }
                            }

                            Label {
                                visible: capabilityGroup.entries.length === 0
                                text: qsTr("None")
                                color: App.Theme.textMuted
                                font.pixelSize: App.Theme.fontCaption
                            }

                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: "transparent"
            }

            Item { Layout.preferredHeight: App.Theme.contentPadding }
        }
    }
}
