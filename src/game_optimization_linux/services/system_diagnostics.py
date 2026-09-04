"""Public, privacy-conscious application and system diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from configparser import ConfigParser, Error as ConfigParserError
import os
from pathlib import Path
import platform
import re
from typing import Any

from ..config import APP_ID, APP_NAME, APP_VERSION


_UNKNOWN_VALUES = frozenset(
    {
        "",
        "-",
        "none",
        "not checked",
        "unavailable",
        "unknown",
    }
)
_APP_COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_MAX_FLATPAK_INFO_BYTES = 128 * 1024
_GAMING_TOOLS = (
    ("Steam", "steam"),
    ("GameMode", "gameMode"),
    ("Gamescope", "gamescope"),
    ("MangoHud", "mangoHud"),
)


def _known_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in _UNKNOWN_VALUES else text


def read_flatpak_app_commit(path: Path = Path("/.flatpak-info")) -> str:
    """Read a validated OSTree application commit without invoking Flatpak."""

    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if not raw or len(raw) > _MAX_FLATPAK_INFO_BYTES:
        return ""

    parser = ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(raw.decode("utf-8", errors="replace"))
    except ConfigParserError:
        return ""

    # Current Flatpak metadata places app-commit in [Application]. Supporting
    # [Instance] as a conservative compatibility fallback costs no probing and
    # still reads only the exact, validated key.
    for section in ("Application", "Instance"):
        commit = parser.get(section, "app-commit", fallback="").strip()
        if _APP_COMMIT_PATTERN.fullmatch(commit):
            return commit.lower()
    return ""


def _tool_status(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized == "available":
        return "available"
    if normalized in {"missing", "not detected", "not installed"}:
        return "not detected"
    return ""


def build_public_system_diagnostics(
    system_info: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
    flatpak_info_path: Path = Path("/.flatpak-info"),
    architecture: str | None = None,
) -> dict[str, Any]:
    """Build the allowlisted diagnostics snapshot exposed to QML and copy."""

    env = os.environ if environment is None else environment
    flatpak_file_present = flatpak_info_path.is_file()
    inside_flatpak = flatpak_file_present or bool(str(env.get("FLATPAK_ID", "")).strip())
    app_commit = read_flatpak_app_commit(flatpak_info_path) if flatpak_file_present else ""

    capabilities_value = system_info.get("capabilities")
    capabilities = (
        capabilities_value if isinstance(capabilities_value, Mapping) else {}
    )
    gaming: dict[str, str] = {}
    steam_status = _tool_status(capabilities.get("Steam"))
    steam_detected = system_info.get("steamExecutableDetected")
    if steam_status:
        gaming["steam"] = (
            "detected" if steam_status == "available" else "not detected"
        )
    elif isinstance(steam_detected, bool):
        gaming["steam"] = "detected" if steam_detected else "not detected"
    for label, key in _GAMING_TOOLS[1:]:
        status = _tool_status(capabilities.get(label))
        if status:
            gaming[key] = status

    renderer = _known_text(
        system_info.get("vulkanDevice") or system_info.get("vulkan_device")
    )
    gpu = _known_text(system_info.get("gpu"))
    if renderer == gpu:
        renderer = ""

    result: dict[str, Any] = {
        "appName": APP_NAME,
        "appVersion": APP_VERSION,
        "appId": APP_ID,
        "flatpak": inside_flatpak,
        "architecture": _known_text(architecture or platform.machine()),
        "distribution": _known_text(system_info.get("distribution")),
        "kernel": _known_text(system_info.get("kernel")),
        "desktop": _known_text(
            system_info.get("desktopEnvironment")
            or system_info.get("desktop_environment")
        ),
        "session": _known_text(
            system_info.get("sessionType") or system_info.get("session_type")
        ),
        "gpu": gpu,
        "renderer": renderer,
        "driver": _known_text(
            system_info.get("gpuDriver") or system_info.get("gpu_driver")
        ),
        "gaming": gaming,
    }
    if app_commit:
        result["appCommit"] = app_commit
    return result


def format_public_system_diagnostics(diagnostics: Mapping[str, Any]) -> str:
    """Format only explicitly allowlisted, known fields for public reports."""

    app_name = _known_text(diagnostics.get("appName")) or APP_NAME
    app_version = _known_text(diagnostics.get("appVersion"))
    lines = [" ".join(part for part in (app_name, app_version) if part)]
    app_id = _known_text(diagnostics.get("appId"))
    if app_id:
        lines.append(f"App ID: {app_id}")
    lines.append(f"Flatpak: {'yes' if diagnostics.get('flatpak') is True else 'no'}")

    fields = (
        ("App commit", "appCommit"),
        ("OS", "distribution"),
        ("Kernel", "kernel"),
        ("Architecture", "architecture"),
        ("Desktop", "desktop"),
        ("Session", "session"),
        ("GPU", "gpu"),
        ("Renderer", "renderer"),
        ("Driver", "driver"),
    )
    for label, key in fields:
        value = _known_text(diagnostics.get(key))
        if value:
            lines.append(f"{label}: {value}")

    gaming_value = diagnostics.get("gaming")
    gaming = gaming_value if isinstance(gaming_value, Mapping) else {}
    for label, key in _GAMING_TOOLS:
        status = _known_text(gaming.get(key))
        if status:
            lines.append(f"{label}: {status}")
    return "\n".join(lines)


__all__ = [
    "build_public_system_diagnostics",
    "format_public_system_diagnostics",
    "read_flatpak_app_commit",
]
