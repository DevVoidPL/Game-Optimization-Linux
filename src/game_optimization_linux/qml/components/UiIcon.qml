import QtQuick
import QtQuick.Effects

Image {
    id: icon

    property bool tintEnabled: false
    property color tintColor: "transparent"

    fillMode: Image.PreserveAspectFit
    cache: true
    smooth: true

    layer.enabled: tintEnabled
    layer.effect: MultiEffect {
        colorization: 1.0
        colorizationColor: icon.tintColor
    }
}
