"""Couch Library: real filters on stable ids, Details hand-off, focus return,
empty states, bottom navigation and the icons it uses."""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import Q_ARG, QCoreApplication, QMetaObject, QObject, Qt, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtTest import QTest

from game_optimization_linux import config

COUCH = config.QML_DIR / "couch"
UI_ICONS = (config.QML_DIR / "UiIcons.qml").read_text(encoding="utf-8")


def _game(game_id: str, launcher: str = "Steam", **extra) -> dict:
    game = {"id": game_id, "name": f"Game {game_id}", "launcher": launcher, "status": "Ready",
            "libraryAvailable": True, "launchAllowed": True, "filesystem": "ext4",
            "sizeBytes": 0, "savedBytes": 0, "portraitArtwork": "", "effectiveArtworkUrl": ""}
    game.update(extra)
    return game


GAMES = [
    _game("a"), _game("b", "Heroic"), _game("c", "Manual", launchAllowed=False),
    _game("d", status="Drive disconnected", libraryAvailable=False, launchAllowed=False),
]
MANY = [_game(f"{index:02d}") for index in range(40)]
WRAPPER = f"""
import QtQuick
import "{QUrl.fromLocalFile(str(COUCH)).toString()}"
CouchLibrary {{
    property string opened: ""
    property string requested: ""
    onOpenGame: function(gameId) {{ opened = gameId }}
    onSectionRequested: function(name) {{ requested = name }}
}}
"""


def _settle(count: int = 6) -> None:
    for _ in range(count):
        QCoreApplication.processEvents()


def _make(engine: QQmlEngine, games, **controller) -> QObject:
    component = QQmlComponent(engine)
    component.setData(WRAPPER.encode(), QUrl.fromLocalFile(str(COUCH / "LibraryTest.qml")))
    data = {"games": games, "isScanning": False, "libraryScanStatus": "", "libraryScanMessage": ""}
    data.update(controller)
    root = component.createWithInitialProperties(
        {"controller": data, "width": 1920, "height": 1080})
    assert root is not None, [error.toString() for error in component.errors()]
    _KEEP.extend((component, root))
    _settle()
    return root


_KEEP: list[QObject] = []


@pytest.fixture()
def engine():
    engine = QQmlEngine()
    yield engine
    _KEEP.clear()
    _settle()
    del engine


def _act(page: QObject, *actions: str) -> None:
    for action in actions:
        QMetaObject.invokeMethod(page, "handleAction", Qt.DirectConnection, Q_ARG("QVariant", action))
        _settle(3)


def _ids(page: QObject) -> list[str]:
    value = page.property("filteredGames")
    value = value.toVariant() if hasattr(value, "toVariant") else value
    return [game["id"] for game in value]


def test_confirm_opens_details_for_the_selected_card(engine) -> None:
    page = _make(engine, GAMES)
    assert page.property("focusZone") == 1 and page.property("selectedIndex") == 0
    _act(page, "NavigateRight", "NavigateRight")          # a, b, c (sorted by name)
    _act(page, "Confirm")
    assert page.property("opened") == "c"                  # Details, even if not launchable
    _act(page, "NavigateRight", "Confirm")
    assert page.property("opened") == "d"                  # disconnected games still open Details


def test_return_from_details_keeps_game_focus_and_scroll(engine) -> None:
    page = _make(engine, MANY)
    columns = page.property("columns")
    _act(page, *["NavigateDown"] * 3, "NavigateRight")
    QTest.qWait(300)                                       # animated row scroll
    grid = page.findChild(QObject, "couchLibraryGrid")
    selected, scrolled = page.property("retainedGameId"), grid.property("contentY")
    assert page.property("selectedIndex") == 3 * columns + 1 and scrolled > 0
    page.setProperty("visible", False)                     # Details shown
    _settle()
    page.setProperty("visible", True)
    QMetaObject.invokeMethod(page, "focusGame", Qt.DirectConnection, Q_ARG("QVariant", selected))
    _settle()
    assert page.property("retainedGameId") == selected
    assert page.property("focusZone") == 1
    assert grid.property("contentY") == pytest.approx(scrolled)


def test_filters_use_stable_ids_and_select_first_game(engine) -> None:
    page = _make(engine, GAMES)
    keys = [item["key"] for item in page.property("filterItems").toVariant()]
    assert keys == ["search", "sort", "source:all", "source:steam", "source:heroic",
                    "source:custom", "state:ready", "state:unavailable"]
    _act(page, "NavigateRight")
    page.setProperty("sourceFilter", "custom")
    QMetaObject.invokeMethod(page, "filterChanged", Qt.DirectConnection)
    _settle()
    assert _ids(page) == ["c"] and page.property("selectedIndex") == 0
    page.setProperty("sourceFilter", "all")
    page.setProperty("stateFilter", "unavailable")
    _settle()
    assert _ids(page) == ["c", "d"]
    page.setProperty("stateFilter", "any")
    page.setProperty("sortMode", "name-desc")
    _settle()
    assert _ids(page) == ["d", "c", "b", "a"]
    _act(page, "NextTab")                                  # R1 cycles the real sources
    assert page.property("sourceFilter") == "steam" and _ids(page) == ["d", "a"]


