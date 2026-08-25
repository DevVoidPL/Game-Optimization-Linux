"""Standalone Steam command wrapper for saved Game Optimization profiles."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time
from uuid import uuid4

from .config import STATE_DIR
from .models import validate_game_key
from .services.optimization_profiles import GameOptimizationProfileRepository
from .services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolDetector
from .services.mangohud import MangoHudProfileRepository
from .services.optiscaler import OptiScalerError, OptiScalerProfileRepository
from .services.proton_tweaks import ProtonTweaksError, ProtonTweaksRepository
from .services.host_service import HostServiceClient
from .services.host_bootstrap import host_home_directory
from .services.performance_session import BaselineSessionRepository


_STEAM_ENV_FILE = "GAME_OPTIMIZATION_STEAM_ENV_FILE"
_STEAM_ENV_DIRECTORY = Path(".local/share/game-optimization-linux/run-env")
_MAX_STEAM_ENV_BYTES = 1024 * 1024
_HOST_LAUNCH_PREFIX = "launch."
_HOST_LAUNCH_PROTOCOL_FILES = frozenset(
    {"steam.env", "launcher", "ready", "owner", "started", "completed"}
)
_HOST_LAUNCH_START_TIMEOUT_SECONDS = 60.0
_HOST_LAUNCH_HEARTBEAT_SECONDS = 5.0


def _wait_for_baseline_process(
    process: subprocess.Popen[bytes],
    sessions: BaselineSessionRepository,
    app_id: str,
    session_id: str,
    runner_token: str,
) -> int:
    while True:
        try:
            return int(process.wait(timeout=5))
        except subprocess.TimeoutExpired:
            sessions.heartbeat(app_id, session_id, runner_token)


def _wait_for_host_launch(
    directory: Path,
    sessions: BaselineSessionRepository,
    app_id: str,
    baseline_session: object | None,
    report: dict[str, object],
    report_root: Path,
    *,
    command_name: str,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    deadline = clock() + _HOST_LAUNCH_START_TIMEOUT_SECONDS
    spawned_pid: int | None = None
    while spawned_pid is None:
        spawned_pid = _read_protocol_integer(directory, "started")
        if spawned_pid is not None:
            break
        if clock() >= deadline:
            raise OSError("the host runner did not report process start")
        sleeper(0.1)

    if baseline_session is not None:
        sessions.mark_process_started(
            app_id,
            str(getattr(baseline_session, "id")),
            str(getattr(baseline_session, "runner_token")),
            spawned_pid=spawned_pid,
            process_group=None,
            command_name=command_name,
        )
        report.update(
            {
                "baselineSpawnedPid": spawned_pid,
                "baselineProcessGroup": None,
                "baselineObservedProcesses": [
                    f"pid={spawned_pid} command={command_name} state=running"
                ],
            }
        )
        _write_report(app_id, report, report_root)
        print(
            "game-optimization-run: baseline lifecycle "
            f"session={getattr(baseline_session, 'id')} appId={app_id} "
            f"runnerPid={os.getpid()} spawnedPid={spawned_pid} "
            "processGroup=host-inherited state=recording",
            file=sys.stderr,
        )

    next_heartbeat = clock() + _HOST_LAUNCH_HEARTBEAT_SECONDS
    while True:
        exit_code = _read_protocol_integer(directory, "completed")
        if exit_code is not None:
            return exit_code
        now = clock()
        if baseline_session is not None and now >= next_heartbeat:
            sessions.heartbeat(
                app_id,
                str(getattr(baseline_session, "id")),
                str(getattr(baseline_session, "runner_token")),
            )
            next_heartbeat = now + _HOST_LAUNCH_HEARTBEAT_SECONDS
        sleeper(0.2)


def _write_report(app_id: str, payload: dict[str, object], root: Path = STATE_DIR / "launch-reports") -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{app_id}.json"
    temporary = root / f".{app_id}.{os.getpid()}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _arguments(argv: Sequence[str]) -> tuple[str, bool, Path | None, list[str]]:
    values = list(argv)
    try:
        separator = values.index("--")
    except ValueError as error:
        raise ValueError("missing -- before the Steam game command") from error
    parser = argparse.ArgumentParser(prog="game-optimization-run")
    parser.add_argument("--appid", required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--prepare-host-launch", default="")
    namespace = parser.parse_args(values[:separator])
    host_launch = str(namespace.prepare_host_launch).strip()
    if namespace.plan_only and host_launch:
        raise ValueError("plan-only and host-launch preparation are mutually exclusive")
    return (
        validate_game_key(namespace.appid),
        bool(namespace.plan_only),
        Path(host_launch) if host_launch else None,
        values[separator + 1:],
    )


def _validate_host_launch_directory(
    environment: Mapping[str, str], requested: Path
) -> Path:
    root = host_home_directory(environment) / _STEAM_ENV_DIRECTORY
    directory = Path(requested)
    if (
        not directory.is_absolute()
        or directory.parent != root
        or not directory.name.startswith(_HOST_LAUNCH_PREFIX)
        or not directory.name.removeprefix(_HOST_LAUNCH_PREFIX).isalnum()
    ):
        raise ValueError("the host launch handoff path is invalid")
    try:
        root_info = root.lstat()
        directory_info = directory.lstat()
    except OSError as error:
        raise ValueError("the host launch handoff directory is unavailable") from error
    for info, label in (
        (root_info, "root"),
        (directory_info, "directory"),
    ):
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError(f"the host launch handoff {label} is not private")
    return directory


def _open_private_directory(directory: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(directory, flags)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        os.close(descriptor)
        raise ValueError("the host launch handoff directory is not private")
    return descriptor


def _atomic_protocol_write(
    directory: Path,
    name: str,
    payload: bytes,
    *,
    mode: int = 0o600,
) -> None:
    if name not in _HOST_LAUNCH_PROTOCOL_FILES:
        raise ValueError("unsupported host launch protocol file")
    directory_fd = _open_private_directory(directory)
    temporary = f".{name}.{uuid4().hex}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, mode, dir_fd=directory_fd)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _read_protocol_integer(directory: Path, name: str) -> int | None:
    if name not in {"started", "completed"}:
        raise ValueError("unsupported host launch status file")
    directory_fd = _open_private_directory(directory)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > 32
            ):
                raise ValueError(f"the host launch {name} status is invalid")
            payload = os.read(descriptor, 33)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)
    value = payload.decode("ascii", errors="strict").strip()
    if not value or not value.removeprefix("-").isdecimal():
        raise ValueError(f"the host launch {name} status is invalid")
    parsed = int(value)
    if name == "started" and parsed <= 0:
        raise ValueError("the host launch PID is invalid")
    if name == "completed" and not 0 <= parsed <= 255:
        raise ValueError("the host launch exit status is invalid")
    return parsed


def _shell_assignment(name: str, value: str) -> list[str]:
    if (
        not name
        or not name.isascii()
        or not name.replace("_", "A").isalnum()
        or not (name[0].isalpha() or name[0] == "_")
        or "\0" in value
        or "\n" in name
    ):
        raise ValueError(f"invalid launch environment variable: {name!r}")
    return [f"{name}={shlex.quote(value)}", f"export {name}"]


def _host_launcher_payload(
    plan: object,
    environment_overrides: Mapping[str, str],
) -> bytes:
    command = [str(value) for value in getattr(plan, "command")]
    steam_command = tuple(str(value) for value in getattr(plan, "steam_command"))
    if (
        not steam_command
        or len(command) < len(steam_command)
        or tuple(command[-len(steam_command):]) != steam_command
    ):
        raise ValueError("the launch plan does not preserve the Steam command suffix")
    prefix = command[:-len(steam_command)]
    lines = ["#!/bin/sh", "set -eu"]
    for key, value in sorted(environment_overrides.items()):
        lines.extend(_shell_assignment(str(key), str(value)))
    for key in getattr(plan, "wrapper_environment_removed"):
        _shell_assignment(str(key), "")
        lines.append(f"unset {key}")
    for key, value in sorted(getattr(plan, "wrapper_environment_overrides").items()):
        lines.extend(_shell_assignment(str(key), str(value)))
    quoted_prefix = " ".join(shlex.quote(value) for value in prefix)
    lines.append(f"exec {quoted_prefix} \"$@\"" if prefix else 'exec "$@"')
    return ("\n".join(lines) + "\n").encode("utf-8")


def _load_steam_environment(
    environment: Mapping[str, str],
    *,
    host_launch_directory: Path | None = None,
) -> dict[str, str]:
    raw_path = str(environment.get(_STEAM_ENV_FILE, "")).strip()
    if not raw_path:
        return dict(environment)

    expected_directory = host_home_directory(environment) / _STEAM_ENV_DIRECTORY
    snapshot = Path(raw_path)
    allowed_parent = (
        host_launch_directory
        if host_launch_directory is not None
        else expected_directory
    )
    if not snapshot.is_absolute() or snapshot.parent != allowed_parent:
        raise ValueError("the Steam environment handoff path is invalid")
    if host_launch_directory is not None and snapshot.name != "steam.env":
        raise ValueError("the Steam environment handoff filename is invalid")
    try:
        directory_info = expected_directory.lstat()
    except OSError as error:
        raise ValueError("the Steam environment handoff directory is unavailable") from error
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_ISLNK(directory_info.st_mode)
        or directory_info.st_uid != os.getuid()
        or directory_info.st_mode & 0o077
    ):
        raise ValueError("the Steam environment handoff directory is not private")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = (
        _open_private_directory(host_launch_directory)
        if host_launch_directory is not None
        else None
    )
    try:
        descriptor = os.open(
            snapshot.name if directory_fd is not None else snapshot,
            flags,
            dir_fd=directory_fd,
        )
    except OSError as error:
        if directory_fd is not None:
            os.close(directory_fd)
        raise ValueError("the Steam environment handoff file is unavailable") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > _MAX_STEAM_ENV_BYTES
        ):
            raise ValueError("the Steam environment handoff file is invalid")
        data = bytearray()
        while len(data) <= _MAX_STEAM_ENV_BYTES:
            chunk = os.read(descriptor, min(65536, _MAX_STEAM_ENV_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_STEAM_ENV_BYTES:
            raise ValueError("the Steam environment handoff file is too large")
    finally:
        os.close(descriptor)
        try:
            if directory_fd is not None:
                os.unlink(snapshot.name, dir_fd=directory_fd)
            else:
                snapshot.unlink()
        except OSError:
            pass
        if directory_fd is not None:
            os.close(directory_fd)

    result: dict[str, str] = {}
    for entry in bytes(data).split(b"\0"):
        if not entry:
            continue
        key_raw, separator, value_raw = entry.partition(b"=")
        if not separator:
            raise ValueError("the Steam environment handoff contains an invalid entry")
        key = os.fsdecode(key_raw)
        if not key or "=" in key or "\0" in key:
            raise ValueError("the Steam environment handoff contains an invalid key")
        result[key] = os.fsdecode(value_raw)
    result.pop(_STEAM_ENV_FILE, None)
    return result


def _restore_steam_app_context(
    app_id: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    result = dict(environment)
    if not app_id.isdecimal():
        return result
    for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
        current = str(result.get(key, "")).strip()
        if current in {"", "0"}:
            result[key] = app_id
        elif current.isdecimal() and current != app_id:
            raise ValueError(f"{key} does not match --appid")
    compatdata = str(result.get("STEAM_COMPAT_DATA_PATH", "")).strip()
    if compatdata:
        compatdata_path = Path(compatdata)
        if compatdata_path.name == "0":
            result["STEAM_COMPAT_DATA_PATH"] = os.fspath(
                compatdata_path.with_name(app_id)
            )
        elif compatdata_path.name.isdecimal() and compatdata_path.name != app_id:
            raise ValueError("STEAM_COMPAT_DATA_PATH does not match --appid")
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    repository: GameOptimizationProfileRepository | None = None,
    detector: RuntimeToolDetector | None = None,
    mangohud_repository: MangoHudProfileRepository | None = None,
    optiscaler_repository: OptiScalerProfileRepository | None = None,
    proton_tweaks_repository: ProtonTweaksRepository | None = None,
    executor: Callable[[str, Sequence[str], dict[str, str]], object] | None = None,
    report_root: Path | None = None,
    baseline_sessions: BaselineSessionRepository | None = None,
) -> int:
    try:
        app_id, plan_only, requested_host_launch, game_argv = _arguments(
            sys.argv[1:] if argv is None else argv
        )
        host_launch_directory = (
            _validate_host_launch_directory(os.environ, requested_host_launch)
            if requested_host_launch is not None
            else None
        )
        original_steam_environment = _load_steam_environment(
            os.environ,
            host_launch_directory=host_launch_directory,
        )
        steam_environment = _restore_steam_app_context(
            app_id,
            original_steam_environment,
        )
        profiles = repository or GameOptimizationProfileRepository()
        profile = profiles.load(app_id)
        mango_profiles = mangohud_repository or MangoHudProfileRepository(profiles.root)
        mangohud_profile = mango_profiles.load(app_id)
        mangohud_activation_owner = (
            "per_application_config"
            if mangohud_profile.enabled and mangohud_profile.executable_path
            else "steam_environment"
            if mangohud_profile.enabled
            else "none"
        )
        optiscaler_profiles = optiscaler_repository or OptiScalerProfileRepository(
            profiles.root
        )
        optiscaler_warning = ""
        try:
            optiscaler_profile = optiscaler_profiles.load(app_id)
            optiscaler_override = (
                optiscaler_profile.proton_override
                if optiscaler_profile.enabled
                and optiscaler_profile.installation_state == "installed"
                else ""
            )
        except OptiScalerError as error:
            # An optional integration profile must never prevent the base game
            # from launching. Ignore only OptiScaler and retain the reason in
            # the compact runner report.
            optiscaler_override = ""
            optiscaler_warning = f"OptiScaler profile ignored: {error}"
        proton_profiles = proton_tweaks_repository or ProtonTweaksRepository(
            profiles.root
        )
        proton_warning = ""
        try:
            proton_environment = proton_profiles.load(app_id).environment()
        except ProtonTweaksError as error:
            proton_environment = {}
            proton_warning = f"Proton Tweaks profile ignored: {error}"
        in_flatpak = bool(os.environ.get("FLATPAK_ID", "").strip())
        host_service = HostServiceClient() if in_flatpak and detector is None else None
        active_detector = detector or RuntimeToolDetector(host_service=host_service)
        gamemode, gamescope = active_detector.detect()
        sessions = baseline_sessions or BaselineSessionRepository()
        baseline_session, baseline_claim_reason = sessions.claim_with_reason(
            app_id, runner_pid=os.getpid()
        )
        if baseline_session is None and baseline_claim_reason != "no baseline session exists for this AppID":
            print(
                "game-optimization-run: baseline invocation rejected "
                f"appId={app_id} reason={baseline_claim_reason}",
                file=sys.stderr,
            )
        measurement_environment: dict[str, str] = {}
        if baseline_session is not None:
            measurement_environment = sessions.environment(baseline_session)
        plan = OptimizationLaunchPlanner().build(
            profile, game_argv, gamemode=gamemode, gamescope=gamescope,
            mangohud_fps_limit=(
                None if baseline_session is not None else mangohud_profile.fps_limit
            ),
            optiscaler_override=optiscaler_override,
            existing_wine_overrides=steam_environment.get("WINEDLLOVERRIDES", ""),
            proton_environment=proton_environment,
            existing_environment=steam_environment,
            mangohud_activation_owner=(
                "measurement_session" if baseline_session else mangohud_activation_owner
            ),
            measurement_environment=measurement_environment,
        )
        print(
            f"game-optimization-run: LaunchPlan AppID={app_id} Steam command: "
            f"{shlex.join(plan.steam_command)}",
            file=sys.stderr,
        )
        print(
            f"game-optimization-run: LaunchPlan AppID={app_id} GameMode wrapper: "
            f"{shlex.join(plan.gamemode_wrapper) if plan.gamemode_wrapper else 'disabled'}",
            file=sys.stderr,
        )
        print(
            f"game-optimization-run: LaunchPlan AppID={app_id} Gamescope wrapper: "
            f"{shlex.join(plan.gamescope_wrapper) if plan.gamescope_wrapper else 'disabled'}",
            file=sys.stderr,
        )
        environment_overrides_shell = (
            shlex.join(
                f"{key}={value}" for key, value in sorted(plan.environment.items())
            )
            if plan.environment
            else "none"
        )
        print(
            f"game-optimization-run: LaunchPlan AppID={app_id} environment overrides: "
            f"{environment_overrides_shell}",
            file=sys.stderr,
        )
        if plan.wrapper_environment_removed:
            print(
                f"game-optimization-run: LaunchPlan AppID={app_id} host wrapper "
                f"environment isolation: {', '.join(plan.wrapper_environment_removed)}",
                file=sys.stderr,
            )
        report = {
            "formatVersion": 1, "appId": app_id, "profile": profile.preset,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "plan_verified" if plan_only else "baseline_started" if baseline_session else "exec_started",
            "executable": plan.executable,
            "arguments": plan.diagnostic_command[1:],
            "wrappers": list(plan.wrappers), "environmentKeys": sorted(plan.environment),
            "environmentSources": dict(plan.environment_sources),
            "environmentConflicts": list(plan.environment_conflicts),
            "reasons": list(plan.reasons),
            "warnings": [
                *plan.warnings,
                *([optiscaler_warning] if optiscaler_warning else []),
                *([proton_warning] if proton_warning else []),
            ],
            "fpsLimitOwner": plan.fps_limit_owner,
            "fpsLimit": plan.fps_limit or 0,
            "mangoHudActivationOwner": plan.mangohud_activation_owner,
            "executionTransport": (
                "steam-host-runner" if host_launch_directory is not None else "native"
            ),
            "steamContextAppId": str(steam_environment.get("SteamAppId", "")),
            "steamContextGameId": str(steam_environment.get("SteamGameId", "")),
            "steamCommand": list(plan.steam_command),
            "gameModeWrapper": list(plan.gamemode_wrapper),
            "gamescopeWrapper": list(plan.gamescope_wrapper),
            "diagnosticCommand": plan.diagnostic_command,
            "diagnosticCommandShell": shlex.join(plan.diagnostic_command),
            "steamCommandShell": shlex.join(plan.steam_command),
            "gameModeWrapperShell": (
                shlex.join(plan.gamemode_wrapper) if plan.gamemode_wrapper else "disabled"
            ),
            "gamescopeWrapperShell": (
                shlex.join(plan.gamescope_wrapper) if plan.gamescope_wrapper else "disabled"
            ),
            "environmentOverridesShell": environment_overrides_shell,
            "wrapperEnvironmentRemoved": list(plan.wrapper_environment_removed),
            "baselineSessionId": baseline_session.id if baseline_session else "",
            "baselineRunnerPid": baseline_session.runner_pid if baseline_session else None,
            "baselineHandshakeAt": (
                baseline_session.handshake_at.isoformat()
                if baseline_session and baseline_session.handshake_at else ""
            ),
            "baselineCompletionReceived": False,
        }
        _write_report(app_id, report, report_root or STATE_DIR / "launch-reports")
        if plan_only:
            if baseline_session is not None:
                sessions.fail(
                    app_id,
                    "Runner plan-only test did not start a game",
                    baseline_session.id,
                )
            return 0
        if in_flatpak and host_launch_directory is None:
            raise ValueError(
                "the installed host runner is outdated; reopen Game Optimization Linux "
                "to refresh it before launching the game"
            )
        environment = steam_environment.copy()
        environment.update(plan.environment)
        process_environment = plan.process_environment(environment)
        process: subprocess.Popen[bytes] | None = None
        if executor is not None:
            result = executor(plan.executable, plan.command, process_environment)
        elif host_launch_directory is not None:
            launch_environment = dict(plan.environment)
            for key in (
                "SteamAppId",
                "SteamGameId",
                "STEAM_COMPAT_APP_ID",
                "STEAM_COMPAT_DATA_PATH",
            ):
                if key in steam_environment:
                    launch_environment[key] = steam_environment[key]
            launcher = _host_launcher_payload(plan, launch_environment)
            _atomic_protocol_write(
                host_launch_directory,
                "launcher",
                launcher,
                mode=0o700,
            )
            _atomic_protocol_write(host_launch_directory, "ready", b"1\n")
            result = _wait_for_host_launch(
                host_launch_directory,
                sessions,
                app_id,
                baseline_session,
                report,
                report_root or STATE_DIR / "launch-reports",
                command_name=Path(plan.command[0]).name,
            )
        else:
            if baseline_session is not None:
                process = subprocess.Popen(
                    plan.command,
                    stdin=subprocess.DEVNULL,
                    shell=False,
                    env=process_environment,
                )
                try:
                    process_group = os.getpgid(process.pid)
                except OSError:
                    process_group = None
                sessions.mark_process_started(
                    app_id,
                    baseline_session.id,
                    baseline_session.runner_token,
                    spawned_pid=process.pid,
                    process_group=process_group,
                    command_name=Path(plan.command[0]).name,
                )
                report.update({
                    "baselineSpawnedPid": process.pid,
                    "baselineProcessGroup": process_group,
                    "baselineObservedProcesses": [
                        f"pid={process.pid} command={Path(plan.command[0]).name} state=running"
                    ],
                })
                _write_report(
                    app_id, report, report_root or STATE_DIR / "launch-reports"
                )
                print(
                    "game-optimization-run: baseline lifecycle "
                    f"session={baseline_session.id} appId={app_id} "
                    f"runnerPid={os.getpid()} spawnedPid={process.pid} "
                    f"processGroup={process_group} state=recording",
                    file=sys.stderr,
                )
                result = _wait_for_baseline_process(
                    process,
                    sessions,
                    app_id,
                    baseline_session.id,
                    baseline_session.runner_token,
                )
            else:
                result = os.execvpe(plan.executable, plan.command, process_environment)
        exit_code = int(result) if isinstance(result, int) else 0
        if baseline_session is not None:
            finished = sessions.finish(
                app_id,
                exit_code,
                baseline_session.id,
                baseline_session.runner_token,
            )
            completion_received = bool(
                finished is not None
                and finished.id == baseline_session.id
                and finished.runner_token == baseline_session.runner_token
                and finished.status in {"processing", "failed"}
            )
            artifacts = sessions.artifact_diagnostics(app_id)
            report.update({
                "status": "baseline_finished" if completion_received else "baseline_superseded",
                "baselineCompletionReceived": completion_received,
                "baselineExitCode": exit_code,
                "baselineLogExists": sessions.newest_log(app_id) is not None,
                "baselineArtifacts": artifacts,
            })
            _write_report(app_id, report, report_root or STATE_DIR / "launch-reports")
            print(
                "game-optimization-run: baseline lifecycle "
                f"session={baseline_session.id} appId={app_id} "
                "spawnedPid="
                f"{process.pid if process is not None else report.get('baselineSpawnedPid', 'executor')} "
                f"completion={completion_received} exitCode={exit_code} "
                f"logExists={sessions.newest_log(app_id) is not None} "
                f"config={artifacts['configPath']} "
                f"configExists={artifacts['configExists']} "
                f"outputDirectory={artifacts['outputDirectory']} "
                f"outputDirectoryExists={artifacts['outputDirectoryExists']} "
                f"files={artifacts['files']} "
                f"measurementFile={artifacts['measurementFile'] or 'none'}",
                file=sys.stderr,
            )
        return 0 if host_launch_directory is not None else exit_code
    except (OSError, ValueError, OptiScalerError) as error:
        try:
            if baseline_session is not None:
                sessions.fail(
                    app_id,
                    str(error),
                    baseline_session.id,
                    baseline_session.runner_token,
                )
        except (NameError, OSError, ValueError):
            pass
        print(f"game-optimization-run: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
