"""Couch CPU/GPU/RAM indicator: real sources only, hidden when unreadable."""

from __future__ import annotations

import ctypes
from pathlib import Path

from game_optimization_linux.controllers import AppController
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore
from game_optimization_linux.services.system_usage import (
    CpuSampler,
    GpuSampler,
    SystemUsageMonitor,
    format_memory,
    read_memory,
)


def _stat(root: Path, user: int, idle: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "stat").write_text(f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\n")


def test_cpu_percent_comes_from_the_difference_of_two_samples(tmp_path: Path) -> None:
    _stat(tmp_path, 100, 900)
    sampler = CpuSampler(tmp_path)
    assert sampler.sample() is None                  # no delta yet: hidden, not 0 %
    _stat(tmp_path, 130, 970)                        # +30 busy, +70 idle
    assert sampler.sample() == 30
    assert CpuSampler(tmp_path / "missing").sample() is None


def test_memory_is_total_minus_available(tmp_path: Path) -> None:
    (tmp_path / "meminfo").write_text(
        "MemTotal:       16777216 kB\nMemFree:  1 kB\nMemAvailable:   10276044 kB\n"
    )
    used, total = read_memory(tmp_path)
    assert total == 16777216 * 1024 and used == (16777216 - 10276044) * 1024
    assert format_memory(used, total, decimal_comma=True) == "6,2 / 16 GB"
    assert format_memory(0, 32758804 * 1024, decimal_comma=True) == "0,0 / 32 GB"
    assert format_memory(used, total, decimal_comma=False) == "6.2 / 16 GB"
    (tmp_path / "meminfo").write_text("MemTotal: 1024 kB\n")      # no MemAvailable
    assert read_memory(tmp_path) is None


def _card(root: Path, name: str, vendor: str, busy: str | None) -> None:
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor + "\n")
    if busy is not None:
        (device / "gpu_busy_percent").write_text(busy + "\n")


def test_gpu_amd_sysfs_is_used_and_intel_or_nothing_hides_gpu(tmp_path: Path) -> None:
    amd = tmp_path / "amd"
    _card(amd, "card1", "0x1002", "37")
    (amd / "card1-DP-1").mkdir()                     # connector: ignored
    sampler = GpuSampler(amd, nvml_loader=lambda: None)
    assert sampler.sample() == 37 and "card1/device/gpu_busy_percent" in sampler.source

    intel = tmp_path / "intel"
    _card(intel, "card0", "0x8086", None)
    hidden = GpuSampler(intel, nvml_loader=lambda: None)
    assert hidden.sample() is None and hidden.source == ""


def test_gpu_nvml_only_when_the_library_loads(tmp_path: Path) -> None:
    class _Nvml:
        def nvmlInit_v2(self):
            return 0

        def nvmlDeviceGetHandleByIndex_v2(self, index, handle):
            return 0

        def nvmlDeviceGetUtilizationRates(self, handle, reference):
            reference._obj.gpu = 64
            return 0

    sampler = GpuSampler(tmp_path / "none", nvml_loader=_Nvml)
    assert sampler.sample() == 64 and sampler.source == "NVIDIA NVML"
    assert isinstance(sampler._nvml_handle, ctypes.c_void_p)


def test_monitor_runs_only_while_enabled_and_couch_visible() -> None:
    reads: list[int] = []
    monitor = SystemUsageMonitor(
        cpu=CpuSampler(Path("/nonexistent")),
        gpu=GpuSampler(Path("/nonexistent"), nvml_loader=lambda: None),
        memory_reader=lambda: (reads.append(1) or (2 * 1024**3, 8 * 1024**3)),
    )
    assert not monitor.running
    monitor.set_visible(True)
    assert monitor.running and reads == [1]          # immediate first sample
    assert monitor.usage.cpu_percent is None and monitor.usage.gpu_percent is None
    monitor.set_enabled(False)
    assert not monitor.running and monitor.usage.memory_total_bytes is None
    monitor.set_enabled(True)
    monitor.set_visible(False)
    assert not monitor.running
    monitor.stop()


def test_controller_exposes_only_read_metrics_and_persists_the_toggle(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    controller = AppController(
        game_provider=DemoGameProvider(), task_service=MockTaskService(),
        settings_store=store, auto_refresh=False,
    )
    try:
        monitor = controller._system_usage
        monitor._cpu = CpuSampler(Path("/nonexistent"))
        monitor._gpu = GpuSampler(Path("/nonexistent"), nvml_loader=lambda: None)
        monitor._memory_reader = lambda: (int(6.2 * 1024**3), 16 * 1024**3)
        assert controller.settings["couchShowSystemUsage"] is True     # default on
        controller.setCouchVisible(True)
        assert monitor.running
        usage = controller.systemUsage
        assert "cpuPercent" not in usage and "gpuPercent" not in usage  # unreadable: absent
        assert usage["memoryText"] in {"6.2 / 16 GB", "6,2 / 16 GB"}
        assert controller.saveSetting("couchShowSystemUsage", False)
        assert not monitor.running
        assert SettingsStore(tmp_path / "settings.json").load().couch_show_system_usage is False
        controller.setCouchVisible(False)
    finally:
        controller.shutdown()


def test_couch_settings_row_is_a_pad_toggle_with_a_reset_default() -> None:
    source = Path("src/game_optimization_linux/qml/couch/CouchSettings.qml").read_text(encoding="utf-8")
    assert '"id": "system-usage"' in source and 'qsTr("Show CPU/GPU/RAM usage")' in source
    assert '"fullscreen", "system-usage",' in source          # bool row: A / left / right toggle
    assert 'saveAdjusted("couchShowSystemUsage", setting("couchShowSystemUsage", true) !== true)' in source
    assert 'controller.saveSetting("couchShowSystemUsage", true)' in source   # reset default on
