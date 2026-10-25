"""Deduplicated GPU-memory queries and explicit operations; shared by both UIs."""
from concurrent.futures import ThreadPoolExecutor
import time


class GpuMemoryController:
    def __init__(self, app, service):
        self.app, self.service = app, service
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-memory")
        self.jobs, self.queries, self.cache, self.checked = {}, {}, {}, {}
        self.serial, self.completed, self.errors = {}, {}, {}

    def status(self, key):
        for collection, operation in ((self.queries, False), (self.jobs, True)):
            future = collection.get(key)
            if future is not None and future.done():
                collection.pop(key)
                try:
                    self.cache[key] = future.result()
                    if operation:
                        self.errors.pop(key, None)
                except Exception as error:
                    self.errors[key] = str(error)
                if operation:
                    self.completed[key] = self.serial[key]
                self.checked[key] = time.monotonic()
        if key not in self.jobs and key not in self.queries and time.monotonic() - self.checked.get(key, 0) > 15:
            game = self.app._resolve_game(key, show_error=False)
            if game:
                self.queries[key] = self.executor.submit(self.service.status, game)
        return {**self.cache.get(key, {}), "busy": key in self.jobs, "checking": key in self.queries,
                "operationId": self.serial.get(key, 0), "completedOperationId": self.completed.get(key, 0),
                "error": self.errors.get(key, "")}

    def submit(self, key, action, values=None):
        self.status(key)
        if key in self.jobs:
            return False
        game = self.app._resolve_game(key, show_error=False)
        if not game:
            return False
        self.serial[key] = self.serial.get(key, 0) + 1
        self.errors.pop(key, None)
        function = {"apply": self.service.apply, "setup": self.service.setup,
                    "restore-system": self.service.restore_system}[action]
        self.jobs[key] = self.executor.submit(function, game, *([dict(values)] if values is not None else []))
        return True

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
