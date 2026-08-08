"""Stable Steam Launch Options command and runner self-test."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

from gameforge.models import validate_app_id

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
    ) -> None:
        self.path = default_runner_path() if path is None else Path(path)
        self._run = runner

    def status(self) -> RunnerStatus:
        installed = self.path.is_file() and self.path.stat().st_mode & 0o111 != 0
        return RunnerStatus(
            self.path, installed,
            "GameForge Runner is installed" if installed else "Install the GameForge Runner for this user",
        )

    def steam_command(self, app_id: object) -> str:
        normalized = validate_app_id(app_id)
        escaped = str(self.path).replace('"', '\\"')
        return f'"{escaped}" --appid {normalized} -- %command%'

    def test(self, app_id: object) -> dict[str, Any]:
        normalized = validate_app_id(app_id)
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


__all__ = ["DEFAULT_RUNNER_PATH", "RunnerIntegration", "RunnerStatus"]
