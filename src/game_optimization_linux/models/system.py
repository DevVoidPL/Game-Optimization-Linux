"""Platform-neutral system capability models."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

from .enums import CapabilityStatus, FilesystemType, SessionType


@dataclass(frozen=True, slots=True)
class FilesystemInfo:
    mount_point: Path
    filesystem: FilesystemType
    compression_supported: bool
    label: str = ""
    device: str | None = None
    mount_options: tuple[str, ...] = ()
    writable: bool | None = None
    filesystem_name: str = ""
    size_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.size_bytes, "size_bytes"),
            (self.used_bytes, "used_bytes"),
            (self.available_bytes, "available_bytes"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mount_point": str(self.mount_point),
            "filesystem": self.filesystem.value,
            "compression_supported": self.compression_supported,
            "label": self.label,
            "device": self.device,
            "mount_options": list(self.mount_options),
            "writable": self.writable,
            "filesystem_name": self.filesystem_name or self.filesystem.value,
            "size_bytes": self.size_bytes,
            "used_bytes": self.used_bytes,
            "available_bytes": self.available_bytes,
        }


@dataclass(frozen=True, slots=True)
class SystemInfo:
    distribution: str
    kernel: str
    desktop_environment: str
    session_type: SessionType
    cpu: str
    gpu: str
    ram_gb: float
    vram_gb: float
    filesystems: tuple[FilesystemInfo, ...] = ()
    capabilities: dict[str, CapabilityStatus] = field(default_factory=dict)
    cpu_cores: int = 0
    cpu_threads: int = 0
    gpu_driver: str = "Unknown"
    demo: bool = False
    capability_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    gpu_vendor: str = ""
    vulkan_device: str = ""
    diagnostics_source: str = "local"
    steam_library_detected: bool = False
    steam_executable_detected: bool = False
    steam_type: str = "unavailable"
    host_launch_available: bool = False

    def __post_init__(self) -> None:
        memory_sizes = (self.ram_gb, self.vram_gb)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0
            for value in memory_sizes
        ):
            raise ValueError("memory sizes must be finite non-negative numbers")
        for value, name in (
            (self.cpu_cores, "cpu_cores"),
            (self.cpu_threads, "cpu_threads"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "distribution": self.distribution,
            "kernel": self.kernel,
            "desktop_environment": self.desktop_environment,
            "session_type": self.session_type.value,
            "cpu": self.cpu,
            "gpu": self.gpu,
            "ram_gb": self.ram_gb,
            "vram_gb": self.vram_gb,
            "filesystems": [filesystem.to_dict() for filesystem in self.filesystems],
            "capabilities": {
                name: status.value for name, status in self.capabilities.items()
            },
            "cpu_cores": self.cpu_cores,
            "cpu_threads": self.cpu_threads,
            "gpu_driver": self.gpu_driver,
            "demo": self.demo,
            "capability_details": {
                name: dict(details)
                for name, details in self.capability_details.items()
            },
            "gpu_vendor": self.gpu_vendor,
            "vulkan_device": self.vulkan_device,
            "diagnostics_source": self.diagnostics_source,
            "steam_library_detected": self.steam_library_detected,
            "steam_executable_detected": self.steam_executable_detected,
            "steam_type": self.steam_type,
            "host_launch_available": self.host_launch_available,
        }
