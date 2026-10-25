import copy
from PySide6.QtCore import QObject, Slot, QUrl, QMetaObject, Q_ARG, Q_RETURN_ARG, Qt, QCoreApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem
from game_optimization_linux.config import QML_DIR
from game_optimization_linux.services.frame_generation import DEFAULTS


class Controller(QObject):
    def __init__(self):
        super().__init__()
        self.saved = None
        self.results = {key: {"success": True, "backend": "off", "api": "vulkan", "busy": False,
            "settings": copy.deepcopy(DEFAULTS), "defaults": copy.deepcopy(DEFAULTS),
            "capabilities": {"available": True, "compute": True, "storageImages": True, "apiVersions": ["1.4"]},
            "runner": {"configured": True}, "steamUpdate": {"blocked": False, "state": "idle"},
            "components": {b: {"installed": True, "managed": True, "version": "fixture", "blockers": [], "environmentCheck": {"available": True}} for b in DEFAULTS}}
            for key in ("first", "second")}

    @Slot(str, result="QVariantMap")
    def frameGenerationStatus(self, key):
        return self.results[key]

    @Slot(str, result="QVariantMap")
    def refreshFrameGeneration(self, key):
        return self.results[key]

    @Slot(str, str, "QVariantMap", result=bool)
    def frameGenerationAction(self, key, action, values):
        self.saved = (key, action, values)
        self.results[key].update(busy=True, operationId=1)
        return True


def call(root, method, *values):
    result = QMetaObject.invokeMethod(root, method, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                    *[Q_ARG("QVariant", value) for value in values])
    QCoreApplication.processEvents()
    return result


def visual_find(item, name):
    if item.objectName() == name: return item
    for child in item.childItems():
        match = visual_find(child, name)
        if match is not None: return match
    return None


def test_drafts_refresh_reset_backend_switch_and_game_isolation():
    controller = Controller(); engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / "pages/details/FrameGenerationPanel.qml")))
    root = component.createWithInitialProperties({"controller": controller, "gameData": {"id": "first"}, "width": 480, "height": 850})
    assert root, [e.toString() for e in component.errors()]
    for _ in range(4): QCoreApplication.processEvents()
    call(root, "handlePadAction", "NavigateRight")
    assert root.property("backend") == "lsfg"
    call(root, "edit", "multiplier", 4)
    call(root, "refresh")
    assert root.property("backend") == "lsfg" and root.property("currentSettings").toVariant()["multiplier"] == 4
    call(root, "chooseBackend", 2); call(root, "edit", "multiplier", 3)
    call(root, "chooseBackend", 1)
    assert root.property("currentSettings").toVariant()["multiplier"] == 4
    call(root, "reset")
    assert root.property("currentSettings").toVariant()["multiplier"] == 2
    assert root.property("settings").toVariant()["mako"]["multiplier"] == 3
    assert controller.saved is None
    call(root, "run", "install")
    controller.results["first"].update(busy=False, completedOperationId=1)
    call(root, "refresh")
    assert root.property("backend") == "lsfg", "component operations must preserve the selected editor"
    call(root, "run", "apply")
    call(root, "edit", "multiplier", 5)
    controller.results["first"].update(busy=False, completedOperationId=1, backend="lsfg")
    call(root, "refresh")
    assert root.property("currentSettings").toVariant()["multiplier"] == 5
    root.setProperty("gameData", {"id": "second"})
    call(root, "refresh")
    assert root.property("backend") == "off"
    assert root.property("settings").toVariant()["lsfg"]["multiplier"] == 2
    assert root.property("settings").toVariant()["mako"]["multiplier"] == 2
    root.deleteLater(); QCoreApplication.processEvents()


