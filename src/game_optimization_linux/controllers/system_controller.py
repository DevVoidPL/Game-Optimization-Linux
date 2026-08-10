from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..models import SystemInfo
from .presenters import qml_value, system_info_to_qml

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


class SystemController:
    def __init__(self, app: AppController) -> None:
        self._app = app

    def _add_gamepad_system_info(self) -> None:
        active = dict(self._app._gamepad_service.activeController)
        diagnostics = dict(self._app._gamepad_service.diagnostics)
        self._app._system_info.update(
            {
                "sdl3Status": str(self._app._gamepad_service.status),
                "gamepadAvailable": bool(self._app._gamepad_service.available),
                "controllerCount": int(self._app._gamepad_service.controllerCount),
                "activeController": active,
                "activeControllerName": str(active.get("name", "")),
                "activeControllerType": str(active.get("type", "")),
                "activeControllerMapping": str(active.get("mappingStatus", "")),
                "controllerDiagnostics": diagnostics,
                "sdl3LibraryAvailable": diagnostics.get("sdl3LibraryAvailable") is True,
                "inputDeviceAccessAvailable": diagnostics.get("inputDeviceAccessAvailable") is True,
                "joystickCount": int(diagnostics.get("joystickCount") or 0),
                "gamepadCount": int(diagnostics.get("gamepadCount") or 0),
                "controllerDiagnosticReason": str(diagnostics.get("reason") or ""),
            }
        )
        capabilities = self._app._system_info.get("capabilities")
        if isinstance(capabilities, Mapping):
            updated_capabilities = dict(capabilities)
        else:
            updated_capabilities = {}
        updated_capabilities["SDL3"] = str(self._app._gamepad_service.status)
        self._app._system_info["capabilities"] = updated_capabilities

    def _reload_system_info(self, *, emit_signal: bool = True) -> None:
        try:
            system_info = self._app._read_system_info()
            if not self._app._demo_mode:
                try:
                    detected_filesystems = self._app._read_filesystems()
                except Exception as error:
                    logger.warning("Could not enumerate mounted filesystems: %s", error)
                    detected_filesystems = ()
                if detected_filesystems:
                    system_info = replace(
                        system_info,
                        filesystems=tuple(detected_filesystems),
                    )
            self._app._system_info = system_info_to_qml(system_info)
        except Exception as error:
            logger.exception("Could not load system information: %s", error)
            self._app._system_info = {
                "distribution": "Unknown",
                "kernel": "Unknown",
                "desktopEnvironment": "Unknown",
                "sessionType": "Unknown",
                "cpu": "Unknown",
                "gpu": "Unknown",
                "gpuDriver": "Unknown",
                "capabilities": {},
                "demo": self._app._demo_mode,
                "error": "System information is temporarily unavailable",
            }
            self._app._deferred_toasts.append(
                ("System information is temporarily unavailable", "warning")
            )
        self._app._add_compression_system_info()
        self._app._add_steam_system_info()
        self._app._add_gamepad_system_info()
        if emit_signal:
            self._app.systemInfoChanged.emit()

    def _add_compression_system_info(self) -> None:
        service = self._app._compression_service
        if service is None:
            capabilities = {
                "btrfsAvailable": False,
                "compsizeAvailable": False,
                "propertySupported": False,
                "recompressionSupported": False,
                "levelSupported": False,
                "compressionAvailable": False,
                "activeJobs": 0,
                "lastError": "",
                "message": "Real compression is unavailable in this mode",
            }
        else:
            try:
                raw = service.capabilities().to_dict()
            except Exception as error:
                logger.warning("Could not inspect Btrfs capabilities: %s", error)
                raw = {
                    "btrfs_available": False,
                    "compsize_available": False,
                    "property_supported": False,
                    "recompression_supported": False,
                    "level_supported": False,
                    "compression_available": False,
                    "message": str(error),
                }
            capabilities = {
                **cast(dict[str, Any], qml_value(raw)),
                "btrfsAvailable": raw.get("btrfs_available") is True,
                "btrfsVersion": str(raw.get("btrfs_version", "")),
                "compsizeAvailable": raw.get("compsize_available") is True,
                "compsizeVersion": str(raw.get("compsize_version", "")),
                "propertySupported": raw.get("property_supported") is True,
                "recompressionSupported": raw.get("recompression_supported") is True,
                "levelSupported": raw.get("level_supported") is True,
                "compressionAvailable": raw.get("compression_available") is True,
                "activeJobs": len(service.active_game_ids()),
                "lastError": str(service.last_error),
                "message": str(raw.get("message", "")),
            }
        host_details = self._app._system_info.get("capabilityDetails")
        if isinstance(host_details, Mapping):
            host_btrfs = host_details.get("Btrfs tools")
            host_compsize = host_details.get("compsize")
            if isinstance(host_btrfs, Mapping):
                capabilities["hostBtrfsAvailable"] = host_btrfs.get("available") is True
                capabilities["hostBtrfsVersion"] = str(host_btrfs.get("version") or "")
            if isinstance(host_compsize, Mapping):
                capabilities["hostCompsizeAvailable"] = (
                    host_compsize.get("available") is True
                )
                if (
                    self._app._host_service is not None
                    and self._app._host_service.measurement_available
                    and host_compsize.get("available") is True
                ):
                    capabilities["compsizeAvailable"] = True
                    capabilities["compsizeVersion"] = str(
                        host_compsize.get("version") or ""
                    )
            if self._app._host_service is not None:
                capabilities["measurementSource"] = (
                    "optional_host_component"
                    if self._app._host_service.measurement_available
                    else "unavailable"
                )
                capabilities["hostComponentInstalled"] = self._app._host_service.installed
        self._app._system_info["compressionCapabilities"] = capabilities
        self._app._system_info["compression_capabilities"] = capabilities

    def _add_steam_system_info(self) -> None:
        details = self._app._system_info.get("capabilityDetails")
        steam = details.get("Steam") if isinstance(details, Mapping) else None
        steam_map = dict(steam) if isinstance(steam, Mapping) else {}
        executable_detected = steam_map.get("available") is True
        steam_type = str(steam_map.get("steam_type") or "unavailable")
        host_launch = steam_map.get("host_launch_available") is True
        self._app._system_info.update(
            {
                "steamLibraryDetected": bool(self._app._steam_found),
                "steam_library_detected": bool(self._app._steam_found),
                "steamExecutableDetected": executable_detected,
                "steam_executable_detected": executable_detected,
                "steamType": steam_type,
                "steam_type": steam_type,
                "hostLaunchAvailable": host_launch,
                "host_launch_available": host_launch,
            }
        )

    def _read_system_info(self) -> SystemInfo:
        for method_name in ("get_system_info", "inspect", "detect", "collect"):
            method = getattr(self._app._system_provider, method_name, None)
            if callable(method):
                return cast(SystemInfo, method())
        raise AttributeError("system provider does not expose an inspection method")

    def _read_filesystems(self) -> Sequence[Any]:
        provider = self._app._filesystem_provider
        method = getattr(provider, "list_filesystems", None)
        if not callable(method):
            return ()

        probe_paths: dict[str, Path] = {}
        for path in (
            *self._app._settings_model.library_directories,
            *self._app._settings_model.steam_installation_directories,
            *(game.install_path for game in self._app._domain_games.values()),
            *(
                game.library_path
                for game in self._app._domain_games.values()
                if game.library_path is not None
            ),
        ):
            normalized_path = os.path.normpath(os.path.abspath(os.fspath(path)))
            probe_paths[normalized_path] = Path(path)

        try:
            return cast(
                Sequence[Any],
                method(
                    game_paths=tuple(probe_paths.values()),
                    show_system_mounts=self._app._show_system_mounts,
                ),
            )
        except TypeError:
            # Preserve compatibility with third-party read-only providers that
            # implemented the original no-argument protocol.
            return cast(Sequence[Any], method())
