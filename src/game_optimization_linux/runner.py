"""Standalone Steam command wrapper for saved Game Optimization profiles."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys

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


def _arguments(argv: Sequence[str]) -> tuple[str, bool, list[str]]:
    values = list(argv)
    try:
        separator = values.index("--")
    except ValueError as error:
        raise ValueError("missing -- before the Steam game command") from error
    parser = argparse.ArgumentParser(prog="game-optimization-run")
    parser.add_argument("--appid", required=True)
    parser.add_argument("--plan-only", action="store_true")
    namespace = parser.parse_args(values[:separator])
    return validate_game_key(namespace.appid), bool(namespace.plan_only), values[separator + 1:]


def _load_steam_environment(environment: Mapping[str, str]) -> dict[str, str]:
    raw_path = str(environment.get(_STEAM_ENV_FILE, "")).strip()
    if not raw_path:
        return dict(environment)

    expected_directory = host_home_directory(environment) / _STEAM_ENV_DIRECTORY
    snapshot = Path(raw_path)
    if not snapshot.is_absolute() or snapshot.parent != expected_directory:
        raise ValueError("the Steam environment handoff path is invalid")
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
    try:
        descriptor = os.open(snapshot, flags)
    except OSError as error:
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
            snapshot.unlink()
        except OSError:
            pass

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
        app_id, plan_only, game_argv = _arguments(sys.argv[1:] if argv is None else argv)
        steam_environment = _restore_steam_app_context(
            app_id,
            _load_steam_environment(os.environ),
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
            "executionTransport": "flatpak-spawn-host" if in_flatpak else "native",
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
        environment = steam_environment.copy()
        environment.update(plan.environment)
        process_environment = plan.process_environment(environment)
        process: subprocess.Popen[bytes] | None = None
        if executor is not None:
            result = executor(plan.executable, plan.command, process_environment)
        elif in_flatpak:
            flatpak_spawn = shutil.which("flatpak-spawn")
            if not flatpak_spawn:
                raise OSError("flatpak-spawn is unavailable in the sandbox")
            host_environment = [
                f"--env={key}={value}"
                for key, value in sorted(process_environment.items())
            ]
            host_command = [
                flatpak_spawn,
                "--host",
                *host_environment,
                *plan.command,
            ]
            if baseline_session is not None:
                process = subprocess.Popen(
                    host_command,
                    stdin=subprocess.DEVNULL,
                    shell=False,
                    env=os.environ.copy(),
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
                    command_name=Path(host_command[0]).name,
                )
                report.update({
                    "baselineSpawnedPid": process.pid,
                    "baselineProcessGroup": process_group,
                    "baselineObservedProcesses": [
                        f"pid={process.pid} command={Path(host_command[0]).name} state=running"
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
                result = os.execvpe(flatpak_spawn, host_command, os.environ.copy())
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
                f"spawnedPid={process.pid if process is not None else 'executor'} "
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
        return exit_code
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
