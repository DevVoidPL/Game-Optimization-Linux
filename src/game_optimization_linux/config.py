"""Application-wide metadata and filesystem locations.

Keeping the public name and version here makes rebranding independent from the
QML and controller layers.  Runtime paths follow the XDG base-directory
convention, with conservative fallbacks for environments that do not define
the XDG variables.
"""

from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Game Optimization Linux"
APP_VERSION = "1.6.0-alpha"
APP_ID = "io.github.DevVoidPL.GameOptimizationLinux"
ORGANIZATION_NAME = APP_NAME
ORGANIZATION_DOMAIN = "game-optimization-linux.local"

PACKAGE_DIR = Path(__file__).resolve().parent
QML_DIR = PACKAGE_DIR / "qml"
MAIN_QML = QML_DIR / "Main.qml"
RESOURCES_DIR = PACKAGE_DIR / "resources"
APP_ICON = RESOURCES_DIR / "GameOptimizationLinuxIcon.png"
APP_ICON_VARIANTS = {
    size: RESOURCES_DIR / "app-icons" / f"{size}x{size}.png"
    for size in (16, 22, 24, 32, 48, 64, 128, 256)
}
TRANSLATIONS_DIR = PACKAGE_DIR / "translations"
COMPRESSION_BENCHMARK_REPORTS_DIR = (
    PACKAGE_DIR.parents[1] / "reports" / "compression_benchmarks"
)

_APP_DIRECTORY_NAME = "game-optimization-linux"


def _xdg_path(environment_name: str, fallback: Path) -> Path:
    """Return an absolute XDG path without creating anything on disk."""

    configured_value = os.environ.get(environment_name, "").strip()
    if not configured_value:
        return fallback

    configured_path = Path(configured_value).expanduser()
    if configured_path.is_absolute():
        return configured_path

    # The XDG specification requires absolute paths.  Ignoring an invalid
    # relative value is safer and more predictable than resolving it against
    # an arbitrary launch directory.
    return fallback


CONFIG_HOME = _xdg_path("XDG_CONFIG_HOME", Path.home() / ".config")
STATE_HOME = _xdg_path("XDG_STATE_HOME", Path.home() / ".local" / "state")
CACHE_HOME = _xdg_path("XDG_CACHE_HOME", Path.home() / ".cache")
DATA_HOME = _xdg_path("XDG_DATA_HOME", Path.home() / ".local" / "share")

CONFIG_DIR = CONFIG_HOME / _APP_DIRECTORY_NAME
STATE_DIR = STATE_HOME / _APP_DIRECTORY_NAME
CACHE_DIR = CACHE_HOME / _APP_DIRECTORY_NAME
DATA_DIR = DATA_HOME / _APP_DIRECTORY_NAME
SETTINGS_FILE = CONFIG_DIR / "settings.json"
GAMEPAD_MAPPINGS_FILE = CONFIG_DIR / "gamecontrollerdb.txt"
LIBRARY_CACHE_FILE = CACHE_DIR / "library-v1.json"
LOCAL_EXECUTABLE_CHOICES_FILE = CONFIG_DIR / "local-executables-v1.json"
ANALYSIS_CACHE_FILE = CACHE_DIR / "compression-analysis-v1.json"
COMPRESSION_HISTORY_FILE = STATE_DIR / "compression-history-v2.json"
TASK_HISTORY_FILE = STATE_DIR / "task-history-v1.json"
UPDATE_STATE_FILE = STATE_DIR / "game-updates-v2.json"
UPDATE_DISPLAY_STATE_FILE = STATE_DIR / "update-display-v1.json"
LOG_DIR = STATE_DIR / "logs"
LOG_FILE = LOG_DIR / "game-optimization-linux.log"
GAMES_CONFIG_DIR = CONFIG_DIR / "games"
MANGOHUD_LOG_DIR = STATE_DIR / "mangohud-logs"
OPTISCALER_DATA_DIR = DATA_DIR / "games"
NARRATOR_COMPONENTS_DIR = DATA_DIR / "narrator" / "components"
NARRATOR_TRANSLATION_CACHE_FILE = (
    CACHE_DIR / "narrator" / "translation-cache-v1.sqlite3"
)
NARRATOR_CAPTURE_GRANTS_FILE = (
    CONFIG_DIR / "narrator" / "capture-grants-v1.json"
)


__all__ = [
    "APP_ID",
    "APP_ICON",
    "APP_ICON_VARIANTS",
    "APP_NAME",
    "APP_VERSION",
    "ANALYSIS_CACHE_FILE",
    "CONFIG_DIR",
    "CONFIG_HOME",
    "DATA_DIR",
    "DATA_HOME",
    "COMPRESSION_HISTORY_FILE",
    "COMPRESSION_BENCHMARK_REPORTS_DIR",
    "GAMEPAD_MAPPINGS_FILE",
    "GAMES_CONFIG_DIR",
    "CACHE_DIR",
    "CACHE_HOME",
    "LOG_DIR",
    "LOG_FILE",
    "MANGOHUD_LOG_DIR",
    "OPTISCALER_DATA_DIR",
    "NARRATOR_CAPTURE_GRANTS_FILE",
    "NARRATOR_COMPONENTS_DIR",
    "NARRATOR_TRANSLATION_CACHE_FILE",
    "LIBRARY_CACHE_FILE",
    "LOCAL_EXECUTABLE_CHOICES_FILE",
    "MAIN_QML",
    "ORGANIZATION_DOMAIN",
    "ORGANIZATION_NAME",
    "PACKAGE_DIR",
    "QML_DIR",
    "RESOURCES_DIR",
    "SETTINGS_FILE",
    "STATE_DIR",
    "STATE_HOME",
    "TRANSLATIONS_DIR",
    "TASK_HISTORY_FILE",
    "UPDATE_STATE_FILE",
    "UPDATE_DISPLAY_STATE_FILE",
]
