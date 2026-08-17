from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine

from game_optimization_linux.config import TRANSLATIONS_DIR
from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import SettingsStore
from game_optimization_linux.translations import TranslationManager


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


def test_translation_manager_loads_all_supported_languages() -> None:
    manager = TranslationManager(_APPLICATION)

    assert [entry["code"] for entry in manager.availableLanguages] == ["en", "pl", "es"]
    for code in ("en", "pl", "es"):
        assert (TRANSLATIONS_DIR / f"game_optimization_{code}.ts").is_file()
        assert (TRANSLATIONS_DIR / f"game_optimization_{code}.qm").is_file()
        assert manager.set_language(code) is True
        assert manager.currentLanguage == code


def test_basic_sidebar_text_exists_in_english_polish_and_spanish() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {"en": "Games", "pl": "Gry", "es": "Juegos"}

    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate("Sidebar", "Games") == translated


def test_narrator_text_exists_in_english_polish_and_spanish() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": ("Narrator", "Start narrator", "Waiting for portal permission or source"),
        "pl": ("Lektor", "Uruchom lektora", "Oczekiwanie na zgodę portalu lub wybór źródła"),
        "es": ("Narrador", "Iniciar narrador", "Esperando permiso del portal o selección de fuente"),
    }

    for code, (sidebar, start, capture_status) in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate("Sidebar", "Narrator") == sidebar
        assert QCoreApplication.translate("NarratorPage", "Start narrator") == start
        assert QCoreApplication.translate(
            "NarratorPage", "Waiting for portal permission or source"
        ) == capture_status


