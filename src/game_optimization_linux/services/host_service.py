"""Distro-agnostic, allowlisted Flatpak-to-host probes.

Normal diagnostics intentionally do not depend on a copied host executable or
on a host Python installation.  Every host process below is selected by this
module and is started as an argv list through ``flatpak-spawn --host``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from game_optimization_linux.models.compression import CompressionMeasurement
from game_optimization_linux.models.game import Game

from .privileged_measurement import (
    PrivilegedMeasurementClient,
    PrivilegedMeasurementError,
)


HOST_SERVICE_VERSION = 2
OPTIONAL_MEASUREMENT_HELPER = Path(
    "/usr/libexec/game-optimization-linux-measure-helper"
)
_TOOLS = frozenset(
    {
        "steam",
        "flatpak",
        "gamescope",
        "gamemode",
        "mangohud",
        "vulkan",
        "btrfs-progs",
        "compsize",
        "bottles",
        "heroic",
        "lutris",
    }
)
_HOST_EXECUTABLES = frozenset(
    {
        "steam",
        "flatpak",
        "gamescope",
        "gamemoderun",
        "gamemoded",
        "mangohud",
        "vulkaninfo",
        "btrfs",
        "compsize",
        "bottles",
        "heroic",
        "heroic-games-launcher",
        "lutris",
        "lspci",
        "xrandr",
        "pkexec",
        "true",
    }
)
_TOOL_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "steam": (("steam",), ("com.valvesoftware.Steam",)),
    "flatpak": (("flatpak",), ()),
    "gamescope": (("gamescope",), ()),
    "gamemode": (("gamemoderun",), ()),
    "mangohud": (
        ("mangohud",),
        ("org.freedesktop.Platform.VulkanLayer.MangoHud",),
    ),
    "vulkan": (("vulkaninfo",), ()),
    "btrfs-progs": (("btrfs",), ()),
    "compsize": (("compsize",), ()),
    "bottles": (("bottles",), ("com.usebottles.bottles",)),
    "heroic": (
        ("heroic", "heroic-games-launcher"),
        ("com.heroicgameslauncher.hgl",),
    ),
    "lutris": (("lutris",), ("net.lutris.Lutris",)),
}
_VERSION_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "steam": ("--version",),
    "flatpak": ("--version",),
    "gamescope": ("--version",),
    # Execute only the fixed no-op program; gamemoderun has no version flag of
    # its own and a no-argument call may dump environment details.
    "gamemode": ("true",),
    "mangohud": ("--version",),
    "vulkan": ("--summary",),
    "btrfs-progs": ("--version",),
    "compsize": ("--version",),
    "bottles": ("--version",),
    "heroic": ("--version",),
    "lutris": ("--version",),
}
_MISSING_MARKERS = (
    "command not found",
    "no such file or directory",
    "failed to execute child process",
    "failed to start command",
    "executable file not found",
)
_MAX_OUTPUT = 256 * 1024


class HostServiceError(PrivilegedMeasurementError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = str(stdout)
        self.stderr = str(stderr)


class HostServiceClient:
    """Run only fixed host probes and the optional measurement endpoint."""

    def __init__(
        self,
        *,
        flatpak_spawn: Path | str | None = None,
        environment: Mapping[str, str] | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        timeout_seconds: float = 150.0,
        measurement_helper: Path = OPTIONAL_MEASUREMENT_HELPER,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._which = which
        discovered_spawn = which("flatpak-spawn")
        self._flatpak_spawn = str(flatpak_spawn or discovered_spawn or "flatpak-spawn")
        self._command_runner = command_runner
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._measurement_helper = Path(measurement_helper)
        self._tool_cache: dict[str, dict[str, Any]] = {}
        self._measurement_capability: bool | None = None

    @property
    def in_flatpak(self) -> bool:
        return bool(str(self._environment.get("FLATPAK_ID", "")).strip())

    @property
    def installed(self) -> bool:
        """Compatibility alias for the optional privileged measurement part."""

        return self.measurement_available

    @property
    def measurement_available(self) -> bool:
        if self._measurement_capability is None:
            self._measurement_capability = self._probe_measurement_component()
        return self._measurement_capability

    def diagnose(self) -> dict[str, Any]:
        tools: dict[str, dict[str, Any]] = {}
        for name in sorted(_TOOLS):
            try:
                tools[name] = self.tool_info(name)
            except Exception as error:
                tools[name] = self._unknown_tool(
                    name, f"Host probe failed: {type(error).__name__}"
                )
        return {
            "schema_version": 1,
            "service_version": HOST_SERVICE_VERSION,
            "source": "host",
            "transport": "flatpak-spawn" if self.in_flatpak else "native",
            "tools": tools,
            "gpu": self._gpu_info(),
            "measurement_component_available": self.measurement_available,
            "read_only": True,
        }

    def display_info(self) -> dict[str, Any]:
        displays: list[dict[str, Any]] = []
        result = self._run_fixed("xrandr", ("--current",), timeout=5.0)
        if result is not None and result.returncode == 0:
            for line in str(result.stdout or "").splitlines():
                match = re.match(
                    r"^(\S+) connected(?: primary)? (\d+)x(\d+)\+", line
                )
                if match:
                    displays.append(
                        {
                            "name": match.group(1),
                            "width": int(match.group(2)),
                            "height": int(match.group(3)),
                        }
                    )
        return {
            "schema_version": 1,
            "service_version": HOST_SERVICE_VERSION,
            "source": "host",
            "displays": displays,
            "gpu": self._gpu_info(),
            "read_only": True,
        }

    def tool_info(self, tool: str, *, refresh: bool = False) -> dict[str, Any]:
        normalized = str(tool).strip().casefold()
        if normalized not in _TOOLS:
            raise HostServiceError("Unsupported host diagnostic tool")
        if not refresh and normalized in self._tool_cache:
            return dict(self._tool_cache[normalized])

        commands, flatpak_ids = _TOOL_SPECS[normalized]
        selected = ""
        result: subprocess.CompletedProcess[str] | None = None
        probe_error = ""
        for command in commands:
            candidate = self._run_fixed(
                command, _VERSION_ARGUMENTS.get(normalized, ("--version",)), timeout=8.0
            )
            if candidate is None:
                continue
            if self._missing_command(candidate):
                continue
            selected = command
            result = candidate
            break

        flatpak_id = ""
        flatpak_version = ""
        if not selected:
            for app_id in flatpak_ids:
                candidate = self._run_fixed(
                    "flatpak", ("info", "--show-version", app_id), timeout=8.0
                )
                if candidate is not None and candidate.returncode == 0:
                    flatpak_id = app_id
                    flatpak_version = self._one_line(candidate.stdout)
                    break

        status = "available" if selected or flatpak_id else "unavailable"
        version = ""
        if result is not None:
            version = self._one_line(result.stdout or result.stderr)
            if result.returncode != 0:
                probe_error = f"Version probe exited with status {result.returncode}"
        if flatpak_version:
            version = flatpak_version

        supported_options: tuple[str, ...] = ()
        runtime_available = bool(selected or flatpak_id)
        if normalized == "gamescope" and selected:
            help_result = self._run_fixed(selected, ("--help",), timeout=8.0)
            if help_result is None or self._missing_command(help_result):
                status = "error"
                runtime_available = False
                probe_error = "gamescope --help could not be executed"
            else:
                help_text = f"{help_result.stdout or ''}\n{help_result.stderr or ''}"
                supported_options = tuple(
                    option
                    for option in (
                        "-W", "-H", "-w", "-h", "-r", "--framerate-limit",
                        "-f", "-b", "-S", "-F", "--display-index",
                    )
                    if option in help_text
                )
        elif normalized == "gamemode" and selected:
            runtime_available = bool(result is not None and result.returncode == 0)
            daemon_version = self._run_fixed("gamemoded", ("--version",), timeout=8.0)
            if daemon_version is not None and not self._missing_command(daemon_version):
                version = self._one_line(
                    daemon_version.stdout or daemon_version.stderr
                )
            daemon = self._run_fixed("gamemoded", ("--status",), timeout=8.0)
            if daemon is None or self._missing_command(daemon):
                runtime_available = False
                probe_error = "gamemoded is not installed"
            else:
                daemon_text = f"{daemon.stdout or ''}\n{daemon.stderr or ''}".casefold()
                if any(
                    marker in daemon_text
                    for marker in (
                        "could not connect", "failed to connect",
                        "gamemode_query_status failed",
                    )
                ):
                    runtime_available = False
                    probe_error = "GameMode service diagnostic failed"

        available = bool(selected or flatpak_id)
        source = "host" if selected else "flatpak" if flatpak_id else "unavailable"
        message = probe_error or (
            f"{tool} detected on the host"
            if available
            else f"{tool} was not detected on the host PATH"
        )
        payload: dict[str, Any] = {
            "available": available,
            "status": status,
            "executable": selected,
            "resolved_via_path": bool(selected),
            "version": version,
            "source": source,
            "diagnostic_message": message,
            "runtime_available": runtime_available,
            "supported_options": list(supported_options),
        }
        if normalized == "steam":
            payload.update(
                {
                    "native_available": bool(selected),
                    "flatpak_available": bool(flatpak_id),
                    "steam_type": (
                        "native" if selected else "flatpak" if flatpak_id else "unavailable"
                    ),
                    "host_launch_available": available,
                }
            )
        elif normalized == "mangohud":
            payload.update(
                {
                    "layer_available": bool(selected),
                    "flatpak_layer_available": bool(flatpak_id),
                }
            )
        self._tool_cache[normalized] = dict(payload)
        return payload

    def analysis(self, game: Game) -> dict[str, Any]:
        payload = self._compression_measure(game)
        try:
            PrivilegedMeasurementClient._validate_identity(game, payload)
        except PrivilegedMeasurementError as error:
            raise HostServiceError(str(error)) from error
        return payload

    def measure(self, game: Game) -> CompressionMeasurement:
        payload = self._compression_measure(game)
        try:
            PrivilegedMeasurementClient._validate_identity(game, payload)
        except PrivilegedMeasurementError as error:
            raise HostServiceError(str(error)) from error
        measurement = PrivilegedMeasurementClient.measurement_from_payload(payload)
        if (
            measurement.compsize_disk_bytes is None
            or measurement.compsize_uncompressed_bytes is None
            or measurement.compsize_referenced_bytes is None
        ):
            raise HostServiceError(
                str(payload.get("measurement_error") or "")
                or "The optional host component returned an incomplete compsize measurement"
            )
        return measurement

    def cancel_all(self) -> None:
        return None

    def _compression_measure(self, game: Game) -> dict[str, Any]:
        if game.library_path is None or not game.steam_app_id or not game.steam_build_id:
            raise HostServiceError(
                "Steam library, AppID and buildid are required for host measurement"
            )
        if not self.measurement_available:
            raise HostServiceError(
                "Exact compsize measurement is unavailable because the optional "
                "privileged host component is not installed"
            )
        result = self._run_fixed(
            "pkexec",
            (
                os.fspath(self._measurement_helper),
                "measure",
                "--library",
                os.fspath(game.library_path),
                "--appid",
                str(game.steam_app_id),
                "--buildid",
                str(game.steam_build_id),
            ),
            timeout=self._timeout_seconds,
        )
        if result is None:
            raise HostServiceError("pkexec is unavailable on the host")
        stdout = str(result.stdout or "")
        stderr = str(result.stderr or "")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise HostServiceError(
                "Privileged measurement returned invalid data",
                exit_code=int(result.returncode),
                stdout=stdout,
                stderr=stderr,
            ) from error
        if not isinstance(payload, dict):
            raise HostServiceError("Privileged measurement returned invalid data")
        if result.returncode != 0 or payload.get("ok") is not True:
            raise HostServiceError(
                str(payload.get("measurement_error") or "Privileged measurement failed"),
                exit_code=int(result.returncode),
                stdout=stdout,
                stderr=stderr,
            )
        return payload

    def _probe_measurement_component(self) -> bool:
        result = self._run_fixed(
            os.fspath(self._measurement_helper),
            ("capability", "--json"),
            timeout=5.0,
            allow_optional_helper=True,
        )
        if result is None or result.returncode != 0:
            return False
        try:
            payload = json.loads(str(result.stdout or ""))
        except json.JSONDecodeError:
            return False
        return bool(isinstance(payload, dict) and payload.get("available") is True)

    def _run_fixed(
        self,
        command: str,
        arguments: Sequence[str],
        *,
        timeout: float,
        allow_optional_helper: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        if command not in _HOST_EXECUTABLES and not (
            allow_optional_helper and command == os.fspath(self._measurement_helper)
        ):
            raise HostServiceError("Unsupported fixed host executable")
        if not self.in_flatpak:
            executable = command if os.path.isabs(command) else self._which(command)
            if not executable:
                return None
            argv = [str(executable), *map(str, arguments)]
        else:
            argv = [self._flatpak_spawn, "--host", command, *map(str, arguments)]
        try:
            result = self._command_runner(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                shell=False,
            )
        except FileNotFoundError:
            return None
        except (OSError, subprocess.SubprocessError) as error:
            raise HostServiceError(f"Host probe could not start: {error}") from error
        result.stdout = str(result.stdout or "")[:_MAX_OUTPUT]
        result.stderr = str(result.stderr or "")[:_MAX_OUTPUT]
        return result

    @staticmethod
    def _missing_command(result: subprocess.CompletedProcess[str]) -> bool:
        text = f"{result.stdout or ''}\n{result.stderr or ''}".casefold()
        return bool(
            int(result.returncode) in {126, 127}
            or any(marker in text for marker in _MISSING_MARKERS)
        )

    @staticmethod
    def _one_line(value: object, *, limit: int = 300) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _unknown_tool(name: str, message: str) -> dict[str, Any]:
        return {
            "available": False,
            "status": "error",
            "executable": "",
            "resolved_via_path": False,
            "version": "",
            "source": "unknown",
            "diagnostic_message": message,
            "runtime_available": False,
            "supported_options": [],
        }

    def _gpu_info(self) -> dict[str, Any]:
        result = self._run_fixed("lspci", ("-nnk",), timeout=8.0)
        model = driver = vendor = ""
        if result is not None and result.returncode == 0:
            in_gpu = False
            for line in str(result.stdout or "").splitlines():
                if line and not line[0].isspace():
                    match = re.match(
                        r"^.+?(?:VGA compatible controller|3D controller|Display controller)"
                        r"(?:\s*\[[^]]+\])?:\s*(.+)$",
                        line,
                        re.IGNORECASE,
                    )
                    in_gpu = match is not None
                    if in_gpu:
                        model = match.group(1).strip()
                        lowered = model.casefold()
                        vendor = (
                            "AMD"
                            if "amd" in lowered or "advanced micro devices" in lowered
                            else "NVIDIA"
                            if "nvidia" in lowered
                            else "Intel"
                            if "intel" in lowered
                            else ""
                        )
                    elif model:
                        break
                elif in_gpu:
                    match = re.match(
                        r"^\s*Kernel driver in use:\s*(.+)$", line, re.IGNORECASE
                    )
                    if match:
                        driver = match.group(1).strip()
        return {
            "vendor": vendor,
            "model": model,
            "driver": driver,
            "vulkan_device": "",
            "partial": not bool(model),
            "diagnostic_message": (
                "Host GPU detected" if model else "Host GPU information is unavailable"
            ),
        }


__all__ = [
    "HOST_SERVICE_VERSION",
    "OPTIONAL_MEASUREMENT_HELPER",
    "HostServiceClient",
    "HostServiceError",
]
