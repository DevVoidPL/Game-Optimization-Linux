"""Read-only process check used to stop narrator capture with the game."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from game_optimization_linux.models import Game


class NarratorGameActivityDetector:
    def __init__(
        self,
        game_loader: Callable[[str], Game | None],
        *,
        proc_root: Path = Path("/proc"),
    ) -> None:
        self._game_loader = game_loader
        self._proc_root = Path(proc_root)

    def is_active(self, game_key: str) -> bool | None:
        # A Flatpak sandbox cannot reliably observe host Steam/Proton processes
        # through its own /proc namespace. Treat activity as unknown and let the
        # explicit ScreenCast portal source establish the capture session.
        if Path("/.flatpak-info").exists():
            return None

        game = self._game_loader(game_key)
        if game is None:
            return None
        try:
            game_root = game.install_path.resolve(strict=True)
        except OSError:
            return False
        try:
            processes = tuple(self._proc_root.iterdir())
        except OSError:
            return None
        root_text = str(game_root)
        root_bytes = root_text.encode(errors="surrogateescape")
        for process in processes:
            if not process.name.isdecimal():
                continue
            try:
                cwd = (process / "cwd").resolve(strict=True)
                if cwd == game_root or game_root in cwd.parents:
                    return True
            except OSError:
                pass
            try:
                arguments = (process / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            for argument in arguments:
                if argument == root_bytes or argument.startswith(root_bytes + b"/"):
                    return True
        return False


__all__ = ["NarratorGameActivityDetector"]
