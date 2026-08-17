from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
from uuid import uuid4

from game_optimization_linux.config import STATE_DIR
from game_optimization_linux.models import (
    BaselineSession,
    PerformanceMeasurement,
    validate_game_key,
)


RUNNER_HANDSHAKE_TIMEOUT_SECONDS = 120
RUNNER_HEARTBEAT_STALE_SECONDS = 30
LEGACY_RECORDING_STALE_SECONDS = 6 * 60 * 60


class BaselineSessionRepository:
    def __init__(self, root: Path = STATE_DIR / "performance-sessions") -> None:
        self.root = Path(root)

    def create(
        self,
        app_id: object,
        game_id: str,
        *,
        kind: str = "baseline",
        expected_runner_path: str = "",
        expected_runner_hash: str = "",
    ) -> BaselineSession:
        key = validate_game_key(app_id)
        current = self.load(key)
        if current is not None and current.status in {
            "waiting_for_steam", "waiting_for_runner", "recording",
            "waiting_for_game_exit", "processing",
        }:
            raise ValueError("A baseline recording is already active for this game")
        if kind not in {"baseline", "comparison"}:
            raise ValueError("Unsupported measurement session kind")
        session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:10]
        directory = self.root / key / session_id
        logs = directory / "logs"
        logs.mkdir(parents=True, mode=0o700)
        config = directory / "MangoHud-baseline.conf"
        config.write_text(self._config(logs), encoding="utf-8")
        os.chmod(config, 0o600)
        session = BaselineSession(
            session_id,
            key,
            str(game_id),
            "waiting_for_steam",
            directory,
            config,
            logs,
            datetime.now(UTC),
            kind=kind,
            expected_runner_path=str(expected_runner_path),
            expected_runner_hash=str(expected_runner_hash),
            handshake_timeout_seconds=RUNNER_HANDSHAKE_TIMEOUT_SECONDS,
        )
        self._save(session)
        return session

    def claim(
        self, app_id: object, *, runner_pid: int | None = None
    ) -> BaselineSession | None:
        session, _reason = self.claim_with_reason(app_id, runner_pid=runner_pid)
        return session

    def claim_with_reason(
        self, app_id: object, *, runner_pid: int | None = None
    ) -> tuple[BaselineSession | None, str]:
        session = self.load(app_id)
        if session is None:
            return None, "no baseline session exists for this AppID"
        if session.status not in {
            "waiting_for_steam", "waiting_for_runner", "recording",
            "waiting_for_game_exit",
        }:
            reason = f"session status is {session.status}"
            self._save(replace(
                session,
                runner_invocation_count=session.runner_invocation_count + 1,
                runner_rejection=reason,
            ))
            return None, reason
        if (
            session.status in {"waiting_for_steam", "waiting_for_runner"}
            and datetime.now(UTC) - session.created_at
            > timedelta(seconds=session.handshake_timeout_seconds)
        ):
            reason = "baseline runner handshake arrived after the allowed timeout"
            self._save(replace(
                session,
                status="failed",
                finished_at=datetime.now(UTC),
                error=reason,
                lifecycle_reason=reason,
                runner_invocation_count=session.runner_invocation_count + 1,
                runner_rejection=reason,
            ))
            return None, reason
        now = datetime.now(UTC)
        token = uuid4().hex
        claimed = replace(
            session,
            status="recording",
            started_at=session.started_at or now,
            handshake_at=now,
            runner_pid=runner_pid,
            spawned_pid=None,
            process_group=None,
            runner_token=token,
            runner_completed_at=None,
            observed_processes=(),
            lifecycle_reason=(
                "Runner attached to an active launcher chain"
                if session.status in {"recording", "waiting_for_game_exit"}
                else "Runner handshake completed"
            ),
            last_heartbeat_at=now,
            runner_invocation_count=session.runner_invocation_count + 1,
            runner_rejection="",
        )
        self._save(claimed)
        return claimed, "claimed"

    def mark_steam_launched(
        self, app_id: object, session_id: str, launch_result: str
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if session is None or session.id != session_id:
            return session
        updated = replace(session, steam_launch_result=str(launch_result))
        self._save(updated)
        return updated

    def mark_process_started(
        self,
        app_id: object,
        session_id: str,
        runner_token: str,
        *,
        spawned_pid: int,
        process_group: int | None,
        command_name: str,
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if (
            session is None
            or session.id != session_id
            or session.runner_token != runner_token
        ):
            return session
        process = f"pid={spawned_pid} command={command_name} state=running"
        updated = replace(
            session,
            spawned_pid=int(spawned_pid),
            process_group=process_group,
            observed_processes=(process,),
            lifecycle_reason=f"Waiting for spawned command PID {spawned_pid} to exit",
            last_heartbeat_at=datetime.now(UTC),
        )
        self._save(updated)
        return updated

    def heartbeat(
        self, app_id: object, session_id: str, runner_token: str
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if (
            session is None
            or session.id != session_id
            or session.runner_token != runner_token
            or session.status not in {"recording", "waiting_for_game_exit"}
        ):
            return session
        updated = replace(session, last_heartbeat_at=datetime.now(UTC))
        self._save(updated)
        return updated

    def mark_waiting_for_runner(
        self, app_id: object, session_id: str
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if session is None or session.id != session_id:
            return session
        if session.status == "waiting_for_steam":
            session = replace(
                session,
                status="waiting_for_runner",
                lifecycle_reason="Waiting for Game Optimization Runner handshake",
            )
            self._save(session)
        return session

    def mark_waiting_for_game_exit(
        self, app_id: object, session_id: str
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if session is None or session.id != session_id:
            return session
        if session.status == "recording":
            session = replace(
                session,
                status="waiting_for_game_exit",
                lifecycle_reason="MangoHud log detected; waiting for runner completion",
            )
            self._save(session)
        return session

    def finish(
        self,
        app_id: object,
        exit_code: int,
        session_id: str = "",
        runner_token: str = "",
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if (
            session is None
            or (session_id and session.id != session_id)
            or (runner_token and session.runner_token != runner_token)
            or session.status not in {"recording", "waiting_for_game_exit"}
        ):
            return session
        status = "processing" if int(exit_code) == 0 else "failed"
        error = "" if status == "processing" else f"Game process exited with status {exit_code}"
        finished = replace(
            session,
            status=status,
            finished_at=datetime.now(UTC),
            exit_code=int(exit_code),
            error=error,
            runner_completed_at=datetime.now(UTC),
            observed_processes=(
                f"pid={session.spawned_pid} state=exited code={int(exit_code)}",
            ) if session.spawned_pid is not None else (),
            lifecycle_reason=(
                "Runner completion received; MangoHud log is ready for processing"
                if status == "processing"
                else error
            ),
            last_heartbeat_at=datetime.now(UTC),
        )
        self._save(finished)
        return finished

    def recover_abandoned(
        self, *, now: datetime | None = None
    ) -> tuple[BaselineSession, ...]:
        current_time = now or datetime.now(UTC)
        recovered: list[BaselineSession] = []
        try:
            app_directories = tuple(self.root.iterdir())
        except OSError:
            return ()
        for directory in app_directories:
            if not directory.is_dir():
                continue
            try:
                session = self.load(directory.name)
            except ValueError:
                continue
            if session is None:
                continue
            reason = ""
            if session.status in {"waiting_for_steam", "waiting_for_runner"}:
                if (
                    current_time - session.created_at
                    > timedelta(seconds=session.handshake_timeout_seconds)
                ):
                    reason = "Baseline session was interrupted before the runner handshake"
            elif session.status in {"recording", "waiting_for_game_exit"}:
                reference = session.last_heartbeat_at
                stale_seconds = RUNNER_HEARTBEAT_STALE_SECONDS
                if reference is None:
                    reference = session.started_at or session.created_at
                    stale_seconds = LEGACY_RECORDING_STALE_SECONDS
                if current_time - reference > timedelta(seconds=stale_seconds):
                    reason = "Baseline session was interrupted because its runner heartbeat stopped"
            if not reason:
                continue
            failed = replace(
                session,
                status="failed",
                finished_at=current_time,
                error=reason,
                lifecycle_reason=reason,
                runner_rejection="",
            )
            self._save(failed)
            recovered.append(failed)
        return tuple(recovered)

    def finish_from_stable_log(
        self, app_id: object, session_id: str
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if (
            session is None
            or session.id != session_id
            or session.status not in {"recording", "waiting_for_game_exit"}
        ):
            return session
        finished = replace(
            session,
            status="processing",
            finished_at=datetime.now(UTC),
            lifecycle_reason=(
                "Runner completion was not received; a stabilized MangoHud log "
                "was detected and will be processed"
            ),
        )
        self._save(finished)
        return finished

    def fail(
        self,
        app_id: object,
        message: str,
        session_id: str = "",
        runner_token: str = "",
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if (
            session is None
            or (session_id and session.id != session_id)
            or (runner_token and session.runner_token != runner_token)
        ):
            return None
        failed = replace(
            session,
            status="failed",
            finished_at=datetime.now(UTC),
            error=str(message),
            runner_completed_at=datetime.now(UTC) if runner_token else session.runner_completed_at,
            lifecycle_reason=str(message),
        )
        self._save(failed)
        return failed

    def complete(
        self, app_id: object, session_id: str = ""
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if session is None or (session_id and session.id != session_id):
            return None
        completed = replace(
            session,
            status="completed",
            lifecycle_reason="Baseline measurement completed",
        )
        self._save(completed)
        return completed

    def mark_unrepresentative(
        self, app_id: object, session_id: str, reason: str
    ) -> BaselineSession | None:
        session = self.load(app_id)
        if session is None or session.id != session_id:
            return None
        recorded = replace(
            session,
            status="recorded_unrepresentative",
            error="",
            finished_at=session.finished_at or datetime.now(UTC),
            lifecycle_reason=str(reason),
        )
        self._save(recorded)
        return recorded

    def import_log(self, app_id: object, game_id: str, source: Path) -> BaselineSession:
        resolved = Path(source).resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("The selected MangoHud log is not supported")
        session = self.create(app_id, game_id)
        target = session.log_directory / "imported.csv"
        shutil.copyfile(resolved, target)
        completed = replace(
            session,
            status="processing",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            exit_code=0,
        )
        self._save(completed)
        return completed

    def load(self, app_id: object) -> BaselineSession | None:
        key = validate_game_key(app_id)
        path = self.root / key / "active.json"
        try:
            if path.stat().st_size > 64 * 1024:
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, Mapping) or str(raw.get("appId") or "") != key:
            return None
        try:
            directory = Path(str(raw["directory"])).resolve(strict=True)
            directory.relative_to((self.root / key).resolve(strict=True))
            created = datetime.fromisoformat(str(raw["createdAt"]))
            started = self._datetime(raw.get("startedAt"))
            finished = self._datetime(raw.get("finishedAt"))
        except (KeyError, OSError, ValueError, TypeError):
            return None
        return BaselineSession(
            str(raw.get("id") or ""),
            key,
            str(raw.get("gameId") or ""),
            str(raw.get("status") or ""),
            directory,
            Path(str(raw.get("configPath") or "")),
            Path(str(raw.get("logDirectory") or "")),
            created,
            started,
            finished,
            int(raw["exitCode"]) if isinstance(raw.get("exitCode"), int) else None,
            str(raw.get("error") or ""),
            str(raw.get("kind") or "baseline"),
            self._datetime(raw.get("handshakeAt")),
            int(raw["runnerPid"]) if isinstance(raw.get("runnerPid"), int) else None,
            int(raw["spawnedPid"]) if isinstance(raw.get("spawnedPid"), int) else None,
            int(raw["processGroup"]) if isinstance(raw.get("processGroup"), int) else None,
            str(raw.get("runnerToken") or ""),
            self._datetime(raw.get("runnerCompletedAt")),
            tuple(
                str(item) for item in raw.get("observedProcesses", ())
                if isinstance(item, str)
            ) if isinstance(raw.get("observedProcesses", ()), list) else (),
            str(raw.get("lifecycleReason") or ""),
            self._datetime(raw.get("lastHeartbeatAt")),
            int(raw.get("runnerInvocationCount") or 0),
            str(raw.get("runnerRejection") or ""),
            str(raw.get("steamLaunchResult") or ""),
            str(raw.get("expectedRunnerPath") or ""),
            str(raw.get("expectedRunnerHash") or ""),
            int(raw.get("handshakeTimeoutSeconds") or RUNNER_HANDSHAKE_TIMEOUT_SECONDS),
        )

    def save_measurement(
        self,
        app_id: object,
        measurement: PerformanceMeasurement,
        *,
        slot: str,
    ) -> Path:
        if slot not in {"before", "after"}:
            raise ValueError("Unsupported measurement slot")
        key = validate_game_key(app_id)
        path = self.root / key / f"{slot}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(measurement.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return path

    def load_measurement(
        self, app_id: object, *, slot: str
    ) -> PerformanceMeasurement | None:
        if slot not in {"before", "after"}:
            raise ValueError("Unsupported measurement slot")
        path = self.root / validate_game_key(app_id) / f"{slot}.json"
        try:
            if path.stat().st_size > 64 * 1024:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            return PerformanceMeasurement.from_dict(value)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def clear_measurement(self, app_id: object, *, slot: str) -> None:
        if slot not in {"before", "after"}:
            raise ValueError("Unsupported measurement slot")
        path = self.root / validate_game_key(app_id) / f"{slot}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def newest_log(self, app_id: object) -> Path | None:
        session = self.load(app_id)
        if session is None or not session.log_directory.is_dir():
            return None
        candidates = [
            path for path in session.log_directory.glob("*.csv")
            if (
                path.is_file()
                and not path.name.casefold().endswith("_summary.csv")
                and path.stat().st_size > 0
            )
        ]
        return max(
            candidates,
            key=lambda path: (path.stat().st_size, path.stat().st_mtime_ns),
            default=None,
        )

    def artifact_diagnostics(self, app_id: object) -> dict[str, object]:
        session = self.load(app_id)
        if session is None:
            return {
                "configPath": "",
                "configExists": False,
                "outputDirectory": "",
                "outputDirectoryExists": False,
                "files": [],
                "measurementFile": "",
            }
        files: list[str] = []
        try:
            files = sorted(
                str(path.relative_to(session.directory))
                for path in session.directory.rglob("*")
                if path.is_file()
            )[:64]
        except OSError:
            pass
        measurement = self.newest_log(app_id)
        return {
            "configPath": str(session.config_path),
            "configExists": session.config_path.is_file(),
            "outputDirectory": str(session.log_directory),
            "outputDirectoryExists": session.log_directory.is_dir(),
            "files": files,
            "measurementFile": str(measurement or ""),
        }

    def environment(self, session: BaselineSession) -> dict[str, str]:
        return {
            "MANGOHUD": "1",
            "MANGOHUD_CONFIGFILE": str(session.config_path),
            "MANGOHUD_CONFIG": "read_cfg",
        }

    def _save(self, session: BaselineSession) -> None:
        path = self.root / session.app_id / "active.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        payload = session.to_dict()
        payload["runnerToken"] = session.runner_token
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if not value:
            return None
        result = datetime.fromisoformat(str(value))
        return result if result.tzinfo is not None else result.replace(tzinfo=UTC)

    @staticmethod
    def _config(log_directory: Path) -> str:
        return "\n".join((
            "# Managed by Game Optimization Linux for one baseline session",
            "fps",
            "frametime",
            "gpu_stats",
            "gpu_temp",
            "cpu_stats",
            "ram",
            "vram",
            "procmem",
            "proc_vram",
            "alpha=0",
            "background_alpha=0",
            f"output_folder={log_directory}",
            "autostart_log=1",
            "log_duration=14400",
            "log_interval=100",
            "permit_upload=0",
            "",
        ))


__all__ = [
    "BaselineSessionRepository",
    "RUNNER_HANDSHAKE_TIMEOUT_SECONDS",
]
