"""Focus and game changes through the real Couch editors."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from game_optimization_linux.config import QML_DIR
from test_couch_details import GAME, _call, _load, _plain, _settle
from test_couch_feature_parity import Controller, choose


class DetailsController(Controller):
    @Slot(str, result="QVariantMap")
    def getOptiScalerStatus(self, key):
        return {}

    @Slot(str, str, str, result=bool)
    def finalizeDeepQuarantine(self, key, transaction, word):
        self.calls.append(("finalize", key, transaction, word))
        return word == "DELETE"


def open_text_input(root, target):
    choose(root, target)
    picker = root.findChild(QObject, "couchExecutablePicker")
    if picker and picker.property("opened"):
        menu = next(child for child in picker.findChildren(QObject)
                    if child.metaObject().indexOfProperty("rows") >= 0)
        menu.setProperty("currentIndex", 0)
        _call(picker, "handlePadAction", "Confirm")
    return next(child for child in root.findChildren(QObject, "couchOnScreenKeyboard")
                if child.property("opened"))


@pytest.fixture
def view_editor():
    views = []

    def create(name):
        controller = DetailsController()
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.setInitialProperties({"controller": controller,
                                   "gameData": {"id": "first", "launcher": "Heroic"}})
        view.setSource(QUrl.fromLocalFile(str(QML_DIR / "couch" / name)))
        assert view.rootObject() is not None, view.errors()
        view.resize(1000, 720)
        view.show()
        QTest.qWait(30)
        views.append((view, controller))
        return view, view.rootObject(), controller

    yield create
    for view, _ in reversed(views):
        view.close()
        view.deleteLater()
    QTest.qWait(10)


@pytest.mark.parametrize("name,target", [
    ("CouchPostProcessingEditor.qml", "executable"),
    ("CouchFrameGenerationEditor.qml", "dll"),
    ("CouchDeepStorageEditor.qml", "export"),
])
def test_keyboard_keeps_focus_after_confirm_and_refresh(view_editor, name, target):
    view, root, controller = view_editor(name)
    if target == "dll":
        choose(root, "backend", "NavigateRight")
        choose(root, "backend", "NavigateRight")
    keyboard = open_text_input(root, target)
    QTest.qWait(30)
    assert keyboard.property("opened")
    assert keyboard.hasActiveFocus()
    _call(root, "refresh")
    QTest.qWait(30)
    assert keyboard.hasActiveFocus()
    _call(root, "handlePadAction", "Back")
    assert not keyboard.property("opened")
    picker = root.findChild(QObject, "couchExecutablePicker")
    if picker and picker.property("opened"):
        _call(root, "handlePadAction", "Back")
    assert view.activeFocusItem().objectName() == "couchOption-" + target
    assert not controller.calls


@pytest.mark.parametrize("name,target", [
    ("CouchPostProcessingEditor.qml", "executable"),
    ("CouchFrameGenerationEditor.qml", "dll"),
    ("CouchDeepStorageEditor.qml", "export"),
])
def test_switching_game_cancels_keyboard_without_saving(view_editor, name, target):
    _, root, controller = view_editor(name)
    if target == "dll":
        choose(root, "backend", "NavigateRight")
        choose(root, "backend", "NavigateRight")
    keyboard = open_text_input(root, target)
    assert keyboard.property("opened")
    root.setProperty("gameData", {"id": "second", "launcher": "Heroic"})
    QTest.qWait(20)
    assert not keyboard.property("opened")
    assert not controller.calls


def test_deep_storage_selection_does_not_carry_to_another_game(view_editor):
    _, root, controller = view_editor("CouchDeepStorageEditor.qml")
    choose(root, "safe")
    analysis = root.findChild(QObject, "deepAnalysisSection")
    assert _plain(analysis.property("selectedGroups")) == {"safe": True}
    root.setProperty("gameData", {"id": "second", "launcher": "Heroic"})
    QTest.qWait(20)
    assert not _plain(analysis.property("selectedGroups"))
    assert not controller.calls


@pytest.mark.parametrize("name,target", [
    ("CouchPostProcessingEditor.qml", "executable"),
    ("CouchFrameGenerationEditor.qml", "dll"),
    ("CouchDeepStorageEditor.qml", "export"),
])
def test_same_game_metadata_refresh_keeps_keyboard_open(view_editor, name, target):
    _, root, controller = view_editor(name)
    if target == "dll":
        choose(root, "backend", "NavigateRight")
        choose(root, "backend", "NavigateRight")
    keyboard = open_text_input(root, target)
    root.setProperty("gameData", {"id": "first", "launcher": "Heroic", "name": "Refreshed"})
    QTest.qWait(20)
    assert keyboard.property("opened")
    assert not controller.calls


def test_quarantine_deletion_requires_keyboard_confirmation(view_editor):
    _, root, controller = view_editor("CouchDeepStorageEditor.qml")
    controller.cleanup = {"active": {"transactionId": "transaction-first", "canFinalize": True}}
    _call(root, "refresh")
    choose(root, "finalize")
    assert not controller.calls
    _call(root, "handlePadAction", "Back")
    assert not controller.calls
    choose(root, "finalize")
    keyboard = root.findChild(QObject, "couchOnScreenKeyboard")
    keyboard.setProperty("text", "DELETE")
    _call(keyboard, "close", True)
    assert controller.calls == [("finalize", "first", "transaction-first", "DELETE")]


@pytest.mark.parametrize("name", [
    "CouchPostProcessingEditor.qml", "CouchFrameGenerationEditor.qml",
    "CouchDeepStorageEditor.qml", "CouchGpuMemoryEditor.qml",
])
def test_refresh_polling_stops_while_editor_is_hidden(view_editor, name):
    _, root, controller = view_editor(name)
    controller.post["runtimeLoaded"] = True
    controller.fg["runtimeState"] = "loaded"
    controller.deep["status"] = "cancelled"
    controller.gpu["runtimeProtectionVerified"] = True
    root.setVisible(False)
    QTest.qWait(1650)
    if "DeepStorage" in name:
        state = root.findChild(QObject, "deepAnalysisSection")
        assert _plain(state.property("analysis"))["status"] == "complete"
    else:
        state = root.findChild(QObject, {
            "CouchPostProcessingEditor.qml": "couchPostProcessingState",
            "CouchFrameGenerationEditor.qml": "couchFrameGenerationState",
            "CouchGpuMemoryEditor.qml": "gpuMemoryCard",
        }[name])
        assert not _plain(state.property("statusData")).get({
            "CouchPostProcessingEditor.qml": "runtimeLoaded",
            "CouchFrameGenerationEditor.qml": "runtimeState",
            "CouchGpuMemoryEditor.qml": "runtimeProtectionVerified",
        }[name])
    assert not controller.calls


@pytest.mark.parametrize("tab,action,flag", [
    (1, "deep-analysis", "deepStorageOverlayOpen"),
    (2, "gpu-memory", "gpuMemoryOverlayOpen"),
    (4, "post-processing", "postProcessingOverlayOpen"),
    (5, "frame-generation", "frameGenerationOverlayOpen"),
])
def test_details_overlay_back_and_game_switch_clear_navigation(tab, action, flag):
    from game_optimization_linux.controllers.couch_navigation import CouchNavigationController

    navigation = CouchNavigationController()
    controller = DetailsController()
    engine, root = _load(QML_DIR / "couch/CouchGameDetails.qml", {
        "controller": controller, "game": GAME, "navigation": navigation,
        "width": 1920, "height": 1080,
    })
    try:
        root.setProperty("selectedTab", tab)
        actions = _plain(root.property("actionModel"))
        root.setProperty("selectedAction", next(i for i, row in enumerate(actions) if row["id"] == action))
        assert _call(root, "activateAction") == "open"
        assert root.property(flag)
        assert navigation.property("modalOpen")
        _call(root, "handleAction", "Back")
        assert not root.property(flag)
        assert not navigation.property("modalOpen")
        assert _call(root, "activateAction") == "open"
        root.setProperty("game", dict(GAME, id="second"))
        _settle()
        assert not root.property(flag)
        assert not navigation.property("modalOpen")
    finally:
        root.deleteLater()
        _settle()