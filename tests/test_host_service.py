from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from game_optimization_linux.models import FilesystemInfo, FilesystemType, Game, Launcher
from game_optimization_linux.providers import LinuxSystemProvider
from game_optimization_linux.services import (
    AnalysisLimits,
    BtrfsAnalysisTaskService,
    BtrfsCompressionAnalyzer,
    HostServiceClient,
    HostServiceError,
)


def _game(tmp_path: Path) -> Game:
    library = tmp_path / "SteamLibrary"
    game_path = library / "steamapps" / "common" / "Fixture"
    game_path.mkdir(parents=True)
    (game_path / "data.bin").write_bytes(b"x" * 4096)
    (library / "steamapps" / "appmanifest_4242.acf").write_text(
        '"AppState"\n{\n"appid" "4242"\n"buildid" "100"\n'
        '"installdir" "Fixture"\n}\n',
        encoding="utf-8",
    )
    return Game(
        id="steam-4242",
        name="Fixture",
        launcher=Launcher.STEAM,
        install_path=game_path,
        logical_size_gb=0.0,
        physical_size_gb=0.0,
        filesystem=FilesystemType.BTRFS,
        filesystem_name="btrfs",
        compression_available=True,
        steam_app_id="4242",
        steam_build_id="100",
        library_path=library,
    )


def _measurement_payload(game: Game) -> dict[str, object]:
    return {
        "schema_version": 1,
        "host_service_version": 1,
        "ok": True,
        "measurement_source": "polkit_helper",
        "measurement_error": None,
        "app_id": "4242",
        "build_id": "100",
        "game_path": str(game.install_path),
        "logical_bytes": 4096,
        "measured_at": "2026-08-06T10:00:00+00:00",
        "compsize": {
            "disk_usage_bytes": 2048,
            "uncompressed_bytes": 4096,
            "referenced_bytes": 4096,
            "compression_types": {"zstd": 2048},
        },
        "btrfs_filesystem_du": {
            "total_bytes": 4096,
            "exclusive_bytes": 4096,
            "set_shared_bytes": 0,
            "state": "not_detected",
        },
        "read_only": True,
    }


def test_sandbox_diagnostics_uses_fixed_path_probes_without_host_python() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, dict(kwargs)))
        command = argv[2]
        if command == "/usr/libexec/game-optimization-linux-measure-helper":
            return subprocess.CompletedProcess(argv, 127, "", "No such file or directory")
        if command == "flatpak" and "info" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "not installed")
        if command == "gamescope" and "--help" in argv:
            return subprocess.CompletedProcess(argv, 0, "-w -h -W -H -r -S -F -f -b", "")
        if command == "gamemoded" and "--status" in argv:
            return subprocess.CompletedProcess(argv, 0, "inactive", "")
        if command == "lspci":
            return subprocess.CompletedProcess(
                argv, 0,
                "01:00.0 VGA compatible controller: AMD Radeon\n"
                "\tKernel driver in use: amdgpu\n", "",
            )
        return subprocess.CompletedProcess(argv, 0, "version 1", "")

    client = HostServiceClient(
        flatpak_spawn="flatpak-spawn",
        environment={"FLATPAK_ID": "io.github.DevVoidPL.GameOptimizationLinux"},
        command_runner=runner,
        which=lambda _name: None,
    )
    diagnostics = client.diagnose()
    assert diagnostics["source"] == "host"
    assert diagnostics["transport"] == "flatpak-spawn"
    assert diagnostics["tools"]["gamescope"]["available"] is True
    assert diagnostics["gpu"]["driver"] == "amdgpu"
    assert calls
    assert all(call[0][:2] == ["flatpak-spawn", "--host"] for call in calls)
    assert all("python" not in " ".join(call[0]).casefold() for call in calls)
    assert all(call[1]["shell"] is False for call in calls)


def test_each_missing_or_broken_host_tool_has_an_independent_result() -> None:
    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = argv[2]
        if command in {"mangohud", "gamemoderun", "gamemoded"}:
            return subprocess.CompletedProcess(argv, 127, "", "command not found")
        if command == "gamescope":
            raise subprocess.TimeoutExpired(argv, 1)
        if command == "/usr/libexec/game-optimization-linux-measure-helper":
            return subprocess.CompletedProcess(argv, 127, "", "not found")
        if command == "flatpak" and "info" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "not installed")
        return subprocess.CompletedProcess(argv, 0, "v1", "")

    client = HostServiceClient(
        flatpak_spawn="flatpak-spawn",
        environment={"FLATPAK_ID": "app"},
        command_runner=runner,
        which=lambda _name: None,
    )
    diagnostics = client.diagnose()
    assert diagnostics["tools"]["mangohud"]["status"] == "unavailable"
    assert diagnostics["tools"]["gamemode"]["status"] == "unavailable"
    assert diagnostics["tools"]["gamescope"]["status"] == "error"
    assert diagnostics["tools"]["btrfs-progs"]["available"] is True
    assert diagnostics["measurement_component_available"] is False


