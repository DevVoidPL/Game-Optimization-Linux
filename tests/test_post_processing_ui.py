from PySide6.QtCore import QObject, Slot, QUrl, QMetaObject, Q_ARG, Q_RETURN_ARG, Qt, QCoreApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from game_optimization_linux.config import QML_DIR
from game_optimization_linux.services.post_processing_catalog import EFFECTS


class Controller(QObject):
    def __init__(self):
        super().__init__()
        self.saved = None
        self.result = dict(success=True, backend="off", busy=False, installed=False, api="auto",
                           catalogue=[dict(id=k, **v) for k, v in EFFECTS.items()])

    @Slot(str, result="QVariantMap")
    def postProcessingStatus(self, key):
        return self.result

    @Slot(str, "QVariantMap", result=bool)
    def applyPostProcessing(self, key, values):
        self.saved = values
        self.result.update(busy=True, operationId=1)
        return True


def call(root, name, *values):
    return QMetaObject.invokeMethod(root, name, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                   *[Q_ARG("QVariant", v) for v in values])


def test_shared_panel_has_working_gamepad_configuration():
    controller = Controller()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / "pages/details/PostProcessingPanel.qml")))
    root = component.createWithInitialProperties(dict(controller=controller, gameData={"id": "fixture"}, width=1000, height=700))
    assert root is not None, [e.toString() for e in component.errors()]
    for _ in range(5): QCoreApplication.processEvents()
    assert root.property("backend") == "off"
    call(root, "handlePadAction", "NavigateRight")
    assert root.property("backend") == "vkbasalt"
    call(root, "handlePadAction", "NavigateRight")
    assert root.property("backend") == "reshade"
    call(root, "preset", "colour")
    call(root, "updateParameter", "Vibrance", .8)
    call(root, "resetEffect", "vibrance")
    assert root.property("parameters").toVariant()["Vibrance"] == 0
    assert controller.saved is None
    call(root, "refresh")
    assert root.property("backend") == "reshade" and root.property("dirty")
    assert root.property("effects").toVariant() == ["vibrance", "curves"]
    call(root, "resetAll")
    assert root.property("backend") == "reshade"
    assert root.property("effects").toVariant() == []
    assert root.property("parameters").toVariant()["Gamma"] == 1
    assert controller.saved is None
    call(root, "preset", "colour")
    call(root, "moveEffect", "curves", -1)
    value = root.property("effects").toVariant()
    assert value == ["curves", "vibrance"]
    call(root, "apply")
    assert controller.saved["backend"] == "reshade"
    assert controller.saved["effects"] == ["curves", "vibrance"]
    call(root, "updateParameter", "Contrast", .4)
    controller.result.update(busy=False, completedOperationId=1, backend="reshade", parameters={"Contrast": .1})
    call(root, "refresh")
    assert root.property("parameters").toVariant()["Contrast"] == .4
    assert root.property("dirty")
    root.deleteLater()
    QCoreApplication.processEvents()
