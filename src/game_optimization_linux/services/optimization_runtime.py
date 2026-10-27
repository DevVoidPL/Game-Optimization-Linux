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
from .tweaks_capabilities import merge_tweak_environment


_HOST_WRAPPER_ENVIRONMENT_PREFIXES = ("PRESSURE_VESSEL_", "STEAM_RUNTIME_")
# Resolved by ld.so on the host and imported by pressure-vessel into the
# Steam Linux Runtime container (verified 22.10.2026 with gamemode 1.8.2/SLR 4).
GAMEMODE_CLIENT_LIBRARY = "libgamemode.so.0"
_STEAM_RUNTIME_PATH_MARKERS = (
    "pinned_libs",
    "pressure-vessel",
    "steam-runtime",
    "steamlinuxruntime",
    "steamrt",
)


_CUSTOM_REFUSED_KEYS = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "PATH", "WINEDLLOVERRIDES", "VK_INSTANCE_LAYERS", "VK_LAYER_PATH",
    "VK_ADD_LAYER_PATH", "VK_IMPLICIT_LAYER_PATH", "VK_ADD_IMPLICIT_LAYER_PATH", "VK_ICD_FILENAMES",
    "VK_DRIVER_FILES", "SteamAppId", "SteamGameId", "SteamOverlayGameId", "HOME", "USER", "DISPLAY",
    "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
})
_CUSTOM_REFUSED_PREFIXES = ("VK_LOADER_", "STEAM_COMPAT_", "PRESSURE_VESSEL_", "STEAM_RUNTIME", "GAME_OPTIMIZATION_")

_ACTIVATION_SOURCES = {
    "proton_tweaks": "Proton Tweaks", "optiscaler": "OptiScaler", "gamemode": "GameMode",
    "gamescope_hdr": "Gamescope HDR", "mangohud_profile": "MangoHud", "baseline_measurement": "MangoHud measurement",
    "gpu_memory": "GPU memory",
}


def _classify_environment(environment: Mapping[str, str], sources: Mapping[str, str], inherited: Mapping[str, str],
                          fg_entries: Sequence[Mapping[str, Any]], fg_warnings: Sequence[str], *,
                          restore_keys: Sequence[str] = (), removed: Sequence[str] = (), unset: Sequence[str] = (),
                          selected_fg: str = "") -> tuple[dict[str, str], ...]:
    """One row per planned variable. Suppression is never reported as an enabled feature.

    Categories: activation (selected feature), inherited (inherited value preserved),
    suppression (targeted conflict suppression), boundary (launcher/runtime boundary),
    unresolved (conflict GameOpti did not change).
    """
    rows: list[dict[str, str]] = []
    guards = {(str(e.get("target")), str(e.get("key"))): e for e in fg_entries}
    def guard_detail(guard):
        return str(guard.get("layer", "")) + (f" ({guard.get('detail')})" if guard.get("detail") else "")
    def row(key, value, category, owner, target, code, detail="", english=""):
        rows.append({"key": key, "value": value, "category": category, "owner": owner, "target": target,
                     "code": code, "detail": detail, "gameOnly": "1" if key in restore_keys and target == "game" and category != "boundary" else "",
                     "reason": english or (f"{code}: {detail}" if detail else code)})
    for key in sorted(environment):
        value, source = str(environment[key]), str(sources.get(key, ""))
        guard = guards.get(("game", key))
        if guard is not None:
            row(key, value, "suppression", str(guard.get("backendName", "")), "game", str(guard.get("reason")), guard_detail(guard))
        elif source == "frame_generation":
            row(key, value, "activation", "Frame generation", "game", "fg_selected", selected_fg)
        elif source == "post_processing":
            if key in {"ENABLE_VKBASALT", "DISABLE_VKBASALT"} and not (key == "ENABLE_VKBASALT" and value == "1"):
                row(key, value, "suppression", "vkBasalt", "game",
                    "vkbasalt_duplicate" if environment.get("ENABLE_VKBASALT") == "1" else "vkbasalt_inherited")
            elif key == "WINEDLLOVERRIDES" and "VK_INSTANCE_LAYERS" not in environment:
                row(key, value, "activation", "ReShade", "game", "reshade_override")
            else:
                row(key, value, "activation", "vkBasalt", "game", "vkbasalt_layer")
        elif source == "custom_launch":
            from .custom_launch import redact
            row(key, redact(key, value), "activation", "Custom launch", "game", "custom_env")
        elif source in _ACTIVATION_SOURCES:
            row(key, value, "activation", _ACTIVATION_SOURCES[source], "game", "feature", source)
        else:
            row(key, value, "inherited", "", "game", "inherited")
    for (target, key), guard in sorted(guards.items()):
        if target == "compositor":
            row(key, str(guard.get("value", "")), "suppression", str(guard.get("backendName", "")), "compositor",
                str(guard.get("reason")), guard_detail(guard))
    for key in sorted(set(restore_keys) - set(environment)):
        row(key, "<preserved>", "boundary", "Gamescope", "game", "boundary_restored")
    for key in sorted(set(removed) - set(restore_keys) - set(environment)):
        row(key, "", "boundary", "Gamescope", "compositor", "boundary_removed")
    for key in unset:
        row(key, "", "boundary", "Gamescope", "game", "boundary_unset")
    for warning in fg_warnings:
        row("", "", "unresolved", "Frame generation", "", "unresolved", "", str(warning))
    return tuple(rows)


