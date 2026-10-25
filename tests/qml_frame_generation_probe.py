"""Render shared FG controls in three languages and exercise controller navigation."""
import copy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
from pathlib import Path
import sys
from PySide6.QtCore import QObject, Slot, Property, QUrl, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication, QColor, QPalette
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from game_optimization_linux.config import QML_DIR
from game_optimization_linux.services.frame_generation import DEFAULTS
from game_optimization_linux.translations import TranslationManager


class Controller(QObject):
    mode = "normal"
    @Property("QVariantMap", constant=True)
    def selectedGame(self):
        return {"id": "fixture", "name": "Frame generation — disposable UI fixture", "launcher": "Steam"}

    @Slot(str, result="QVariantMap")
    def getOptiScalerStatus(self, key): return {"success": False}

    @Slot(str, result="QVariantMap")
    def frameGenerationStatus(self, key):
        result = {"success": True, "backend": "afmf", "settings": copy.deepcopy(DEFAULTS),
            "defaults": copy.deepcopy(DEFAULTS), "runtimeState": "unverified", "configuredAt": "2026-10-25T13:00:00Z",
            "executable": "/games/Example/Game-Win64-Shipping.exe", "binary": {"bits": 64, "api": "d3d12"},
            "environment": "host / Steam Linux Runtime", "runner": {"configured": True}, "api": "vkd3d", "configured": True,
            "steamUpdate": {"blocked": False, "state": "paused", "stateFlags": 516, "appId": "3240220", "fresh": True},
            "components": {b: {"installed": True, "managed": True, "version": v, "availableVersion": v,
                "downloadSize": size, "environmentCheck": {"available": True, "details": "ELF64 · dependencies available"}}
                for b, v, size in (("lsfg", "2.0.0", 561348), ("mako", "4.6.0", 2890224), ("afmf", "1.3.1", 91064))},
            "capabilities": {"devices": ["AMD Radeon RX 6600 XT (RADV NAVI23)"], "available": True, "compute": True, "storageImages": True, "apiVersions": ["1.4"]}}
        if self.mode == "conflict":
            result["components"]["afmf"].update(optiScalerConflict=True, blockers=["afmf-linux upstream does not support OptiScaler injectors. Preserve OptiScaler and choose lsfg-vk/MAKO, or restore it explicitly on its own page"])
            result["optiScalerPresent"] = True
        elif self.mode == "missing":
            result["components"]["afmf"].update(installed=False)
        elif self.mode == "update":
            result["steamUpdate"].update(blocked=True, state="active", stateFlags=260)
        return result

    @Slot(str, result="QVariantMap")
    def refreshFrameGeneration(self, key): return self.frameGenerationStatus(key)


def main():
    app = QGuiApplication([]); controller = Controller(); translations = TranslationManager(app)
    palette = app.palette(); palette.setColor(QPalette.Window, QColor("#0b1018"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText): palette.setColor(role, QColor("#e5e7ee"))
    app.setPalette(palette)
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    def call(root, method, *values):
        QMetaObject.invokeMethod(root, method, *[Q_ARG("QVariant", v) for v in values])
        QTest.qWait(70)
    for language in ("pl", "en", "es"):
        translations.set_language(language)
        for width in (320, 480, 1100):
            controller.mode = "normal"
            view = QQuickView(); view.setColor(QColor("#10121a")); view.setResizeMode(QQuickView.SizeRootObjectToView)
            view.setInitialProperties({"controller": controller, "gameData": controller.selectedGame})
            view.setSource(QUrl.fromLocalFile(str(QML_DIR / "pages/details/FrameGenerationPanel.qml")))
            view.resize(width, 900); view.show(); QTest.qWait(160)
            root = view.rootObject(); assert root, view.errors()
            for backend, index in (("lsfg", 1), ("mako", 2), ("afmf", 3)):
                call(root, "chooseBackend", index)
                view.grabWindow().save(str(output / f"fg-{backend}-{width}-{language}.png"))
                root.property("scrollFlickable").setProperty("contentY", 720)
                QTest.qWait(70)
                view.grabWindow().save(str(output / f"fg-settings-{backend}-{width}-{language}.png"))
                root.property("scrollFlickable").setProperty("contentY", 0)
            call(root, "edit", "min_fps", 40); call(root, "refresh")
            assert root.property("currentSettings").toVariant()["min_fps"] == 40
            for state in ("conflict", "missing", "update"):
                controller.mode = state; call(root, "recheck")
                if state in ("conflict", "update"):
                    assert not root.findChild(QObject, "applyFrameGeneration").property("enabled")
                    root.property("scrollFlickable").setProperty("contentY", root.findChild(QObject, "fgRequirements").property("y") - 10)
                else: root.property("scrollFlickable").setProperty("contentY", 0)
                QTest.qWait(70)
                view.grabWindow().save(str(output / f"fg-{state}-{width}-{language}.png"))
                assert root.property("currentSettings").toVariant()["min_fps"] == 40
            view.close(); view.deleteLater(); QTest.qWait(30)
        controller.mode = "conflict"
        view = QQuickView(); view.setColor(QColor("#10121a")); view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.setInitialProperties({"controller": controller})
        view.setSource(QUrl.fromLocalFile(str(QML_DIR / "couch/CouchGameDetails.qml")))
        view.resize(1920, 1080); view.show(); QTest.qWait(150)
        root = view.rootObject(); assert root, view.errors()
        root.setProperty("selectedTab", 6); root.setProperty("frameGenerationOverlayOpen", True); QTest.qWait(400)
        view.grabWindow().save(str(output / f"fg-couch-{language}.png"))
        panel = root.findChild(QObject, "frameGenerationPanel"); assert panel
        call(panel, "edit", "min_fps", 42)
        QMetaObject.invokeMethod(panel, "optiScalerRequested"); QTest.qWait(70)
        assert root.property("selectedTab") == 3 and not root.property("frameGenerationOverlayOpen")
        controller.mode = "normal"; root.setProperty("selectedTab", 6); root.setProperty("selectedAction", 0)
        call(root, "activateAction")
        assert root.property("frameGenerationOverlayOpen") and not panel.property("optiConflict")
        assert panel.property("currentSettings").toVariant()["min_fps"] == 42
        call(root, "handleAction", "NavigateDown"); call(root, "handleAction", "NavigateRight")
        view.close(); view.deleteLater(); QTest.qWait(30)
    print("FG render Desktop 320/480/1100 and Couch PL/EN/ES PASS; missing/conflict/update/drafts/pad/Opti navigation PASS")


if __name__ == "__main__": main()
