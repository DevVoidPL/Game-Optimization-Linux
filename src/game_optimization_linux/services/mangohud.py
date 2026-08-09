"""Per-AppID MangoHud persistence, config generation, and launch activation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from game_optimization_linux.config import GAMES_CONFIG_DIR, MANGOHUD_LOG_DIR
from game_optimization_linux.models import Game, MangoHudProfile, validate_game_key

from .game_executable import ExecutableResolution, GameExecutableResolver
from .host_bootstrap import host_home_directory
from .steam_launch import running_native_steam_environment, uses_flatpak_steam


PROFILE_FILE_NAME = "mangohud.json"
CONFIG_FILE_NAME = "MangoHud.conf"
FLATPAK_STEAM_APP_ID = "com.valvesoftware.Steam"
FLATPAK_MANGOHUD_PREFIX = "org.freedesktop.Platform.VulkanLayer.MangoHud"
MANAGED_HEADER = "# Managed by Game Optimization Linux"

METRIC_CONFIG_KEYS: dict[str, str] = {
    "fps": "fps",
    "frametime": "frametime",
    "gpu_usage": "gpu_stats",
    "gpu_temperature": "gpu_temp",
    "gpu_clock": "gpu_core_clock",
    "gpu_power": "gpu_power",
    "vram": "vram",
    "cpu_usage": "cpu_stats",
    "cpu_temperature": "cpu_temp",
    "cpu_clock": "cpu_mhz",
    "cpu_power": "cpu_power",
    "ram": "ram",
    "process_memory": "procmem",
    "process_vram": "proc_vram",
    "resolution": "resolution",
    "wine_proton": "wine",
    "gamemode": "gamemode",
    "battery": "battery",
    "network": "network",
}

# Every key emitted by Game Optimization is present in MangoHud 0.8.4's distributed
# example file.  Runtime parsing narrows this set on systems shipping a
# different example instead of writing an unknown option.
KNOWN_CONFIG_KEYS = frozenset(
    {
        "position",
        "font_size",
        "background_alpha",
        "round_corners",
        "hud_compact",
        "horizontal",
        "table_columns",
        "fps_limit",
        "fps_limit_method",
        "vulkan_present_mode",
        "vsync",
        "toggle_hud",
        "toggle_logging",
        "log_duration",
        "log_interval",
        "output_folder",
        "permit_upload",
        "frame_timing",
        "throttling_status",
        *METRIC_CONFIG_KEYS.values(),
    }
)

_EXAMPLE_KEY = re.compile(r"^\s*#?\s*([a-z][a-z0-9_]*)\s*(?:=|$)")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


class MangoHudProfileRepository:
    """Own only Game Optimization's per-AppID JSON files under XDG config."""

    def __init__(
        self,
        root: Path = GAMES_CONFIG_DIR,
        *,
        log_root: Path = MANGOHUD_LOG_DIR,
    ) -> None:
        self.root = Path(root)
        self.log_root = Path(log_root)

    def game_directory(self, app_id: object) -> Path:
        return self.root / validate_game_key(app_id)

    def profile_path(self, app_id: object) -> Path:
        return self.game_directory(app_id) / PROFILE_FILE_NAME

    def config_path(self, app_id: object) -> Path:
        return self.game_directory(app_id) / CONFIG_FILE_NAME

    def default(self, app_id: object) -> MangoHudProfile:
        normalized = validate_game_key(app_id)
        return MangoHudProfile.default(
            normalized, output_folder=self.log_root / normalized
        )

    def load(self, app_id: object) -> MangoHudProfile:
        normalized = validate_game_key(app_id)
        path = self.profile_path(normalized)
        if not path.exists():
            return self.default(normalized)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read MangoHud profile: {error}") from error
        if not isinstance(data, Mapping):
            raise ValueError("MangoHud profile must contain a JSON object")
        return MangoHudProfile.from_dict(
            data,
            expected_app_id=normalized,
            default_output_folder=self.log_root / normalized,
        )

    def save(self, profile: MangoHudProfile) -> Path:
        path = self.profile_path(profile.app_id)
        serialized = json.dumps(
            profile.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        )
        _atomic_write(path, serialized + "\n")
        return path

    def reset(self, app_id: object) -> MangoHudProfile:
        normalized = validate_game_key(app_id)
        for path in (self.profile_path(normalized), self.config_path(normalized)):
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise ValueError(f"could not reset MangoHud profile: {error}") from error
        return self.default(normalized)


