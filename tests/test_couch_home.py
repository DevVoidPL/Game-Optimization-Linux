"""Stage 2 Couch Home: real data sources, preserved actions and selection."""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import Q_ARG, QCoreApplication, QMetaObject, QObject, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config

COUCH = config.QML_DIR / "couch"


def _game(game_id: str, **extra) -> dict:
    game = {
        "id": game_id, "name": f"Game {game_id}", "launcher": "Steam",
        "status": "Ready", "libraryAvailable": True, "availabilityStatus": "",
        "launchAllowed": True, "launchUnavailableReason": "", "runner": "",
        "headerArtwork": "", "portraitArtwork": "", "fallbackArtwork": "",
        "effectiveArtworkUrl": "",
    }
    game.update(extra)
    return game


GAMES = [
    _game("a"),
    _game("b", launcher="Heroic", runner="/opt/runners/wine-ge-8-26"),
    _game("c", launchAllowed=False, launchUnavailableReason="Choose the main executable first"),
    _game("d", status="Drive disconnected", libraryAvailable=False, launchAllowed=False),
]


@pytest.fixture()
def home():
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / "CouchHome.qml")))
    controller = {"games": GAMES, "updatesSummary": {}, "activeTasks": []}
    root = component.createWithInitialProperties(
        {"controller": controller, "width": 1920, "height": 1080})
    assert root is not None, [error.toString() for error in component.errors()]
    for _ in range(5):
        QCoreApplication.processEvents()
    yield root
    root.deleteLater()
    QCoreApplication.processEvents()
    del engine


def _child(root: QObject, name: str) -> QObject:
    item = root.findChild(QObject, name)
    assert item is not None, f"missing {name}"
    return item


def _plain(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _invoke(home, method: str, value) -> None:
    QMetaObject.invokeMethod(home, method, Qt.DirectConnection, Q_ARG("QVariant", value))
    QCoreApplication.processEvents()


@pytest.mark.parametrize(
    ("index", "tone", "text", "runtime", "reason"),
    [
        (0, "success", "Ready to launch", "", ""),
        (1, "success", "Ready to launch", "Runtime: wine-ge-8-26", ""),
        (2, "warning", "Launch unavailable", "", "Choose the main executable first"),
        (3, "danger", "Drive disconnected", "", ""),
    ],
)
def test_hero_shows_only_real_game_fields(home, index, tone, text, runtime, reason) -> None:
    _invoke(home, "selectGameIndex", index)
    game = GAMES[index]
    assert _child(home, "couchHomeHeroTitle").property("text") == game["name"]
    pill = _child(home, "couchHomeReadiness")
    assert (pill.property("tone"), pill.property("text")) == (tone, text)
    runtime_label = _child(home, "couchHomeRuntime")
    assert runtime_label.property("text") == runtime
    assert runtime_label.property("visible") is bool(runtime)
    assert _child(home, "couchHomeReadinessReason").property("text") == reason
    names = {"Steam": "Steam", "Heroic": "Heroic Games Launcher"}
    assert _child(home, "couchHomeLauncherLabel").property("text") == names[game["launcher"]]


def test_existing_action_ids_are_preserved(home) -> None:
    hero = [entry["id"] for entry in _plain(home.property("heroActions"))]
    context = [entry["id"] for entry in _plain(home.property("contextEntries"))]
    assert hero == ["launch", "details", "more"]
    assert context == ["launch", "details", "updates", "close"]
    tiles = _plain(home.property("homeTiles"))
    assert [tile["id"] for tile in tiles] == ["library", "tasks", "updates", "settings"]
    assert [tile["title"] for tile in tiles] == ["Library", "Tasks", "Updates", "Settings"]


def test_selected_game_survives_library_refresh(home) -> None:
    _invoke(home, "selectGameIndex", 2)
    assert home.property("retainedGameId") == "c"
    home.setProperty("games", list(reversed(GAMES)))
    for _ in range(5):
        QCoreApplication.processEvents()
    assert _plain(home.property("selectedGame"))["id"] == "c"
    assert _child(home, "couchGameStrip").property("currentIndex") == 1


def test_vertical_focus_follows_visual_order(home) -> None:
    def act(name: str) -> int:
        _invoke(home, "handleAction", name)
        return home.property("focusZone")

    assert home.property("focusZone") == 0  # carousel
    assert act("NavigateUp") == 2          # hero actions above it
    assert act("NavigateDown") == 0
    assert act("NavigateDown") == 1        # section tiles below it
    assert act("NavigateUp") == 0


def test_hero_backdrop_caps_decoding_and_has_fallback(home) -> None:
    backdrop = _child(home, "couchHeroBackdrop")
    assert 0 < backdrop.property("decodeWidth") <= 1920
    for name in ("couchHeroArtworkA", "couchHeroArtworkB"):
        size = _child(backdrop, name).property("sourceSize")
        assert 0 < size.width() <= 1920
    assert backdrop.property("showingArtwork") is False  # fixtures have no artwork
    assert _child(backdrop, "couchHeroFallback").property("visible") is True


def test_top_bar_has_no_telemetry_or_placeholder_values() -> None:
    sources = [
        (COUCH / "components" / "CouchTopBar.qml").read_text(encoding="utf-8"),
        (COUCH / "CouchHome.qml").read_text(encoding="utf-8"),
    ]
    for source in sources:
        code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
        assert not re.search(r"\b(CPU|GPU|RAM|VRAM)\b", code)
        assert not re.search(r"\d+\s*%\"|\"\s*—\s*\"", code)


def test_bottom_navigation_contract(home) -> None:
    nav = _child(home, "couchHomeNavigation")
    assert nav.property("count") == 4
    tiles = _plain(home.property("homeTiles"))
    assert all(tile["subtitle"] and tile["icon"] and tile["iconOnLight"] for tile in tiles)
    assert not any("donate" in str(tile).casefold() for tile in tiles)
    assert tiles[2]["subtitle"] == "File changes and re-analysis"
    assert nav.property("activeIndex") == 0  # Home belongs to Library

    _invoke(home, "handleAction", "NavigateDown")      # carousel -> navigation
    assert home.property("focusZone") == 1
    assert nav.property("navFocused") is True
    _invoke(home, "handleAction", "NavigateRight")
    _invoke(home, "handleAction", "NavigateRight")
    assert nav.property("currentIndex") == 2
    _invoke(home, "handleAction", "NavigateUp")        # back to the carousel
    assert home.property("focusZone") == 0
    assert nav.property("navFocused") is False


def test_home_uses_only_couch_glyphs() -> None:
    source = (COUCH / "CouchHome.qml").read_text(encoding="utf-8")
    # launcherLogo() is the Couch launcher-logo lookup (couch/launchers/).
    used = set(re.findall(r"App\.UiIcons\.(\w+)", source)) - {"launcherLogo"}
    assert used and all(name.startswith("couchGlyph") for name in used), used
    for name in ("CouchTopBar", "CouchGameCard", "CouchBottomNav"):
        component = (COUCH / "components" / f"{name}.qml").read_text(encoding="utf-8")
        assert all(n.startswith("couchGlyph") or n == "launcherLogo"
                   for n in re.findall(r"App\.UiIcons\.(\w+)", component)), name


def test_top_bar_has_no_settings_shortcut() -> None:
    source = (COUCH / "components" / "CouchTopBar.qml").read_text(encoding="utf-8")
    assert "couchTopBarSettings" not in source
    assert "settingsRequested" not in source
