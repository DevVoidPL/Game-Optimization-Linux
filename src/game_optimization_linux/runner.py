"""Standalone Steam command wrapper for saved Game Optimization profiles."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sys

from .config import STATE_DIR
from .models import validate_game_key
from .services.optimization_profiles import GameOptimizationProfileRepository
from .services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolDetector
from .services.mangohud import MangoHudProfileRepository
from .services.optiscaler import OptiScalerError, OptiScalerProfileRepository
from .services.proton_tweaks import ProtonTweaksError, ProtonTweaksRepository
from .services.host_service import HostServiceClient


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
) -> int:
    try:
        app_id, plan_only, game_argv = _arguments(sys.argv[1:] if argv is None else argv)
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
        plan = OptimizationLaunchPlanner().build(
            profile, game_argv, gamemode=gamemode, gamescope=gamescope,
            mangohud_fps_limit=mangohud_profile.fps_limit,
            optiscaler_override=optiscaler_override,
            existing_wine_overrides=os.environ.get("WINEDLLOVERRIDES", ""),
            proton_environment=proton_environment,
            existing_environment=os.environ,
            mangohud_activation_owner=mangohud_activation_owner,
        )
        report = {
            "formatVersion": 1, "appId": app_id, "profile": profile.preset,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "plan_verified" if plan_only else "exec_started",
            "executable": plan.executable, "arguments": list(plan.arguments),
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
        }
        _write_report(app_id, report, report_root or STATE_DIR / "launch-reports")
        if plan_only:
            return 0
        environment = os.environ.copy()
        environment.update(plan.environment)
        if executor is not None:
            result = executor(plan.executable, plan.command, environment)
        elif in_flatpak:
            flatpak_spawn = shutil.which("flatpak-spawn")
            if not flatpak_spawn:
                raise OSError("flatpak-spawn is unavailable in the sandbox")
            host_environment = [
                f"--env={key}={value}" for key, value in sorted(plan.environment.items())
            ]
            host_command = [
                flatpak_spawn,
                "--host",
                *host_environment,
                *plan.command,
            ]
            result = os.execvpe(flatpak_spawn, host_command, os.environ.copy())
        else:
            result = os.execvpe(plan.executable, plan.command, environment)
        return int(result) if isinstance(result, int) else 0
    except (OSError, ValueError, OptiScalerError) as error:
        print(f"game-optimization-run: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