class MangoHudConfigWriter:
    """Generate a deterministic allowlisted MangoHud configuration."""

    def __init__(self, supported_keys: Iterable[str] = KNOWN_CONFIG_KEYS) -> None:
        self.supported_keys = frozenset(supported_keys).intersection(KNOWN_CONFIG_KEYS)

    @staticmethod
    def _alpha(value: float) -> str:
        return f"{value:.3f}".rstrip("0").rstrip(".") or "0"

    def render(self, profile: MangoHudProfile) -> str:
        entries: list[tuple[str, str | None]] = []

        def add(key: str, value: object | None = None) -> None:
            if key not in self.supported_keys:
                return
            text = None if value is None else str(value)
            if any(existing == key for existing, _ in entries):
                raise ValueError(f"duplicate MangoHud config key: {key}")
            entries.append((key, text))

        add("position", profile.position)
        add("font_size", profile.font_size)
        add("background_alpha", self._alpha(profile.background_alpha))
        add("round_corners", profile.round_corners)
        if profile.compact:
            add("hud_compact")
        if profile.horizontal:
            add("horizontal")
        add("table_columns", profile.table_columns)
        if profile.fps_limit is not None:
            add("fps_limit", profile.fps_limit)
        if profile.fps_limit_method:
            add("fps_limit_method", profile.fps_limit_method)
        if profile.vulkan_present_mode:
            add("vulkan_present_mode", profile.vulkan_present_mode)
        if profile.vsync is not None:
            add("vsync", profile.vsync)
        add("toggle_hud", profile.toggle_hud_key)

        selected_keys = {METRIC_CONFIG_KEYS[metric] for metric in profile.metrics}
        # These are enabled by default in MangoHud. Explicit zeroes make a
        # custom profile faithfully reflect the user's metric selection.
        for key in ("fps", "frametime", "gpu_stats", "cpu_stats"):
            add(key, None if key in selected_keys else 0)
        add("frame_timing", 0)
        add("throttling_status", 0)
        for metric in profile.metrics:
            key = METRIC_CONFIG_KEYS[metric]
            if key not in {"fps", "frametime", "gpu_stats", "cpu_stats"}:
                add(key)

        add("permit_upload", 0)
        if profile.logging_enabled:
            add("output_folder", profile.output_folder)
            add("log_duration", profile.log_duration)
            add("log_interval", self._alpha(profile.log_interval))
            add("toggle_logging", profile.toggle_logging_key)

        lines = [
            MANAGED_HEADER,
            f"# Steam AppID: {profile.app_id}",
            "# This file is managed by Game Optimization; global MangoHud files are untouched.",
            "",
        ]
        lines.extend(key if value is None else f"{key}={value}" for key, value in entries)
        return "\n".join(lines) + "\n"

    def write(self, profile: MangoHudProfile, path: Path) -> Path:
        _atomic_write(Path(path), self.render(profile))
        return Path(path)


@dataclass(frozen=True, slots=True)
class MangoHudAvailability:
    available: bool
    steam_type: str
    version: str = ""
    command_path: str = ""
    layer_available: bool = False
    flatpak_layer_available: bool = False
    supported_keys: tuple[str, ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "steamType": self.steam_type,
            "version": self.version,
            "commandPath": self.command_path,
            "layerAvailable": self.layer_available,
            "flatpakLayerAvailable": self.flatpak_layer_available,
            "supportedKeys": list(self.supported_keys),
            "message": self.message,
        }


