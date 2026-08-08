import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

Rectangle {
    id: hints
    property var buttonHints: ({})
    property real couchScale: 1.0
    property string acceptText: qsTr("Select")
    property string backText: qsTr("Back")
    property string menuText: qsTr("System menu")
    property string contextText: qsTr("More")
    property string sectionText: qsTr("Tabs")
    property bool showConfirm: true
    property bool showBack: true
    property bool showContext: true
    property bool showTabs: true
    property bool showMenu: true
    readonly property var visibleHints: buildHints()

    function buildHints() {
        var result = []
        if (showConfirm)
            result.push({ "button": String(buttonHints.confirm || buttonHints.accept || "A"), "label": acceptText })
        if (showBack)
            result.push({ "button": String(buttonHints.back || "B"), "label": backText })
        if (showContext)
            result.push({ "button": String(buttonHints.context || buttonHints.search || "Y"), "label": contextText })
        if (showTabs)
            result.push({ "button": String(buttonHints.pageLeft || buttonHints.previous || "L1") + "/" + String(buttonHints.pageRight || buttonHints.next || "R1"), "label": sectionText })
        if (showMenu)
            result.push({ "button": String(buttonHints.systemMenu || buttonHints.menu || "Menu"), "label": menuText })
        return result
    }

    implicitWidth: hintRow.implicitWidth + 26 * couchScale
    implicitHeight: 52 * couchScale
    radius: 18 * couchScale
    color: App.Theme.dark ? "#D31A2430" : "#E8FFFFFF"
    border.width: 1
    border.color: App.Theme.borderStrong

    RowLayout {
        id: hintRow
        anchors.centerIn: parent
        spacing: 25 * hints.couchScale

        Repeater {
            model: hints.visibleHints
            delegate: RowLayout {
                required property var modelData
                spacing: 9 * hints.couchScale
                Rectangle {
                    Layout.preferredWidth: Math.max(38 * hints.couchScale,
                                                    hintButton.implicitWidth
                                                    + 15 * hints.couchScale)
                    Layout.preferredHeight: 38 * hints.couchScale
                    radius: height / 2
                    color: App.Theme.surfaceSelected
                    border.width: 2
                    border.color: App.Theme.accent
                    Label {
                        id: hintButton
                        anchors.centerIn: parent
                        text: modelData.button
                        color: App.Theme.text
                        font.pixelSize: 14 * hints.couchScale
                        font.weight: Font.Bold
                    }
                }
                Label {
                    text: modelData.label
                    color: App.Theme.text
                    font.pixelSize: 16 * hints.couchScale
                    font.weight: Font.DemiBold
                }
            }
        }
    }
}
