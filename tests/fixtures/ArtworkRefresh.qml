pragma ComponentBehavior: Bound

import QtQuick
import "../../src/game_optimization_linux/qml/components"

Item {
    id: root
    width: 720
    height: 420

    property var gamesData: []
    property bool filtered: false
    property bool listMode: false
    readonly property var filteredGames: {
        var source = gamesData || []
        if (!filtered)
            return source
        var result = []
        for (var i = 0; i < source.length; ++i) {
            var appId = String(source[i].appId || "")
            if (appId === "242550" || appId === "204360"
                    || Number(source[i].sequence) % 2 === 1)
                result.push(source[i])
        }
        return result
    }

    Loader {
        anchors.fill: parent
        sourceComponent: root.listMode ? listComponent : gridComponent
    }

    Component {
        id: gridComponent

        Flickable {
            id: refreshFlick
            objectName: "artworkRefreshFlick"
            clip: true
            contentWidth: width
            contentHeight: refreshGrid.height

            Grid {
                id: refreshGrid
                objectName: "artworkRefreshGrid"
                width: refreshFlick.width
                columns: 5
                columnSpacing: 8
                rowSpacing: 8
                height: Math.ceil(refreshRepeater.count / columns) * 210

                Repeater {
                    id: refreshRepeater
                    model: root.filteredGames

                    delegate: Item {
                        id: delegateRoot
                        required property var modelData
                        width: (refreshGrid.width - 32) / 5
                        height: 202

                        GameArtwork {
                            objectName: "refreshGameArtwork"
                            width: parent.width
                            height: 192
                            gameId: String(delegateRoot.modelData.id)
                            title: String(delegateRoot.modelData.name)
                            diagnosticViewKind: "grid"
                            artworkSource: String(
                                delegateRoot.modelData.effectiveArtworkUrl || "")
                            artworkFillMode: Image.PreserveAspectCrop
                        }
                    }
                }
            }
        }
    }

    Component {
        id: listComponent

        Flickable {
            objectName: "artworkRefreshListFlick"
            clip: true
            contentWidth: width
            contentHeight: listColumn.height

            Column {
                id: listColumn
                width: parent.width
                spacing: 6

                Repeater {
                    model: root.filteredGames

                    delegate: Item {
                        id: listDelegate
                        required property var modelData
                        width: listColumn.width
                        height: 82

                        GameArtwork {
                            objectName: "refreshGameArtwork"
                            width: 128
                            height: 72
                            anchors.verticalCenter: parent.verticalCenter
                            gameId: String(listDelegate.modelData.id)
                            title: String(listDelegate.modelData.name)
                            diagnosticViewKind: "list"
                            artworkSource: String(
                                listDelegate.modelData.effectiveArtworkUrl || "")
                            artworkFillMode: Image.PreserveAspectFit
                        }
                    }
                }
            }
        }
    }
}
