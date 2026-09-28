"""Couch Tasks: real task fields, honest progress, active navigation, action ids."""

from __future__ import annotations

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, Property, QCoreApplication, QMetaObject, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config

COUCH = config.QML_DIR / "couch"


def _task(task_id: str, status: str, **extra) -> dict:
    task = {"id": task_id, "gameId": task_id, "gameName": f"Game {task_id}", "operation": "Compression",
            "status": status, "progress": 0.4, "progressDeterminate": True, "cancellable": True, "error": ""}
    task.update(extra)
    return task


TASKS = [_task("a", "running"), _task("b", "analyzing", operation="Analysis", progressDeterminate=False),
         _task("c", "queued"), _task("d", "failed", error="Disk full")]


def test_tasks_screen_contract() -> None:
    engine = QQmlEngine()
    for name in ("CouchTasks.qml", "CouchMain.qml"):
        assert QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / name))).isReady(), name
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "CouchTasks.qml")))
    page = component.createWithInitialProperties({"controller": {"tasks": TASKS}, "width": 1920, "height": 1080})
    assert page is not None, [error.toString() for error in component.errors()]
    QCoreApplication.processEvents()

    def call(name: str, task: dict):
        result = QMetaObject.invokeMethod(page, name, Qt.DirectConnection,
                                          Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", task))
        return result.toVariant() if hasattr(result, "toVariant") else result

    assert call("progressText", TASKS[0]) == "40%"               # real determinate progress
    assert call("progressText", TASKS[1]) == "Progress: no data"  # no fake 0%
    assert call("progressText", TASKS[2]) == "" and call("progressText", TASKS[3]) == ""
    assert call("typeLabel", TASKS[1]) == "Analysis" and call("messageText", TASKS[3]) == "Disk full"
    assert [s["section"] for s in page.property("summarySections").toVariant()] == ["active", "queued", "recent"]
    assert [a["id"] for a in page.property("taskActions").toVariant()] == ["cancel-task"]

    nav = page.findChild(type(page), "couchTasksNavigation")
    assert nav.property("count") == 4 and nav.property("activeIndex") == 1

    def act(*actions: str) -> None:
        for action in actions:
            QMetaObject.invokeMethod(page, "handleAction", Qt.DirectConnection, Q_ARG("QVariant", action))
            QCoreApplication.processEvents()

    act("NavigateDown")
    assert page.property("retainedTaskId") == "b"
    act("NavigateDown", "NavigateDown", "NavigateDown")           # last row -> navigation
    assert page.property("focusZone") == 2
    act("NavigateUp")                                             # back to the last task
    assert page.property("focusZone") == 0 and page.property("retainedTaskId") == "d"
    act("NavigateUp", "Confirm")                                  # queued, cancellable
    assert page.property("cancellationOpen") is True
    act("Back")
    assert page.property("cancellationOpen") is False
    page.deleteLater()
    QCoreApplication.processEvents()


class _HistoryController(QObject):
    tasksChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._tasks = [_task("run", "running"), _task("old", "completed"), _task("bad", "failed")]
        self.removed: list[str] = []
        self.cleared = 0

    @Property("QVariantList", notify=tasksChanged)
    def tasks(self):
        return self._tasks

    @Slot(str, result=bool)
    def removeFinishedTask(self, task_id):
        self.removed.append(task_id)
        self._tasks = [task for task in self._tasks if task["id"] != task_id]
        self.tasksChanged.emit()
        return True

    @Slot(result=int)
    def clearFinishedTasks(self):
        self.cleared += 1
        return 1


def test_history_delete_needs_confirmation_and_targets_finished_records() -> None:
    engine = QQmlEngine()
    controller = _HistoryController()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "CouchTasks.qml")))
    page = component.createWithInitialProperties({"controller": controller, "width": 1920, "height": 1080})
    assert page is not None, [error.toString() for error in component.errors()]
    QCoreApplication.processEvents()

    def act(*actions: str) -> None:
        for action in actions:
            QMetaObject.invokeMethod(page, "handleAction", Qt.DirectConnection, Q_ARG("QVariant", action))
            QCoreApplication.processEvents()

    act("NavigateDown", "Confirm")                        # "old" -> history menu
    assert page.property("actionMenuOpen") is True
    assert [a["id"] for a in page.property("historyActions").toVariant()] == ["delete"]
    act("Confirm")                                        # Delete -> confirmation, default Cancel
    assert page.property("deleteConfirm") == "one" and page.property("deleteChoice") == 0
    act("Confirm")                                        # Cancel deletes nothing
    assert controller.removed == [] and page.property("overlayOpen") is False
    act("Confirm", "Confirm", "NavigateRight", "Confirm")
    assert controller.removed == ["old"]
    act("MoreActions")                                    # Y: delete all finished
    assert page.property("deleteConfirm") == "all"
    act("Back")
    assert controller.removed == ["old"] and page.property("overlayOpen") is False
    act("MoreActions", "NavigateRight", "Confirm")          # exactly the counted, visible entries
    assert controller.removed == ["old", "bad"] and [t["id"] for t in controller._tasks] == ["run"]
    page.deleteLater()
    QCoreApplication.processEvents()


def test_routine_tasks_hidden_when_done_but_failed_shown() -> None:
    engine = QQmlEngine()
    tasks = [_task("scan", "completed", operation="Library scan", gameId=""),
             _task("size", "completed", operation="Size calculation"),
             _task("scan-bad", "failed", operation="Library scan", gameId="", error="Disk"),
             _task("done", "completed")]
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "CouchTasks.qml")))
    page = component.createWithInitialProperties({"controller": {"tasks": tasks}, "width": 1920, "height": 1080})
    assert page is not None, [error.toString() for error in component.errors()]
    QCoreApplication.processEvents()
    shown = [row["rowId"] for row in page.property("visibleTasks").toVariant()]
    assert shown == ["scan-bad", "done"] and page.property("removableCount") == 2
    label = QMetaObject.invokeMethod(page, "typeLabel", Qt.DirectConnection,
                                     Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", tasks[2]))
    assert (label.toVariant() if hasattr(label, "toVariant") else label) == "Library scan"
    page.deleteLater()
    QCoreApplication.processEvents()
