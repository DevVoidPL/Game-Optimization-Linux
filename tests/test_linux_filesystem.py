from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from game_optimization_linux.models.enums import FilesystemType
from game_optimization_linux.providers.linux_filesystem import LinuxFilesystemProvider


def _completed_findmnt(filesystems: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["findmnt"],
        returncode=0,
        stdout=json.dumps({"filesystems": filesystems}),
        stderr="",
    )


def test_findmnt_uses_argument_list_json_and_longest_mount(tmp_path: Path) -> None:
    game_path = tmp_path / "library" / "game"
    game_path.mkdir(parents=True)
    response = _completed_findmnt(
        [
            {
                "target": "/",
                "source": "/dev/root",
                "fstype": "ext4",
                "options": "rw,relatime",
                "children": [
                    {
                        "target": str(tmp_path / "library"),
                        "source": "/dev/nvme0n1p2",
                        "fstype": "btrfs",
                        "options": "rw,compress=zstd:3",
                    }
                ],
            }
        ]
    )

    with patch(
        "game_optimization_linux.providers.linux_filesystem.subprocess.run", return_value=response
    ) as run:
        info = LinuxFilesystemProvider(timeout=1.25).inspect(game_path)

    command = run.call_args.args[0]
    assert command == [
        "findmnt",
        "--json",
        "--bytes",
        "--output",
        "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,USED,AVAIL",
        "--target",
        str(game_path),
    ]
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["timeout"] == 1.25
    assert info.mount_point == tmp_path / "library"
    assert info.filesystem is FilesystemType.BTRFS
    assert info.filesystem_name == "btrfs"
    assert info.device == "/dev/nvme0n1p2"
    assert info.mount_options == ("rw", "compress=zstd:3")
    assert info.compression_supported


def test_mountinfo_fallback_decodes_paths_and_uses_existing_parent(
    tmp_path: Path,
) -> None:
    mount_point = tmp_path / "Game Drive"
    existing_parent = mount_point / "Steam Library"
    existing_parent.mkdir(parents=True)
    missing_game = existing_parent / "Missing Game"
    escaped_mount = str(mount_point).replace(" ", r"\040")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "24 1 0:1 / / rw,relatime - ext4 /dev/root rw\n"
        f"25 24 0:2 / {escaped_mount} rw,nosuid - btrfs /dev/sdb1 "
        "rw,compress=zstd\n"
        "malformed entry\n",
        encoding="utf-8",
    )

    with (
        patch(
            "game_optimization_linux.providers.linux_filesystem.subprocess.run",
            side_effect=FileNotFoundError("findmnt"),
        ),
        patch(
            "game_optimization_linux.providers.linux_filesystem.os.access", return_value=False
        ) as access,
    ):
        info = LinuxFilesystemProvider(mountinfo).inspect(missing_game)

    assert info.mount_point == mount_point
    assert info.filesystem is FilesystemType.BTRFS
    assert info.device == "/dev/sdb1"
    assert info.mount_options == ("rw", "nosuid", "compress=zstd")
    assert info.writable is False
    assert info.compression_supported
    access.assert_called_once_with(existing_parent.resolve(), os.W_OK)


@pytest.mark.parametrize(
    ("filesystem_name", "expected"),
    [
        ("btrfs", FilesystemType.BTRFS),
        ("ext4", FilesystemType.EXT4),
        ("xfs", FilesystemType.XFS),
        ("ntfs3", FilesystemType.NTFS),
        ("tmpfs", FilesystemType.OTHER),
    ],
)
def test_only_btrfs_is_compression_compatible(
    tmp_path: Path,
    filesystem_name: str,
    expected: FilesystemType,
) -> None:
    response = _completed_findmnt(
        [
            {
                "target": str(tmp_path),
                "source": "test-device",
                "fstype": filesystem_name,
                "options": "rw",
            }
        ]
    )
    with patch(
        "game_optimization_linux.providers.linux_filesystem.subprocess.run", return_value=response
    ):
        info = LinuxFilesystemProvider().inspect(tmp_path)

    assert info.filesystem is expected
    assert info.compression_supported is (expected is FilesystemType.BTRFS)


def test_findmnt_and_mountinfo_failures_return_unknown(tmp_path: Path) -> None:
    missing_mountinfo = tmp_path / "does-not-exist"
    response = subprocess.CompletedProcess(
        args=["findmnt"], returncode=1, stdout="", stderr="not found"
    )

    with patch(
        "game_optimization_linux.providers.linux_filesystem.subprocess.run", return_value=response
    ):
        info = LinuxFilesystemProvider(missing_mountinfo).inspect(tmp_path)

    assert info.filesystem is FilesystemType.UNKNOWN
    assert info.mount_point == tmp_path.resolve()
    assert not info.compression_supported


