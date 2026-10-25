"""Distro-agnostic, allowlisted Flatpak-to-host probes.

Normal diagnostics intentionally do not depend on a copied host executable or
on a host Python installation.  Every host process below is selected by this
module and is started as an argv list through ``flatpak-spawn --host``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import base64
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time
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
        "vkcube",
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
        # Read-only process list for "is the game running" (Narrator autostart).
        "ps",
        # Read only /proc/<validated numeric PID>/maps for module diagnostics.
        "cat",
        "ldd",
        # Fixed probes and managed FG CLI argv transport below; no user shell text.
        "sh",
        "base64",
        "env",
        # Same-filesystem rename of a staged launcher config (launcher integration).
        "mv",
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
_LIVE_PREVIEW_PROBES: dict[str, tuple[str, ...]] = {
    "gamescope": ("--version",),
    "mangohud": ("--version",),
    "vkcube": ("--help",),
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
# Flatpak extensions GameOpti may install for a launcher (explicit whitelist).
# Nothing else can reach ``flatpak install`` through the host bridge.
FLATPAK_EXTENSION_WHITELIST = frozenset(
    {
        "org.freedesktop.Platform.VulkanLayer.MangoHud",
        "org.freedesktop.Platform.VulkanLayer.gamescope",
        "org.freedesktop.Platform.VulkanLayer.vkBasalt",
    }
)
_FLATPAK_EXTENSION_REF = re.compile(
    r"^runtime/(?P<name>org\.freedesktop\.Platform\.VulkanLayer\.(?:MangoHud|gamescope|vkBasalt))"
    r"/(?P<arch>x86_64|aarch64|i386)/(?P<branch>[0-9]{2}\.[0-9]{2})$"
)
_FLATPAK_REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FLATPAK_APP_ID = re.compile(r"^[A-Za-z0-9_.-]{3,200}$")


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
        supported_filters: tuple[str, ...] = ()
        supported_scalers: tuple[str, ...] = ()
        supported_backends: tuple[str, ...] = ()
        runtime_available = bool(selected or flatpak_id)
        if normalized == "gamescope" and selected:
            help_result = self._run_fixed(selected, ("--help",), timeout=8.0)
            if help_result is None or self._missing_command(help_result):
                status = "error"
                runtime_available = False
                probe_error = "gamescope --help could not be executed"
            else:
                help_text = f"{help_result.stdout or ''}\n{help_result.stderr or ''}"
                from .optimization_runtime import (
                    parse_gamescope_backends,
                    parse_gamescope_help,
                )

                supported_options, supported_filters, supported_scalers = parse_gamescope_help(help_text)
                supported_backends = parse_gamescope_backends(help_text)
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
            "supported_filters": list(supported_filters),
            "supported_scalers": list(supported_scalers),
            "supported_backends": list(supported_backends),
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

    def host_command_available(self, command: str) -> bool:
        """Whether a MangoHud live-preview command exists on the host PATH.

        Only the two fixed preview programs are accepted.  ``vkcube --help``
        prints usage and exits without opening a window.
        """

        arguments = _LIVE_PREVIEW_PROBES.get(str(command))
        if arguments is None:
            raise HostServiceError("Unsupported host preview command")
        result = self._run_fixed(str(command), arguments, timeout=8.0)
        return result is not None and not self._missing_command(result)

    def host_command_help(self, command: str) -> str:
        """``--help`` text of a fixed preview program (currently only vkcube)."""

        if str(command) != "vkcube":
            raise HostServiceError("Unsupported host preview command")
        result = self._run_fixed("vkcube", ("--help",), timeout=8.0)
        if result is None or self._missing_command(result):
            return ""
        return f"{result.stdout or ''}\n{result.stderr or ''}"

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

    def host_process_commands(self) -> tuple[str, ...] | None:
        """Command lines of host processes (read-only ``ps``); None if unknown.

        Used only to recognise a running game from the sandbox: Steam's
        ``reaper SteamLaunch AppId=<id>`` wrapper, or the game's own path.
        """

        try:
            result = self._run_fixed("ps", ("-eo", "args=", "--width", "512"), timeout=3.0)
        except HostServiceError:
            return None
        if result is None or result.returncode != 0:
            return None
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())

    def frame_generation_command(self, binary: Path, arguments: Sequence[str]):
        """Run only the two managed upstream validation CLIs, unprivileged."""
        from .frame_generation_artifacts import FrameGenerationArtifacts
        components = FrameGenerationArtifacts()
        allowed = {components.directory("lsfg") / "bin/lsfg-vk-cli",
                   components.directory("mako") / "bin/mako-cli"}
        if Path(binary) not in allowed or not arguments or arguments[0] not in {"benchmark", "inspect-dll", "validate"}:
            raise HostServiceError("Unsupported frame-generation validation command")
        return self._run_fixed("sh", ("-c", 'exec "$@"', "sh", str(binary), *arguments), timeout=40)

    def frame_generation_capabilities(self) -> dict[str, Any]:
        key = "frame-generation-vulkan"
        cached = self._tool_cache.get(key)
        if cached and cached.get("_expires", 0) > datetime.now(UTC).timestamp(): return cached
        probe = 'export LC_ALL=C DISABLE_LSFG=1 DISABLE_LSFGVK=1 DISABLE_MAKO=1 DISABLE_MAKO_SPATIAL_SCALING=1 DISABLE_AFMF=1 DISABLE_VKBASALT=1 DISABLE_MANGOHUD=1; exec vulkaninfo'
        result = self._run_fixed("sh", ("-c", probe), timeout=20, max_output=12 * 1024 * 1024)
        output = result.stdout if result and result.returncode == 0 else ""
        payload = {"available": bool(output),
                   "devices": list(dict.fromkeys(re.findall(r"deviceName\s*=\s*([^\n]+)", output))),
                   "apiVersions": list(dict.fromkeys(re.findall(r"apiVersion\s*=\s*([^\n]+)", output))),
                   "compute": "QUEUE_COMPUTE" in output,
                   "storageImages": bool(re.search(r"shaderStorageImageExtendedFormats\s*=\s*true", output)),
                   "float16": bool(re.search(r"shaderFloat16\s*=\s*true", output)),
                   "timelineSemaphore": bool(re.search(r"timelineSemaphore\s*=\s*true", output)),
                   "note": "Host probe; selected game device and launcher runtime require separate validation",
                   "_expires": datetime.now(UTC).timestamp() + 120}
        self._tool_cache[key] = payload
        return payload

    def frame_generation_external_layers(self) -> list[dict[str, Any]]:
        from .host_bootstrap import host_home_directory
        home = host_home_directory()
        roots = [str(home / ".local/share/vulkan/implicit_layer.d"),
                 str(home / ".config/vulkan/implicit_layer.d"), "/usr/share/vulkan/implicit_layer.d", "/etc/vulkan/implicit_layer.d"]
        probe = 'for d do for p in "$d"/*.json; do [ -f "$p" ] || continue; printf "\\036%s\\n" "$p"; head -c 65536 "$p"; done; done'
        result = self._run_fixed("sh", ("-c", probe, "sh", *roots), timeout=5)
        found = []
        for record in (result.stdout if result else "").split("\x1e"):
            if not record: continue
            path, _, text = record.partition("\n")
            try:
                layer = json.loads(text).get("layer", {})
                if any(p in layer.get("name", "").upper() for p in ("LSFG", "MAKO", "AFMF")):
                    found.append({"path": path, "name": layer.get("name"), "disable": layer.get("disable_environment", {})})
            except ValueError: continue
        return found

    def frame_generation_grant(self, app_id: str, paths: Sequence[str], *, bridge: bool = False):
        from .frame_generation_access import LAUNCHERS
        from .host_bootstrap import host_home_directory
        home = host_home_directory()
        if app_id not in LAUNCHERS.values() or not paths: raise HostServiceError("Invalid launcher access request")
        allowed = [home / ".local/share/game-optimization-linux/frame-generation",
                   home / ".var/app/io.github.DevVoidPL.GameOptimizationLinux/config/game-optimization-linux/games",
                   home / ".config/game-optimization-linux/games"]
        exact = {home / ".local/share/game-optimization-linux/bin", home / ".local/share/game-optimization-linux/run-env"}
        for value in paths:
            path = Path(value.removesuffix(":ro"))
            if ".." in path.parts or not (any(path.is_relative_to(root) for root in allowed) or (app_id == "com.valvesoftware.Steam" and path in exact)):
                raise HostServiceError("Launcher access must be limited to managed frame-generation files")
        if bridge and app_id != "com.valvesoftware.Steam": raise HostServiceError("Only the Steam runner requires the existing host bridge")
        options = ["--talk-name=org.freedesktop.Flatpak"] if bridge else []
        result = self._run_fixed("flatpak", ("override", "--user", *options, *("--filesystem=" + p for p in paths), app_id), timeout=15)
        for key in list(self._tool_cache):
            if key.startswith("fg-environment:" + app_id + ":"): self._tool_cache.pop(key, None)
        return result

    def frame_generation_environment(self, app_id: str, paths: Sequence[str]) -> dict[str, Any]:
        """Probe files and ELF dependencies inside the launcher that executes the game."""
        if app_id and not _FLATPAK_APP_ID.fullmatch(app_id): raise HostServiceError("Invalid launcher ID")
        if any(not Path(p).is_absolute() or any(c in p for c in "\0\r\n") for p in paths):
            raise HostServiceError("Invalid component paths")
        cache_key = "fg-environment:" + app_id + ":" + ":".join(paths)
        cached = self._tool_cache.get(cache_key)
        if cached and cached.get("_expires", 0) > datetime.now(UTC).timestamp(): return cached
        probe = 'export LC_ALL=C; for p do if [ -r "$p" ]; then printf "read:%s\\n" "$p"; case "$p" in *.so) ldd "$p" 2>&1 || printf "dependency-failed:%s\\n" "$p";; */mako-launch) command -v bash >/dev/null || printf "missing:bash\\n";; esac; else printf "missing:%s\\n" "$p"; fi; done'
        args = ("run", "--command=sh", app_id, "-c", probe, "sh", *paths) if app_id else ("-c", probe, "sh", *paths)
        result = self._run_fixed("flatpak" if app_id else "sh", args, timeout=20)
        output = (result.stdout + result.stderr) if result else ""
        payload = {"checked": bool(result and result.returncode == 0),
                "available": bool(result and result.returncode == 0 and "missing:" not in output and "not found" not in output and "dependency-failed:" not in output),
                "details": output[-6000:], "launcherAppId": app_id, "_expires": datetime.now(UTC).timestamp() + 30}
        self._tool_cache[cache_key] = payload
        return payload

    def game_process_modules(self, executable_name: str) -> tuple[dict[str, Any], ...]:
        """Read mapped modules of matching game processes through the host bridge."""

        name = str(executable_name).strip()
        if not name or "/" in name or "\\" in name or "\0" in name or "\n" in name:
            raise HostServiceError("Invalid game executable name")
        result = self._run_fixed("ps", ("-eo", "pid=,comm=", "--width", "512"), timeout=3.0)
        if result is None or result.returncode != 0:
            return ()
        matches: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) != 2 or not fields[0].isdigit():
                continue
            if fields[1].casefold() != name[:15].casefold():
                continue
            process_id = int(fields[0])
            if process_id <= 0:
                continue
            maps = self._run_fixed("cat", (f"/proc/{process_id}/maps",), timeout=3.0, max_output=4 * 1024 * 1024)
            if maps is None or maps.returncode != 0:
                continue
            modules = set()
            for mapping in maps.stdout.splitlines():
                parts = mapping.split(None, 5)
                if len(parts) == 6 and parts[5].startswith("/"):
                    modules.add(parts[5])
            matches.append({"processId": process_id, "modules": sorted(modules)})
            if len(matches) >= 8:
                break
        return tuple(matches)

    def post_processing_layer_directory(self, *, install: bool = False) -> str:
        """Obtain the verified Flathub package; never modify a global Vulkan config."""
        from .post_processing_artifacts import VKBASALT_REF
        result = self._run_fixed("flatpak", ("info", "--user", "--show-location", VKBASALT_REF), timeout=8)
        if (result is None or result.returncode != 0) and install:
            installed = self.install_flatpak_extension("user", "flathub", VKBASALT_REF, lambda _line: None)
            if installed.returncode != 0:
                raise HostServiceError("Could not install vkBasalt: " + self._one_line(installed.stdout))
            result = self._run_fixed("flatpak", ("info", "--user", "--show-location", VKBASALT_REF), timeout=8)
        if result is None or result.returncode != 0:
            raise HostServiceError("The vkBasalt Flatpak component is not installed")
        location = result.stdout.strip()
        if not location.startswith("/") or "\n" in location:
            raise HostServiceError("Invalid vkBasalt package location")
        return location + "/files"

    def post_processing_component(self, relative: str) -> bytes:
        """Read only the package's fixed files across Flatpak's hidden installation tree."""
        allowed = {f"lib/{arch}-linux-gnu/vkbasalt/libvkbasalt.so" for arch in ("x86_64", "i386")}
        allowed.update("share/licenses/org.freedesktop.Platform.VulkanLayer.vkBasalt/" + name + "/LICENSE"
                       for name in ("vkBasalt", "vkBasalt-32bit", "spirv-headers"))
        if relative not in allowed:
            raise HostServiceError("Unsupported vkBasalt package component")
        source = str(Path(self.post_processing_layer_directory()) / relative)
        result = self._run_fixed("base64", ("--wrap=0", "--", source), timeout=15, max_output=12 * 1024 * 1024)
        if result is None or result.returncode != 0 or len(result.stdout) > 12 * 1024 * 1024:
            raise HostServiceError("The host could not read the vkBasalt package component")
        try:
            return base64.b64decode(result.stdout.strip(), validate=True)
        except ValueError as error:
            raise HostServiceError("Invalid vkBasalt component transfer") from error

    def post_processing_probe(self, library: Path, *, launcher_id: str = "", runtime: str = "") -> dict[str, Any]:
        """Probe dependencies in the actual launcher/container, not GameOpti's sandbox."""
        library = Path(library)
        if not library.is_absolute() or library.name != "libvkbasalt.so":
            raise HostServiceError("Invalid Vulkan layer path")
        if launcher_id:
            if launcher_id not in {"com.heroicgameslauncher.hgl", "net.lutris.Lutris", "com.valvesoftware.Steam"}:
                raise HostServiceError("Unsupported launcher sandbox")
            label = launcher_id + " Flatpak"
            command, args = "flatpak", ("run", "--command=sh", launcher_id, "-c",
                                        'test -r "$1" && ldd "$1"', "sh", str(library))
        elif runtime:
            path = Path(runtime)
            if path.name != "_v2-entry-point" or not path.parent.name.startswith("SteamLinuxRuntime") or not path.is_file():
                raise HostServiceError("Invalid Steam Linux Runtime entry point")
            label = path.parent.name
            command, args = "env", (str(path), "--verb=run", "--", "ldd", str(library))
        else:
            label = "host"
            command, args = "ldd", (str(library),)
        result = self._run_fixed(command, args, timeout=25)
        output = (result.stdout + result.stderr) if result else "Probe unavailable"
        ready = result is not None and result.returncode == 0 and "not found" not in output
        return {"environment": label, "dependenciesAvailable": ready,
                "runtimeLoaded": False, "detail": output[-3000:]}

    def replace_file(self, staged: Path, target: Path) -> None:
        """Atomically rename ``staged`` over ``target`` on the host.

        Used for launcher configs that are mounted read-only in this sandbox.
        ``--no-copy`` makes ``mv`` fail instead of copying across filesystems,
        so the replacement is a single rename(2) or nothing.
        """

        staged_text, target_text = os.fspath(staged), os.fspath(target)
        for value in (staged_text, target_text):
            if not os.path.isabs(value) or "\0" in value or "\n" in value:
                raise HostServiceError("Invalid path for a host file replacement")
        result = self._run_fixed(
            "mv", ("--no-copy", "-f", "-T", "--", staged_text, target_text), timeout=10.0
        )
        if result is None:
            raise HostServiceError("The host bridge (flatpak-spawn) is unavailable")
        if result.returncode != 0:
            raise HostServiceError(
                "The host refused to replace the launcher file: "
                + self._one_line(result.stderr or result.stdout or f"exit {result.returncode}"),
                exit_code=int(result.returncode),
                stdout=result.stdout,
                stderr=result.stderr,
            )

    def flatpak_runtime(self, app_id: str) -> str:
        """``org.freedesktop.Platform/x86_64/25.08`` for an installed app, or ""."""

        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,200}", str(app_id)):
            raise HostServiceError("Invalid Flatpak application ID")
        result = self._run_fixed("flatpak", ("info", "--show-runtime", str(app_id)), timeout=8.0)
        if result is None or result.returncode != 0:
            return ""
        return self._one_line(result.stdout)

    def flatpak_ref_installed(self, ref: str) -> bool | None:
        """Whether a Flatpak ref (e.g. a Vulkan layer extension) is installed."""

        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,200}//[A-Za-z0-9_.-]{1,40}", str(ref)):
            raise HostServiceError("Invalid Flatpak ref")
        result = self._run_fixed("flatpak", ("info", str(ref)), timeout=8.0)
        if result is None:
            return None
        return result.returncode == 0

    _SANDBOX_PROBE = (
        'for c in "$@"; do command -v "$c" >/dev/null 2>&1 && echo "cmd:$c"; done; '
        'for d in /usr/lib/extensions/vulkan/*/; do [ -d "$d" ] && echo "ext:$(basename "$d")"; done; '
        '[ -d "$HOME/.config/MangoHud" ] && [ -r "$HOME/.config/MangoHud" ] && echo "cfg:MangoHud"; '
        'exit 0'
    )

    def flatpak_app_capabilities(
        self, app_id: str, commands: Sequence[str]
    ) -> dict[str, Any] | None:
        """Which commands and Vulkan layer extensions exist inside another app's sandbox.

        Runs ``flatpak run --command=sh APP -c PROBE`` on the host: the app's
        own program is not started, only a fixed POSIX shell probe.  ``None``
        means the answer is unknown.
        """

        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,200}", str(app_id)):
            raise HostServiceError("Invalid Flatpak application ID")
        names = [str(item) for item in commands]
        if any(not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", name) for name in names):
            raise HostServiceError("Invalid command name")
        key = f"sandbox:{app_id}:{','.join(names)}"
        cached = self._tool_cache.get(key)
        if cached is not None and cached.get("_expires", 0) > datetime.now(UTC).timestamp():
            return cached
        result = self._run_fixed(
            "flatpak",
            ("run", "--command=sh", str(app_id), "-c", self._SANDBOX_PROBE, "sh", *names),
            timeout=20.0,
        )
        if result is None or result.returncode != 0:
            return None
        lines = result.stdout.splitlines()
        payload: dict[str, Any] = {
            "commands": sorted(line[4:] for line in lines if line.startswith("cmd:")),
            "extensions": sorted(line[4:] for line in lines if line.startswith("ext:")),
            # Host config folders readable inside the app's sandbox.
            "configDirs": sorted(line[4:] for line in lines if line.startswith("cfg:")),
            "_expires": datetime.now(UTC).timestamp() + 60.0,
        }
        self._tool_cache[key] = payload
        return payload

    @staticmethod
    def validate_extension_ref(ref: str) -> str:
        """Only whitelisted VulkanLayer refs with an exact arch/branch shape."""
        match = _FLATPAK_EXTENSION_REF.fullmatch(str(ref or ""))
        if match is None or match.group("name") not in FLATPAK_EXTENSION_WHITELIST:
            raise HostServiceError("This Flatpak extension is not allowed")
        return match.group(0)

    @staticmethod
    def _scope_flag(scope: str) -> str:
        if scope not in {"user", "system"}:
            raise HostServiceError("Invalid Flatpak installation scope")
        return f"--{scope}"

    def flatpak_app_installation(self, app_id: str) -> dict[str, str] | None:
        """Scope, ref, origin and runtime of an installed app (read only)."""
        if not _FLATPAK_APP_ID.fullmatch(str(app_id)):
            raise HostServiceError("Invalid Flatpak application ID")
        for scope in ("user", "system"):
            ref = self._run_fixed(
                "flatpak", ("info", f"--{scope}", "--show-ref", str(app_id)), timeout=8.0
            )
            if ref is None:
                return None
            if ref.returncode != 0:
                continue
            origin = self._run_fixed(
                "flatpak", ("info", f"--{scope}", "--show-origin", str(app_id)), timeout=8.0
            )
            runtime = self._run_fixed(
                "flatpak", ("info", f"--{scope}", "--show-runtime", str(app_id)), timeout=8.0
            )
            return {
                "scope": scope,
                "ref": self._one_line(ref.stdout),
                "origin": self._one_line(origin.stdout) if origin and origin.returncode == 0 else "",
                "runtime": self._one_line(runtime.stdout) if runtime and runtime.returncode == 0 else "",
            }
        return None

    def flatpak_runtime_extension_version(self, runtime_ref: str, extension: str) -> str:
        """``version=`` of an extension point in the runtime metadata, or ""."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_]+/[A-Za-z0-9_.-]+", str(runtime_ref)):
            raise HostServiceError("Invalid Flatpak runtime")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,200}", str(extension)):
            raise HostServiceError("Invalid Flatpak extension point")
        name, arch, branch = str(runtime_ref).split("/")
        result = self._run_fixed(
            "flatpak", ("info", "-m", f"runtime/{name}/{arch}/{branch}"), timeout=8.0
        )
        if result is None or result.returncode != 0:
            return ""
        section = False
        for line in result.stdout.splitlines():
            text = line.strip()
            if text.startswith("["):
                section = text == f"[Extension {extension}]"
                continue
            if section and text.replace(" ", "").startswith("version="):
                return text.split("=", 1)[1].strip()
        return ""

    def flatpak_extension_remote_info(self, scope: str, remote: str, ref: str) -> dict[str, str] | None:
        """``remote-info`` for a whitelisted extension; None when unknown."""
        flag = self._scope_flag(scope)
        ref = self.validate_extension_ref(ref)
        if not _FLATPAK_REMOTE.fullmatch(str(remote)):
            raise HostServiceError("Invalid Flatpak remote")
        result = self._run_fixed(
            "flatpak", ("remote-info", flag, str(remote), ref), timeout=60.0
        )
        if result is None:
            return None
        return {
            "returncode": str(result.returncode),
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def flatpak_extension_install_argv(self, scope: str, remote: str, ref: str) -> list[str]:
        """The exact host argv that :meth:`install_flatpak_extension` runs."""
        flag = self._scope_flag(scope)
        ref = self.validate_extension_ref(ref)
        if not _FLATPAK_REMOTE.fullmatch(str(remote)):
            raise HostServiceError("Invalid Flatpak remote")
        return ["flatpak", "install", flag, "--noninteractive", str(remote), ref]

    def install_flatpak_extension(
        self,
        scope: str,
        remote: str,
        ref: str,
        on_line: Callable[[str], None] | None = None,
        *,
        timeout: float = 1800.0,
    ) -> subprocess.CompletedProcess[str]:
        """Install one whitelisted extension (Polkit asks for --system).

        Fixed argv, no shell; output lines are reported as they arrive.
        """
        argv = self.flatpak_extension_install_argv(scope, remote, ref)
        # C locale so failures can be classified (network / Polkit / ref).
        environment = None
        if self.in_flatpak:
            full = [self._flatpak_spawn, "--host", "--env=LC_ALL=C", *argv]
        else:
            executable = self._which("flatpak")
            if not executable:
                raise HostServiceError("flatpak is not available on the host")
            full = [executable, *argv[1:]]
            environment = {**os.environ, "LC_ALL": "C"}
        popen = getattr(self, "_popen", subprocess.Popen)
        try:
            process = popen(
                full,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise HostServiceError(f"flatpak install could not start: {error}") from error
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\r\n")
            # Progress lines rewrite themselves with \r; keep the last part.
            line = line.rsplit("\r", 1)[-1]
            if line:
                lines.append(line)
                if on_line is not None:
                    on_line(line)
            if time.monotonic() > deadline:
                process.kill()
                break
        code = process.wait()
        output = "\n".join(lines)[-_MAX_OUTPUT:]
        return subprocess.CompletedProcess(full, code, output, "")

    def invalidate_app_capabilities(self, app_id: str) -> None:
        """Drop cached sandbox probes of one app (after installing an extension)."""
        prefix = f"sandbox:{app_id}:"
        for key in [key for key in self._tool_cache if str(key).startswith(prefix)]:
            self._tool_cache.pop(key, None)

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
        max_output: int = _MAX_OUTPUT,
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
        result.stdout = str(result.stdout or "")[:min(max_output, 12 * 1024 * 1024)]
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
