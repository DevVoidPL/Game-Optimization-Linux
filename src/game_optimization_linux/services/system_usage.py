"""Real CPU/GPU/RAM usage for the Couch header (no invented values).

* CPU %: difference between two ``/proc/stat`` samples (first sample: hidden).
* RAM: ``MemTotal - MemAvailable`` from ``/proc/meminfo``.
* GPU %: AMD ``/sys/class/drm/card*/device/gpu_busy_percent``; NVIDIA through
  NVML only when ``libnvidia-ml`` can be loaded (no ``nvidia-smi`` process).
  Intel or no source: GPU is hidden.

A metric that cannot be read is ``None`` and the UI hides it; it is never
reported as 0 %.
"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from dataclasses import dataclass
import logging
import math
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_MS = 2000
_AMD_VENDOR = "0x1002"


@dataclass(frozen=True, slots=True)
class SystemUsage:
    cpu_percent: int | None = None
    gpu_percent: int | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None


class CpuSampler:
    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self._stat = Path(proc_root) / "stat"
        self._previous: tuple[int, int] | None = None

    def reset(self) -> None:
        self._previous = None

    def sample(self) -> int | None:
        try:
            first = self._stat.read_text(encoding="ascii").splitlines()[0].split()
        except (OSError, IndexError, UnicodeDecodeError):
            self._previous = None
            return None
        if not first or first[0] != "cpu" or len(first) < 5:
            return None
        try:
            values = [int(value) for value in first[1:]]
        except ValueError:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)   # idle + iowait
        total = sum(values[:8])                                    # without guest
        previous, self._previous = self._previous, (idle, total)
        if previous is None:
            return None
        total_delta = total - previous[1]
        if total_delta <= 0:
            return None
        busy = total_delta - (idle - previous[0])
        return max(0, min(100, round(100.0 * busy / total_delta)))


def read_memory(proc_root: Path = Path("/proc")) -> tuple[int, int] | None:
    """(used, total) bytes, or None when /proc/meminfo is unreadable."""

    values: dict[str, int] = {}
    try:
        lines = (Path(proc_root) / "meminfo").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        name, _, rest = line.partition(":")
        parts = rest.split()
        if name in {"MemTotal", "MemAvailable"} and parts and parts[0].isdigit():
            values[name] = int(parts[0]) * 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if not total or available is None or available > total:
        return None
    return total - available, total


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class GpuSampler:
    """Detect one readable GPU busy source once; None when there is none."""

    def __init__(
        self,
        drm_root: Path = Path("/sys/class/drm"),
        nvml_loader: Callable[[], object | None] | None = None,
    ) -> None:
        self._drm_root = Path(drm_root)
        self._nvml_loader = nvml_loader or self._load_nvml
        self._detected = False
        self._amd_files: tuple[Path, ...] = ()
        self._nvml: object | None = None
        self._nvml_handle: ctypes.c_void_p | None = None
        self.source = ""

    def detect(self) -> str:
        if self._detected:
            return self.source
        self._detected = True
        files = []
        try:
            cards = sorted(self._drm_root.glob("card[0-9]*"))
        except OSError:
            cards = []
        for card in cards:
            if "-" in card.name:            # connectors (card1-DP-1), not devices
                continue
            device = card / "device"
            try:
                vendor = (device / "vendor").read_text(encoding="ascii").strip()
            except OSError:
                continue
            busy = device / "gpu_busy_percent"
            if vendor == _AMD_VENDOR and self._read_int(busy) is not None:
                files.append(busy)
        if files:
            self._amd_files = tuple(files)
            self.source = "amdgpu sysfs " + ", ".join(str(path) for path in files)
        else:
            self._init_nvml()
        logger.info("System usage: GPU source: %s", self.source or "none (GPU hidden)")
        return self.source

    def sample(self) -> int | None:
        self.detect()
        if self._amd_files:
            readings = [self._read_int(path) for path in self._amd_files]
            valid = [value for value in readings if value is not None]
            return max(0, min(100, max(valid))) if valid else None
        if self._nvml is not None and self._nvml_handle is not None:
            utilization = _NvmlUtilization()
            try:
                status = self._nvml.nvmlDeviceGetUtilizationRates(
                    self._nvml_handle, ctypes.byref(utilization)
                )
            except Exception:
                return None
            return max(0, min(100, int(utilization.gpu))) if status == 0 else None
        return None

    @staticmethod
    def _read_int(path: Path) -> int | None:
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _load_nvml() -> object | None:
        for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                return ctypes.CDLL(name)
            except OSError:
                continue
        return None

    def _init_nvml(self) -> None:
        library = self._nvml_loader()
        if library is None:
            return
        try:
            init = getattr(library, "nvmlInit_v2", None) or library.nvmlInit
            if init() != 0:
                return
            handle = ctypes.c_void_p()
            get_handle = (
                getattr(library, "nvmlDeviceGetHandleByIndex_v2", None)
                or library.nvmlDeviceGetHandleByIndex
            )
            if get_handle(0, ctypes.byref(handle)) != 0:
                return
        except Exception as error:
            logger.info("System usage: NVML unavailable: %s", error)
            return
        self._nvml, self._nvml_handle = library, handle
        self.source = "NVIDIA NVML"


class SystemUsageMonitor(QObject):
    """Sample every 2 s only while enabled and the Couch UI is visible."""

    changed = Signal()

    def __init__(
        self,
        *,
        cpu: CpuSampler | None = None,
        gpu: GpuSampler | None = None,
        memory_reader: Callable[[], tuple[int, int] | None] = read_memory,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cpu = cpu or CpuSampler()
        self._gpu = gpu or GpuSampler()
        self._memory_reader = memory_reader
        self._enabled = True
        self._visible = False
        self.usage = SystemUsage()
        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        self._timer.timeout.connect(self.sample_now)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._update_timer()

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._update_timer()

    def stop(self) -> None:
        self._timer.stop()

    def _update_timer(self) -> None:
        should_run = self._enabled and self._visible
        if should_run and not self._timer.isActive():
            self._cpu.reset()               # no delta across a pause
            self._timer.start()
            self.sample_now()               # CPU baseline, RAM/GPU at once
        elif not should_run and self._timer.isActive():
            self._timer.stop()
            self.usage = SystemUsage()
            self.changed.emit()

    def sample_now(self) -> None:
        memory = self._memory_reader()
        self.usage = SystemUsage(
            cpu_percent=self._cpu.sample(),
            gpu_percent=self._gpu.sample(),
            memory_used_bytes=memory[0] if memory else None,
            memory_total_bytes=memory[1] if memory else None,
        )
        self.changed.emit()


def format_memory(used: int, total: int, *, decimal_comma: bool) -> str:
    """ "6,2 / 16 GB": used with one decimal; the total is rounded up to whole
    GiB because MemTotal excludes kernel-reserved memory (32 GB -> 31.2)."""

    gib = 1024**3
    text = f"{used / gib:.1f} / {math.ceil(total / gib - 1e-9)} GB"
    return text.replace(".", ",") if decimal_comma else text


__all__ = [
    "CpuSampler",
    "GpuSampler",
    "SAMPLE_INTERVAL_MS",
    "SystemUsage",
    "SystemUsageMonitor",
    "format_memory",
    "read_memory",
]
