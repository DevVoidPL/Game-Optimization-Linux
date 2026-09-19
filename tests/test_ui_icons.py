from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from game_optimization_linux import config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QML_ROOT = config.QML_DIR
ICON_DIR = config.PACKAGE_DIR / "assets" / "ui-icons"
UI_ICONS_QML = QML_ROOT / "UiIcons.qml"
UI_ICON_COMPONENT = QML_ROOT / "components" / "UiIcon.qml"


def _manifest_names() -> set[str]:
    manifest = json.loads((ICON_DIR / "manifest.json").read_text(encoding="utf-8"))
    return {str(entry["filename"]) for entry in manifest}


def test_all_37_final_ui_icons_are_packaged_and_centralized() -> None:
    names = _manifest_names()
    assert len(names) == 37
    assert {path.name for path in ICON_DIR.glob("*.svg")} == names

    source = UI_ICONS_QML.read_text(encoding="utf-8")
    referenced = set(re.findall(r'assets/ui-icons/([^"?]+\.svg)', source))
    assert referenced == names
    for name in names:
        svg = (ICON_DIR / name).read_text(encoding="utf-8")
        assert 'viewBox="0 0 24 24"' in svg
        assert 'stroke-width="1.75"' in svg
        assert "<symbol" not in svg
        assert "<use" not in svg

    package_data = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["setuptools"]["package-data"]["game_optimization_linux"]
    assert "assets/ui-icons/*.svg" in package_data
    assert "assets/ui-icons/manifest.json" in package_data


def test_every_runtime_ui_icon_reference_resolves_to_the_central_helper() -> None:
    helper_source = UI_ICONS_QML.read_text(encoding="utf-8")
    properties = set(
        re.findall(r"readonly property url ([A-Za-z0-9_]+):", helper_source)
    )
    for path in QML_ROOT.rglob("*.qml"):
        if path == UI_ICONS_QML:
            continue
        source = path.read_text(encoding="utf-8")
        for property_name in re.findall(r"App\.UiIcons\.([A-Za-z0-9_]+)", source):
            assert property_name in properties, f"{path}: {property_name}"
    assert "GrafikiProgramu" not in "\n".join(
        path.read_text(encoding="utf-8") for path in QML_ROOT.rglob("*.qml")
    )


def test_fixed_mint_icons_receive_theme_aware_presentation() -> None:
    icon_component = UI_ICON_COMPONENT.read_text(encoding="utf-8")
    assert "MultiEffect" in icon_component
    assert "colorizationColor: icon.tintColor" in icon_component

    button = (QML_ROOT / "components/AppButton.qml").read_text(encoding="utf-8")
    metric = (QML_ROOT / "components/MetricTile.qml").read_text(encoding="utf-8")
    sidebar = (QML_ROOT / "components/Sidebar.qml").read_text(encoding="utf-8")
    graphics = (QML_ROOT / "pages/details/GraphicsTab.qml").read_text(
        encoding="utf-8"
    )
    assert 'tintEnabled: !App.Theme.dark || control.kind === "primary"' in button
    assert "tintColor: control.enabled ? control.foregroundColor" in button
    assert "tintEnabled: !App.Theme.dark" in metric
    assert "tintColor: tile.tone" in metric
    assert "tintEnabled: !App.Theme.dark" in sidebar
    assert "? App.Theme.accent : App.Theme.textSecondary" in sidebar
    assert "tintEnabled: !App.Theme.dark" in graphics

    # Status artwork already has semantic colors and must not be flattened into
    # the generic action-icon tint.
    toast = (QML_ROOT / "components/ToastHost.qml").read_text(encoding="utf-8")
    assert "source: host.iconFor(toast.tone)" in toast
    assert "\n                UiIcon {" not in toast


def test_requested_ui_surfaces_use_the_final_icon_mappings() -> None:
    storage = (QML_ROOT / "pages/details/StorageTab.qml").read_text(encoding="utf-8")
    graphics = (QML_ROOT / "pages/details/GraphicsTab.qml").read_text(encoding="utf-8")
    overview = (QML_ROOT / "pages/details/OverviewTab.qml").read_text(encoding="utf-8")
    sidebar = (QML_ROOT / "components/Sidebar.qml").read_text(encoding="utf-8")

    assert 'text: "B"' not in storage
    assert "App.UiIcons.btrfsAnalyzeCompression" in storage
    assert "App.UiIcons.btrfsVerifyCompression" in storage
    assert "App.UiIcons.btrfsExactMeasurement" in storage

    for name in ("remasterClassic", "remasterLightAi", "remasterQualityAi"):
        assert f"App.UiIcons.{name}" in graphics
    for name in (
        "overviewLogicalSize",
        "overviewPhysicalSize",
        "overviewSavedSpace",
        "overviewOptimizationProfile",
    ):
        assert f"App.UiIcons.{name}" in overview
    for name in (
        "sidebarGames",
        "sidebarUpdates",
        "sidebarTasks",
        "sidebarSystem",
        "sidebarSettings",
    ):
        assert f"App.UiIcons.{name}" in sidebar


def test_monolithic_core_branding_replaces_refined_b_at_runtime() -> None:
    branding_dir = config.BRANDING_DIR
    assert not (branding_dir / "game-optimization-linux-horizontal-light.svg").exists()
    assert (branding_dir / "game-optimization-linux-symbol.svg").is_file()
    assert config.APP_ICON_SVG.read_text(encoding="utf-8").find("M 500.824,132.000") >= 0
    assert "M 762.151,290.685" not in config.APP_ICON_SVG.read_text(encoding="utf-8")

    runtime_sources = [
        *QML_ROOT.rglob("*.qml"),
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "flatpak" / f"{config.APP_ID}.yml",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources)
    for stale in ("Branding.qml", "pingwin", "penguin", "RefinedB", "GrafikaProgramu"):
        assert stale not in combined


def test_sidebar_uses_compact_brand_without_renaming_the_application() -> None:
    sidebar = (QML_ROOT / "components/Sidebar.qml").read_text(encoding="utf-8")

    assert 'text: "GameOpti"' in sidebar
    assert "ToolTip.text: sidebar.appName" in sidebar
    assert config.APP_NAME == "Game Optimization Linux"
