"""Stable Steam Launch Options command and runner self-test."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any, Callable, Mapping, Sequence

from game_optimization_linux.models import Game, Launcher, validate_app_id, validate_game_key

from .game_executable import GameExecutableResolver

from .host_bootstrap import default_runner_path


DEFAULT_RUNNER_PATH = default_runner_path()
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunnerStatus:
    path: Path
    installed: bool
    message: str
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "installed": self.installed,
            "message": self.message,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class SteamLaunchOptionStatus:
    configured: bool | None
    command: str
    source: str
    message: str
    app_node_found: bool = False
    app_node_path: str = ""
    raw_launch_options: str = ""
    parsed_executable: str = ""
    parsed_app_id: str = ""
    separator_found: bool = False
    command_placeholder_found: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "command": self.command,
            "source": self.source,
            "message": self.message,
            "appNodeFound": self.app_node_found,
            "appNodePath": self.app_node_path,
            "rawLaunchOptions": self.raw_launch_options,
            "parsedExecutable": self.parsed_executable,
            "parsedAppId": self.parsed_app_id,
            "separatorFound": self.separator_found,
            "commandPlaceholderFound": self.command_placeholder_found,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _LaunchOptionParse:
    configured: bool
    executable: str = ""
    app_id: str = ""
    separator_found: bool = False
    command_placeholder_found: bool = False
    reason: str = ""


class RunnerIntegration:
    def __init__(
        self,
        path: Path | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        executable_resolver: GameExecutableResolver | None = None,
        steam_config_paths: Sequence[Path] | None = None,
    ) -> None:
        self.path = default_runner_path() if path is None else Path(path)
        self._run = runner
        self._popen = popen
        self._executable_resolver = executable_resolver or GameExecutableResolver()
        self._steam_config_paths = (
            tuple(Path(path) for path in steam_config_paths)
            if steam_config_paths is not None
            else None
        )

    def status(self) -> RunnerStatus:
        installed = self.path.is_file() and self.path.stat().st_mode & 0o111 != 0
        digest = ""
        if installed:
            try:
                digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
            except OSError:
                installed = False
        return RunnerStatus(
            self.path, installed,
            "Game Optimization Runner is installed" if installed else "Install the Game Optimization Runner for this user",
            digest,
        )

    def steam_command(self, app_id: object) -> str:
        normalized = validate_app_id(app_id)
        escaped = str(self.path).replace('"', '\\"')
        return f'"{escaped}" --appid {normalized} -- %command%'

    def steam_launch_option_status(self, game: Game) -> SteamLaunchOptionStatus:
        app_id = validate_app_id(game.steam_app_id)
        expected = self.steam_command(app_id)
        found_app = False
        best_failure: SteamLaunchOptionStatus | None = None
        for path in self._localconfig_paths(game):
            try:
                if path.stat().st_size > 32 * 1024 * 1024:
                    continue
                from game_optimization_linux.providers.keyvalues import parse_keyvalues

                document = parse_keyvalues(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError, TypeError):
                continue
            app_nodes = self._find_apps(document, app_id)
            if not app_nodes:
                continue
            found_app = True
            for node_path, app in app_nodes:
                launch_options = self._casefold_value(app, "launchoptions")
                raw = str(launch_options) if launch_options is not None else ""
                parsed = self._parse_launch_options(raw, app_id)
                status = self._launch_option_status(
                    expected, path, node_path, raw, parsed
                )
                if status.configured:
                    self._log_launch_option_status(app_id, status)
                    return status
                if best_failure is None or (
                    status.raw_launch_options and not best_failure.raw_launch_options
                ):
                    best_failure = status
        if best_failure is not None:
            self._log_launch_option_status(app_id, best_failure)
            return best_failure
        status = SteamLaunchOptionStatus(
            False if found_app else None,
            expected,
            "",
            (
                "Game Optimization Runner is not configured for this game"
                if found_app
                else "Steam Launch Options could not be verified; the runner handshake will be used"
            ),
            app_node_found=found_app,
            reason="launch_options_missing" if found_app else "app_node_not_found",
        )
        self._log_launch_option_status(app_id, status)
        return status

    def _localconfig_paths(self, game: Game) -> tuple[Path, ...]:
        if self._steam_config_paths is not None:
            paths = list(self._steam_config_paths)
        else:
            home = Path.home()
            roots = (
                home / ".local/share/Steam/userdata",
                home / ".steam/steam/userdata",
                home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata",
            )
            paths = []
            for root in roots:
                try:
                    paths.extend(root.glob("*/config/localconfig.vdf"))
                except OSError:
                    continue
        del game
        unique: dict[Path, Path] = {}
        for path in paths:
            try:
                canonical = path.resolve(strict=False)
            except OSError:
                canonical = path.absolute()
            unique.setdefault(canonical, canonical)
        return tuple(unique.values())

    @classmethod
    def _find_apps(
        cls,
        value: object,
        app_id: str,
        path: tuple[str, ...] = (),
    ) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        if not isinstance(value, Mapping):
            return ()
        found: list[tuple[str, Mapping[str, Any]]] = []
        apps = cls._casefold_value(value, "apps")
        if isinstance(apps, Mapping):
            app = cls._casefold_value(apps, app_id.casefold())
            if isinstance(app, Mapping):
                found.append(("/".join((*path, "apps", app_id)), app))
        for name, child in value.items():
            found.extend(cls._find_apps(child, app_id, (*path, str(name))))
        return tuple(found)

    @staticmethod
    def _casefold_value(mapping: Mapping[str, Any], key: str) -> Any:
        wanted = key.casefold()
        return next(
            (value for name, value in mapping.items() if str(name).casefold() == wanted),
            None,
        )

    @staticmethod
    def _parse_launch_options(value: str, app_id: str) -> _LaunchOptionParse:
        if not value.strip():
            return _LaunchOptionParse(False, reason="launch_options_missing")
        try:
            tokens = shlex.split(value, posix=True)
        except ValueError:
            return _LaunchOptionParse(False, reason="invalid_launch_options_quoting")
        if not tokens:
            return _LaunchOptionParse(False, reason="launch_options_empty")
        executable = tokens[0]
        separator_found = "--" in tokens
        command_placeholder_found = "%command%" in tokens
        if Path(executable).name != "game-optimization-run":
            return _LaunchOptionParse(
                False, executable=executable,
                separator_found=separator_found,
                command_placeholder_found=command_placeholder_found,
                reason="runner_executable_mismatch",
            )
        try:
            marker = tokens.index("--")
            appid = tokens.index("--appid")
        except ValueError:
            return _LaunchOptionParse(
                False, executable=executable,
                separator_found=separator_found,
                command_placeholder_found=command_placeholder_found,
                reason=(
                    "separator_missing" if not separator_found else "appid_option_missing"
                ),
            )
        parsed_app_id = tokens[appid + 1] if appid + 1 < len(tokens) else ""
        if parsed_app_id != app_id:
            return _LaunchOptionParse(
                False, executable=executable, app_id=parsed_app_id,
                separator_found=True,
                command_placeholder_found=command_placeholder_found,
                reason="appid_mismatch",
            )
        if appid > marker:
            return _LaunchOptionParse(
                False, executable=executable, app_id=parsed_app_id,
                separator_found=True,
                command_placeholder_found=command_placeholder_found,
                reason="appid_after_separator",
            )
        if marker + 1 >= len(tokens) or tokens[marker + 1] != "%command%":
            return _LaunchOptionParse(
                False, executable=executable, app_id=parsed_app_id,
                separator_found=True,
                command_placeholder_found=command_placeholder_found,
                reason="command_placeholder_missing",
            )
        return _LaunchOptionParse(
            True, executable=executable, app_id=parsed_app_id,
            separator_found=True, command_placeholder_found=True,
            reason="configured",
        )

    @staticmethod
    def _launch_option_status(
        expected: str,
        source: Path,
        node_path: str,
        raw: str,
        parsed: _LaunchOptionParse,
    ) -> SteamLaunchOptionStatus:
        return SteamLaunchOptionStatus(
            parsed.configured,
            expected,
            str(source),
            (
                "Game Optimization Runner is configured for this game"
                if parsed.configured
                else "Game Optimization Runner is not configured for this game"
            ),
            app_node_found=True,
            app_node_path=node_path,
            raw_launch_options=raw,
            parsed_executable=parsed.executable,
            parsed_app_id=parsed.app_id,
            separator_found=parsed.separator_found,
            command_placeholder_found=parsed.command_placeholder_found,
            reason=parsed.reason,
        )

    def _matches_launch_options(self, value: str, app_id: str) -> bool:
        return self._parse_launch_options(value, app_id).configured

    @staticmethod
    def _log_launch_option_status(
        app_id: str, status: SteamLaunchOptionStatus
    ) -> None:
        logger.info(
            "Steam runner preflight: appId=%s localconfig=%s appNodeFound=%s "
            "appNode=%s rawLaunchOptions=%r parsedExecutable=%r parsedAppId=%r "
            "separator=%s commandPlaceholder=%s configured=%s reason=%s",
            app_id,
            status.source or "not found",
            status.app_node_found,
            status.app_node_path or "not found",
            status.raw_launch_options,
            status.parsed_executable,
            status.parsed_app_id,
            status.separator_found,
            status.command_placeholder_found,
            status.configured,
            status.reason,
        )

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


__all__ = [
    "DEFAULT_RUNNER_PATH",
    "RunnerIntegration",
    "RunnerStatus",
    "SteamLaunchOptionStatus",
]