def test_core_interface_texts_are_translated_in_every_language() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": {
            ("Sidebar", "Tasks"): "Tasks",
            ("GamesPage", "Refresh"): "Refresh",
            ("GameGridCard", "Details"): "Details",
            ("GameDetailsPage", "Launch"): "Launch",
            ("OverviewTab", "Game information"): "Game information",
            ("StorageTab", "Btrfs compression analysis"): "Btrfs compression analysis",
            ("GraphicsTab", "Graphics Remaster"): "Graphics Remaster",
                ("OptimizationTab", "Preliminary settings"): "Preliminary settings",
            ("I18n", "GameMode is installed, but its service is unavailable"): "GameMode is installed, but its service is unavailable",
            ("SystemPage", "System"): "System",
            ("SettingsPage", "Settings"): "Settings",
            ("SettingsPage", "Fast"): "Fast",
            ("OptimizationTab", "Balanced"): "Balanced",
            ("GraphicsTab", "Classic Enhance"): "Classic Enhance",
            ("StorageTab", "Analyze compression"): "Analyze compression",
            ("I18n", "Demo mode · update checks disabled"): "Demo mode · update checks disabled",
            ("I18n", "Analysis queued for %1"): "Analysis queued for %1",
            ("I18n", "Drive disconnected"): "Drive disconnected",
            ("I18n", "Library unavailable"): "Library unavailable",
            ("Sidebar", "Updates"): "Updates",
            ("UpdatesPage", "Game updates"): "Game updates",
            ("SettingsPage", "Automatic Btrfs compression"): "Automatic Btrfs compression",
            ("StorageTab", "Btrfs shared-extent safety check"): "Btrfs shared-extent safety check",
            ("GameDetailsPage", "Current physical usage"): "Current physical usage",
            ("StorageTab", "Measurement status"): "Measurement status",
            ("StorageTab", "Estimated additional saving"): "Estimated additional saving",
            ("StorageTab", "Profitability"): "Profitability",
            ("Main", "Compression is still running"): "Compression is still running",
        },
        "pl": {
            ("Sidebar", "Tasks"): "Zadania",
            ("GamesPage", "Refresh"): "Odśwież",
            ("GameGridCard", "Details"): "Szczegóły",
            ("GameDetailsPage", "Launch"): "Uruchom",
            ("OverviewTab", "Game information"): "Informacje o grze",
            ("StorageTab", "Btrfs compression analysis"): "Analiza kompresji Btrfs",
            ("GraphicsTab", "Graphics Remaster"): "Remaster grafiki",
                ("OptimizationTab", "Preliminary settings"): "Ustawienia wstępne",
            ("I18n", "GameMode is installed, but its service is unavailable"): "GameMode jest zainstalowany, ale jego usługa jest niedostępna",
            ("SystemPage", "System"): "System",
            ("SettingsPage", "Settings"): "Ustawienia",
            ("SettingsPage", "Fast"): "Szybki",
            ("OptimizationTab", "Balanced"): "Zrównoważony",
            ("GraphicsTab", "Classic Enhance"): "Klasyczne ulepszenie",
            ("StorageTab", "Analyze compression"): "Analizuj kompresję",
            ("I18n", "Demo mode · update checks disabled"): "Tryb demonstracyjny · sprawdzanie aktualizacji wyłączone",
            ("I18n", "Analysis queued for %1"): "Dodano analizę gry %1 do kolejki",
            ("I18n", "Drive disconnected"): "Dysk odłączony",
            ("I18n", "Library unavailable"): "Biblioteka niedostępna",
            ("Sidebar", "Updates"): "Aktualizacje",
            ("UpdatesPage", "Game updates"): "Aktualizacje gier",
            ("SettingsPage", "Automatic Btrfs compression"): "Automatyczna kompresja Btrfs",
            ("StorageTab", "Btrfs shared-extent safety check"): "Kontrola bezpieczeństwa współdzielonych extentów Btrfs",
            ("GameDetailsPage", "Current physical usage"): "Aktualne użycie fizyczne",
            ("StorageTab", "Measurement status"): "Status pomiaru",
            ("StorageTab", "Estimated additional saving"): "Szacowany dodatkowy zysk",
            ("StorageTab", "Profitability"): "Opłacalność",
            ("Main", "Compression is still running"): "Kompresja nadal trwa",
        },
        "es": {
            ("Sidebar", "Tasks"): "Tareas",
            ("GamesPage", "Refresh"): "Actualizar",
            ("GameGridCard", "Details"): "Detalles",
            ("GameDetailsPage", "Launch"): "Iniciar",
            ("OverviewTab", "Game information"): "Información del juego",
            ("StorageTab", "Btrfs compression analysis"): "Análisis de compresión Btrfs",
            ("GraphicsTab", "Graphics Remaster"): "Remasterización gráfica",
                ("OptimizationTab", "Preliminary settings"): "Ajustes preliminares",
            ("I18n", "GameMode is installed, but its service is unavailable"): "GameMode está instalado, pero su servicio no está disponible",
            ("SystemPage", "System"): "Sistema",
            ("SettingsPage", "Settings"): "Ajustes",
            ("SettingsPage", "Fast"): "Rápido",
            ("OptimizationTab", "Balanced"): "Equilibrado",
            ("GraphicsTab", "Classic Enhance"): "Mejora clásica",
            ("StorageTab", "Analyze compression"): "Analizar compresión",
            ("I18n", "Demo mode · update checks disabled"): "Modo de demostración · comprobación de actualizaciones desactivada",
            ("I18n", "Analysis queued for %1"): "Se puso en cola el análisis de %1",
            ("I18n", "Drive disconnected"): "Unidad desconectada",
            ("I18n", "Library unavailable"): "Biblioteca no disponible",
            ("Sidebar", "Updates"): "Actualizaciones",
            ("UpdatesPage", "Game updates"): "Actualizaciones de juegos",
            ("SettingsPage", "Automatic Btrfs compression"): "Compresión Btrfs automática",
            ("StorageTab", "Btrfs shared-extent safety check"): "Comprobación de seguridad de extents compartidos de Btrfs",
            ("GameDetailsPage", "Current physical usage"): "Uso físico actual",
            ("StorageTab", "Measurement status"): "Estado de la medición",
            ("StorageTab", "Estimated additional saving"): "Ahorro adicional estimado",
            ("StorageTab", "Profitability"): "Rentabilidad",
            ("Main", "Compression is still running"): "La compresión sigue en curso",
        },
    }

    for code, translations in expected.items():
        assert manager.set_language(code)
        for (context, source), translated in translations.items():
            assert QCoreApplication.translate(context, source) == translated


def test_catalogs_have_no_empty_or_unfinished_messages() -> None:
    for code in ("en", "pl", "es"):
        root = ET.parse(TRANSLATIONS_DIR / f"game_optimization_{code}.ts").getroot()
        messages = root.findall(".//message")

        assert len(messages) >= 400
        for message in messages:
            source = message.findtext("source", "")
            translation = message.find("translation")
            assert translation is not None, source
            assert translation.get("type") != "unfinished", source
            translated = translation.text or ""
            assert translated.strip(), source
            assert sorted(re.findall(r"%\d+", translated)) == sorted(
                re.findall(r"%\d+", source)
            ), source


