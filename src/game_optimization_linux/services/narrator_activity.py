"""Read-only check whether a game is running (Narrator autostart and autostop).

A game counts as running when one process matches it:

* Steam: the ``reaper SteamLaunch AppId=<id>`` wrapper Steam starts for every
  game (also for Proton titles);
* any launcher: a command line containing the game's install directory (POSIX
  or Wine ``Z:\\`` form) or, natively, a process whose working directory is
  inside it.

Inside Flatpak the sandbox cannot see host processes, so the host process
list comes from the host service (``ps`` via ``flatpak-spawn --host``). If no
process list is available the answer is ``None`` (unknown), never a guess.

Results are cached per game for ``min_interval`` seconds, so callers polling
every frame do not spawn a process each time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import re
import time

from game_optimization_linux.models import Game, Launcher

DEFAULT_MIN_INTERVAL_SECONDS = 4.0


def _in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


class NarratorGameActivityDetector:
    def __init__(
        self,
        game_loader: Callable[[str], Game | None],
        *,
        proc_root: Path = Path("/proc"),
        host_processes: Callable[[], Sequence[str] | None] | None = None,
        sandboxed: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._game_loader = game_loader
        self._proc_root = Path(proc_root)
        self._host_processes = host_processes
        self._sandboxed = _in_flatpak() if sandboxed is None else bool(sandboxed)
        self._clock = clock
        self._min_interval = max(0.0, float(min_interval))
        self._cache: dict[str, tuple[float, bool | None]] = {}
        self.probe_count = 0

    @property
    def can_observe_processes(self) -> bool:
        """Whether running games can be recognised at all in this runtime."""

        return not self._sandboxed or self._host_processes is not None

    def supports(self, game: Game | None) -> bool:
        """Whether this game can be recognised when it runs (for autostart)."""

        if game is None or not self.can_observe_processes:
            return False
        if game.launcher is Launcher.STEAM and game.steam_app_id:
            return True
        return bool(str(game.install_path or "").strip())

    def invalidate(self, game_key: str = "") -> None:
        if game_key:
            self._cache.pop(game_key, None)
        else:
            self._cache.clear()

    def is_active(self, game_key: str) -> bool | None:
        now = self._clock()
        cached = self._cache.get(game_key)
        if cached is not None and now - cached[0] < self._min_interval:
            return cached[1]
        result = self._probe(game_key)
        self._cache[game_key] = (now, result)
        return result

    # -- probing ---------------------------------------------------------------
    def _probe(self, game_key: str) -> bool | None:
        game = self._game_loader(game_key)
        if game is None:
            return None
        self.probe_count += 1
        if self._sandboxed:
            if self._host_processes is None:
                return None
            commands = self._host_processes()
            if commands is None:
                return None
            return any(self._command_matches(game, line) for line in commands)
        return self._native_probe(game)

    @staticmethod
    def _path_markers(game: Game) -> tuple[str, ...]:
        root = str(game.install_path or "").rstrip("/")
        if not root:
            return ()
        wine = "z:" + root.replace("/", "\\")
        return (root.casefold(), wine.casefold())

    @classmethod
    def _command_matches(cls, game: Game, command: str) -> bool:
        text = str(command)
        if game.steam_app_id and "SteamLaunch" in text and re.search(
            rf"\bAppId={re.escape(str(game.steam_app_id))}\b", text
        ):
            return True
        folded = text.casefold()
        for marker in cls._path_markers(game):
            index = folded.find(marker)
            while index >= 0:
                end = index + len(marker)
                # Whole path component only ("/Game" must not match "/Game 2").
                if end == len(folded) or folded[end] in "/\\ \"'":
                    return True
                index = folded.find(marker, index + 1)
        return False

    def _native_probe(self, game: Game) -> bool | None:
        try:
            game_root = game.install_path.resolve(strict=True)
        except OSError:
            game_root = None
        try:
            processes = tuple(self._proc_root.iterdir())
        except OSError:
            return None
        for process in processes:
            if not process.name.isdecimal():
                continue
            if game_root is not None:
                try:
                    cwd = (process / "cwd").resolve(strict=True)
                    if cwd == game_root or game_root in cwd.parents:
                        return True
                except OSError:
                    pass
            try:
                raw = (process / "cmdline").read_bytes()
            except OSError:
                continue
            command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
            if self._command_matches(game, command):
                return True
        return False


__all__ = ["DEFAULT_MIN_INTERVAL_SECONDS", "NarratorGameActivityDetector"]
