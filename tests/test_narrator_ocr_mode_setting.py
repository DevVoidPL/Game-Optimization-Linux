"""Narrator "OCR mode" setting (settings.json): automatic / classic."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from game_optimization_linux.controllers.app_controller import AppController
from game_optimization_linux.controllers.presenters import settings_to_qml
from game_optimization_linux.controllers.settings_controller import SettingsController
from game_optimization_linux.models import AppSettings
from game_optimization_linux.services import SettingsStore


def test_ocr_mode_defaults_to_automatic_and_round_trips(tmp_path: Path) -> None:
    assert AppSettings().narrator_ocr_mode == "automatic"
    store = SettingsStore(tmp_path / "settings.json")
    for mode in ("classic", "automatic"):
        store.save(AppSettings(narrator_ocr_mode=mode))
        assert store.load().narrator_ocr_mode == mode
    # Older files and unknown values fall back to automatic.
    assert AppSettings.from_dict({}).narrator_ocr_mode == "automatic"
    assert AppSettings.from_dict({"narrator_ocr_mode": "fancy"}).narrator_ocr_mode == "automatic"
    assert settings_to_qml(AppSettings(narrator_ocr_mode="classic"))["narratorOcrMode"] == "classic"


class _Signal:
    def emit(self) -> None:
        pass


class _FakeApp:
    """Only what SettingsController.saveSetting touches for this field."""

    def __init__(self, store: SettingsStore) -> None:
        self._settings_store = store
        self._settings_model = AppSettings()
        self._settings = settings_to_qml(self._settings_model)
        self._theme_mode = "system"
        self.settingsChanged = _Signal()
        self.themeModeChanged = _Signal()
        self.applied: list[str] = []
        self.toasts: list[tuple[str, str]] = []
        self.controller = SettingsController(self)

    def _setting_field_name(self, key: str):
        return self.controller._setting_field_name(key)

    def _coerce_setting_value(self, current, value, *, field_name=""):
        return self.controller._coerce_setting_value(current, value, field_name=field_name)

    def _extract_theme_mode(self, _settings) -> str:
        return self._theme_mode

    def _apply_narrator_ocr_mode(self, mode: str) -> None:
        self.applied.append(mode)

    def _report_error(self, context: str, error: Exception) -> None:
        self.toasts.append((context, str(error)))

    def _emit_toast(self, message: str, level: str) -> None:
        self.toasts.append((message, level))


def test_saving_the_setting_persists_and_applies_it(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    app = _FakeApp(store)
    assert app.controller.saveSetting("narratorOcrMode", "classic") is True
    assert store.load().narrator_ocr_mode == "classic"
    assert app.applied == ["classic"]
    # An invalid value is refused and nothing is applied.
    assert app.controller.saveSetting("narratorOcrMode", "turbo") is False
    assert store.load().narrator_ocr_mode == "classic"
    assert app.applied == ["classic"]


def test_app_passes_the_setting_to_the_ocr_provider() -> None:
    calls: list[str] = []
    fake = SimpleNamespace(
        _narrator_pipeline=SimpleNamespace(ocr=SimpleNamespace(set_settings_mode=calls.append))
    )
    AppController._apply_narrator_ocr_mode(fake, "classic")  # type: ignore[arg-type]
    assert calls == ["classic"]
    # Providers without the method (test doubles) are tolerated.
    AppController._apply_narrator_ocr_mode(
        SimpleNamespace(_narrator_pipeline=SimpleNamespace(ocr=object())), "automatic"  # type: ignore[arg-type]
    )
