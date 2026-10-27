"""Couch and Desktop Editions from one codebase (build-time marker)."""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from game_optimization_linux import edition

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
DESKTOP = ROOT / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml"


def test_marker_values(tmp_path: Path) -> None:
    assert edition._read_edition(tmp_path / "missing") == "couch"
    (tmp_path / "e").write_text("desktop\n")
    assert edition._read_edition(tmp_path / "e") == "desktop"
    (tmp_path / "e").write_text("tv")
    with pytest.raises(RuntimeError):
        edition._read_edition(tmp_path / "e")
    assert edition.couch_available("couch") and not edition.couch_available("desktop")
    assert edition.EDITION == "couch" and not edition.EDITION_FILE.exists()  # source checkout


def test_desktop_manifest_is_generated_from_the_main_one() -> None:
    main, desktop = MAIN.read_text(), DESKTOP.read_text()
    body = "\n".join(line for line in desktop.splitlines() if not line.startswith("# GENERATED")
                     and not line.startswith("# io.github") and not line.startswith("# Desktop Edition of"))
    command = next(line for line in desktop.splitlines() if "edition.txt" in line)
    assert body.replace(command + "\n", "").replace("      # Desktop Edition: no Couch Mode or controller navigation.\n", "") == main.rstrip("\n")
    assert 'rm -rf "$PKG/qml/couch"' in command and "echo desktop" in command
    assert re.search(r"^app-id: io\.github\.DevVoidPL\.GameOptimizationLinux$", desktop, re.M)


def test_desktop_edition_controller_disables_couch(tmp_path: Path, monkeypatch) -> None:
    from game_optimization_linux.controllers.app_controller import AppController
    from game_optimization_linux.providers import DemoGameProvider, DemoSystemProvider
    from game_optimization_linux.services import MockTaskService, SettingsStore
    monkeypatch.setattr(edition, "couch_available", lambda edition_name=None: False)
    started = []

    class Pad:
        def __getattr__(self, name):
            if name == "start":
                return lambda: started.append(True)
            raise AttributeError(name)

    controller = AppController(game_provider=DemoGameProvider(()), task_service=MockTaskService(),
                               settings_store=SettingsStore(tmp_path / "settings.json"),
                               system_provider=DemoSystemProvider(), initial_games=(), demo_mode=True,
                               auto_refresh=False, initial_interface_mode="couch")
    try:
        assert controller.couchAvailable is False and controller.interfaceMode == "desktop"
        assert controller.setInterfaceMode("couch") is False and controller.toggleInterfaceMode() is False
        controller._on_gamepad_activity("Confirm")
        assert controller.interfaceMode == "desktop"
        assert controller._gamepad_service.available in (True, False)
    finally:
        controller.shutdown()


def test_qml_hides_couch_and_controller_settings_without_couch() -> None:
    qml = ROOT / "src/game_optimization_linux/qml"
    main = (qml / "Main.qml").read_text()
    settings = (qml / "pages/SettingsPage.qml").read_text()
    assert 'window.interfaceMode === "couch" && Boolean(window.controller && window.controller.couchAvailable)' in main
    assert 'objectName: "controllerSettingsCard"' in settings
    assert "visible: Boolean(page.controller && page.controller.couchAvailable)" in settings
