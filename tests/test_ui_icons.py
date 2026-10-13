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


def test_all_38_final_ui_icons_are_packaged_and_centralized() -> None:
    names = _manifest_names()
    assert len(names) == 38
    assert {path.name for path in ICON_DIR.glob("*.svg")} == names

    source = UI_ICONS_QML.read_text(encoding="utf-8")
    referenced = set(re.findall(r'assets/ui-icons/([^/"?]+\.svg)', source))
    assert referenced == names
    for name in names:
        svg = (ICON_DIR / name).read_text(encoding="utf-8")
        assert 'viewBox="0 0 24 24"' in svg
        assert 'stroke-width="1.75"' in svg
        assert "<symbol" not in svg
        assert "<use" not in svg


def test_couch_icon_set_is_packaged_and_mapped() -> None:
    couch_dir = ICON_DIR / "couch"
    expected = {
        "library", "tasks", "updates", "settings", "launch",
        "check-updates", "overview", "storage", "optimization",
        "optiscaler", "narrator",
    }
    assert {path.stem for path in couch_dir.glob("*.svg")} == expected
    for path in couch_dir.glob("*.svg"):
        svg = path.read_text(encoding="utf-8")
        assert 'viewBox="0 0 48 48"' in svg
        assert "<symbol" not in svg
        assert "<use" not in svg

    source = UI_ICONS_QML.read_text(encoding="utf-8")
    for name in expected:
        assert f'couch/{name}.svg' in source

    action_names = {
        "analyze", "verify-compression", "apply-optimization",
        "polish-subtitles", "capture-window", "voice", "volume",
        "speech-speed", "subtitle-area", "install", "remove",
        "gamemode", "gamescope", "mangohud",
    }
    action_dir = couch_dir / "actions"
    assert {path.stem for path in action_dir.glob("*.svg")} == action_names
    for path in action_dir.glob("*.svg"):
        svg = path.read_text(encoding="utf-8")
        assert 'viewBox="0 0 48 48"' in svg
        assert "<text" not in svg
        assert "<image" not in svg
    for name in action_names:
        assert f'couch/actions/{name}.svg' in source

    package_data = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["setuptools"]["package-data"]["game_optimization_linux"]
    assert "assets/ui-icons/*.svg" in package_data
    assert "assets/ui-icons/manifest.json" in package_data


def test_every_runtime_ui_icon_reference_resolves_to_the_central_helper() -> None:
    helper_source = UI_ICONS_QML.read_text(encoding="utf-8")
    properties = set(
        re.findall(r"readonly property url ([A-Za-z0-9_]+):", helper_source)
    ) | set(re.findall(r"^    function ([A-Za-z0-9_]+)\(", helper_source, re.MULTILINE))
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


COUCH_QML_ROOT = QML_ROOT / "couch"
COUCH_DETAILS_QML = COUCH_QML_ROOT / "CouchGameDetails.qml"


def _ui_icon_property_targets() -> dict[str, str]:
    """Map every ``UiIcons`` url property to its packaged asset path."""
    source = UI_ICONS_QML.read_text(encoding="utf-8")
    pairs = re.findall(
        r'readonly property url (\w+):\s*Qt\.resolvedUrl\('
        r'"\.\./assets/ui-icons/([^"]+)"\)',
        source,
    )
    return {name: rel for name, rel in pairs}


def _package_data_globs() -> list[str]:
    package_data = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["setuptools"]["package-data"]["game_optimization_linux"]
    return [g for g in package_data if g.startswith("assets/ui-icons/")]


def _is_packaged(rel_path: str, globs: list[str]) -> bool:
    import fnmatch

    full = f"assets/ui-icons/{rel_path}"
    return any(fnmatch.fnmatch(full, glob) for glob in globs)


def test_every_couch_ui_icon_reference_resolves_to_a_packaged_svg() -> None:
    """Requirement 1: each referenced Couch UiIcons property must resolve to an
    existing SVG that package-data actually ships."""
    targets = _ui_icon_property_targets()
    globs = _package_data_globs()

    referenced: set[str] = set()
    for path in COUCH_QML_ROOT.rglob("*.qml"):
        referenced.update(
            re.findall(r"App\.UiIcons\.(couch[A-Za-z0-9_]+)", path.read_text(encoding="utf-8"))
        )

    assert referenced, "expected Couch QML to reference App.UiIcons.couch* icons"

    for name in sorted(referenced):
        assert name in targets, f"UiIcons has no property named {name}"
        rel = targets[name]
        svg = ICON_DIR / rel
        assert svg.is_file(), f"{name} -> {rel} does not exist on disk"
        assert _is_packaged(rel, globs), f"{rel} is not covered by package-data"
        # Preserve the MultiEffect tinting pipeline: source SVGs stay concrete
        # colour, never CSS currentColor (Qt's SVG renderer cannot resolve it).
        assert "currentColor" not in svg.read_text(encoding="utf-8"), rel


def _action_entries(function_name: str) -> list[dict[str, str]]:
    """Extract ``{ ... }`` object literals returned by a named QML function.

    This is a lightweight static parse: it isolates the function body and
    captures each top-level object literal so the test can assert on the
    presence of an ``icon`` key without a full QML engine.
    """
    source = COUCH_DETAILS_QML.read_text(encoding="utf-8")
    start = source.index(f"function {function_name}(")
    depth = 0
    body_start = source.index("{", start)
    i = body_start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = source[body_start : i + 1]

    entries: list[dict[str, str]] = []
    for match in re.finditer(r"\{[^{}]*\}", body):
        literal = match.group(0)
        id_match = re.search(r'"id"\s*:\s*"([^"]+)"', literal)
        if not id_match:
            continue
        icon_match = re.search(r'"icon"\s*:\s*App\.UiIcons\.(\w+)', literal)
        entries.append(
            {"id": id_match.group(1), "icon": icon_match.group(1) if icon_match else ""}
        )
    return entries


def test_optimization_action_cards_have_non_empty_icon_mappings() -> None:
    """Requirement 2: every visible optimization action card that renders an
    icon container must carry a non-empty icon mapping.

    This is the exact class of regression that empty GameMode/Gamescope/
    MangoHud containers slipped through: an action dict with no ``icon`` key.
    """
    entries = {entry["id"]: entry["icon"] for entry in _action_entries("optimizationActions")}
    targets = _ui_icon_property_targets()

    required = ("optimization-profile", "gamemode", "gamescope", "mangohud-profile")
    for action_id in required:
        assert action_id in entries, f"optimizationActions() no longer defines {action_id}"
        icon_property = entries[action_id]
        assert icon_property, (
            f"action card {action_id!r} has no icon mapping and would render an "
            f"empty icon container"
        )
        assert icon_property in targets, (
            f"{action_id} maps to App.UiIcons.{icon_property} which is not a UiIcons property"
        )
        rel = targets[icon_property]
        assert (ICON_DIR / rel).is_file(), f"{action_id} -> {rel} does not exist"
