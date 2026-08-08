"""Shared enum values used by the domain models and the QML bridge."""

from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    """A JSON- and Qt-friendly string enum with a useful string form."""

    def __str__(self) -> str:
        return self.value


class Launcher(StringEnum):
    STEAM = "Steam"
    HEROIC = "Heroic"
    MANUAL = "Manual"


class FilesystemType(StringEnum):
    BTRFS = "Btrfs"
    EXT4 = "ext4"
    XFS = "XFS"
    NTFS = "NTFS"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class GameStatus(StringEnum):
    READY = "Ready"
    RUNNING = "Running"
    NEEDS_ATTENTION = "Needs attention"
    MISSING_FILES = "Missing files"
    DRIVE_DISCONNECTED = "Drive disconnected"


class SizeScanStatus(StringEnum):
    NOT_REQUESTED = "not requested"
    CALCULATING = "calculating"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(StringEnum):
    """Every state exposed by the task queue."""

    QUEUED = "queued"
    ANALYZING = "analyzing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TaskType(StringEnum):
    ANALYSIS = "Analysis"
    VERIFICATION = "Verification"
    COMPRESSION = "Compression"
    OPTIMIZATION = "Optimization"
    TEXTURE_ENHANCEMENT = "Texture enhancement"
    BACKUP = "Backup"
    RESTORE = "Restore"


class CompressionProfile(StringEnum):
    FAST = "Fast"
    BALANCED = "Balanced"
    MAXIMUM = "Maximum"
    AUTO = "Auto"


class AutomaticCompressionMode(StringEnum):
    """When Game Optimization may enqueue compression while the application is open."""

    OFF = "Off"
    AFTER_INSTALLATION = "After new game installation"
    AFTER_UPDATE = "After game update"
    AFTER_INSTALLATION_AND_UPDATE = "After installation and update"

    @property
    def allows_installation(self) -> bool:
        return self in {
            AutomaticCompressionMode.AFTER_INSTALLATION,
            AutomaticCompressionMode.AFTER_INSTALLATION_AND_UPDATE,
        }

    @property
    def allows_update(self) -> bool:
        return self in {
            AutomaticCompressionMode.AFTER_UPDATE,
            AutomaticCompressionMode.AFTER_INSTALLATION_AND_UPDATE,
        }


class OptimizationProfile(StringEnum):
    MAXIMUM_PERFORMANCE = "Maximum Performance"
    BALANCED = "Balanced"
    QUIET = "Quiet"
    CUSTOM = "Custom"


class TextureMode(StringEnum):
    CLASSIC_ENHANCE = "Classic Enhance"
    AI_LITE = "AI Lite"
    AI_QUALITY = "AI Quality"


class TextureScope(StringEnum):
    LOW_QUALITY_ONLY = "Low-quality textures only"
    WORLD = "World textures"
    CHARACTERS = "Characters"
    INTERFACE = "Interface"
    ALL_SAFE = "All safe textures"


class TextureCompatibility(StringEnum):
    FULLY_SUPPORTED = "Fully supported"
    PARTIAL_SUPPORT = "Partial support"
    UNSUPPORTED = "Unsupported"
    NOT_CHECKED = "Not checked"


class BackupStatus(StringEnum):
    AVAILABLE = "Available"
    RESTORED = "Restored"
    FAILED = "Failed"
    NOT_DETECTED = "Not detected"


class CapabilityStatus(StringEnum):
    AVAILABLE = "Available"
    MISSING = "Missing"
    NOT_DETECTED = "Not detected"
    NOT_INSTALLED = "Not installed"
    OPTIONAL = "Optional"
    GAME_DEPENDENT = "Game-dependent"
    UNSUPPORTED = "Unsupported"
    NOT_CHECKED = "Not checked"


class ThemeMode(StringEnum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


class ControllerMode(StringEnum):
    AUTOMATIC = "Automatic"
    DESKTOP_ONLY = "Desktop only"
    COUCH_ONLY = "Couch only"


class PostLaunchBehavior(StringEnum):
    MINIMIZE = "Minimize"
    STAY_OPEN = "Stay open"
    CLOSE = "Close launcher"


class LogLevel(StringEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class SessionType(StringEnum):
    WAYLAND = "Wayland"
    X11 = "X11"
    UNKNOWN = "Unknown"
