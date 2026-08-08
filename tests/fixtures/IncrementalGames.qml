pragma ComponentBehavior: Bound

import QtQuick
import "../../src/game_optimization_linux/qml/components"

Item {
    id: root
    width: 720
    height: 420

    property var gamesModel: null
    property int nextCreationSerial: 1

    Grid {
        width: parent.width
        columns: 5
        spacing: 8

        Repeater {
            model: root.gamesModel

            delegate: Item {
                id: gameDelegate
                required property var modelData
                property int creationSerial: -1
                objectName: "incrementalGameDelegate"
                width: 136
                height: 202

                Component.onCompleted: {
                    creationSerial = root.nextCreationSerial
                    root.nextCreationSerial += 1
                }

                GameArtwork {
                    objectName: "incrementalGameArtwork"
                    width: parent.width
                    height: 192
                    gameId: String(gameDelegate.modelData.id)
                    title: String(gameDelegate.modelData.name)
                    diagnosticViewKind: "incremental-model-probe"
                    artworkSource: String(
                        gameDelegate.modelData.effectiveArtworkUrl || "")
                    artworkFillMode: Image.PreserveAspectCrop
                }
            }
        }
    }
}
