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


class MangoHudLivePreview:
    """Run exactly one ``mangohud vkcube`` owned by this application."""

    def __init__(
        self,
        *,
        preview_root: Path = PREVIEW_DIRECTORY,
        environment: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        host_service: object | None = None,
        on_finished: Callable[[LivePreviewResult], None] | None = None,
    ) -> None:
        self.preview_root = Path(preview_root)
        self._environment = os.environ if environment is None else environment
        self._which = which
        self._popen = popen
        self._host_service = host_service
        self.on_finished = on_finished
        self._lock = threading.Lock()
        self._process: Any | None = None
        self._config_path: Path | None = None
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
                self._remove(config_path)
                return LivePreviewResult(
                    False, "start_failed", f"Could not start the live preview: {error}"
                )
            self._process = process
            self._config_path = config_path
            self._stopping = False
        logger.info("Started MangoHud live preview pid=%s", getattr(process, "pid", "?"))
        watcher = threading.Thread(
            target=self._watch,
            args=(process, config_path),
            name="game-optimization-mangohud-preview",
            daemon=True,
        )
        watcher.start()
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
            config_path = self._config_path
            self._stopping = True
        if process is not None:
            _terminate_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=1.0)
            except Exception:
                _terminate_group(process, signal.SIGKILL)
        if config_path is not None:
            self._remove(config_path)

    def _command(
        self, mangohud: str, vkcube: str, config_path: Path
    ) -> tuple[list[str], dict[str, str]]:
        environment = {
            key: value
            for key, value in self._environment.items()
            if key not in _CONFIG_OVERRIDE_KEYS
        }
        environment["MANGOHUD_CONFIGFILE"] = str(config_path)
        if not self.in_flatpak:
            return [mangohud, vkcube], environment
        spawn = self._which("flatpak-spawn") or "flatpak-spawn"
        sandbox_unsets = [
            f"--unset-env={key}"
            for key in _sandbox_runtime_environment_keys(self._environment)
        ]
        argv = [
            spawn,
            "--host",
            *sandbox_unsets,
            *(f"--unset-env={key}" for key in _CONFIG_OVERRIDE_KEYS),
            f"--env=MANGOHUD_CONFIGFILE={config_path}",
            mangohud,
            vkcube,
        ]
        return argv, environment

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

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("Could not remove MangoHud preview config: %s", error)

    def _watch(self, process: Any, config_path: Path) -> None:
        stderr = ""
        try:
            _stdout, stderr = process.communicate()
        except Exception as error:
            stderr = str(error)
        returncode = int(getattr(process, "returncode", 0) or 0)
        self._remove(config_path)
        with self._lock:
            stopped = self._stopping
            if self._process is process:
                self._process = None
                self._config_path = None
            self._stopping = False
        if stopped or returncode in (0, -signal.SIGTERM, 128 + signal.SIGTERM):
            result = LivePreviewResult(True, "finished", "")
        elif returncode == 127:
            result = LivePreviewResult(
                False, "vkcube_missing", "Live preview requires the vulkan-tools package"
            )
        else:
            detail = " ".join(str(stderr or "").split())[-_MAX_ERROR:]
            result = LivePreviewResult(
                False,
                "process_failed",
                f"Live preview exited with status {returncode}"
                + (f": {detail}" if detail else ""),
            )
        logger.info("MangoHud live preview finished code=%s status=%s", result.code, returncode)
        callback = self.on_finished
        if callback is not None:
            try:
                callback(result)
            except Exception:
                logger.exception("MangoHud live preview callback failed")


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


__all__ = ["LivePreviewResult", "MangoHudLivePreview", "PREVIEW_DIRECTORY"]
