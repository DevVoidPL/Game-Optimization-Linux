"""Polkit client and calculations for authoritative Btrfs measurements."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import signal
import shutil
import subprocess
from threading import RLock
from typing import Any

from game_optimization_linux.models.compression import CompressionMeasurement
from game_optimization_linux.models.game import Game


DEFAULT_HELPER_PATH = Path("/usr/libexec/game-optimization-linux-measure-helper")
DEFAULT_PKEXEC_PATH = Path(shutil.which("pkexec") or "/nonexistent/pkexec")
logger = logging.getLogger(__name__)


class PrivilegedMeasurementError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = str(stdout)
        self.stderr = str(stderr)


def measurement_delta(before_bytes: int, after_bytes: int) -> int:
    """Return the signed before-minus-after difference for two byte counters."""

    for value, name in ((before_bytes, "before_bytes"), (after_bytes, "after_bytes")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    return before_bytes - after_bytes


class PrivilegedMeasurementClient:
    """Invoke only the installed, root-owned read-only helper through Polkit."""

    def __init__(
        self,
        *,
        helper_path: Path = DEFAULT_HELPER_PATH,
        pkexec_path: Path = DEFAULT_PKEXEC_PATH,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 90.0,
    ) -> None:
        self._helper_path = Path(helper_path)
        self._pkexec_path = Path(pkexec_path)
        self._command_runner = command_runner
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._processes: set[subprocess.Popen[str]] = set()
        self._processes_lock = RLock()

    @property
    def installed(self) -> bool:
        return bool(
            self._helper_path.is_file()
            and os.access(self._helper_path, os.X_OK)
            and self._pkexec_path.is_file()
            and os.access(self._pkexec_path, os.X_OK)
        )

    def measure(self, game: Game) -> CompressionMeasurement:
        if not self.installed:
            raise PrivilegedMeasurementError(
                "The privileged compression measurement helper is not installed"
            )
        command = self._measurement_command(game)
        logger.info("Privileged measurement helper argv=%r", command)
        try:
            if self._command_runner is subprocess.run:
                completed = self._run_tracked(command)
            else:
                completed = self._command_runner(
                    command,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self._timeout_seconds,
                    shell=False,
                )
        except (OSError, subprocess.SubprocessError) as error:
            raise PrivilegedMeasurementError(
                f"Privileged compression measurement could not start: {error}"
            ) from error
        helper_log = str(completed.stderr or "").strip()
        if helper_log:
            logger.info("Privileged measurement helper log: %s", helper_log)
        if completed.returncode != 0:
            detail = helper_log
            try:
                failed = json.loads(str(completed.stdout or ""))
            except json.JSONDecodeError:
                failed = {}
            if isinstance(failed, Mapping) and failed.get("measurement_error"):
                detail = str(failed["measurement_error"])
            if completed.returncode in {126, 127} and not detail:
                detail = "Authentication was cancelled or denied"
            raise PrivilegedMeasurementError(
                detail
                or f"Privileged measurement exited with status {completed.returncode}",
                exit_code=int(completed.returncode),
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
            )
        try:
            payload = json.loads(str(completed.stdout or ""))
        except json.JSONDecodeError as error:
            raise PrivilegedMeasurementError(
                "The privileged helper returned invalid JSON",
                exit_code=int(completed.returncode),
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
            ) from error
        if not isinstance(payload, Mapping):
            raise PrivilegedMeasurementError(
                "The privileged helper returned an invalid result",
                exit_code=int(completed.returncode),
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
            )
        try:
            self._validate_identity(game, payload)
        except PrivilegedMeasurementError as error:
            raise PrivilegedMeasurementError(
                str(error),
                exit_code=int(completed.returncode),
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
            ) from error
        if payload.get("ok") is not True:
            raise PrivilegedMeasurementError(
                str(payload.get("measurement_error") or "")
                or "The privileged helper did not complete the compsize measurement",
                exit_code=int(completed.returncode),
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
            )
        measurement = self.measurement_from_payload(payload)
        if (
            not measurement.compsize_disk_bytes
            or not measurement.compsize_uncompressed_bytes
            or not measurement.compsize_referenced_bytes
        ):
            raise PrivilegedMeasurementError(
                "The privileged helper returned an incomplete compsize result",
                exit_code=int(completed.returncode),
                stdout=str(completed.stdout or ""),
                stderr=str(completed.stderr or ""),
            )
        if measurement.logical_bytes <= 0:
            known_logical_bytes = max(
                0,
                int(float(game.logical_size_gb) * 1_000_000_000),
            )
            if known_logical_bytes > 0:
                measurement = replace(
                    measurement,
                    logical_bytes=known_logical_bytes,
                )
        return measurement

    def _measurement_command(self, game: Game) -> list[str]:
        command = [
            os.fspath(self._pkexec_path),
            os.fspath(self._helper_path),
            "measure",
        ]
        if game.library_path is not None and game.steam_app_id and game.steam_build_id:
            return [
                *command,
                "--library",
                os.fspath(game.library_path.resolve(strict=True)),
                "--appid",
                str(game.steam_app_id),
                "--buildid",
                str(game.steam_build_id),
            ]
        try:
            game_path = game.install_path.resolve(strict=True)
            identity = game_path.stat()
        except OSError as error:
            raise PrivilegedMeasurementError(
                f"The selected game directory is unavailable: {error}"
            ) from error
        if not game_path.is_dir():
            raise PrivilegedMeasurementError(
                "The selected game path is not a directory"
            )
        return [
            *command,
            "--game-path",
            os.fspath(game_path),
            "--game-id",
            str(game.id),
            "--device",
            str(identity.st_dev),
            "--inode",
            str(identity.st_ino),
        ]

    def cancel_all(self) -> None:
        """Terminate complete pkexec/helper process groups started by this client."""

        with self._processes_lock:
            processes = tuple(self._processes)
        for process in processes:
            self._terminate_group(process, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._terminate_group(process, signal.SIGKILL)

    def _run_tracked(
        self,
        command: list[str],
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
        )
        with self._processes_lock:
            self._processes.add(process)
        try:
            try:
                stdout, stderr = process.communicate(timeout=self._timeout_seconds)
            except subprocess.TimeoutExpired:
                self._terminate_group(process, signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self._terminate_group(process, signal.SIGKILL)
                    stdout, stderr = process.communicate()
                raise PrivilegedMeasurementError(
                    "Privileged compression measurement timed out"
                )
            return subprocess.CompletedProcess(
                command,
                int(process.returncode or 0),
                stdout,
                stderr,
            )
        finally:
            with self._processes_lock:
                self._processes.discard(process)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.send_signal(sig)
            except OSError:
                return

    @staticmethod
    def measurement_from_payload(
        payload: Mapping[str, Any],
    ) -> CompressionMeasurement:
        compsize = payload.get("compsize")
        compsize_map = compsize if isinstance(compsize, Mapping) else {}
        btrfs_du = payload.get("btrfs_filesystem_du")
        du_map = btrfs_du if isinstance(btrfs_du, Mapping) else {}
        measured_raw = payload.get("measured_at")
        try:
            measured_at = datetime.fromisoformat(str(measured_raw))
        except (TypeError, ValueError):
            measured_at = datetime.now(UTC)
        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=UTC)

        def optional_int(source: Mapping[str, Any], key: str) -> int | None:
            value = source.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
            return None

        disk = optional_int(compsize_map, "disk_usage_bytes")
        state = str(du_map.get("state") or "unknown")
        if state not in {"detected", "not_detected", "unknown"}:
            state = "unknown"
        error = str(payload.get("measurement_error") or "").strip() or None
        return CompressionMeasurement(
            logical_bytes=max(0, int(payload.get("logical_bytes") or 0)),
            # Kept for backward-compatible history schema only. GUI physical
            # usage reads compsize_disk_bytes and never this fallback.
            physical_bytes=disk or 0,
            exclusive_bytes=optional_int(du_map, "exclusive_bytes"),
            shared_bytes=optional_int(du_map, "set_shared_bytes"),
            compsize_disk_bytes=disk,
            compsize_uncompressed_bytes=optional_int(
                compsize_map, "uncompressed_bytes"
            ),
            compsize_referenced_bytes=optional_int(
                compsize_map, "referenced_bytes"
            ),
            scan_complete=True,
            shared_extent_state=state,
            filesystem_available_bytes=optional_int(
                payload, "filesystem_available_bytes"
            ),
            filesystem_free_bytes=optional_int(payload, "filesystem_free_bytes"),
            filesystem_used_bytes=optional_int(payload, "filesystem_used_bytes"),
            filesystem_total_bytes=optional_int(payload, "filesystem_total_bytes"),
            measurement_source="polkit_helper",
            measurement_error=error,
            measured_at=measured_at,
        )

    @staticmethod
    def _validate_identity(game: Game, payload: Mapping[str, Any]) -> None:
        expected_path = os.path.realpath(os.fspath(game.install_path))
        returned_path = os.path.abspath(str(payload.get("game_path") or ""))
        common_valid = bool(
            int(payload.get("schema_version") or 0) == 1
            and returned_path == expected_path
            and payload.get("read_only") is True
            and str(payload.get("measurement_source") or "") == "polkit_helper"
        )
        if game.library_path is not None and game.steam_app_id and game.steam_build_id:
            identity_valid = bool(
                str(payload.get("identity_kind") or "steam") == "steam"
                and str(payload.get("app_id") or "") == str(game.steam_app_id)
                and str(payload.get("build_id") or "") == str(game.steam_build_id)
            )
        else:
            try:
                identity = Path(expected_path).stat()
                identity_valid = bool(
                    str(payload.get("identity_kind") or "") == "local"
                    and str(payload.get("game_id") or "") == str(game.id)
                    and int(payload.get("path_device")) == identity.st_dev
                    and int(payload.get("path_inode")) == identity.st_ino
                )
            except (OSError, TypeError, ValueError):
                identity_valid = False
        if not common_valid or not identity_valid:
            raise PrivilegedMeasurementError(
                "The privileged measurement identity could not be verified"
            )


__all__ = [
    "DEFAULT_HELPER_PATH",
    "DEFAULT_PKEXEC_PATH",
    "PrivilegedMeasurementClient",
    "PrivilegedMeasurementError",
    "measurement_delta",
]
