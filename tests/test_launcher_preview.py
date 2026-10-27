"""Launcher-specific launch preview: Heroic/Lutris never show Steam Launch Options or %command%."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from test_heroic_optiscaler import APP_NAME
from test_heroic_proton_tweaks_only import _setup


def test_heroic_preview_lists_launcher_fields_saved_draft_and_restore(tmp_path, monkeypatch) -> None:
    game, controller, path = _setup(tmp_path, monkeypatch)
    try:
        controller.saveProtonTweaks(game.id, {"states": {"steam_deck_spoof": "enabled"}})
        saved = controller.getOptimizationProfile(game.id)["launcherPreview"]
        assert saved["launcher"] == "Heroic" and saved["fields"]["wrapper"] == "wrapperOptions"
        assert saved["apply"]["environment"] == {"SteamDeck": "1"} and saved["apply"]["wrapper"] is None
        assert saved["current"]["environment"] == {"FOREIGN": "1"}
        assert saved["current"]["settings"] == {"disableUMU": None, "launcherArgs": None}
        assert saved["state"] == "not_applied" and saved["restore"] == {} and saved["draft"] is None
        assert saved["savedAt"]["protonTweaks"]
        controller.applyLauncherIntegration(game.id)
        applied = controller.getOptimizationProfile(game.id)["launcherPreview"]
        assert applied["state"] == "applied" and applied["restore"]["environment"] == {"SteamDeck": None}
        assert applied["restore"]["byteIdentical"] is True
        assert applied["restore"]["appliedRevision"] == applied["apply"]["revision"]
        # Draft with GameMode on (Heroic already enables it, so nothing changes) and a draft with
        # an explicit Gamescope wrapper on a host that has it: draft is listed separately.
        values = {"preset": "custom", "gamemodeEnabled": True}
        draft = controller.previewOptimizationProfile(game.id, values)["launcherPreview"]
        assert draft["draft"] is None  # same launcher entries
        assert draft["apply"]["revision"] == applied["apply"]["revision"]
        assert json.loads(path.read_text())[APP_NAME]["enviromentOptions"][-1] == {"key": "SteamDeck", "value": "1"}
    finally:
        controller.shutdown()


def test_heroic_draft_differs_from_saved_until_saved(tmp_path, monkeypatch) -> None:
    from game_optimization_linux.services.optimization_runtime import RuntimeToolAvailability

    class Tools:
        def detect(self, *, refresh: bool = False):
            return (RuntimeToolAvailability("GameMode", False),
                    RuntimeToolAvailability("Gamescope", True, "/usr/bin/gamescope",
                                            supported_options=("-W", "-H", "-r", "-f")))

    game, controller, _path = _setup(tmp_path, monkeypatch)
    controller._runtime_tool_detector = Tools()
    try:
        values = {"preset": "custom", "gamescopeEnabled": True, "gamescopeMode": "native"}
        preview = controller.previewOptimizationProfile(game.id, values)["launcherPreview"]
        assert preview["apply"]["wrapper"] is None
        assert preview["draft"]["wrapper"]["exe"] == "/usr/bin/gamescope"
        assert preview["draft"]["settings"] == {}  # native Heroic: Disable UMU only for Heroic Flatpak
        # Nothing was saved or written by the preview.
        assert controller._optimization_profile_repository.load(game.id).gamescope_enabled is False
        assert controller.getLauncherIntegration(game.id)["state"] == "not_applied"
    finally:
        controller.shutdown()


def test_lutris_preview_uses_lutris_terms(tmp_path, monkeypatch) -> None:
    from test_custom_launch import _lutris_controller, _shutdown_controllers  # noqa: F401
    game, controller, _path = _lutris_controller(tmp_path, monkeypatch)
    controller.saveProtonTweaks(game.id, {"states": {"steam_deck_spoof": "enabled"}})
    preview = controller._optimization_controller._launcher_preview(game)
    assert preview["fields"] == {"environment": "system.env", "wrapper": "system.prefix_command", "arguments": "game.args"}
    assert preview["current"]["environment"] == {"FOREIGN": "keep"}


@pytest.fixture
def qml_app():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def _details(qml_app, kind, preview, plan_text="steam-launch-wrapper -- %command%"):
    from qml_runtime_probe import _settle, _view
    view, root = _view(qml_app, "pages/details/LaunchEnvironmentDetails.qml", 900, 1200, initial_properties={
        "plan": {"wrappers": ["gamemode"], "environment": {"LD_PRELOAD": "libgamemode.so.0"},
                 "environmentClassification": [{"key": "LD_PRELOAD", "value": "libgamemode.so.0", "category": "activation",
                                                "owner": "GameMode", "target": "game", "code": "feature"}]},
        "planSource": {}, "commandText": plan_text,
        "launcherKind": kind, "launcherPreview": preview, "expanded": True})
    _settle(qml_app, 6)
    return view, root


def _texts(root):
    from qml_runtime_probe import _descendants
    return "\n".join(str(item.property("text") or "") for item in _descendants(root)
                     if item.property("visible") and item.property("text") is not None)


def test_heroic_details_never_show_steam_terms(qml_app, tmp_path, monkeypatch) -> None:
    game, controller, _path = _setup(tmp_path, monkeypatch)
    try:
        controller.saveProtonTweaks(game.id, {"states": {"steam_deck_spoof": "enabled"}})
        controller.applyLauncherIntegration(game.id)
        preview = controller.getOptimizationProfile(game.id)["launcherPreview"]
    finally:
        controller.shutdown()
    view, root = _details(qml_app, "Heroic", preview)
    try:
        text = _texts(root)
        assert "%command%" not in text and "Launch Options" not in text and "Steam" not in text.replace("SteamDeck", "").replace("Steam Deck", "")
        assert "enviromentOptions: SteamDeck=1" in text
        assert "Restore puts back" in text and "enviromentOptions: SteamDeck=(not set)" in text
        assert "byte for byte" in text
        summary = next(line for line in text.splitlines() if line.startswith("Selected features:"))
        assert "GameMode (enabled by Heroic)" in summary and "Steam Deck mode (requested)" in summary
        assert "LD_PRELOAD" not in text  # Steam runner rows are not shown for a launcher file
    finally:
        view.close()


def test_steam_and_local_details_keep_their_own_terms(qml_app) -> None:
    view, root = _details(qml_app, "steam", {})
    try:
        assert "Steam Launch Options entry stays the same" in _texts(root) and "%command%" in _texts(root)
    finally:
        view.close()
    view, root = _details(qml_app, "local", {}, plan_text="gamemoderun %command%")
    try:
        text = _texts(root)
        assert "Launch Options" not in text and "%command%" not in text and "<game executable>" in text
    finally:
        view.close()
