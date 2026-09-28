"""Couch Settings: motion mode persistence, row kinds, safe confirmation, nav."""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest
from PySide6.QtTest import QTest

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QCoreApplication, QMetaObject, QObject, Property, Qt, QUrl, Signal, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config
from game_optimization_linux.models import AppSettings
from game_optimization_linux.services import SettingsStore
from game_optimization_linux.translations import TranslationManager

COUCH = config.QML_DIR / "couch"


class FakeSettings(QObject):
    settingsChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.saved: list[tuple[str, object]] = []
        self._settings = {"couchMotionMode": "full", "automaticCompressionMode": "Off"}

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        return self._settings

    @Slot(str, "QVariant", result=bool)
    def saveSetting(self, key, value):
        self.saved.append((key, value))
        self._settings = {**self._settings, key: value}
        self.settingsChanged.emit()
        return True


def test_motion_mode_round_trips_and_defaults(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    assert AppSettings().couch_motion_mode == "full"
    for mode in ("reduced", "off", "full"):
        store.save(AppSettings(couch_motion_mode=mode))
        assert store.load().couch_motion_mode == mode
    assert AppSettings.from_dict({"couch_motion_mode": "Fancy"}).couch_motion_mode == "full"


def test_settings_screen_contract() -> None:
    engine = QQmlEngine()
    for name in ("CouchSettings.qml", "CouchMain.qml"):
        assert QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / name))).isReady(), name
    controller = FakeSettings()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "CouchSettings.qml")))
    page = component.createWithInitialProperties({"controller": controller, "width": 1920, "height": 1080})
    assert page is not None, [error.toString() for error in component.errors()]
    QCoreApplication.processEvents()

    def act(*actions: str) -> None:
        for action in actions:
            QMetaObject.invokeMethod(page, "handleAction", Qt.DirectConnection, Q_ARG("QVariant", action))
            QCoreApplication.processEvents()

    rows = {row["id"]: row for row in page.property("rows").toVariant()}
    assert {"language", "appearance", "couch-motion", "swap", "deadzone", "backup-path", "reset",
            "desktop", "menu-volume", "experimental"} <= set(rows)
    nav = page.findChild(QObject, "couchSettingsNavigation")
    assert nav.property("count") == 4 and nav.property("activeIndex") == 3

    QMetaObject.invokeMethod(page, "selectIndex", Qt.DirectConnection, Q_ARG("QVariant", list(rows).index("couch-motion")),
                             Q_ARG("QVariant", True))
    act("NavigateRight")
    assert controller.saved[-1] == ("couchMotionMode", "reduced")      # stable id, not a label

    QMetaObject.invokeMethod(page, "selectIndex", Qt.DirectConnection, Q_ARG("QVariant", list(rows).index("reset")),
                             Q_ARG("QVariant", True))
    before = len(controller.saved)
    act("Confirm")
    assert page.property("confirmationOpen") is True and page.property("confirmChoice") == 0
    act("Confirm")                                                     # safe default: Cancel
    assert page.property("confirmationOpen") is False and len(controller.saved) == before
    act("Back")                                                        # settings -> categories
    assert page.property("focusZone") == 0
    page.deleteLater()
    QCoreApplication.processEvents()


# ---- Visual contracts, measured in a real (offscreen) window, in Polish -------

_PROBE = """
    function probe() {
        var out = []
        function walk(item) {
            if (%s) {
                var p = item.mapToItem(null, 0, 0)
                var entry = { "name": item.objectName, "x": Math.round(p.x), "w": Math.round(item.width),
                              "h": Math.round(item.height), "truncated": item.truncated === true,
                              "text": item.text === undefined ? "" : String(item.text) }
                if (item.objectName === "couchKeyboardKey") {
                    var label = null
                    function find(i) { if (i.objectName === "couchKeyboardKeyLabel") label = i
                                       for (var k = 0; k < i.children.length; ++k) find(i.children[k]) }
                    find(item)
                    var kc = item.mapToItem(null, item.width / 2, item.height / 2)
                    var lc = label.mapToItem(null, label.width / 2, label.height / 2)
                    entry.action = item.modelData.action
                    entry.dx = lc.x - kc.x; entry.dy = lc.y - kc.y
                }
                out.push(entry)
            }
            for (var i = 0; i < item.children.length; ++i) walk(item.children[i])
        }
        walk(this)
        return out
    }
"""


