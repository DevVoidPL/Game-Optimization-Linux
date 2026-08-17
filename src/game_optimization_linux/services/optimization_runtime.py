"""Tool detection and shell-free launch-plan composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
from typing import Any

from game_optimization_linux.models import GameOptimizationProfile

from .optiscaler import merge_wine_dll_overrides


_HOST_WRAPPER_ENVIRONMENT_KEYS = frozenset(
    {
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LIBGL_DRIVERS_PATH",
        "MESA_LOADER_DRIVER_OVERRIDE",
        "STEAM_LD_LIBRARY_PATH",
        "STEAM_RUNTIME",
        "SYSTEM_LD_LIBRARY_PATH",
        "SYSTEM_PATH",
        "VK_ADD_LAYER_PATH",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "VK_INSTANCE_LAYERS",
        "VK_LAYER_PATH",
        "VK_LOADER_LAYERS_DISABLE",
        "VK_LOADER_LAYERS_ENABLE",
    }
)
_HOST_WRAPPER_ENVIRONMENT_PREFIXES = ("PRESSURE_VESSEL_", "STEAM_RUNTIME_")
_STEAM_RUNTIME_PATH_MARKERS = (
    "pinned_libs",
    "pressure-vessel",
    "steam-runtime",
    "steamlinuxruntime",
    "steamrt",
)


def _gamescope_environment_boundary(
    environment: Mapping[str, str],
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str]]:
    restore: dict[str, str] = {}
    removed: list[str] = []
    for key, raw_value in environment.items():
        if key in _HOST_WRAPPER_ENVIRONMENT_KEYS or key.startswith(
            _HOST_WRAPPER_ENVIRONMENT_PREFIXES
        ):
            restore[key] = str(raw_value)
            removed.append(key)

    overrides: dict[str, str] = {}
    original_path = str(environment.get("PATH", ""))
    if original_path:
        safe_entries = [
            entry
            for entry in original_path.split(":")
            if entry
            and not any(
                marker in entry.casefold() for marker in _STEAM_RUNTIME_PATH_MARKERS
            )
        ]
        safe_path = ":".join(safe_entries)
        if safe_path != original_path:
            if not safe_path:
                raise ValueError(
                    "Gamescope cannot be launched safely because PATH contains only Steam Runtime entries"
                )
            restore["PATH"] = original_path
            overrides["PATH"] = safe_path
            removed.append("PATH")

    return restore, tuple(sorted(removed)), overrides


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
    environment_sources: Mapping[str, str] = field(default_factory=dict)
    environment_conflicts: tuple[str, ...] = ()
    mangohud_activation_owner: str = "none"
    steam_command: tuple[str, ...] = ()
    gamemode_wrapper: tuple[str, ...] = ()
    gamescope_wrapper: tuple[str, ...] = ()
    wrapper_environment_removed: tuple[str, ...] = ()
    wrapper_environment_overrides: Mapping[str, str] = field(default_factory=dict)
    environment_restore_keys: tuple[str, ...] = ()

    @property
    def command(self) -> list[str]:
        return [self.executable, *self.arguments]

    @property
    def diagnostic_command(self) -> list[str]:
        restore = set(self.environment_restore_keys)
        return [
            f"{key}=<preserved>"
            if "=" in value and (key := value.partition("=")[0]) in restore
            else value
            for value in self.command
        ]

    def process_environment(self, game_environment: Mapping[str, str]) -> dict[str, str]:
        result = dict(game_environment)
        for key in self.wrapper_environment_removed:
            result.pop(key, None)
        result.update(self.wrapper_environment_overrides)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "appId": self.app_id, "profile": self.profile,
            "executable": self.executable, "arguments": list(self.arguments),
            "environment": dict(self.environment), "wrappers": list(self.wrappers),
            "environmentSources": dict(self.environment_sources),
            "environmentConflicts": list(self.environment_conflicts),
            "mangoHudActivationOwner": self.mangohud_activation_owner,
            "reasons": list(self.reasons), "warnings": list(self.warnings),
            "fpsLimitOwner": self.fps_limit_owner,
            "fpsLimit": self.fps_limit or 0,
            "command": self.command,
            "diagnosticCommand": self.diagnostic_command,
            "steamCommand": list(self.steam_command),
            "gameModeWrapper": list(self.gamemode_wrapper),
            "gamescopeWrapper": list(self.gamescope_wrapper),
            "wrapperEnvironmentRemoved": list(self.wrapper_environment_removed),
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
        proton_environment: Mapping[str, str] | None = None,
        existing_environment: Mapping[str, str] | None = None,
        mangohud_activation_owner: str = "none",
        measurement_environment: Mapping[str, str] | None = None,
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
        environment_sources: dict[str, str] = {}
        environment_conflicts: list[str] = []
        gamemode_wrapper: list[str] = []
        gamescope_wrapper: list[str] = []
        wrapper_environment_removed: tuple[str, ...] = ()
        wrapper_environment_overrides: dict[str, str] = {}
        environment_restore_keys: tuple[str, ...] = ()
        inherited = existing_environment or {}
        normalized_mangohud_owner = str(
            mangohud_activation_owner or "none"
        ).strip().casefold()
        if normalized_mangohud_owner not in {
            "none", "per_application_config", "steam_environment", "mangoapp",
            "measurement_session",
        }:
            raise ValueError("unsupported MangoHud activation owner")
        if normalized_mangohud_owner == "per_application_config":
            reasons.append("MangoHud uses its per-executable application configuration")
        elif normalized_mangohud_owner == "steam_environment":
            reasons.append("MangoHud uses the existing Steam environment activation")
        elif normalized_mangohud_owner == "mangoapp":
            reasons.append("Gamescope mangoapp is the sole MangoHud activation owner")
        elif normalized_mangohud_owner == "measurement_session":
            reasons.append("MangoHud logging is active for one baseline session")
        for raw_key, raw_value in sorted((proton_environment or {}).items()):
            key = str(raw_key)
            value = str(raw_value)
            if (
                not key
                or not key.replace("_", "A").isalnum()
                or not (key[0].isalpha() or key[0] == "_")
                or "\0" in key
                or "\0" in value
            ):
                raise ValueError(f"invalid Proton environment variable: {key!r}")
            if key == "WINEDLLOVERRIDES":
                raise ValueError(
                    "WINEDLLOVERRIDES is managed by the OptiScaler integration"
                )
            inherited_value = inherited.get(key)
            if inherited_value is not None and str(inherited_value) != value:
                environment_conflicts.append(key)
                warnings.append(
                    f"{key} from the process environment is overridden by the saved per-game Proton tweak"
                )
            environment[key] = value
            environment_sources[key] = "proton_tweaks"
            reasons.append(f"Proton tweak: {key}={value}")
        if optiscaler_override:
            required_name, _separator, required_modes = optiscaler_override.partition("=")
            required_name = required_name.strip().casefold()
            required_mode_list = [
                item.strip().casefold()
                for item in required_modes.split(",")
                if item.strip()
            ]
            for raw_entry in str(existing_wine_overrides or "").split(";"):
                current_name, current_separator, current_modes = raw_entry.partition("=")
                if (
                    current_separator
                    and current_name.strip().casefold() == required_name
                ):
                    current_mode_list = [
                        item.strip().casefold()
                        for item in current_modes.split(",")
                        if item.strip()
                    ]
                    if current_mode_list != required_mode_list:
                        conflict_key = f"WINEDLLOVERRIDES:{required_name}"
                        environment_conflicts.append(conflict_key)
                        warnings.append(
                            f"OptiScaler replaces the existing {required_name} DLL override with the required native-first order"
                        )
                    break
            merged_overrides = merge_wine_dll_overrides(
                existing_wine_overrides,
                optiscaler_override,
            )
            if merged_overrides:
                environment["WINEDLLOVERRIDES"] = merged_overrides
                environment_sources["WINEDLLOVERRIDES"] = "optiscaler"
                reasons.append(
                    f"OptiScaler Proton override: {optiscaler_override}"
                )
        allowed_measurement_keys = {
            "MANGOHUD", "MANGOHUD_CONFIG", "MANGOHUD_CONFIGFILE"
        }
        for raw_key, raw_value in sorted((measurement_environment or {}).items()):
            key = str(raw_key)
            value = str(raw_value)
            if key not in allowed_measurement_keys or not value or "\0" in value:
                raise ValueError("invalid MangoHud baseline environment")
            if key == "MANGOHUD_CONFIGFILE" and not Path(value).is_absolute():
                raise ValueError("MangoHud baseline config path must be absolute")
            if key == "MANGOHUD_CONFIG" and value != "read_cfg":
                raise ValueError("MangoHud baseline inline config must load the private config")
            if key in inherited and str(inherited[key]) != value:
                environment_conflicts.append(key)
                warnings.append(
                    f"{key} is temporarily overridden for the baseline session"
                )
            environment[key] = value
            environment_sources[key] = "baseline_measurement"
        fps_limit_owner = "mangohud" if mangohud_fps_limit else "none"
        effective_fps_limit = mangohud_fps_limit
        inner = command
        if profile.gamemode_enabled:
            if gamemode.available:
                inner = [gamemode.executable, *inner]
                gamemode_wrapper = [gamemode.executable]
                wrappers.append("gamemode")
                reasons.append("GameMode directly wraps the game process")
            else:
                warnings.append("GameMode was requested but is unavailable")
        use_gamescope = profile.gamescope_enabled and profile.gamescope_mode != "disabled"
        if use_gamescope:
            if not gamescope.available:
                raise ValueError(
                    gamescope.message or "Gamescope was requested but is unavailable"
                )
            else:
                supported = set(gamescope.supported_options)
                flags: list[str] = []
                def add(option: str, value: object | None = None) -> None:
                    if option not in supported:
                        raise ValueError(
                            f"Gamescope configuration cannot be applied: installed version does not support {option}"
                        )
                    flags.append(option)
                    if value is not None:
                        flags.append(str(value))

                same_size = (
                    profile.gamescope_input_width == profile.gamescope_output_width
                    and profile.gamescope_input_height == profile.gamescope_output_height
                )
                if not same_size:
                    add("-w", profile.gamescope_input_width)
                    add("-h", profile.gamescope_input_height)
                add("-W", profile.gamescope_output_width)
                add("-H", profile.gamescope_output_height)
                reasons.append(
                    f"Gamescope renders at {profile.gamescope_input_width}×{profile.gamescope_input_height} "
                    f"and outputs {profile.gamescope_output_width}×{profile.gamescope_output_height}"
                )
                # Gamescope is the sole limiter owner when active.  The local
                # release documents -r as the nested refresh in frames per
                # second; emitting --framerate-limit as well created two
                # competing limiters for the same game.
                if profile.target_fps_mode != "unlimited":
                    add("-r", profile.target_fps)
                    fps_limit_owner = "gamescope"
                    effective_fps_limit = profile.target_fps
                    reasons.append(f"Gamescope owns the FPS limit at {profile.target_fps} FPS")
                if profile.gamescope_scaler != "auto":
                    add("-S", profile.gamescope_scaler)
                    reasons.append(f"Gamescope scaler: {profile.gamescope_scaler}")
                if profile.gamescope_filter != "linear":
                    add("-F", profile.gamescope_filter)
                    reasons.append(f"Gamescope filter: {profile.gamescope_filter}")
                add("-f" if profile.gamescope_fullscreen else "-b")
                reasons.append("Gamescope uses fullscreen mode" if profile.gamescope_fullscreen else "Gamescope uses borderless mode")
                if profile.target_display_id:
                    warnings.append(
                        "The selected Qt display cannot be mapped safely to Gamescope; desktop placement will be used"
                    )

                game_environment = dict(inherited)
                game_environment.update(environment)
                restore, removed, wrapper_environment_overrides = (
                    _gamescope_environment_boundary(game_environment)
                )
                measurement_keys = {
                    key
                    for key in allowed_measurement_keys
                    if environment_sources.get(key) == "baseline_measurement"
                }
                for key in measurement_keys:
                    restore[key] = environment[key]
                wrapper_environment_removed = tuple(sorted({*removed, *measurement_keys}))
                environment_restore_keys = tuple(sorted(restore))
                if restore:
                    inner = [
                        "env",
                        *(f"{key}={value}" for key, value in sorted(restore.items())),
                        *inner,
                    ]
                    reasons.append(
                        "Steam Runtime loader variables are isolated from the host Gamescope process and restored for the game"
                    )
                if measurement_keys:
                    reasons.append(
                        "Private MangoHud measurement variables are applied only to the game command"
                    )
                gamescope_wrapper = [gamescope.executable, *flags, "--"]
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
            environment_sources=environment_sources,
            environment_conflicts=tuple(environment_conflicts),
            mangohud_activation_owner=normalized_mangohud_owner,
            steam_command=tuple(command),
            gamemode_wrapper=tuple(gamemode_wrapper),
            gamescope_wrapper=tuple(gamescope_wrapper),
            wrapper_environment_removed=wrapper_environment_removed,
            wrapper_environment_overrides=wrapper_environment_overrides,
            environment_restore_keys=environment_restore_keys,
        )


__all__ = [
    "GameRuntimeLaunchPlan", "OptimizationLaunchPlanner",
    "RuntimeToolAvailability", "RuntimeToolDetector",
]
