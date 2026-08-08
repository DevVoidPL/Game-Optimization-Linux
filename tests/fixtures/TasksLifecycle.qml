import QtQuick
import QtQuick.Layouts
import "../../src/gameforge/qml/pages" as Pages
import "../../src/gameforge/qml/pages/details" as Details

Item {
    id: root
    property var controller: null
    property int currentIndex: 0

    StackLayout {
        anchors.fill: parent
        currentIndex: root.currentIndex

        Pages.GamesPage {
            objectName: "lifecycleGamesPage"
            controller: root.controller
        }

        Details.StorageTab {
            objectName: "lifecycleStorageTab"
            controller: root.controller
            gameData: root.controller && root.controller.selectedGame ? root.controller.selectedGame : ({})
            tasksData: root.controller && root.controller.tasks ? root.controller.tasks : []
        }

        Pages.TasksPage {
            objectName: "lifecycleTasksPage"
            controller: root.controller
        }
    }
}