def _view(component: str, directory: Path, condition: str, width: int, height: int, props: dict):
    from PySide6.QtQuick import QQuickView

    source = f'import QtQuick\nimport "{QUrl.fromLocalFile(str(directory)).toString()}"\n{component} {{\n{_PROBE % condition}\n}}\n'
    path = Path(tempfile.gettempdir()) / f"_Probe{component}.qml"   # never inside the source tree
    view = QQuickView()
    view.engine().addImportPath(str(config.QML_DIR))
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(width, height)
    view.setInitialProperties({"couchScale": max(0.82, min(1.8, width / 1920)), **props})
    component_obj = QQmlComponent(view.engine())
    component_obj.setData(source.encode(), QUrl.fromLocalFile(str(path)))
    assert component_obj.isReady(), [error.toString() for error in component_obj.errors()]
    view.setContent(QUrl.fromLocalFile(str(path)), component_obj, component_obj.createWithInitialProperties(
        {"couchScale": max(0.82, min(1.8, width / 1920)), "width": width, "height": height, **props}))
    view.show()
    QTest.qWait(250)
    return view, view.rootObject()


def _probe(root) -> list[dict]:
    result = QMetaObject.invokeMethod(root, "probe", Qt.DirectConnection, Q_RETURN_ARG("QVariant"))
    return result.toVariant() if hasattr(result, "toVariant") else result


def _act(root, *actions: str) -> None:
    for action in actions:
        QMetaObject.invokeMethod(root, "handleAction", Qt.DirectConnection, Q_ARG("QVariant", action))
        QTest.qWait(60)


@pytest.fixture()
def polish():
    manager = TranslationManager(QCoreApplication.instance())
    manager.set_language("pl")
    yield manager
    manager.set_language("en")


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (1280, 720)])
def test_settings_rows_align_and_polish_titles_fit(polish, width, height) -> None:
    controller = FakeSettings()
    condition = 'item.objectName === "couchSettingsCategoryCard" || item.objectName === "couchSettingRowCard" || item.objectName === "couchSettingTitle"'
    view, root = _view("CouchSettings", COUCH, condition, width, height, {"controller": controller})
    try:
        def geometry(name):
            return {(item["x"], item["w"]) for item in _probe(root) if item["name"] == name}
        before = (geometry("couchSettingsCategoryCard"), geometry("couchSettingRowCard"))
        assert all(len(group) == 1 for group in before)          # one x / width per list
        _act(root, "Back", "NavigateDown", "NavigateDown")      # focus moves through categories
        _act(root, "NavigateRight", "NavigateDown")             # and through rows
        assert geometry("couchSettingsCategoryCard") == before[0]
        assert geometry("couchSettingRowCard") == before[1]
        for category in range(7):
            QMetaObject.invokeMethod(root, "selectCategory", Qt.DirectConnection, Q_ARG("QVariant", category))
            QTest.qWait(80)
            titles = [item for item in _probe(root) if item["name"] == "couchSettingTitle"]
            assert titles and not [item["text"] for item in titles if item["truncated"]], category
    finally:
        view.close()


def test_on_screen_keyboard_keys_are_uniform_centred_and_navigable(polish) -> None:
    view, root = _view("CouchOnScreenKeyboard", COUCH / "components", 'item.objectName === "couchKeyboardKey"',
                       1920, 1080, {"buttonHints": {"back": "B"}})
    try:
        QMetaObject.invokeMethod(root, "open", Qt.DirectConnection, Q_ARG("QVariant", ""), Q_ARG("QVariant", "Test"))
        QTest.qWait(250)
        keys = _probe(root)
        characters = [key for key in keys if key["action"] == "character"]
        assert len(characters) == 35 and len({(key["w"], key["h"]) for key in characters}) == 1
        assert all(abs(key["dx"]) <= 1 and abs(key["dy"]) <= 1 for key in keys
                   if key["action"] in ("character", "shift", "symbols", "space", "clear"))
        actions = [key["action"] for key in root.property("keyModel").toVariant()]
        assert actions[35:] == ["shift", "symbols", "space", "backspace", "clear", "cancel", "confirm"]
        assert [key["label"] for key in root.property("keyModel").toVariant()][:10] == list("qwertyuiop")
        # Up/down go to the nearest key of the adjacent (shorter/longer) row.
        visited = set()
        for action in ("NavigateDown",) * 6 + ("NavigateRight",) * 3 + ("NavigateUp",) * 6:
            _act(root, action)
            visited.add(int(root.property("selectedIndex")))
        assert all(0 <= index < len(actions) for index in visited) and len(visited) > 6
        assert root.property("opened") is True
    finally:
        view.close()


def test_hints_use_keyboard_keys_when_no_controller_is_active() -> None:
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "components" / "CouchHints.qml")))
    generic = {"confirm": "South", "back": "East"}
    hints = component.createWithInitialProperties({"buttonHints": generic, "keyboardInput": True})
    assert [h["button"] for h in hints.property("visibleHints").toVariant()][:2] == ["Enter", "Esc"]
    hints.setProperty("keyboardInput", False)                 # a real controller keeps its own names
    assert [h["button"] for h in hints.property("visibleHints").toVariant()][:2] == ["South", "East"]
