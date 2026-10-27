"""Install missing Vulkan-layer tools for a Flatpak launcher (Heroic).

The launcher's own sandbox decides whether MangoHud or Gamescope can run for
its games. When the saved profile asks for one of them and the launcher's
sandbox lacks it, this service offers the matching Flatpak extension:

* refs come only from an explicit whitelist; arch and branch are read from
  the launcher's installed ref and its runtime's extension point (no
  hard-coded branch);
* ``remote-info`` must confirm the ref before installation is offered;
* the extension is installed in the launcher's own scope (user or system;
  for system Flatpak/Polkit asks for authorisation graphically);
* everything that talks to the host runs on a background worker;
* success means a fresh probe inside the launcher sandbox finds the tool.

Nothing here changes the game profile or the launcher configuration.
vkBasalt is only listed as a future integration and is never installed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Lock
import time
from typing import Any

logger = logging.getLogger(__name__)

EXTENSION_POINT = "org.freedesktop.Platform.VulkanLayer"
TOOLS: dict[str, tuple[str, str]] = {
    "mangohud": ("MangoHud", f"{EXTENSION_POINT}.MangoHud"),
    "gamescope": ("Gamescope", f"{EXTENSION_POINT}.gamescope"),
}
FUTURE_TOOLS: dict[str, tuple[str, str]] = {
    "vkbasalt": ("vkBasalt", f"{EXTENSION_POINT}.vkBasalt"),
}
_INSPECTION_TTL_SECONDS = 300.0

_POLKIT_MARKERS = (
    "not allowed for user", "not authorized", "authorization", "polkit",
    "interaction required", "operation was cancelled", "dismissed",
)
_NETWORK_MARKERS = (
    "could not resolve", "error resolving", "timeout was reached", "could not connect",
    "network", "failed to connect", "connection refused", "temporary failure",
)
_MISSING_REF_MARKERS = ("nothing matches", "no remote refs found", "not found", "no such ref")


def classify_install_failure(output: str) -> tuple[str, str]:
    """(kind, message) for a failed ``flatpak install`` (C locale output)."""
    text = str(output or "").casefold()
    if any(marker in text for marker in _POLKIT_MARKERS):
        return "polkit", "Authorization was denied or cancelled; nothing was installed"
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "network", "The Flatpak remote could not be reached; check the network connection"
    if any(marker in text for marker in _MISSING_REF_MARKERS):
        return "missing_ref", "The Flatpak remote does not provide this extension"
    last = next((line for line in reversed(str(output or "").splitlines()) if line.strip()), "")
    return "error", f"flatpak install failed: {last or 'no diagnostic'}"


@dataclass(slots=True)
class _Inspection:
    checked_at: float
    error: str = ""
    scope: str = ""
    arch: str = ""
    branch: str = ""
    remote: str = ""
    runtime: str = ""
    refs: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class _ToolState:
    state: str  # installing | ready | restart_required | error
    message: str = ""
    kind: str = ""
    progress: str = ""
    started_at: float = 0.0


class LauncherComponentsService:
    """Per launcher Flatpak app: inspection cache and install jobs."""

    def __init__(
        self,
        host: Callable[[], Any],
        *,
        refresh_probe: Callable[[str], dict[str, Any] | None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        # Invalidate every capability cache of the launcher app and probe its
        # sandbox again (blocking; only called on the worker).
        self._refresh_probe = refresh_probe
        self._clock = clock
        self._lock = Lock()
        self._inspections: dict[tuple[str, tuple[str, ...]], _Inspection] = {}
        self._tools: dict[tuple[str, str], _ToolState] = {}
        self._jobs: dict[Any, Future[Any]] = {}
        self._executor: ThreadPoolExecutor | None = None

    # -- worker -------------------------------------------------------------
    def _submit(self, key: Any, function: Callable[..., Any], *args: Any) -> None:
        job = self._jobs.get(key)
        if job is not None and not job.done():
            return
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="game-optimization-launcher-components"
            )
        self._jobs[key] = self._executor.submit(function, *args)

    def shutdown(self) -> None:
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # -- inspection (host calls; worker only) -------------------------------
    def _inspect(self, app_id: str, tools: tuple[str, ...]) -> None:
        inspection = _Inspection(checked_at=self._clock())
        host = self._host()
        try:
            if host is None:
                raise RuntimeError("The host cannot be queried from here")
            installed = host.flatpak_app_installation(app_id)
            if not installed:
                raise RuntimeError(f"{app_id} is not installed as a Flatpak")
            parts = str(installed.get("ref", "")).split("/")
            if len(parts) != 4 or parts[0] != "app":
                raise RuntimeError("The launcher's Flatpak ref could not be read")
            inspection.scope = str(installed["scope"])
            inspection.arch = parts[2]
            inspection.remote = str(installed.get("origin") or "")
            inspection.runtime = str(installed.get("runtime") or "")
            runtime_parts = inspection.runtime.split("/")
            if len(runtime_parts) != 3:
                raise RuntimeError("The launcher's runtime could not be read")
            # The branch of the extension point in the runtime metadata;
            # the runtime branch only when the point does not state one.
            inspection.branch = (
                host.flatpak_runtime_extension_version(inspection.runtime, EXTENSION_POINT)
                or runtime_parts[2]
            )
            if not inspection.remote:
                raise RuntimeError("The launcher's Flatpak remote is unknown")
            for tool in tools:
                label, name = TOOLS[tool]
                ref = f"runtime/{name}/{inspection.arch}/{inspection.branch}"
                info = host.flatpak_extension_remote_info(inspection.scope, inspection.remote, ref)
                if info is None:
                    state, detail = "unknown", "The Flatpak remote could not be queried"
                elif info.get("returncode") == "0":
                    state, detail = "available", ""
                else:
                    kind, detail = classify_install_failure(
                        f"{info.get('stderr', '')}\n{info.get('stdout', '')}"
                    )
                    state = "not_in_remote" if kind == "missing_ref" else "unknown"
                    if kind == "error":
                        detail = (
                            f"{inspection.remote} does not list {label} for runtime "
                            f"{inspection.branch} ({inspection.arch})"
                        )
                inspection.refs[tool] = {
                    "ref": ref,
                    "remoteState": state,
                    "remoteMessage": detail,
                    "argv": " ".join(
                        host.flatpak_extension_install_argv(inspection.scope, inspection.remote, ref)
                    ),
                }
        except Exception as error:  # noqa: BLE001 - shown to the user
            inspection.error = str(error)
        with self._lock:
            self._inspections[(app_id, tools)] = inspection

    # -- installation (worker only) -----------------------------------------
    def _install(self, app_id: str, tool: str, scope: str, remote: str, ref: str,
                 launcher_running: Callable[[], bool | None], runtime: str = "") -> None:
        label = TOOLS[tool][0]
        key = (app_id, tool)
        if runtime:
            # The launcher may have moved to another runtime (25.08 -> 26.08)
            # since the cached inspection: never install the old branch.
            try:
                current = str((self._host().flatpak_app_installation(app_id) or {}).get("runtime") or "")
            except Exception:  # noqa: BLE001 - treated as unknown below
                current = ""
            if current != runtime:
                with self._lock:
                    for inspection_key in [k for k in self._inspections if k[0] == app_id]:
                        self._inspections.pop(inspection_key, None)
                self._set(key, _ToolState(
                    "error",
                    f"The launcher runtime changed ({runtime or 'unknown'} -> {current or 'unknown'}); "
                    f"{label} was not installed. Check again to get the matching extension",
                    "runtime_changed",
                ))
                return

        def progress(line: str) -> None:
            with self._lock:
                current = self._tools.get(key)
                if current is not None:
                    current.progress = line[-160:]

        host = self._host()
        try:
            result = host.install_flatpak_extension(scope, remote, ref, progress)
        except Exception as error:  # noqa: BLE001
            self._set(key, _ToolState("error", str(error), "error"))
            return
        if result.returncode != 0:
            kind, message = classify_install_failure(str(result.stdout))
            self._set(key, _ToolState("error", message, kind))
            return
        # Installed: the success is only real once the launcher sandbox has it.
        capabilities = self._refresh_probe(app_id)
        commands = set((capabilities or {}).get("commands", ()))
        extensions = set((capabilities or {}).get("extensions", ()))
        found = (
            ("MangoHud" in extensions or "mangohud" in commands)
            if tool == "mangohud"
            else "gamescope" in commands
        )
        if not found:
            self._set(key, _ToolState(
                "error",
                f"{label} was installed, but the launcher sandbox still does not provide it",
                "not_in_sandbox",
            ))
            return
        if launcher_running() is True:
            self._set(key, _ToolState(
                "restart_required",
                f"{label} is installed; restart the launcher so the running instance sees it",
            ))
        else:
            self._set(key, _ToolState("ready", f"{label} is available in the launcher sandbox"))
        with self._lock:
            # Next status call re-reads remote/scope facts.
            for inspection_key in [k for k in self._inspections if k[0] == app_id]:
                self._inspections.pop(inspection_key, None)

    def _set(self, key: tuple[str, str], state: _ToolState) -> None:
        with self._lock:
            self._tools[key] = state
        logger.info("Launcher component %s/%s: %s %s", key[0], key[1], state.state, state.message)

    # -- public API (GUI thread; never blocks) ------------------------------
    def status(self, app_id: str, needed: Sequence[str], *, launcher_label: str) -> dict[str, Any]:
        tools = tuple(sorted(tool for tool in needed if tool in TOOLS))
        with self._lock:
            touched = sorted(tool for (app, tool) in self._tools if app == app_id)
        shown = tuple(sorted(set(tools) | set(touched)))
        future = [
            {"tool": tool, "label": label, "ref": name, "note": "Not installed by GameOpti yet"}
            for tool, (label, name) in FUTURE_TOOLS.items()
        ]
        if not shown:
            return {"components": [], "future": future, "pending": False, "canRepair": False}
        key = (app_id, shown)
        with self._lock:
            inspection = self._inspections.get(key)
        if inspection is None or self._clock() - inspection.checked_at > _INSPECTION_TTL_SECONDS:
            self._submit(("inspect", key), self._inspect, app_id, shown)
            if inspection is None:
                return {
                    "components": [
                        {"tool": tool, "label": TOOLS[tool][0], "state": "checking"} for tool in shown
                    ],
                    "future": future, "pending": True, "canRepair": False,
                }
        components = []
        can_repair = False
        for tool in shown:
            label = TOOLS[tool][0]
            facts = inspection.refs.get(tool, {})
            with self._lock:
                job = self._tools.get((app_id, tool))
            if inspection.error:
                state, message = "error", inspection.error
            elif job is not None:
                state, message = job.state, job.message
            elif tool not in tools:
                state, message = "ready", f"{label} is available in the launcher sandbox"
            elif facts.get("remoteState") == "available":
                state, message = "missing", (
                    f"{label} is missing in the {launcher_label} Flatpak environment"
                )
                can_repair = True
            else:
                state = "unavailable"
                message = facts.get("remoteMessage") or "The extension cannot be installed"
            components.append({
                "tool": tool,
                "label": label,
                "ref": facts.get("ref", ""),
                "branch": inspection.branch,
                "arch": inspection.arch,
                "scope": inspection.scope,
                "remote": inspection.remote,
                "argv": facts.get("argv", ""),
                "state": state,
                "message": message,
                "kind": job.kind if job is not None else "",
                "progress": job.progress if job is not None else "",
                "polkit": inspection.scope == "system",
            })
        installing = any(item["state"] == "installing" for item in components)
        return {
            "components": components,
            "future": future,
            "pending": installing,
            "canRepair": can_repair and not installing,
            "scope": inspection.scope,
            "branch": inspection.branch,
            "runtime": inspection.runtime,
            # A --user extension for a --system launcher could not be
            # confirmed without installing; it is never used as a fallback.
            "userFallback": False,
        }

    def repair(self, app_id: str, needed: Sequence[str], *,
               launcher_running: Callable[[], bool | None]) -> list[str]:
        """Start installing every missing, remote-confirmed tool. Returns the
        tools that were started."""
        tools = tuple(sorted(tool for tool in needed if tool in TOOLS))
        with self._lock:
            touched = sorted(tool for (app, tool) in self._tools if app == app_id)
            inspection = self._inspections.get((app_id, tuple(sorted(set(tools) | set(touched)))))
        if inspection is None or inspection.error:
            return []
        started = []
        for tool in tools:
            facts = inspection.refs.get(tool, {})
            with self._lock:
                job = self._tools.get((app_id, tool))
            if facts.get("remoteState") != "available" or (job and job.state == "installing"):
                continue
            self._set((app_id, tool), _ToolState(
                "installing", f"Installing {TOOLS[tool][0]}", started_at=self._clock()
            ))
            self._submit(("install", app_id, tool), self._install, app_id, tool,
                         inspection.scope, inspection.remote, facts["ref"], launcher_running,
                         inspection.runtime)
            started.append(tool)
        return started


__all__ = ["FUTURE_TOOLS", "LauncherComponentsService", "TOOLS", "classify_install_failure"]
