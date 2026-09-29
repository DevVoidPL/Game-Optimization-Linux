import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../.." as App

// Compact Couch Mode header. All user-visible strings are supplied by the
// owner (CouchMain) so they stay in its translation context.
//
// Right side, in order: active input device, real system telemetry chips
// (hidden when disabled or unreadable), and the clock.
// Settings live in the bottom navigation, so there is no duplicate here.
Rectangle {
    id: bar
    objectName: "couchTopBar"

    property real couchScale: 1.0
    property string appName: ""
    property url appLogo: ""
    property string sectionTitle: ""
    property string inputLabel: ""
    property bool inputIsController: false
    // CPU/GPU/RAM chips are placed into this slot by the owner, which also
    // says whether any of them is shown. (A visibleChildren check cannot work:
    // children of an invisible slot report visible=false themselves.)
    default property alias telemetry: telemetrySlot.data
    property bool telemetryShown: false
    property date now: new Date()
    readonly property string clockText: Qt.formatTime(now, "HH:mm")
    readonly property bool telemetryVisible: telemetryShown

    implicitHeight: 84 * couchScale
    color: "transparent"

    function refreshClock() {
        now = new Date()
        // Wake up just after the next minute boundary instead of polling.
        clockTimer.interval = 60000 - (now.getTime() % 60000) + 50
        clockTimer.restart()
    }

    Timer {
        id: clockTimer
        repeat: false
        onTriggered: bar.refreshClock()
    }
    onVisibleChanged: {
        if (visible)
            refreshClock()
        else
            clockTimer.stop()
    }
    Component.onCompleted: refreshClock()

    // Soft top shade so the bar stays readable over hero artwork.
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(App.Theme.background.r, App.Theme.background.g, App.Theme.background.b, App.Theme.dark ? 0.72 : 0.8) }
            GradientStop { position: 1.0; color: Qt.rgba(App.Theme.background.r, App.Theme.background.g, App.Theme.background.b, 0.0) }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: App.Theme.couchPageMargin * bar.couchScale
        anchors.rightMargin: App.Theme.couchPageMargin * bar.couchScale
        spacing: App.Theme.couchSpaceM * bar.couchScale

        Rectangle {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 46 * bar.couchScale
            Layout.preferredHeight: 46 * bar.couchScale
            radius: App.Theme.couchRadiusSmall * bar.couchScale
            color: App.Theme.surfaceRaised
            border.width: 1
            border.color: App.Theme.border
            Image {
                anchors.centerIn: parent
                width: 34 * bar.couchScale
                height: 34 * bar.couchScale
                source: bar.appLogo
                sourceSize.width: Math.ceil(34 * bar.couchScale)
                sourceSize.height: Math.ceil(34 * bar.couchScale)
                fillMode: Image.PreserveAspectFit
                visible: status === Image.Ready
            }
        }
        ColumnLayout {
            Layout.alignment: Qt.AlignVCenter
            spacing: 0
            Label {
                objectName: "couchTopBarAppName"
                text: bar.appName
                color: App.Theme.text
                font.pixelSize: 20 * bar.couchScale
                font.weight: Font.Bold
            }
            Label {
                objectName: "couchTopBarSection"
                text: bar.sectionTitle
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchCaptionSize * bar.couchScale
            }
        }
        Item { Layout.fillWidth: true }

        // Active input device.
        Rectangle {
            objectName: "couchTopBarInput"
            Layout.alignment: Qt.AlignVCenter
            implicitHeight: 44 * bar.couchScale
            implicitWidth: inputRow.implicitWidth + 2 * App.Theme.couchSpaceM * bar.couchScale
            radius: App.Theme.couchRadiusSmall * bar.couchScale
            color: Qt.rgba(App.Theme.surfaceRaised.r, App.Theme.surfaceRaised.g,
                           App.Theme.surfaceRaised.b, 0.82)
            border.width: 1
            border.color: App.Theme.border
            Accessible.role: Accessible.StaticText
            Accessible.name: bar.inputLabel
            Row {
                id: inputRow
                anchors.centerIn: parent
                spacing: App.Theme.couchSpaceS * bar.couchScale
                CouchIcon {
                    objectName: "couchTopBarInputIcon"
                    anchors.verticalCenter: parent.verticalCenter
                    size: App.Theme.couchIconSmall
                    couchScale: bar.couchScale
                    source: bar.inputIsController ? App.UiIcons.couchGlyphController : App.UiIcons.couchGlyphKeyboard
                    lightSource: bar.inputIsController ? App.UiIcons.couchGlyphControllerOnLight : App.UiIcons.couchGlyphKeyboardOnLight
                }
                Rectangle {
                    visible: bar.inputIsController
                    anchors.verticalCenter: parent.verticalCenter
                    width: 9 * bar.couchScale
                    height: width
                    radius: width / 2
                    color: App.Theme.success
                }
                Label {
                    objectName: "couchTopBarInputLabel"
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, 260 * bar.couchScale)
                    text: bar.inputLabel
                    color: App.Theme.text
                    font.pixelSize: App.Theme.couchLabelSize * bar.couchScale
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
            }
        }

        // Reserved for real CPU/GPU/RAM chips (separate backend stage).
        Row {
            id: telemetrySlot
            objectName: "couchTopBarTelemetry"
            Layout.alignment: Qt.AlignVCenter
            visible: bar.telemetryVisible
            spacing: App.Theme.couchSpaceM * bar.couchScale
        }

        Rectangle {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: Math.max(1, Math.round(bar.couchScale))
            Layout.preferredHeight: 30 * bar.couchScale
            color: App.Theme.borderStrong
        }
        Label {
            objectName: "couchTopBarClock"
            Layout.alignment: Qt.AlignVCenter
            text: bar.clockText
            color: App.Theme.text
            font.pixelSize: 26 * bar.couchScale
            font.weight: Font.DemiBold
        }
    }
}
