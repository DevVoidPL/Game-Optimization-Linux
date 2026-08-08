import QtQuick
import QtQuick.Controls
import ".." as App

Pane {
    id: control

    property bool interactive: false
    property bool selected: false
    property bool elevated: false

    padding: 18
    hoverEnabled: interactive

    background: Rectangle {
        radius: App.Theme.radiusLarge
        color: {
            if (control.selected)
                return App.Theme.surfaceSelected
            if (control.interactive && control.hovered)
                return App.Theme.surfaceHover
            return control.elevated ? App.Theme.surfaceRaised : App.Theme.surface
        }
        border.width: control.selected ? 2 : 1
        border.color: control.selected ? App.Theme.accent : App.Theme.border
        Behavior on color { ColorAnimation { duration: App.Theme.animationFast } }
        Behavior on border.color { ColorAnimation { duration: App.Theme.animationFast } }
    }

}