def test_list_filesystems_uses_mountinfo_when_findmnt_json_is_invalid(
    tmp_path: Path,
) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "24 1 0:1 / / ro,relatime - ext4 /dev/root ro\n"
        "25 24 0:2 / /games rw - xfs /dev/games rw,inode64\n",
        encoding="utf-8",
    )
    invalid_json = subprocess.CompletedProcess(
        args=["findmnt"], returncode=0, stdout="not-json", stderr=""
    )

    with patch(
        "game_optimization_linux.providers.linux_filesystem.subprocess.run",
        return_value=invalid_json,
    ):
        filesystems = LinuxFilesystemProvider(mountinfo).list_filesystems()

    assert [item.mount_point for item in filesystems] == [Path("/"), Path("/games")]
    assert [item.filesystem for item in filesystems] == [
        FilesystemType.EXT4,
        FilesystemType.XFS,
    ]
    assert not any(item.compression_supported for item in filesystems)


def test_findmnt_sizes_and_default_mount_filtering() -> None:
    response = _completed_findmnt(
        [
            {
                "target": "/",
                "source": "/dev/root",
                "fstype": "ext4",
                "options": "rw,relatime",
                "size": 1000,
                "used": 400,
                "avail": 550,
                "children": [
                    {
                        "target": "/proc",
                        "source": "proc",
                        "fstype": "proc",
                        "options": "rw,nosuid",
                    },
                    {
                        "target": "/home",
                        "source": "/dev/home",
                        "fstype": "btrfs",
                        "options": "rw,compress=zstd:3",
                        "size": "2 GiB",
                        "used": "512 MiB",
                        "avail": "1.5 GiB",
                    },
                    {
                        "target": "/network/steam",
                        "source": "server:/steam",
                        "fstype": "nfs4",
                        "options": "ro",
                        "size": 4096,
                        "used": 1024,
                        "avail": 3072,
                    },
                    {
                        "target": "/var/log",
                        "source": "/dev/root[/@log]",
                        "fstype": "btrfs",
                        "options": "rw",
                    },
                    {
                        "target": "/data",
                        "source": "/dev/sdc1",
                        "fstype": "ntfs3",
                        "options": "ro",
                    },
                ],
            }
        ]
    )

    with patch(
        "game_optimization_linux.providers.linux_filesystem.subprocess.run", return_value=response
    ):
        filesystems = LinuxFilesystemProvider().list_filesystems(
            game_paths=(Path("/network/steam/steamapps/common/Test"),)
        )

    by_mount = {item.mount_point: item for item in filesystems}
    assert tuple(by_mount) == (Path("/"), Path("/home"), Path("/data"), Path("/network/steam"))
    assert Path("/proc") not in by_mount
    assert Path("/var/log") not in by_mount
    assert by_mount[Path("/")].size_bytes == 1000
    assert by_mount[Path("/")].used_bytes == 400
    assert by_mount[Path("/")].available_bytes == 550
    assert by_mount[Path("/home")].size_bytes == 2 * 1024**3
    assert by_mount[Path("/home")].used_bytes == 512 * 1024**2
    assert by_mount[Path("/home")].available_bytes == int(1.5 * 1024**3)
    assert by_mount[Path("/network/steam")].writable is False
    assert by_mount[Path("/data")].filesystem is FilesystemType.NTFS


def test_show_system_mounts_exposes_filtered_pseudo_filesystems() -> None:
    response = _completed_findmnt(
        [
            {
                "target": "/",
                "source": "/dev/root",
                "fstype": "ext4",
                "options": "rw",
                "children": [
                    {
                        "target": "/proc",
                        "source": "proc",
                        "fstype": "proc",
                        "options": "rw",
                    },
                    {
                        "target": "/sys/kernel/debug",
                        "source": "debugfs",
                        "fstype": "debugfs",
                        "options": "rw",
                    },
                ],
            }
        ]
    )

    with patch(
        "game_optimization_linux.providers.linux_filesystem.subprocess.run", return_value=response
    ):
        provider = LinuxFilesystemProvider()
        default_mounts = provider.list_filesystems()
        all_mounts = provider.list_filesystems(show_system_mounts=True)

    assert [item.mount_point for item in default_mounts] == [Path("/")]
    assert {item.mount_point for item in all_mounts} == {
        Path("/"),
        Path("/proc"),
        Path("/sys/kernel/debug"),
    }


def test_mountinfo_fallback_populates_space_with_statvfs(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "24 1 0:1 / / ro,relatime - ext4 /dev/root ro\n",
        encoding="utf-8",
    )
    invalid_json = subprocess.CompletedProcess(
        args=["findmnt"], returncode=0, stdout="not-json", stderr=""
    )

    fake_statvfs = os.statvfs_result((4096, 4096, 100, 40, 30, 0, 0, 0, 0, 255))
    with (
        patch(
            "game_optimization_linux.providers.linux_filesystem.subprocess.run",
            return_value=invalid_json,
        ),
        patch(
            "game_optimization_linux.providers.linux_filesystem.os.statvfs",
            return_value=fake_statvfs,
        ),
    ):
        filesystem = LinuxFilesystemProvider(mountinfo).list_filesystems()[0]

    assert filesystem.size_bytes == 409_600
    assert filesystem.used_bytes == 245_760
    assert filesystem.available_bytes == 122_880
    assert filesystem.writable is False