def _gamescope_environment_boundary(
    environment: Mapping[str, str], *, game_only_preloads: Sequence[str] = (),
    game_only_layers: Sequence[str] = (), game_only_paths: Sequence[str] = (),
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str]]:
    """Isolate identified Steam/game injections, retaining unrelated host settings.

    Full original values are restored before the actual Steam command, never
    substituted with values from the GUI process. Unknown user entries survive.
    """
    restore: dict[str, str] = {}
    removed: list[str] = []
    overrides: dict[str, str] = {}
    def runtime_path(value):
        parts = Path(value).parts
        return any(part.casefold() in {*_STEAM_RUNTIME_PATH_MARKERS, "ubuntu12_32", "ubuntu12_64"}
                   or part.casefold().startswith(("steamlinuxruntime_", "pinned_libs_")) for part in parts)

    for key, raw_value in environment.items():
        value = str(raw_value)
        outer = value
        if key in {"STEAM_LD_LIBRARY_PATH", "STEAM_RUNTIME", "SYSTEM_LD_LIBRARY_PATH", "SYSTEM_PATH"} or key.startswith(_HOST_WRAPPER_ENVIRONMENT_PREFIXES):
            outer = ""
        elif key == "LD_PRELOAD":
            entries = [x for x in re.split(r"[:\s]+", value) if x]
            outer = ":".join(x for x in entries if Path(x).name != "gameoverlayrenderer.so"
                             and not runtime_path(x) and not any(Path(x).name.startswith(name) for name in game_only_preloads))
        elif key == "VK_INSTANCE_LAYERS":
            outer = ":".join(x for x in value.split(":") if x and x not in game_only_layers)
        elif key in {"PATH", "LD_LIBRARY_PATH", "LIBGL_DRIVERS_PATH", "VK_ADD_LAYER_PATH", "VK_LAYER_PATH", "VK_IMPLICIT_LAYER_PATH", "VK_ADD_IMPLICIT_LAYER_PATH", "VK_DRIVER_FILES", "VK_ICD_FILENAMES"}:
            outer = ":".join(x for x in value.split(":") if not runtime_path(x) and x not in game_only_paths)
            if key == "PATH" and value and not outer:
                raise ValueError("Gamescope cannot be launched safely because PATH contains only Steam Runtime entries")
        if outer != value:
            restore[key] = value
            removed.append(key)
            if outer:
                overrides[key] = outer
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
    environment_unset_keys: tuple[str, ...] = ()
    frame_generation: Mapping[str, Any] = field(default_factory=dict)
    environment_classification: tuple[Mapping[str, str], ...] = ()
    custom_arguments: tuple[str, ...] = ()
    pre_launch: tuple[Any, ...] = ()
    post_exit: tuple[Any, ...] = ()
    custom_rows: tuple[Mapping[str, str], ...] = ()

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
            "environmentUnsetForGame": list(self.environment_unset_keys),
            "environmentClassification": [dict(x) for x in self.environment_classification],
            "customArguments": list(self.custom_arguments),
            "preLaunch": [{"argv": list(c.argv), "timeout": c.timeout, "onFailure": c.on_failure,
                           "where": "host_runner"} for c in self.pre_launch],
            "postExit": [{"argv": list(c.argv), "timeout": c.timeout, "runIfNotStarted": c.run_if_not_started,
                          "where": "host_runner"} for c in self.post_exit],
            "frameGeneration": {
                "backend": str(self.frame_generation.get("backend", "")),
                "entries": [dict(x) for x in self.frame_generation.get("entries", ())],
                "detected": [dict(x) for x in self.frame_generation.get("detected", ())],
            },
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
        gamemode_preload_client: bool = False,
        post_processing_environment: Mapping[str, str] | None = None,
        gpu_memory_environment: Mapping[str, str] | None = None,
        frame_generation_environment: Mapping[str, str] | None = None,
        frame_generation_prefix: Sequence[str] = (),
        frame_generation_report: Mapping[str, Any] | None = None,
        custom_launch: Any = None,
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
        environment_unset_keys: tuple[str, ...] = ()
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
        for raw_key, raw_value in sorted(merge_tweak_environment(inherited, proton_environment or {}).items()):
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
        # OptiScaler sets one entry; DLSS Enabler may set two (version;dxgi).
        required_entries = [e for e in str(optiscaler_override or "").split(";") if e.strip()]
        for required_entry in required_entries:
            required_name, _separator, required_modes = required_entry.partition("=")
            required_name = required_name.strip().casefold()
            required_mode_list = [
                item.strip().casefold() for item in required_modes.split(",") if item.strip()
            ]
            for raw_entry in str(existing_wine_overrides or "").split(";"):
                current_name, current_separator, current_modes = raw_entry.partition("=")
                if current_separator and current_name.strip().casefold() == required_name:
                    current_mode_list = [
                        item.strip().casefold() for item in current_modes.split(",") if item.strip()
                    ]
                    if current_mode_list != required_mode_list:
                        environment_conflicts.append(f"WINEDLLOVERRIDES:{required_name}")
                        warnings.append(
                            f"OptiScaler replaces the existing {required_name} DLL override with the required native-first order"
                        )
                    break
        if optiscaler_override:
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
        post_processing_environment = dict(post_processing_environment or {})
        if post_processing_environment.get("ENABLE_VKBASALT") != "1":
            # ReShade or a neutral vkBasalt profile does not activate vkBasalt.
            # Its implicit-layer gate is only needed when the inherited
            # environment concretely enables vkBasalt (ENABLE_VKBASALT=1).
            post_processing_environment.pop("ENABLE_VKBASALT", None)
            post_processing_environment.pop("DISABLE_VKBASALT", None)
            if str(inherited.get("ENABLE_VKBASALT", "")) == "1" and "DISABLE_VKBASALT" not in inherited:
                post_processing_environment["DISABLE_VKBASALT"] = "1"
        for key, value in (post_processing_environment or {}).items():
            if key not in {"ENABLE_VKBASALT", "DISABLE_VKBASALT", "VKBASALT_CONFIG_FILE",
                           "VKBASALT_LOG_LEVEL", "VKBASALT_LOG_FILE", "VK_ADD_LAYER_PATH",
                           "VK_INSTANCE_LAYERS", "WINEDLLOVERRIDES"} or "\0" in value:
                raise ValueError("Invalid post-processing launch environment")
            if key == "WINEDLLOVERRIDES":
                value = merge_wine_dll_overrides(environment.get(key, existing_wine_overrides), value)
            elif key in {"VK_ADD_LAYER_PATH", "VK_INSTANCE_LAYERS"}:
                value = ":".join(dict.fromkeys(filter(None, (value + ":" + str(inherited.get(key, ""))).split(":"))))
            environment[key] = value
            environment_sources[key] = "post_processing"
        if post_processing_environment:
            reasons.append("Per-game post-processing is configured; loading and effects require runtime verification")
        for key, value in (gpu_memory_environment or {}).items():
            if key != "VKD3D_CONFIG" or "\0" in value:
                raise ValueError("Invalid GPU-memory launch environment")
            environment[key] = value
            environment_sources[key] = "gpu_memory"
        if gpu_memory_environment:
            reasons.append("Per-game vkd3d memory-pressure trade-off; improvement requires an A/B measurement")
        from .frame_generation import ALLOWED_ENV
        from .frame_generation_isolation import discover_layers, isolation_plan
        fg_report = dict(frame_generation_report or {})
        fg_entries = [dict(entry) for entry in fg_report.get("entries", ())]
        fg_removed_layers = {entry["layer"] for entry in fg_entries if entry.get("key") == "VK_INSTANCE_LAYERS"}
        for key, value in (frame_generation_environment or {}).items():
            if key not in ALLOWED_ENV or not isinstance(value, str) or "\0" in value:
                raise ValueError("Invalid frame-generation launch environment")
            if key == "VK_ADD_IMPLICIT_LAYER_PATH":
                value = ":".join(dict.fromkeys(filter(None, (value + ":" + str(inherited.get(key, ""))).split(":"))))
            elif key == "VK_INSTANCE_LAYERS":
                # Remove exactly the generator entries the isolation removed;
                # keep the order and the post-processing layer of this plan.
                removed_tokens = fg_removed_layers or (set(str(inherited.get(key, "")).split(":")) - set(value.split(":")))
                base = environment.get(key, str(inherited.get(key, "")))
                value = ":".join(dict.fromkeys(x for x in base.split(":") if x and x not in removed_tokens))
            environment[key] = value
            environment_sources[key] = "frame_generation"
        if frame_generation_prefix or any((frame_generation_environment or {}).get(k) == "1" for k in ("AFMF_ENABLE", "ENABLE_MAKO")) or (frame_generation_environment or {}).get("LSFGVK_CONFIG"):
            reasons.append("External frame generation is selected; generation requires separate runtime evidence")
        elif fg_entries:
            reasons.append(f"Frame generation Off: {len(fg_entries)} targeted guard(s) block a concrete generator activation in the game; see technical details")
        fg_warnings = [str(item) for item in fg_report.get("warnings", ())]
        warnings.extend(fg_warnings)
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
        # Per-game custom launch configuration. It never overrides a value a
        # GameOpti feature set, and list-valued runtime variables are refused.
        custom_rows: list[dict[str, str]] = []
        custom_env_keys: set[str] = set()
        custom_wrapper: list[str] = []
        custom_arguments: tuple[str, ...] = ()
        custom_pre: tuple[Any, ...] = ()
        custom_post: tuple[Any, ...] = ()
        if custom_launch is not None and not custom_launch.empty:
            from .custom_launch import MANAGED_WRAPPERS, redact
            for key, value in custom_launch.active_environment().items():
                if key in _CUSTOM_REFUSED_KEYS or key.startswith(_CUSTOM_REFUSED_PREFIXES):
                    reason = f"{key} is a launcher/runtime variable; use the dedicated GameOpti feature or a wrapper script"
                elif key in environment:
                    reason = f"{key} is already set by {environment_sources.get(key, 'GameOpti')}; the custom value is not applied"
                else:
                    environment[key] = value
                    environment_sources[key] = "custom_launch"
                    custom_env_keys.add(key)
                    continue
                environment_conflicts.append(f"custom:{key}")
                warnings.append(reason)
                custom_rows.append({"key": key, "value": redact(key, value), "category": "unresolved",
                                    "owner": "Custom launch", "target": "game", "code": "custom_refused", "reason": reason})
            wrapper = list(custom_launch.active_wrapper())
            if wrapper:
                name = Path(wrapper[0]).name
                if name in MANAGED_WRAPPERS:
                    reason = f"{MANAGED_WRAPPERS[name]} is placed by GameOpti itself; enable it in its own section instead of a custom wrapper"
                    warnings.append(reason)
                    custom_rows.append({"key": "", "value": " ".join(wrapper), "category": "unresolved", "owner": "Custom launch",
                                        "target": "", "code": "custom_refused", "reason": reason})
                else:
                    custom_wrapper = wrapper
                    reasons.append("Custom wrapper runs on the host around the Steam launch command, inside GameMode/Gamescope")
            custom_arguments = custom_launch.active_arguments()
            custom_pre, custom_post = custom_launch.active_pre(), custom_launch.active_post()
            if custom_env_keys:
                reasons.append(f"Custom environment: {', '.join(sorted(custom_env_keys))} (game process)")
        fps_limit_owner = "mangohud" if mangohud_fps_limit else "none"
        effective_fps_limit = mangohud_fps_limit
        if any(not isinstance(item, str) or "\0" in item for item in frame_generation_prefix):
            raise ValueError("Invalid frame-generation wrapper")
        inner = [*custom_wrapper, *frame_generation_prefix, *command, *custom_arguments]
        if custom_wrapper: wrappers.append("custom")
        if frame_generation_prefix: wrappers.append("frame_generation")
        if profile.gamemode_enabled:
            if gamemode.available:
                inner = [gamemode.executable, *inner]
                gamemode_wrapper = [gamemode.executable]
                wrappers.append("gamemode")
                reasons.append("GameMode directly wraps the game process")
                if gamemode_preload_client:
                    # gamemoderun preloads only libgamemodeauto, which then
                    # dlopen()s libgamemode. Inside the Steam Linux Runtime
                    # (pressure-vessel) container that dlopen fails, so the
                    # game never registers. Preloaded libraries are imported
                    # into the container, so the client library is preloaded too.
                    current = str(environment.get("LD_PRELOAD", inherited.get("LD_PRELOAD", "")) or "")
                    entries = [item for item in re.split(r"[:\s]+", current) if item]
                    if not any(Path(item).name.startswith("libgamemode.so") for item in entries):
                        environment["LD_PRELOAD"] = ":".join([GAMEMODE_CLIENT_LIBRARY, *entries])
                        environment_sources["LD_PRELOAD"] = "gamemode"
                        reasons.append(
                            "GameMode client library is preloaded so the game inside the Steam Linux Runtime registers with GameMode"
                        )
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
                    _gamescope_environment_boundary(game_environment,
                        game_only_preloads=(("libgamemode.so", "libgamemodeauto.so") if profile.gamemode_enabled else ())
                                           + (("libMangoHud.so",) if normalized_mangohud_owner == "mangoapp" else ()),
                        game_only_layers=(*str((post_processing_environment or {}).get("VK_INSTANCE_LAYERS", "")).split(":"),
                                          *(("VK_LAYER_MANGOHUD_overlay",) if normalized_mangohud_owner == "mangoapp" else ())),
                        game_only_paths=tuple(x for source in (post_processing_environment or {}, frame_generation_environment or {})
                                              for key in ("VK_ADD_LAYER_PATH", "VK_ADD_IMPLICIT_LAYER_PATH")
                                              for x in str(source.get(key, "")).split(":") if x))
                )
                measurement_keys = {
                    key
                    for key in allowed_measurement_keys
                    if environment_sources.get(key) == "baseline_measurement"
                }
                for key in measurement_keys:
                    restore[key] = environment[key]
                post_keys = {key for key in (post_processing_environment or {})
                             if key != "WINEDLLOVERRIDES"}
                post_keys.update((gpu_memory_environment or {}).keys())
                post_keys.update((frame_generation_environment or {}).keys())
                # Custom variables belong to the game; the compositor keeps the
                # inherited value, if any.
                post_keys.update(custom_env_keys)
                for key in custom_env_keys:
                    if key in inherited:
                        wrapper_environment_overrides[key] = str(inherited[key])
                shared_layer_keys = {"VK_INSTANCE_LAYERS", "VK_ADD_LAYER_PATH", "VK_ADD_IMPLICIT_LAYER_PATH", "VK_LOADER_LAYERS_DISABLE", "VK_LOADER_LAYERS_ENABLE", "VK_LOADER_LAYERS_ALLOW"}
                for key in post_keys:
                    restore[key] = environment[key]
                    if key in shared_layer_keys:
                        if key not in removed:
                            wrapper_environment_overrides.setdefault(key, str(environment[key]))
                for key in mangoapp_removed:
                    restore.pop(key, None)
                wrapper_environment_removed = tuple(
                    sorted({*removed, *measurement_keys, *mangoapp_removed, *post_keys})
                )
                # Gamescope is a separate Vulkan process: analyze the environment it
                # actually receives. Its guards must not change the game's environment.
                outer = {str(k): str(v) for k, v in game_environment.items() if k not in set(wrapper_environment_removed)}
                outer.update(wrapper_environment_overrides)
                selected_fg = str(fg_report.get("backend", ""))
                compositor = isolation_plan(outer, discover_layers(outer), selected="off", target="compositor",
                                            protect="" if selected_fg in {"", "off"} else selected_fg)
                unset_for_game = []
                for key, value in compositor["environment"].items():
                    wrapper_environment_overrides[key] = value
                    if key not in game_environment:
                        unset_for_game.append(key)
                    elif key not in restore and str(game_environment[key]) != value:
                        restore[key] = str(game_environment[key])
                fg_entries.extend(compositor["entries"])
                warnings.extend(compositor["warnings"])
                fg_warnings.extend(compositor["warnings"])
                if compositor["entries"]:
                    reasons.append(f"Gamescope compositor: {len(compositor['entries'])} targeted generator guard(s); the game's own environment is restored separately")
                environment_unset_keys = tuple(sorted(unset_for_game))
                environment_restore_keys = tuple(sorted(restore))
                if restore or environment_unset_keys:
                    inner = [
                        "env",
                        *(item for key in environment_unset_keys for item in ("-u", key)),
                        *(f"{key}={value}" for key, value in sorted(restore.items())),
                        *inner,
                    ]
                if restore:
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
            environment_unset_keys=environment_unset_keys,
            custom_arguments=tuple(custom_arguments),
            pre_launch=tuple(custom_pre),
            post_exit=tuple(custom_post),
            custom_rows=tuple(custom_rows),
            frame_generation={"backend": str(fg_report.get("backend", "")), "entries": tuple(fg_entries),
                              "detected": tuple(dict(x) for x in fg_report.get("detected", ())),
                              "warnings": tuple(fg_warnings)},
            environment_classification=(*_classify_environment(
                environment, environment_sources, inherited, fg_entries, fg_warnings,
                restore_keys=environment_restore_keys, removed=wrapper_environment_removed,
                unset=environment_unset_keys, selected_fg=str(fg_report.get("backend", ""))), *custom_rows),
        )


__all__ = [
    "GameRuntimeLaunchPlan", "OptimizationLaunchPlanner",
    "RuntimeToolAvailability", "RuntimeToolDetector",
]
