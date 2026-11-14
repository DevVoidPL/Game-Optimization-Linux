"""One asynchronous LXB-based Steam connection for Desktop and Couch."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
import time

from ..config import STATE_DIR
from ..models import Launcher
from ..services.host_bootstrap import host_home_directory
from ..services.steam_integration import SteamIntegration, SteamIntegrationError
from ..services.steam_integration import steam_root_for


class SteamIntegrationController:
    def __init__(self, app, service=None):
        self.app = app
        self.service = service or SteamIntegration(STATE_DIR / "steam-integration", home=host_home_directory(),
                                                   recover_on_init=False)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="steam-integration")
        self.future = None
        self.game_id = ""
        self.cache = {}
        self.checked = {}
        self.operation = ""

    def _drain(self):
        if self.future is not None and self.future.done():
            try:
                self.cache[self.game_id] = self.future.result()
            except Exception as error:
                self.cache[self.game_id] = {"kind": "unknown", "error": str(error), "success": False}
            self.checked[self.game_id] = time.monotonic()
            self.future = None
            self.operation = ""

    def status(self, game_id):
        self._drain()
        game = self.app._resolve_game(game_id, show_error=False)
        if game is None or game.launcher is not Launcher.STEAM:
            return {"kind": "unsupported", "busy": False}
        if self.future is None and time.monotonic() - self.checked.get(game_id, 0) > 5:
            self.game_id = game_id
            self.future = self.executor.submit(self._status, game)
        return {**self.cache.get(game_id, {"kind": "checking"}), "busy": self.future is not None,
                "operation": self.operation}

    def _status(self, game):
        runner = self.app._runner_integration
        source = "Steam Flatpak" if game.launcher_variant == "flatpak" or game.data_source == "Steam Flatpak" else "Steam"
        data = self.service.status(str(game.steam_app_id), game.library_path, source, runner.path)
        return {"kind": data["kind"], "raw": data.get("raw", ""), "error": data.get("error", ""),
                "canRestore": data.get("record") is not None, "runnerInstalled": runner.status().installed,
                "command": runner.steam_command(str(game.steam_app_id))}

    def submit(self, game_id, action, confirmed):
        self._drain()
        game = self.app._resolve_game(game_id, show_error=False)
        if (confirmed is not True or self.future is not None or game is None
                or game.launcher is not Launcher.STEAM or action not in {"apply", "restore"}):
            return False
        self.game_id, self.operation = game_id, action
        self.cache.pop(game_id, None)
        self.future = self.executor.submit(self._change, game, action)
        return True

    def _change(self, game, action):
        # Directory flock also excludes a second Desktop/Couch process.
        directory = self.service.state_dir
        directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SteamIntegrationError("Another GameOpti Steam operation is running") from error
            source = "Steam Flatpak" if game.launcher_variant == "flatpak" or game.data_source == "Steam Flatpak" else "Steam"
            _steam_root, sandboxed = steam_root_for(game.library_path, source, self.service.home)
            if sandboxed:
                source = "Steam Flatpak"
            libraries = {g.library_path for g in self.app._domain_games.values() if g.library_path is not None}
            runner = self.app._runner_integration
            if action == "apply":
                self.service.assert_runner_access(runner.path, flatpak=sandboxed)
            state = self.service.steam_state(source, libraries)
            if state["games"] or state["downloads"]:
                raise SteamIntegrationError("Close running games and finish Steam downloads before setup")
            # Explicit confirmation in either UI authorizes graceful shutdown,
            # never kill, permission changes, or termination of a game.
            if state["running"]:
                self.service.close_steam(source, libraries)
            # Recovery may restore a prior write: it too must happen offline.
            self.service.recover()
            if action == "apply":
                self.service.apply(str(game.steam_app_id), game.library_path, source, runner.path, take_over=False)
            else:
                self.service.restore(str(game.steam_app_id), game.library_path, source)
            result = self._status(game)
            if action == "apply" and result["kind"] != "lxb":
                raise SteamIntegrationError("The Steam Launch Options write could not be verified")
            return {**result, "success": True, "lastAction": action,
                    "steamClosed": state["running"]}
        finally:
            os.close(descriptor)

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)