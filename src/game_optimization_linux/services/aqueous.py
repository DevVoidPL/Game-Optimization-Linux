"""Optional Desktop scope for Aqueous's rules.toml / -c contract.

Contract reference: Seafoam-Labs/Aqueous 9e8baae, docs/rules.md and
compositor/aqueous/main.zig. No installation, session replacement or IPC guesses.
GameOpti owns a separate rules file; manual compositor configuration is untouched.
"""
from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

if __package__:
    from ..config import GAMES_CONFIG_DIR
    from ..models import validate_game_key
    from .optimization_profiles import _atomic_write
else:  # reviewed, standalone stdlib-only host bridge
    GAMES_CONFIG_DIR = Path.home() / ".config/game-optimization-linux/games"

    def validate_game_key(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.%+-]{1,200}", value) or value in {".", ".."}:
            raise ValueError("Invalid game key")
        return value

    def _atomic_write(path, text):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

SYSTEM_EXECUTABLE = Path("/usr/bin/aqueous")
INSTALL_URL = "https://github.com/Seafoam-Labs/Aqueous#installation"
_lock = threading.RLock()
_process = None
_temporary = None


def executable() -> str:
    return shutil.which("aqueous") or (str(SYSTEM_EXECUTABLE) if SYSTEM_EXECUTABLE.is_file() and os.access(SYSTEM_EXECUTABLE, os.X_OK) else "")


def is_available() -> bool:
    if os.environ.get("FLATPAK_ID"):
        try:
            return subprocess.run(["flatpak-spawn", "--host", "aqueous", "-version"],
                                  capture_output=True, timeout=3, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return bool(executable())


def runner_supported() -> bool:
    return not (getattr(sys, "frozen", False) or "__compiled__" in globals())


def ensure_host_bridge():
    from .host_bootstrap import host_home_directory
    target = host_home_directory(os.environ) / ".local/share/game-optimization-linux/bin/aqueous-host.py"
    if target.is_symlink():
        raise ValueError("Aqueous host bridge must not be a symlink")
    source = Path(__file__).read_text()
    if not target.exists() or target.read_text() != source:
        _atomic_write(target, source)
    return target


def window_observation(app_id, *, environment=None):
    """Read upstream window-info JSON, without guessing a window-to-PID mapping."""
    unavailable = {"available": False, "focused": None, "pidCorrelation": "unavailable", "observedAt": time.time()}
    try:
        result = subprocess.run(["aqueousctl", "windows", "--json"], capture_output=True,
                                text=True, timeout=2, check=False, env=environment)
        if result.returncode:
            return unavailable
        windows = json.loads(result.stdout)
        if not isinstance(windows, list):
            return unavailable
        matched = [window for window in windows if isinstance(window, dict) and
                   app_id in (window.get("app_id"), window.get("class"))]
        focused = any("focused" in window.get("states", []) for window in matched) if matched else None
        return {**unavailable, "available": True, "focused": focused, "matcher": app_id,
                "windows": [{key: window.get(key) for key in
                    ("id", "app_id", "class", "output", "workspace", "layout", "states")} for window in matched]}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return unavailable


def get_version() -> str:
    binary = executable()
    if not binary:
        return ""
    try:
        result = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=3, check=False)
        return (result.stdout or result.stderr).strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _identifier(app_id: str) -> str:
    if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", app_id) or ".." in app_id:
        raise ValueError("Invalid Aqueous application matcher")
    return app_id


