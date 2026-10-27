"""Argv-safe launcher-native starts for Heroic and Lutris games."""

from __future__ import annotations

import logging

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from game_optimization_linux.models import Game, Launcher, ManualGameConfig

from .steam_launch import _sandbox_runtime_environment_keys


class LauncherNativeError(RuntimeError):
    pass


class ManualLaunchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ManualLaunchResult:
    game_id: str
    game_exit_code: int | None
    pre_launch_exit_code: int | None = None
    post_launch_exit_code: int | None = None
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error and self.game_exit_code == 0


@dataclass(frozen=True, slots=True)
class ManualLaunchPlan:
    game_id: str
    game_command: tuple[str, ...]
    pre_launch_command: tuple[str, ...]
    post_launch_command: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...]
    custom_pre: tuple[Any, ...] = ()
    custom_post: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()
    tree_unit: str = ""
    # Only for the game command (never for pre/post commands): GameOpti runtime
    # variables such as private MangoHud measurement settings, and variables
    # removed from the outer wrapper process (Gamescope restores them itself).
    game_environment: tuple[tuple[str, str], ...] = ()
    game_unset: tuple[str, ...] = ()


def build_launcher_native_command(
    game: Game,
    *,
    which: Callable[[str], str | None] = shutil.which,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Build an official launcher invocation without executing game binaries."""

    env = os.environ if environment is None else environment
    uri = game.launch_uri.strip()
    if game.launcher is Launcher.HEROIC:
        if not uri.startswith("heroic://launch?"):
            raise LauncherNativeError("Heroic launch metadata is incomplete")
        if game.launcher_variant == "flatpak":
            inner = [
                "flatpak",
                "run",
                "com.heroicgameslauncher.hgl",
                "--no-gui",
                "--no-sandbox",
                uri,
            ]
        else:
            executable = "heroic" if env.get("FLATPAK_ID") else which("heroic")
            if not executable:
                raise LauncherNativeError("Heroic executable was not found")
            inner = [executable, "--no-gui", "--no-sandbox", uri]
    elif game.launcher is Launcher.LUTRIS:
        if not uri.startswith("lutris:rungameid/"):
            raise LauncherNativeError("Lutris launch metadata is incomplete")
        if game.launcher_variant == "flatpak":
            inner = ["flatpak", "run", "net.lutris.Lutris", uri]
        else:
            executable = "lutris" if env.get("FLATPAK_ID") else which("lutris")
            if not executable:
                raise LauncherNativeError("Lutris executable was not found")
            inner = [executable, uri]
    else:
        raise LauncherNativeError("The selected game has no launcher-native start method")

    if not env.get("FLATPAK_ID"):
        if inner[0] == "flatpak":
            executable = which("flatpak")
            if not executable:
                raise LauncherNativeError("Flatpak executable was not found")
            inner[0] = executable
        return inner

    spawn = which("flatpak-spawn")
    if not spawn:
        raise LauncherNativeError("flatpak-spawn is unavailable in the sandbox")
    sandbox_unsets = [
        f"--unset-env={key}" for key in _sandbox_runtime_environment_keys(env)
    ]
    return [spawn, "--host", *sandbox_unsets, *inner]


class LauncherNativeLauncher:
    """Start Heroic/Lutris and let that launcher preserve its configured runtime."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._which = which
        self._popen = popen
        self._environment = os.environ if environment is None else environment

    def build_command(self, game: Game) -> list[str]:
        return build_launcher_native_command(
            game, which=self._which, environment=self._environment
        )

    def launch(self, game: Game) -> Sequence[str]:
        if not game.launch_available:
            raise LauncherNativeError(
                game.launch_unavailable_reason or "Launcher metadata is incomplete"
            )
        command = self.build_command(game)
        try:
            self._popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as error:
            raise LauncherNativeError(f"Could not start {game.launcher.value}: {error}") from error
        return command


class ManualGameLauncher:
    """Validate and monitor one custom argv lifecycle away from the GUI thread."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        run: Callable[..., Any] = subprocess.run,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._which = which
        self._popen = popen
        self._run = run
        self._environment = dict(os.environ if environment is None else environment)

    def prepare(self, configuration: ManualGameConfig) -> ManualLaunchPlan:
        executable = configuration.executable
        if not configuration.install_directory.is_dir():
            raise ManualLaunchError("The configured game directory is missing or inaccessible")
        if not executable.is_file():
            raise ManualLaunchError("The configured game executable is missing or inaccessible")
        cwd = configuration.working_directory or configuration.install_directory
        if not cwd.is_dir():
            raise ManualLaunchError("The configured working directory is missing or inaccessible")
        if configuration.wine_prefix is not None and not configuration.wine_prefix.is_dir():
            raise ManualLaunchError("The configured Wine prefix is missing or inaccessible")

        runner = list(configuration.runner_command)
        if runner:
            self._validate_program(runner[0], "Wine/Proton executable")
        elif not os.access(executable, os.X_OK):
            raise ManualLaunchError("The configured native executable is not executable")
        game_command = (*runner, str(executable), *configuration.arguments)
        environment = dict(configuration.environment)
        if configuration.wine_prefix is not None:
            environment["WINEPREFIX"] = str(configuration.wine_prefix)
        return ManualLaunchPlan(
            game_id=configuration.id,
            game_command=tuple(game_command),
            pre_launch_command=configuration.pre_launch,
            post_launch_command=configuration.post_launch,
            cwd=cwd,
            environment=tuple(sorted(environment.items())),
        )

    def tree_tracking(self) -> tuple[str, list[str]]:
        """A host systemd user scope that outlives the main process until every child ends.

        systemd >= 254 expands $NAME in the command line; where the option
        exists it is turned off so every argument stays literal.
        """
        from uuid import uuid4
        unit = f"app-gameopti-launch-{uuid4().hex[:12]}.scope"
        probe = ["systemd-run", "--help"]
        if self._environment.get("FLATPAK_ID"):
            spawn = self._which("flatpak-spawn")
            probe = [spawn, "--host", *probe] if spawn else []
        literal: list[str] = []
        try:
            if probe and "--expand-environment" in str(self._run(probe, capture_output=True, text=True, timeout=10,
                                                                 check=False, shell=False).stdout or ""):
                literal = ["--expand-environment=no"]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return unit, ["systemd-run", "--user", "--scope", "--collect", "--quiet", f"--unit={unit}", *literal, "--"]

    def _wait_for_tree(self, plan: ManualLaunchPlan, environment: Mapping[str, str]) -> None:
        import time
        while True:
            try:
                state = self._run(self._host_command(["systemctl", "--user", "is-active", "--quiet", plan.tree_unit], plan),
                                  env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, check=False, shell=False, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                return
            if int(state.returncode) != 0:
                return
            time.sleep(1)

    def apply_custom(self, plan: ManualLaunchPlan, profile: Any, *, tree_prefix: Sequence[str] = (), tree_unit: str = "") -> ManualLaunchPlan:
        """Add the shared per-game custom launch profile on top of the manual configuration.

        A key the manual configuration already sets is not overridden. The wrapper
        is placed before the configured Wine/Proton runner; arguments follow the
        configured ones. ``tree_prefix`` keeps the host process tree tracked so
        after-exit commands start only when every game process has ended.
        """
        if profile is None or profile.empty:
            return plan
        from dataclasses import replace as _replace
        from .custom_launch import MANAGED_WRAPPERS
        environment = dict(plan.environment)
        warnings = list(plan.warnings)
        for key, value in profile.active_environment().items():
            if key in environment:
                warnings.append(f"{key} is already set by the manual game configuration; the custom value is not applied")
            else:
                environment[key] = value
        wrapper = list(profile.active_wrapper())
        if wrapper and Path(wrapper[0]).name in MANAGED_WRAPPERS:
            warnings.append(f"{MANAGED_WRAPPERS[Path(wrapper[0]).name]} cannot be added as a custom wrapper here")
            wrapper = []
        if wrapper:
            self._validate_program(wrapper[0], "custom wrapper")
        for entry in (*profile.active_pre(), *profile.active_post()):
            self._validate_program(entry.argv[0], "custom command")
        post = profile.active_post()
        track = bool(tree_prefix) and bool(post or plan.post_launch_command)
        return _replace(plan, environment=tuple(sorted(environment.items())),
                        game_command=(*(tree_prefix if track else ()), *wrapper, *plan.game_command, *profile.active_arguments()),
                        custom_pre=profile.active_pre(), custom_post=post, warnings=tuple(warnings),
                        tree_unit=tree_unit if track else "")

    def _hook(self, entry: Any, plan: ManualLaunchPlan, environment: Mapping[str, str]) -> int:
        """Run one custom command on the host, directly; timeout enforced by coreutils timeout."""
        argv = ["timeout", "-k", "5", str(int(entry.timeout)), *entry.argv]
        try:
            completed = self._run(
                self._host_command(argv, plan),
                cwd=None if self._environment.get("FLATPAK_ID") else str(plan.cwd),
                env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, shell=False, timeout=int(entry.timeout) + 15,
            )
            return int(completed.returncode)
        except subprocess.TimeoutExpired:
            return 124
        except OSError:
            return 127

    def _validate_program(self, program: str, label: str) -> None:
        candidate = Path(program)
        if candidate.is_absolute():
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                raise ManualLaunchError(f"The configured {label} is missing or not executable")
        elif not self._which(program):
            raise ManualLaunchError(f"The configured {label} was not found in PATH")

    def _host_command(self, argv: Sequence[str], plan: ManualLaunchPlan, *, game: bool = False) -> list[str]:
        if not self._environment.get("FLATPAK_ID"):
            return list(argv)
        spawn = self._which("flatpak-spawn")
        if not spawn:
            raise ManualLaunchError("flatpak-spawn is unavailable in the sandbox")
        unset = [
            f"--unset-env={key}"
            for key in _sandbox_runtime_environment_keys(self._environment)
        ]
        explicit = [f"--env={key}={value}" for key, value in plan.environment]
        if game:
            unset.extend(f"--unset-env={key}" for key in plan.game_unset)
            explicit = [item for item in explicit if item.split("=", 1)[0][6:] not in plan.game_unset]
            explicit.extend(f"--env={key}={value}" for key, value in plan.game_environment)
        return [
            spawn,
            "--host",
            f"--directory={plan.cwd}",
            *unset,
            *explicit,
            *argv,
        ]

    def _direct_environment(self, plan: ManualLaunchPlan) -> dict[str, str]:
        environment = dict(self._environment)
        environment.update(dict(plan.environment))
        return environment

    @staticmethod
    def game_process_environment(plan: ManualLaunchPlan, environment: Mapping[str, str]) -> dict[str, str]:
        result = dict(environment)
        result.update(dict(plan.game_environment))
        for key in plan.game_unset:
            result.pop(key, None)
        return result

    def execute(self, plan: ManualLaunchPlan) -> ManualLaunchResult:
        environment = self._direct_environment(plan)
        if plan.pre_launch_command:
            try:
                completed = self._run(
                    self._host_command(plan.pre_launch_command, plan),
                    cwd=None if self._environment.get("FLATPAK_ID") else str(plan.cwd),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except OSError as error:
                return ManualLaunchResult(
                    plan.game_id, None, error=f"Pre-launch command could not start: {error}"
                )
            pre_code = int(completed.returncode)
            if pre_code != 0:
                return ManualLaunchResult(
                    plan.game_id,
                    None,
                    pre_launch_exit_code=pre_code,
                    error=f"Pre-launch command failed with status {pre_code}",
                )
        else:
            pre_code = None

        for index, entry in enumerate(plan.custom_pre, 1):
            status = self._hook(entry, plan, environment)
            if status != 0 and entry.on_failure == "continue":
                logging.getLogger(__name__).warning("Before-launch command %d of %s failed with status %d; continuing",
                                                    index, plan.game_id, status)
            if status != 0 and entry.on_failure != "continue":
                for post in plan.custom_post:
                    if post.run_if_not_started:
                        self._hook(post, plan, environment)
                return ManualLaunchResult(plan.game_id, None, pre_launch_exit_code=status,
                                          error=f"Before-launch command {index} failed with status {status}; the game was not started")

        try:
            process = self._popen(
                self._host_command(plan.game_command, plan, game=True),
                cwd=None if self._environment.get("FLATPAK_ID") else str(plan.cwd),
                env=self.game_process_environment(plan, environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            game_code = int(process.wait())
            if plan.tree_unit:
                self._wait_for_tree(plan, environment)
        except OSError as error:
            for post in plan.custom_post:
                if post.run_if_not_started:
                    self._hook(post, plan, environment)
            return ManualLaunchResult(
                plan.game_id,
                None,
                pre_launch_exit_code=pre_code,
                error=f"Game executable could not start: {error}",
            )

        post_code: int | None = None
        post_error = ""
        if plan.post_launch_command:
            try:
                completed = self._run(
                    self._host_command(plan.post_launch_command, plan),
                    cwd=None if self._environment.get("FLATPAK_ID") else str(plan.cwd),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                post_code = int(completed.returncode)
                if post_code != 0:
                    post_error = f"Post-launch command failed with status {post_code}"
            except OSError as error:
                post_error = f"Post-launch command could not start: {error}"
        for index, entry in enumerate(plan.custom_post, 1):
            status = self._hook(entry, plan, environment)
            if status != 0 and not post_error:
                post_code = status
                post_error = f"After-exit command {index} failed with status {status}"
        error = post_error or (
            f"Game exited with status {game_code}" if game_code != 0 else ""
        )
        return ManualLaunchResult(
            plan.game_id,
            game_code,
            pre_launch_exit_code=pre_code,
            post_launch_exit_code=post_code,
            error=error,
        )

    def launch(self, configuration: ManualGameConfig) -> ManualLaunchResult:
        plan = self.prepare(configuration)
        # Values are intentionally excluded: launch diagnostics may list only
        # environment names, never user-provided secrets.
        return self.execute(plan)


__all__ = [
    "LauncherNativeError",
    "LauncherNativeLauncher",
    "ManualGameLauncher",
    "ManualLaunchError",
    "ManualLaunchPlan",
    "ManualLaunchResult",
    "build_launcher_native_command",
]
