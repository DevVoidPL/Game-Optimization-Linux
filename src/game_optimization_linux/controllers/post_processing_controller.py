"""Nonblocking controller API shared by Desktop and Couch."""
from concurrent.futures import ThreadPoolExecutor
import time
from PySide6.QtCore import QUrl
from ..services.post_processing_catalog import EFFECTS


class PostProcessingController:
    def __init__(self, app):
        self.app = app
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="post-processing")
        self.jobs = {}
        self.queries = {}
        self.cache = {}
        self.checked = {}
        self.serial = {}
        self.completed = {}
        self.operation_errors = {}

    def busy(self, game_id):
        return game_id in self.jobs and not self.jobs[game_id].done()

    def _cache_status(self, game_id, result):
        previous = self.cache.get(game_id, {})
        evidence = previous.get("runtimeEvidence", {})
        diagnostics = result.get("diagnostics", {})
        if (evidence and result.get("installed") and not result.get("runtimeErrors")
                and result.get("statusState") != "error"
                and evidence.get("configuredAt") == diagnostics.get("configuredAt")
                and evidence.get("sessionAt") == diagnostics.get("sessionAt", "")):
            result = {**result, "runtimeLoaded": True, "statusState": "runtime_loaded",
                      "runtimeEvidence": evidence}
        self.cache[game_id] = result

    def _drain(self, game_id):
        query = self.queries.get(game_id)
        if query is not None and query.done():
            self.queries.pop(game_id)
            try:
                self._cache_status(game_id, query.result())
            except Exception as error:
                self.cache[game_id] = {**self.cache.get(game_id, {}), "success": False,
                                       "error": str(error), "statusState": "error"}
            self.checked[game_id] = time.monotonic()
        job = self.jobs.get(game_id)
        if job is not None and job.done():
            self.jobs.pop(game_id)
            try:
                self._cache_status(game_id, job.result())
                self.operation_errors.pop(game_id, None)
            except Exception as error:
                self.operation_errors[game_id] = str(error)
            self.completed[game_id] = self.serial.get(game_id, 0)
            self.checked[game_id] = time.monotonic()

    def status(self, game_id):
        self._drain(game_id)
        if not self.busy(game_id) and game_id not in self.queries and time.monotonic() - self.checked.get(game_id, 0) > 5:
            game = self.app._resolve_game(game_id, show_error=False)
            if game:
                self.queries[game_id] = self.executor.submit(self.app._post_processing_service.status, game)
        return {"backend": "off", "catalogue": [{"id": k, **v} for k, v in EFFECTS.items()],
                **self.cache.get(game_id, {}), "busy": self.busy(game_id),
                "checking": game_id in self.queries, "operationId": self.serial.get(game_id, 0),
                "completedOperationId": self.completed.get(game_id, 0),
                "operationError": self.operation_errors.get(game_id, "")}

    def _submit(self, game_id, action, *arguments):
        self._drain(game_id)
        game = self.app._resolve_game(game_id, show_error=False)
        if game is None or self.busy(game_id): return False
        if any(gid == game_id and not future.done()
               for future, _cancel, gid in self.app._optiscaler_jobs.values()):
            return False
        self.serial[game_id] = self.serial.get(game_id, 0) + 1
        self.operation_errors.pop(game_id, None)
        self.jobs[game_id] = self.executor.submit(action, game, *arguments)
        return True

    def apply(self, game_id, values):
        return self._submit(game_id, self.app._post_processing_service.apply, dict(values))

    def import_preset(self, game_id, path):
        url = QUrl(path)
        local = url.toLocalFile() if url.isLocalFile() else path
        return self._submit(game_id, self.app._post_processing_service.import_preset, local)

    def import_lut(self, game_id, path):
        url = QUrl(path)
        local = url.toLocalFile() if url.isLocalFile() else path
        return self._submit(game_id, self.app._post_processing_service.import_lut, local)

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
