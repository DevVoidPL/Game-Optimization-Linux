"""Read-only Linux system and gaming-tool detection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Any

from gameforge.models.enums import CapabilityStatus, SessionType
from gameforge.models.system import SystemInfo


UNKNOWN = "Unknown"
NOT_DETECTED = CapabilityStatus.NOT_DETECTED


class LinuxSystemProvider:
    """Collect host facts without privileges, shell parsing, or writes."""

    def __init__(
        self,
        *,
        os_release_path: Path = Path("/etc/os-release"),
        cpuinfo_path: Path = Path("/proc/cpuinfo"),
        meminfo_path: Path = Path("/proc/meminfo"),
        environment: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 4.0,
        home: Path | None = None,
        host_service: object | None = None,
    ) -> None:
        self._os_release_path = Path(os_release_path)
        self._cpuinfo_path = Path(cpuinfo_path)
        self._meminfo_path = Path(meminfo_path)
        self._environment = os.environ if environment is None else environment
        self._which = which
        self._command_runner = command_runner
        self._timeout_seconds = timeout_seconds
        self._home = Path.home() if home is None else Path(home)
        if host_service is None and str(self._environment.get("FLATPAK_ID", "")).strip():
            try:
                from gameforge.services.host_service import HostServiceClient

                host_service = HostServiceClient(environment=self._environment)
            except Exception:
                host_service = None
        self._host_service = host_service

    def collect(self) -> SystemInfo:
        cpu_name, cpu_cores, cpu_threads = self._cpu_info()
        host = self._host_diagnostics()
        host_gpu = host.get("gpu") if isinstance(host.get("gpu"), Mapping) else {}
        tools = host.get("tools") if isinstance(host.get("tools"), Mapping) else {}
        gpu_name, gpu_driver, vulkan_available = self._gpu_info()
        if host_gpu:
            gpu_name = str(host_gpu.get("model") or gpu_name)
            gpu_driver = str(host_gpu.get("driver") or gpu_driver)
            vulkan_tool = tools.get("vulkan")
            vulkan_available = bool(
                isinstance(vulkan_tool, Mapping)
                and vulkan_tool.get("available") is True
            )
        capabilities, details = self._host_capabilities(tools, vulkan_available)
        steam = tools.get("steam") if isinstance(tools.get("steam"), Mapping) else {}
        return SystemInfo(
            distribution=self._distribution(),
            kernel=platform.release() or UNKNOWN,
            desktop_environment=self._desktop_environment(),
            session_type=self._session_type(),
            cpu=cpu_name,
            cpu_cores=cpu_cores,
            cpu_threads=cpu_threads,
            gpu=gpu_name,
            gpu_driver=gpu_driver,
            ram_gb=self._ram_gb(),
            vram_gb=0.0,
            capabilities=capabilities,
            filesystems=(),
            demo=False,
            capability_details=details,
            gpu_vendor=str(host_gpu.get("vendor") or ""),
            vulkan_device=str(host_gpu.get("vulkan_device") or ""),
            diagnostics_source="host" if host else "local",
            steam_executable_detected=bool(steam.get("available") is True),
            steam_type=str(steam.get("steam_type") or "unavailable"),
            host_launch_available=bool(steam.get("host_launch_available") is True),
        )

    def get_system_info(self) -> SystemInfo:
        return self.collect()

    def _host_diagnostics(self) -> dict[str, Any]:
        diagnose = getattr(self._host_service, "diagnose", None)
        if not callable(diagnose):
            return {}
        try:
            raw = diagnose()
        except Exception:
            return {}
        return (
            dict(raw)
            if isinstance(raw, Mapping) and raw.get("source") == "host"
            else {}
        )

    def _host_capabilities(
        self,
        tools: Mapping[str, Any],
        vulkan_available: bool,
    ) -> tuple[dict[str, CapabilityStatus], dict[str, dict[str, Any]]]:
        labels = {
            "steam": "Steam",
            "flatpak": "Flatpak",
            "gamescope": "Gamescope",
            "gamemode": "GameMode",
            "mangohud": "MangoHud",
            "vulkan": "Vulkan",
            "btrfs-progs": "Btrfs tools",
            "compsize": "compsize",
            "bottles": "Bottles",
            "heroic": "Heroic",
            "lutris": "Lutris",
        }
        if not tools:
            return self._capabilities(vulkan_available), {}
        statuses: dict[str, CapabilityStatus] = {}
        details: dict[str, dict[str, Any]] = {}
        for key, label in labels.items():
            value = tools.get(key)
            row = dict(value) if isinstance(value, Mapping) else {}
            statuses[label] = (
                CapabilityStatus.AVAILABLE
                if row.get("available") is True
                else NOT_DETECTED
            )
            details[label] = row
        statuses["OptiScaler"] = CapabilityStatus.GAME_DEPENDENT
        return statuses, details

    def _distribution(self) -> str:
        values: dict[str, str] = {}
        try:
            lines = self._os_release_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return UNKNOWN
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key.strip()] = value.replace(r"\n", " ").replace(r'\"', '"')
        pretty = values.get("PRETTY_NAME", "").strip()
        if pretty:
            return pretty
        name = values.get("NAME", "").strip()
        version = values.get("VERSION", "").strip()
        return " ".join(part for part in (name, version) if part) or UNKNOWN

    def _desktop_environment(self) -> str:
        value = (
            self._environment.get("XDG_CURRENT_DESKTOP", "").strip()
            or self._environment.get("DESKTOP_SESSION", "").strip()
        )
        if not value:
            return UNKNOWN
        return " / ".join(part for part in value.split(":") if part) or UNKNOWN

    def _session_type(self) -> SessionType:
        declared = self._environment.get("XDG_SESSION_TYPE", "").strip().casefold()
        if declared == "wayland" or self._environment.get("WAYLAND_DISPLAY", "").strip():
            return SessionType.WAYLAND
        if declared in {"x11", "xorg"} or self._environment.get("DISPLAY", "").strip():
            return SessionType.X11
        return SessionType.UNKNOWN

    def _cpu_info(self) -> tuple[str, int, int]:
        try:
            text = self._cpuinfo_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        records = self._colon_records(text)
        name = next(
            (
                record[key].strip()
                for record in records
                for key in ("model name", "hardware", "processor")
                if key in record and record[key].strip() and not record[key].strip().isdigit()
            ),
            "",
        )
        threads = sum("processor" in record for record in records)
        core_pairs = {
            (record["physical id"], record["core id"])
            for record in records
            if "physical id" in record and "core id" in record
        }
        cores = len(core_pairs)
        if not cores:
            core_counts = [
                self._positive_int(record.get("cpu cores")) for record in records
            ]
            cores = max((value for value in core_counts if value), default=0)

        lscpu = self._run(("lscpu",)) if self._which("lscpu") else None
        if lscpu is not None and lscpu.returncode == 0:
            values = self._key_value_lines(lscpu.stdout)
            name = name or values.get("model name", "").strip()
            threads = threads or self._positive_int(values.get("cpu(s)"))
            if not cores:
                per_socket = self._positive_int(values.get("core(s) per socket"))
                sockets = self._positive_int(values.get("socket(s)"))
                if per_socket:
                    cores = per_socket * (sockets or 1)

        threads = threads or (os.cpu_count() or 0)
        cores = cores or threads
        return name or UNKNOWN, cores, threads

    def _ram_gb(self) -> float:
        try:
            text = self._meminfo_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0.0
        match = re.search(r"^MemTotal:\s*(\d+)\s+kB\b", text, re.MULTILINE)
        if match is None:
            return 0.0
        return round(int(match.group(1)) / (1024 * 1024), 1)

    def _gpu_info(self) -> tuple[str, str, bool]:
        vulkan_available = False
        if self._which("vulkaninfo"):
            result = self._run(("vulkaninfo", "--summary"))
            if result is not None and result.returncode == 0:
                vulkan_available = True
                name = self._match_value(
                    result.stdout,
                    (r"^\s*deviceName\s*=\s*(.+)$", r"^\s*GPU\d+\s*:\s*(.+)$"),
                )
                driver = self._match_value(
                    result.stdout,
                    (
                        r"^\s*driverName\s*=\s*(.+)$",
                        r"^\s*driverInfo\s*=\s*(.+)$",
                        r"^\s*driverVersion\s*=\s*(.+)$",
                    ),
                )
                if name:
                    return name, driver or UNKNOWN, vulkan_available

        if self._which("glxinfo"):
            result = self._run(("glxinfo", "-B"))
            if result is not None and result.returncode == 0:
                name = self._match_value(
                    result.stdout,
                    (
                        r"^\s*Device:\s*(.+?)(?:\s*\([^)]*\))?\s*$",
                        r"^\s*OpenGL renderer string:\s*(.+)$",
                    ),
                )
                driver = self._match_value(
                    result.stdout,
                    (
                        r"^\s*OpenGL version string:\s*(.+)$",
                        r"^\s*OpenGL core profile version string:\s*(.+)$",
                    ),
                )
                if name:
                    return name, driver or UNKNOWN, vulkan_available

        if self._which("lspci"):
            result = self._run(("lspci", "-nnk"))
            if result is not None and result.returncode == 0:
                name, driver = self._lspci_gpu(result.stdout)
                if name:
                    return name, driver or UNKNOWN, vulkan_available
        return UNKNOWN, UNKNOWN, vulkan_available

    def _capabilities(self, vulkan_available: bool) -> dict[str, CapabilityStatus]:
        capability_commands = {
            "GameMode": "gamemoderun",
            "Gamescope": "gamescope",
            "MangoHud": "mangohud",
            "Btrfs tools": "btrfs",
            "compsize": "compsize",
            "Flatpak": "flatpak",
        }
        result = {
            label: self._command_status(command)
            for label, command in capability_commands.items()
        }
        steam_available = bool(self._which("steam")) or self._flatpak_app_present(
            "com.valvesoftware.Steam"
        )
        result.update(
            {
                "Steam": (
                    CapabilityStatus.AVAILABLE if steam_available else NOT_DETECTED
                ),
                "Vulkan": (
                    CapabilityStatus.AVAILABLE if vulkan_available else NOT_DETECTED
                ),
                "Heroic": self._application_status(
                    ("heroic", "heroic-games-launcher"),
                    "com.heroicgameslauncher.hgl",
                ),
                "Lutris": self._application_status(("lutris",), "net.lutris.Lutris"),
                "Bottles": self._application_status(
                    ("bottles",), "com.usebottles.bottles"
                ),
                "OptiScaler": CapabilityStatus.GAME_DEPENDENT,
            }
        )
        return result

    def _application_status(
        self, commands: Sequence[str], flatpak_app_id: str
    ) -> CapabilityStatus:
        if any(self._which(command) for command in commands):
            return CapabilityStatus.AVAILABLE
        if self._flatpak_app_present(flatpak_app_id):
            return CapabilityStatus.AVAILABLE
        return NOT_DETECTED

    def _flatpak_app_present(self, app_id: str) -> bool:
        if not self._which("flatpak"):
            return False
        candidates = (
            self._home / ".local" / "share" / "flatpak" / "app" / app_id,
            self._home / ".var" / "app" / app_id,
            Path("/var/lib/flatpak/app") / app_id,
        )
        return any(candidate.is_dir() for candidate in candidates)

    def _command_status(self, command: str) -> CapabilityStatus:
        return CapabilityStatus.AVAILABLE if self._which(command) else NOT_DETECTED

    def _run(
        self, arguments: Sequence[str]
    ) -> subprocess.CompletedProcess[str] | None:
        try:
            return self._command_runner(
                list(arguments),
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _colon_records(text: str) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        for section in re.split(r"\n\s*\n", text.strip()):
            record = LinuxSystemProvider._key_value_lines(section)
            if record:
                records.append(record)
        return records

    @staticmethod
    def _key_value_lines(text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip().casefold()] = value.strip()
        return values

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0

    @staticmethod
    def _match_value(text: str, patterns: Sequence[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            if match is not None and match.group(1).strip():
                return match.group(1).strip()
        return ""

    @staticmethod
    def _lspci_gpu(text: str) -> tuple[str, str]:
        name = ""
        driver = ""
        in_gpu_block = False
        for line in text.splitlines():
            if line and not line[0].isspace():
                match = re.match(
                    r"^.+?(?:VGA compatible controller|3D controller|Display controller):\s*(.+)$",
                    line,
                    re.IGNORECASE,
                )
                in_gpu_block = match is not None
                if in_gpu_block:
                    name = match.group(1).strip()
                elif name:
                    break
                continue
            if in_gpu_block:
                match = re.match(
                    r"^\s*Kernel driver in use:\s*(.+)$", line, re.IGNORECASE
                )
                if match is not None:
                    driver = match.group(1).strip()
        return name, driver


__all__ = ["LinuxSystemProvider", "NOT_DETECTED", "UNKNOWN"]