@pytest.mark.parametrize(
    ("games", "controller", "props", "state"),
    [
        ([], {"isScanning": True}, {}, "loading"),
        ([], {"libraryScanStatus": "error"}, {}, "error"),
        ([], {}, {}, "empty"),
        (GAMES, {}, {"searchQuery": "zzz"}, "no-results"),
        (GAMES, {}, {"sourceFilter": "heroic", "stateFilter": "unavailable"}, "no-matches"),
        (GAMES, {}, {}, "grid"),
    ],
)
def test_empty_states(engine, games, controller, props, state) -> None:
    page = _make(engine, games, **controller)
    for name, value in props.items():
        page.setProperty(name, value)
    _settle()
    assert page.property("viewState") == state
    assert page.findChild(QObject, "couchLibraryEmptyState").property("visible") is (state != "grid")
    assert page.findChild(QObject, "couchLibraryGridFrame").property("visible") is (state == "grid")
    if state != "grid":
        assert page.property("focusZone") != 1              # never focus an empty grid
        assert page.findChild(QObject, "couchLibraryEmptyIcon").property("imageStatus") == 1


def test_bottom_navigation_order_and_return(engine) -> None:
    page = _make(engine, GAMES)
    nav = page.findChild(QObject, "couchLibraryNavigation")
    tiles = page.property("libraryTiles").toVariant()
    assert nav.property("count") == 4 and nav.property("activeIndex") == 0
    assert [tile["id"] for tile in tiles] == ["library", "tasks", "updates", "settings"]
    assert "donate" not in str(tiles).casefold()
    _act(page, "NavigateRight", "NavigateUp")               # grid -> filters
    assert page.property("focusZone") == 0
    _act(page, "NavigateDown", "NavigateDown")              # filters -> grid -> nav
    assert page.property("focusZone") == 2 and nav.property("navFocused") is True
    _act(page, "NavigateRight", "Confirm")
    assert page.property("requested") == "tasks"
    _act(page, "NavigateUp")
    assert page.property("focusZone") == 1 and page.property("selectedIndex") == 1


def test_library_uses_existing_couch_glyphs_only() -> None:
    for name in ("CouchLibrary.qml", "components/CouchGameCard.qml"):
        used = set(re.findall(r"App\.UiIcons\.(\w+)", (COUCH / name).read_text(encoding="utf-8"))) - {"launcherLogo"}
        assert used and all(icon.startswith("couchGlyph") for icon in used), (name, used)
        for icon in used:
            match = re.search(rf'property url {icon}: Qt\.resolvedUrl\("\.\./([^"]+)"\)', UI_ICONS)
            assert match and (config.PACKAGE_DIR / match.group(1)).is_file(), icon


def test_changed_couch_qml_compiles(engine) -> None:
    for name in ("CouchMain.qml", "CouchLibrary.qml", "components/CouchGameCard.qml"):
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(COUCH / name)))
        assert component.isReady(), [error.toString() for error in component.errors()]


def test_launcher_names_keep_stable_ids_and_detected_empty_lutris(engine) -> None:
    diagnostics = [{"launcher": "Lutris", "roots": [{"state": "found"}]}]
    page = _make(engine, GAMES, libraryProviderDiagnostics=diagnostics)
    items = {item["key"]: item for item in page.property("filterItems").toVariant()}
    assert items["source:heroic"]["label"] == "Heroic Games Launcher"   # label only
    assert items["source:lutris"]["label"] == "Lutris (0)" and items["source:lutris"]["disabled"] is True
    page.setProperty("sourceFilter", "heroic")                             # data keeps the stable id
    _settle()
    assert _ids(page) == ["b"]
    i18n = (config.QML_DIR / "I18n.qml").read_text(encoding="utf-8")
    assert 'case "heroic": return "Heroic Games Launcher"' in i18n



def test_launcher_logos_are_packaged_transparent_and_optional() -> None:
    import fnmatch
    import tomllib

    from PySide6.QtGui import QImage

    package = tomllib.loads((config.PACKAGE_DIR.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    globs = package["tool"]["setuptools"]["package-data"]["game_optimization_linux"]
    for name in ("steam", "heroic", "lutris"):
        relative = f"assets/ui-icons/couch/launchers/{name}.png"
        assert any(fnmatch.fnmatch(relative, glob) for glob in globs), relative
        image = QImage(str(config.PACKAGE_DIR / relative))
        assert not image.isNull() and image.hasAlphaChannel(), name
        corners = [image.pixelColor(x, y).alpha() for x in (0, image.width() - 1) for y in (0, image.height() - 1)]
        assert corners == [0, 0, 0, 0], name
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nimport "%s" as App\nQtObject { property var logos: ["steam", "Heroic", "lutris", "Manual", ""].map(function(id) { return String(App.UiIcons.launcherLogo(id)) }) }'
                      % QUrl.fromLocalFile(str(config.QML_DIR)).toString().encode(), QUrl.fromLocalFile(str(COUCH / "LogoTest.qml")))
    logos = component.create().property("logos").toVariant()
    assert [bool(item) for item in logos] == [True, True, True, False, False]   # custom/unknown -> neutral glyph
    assert logos[1].endswith("launchers/heroic.png")
