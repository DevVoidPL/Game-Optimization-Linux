pragma ComponentBehavior: Bound

import QtQuick
import "../../src/game_optimization_linux/qml/components"

Item {
    id: root
    width: 720
    height: 420

    property var gamesData: []
    // -1: all, 0: even, 1: odd
    property int filterParity: -1
    readonly property var filteredGames: {
        if (filterParity < 0)
            return gamesData || []
        var result = []
        var source = gamesData || []
        for (var i = 0; i < source.length; ++i) {
            if (Number(source[i].sequence) % 2 === filterParity)
                result.push(source[i])
        }
        return result
    }

    GridView {
        id: reuseGrid
        objectName: "artworkReuseGrid"
        anchors.fill: parent
        clip: true
        reuseItems: true
        cacheBuffer: 0
        cellWidth: 144
        cellHeight: 210
        model: root.filteredGames

        delegate: Item {
            id: delegateRoot
            required property var modelData
            width: GridView.view.cellWidth
            height: GridView.view.cellHeight
            GridView.onPooled: reusedCover.markGridViewPooled(
                                   GridView.isCurrentItem)
            GridView.onReused: reusedCover.markGridViewReused(
                                   GridView.isCurrentItem)

            GameArtwork {
                id: reusedCover
                objectName: "reusedGameArtwork"
                width: 128
                height: 192
                anchors.centerIn: parent
                gameId: String(delegateRoot.modelData.id)
                title: String(delegateRoot.modelData.name)
                diagnosticViewKind: "gridview-probe"
                diagnosticGridViewIsCurrentItem: delegateRoot.GridView.isCurrentItem
                artworkSource: String(
                    delegateRoot.modelData.effectiveArtworkUrl || "")
                artworkFillMode: Image.PreserveAspectCrop
            }
        }
    }
}
