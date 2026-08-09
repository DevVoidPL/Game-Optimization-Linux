"""Stable Steam Launch Options command and runner self-test."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

from game_optimization_linux.models import Game, Launcher, validate_app_id, validate_game_key

from .game_executable import GameExecutableResolver

from .host_bootstrap import default_runner_path


DEFAULT_RUNNER_PATH = default_runner_path()


@dataclass(frozen=True, slots=True)
class RunnerStatus:
    path: Path
    installed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "installed": self.installed, "message": self.message}


class RunnerIntegration:
    def __init__(
        self,
        path: Path | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        executable_resolver: GameExecutableResolver | None = None,
    ) -> None:
        self.path = default_runner_path() if path is None else Path(path)
        self._run = runner
        self._popen = popen
        self._executable_resolver = executable_resolver or GameExecutableResolver()

    def status(self) -> RunnerStatus:
        installed = self.path.is_file() and self.path.stat().st_mode & 0o111 != 0
        return RunnerStatus(
            self.path, installed,
            "Game Optimization Runner is installed" if installed else "Install the Game Optimization Runner for this user",
        )

    def steam_command(self, app_id: object) -> str:
        normalized = validate_app_id(app_id)
        escaped = str(self.path).replace('"', '\\"')
        return f'"{escaped}" --appid {normalized} -- %command%'

    def test(self, app_id: object) -> dict[str, Any]:
        normalized = validate_game_key(app_id)
        status = self.status()
        if not status.installed:
            return {"success": False, "exitCode": -1, **status.to_dict()}
        true_command = shutil.which("true") or "true"
        try:
            result = self._run(
                [str(self.path), "--appid", normalized, "--plan-only", "--", true_command],
                check=False, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {"success": False, "exitCode": -1, "path": str(self.path), "message": str(error)}
        return {
            "success": result.returncode == 0, "exitCode": result.returncode,
            "path": str(self.path),
            "message": (result.stderr or result.stdout or "Runner plan verified").strip(),
        }

    def launch_local(self, game: Game) -> tuple[str, ...]:
        if (
            game.launcher is not Launcher.MANUAL
            or game.data_source.casefold() != "local"
        ):
            raise ValueError("the selected game is not a configured local game")
        resolution = self._executable_resolver.resolve(
            game, game.executable_path
        )
        selected = resolution.selected
        if selected is None or not resolution.reliable:
            raise ValueError("choose the main local game executable first")
        if selected.wine:
            raise ValueError(
                "a compatible Proton runner has not been configured for this local game"
            )
        status = self.status()
        if not status.installed:
            raise ValueError(status.message)
        root = game.install_path.resolve(strict=True)
        executable = (root / selected.relative_path).resolve(strict=True)
        executable.relative_to(root)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("the selected local executable is not runnable")
        runner_argv = [
            str(self.path), "--appid", validate_game_key(game.id), "--",
            str(executable),
        ]
        if os.environ.get("FLATPAK_ID", "").strip():
            flatpak_spawn = shutil.which("flatpak-spawn")
            if not flatpak_spawn:
                raise ValueError("flatpak-spawn is unavailable in the sandbox")
            argv = [flatpak_spawn, "--host", *runner_argv]
        else:
            argv = runner_argv
        self._popen(
            argv,
            cwd=str(root),
            close_fds=True,
            start_new_session=True,
            shell=False,
        )
        return tuple(argv)


__all__ = ["DEFAULT_RUNNER_PATH", "RunnerIntegration", "RunnerStatus"]
