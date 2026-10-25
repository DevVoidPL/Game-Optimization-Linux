"""Visual contracts: transparent depth and the actual System/Couch mappings."""
from __future__ import annotations

from pathlib import Path
import json
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QRectF, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickView
from PySide6.QtSvg import QSvgRenderer
import pytest

from game_optimization_linux import config

ICONS = config.PACKAGE_DIR / 'assets/ui-icons'
MANGOHUD_BRAND_ICONS = {
    'couch/actions/mangohud.svg', 'couch/glyphs/mangohud.svg',
    'couch/glyphs/mangohud-on-light.svg',
}


def authored_icons() -> list[Path]:
    return [path for path in ICONS.rglob('*.svg')
            if 'brands' not in path.relative_to(ICONS).parts
            and path.relative_to(ICONS).as_posix() not in MANGOHUD_BRAND_ICONS]


def render(path: Path, size: int) -> QImage:
    svg = QSvgRenderer(str(path))
    assert svg.isValid(), path
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    svg.render(painter)
    painter.end()
    return image


def test_authored_icons_follow_the_facet_contract() -> None:
    """Facet set: solid filled planes, no strokes, exactly one accent colour
    per icon (the manifest's default_color on Desktop), plain <path> only."""
    manifest = {e['filename']: e for e in json.loads((ICONS/'manifest.json').read_text())}
    for path in authored_icons():
        root = ET.fromstring(path.read_text())
        assert root.attrib['viewBox'] == '0 0 24 24', path
        assert root.attrib.get('stroke') == 'none', path
        assert 'stroke-width' not in root.attrib, path
        fills = set()
        for node in root.iter():
            tag = node.tag.rsplit('}', 1)[-1]
            assert tag not in {'image', 'use', 'filter', 'mask', 'text', 'style', 'symbol', 'g'}, path
            if tag == 'path':
                assert node.attrib.get('fill', '').startswith('#'), path
                assert 'stroke' not in node.attrib, path
                assert 'fill-opacity' not in node.attrib, path
                fills.add(node.attrib['fill'].upper())
                assert 'currentColor' not in node.attrib['fill'], path
        assert 1 <= len(fills) <= 2, (path, fills)
        if path.parent == ICONS:
            assert manifest[path.name]['default_color'].upper() in fills, path


def test_all_authored_icons_render_with_transparent_safe_margins() -> None:
    # Render, rather than trusting viewBox/isValid: the previous MangoHud SVG
    # was valid but leaked clipped, off-canvas artwork into neighbouring UI.
    size = 240
    # Facet icons are solid silhouettes on a 24-grid with a 1.5-unit safe
    # margin (15 px at 240 px): nothing may touch the canvas edge.
    margin = 15
    for path in authored_icons():
        image = render(path, size)
        alpha = bytes(image.constBits())[3::4]
        assert any(v > 8 for v in alpha), path
        assert not any(v > 8 for v in alpha[:size*margin]), path
        assert not any(v > 8 for v in alpha[-size*margin:]), path
        for y in range(size):
            row = alpha[y*size:(y+1)*size]
            assert not any(v > 8 for v in row[:margin] + row[-margin:]), path


@pytest.mark.parametrize('relative,cut_point,body_point,accent_point', [
    # (x, y) at 240 px: a point inside the diagonal light cut, one on the body
    # plane above-left of it and one on the accent plane below-right of it.
    ('sidebar-settings.svg', (165, 48), (60, 120), (180, 120)),
    ('sidebar-donate.svg', (120, 120), (70, 90), (160, 150)),
    ('couch/glyphs/stop.svg', (120, 120), (70, 120), (170, 120)),
])
def test_facet_cut_is_transparent_and_separates_two_planes(relative, cut_point, body_point, accent_point) -> None:
    image = render(ICONS/relative, 240)
    assert image.pixelColor(*cut_point).alpha() == 0, relative
    body = image.pixelColor(*body_point)
    accent = image.pixelColor(*accent_point)
    assert body.alpha() == 255 and accent.alpha() == 255, relative
    assert body.name() != accent.name(), relative


