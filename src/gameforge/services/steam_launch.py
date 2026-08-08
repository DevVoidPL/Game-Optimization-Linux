"""Safe, non-blocking Steam process launch helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Protocol

from gameforge.models import Game, Launcher


class SteamLaunchError(RuntimeError):
    """A user-readable launch precondition or process error."""


NATIVE_STEAM_ENVIRONMENT_ERROR = (
    "Steam is already running without this GameForge MangoHud profile. "
    "Close Steam completely, then launch the game from GameForge."
)


def running_native_steam_environment() -> Mapping[str, str] | None:
    """Return the active user's Steam environment, or ``None`` if not running.

    Only the two MangoHud variables are retained. An unreadable Steam process
    returns an empty mapping so callers fail closed instead of claiming that
    per-game environment injection is reliable through Steam IPC.
    """

    proc = Path("/proc")
    try:
        entries = tuple(proc.iterdir())
    except OSError:
        return {}
    user_id = os.getuid()
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != user_id:
                continue
            command = (entry / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if command != "steam":
            continue
        try:
            raw = (entry / "environ").read_bytes()
        except OSError:
            return {}
        selected: dict[str, str] = {}
        for item in raw.split(b"\0"):
            key, separator, value = item.partition(b"=")
            if separator and key in {b"MANGOHUD", b"MANGOHUD_CONFIGFILE"}:
                selected[key.decode("ascii")] = value.decode("utf-8", errors="replace")
        return selected
    return None


class LaunchActivationLike(Protocol):
    enabled: bool
    available: bool
    environment: Mapping[str, str]
    config_path: Path | None
    steam_type: str
    message: str
    strategy: str
    strategy_status: str
    requires_steam_restart: bool


@dataclass(frozen=True, slots=True)
class SteamLaunchPlan:
    """Shell-free launch description that can accept future tool stages."""

    executable: str
    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    mangohud_config_path: Path | None
    steam_type: str
    app_id: str
    mangohud_enabled: bool = False
    mangohud_strategy: str = "steam_environment"
    mangohud_strategy_status: str = ""
    requires_steam_restart: bool = False

    @property
    def command(self) -> list[str]:
        return [self.executable, *self.arguments]

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "arguments": list(self.arguments),
            "environment": dict(self.environment),
            "mangoHudConfigPath": (
                str(self.mangohud_config_path)
                if self.mangohud_config_path is not None
                else ""
            ),
            "steamType": self.steam_type,
            "appId": self.app_id,
            "mangoHudEnabled": self.mangohud_enabled,
            "mangoHudStrategy": self.mangohud_strategy,
            "mangoHudStrategyStatus": self.mangohud_strategy_status,
            "requiresSteamRestart": self.requires_steam_restart,
        }


def uses_flatpak_steam(game: Game) -> bool:
    source = game.data_source.strip().casefold()
    if source == "steam flatpak":
        return True
    paths = (game.library_path, game.install_path)
    return any(
        path is not None
        and ".var" in Path(path).parts
        and "com.valvesoftware.Steam" in Path(path).parts
        for path in paths
    )


def build_steam_launch_command(
    game: Game,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Validate a game and return an argv list suitable for ``Popen``."""

    if game.launcher is not Launcher.STEAM:
        raise SteamLaunchError("The selected entry is not a Steam game")
    app_id = str(game.steam_app_id or "").strip()
    if not app_id.isascii() or not app_id.isdecimal() or int(app_id) <= 0:
        raise SteamLaunchError("Invalid Steam AppID")
    try:
        installed = game.install_path.is_dir()
    except OSError:
        installed = False
    if not installed:
        raise SteamLaunchError("Game installation directory not found")

    if uses_flatpak_steam(game):
        executable = which("flatpak")
        if not executable:
            raise SteamLaunchError("Flatpak executable not found")
        return [executable, "run", "com.valvesoftware.Steam", "-applaunch", app_id]

    executable = which("steam")
    if not executable:
        raise SteamLaunchError("Steam executable not found")
    return [executable, "-applaunch", app_id]


