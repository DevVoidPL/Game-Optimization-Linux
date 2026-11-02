"""Controller-first editors share Desktop state, not its focus traversal."""
from __future__ import annotations

import copy

import pytest
from PySide6.QtCore import QObject, Slot, QCoreApplication

from game_optimization_linux.config import QML_DIR
from game_optimization_linux.services.frame_generation import DEFAULTS
from test_couch_details import _call, _load, _plain, _settle, GAME


class Controller(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.post = {"success": True, "backend": "vkbasalt", "api": "vulkan",
                     "executable": "game.exe", "effects": ["cas"], "parameters": {"sharpness": 0.5},
                     "catalogue": [{"id": "cas", "label": "Sharpness", "backends": ["vkbasalt"],
                                    "parameters": [{"key": "sharpness", "label": "Sharpness", "default": 0.5,
                                                    "minimum": 0, "maximum": 1, "integer": False}]}]}
        self.fg = {"success": True, "backend": "afmf", "api": "vkd3d", "configured": True,
                   "settings": copy.deepcopy(DEFAULTS), "defaults": copy.deepcopy(DEFAULTS),
                   "capabilities": {"available": True, "compute": True, "storageImages": True, "apiVersions": ["1.4"]},
                   "components": {b: {"installed": True, "managed": True, "blockers": [],
                                      "environmentCheck": {"available": True}} for b in DEFAULTS}}
        self.deep = {"status": "complete", "hasPlan": True, "summary": {"targetLanguage": "en"},
                     "categories": [{"id": "cache", "groups": [
                         {"groupId": "safe", "key": "cache", "safety": "safe", "scope": "game", "fileCount": 2,
                          "bytes": 100, "actionability": "selectable"},
                         {"groupId": "protected", "key": "core", "safety": "unsupported", "scope": "game",
                          "fileCount": 3, "bytes": 200, "actionability": "protected"}]}]}
        self.cleanup = {"selectable": ["safe"]}
        self.recompression = {"available": True, "capabilities": {"levels": [1, 3, 6]},
                              "plan": {"planId": "p1", "groups": [{"type": ".txt", "files": 2, "bytes": 100,
                                                                       "recommended": True}]}}
        self.gpu = {"success": True, "ready": True, "profile": {"priority": False, "pressure": "default", "api": "auto"},
                    "system": {"regions": {"vram": {}}}}

    @Slot(str, result="QVariantMap")
    def postProcessingStatus(self, key):
        return self.post

    @Slot(str, "QVariantMap", result=bool)
    def applyPostProcessing(self, key, values):
        self.calls.append(("post", key, values)); return True

    @Slot(str, result="QVariantMap")
    def frameGenerationStatus(self, key):
        return self.fg

    @Slot(str, result="QVariantMap")
    def refreshFrameGeneration(self, key):
        return self.fg

    @Slot(str, str, "QVariantMap", result=bool)
    def frameGenerationAction(self, key, action, values):
        self.calls.append(("fg", key, action, values)); return True

    @Slot(str, str, result="QVariantMap")
    def getDeepAnalysis(self, key, safety):
        return self.deep

    @Slot(str, result="QVariantMap")
    def getDeepCleanup(self, key):
        return self.cleanup

    @Slot(str, result="QVariantMap")
    def getDeepRecompression(self, key):
        return self.recompression

    @Slot(str, "QVariantList", result=bool)
    def startDeepQuarantine(self, key, groups):
        self.calls.append(("quarantine", key, groups)); return True

    @Slot(str, "QVariantList", result=bool)
    def startDeepRecompression(self, key, types):
        self.calls.append(("recompress", key, types)); return True

    @Slot(str, result="QVariantMap")
    def gpuMemoryStatus(self, key):
        return self.gpu

    @Slot(str, "QVariantMap", result=bool)
    def applyGpuMemory(self, key, values):
        self.calls.append(("gpu", key, values)); return True


@pytest.fixture
def editor():
    opened = []
    def create(name, **properties):
        controller = Controller()
        engine, root = _load(QML_DIR / "couch" / name,
                             {"controller": controller, "gameData": {"id": "first", "launcher": "Heroic"},
                              "width": 1000, "height": 720, **properties})
        opened.append((engine, root, controller))
        return root, controller
    yield create
    for engine, root, controller in reversed(opened):
        root.deleteLater(); _settle()
    opened.clear()


def rows(root):
    return _call(root, "rows")


def choose(root, identifier, action="Confirm"):
    entries = rows(root)
    index = next(i for i, entry in enumerate(entries) if entry["id"] == identifier)
    menu = root.findChild(QObject, "couchOptionList").parent()
    # The ListView belongs to a ColumnLayout; find its containing FocusScope.
    while menu.metaObject().indexOfProperty("currentIndex") < 0:
        menu = menu.parent()
    menu.setProperty("currentIndex", index)
    _call(root, "handlePadAction", action)


def test_post_parameters_are_bounded_and_apply_requires_confirmation(editor):
    root, controller = editor("CouchPostProcessingEditor.qml")
    state = root.findChild(QObject, "couchPostProcessingState")
    choose(root, "sharpness", "NavigateRight")
    assert _plain(state.property("parameters"))["sharpness"] == pytest.approx(0.51)
    for _ in range(70):
        choose(root, "sharpness", "NavigateRight")
    assert _plain(state.property("parameters"))["sharpness"] == 1
    _call(root, "refresh")
    assert _plain(state.property("parameters"))["sharpness"] == 1
    assert not controller.calls
    choose(root, "apply")
    assert root.property("confirmation") == "apply" and not controller.calls
    assert _call(root, "handlePadAction", "Back") is True
    assert not root.property("confirmation") and not controller.calls
    choose(root, "apply"); choose(root, "confirm")
    assert controller.calls[-1][0:2] == ("post", "first")
    assert controller.calls[-1][2]["parameters"]["sharpness"] == 1


def test_frame_generator_uses_canonical_api_and_preserves_drafts(editor):
    root, controller = editor("CouchFrameGenerationEditor.qml")
    state = root.findChild(QObject, "couchFrameGenerationState")
    choose(root, "api", "NavigateLeft")
    assert state.property("api") == "dxvk"
    choose(root, "min_fps", "NavigateRight")
    value = _plain(state.property("currentSettings"))["min_fps"]
    _call(root, "recheck")
    assert _plain(state.property("currentSettings"))["min_fps"] == value
    assert not controller.calls
    choose(root, "install"); _call(root, "handlePadAction", "Back")
    assert not controller.calls
    choose(root, "apply")
    assert controller.calls[-1][2] == "apply" and controller.calls[-1][3]["api"] == "dxvk"


def test_frame_generation_blockers_disable_apply(editor):
    root, controller = editor("CouchFrameGenerationEditor.qml")
    controller.fg["steamUpdate"] = {"blocked": True}
    _call(root, "refresh")
    assert next(r for r in rows(root) if r["id"] == "apply")["enabled"] is False
    choose(root, "apply")
    assert not controller.calls


def test_deep_storage_never_preselects_and_respects_protected_and_stale(editor):
    root, controller = editor("CouchDeepStorageEditor.qml")
    _call(root, "refresh")
    analysis = root.findChild(QObject, "deepAnalysisSection")
    assert not _plain(analysis.property("selectedGroups"))
    choose(root, "protected")
    assert not _plain(analysis.property("selectedGroups"))
    choose(root, "safe")
    choose(root, "quarantine")
    assert not controller.calls
    _call(root, "handlePadAction", "Back")
    assert not controller.calls
    choose(root, "quarantine"); choose(root, "confirm")
    assert controller.calls[-1] == ("quarantine", "first", ["safe"])
    controller.calls.clear()
    root.setProperty("recompressionMode", True)
    choose(root, ".txt")
    choose(root, "start")
    controller.recompression["planStale"] = True
    _call(root, "refresh")
    choose(root, "confirm")
    assert not controller.calls


def test_gpu_memory_is_a_draft_until_apply(editor):
    root, controller = editor("CouchGpuMemoryEditor.qml")
    choose(root, "protection")
    assert not controller.calls
    choose(root, "apply")
    assert controller.calls[-1][0:2] == ("gpu", "first")
    assert controller.calls[-1][2]["priority"] is True


@pytest.mark.parametrize("launcher", ["Steam", "Heroic", "Lutris", "Manual"])
def test_couch_does_not_replace_backend_states_with_launcher_unavailable(launcher):
    engine, root = _load(QML_DIR / "couch/CouchGameDetails.qml",
                         {"controller": {"selectedGame": dict(GAME, launcher=launcher)}, "width": 1920, "height": 1080})
    try:
        root.setProperty("optimizationData", {"success": True, "gamemode": {"available": True}, "gamescope": {"available": True}})
        root.setProperty("mangoHudProfile", {"available": True})
        root.setProperty("optiScalerData", {"success": True, "installed": True, "installationState": "installed"})
        for feature in ("gamemode", "gamescope", "mangohud", "optiscaler"):
            assert _call(root, "featureInfo", feature)["tone"] != "unavailable"
        assert all(c["enabled"] for c in _plain(root.property("headerCards")))
    finally:
        root.deleteLater(); _settle()