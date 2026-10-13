"""Tool detection and shell-free launch-plan composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.models.optimization_profile import GAMESCOPE_SHARPNESS_FILTERS

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
    # Values the local release lists for -F / -S (empty = not verified).
    supported_filters: tuple[str, ...] = ()
    supported_scalers: tuple[str, ...] = ()
    supported_backends: tuple[str, ...] = ()
    # Enable variable of the installed Gamescope WSI layer manifest, if found.
    wsi_environment: tuple[str, str] | tuple[()] = ()

    @property
    def help_verified(self) -> bool:
        """True when ``--help`` was parsed; otherwise only basic flags are used."""

        return bool(self.supported_options)

    @property
    def effective_options(self) -> tuple[str, ...]:
        return self.supported_options if self.help_verified else GAMESCOPE_BASIC_OPTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "available": self.available,
            "executable": self.executable, "version": self.version,
            "supportedOptions": list(self.effective_options), "message": self.message,
            "supportedFilters": list(self.supported_filters),
            "supportedScalers": list(self.supported_scalers),
            "supportedBackends": list(self.supported_backends),
            "helpVerified": self.help_verified,
            "wsiEnvironment": "=".join(self.wsi_environment) if self.wsi_environment else "",
        }


# Gamescope flags GameOpti may emit. Only those found in the local
# `gamescope --help` are ever used.
GAMESCOPE_KNOWN_OPTIONS = (
    "-W", "-H", "-w", "-h", "-r", "--framerate-limit", "-f", "-b",
    "-S", "-F", "--display-index", "--sharpness", "--fsr-sharpness",
    "--adaptive-sync", "-g", "--hdr-enabled", "--mangoapp",
    "-m", "-o", "--backend", "--force-windows-fullscreen",
    "--hdr-sdr-content-nits", "--sdr-gamut-wideness",
    # Older releases spell the inverse tone mapping switch without "d".
    "--hdr-itm-enabled", "--hdr-itm-enable",
    "--hdr-itm-sdr-nits", "--hdr-itm-target-nits",
    "--force-grab-cursor", "-C", "-s", "--expose-wayland", "-e",
)
# Used when --help cannot be read: the long-standing core of the integration.
GAMESCOPE_BASIC_OPTIONS = ("-W", "-H", "-w", "-h", "-r", "-f", "-b", "-S")
# Only nested desktop backends; drm/headless/openvr are never emitted.
GAMESCOPE_DESKTOP_BACKENDS = ("wayland", "sdl")
GAMESCOPE_WSI_LAYER_DIRECTORIES = (
    Path("/usr/share/vulkan/implicit_layer.d"),
    Path("/etc/vulkan/implicit_layer.d"),
    Path("/usr/local/share/vulkan/implicit_layer.d"),
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def parse_gamescope_backends(help_text: str) -> tuple[str, ...]:
    """Backend names listed under ``--backend`` as ``name => description``."""

    lines = _ANSI_ESCAPE.sub("", help_text or "").splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*--backend\b", line):
            values: list[str] = []
            for following in lines[index + 1:]:
                match = re.match(r"^\s+([a-z][a-z0-9_-]*)\s+=>", following)
                if not match:
                    break
                values.append(match.group(1).casefold())
            return tuple(values)
    return ()


def detect_gamescope_wsi(
    directories: Sequence[Path] = GAMESCOPE_WSI_LAYER_DIRECTORIES,
) -> tuple[str, str] | tuple[()]:
    """Read the enable variable from the installed Gamescope WSI layer manifest."""

    for directory in directories:
        try:
            manifests = sorted(Path(directory).glob("*gamescope_wsi*.json"))
        except OSError:
            continue
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            layer = data.get("layer") if isinstance(data, Mapping) else None
            enable = layer.get("enable_environment") if isinstance(layer, Mapping) else None
            if isinstance(enable, Mapping) and len(enable) == 1:
                key, value = next(iter(enable.items()))
                if re.fullmatch(r"[A-Z][A-Z0-9_]*", str(key)) and str(value):
                    return str(key), str(value)
    return ()


def parse_gamescope_help(help_text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (flags, filter values, scaler values) listed by `gamescope --help`."""
    text = _ANSI_ESCAPE.sub("", help_text or "")
    options = tuple(
        option for option in GAMESCOPE_KNOWN_OPTIONS
        if re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", text)
    )

    def values(flag: str) -> tuple[str, ...]:
        match = re.search(rf"^\s*{re.escape(flag)},[^\n(]*\(([^)]*)\)", text, re.MULTILINE)
        if not match:
            return ()
        return tuple(item.strip().casefold() for item in match.group(1).split(",") if item.strip())

    return options, values("-F"), values("-S")


