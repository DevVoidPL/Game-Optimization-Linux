"""Couch glyph set: every glyph has a dark and an on-light variant that is
packaged, Qt-renderable and free of text, raster images and currentColor."""

from __future__ import annotations

import fnmatch
import re
import tomllib

from PySide6.QtSvg import QSvgRenderer

from game_optimization_linux import config

GLYPHS = config.PACKAGE_DIR / "assets" / "ui-icons" / "couch" / "glyphs"
UI_ICONS = config.QML_DIR / "UiIcons.qml"


def test_every_glyph_property_has_packaged_dark_and_light_artwork() -> None:
    source = UI_ICONS.read_text(encoding="utf-8")
    props = dict(re.findall(r'property url (couchGlyph\w+): Qt\.resolvedUrl\("\.\./([^"]+)"\)', source))
    dark = {k: v for k, v in props.items() if not k.endswith("OnLight")}
    assert len(dark) >= 13
    package = tomllib.loads((config.PACKAGE_DIR.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    globs = package["tool"]["setuptools"]["package-data"]["game_optimization_linux"]
    for name, rel in dark.items():
        light_rel = props.get(name + "OnLight")
        assert light_rel, f"{name} has no OnLight variant"
        for path_rel in (rel, light_rel):
            path = config.PACKAGE_DIR / path_rel
            assert path.is_file(), path_rel
            assert any(fnmatch.fnmatch(path_rel, glob) for glob in globs), path_rel
            svg = path.read_text(encoding="utf-8")
            assert 'viewBox="0 0 24 24"' in svg
            for banned in ("currentColor", "<text", "<image", "<style", "filter"):
                assert banned not in svg, (path_rel, banned)
            assert QSvgRenderer(str(path)).isValid(), path_rel


def test_glyph_directory_has_no_unmapped_files() -> None:
    source = UI_ICONS.read_text(encoding="utf-8")
    mapped = set(re.findall(r'couch/glyphs/([^"]+\.svg)', source))
    assert {path.name for path in GLYPHS.glob("*.svg")} == mapped
