from __future__ import annotations

import os
from pathlib import Path
from threading import Event

from game_optimization_linux.services.directory_size import DirectorySizeScanner


def _allocated_size(path: Path) -> int:
    return os.lstat(path).st_blocks * 512


def test_scanner_counts_nested_files_once_and_ignores_symlinks(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "game"
    nested = tree / "content"
    nested.mkdir(parents=True)
    first = tree / "first.bin"
    second = nested / "second.bin"
    first.write_bytes(b"abc")
    second.write_bytes(b"12345")
    os.link(first, nested / "first-hardlink.bin")

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 4096)
    (tree / "outside-link").symlink_to(outside)
    (tree / "directory-link").symlink_to(nested, target_is_directory=True)

    result = DirectorySizeScanner().scan(tree)

    assert result.logical_bytes == 8
    assert result.physical_bytes == _allocated_size(first) + _allocated_size(second)
    assert result.errors == ()
    assert result.complete


def test_scanner_uses_lstat_blocks_for_sparse_file(tmp_path: Path) -> None:
    sparse = tmp_path / "sparse.bin"
    with sparse.open("wb") as stream:
        stream.seek(1024 * 1024)
        stream.write(b"x")

    result = DirectorySizeScanner().scan(sparse)

    file_stat = os.lstat(sparse)
    assert result.logical_bytes == file_stat.st_size
    assert result.physical_bytes == file_stat.st_blocks * 512
    assert result.complete


def test_scanner_honours_preexisting_cancellation(tmp_path: Path) -> None:
    (tmp_path / "ignored.bin").write_bytes(b"data")
    cancelled = Event()
    cancelled.set()

    result = DirectorySizeScanner().scan(tmp_path, cancelled)

    assert result.logical_bytes == 0
    assert result.physical_bytes == 0
    assert result.errors == ()
    assert not result.complete


def test_scanner_records_missing_paths_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "vanished"

    result = DirectorySizeScanner().scan(missing)

    assert result.logical_bytes == 0
    assert result.physical_bytes == 0
    assert len(result.errors) == 1
    assert str(missing) in result.errors[0]
    assert result.complete


def test_scanner_continues_after_permission_error(
    tmp_path: Path, monkeypatch
) -> None:
    tree = tmp_path / "game"
    denied = tree / "denied"
    denied.mkdir(parents=True)
    readable = tree / "readable.bin"
    readable.write_bytes(b"readable")
    real_scandir = os.scandir

    def selective_scandir(path: os.PathLike[str] | str):
        if Path(path) == denied:
            raise PermissionError("fixture denies this directory")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", selective_scandir)

    result = DirectorySizeScanner().scan(tree)

    assert result.logical_bytes == len(b"readable")
    assert result.physical_bytes == _allocated_size(readable)
    assert len(result.errors) == 1
    assert str(denied) in result.errors[0]
    assert result.complete