def _settings(settings: dict) -> dict:
    layout = settings.get("layout", "game")
    if layout not in {"game", "floating"}:
        raise ValueError("Aqueous layout must be game or floating")
    for key, default in (("fullscreen", True), ("vrr", False), ("hdr", False)):
        if type(settings.get(key, default)) is not bool:
            raise ValueError(f"{key} must be boolean")
    if settings.get("vrr") or settings.get("hdr"):
        raise ValueError("Per-app VRR/HDR is not supported by this Aqueous integration")
    value = {"layout": layout, "fullscreen": settings.get("fullscreen", True), "vrr": False, "hdr": False}
    enums = {"anchor": {"center", "top", "bottom", "left", "right"},
             "buffer_scale_policy": {"native", "integer-ceil"}}
    for key, allowed in enums.items():
        if key in settings:
            if settings[key] not in allowed:
                raise ValueError(f"Invalid Aqueous {key}")
            value[key] = settings[key]
    if settings.get("output", ""):
        output = settings["output"]
        if not isinstance(output, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", output):
            raise ValueError("Invalid output connector")
        value["output"] = output
    if "workspace" in settings:
        workspace = settings["workspace"]
        if type(workspace) is not int or not 1 <= workspace <= 1024:
            raise ValueError("Workspace must be a positive integer")
        value["workspace"] = workspace
    if "size" in settings:
        size = settings["size"]
        if not isinstance(size, str) or not (size == "native" or re.fullmatch(r"[1-9][0-9]{0,4}x[1-9][0-9]{0,4}", size)):
            raise ValueError("Size must be native or WxH")
        value["size"] = size
    return value


def generate_rules(app_id: str, settings: dict) -> str:
    app_id = _identifier(app_id)
    value = _settings(settings)
    layout = "game-mode" if value["layout"] == "game" else "float"
    # Wayland app_id and XWayland WM_CLASS are distinct matchers (OR rules).
    extra = "".join(f"{key} = {json.dumps(value[key])}\n" for key in
                    ("output", "workspace", "anchor", "size", "buffer_scale_policy") if key in value)
    return "".join(f'[[window]]\n{matcher} = {json.dumps(app_id)}\nlayout = "{layout}"\nfullscreen = {str(value["fullscreen"]).lower()}\n{extra}\n' for matcher in ("app_id", "class"))


def rules_path(root: Path = GAMES_CONFIG_DIR) -> Path:
    return Path(root) / "aqueous" / "rules.toml"


def _markers(app_id):
    token = _identifier(app_id)
    return f"# BEGIN GameOpti {token}\n", f"# END GameOpti {token}\n"


def _update_rules(app_id, settings, path):
    path = Path(path)
    with _lock:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        tomllib.loads(original)
        start, end = _markers(app_id)
        pattern = re.compile(r"^" + re.escape(start) + r".*?^" + re.escape(end), re.M | re.S)
        if (start in original or end in original) and not pattern.search(original):
            raise ValueError("Incomplete GameOpti rule markers; file left untouched")
        updated = pattern.sub("", original)
        if settings is not None:
            if updated and not updated.endswith("\n"):
                updated += "\n"
            updated += start + generate_rules(app_id, settings) + end
        tomllib.loads(updated)
        if updated != original:
            _atomic_write(path, updated)


def write_rules(app_id: str, settings: dict, *, path: Path | None = None) -> None:
    _update_rules(app_id, settings, path or rules_path())


@contextmanager
def session_rules(key, scope, *, root=GAMES_CONFIG_DIR):
    """Private nested-instance configuration; never mutate live desktop rules.

    Native buffer scaling avoids integer-ceil over-rendering. This is a supported
    rule, not GPU scheduling or proof of FPS improvement. No undocumented effects.
    """
    settings = dict(scope)
    profile = Path(root) / validate_game_key(key) / 'gpu-memory.json'
    value = json.loads(profile.read_text()) if profile.exists() else {}
    if value.get('smartScope') and value.get('smartMode') in {'balanced', 'performance'}:
        settings['buffer_scale_policy'] = 'native'
    with tempfile.TemporaryDirectory(prefix='gameopti-aqueous-rules-') as directory:
        path = Path(directory) / 'rules.toml'
        write_rules(scope['appId'], settings, path=path)
        yield path


def remove_rules(app_id: str, *, path: Path | None = None) -> None:
    _update_rules(app_id, None, path or rules_path())


def load_profile(app_id, *, root: Path = GAMES_CONFIG_DIR) -> dict:
    key = validate_game_key(app_id)
    path = Path(root) / key / "scope.json"
    value = json.loads(path.read_text()) if path.exists() else {"scope": "legacy"}
    if not isinstance(value, dict) or value.get("scope") not in {"none", "gamescope", "aqueous", "legacy"}:
        raise ValueError("Invalid scope profile")
    matcher = _identifier(value.get("appId", "steam_app_" + key if key.isdigit() else key))
    return {**_settings(value), "scope": value["scope"], "rulesPath": str(rules_path(root)), "appId": matcher}


def save_profile(app_id, settings, *, root: Path = GAMES_CONFIG_DIR) -> dict:
    key = validate_game_key(app_id)
    scope = settings.get("scope", "none")
    if scope not in {"none", "gamescope", "aqueous"}:
        raise ValueError("Invalid scope selection")
    matcher = _identifier(settings.get("appId") or ("steam_app_" + key if key.isdigit() else key))
    value = {**_settings(settings), "scope": scope, "appId": matcher}
    with _lock:
        path = rules_path(root)
        original = path.read_text() if path.exists() else None
        old_matcher = load_profile(key, root=root)["appId"]
        try:
            if old_matcher != matcher:
                remove_rules(old_matcher, path=path)
            if scope == "aqueous":
                write_rules(matcher, value, path=path)
            else:
                remove_rules(matcher, path=path)
            _atomic_write(Path(root) / key / "scope.json", json.dumps(value, indent=2) + "\n")
        except (OSError, ValueError):
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, original)
            raise
    return load_profile(key, root=root)


