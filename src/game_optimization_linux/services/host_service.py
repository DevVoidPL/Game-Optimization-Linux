"""Distro-agnostic, allowlisted Flatpak-to-host probes.

Normal diagnostics intentionally do not depend on a copied host executable or
on a host Python installation.  Every host process below is selected by this
module and is started as an argv list through ``flatpak-spawn --host``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
from threading import RLock
from typing import Any

from game_optimization_linux.models.compression import CompressionMeasurement
from game_optimization_linux.models.game import Game
from game_optimization_linux.models.enums import FilesystemType

from .btrfs_analysis import BtrfsCompressionAnalyzer
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
        self._measurement_mode = ""
        self._measurement_processes: set[subprocess.Popen[str]] = set()
        self._measurement_processes_lock = RLock()

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

        if normalized == "steam":
            payload = self._steam_info()
            self._tool_cache[normalized] = dict(payload)
            return payload

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
        if normalized == "mangohud":
            payload.update(
                {
                    "layer_available": bool(selected),
                    "flatpak_layer_available": bool(flatpak_id),
                }
            )
        self._tool_cache[normalized] = dict(payload)
        return payload

    def _steam_info(self) -> dict[str, Any]:
        """Detect Steam without starting the client or any compatibility probe."""

        home = Path(str(self._environment.get("HOME") or Path.home())).expanduser()
        native_markers = (
            home / ".local/share/Steam/steam.sh",
            home / ".local/share/Steam/ubuntu12_32/steam",
            home / ".steam/root/steam.sh",
            home / ".steam/steam/steam.sh",
            home / ".local/share/applications/steam.desktop",
        )
        native_available = any(path.exists() for path in native_markers)
        if not self.in_flatpak and self._which("steam"):
            native_available = True

        flatpak_id = ""
        flatpak_version = ""
        candidate = self._run_fixed(
            "flatpak", ("info", "--show-version", "com.valvesoftware.Steam"),
            timeout=8.0,
        )
        if candidate is not None and candidate.returncode == 0:
            flatpak_id = "com.valvesoftware.Steam"
            flatpak_version = self._one_line(candidate.stdout)

        available = bool(native_available or flatpak_id)
        steam_type = (
            "native" if native_available else "flatpak" if flatpak_id else "unavailable"
        )
        return {
            "available": available,
            "status": "available" if available else "unavailable",
            "executable": "steam" if native_available else "",
            "resolved_via_path": bool(native_available and not self.in_flatpak),
            "version": flatpak_version,
            "source": "host" if native_available else "flatpak" if flatpak_id else "unavailable",
            "diagnostic_message": (
                "Steam installation detected without starting the client"
                if available
                else "Steam was not detected in user data or as a Flatpak"
            ),
            "runtime_available": available,
            "supported_options": [],
            "native_available": native_available,
            "flatpak_available": bool(flatpak_id),
            "steam_type": steam_type,
            "host_launch_available": available,
        }

    def analysis(self, game: Game) -> dict[str, Any]:
        available = self.measurement_available
        if self._measurement_mode == "direct_compsize":
            raise HostServiceError(
                "Exact compsize is available as an explicit authenticated measurement"
            )
        if not available:
            raise HostServiceError(
                "Exact compsize measurement is unavailable because host compsize or pkexec was not detected"
            )
        payload = self._compression_measure(game)
        try:
            PrivilegedMeasurementClient._validate_identity(game, payload)
        except PrivilegedMeasurementError as error:
            raise HostServiceError(str(error)) from error
        return payload

    def measure(self, game: Game) -> CompressionMeasurement:
        if not self.measurement_available:
            raise HostServiceError(
                "Exact compsize measurement is unavailable because host compsize or pkexec was not detected"
            )
        if self._measurement_mode == "direct_compsize":
            return self._measure_direct_compsize(game)
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
        with self._measurement_processes_lock:
            processes = tuple(self._measurement_processes)
        for process in processes:
            self._terminate_group(process, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._terminate_group(process, signal.SIGKILL)

    def _compression_measure(self, game: Game) -> dict[str, Any]:
        if not self.measurement_available:
            raise HostServiceError(
                "Exact compsize measurement is unavailable because the optional "
                "privileged host component is not installed"
            )
        if self._measurement_mode != "installed_helper":
            raise HostServiceError(
                "Exact compsize is available only as an explicit authenticated measurement"
            )
        arguments: tuple[str, ...]
        if game.library_path is not None and game.steam_app_id and game.steam_build_id:
            arguments = (
                os.fspath(self._measurement_helper),
                "measure",
                "--library",
                os.fspath(game.library_path.resolve(strict=True)),
                "--appid",
                str(game.steam_app_id),
                "--buildid",
                str(game.steam_build_id),
            )
        else:
            try:
                game_path = game.install_path.resolve(strict=True)
                identity = game_path.stat()
            except OSError as error:
                raise HostServiceError(
                    f"The selected game directory is unavailable: {error}"
                ) from error
            if not game_path.is_dir():
                raise HostServiceError("The selected game path is not a directory")
            arguments = (
                os.fspath(self._measurement_helper),
                "measure",
                "--game-path",
                os.fspath(game_path),
                "--game-id",
                str(game.id),
                "--device",
                str(identity.st_dev),
                "--inode",
                str(identity.st_ino),
            )
        result = self._run_fixed(
            "pkexec",
            arguments,
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
        try:
            compsize = self.tool_info("compsize", refresh=True)
            pkexec = self._run_fixed("pkexec", ("--version",), timeout=5.0)
        except HostServiceError:
            compsize = {}
            pkexec = None
        if (
            compsize.get("available") is True
            and pkexec is not None
            and pkexec.returncode == 0
        ):
            self._measurement_mode = "direct_compsize"
            return True
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
        available = bool(
            isinstance(payload, dict) and payload.get("available") is True
        )
        if available:
            self._measurement_mode = "installed_helper"
        return available

    def _measure_direct_compsize(self, game: Game) -> CompressionMeasurement:
        try:
            root = game.install_path.resolve(strict=True)
        except OSError as error:
            raise HostServiceError(
                f"The selected game directory is unavailable: {error}"
            ) from error
        if not root.is_absolute() or not root.is_dir():
            raise HostServiceError("The selected game path is not a directory")
        if game.filesystem is not FilesystemType.BTRFS and (
            game.filesystem_name.casefold() != "btrfs"
        ):
            raise HostServiceError("Exact compsize is available only for a verified Btrfs game")

        result = self._run_privileged(
            [self._flatpak_spawn, "--host", "pkexec", "compsize", "--", os.fspath(root)]
            if self.in_flatpak
            else [str(self._which("pkexec") or "pkexec"), "compsize", "--", os.fspath(root)]
        )
        stdout = str(result.stdout or "")[:_MAX_OUTPUT]
        stderr = str(result.stderr or "")[:_MAX_OUTPUT]
        debug_measurement = (
            str(
                self._environment.get(
                    "GAME_OPTIMIZATION_DEBUG_COMPRESSION", ""
                )
            ).strip()
            == "1"
        )
        if debug_measurement:
            logger.info(
                "Exact compsize raw output: gameId=%s path=%s exitCode=%s "
                "stdout=%r stderr=%r",
                game.id,
                root,
                result.returncode,
                stdout,
                stderr,
            )
        if result.returncode in {126, 127}:
            raise HostServiceError(
                "Authorization cancelled",
                exit_code=int(result.returncode),
                stdout=stdout,
                stderr=stderr,
            )
        if result.returncode != 0:
            detail = " ".join(stderr.split())
            raise HostServiceError(
                detail or f"compsize exited with status {result.returncode}",
                exit_code=int(result.returncode),
                stdout=stdout,
                stderr=stderr,
            )
        parsed = BtrfsCompressionAnalyzer.parse_compsize(stdout)
        if debug_measurement:
            complete = bool(
                parsed.available
                and parsed.disk_usage_bytes is not None
                and parsed.disk_usage_bytes > 0
                and parsed.uncompressed_bytes is not None
                and parsed.uncompressed_bytes > 0
                and parsed.referenced_bytes is not None
                and parsed.referenced_bytes > 0
            )
            logger.info(
                "Exact compsize parsed output: gameId=%s disk=%r "
                "uncompressed=%r referenced=%r ratio=%r saving=%r "
                "source=polkit_compsize exact=true complete=%s message=%r",
                game.id,
                parsed.disk_usage_bytes,
                parsed.uncompressed_bytes,
                parsed.referenced_bytes,
                parsed.current_compression_ratio,
                (
                    max(0, parsed.uncompressed_bytes - parsed.disk_usage_bytes)
                    if complete
                    else None
                ),
                complete,
                parsed.message,
            )
        if (
            not parsed.available
            or parsed.disk_usage_bytes is None
            or parsed.disk_usage_bytes <= 0
            or parsed.uncompressed_bytes is None
            or parsed.uncompressed_bytes <= 0
            or parsed.referenced_bytes is None
            or parsed.referenced_bytes <= 0
        ):
            raise HostServiceError(
                "Exact compsize returned an incomplete result: "
                + (parsed.message or "required values are missing"),
                exit_code=int(result.returncode),
                stdout=stdout,
                stderr=stderr,
            )
        try:
            filesystem = os.statvfs(root)
            block_size = filesystem.f_frsize or filesystem.f_bsize
            total = int(filesystem.f_blocks * block_size)
            free = int(filesystem.f_bfree * block_size)
            available = int(filesystem.f_bavail * block_size)
        except OSError:
            total = free = available = None
        return CompressionMeasurement(
            logical_bytes=max(0, int(float(game.logical_size_gb) * 1_000_000_000)),
            physical_bytes=parsed.disk_usage_bytes,
            exclusive_bytes=None,
            shared_bytes=None,
            compsize_disk_bytes=parsed.disk_usage_bytes,
            compsize_uncompressed_bytes=parsed.uncompressed_bytes,
            compsize_referenced_bytes=parsed.referenced_bytes,
            scan_complete=True,
            shared_extent_state=(
                "detected"
                if parsed.possible_shared_extents is True
                else "not_detected"
                if parsed.possible_shared_extents is False
                else "unknown"
            ),
            filesystem_available_bytes=available,
            filesystem_free_bytes=free,
            filesystem_used_bytes=(total - free if total is not None and free is not None else None),
            filesystem_total_bytes=total,
            measurement_source="polkit_compsize",
            measured_at=datetime.now(UTC),
        )

    def _run_privileged(
        self, command: list[str]
    ) -> subprocess.CompletedProcess[str]:
        if self._command_runner is not subprocess.run:
            try:
                return self._command_runner(
                    command,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self._timeout_seconds,
                    shell=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise HostServiceError(
                    f"Exact measurement could not start: {error}"
                ) from error
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            raise HostServiceError(
                f"Exact measurement could not start: {error}"
            ) from error
        with self._measurement_processes_lock:
            self._measurement_processes.add(process)
        try:
            try:
                stdout, stderr = process.communicate(timeout=self._timeout_seconds)
            except subprocess.TimeoutExpired:
                self._terminate_group(process, signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self._terminate_group(process, signal.SIGKILL)
                    stdout, stderr = process.communicate()
                raise HostServiceError("Exact compsize measurement timed out")
            return subprocess.CompletedProcess(
                command, int(process.returncode or 0), stdout, stderr
            )
        finally:
            with self._measurement_processes_lock:
                self._measurement_processes.discard(process)

    @staticmethod
    def _terminate_group(
        process: subprocess.Popen[str], sig: signal.Signals
    ) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.send_signal(sig)
            except OSError:
                return

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