class MangoHudDetector:
    """Read-only detection of host and Flatpak MangoHud installations."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        example_paths: Sequence[Path] = (
            Path("/usr/share/doc/mangohud/MangoHud.conf.example"),
            Path("/usr/local/share/doc/mangohud/MangoHud.conf.example"),
        ),
        layer_paths: Sequence[Path] = (
            Path("/usr/share/vulkan/implicit_layer.d/MangoHud.x86_64.json"),
            Path("/usr/share/vulkan/implicit_layer.d/MangoHud.x86.json"),
            Path("/usr/local/share/vulkan/implicit_layer.d/MangoHud.x86_64.json"),
        ),
        host_service: object | None = None,
    ) -> None:
        self._which = which
        self._run = command_runner
        self.example_paths = tuple(Path(path) for path in example_paths)
        self.layer_paths = tuple(Path(path) for path in layer_paths)
        self._host_service = host_service
        self._cache: dict[str, MangoHudAvailability] = {}

    def supported_keys(self) -> frozenset[str]:
        for path in self.example_paths:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = {
                match.group(1)
                for line in content.splitlines()
                if (match := _EXAMPLE_KEY.match(line)) is not None
            }
            supported = found.intersection(KNOWN_CONFIG_KEYS)
            if supported:
                return frozenset(supported)
        return KNOWN_CONFIG_KEYS

    def _version(self, command: str) -> str:
        try:
            result = self._run(
                [command, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return (result.stdout or result.stderr or "").strip()

    def _flatpak_layer_available(self) -> bool:
        flatpak = self._which("flatpak")
        if not flatpak:
            return False
        try:
            result = self._run(
                [flatpak, "list", "--runtime", "--columns=application"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        return any(
            line.strip().startswith(FLATPAK_MANGOHUD_PREFIX)
            for line in result.stdout.splitlines()
        )

    def detect(self, steam_type: str = "native") -> MangoHudAvailability:
        normalized_type = "flatpak" if steam_type == "flatpak" else "native"
        cached = self._cache.get(normalized_type)
        if cached is not None:
            return cached
        host_info: Mapping[str, Any] = {}
        query = getattr(self._host_service, "tool_info", None)
        if callable(query):
            try:
                raw = query("mangohud")
                host_info = raw if isinstance(raw, Mapping) else {}
            except Exception:
                host_info = {}
        command = (
            str(host_info.get("executable") or "")
            if host_info
            else self._which("mangohud") or ""
        )
        layer_available = (
            host_info.get("available") is True
            if host_info
            else any(path.is_file() for path in self.layer_paths)
        )
        flatpak_layer = (
            host_info.get("flatpak_layer_available") is True
            if host_info and normalized_type == "flatpak"
            else self._flatpak_layer_available() if normalized_type == "flatpak" else False
        )
        if normalized_type == "flatpak":
            if host_info:
                try:
                    flatpak_tool = self._host_service.tool_info("flatpak")
                except Exception:
                    flatpak_tool = {}
                flatpak_available = bool(
                    isinstance(flatpak_tool, Mapping)
                    and flatpak_tool.get("available") is True
                )
            else:
                flatpak_available = bool(self._which("flatpak"))
            available = flatpak_available and flatpak_layer
            message = (
                "MangoHud Flatpak layer detected"
                if available
                else "MangoHud profile unavailable for this Steam installation"
            )
        else:
            available = bool(command) and layer_available
            message = (
                "MangoHud detected"
                if available
                else "MangoHud is not installed or its Vulkan layer is unavailable"
            )
        result = MangoHudAvailability(
            available=available,
            steam_type=normalized_type,
            version=(
                str(host_info.get("version") or "")
                if host_info
                else self._version(command) if command else ""
            ),
            command_path=command,
            layer_available=layer_available,
            flatpak_layer_available=flatpak_layer,
            supported_keys=tuple(sorted(self.supported_keys())),
            message=message,
        )
        self._cache[normalized_type] = result
        return result


@dataclass(frozen=True, slots=True)
class MangoHudLaunchActivation:
    enabled: bool
    available: bool
    environment: Mapping[str, str]
    config_path: Path | None
    steam_type: str
    message: str = ""
    strategy: str = "steam_environment"
    strategy_status: str = ""
    application_config_path: Path | None = None
    executable_path: str = ""
    conflict_path: Path | None = None
    requires_steam_restart: bool = False


@dataclass(frozen=True, slots=True)
class MangoHudApplicationConfigStatus:
    strategy: str
    status: str
    message: str
    resolution: ExecutableResolution
    application_config_path: Path | None = None
    conflict_path: Path | None = None
    requires_steam_restart: bool = False

    def to_dict(self) -> dict[str, Any]:
        selected = self.resolution.selected
        return {
            "activationStrategy": self.strategy,
            "strategyStatus": self.status,
            "strategyMessage": self.message,
            "applicationConfigPath": str(self.application_config_path or ""),
            "conflictPath": str(self.conflict_path or ""),
            "requiresSteamRestart": self.requires_steam_restart,
            "selectedExecutable": selected.relative_path if selected else "",
            "executableResolutionStatus": self.resolution.status,
            "executableCandidates": [
                candidate.to_dict() for candidate in self.resolution.candidates
            ],
        }


class MangoHudLaunchIntegration:
    """Prepare one game's config and environment without composing shell text."""

    def __init__(
        self,
        repository: MangoHudProfileRepository,
        detector: MangoHudDetector,
        *,
        flatpak_config_root: Path | None = None,
        application_config_root: Path | None = None,
        executable_resolver: GameExecutableResolver | None = None,
        native_steam_environment: Callable[[], Mapping[str, str] | None] = (
            running_native_steam_environment
        ),
    ) -> None:
        self.repository = repository
        self.detector = detector
        self.flatpak_config_root = flatpak_config_root or (
            Path.home()
            / ".var"
            / "app"
            / FLATPAK_STEAM_APP_ID
            / "config"
            / "game-optimization-linux"
            / "games"
        )
        config_home = (
            host_home_directory(os.environ) / ".config"
            if os.environ.get("FLATPAK_ID", "").strip()
            else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        )
        self.application_config_root = (
            Path(application_config_root)
            if application_config_root is not None
            else config_home / "MangoHud"
        )
        self.executable_resolver = executable_resolver or GameExecutableResolver()
        self._native_steam_environment = native_steam_environment

    @staticmethod
    def _managed_by_game_optimization(path: Path, app_id: str) -> bool:
        if path.is_symlink():
            return False
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            return False
        return MANAGED_HEADER in content and f"# Steam AppID: {app_id}" in content

    def _application_path(self, resolution: ExecutableResolution) -> Path | None:
        selected = resolution.selected
        if selected is None:
            return None
        filename = (
            f"wine-{Path(selected.name).stem}.conf"
            if selected.wine
            else f"{selected.name}.conf"
        )
        return self.application_config_root / filename

    def _remove_owned_application_config(
        self, game: Game, profile: MangoHudProfile
    ) -> None:
        resolution = self.executable_resolver.resolve(game, profile.executable_path)
        path = self._application_path(resolution)
        if path is None and profile.executable_path:
            name = Path(profile.executable_path).name
            filename = (
                f"wine-{Path(name).stem}.conf"
                if name.casefold().endswith(".exe")
                else f"{name}.conf"
            )
            path = self.application_config_root / filename
        if path is None or not self._managed_by_game_optimization(path, profile.app_id):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

    def status(
        self, game: Game, profile: MangoHudProfile
    ) -> MangoHudApplicationConfigStatus:
        resolution = self.executable_resolver.resolve(game, profile.executable_path)
        if uses_flatpak_steam(game):
            return MangoHudApplicationConfigStatus(
                "steam_environment", "flatpak_environment",
                "Steam environment profile - restarting Steam may be required",
                resolution, requires_steam_restart=True,
            )
        target = self._application_path(resolution)
        if target is None:
            return MangoHudApplicationConfigStatus(
                "steam_environment", "executable_missing",
                "Game executable was not determined", resolution,
                requires_steam_restart=True,
            )
        if (target.exists() or target.is_symlink()) and not self._managed_by_game_optimization(
            target, profile.app_id
        ):
            return MangoHudApplicationConfigStatus(
                "steam_environment", "application_config_conflict",
                "An existing MangoHud application configuration is not managed by Game Optimization",
                resolution, application_config_path=target, conflict_path=target,
                requires_steam_restart=True,
            )
        if game.launcher.value == "Manual" and game.data_source.casefold() == "local":
            return MangoHudApplicationConfigStatus(
                "per_application_config", "application_profile",
                "Application profile - changes apply on the next game launch",
                resolution, application_config_path=target,
            )
        running = self._native_steam_environment()
        if running is not None:
            explicit = str(running.get("MANGOHUD_CONFIGFILE", "")).strip()
            active = str(running.get("MANGOHUD", "")).strip().casefold() in {
                "1", "true", "yes", "on"
            }
            if explicit:
                canonical = str(self.repository.config_path(profile.app_id))
                matches = Path(explicit).expanduser() == Path(canonical)
                return MangoHudApplicationConfigStatus(
                    "steam_environment", "steam_config_override",
                    (
                        "Steam environment profile is already active"
                        if matches
                        else "Steam has an explicit MangoHud configuration; restart Steam to change it"
                    ),
                    resolution, application_config_path=target,
                    requires_steam_restart=not matches,
                )
            if not active:
                return MangoHudApplicationConfigStatus(
                    "steam_environment", "steam_mangohud_inactive",
                    "MangoHud is not active in the running Steam environment",
                    resolution, application_config_path=target,
                    requires_steam_restart=True,
                )
        return MangoHudApplicationConfigStatus(
            "per_application_config", "application_profile",
            "Application profile - changes apply on the next game launch",
            resolution, application_config_path=target,
        )

    def synchronize(
        self,
        game: Game,
        profile: MangoHudProfile,
        *,
        previous_profile: MangoHudProfile | None = None,
    ) -> MangoHudApplicationConfigStatus:
        if (
            previous_profile is not None
            and previous_profile.executable_path
            and previous_profile.executable_path != profile.executable_path
        ):
            self._remove_owned_application_config(game, previous_profile)
        status = self.status(game, profile)
        target = status.application_config_path
        if not profile.enabled:
            self._remove_owned_application_config(game, profile)
            return status
        if (
            target is not None
            and status.conflict_path is None
            and status.resolution.reliable
            and not uses_flatpak_steam(game)
        ):
            availability = self.detector.detect("native")
            MangoHudConfigWriter(availability.supported_keys).write(profile, target)
        return status

    def prepare(self, game: Game, profile: MangoHudProfile) -> MangoHudLaunchActivation:
        steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
        if not profile.enabled:
            return MangoHudLaunchActivation(
                enabled=False,
                available=True,
                environment={},
                config_path=self.repository.config_path(profile.app_id),
                steam_type=steam_type,
                message="MangoHud disabled for this game",
            )
        availability = self.detector.detect(steam_type)
        if not availability.available:
            return MangoHudLaunchActivation(
                enabled=True,
                available=False,
                environment={},
                config_path=None,
                steam_type=steam_type,
                message=availability.message,
            )
        writer = MangoHudConfigWriter(availability.supported_keys)
        if steam_type == "flatpak":
            config_path = self.flatpak_config_root / profile.app_id / CONFIG_FILE_NAME
        else:
            config_path = self.repository.config_path(profile.app_id)
        writer.write(profile, config_path)
        strategy = self.synchronize(game, profile)
        if steam_type == "native" and strategy.strategy == "per_application_config":
            running = self._native_steam_environment()
            local_game = (
                game.launcher.value == "Manual"
                and game.data_source.casefold() == "local"
            )
            environment = (
                {"MANGOHUD": "1"}
                if local_game or running is None
                else {}
            )
            return MangoHudLaunchActivation(
                enabled=True,
                available=True,
                environment=environment,
                config_path=strategy.application_config_path,
                steam_type=steam_type,
                message=strategy.message,
                strategy=strategy.strategy,
                strategy_status=strategy.status,
                application_config_path=strategy.application_config_path,
                executable_path=(
                    strategy.resolution.selected.relative_path
                    if strategy.resolution.selected else ""
                ),
            )
        return MangoHudLaunchActivation(
            enabled=True,
            available=True,
            environment={
                "MANGOHUD": "1",
                "MANGOHUD_CONFIGFILE": str(config_path),
            },
            config_path=config_path,
            steam_type=steam_type,
            message=strategy.message,
            strategy=strategy.strategy,
            strategy_status=strategy.status,
            application_config_path=strategy.application_config_path,
            executable_path=(
                strategy.resolution.selected.relative_path
                if strategy.resolution.selected else ""
            ),
            conflict_path=strategy.conflict_path,
            requires_steam_restart=strategy.requires_steam_restart,
        )

    def reset(self, game: Game) -> None:
        """Remove only the per-game Flatpak mirror managed by Game Optimization."""

        game_key = str(game.steam_app_id or game.id)
        if not game.steam_app_id and not (
            game.launcher.value == "Manual"
            and game.data_source.casefold() == "local"
        ):
            return
        if not uses_flatpak_steam(game):
            try:
                profile = self.repository.load(game_key)
            except (OSError, ValueError):
                return
            self._remove_owned_application_config(game, profile)
            return
        config_path = self.flatpak_config_root / game_key / CONFIG_FILE_NAME
        try:
            config_path.unlink(missing_ok=True)
            config_path.parent.rmdir()
        except OSError:
            # A non-empty directory may contain future Game Optimization-owned artifacts.
            # Resetting the profile must never broaden into recursive deletion.
            return


__all__ = [
    "CONFIG_FILE_NAME",
    "FLATPAK_MANGOHUD_PREFIX",
    "FLATPAK_STEAM_APP_ID",
    "KNOWN_CONFIG_KEYS",
    "METRIC_CONFIG_KEYS",
    "MangoHudAvailability",
    "MangoHudApplicationConfigStatus",
    "MangoHudConfigWriter",
    "MangoHudDetector",
    "MangoHudLaunchActivation",
    "MangoHudLaunchIntegration",
    "MangoHudProfileRepository",
    "PROFILE_FILE_NAME",
]