def test_host_has_no_generic_command_api() -> None:
    client = HostServiceClient()
    assert not hasattr(client, "run_command")
    with pytest.raises(HostServiceError, match="Unsupported"):
        client.tool_info("/bin/sh")
    assert not hasattr(client, "execute")
    assert not hasattr(client, "shell")


class _Filesystem:
    def inspect(self, path: Path) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=path,
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            writable=True,
            filesystem_name="btrfs",
        )


class _HostMeasurement:
    def __init__(self, game: Game, *, fail: bool = False) -> None:
        self.game = game
        self.fail = fail

    def analysis(self, requested: Game) -> dict[str, object]:
        assert requested.id == self.game.id
        if self.fail:
            raise HostServiceError("compsize is unavailable on the host")
        return _measurement_payload(self.game)


def _analyzer(game: Game, *, fail: bool = False) -> BtrfsCompressionAnalyzer:
    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "du" in argv:
            return subprocess.CompletedProcess(
                argv, 0, "4096 4096 0 " + str(game.install_path) + "\n", ""
            )
        return subprocess.CompletedProcess(argv, 127, "", "not installed")

    return BtrfsCompressionAnalyzer(
        _Filesystem(),
        limits=AnalysisLimits(
            max_sample_bytes=4096,
            max_bytes_per_file=4096,
            timeout_seconds=2,
            command_timeout_seconds=1,
        ),
        compressor=lambda data, level: data,
        process_detector=lambda path, cancelled: (),
        host_service=_HostMeasurement(game, fail=fail),
        executable_finder=lambda name: "btrfs" if name == "btrfs" else None,
        command_runner=run,
    )


def test_host_compsize_unlocks_analysis_and_records_success(tmp_path: Path) -> None:
    game = _game(tmp_path)
    service = BtrfsAnalysisTaskService(analyzer=_analyzer(game), max_workers=1)
    task = service.wait_for(service.enqueue_analysis(game).id, timeout=2)
    assert task.status.value == "completed"
    assert task.metadata["outcome"] == "completed_success"
    assert task.result is not None
    assert task.result["compsize"]["disk_usage_bytes"] == 2048
    assert task.result["profiles_unlocked"] is True
    service.shutdown()


def test_missing_host_measurement_completes_with_warning_and_unlocks_btrfs(tmp_path: Path) -> None:
    game = _game(tmp_path)
    service = BtrfsAnalysisTaskService(
        analyzer=_analyzer(game, fail=True), max_workers=1
    )
    task = service.wait_for(service.enqueue_analysis(game).id, timeout=2)
    assert task.status.value == "completed"
    assert task.metadata["outcome"] == "completed_warning"
    assert task.progress == 100
    assert task.error is None
    assert task.result is not None
    assert task.result["compsize"]["available"] is False
    assert task.result["profiles_unlocked"] is True
    assert any(
        "Host compression measurement failed" in warning
        for warning in task.result["warnings"]
    )
    service.shutdown()


def test_linux_system_provider_uses_host_tools_and_partial_gpu(tmp_path: Path) -> None:
    class Host:
        def diagnose(self) -> dict[str, object]:
            tools = {
                key: {
                    "available": key in {"steam", "flatpak", "gamescope", "mangohud", "vulkan", "btrfs-progs", "compsize"},
                    "source": "host",
                    "version": "1",
                    "diagnostic_message": "host result",
                }
                for key in ("steam", "flatpak", "gamescope", "gamemode", "mangohud", "vulkan", "btrfs-progs", "compsize", "bottles", "heroic", "lutris")
            }
            tools["steam"].update({
                "steam_type": "native",
                "host_launch_available": True,
            })
            return {
                "source": "host",
                "tools": tools,
                "gpu": {
                    "vendor": "AMD",
                    "model": "Radeon RX test",
                    "driver": "amdgpu",
                    "vulkan_device": "",
                    "partial": True,
                },
            }

    os_release = tmp_path / "os-release"
    cpuinfo = tmp_path / "cpuinfo"
    meminfo = tmp_path / "meminfo"
    os_release.write_text('PRETTY_NAME="Test Linux"\n', encoding="utf-8")
    cpuinfo.write_text("processor: 0\nmodel name: CPU\n", encoding="utf-8")
    meminfo.write_text("MemTotal: 1048576 kB\n", encoding="utf-8")
    provider = LinuxSystemProvider(
        os_release_path=os_release,
        cpuinfo_path=cpuinfo,
        meminfo_path=meminfo,
        environment={"FLATPAK_ID": "app"},
        which=lambda name: None,
        host_service=Host(),
    )
    info = provider.collect()
    assert info.diagnostics_source == "host"
    assert info.gpu == "Radeon RX test"
    assert info.gpu_driver == "amdgpu"
    assert info.capabilities["Steam"].value == "Available"
    assert info.capabilities["Gamescope"].value == "Available"
    assert info.capabilities["MangoHud"].value == "Available"
    assert info.capabilities["compsize"].value == "Available"
    assert info.steam_type == "native"
    assert info.host_launch_available is True