def test_system_controller_and_runtime_cards_load_the_intended_icons() -> None:
    view = QQuickView()
    view.resize(1280, 900)
    view.setSource(QUrl.fromLocalFile(str(config.QML_DIR/'pages/SystemPage.qml')))
    assert view.status() == QQuickView.Status.Ready, view.errors()
    page = view.rootObject()
    page.setProperty('systemData', {'distribution': 'Freedesktop SDK', 'controllerCount': 1})
    view.show()
    QGuiApplication.processEvents()
    for name, filename in [('systemControllersIcon', 'system-controllers'), ('systemRuntimeIcon', 'system-runtime')]:
        icon = page.findChild(QObject, name)
        assert icon is not None
        source = icon.property('source').toLocalFile()
        assert Path(source).stem in {filename, filename+'-on-light'}
        assert view.engine().newQObject(icon).property('status').toInt() == 1, source  # Image.Ready
    view.close()
    view.deleteLater()


def test_couch_desktop_entry_uses_monitor_in_both_themes() -> None:
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(config.QML_DIR/'couch/CouchSystemMenu.qml')))
    menu = component.create()
    assert menu is not None, component.errors()
    entries = menu.property('entries').toVariant()
    desktop = next(e for e in entries if e['id'] == 'desktop')
    assert desktop['icon'].toLocalFile().endswith('/couch/glyphs/desktop.svg')
    assert desktop['iconOnLight'].toLocalFile().endswith('/couch/glyphs/desktop-on-light.svg')
    assert 'sidebar-system' not in desktop['icon'].toLocalFile()
    menu.deleteLater()


def test_mangohud_uses_original_geometry_and_colours_in_both_themes() -> None:
    original = ET.parse(config.PACKAGE_DIR.parents[1]/'scripts/icon-sources/mangohud-original.svg').getroot()
    original_nodes = {node.get('id'): node for node in original.iter() if node.get('id')}
    # Rendering the Qt-compatible upstream artwork at the intended inset must
    # match every UI variant: no new path, stretching, theme tint or clipping.
    reference = QImage(240, 240, QImage.Format.Format_RGBA8888)
    reference.fill(Qt.GlobalColor.transparent)
    painter = QPainter(reference)
    renderer = QSvgRenderer(str(ICONS/'brands/mangohud.svg'))
    renderer.render(painter, QRectF(25, 25, 190, 190))
    painter.end()
    reference_bytes = bytes(reference.constBits())
    for relative in MANGOHUD_BRAND_ICONS:
        root = ET.parse(ICONS/relative).getroot()
        assert root.attrib['viewBox'] == '0 0 24 24'
        paths = [node for node in root.iter() if node.tag.endswith('}path')]
        assert len(paths) == 4
        for node in paths:
            original_node = original_nodes[node.attrib['id']]
            assert node.attrib['d'] == original_node.attrib['d']
            assert node.attrib['style'] == original_node.attrib['style']
        image = render(ICONS/relative, 240)
        actual = bytes(image.constBits())
        assert max(abs(a-b) for a, b in zip(actual, reference_bytes)) <= 2, relative
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(30, 30).name() == '#fffae3'


def test_different_graphics_functions_do_not_reuse_the_same_rendered_symbol() -> None:
    # Compare pixels, not filenames/titles: differently named source entries
    # previously rendered the exact same artwork for unrelated controls.
    groups = [
        ['remaster-classic', 'remaster-light-ai', 'remaster-quality-ai'],
        ['action-measure', 'btrfs-exact-measurement'],
        ['couch/glyphs/mangohud', 'couch/glyphs/analyze'],
        ['couch/glyphs/settings', 'couch/glyphs/tune', 'couch/glyphs/preset'],
        ['sidebar-settings', 'action-advanced-settings', 'overview-optimization-profile'],
        ['couch/glyphs/fullscreen', 'couch/glyphs/gamescope', 'couch/glyphs/borderless', 'couch/glyphs/resolution-game'],
        ['couch/glyphs/optimization', 'couch/glyphs/fps-limit'],
        ['couch/glyphs/frametime', 'couch/glyphs/vrr'],
    ]
    for group in groups:
        for suffix in ('.svg', '-on-light.svg'):
            for size in (16, 24, 48):
                silhouettes = []
                for name in group:
                    image = render(ICONS/(name+suffix), size)
                    silhouettes.append(bytes(image.constBits())[3::4])
                assert len(set(silhouettes)) == len(group), (group, suffix, size)
