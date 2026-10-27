from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shlex
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QDesktopServices

from ..models import (
    Game,
    DetectedValue,
    DetectionEvidence,
    OptimizationAnalysis,
    OptimizationCandidate,
    PerformanceMeasurement,
    GameOptimizationProfile,
    Launcher,
    OptimizationOptions,
    OptimizationProfile,
)
from ..services import (
    OptiScalerError,
    ProtonTweaksError,
    SessionPerformanceData,
    uses_flatpak_steam,
)
from ..models.proton_tweaks import PROTON_TWEAK_REGISTRY
from ..models.optimization_profile import (
    GAMESCOPE_DESKTOP_FIELDS,
    GAMESCOPE_PROFILE_FIELDS,
    sharpness_cli_to_percent,
    sharpness_percent_to_cli,
)
from ..services.host_bootstrap import host_home_directory
from ..services.game_capabilities import game_capabilities
from ..services.launcher_mangohud import (
    LauncherMangoHudError,
    launcher_mangohud_path,
    read_launcher_mangohud,
    write_launcher_mangohud,
)
from ..services.launcher_components import TOOLS as LAUNCHER_COMPONENT_TOOLS, LauncherComponentsService
from ..services.launcher_integration import launcher_argument_setting
from ..services.launcher_integration import (
    LauncherPlan,
    LauncherSyncError,
    launcher_facts,
)
from ..services.mangohud import CONFIG_FILE_NAME, MangoHudConfigWriter
from ..services.performance_analysis import compare_measurements
from ..services.tweaks_capabilities import runner_capabilities, log_directory, conflicts, merge_tweak_environment
from ..services.launcher_integration import locate_config
from ..services.narrator_activity import NarratorGameActivityDetector
from ..services.performance_session import RUNNER_HEARTBEAT_STALE_SECONDS
from ..services.automatic_optimization import verify_runtime_activation
from ..services.post_processing import optional_launch_environment
from ..services.frame_generation import FrameGenerationLaunch, resolve_launch as frame_generation_resolve, load_profile as load_fg_profile

if TYPE_CHECKING:
    from .app_controller import AppController

logger = logging.getLogger(__name__)


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


# QML names of the desktop nested Gamescope options, e.g. gamescopeHdrItm.
_GAMESCOPE_DESKTOP_ALIASES = {_camel(name): name for name in GAMESCOPE_DESKTOP_FIELDS}


def _display_path(value: str) -> str:
    """Show the private home directory as ``~`` in previews and copied text."""

    home = str(Path.home()).rstrip("/")
    if not home or home == "/":
        return value
    return re.sub(re.escape(home) + r"(?=/|$)", "~", value)