def select_scope(profile, *, root=GAMES_CONFIG_DIR):
    try:
        mode = (Path(root) / "aqueous" / "interface-mode").read_text().strip()
    except OSError:
        mode = "desktop"
    if mode == "couch":
        return profile, {"scope": "legacy", "active": False}, ""
    selection = load_profile(profile.app_id, root=root)
    scope = selection["scope"]
    active = scope == "aqueous" and runner_supported() and is_available() and bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    selection["active"] = active
    selection["host"] = bool(os.environ.get("FLATPAK_ID"))
    warning = ""
    if scope == "aqueous" and not active:
        warning = "Aqueous not installed, no parent display, or unsupported runner; using saved Gamescope settings or direct launch"
    if scope == "none" or active:
        profile = replace(profile, gamescope_enabled=False)
    elif scope == "gamescope":
        profile = replace(profile, gamescope_enabled=True, gamescope_mode="native" if profile.gamescope_mode == "disabled" else profile.gamescope_mode)
    return profile, selection, warning


def set_interface_mode(mode, *, root=GAMES_CONFIG_DIR):
    """Runner guard: a Desktop sidecar must never take over Couch launches."""
    if mode not in {"desktop", "couch"}:
        raise ValueError("Invalid interface mode")
    _atomic_write(Path(root) / "aqueous" / "interface-mode", mode + "\n")


