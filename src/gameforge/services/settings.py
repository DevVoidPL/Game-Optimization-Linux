"""Small JSON settings store with validated and atomic persistence."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable
from uuid import uuid4

from gameforge.models.settings import AppSettings


class SettingsStoreError(RuntimeError):
    """Wrap malformed input and filesystem errors with a UI-safe message."""


class SettingsStore:
    """Load and atomically save :class:`AppSettings` at an explicit path.

    This is the only backend component in demo mode that writes to disk, and it
    writes only to the settings path supplied by the application.
    """

    def __init__(
        self,
        path: Path,
        default_factory: Callable[[], AppSettings] = AppSettings,
        logger: logging.Logger | None = None,
    ) -> None:
        self._path = Path(path)
        self._default_factory = default_factory
        self._logger = logger or logging.getLogger(__name__)
        self.last_error: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppSettings:
        try:
            if not self._path.exists():
                self.last_error = None
                return self._default_factory()
            raw_data = json.loads(self._path.read_text(encoding="utf-8"))
            settings = AppSettings.from_dict(raw_data)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            self.last_error = str(error)
            raise SettingsStoreError(
                f"could not load settings from {self._path}: {error}"
            ) from error
        self.last_error = None
        return settings

    def load_or_default(self) -> AppSettings:
        """Recover from invalid local data while retaining an error for the UI."""

        try:
            return self.load()
        except SettingsStoreError as error:
            self._logger.warning("Using default settings: %s", error)
            # ``load`` has already populated ``last_error`` for a toast/log view.
            return self._default_factory()

    def save(self, settings: AppSettings) -> None:
        try:
            payload = json.dumps(
                settings.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
            )
        except (TypeError, ValueError) as error:
            self.last_error = str(error)
            raise SettingsStoreError(f"could not serialize settings: {error}") from error

        temporary_path: Path | None = None
        try:
            temporary_path = self._path.with_name(
                f".{self._path.name}.{uuid4().hex}.tmp"
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self._path)
        except (OSError, ValueError) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    self._logger.debug(
                        "Could not remove temporary settings file %s",
                        temporary_path,
                        exc_info=True,
                    )
            self.last_error = str(error)
            raise SettingsStoreError(
                f"could not save settings to {self._path}: {error}"
            ) from error
        self.last_error = None