class OptimizationController:
    def __init__(self, app: AppController) -> None:
        self._app = app
        self._analysis_cache_states: dict[str, str] = {}
        # Launcher Flatpak sandbox checks run `flatpak run` (up to ~20 s) and
        # must never run on the QML thread: results are cached and refreshed
        # by one background worker.
        self._sandbox_cache: dict[tuple[str, tuple[str, ...]], tuple[float, dict[str, Any] | None, str]] = {}
        self._sandbox_jobs: dict[tuple[str, tuple[str, ...]], Future[Any]] = {}
        self._sandbox_lock = Lock()
        self._sandbox_executor: ThreadPoolExecutor | None = None
        # MangoHud config sync for launcher games (profile save -> host file).
        self._mangohud_sync: dict[str, dict[str, str]] = {}
        self._mangohud_sync_lock = Lock()
        self._mangohud_sync_executor: ThreadPoolExecutor | None = None
        # Heroic measurement sessions: game process observation.
        self._launcher_activity: Any | None = None
        self._launcher_negative_reads: dict[str, int] = {}
        self._launcher_next_check: dict[str, float] = {}
        self._launcher_cleanup_at = 0.0
        self._components = LauncherComponentsService(
            lambda: getattr(self._app, "_host_service", None),
            refresh_probe=self._refresh_launcher_sandbox,
        )

    _INTEGER_PROFILE_FIELDS = (
        "target_fps", "gamescope_refresh_rate", "gamescope_input_width",
        "gamescope_input_height", "gamescope_output_width", "gamescope_output_height",
        "gamescope_sharpness",
    )

    @staticmethod
    def _whole_number(value: Any, name: str) -> int:
        """Accept int, integral finite floats (QML numbers) or integer text."""
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a whole number")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if math.isfinite(value) and value.is_integer():
                return int(value)
            raise ValueError(f"{name} must be a whole number")
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            return int(value.strip())
        raise ValueError(f"{name} must be a whole number")

    @staticmethod
    def _profile_key(game: Game | None) -> str:
        if game is None:
            return ""
        # Custom/Manual entries: a key derived from their persistent UUID.
        from ..services.measured_optimization.manual import optimization_key
        return optimization_key(game)

    def buildLaunchPreview(self, game_id: str, options: Mapping[str, Any]) -> str:
        """Build a display-only Steam launch preview."""

        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return "%command%"
        try:
            normalized_options = self._app._optimization_options(options)
            preview = self._app._provider_launch_preview(game, normalized_options)
        except Exception as error:
            self._app._report_error(f"building launch preview for {game.name}", error)
            return "%command%"

        logger.debug("Generated display-only launch preview for %s", game.id)
        return preview

    def playUiSound(self, kind: str) -> bool:
        if self._app._interface_mode == "couch":
            return False
        return self._app._ui_sound_service.play(kind)

    def optimizationDefaults(self, profile: str) -> dict[str, Any]:
        """Return provider-owned defaults for an optimization profile."""

        try:
            normalized_profile = self._app._coerce_enum(OptimizationProfile, profile)
            options = self._app._optimization_provider.defaults_for(normalized_profile)
        except Exception as error:
            self._app._report_error(f"loading optimization profile {profile}", error)
            return {}

        return {
            "gamemode": options.gamemode,
            "gamescope": options.gamescope,
            "mangohud": options.mangohud,
            "fpsLimit": options.fps_limit,
            "adaptiveSync": options.adaptive_sync,
            "cursorGrab": options.cursor_grab,
            "cpuPerformanceProfile": options.cpu_performance_profile,
            "memoryMonitoring": options.memory_monitoring,
            "optiscaler": options.optiscaler,
        }

    def _proton_tweaks_to_qml(self, app_id: str) -> dict[str, Any]:
        profile = self._app._proton_tweaks_repository.load(app_id)
        gpu_vendor = str(self._app._system_info.get("gpuVendor", ""))
        return self._app._proton_tweaks_repository.to_qml(
            profile, gpu_vendor=gpu_vendor
        )

    def _tweaks_context(self, game: Game, profile) -> dict[str, Any]:
        home = host_home_directory()
        caps = runner_capabilities(game, home)
        inherited = {}
        if game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}:
            try:
                config = locate_config(game, home)
                inherited = config.env(config.load())
            except (LauncherSyncError, OSError):
                pass
        elif game.launcher is Launcher.STEAM:
            status = self._app._runner_integration.steam_launch_option_status(game)
            # Parse only leading environment assignments; never execute shell text.
            try:
                for part in shlex.split(status.raw_launch_options):
                    key, sep, value = part.partition("=")
                    if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                        break
                    inherited[key] = value
            except ValueError:
                pass
        opt = self._app._optimization_profile_repository.load(profile.app_id)
        fg = load_fg_profile(profile.app_id, self._app._proton_tweaks_repository.root)
        result = self._app._proton_tweaks_repository.to_qml(profile, gpu_vendor=str(self._app._system_info.get("gpuVendor", "")))
        directory = log_directory(game, profile.app_id, home)
        environment = profile.environment()
        if profile.states.get("proton_log") == "enabled":
            environment["PROTON_LOG_DIR"] = str(directory)
        result.update({"capabilities": caps, "activation": self._launch_activation(game),
                       "logDirectory": str(directory), "inheritedEnvironment": {k: v for k, v in inherited.items() if k in {d.environment_key for d in PROTON_TWEAK_REGISTRY} | {"PROTON_LOG_DIR", "DISABLE_LOW_LATENCY_LAYER"}},
                       "environment": merge_tweak_environment(inherited, environment),
                       "conflicts": conflicts(profile.states, inherited, gamescope=opt.gamescope_enabled and opt.gamescope_mode != "disabled", frame_generation=bool(fg.get("backend", "off") != "off")),
                       "runtimeState": "unverified",
                       "steamCommand": self._app._runner_integration.steam_command(profile.app_id) if game.launcher is Launcher.STEAM else ""})
        return result

    def getProtonTweaks(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        key = self._profile_key(game)
        if not key:
            return {"success": False, "error": "Proton Tweaks require a supported game"}
        try:
            return self._tweaks_context(game, self._app._proton_tweaks_repository.load(key))
        except (OSError, ValueError, ProtonTweaksError) as error:
            return {"success": False, "error": str(error)}

    def previewProtonTweaks(self, game_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        key = self._profile_key(game)
        if not key:
            return {"success": False, "error": "Select a supported game"}
        try:
            repo = self._app._proton_tweaks_repository
            current = repo.load(key)
            draft = repo.from_payload(key, values)
            result = self._tweaks_context(game, draft)
            old = current.environment()
            new = draft.environment()
            result["changes"] = [{"key": k, "before": old.get(k), "after": new.get(k)} for k in sorted(old.keys() | new.keys()) if old.get(k) != new.get(k)]
            # Do not block removal of a saved legacy override after a runner update.
            result["unsupported"] = [k for k, v in draft.states.items() if v == "enabled" and current.states.get(k) != "enabled" and result["capabilities"]["support"].get(k) == "unsupported"]
            return result
        except (OSError, ValueError, ProtonTweaksError) as error:
            return {"success": False, "error": str(error)}

    def saveProtonTweaks(self, game_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        key = self._profile_key(game)
        if game is None or not key:
            return {"success": False, "error": "Select a supported game"}
        try:
            preview = self.previewProtonTweaks(game_id, values)
            if not preview.get("success"):
                return preview
            if preview.get("conflicts") or preview.get("unsupported"):
                return {**preview, "success": False, "error": "Resolve the marked conflicts before applying"}
            repo = self._app._proton_tweaks_repository
            updated = repo.from_payload(key, values)
            directory = log_directory(game, key, host_home_directory())
            if updated.states.get("proton_log") == "enabled":
                directory.mkdir(parents=True, exist_ok=True)
            updated = replace(updated, extra={**updated.extra, "log_directory": str(directory)})
            repo.save(updated)
            self._app.protonTweaksChanged.emit(key)
            return self.getProtonTweaks(game_id)
        except (OSError, ValueError, ProtonTweaksError) as error:
            return {"success": False, "error": str(error)}

    def openProtonLogs(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=False)
        key = self._profile_key(game)
        if not key:
            return False
        path = log_directory(game, key, host_home_directory())
        path.mkdir(parents=True, exist_ok=True)
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def getOptimizationProfile(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            previous_profile = self._app._optimization_profile_repository.load(app_id)
            profile = previous_profile
            result = self._app._optimization_profile_to_qml(profile)
            result["gameAnalysis"] = self.getGameOptimizationAnalysis(game_id)
            result["launchActivation"] = self._launch_activation(game)
            result["launcherPreview"] = self._launcher_preview(game)
            return result
        except Exception as error:
            logger.warning("Could not load optimization profile for %s: %s", game.id, error)
            return {"success": False, "error": str(error)}

    def _launch_activation(self, game: Game) -> dict[str, Any]:
        """Report whether the real launch path applies GameOpti wrappers.

        Steam games only get wrappers when Steam starts them through the
        GameOpti runner (Launch Options); local games always run through it.
        Heroic, Lutris and manual launcher entries start without the runner.
        """
        if game.launcher is Launcher.MANUAL and game.data_source.casefold() == "local":
            return {"state": "active", "message": "Local games start through the GameOpti runner"}
        if game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}:
            status = self._with_mangohud_config_state(game, self._app._launcher_integration.status(game))
            state = {"applied": "active", "unavailable": "unknown"}.get(
                str(status.get("state")), "launcher_not_applied"
            )
            return {**status, "state": state, "message": str(status.get("message", ""))}
        if game.launcher is not Launcher.STEAM:
            return {
                "state": "unsupported",
                "message": f"{game.launcher.value} launches do not use the GameOpti runner",
            }
        status_method = getattr(self._app._runner_integration, "steam_launch_option_status", None)
        status = status_method(game) if callable(status_method) else None
        if status is None or status.configured is None:
            return {
                "state": "unknown",
                "message": status.message if status is not None else "Steam launch options could not be checked",
            }
        if status.configured:
            return {"state": "active", "message": "Steam starts this game through the GameOpti runner"}
        return {
            "state": "runner_not_configured",
            "message": status.message,
            "steamLaunchCommand": status.command,
        }

    @staticmethod
    def _launcher_tweaks_environment(game: Game) -> dict[str, str]:
        try:
            config = locate_config(game, host_home_directory())
            return config.env(config.load())
        except (LauncherSyncError, OSError):
            return {}

    def launcher_plan(self, game: Game, *, write: bool = False, custom_launch: Any = None,
                      profile_draft: Any = None) -> LauncherPlan:
        """Wrapper prefix and environment of the saved profiles for a Heroic/Lutris game.

        Every feature (GameMode, Gamescope, MangoHud, OptiScaler, Proton
        tweaks) is checked on its own: one that is enabled but not available
        (on the host or inside the launcher's Flatpak) is left out of the plan
        with its reason, and the remaining features are still applied.
        """

        key = game.profile_key
        if not key:
            raise LauncherSyncError("The game has no stable profile key")
        app = self._app
        launcher = game.launcher.value
        # An unsaved optimization draft is only previewed; Apply always uses the saved profile.
        profile = profile_draft if profile_draft is not None and not write else app._optimization_profile_repository.load(key)
        gamemode, gamescope = app._runtime_tool_detector.detect()
        if game.launcher_variant == "flatpak":
            # The wrapper runs inside the launcher's Flatpak: resolve it on
            # that sandbox's PATH (VulkanLayer extensions), never a host path.
            if gamemode.executable:
                gamemode = replace(gamemode, executable="gamemoderun")
            if gamescope.executable:
                gamescope = replace(gamescope, executable="gamescope")
        mangohud_profile = app._mangohud_repository.load(key)
        warnings: list[str] = []
        blockers: list[str] = []
        features: dict[str, dict[str, Any]] = {}

        def feature(name: str, enabled: bool, available: bool, reason: str = "") -> bool:
            usable = bool(enabled and available)
            features[name] = {
                "enabled": bool(enabled),
                "available": usable,
                "reason": "" if usable or not enabled else reason,
            }
            if enabled and not usable and reason:
                warnings.append(reason)
            return usable

        want_gamemode = bool(profile.gamemode_enabled)
        want_gamescope = bool(profile.gamescope_enabled and profile.gamescope_mode != "disabled")
        want_mangohud = bool(mangohud_profile.enabled)
        facts = launcher_facts(game, getattr(app._launcher_integration, "_home", None))
        builtins = dict(facts.builtins)
        builtin_labels = {"gamemode": "GameMode", "gamescope": "Gamescope", "mangohud": "MangoHud"}

        def launcher_owns(name: str, wanted_tool: bool) -> bool:
            """The launcher already enables this tool: never add it twice."""
            if wanted_tool and builtins.get(name):
                feature(
                    name, True, False,
                    f"{launcher} already enables {builtin_labels[name]} for this game; "
                    "GameOpti does not add it a second time",
                )
                return True
            return False

        skip_gamemode = launcher_owns("gamemode", want_gamemode)
        skip_gamescope = launcher_owns("gamescope", want_gamescope)
        skip_mangohud = launcher_owns("mangohud", want_mangohud)
        want_gamemode = want_gamemode and not skip_gamemode
        want_gamescope = want_gamescope and not skip_gamescope
        want_mangohud = want_mangohud and not skip_mangohud
        sandbox: dict[str, Any] | None = None
        pending = False
        if game.launcher_variant == "flatpak" and (want_gamemode or want_gamescope or want_mangohud):
            wanted = [
                name
                for name, wanted_tool in (
                    ("gamemoderun", want_gamemode),
                    ("gamescope", want_gamescope),
                    ("mangohud", want_mangohud),
                )
                if wanted_tool
            ]
            sandbox, sandbox_error, pending = self._launcher_sandbox(game, wanted)
            if sandbox is None:
                # Unknown (or not yet checked) sandbox: nothing may be claimed
                # to work there.
                blockers.append(sandbox_error)

        def sandbox_missing(tool: str) -> str:
            """Reason the tool is missing in the launcher's Flatpak, or ""."""
            if sandbox is None:
                return ""
            commands = set(sandbox.get("commands", ()))
            extensions = set(sandbox.get("extensions", ()))
            if tool == "gamemoderun":
                return "" if tool in commands else f"GameMode is not available inside the {launcher} Flatpak"
            if tool == "gamescope" and tool in commands:
                return ""
            if tool == "mangohud" and ("MangoHud" in extensions or tool in commands):
                return ""
            extension = f"org.freedesktop.Platform.VulkanLayer.{self._SANDBOX_LAYER_EXTENSIONS[tool]}"
            branch = str(sandbox.get("branch", ""))
            ref = f"{extension}//{branch}" if branch else extension
            label = "Gamescope" if tool == "gamescope" else "MangoHud"
            return (
                f"{label} is not available inside the {launcher} Flatpak; "
                f"install it with: flatpak install flathub {ref}"
            )

        if skip_gamemode:
            use_gamemode = False
        elif want_gamemode and not gamemode.available:
            use_gamemode = feature("gamemode", True, False, gamemode.message or "GameMode is not available")
        else:
            missing = sandbox_missing("gamemoderun") if want_gamemode else ""
            use_gamemode = feature("gamemode", want_gamemode, not missing, missing)
        if skip_gamescope:
            use_gamescope = False
        elif want_gamescope and not gamescope.available:
            use_gamescope = feature("gamescope", True, False, gamescope.message or "Gamescope is not available")
        else:
            missing = sandbox_missing("gamescope") if want_gamescope else ""
            use_gamescope = feature("gamescope", want_gamescope, not missing, missing)
        plan_profile = replace(profile, gamemode_enabled=use_gamemode, gamescope_enabled=use_gamescope)

        mangohud_owner = "none"
        mangohud_config = ""
        environment: dict[str, str] = {}
        if want_mangohud:
            availability, _runtime = app._mangohud_controller._availability(game, mangohud_profile)
            if not availability.available:
                use_mangohud = feature(
                    "mangohud", True, False, availability.message or "MangoHud is not available"
                )
            else:
                missing = sandbox_missing("mangohud")
                heroic_flatpak = game.launcher is Launcher.HEROIC and game.launcher_variant == "flatpak"
                if not missing and heroic_flatpak:
                    # The per-game config must live where Heroic's sandbox can
                    # read it (xdg-config/MangoHud), never in GameOpti's own
                    # private folders.
                    config_dir = host_home_directory(os.environ) / ".config/MangoHud"
                    if not config_dir.is_dir():
                        config_dir.mkdir(parents=True, exist_ok=True)
                        warnings.append(
                            f"Created {config_dir}; restart {launcher} if it is running so it can read it"
                        )
                    if sandbox is not None and "MangoHud" not in set(sandbox.get("configDirs", ())):
                        missing = (
                            f"The {launcher} Flatpak cannot read ~/.config/MangoHud "
                            "(permission xdg-config/MangoHud); MangoHud is not applied"
                        )
                use_mangohud = feature("mangohud", True, not missing, missing)
            if use_mangohud and use_gamescope and game.launcher is not Launcher.HEROIC:
                # Heroic: the WSI error came from the UMU container (see
                # launcher_settings below), MangoHud is drawn by --mangoapp.
                # Lutris has not been tested with this combination yet.
                reason = (
                    f"MangoHud together with Gamescope is not confirmed for {launcher} yet "
                    "(Gamescope would draw MangoHud with --mangoapp); turn one of them off "
                    "and test them separately"
                )
                blockers.append(reason)
                features["mangohud"] = {"enabled": True, "available": False, "reason": reason}
                features["gamescope"] = {"enabled": True, "available": False, "reason": reason}
                use_mangohud = use_gamescope = False
                plan_profile = replace(plan_profile, gamescope_enabled=False)
            if use_mangohud:
                # The Gamescope FPS limit only wins while Gamescope is in this plan.
                effective = (
                    app._effective_mangohud_profile(mangohud_profile)
                    if use_gamescope
                    else mangohud_profile
                )
                # One place for every launcher: ~/.config/MangoHud of the host
                # home (Heroic Flatpak can read only xdg-config/MangoHud), never
                # GameOpti's private folders. Kept in sync on profile saves.
                target = launcher_mangohud_path(key)
                if write:
                    try:
                        write_launcher_mangohud(
                            target,
                            MangoHudConfigWriter(availability.supported_keys).render(effective),
                            host_writer=getattr(app._launcher_integration, "_host_writer", None)
                            if callable(getattr(app._launcher_integration, "_host_writer", None)) else None,
                            staging_dir=self._mangohud_staging_dir(),
                        )
                    except LauncherMangoHudError as error:
                        raise LauncherSyncError(str(error)) from error
                mangohud_config = str(target)
                mangohud_owner = "steam_environment"
        elif not skip_mangohud:
            feature("mangohud", False, False)
        try:
            optiscaler = app._optiscaler_service.profile_repository.load(key)
            installed = optiscaler.installation_state == "installed"
            optiscaler_override = (
                optiscaler.proton_override
                if feature(
                    "optiscaler", optiscaler.enabled, installed and bool(optiscaler.proton_override),
                    "OptiScaler is enabled but not installed for this game",
                )
                else ""
            )
        except (OSError, ValueError, OptiScalerError):
            feature("optiscaler", False, False)
            optiscaler_override = ""
        try:
            proton_environment = app._proton_tweaks_repository.environment(key)
        except (OSError, ValueError, ProtonTweaksError):
            proton_environment = {}
        # Proton tweaks are PROTON_* variables: only meaningful when the
        # launcher config confirms a Proton runner for this game.
        if not feature(
            "proton", bool(proton_environment), facts.proton_runner is True,
            f"{launcher} does not report a Proton runner for this game; Proton tweaks are not applied",
        ):
            proton_environment = {}
        try:
            fg_resolved = frame_generation_resolve(key, app._optiscaler_service.profile_repository.root,
                gamescope=use_gamescope)
        except (OSError, ValueError) as error:
            blockers.append(str(error))
            fg_resolved = FrameGenerationLaunch({}, [])
        fg_environment, fg_prefix = fg_resolved.environment, fg_resolved.prefix
        # A draft is only previewed; write=True always uses the saved profile.
        custom_profile = custom_launch if custom_launch is not None and not write else self._custom_launch(key, blockers)
        plan = app._optimization_launch_planner.build(
            plan_profile, ["%command%"], gamemode=gamemode, gamescope=gamescope,
            mangohud_fps_limit=(
                None
                if use_gamescope and app._gamescope_owns_fps_limit(plan_profile)
                else mangohud_profile.fps_limit if mangohud_config else None
            ),
            optiscaler_override=optiscaler_override,
            post_processing_environment=self._post_environment(key),
            frame_generation_environment=fg_environment,
            frame_generation_prefix=fg_prefix,
            frame_generation_report=fg_resolved.report(),
            custom_launch=custom_profile,
            proton_environment=proton_environment,
            existing_environment=self._launcher_tweaks_environment(game),
            mangohud_activation_owner=mangohud_owner,
            allow_placeholder=True,
            mangohud_config_path=mangohud_config,
        )
        environment.update(plan.environment)
        if mangohud_config:
            environment["MANGOHUD_CONFIGFILE"] = mangohud_config
            if plan.mangohud_activation_owner != "mangoapp":
                environment["MANGOHUD"] = "1"
        settings: dict[str, Any] = {}
        if (
            use_gamescope
            and game.launcher is Launcher.HEROIC
            and game.launcher_variant == "flatpak"
            and facts.proton_runner is True
        ):
            # Heroic Flatpak + UMU: pressure-vessel starts the game in a
            # Flatpak sub-sandbox with the original DISPLAY/WAYLAND_DISPLAY,
            # so the game window never reaches Gamescope's Xwayland and the
            # Gamescope WSI layer reports "Hooking has failed". Without UMU
            # Heroic runs Proton directly under Gamescope (verified: the
            # Windows process sees Gamescope's DISPLAY). Restore puts the
            # previous value back.
            settings["disableUMU"] = True
            if facts.umu_active is True:
                warnings.append(
                    "Gamescope cannot reach the game inside the UMU container of the Heroic Flatpak; "
                    "GameOpti turns on Heroic's Disable UMU option for this game while Gamescope is "
                    "used (Proton runs without the Steam Runtime container and UMU game fixes)"
                )
        from ..services.gpu_memory import load_profile as gpu_memory_profile, launch_prefix as gpu_memory_prefix
        gpu_profile = gpu_memory_profile(key, app._optiscaler_service.profile_repository.root)
        gpu_prefix = []
        if feature("gpu_memory", gpu_profile.get("priority", False), game.launcher_variant != "flatpak",
                   "The launcher Flatpak cannot access the host systemd GPU-memory scope"):
            metrics = None
            session = app._baseline_sessions.load(key)
            if session and session.status in {"waiting_for_launcher", "recording", "waiting_for_game_exit"}:
                metrics = session.directory / "gpu-memory.jsonl"
            gpu_prefix = gpu_memory_prefix(key, app._optiscaler_service.profile_repository.root, metrics=metrics)
        command = list(plan.command)
        marker = command.index("%command%") if "%command%" in command else len(command)
        if plan.custom_arguments:
            setting = launcher_argument_setting(game)
            if setting:
                settings[setting] = shlex.join(plan.custom_arguments)
            else:
                warnings.append(f"Custom game arguments are not supported for this {launcher} game type; they are not applied")
        hooks_prefix: list[str] = []
        if plan.pre_launch or plan.post_exit:
            from ..services import launch_hooks
            umu = False if settings.get("disableUMU") is True else facts.umu_active
            available, where, reason = launch_hooks.support(game, umu_active=umu)
            if available:
                home = Path(getattr(app._launcher_integration, "_home", None) or Path.home())
                hooks_prefix = launch_hooks.prefix(game, custom_profile, home, write=write)
                if game.launcher_variant == "flatpak":
                    warnings.append(f"Before-launch and after-exit commands run in the {where}; "
                                    "their programs must exist there")
            else:
                warnings.append(f"Before-launch and after-exit commands are not applied: {reason}")
        if "custom" in plan.wrappers and game.launcher_variant == "flatpak":
            warnings.append(f"The custom wrapper runs inside the {launcher} Flatpak sandbox; its executable must exist there")
        return LauncherPlan(
            prefix=(*hooks_prefix, *gpu_prefix, *command[:marker]),
            environment=environment,
            warnings=tuple(dict.fromkeys((*plan.warnings, *warnings))),
            blockers=tuple(blockers),
            features=features,
            included=tuple(name for name, item in features.items() if item["available"]),
            pending=pending,
            settings=settings,
        )

    _LAUNCHER_FLATPAK_IDS = {
        "Heroic": "com.heroicgameslauncher.hgl",
        "Lutris": "net.lutris.Lutris",
    }

    def _custom_launch(self, key: str, blockers: list[str] | None = None) -> Any:
        """Saved custom launch profile, or None. An unreadable file blocks launcher Apply."""
        from ..services.custom_launch import CustomLaunchError, CustomLaunchRepository
        try:
            return CustomLaunchRepository(self._app._optiscaler_service.profile_repository.root).load(key)
        except (OSError, ValueError, CustomLaunchError) as error:
            if blockers is not None:
                blockers.append(f"The custom launch profile cannot be read: {error}")
            return None

    def _post_environment(self, key: str) -> dict[str, str]:
        service = getattr(self._app, "_post_processing_service", None)
        return optional_launch_environment(key, service.root) if service is not None else {}

    def _launcher_preview(self, game: Game, profile_draft: Any = None) -> dict[str, Any]:
        """Heroic/Lutris preview in the launcher's own terms; never Steam Launch Options.

        Read only: builds the plan, reads the launcher file and the GameOpti
        ownership record. ``profile_draft`` previews an unsaved optimization
        draft next to the saved plan.
        """
        if game.launcher not in {Launcher.HEROIC, Launcher.LUTRIS}:
            return {}
        app = self._app
        service = app._launcher_integration
        heroic = game.launcher is Launcher.HEROIC
        names = ({"environment": "enviromentOptions", "wrapper": "wrapperOptions", "arguments": "launcherArgs"}
                 if heroic else {"environment": "system.env", "wrapper": "system.prefix_command", "arguments": "game.args"})
        result: dict[str, Any] = {"launcher": game.launcher.value, "variant": game.launcher_variant or "native",
                                  "fields": names, "configPath": "", "current": {}, "apply": {}, "restore": {},
                                  "draft": None, "error": ""}

        def describe(plan: LauncherPlan, config: Any) -> dict[str, Any]:
            try:
                wrapper = config.wrapper_for(plan.prefix) if plan.prefix and config is not None else None
            except LauncherSyncError as error:
                wrapper = {"text": shlex.join(plan.prefix), "error": str(error)}
            return {"environment": {k: str(v) for k, v in sorted(plan.environment.items())},
                    "wrapper": wrapper, "settings": {k: v for k, v in sorted(plan.settings.items())},
                    "warnings": list(plan.warnings), "blockers": list(plan.blockers), "pending": bool(plan.pending),
                    "revision": plan.digest(), "empty": plan.empty, "included": list(plan.included),
                    "skipped": {name: item.get("reason", "") for name, item in plan.features.items()
                                if item.get("enabled") and not item.get("available")}}
        try:
            saved_plan = self.launcher_plan(game)
        except (LauncherSyncError, OSError, ValueError) as error:
            return {**result, "error": str(error)}
        config = None
        try:
            config = locate_config(game, getattr(service, "_home", None))
            document = config.load()
            settings = {}
            for key in ("disableUMU", "launcherArgs") if heroic else ("args",):
                try:
                    value = config.setting(document, key)
                except LauncherSyncError:
                    continue
                settings[key] = None if value == "__gameopti_missing__" else value
            result["configPath"] = str(config.path)
            result["current"] = {"environment": config.env(document), "wrapper": config.wrapper_text(document),
                                 "settings": settings, "builtins": dict(config.builtin_features(document))}
        except (LauncherSyncError, OSError, ValueError) as error:
            result["error"] = str(error)
        result["apply"] = describe(saved_plan, config)
        record = service._load_record(game) if hasattr(service, "_load_record") else None
        if record:
            result["restore"] = {"environment": {k: v.get("previous") for k, v in sorted(record.get("env", {}).items())},
                                 "dll": {k: v.get("previous") for k, v in sorted(record.get("dll", {}).items())},
                                 "settings": {k: (None if v.get("previous") == "__gameopti_missing__" else v.get("previous"))
                                              for k, v in sorted(record.get("settings", {}).items())},
                                 "wrapper": record.get("wrapper"), "appliedAt": record.get("appliedAt", ""),
                                 "appliedRevision": record.get("planDigest", ""),
                                 "byteIdentical": bool(record.get("appliedDigest")) and bool(config)
                                 and hashlib.sha256(config.text.encode()).hexdigest() == record.get("appliedDigest")}
        key = game.profile_key
        stamps = {}
        for name, repository in (("optimization", app._optimization_profile_repository),
                                 ("protonTweaks", app._proton_tweaks_repository)):
            try:
                path = repository.path(key)
                stamps[name] = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat() if path.is_file() else ""
            except (OSError, ValueError, AttributeError):
                stamps[name] = ""
        result["savedAt"] = stamps
        result["state"] = ("not_applied" if not record else
                           "applied" if record.get("planDigest") == saved_plan.digest() else "outdated")
        if profile_draft is not None:
            try:
                draft_plan = self.launcher_plan(game, profile_draft=profile_draft)
                draft = describe(draft_plan, config)
                result["draft"] = draft if draft["revision"] != result["apply"]["revision"] else None
            except (LauncherSyncError, OSError, ValueError) as error:
                result["draft"] = {"error": str(error)}
        return result

    def _plan_source(self, app_id: str, plan: Any) -> dict[str, Any]:
        """What the preview was built from: saved profiles only, never an editor draft."""
        from ..services.frame_generation import profile_path as fg_profile_path
        root = self._app._optiscaler_service.profile_repository.root
        path = fg_profile_path(app_id, root) if app_id else None
        saved, configured, modified = "", "", ""
        try:
            if path is not None and path.is_file():
                state = load_fg_profile(app_id, root)
                saved, configured = str(state.get("backend", "")), str(state.get("configuredAt", ""))
                modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        except (OSError, ValueError):
            saved = "invalid"
        post = {}
        try:
            from ..services.post_processing import load_profile as load_post_profile
            post = load_post_profile(app_id, root)
        except (OSError, ValueError):
            pass
        commit = ""
        try:
            info = Path("/.flatpak-info").read_text(encoding="utf-8")
            commit = next((line.split("=", 1)[1].strip() for line in info.splitlines() if line.startswith("app-commit=")), "")
        except OSError:
            pass
        digest = hashlib.sha256(json.dumps(plan.to_dict(), sort_keys=True, default=str).encode()).hexdigest()
        return {"source": "saved_profiles", "savedFrameGenerationBackend": saved,
                "frameGenerationConfiguredAt": configured, "frameGenerationProfileModifiedAt": modified,
                "postProcessingBackend": str(post.get("backend", "off")) if post.get("phase") == "installed" else "off",
                "planRevision": digest[:12], "generatedAt": datetime.now(UTC).isoformat(),
                "applicationCommit": commit[:12],
                "runnerSource": "installed Flatpak at launch time (flatpak run --command=game-optimization-run)"}
    _SANDBOX_LAYER_EXTENSIONS = {"gamescope": "gamescope", "mangohud": "MangoHud"}

    _SANDBOX_TOOLS = ("gamemoderun", "gamescope", "mangohud")
    _SANDBOX_TTL_SECONDS = 60.0
    _SANDBOX_FAILURE_TTL_SECONDS = 10.0

    def _launcher_sandbox(
        self, game: Game, wanted: list[str]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """Commands and Vulkan layer extensions inside the launcher's Flatpak.

        Never blocks the caller: the host check (``flatpak run``, up to ~20 s)
        runs on a background worker and its result is cached.  Returns
        (capabilities | None, reason, pending).  ``None`` means unknown or not
        checked yet; that blocks Apply instead of pretending the tools exist.
        """

        launcher = game.launcher.value
        app_id = self._LAUNCHER_FLATPAK_IDS.get(launcher, "")
        host = getattr(self._app, "_host_service", None)
        if not app_id or host is None:
            return None, f"{launcher} Flatpak cannot be checked from here", False
        # Always the full tool set: one cached answer serves the launch plan and
        # the capabilities of every game of this launcher.
        del wanted
        key = (app_id, self._SANDBOX_TOOLS)
        now = time.monotonic()
        with self._sandbox_lock:
            cached = self._sandbox_cache.get(key)
            if cached is not None:
                checked_at, capabilities, reason = cached
                ttl = self._SANDBOX_TTL_SECONDS if capabilities is not None else self._SANDBOX_FAILURE_TTL_SECONDS
                if now - checked_at < ttl:
                    return capabilities, reason, False
            job = self._sandbox_jobs.get(key)
            if job is None or job.done():
                if self._sandbox_executor is None:
                    self._sandbox_executor = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="game-optimization-launcher-sandbox"
                    )
                self._sandbox_jobs[key] = self._sandbox_executor.submit(
                    self._probe_launcher_sandbox, host, launcher, app_id, key
                )
        if cached is not None:
            # Serve the stale answer while it refreshes in the background.
            return cached[1], cached[2], False
        return None, f"Checking the {launcher} Flatpak sandbox; try again in a moment", True

    def _probe_launcher_sandbox(
        self, host: Any, launcher: str, app_id: str, key: tuple[str, tuple[str, ...]]
    ) -> None:
        try:
            capabilities = host.flatpak_app_capabilities(app_id, list(key[1]))
            runtime = host.flatpak_runtime(app_id) if capabilities is not None else ""
        except Exception as error:  # noqa: BLE001 - shown as a blocker
            result: tuple[dict[str, Any] | None, str] = (
                None, f"The {launcher} Flatpak sandbox could not be checked: {error}",
            )
        else:
            if capabilities is None:
                result = (None, f"The {launcher} Flatpak sandbox could not be checked")
            else:
                branch = runtime.rsplit("/", 1)[-1] if runtime else ""
                result = ({**capabilities, "branch": branch}, "")
        with self._sandbox_lock:
            self._sandbox_cache[key] = (time.monotonic(), *result)

    def _refresh_launcher_sandbox(self, app_id: str) -> dict[str, Any] | None:
        """Drop every cached sandbox answer of ``app_id`` and probe again now.
        Blocking: only called from the components worker."""
        host = getattr(self._app, "_host_service", None)
        if host is None:
            return None
        invalidate = getattr(host, "invalidate_app_capabilities", None)
        if callable(invalidate):
            invalidate(app_id)
        key = (app_id, self._SANDBOX_TOOLS)
        with self._sandbox_lock:
            self._sandbox_cache.pop(key, None)
        launcher = next(
            (name for name, value in self._LAUNCHER_FLATPAK_IDS.items() if value == app_id), app_id
        )
        self._probe_launcher_sandbox(host, launcher, app_id, key)
        with self._sandbox_lock:
            cached = self._sandbox_cache.get(key)
        return cached[1] if cached is not None else None

    def _components_needed(self, game: Game) -> tuple[str, list[str], bool]:
        """(launcher app id, tools the profile needs but the launcher sandbox
        lacks, sandbox still pending)."""
        app_id = self._LAUNCHER_FLATPAK_IDS.get(game.launcher.value, "")
        if game.launcher_variant != "flatpak" or not app_id or not game.profile_key:
            return "", [], False
        profile = self._app._optimization_profile_repository.load(game.profile_key)
        wanted = {
            "mangohud": bool(self._app._mangohud_repository.load(game.profile_key).enabled),
            "gamescope": bool(profile.gamescope_enabled and profile.gamescope_mode != "disabled"),
        }
        environments = self._launcher_tool_environments(game)
        pending = any(item.get("pending") for item in environments.values())
        needed = [
            tool for tool in LAUNCHER_COMPONENT_TOOLS
            if wanted.get(tool) and environments.get(tool, {}).get("launcher") is False
        ]
        return app_id, needed, pending

    def getLauncherComponents(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"components": [], "future": [], "pending": False, "canRepair": False}
        app_id, needed, pending = self._components_needed(game)
        if not app_id:
            return {"components": [], "future": [], "pending": False, "canRepair": False}
        status = self._components.status(app_id, needed, launcher_label=game.launcher.value)
        status["pending"] = bool(status.get("pending") or pending)
        # Installed (available in the sandbox), applied to the launcher and
        # active in a game are separate facts; activity is never claimed
        # without runtime evidence.
        launcher_status: dict[str, Any] = {}
        if status.get("components"):
            try:
                launcher_status = self._app._launcher_integration.status(game)
            except Exception:  # noqa: BLE001 - informational only
                launcher_status = {}
        included = set(launcher_status.get("included", ()))
        applied = launcher_status.get("state") == "applied"
        for item in status.get("components", ()):
            item["applied"] = bool(applied and item.get("tool") in included)
            item["runtimeVerified"] = False
        return status

    def repairLauncherComponents(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Select a game first"}
        app_id, needed, _pending = self._components_needed(game)
        started = self._components.repair(
            app_id, needed,
            launcher_running=lambda: self._app._launcher_integration.launcher_running(game),
        ) if app_id else []
        result = self.getLauncherComponents(game_id)
        result["success"] = bool(started)
        result["started"] = started
        if not started:
            result["error"] = "Nothing can be installed right now"
        return result

    def shutdown(self) -> None:
        self._components.shutdown()
        sync_executor, self._mangohud_sync_executor = self._mangohud_sync_executor, None
        if sync_executor is not None:
            sync_executor.shutdown(wait=False, cancel_futures=True)
        executor = self._sandbox_executor
        self._sandbox_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def getGameCapabilities(self, game_id: str) -> dict[str, Any]:
        """Cheap per-game feature availability with concrete reasons."""
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {}
        service = self._app._launcher_integration
        facts = writable = None
        tools = None
        if service.supports(game):
            facts = launcher_facts(game, getattr(service, "_home", None))
            writable = service.config_access(game) if facts.config_found else None
            tools = self._launcher_tool_environments(game)
        result = game_capabilities(
            game, launcher_facts=facts, launcher_writable=writable, launcher_tools=tools
        )
        from ..services.measured_optimization.manual import is_manual
        if is_manual(game):
            # Custom games: the optimization profile and measured workflow are
            # available exactly when the launch path can be measured.
            from ..services.measured_optimization.capabilities import REASONS
            support = self.manual_measurement_support(game)
            reason = support.get("reason") or REASONS.get(support.get("code", ""), "")
            result["optimization"] = {"available": bool(support["supported"]),
                                      "reason": "" if support["supported"] else reason}
        return result

    _TOOL_LABELS = {"gamemoderun": "GameMode", "gamescope": "Gamescope", "mangohud": "MangoHud"}

    def _cached_host_tools(self) -> dict[str, bool | None]:
        """Host availability only from detectors that already ran (never
        blocks the GUI thread with a host call)."""
        result: dict[str, bool | None] = {name: None for name in self._TOOL_LABELS}
        cached = getattr(self._app._runtime_tool_detector, "_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2:
            result["gamemoderun"] = bool(cached[0].available)
            result["gamescope"] = bool(cached[1].available)
        mangohud_cache = getattr(self._app._mangohud_detector, "_cache", None)
        if isinstance(mangohud_cache, dict) and "native" in mangohud_cache:
            result["mangohud"] = bool(mangohud_cache["native"].available)
        return result

    def _launcher_tool_environments(self, game: Game) -> dict[str, dict[str, Any]]:
        """GameMode/Gamescope/MangoHud per environment: host, GameOpti's own
        sandbox and the launcher/game runtime that really starts the game."""
        launcher = game.launcher.value
        host = self._cached_host_tools()
        sandbox: dict[str, Any] | None = None
        reason = ""
        pending = False
        flatpak = game.launcher_variant == "flatpak"
        if flatpak:
            sandbox, reason, pending = self._launcher_sandbox(game, list(self._SANDBOX_TOOLS))
        commands = set((sandbox or {}).get("commands", ()))
        extensions = set((sandbox or {}).get("extensions", ()))
        branch = str((sandbox or {}).get("branch", ""))
        result: dict[str, dict[str, Any]] = {}
        for tool, label in self._TOOL_LABELS.items():
            if flatpak:
                if sandbox is None:
                    in_launcher: bool | None = None
                elif tool == "mangohud":
                    in_launcher = "MangoHud" in extensions or tool in commands
                elif tool == "gamescope":
                    in_launcher = tool in commands
                else:
                    in_launcher = tool in commands
            else:
                in_launcher = host[tool]
            if in_launcher is True:
                entry_reason = ""
            elif in_launcher is False:
                entry_reason = (
                    f"{label} is not available in the {launcher} Flatpak environment"
                    f" (runtime {branch or 'unknown'})"
                    if flatpak else f"{label} is not installed on the host"
                )
            else:
                entry_reason = reason or f"{label} availability for {launcher} is not known yet"
            result[tool] = {
                "label": label,
                "host": host[tool],
                "gameopti": bool(shutil.which(tool)),
                "launcher": in_launcher,
                "launcherEnvironment": (
                    f"{launcher} Flatpak (runtime {branch})" if flatpak and branch
                    else f"{launcher} Flatpak" if flatpak else "host"
                ),
                "available": in_launcher is True,
                "pending": bool(pending),
                "reason": entry_reason,
            }
        return result

    # -- MangoHud config of launcher games ------------------------------------
    def _mangohud_staging_dir(self) -> Path | None:
        staging = getattr(self._app._launcher_integration, "_staging_dir", None)
        return staging() if callable(staging) else None

    def _launcher_mangohud_expected(self, game: Game) -> tuple[Path, str] | None:
        """Host path and exact text the launcher should read for this game."""
        key = game.profile_key
        if not key:
            return None
        profile = self._app._mangohud_repository.load(key)
        if not profile.enabled:
            return None
        availability, _runtime = self._app._mangohud_controller._availability(game, profile)
        effective = self._app._effective_mangohud_profile(profile)
        return launcher_mangohud_path(key), MangoHudConfigWriter(availability.supported_keys).render(effective)

    def schedule_launcher_mangohud_sync(self, game: Game) -> dict[str, str]:
        """After a MangoHud profile save: refresh the host config the launcher
        reads, but only while GameOpti's launcher integration is really applied
        and points at GameOpti's own file. Runs on a worker."""
        service = self._app._launcher_integration
        if not service.supports(game) or not game.profile_key:
            return {"state": "not_launcher", "message": ""}
        applied = service.managed_env_value(game, "MANGOHUD_CONFIGFILE")
        if applied is None:
            return {"state": "not_applied", "message": "Profile saved; apply it to the launcher"}
        expected = launcher_mangohud_path(game.profile_key)
        if applied != str(expected):
            return {
                "state": "refused",
                "message": "The launcher uses a MangoHud config GameOpti does not manage; it was not changed",
            }
        with self._mangohud_sync_lock:
            self._mangohud_sync[game.id] = {"state": "syncing", "message": ""}
            if self._mangohud_sync_executor is None:
                self._mangohud_sync_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="game-optimization-mangohud-sync"
                )
            self._mangohud_sync_executor.submit(self._sync_launcher_mangohud, game)
        return {"state": "syncing", "message": "Synchronizing the MangoHud config"}

    def _sync_launcher_mangohud(self, game: Game) -> None:
        state, message = "current", ""
        try:
            service = self._app._launcher_integration
            expected = self._launcher_mangohud_expected(game)
            if expected is None:
                state, message = "error", "MangoHud is disabled in the profile"
            elif service.managed_env_value(game, "MANGOHUD_CONFIGFILE") != str(expected[0]):
                state, message = "error", "The launcher integration is no longer applied"
            else:
                writer = getattr(service, "_host_writer", None)
                write_launcher_mangohud(
                    expected[0], expected[1],
                    host_writer=writer if callable(writer) else None,
                    staging_dir=self._mangohud_staging_dir(),
                )
                logger.info("Synchronized launcher MangoHud config for %s: %s", game.id, expected[0])
        except (LauncherMangoHudError, OSError, ValueError) as error:
            state, message = "error", str(error)
            logger.warning("Could not synchronize launcher MangoHud config for %s: %s", game.id, error)
        with self._mangohud_sync_lock:
            self._mangohud_sync[game.id] = {"state": state, "message": message}

    def _with_mangohud_config_state(self, game: Game, status: dict[str, Any]) -> dict[str, Any]:
        """'Applied' needs the launcher entries AND the host MangoHud config
        to match the saved profile."""
        if status.get("state") != "applied" or "MANGOHUD_CONFIGFILE" not in (status.get("environment") or {}):
            return status
        with self._mangohud_sync_lock:
            job = dict(self._mangohud_sync.get(game.id, {}))
        launcher = game.launcher.value
        if job.get("state") == "syncing":
            return {**status, "state": "syncing", "canApply": False,
                    "message": "Synchronizing the MangoHud config"}
        expected = self._launcher_mangohud_expected(game)
        current = read_launcher_mangohud(expected[0]) if expected is not None else None
        if expected is not None and current == expected[1]:
            return {**status, "configCurrent": True,
                    "message": f"{launcher} starts this game with the GameOpti settings; the MangoHud config is current"}
        error = job.get("message") if job.get("state") == "error" else ""
        return {
            **status, "state": "config_outdated", "configCurrent": False, "canSyncConfig": True,
            "message": "The MangoHud config is outdated" + (f": {error}" if error else ""),
        }

    def syncLauncherMangoHudConfig(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"state": "unsupported", "message": "Select a game first"}
        sync = self.schedule_launcher_mangohud_sync(game)
        return {**self.getLauncherIntegration(game_id), "sync": sync}

    def getLauncherIntegration(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"state": "unsupported", "message": "Select a game first"}
        return self._with_mangohud_config_state(game, self._app._launcher_integration.status(game))

    def _launcher_change(self, game_id: str, action: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"state": "unsupported", "message": "Select a game first", "success": False}
        service = self._app._launcher_integration
        try:
            result = getattr(service, action)(game)
        except (LauncherSyncError, OSError) as error:
            logger.warning("Launcher %s failed for %s: %s", action, game.id, error)
            return {**self._with_mangohud_config_state(game, service.status(game)),
                    "success": False, "error": str(error)}
        with self._mangohud_sync_lock:
            # Apply wrote the latest profile; Restore makes old sync state moot.
            self._mangohud_sync.pop(game.id, None)
        try:
            from ..services import launch_hooks
            home = Path(getattr(service, "_home", None) or Path.home())
            record = service._load_record(game) if action == "apply" else None
            text = " ".join(str(v) for v in ((record or {}).get("wrapper") or {}).values())
            directory = launch_hooks.location(game, home)
            keep = [path for path in directory.iterdir() if str(path) in text] if text and directory.is_dir() else []
            launch_hooks.prune(game, home, keep)
        except (OSError, ValueError) as error:
            logger.warning("Could not tidy GameOpti launch hook files for %s: %s", game.id, error)
        return {**self._with_mangohud_config_state(game, result), "success": True}

    def applyLauncherIntegration(self, game_id: str) -> dict[str, Any]:
        return self._launcher_change(game_id, "apply")

    def restoreLauncherIntegration(self, game_id: str) -> dict[str, Any]:
        return self._launcher_change(game_id, "restore")

    def previewOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            profile = self._app._optimization_profile_from_payload(app_id, values)
            result = self._app._optimization_profile_to_qml(profile)
            result["launchActivation"] = self._launch_activation(game)
            result["launcherPreview"] = self._launcher_preview(game, profile)
            return result
        except Exception as error:
            return {"success": False, "error": str(error)}

    def saveOptimizationProfile(
        self, game_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            previous_profile = self._app._optimization_profile_repository.load(
                app_id
            )
            profile = self._app._optimization_profile_from_payload(app_id, values)
            display = self._app._optimization_display_for(profile.target_display_id)
            recommendation = self._app._optimization_advisor.recommend(
                profile,
                display,
                self._saved_session_performance(app_id),
                system_info=self._app._system_info,
            )
            profile = replace(
                profile,
                target_fps=(
                    recommendation.target_fps
                    if profile.target_fps_mode == "automatic"
                    else profile.target_fps
                ),
                last_recommendation=recommendation.to_dict(),
                updated_at=datetime.now(UTC),
            )
            gamemode, gamescope = self._app._runtime_tool_detector.detect()
            if profile.gamemode_enabled and not gamemode.available:
                raise ValueError(gamemode.message)
            if profile.gamescope_enabled and not gamescope.available:
                raise ValueError(gamescope.message)
            result = self._app._optimization_profile_to_qml(profile)
            path = self._app._optimization_profile_repository.save(profile)
            try:
                # Rewrites MangoHud's generated config for the new limiter owner;
                # the stored MangoHud preference itself is never erased.
                self._app._sync_mangohud_fps_owner(game)
            except Exception:
                self._app._optimization_profile_repository.save(previous_profile)
                raise
            result.update({"success": True, "profilePath": str(path),
                           "launchActivation": self._launch_activation(game)})
            self._app._emit_toast("Optimization profile saved", "success")
            return result
        except Exception as error:
            self._app._emit_toast(f"Could not save optimization profile: {error}", "error")
            return {"success": False, "error": str(error)}

    def _gamescope_hdr_readiness(self, gamescope: Any) -> dict[str, Any]:
        """Evidence-based HDR hints; the monitor's HDR support is never claimed."""

        session = str(os.environ.get("XDG_SESSION_TYPE", "")).strip().casefold()
        wayland = session == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        supported = "--hdr-enabled" in set(gamescope.effective_options)
        vendor = str(self._app._system_info.get("gpuVendor", "")).casefold()
        wsi = str(gamescope.to_dict().get("wsiEnvironment") or "")
        notes = ["wayland_session" if wayland else "not_wayland_session"]
        if not supported:
            notes.append("hdr_flag_missing")
        if "nvidia" in vendor:
            notes.append("nvidia_modeset")
        if wsi:
            notes.append("wsi_detected")
        elif os.environ.get("FLATPAK_ID"):
            notes.append("wsi_checked_at_launch")
        else:
            notes.append("wsi_missing")
        state = "unavailable" if not supported else "ready" if wayland else "warning"
        return {"state": state, "notes": notes, "wsiEnvironment": wsi, "session": session}

    def refreshGamescopeDetection(self) -> dict[str, Any]:
        """Re-run ``gamescope --version`` / ``--help`` once, on user request."""

        _gamemode, gamescope = self._app._runtime_tool_detector.detect(refresh=True)
        result = gamescope.to_dict()
        result["hdrReadiness"] = self._gamescope_hdr_readiness(gamescope)
        return result

    def resetGamescopeSettings(self, game_id: str) -> dict[str, Any]:
        """Restore only this game's Gamescope fields to the program defaults."""

        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "error": "Optimization profiles require a supported game"}
        try:
            previous = self._app._optimization_profile_repository.load(app_id)
            defaults = type(previous).default(app_id)
            profile = replace(
                previous,
                **{name: getattr(defaults, name) for name in GAMESCOPE_PROFILE_FIELDS},
                updated_at=datetime.now(UTC),
            )
            path = self._app._optimization_profile_repository.save(profile)
            try:
                self._app._sync_mangohud_fps_owner(game)
            except Exception:
                self._app._optimization_profile_repository.save(previous)
                raise
        except Exception as error:
            self._app._emit_toast(f"Could not restore Gamescope settings: {error}", "error")
            return {"success": False, "error": str(error)}
        result = self._app._optimization_profile_to_qml(profile)
        result.update({"success": True, "profilePath": str(path),
                       "launchActivation": self._launch_activation(game)})
        self._app._emit_toast("Gamescope settings restored", "success")
        return result

    def startGamescopeTest(
        self, game_id: str, values: Mapping[str, Any], include_hdr: bool = False
    ) -> dict[str, Any]:
        """Run ``gamescope [current args] -- vkcube``; nothing is saved.

        HDR is dropped unless the user explicitly confirmed an HDR test.
        """

        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "code": "unsupported_game",
                    "message": "Optimization profiles require a supported game"}
        try:
            profile = self._app._optimization_profile_from_payload(app_id, values)
        except Exception as error:
            return {"success": False, "code": "invalid", "message": str(error)}
        _gamemode, gamescope = self._app._runtime_tool_detector.detect()
        if not gamescope.available:
            return {"success": False, "code": "gamescope_missing",
                    "message": gamescope.message or "Gamescope is not installed"}
        profile = replace(
            profile,
            gamescope_enabled=True,
            gamescope_mode=profile.gamescope_mode if profile.gamescope_mode != "disabled" else "custom",
            gamemode_enabled=False,
            gamescope_hdr=bool(profile.gamescope_hdr and include_hdr),
        )
        try:
            mangohud_profile = self._app._mangohud_repository.load(app_id)
            owner = "steam_environment" if mangohud_profile.enabled else "none"
            config = (
                str(self._app._mangohud_repository.config_path(app_id))
                if mangohud_profile.enabled else ""
            )
            plan = self._app._optimization_launch_planner.build(
                profile, ["vkcube"], gamemode=_gamemode, gamescope=gamescope,
                mangohud_activation_owner=owner, mangohud_config_path=config,
            )
        except Exception as error:
            return {"success": False, "code": "invalid", "message": str(error)}
        allowed = {"MANGOHUD_CONFIGFILE", *(gamescope.wsi_environment[:1] if gamescope.wsi_environment else ())}
        environment = {key: value for key, value in plan.environment.items() if key in allowed}
        result = self._app._gamescope_test_run.start(
            gamescope.executable, list(plan.gamescope_wrapper[1:-1]), environment
        ).to_dict()
        result["running"] = self._app._gamescope_test_run.running
        result["arguments"] = [_display_path(value) for value in plan.gamescope_wrapper[1:-1]]
        return result

    def stopGamescopeTest(self) -> bool:
        return self._app._gamescope_test_run.stop()

    def testGameOptimizationRunner(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id:
            return {"success": False, "message": "Optimization profiles require a supported game"}
        result = self._app._runner_integration.test(app_id)
        self._app._emit_toast(
            str(result.get("message", "Runner test completed")),
            "success" if result.get("success") else "warning",
        )
        return result

    def analyzeGameOptimization(
        self, game_id: str, log_path: str = ""
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "error": "Game analysis requires an available game"}
        if game_id in self._app._optimization_jobs:
            return {"success": True, "status": "running", "gameId": game_id}
        try:
            profile = self._app._optimization_profile_repository.load(app_id)
            display = self._app._optimization_display_for(profile.target_display_id)
            gamemode, gamescope = self._app._runtime_tool_detector.detect()
            selected_log = self._select_mangohud_log(game, app_id, log_path)
            future = self._app._optimization_executor.submit(
                self._analyze_game,
                game,
                profile,
                display,
                dict(self._app._system_info),
                selected_log,
                gamemode.available,
                gamescope.available,
            )
            self._app._optimization_jobs[game_id] = future
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}
        return {
            "success": True,
            "status": "running",
            "gameId": game_id,
            "baselineLog": str(selected_log or ""),
        }

    def recordOptimizationBaseline(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        logger.info(
            "Optimization measurement requested: kind=baseline gameId=%s appId=%s",
            game_id,
            app_id or "unresolved",
        )
        active_change = self._active_optimization_change(game)
        if active_change is not None:
            if active_change.get("kind") == "runtime_profile":
                return self._measurement_rejection(
                    game_id,
                    app_id,
                    "baseline",
                    "pending_automatic_comparison",
                    "Record a comparison or revert the pending Automatic Optimization test first",
                )
            return self._measurement_rejection(
                game_id,
                app_id,
                "baseline",
                "pending_settings_comparison",
                "Record a comparison or revert the pending setting test first",
            )
        return self._record_optimization_measurement(game_id, kind="baseline")

    LAUNCHER_MEASUREMENT_UNSUPPORTED = (
        "Automatic recording is not available for Lutris games yet. Record a "
        "MangoHud log in the game (logging key) and use Import MangoHud log"
    )
    # Heroic Flatpak may write only below ~/Games/Heroic (permission
    # ~/Games/Heroic:create); MangoHud logs from inside the game there.
    HEROIC_MEASUREMENT_FOLDER = "Games/Heroic/.gameopti-measurements"
    LAUNCHER_EXIT_CONFIRMATIONS = 3
    LAUNCHER_PROCESS_CHECK_SECONDS = 5.0
    MANUAL_PROCESS_CHECK_SECONDS = 2.0

    def _launcher_measurement_root(self, game: Game) -> Path | None:
        if game.launcher_variant != "flatpak":
            return None  # a native launcher can use GameOpti's own folder
        return host_home_directory(os.environ) / self.HEROIC_MEASUREMENT_FOLDER

    def _process_detector(self) -> Any | None:
        """Read-only game process check (host ``ps`` inside Flatpak)."""
        if self._launcher_activity is not None:
            return self._launcher_activity
        host = getattr(self._app, "_host_service", None)
        host_processes = (
            host.host_process_commands
            if host is not None and getattr(host, "in_flatpak", False)
            else None
        )
        detector = NarratorGameActivityDetector(
            lambda game_id: self._app._resolve_game(game_id, show_error=False),
            host_processes=host_processes,
        )
        if not detector.can_observe_processes:
            return None
        self._launcher_activity = detector
        return detector

    def _record_launcher_measurement(self, game: Game, app_id: str, kind: str) -> dict[str, Any]:
        """Baseline/comparison for a Heroic game: GameOpti writes the private
        MangoHud logging variables into the game's Heroic settings, starts the
        game through Heroic, observes the real game process and removes the
        variables again when the game has ended. Only a real MangoHud log is
        analysed."""

        service = self._app._launcher_integration
        launcher = game.launcher.value
        game_id = game.id
        if game.launcher_variant == "flatpak":
            mangohud = self._launcher_tool_environments(game).get("mangohud", {})
            if mangohud.get("launcher") is None:
                return self._measurement_rejection(
                    game_id, app_id, kind, "launcher_sandbox_pending",
                    str(mangohud.get("reason")
                        or f"Checking the {launcher} Flatpak sandbox; try again in a moment"),
                )
            if mangohud.get("launcher") is False:
                return self._measurement_rejection(
                    game_id, app_id, kind, "mangohud_unavailable", str(mangohud.get("reason") or ""),
                )
        else:
            availability = self._app._mangohud_detector.detect("native")
            if not availability.available:
                return self._measurement_rejection(
                    game_id, app_id, kind, "mangohud_unavailable", availability.message,
                )
        if self._process_detector() is None:
            return self._measurement_rejection(
                game_id, app_id, kind, "process_list_unavailable",
                "GameOpti cannot see running games here, so it could not tell when the game ends",
            )
        if service.launcher_running(game) is True:
            return self._measurement_rejection(
                game_id, app_id, kind, "launcher_running",
                f"Close {launcher} first; GameOpti starts it for the measurement",
            )
        session = None
        try:
            session = self._app._baseline_sessions.create(
                app_id, game_id, kind=kind, launcher=launcher,
                output_root=self._launcher_measurement_root(game),
            )
            service.begin_measurement(game, self._app._baseline_sessions.environment(session))
            if kind == "baseline":
                self._app._optimization_comparisons.pop(game_id, None)
            command = self._app._launcher_native.launch(game)
            session = self._app._baseline_sessions.mark_launcher_started(
                app_id, session.id, shlex.join(str(part) for part in command)
            ) or session
        except Exception as error:  # noqa: BLE001 - reported to the user
            if session is not None:
                try:
                    self._app._baseline_sessions.fail(app_id, str(error), session.id)
                except Exception:  # noqa: BLE001
                    pass
            try:
                service.end_measurement(game)
            except Exception:  # noqa: BLE001 - retried by the poller
                pass
            return self._measurement_rejection(
                game_id, app_id, kind, "session_start_failed", str(error) or type(error).__name__,
            )
        self._launcher_negative_reads.pop(game_id, None)
        self._launcher_next_check.pop(game_id, None)
        self._app._active_baseline_games.add(game_id)
        self._app._baseline_statuses[game_id] = session.status
        self._app.optimizationAnalysisChanged.emit(game_id)
        self._app._emit_toast(
            f"{launcher} is starting the game; recording starts with the game", "info",
        )
        logger.info(
            "Optimization measurement session started: kind=%s gameId=%s appId=%s "
            "sessionId=%s status=%s launcher=%s config=%s",
            kind, game_id, app_id, session.id, session.status, launcher, session.config_path,
        )
        return {"success": True, "status": session.status, "baselineSession": session.to_dict()}

    def _poll_launcher_session(self, game: Game, app_id: str, session: Any) -> Any:
        """Advance a Heroic measurement session from the observed game process."""
        sessions = self._app._baseline_sessions
        now = time.monotonic()
        if now < self._launcher_next_check.get(game.id, 0.0):
            return session
        manual = session.launcher == "Manual"
        self._launcher_next_check[game.id] = now + (self.MANUAL_PROCESS_CHECK_SECONDS if manual
                                                    else self.LAUNCHER_PROCESS_CHECK_SECONDS)
        detector = self._process_detector()
        if manual:
            if detector is not None:
                detector.invalidate()  # a short game must not be missed behind the shared cache
            running, match = self._manual_game_running(game)
        else:
            running = detector.game_process_running(game.id) if detector is not None else None
            match = getattr(detector, "last_match", {}).get(game.id, "") if detector is not None else ""
        if running is True:
            self._launcher_negative_reads.pop(game.id, None)
            return sessions.mark_launcher_game_running(app_id, session.id, match) or session
        if session.status == "waiting_for_launcher":
            if datetime.now(UTC) - session.created_at > timedelta(seconds=session.handshake_timeout_seconds):
                return sessions.fail(
                    app_id,
                    f"The game did not start within {session.handshake_timeout_seconds} seconds "
                    f"after {session.launcher} was opened",
                    session.id,
                ) or session
            return session
        if running is False:
            count = self._launcher_negative_reads.get(game.id, 0) + 1
            self._launcher_negative_reads[game.id] = count
            if count >= self.LAUNCHER_EXIT_CONFIRMATIONS:
                self._launcher_negative_reads.pop(game.id, None)
                return sessions.finish_launcher_session(app_id, session.id) or session
        elif session.started_at and datetime.now(UTC) - session.started_at > timedelta(hours=4, minutes=5):
            return sessions.fail(
                app_id, "Baseline recording exceeded the four-hour MangoHud limit", session.id,
            ) or session
        return session

    def manual_measurement_support(self, game: Game) -> dict[str, Any]:
        """Whether this Custom/Manual entry can be measured, with the exact reason."""
        from ..services.measured_optimization import manual
        getter = getattr(self._app._game_provider, "manual_config", None)
        configuration = getter(game.id) if callable(getter) else None
        if configuration is None:
            return {"supported": False, "code": "manual_config_missing", "runner": {}}
        if not manual.optimization_key(game):
            return {"supported": False, "code": "manual_identity_invalid", "runner": {}}
        runner = manual.runner_info(configuration)
        if runner["code"]:
            return {"supported": False, "code": runner["code"], "runner": runner, "configuration": configuration}
        if not manual.tree_tracking_available():
            return {"supported": False, "code": "manual_tree_unverified", "runner": runner, "configuration": configuration}
        if not game.launch_available:
            return {"supported": False, "code": "manual_unavailable", "runner": runner, "configuration": configuration,
                    "reason": game.launch_unavailable_reason}
        return {"supported": True, "code": "", "runner": runner, "configuration": configuration}

    def _manual_game_running(self, game: Game) -> tuple[bool | None, str]:
        from ..services.measured_optimization.manual import ManualProcessMatcher
        support = self.manual_measurement_support(game)
        detector = self._process_detector()
        if detector is None or "configuration" not in support:
            return None, ""
        matcher = ManualProcessMatcher(support["configuration"], support["runner"].get("kind", ""))
        commands = detector.process_commands()
        if commands is None:
            return None, ""
        match = next((line for line in commands if matcher.matches(line)), "")
        return bool(match), match

    def _record_manual_measurement(self, game: Game, app_id: str, kind: str) -> dict[str, Any]:
        """Baseline/comparison for a Custom/Manual game through its own launch path.

        The launch is the normal Manual lifecycle (same runner, environment,
        custom launch and pre/post commands) inside a tracked host scope; only
        the game command gets private MangoHud logging variables.
        """
        from ..services.measured_optimization.capabilities import REASONS
        game_id = game.id
        support = self.manual_measurement_support(game)
        if not support["supported"]:
            return self._measurement_rejection(game_id, app_id, kind, support["code"],
                                               support.get("reason") or REASONS.get(support["code"], support["code"]))
        availability = self._app._mangohud_detector.detect("native")
        if not availability.available:
            return self._measurement_rejection(game_id, app_id, kind, "mangohud_unavailable", availability.message)
        if self._process_detector() is None:
            return self._measurement_rejection(
                game_id, app_id, kind, "process_list_unavailable",
                "GameOpti cannot see running games here, so it could not tell when the game ends")
        if game.id in getattr(self._app, "_manual_launch_jobs", {}) or self._manual_game_running(game)[0]:
            return self._measurement_rejection(game_id, app_id, kind, "game_running", "The game is running")
        session = None
        try:
            session = self._app._baseline_sessions.create(app_id, game_id, kind=kind, launcher="Manual")
            if kind == "baseline":
                self._app._optimization_comparisons.pop(game_id, None)
            plan = self._app._manual_launch_plan(
                game, support["configuration"],
                measurement_environment=self._app._baseline_sessions.environment(session),
                gpu_metrics=session.directory / "gpu-memory.jsonl", require_tree=True)
            report = dict(self._app._manual_runtime_reports.get(game_id) or {})
            from ..services.gpu_memory import launch_prefix as gpu_prefix, game_key as gpu_key
            if gpu_prefix(gpu_key(game), self._app._gpu_memory_service.root):
                report["gpuMemoryMetrics"] = str(session.directory / "gpu-memory.jsonl")
            report.update(baselineSessionId=session.id, timestamp=datetime.now(UTC).isoformat(),
                          runner=support["runner"].get("kind", ""))
            path = self._app._runner_report_path(app_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self._app._manual_launch_jobs[game_id] = self._app._manual_launch_executor.submit(
                self._app._manual_game_launcher.execute, plan)
            session = self._app._baseline_sessions.mark_launcher_started(
                app_id, session.id, shlex.join(str(part) for part in plan.game_command)) or session
        except Exception as error:  # noqa: BLE001 - reported to the user
            if session is not None:
                try:
                    self._app._baseline_sessions.fail(app_id, str(error), session.id)
                except Exception:  # noqa: BLE001
                    pass
            return self._measurement_rejection(game_id, app_id, kind, "manual_launch_failed",
                                               str(error) or type(error).__name__)
        self._app._active_baseline_games.add(game_id)
        self._app._baseline_statuses[game_id] = session.status
        self._app.optimizationAnalysisChanged.emit(game_id)
        logger.info("Manual measurement started: kind=%s gameId=%s session=%s runner=%s",
                    kind, game_id, session.id, support["runner"].get("kind", ""))
        return {"success": True, "status": session.status, "baselineSession": session.to_dict()}

    def manual_lifecycle_finished(self, game: Game, result: Any, error: str) -> None:
        """The tracked Manual process tree ended: finish or fail its measurement."""
        app_id = self._profile_key(game)
        session = self._app._baseline_sessions.load(app_id) if app_id else None
        if session is None or session.launcher != "Manual" or session.status not in {
            "waiting_for_launcher", "recording", "waiting_for_game_exit",
        }:
            return
        sessions = self._app._baseline_sessions
        detector = self._process_detector()
        if detector is not None:
            detector.invalidate()  # the cached list may predate the end of the tree
        running, _match = self._manual_game_running(game)
        if running:
            sessions.fail(app_id, "The game process left the tracked process tree; GameOpti cannot verify when it ends",
                          session.id)
        elif session.status == "waiting_for_launcher":
            sessions.fail(app_id, error or "The configured game executable was never observed running", session.id)
        else:
            sessions.finish_launcher_session(app_id, session.id)
        self._launcher_negative_reads.pop(game.id, None)
        self._app.optimizationAnalysisChanged.emit(game.id)

    def _cleanup_launcher_measurements(self) -> None:
        """Remove measurement variables from launcher settings once the
        session is over and the launcher has closed (retried while needed)."""
        service = getattr(self._app, "_launcher_integration", None)
        games = getattr(service, "measurement_games", None)
        if not callable(games):
            return
        now = time.monotonic()
        if now < self._launcher_cleanup_at:
            return
        self._launcher_cleanup_at = now + self.LAUNCHER_PROCESS_CHECK_SECONDS
        for game_id in games():
            game = self._app._resolve_game(game_id, show_error=False)
            if game is None:
                continue
            session = self._app._baseline_sessions.load(game.profile_key) if game.profile_key else None
            if session is not None and session.launcher and session.status in {
                "waiting_for_launcher", "recording", "waiting_for_game_exit",
            }:
                # Still measuring (also after a GameOpti restart): keep observing.
                self._app._active_baseline_games.add(game_id)
                continue
            try:
                service.end_measurement(game)
            except Exception as error:  # noqa: BLE001 - retried later
                logger.warning("Could not remove measurement settings for %s: %s", game_id, error)

    def cancelOptimizationMeasurement(self, game_id: str) -> dict[str, Any]:
        """Stop waiting for/recording a measurement. The game is not closed;
        the session ends as cancelled and its launcher settings are removed as
        soon as the launcher has closed."""
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        session = self._app._baseline_sessions.load(app_id) if app_id else None
        if game is None or session is None or session.status not in {
            "waiting_for_steam", "waiting_for_runner", "waiting_for_launcher",
            "recording", "waiting_for_game_exit",
        }:
            return {"success": False, "error": "No measurement is running for this game"}
        self._app._baseline_sessions.fail(app_id, "Recording cancelled", session.id)
        if session.launcher and session.launcher != "Manual":
            try:
                self._app._launcher_integration.end_measurement(game)
            except Exception as error:  # noqa: BLE001 - retried by the poller
                logger.warning("Could not remove measurement settings for %s: %s", game.id, error)
        logger.info("Optimization measurement cancelled: gameId=%s session=%s", game.id, session.id)
        self._app._baseline_statuses[game.id] = "failed"
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {"success": True, "status": "failed"}


    @staticmethod
    def _measurement_rejection(
        game_id: str,
        app_id: str,
        kind: str,
        guard: str,
        reason: str,
        **details: Any,
    ) -> dict[str, Any]:
        logger.warning(
            "Optimization measurement request rejected: kind=%s gameId=%s "
            "appId=%s guard=%s reason=%s",
            kind,
            game_id,
            app_id or "unresolved",
            guard,
            reason,
        )
        return {
            "success": False,
            "code": guard,
            "error": reason,
            **details,
        }

    def recordOptimizationComparison(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if not app_id or self._app._baseline_sessions.load_measurement(
            app_id, slot="before"
        ) is None:
            return {"success": False, "error": "Record a baseline before the comparison"}
        if game is None or self._active_optimization_change(game) is None:
            return {"success": False, "error": "No optimization change is pending comparison"}
        result = self._record_optimization_measurement(game_id, kind="comparison")
        if result.get("success"):
            self._mark_automatic_comparison_started(
                app_id,
                str(result.get("baselineSession", {}).get("id") or ""),
            )
        return result

    def _record_optimization_measurement(
        self, game_id: str, *, kind: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        session = None
        if game is None or not app_id or not game.library_available:
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "game_unavailable",
                "Baseline recording requires an available game",
            )
        current = self._app._baseline_sessions.load(app_id)
        if current is not None and current.status in {
            "waiting_for_steam",
            "waiting_for_runner",
            "waiting_for_launcher",
            "recording",
            "waiting_for_game_exit",
            "processing",
        }:
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "active_measurement_session",
                "A baseline recording is already active for this game",
            )
        if game.launcher is Launcher.HEROIC:
            return self._record_launcher_measurement(game, app_id, kind)
        from ..services.measured_optimization.manual import is_manual
        if is_manual(game):
            return self._record_manual_measurement(game, app_id, kind)
        if game.launcher is Launcher.LUTRIS:
            # The Steam runner never sees Lutris launches and the launcher
            # measurement below has only been built and checked for Heroic.
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "launcher_measurement_unsupported",
                self.LAUNCHER_MEASUREMENT_UNSUPPORTED,
            )
        runner_status = self._app._runner_integration.status()
        if not runner_status.installed:
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "runner_unavailable",
                "Install Game Optimization Runner first",
            )
        try:
            steam_type = "flatpak" if uses_flatpak_steam(game) else "native"
            availability = self._app._mangohud_detector.detect(steam_type)
            if not availability.available:
                return self._measurement_rejection(
                    game_id,
                    app_id,
                    kind,
                    "mangohud_unavailable",
                    availability.message,
                )
            if game.launcher is not Launcher.MANUAL or game.data_source.casefold() != "local":
                preflight_method = getattr(
                    self._app._runner_integration,
                    "steam_launch_option_status",
                    None,
                )
                preflight = preflight_method(game) if callable(preflight_method) else None
                if preflight is not None and preflight.configured is False:
                    return self._measurement_rejection(
                        game_id,
                        app_id,
                        kind,
                        "runner_not_configured",
                        preflight.message,
                        runnerPreflight=preflight.to_dict(),
                    )
            session = self._app._baseline_sessions.create(
                app_id,
                game.id,
                kind=kind,
                expected_runner_path=str(runner_status.path),
                expected_runner_hash=runner_status.sha256,
            )
            if kind == "baseline":
                self._app._optimization_comparisons.pop(game.id, None)
            if game.launcher is Launcher.MANUAL and game.data_source.casefold() == "local":
                self._app._runner_integration.launch_local(game)
            else:
                launch_result = self._app._game_launcher.launch(game)
                self._app._baseline_sessions.mark_steam_launched(
                    app_id, session.id, str(launch_result)
                )
            session = self._app._baseline_sessions.mark_waiting_for_runner(
                app_id, session.id
            ) or session
        except Exception as error:
            if session is not None:
                try:
                    self._app._baseline_sessions.fail(
                        app_id, str(error), session.id
                    )
                except Exception:
                    pass
            return self._measurement_rejection(
                game_id,
                app_id,
                kind,
                "session_start_failed",
                str(error) or type(error).__name__,
            )
        self._app._active_baseline_games.add(game.id)
        self._app._baseline_statuses[game.id] = session.status
        self._app.optimizationAnalysisChanged.emit(game.id)
        self._app._emit_toast(
            "Comparison recording will start with the game"
            if kind == "comparison"
            else "Baseline recording will start with the game",
            "info",
        )
        logger.info(
            "Optimization measurement session started: kind=%s gameId=%s "
            "appId=%s sessionId=%s status=%s",
            kind,
            game_id,
            app_id,
            session.id,
            session.status,
        )
        return {
            "success": True,
            "status": session.status,
            "baselineSession": session.to_dict(),
        }

    def importOptimizationBaseline(
        self, game_id: str, log_path: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        if game is None or not app_id:
            return {"success": False, "error": "Select an available game"}
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Record a comparison or revert the pending setting test first",
            }
        text = str(log_path or "").strip()
        url = QUrl(text)
        if url.isValid() and url.isLocalFile():
            text = url.toLocalFile()
        try:
            session = self._app._baseline_sessions.import_log(
                app_id, game.id, Path(text)
            )
            log = self._app._baseline_sessions.newest_log(app_id)
            if log is None:
                raise ValueError("The imported MangoHud log is empty")
            self._app._mangohud_log_parser.parse(log)
            result = self.analyzeGameOptimization(game.id, str(log))
            if not result.get("success"):
                raise ValueError(str(result.get("error") or "Could not analyze the imported log"))
        except Exception as error:
            try:
                self._app._baseline_sessions.fail(app_id, str(error))
            except Exception:
                pass
            return {"success": False, "error": str(error) or type(error).__name__}
        self._app._active_baseline_games.add(game.id)
        self._app._baseline_statuses[game.id] = session.status
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {"success": True, "status": "running", "baselineSession": session.to_dict()}

    def _poll_baseline_sessions(self) -> None:
        self._cleanup_launcher_measurements()
        for game_id in tuple(self._app._active_baseline_games):
            game = self._app._resolve_game(game_id, show_error=False)
            app_id = self._profile_key(game)
            session = self._app._baseline_sessions.load(app_id) if app_id else None
            if game is None or session is None:
                self._app._active_baseline_games.discard(game_id)
                continue
            previous = self._app._baseline_statuses.get(game_id, "")
            if session.launcher and session.status in {
                "waiting_for_launcher", "recording", "waiting_for_game_exit",
            }:
                session = self._poll_launcher_session(game, app_id, session)
            if session.status == "processing":
                log = self._app._baseline_sessions.newest_log(app_id)
                if log is not None:
                    result = (
                        {"success": True, "status": "running"}
                        if game_id in self._app._optimization_jobs
                        else self.analyzeGameOptimization(game_id, str(log))
                    )
                    if not result.get("success"):
                        session = self._app._baseline_sessions.fail(
                            app_id,
                            str(result.get("error") or "Baseline analysis failed"),
                            session.id,
                        ) or session
                elif session.finished_at and datetime.now(UTC) - session.finished_at > timedelta(seconds=5):
                    artifacts = self._app._baseline_sessions.artifact_diagnostics(
                        app_id
                    )
                    logger.error(
                        "MangoHud baseline output missing: session=%s appId=%s "
                        "config=%s configExists=%s outputDirectory=%s "
                        "outputDirectoryExists=%s files=%s runnerCompletion=%s",
                        session.id,
                        app_id,
                        artifacts["configPath"],
                        artifacts["configExists"],
                        artifacts["outputDirectory"],
                        artifacts["outputDirectoryExists"],
                        artifacts["files"],
                        session.runner_completed_at is not None,
                    )
                    session = self._app._baseline_sessions.fail(
                        app_id,
                        "MangoHud did not produce a baseline log in the private session directory",
                        session.id,
                    ) or session
            elif session.launcher:
                # Launcher sessions end on the observed game exit (above).
                pass
            elif session.status == "recording":
                if self._app._baseline_sessions.newest_log(app_id) is not None:
                    session = self._app._baseline_sessions.mark_waiting_for_game_exit(
                        app_id, session.id
                    ) or session
                elif (
                    session.started_at
                    and datetime.now(UTC) - session.started_at > timedelta(hours=4, minutes=5)
                ):
                    session = self._app._baseline_sessions.fail(
                        app_id,
                        "Baseline recording exceeded the four-hour MangoHud limit",
                        session.id,
                    ) or session
            elif session.status == "waiting_for_game_exit":
                log = self._app._baseline_sessions.newest_log(app_id)
                heartbeat_stale = bool(
                    session.last_heartbeat_at is None
                    or datetime.now(UTC) - session.last_heartbeat_at
                    > timedelta(seconds=RUNNER_HEARTBEAT_STALE_SECONDS)
                )
                try:
                    log_stable = bool(
                        log is not None
                        and datetime.now(UTC).timestamp() - log.stat().st_mtime >= 15
                    )
                except OSError:
                    log_stable = False
                if log_stable and heartbeat_stale:
                    session = self._app._baseline_sessions.finish_from_stable_log(
                        app_id, session.id
                    ) or session
            elif (
                session.status in {"waiting_for_steam", "waiting_for_runner"}
                and datetime.now(UTC) - session.created_at
                > timedelta(seconds=session.handshake_timeout_seconds)
            ):
                runner_path = Path(session.expected_runner_path)
                if session.runner_invocation_count:
                    failure = (
                        "Game Optimization Runner was invoked but rejected the baseline "
                        f"session: {session.runner_rejection or 'reason not reported'}"
                    )
                else:
                    failure = (
                        "Steam did not invoke Game Optimization Runner "
                        f"within {session.handshake_timeout_seconds} seconds"
                    )
                logger.error(
                    "Baseline runner handshake timeout: session=%s appId=%s "
                    "createdAt=%s expectedRunner=%s runnerExists=%s runnerHash=%s "
                    "runnerInvocations=%s runnerRejection=%s steamLaunch=%s "
                    "timeoutSeconds=%s activeState=%s",
                    session.id,
                    app_id,
                    session.created_at.isoformat(),
                    session.expected_runner_path or "not recorded",
                    runner_path.is_file() if session.expected_runner_path else False,
                    session.expected_runner_hash or "not recorded",
                    session.runner_invocation_count,
                    session.runner_rejection or "none observed",
                    session.steam_launch_result or "not recorded",
                    session.handshake_timeout_seconds,
                    session.status,
                )
                session = self._app._baseline_sessions.fail(
                    app_id,
                    failure,
                    session.id,
                ) or session
            if session.status != previous:
                logger.info(
                    "Baseline lifecycle: session=%s appId=%s gameId=%s status=%s "
                    "runnerPid=%s spawnedPid=%s processGroup=%s handshakeAt=%s "
                    "runnerCompletion=%s logExists=%s observed=%s reason=%s",
                    session.id,
                    app_id,
                    game_id,
                    session.status,
                    session.runner_pid,
                    session.spawned_pid,
                    session.process_group,
                    session.handshake_at.isoformat() if session.handshake_at else "not received",
                    session.runner_completed_at is not None,
                    self._app._baseline_sessions.newest_log(app_id) is not None,
                    list(session.observed_processes),
                    session.lifecycle_reason or "not reported",
                )
                self._app._baseline_statuses[game_id] = session.status
                self._app.optimizationAnalysisChanged.emit(game_id)
            if session.status in {"completed", "recorded_unrepresentative", "failed"}:
                self._app._active_baseline_games.discard(game_id)
                if session.status == "failed":
                    self._app._emit_toast(session.error or "Baseline recording failed", "error")

    def _analyze_game(
        self,
        game: Game,
        profile: GameOptimizationProfile,
        display: Any,
        system_info: Mapping[str, Any],
        log_path: Path | None,
        gamemode_available: bool,
        gamescope_available: bool,
    ) -> OptimizationAnalysis:
        app_id = self._profile_key(game)
        selected_executable = self._selected_executable(game, app_id)
        fingerprint = self._app._game_analyzer.analyze(
            game,
            system_info=system_info,
            display=display,
            category=profile.game_category,
            manual_category_override=bool(
                profile.manual_overrides.get("category")
            ),
            selected_executable=selected_executable,
            runtime_hint=self._runtime_hint(app_id, selected_executable),
        )
        measurement = (
            self._app._mangohud_log_parser.parse(log_path)
            if log_path is not None
            else self._app._baseline_sessions.load_measurement(
                app_id, slot="before"
            )
        )
        bottleneck = self._app._bottleneck_analyzer.analyze(
            measurement,
            fingerprint.system,
            target_fps=profile.target_fps,
        )
        frame_rate = self._app._frame_rate_analyzer.analyze(
            measurement,
            fingerprint.system,
        )
        candidates = self._app._game_recommendation_engine.recommend(
            fingerprint,
            measurement,
            bottleneck,
            profile,
            gamemode_available=gamemode_available,
            gamescope_available=gamescope_available,
        )
        settings, settings_candidates = self._app._game_settings_advisor.analyze(
            game,
            fingerprint,
            measurement,
            bottleneck,
            frame_rate,
        )
        return OptimizationAnalysis(
            fingerprint,
            measurement,
            bottleneck,
            (*candidates, *settings_candidates),
            frame_rate,
            settings,
        )

    def _selected_executable(self, game: Game, app_id: str) -> str:
        resolver = self._app._mangohud_launch_integration.executable_resolver
        candidates: list[str] = [game.executable_path]
        try:
            candidates.append(
                self._app._optiscaler_service.profile_repository.load(app_id).executable
            )
        except Exception:
            pass
        try:
            candidates.append(
                self._app._mangohud_repository.load(app_id).executable_path
            )
        except (OSError, ValueError):
            pass
        for candidate in candidates:
            if candidate and resolver.validate_selected(game, candidate) is not None:
                return candidate
        resolution = resolver.resolve(game)
        return (
            resolution.selected.relative_path
            if resolution.reliable and resolution.selected is not None
            else ""
        )

    @staticmethod
    def _runtime_label(tool_name: str) -> str:
        folded = tool_name.casefold()
        if "ge-proton" in folded or "proton-ge" in folded:
            return tool_name
        if "cachy" in folded and "proton" in folded:
            return tool_name
        if "proton" in folded:
            return tool_name
        return ""

    def _runtime_hint(
        self, app_id: str, selected_executable: str
    ) -> DetectedValue | None:
        if not app_id or not selected_executable.casefold().endswith(".exe"):
            return None
        report_path = self._app._runner_report_path(app_id)
        try:
            if report_path.stat().st_size > 1024 * 1024:
                return None
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping) or str(payload.get("appId") or "") != app_id:
            return None
        command = payload.get("steamCommand")
        values = command if isinstance(command, list) else []
        for raw in values:
            path = Path(str(raw))
            if path.name.casefold() != "proton":
                continue
            label = self._runtime_label(path.parent.name)
            if label:
                evidence = DetectionEvidence(
                    "runner launch report",
                    f"Steam selected {path.parent.name}",
                    0.98,
                )
                return DetectedValue(
                    label, 0.98, "last verified Steam LaunchPlan", (evidence,)
                )
        return DetectedValue(
            "Windows game using Steam compatibility layer",
            0.85,
            "resolved PE executable",
        )

    def _poll_analysis_jobs(self) -> None:
        for game_id, future in tuple(self._app._optimization_jobs.items()):
            if not future.done():
                continue
            self._app._optimization_jobs.pop(game_id, None)
            try:
                analysis = future.result()
            except Exception as error:
                logger.exception("Game analysis failed for %s", game_id)
                self._app._optimization_analyses[game_id] = {
                    "success": False,
                    "status": "failed",
                    "error": str(error) or type(error).__name__,
                }
                self._app._emit_toast("Game analysis failed", "error")
            else:
                payload = analysis.to_dict()
                payload.update({"success": True, "status": "completed"})
                self._app._optimization_analyses[game_id] = analysis
                game = self._app._resolve_game(game_id, show_error=False)
                app_id = self._profile_key(game)
                session = self._app._baseline_sessions.load(app_id) if app_id else None
                toast_message = "Game analysis completed"
                toast_level = "success"
                if session is not None and session.status == "processing":
                    if analysis.measurement is not None and analysis.measurement.available:
                        if session.kind == "comparison":
                            before = self._app._baseline_sessions.load_measurement(
                                app_id, slot="before"
                            )
                            self._app._baseline_sessions.save_measurement(
                                app_id, analysis.measurement, slot="after"
                            )
                            if before is not None:
                                self._app._optimization_comparisons[game_id] = (
                                    compare_measurements(
                                        before,
                                        analysis.measurement,
                                        before_frame_rate=self._app._frame_rate_analyzer.analyze(
                                            before, analysis.fingerprint.system
                                        ),
                                        after_frame_rate=analysis.frame_rate,
                                    )
                                )
                            self._complete_automatic_experiment(
                                game_id, app_id, session.id, analysis
                            )
                        else:
                            self._app._baseline_sessions.save_measurement(
                                app_id, analysis.measurement, slot="before"
                            )
                            self._app._optimization_comparisons.pop(game_id, None)
                        self._app._baseline_sessions.complete(app_id, session.id)
                        toast_message = "Baseline recorded"
                    elif analysis.measurement is not None and analysis.measurement.samples > 0:
                        if session.kind == "comparison":
                            before = self._app._baseline_sessions.load_measurement(
                                app_id, slot="before"
                            )
                            self._app._baseline_sessions.save_measurement(
                                app_id, analysis.measurement, slot="after"
                            )
                            if before is not None:
                                self._app._optimization_comparisons[game_id] = (
                                    compare_measurements(before, analysis.measurement)
                                )
                            self._complete_automatic_experiment(
                                game_id, app_id, session.id, analysis
                            )
                        reason = (
                            "Baseline recorded, but the measurement was not representative "
                            "enough for automatic optimization. Repeat the test during "
                            "representative gameplay."
                        )
                        self._app._baseline_sessions.mark_unrepresentative(
                            app_id, session.id, reason
                        )
                        toast_message = reason
                        toast_level = "warning"
                    else:
                        self._app._baseline_sessions.fail(
                            app_id,
                            "The MangoHud log did not contain usable performance samples",
                            session.id,
                        )
                        toast_message = (
                            "Baseline recording failed: the MangoHud log contained "
                            "no performance samples"
                        )
                        toast_level = "error"
                measured = getattr(self._app, "_measured_optimization", None)
                if measured is not None and game is not None and session is not None and session.status == "processing":
                    measured.on_measurement_processed(game, app_id, session)
                if app_id and getattr(
                    self._app, "_optimization_analysis_persistence_enabled", False
                ):
                    try:
                        self._app._optimization_analysis_repository.save(
                            app_id, analysis
                        )
                        self._analysis_cache_states[game_id] = "current"
                    except (OSError, ValueError) as error:
                        logger.warning(
                            "Could not persist optimization analysis for %s: %s",
                            game_id,
                            error,
                        )
                self._app._emit_toast(toast_message, toast_level)
            self._app.optimizationAnalysisChanged.emit(game_id)

    def getGameOptimizationAnalysis(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        session = self._app._baseline_sessions.load(app_id) if app_id else None
        if session is not None and session.status in {
            "waiting_for_steam", "waiting_for_runner", "waiting_for_launcher", "recording",
            "waiting_for_game_exit", "processing"
        }:
            self._app._active_baseline_games.add(game_id)
            self._app._baseline_statuses.setdefault(game_id, session.status)
        if (
            session is not None
            and session.status == "processing"
            and game_id not in self._app._optimization_jobs
            and not isinstance(
                self._app._optimization_analyses.get(game_id), OptimizationAnalysis
            )
        ):
            log = self._app._baseline_sessions.newest_log(app_id)
            if log is not None:
                self.analyzeGameOptimization(game_id, str(log))
        if game_id in self._app._optimization_jobs:
            return {
                "success": True,
                "status": "running",
                "gameId": game_id,
                "baselineSession": session.to_dict() if session else {},
            }
        value = self._app._optimization_analyses.get(game_id)
        if value is None and game is not None and app_id:
            value = self._restore_persisted_analysis(game, app_id)
        elif (
            isinstance(value, OptimizationAnalysis)
            and not value.baseline_stale
            and game is not None
            and app_id
            and self._app._optimization_analysis_persistence_enabled
            and self._active_optimization_change(game) is None
            and self._cached_settings_changed(value.settings)
        ):
            self._app._optimization_analyses.pop(game_id, None)
            value = self._restore_persisted_analysis(game, app_id)
        if isinstance(value, OptimizationAnalysis):
            result = value.to_dict()
            result.update({"success": True, "status": "completed"})
            result["analysisCacheState"] = self._analysis_cache_states.get(
                game_id, "current"
            )
            result["baselineSession"] = session.to_dict() if session else {}
            before = self._app._baseline_sessions.load_measurement(
                app_id, slot="before"
            ) if app_id else None
            after = self._app._baseline_sessions.load_measurement(
                app_id, slot="after"
            ) if app_id else None
            comparison = self._app._optimization_comparisons.get(game_id)
            if comparison is None and before is not None and after is not None:
                comparison = compare_measurements(
                    before,
                    after,
                    before_frame_rate=self._app._frame_rate_analyzer.analyze(
                        before, value.fingerprint.system
                    ),
                    after_frame_rate=self._app._frame_rate_analyzer.analyze(
                        after, value.fingerprint.system
                    ),
                )
                self._app._optimization_comparisons[game_id] = comparison
            result["beforeMeasurement"] = before.to_dict() if before else {}
            result["afterMeasurement"] = after.to_dict() if after else {}
            result["comparison"] = comparison.to_dict() if comparison else {}
            active = self._active_optimization_change(game)
            result["appliedChange"] = dict(active or {})
            result["automaticOptimization"] = self._automatic_optimization_state(
                game, app_id, value
            )
            return result
        if isinstance(value, Mapping):
            result = dict(value)
            result["baselineSession"] = session.to_dict() if session else {}
            return result
        return {
            "success": True,
            "status": "not_analyzed",
            "gameId": game_id,
            "baselineSession": session.to_dict() if session else {},
        }

    def _automatic_optimization_state(
        self,
        game: Game,
        app_id: str,
        analysis: OptimizationAnalysis,
    ) -> dict[str, Any]:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        registry = getattr(self._app, "_runtime_candidate_registry", None)
        if repository is None or registry is None:
            return {}
        profile = self._app._optimization_profile_repository.load(app_id)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        document = repository.load(app_id)
        plan = registry.plan(
            analysis,
            profile,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            history=tuple(document.get("history", ())),
        ).to_dict()
        plan["session"] = dict(document.get("session") or {})
        plan["history"] = list(document.get("history") or ())
        plan["hasPendingChange"] = bool(self._active_optimization_change(game))
        return plan

    def _mark_automatic_comparison_started(
        self, app_id: str, measurement_session_id: str
    ) -> None:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        if repository is None or not measurement_session_id:
            return
        document = repository.load(app_id)
        session = document.get("session")
        if not isinstance(session, Mapping):
            return
        state = str(session.get("state") or "")
        result = session.get("result")
        retry_inconclusive = bool(
            state == "result_ready"
            and isinstance(result, Mapping)
            and str(result.get("outcome") or "") == "insufficient_data"
        )
        if state not in {"candidate_applied", "comparison_recording"} and not retry_inconclusive:
            return
        repository.mark_comparison_recording(
            app_id,
            str(session.get("id") or ""),
            measurement_session_id,
        )

    def _complete_automatic_experiment(
        self,
        game_id: str,
        app_id: str,
        measurement_session_id: str,
        analysis: OptimizationAnalysis,
    ) -> None:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        evaluator = getattr(self._app, "_automatic_optimization_evaluator", None)
        if repository is None or evaluator is None:
            return
        document = repository.load(app_id)
        session = document.get("session")
        if not isinstance(session, Mapping):
            return
        if (
            str(session.get("state") or "") != "comparison_recording"
            or str(session.get("comparisonSessionId") or "")
            != measurement_session_id
        ):
            return
        before_payload = session.get("originalBaseline")
        candidate = session.get("candidate")
        problem_payload = session.get("problem")
        if not all(
            isinstance(item, Mapping)
            for item in (before_payload, candidate, problem_payload)
        ):
            repository.fail(
                app_id,
                str(session.get("id") or ""),
                "The Automatic Optimization session is incomplete",
            )
            return
        before = PerformanceMeasurement.from_dict(before_payload)
        after = analysis.measurement
        if after is None:
            return
        try:
            report = json.loads(
                self._app._runner_report_path(app_id).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            report = {}
        verified, activation_reason = verify_runtime_activation(
            str(candidate.get("id") or ""),
            report if isinstance(report, Mapping) else {},
            measurement_session_id,
        )
        from ..services.automatic_optimization import OptimizationProblem

        problem = OptimizationProblem(
            str(problem_payload.get("kind") or "insufficient_data"),
            float(problem_payload.get("confidence") or 0.0),
            str(problem_payload.get("target") or ""),
            tuple(str(item) for item in problem_payload.get("evidence", ())),
            tuple(str(item) for item in problem_payload.get("limitations", ())),
        )
        result = evaluator.evaluate(
            problem,
            before,
            after,
            activation_verified=verified,
            activation_reason=activation_reason,
            before_frame_rate=self._app._frame_rate_analyzer.analyze(
                before, analysis.fingerprint.system
            ),
            after_frame_rate=analysis.frame_rate,
        )
        repository.mark_result(
            app_id,
            str(session.get("id") or ""),
            result,
            after,
        )

    def _finish_automatic_session_for_change(
        self, app_id: str, change_id: str, action: str
    ) -> None:
        repository = getattr(self._app, "_automatic_optimization_repository", None)
        if repository is None or not app_id:
            return
        document = repository.load(app_id)
        session = document.get("session")
        if not isinstance(session, Mapping):
            return
        if str(session.get("candidateChangeId") or "") != change_id:
            return
        repository.finish(
            app_id,
            str(session.get("id") or ""),
            action=action,
        )

    def _restore_persisted_analysis(
        self, game: Game, app_id: str
    ) -> OptimizationAnalysis | None:
        if not self._app._optimization_analysis_persistence_enabled:
            return None
        cached = self._app._optimization_analysis_repository.load(app_id)
        if cached is None or cached.fingerprint.game_id != game.id:
            return None
        reasons = list(cached.stale_reasons)
        root_valid = True
        try:
            current_root = game.install_path.resolve(strict=True)
            cached_root = Path(cached.fingerprint.game_root).resolve(strict=True)
            if current_root != cached_root:
                reasons.append("The game installation path changed after analysis")
                root_valid = False
        except OSError:
            reasons.append("The analyzed game path is no longer available")
            root_valid = False
        executable = Path(cached.fingerprint.main_executable)
        if cached.fingerprint.main_executable and not executable.is_file():
            reasons.append("The analyzed executable is no longer available")

        active_change = self._active_optimization_change(game)
        settings_changed = (
            active_change is None and self._cached_settings_changed(cached.settings)
        )
        restored = cached
        if settings_changed and root_valid:
            try:
                refreshed_settings, _unused = self._app._game_settings_advisor.analyze(
                    game,
                    cached.fingerprint,
                    None,
                    cached.bottleneck,
                    cached.frame_rate,
                )
                refreshed_settings = replace(
                    refreshed_settings,
                    message=(
                        "The graphics configuration changed after the saved baseline. "
                        "Settings were refreshed; record a new baseline before using "
                        "measured Automatic recommendations."
                    ),
                    recommendation_state="baseline_stale",
                )
                restored = replace(cached, settings=refreshed_settings, candidates=())
            except (OSError, ValueError) as error:
                logger.warning(
                    "Could not refresh stale settings analysis for %s: %s",
                    game.id,
                    error,
                )
            reasons.append("Graphics settings changed after the saved baseline")
        if reasons:
            restored = replace(
                restored,
                candidates=(),
                baseline_stale=True,
                stale_reasons=tuple(dict.fromkeys(reasons)),
            )
            cache_state = "stale"
        else:
            cache_state = "cached"
        self._app._optimization_analyses[game.id] = restored
        self._analysis_cache_states[game.id] = cache_state
        if restored != cached:
            try:
                self._app._optimization_analysis_repository.save(app_id, restored)
            except (OSError, ValueError) as error:
                logger.warning(
                    "Could not update persisted optimization analysis for %s: %s",
                    game.id,
                    error,
                )
        return restored

    @staticmethod
    def _cached_settings_changed(settings: Any) -> bool:
        expected: dict[str, str] = {}
        for item in settings.detected:
            if item.file and item.config_sha256:
                expected[item.file] = item.config_sha256
        for raw_path, digest in expected.items():
            path = Path(raw_path)
            try:
                if path.is_symlink() or not path.is_file():
                    return True
                current = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return True
            if current != digest:
                return True
        return False

    def startAutomaticOptimization(
        self, game_id: str, candidate_id: str = ""
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        analysis = self._app._optimization_analyses.get(game_id)
        if game is None or not app_id or not isinstance(analysis, OptimizationAnalysis):
            return {
                "success": False,
                "error": "Analyze the game and record a representative baseline first",
            }
        if analysis.baseline_stale or analysis.measurement is None or not analysis.measurement.available:
            return {
                "success": False,
                "error": "Automatic Optimization requires a current representative baseline",
            }
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Finish the current comparison cycle before starting Automatic Optimization",
            }
        measured = getattr(self._app, "_measured_optimization", None)
        if measured is not None:
            from ..services.measured_optimization import ACTIVE
            if measured.store.workflow(app_id).get("state") in ACTIVE:
                return {"success": False, "error": "A measured optimization is already in progress for this game"}
        profile = self._app._optimization_profile_repository.load(app_id)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        document = self._app._automatic_optimization_repository.load(app_id)
        plan = self._app._runtime_candidate_registry.plan(
            analysis,
            profile,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            history=tuple(document.get("history", ())),
        )
        selected = next(
            (
                item for item in plan.available
                if not candidate_id or str(item.get("id") or "") == candidate_id
            ),
            None,
        )
        if selected is None:
            return {"success": False, "error": plan.message}
        before = self._app._baseline_sessions.load_measurement(app_id, slot="before")
        if before is None or not before.available:
            return {
                "success": False,
                "error": "The original representative baseline is unavailable",
            }
        if before.to_dict() != analysis.measurement.to_dict():
            return {
                "success": False,
                "error": "The saved baseline changed after analysis; analyze the game again",
            }
        session: Mapping[str, Any] | None = None
        try:
            session = self._app._automatic_optimization_repository.create(
                app_id,
                game.id,
                plan,
                before,
                selected,
            )
            definition = self._app._runtime_candidate_registry.get(
                str(selected.get("id") or "")
            )
            candidate = definition.as_profile_candidate(profile, plan.problem)
            applied = self._apply_runtime_profile_candidate(
                game, app_id, candidate
            )
            if not applied.get("success"):
                raise RuntimeError(str(applied.get("error") or "Candidate apply failed"))
            change = applied.get("change") or {}
            session = self._app._automatic_optimization_repository.mark_applied(
                app_id,
                str(session.get("id") or ""),
                str(change.get("id") or ""),
            )
        except Exception as error:
            if session is not None:
                try:
                    self._app._automatic_optimization_repository.fail(
                        app_id,
                        str(session.get("id") or ""),
                        str(error) or type(error).__name__,
                    )
                except Exception:
                    pass
            return {"success": False, "error": str(error) or type(error).__name__}
        self._app.optimizationAnalysisChanged.emit(game_id)
        return {"success": True, "automaticSession": dict(session)}

    def _apply_runtime_profile_candidate(
        self,
        game: Game,
        app_id: str,
        candidate: OptimizationCandidate,
    ) -> dict[str, Any]:
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Finish the current comparison cycle before applying another change",
            }
        previous_profile = self._app._optimization_profile_repository.load(app_id)
        profile = previous_profile
        if candidate.id == "gamemode_runtime":
            profile = replace(profile, gamemode_enabled=True)
        elif candidate.id == "gamemode_cpu_schedule":
            profile = replace(profile, preset="custom", gamemode_enabled=True)
        elif candidate.id == "gamescope_gpu_scaling":
            width, height = candidate.proposed_value.split("x", 1)
            profile = replace(
                profile,
                preset="custom",
                gamescope_enabled=True,
                gamescope_mode="performance",
                gamescope_input_width=int(width),
                gamescope_input_height=int(height),
            )
        elif candidate.id == "quiet_fps_target":
            profile = replace(
                profile,
                preset="custom",
                target_fps_mode="manual",
                target_fps=int(candidate.proposed_value.split()[0]),
            )
        else:
            return {"success": False, "error": "Unsupported runtime recommendation"}
        path = self._app._optimization_profile_repository.save(profile)
        try:
            manifest = self._app._optimization_change_service.record_runtime_change(
                game, candidate, previous_profile, profile
            )
        except Exception:
            self._app._optimization_profile_repository.save(previous_profile)
            raise
        self._app._optimization_applied_changes[game.id] = dict(manifest)
        self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        self._app._optimization_comparisons.pop(game.id, None)
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {
            "success": True,
            "profilePath": str(path),
            "change": manifest,
        }

    def applyOptimizationCandidate(
        self, game_id: str, candidate_id: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        analysis = self._app._optimization_analyses.get(game_id)
        if game is None or not app_id or not isinstance(analysis, OptimizationAnalysis):
            return {"success": False, "error": "Analyze the game before applying a recommendation"}
        active = self._active_optimization_change(game)
        if active is not None:
            return {
                "success": False,
                "error": (
                    "Finish the current comparison cycle by keeping or reverting "
                    "the existing change"
                ),
            }
        candidate = next(
            (item for item in analysis.candidates if item.id == candidate_id),
            None,
        )
        if candidate is None:
            return {"success": False, "error": "Optimization candidate is no longer available"}
        try:
            if candidate.files_to_modify:
                return self._apply_file_candidate(game, app_id, candidate)
            return self._apply_runtime_profile_candidate(game, app_id, candidate)
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}

    def previewGameSettingChange(
        self, game_id: str, instance_id: str, proposed_value: str
    ) -> dict[str, Any]:
        analysis = self._app._optimization_analyses.get(game_id)
        if not isinstance(analysis, OptimizationAnalysis):
            return {"success": False, "error": "Analyze the game before previewing a setting test"}
        try:
            candidate = self._app._game_settings_advisor.manual_candidate(
                analysis.settings, instance_id, proposed_value
            )
            setting = next(
                item
                for item in analysis.settings.detected
                if item.instance_id == instance_id
            )
        except (ValueError, StopIteration) as error:
            return {"success": False, "error": str(error) or "Setting is unavailable"}
        result = candidate.to_dict()
        result.update({
            "success": True,
            "settingInstanceId": setting.instance_id,
            "automaticRecommended": setting.automatically_recommended,
            "automaticReason": setting.automatic_reason,
            "manualTestAvailable": True,
            "backupAvailable": True,
            "aggressive": proposed_value != setting.suggested_value,
        })
        return result

    def applyGameSettingChange(
        self, game_id: str, instance_id: str, proposed_value: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        app_id = self._profile_key(game)
        analysis = self._app._optimization_analyses.get(game_id)
        if game is None or not app_id or not isinstance(analysis, OptimizationAnalysis):
            return {"success": False, "error": "Analyze the game before applying a setting test"}
        try:
            candidate = self._app._game_settings_advisor.manual_candidate(
                analysis.settings, instance_id, proposed_value
            )
            return self._apply_file_candidate(game, app_id, candidate)
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}

    def _apply_file_candidate(
        self, game: Game, app_id: str, candidate: OptimizationCandidate
    ) -> dict[str, Any]:
        analysis = self._app._optimization_analyses.get(game.id)
        if isinstance(analysis, OptimizationAnalysis) and analysis.baseline_stale:
            return {
                "success": False,
                "error": "Record a new representative baseline after the graphics settings change",
            }
        before = self._app._baseline_sessions.load_measurement(app_id, slot="before")
        if before is None or not before.available:
            return {
                "success": False,
                "error": "Record a representative baseline before applying a setting test",
            }
        if self._active_optimization_change(game) is not None:
            return {
                "success": False,
                "error": "Finish the current comparison cycle before applying another change",
            }
        manifest = self._app._optimization_change_service.apply(game, candidate)
        self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        self._app._optimization_comparisons.pop(game.id, None)
        self._app._optimization_applied_changes[game.id] = dict(manifest)
        self._app.optimizationAnalysisChanged.emit(game.id)
        return {"success": True, "change": manifest}

    def _active_optimization_change(self, game: Game | None) -> dict[str, Any] | None:
        if game is None:
            return None
        cached = self._app._optimization_applied_changes.get(game.id)
        if isinstance(cached, Mapping) and cached.get("state") == "applied":
            return dict(cached)
        active = self._app._optimization_change_service.active_change(game)
        if active is not None:
            self._app._optimization_applied_changes[game.id] = dict(active)
        return active

    def revertOptimizationChange(
        self, game_id: str, change_id: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Game is unavailable"}
        active = self._active_optimization_change(game) or {}
        measured = getattr(self._app, "_measured_optimization", None)
        if active.get("kind") == "measured_experiment" and measured is not None:
            workflow = measured.store.workflow(self._profile_key(game))
            return (measured.revert(game_id) if workflow.get("state") == "result_ready"
                    else measured.cancel(game_id))
        try:
            if active.get("kind") == "runtime_profile":
                manifest = self._app._optimization_change_service.runtime_change(
                    game, change_id
                )
                previous = GameOptimizationProfile.from_dict(
                    manifest["before_profile"],
                    expected_app_id=self._profile_key(game),
                )
                current = self._app._optimization_profile_repository.load(
                    self._profile_key(game)
                )
                if current.to_dict() != manifest["after_profile"]:
                    raise RuntimeError(
                        "The optimization profile changed outside this recommendation"
                    )
                self._app._optimization_profile_repository.save(previous)
                try:
                    manifest = self._app._optimization_change_service.mark_runtime_reverted(
                        game, change_id
                    )
                except Exception:
                    self._app._optimization_profile_repository.save(current)
                    raise
            else:
                manifest = self._app._optimization_change_service.revert(
                    game, change_id
                )
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}
        self._finish_automatic_session_for_change(
            self._profile_key(game), change_id, "reverted"
        )
        self._app._optimization_applied_changes.pop(game_id, None)
        app_id = self._profile_key(game)
        if app_id:
            self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        self._app._optimization_comparisons.pop(game_id, None)
        self._app.optimizationAnalysisChanged.emit(game_id)
        self.analyzeGameOptimization(game_id)
        return {"success": True, "change": manifest}

    def keepOptimizationChange(
        self, game_id: str, change_id: str
    ) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {"success": False, "error": "Game is unavailable"}
        active = self._active_optimization_change(game) or {}
        if active.get("id") != change_id or active.get("state") != "applied":
            return {"success": False, "error": "No matching active change exists"}
        if active.get("kind") == "measured_experiment" and getattr(self._app, "_measured_optimization", None) is not None:
            return self._app._measured_optimization.keep(game_id, "keep_unevaluated")
        app_id = self._profile_key(game)
        automatic_repository = getattr(
            self._app, "_automatic_optimization_repository", None
        )
        if automatic_repository is not None and app_id:
            automatic_session = automatic_repository.load(app_id).get("session")
            if (
                isinstance(automatic_session, Mapping)
                and str(automatic_session.get("candidateChangeId") or "")
                == change_id
            ):
                if str(automatic_session.get("state") or "") != "result_ready":
                    return {
                        "success": False,
                        "error": (
                            "Record and evaluate a representative comparison "
                            "before keeping this optimization"
                        ),
                    }
                automatic_result = automatic_session.get("result")
                if (
                    not isinstance(automatic_result, Mapping)
                    or str(automatic_result.get("outcome") or "")
                    == "insufficient_data"
                ):
                    return {
                        "success": False,
                        "error": (
                            "The comparison is inconclusive; retry it or revert "
                            "the optimization"
                        ),
                    }
        try:
            before = self._app._baseline_sessions.load_measurement(
                app_id, slot="before"
            ) if app_id else None
            after = self._app._baseline_sessions.load_measurement(
                app_id, slot="after"
            ) if app_id else None
            comparison = self._app._optimization_comparisons.get(game_id)
            manifest = self._app._optimization_change_service.keep(
                game,
                change_id,
                comparison=comparison.to_dict() if comparison else None,
                before_measurement=before.to_dict() if before else None,
                after_measurement=after.to_dict() if after else None,
            )
            if app_id and after is not None and after.available:
                self._app._baseline_sessions.save_measurement(
                    app_id, after, slot="before"
                )
            if app_id:
                self._app._baseline_sessions.clear_measurement(app_id, slot="after")
        except Exception as error:
            return {"success": False, "error": str(error) or type(error).__name__}
        self._finish_automatic_session_for_change(app_id, change_id, "kept")
        self._app._optimization_applied_changes.pop(game_id, None)
        self._app._optimization_comparisons.pop(game_id, None)
        self._app.optimizationAnalysisChanged.emit(game_id)
        self.analyzeGameOptimization(game_id)
        return {"success": True, "change": manifest}

    def _select_mangohud_log(
        self, game: Game, app_id: str, explicit: str
    ) -> Path | None:
        if explicit:
            path = Path(explicit).expanduser()
            return path.resolve(strict=True)
        try:
            profile = self._app._mangohud_repository.load(app_id)
        except (OSError, ValueError):
            return None
        if not profile.output_folder:
            return None
        folder = Path(profile.output_folder)
        if not folder.is_dir():
            return None
        tokens = {
            re.sub(r"[^a-z0-9]", "", game.name.casefold()),
            re.sub(r"[^a-z0-9]", "", app_id.casefold()),
            re.sub(r"[^a-z0-9]", "", Path(game.executable_path).stem.casefold()),
        }
        tokens.discard("")
        candidates: list[Path] = []
        for path in folder.glob("*.csv"):
            folded = re.sub(r"[^a-z0-9]", "", path.name.casefold())
            if any(token in folded for token in tokens):
                candidates.append(path)
        return max(candidates, key=lambda item: item.stat().st_mtime, default=None)

    def _optimization_options(self, options: Mapping[str, Any]) -> OptimizationOptions:
        def option(*names: str, default: Any = None) -> Any:
            for name in names:
                if name in options:
                    return options[name]
            return default

        fps_value = option("fpsLimit", "fps_limit")
        fps_limit = int(fps_value) if fps_value not in (None, "", 0, "0") else None
        return OptimizationOptions(
            profile=self._app._coerce_enum(
                OptimizationProfile, str(option("profile", default="Balanced"))
            ),
            gamemode=self._app._coerce_bool(
                option("gamemode", "gameMode", default=True)
            ),
            gamescope=self._app._coerce_bool(option("gamescope", default=False)),
            mangohud=self._app._coerce_bool(
                option("mangohud", "mangoHud", default=False)
            ),
            fps_limit=fps_limit,
            adaptive_sync=self._app._coerce_bool(
                option("adaptiveSync", "adaptive_sync", default=True)
            ),
            cursor_grab=self._app._coerce_bool(
                option("cursorGrab", "cursor_grab", default=False)
            ),
            cpu_performance_profile=self._app._coerce_bool(
                option("cpuPerformanceProfile", "cpu_performance_profile", default=False)
            ),
            memory_monitoring=self._app._coerce_bool(
                option("memoryMonitoring", "memory_monitoring", default=True)
            ),
            optiscaler=self._app._coerce_bool(
                option("optiscaler", "optiScaler", default=False)
            ),
        )

    def _optimization_displays(self) -> list[Any]:
        # instance() returns any QCoreApplication subclass, so check capability
        # rather than None.  from_application only calls instance methods
        # (screens/primaryScreen), so a non-GUI application raises a catchable
        # AttributeError rather than segfaulting -- but the guard keeps this
        # consistent with the other application lookups.
        application = QGuiApplication.instance()
        if not isinstance(application, QGuiApplication):
            return []
        try:
            return list(self._app._display_detector.from_application(application))
        except Exception as error:
            logger.warning("Could not detect displays: %s", error)
            return []

    def _optimization_display_for(self, display_id: str) -> Any | None:
        displays = self._app._optimization_displays()
        return next(
            (display for display in displays if display.display_id == display_id),
            next(
                (display for display in displays if display.primary),
                displays[0] if displays else None,
            ),
        )

    def _optimization_profile_from_payload(
        self, app_id: str, values: Mapping[str, Any]
    ) -> GameOptimizationProfile:
        base = self._app._optimization_profile_repository.load(app_id)
        aliases = {
            "gameCategory": "game_category", "userGoal": "user_goal",
            "targetDisplayId": "target_display_id", "targetFpsMode": "target_fps_mode",
            "targetFps": "target_fps", "gamemodeEnabled": "gamemode_enabled",
            "gamescopeEnabled": "gamescope_enabled", "gamescopeMode": "gamescope_mode",
            "gamescopeInputWidth": "gamescope_input_width",
            "gamescopeInputHeight": "gamescope_input_height",
            "gamescopeOutputWidth": "gamescope_output_width",
            "gamescopeOutputHeight": "gamescope_output_height",
            "gamescopeRefreshRate": "gamescope_refresh_rate",
            "gamescopeFullscreen": "gamescope_fullscreen",
            "gamescopeScaler": "gamescope_scaler", "gamescopeFilter": "gamescope_filter",
            "gamescopeWindowMode": "gamescope_window_mode",
            "gamescopeSharpness": "gamescope_sharpness",
            "gamescopeAdaptiveSync": "gamescope_adaptive_sync",
            "gamescopeGrabKeyboard": "gamescope_grab_keyboard",
            "gamescopeHdr": "gamescope_hdr",
            **_GAMESCOPE_DESKTOP_ALIASES,
            "manualOverrides": "manual_overrides", "lastRecommendation": "last_recommendation",
        }
        data = base.to_dict()
        runtime_keys = {
            "targetFpsMode", "target_fps_mode", "targetFps", "target_fps",
            "gamemodeEnabled", "gamemode_enabled", "gamescopeEnabled",
            "gamescope_enabled", "gamescopeMode", "gamescope_mode",
            "gamescopeInputWidth", "gamescope_input_width",
            "gamescopeInputHeight", "gamescope_input_height",
            "gamescopeOutputWidth", "gamescope_output_width",
            "gamescopeOutputHeight", "gamescope_output_height",
            "gamescopeRefreshRate", "gamescope_refresh_rate",
            "gamescopeFullscreen", "gamescope_fullscreen",
            "gamescopeScaler", "gamescope_scaler",
            "gamescopeFilter", "gamescope_filter",
            "gamescopeWindowMode", "gamescope_window_mode",
            "gamescopeSharpness", "gamescope_sharpness",
            "gamescopeAdaptiveSync", "gamescope_adaptive_sync",
            "gamescopeGrabKeyboard", "gamescope_grab_keyboard",
            "gamescopeHdr", "gamescope_hdr",
            "gamescopeSharpnessPercent",
            *_GAMESCOPE_DESKTOP_ALIASES, *GAMESCOPE_DESKTOP_FIELDS,
        }
        if "preset" not in values and runtime_keys.intersection(values):
            data["preset"] = "custom"
        for key, value in values.items():
            normalized = aliases.get(str(key), str(key))
            if normalized in data and normalized not in {"schema_version", "app_id", "updated_at"}:
                data[normalized] = value
        # The desktop slider shows 0 % (least) .. 100 % (most); Gamescope's
        # CLI is inverted (0 sharpest .. 20 softest).
        if "gamescopeSharpnessPercent" in values:
            data["gamescope_sharpness"] = sharpness_percent_to_cli(
                values["gamescopeSharpnessPercent"]
            )
        # Callers that only know the legacy fullscreen boolean (Desktop Mode)
        # keep their meaning: True -> fullscreen, False -> borderless.
        if not {"gamescopeWindowMode", "gamescope_window_mode"}.intersection(values):
            for key in ("gamescopeFullscreen", "gamescope_fullscreen"):
                if key in values:
                    data["gamescope_window_mode"] = (
                        "fullscreen" if self._app._coerce_bool(values[key]) else "borderless"
                    )
        # QML numbers arrive as float (e.g. 10.0); only whole, finite numbers
        # become the canonical integers. Everything else is rejected clearly.
        for name in self._INTEGER_PROFILE_FIELDS:
            data[name] = self._whole_number(data[name], name)
        data.update(
            {
                "schema_version": base.schema_version,
                "app_id": app_id,
                "updated_at": datetime.now(UTC),
            }
        )
        profile = GameOptimizationProfile.from_dict(data, expected_app_id=app_id)
        display = self._app._optimization_display_for(profile.target_display_id)
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        return self._app._optimization_advisor.resolve_preset(
            profile,
            display,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            measurements=self._saved_session_performance(app_id),
            system_info=self._app._system_info,
            accepted_results=self._accepted_results(app_id, profile.preset),
        ).profile

    def _accepted_results(self, app_id: str, preset: str) -> dict[str, Any]:
        """Kept, verified local results for the goal of this preset (never Custom)."""
        measured = getattr(self._app, "_measured_optimization", None)
        if measured is None or preset == "custom":
            return {}
        from ..services.measured_optimization import GOALS
        goal = preset if preset in GOALS else ""
        try:
            if preset == "automatic":
                # The goal of the newest result the owner kept for this game.
                history = measured.store.load(app_id)["history"]
                goal = next((str(item.get("goal")) for item in reversed(history)
                             if item.get("userAction") == "kept" and item.get("goal") in GOALS), "")
            return measured.accepted_for(app_id, goal) if goal else {}
        except Exception as error:  # noqa: BLE001 - evidence is optional
            logger.warning("Could not read measured results for %s: %s", app_id, error)
            return {}

    def _saved_session_performance(
        self, app_id: str
    ) -> SessionPerformanceData | None:
        if self._app._optimization_analysis_persistence_enabled:
            saved_analysis = self._app._optimization_analysis_repository.load(app_id)
            if saved_analysis is not None and saved_analysis.baseline_stale:
                return None
        measurement = self._app._baseline_sessions.load_measurement(
            app_id, slot="before"
        )
        if measurement is None or not measurement.available:
            return None
        return SessionPerformanceData(
            average_fps=measurement.average_fps,
            one_percent_low_fps=measurement.one_percent_low_fps,
            frametime_ms=measurement.average_frametime_ms,
            gpu_usage_percent=measurement.gpu_usage_percent,
            cpu_usage_percent=measurement.cpu_usage_percent,
            vram_used_mb=measurement.vram_used_mb,
        )

    def _optimization_profile_to_qml(
        self, profile: GameOptimizationProfile, custom_launch: Any = None
    ) -> dict[str, Any]:
        displays = self._app._optimization_displays()
        display = self._app._optimization_display_for(profile.target_display_id)
        measurements = self._saved_session_performance(profile.app_id)
        recommendation = self._app._optimization_advisor.recommend(
            profile,
            display,
            measurements,
            system_info=self._app._system_info,
        )
        gamemode, gamescope = self._app._runtime_tool_detector.detect()
        preset_plan = self._app._optimization_advisor.resolve_preset(
            profile,
            display,
            gamemode_available=gamemode.available,
            gamescope_available=gamescope.available,
            measurements=measurements,
            system_info=self._app._system_info,
            accepted_results=self._accepted_results(profile.app_id, profile.preset),
        )
        mangohud_activation_owner = "none"
        mangohud_config_path = ""
        try:
            mangohud_profile = self._app._mangohud_repository.load(profile.app_id)
            mangohud_fps_limit = mangohud_profile.fps_limit
            if mangohud_profile.enabled:
                mangohud_config_path = str(
                    self._app._mangohud_repository.config_path(profile.app_id)
                )
            if mangohud_profile.enabled:
                profile_game = next(
                    (
                        item
                        for item in self._app._domain_games.values()
                        if str(item.steam_app_id or item.id) == profile.app_id
                    ),
                    None,
                )
                if profile_game is not None:
                    mangohud_activation_owner = (
                        "steam_environment"
                        if profile_game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}
                        else self._app._mangohud_launch_integration.status(
                            profile_game, mangohud_profile
                        ).strategy
                    )
                else:
                    mangohud_activation_owner = "steam_environment"
        except (OSError, ValueError):
            mangohud_fps_limit = None
        try:
            optiscaler_profile = self._app._optiscaler_service.profile_repository.load(
                profile.app_id
            )
            optiscaler_override = (
                optiscaler_profile.proton_override
                if optiscaler_profile.enabled
                and optiscaler_profile.installation_state == "installed"
                else ""
            )
        except (OSError, ValueError, OptiScalerError):
            optiscaler_override = ""
        proton_tweaks_error = ""
        try:
            proton_environment = self._app._proton_tweaks_repository.environment(profile.app_id)
        except (OSError, ValueError, ProtonTweaksError) as error:
            proton_environment = {}
            proton_tweaks_error = str(error)
        fg_resolved = frame_generation_resolve(profile.app_id,
            self._app._optiscaler_service.profile_repository.root, inherited=os.environ,
            gamescope=profile.gamescope_enabled and profile.gamescope_mode != "disabled")
        plan = self._app._optimization_launch_planner.build(
            profile, ["%command%"], gamemode=gamemode, gamescope=gamescope,
            mangohud_fps_limit=mangohud_fps_limit,
            optiscaler_override=optiscaler_override,
            post_processing_environment=self._post_environment(profile.app_id),
            frame_generation_environment=fg_resolved.environment,
            frame_generation_prefix=fg_resolved.prefix,
            frame_generation_report=fg_resolved.report(),
            existing_wine_overrides=os.environ.get("WINEDLLOVERRIDES", ""),
            proton_environment=proton_environment,
            custom_launch=custom_launch if custom_launch is not None else self._custom_launch(profile.app_id),
            existing_environment=os.environ,
            mangohud_activation_owner=mangohud_activation_owner,
            allow_placeholder=True,
            mangohud_config_path=mangohud_config_path,
            # Same plan as game-optimization-run builds for the Steam launch.
            gamemode_preload_client=True,
        )
        runner = self._app._runner_integration.status()
        data = profile.to_dict()
        data.update({
            "success": True, "schemaVersion": profile.schema_version,
            "appId": profile.app_id, "gameCategory": profile.game_category,
            "userGoal": profile.user_goal, "targetDisplayId": profile.target_display_id,
            "targetFpsMode": profile.target_fps_mode, "targetFps": profile.target_fps,
            "gamemodeEnabled": profile.gamemode_enabled,
            "gamescopeEnabled": profile.gamescope_enabled,
            "gamescopeMode": profile.gamescope_mode,
            "gamescopeInputWidth": profile.gamescope_input_width,
            "gamescopeInputHeight": profile.gamescope_input_height,
            "gamescopeOutputWidth": profile.gamescope_output_width,
            "gamescopeOutputHeight": profile.gamescope_output_height,
            "gamescopeRefreshRate": profile.gamescope_refresh_rate,
            "gamescopeFullscreen": profile.gamescope_fullscreen,
            "gamescopeScaler": profile.gamescope_scaler,
            "gamescopeFilter": profile.gamescope_filter,
            "gamescopeWindowMode": profile.gamescope_window_mode,
            "gamescopeSharpness": profile.gamescope_sharpness,
            "gamescopeAdaptiveSync": profile.gamescope_adaptive_sync,
            "gamescopeGrabKeyboard": profile.gamescope_grab_keyboard,
            "gamescopeHdr": profile.gamescope_hdr,
            **{_camel(name): getattr(profile, name) for name in GAMESCOPE_DESKTOP_FIELDS},
            "gamescopeSharpnessPercent": sharpness_cli_to_percent(profile.gamescope_sharpness),
            # Display-only: the launcher always uses the argv list of the plan.
            "gamescopeArguments": [_display_path(value) for value in plan.gamescope_wrapper[1:-1]],
            "gamescopeSteamLaunchOptions": (
                _display_path(shlex.join(["gamescope", *plan.gamescope_wrapper[1:-1]])) + " -- %command%"
                if plan.gamescope_wrapper else ""
            ),
            "gamescopeHdrReadiness": self._gamescope_hdr_readiness(gamescope),
            "gamescopeTestRunning": self._app._gamescope_test_run.running,
            "manualOverrides": dict(profile.manual_overrides),
            "lastRecommendation": dict(profile.last_recommendation),
            "updatedAt": profile.updated_at.astimezone(UTC).isoformat(),
            "displays": [item.to_dict() for item in displays],
            "recommendation": recommendation.to_dict(),
            "presetPlan": preset_plan.to_dict(),
            "categoryClassification": {
                "value": profile.game_category,
                "source": (
                    "manual override"
                    if profile.manual_overrides.get("category")
                    else "saved profile"
                    if profile.game_category != "unknown"
                    else "not detected"
                ),
                "confidence": (
                    1.0
                    if profile.manual_overrides.get("category")
                    else 0.75
                    if profile.game_category != "unknown"
                    else 0.0
                ),
                "manualOverride": bool(profile.manual_overrides.get("category")),
            },
            "gamemode": gamemode.to_dict(), "gamescope": gamescope.to_dict(),
            "launchPlan": plan.to_dict(),
            "planSource": self._plan_source(profile.app_id, plan),
            "launchPlanText": shlex.join(plan.diagnostic_command),
            "fpsLimitOwner": plan.fps_limit_owner,
            "steamLaunchCommand": (
                self._app._runner_integration.steam_command(profile.app_id)
                if profile.app_id.isdecimal()
                else ""
            ),
            "localGame": not profile.app_id.isdecimal(),
            "launcherGame": profile.app_id.startswith(("heroic-", "lutris-")),
            "runner": runner.to_dict(),
            "profilePath": str(self._app._optimization_profile_repository.path(profile.app_id)),
            "protonTweaksError": proton_tweaks_error,
            "renderingSummary": (
                "Gamescope chooses the render and output size automatically"
                if profile.gamescope_input_auto and profile.gamescope_output_auto
                else f"The game will render at {profile.gamescope_input_width}×{profile.gamescope_input_height} "
                f"and display at {profile.gamescope_output_width}×{profile.gamescope_output_height}"
            ),
            "protonOverrides": [
                f"{key}={value}"
                for key, value in sorted(plan.environment.items())
            ],
        })
        return data

    def _provider_launch_preview(
        self, game: Game, options: OptimizationOptions
    ) -> str:
        for method_name in (
            "preview_command",
            "generate_command_preview",
            "build_launch_preview",
            "generate_launch_preview",
            "build_launch_command",
            "generate_launch_command",
        ):
            method = getattr(self._app._optimization_provider, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(game, options)
            except TypeError:
                result = method(options)
            if isinstance(result, str):
                return result
            for attribute in ("preview", "command", "command_preview"):
                value = getattr(result, attribute, None)
                if isinstance(value, str):
                    return value
        raise AttributeError("optimization provider cannot build a launch preview")