def parse_gamescope_version(output: str) -> str:
    text = _ANSI_ESCAPE.sub("", output or "")
    match = re.search(r"gamescope version\s+(\S+)", text)
    return match.group(1) if match else text.strip().splitlines()[-1].strip() if text.strip() else ""


class RuntimeToolDetector:
    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        host_service: object | None = None,
        wsi_layer_directories: Sequence[Path] = GAMESCOPE_WSI_LAYER_DIRECTORIES,
    ) -> None:
        self._which = which
        self._run = runner
        self._host_service = host_service
        self._wsi_layer_directories = tuple(wsi_layer_directories)
        self._cache: tuple[RuntimeToolAvailability, RuntimeToolAvailability] | None = None
        self._refresh = False

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
        help_text = ((help_result.stdout or "") + "\n" + (help_result.stderr or "")) if help_result else ""
        version = parse_gamescope_version(
            ((version_result.stdout or "") + "\n" + (version_result.stderr or "")) if version_result else ""
        )
        supported, filters, scalers = parse_gamescope_help(help_text)
        runs = help_result is not None or version_result is not None
        return RuntimeToolAvailability(
            "Gamescope", runs, executable, version, supported,
            "Gamescope is available" if supported
            else "Gamescope --help could not be read; only basic options are available"
            if runs else "gamescope --help failed",
            filters, scalers, parse_gamescope_backends(help_text),
            detect_gamescope_wsi(self._wsi_layer_directories),
        )

    def _host_tool(self, key: str, label: str) -> RuntimeToolAvailability:
        query = getattr(self._host_service, "tool_info", None)
        if not callable(query):
            return RuntimeToolAvailability(
                label, False, message=f"{label} host detection is unavailable"
            )
        try:
            raw = query(key, refresh=True) if self._refresh else query(key)
        except TypeError:
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
        def string_list(key: str) -> tuple[str, ...]:
            value = raw.get(key)
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return ()
            return tuple(str(item).casefold() for item in value if isinstance(item, str))

        version = str(raw.get("version") or "")
        if key == "gamescope":
            version = parse_gamescope_version(version)
        message = str(raw.get("diagnostic_message") or "")
        if key == "gamescope" and available and not options:
            message = "Gamescope --help could not be read; only basic options are available"
        return RuntimeToolAvailability(
            label,
            available,
            executable if available else "",
            version,
            options,
            message,
            string_list("supported_filters"),
            string_list("supported_scalers"),
            string_list("supported_backends"),
            # The layer manifest lives on the host; the host runner reads it
            # itself at launch, so the sandbox never guesses the variable.
            (),
        )

    def detect(self, *, refresh: bool = False) -> tuple[RuntimeToolAvailability, RuntimeToolAvailability]:
        """Probe once per application run; ``refresh`` re-runs the probes."""

        if self._cache is None or refresh:
            self._refresh = refresh
            try:
                self._cache = (self.gamemode(), self.gamescope())
            finally:
                self._refresh = False
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
        mangohud_config_path: str | Path = "",
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
        mangoapp_removed: set[str] = set()
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
                supported = set(gamescope.effective_options)
                flags: list[str] = []
                def add(option: str, value: object | None = None) -> None:
                    if option not in supported:
                        raise ValueError(
                            f"Gamescope configuration cannot be applied: installed version does not support {option}"
                        )
                    flags.append(option)
                    if value is not None:
                        flags.append(str(value))

                def optional(option: str, value: object | None = None) -> bool:
                    # Newer desktop options never block a launch: an option
                    # missing from the local --help is skipped, not guessed.
                    if option not in supported:
                        warnings.append(
                            f"Installed Gamescope does not support {option}; the option was skipped"
                        )
                        return False
                    flags.append(option)
                    if value is not None:
                        flags.append(str(value))
                    return True

                def decimal(value: float) -> str:
                    return f"{value:g}"

                same_size = (
                    profile.gamescope_input_width == profile.gamescope_output_width
                    and profile.gamescope_input_height == profile.gamescope_output_height
                )
                emit_input = not profile.gamescope_input_auto and (
                    profile.gamescope_output_auto or not same_size
                )
                if emit_input:
                    add("-w", profile.gamescope_input_width)
                    add("-h", profile.gamescope_input_height)
                if not profile.gamescope_output_auto:
                    add("-W", profile.gamescope_output_width)
                    add("-H", profile.gamescope_output_height)
                render = (
                    "automatic size" if profile.gamescope_input_auto
                    else f"{profile.gamescope_input_width}×{profile.gamescope_input_height}"
                )
                output = (
                    "the native size" if profile.gamescope_output_auto
                    else f"{profile.gamescope_output_width}×{profile.gamescope_output_height}"
                )
                reasons.append(f"Gamescope renders at {render} and outputs {output}")
                # Gamescope is the sole limiter owner when active.  -r is the
                # nested refresh in frames per second.  Without an explicit
                # virtual refresh the profile FPS target keeps driving -r,
                # exactly as before; --framerate-limit is only an explicit
                # user choice.
                refresh = 0
                if profile.gamescope_nested_refresh > 0:
                    refresh = profile.gamescope_nested_refresh
                    add("-r", refresh)
                    reasons.append(f"Gamescope virtual refresh rate: {refresh} Hz")
                elif profile.target_fps_mode != "unlimited":
                    refresh = profile.target_fps
                    add("-r", refresh)
                    reasons.append(f"Gamescope owns the FPS limit at {profile.target_fps} FPS")
                limit = 0
                if profile.gamescope_framerate_limit > 0 and optional(
                    "--framerate-limit", profile.gamescope_framerate_limit
                ):
                    limit = profile.gamescope_framerate_limit
                    reasons.append(
                        f"Gamescope framerate limit {limit} FPS (applied as a divisor of the refresh rate)"
                    )
                if refresh or limit:
                    fps_limit_owner = "gamescope"
                    effective_fps_limit = limit or refresh
                if profile.gamescope_unfocused_refresh > 0 and optional(
                    "-o", profile.gamescope_unfocused_refresh
                ):
                    reasons.append(
                        f"Gamescope limits the unfocused game to {profile.gamescope_unfocused_refresh} FPS"
                    )
                if profile.gamescope_scaler != "auto":
                    if gamescope.supported_scalers and profile.gamescope_scaler not in gamescope.supported_scalers:
                        raise ValueError(
                            f"Gamescope configuration cannot be applied: installed version does not support scaler {profile.gamescope_scaler}"
                        )
                    add("-S", profile.gamescope_scaler)
                    reasons.append(f"Gamescope scaler: {profile.gamescope_scaler}")
                if profile.gamescope_filter != "linear":
                    # A filter is only emitted when the local --help lists it.
                    if profile.gamescope_filter not in gamescope.supported_filters:
                        raise ValueError(
                            f"Gamescope configuration cannot be applied: installed version does not support filter {profile.gamescope_filter}"
                        )
                    add("-F", profile.gamescope_filter)
                    reasons.append(f"Gamescope filter: {profile.gamescope_filter}")
                    if (
                        profile.gamescope_filter in GAMESCOPE_SHARPNESS_FILTERS
                        and profile.gamescope_sharpness >= 0
                    ):
                        sharpness_flag = (
                            "--sharpness" if "--sharpness" in supported else "--fsr-sharpness"
                        )
                        add(sharpness_flag, profile.gamescope_sharpness)
                        reasons.append(
                            f"Gamescope sharpness {profile.gamescope_sharpness} (0 sharpest, 20 softest)"
                        )
                if profile.gamescope_max_scale > 0:
                    optional("-m", decimal(profile.gamescope_max_scale))
                # Exactly one window mode; windowed passes neither -f nor -b.
                if profile.gamescope_window_mode == "fullscreen":
                    add("-f")
                    reasons.append("Gamescope uses fullscreen mode")
                elif profile.gamescope_window_mode == "borderless":
                    add("-b")
                    reasons.append("Gamescope uses borderless mode")
                else:
                    reasons.append("Gamescope uses a normal window")
                if profile.gamescope_display_index >= 0:
                    optional("--display-index", profile.gamescope_display_index)
                if profile.gamescope_force_windows_fullscreen:
                    optional("--force-windows-fullscreen")
                if profile.gamescope_backend != "auto":
                    if (
                        profile.gamescope_backend in GAMESCOPE_DESKTOP_BACKENDS
                        and profile.gamescope_backend in gamescope.supported_backends
                    ):
                        optional("--backend", profile.gamescope_backend)
                    else:
                        warnings.append(
                            f"Installed Gamescope does not list the {profile.gamescope_backend} backend; automatic backend is used"
                        )
                if profile.gamescope_adaptive_sync:
                    add("--adaptive-sync")
                    reasons.append("Gamescope requests adaptive sync; display support is not verified")
                if profile.gamescope_grab_keyboard:
                    add("-g")
                    reasons.append("Gamescope grabs the keyboard")
                if profile.gamescope_force_grab_cursor:
                    optional("--force-grab-cursor")
                if profile.gamescope_hide_cursor_delay > 0:
                    optional("-C", profile.gamescope_hide_cursor_delay)
                if profile.gamescope_mouse_sensitivity > 0:
                    optional("-s", decimal(profile.gamescope_mouse_sensitivity))
                if profile.gamescope_expose_wayland:
                    optional("--expose-wayland")
                if profile.gamescope_steam_integration:
                    optional("-e")
                if profile.gamescope_hdr:
                    add("--hdr-enabled")
                    warnings.append(
                        "Gamescope HDR is experimental and needs a compatible display, driver and Gamescope WSI"
                    )
                    # HDR sub-options only apply with HDR on; ITM only to SDR input.
                    if profile.gamescope_hdr_sdr_content_nits > 0:
                        optional("--hdr-sdr-content-nits", profile.gamescope_hdr_sdr_content_nits)
                    if profile.gamescope_sdr_gamut_wideness >= 0:
                        optional("--sdr-gamut-wideness", decimal(profile.gamescope_sdr_gamut_wideness))
                    if profile.gamescope_hdr_itm:
                        itm_flag = next(
                            (flag for flag in ("--hdr-itm-enabled", "--hdr-itm-enable") if flag in supported),
                            "",
                        )
                        if itm_flag:
                            flags.append(itm_flag)
                            reasons.append("Gamescope converts SDR games to HDR (inverse tone mapping)")
                            if profile.gamescope_hdr_itm_sdr_nits > 0:
                                optional("--hdr-itm-sdr-nits", profile.gamescope_hdr_itm_sdr_nits)
                            if profile.gamescope_hdr_itm_target_nits > 0:
                                optional("--hdr-itm-target-nits", profile.gamescope_hdr_itm_target_nits)
                        else:
                            warnings.append(
                                "Installed Gamescope does not support inverse tone mapping; the option was skipped"
                            )
                    if gamescope.wsi_environment:
                        key, value = gamescope.wsi_environment
                        if inherited.get(key) not in (None, value):
                            environment_conflicts.append(key)
                        environment[key] = value
                        environment_sources[key] = "gamescope_hdr"
                        reasons.append(f"Gamescope WSI layer enabled for HDR clients ({key}={value})")
                if normalized_mangohud_owner in {"per_application_config", "steam_environment"}:
                    if "--mangoapp" in supported:
                        # One overlay only: mangoapp draws MangoHud in
                        # Gamescope, so the in-game layer is switched off.
                        flags.append("--mangoapp")
                        normalized_mangohud_owner = "mangoapp"
                        mangoapp_removed = {"MANGOHUD", "MANGOHUD_CONFIG"}
                        if mangohud_config_path:
                            environment["MANGOHUD_CONFIGFILE"] = str(mangohud_config_path)
                            environment_sources["MANGOHUD_CONFIGFILE"] = "mangohud_profile"
                        reasons.append(
                            "Gamescope mangoapp is the sole MangoHud activation owner"
                        )
                        if fps_limit_owner == "mangohud":
                            warnings.append(
                                "The MangoHud FPS limit is not applied while Gamescope draws MangoHud with --mangoapp"
                            )
                    else:
                        warnings.append(
                            "Installed Gamescope has no --mangoapp; MangoHud stays inside the game and may not work with Gamescope"
                        )
                if profile.target_display_id and profile.gamescope_display_index < 0:
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
                for key in mangoapp_removed:
                    restore.pop(key, None)
                wrapper_environment_removed = tuple(
                    sorted({*removed, *measurement_keys, *mangoapp_removed})
                )
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