def test_polish_optimization_text_survives_runtime_translation() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "Current value": "Bieżąca wartość",
        "Manual test value": "Wartość testu ręcznego",
        "Setting change applied and verified": (
            "Zmiana ustawienia została zastosowana i zweryfikowana"
        ),
        "Samples used": "Użyte próbki",
        "Measurement quality": "Jakość pomiaru",
    }

    assert manager.set_language("pl")
    for source, translated in expected.items():
        assert QCoreApplication.translate("OptimizationTab", source) == translated


def test_automatic_optimization_texts_are_translated_in_every_language() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": (
            "Automatic Optimization",
            "Apply optimization",
            "Launch and record comparison",
        ),
        "pl": (
            "Automatyczna optymalizacja",
            "Zastosuj optymalizację",
            "Uruchom i zarejestruj porównanie",
        ),
        "es": (
            "Optimización automática",
            "Aplicar optimización",
            "Iniciar y registrar comparación",
        ),
    }

    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate(
            "OptimizationTab", "Automatic Optimization"
        ) == translated[0]
        assert QCoreApplication.translate(
            "OptimizationTab", "Apply optimization"
        ) == translated[1]
        assert QCoreApplication.translate(
            "OptimizationTab", "Launch and record comparison"
        ) == translated[2]


def test_qml_preserves_complete_polish_unicode_alphabet() -> None:
    expected = "ą ć ę ł ń ó ś ź ż Ą Ć Ę Ł Ń Ó Ś Ź Ż"
    manager = TranslationManager(_APPLICATION)
    assert manager.set_language("pl")
    engine = QQmlEngine()
    manager.attach_engine(engine)
    component = QQmlComponent(engine)
    component.setData(
        (
            'pragma Translator: "OptimizationTab"\n'
            'import QtQml\n'
            'QtObject { '
            'property string translated: qsTr("Current value"); '
            f'property string alphabet: "{expected}" '
            '}'
        ).encode(),
        QUrl(),
    )
    instance = component.create()
    try:
        assert instance is not None, component.errorString()
        assert instance.property("translated") == "Bieżąca wartość"
        assert instance.property("alphabet") == expected
    finally:
        if instance is not None:
            instance.deleteLater()
        engine.deleteLater()


def test_translation_catalogs_contain_no_mojibake() -> None:
    mojibake_markers = ("Ã", "Å", "Ä", "Â", "â€")
    for code in ("en", "pl", "es"):
        path = TRANSLATIONS_DIR / f"game_optimization_{code}.ts"
        text = path.read_text(encoding="utf-8")
        assert not any(marker in text for marker in mojibake_markers), path


def test_language_aliases_and_unknown_language() -> None:
    manager = TranslationManager(_APPLICATION)

    assert manager.set_language("Polski")
    assert manager.currentLanguage == "pl"
    assert manager.set_language("Español")
    assert manager.currentLanguage == "es"
    assert manager.set_language("unsupported") is False
    assert manager.currentLanguage == "es"


def test_compression_summary_and_classifications_are_translated() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": (
            "Library storage status",
            "Strongly compressed",
            "Operation blocked by shared extents or snapshots",
        ),
        "pl": (
            "Stan pamięci bibliotek",
            "Mocno skompresowana",
            "Operacja zablokowana przez współdzielone extenty lub snapshoty",
        ),
        "es": (
            "Estado de almacenamiento de las bibliotecas",
            "Muy comprimido",
            "Operación bloqueada por extents compartidos o instantáneas",
        ),
    }

    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate(
            "GamesPage", "Library storage status"
        ) == translated[0]
        assert QCoreApplication.translate(
            "I18n", "Strongly compressed"
        ) == translated[1]
        assert QCoreApplication.translate(
            "I18n", "Operation blocked by shared extents or snapshots"
        ) == translated[2]

    assert manager.set_language("pl")
    assert QCoreApplication.translate(
        "GamesPage", "Measured games total"
    ) == "Suma zmierzonych gier"
    assert QCoreApplication.translate(
        "GamesPage", "Partial measurement"
    ) == "Pomiar częściowy"


