"""On-demand real MangoHud preview: ``mangohud vkcube`` with a temporary config.

The preview never touches the saved profile or the user's global MangoHud
files.  It is started only by an explicit user action, runs at most one
process, and removes its temporary configuration when the process ends.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
from typing import Any

from game_optimization_linux.config import CACHE_DIR

from .steam_launch import _sandbox_runtime_environment_keys

logger = logging.getLogger(__name__)

PREVIEW_DIRECTORY = CACHE_DIR / "mangohud-preview"
# Variables that could make MangoHud read another configuration than ours.
_CONFIG_OVERRIDE_KEYS = ("MANGOHUD_CONFIG", "MANGOHUD_PRESETSFILE")
_MAX_ERROR = 300


@dataclass(frozen=True, slots=True)
class LivePreviewResult:
    success: bool
    code: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "code": self.code, "message": self.message}


class OwnedHostProcess:
    """Exactly one explicitly started host process owned by this application.

    Commands are argv lists (never a shell).  Only the process group started
    here can be terminated, and temporary files are removed when it ends.
    """

    # Result code when the process exits with 127 (command not found).
    missing_code = "command_missing"
    missing_message = "A required command was not found"
    thread_name = "game-optimization-host-preview"

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        host_service: object | None = None,
        on_finished: Callable[[LivePreviewResult], None] | None = None,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._which = which
        self._popen = popen
        self._host_service = host_service
        self.on_finished = on_finished
        self._lock = threading.Lock()
        self._process: Any | None = None
        self._cleanup: tuple[Path, ...] = ()
        self._stopping = False

    @property
    def in_flatpak(self) -> bool:
        return bool(str(self._environment.get("FLATPAK_ID", "")).strip())

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None

    def _host_command(self, name: str) -> str:
        """Return the host command for ``name`` or ``""`` when it is missing."""

        query = getattr(self._host_service, "host_command_available", None)
        if self.in_flatpak:
            if not callable(query):
                return ""
            try:
                return name if query(name) else ""
            except Exception:
                return ""
        return self._which(name) or ""

    def _host_argv(
        self,
        command: list[str],
        *,
        set_environment: Mapping[str, str],
        unset_environment: tuple[str, ...],
    ) -> tuple[list[str], dict[str, str]]:
        environment = {
            key: value
            for key, value in self._environment.items()
            if key not in unset_environment
        }
        environment.update(set_environment)
        if not self.in_flatpak:
            return list(command), environment
        spawn = self._which("flatpak-spawn") or "flatpak-spawn"
        sandbox_unsets = [
            f"--unset-env={key}"
            for key in _sandbox_runtime_environment_keys(self._environment)
        ]
        argv = [
            spawn,
            "--host",
            *sandbox_unsets,
            *(f"--unset-env={key}" for key in unset_environment),
            *(f"--env={key}={value}" for key, value in sorted(set_environment.items())),
            *command,
        ]
        return argv, environment

    def _start_locked(
        self, argv: list[str], environment: dict[str, str], cleanup: tuple[Path, ...]
    ) -> LivePreviewResult:
        try:
            process = self._popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                close_fds=True,
                shell=False,
                start_new_session=True,
                env=environment,
            )
        except OSError as error:
            for path in cleanup:
                self._remove(path)
            return LivePreviewResult(
                False, "start_failed", f"Could not start the process: {error}"
            )
        self._process = process
        self._cleanup = cleanup
        self._stopping = False
        logger.info("Started %s pid=%s", self.thread_name, getattr(process, "pid", "?"))
        threading.Thread(
            target=self._watch,
            args=(process, cleanup),
            name=self.thread_name,
            daemon=True,
        ).start()
        return LivePreviewResult(True, "started", "")

    def stop(self) -> bool:
        """Terminate only the process group this object started."""

        with self._lock:
            process = self._process
            if process is None:
                return False
            self._stopping = True
        _terminate_group(process, signal.SIGTERM)
        return True

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            cleanup = self._cleanup
            self._stopping = True
        if process is not None:
            _terminate_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=1.0)
            except Exception:
                _terminate_group(process, signal.SIGKILL)
        for path in cleanup:
            self._remove(path)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("Could not remove preview file: %s", error)

    def _watch(self, process: Any, cleanup: tuple[Path, ...]) -> None:
        stderr = ""
        try:
            _stdout, stderr = process.communicate()
        except Exception as error:
            stderr = str(error)
        returncode = int(getattr(process, "returncode", 0) or 0)
        for path in cleanup:
            self._remove(path)
        with self._lock:
            stopped = self._stopping
            if self._process is process:
                self._process = None
                self._cleanup = ()
            self._stopping = False
        if stopped or returncode in (0, -signal.SIGTERM, 128 + signal.SIGTERM):
            result = LivePreviewResult(True, "finished", "")
        elif returncode == 127:
            result = LivePreviewResult(False, self.missing_code, self.missing_message)
        else:
            detail = " ".join(str(stderr or "").split())[-_MAX_ERROR:]
            result = LivePreviewResult(
                False,
                "process_failed",
                f"Process exited with status {returncode}"
                + (f": {detail}" if detail else ""),
            )
        logger.info("%s finished code=%s status=%s", self.thread_name, result.code, returncode)
        callback = self.on_finished
        if callback is not None:
            try:
                callback(result)
            except Exception:
                logger.exception("Preview process callback failed")


class MangoHudLivePreview(OwnedHostProcess):
    """Run exactly one ``mangohud vkcube`` owned by this application."""

    missing_code = "vkcube_missing"
    missing_message = "Live preview requires the vulkan-tools package"
    thread_name = "game-optimization-mangohud-preview"

    def __init__(self, *, preview_root: Path = PREVIEW_DIRECTORY, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.preview_root = Path(preview_root)

    def start(self, config_text: str) -> LivePreviewResult:
        with self._lock:
            if self._process is not None:
                return LivePreviewResult(
                    False, "already_running", "A live preview is already running"
                )
            mangohud = self._host_command("mangohud")
            if not mangohud:
                return LivePreviewResult(
                    False, "mangohud_missing", "MangoHud is not installed"
                )
            vkcube = self._host_command("vkcube")
            if not vkcube:
                return LivePreviewResult(
                    False, "vkcube_missing", "Live preview requires the vulkan-tools package"
                )
            try:
                config_path = self._write_config(config_text)
            except OSError as error:
                return LivePreviewResult(
                    False, "config_failed", f"Could not prepare the preview: {error}"
                )
            argv, environment = self._command(mangohud, vkcube, config_path)
            return self._start_locked(argv, environment, (config_path,))

    def _command(
        self, mangohud: str, vkcube: str, config_path: Path
    ) -> tuple[list[str], dict[str, str]]:
        return self._host_argv(
            [mangohud, vkcube],
            set_environment={"MANGOHUD_CONFIGFILE": str(config_path)},
            unset_environment=_CONFIG_OVERRIDE_KEYS,
        )

    def _write_config(self, config_text: str) -> Path:
        # The app's XDG cache is mounted at the same absolute path on the host,
        # unlike the sandbox-private /tmp, so the host MangoHud can read it.
        self.preview_root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix="preview-", suffix=".conf", dir=self.preview_root
        )
        path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(config_text)
            os.chmod(path, 0o600)
        except OSError:
            self._remove(path)
            raise
        return path


class GamescopeTestRun(OwnedHostProcess):
    """Run exactly one ``gamescope [args] -- vkcube`` started by the user."""

    missing_code = "command_missing"
    missing_message = "Gamescope or vkcube was not found on the host"
    thread_name = "game-optimization-gamescope-test"
    # Gamescope draws MangoHud itself with --mangoapp; never a second layer.
    _UNSET = ("MANGOHUD", "MANGOHUD_CONFIG", "MANGOHUD_PRESETSFILE")

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._run = runner
        self._vkcube_arguments: tuple[str, ...] | None = None

    def _vkcube_extra_arguments(self, vkcube: str) -> tuple[str, ...]:
        """``--wsi xcb`` when the local vkcube lists it.

        Games in nested Gamescope normally present through XWayland; vkcube
        would otherwise pick Wayland and exit at once without --expose-wayland.
        """

        if self._vkcube_arguments is not None:
            return self._vkcube_arguments
        text = ""
        if self.in_flatpak:
            query = getattr(self._host_service, "host_command_help", None)
            if callable(query):
                try:
                    text = str(query("vkcube") or "")
                except Exception:
                    text = ""
        else:
            try:
                result = self._run(
                    [vkcube, "--help"], check=False, capture_output=True,
                    text=True, timeout=5, stdin=subprocess.DEVNULL,
                )
                text = f"{result.stdout or ''}\n{result.stderr or ''}"
            except (OSError, subprocess.SubprocessError):
                text = ""
        supported = "--wsi" in text and "xcb" in text
        self._vkcube_arguments = ("--wsi", "xcb") if supported else ()
        return self._vkcube_arguments

    def start(
        self,
        gamescope: str,
        flags: list[str] | tuple[str, ...],
        environment: Mapping[str, str] | None = None,
    ) -> LivePreviewResult:
        values = [str(value) for value in flags]
        if "--" in values or any("\0" in value for value in values):
            return LivePreviewResult(False, "invalid", "Invalid Gamescope arguments")
        with self._lock:
            if self._process is not None:
                return LivePreviewResult(
                    False, "already_running", "A Gamescope test is already running"
                )
            command = "gamescope" if self.in_flatpak else str(gamescope or "")
            if not command or (self.in_flatpak and not self._host_command("gamescope")):
                return LivePreviewResult(
                    False, "gamescope_missing", "Gamescope is not installed"
                )
            vkcube = self._host_command("vkcube")
            if not vkcube:
                return LivePreviewResult(
                    False, "vkcube_missing", "The Gamescope test requires the vulkan-tools package"
                )
            argv, process_environment = self._host_argv(
                [command, *values, "--", vkcube, *self._vkcube_extra_arguments(vkcube)],
                set_environment=dict(environment or {}),
                unset_environment=self._UNSET,
            )
            return self._start_locked(argv, process_environment, ())


def _terminate_group(process: Any, sig: signal.Signals) -> None:
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


__all__ = [
    "GamescopeTestRun", "LivePreviewResult", "MangoHudLivePreview",
    "OwnedHostProcess", "PREVIEW_DIRECTORY",
]
