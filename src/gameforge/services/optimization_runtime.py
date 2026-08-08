"""Tool detection and shell-free launch-plan composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import shutil
import subprocess
from typing import Any

from gameforge.models import GameOptimizationProfile

from .optiscaler import merge_wine_dll_overrides


@dataclass(frozen=True, slots=True)
class RuntimeToolAvailability:
    name: str
    available: bool
    executable: str = ""
    version: str = ""
    supported_options: tuple[str, ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "available": self.available,
            "executable": self.executable, "version": self.version,
            "supportedOptions": list(self.supported_options), "message": self.message,
        }


class RuntimeToolDetector:
    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        host_service: object | None = None,
    ) -> None:
        self._which = which
        self._run = runner
        self._host_service = host_service
        self._cache: tuple[RuntimeToolAvailability, RuntimeToolAvailability] | None = None

    def _command(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return self._run(list(arguments), check=False, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None

    def gamemode(self) -> RuntimeToolAvailability:
        if self._host_service is not None:
            return self._host_tool("gamemode", "GameMode")
        executable = self._which("gamemoderun") or ""
        daemon = self._which("gamemoded") or ""
        if not executable or not daemon:
            return RuntimeToolAvailability("GameMode", False, message="gamemoderun or gamemoded is not installed")
        result = self._command([daemon, "--version"])
        version = ((result.stdout or result.stderr).strip() if result else "")
        status = self._command([daemon, "--status"])
        status_text = ((status.stdout or "") + "\n" + (status.stderr or "")).strip()
        diagnostic_failed = status is None or any(marker in status_text.casefold() for marker in (
            "could not connect", "failed to connect", "gamemode_query_status failed",
        ))
        if diagnostic_failed:
            return RuntimeToolAvailability(
                "GameMode", False, executable, version,
                message="GameMode is installed, but its service is unavailable",
            )
        return RuntimeToolAvailability(
            "GameMode", True, executable, version,
            message="GameMode is available (service diagnostic passed)",
        )

    def gamescope(self) -> RuntimeToolAvailability:
        if self._host_service is not None:
            return self._host_tool("gamescope", "Gamescope")
        executable = self._which("gamescope") or ""
        if not executable:
            return RuntimeToolAvailability("Gamescope", False, message="gamescope is not installed")
        help_result = self._command([executable, "--help"])
        version_result = self._command([executable, "--version"])
        help_text = (help_result.stdout or help_result.stderr) if help_result else ""
        version = ((version_result.stdout or version_result.stderr).strip() if version_result else "")
        known = (
            "-W", "-H", "-w", "-h", "-r", "--framerate-limit", "-f", "-b",
            "-S", "-F", "--display-index",
        )
        supported = tuple(option for option in known if option in help_text)
        return RuntimeToolAvailability(
            "Gamescope", bool(help_result and help_result.returncode == 0), executable,
            version, supported,
            "Gamescope is available" if help_result and help_result.returncode == 0 else "gamescope --help failed",
        )

    def _host_tool(self, key: str, label: str) -> RuntimeToolAvailability:
        query = getattr(self._host_service, "tool_info", None)
        if not callable(query):
            return RuntimeToolAvailability(
                label, False, message=f"{label} host detection is unavailable"
            )
        try:
            raw = query(key)
        except Exception as error:
            return RuntimeToolAvailability(
                label,
                False,
                message=f"{label} host probe failed: {type(error).__name__}",
            )
        if not isinstance(raw, Mapping):
            return RuntimeToolAvailability(
                label, False, message=f"{label} host probe returned invalid data"
            )
        executable = str(raw.get("executable") or "")
        available = bool(
            raw.get("available") is True
            and raw.get("runtime_available", raw.get("available")) is True
            and executable
        )
        options_raw = raw.get("supported_options")
        options = tuple(
            str(value)
            for value in (
                options_raw
                if isinstance(options_raw, Sequence)
                and not isinstance(options_raw, (str, bytes))
                else ()
            )
            if isinstance(value, str)
        )
        return RuntimeToolAvailability(
            label,
            available,
            executable if available else "",
            str(raw.get("version") or ""),
            options,
            str(raw.get("diagnostic_message") or ""),
        )

    def detect(self, *, refresh: bool = False) -> tuple[RuntimeToolAvailability, RuntimeToolAvailability]:
        if self._cache is None or refresh:
            self._cache = (self.gamemode(), self.gamescope())
        return self._cache


@dataclass(frozen=True, slots=True)
class GameRuntimeLaunchPlan:
    app_id: str
    profile: str
    executable: str
    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    wrappers: tuple[str, ...]
    reasons: tuple[str, ...]
    fps_limit_owner: str
    fps_limit: int | None
    warnings: tuple[str, ...] = ()

    @property
    def command(self) -> list[str]:
        return [self.executable, *self.arguments]

    def to_dict(self) -> dict[str, Any]:
        return {
            "appId": self.app_id, "profile": self.profile,
            "executable": self.executable, "arguments": list(self.arguments),
            "environment": dict(self.environment), "wrappers": list(self.wrappers),
            "reasons": list(self.reasons), "warnings": list(self.warnings),
            "fpsLimitOwner": self.fps_limit_owner,
            "fpsLimit": self.fps_limit or 0,
            "command": self.command,
        }


class OptimizationLaunchPlanner:
    """Compose Gamescope outside GameMode so GameMode directly wraps the game."""

    def build(
        self,
        profile: GameOptimizationProfile,
        game_argv: Sequence[str],
        *,
        gamemode: RuntimeToolAvailability,
        gamescope: RuntimeToolAvailability,
        mangohud_fps_limit: int | None = None,
        optiscaler_override: str = "",
        existing_wine_overrides: str = "",
        allow_placeholder: bool = False,
    ) -> GameRuntimeLaunchPlan:
        command = [str(value) for value in game_argv]
        if not command or not command[0] or (command[0] == "%command%" and not allow_placeholder):
            raise ValueError("Steam did not provide the game command after --")
        if any("\0" in value for value in command):
            raise ValueError("game argv contains a null byte")
        wrappers: list[str] = []
        reasons: list[str] = []
        warnings: list[str] = []
        environment: dict[str, str] = {}
        if optiscaler_override:
            merged_overrides = merge_wine_dll_overrides(
                existing_wine_overrides,
                optiscaler_override,
            )
            if merged_overrides:
                environment["WINEDLLOVERRIDES"] = merged_overrides
                reasons.append(
                    f"OptiScaler Proton override: {optiscaler_override}"
                )
        fps_limit_owner = "mangohud" if mangohud_fps_limit else "none"
        effective_fps_limit = mangohud_fps_limit
        inner = command
        if profile.gamemode_enabled:
            if gamemode.available:
                inner = [gamemode.executable, *inner]
                wrappers.append("gamemode")
                reasons.append("GameMode directly wraps the game process")
            else:
                warnings.append("GameMode was requested but is unavailable")
        use_gamescope = profile.gamescope_enabled and profile.gamescope_mode != "disabled"
        if use_gamescope:
            if not gamescope.available:
                warnings.append("Gamescope was requested but is unavailable")
            else:
                supported = set(gamescope.supported_options)
                flags: list[str] = []
                def add(option: str, value: object | None = None) -> bool:
                    if option not in supported:
                        warnings.append(f"Installed Gamescope does not support {option}")
                        return False
                    flags.append(option)
                    if value is not None:
                        flags.append(str(value))
                    return True
                input_size = add("-w", profile.gamescope_input_width) & add("-h", profile.gamescope_input_height)
                output_size = add("-W", profile.gamescope_output_width) & add("-H", profile.gamescope_output_height)
                if input_size and output_size:
                    reasons.append(
                        f"Gamescope renders at {profile.gamescope_input_width}×{profile.gamescope_input_height} "
                        f"and outputs {profile.gamescope_output_width}×{profile.gamescope_output_height}"
                    )
                # Gamescope is the sole limiter owner when active.  The local
                # release documents -r as the nested refresh in frames per
                # second; emitting --framerate-limit as well created two
                # competing limiters for the same game.
                if add("-r", profile.target_fps):
                    fps_limit_owner = "gamescope"
                    effective_fps_limit = profile.target_fps
                    reasons.append(f"Gamescope owns the FPS limit at {profile.target_fps} FPS")
                if add("-S", profile.gamescope_scaler):
                    reasons.append(f"Gamescope scaler: {profile.gamescope_scaler}")
                if add("-F", profile.gamescope_filter):
                    reasons.append(f"Gamescope filter: {profile.gamescope_filter}")
                if add("-f" if profile.gamescope_fullscreen else "-b"):
                    reasons.append("Gamescope uses fullscreen mode" if profile.gamescope_fullscreen else "Gamescope uses borderless mode")
                if profile.target_display_id.startswith("screen-"):
                    index = profile.target_display_id.partition(":")[0].removeprefix("screen-")
                    if index.isdecimal():
                        if add("--display-index", int(index)):
                            reasons.append(f"Gamescope targets display index {index}")
                inner = [gamescope.executable, *flags, "--", *inner]
                wrappers.insert(0, "gamescope")
        elif profile.target_fps_mode != "unlimited":
            warnings.append("The FPS target is advisory until Gamescope is enabled")
        if fps_limit_owner == "mangohud":
            reasons.append(f"MangoHud owns the FPS limit at {mangohud_fps_limit} FPS")
        return GameRuntimeLaunchPlan(
            app_id=profile.app_id, profile=profile.preset,
            executable=inner[0], arguments=tuple(inner[1:]), environment=environment,
            wrappers=tuple(wrappers), reasons=tuple(reasons), warnings=tuple(warnings),
            fps_limit_owner=fps_limit_owner, fps_limit=effective_fps_limit,
        )


__all__ = [
    "GameRuntimeLaunchPlan", "OptimizationLaunchPlanner",
    "RuntimeToolAvailability", "RuntimeToolDetector",
]
