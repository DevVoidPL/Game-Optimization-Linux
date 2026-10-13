"""Couch Updates: real summary, stable section filters, measured values, focus."""

from __future__ import annotations

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QCoreApplication, QMetaObject, QObject, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config

COUCH = config.QML_DIR / "couch"


def _row(row_id: str, section: str, **extra) -> dict:
    row = {"rowId": row_id, "gameId": row_id, "name": f"Game {row_id}", "provider": "Steam",
           "sectionKey": section, "compressionState": "Analysis required", "libraryAvailable": True,
           "changedBytes": 0, "changesReliable": False, "canAnalyze": True}
    row.update(extra)
    return row


ROWS = [_row("a", "compression_pending"), _row("b", "game_updates", changedBytes=2048, changesReliable=True),
        _row("c", "recently_optimized", compressionState="Optimized")]


def _act(page: QObject, *actions: str) -> None:
    for action in actions:
        QMetaObject.invokeMethod(page, "handleAction", Qt.DirectConnection, Q_ARG("QVariant", action))
        QCoreApplication.processEvents()


def test_updates_screen_contract() -> None:
    engine = QQmlEngine()
    for name in ("CouchUpdates.qml", "CouchMain.qml"):
        assert QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / name))).isReady(), name
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "CouchUpdates.qml")))
    controller = {"updates": ROWS, "updatesSummary": {"needsCheckCount": 1, "pendingCount": 1}}
    page = component.createWithInitialProperties({"controller": controller, "width": 1920, "height": 1080})
    assert page is not None, [error.toString() for error in component.errors()]
    for _ in range(5):
        QCoreApplication.processEvents()

    # Recovered card is hidden: the summary has no measured recovery.
    assert [card["id"] for card in page.property("summaryCards").toVariant()] == ["attention", "pending"]
    assert [item["id"] for item in page.property("filterItems").toVariant()] == [
        "all", "compression_pending", "game_updates", "recently_optimized"]
    def changed(row: dict) -> str:
        result = QMetaObject.invokeMethod(page, "changedText", Qt.DirectConnection,
                                          Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", row))
        return result.toVariant() if hasattr(result, "toVariant") else result

    assert changed(ROWS[0]) == "Changed: Not measured" and changed(ROWS[1]) == "Changed: 2.0 KiB"

    nav = page.findChild(QObject, "couchUpdatesNavigation")
    assert nav.property("count") == 4 and nav.property("activeIndex") == 2
    _act(page, "NavigateDown")                                   # row a -> b
    _act(page, "NavigateUp", "NavigateUp")                        # b -> a -> filters
    assert page.property("focusZone") == 1
    _act(page, "NavigateRight", "NavigateRight", "Confirm")      # "game_updates"
    assert page.property("sectionFilter") == "game_updates" and page.property("selectedIndex") == 0
    _act(page, "NavigateDown", "NavigateDown")                   # list -> navigation
    assert page.property("focusZone") == 2
    _act(page, "NavigateUp")                                     # back to the last row
    assert page.property("focusZone") == 0 and page.property("retainedRowId") == "b"
    _act(page, "Confirm")                                        # existing per-row actions
    assert page.property("actionsOpen") is True
    _act(page, "Back")
    assert page.property("actionsOpen") is False
    page.deleteLater()
    QCoreApplication.processEvents()
