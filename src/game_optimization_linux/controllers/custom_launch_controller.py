"""Per-game custom launch settings: support matrix, saved/draft preview, transactional Apply.

Status and preview only read files and build plans; they never execute a user
command and never write launcher files or hook helpers.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import shlex
from typing import TYPE_CHECKING, Any, Mapping

from ..models import Game, Launcher
from ..services.custom_launch import (
    CustomLaunchError,
    CustomLaunchProfile,
    CustomLaunchRepository,
    field_errors,
    launch_key,
    redact,
)

if TYPE_CHECKING:  # pragma: no cover
    from .app_controller import AppController

logger = logging.getLogger(__name__)

FEATURES = ("environment", "arguments", "wrapper", "preLaunch", "postExit")
# Trusted user command execution: GameOpti prevents shell parsing and
# privileged programs, but a user's own script may do anything that user can.
SECURITY_NOTE = "trusted_user_commands"


def _row(state: str, where: str, reason: str = "", code: str = "", detail: str = "") -> dict[str, str]:
    return {"state": state, "where": where, "reason": reason, "code": code, "detail": detail}


def _unavailable(reason: str, code: str, detail: str = "") -> dict[str, dict[str, str]]:
    return {name: _row("unavailable", "unsupported", reason, code, detail) for name in FEATURES}


class CustomLaunchController:
    def __init__(self, app: "AppController") -> None:
        self._app = app

    # -- helpers ---------------------------------------------------------------
    @property
    def repository(self) -> CustomLaunchRepository:
        return CustomLaunchRepository(self._app._optiscaler_service.profile_repository.root)

    def _home(self) -> Path:
        return Path(getattr(self._app._launcher_integration, "_home", None) or Path.home())

    def _game(self, game_id: str) -> Game | None:
        return self._app._resolve_game(game_id, show_error=False)

    def _local_windows(self, game: Game) -> bool:
        try:
            resolution = self._app._runner_integration._executable_resolver.resolve(game, game.executable_path)
        except Exception:  # resolver problems surface at launch with their own message
            return False
        return bool(resolution.selected is not None and resolution.selected.wine)

    def support(self, game: Game, *, umu_active: bool | None = None, launcher_error: str = "") -> dict[str, dict[str, str]]:
        """Where each custom feature runs for this game, or why it is unavailable."""
        if game.launcher is Launcher.STEAM:
            if str(game.data_source) == "Steam Flatpak":
                return _unavailable(
                    "Steam Flatpak runs Launch Options inside its sandbox, which cannot reach the GameOpti host "
                    "runner; not verified (Steam Flatpak is not installed on this system)", "steam_flatpak")
            return {"environment": _row("supported", "game_environment"),
                    "arguments": _row("supported", "game_command"),
                    "wrapper": _row("supported", "host_runner"),
                    "preLaunch": _row("supported", "host_runner"),
                    "postExit": _row("supported", "host_runner")}
        if game.launcher is Launcher.MANUAL and str(game.data_source).casefold() == "local":
            if self._local_windows(game):
                return _unavailable("No Proton runner is configured for this Local Windows executable; "
                                    "add it as a Custom game with a Wine/Proton runner instead", "local_windows_no_runner")
            if os.environ.get("FLATPAK_ID"):
                # libexec/game-optimization-run-host accepts only numeric Steam AppIDs,
                # so a Local game started from the Flatpak build never reaches the game.
                return _unavailable("The GameOpti host runner of the Flatpak build accepts only Steam AppIDs; "
                                    "Local games cannot start through it yet", "local_flatpak_host_runner")
            return {"environment": _row("supported", "game_environment"),
                    "arguments": _row("supported", "game_command"),
                    "wrapper": _row("supported", "host_runner"),
                    "preLaunch": _row("supported", "host_runner"),
                    "postExit": _row("supported", "host_runner")}
        if game.launcher is Launcher.MANUAL:
            return {"environment": _row("supported", "game_environment"),
                    "arguments": _row("supported", "game_command"),
                    "wrapper": _row("supported", "host_gameopti"),
                    "preLaunch": _row("supported", "host_gameopti"),
                    "postExit": _row("supported", "host_gameopti")}
        if game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}:
            if launcher_error:
                return _unavailable(launcher_error, "launcher_config_unavailable", launcher_error)
            from ..services import launch_hooks
            from ..services.launcher_integration import launcher_argument_setting
            name = game.launcher.value
            sandbox = game.launcher_variant == "flatpak"
            where = "launcher_sandbox" if sandbox else "launcher_host"
            argument = launcher_argument_setting(game)
            hooks, hooks_where, hooks_reason = launch_hooks.support(game, umu_active=umu_active)
            hook_code = "hooks_lutris_flatpak" if game.launcher is Launcher.LUTRIS else "hooks_heroic_umu"
            hook_row = _row("supported", "launcher_sandbox" if sandbox else "launcher_host") if hooks else \
                _row("unavailable", "unsupported", hooks_reason, hook_code)
            return {"environment": _row("supported", "launcher_config"),
                    "arguments": _row("supported", "launcher_config") if argument else _row(
                        "unavailable", "unsupported",
                        "Heroic 2.22.4 passes custom game arguments only for Epic (legendary) games"
                        if game.launcher is Launcher.HEROIC else
                        f"Lutris passes game arguments only for the wine and linux runners (this game: {game.runner or 'unknown'})",
                        "heroic_args_legendary_only" if game.launcher is Launcher.HEROIC else "lutris_args_runner",
                        "" if game.launcher is Launcher.HEROIC else str(game.runner or "")),
                    "wrapper": _row("supported", where),
                    "preLaunch": hook_row, "postExit": hook_row}
        return _unavailable("Custom launch settings are not available for this game", "not_supported")

    # -- preview ---------------------------------------------------------------
    def _resolved(self, game: Game, profile: CustomLaunchProfile) -> dict[str, Any]:
        result: dict[str, Any] = {"argv": [], "environment": [], "wrappers": [], "pre": [], "post": [],
                                  "conflicts": [], "unsupported": [], "launcherWrites": {}, "inherited": [],
                                  "warnings": [], "launcherError": ""}
        umu: bool | None = None
        if game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}:
            from ..services.launcher_integration import LauncherSyncError, launcher_facts, locate_config
            try:
                plan = self._app._optimization_controller.launcher_plan(game, custom_launch=profile)
                config = locate_config(game, self._home())
                document = config.load()
                inherited = config.env(document)
            except (LauncherSyncError, OSError, ValueError) as error:
                result["launcherError"] = (f"The {game.launcher.value} game configuration is unavailable: {error}")
                result["support"] = self.support(game, launcher_error=result["launcherError"])
                return result
            umu = False if plan.settings.get("disableUMU") is True else launcher_facts(game, self._home()).umu_active
            result["argv"] = [*plan.prefix, f"<{game.launcher.value} game command>",
                              *(shlex.split(str(plan.settings.get("launcherArgs") or plan.settings.get("args") or "")))]
            result["launcherWrites"] = {
                "environment": {k: redact(k, str(v)) for k, v in sorted(plan.environment.items())},
                "wrapper": shlex.join(plan.prefix) if plan.prefix else "",
                "settings": {k: str(v) for k, v in sorted(plan.settings.items())},
                "blockers": list(plan.blockers),
            }
            result["warnings"] = list(plan.warnings)
            result["inherited"] = sorted(k for k in inherited if k not in plan.environment)
            service = self._app._launcher_integration
            for text in service._conflicts(plan, service._load_record(game), config, document):
                result["conflicts"].append({"key": "", "code": "launcher_conflict", "reason": str(text)})
            applied = plan.environment
        elif game.launcher is Launcher.MANUAL and str(game.data_source).casefold() == "manual":
            applied = {}
            getter = getattr(self._app._game_provider, "manual_config", None)
            configuration = getter(game.id) if callable(getter) else None
            if configuration is None:
                result["launcherError"] = "Manual game launch configuration was not found"
            else:
                launcher = self._app._manual_game_launcher
                try:
                    plan = launcher.prepare(configuration)
                    resolved = launcher.apply_custom(plan, profile, tree_prefix=["<host process-tree scope>"])
                    result["argv"] = list(resolved.game_command)
                    applied = dict(resolved.environment)
                    result["warnings"] = list(resolved.warnings)
                    result["inherited"] = sorted(k for k, _v in plan.environment)
                except (OSError, ValueError, RuntimeError) as error:
                    result["launcherError"] = str(error)
        else:
            from ..models import GameOptimizationProfile  # noqa: F401  (type only)
            opt = self._app._optimization_profile_repository.load(game.profile_key)
            data = self._app._optimization_controller._optimization_profile_to_qml(opt, custom_launch=profile)
            plan = data.get("launchPlan") or {}
            result["argv"] = [str(v) for v in plan.get("diagnosticCommand") or plan.get("command") or []]
            result["wrappers"] = list(plan.get("wrappers") or [])
            result["warnings"] = [w for w in plan.get("warnings") or [] if "custom" in str(w).casefold()]
            applied = {}
            for row in plan.get("environmentClassification") or []:
                if row.get("category") == "inherited":
                    result["inherited"].append(str(row.get("key")))
            applied = {k: v for k, v in (plan.get("environment") or {}).items()}
        active = profile.active_environment()
        for entry in profile.environment:
            state = "disabled"
            if entry.enabled:
                state = "applied" if applied.get(entry.key) == entry.value else "conflict"
            result["environment"].append({"key": entry.key, "value": redact(entry.key, entry.value),
                                          "secret": redact(entry.key, "x") != "x", "state": state})
            if state == "conflict":
                result["conflicts"].append({"key": entry.key, "code": "env_managed",
                                            "reason": f"{entry.key}: GameOpti or the launcher configuration already "
                                                      "sets this variable; the custom value is not applied"})
        del active
        result["support"] = self.support(game, umu_active=umu)
        for name, rows in (("pre", profile.active_pre()), ("post", profile.active_post())):
            feature = result["support"]["preLaunch" if name == "pre" else "postExit"]
            for entry in rows:
                result[name].append({"argv": list(entry.argv), "timeout": entry.timeout, "onFailure": entry.on_failure,
                                     "runIfNotStarted": entry.run_if_not_started, "where": feature["where"],
                                     "state": feature["state"]})
        for name, label in (("arguments", profile.active_arguments()), ("wrapper", profile.active_wrapper()),
                            ("preLaunch", profile.active_pre()), ("postExit", profile.active_post()),
                            ("environment", active_keys(profile))):
            if label and result["support"][name]["state"] != "supported":
                row = result["support"][name]
                result["unsupported"].append({"feature": name, "reason": row["reason"], "code": row["code"],
                                              "detail": row["detail"]})
        if result["launcherError"]:
            result["unsupported"].append({"feature": "", "reason": result["launcherError"],
                                          "code": "launcher_config_unavailable", "detail": result["launcherError"]})
        home = str(Path.home())
        for entry in (*((profile.wrapper,) if profile.wrapper and profile.wrapper.enabled else ()),
                      *profile.active_pre(), *profile.active_post()):
            program = entry.argv[0]
            if program.startswith(home + "/") and not os.path.exists(program):
                result["warnings"].append(f"{program} does not exist")
        result["technicalText"] = self._technical_text(game, profile, result)
        return result

    @staticmethod
    def _technical_text(game: Game, profile: CustomLaunchProfile, resolved: Mapping[str, Any]) -> str:
        """Copyable diagnostics; secret values are redacted."""
        lines = [f"game: {game.id} ({game.launcher.value}{' Flatpak' if game.launcher_variant == 'flatpak' else ''})",
                 f"profile key: {profile.key}", f"argv: {shlex.join(str(a) for a in resolved['argv'])}"]
        for entry in profile.environment:
            lines.append(f"env {'+' if entry.enabled else '-'} {entry.key}={redact(entry.key, entry.value)}")
        for name, label in (("pre", "before launch"), ("post", "after exit")):
            for entry in resolved[name]:
                lines.append(f"{label} [{entry['where']}, {entry['timeout']} s]: {shlex.join(entry['argv'])}")
        for name, item in resolved.get("support", {}).items():
            lines.append(f"{name}: {item['state']} / {item['where']}{' - ' + item['reason'] if item['reason'] else ''}")
        writes = resolved.get("launcherWrites") or {}
        if writes:
            lines.append(f"launcher wrapper: {writes.get('wrapper', '')}")
            lines.extend(f"launcher env {k}={v}" for k, v in writes.get("environment", {}).items())
            lines.extend(f"launcher option {k}={v}" for k, v in writes.get("settings", {}).items())
        return "\n".join(lines)

    def get(self, game_id: str) -> dict[str, Any]:
        game = self._game(game_id)
        if game is None:
            return {"success": False, "error": "Select a game first"}
        try:
            key = launch_key(game)
            repo = self.repository
            saved = repo.load(key)
        except (OSError, ValueError, CustomLaunchError) as error:
            return {"success": False, "error": str(error)}
        return {"success": True, "key": key, "launcher": game.launcher.value,
                "launcherVariant": game.launcher_variant or "native",
                "launcherConfigWrite": game.launcher in {Launcher.HEROIC, Launcher.LUTRIS},
                "profile": saved.to_dict(), "profilePath": str(repo.path(key)),
                "savedAt": repo.modified_at(key), "savedDigest": repo.digest(key),
                "resolved": self._resolved(game, saved), "securityNote": SECURITY_NOTE}

    def preview(self, game_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self.get(game_id)
        if not result.get("success"):
            return result
        errors = field_errors(payload)
        result["errors"] = errors
        if errors:
            result["draftResolved"] = None
            return result
        game = self._game(game_id)
        draft = CustomLaunchProfile.from_dict(result["key"], payload)
        result["draft"] = draft.to_dict()
        result["dirty"] = _comparable(draft.to_dict()) != _comparable(result["profile"])
        result["draftResolved"] = self._resolved(game, draft)
        return result

    # -- apply -----------------------------------------------------------------
    def save(self, game_id: str, payload: Mapping[str, Any], confirmed: bool = False) -> dict[str, Any]:
        """Save the profile and, for Heroic/Lutris, write the launcher in one transaction."""
        preview = self.preview(game_id, payload)
        if not preview.get("success"):
            return preview
        if preview["errors"]:
            return {**preview, "success": False, "error": preview["errors"][0]["message"]}
        game = self._game(game_id)
        launcher_write = game.launcher in {Launcher.HEROIC, Launcher.LUTRIS}
        if launcher_write and not confirmed:
            return {**preview, "success": False, "needsConfirmation": True,
                    "error": f"Confirm writing the {game.launcher.value} game configuration"}
        key = preview["key"]
        repo = self.repository
        previous = repo.read_bytes(key)
        draft = CustomLaunchProfile.from_dict(key, payload)
        try:
            repo.save(draft)
        except (OSError, CustomLaunchError) as error:
            return {**preview, "success": False, "error": str(error)}
        if launcher_write:
            service = self._app._launcher_integration
            needs_launcher = not draft.empty or service._load_record(game) is not None
            if needs_launcher:
                result = self._app._optimization_controller.applyLauncherIntegration(game.id)
                if not result.get("success") and "adds nothing to the launch" not in str(result.get("error", "")):
                    repo.restore_bytes(key, previous)
                    logger.warning("Custom launch Apply for %s rolled back: %s", game.id, result.get("error"))
                    return {**self.get(game_id), "success": False, "rolledBack": True,
                            "error": str(result.get("error") or "The launcher configuration could not be written")}
        logger.info("Saved custom launch profile for %s: env=%s args=%d wrapper=%s pre=%d post=%d",
                    game.id, sorted(draft.active_environment()), len(draft.active_arguments()),
                    bool(draft.active_wrapper()), len(draft.active_pre()), len(draft.active_post()))
        return {**self.get(game_id), "success": True}


def active_keys(profile: CustomLaunchProfile) -> tuple[str, ...]:
    return tuple(profile.active_environment())


def _comparable(document: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k not in {"updatedAt", "schemaVersion"}}