def test_mangohud_editor_texts_are_translated() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": ("Presets", "GPU temperature", "Save profile", "MangoHud detected", "Main executable", "Application profile - changes apply on the next game launch"),
        "pl": ("Presety", "Temperatura GPU", "Zapisz profil", "Wykryto MangoHud", "Główny plik wykonywalny", "Profil aplikacji - zmiany zadziałają przy następnym uruchomieniu gry"),
        "es": ("Preajustes", "Temperatura de la GPU", "Guardar perfil", "MangoHud detectado", "Ejecutable principal", "Perfil de aplicación - los cambios se aplican al iniciar el juego de nuevo"),
    }

    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate("MangoHudTab", "Presets") == translated[0]
        assert QCoreApplication.translate(
            "MangoHudTab", "GPU temperature"
        ) == translated[1]
        assert QCoreApplication.translate(
            "CouchGameDetails", "Save profile"
        ) == translated[2]
        assert QCoreApplication.translate(
            "I18n", "MangoHud detected"
        ) == translated[3]
        assert QCoreApplication.translate(
            "MangoHudTab", "Main executable"
        ) == translated[4]
        assert QCoreApplication.translate(
            "MangoHudTab", "Application profile - changes apply on the next game launch"
        ) == translated[5]


def test_optimization_profile_editor_texts_are_translated() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": ("Automatic", "Game category", "Steam connection", "Test runner"),
        "pl": ("Automatyczny", "Typ gry", "Połączenie ze Steam", "Testuj runner"),
        "es": ("Automático", "Categoría del juego", "Conexión con Steam", "Probar runner"),
    }
    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate("OptimizationTab", "Automatic") == translated[0]
        assert QCoreApplication.translate("OptimizationTab", "Game category") == translated[1]
        assert QCoreApplication.translate("OptimizationTab", "Steam connection") == translated[2]
        assert QCoreApplication.translate("OptimizationTab", "Test runner") == translated[3]


def test_optiscaler_texts_are_translated() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": ("Image scaling", "Install OptiScaler", "Restore previous files", "Choose an OptiScaler archive"),
        "pl": ("Skalowanie obrazu", "Zainstaluj OptiScaler", "Przywróć poprzednie pliki", "Wybierz archiwum OptiScalera"),
        "es": ("Escalado de imagen", "Instalar OptiScaler", "Restaurar archivos anteriores", "Elige un archivo de OptiScaler"),
    }

    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate(
            "OptiScalerSection", "Image scaling"
        ) == translated[0]
        assert QCoreApplication.translate(
            "OptiScalerSection", "Install OptiScaler"
        ) == translated[1]
        assert QCoreApplication.translate(
            "OptiScalerSection", "Restore previous files"
        ) == translated[2]
        assert QCoreApplication.translate(
            "OptiScalerSection", "Choose an OptiScaler archive"
        ) == translated[3]


def test_online_optiscaler_and_proton_tweaks_are_translated() -> None:
    manager = TranslationManager(_APPLICATION)
    expected = {
        "en": ("Check online", "Proton Tweaks", "Compatibility"),
        "pl": ("Sprawdź online", "Modyfikacje Protona", "Zgodność"),
        "es": ("Comprobar en línea", "Ajustes de Proton", "Compatibilidad"),
    }
    for code, translated in expected.items():
        assert manager.set_language(code)
        assert QCoreApplication.translate(
            "OptiScalerSection", "Check online"
        ) == translated[0]
        assert QCoreApplication.translate(
            "ProtonTweaksSection", "Proton Tweaks"
        ) == translated[1]
        assert QCoreApplication.translate(
            "ProtonTweaksSection", "Compatibility"
        ) == translated[2]


def test_interface_sources_and_finished_translations_use_plain_hyphens() -> None:
    checked = [
        *Path("src/game_optimization_linux").rglob("*.qml"),
        *Path("src/game_optimization_linux").rglob("*.py"),
        *Path("src/game_optimization_linux/translations").glob("*.ts"),
        *Path("data").glob("*.xml"),
    ]
    testing_guide = Path("TESTING.md")
    if testing_guide.exists():
        checked.append(testing_guide)
    for path in checked:
        text = path.read_text(encoding="utf-8")
        assert "\N{EN DASH}" not in text, path
        assert "\N{EM DASH}" not in text, path


def test_selected_language_is_saved_and_restored(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    first = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(settings_path),
    )
    try:
        assert first.saveSetting("language", "pl")
    finally:
        first.shutdown()

    restored = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(settings_path),
    )
    try:
        assert restored.settings["language"] == "pl"
        manager = TranslationManager(_APPLICATION)
        assert manager.set_language(restored.settings["language"])
        assert QCoreApplication.translate(
            "OptimizationTab", "Current value"
        ) == "Bieżąca wartość"
    finally:
        restored.shutdown()
