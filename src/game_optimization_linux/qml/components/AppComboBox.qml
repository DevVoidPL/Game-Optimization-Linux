pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import ".." as App

ComboBox {
    id: control

    property string menuObjectName: "appComboBoxPopup"
    readonly property bool menuVisible: menuPopup.visible
    readonly property bool menuUsesWindowOverlay: Overlay.overlay !== null
                                                   && menuPopup.parent === Overlay.overlay
    readonly property real menuZ: menuPopup.z
    readonly property real menuX: menuPopup.x
    readonly property real menuY: menuPopup.y
    readonly property real menuWidth: menuPopup.width
    readonly property int menuClosePolicy: menuPopup.closePolicy
    readonly property bool closesOnOutside: Boolean(
                                                menuPopup.closePolicy
                                                & Popup.CloseOnPressOutside)

    function openMenu() {
        menuPopup.open()
    }

    function closeMenu() {
        menuPopup.close()
    }

    implicitHeight: App.Theme.controlHeight
    implicitWidth: 150
    leftPadding: 13
    rightPadding: 34
    focusPolicy: Qt.StrongFocus

    delegate: ItemDelegate {
        id: optionDelegate
        required property int index
        required property var modelData
        width: control.width
        height: 38
        highlighted: control.highlightedIndex === optionDelegate.index
        contentItem: Label {
            text: String(optionDelegate.modelData)
            color: optionDelegate.highlighted ? App.Theme.text : App.Theme.textSecondary
            font.pixelSize: App.Theme.fontBody
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: optionDelegate.highlighted ? App.Theme.surfaceHover : "transparent"
            radius: App.Theme.radiusSmall
        }
    }

    indicator: Label {
        x: control.width - width - 13
        anchors.verticalCenter: parent.verticalCenter
        text: "⌄"
        color: App.Theme.textSecondary
        font.pixelSize: 17
    }

    contentItem: Label {
        leftPadding: 0
        rightPadding: control.indicator.width + control.spacing
        text: control.displayText
        font.pixelSize: App.Theme.fontBody
        color: control.enabled ? App.Theme.text : App.Theme.textMuted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: App.Theme.radiusSmall
        color: control.pressed ? App.Theme.surfacePressed : App.Theme.input
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus ? App.Theme.accent : App.Theme.border
    }

    popup: Popup {
        id: menuPopup
        objectName: control.menuObjectName
        parent: Overlay.overlay

        function reposition() {
            var overlay = Overlay.overlay
            if (!overlay)
                return
            // Popup coordinates are relative to Overlay.overlay, not to the
            // ComboBox. Recalculate explicitly because mapToItem() does not
            // create bindings to movement of every visual ancestor (notably a
            // Flickable's contentY).
            var below = control.mapToItem(overlay, 0, control.height)
            var above = control.mapToItem(overlay, 0, 0)
            width = Math.max(control.width, 1)
            x = Math.max(5, Math.min(below.x, overlay.width - width - 5))
            var wantedBelow = below.y + 5
            y = wantedBelow + implicitHeight <= overlay.height - 5
                    ? wantedBelow
                    : Math.max(5, above.y - implicitHeight - 5)
        }

        x: 0
        y: 0
        z: 10000
        width: Math.max(control.width, 1)
        implicitHeight: Math.min(contentItem.implicitHeight + 10, 300)
        padding: 5
        modal: false
        dim: false
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                     | Popup.CloseOnReleaseOutside

        onAboutToShow: {
            App.PopupCoordinator.opening(menuPopup)
            reposition()
        }
        onOpened: Qt.callLater(reposition)
        onClosed: {
            App.PopupCoordinator.closed(menuPopup)
            if (control.visible && control.enabled)
                control.forceActiveFocus(Qt.PopupFocusReason)
        }

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator { }
        }

        background: Rectangle {
            radius: App.Theme.radiusMedium
            color: App.Theme.surfaceRaised
            border.width: 1
            border.color: App.Theme.borderStrong
        }

        Timer {
            // Ancestor motion is not observable through a generic ComboBox.
            // While the small menu is open, keep it attached to the control;
            // the timer stops immediately when the popup closes.
            interval: 33
            repeat: true
            running: menuPopup.visible
            onTriggered: menuPopup.reposition()
        }
    }
}
