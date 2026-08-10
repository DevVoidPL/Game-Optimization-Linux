from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from game_optimization_linux.models import CapabilityStatus, SessionType
from game_optimization_linux.providers import LinuxSystemProvider


def _completed(arguments: list[str], output: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, returncode, output, "")


def test_linux_system_provider_reads_real_sources_and_mocked_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os_release = tmp_path / "os-release"
    cpuinfo = tmp_path / "cpuinfo"
    meminfo = tmp_path / "meminfo"
    os_release.write_text(
        'NAME="Example Linux"\nVERSION="42"\nPRETTY_NAME="Example Linux 42"\n',
        encoding="utf-8",
    )
    cpuinfo.write_text(
        "processor : 0\nmodel name : Test CPU 9000\nphysical id : 0\ncore id : 0\n\n"
        "processor : 1\nmodel name : Test CPU 9000\nphysical id : 0\ncore id : 1\n",
        encoding="utf-8",
    )
    meminfo.write_text("MemTotal:       16777216 kB\n", encoding="utf-8")
    executables = {
        "vulkaninfo": "/usr/bin/vulkaninfo",
        "gamemoderun": "/usr/bin/gamemoderun",
        "gamescope": "/usr/bin/gamescope",
        "mangohud": "/usr/bin/mangohud",
        "btrfs": "/usr/bin/btrfs",
        "compsize": "/usr/bin/compsize",
        "flatpak": "/usr/bin/flatpak",
        "steam": "/usr/bin/steam",
    }
    calls: list[list[str]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        assert "shell" not in kwargs
        assert kwargs["check"] is False
        return _completed(
            arguments,
            "GPU0:\n"
            "    deviceName = Example GPU\n"
            "    driverName = Example Vulkan Driver\n",
        )

    monkeypatch.setattr(
        "game_optimization_linux.providers.linux_system.platform.release", lambda: "6.99-test"
    )
    provider = LinuxSystemProvider(
        os_release_path=os_release,
        cpuinfo_path=cpuinfo,
        meminfo_path=meminfo,
        environment={"XDG_CURRENT_DESKTOP": "KDE:Plasma", "XDG_SESSION_TYPE": "wayland"},
        which=lambda name: executables.get(name),
        command_runner=runner,
        home=tmp_path,
    )

    info = provider.collect()

    assert info.distribution == "Example Linux 42"
    assert info.kernel == "6.99-test"
    assert info.desktop_environment == "KDE / Plasma"
    assert info.session_type is SessionType.WAYLAND
    assert info.cpu == "Test CPU 9000"
    assert (info.cpu_cores, info.cpu_threads) == (2, 2)
    assert info.ram_gb == 16.0
    assert info.gpu == "Example GPU"
    assert info.gpu_driver == "Example Vulkan Driver"
    assert info.demo is False
    assert calls == [["vulkaninfo", "--summary"]]
    for capability in (
        "MangoHud",
        "GameMode",
        "Gamescope",
        "Btrfs tools",
        "compsize",
        "Flatpak",
        "Steam",
        "Vulkan",
    ):
        assert info.capabilities[capability] is CapabilityStatus.AVAILABLE
    assert info.capabilities["OptiScaler"] is CapabilityStatus.GAME_DEPENDENT


def test_linux_system_provider_uses_glxinfo_gpu_fallback(tmp_path: Path) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor : 0\nmodel name : CPU\ncpu cores : 1\n", encoding="utf-8")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1048576 kB\n", encoding="utf-8")
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Fallback OS"\n', encoding="utf-8")
    calls: list[list[str]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return _completed(
            arguments,
            "Device: Fallback GPU (0x1234)\nOpenGL version string: Mesa 26.1\n",
        )

    provider = LinuxSystemProvider(
        os_release_path=os_release,
        cpuinfo_path=cpuinfo,
        meminfo_path=meminfo,
        environment={"DISPLAY": ":1"},
        which=lambda name: f"/usr/bin/{name}" if name in {"glxinfo", "lspci"} else None,
        command_runner=runner,
        home=tmp_path,
    )

    info = provider.collect()

    assert info.session_type is SessionType.X11
    assert info.gpu == "Fallback GPU"
    assert info.gpu_driver == "Mesa 26.1"
    assert calls == [["glxinfo", "-B"]]
    assert info.capabilities["Vulkan"] is CapabilityStatus.NOT_DETECTED


def test_linux_system_provider_reports_missing_launchers_without_demo_data(
    tmp_path: Path,
) -> None:
    provider = LinuxSystemProvider(
        os_release_path=tmp_path / "missing-os-release",
        cpuinfo_path=tmp_path / "missing-cpuinfo",
        meminfo_path=tmp_path / "missing-meminfo",
        environment={},
        which=lambda _name: None,
        home=tmp_path,
    )

    info = provider.collect()

    assert info.distribution == "Unknown"
    assert info.cpu == "Unknown"
    assert info.gpu == "Unknown"
    for launcher in ("Heroic", "Lutris", "Bottles"):
        assert info.capabilities[launcher] is CapabilityStatus.NOT_DETECTED
    assert "Ryzen 7 7800X3D" not in str(info.to_dict())


def test_linux_system_provider_lspci_driver_belongs_to_gpu(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    cpuinfo = tmp_path / "cpuinfo"
    meminfo = tmp_path / "meminfo"
    os_release.write_text('NAME="PCI Linux"\n', encoding="utf-8")
    cpuinfo.write_text("processor : 0\nmodel name : CPU\n", encoding="utf-8")
    meminfo.write_text("MemTotal: 1048576 kB\n", encoding="utf-8")

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(
            arguments,
            "00:00.0 Host bridge: Example Host\n"
            "\tKernel driver in use: host-driver\n"
            "03:00.0 VGA compatible controller: Example PCI GPU [1234:5678]\n"
            "\tSubsystem: Example Board\n"
            "\tKernel driver in use: gpu-driver\n"
            "04:00.0 Audio device: Example Audio\n",
        )

    provider = LinuxSystemProvider(
        os_release_path=os_release,
        cpuinfo_path=cpuinfo,
        meminfo_path=meminfo,
        environment={},
        which=lambda name: "/usr/bin/lspci" if name == "lspci" else None,
        command_runner=runner,
        home=tmp_path,
    )

    info = provider.collect()

    assert info.gpu == "Example PCI GPU [1234:5678]"
    assert info.gpu_driver == "gpu-driver"


def test_linux_system_provider_reads_eight_gib_vram_from_drm(tmp_path: Path) -> None:
    drm = tmp_path / "drm"
    vram = drm / "card1" / "device" / "mem_info_vram_total"
    vram.parent.mkdir(parents=True)
    vram.write_text(str(8 * 1024**3) + "\n", encoding="utf-8")
    provider = LinuxSystemProvider(
        os_release_path=tmp_path / "missing-os-release",
        cpuinfo_path=tmp_path / "missing-cpuinfo",
        meminfo_path=tmp_path / "missing-meminfo",
        drm_path=drm,
        environment={},
        which=lambda _name: None,
        home=tmp_path,
    )

    info = provider.collect()

    assert info.vram_gb == 8.0
    assert f"{info.vram_gb:.1f} GiB" == "8.0 GiB"
