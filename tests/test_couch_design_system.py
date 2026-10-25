"""Stage 1 Couch visual system: component contracts and readable results.

The tests instantiate the real QML components in-process (offscreen, no
window) and check observable behaviour in both themes and all motion modes:
which artwork an icon shows, when it is flattened to one colour, whether text
and focus rings keep sufficient contrast, and whether motion can be removed.
They deliberately do not assert on implementation techniques.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux import config

COMPONENTS = config.QML_DIR / "couch" / "components"
ICONS = config.PACKAGE_DIR / "assets" / "ui-icons"
DARK_ART = QUrl.fromLocalFile(str(ICONS / "couch" / "launch.svg")).toString()
LIGHT_ART = QUrl.fromLocalFile(str(ICONS / "couch" / "actions" / "analyze.svg")).toString()
TONES = ("success", "warning", "danger", "info", "neutral", "unavailable")
PILLS = "\n    ".join(
    f'CouchStatePill {{ objectName: "pill_{tone}"; tone: "{tone}"; text: "State {tone}" }}'
    for tone in TONES
)

PROBE = f"""
import QtQuick
import "."
import "../.." as App

Item {{
    id: root
    property string themeMode: "dark"
    property string motionMode: "full"
    property bool frameActive: false
    property bool tileFocused: false
    property bool controlsEnabled: true

    Binding {{ target: App.Theme; property: "mode"; value: root.themeMode }}
    Binding {{ target: App.Theme; property: "couchMotionMode"; value: root.motionMode }}

    readonly property bool themeDark: App.Theme.dark
    readonly property color themeText: App.Theme.text
    readonly property color themeBackground: App.Theme.background
    readonly property color themeSurface: App.Theme.surface
    readonly property color focusSurface: App.Theme.couchFocusSurface
    readonly property color focusRing: App.Theme.couchFocusRing
    readonly property int motionFast: App.Theme.couchMotionFast
    readonly property int motionNormal: App.Theme.couchMotionNormal
    readonly property int fadeFast: App.Theme.couchFadeFast
    readonly property int animation: App.Theme.couchAnimation

    CouchIcon {{ objectName: "artworkOnly"; source: "{DARK_ART}" }}
    CouchIcon {{ objectName: "withLight"; source: "{DARK_ART}"; lightSource: "{LIGHT_ART}" }}
    CouchIcon {{ objectName: "muted"; source: "{DARK_ART}"; muted: true }}
    CouchIcon {{ objectName: "empty" }}

    {PILLS}

    Item {{
        width: 200; height: 80
        CouchFocusFrame {{ objectName: "frame"; active: root.frameActive }}
    }}

    CouchTile {{
        objectName: "tile"
        width: 400; height: 132
        text: "Launch"
        iconSource: "{DARK_ART}"
        focus: root.tileFocused
        enabled: root.controlsEnabled
    }}
    CouchButton {{
        objectName: "button"
        text: "Settings"
        iconSource: "{DARK_ART}"
        focus: root.tileFocused
        enabled: root.controlsEnabled
    }}
}}
"""


def _luminance(color: QColor) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.redF())
        + 0.7152 * channel(color.greenF())
        + 0.0722 * channel(color.blueF())
    )


def _contrast(first: QColor, second: QColor) -> float:
    a, b = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.fixture(scope="module")
def probe():
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(PROBE.encode("utf-8"), QUrl.fromLocalFile(str(COMPONENTS / "_stage1_probe.qml")))
    root = component.create()
    assert root is not None, [error.toString() for error in component.errors()]
    QCoreApplication.processEvents()
    yield root
    root.deleteLater()
    QCoreApplication.processEvents()
    del engine


def _set(root, **values) -> None:
    for key, value in values.items():
        root.setProperty(key, value)
    QCoreApplication.processEvents()


def _child(root, name: str):
    from PySide6.QtCore import QObject

    item = root.findChild(QObject, name)
    assert item is not None, f"missing {name}"
    return item


@pytest.fixture(autouse=True)
def _reset(probe):
    # Colour/scale Behaviors would otherwise be caught mid-transition; results
    # are checked in their settled state. The motion test sets its own modes.
    _set(probe, motionMode="off", themeMode="dark", frameActive=False,
         tileFocused=False, controlsEnabled=True)
    yield


def test_two_tone_artwork_is_shown_untouched_in_dark_theme(probe) -> None:
    icon = _child(probe, "artworkOnly")
    assert icon.property("renderMode") == "artwork"
    assert icon.property("effectiveSource").toString() == DARK_ART
    image = _child(icon, "couchIconImage")
    assert image.property("tintEnabled") is False  # no flattening effect layer
    assert icon.property("imageStatus") == 1  # Image.Ready: the SVG really loads


def test_light_variant_keeps_two_tone_artwork_in_light_theme(probe) -> None:
    _set(probe, themeMode="light")
    icon = _child(probe, "withLight")
    assert probe.property("themeDark") is False
    assert icon.property("renderMode") == "artwork"
    assert icon.property("effectiveSource").toString() == LIGHT_ART
    assert _child(icon, "couchIconImage").property("tintEnabled") is False


def test_light_theme_without_variant_falls_back_to_readable_monochrome(probe) -> None:
    _set(probe, themeMode="light")
    icon = _child(probe, "artworkOnly")
    assert icon.property("renderMode") == "monochrome"
    assert _child(icon, "couchIconImage").property("tintEnabled") is True
    assert _contrast(icon.property("renderColor"), probe.property("themeSurface")) >= 4.5


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_muted_icon_is_dimmed_but_keeps_its_artwork(probe, theme: str) -> None:
    _set(probe, themeMode=theme)
    icon = _child(probe, "muted")
    assert icon.property("renderMode") == "muted"
    assert 0.2 < icon.property("opacity") < 0.6
    image = _child(icon, "couchIconImage")
    assert image.property("tintEnabled") is False
    assert icon.property("imageStatus") == 1


def test_icon_without_source_takes_no_space(probe) -> None:
    assert _child(probe, "empty").property("visible") is False


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("tone", TONES)
def test_state_pill_is_readable_and_not_colour_only(probe, theme: str, tone: str) -> None:
    _set(probe, themeMode=theme)
    pill = _child(probe, f"pill_{tone}")
    label = _child(pill, "couchStatePillLabel")
    dot = _child(pill, "couchStatePillDot")
    assert label.property("text") == f"State {tone}"
    assert _contrast(pill.property("labelColor"), pill.property("color")) >= 4.5
    assert _contrast(pill.property("toneColor"), pill.property("color")) >= 3.0
    # Shape cue in addition to colour: unavailable is a hollow ring.
    assert pill.property("hollow") is (tone == "unavailable")
    assert dot.property("visible") is True


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_focus_ring_is_visible_against_page_and_focused_surface(probe, theme: str) -> None:
    _set(probe, themeMode=theme)
    ring = probe.property("focusRing")
    assert _contrast(ring, probe.property("themeBackground")) >= 3.0
    assert _contrast(ring, probe.property("focusSurface")) >= 3.0


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_focused_tile_and_button_text_stays_readable(probe, theme: str) -> None:
    _set(probe, themeMode=theme, tileFocused=True)
    tile = _child(probe, "tile")
    button = _child(probe, "button")
    assert tile.property("focusVisible") is True
    tile_bg = _child(tile, "couchTileBackground").property("color")
    button_bg = _child(button, "couchButtonBackground").property("color")
    assert _contrast(_child(tile, "couchTileTitle").property("color"), tile_bg) >= 4.5
    assert _contrast(_child(button, "couchButtonLabel").property("color"), button_bg) >= 4.5


def test_disabled_tile_and_button_mute_their_icons(probe) -> None:
    _set(probe, controlsEnabled=False)
    assert _child(_child(probe, "tile"), "couchTileIcon").property("renderMode") == "muted"
    assert _child(_child(probe, "button"), "couchButtonIcon").property("renderMode") == "muted"


def test_motion_modes_shorten_or_remove_animation(probe) -> None:
    _set(probe, motionMode="full")
    assert (probe.property("motionFast"), probe.property("animation")) == (120, 150)

    _set(probe, motionMode="reduced")
    assert probe.property("motionFast") == 0
    assert probe.property("motionNormal") == 0
    assert 0 < probe.property("fadeFast") <= 60

    _set(probe, motionMode="off")
    assert probe.property("motionFast") == 0
    assert probe.property("fadeFast") == 0
    assert probe.property("animation") == 0


def test_focus_frame_appears_instantly_when_motion_is_off(probe) -> None:
    _set(probe, motionMode="off", frameActive=True)
    frame = _child(probe, "frame")
    assert frame.property("opacity") == 1.0
    assert frame.property("visible") is True
    _set(probe, frameActive=False)
    assert frame.property("opacity") == 0.0
