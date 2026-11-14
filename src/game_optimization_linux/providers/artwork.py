from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PySide6.QtGui import QImageReader


@lru_cache(maxsize=4096)
def _dimensions(path: str, mtime_ns: int, size_bytes: int) -> tuple[int, int]:
    reader = QImageReader(path)
    size = reader.size()
    if not reader.canRead() or not size.isValid():
        return 0, 0
    return size.width(), size.height()


def image_dimensions(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return _dimensions(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return 0, 0


def best_local_image(paths: Iterable[Path]) -> Path | None:
    best = None
    best_area = 0
    for path in dict.fromkeys(paths):
        width, height = image_dimensions(path)
        if width * height > best_area:
            best = path
            best_area = width * height
    return best.resolve(strict=False) if best is not None else None