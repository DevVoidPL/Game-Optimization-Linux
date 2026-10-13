"""Couch game details: real states, preserved actions, focus and the
launch-preview profile regression."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
from PySide6.QtCore import (
    Q_ARG, Q_RETURN_ARG, Property, QCoreApplication, QMetaObject, QObject, Qt, QUrl, Slot,
)
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config
from game_optimization_linux.controllers import AppController
from game_optimization_linux.models import OptimizationProfile
from game_optimization_linux.models.optimization_profile import OPTIMIZATION_PRESETS
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import SettingsStore

COUCH = config.QML_DIR / "couch"
GAME = {
    "id": "g1", "name": "Game One", "launcher": "Steam", "status": "Ready",
    "libraryAvailable": True, "launchAllowed": True, "analysisAllowed": True,
    "launchUnavailableReason": "", "runner": "", "headerArtwork": "",
    "portraitArtwork": "", "fallbackArtwork": "", "effectiveArtworkUrl": "",
}


def _plain(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _settle(rounds: int = 4) -> None:
    for _ in range(rounds):
        QCoreApplication.processEvents()


def _call(obj: QObject, method: str, *args):
    if args:
        result = QMetaObject.invokeMethod(obj, method, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                          *[Q_ARG("QVariant", a) for a in args])
    else:
        result = QMetaObject.invokeMethod(obj, method, Qt.DirectConnection, Q_RETURN_ARG("QVariant"))
    _settle(2)
    return _plain(result)


def _load(path: Path, properties: dict):
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
    root = component.createWithInitialProperties(properties)
    assert root is not None, [error.toString() for error in component.errors()]
    _settle(6)
    # Keep the component alive as long as the object it created.
    root.setProperty("objectName", root.property("objectName"))
    engine._component = component
    return engine, root


@pytest.fixture()
def details():
    engine, root = _load(COUCH / "CouchGameDetails.qml",
                         {"controller": {"selectedGame": GAME}, "width": 1920, "height": 1080})
    yield root
    root.deleteLater()
    _settle()
    del engine


def test_launch_preview_uses_internal_profile_value_for_every_preset(details, tmp_path: Path) -> None:
    controller = AppController(game_provider=DemoGameProvider(),
                               settings_store=SettingsStore(tmp_path / "settings.json"))
    try:
        for preset in OPTIMIZATION_PRESETS:
            details.setProperty("optimizationProfile", preset)
            value = _call(details, "legacyOptimizationProfileValue")
            assert OptimizationProfile(value)  # a real enum value, never a UI label
            preview = controller.buildLaunchPreview(
                "batman-arkham-knight", {"profile": value, "gamemode": True})
            assert preview.startswith("gamemoderun"), (preset, value, preview)
        # Root cause of the original error: translated labels are not values.
        with pytest.raises(ValueError, match="expected one of"):
            AppController._coerce_enum(OptimizationProfile, "Automatyczny")
    finally:
        controller.shutdown()


def test_tab_and_action_ids_are_preserved(details) -> None:
    tabs = [tab["id"] for tab in _plain(details.property("tabs"))]
    assert tabs == ["overview", "storage", "optimization", "optiscaler", "narrator"]
    expected = {
        0: ["launch", "updates"],
        1: ["analyze", "verify", "exact-measure", "profile", "compress"],
        2: ["optimization-profile", "gamemode", "gamescope", "mangohud-profile"],
    }
    for tab, ids in expected.items():
        details.setProperty("selectedTab", tab)
        _settle()
        assert [a["id"] for a in _plain(details.property("actionModel"))] == ids
    cards = [card["id"] for card in _plain(details.property("headerCards"))]
    assert cards == ["launch", "gamemode", "gamescope", "mangohud", "optiscaler", "narrator"]


def test_feature_cards_show_real_state(details) -> None:
    details.setProperty("optimizationData", {
        "success": True, "preset": "automatic",
        "gamemode": {"available": True, "message": ""},
        "gamescope": {"available": False, "message": "gamescope is not installed"},
    })
    details.setProperty("gameModeEnabled", True)
    details.setProperty("mangoHudProfile", {"available": False, "availabilityMessage": "MangoHud is unavailable."})
    details.setProperty("optiScalerData", {"success": True, "installed": True, "installationState": "installed",
                                           "requestedFsr4Mode": "normal"})
    details.setProperty("narratorData", {"success": True, "enabled": True})
    _settle()
    info = {card["id"]: card["info"] for card in _plain(details.property("headerCards"))}
    assert (info["gamemode"]["state"], info["gamemode"]["preview"]) == ("On", True)
    assert (info["gamescope"]["state"], info["gamescope"]["tone"]) == ("Not installed", "unavailable")
    assert info["gamescope"]["preview"] is False
    assert info["mangohud"]["tone"] == "unavailable"
    assert (info["optiscaler"]["state"], info["optiscaler"]["tone"]) == ("Installed", "success")
    # Without a session state the card never claims the Narrator will start.
    assert info["narrator"]["state"] == "On · start the Narrator manually"
    details.setProperty("narratorSession", {"cardState": "missing", "missingRequirements": ["tts"]})
    _settle()
    info = {card["id"]: card["info"] for card in _plain(details.property("headerCards"))}
    assert info["narrator"]["state"] == "On · missing: voice"
    launch = _plain(details.property("headerCards"))[0]["info"]
    assert launch["detail"] == "Profile: Automatic"


def test_focus_order_header_tabs_actions_content(details) -> None:
    def act(name: str) -> int:
        _call(details, "handleAction", name)
        return int(details.property("focusArea"))

    assert int(details.property("focusArea")) == 0 and int(details.property("headerIndex")) == 0
    act("NavigateRight")
    assert int(details.property("headerIndex")) == 1       # Launch -> GameMode
    act("NavigateDown")
    assert int(details.property("headerIndex")) == 4       # second card row
    assert act("NavigateDown") == 1                        # -> tabs
    assert act("NavigateDown") == 2                        # -> tab actions
    assert act("NavigateDown") == 3                        # -> content
    assert act("NavigateUp") == 2
    assert act("NavigateUp") == 1
    assert act("NavigateUp") == 0


def test_optiscaler_and_narrator_cards_open_their_tabs(details) -> None:
    details.setProperty("headerIndex", 4)
    _call(details, "activateHeader")
    assert int(details.property("selectedTab")) == 3 and int(details.property("focusArea")) == 2
    details.setProperty("focusArea", 0)
    details.setProperty("headerIndex", 5)
    _call(details, "activateHeader")
    assert int(details.property("selectedTab")) == 4


def test_details_use_only_couch_glyphs_and_no_hardcoded_controller_state() -> None:
    source = (COUCH / "CouchGameDetails.qml").read_text(encoding="utf-8")
    # launcherLogo() is the Couch launcher-logo lookup (couch/launchers/).
    used = set(re.findall(r"App\.UiIcons\.(\w+)", source)) - {"launcherLogo"}
    assert used and all(name.startswith("couchGlyph") for name in used), sorted(
        name for name in used if not name.startswith("couchGlyph"))
    assert "Controller ready" not in source
    assert "legacyOptimizationProfileLabel" not in source


def test_back_returns_home_with_the_same_game_focused() -> None:
    games = [dict(GAME, id=f"g{i}", name=f"Game {i}") for i in range(8)]
    engine, home = _load(COUCH / "CouchHome.qml", {
        "controller": {"games": games, "updatesSummary": {}, "activeTasks": []},
        "width": 1920, "height": 1080})
    try:
        home.setProperty("focusZone", 2)
        _call(home, "focusGame", "g5")
        assert _plain(home.property("selectedGame"))["id"] == "g5"
        assert int(home.property("focusZone")) == 0
        main = (COUCH / "CouchMain.qml").read_text(encoding="utf-8")
        leave = main[main.index("function leaveDetails()"):main.index("function syncControllerPage()")]
        assert "homePage.focusGame(gameId)" in leave
        assert 'section = origin' in leave
    finally:
        home.deleteLater()
        _settle()
        del engine


def test_carousel_shows_only_whole_covers() -> None:
    games = [dict(GAME, id=f"g{i}", name=f"Game {i}") for i in range(12)]
    for width, height in ((1920, 1080), (1920, 1040), (1280, 720)):
        engine, home = _load(COUCH / "CouchHome.qml", {
            "controller": {"games": games, "updatesSummary": {}, "activeTasks": []},
            "width": width, "height": height})
        try:
            strip = home.findChild(QObject, "couchGameStrip")
            count = int(home.property("fittedCards"))
            card, gap, pad = (float(home.property(n)) for n in ("cardWidth", "cardGap", "cardPad"))
            assert abs(float(strip.property("width")) - (count * card + (count - 1) * gap + 2 * pad)) < 1.0
        finally:
            home.deleteLater()
            _settle()
            del engine


def test_narrator_action_grid_is_balanced_readable_and_navigable() -> None:
    """9 Lektor actions -> 3x3; short names + values fit in Polish at 1920x1080."""

    from PySide6.QtCore import Property, Signal, Slot
    from PySide6.QtTest import QTest

    from test_couch_settings_screen import _act, _probe, _view  # shared small probe helpers
    from game_optimization_linux.translations import TranslationManager

    class Controller(QObject):
        changed = Signal()

        @Property("QVariantMap", notify=changed)
        def selectedGame(self):
            return {"id": "steam-1", "name": "Game", "launcher": "Steam", "launchAllowed": True,
                    "libraryAvailable": True, "status": "Ready"}

        @Slot(str, result="QVariant")
        def getNarratorGameSettings(self, _game_id):
            return {"success": True, "enabled": False, "volume": 0.85, "speechRate": 1.0,
                    "voices": [{"id": "gosia", "name": "Gosia", "available": True}], "voiceId": "gosia"}

        @Slot(str, result="QVariant")
        def getNarratorSessionState(self, _game_id):
            return {"status": "idle", "canStart": False, "reasonCode": "components_missing"}

    manager = TranslationManager(QCoreApplication.instance())
    manager.set_language("pl")
    controller = Controller()
    condition = ('item.objectName === "couchDetailsActionTile" || item.objectName === "couchTileTitle"'
                 ' || item.objectName === "couchDetailsActionsViewport"')
    view, root = _view("CouchGameDetails", COUCH, condition, 1920, 1080, {"controller": controller})
    try:
        root.setProperty("selectedTab", 4)
        QMetaObject.invokeMethod(root, "loadNarrator", Qt.DirectConnection)
        root.setProperty("focusArea", 2)
        root.setProperty("selectedAction", 0)
        QTest.qWait(250)
        ids = [action["id"] for action in _plain(root.property("actionModel"))]
        assert ids == ["narrator-enabled", "narrator-language", "narrator-source", "narrator-capture",
                       "narrator-voice", "narrator-volume", "narrator-rate", "narrator-region", "narrator-reselect", "narrator-start"]
        viewport = root.findChild(QObject, "couchDetailsActionsViewport")
        assert (viewport.property("columns"), viewport.property("rows")) == (4, 3)
        titles = [item for item in _probe(root) if item["name"] == "couchTileTitle"]
        assert titles and not [item["text"] for item in titles if item["truncated"]]
        _act(root, "NavigateDown")                                   # same column, next row
        assert root.property("selectedAction") == 4
        _act(root, "NavigateDown")                                   # third row, an enabled tile
        actions = _plain(root.property("actionModel"))
        selected = root.property("selectedAction")
        assert 8 <= selected <= 9 and actions[selected]["enabled"] is True
        _act(root, "NavigateDown")                                   # past the last row -> content
        assert root.property("focusArea") == 3
    finally:
        view.close()
        manager.set_language("en")


class _MeasureController(QObject):
    """Minimal controller: records exact measurements, never starts pkexec."""

    def __init__(self, installed: bool) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._installed = installed

    @Property("QVariantMap", constant=True)
    def selectedGame(self):  # noqa: N802
        return dict(GAME, filesystem="Btrfs", physicalSize="12.0 GB",
                    physicalSizeMeasuredByCompsize=False, savingsMeasured=False)

    @Property("QVariantMap", constant=True)
    def systemInfo(self):  # noqa: N802
        source = "optional_host_component" if self._installed else "unavailable"
        return {"compressionCapabilities": {"measurementSource": source}}

    @Slot(str, result=bool)
    def exactCompressionMeasurement(self, game_id: str) -> bool:  # noqa: N802
        self.calls.append(game_id)
        return True


@pytest.mark.parametrize("installed", [True, False])
def test_exact_measurement_is_an_explicit_confirmed_pad_action(installed: bool) -> None:
    controller = _MeasureController(installed)
    engine, page = _load(COUCH / "CouchGameDetails.qml",
                         {"controller": controller, "width": 1920, "height": 1080})
    try:
        page.setProperty("selectedTab", 1)
        _settle()
        actions = _plain(page.property("actionModel"))
        index = [a["id"] for a in actions].index("exact-measure")
        action = actions[index]
        assert "password" in action["title"] and "keyboard" in action["title"]
        assert action["enabled"] is installed
        # Unmeasured Btrfs values are shown honestly, the estimate is marked.
        assert _call(page, "physicalUsageText") == "Not measured"
        assert _call(page, "measuredValue", "savedSpace", "savingsMeasured") == "Not measured"
        if not installed:
            return
        page.setProperty("focusArea", 2)
        page.setProperty("selectedAction", index)
        _call(page, "handleAction", "Confirm")          # A: opens a confirmation only
        assert page.property("confirmationOpen") is True and controller.calls == []
        _call(page, "handleAction", "Back")             # B: cancel, nothing measured
        assert page.property("confirmationOpen") is False and controller.calls == []
        _call(page, "handleAction", "Confirm")
        _call(page, "handleAction", "NavigateRight")    # choose "Measure"
        _call(page, "handleAction", "Confirm")
        assert controller.calls == ["g1"] and page.property("confirmationOpen") is False
    finally:
        page.deleteLater()
        _settle()
        del engine