def test_missing_lossless_and_conflict_have_actions_before_apply():
    controller = Controller(); engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / "pages/details/FrameGenerationPanel.qml")))
    root = component.createWithInitialProperties({"controller": controller, "gameData": {"id": "first"}, "width": 480, "height": 850})
    assert root, component.errors()
    for backend in (1, 2):
        call(root, "chooseBackend", backend)
        assert root.findChild(QObject, "fgPurchaseRequirement").property("visible")
        assert "separately purchased" in root.findChild(QObject, "fgPurchaseRequirement").property("text")
        for name in ("fgOpenLosslessSteam", "fgDiscover", "fgBrowse", "fgValidate"):
            assert root.findChild(QObject, name).property("visible")
        assert not root.findChild(QObject, "applyFrameGeneration").property("enabled")
    call(root, "chooseBackend", 3)
    conflict = "afmf-linux upstream does not support OptiScaler injectors. Preserve OptiScaler and choose lsfg-vk/MAKO, or restore it explicitly on its own page"
    controller.results["first"]["components"]["afmf"].update(optiScalerConflict=True, blockers=[conflict])
    controller.results["first"]["operationError"] = conflict
    call(root, "refresh")
    assert root.findChild(QObject, "fgOptiConflict").property("visible")
    assert root.findChild(QObject, "fgOpenOptiScaler").property("visible")
    assert root.property("otherBlockers").toVariant() == []
    assert call(root, "operationError") == ""
    assert not root.findChild(QObject, "fgPurchaseRequirement").property("visible")
    assert not root.findChild(QObject, "applyFrameGeneration").property("enabled")
    call(root, "edit", "min_fps", 45)
    controller.results["first"]["components"]["afmf"].update(optiScalerConflict=False, blockers=[])
    controller.results["first"].pop("operationError")
    call(root, "recheck")
    assert not root.findChild(QObject, "fgOptiConflict").property("visible")
    assert root.findChild(QObject, "applyFrameGeneration").property("enabled"), {key: root.property(key) for key in ("canApply", "dependenciesReady", "hardwareReady", "modelsReady", "apiReady", "environmentReady", "loaded", "backend", "api", "enabled")}
    assert root.property("currentSettings").toVariant()["min_fps"] == 45
    assert root.findChild(QObject, "fgRunnerSetup").property("text") == "Repair runner setup"
    assert visual_find(root, "fgValue-min_fps").property("text") == "45 FPS"
    root.deleteLater(); QCoreApplication.processEvents()


def test_retained_editor_survives_navigation_and_update_blocks_apply_without_losing_values():
    controller = Controller(); engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / "pages/details/FrameGenerationPanel.qml")))
    values = copy.deepcopy(DEFAULTS); values["afmf"]["min_fps"] = 42
    draft = {"gameId": "first", "backend": "afmf", "api": "vulkan", "settings": values, "dllPath": "", "internalFgOff": False, "dirty": True}
    root = component.createWithInitialProperties({"controller": controller, "gameData": {"id": "first"}, "retainedDraft": draft, "width": 480, "height": 850})
    assert root, component.errors()
    assert root.property("backend") == "afmf" and root.property("currentSettings").toVariant()["min_fps"] == 42
    controller.results["first"]["steamUpdate"] = {"blocked": True, "state": "active", "appId": "123", "stateFlags": 260}
    call(root, "refresh")
    assert not root.findChild(QObject, "applyFrameGeneration").property("enabled")
    assert root.findChild(QObject, "fgSteamUpdate").property("visible")
    controller.results["first"]["steamUpdate"] = {"blocked": False, "state": "paused", "stateFlags": 516}
    call(root, "recheck")
    assert root.findChild(QObject, "applyFrameGeneration").property("enabled"), {key: root.property(key) for key in ("canApply", "dependenciesReady", "hardwareReady", "modelsReady", "apiReady", "environmentReady", "loaded", "backend", "api", "enabled")}
    assert root.property("currentSettings").toVariant()["min_fps"] == 42
    assert not root.property("advanced")
    root.deleteLater(); QCoreApplication.processEvents()
