"""Render the shared GPU-memory card and exercise draft/controller navigation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
import json
from pathlib import Path
import sys
import tempfile
from PySide6.QtCore import QObject, Slot, Property, QUrl, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication, QColor, QPalette
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from game_optimization_linux.config import QML_DIR
from game_optimization_linux.translations import TranslationManager


class Controller(QObject):
    priority = True
    @Slot(str, result="QVariantMap")
    def getOptiScalerStatus(self, key):
        return {"success": False}
    @Property("QVariantMap", constant=True)
    def selectedGame(self):
        return {"id": "steam-3240220", "name": "Grand Theft Auto V Enhanced", "launcher": "Steam"}

    @Slot(str, result="QVariantMap")
    def gpuMemoryStatus(self, key):
        return {"success": True, "ready": True, "launchSupported": True, "state": "configured", "busy": False,
                "profile": {"priority": self.priority, "pressure": "default", "api": "d3d12", "region": "drm/0000:08:00.0/vram"},
                "runtimeState": "protected", "runtimeProtectionVerified": True, "protectionOrigin": "independent",
                "observedAt": "2026-10-25T08:14:43+00:00",
                "system": {"kernel": "7.2.11-1-cachyos", "regions": {"drm/0000:08:00.0/vram": 8573157376},
                           "ttmProtectionEvidence": "cachyos-backport-btf", "managedSetup": True},
                "detectedApi": "d3d12", "tool": {"version": "vkd3d-proton 0bb64c56c / Proton Experimental"}}


def main():
    app = QGuiApplication([])
    palette = app.palette(); palette.setColor(QPalette.Window, QColor("#0B1018"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(role, QColor("#e5e7ee"))
    app.setPalette(palette)
    translations = TranslationManager(app)
    controller = Controller()
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    def settle():
        QTest.qWait(180)
    with tempfile.TemporaryDirectory(prefix="gpu-qml-") as folder:
        wrapper = Path(folder) / "Render.qml"
        wrapper.write_text('''import QtQuick
import QtQuick.Controls
import "''' + (QML_DIR / "pages/details").as_uri() + '''" as Details
ScrollView { id: view; property var controller; property var gameData
 contentWidth: availableWidth
 Details.GpuMemoryCard { objectName: "card"; width: view.availableWidth; controller: view.controller; gameData: view.gameData }
}''')
        for language in ("pl", "en", "es"):
            translations.set_language(language)
            for width in (480, 1100):
                view = QQuickView(); view.setColor(QColor("#10121a"))
                view.setInitialProperties({"controller": controller, "gameData": controller.selectedGame})
                view.setSource(QUrl.fromLocalFile(str(wrapper))); view.setResizeMode(QQuickView.SizeRootObjectToView)
                view.resize(width, 900); view.show(); settle()
                assert view.rootObject(), view.errors()
                card = view.rootObject().findChild(QObject, "card"); assert card
                view.grabWindow().save(str(output / f"gpu-{width}-{language}.png"))
                # A clean editor follows external saved changes; Off must still show independent protection.
                controller.priority = False
                QMetaObject.invokeMethod(card, "refresh"); settle()
                assert not card.property("priority")
                assert not card.findChild(QObject, "gpuPrioritySwitch").property("checked")
                runtime = card.findChild(QObject, "gpuRuntimeProtection")
                assert runtime and runtime.property("text")
                view.grabWindow().save(str(output / f"gpu-independent-{width}-{language}.png"))
                # A status refresh must preserve a pending edit.
                controller.priority = True
                card.setProperty("priority", False); card.setProperty("dirty", True)
                QMetaObject.invokeMethod(card, "refresh"); settle()
                assert not card.property("priority")
                card.setProperty("expanded", True); settle()
                view.grabWindow().save(str(output / f"gpu-expanded-{width}-{language}.png"))
                QMetaObject.invokeMethod(card, "handlePadAction", Q_ARG("QVariant", "NavigateDown")); settle()
                view.close()
        view = QQuickView(); view.setColor(QColor("#10121a"))
        view.setInitialProperties({"controller": controller, "game": controller.selectedGame, "gpuMemoryOverlayOpen": True})
        view.setSource(QUrl.fromLocalFile(str(QML_DIR / "couch/CouchGameDetails.qml")))
        view.setResizeMode(QQuickView.SizeRootObjectToView); view.resize(1920, 1080); view.show(); settle()
        assert view.rootObject(), view.errors()
        view.grabWindow().save(str(output / "gpu-couch.png")); view.close()
    print("GPU memory: Desktop PL/EN/ES 480/1100, Couch and pending draft PASS")


if __name__ == "__main__": main()
