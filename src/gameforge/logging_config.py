"""Logging configuration shared by the command-line entry point and GUI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_FILE


_HANDLER_MARKER = "_gameforge_managed_handler"
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_log_level(level: int | str) -> int:
    """Normalize a standard logging level, falling back to ``INFO``."""

    if isinstance(level, int):
        return level

    normalized = level.strip().upper()
    parsed = logging.getLevelNamesMapping().get(normalized)
    return parsed if isinstance(parsed, int) else logging.INFO


def configure_logging(
    level: int | str = logging.INFO,
    *,
    log_file: Path | None = LOG_FILE,
) -> Path | None:
    """Configure console logging and an optional rotating UTF-8 log file.

    The function is idempotent.  Failure to create a file handler never keeps
    the UI from starting; the full exception is recorded by the console
    handler and ``None`` is returned.
    """

    numeric_level = parse_log_level(level)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    for handler in tuple(root_logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARKER, True)
    root_logger.addHandler(console_handler)

    if log_file is None:
        return None

    resolved_log_file = Path(log_file).expanduser()
    try:
        resolved_log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            resolved_log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARKER, True)
        root_logger.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).exception(
            "Could not initialize rotating log file at %s; continuing with "
            "console logging only",
            resolved_log_file,
        )
        return None

    return resolved_log_file


__all__ = ["configure_logging", "parse_log_level"]