def launch_nested(app_id: str, command: str, *, rules_path: Path | None = None, environment=None) -> subprocess.Popen:
    """Start -c inside the compositor's child environment, not the parent display.

    The command string is parsed as argv, then quoted again; shell operators are
    never interpreted. A private completion file captures the game's exit code.
    """
    global _process, _temporary
    _identifier(app_id)
    argv = shlex.split(command)
    if not argv:
        raise ValueError("Empty game command")
    with _lock:
        if _process is not None:
            raise RuntimeError("A GameOpti nested instance is already owned")
        binary = executable()
        if not binary:
            raise FileNotFoundError("Aqueous not installed")
        env = dict(os.environ if environment is None else environment)
        if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
            raise ValueError("Nested Aqueous requires a parent Wayland or X11 display")
        env["WLR_BACKENDS"] = "wayland" if env.get("WAYLAND_DISPLAY") else "x11"
        # Same nested policy hints as upstream launch_aqueous.sh.
        env["AQUEOUS_NESTED"] = "1"
        env["AQUEOUS_MOD"] = "Alt"
        if rules_path is not None:
            env["AQUEOUS_RULES"] = str(Path(rules_path).resolve())
        temporary = tempfile.TemporaryDirectory(prefix="gameopti-aqueous-")
        directory = Path(temporary.name)
        # -c starts only after Aqueous has established its own display environment.
        # Record the init process group for cleanup: Aqueous creates a separate one.
        script = f'printf "%s\\n" "$$" > {shlex.quote(str(directory / "child"))}; sleep 0.5; printf "1\\n" > {shlex.quote(str(directory / "started"))}; {shlex.join(argv)}; result=$?; printf "%s\\n" "$result" > {shlex.quote(str(directory / "completed"))}; exit "$result"'
        # Query only this nested compositor, never the parent's display/socket.
        display_file = shlex.quote(str(directory / "display"))
        script = f'printf "%s\\n%s\\n%s\\n" "$WAYLAND_DISPLAY" "${{AQUEOUS_SOCKET-}}" "${{AQUEOUS_INSTANCE-}}" > {display_file}; ' + script
        try:
            process = subprocess.Popen([binary, "-c", script], env=env, start_new_session=True, shell=False)
        except BaseException:
            temporary.cleanup()
            raise
        _process, _temporary = process, temporary
        return process


def wait_nested(*, timeout=None, heartbeat=None, start_timeout=15) -> int:
    with _lock:
        process, temporary = _process, _temporary
    if process is None:
        raise RuntimeError("No owned nested process")
    deadline = time.monotonic() + timeout if timeout else None
    start_deadline = time.monotonic() + start_timeout
    while True:
        completed = Path(temporary.name) / "completed"
        if completed.exists():
            text = completed.read_text().strip()
            if text:
                return int(text)
        if process.poll() is not None:
            raise OSError("Aqueous exited before the game reported completion")
        if not (Path(temporary.name) / "started").exists() and time.monotonic() >= start_deadline:
            raise TimeoutError("Aqueous game did not start before the startup deadline")
        if deadline and time.monotonic() >= deadline:
            raise TimeoutError("Nested game did not finish")
        if heartbeat:
            heartbeat()
        time.sleep(0.1)


