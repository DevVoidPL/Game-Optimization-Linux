"""Runtime Qt translation management for the active QML interface."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QCoreApplication, QLocale, QObject, QTranslator, Signal, Slot

from .config import TRANSLATIONS_DIR


logger = logging.getLogger(__name__)


_LANGUAGES: tuple[dict[str, str], ...] = (
    {"code": "en", "name": "English", "locale": "en_US"},
    {"code": "pl", "name": "Polski", "locale": "pl_PL"},
    {"code": "es", "name": "Español", "locale": "es_ES"},
)


class TranslationManager(QObject):
    """Install one application translator and ask QML to retranslate in place."""

    languageChanged = Signal()
    translationError = Signal(str)

    def __init__(
        self,
        application: QCoreApplication,
        translations_dir: Path = TRANSLATIONS_DIR,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        self._translations_dir = Path(translations_dir)
        self._translator: QTranslator | None = None
        self._engine: Any = None
        self._language = "en"

    @staticmethod
    def normalize(language: str) -> str | None:
        """Return a supported two-letter code for saved names/locales/codes."""

        normalized = str(language).strip().casefold().replace("-", "_")
        aliases = {
            "en": "en",
            "en_us": "en",
            "en_gb": "en",
            "english": "en",
            "pl": "pl",
            "pl_pl": "pl",
            "polski": "pl",
            "polish": "pl",
            "es": "es",
            "es_es": "es",
            "español": "es",
            "espanol": "es",
            "spanish": "es",
        }
        return aliases.get(normalized)

    @Property(str, notify=languageChanged)
    def currentLanguage(self) -> str:
        return self._language

    @Property("QVariantList", constant=True)
    def availableLanguages(self) -> list[dict[str, str]]:
        return [dict(language) for language in _LANGUAGES]

    @property
    def translations_dir(self) -> Path:
        return self._translations_dir

    def attach_engine(self, engine: Any) -> None:
        """Attach the QML engine whose existing objects should be retranslated."""

        self._engine = engine

    @Slot(str, result=bool)
    def setLanguage(self, language: str) -> bool:
        return self.set_language(language)

    def set_language(self, language: str) -> bool:
        code = self.normalize(language)
        if code is None:
            message = f"Unsupported interface language: {language}"
            logger.warning(message)
            self.translationError.emit(message)
            return False

        candidate = QTranslator(self)
        catalog = self._translations_dir / f"game_optimization_{code}.qm"
        if not catalog.is_file() or not candidate.load(str(catalog)):
            message = f"Translation catalog could not be loaded: {catalog.name}"
            logger.error(message)
            self.translationError.emit(message)
            return False

        previous = self._translator
        if previous is not None:
            self._application.removeTranslator(previous)
        if not self._application.installTranslator(candidate):
            if previous is not None:
                self._application.installTranslator(previous)
            message = f"Translation catalog could not be installed: {catalog.name}"
            logger.error(message)
            self.translationError.emit(message)
            return False

        self._translator = candidate
        changed = code != self._language
        self._language = code
        locale_name = next(
            item["locale"] for item in _LANGUAGES if item["code"] == code
        )
        QLocale.setDefault(QLocale(locale_name))

        retranslate = getattr(self._engine, "retranslate", None)
        if callable(retranslate):
            retranslate()
        if changed:
            self.languageChanged.emit()
        logger.info("Installed interface language %s from %s", code, catalog)
        return True


__all__ = ["TranslationManager"]
