import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

Item {
    id: host

    function show(message, tone, duration) {
        toastModel.append({
            "message": String(message || "Done"),
            "tone": String(tone || "info"),
            "duration": Number(duration || 3600)
        })
        if (toastModel.count > 4)
            toastModel.remove(0)
    }

    function showSingle(message, tone, duration) {
        // TV notifications replace one another so a burst of setting changes
        // cannot cover the focused row from several metres away.
        toastModel.clear()
        show(message, tone, duration)
    }

    function dismiss(message) {
        var expected = String(message || "")
        for (var index = toastModel.count - 1; index >= 0; --index) {
            if (String(toastModel.get(index).message) === expected)
                toastModel.remove(index)
        }
    }

    function colorFor(tone) {
        if (tone === "success") return App.Theme.success
        if (tone === "warning") return App.Theme.warning
        if (tone === "error") return App.Theme.danger
        return App.Theme.info
    }

    ListModel { id: toastModel }

    ListView {
        anchors.fill: parent
        anchors.margins: 20
        interactive: false
        spacing: 10
        verticalLayoutDirection: ListView.BottomToTop
        model: toastModel

        add: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: App.Theme.animationNormal }
        }

        delegate: Rectangle {
            id: toast
            required property int index
            required property string message
            required property string tone
            required property real duration

            width: Math.min(390, ListView.view.width)
            height: Math.max(58, toastRow.implicitHeight + 22)
            x: ListView.view.width - width
            radius: App.Theme.radiusMedium
            color: App.Theme.surfaceRaised
            border.width: 1
            border.color: host.colorFor(tone)

            RowLayout {
                id: toastRow
                anchors.fill: parent
                anchors.margins: 11
                spacing: 10

                Rectangle {
                    Layout.preferredWidth: 8
                    Layout.fillHeight: true
                    Layout.maximumHeight: 30
                    Layout.alignment: Qt.AlignVCenter
                    radius: 4
                    color: host.colorFor(toast.tone)
                }

                Label {
                    Layout.fillWidth: true
                    text: App.I18n.message(toast.message)
                    color: App.Theme.text
                    font.pixelSize: App.Theme.fontBody
                    wrapMode: Text.WordWrap
                }

                IconButton {
                    symbol: "×"
                    toolTip: qsTr("Dismiss")
                    onClicked: toastModel.remove(toast.index)
                }
            }

            Timer {
                interval: toast.duration
                running: true
                onTriggered: {
                    if (toast.index >= 0 && toast.index < toastModel.count)
                        toastModel.remove(toast.index)
                }
            }

        }
    }
}
