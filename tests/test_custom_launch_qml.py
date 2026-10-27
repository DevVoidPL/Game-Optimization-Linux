"""CustomLaunchSection interaction on the real controller with temporary data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402

from qml_runtime_probe import _item, _settle, _view  # noqa: E402
from test_custom_launch import _heroic_controller, _shutdown_controllers  # noqa: E402,F401


def _eval(root, expression):
    result = QQmlExpression(QQmlEngine.contextForObject(root), root, expression).evaluate()
    return result[0] if isinstance(result, tuple) else result


@pytest.fixture
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def _section(app, controller, game, *, couch=False, width=900):
    view, root = _view(app, "pages/details/CustomLaunchSection.qml", width, 1600, initial_properties={
        "controller": controller, "gameData": {"id": game.id, "launcher": game.launcher.value}, "couchMode": couch})
    _settle(app, 6)
    return view, root


def test_desktop_draft_reset_refresh_and_confirmed_apply(app, tmp_path, monkeypatch) -> None:
    from test_heroic_optiscaler import APP_NAME
    game, controller, path = _heroic_controller(tmp_path, monkeypatch)
    original = path.read_bytes()
    view, root = _section(app, controller, game)
    try:
        assert _eval(root, "section.dirty") is False
        _eval(root, 'section.change(function(d) { d.environment.push({"key": "CL_A", "value": "a b", "enabled": true}); '
                    'd.arguments.push({"value": "-dx12", "enabled": true}) })')
        _eval(root, "section.preview()")
        assert _eval(root, "section.dirty") is True
        assert _eval(root, "section.resolved.environment[0].state") == "applied"
        # A status refresh keeps the unsaved draft.
        _eval(root, "section.load(false)")
        assert _eval(root, "section.draft.environment.length") == 1
        # Reset touches only the draft; nothing is saved.
        _eval(root, "section.resetDraft()")
        assert _eval(root, "section.draft.environment.length") == 0 and _eval(root, "section.dirty") is False
        assert not controller._custom_launch_controller.repository.path(game.id).exists()
        _eval(root, 'section.change(function(d) { d.arguments.push({"value": "-dx12", "enabled": true}) })')
        _eval(root, "section.apply(false)")
        assert _eval(root, "section.confirmOpen") is True and path.read_bytes() == original
        _eval(root, "section.apply(true)")
        assert _eval(root, "section.dirty") is False, _eval(root, "section.errorMessage")
        assert json.loads(path.read_text())[APP_NAME]["launcherArgs"] == "-dx12"
        assert _item(root, "customLaunchSummary").property("text").startswith("Arguments: 1")
    finally:
        view.close(); view.deleteLater(); _settle(app, 4)


def test_invalid_field_shows_error_and_blocks_apply(app, tmp_path, monkeypatch) -> None:
    game, controller, _path = _heroic_controller(tmp_path, monkeypatch)
    view, root = _section(app, controller, game)
    try:
        _eval(root, 'section.change(function(d) { d.environment.push({"key": "1BAD", "value": "", "enabled": true}) })')
        _eval(root, "section.preview()")
        assert "cannot start with a digit" in _eval(root, 'section.errorFor("environment", 0)')
        assert _item(root, "customLaunchApply").property("enabled") is False
    finally:
        view.close(); view.deleteLater(); _settle(app, 4)


def test_couch_dpad_adds_rows_and_uses_the_on_screen_keyboard(app, tmp_path, monkeypatch) -> None:
    game, controller, _path = _heroic_controller(tmp_path, monkeypatch)
    view, root = _section(app, controller, game, couch=True, width=1100)
    try:
        _eval(root, "section.textInputRequested.connect(function(field, heading) { section.statusMessage = 'REQ:' + heading })")
        assert _eval(root, 'section.handlePadAction("Focus")') is True
        for _ in range(60):
            if _eval(root, "section.padFocusedItem ? section.padFocusedItem.objectName : ''") == "customAddEnv":
                break
            _eval(root, 'section.handlePadAction("NavigateDown")')
        assert _eval(root, "section.padFocusedItem.objectName") == "customAddEnv"
        assert _eval(root, "section.padFocusedItem.activeFocus") is True
        assert _eval(root, "section.padFocusedItem.visualFocus") is True  # AppButton draws its focus border
        _eval(root, 'section.handlePadAction("Confirm")')
        _settle(app, 6)
        assert _eval(root, "section.draft.environment.length") == 1
        _eval(root, 'section.handlePadAction("NavigateUp"); section.handlePadAction("NavigateUp"); section.handlePadAction("NavigateUp"); section.handlePadAction("NavigateUp")')
        assert _eval(root, "section.padFocusedItem.padAction") == "toggle"
        ring = _eval(root, "(function(){ var c = section.padFocusedItem.children; for (var i = 0; i < c.length; i++) if (c[i].border && c[i].border.width === 2) return c[i].visible; return false })()")
        assert ring is True  # switches get a focus ring
        _eval(root, 'section.handlePadAction("NavigateDown")')
        for _ in range(60):
            if _eval(root, "section.padFocusedItem ? section.padFocusedItem.objectName : ''") == "customEnvKey-0":
                break
            _eval(root, 'section.handlePadAction("NavigateUp")')
        assert _eval(root, "section.padFocusedItem.objectName") == "customEnvKey-0"
        _eval(root, 'section.handlePadAction("Confirm")')
        assert _eval(root, "section.statusMessage") == "REQ:Variable name"
        _eval(root, 'section.commitPadText(section.padFocusedItem, "CL_PAD")')
        assert _eval(root, "section.draft.environment[0].key") == "CL_PAD"
        _eval(root, 'section.apply(false)')
        assert _eval(root, "section.confirmOpen") is True
        assert _eval(root, 'section.handlePadAction("Back")') is True  # Back closes the confirmation first
        assert _eval(root, "section.confirmOpen") is False
        assert _eval(root, 'section.handlePadAction("Back")') is False  # then leaves the editor
    finally:
        view.close(); view.deleteLater(); _settle(app, 4)


def test_switching_games_loads_the_other_isolated_profile(app, tmp_path, monkeypatch) -> None:
    from dataclasses import replace
    from game_optimization_linux.services.custom_launch import CustomLaunchProfile
    game, controller, _path = _heroic_controller(tmp_path, monkeypatch)
    repo = controller._custom_launch_controller.repository
    repo.save(CustomLaunchProfile.from_dict(game.id, {"arguments": [{"value": "-first"}]}))
    other = replace(game, id="heroic-legendary-other1", launcher_game_id="other1")
    controller._domain_games[other.id] = other
    repo.save(CustomLaunchProfile.from_dict(other.id, {"arguments": [{"value": "-second"}]}))
    view, root = _section(app, controller, game)
    try:
        assert _eval(root, "section.draft.arguments[0].value") == "-first"
        _eval(root, 'section.change(function(d) { d.arguments[0].value = "-edited" })')
        root.setProperty("gameData", {"id": other.id, "launcher": "Heroic"})
        _settle(app, 6)
        assert _eval(root, "section.draft.arguments[0].value") == "-second" and _eval(root, "section.dirty") is False
        assert repo.load(game.id).arguments[0].value == "-first"  # the unsaved edit was never saved
    finally:
        view.close(); view.deleteLater(); _settle(app, 4)


@pytest.mark.parametrize("language, expected", [("pl", "Brak własnych ustawień uruchamiania"),
                                                ("es", "Sin configuración de inicio personalizada"),
                                                ("en", "No custom launch settings")])
def test_editor_is_translated(app, tmp_path, monkeypatch, language, expected) -> None:
    from game_optimization_linux.translations import TranslationManager
    game, controller, _path = _heroic_controller(tmp_path, monkeypatch)
    translator = TranslationManager(app)
    translator.set_language(language)
    try:
        view, root = _section(app, controller, game)
        try:
            assert _item(root, "customLaunchSummary").property("text") == expected
        finally:
            view.close(); view.deleteLater(); _settle(app, 4)
    finally:
        translator.set_language("en")


def test_couch_menu_opens_the_full_editor() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/game_optimization_linux/qml/couch/CouchGameDetails.qml").read_text()
    assert 'qsTr("Custom launch")' in source and "Details.CustomLaunchSection" in source
    assert "customLaunchKeyboard.open(" in source and "optimizationRow === 7" in source
