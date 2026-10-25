"""Nonblocking component jobs and read-only status for both interfaces."""
from concurrent.futures import ThreadPoolExecutor
import copy
import time
from threading import Event
from PySide6.QtCore import QUrl

from ..services.frame_generation import DEFAULTS, profile_path


class FrameGenerationController:
    def __init__(self, app, service):
        self.app, self.service = app, service
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frame-generation")
        self.cache, self.queries, self.checked = {}, {}, {}
        self.job, self.cancel, self.job_game = None, Event(), ""
        self.serial, self.completed, self.errors = {}, {}, {}
        self.query_errors = {}
        self.progress = {}

    def _drain(self):
        for key, query in list(self.queries.items()):
            if query.done():
                self.queries.pop(key)
                try:
                    self.cache[key] = query.result()
                    self.query_errors.pop(key, None)
                except Exception as error: self.query_errors[key] = str(error)
                self.checked[key] = time.monotonic()
        if self.job is not None and self.job.done():
            key = self.job_game
            try:
                self.cache[key] = self.job.result()
                self.errors.pop(key, None)
            except Exception as error: self.errors[key] = str(error)
            self.completed[key] = self.serial.get(key, 0)
            self.job = None
            self.checked.clear()

    def status(self, game_id):
        self._drain()
        if self.job is None and game_id not in self.queries and time.monotonic() - self.checked.get(game_id, 0) > 8:
            game = self.app._resolve_game(game_id, show_error=False)
            if game: self.queries[game_id] = self.executor.submit(self.service.status, game)
        return {"backend": "off", "settings": copy.deepcopy(DEFAULTS), "defaults": copy.deepcopy(DEFAULTS),
                **self.cache.get(game_id, {}), "busy": self.job is not None,
                "checking": game_id in self.queries, "operationId": self.serial.get(game_id, 0),
                "completedOperationId": self.completed.get(game_id, 0),
                "operationError": self.errors.get(game_id, self.query_errors.get(game_id, "")), "progress": self.progress if self.job is not None else {}}

    def submit(self, game_id, action, values):
        self._drain()
        game = self.app._resolve_game(game_id, show_error=False)
        if game is None or not game.profile_key or self.job is not None: return False
        if any(not future.done() for future, *_ in self.app._optiscaler_jobs.values()): return False
        values = copy.deepcopy(dict(values))
        self.cancel = Event(); self.job_game = game_id
        self.errors.pop(game_id, None)
        self.serial[game_id] = self.serial.get(game_id, 0) + 1
        self.queries.pop(game_id, None)  # Discard a pre-operation status snapshot.
        self.progress = {"phase": action, "value": 0.0}
        self.job = self.executor.submit(self._run, game, action, values)
        return True

    def refresh(self, game_id):
        """Explicit read-only retry after Steam/Opti changes, without an operation spinner."""
        self._drain()
        self.checked.pop(game_id, None)
        self.errors.pop(game_id, None)
        self.query_errors.pop(game_id, None)
        return self.status(game_id)

    def _run(self, game, action, values):
        backend = values.get("backend", "off")
        if action in {"install", "remove"}:
            # Updating a shared component requires all known games to be closed.
            for candidate in self.app._domain_games.values():
                if candidate.install_path and self.service.post.optiscaler._process_detector(candidate.install_path):
                    raise ValueError("Close games before changing shared frame-generation components")
            if action == "install":
                self.service.artifacts.install(backend, self.cancel,
                    lambda phase, value: setattr(self, "progress", {"phase": phase, "value": value}))
            else: self.service.artifacts.remove(backend)
        elif action == "validate":
            url = QUrl(str(values.get("path", "")))
            path = url.toLocalFile() if url.isLocalFile() else str(values.get("path", ""))
            return self.service.validate_dll(game, backend, path)
        elif action in {"setup-runner", "restore-runner"}:
            self.service.post._safe(game, self.service.post.executable(game))
            from ..services.frame_generation_access import prepare
            if game.launcher_variant == "flatpak":
                if game.launcher.value == "Steam":
                    from ..services.frame_generation_runner_setup import assert_steam_closed
                    assert_steam_closed(self.service.host)
                elif self.app._launcher_integration.launcher_running(game) is not False:
                    raise ValueError("Close the launcher before changing frame-generation access")
                if action == "restore-runner": prepare(game, self.service, restore=True)
                elif backend != "off" and self.service.artifacts.status(backend)["installed"]:
                    prepare(game, self.service, backend)
            if game.launcher.value == "Steam":
                from ..services.frame_generation_runner_setup import configure_steam
                configure_steam(game, self.service.host, self.service.root, restore=action == "restore-runner")
            elif game.launcher.value in {"Heroic", "Lutris"}:
                getattr(self.app._launcher_integration, "restore" if action == "restore-runner" else "apply")(game)
            else: raise ValueError("This launcher has no supported frame-generation runner setup")
        elif action == "apply":
            path = profile_path(game.profile_key, self.service.root)
            files = [path, *(path.parent / "frame-generation").glob("*")]
            before = {p: p.read_bytes() for p in files if p.is_file()}
            try:
                self.service.apply(game, values)
                if game.launcher.value in {"Heroic", "Lutris"}:
                    self.app._launcher_integration.apply(game)
            except Exception:
                for p in {path, *(path.parent / "frame-generation").glob("*")}:
                    if p in before: p.write_bytes(before[p])
                    elif p.is_file(): p.unlink()
                raise
        else: raise ValueError("Unknown frame-generation operation")
        return self.service.status(game)

    def cancel_operation(self):
        self.cancel.set()

    def shutdown(self):
        self.cancel.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