def stop_nested() -> None:
    global _process, _temporary
    with _lock:
        if _process is None:
            return
        try:
            child_file = Path(_temporary.name) / "child"
            if child_file.exists():
                try:
                    child = int(child_file.read_text().strip())
                    if child > 1 and os.getpgid(child) == child:
                        os.killpg(child, signal.SIGTERM)
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            if _process.poll() is None:
                _process.terminate()
                try:
                    _process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _process.kill()
                    _process.wait(timeout=3)
            # Clean up our own group only (never pkill a user's compositor).
            try:
                os.killpg(_process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        finally:
            _temporary.cleanup()
            _process = _temporary = None


def wrap_plan(plan, scope, app_id, *, root=GAMES_CONFIG_DIR):
    """Native-only wrapper retaining the original command suffix and hooks."""
    if not scope.get("active"):
        return plan
    prefix = (["/usr/bin/python3", str(ensure_host_bridge())] if scope.get("host") else
              [sys.executable, "-m", "game_optimization_linux.services.aqueous"])
    prefix += [str(app_id), str(root), "--"]
    command = [*prefix, *plan.command]
    return replace(plan, executable=command[0], arguments=tuple(command[1:]), wrappers=("Aqueous", *plan.wrappers))


def sample_vram() -> dict:
    """Point samples only: never mistake post-exit VRAM for in-game peak VRAM."""
    binary = shutil.which("nvidia-smi")
    if binary:
        try:
            result = subprocess.run([binary, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3, check=False)
            values = [float(line.strip()) for line in result.stdout.splitlines()]
            if result.returncode == 0 and values:
                return {"available": True, "source": "nvidia-smi", "gpus": [{"index": i, "memoryUsedMiB": v} for i, v in enumerate(values)]}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    # AMD's read-only sysfs counter is per DRM device, without rocm-smi text parsing.
    values = []
    for path in sorted(Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_used")):
        try:
            values.append({"device": path.parents[1].name, "memoryUsedMiB": int(path.read_text()) / 1048576})
        except (OSError, ValueError):
            pass
    return {"available": bool(values), "source": "amdgpu-sysfs", "gpus": values} if values else {"available": False, "gpus": []}


def record_history(app_id, payload, *, root=GAMES_CONFIG_DIR):
    path = Path(root) / validate_game_key(app_id) / "scope-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(json.dumps({"recordedAt": datetime.now(UTC).isoformat(), **payload}) + "\n")


def last_history(app_id, *, root=GAMES_CONFIG_DIR):
    path = Path(root) / validate_game_key(app_id) / "scope-history.jsonl"
    try:
        # Bound the read even after many sessions.
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 65536))
            lines = handle.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def fps_from_log(path, *, since):
    from .performance_analysis import MangoHudLogParser
    try:
        path = Path(path)
        if path.stat().st_mtime < since:
            return None
        measurement = MangoHudLogParser().parse(path)
        return {"averageFps": measurement.average_fps, "onePercentLowFps": measurement.one_percent_low_fps,
                "samples": measurement.samples, "source": str(path), "limitations": list(measurement.limitations)}
    except (OSError, ValueError):
        return None


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4 or args[2] != "--":
        print("Aqueous wrapper requires APP_ID ROOT -- COMMAND", file=sys.stderr)
        return 2
    app_id, root = validate_game_key(args[0]), Path(args[1])
    command = args[3:]
    before = sample_vram()
    started_at = time.time()
    result = 2
    previous = {}
    rules_context = None
    try:
        scope = load_profile(app_id, root=root)
        if not is_available():
            print("Aqueous not installed; direct launch", file=sys.stderr)
            result = subprocess.call(command, shell=False)
            return result
        rules_context = session_rules(app_id, scope, root=root)
        temporary_rules = rules_context.__enter__()
        def interrupted(number, _frame):
            raise InterruptedError(f"Aqueous launch interrupted by signal {number}")
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, interrupted)
        launch_nested(scope["appId"], shlex.join(command), rules_path=temporary_rules)
        next_observation = 0

        def observe_window():
            nonlocal next_observation
            if time.monotonic() < next_observation:
                return
            next_observation = time.monotonic() + 2
            display = Path(_temporary.name) / "display"
            if not display.exists():
                return
            values = display.read_text().splitlines()
            if len(values) < 3 or not values[0]:
                return
            env = dict(os.environ)
            env.update(WAYLAND_DISPLAY=values[0], AQUEOUS_SOCKET=values[1], AQUEOUS_INSTANCE=values[2])
            observation = window_observation(scope["appId"], environment=env)
            _atomic_write(root / app_id / "scope-window.json", json.dumps(observation) + "\n")

        result = wait_nested(heartbeat=observe_window)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Aqueous: {error}", file=sys.stderr)
    finally:
        stop_nested()
        if rules_context is not None:
            rules_context.__exit__(None, None, None)
        _atomic_write(root / app_id / "scope-window.json", json.dumps({
            "available": False, "focused": None, "state": "finished", "observedAt": time.time()}) + "\n")
        for number, handler in previous.items():
            signal.signal(number, handler)
        try:
            # Reuse real current MangoHud recordings only, never an old baseline.
            if __package__:
                from .performance_session import BaselineSessionRepository
                log = BaselineSessionRepository().newest_log(app_id)
            else:
                log = None  # host bridge has no GUI/parser dependencies
            fps = fps_from_log(log, since=started_at) if log else None
            record_history(app_id, {"scope": "aqueous", "before": before, "after": sample_vram(), "exitCode": result, "fps": fps,
                           "sampleTiming": "before-start/after-exit", "fpsSource": "Current Performance Analysis MangoHud recording, when present"}, root=root)
        except (OSError, ImportError) as error:
            print(f"Aqueous history unavailable: {error}", file=sys.stderr)
    return result


if __name__ == "__main__":
    raise SystemExit(main())