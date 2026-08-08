import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as App

Button {
    id: control

    property string kind: "secondary" // primary, secondary, ghost, danger
    property string iconText: ""
    property bool compact: false
    property bool busy: false
    property string toolTip: ""

    implicitHeight: compact ? 36 : App.Theme.controlHeight
    implicitWidth: Math.max(compact ? 76 : 108, contentRow.implicitWidth + leftPadding + rightPadding)
    leftPadding: compact ? 12 : 17
    rightPadding: compact ? 12 : 17
    topPadding: 0
    bottomPadding: 0
    focusPolicy: Qt.StrongFocus
    opacity: enabled ? 1.0 : 0.55

    readonly property color baseColor: {
        if (kind === "primary")
            return App.Theme.accent
        if (kind === "danger")
            return App.Theme.danger
        if (kind === "ghost")
            return "transparent"
        return App.Theme.surfaceRaised
    }
    readonly property color foregroundColor: {
        if (kind === "primary")
            return App.Theme.textOnAccent
        if (kind === "danger")
            return App.Theme.textOnDanger
        return App.Theme.text
    }

    contentItem: RowLayout {
        id: contentRow
        spacing: 8

        BusyIndicator {
            visible: control.busy
            running: visible
            implicitWidth: 17
            implicitHeight: 17
            palette.dark: control.foregroundColor
        }

        Label {
            visible: !control.busy && control.iconText.length > 0
            text: control.iconText
            color: control.enabled ? control.foregroundColor : App.Theme.textMuted
            font.pixelSize: control.compact ? 14 : 16
            horizontalAlignment: Text.AlignHCenter
        }

        Label {
            visible: control.text.length > 0
            text: control.text
            color: control.enabled ? control.foregroundColor : App.Theme.textMuted
            font.pixelSize: App.Theme.fontBody
            font.weight: Font.DemiBold
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }
    }

    background: Rectangle {
        radius: App.Theme.radiusSmall
        color: {
            if (!control.enabled)
                return App.Theme.surfaceRaised
            if (control.down)
                return control.kind === "primary" ? App.Theme.accentHover : App.Theme.surfacePressed
            if (control.hovered) {
                if (control.kind === "primary")
                    return App.Theme.accentHover
                if (control.kind === "danger")
                    return Qt.lighter(App.Theme.danger, 1.08)
                return App.Theme.surfaceHover
            }
            return control.baseColor
        }
        border.width: control.visualFocus ? 2 : (control.kind === "secondary" ? 1 : 0)
        border.color: control.visualFocus ? App.Theme.accent : App.Theme.border

        Behavior on color {
            ColorAnimation { duration: App.Theme.animationFast }
        }
    }

    scale: down ? 0.98 : 1.0
    Behavior on scale {
        NumberAnimation { duration: App.Theme.animationFast; easing.type: Easing.OutCubic }
    }

    ToolTip.visible: toolTip.length > 0 && hovered
    ToolTip.text: toolTip
    ToolTip.delay: 500
}
