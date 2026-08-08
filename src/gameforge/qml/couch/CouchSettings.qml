import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

FocusScope {
    id: page
    objectName: "couchSettings"
    property var controller
    property var navigation
    property real couchScale: 1.0
    property int selectedIndex: 0
    property int activeCategoryIndex: 0
    property var settingsData: controller && controller.settings ? controller.settings : ({})

    readonly property var categories: [
        {
            "id": "general",
            "title": qsTr("General"),
            "subtitle": qsTr("Language and appearance")
        },
        {
            "id": "couch",
            "title": qsTr("Couch Mode"),
            "subtitle": qsTr("TV interface behaviour")
        },
        {
            "id": "system",
            "title": qsTr("System"),
            "subtitle": qsTr("Mode and safe defaults")
        }
    ]
    readonly property var rows: [
        {
            "id": "language", "category": "general", "title": qsTr("Language"),
            "value": String(setting("language", "English")), "enabled": true,
            "description": qsTr("Choose the language used throughout the GameForge interface.")
        },
        {
            "id": "appearance", "category": "general", "title": qsTr("Appearance"),
            "value": String(setting("themeMode", "System")), "enabled": true,
            "description": qsTr("Follow the system colours or force a light or dark interface.")
        },
        {
            "id": "interface", "category": "couch", "title": qsTr("Interface mode"),
            "value": String(setting("controllerMode", "Automatic")), "enabled": true,
            "description": qsTr("Choose when GameForge should use the television-friendly Couch Mode interface.")
        },
        {
            "id": "fullscreen", "category": "couch", "title": qsTr("Start Couch Mode fullscreen"),
            "value": boolLabel(setting("startCouchModeFullscreen", true)), "enabled": true,
            "description": qsTr("Open Couch Mode in fullscreen when it is selected at startup.")
        },
        {
            "id": "detect", "category": "couch", "title": qsTr("Switch after controller input"),
            "value": String(setting("controllerMode", "Automatic")) === "Automatic" ? qsTr("On") : qsTr("Controlled by interface mode"),
            "enabled": true,
            "description": qsTr("Automatically enter Couch Mode after GameForge detects controller input.")
        },
        {
            "id": "vibration", "category": "couch", "title": qsTr("Vibration"),
            "value": qsTr("Planned"), "enabled": false,
            "description": qsTr("Controller vibration is planned for a later implementation stage.")
        },
        {
            "id": "sounds", "category": "couch", "title": qsTr("Interface sounds"),
            "value": qsTr("Off"), "enabled": false,
            "description": qsTr("Couch Mode does not play interface sounds at this implementation stage.")
        },
        {
            "id": "desktop", "category": "system", "title": qsTr("Switch to Desktop Mode"),
            "value": qsTr("Always available"), "enabled": true,
            "description": qsTr("Leave Couch Mode and return to the standard desktop interface.")
        },
        {
            "id": "reset", "category": "system", "title": qsTr("Reset Couch Mode settings"),
            "value": qsTr("Safe defaults"), "enabled": true,
            "description": qsTr("Restore safe controller and Couch Mode defaults without changing game data.")
        }
    ]
    readonly property var activeRows: rowsForCategory(activeCategoryIndex)
    readonly property int selectedActiveIndex: activeIndexForGlobal(selectedIndex)
    readonly property var selectedRow: rows[selectedIndex] || ({})

    signal backRequested()

    function restoreActiveFocus() {
        forceActiveFocus()
        Qt.callLater(function() {
            if (page.visible)
                settingsList.forceActiveFocus()
        })
    }

    function setting(key, fallback) {
        var value = settingsData ? settingsData[key] : undefined
        return value === undefined || value === null || value === "" ? fallback : value
    }

    function boolLabel(value) {
        return value === true ? qsTr("On") : qsTr("Off")
    }

    function cycle(values, current, delta) {
        var index = values.indexOf(String(current))
        if (index < 0)
            index = 0
        return values[(index + delta + values.length) % values.length]
    }

    function categoryIndexForId(categoryId) {
        for (var index = 0; index < categories.length; ++index) {
            if (categories[index].id === categoryId)
                return index
        }
        return 0
    }

    function rowsForCategory(categoryIndex) {
        if (!categories[categoryIndex])
            return []
        var categoryId = categories[categoryIndex].id
        var result = []
        for (var index = 0; index < rows.length; ++index) {
            if (rows[index].category === categoryId)
                result.push(rows[index])
        }
        return result
    }

    function globalIndexForId(rowId) {
        for (var index = 0; index < rows.length; ++index) {
            if (rows[index].id === rowId)
                return index
        }
        return -1
    }

    function activeIndexForGlobal(globalIndex) {
        if (!rows[globalIndex])
            return -1
        for (var index = 0; index < activeRows.length; ++index) {
            if (activeRows[index].id === rows[globalIndex].id)
                return index
        }
        return -1
    }

    function firstEnabledIndex(categoryIndex) {
        var categoryRows = rowsForCategory(categoryIndex)
        for (var index = 0; index < categoryRows.length; ++index) {
            if (categoryRows[index].enabled)
                return globalIndexForId(categoryRows[index].id)
        }
        return -1
    }

    function selectIndex(index, rememberFocus) {
        if (index < 0 || index >= rows.length || !rows[index].enabled)
            return
        selectedIndex = index
        activeCategoryIndex = categoryIndexForId(rows[index].category)
        Qt.callLater(function() {
            if (settingsList && selectedActiveIndex >= 0)
                settingsList.positionViewAtIndex(selectedActiveIndex, ListView.Contain)
        })
        if (rememberFocus && navigation)
            navigation.rememberFocus("settings", rows[index].id, index)
    }

    function selectCategory(index) {
        var target = Math.max(0, Math.min(categories.length - 1, index))
        if (target === activeCategoryIndex && rows[selectedIndex]
                && rows[selectedIndex].category === categories[target].id)
            return
        var rowIndex = firstEnabledIndex(target)
        if (rowIndex >= 0)
            selectIndex(rowIndex, true)
    }

    function changeCategory(delta) {
        selectCategory(Math.max(0, Math.min(categories.length - 1,
                                             activeCategoryIndex + delta)))
    }

    function change(delta) {
        if (!controller || !rows[selectedIndex] || !rows[selectedIndex].enabled)
            return
        var id = rows[selectedIndex].id
        if (id === "language") {
            var language = cycle(["English", "Polski", "Español"], setting("language", "English"), delta)
            if (controller.saveSetting("language", language)
                    && typeof translationManager !== "undefined"
                    && translationManager && translationManager.setLanguage)
                translationManager.setLanguage(language)
        } else if (id === "interface") {
            controller.saveSetting("controllerMode", cycle(["Automatic", "Desktop only", "Couch only"], setting("controllerMode", "Automatic"), delta))
        } else if (id === "appearance") {
            controller.saveSetting("themeMode", cycle(["System", "Dark", "Light"], setting("themeMode", "System"), delta))
        } else if (id === "fullscreen") {
            controller.saveSetting("startCouchModeFullscreen", setting("startCouchModeFullscreen", true) !== true)
        } else if (id === "detect") {
            controller.saveSetting("controllerMode", String(setting("controllerMode", "Automatic")) === "Automatic" ? "Couch only" : "Automatic")
        }
    }

    function activate() {
        if (!rows[selectedIndex] || !rows[selectedIndex].enabled || !controller)
            return
        var id = rows[selectedIndex].id
        if (id === "desktop") {
            controller.setInterfaceMode("desktop")
        } else if (id === "reset") {
            controller.saveSetting("controllerMode", "Automatic")
            controller.saveSetting("swapAcceptBack", false)
            controller.saveSetting("analogDeadzone", 0.20)
            controller.saveSetting("navigationRepeatDelayMs", 350)
            controller.saveSetting("navigationRepeatRateMs", 110)
            controller.saveSetting("hideCursorInCouchMode", true)
            controller.saveSetting("startCouchModeFullscreen", true)
            controller.saveSetting("interfaceSounds", false)
        } else {
            change(1)
        }
    }

    function move(delta) {
        var candidate = selectedIndex
        for (var count = 0; count < rows.length; ++count) {
            candidate = Math.max(0, Math.min(rows.length - 1, candidate + delta))
            if (rows[candidate].enabled) {
                selectIndex(candidate, true)
                break
            }
            if (candidate === 0 || candidate === rows.length - 1)
                break
        }
    }

    function restoreFocus() {
        if (!navigation)
            return
        var rememberedIndex = globalIndexForId(String(navigation.focusedId || ""))
        if (rememberedIndex >= 0 && rows[rememberedIndex].enabled)
            selectIndex(rememberedIndex, false)
        else
            selectIndex(selectedIndex, true)
    }

    function handleAction(action) {
        if (action === "Back")
            backRequested()
        else if (action === "NavigateUp")
            move(-1)
        else if (action === "NavigateDown")
            move(1)
        else if (action === "NavigateLeft")
            change(-1)
        else if (action === "NavigateRight")
            change(1)
        else if (action === "Confirm")
            activate()
        else if (action === "PageLeft")
            changeCategory(-1)
        else if (action === "PageRight")
            changeCategory(1)
    }

    focus: visible
    Component.onCompleted: {
        restoreFocus()
        restoreActiveFocus()
    }
    onVisibleChanged: if (visible) {
        Qt.callLater(restoreFocus)
        restoreActiveFocus()
    }

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
        gradient: Gradient {
            GradientStop { position: 0.0; color: App.Theme.dark ? "#111B28" : "#F8FAFD" }
            GradientStop { position: 0.65; color: App.Theme.background }
            GradientStop { position: 1.0; color: App.Theme.dark ? "#080D14" : "#EAF0F7" }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 64 * page.couchScale
        anchors.rightMargin: 64 * page.couchScale
        anchors.topMargin: 112 * page.couchScale
        anchors.bottomMargin: 90 * page.couchScale
        spacing: 22 * page.couchScale

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 5 * page.couchScale

            Label {
                Layout.fillWidth: true
                text: qsTr("Couch Mode settings")
                color: App.Theme.text
                font.pixelSize: 42 * page.couchScale
                font.weight: Font.Bold
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Use the controller to choose a category and adjust its options.")
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.couchScale
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 22 * page.couchScale

            Rectangle {
                Layout.preferredWidth: 310 * page.couchScale
                Layout.fillHeight: true
                radius: 24 * page.couchScale
                color: App.Theme.dark ? "#D91A2432" : "#ECFFFFFF"
                border.width: 1
                border.color: App.Theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18 * page.couchScale
                    spacing: 12 * page.couchScale

                    Label {
                        Layout.fillWidth: true
                        Layout.leftMargin: 12 * page.couchScale
                        text: qsTr("Categories")
                        color: App.Theme.textSecondary
                        font.pixelSize: 17 * page.couchScale
                        font.weight: Font.DemiBold
                    }

                    Repeater {
                        model: page.categories
                        delegate: Rectangle {
                            id: categoryDelegate
                            required property var modelData
                            required property int index
                            readonly property bool active: page.activeCategoryIndex === index
                            Layout.fillWidth: true
                            Layout.preferredHeight: 84 * page.couchScale
                            radius: 16 * page.couchScale
                            color: active ? App.Theme.surfaceSelected : "transparent"
                            border.width: active ? 2 * page.couchScale : 0
                            border.color: active ? App.Theme.accent : "transparent"

                            Rectangle {
                                anchors.left: parent.left
                                anchors.leftMargin: 10 * page.couchScale
                                anchors.verticalCenter: parent.verticalCenter
                                width: 5 * page.couchScale
                                height: 42 * page.couchScale
                                radius: width / 2
                                color: App.Theme.accent
                                visible: categoryDelegate.active
                            }

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 28 * page.couchScale
                                anchors.rightMargin: 14 * page.couchScale
                                spacing: 2 * page.couchScale
                                Label {
                                    Layout.fillWidth: true
                                    text: categoryDelegate.modelData.title
                                    color: categoryDelegate.active ? App.Theme.text : App.Theme.textSecondary
                                    font.pixelSize: 21 * page.couchScale
                                    font.weight: categoryDelegate.active ? Font.Bold : Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: categoryDelegate.modelData.subtitle
                                    color: categoryDelegate.active ? App.Theme.textSecondary : App.Theme.textMuted
                                    font.pixelSize: 15 * page.couchScale
                                    elide: Text.ElideRight
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: page.selectCategory(categoryDelegate.index)
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }

                    Label {
                        Layout.fillWidth: true
                        Layout.leftMargin: 12 * page.couchScale
                        Layout.rightMargin: 12 * page.couchScale
                        text: qsTr("Use LB and RB to change category")
                        color: App.Theme.textMuted
                        font.pixelSize: 15 * page.couchScale
                        wrapMode: Text.WordWrap
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 24 * page.couchScale
                color: App.Theme.dark ? "#E6151D29" : "#F4FFFFFF"
                border.width: 1
                border.color: App.Theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 22 * page.couchScale
                    spacing: 14 * page.couchScale

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2 * page.couchScale
                        Label {
                            Layout.fillWidth: true
                            text: page.categories[page.activeCategoryIndex]
                                  ? page.categories[page.activeCategoryIndex].title : ""
                            color: App.Theme.text
                            font.pixelSize: 30 * page.couchScale
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Use left and right to change the selected value.")
                            color: App.Theme.textSecondary
                            font.pixelSize: 16 * page.couchScale
                        }
                    }

                    ListView {
                        id: settingsList
                        objectName: "couchSettingsList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: page.activeRows
                        spacing: 11 * page.couchScale
                        clip: true
                        interactive: contentHeight > height
                        boundsBehavior: Flickable.StopAtBounds

                        delegate: Rectangle {
                            id: settingDelegate
                            required property var modelData
                            required property int index
                            readonly property int globalIndex: page.globalIndexForId(modelData.id)
                            readonly property bool selected: page.selectedIndex === globalIndex
                            width: settingsList.width
                            height: 82 * page.couchScale
                            radius: 17 * page.couchScale
                            color: selected ? App.Theme.surfaceSelected : App.Theme.surfaceRaised
                            border.width: selected ? 4 * page.couchScale : 1
                            border.color: selected ? App.Theme.accent : App.Theme.border
                            opacity: modelData.enabled ? 1 : 0.58
                            scale: selected ? 1.015 : 1.0
                            transformOrigin: Item.Center

                            Behavior on scale {
                                NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
                            }
                            Behavior on color {
                                ColorAnimation { duration: 140 }
                            }

                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: 5 * page.couchScale
                                radius: 13 * page.couchScale
                                color: "transparent"
                                border.width: settingDelegate.selected ? 1 : 0
                                border.color: settingDelegate.selected ? App.Theme.accentGlow : "transparent"
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 22 * page.couchScale
                                anchors.rightMargin: 18 * page.couchScale
                                spacing: 18 * page.couchScale

                                Label {
                                    Layout.fillWidth: true
                                    text: settingDelegate.modelData.title
                                    color: App.Theme.text
                                    font.pixelSize: 21 * page.couchScale
                                    font.weight: settingDelegate.selected ? Font.Bold : Font.DemiBold
                                    elide: Text.ElideRight
                                }

                                Rectangle {
                                    Layout.preferredWidth: Math.max(150 * page.couchScale,
                                                                    valueLabel.implicitWidth + 34 * page.couchScale)
                                    Layout.maximumWidth: 410 * page.couchScale
                                    Layout.preferredHeight: 46 * page.couchScale
                                    radius: height / 2
                                    color: settingDelegate.modelData.enabled
                                           ? (settingDelegate.selected ? App.Theme.accentSoft : App.Theme.surface)
                                           : App.Theme.backgroundElevated
                                    border.width: 1
                                    border.color: settingDelegate.selected ? App.Theme.accent : App.Theme.border

                                    Label {
                                        id: valueLabel
                                        anchors.centerIn: parent
                                        width: Math.min(implicitWidth, parent.width - 24 * page.couchScale)
                                        text: settingDelegate.modelData.value
                                        color: settingDelegate.modelData.enabled
                                               ? (settingDelegate.selected ? App.Theme.accent : App.Theme.textSecondary)
                                               : App.Theme.textMuted
                                        font.pixelSize: 18 * page.couchScale
                                        font.weight: Font.Bold
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                enabled: settingDelegate.modelData.enabled
                                onClicked: {
                                    page.selectIndex(settingDelegate.globalIndex, true)
                                    page.activate()
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 126 * page.couchScale
                        radius: 17 * page.couchScale
                        color: App.Theme.dark ? "#C7101722" : "#EAF3F7FB"
                        border.width: 1
                        border.color: page.selectedRow.enabled ? App.Theme.borderStrong : App.Theme.warning

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 20 * page.couchScale
                            spacing: 16 * page.couchScale

                            Rectangle {
                                Layout.preferredWidth: 46 * page.couchScale
                                Layout.preferredHeight: 46 * page.couchScale
                                radius: width / 2
                                color: page.selectedRow.enabled ? App.Theme.accentSoft : App.Theme.warningSoft
                                Label {
                                    anchors.centerIn: parent
                                    text: page.selectedRow.enabled ? "i" : "!"
                                    color: page.selectedRow.enabled ? App.Theme.accent : App.Theme.warning
                                    font.pixelSize: 23 * page.couchScale
                                    font.weight: Font.Bold
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4 * page.couchScale
                                Label {
                                    Layout.fillWidth: true
                                    text: page.selectedRow.title || ""
                                    color: App.Theme.text
                                    font.pixelSize: 19 * page.couchScale
                                    font.weight: Font.Bold
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: page.selectedRow.description || ""
                                    color: App.Theme.textSecondary
                                    font.pixelSize: 16 * page.couchScale
                                    wrapMode: Text.WordWrap
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