def build_steam_launch_plan(
    game: Game,
    *,
    activation: LaunchActivationLike | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> SteamLaunchPlan:
    """Build the complete argv/environment plan without executing it."""

    command = build_steam_launch_command(game, which=which)
    app_id = str(game.steam_app_id or "").strip()
    steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
    environment: dict[str, str] = {}
    config_path: Path | None = None
    enabled = False
    if activation is not None and activation.enabled:
        if not activation.available:
            raise SteamLaunchError(
                activation.message or "MangoHud is unavailable for this Steam installation"
            )
        if activation.steam_type != steam_type:
            raise SteamLaunchError("MangoHud activation does not match the Steam installation")
        environment.update({str(key): str(value) for key, value in activation.environment.items()})
        config_path = activation.config_path
        enabled = True

    if steam_type == "flatpak" and environment:
        # Flatpak does not inherit host variables into the sandbox. Each
        # allowlisted variable is passed as its own argv item before APP_ID.
        insert_at = command.index("com.valvesoftware.Steam")
        flatpak_environment = [
            f"--env={key}={value}" for key, value in sorted(environment.items())
        ]
        command[insert_at:insert_at] = flatpak_environment

    return SteamLaunchPlan(
        executable=command[0],
        arguments=tuple(command[1:]),
        environment=environment,
        mangohud_config_path=config_path,
        steam_type=steam_type,
        app_id=app_id,
        mangohud_enabled=enabled,
        mangohud_strategy=(
            activation.strategy if activation is not None else "steam_environment"
        ),
        mangohud_strategy_status=(
            activation.strategy_status if activation is not None else ""
        ),
        requires_steam_restart=(
            activation.requires_steam_restart if activation is not None else False
        ),
    )


class SteamLauncher:
    """Start Steam without waiting for it and without invoking a shell."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        native_steam_environment: Callable[[], Mapping[str, str] | None] = (
            running_native_steam_environment
        ),
        host_service: object | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._which = which
        self._popen = popen
        self._native_steam_environment = native_steam_environment
        self._host_service = host_service
        self._environment = os.environ if environment is None else environment

    def _host_which(self, name: str) -> str | None:
        if self._host_service is None or name not in {"steam", "flatpak"}:
            return self._which(name)
        query = getattr(self._host_service, "tool_info", None)
        if not callable(query):
            return None
        try:
            steam = query("steam")
        except Exception:
            return None
        if not isinstance(steam, Mapping):
            return None
        if name == "steam" and steam.get("native_available") is True:
            return str(steam.get("executable") or "steam")
        if name == "flatpak" and steam.get("flatpak_available") is True:
            return "flatpak"
        return None

    def build_command(self, game: Game) -> list[str]:
        return build_steam_launch_command(game, which=self._host_which)

    def build_plan(
        self, game: Game, activation: LaunchActivationLike | None = None
    ) -> SteamLaunchPlan:
        return build_steam_launch_plan(game, activation=activation, which=self._host_which)

    def launch_plan(self, plan: SteamLaunchPlan) -> list[str]:
        command = plan.command
        if plan.steam_type == "native" and plan.mangohud_enabled:
            running_environment = self._native_steam_environment()
            if running_environment is not None and any(
                running_environment.get(key) != value
                for key, value in plan.environment.items()
            ):
                raise SteamLaunchError(NATIVE_STEAM_ENVIRONMENT_ERROR)
        environment = os.environ.copy()
        environment.update(plan.environment)
        launch_command = list(command)
        if str(self._environment.get("FLATPAK_ID", "")).strip():
            spawn = self._which("flatpak-spawn")
            if not spawn:
                raise SteamLaunchError("flatpak-spawn is unavailable in the sandbox")
            host_environment = [
                f"--env={key}={value}" for key, value in sorted(plan.environment.items())
            ]
            launch_command = [spawn, "--host", *host_environment, *command]
        try:
            self._popen(
                launch_command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
                env=environment,
            )
        except OSError as error:
            raise SteamLaunchError(f"Could not start Steam: {error}") from error
        return launch_command

    def launch(
        self, game: Game, activation: LaunchActivationLike | None = None
    ) -> list[str]:
        return self.launch_plan(self.build_plan(game, activation))


__all__ = [
    "SteamLaunchError",
    "SteamLauncher",
    "SteamLaunchPlan",
    "NATIVE_STEAM_ENVIRONMENT_ERROR",
    "build_steam_launch_command",
    "build_steam_launch_plan",
    "running_native_steam_environment",
    "uses_flatpak_steam",
]
