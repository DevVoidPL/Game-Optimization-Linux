"""Per-game dmem scopes and an opt-in, capability-checked vkd3d memory trade-off."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from ..config import GAMES_CONFIG_DIR, STATE_DIR
from ..models import validate_game_key
from .host_bootstrap import host_home_directory
from .optimization_profiles import _atomic_write
from . import gpu_memory_host


def profile_path(key, root=GAMES_CONFIG_DIR):
    return Path(root) / validate_game_key(key) / "gpu-memory.json"


def game_key(game):
    if game.profile_key:
        return validate_game_key(game.profile_key)
    if game.launcher.value == "Manual" and game.data_source.casefold() == "manual":
        # GPU-only identity: do not change keys/ownership of other integrations.
        return "local-" + hashlib.sha256(("gpu-memory:" + game.id).encode()).hexdigest()[:24]
    raise ValueError("This game has no stable GPU-memory profile identity")


def load_profile(key, root=GAMES_CONFIG_DIR):
    path = profile_path(key, root)
    if not path.exists():
        return {"priority": False, "pressure": "default", "region": "", "api": "auto"}
    result = json.loads(path.read_text())
    if not isinstance(result, dict) or result.get("pressure") not in {"default", "upload-system-memory"}:
        raise ValueError("Invalid GPU-memory profile")
    return result


def host_bridge_path():
    return host_home_directory(os.environ) / ".local/share/game-optimization-linux/bin/gpu-memory-host.py"


def ensure_host_bridge():
    target = host_bridge_path()
    source = Path(gpu_memory_host.__file__).read_text()
    if target.is_symlink():
        raise ValueError("GPU-memory host bridge must not be a symlink")
    if not target.exists() or target.read_text() != source:
        _atomic_write(target, source)
    return target


def host_call(*argv, privileged=False, timeout=30):
    bridge = ensure_host_bridge()
    if privileged:
        # Existing root-owned helper is preferred. Initial bootstrap is an explicit
        # standard pkexec authorization of the shipped, reviewable setup program.
        helper = gpu_memory_host.HELPER
        if not helper.exists() or helper.read_bytes() != bridge.read_bytes():
            helper = bridge
        command = ["pkexec", "/usr/bin/python3", str(helper), *argv]
    else:
        command = ["/usr/bin/python3", str(bridge), *argv]
    if os.environ.get("FLATPAK_ID"):
        command = ["flatpak-spawn", "--host", *command]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        value = {}
    if result.returncode or value.get("error"):
        raise ValueError(value.get("error") or result.stderr.strip() or "Host authorization was cancelled or denied")
    return value


def vkd3d_tool(argv):
    """Identify the actual launch's Proton; never use a random installed version."""
    for value in argv:
        path = Path(str(value))
        if path.name != "proton" or not path.is_file():
            continue
        root = path.parent / "files/lib/wine/vkd3d-proton"
        dll = root / "x86_64-windows/d3d12core.dll"
        if dll.is_file():
            data = dll.read_bytes()
            dll32 = root / "i386-windows/d3d12core.dll"
            data32 = dll32.read_bytes() if dll32.exists() else b""
            return {"path": str(dll), "hash": hashlib.sha256(data).hexdigest(),
                    "version": gpu_memory_host.read(root / "version"),
                    "noUploadHvv": b"no_upload_hvv\0" in data and b"no_upload_hvv\0" in data32,
                    "hash32": hashlib.sha256(data32).hexdigest() if data32 else ""}
    return {}


def pressure_environment(key, root, environment, argv):
    profile = load_profile(key, root)
    if profile["pressure"] == "default":
        return {}
    tool = vkd3d_tool(argv)
    if (not tool.get("noUploadHvv") or profile.get("toolHash") != tool.get("hash")
            or profile.get("toolHash32", "") != tool.get("hash32", "")):
        raise ValueError("The selected Proton/vkd3d changed or lacks no_upload_hvv; review GPU-memory settings")
    if profile.get("effectiveApi") != "d3d12" or not profile.get("dedicatedRebar"):
        raise ValueError("The memory-pressure profile requires D3D12/vkd3d and verified discrete GPU ReBAR")
    flags = [value for value in re.split(r"[,;]", environment.get("VKD3D_CONFIG", "")) if value]
    if "no_upload_hvv" not in flags:
        flags.append("no_upload_hvv")
    return {"VKD3D_CONFIG": ",".join(flags)}


def launch_prefix(key, root=GAMES_CONFIG_DIR, *, metrics=None):
    profile = load_profile(key, root)
    if not profile.get("priority") and not metrics:
        return []
    if not profile.get("priority") and not profile_path(key, root).exists():
        return []  # Existing recordings are unchanged until this feature is configured.
    bridge = ensure_host_bridge()
    output = profile_path(key, root).with_name("gpu-memory-session.json")
    return ["/usr/bin/python3", str(bridge), "run", "--key", str(key),
            "--region", str(profile.get("region", "")), "--output", str(output),
            "--protect", "1" if profile.get("priority") else "0",
            *(["--target-executable", profile["targetExecutable"]] if profile.get("targetExecutable") else []),
            *(["--metrics", str(metrics)] if metrics else []), "--"]


class GpuMemoryService:
    def __init__(self, post_processing, root=None):
        self.post = post_processing
        self.root = Path(root or post_processing.root)
        self.apply_launcher = None

    @staticmethod
    def supported_launcher(game):
        return ((game.launcher.value == "Steam" and game.launcher_variant != "flatpak") or
                (game.launcher.value in {"Heroic", "Lutris"} and game.launcher_variant != "flatpak") or
                (game.launcher.value == "Manual" and game.data_source.casefold() == "manual"))

    def _command(self, game):
        try:
            report = json.loads((STATE_DIR / "launch-reports" / f"{game_key(game)}.json").read_text())
            return report.get("steamCommand", [])
        except (OSError, ValueError):
            return []

    def measurements(self, game, same_scene=False):
        from .performance_session import BaselineSessionRepository
        sessions = BaselineSessionRepository()
        result = {}
        for slot in ("before", "after"):
            measurement = sessions.load_measurement(game_key(game), slot=slot)
            if measurement is None:
                continue
            path = Path(measurement.source_path).parent.parent / "gpu-memory-summary.json"
            memory = {}
            if path.is_file() and path.stat().st_size < 65536:
                memory = json.loads(path.read_text())
            result[slot] = {"memory": memory, "summaryPath": str(path),
                "fps": measurement.average_fps, "p95": measurement.p95_frametime_ms,
                "p99": measurement.p99_frametime_ms, "samples": measurement.samples,
                "valid": measurement.available, "source": measurement.source_path}
            if same_scene:
                result[slot]["sampledHitches"] = sampled_hitches(Path(measurement.source_path))
        before, after = result.get("before", {}), result.get("after", {})
        a, b = before.get("memory", {}), after.get("memory", {})
        def observed(memory):
            counts = memory.get("targetProtection", {})
            if counts.get("unknown", 0):
                return "unknown"
            if counts.get("protected", 0) >= 10 and not counts.get("unprotected", 0):
                return "protected"
            if counts.get("unprotected", 0) >= 10 and not counts.get("protected", 0):
                return "unprotected"
            return "unknown"
        protection_a, protection_b = observed(a), observed(b)
        target_verified = bool(a.get("targetExecutable") and a.get("targetExecutable") == b.get("targetExecutable")
                               and a.get("targetSamples", 0) >= 10 and b.get("targetSamples", 0) >= 10)
        protection_contrast = target_verified and {protection_a, protection_b} == {"protected", "unprotected"}
        allocator_contrast = (target_verified and a.get("targetNoUploadHvv") is not None
                              and b.get("targetNoUploadHvv") is not None
                              and a["targetNoUploadHvv"] != b["targetNoUploadHvv"]
                              and protection_a == protection_b != "unknown")
        matched = bool(same_scene and a.get("key") == b.get("key") == game_key(game)
                       and a.get("samples", 0) >= 10 and b.get("samples", 0) >= 10
                       and a.get("createdAt") != b.get("createdAt")
                       and (protection_contrast or allocator_contrast)
                        and before.get("valid") and after.get("valid")
                       and before.get("source") != after.get("source"))
        improved = bool(matched and all(before.get(k) and after.get(k) for k in ("fps", "p95", "p99"))
                        and after["fps"] >= before["fps"] * 1.05
                        and after["p99"] <= before["p99"] * .95
                        and after["p95"] <= before["p95"] * 1.05
                        and not a.get("errors") and not b.get("errors")
                        and not a.get("kernelGpuErrors") and not b.get("kernelGpuErrors")
                        and a.get("exitCode") == b.get("exitCode") == 0)
        return {**result, "sameSceneConfirmed": bool(same_scene), "matched": matched,
                "protectionBefore": protection_a, "protectionAfter": protection_b,
                "protectionContrast": bool(protection_contrast), "allocatorContrast": bool(allocator_contrast),
                "sameProtectionState": bool(target_verified and protection_a == protection_b != "unknown"),
                "measuredImprovement": improved,
                "criteria": "FPS >= +5%, p99 <= -5%, p95 <= +5%; one pair is not a statistical guarantee"}

    def status(self, game):
        key = game_key(game)
        profile = load_profile(key, self.root)
        system = host_call("probe")
        tool = vkd3d_tool(self._command(game))
        api = "unknown"
        executable = ""
        try:
            from .post_processing import inspect_binary, load_profile as post_profile
            executable = (str(game.executable_path) if not game.profile_key and game.launcher.value == "Manual"
                          else str(self.post.executable(game)))
            api = inspect_binary(Path(executable))["api"]
            saved = post_profile(key, self.root).get("effectiveApi")
            if api == "unknown" and saved:
                api = saved
        except (OSError, ValueError, RuntimeError):
            pass
        gpus = system["gpus"]
        rebar = any(g.get("type") == "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU" and g.get("vramTotal")
                    and (g.get("visibleVram") or 0) >= g["vramTotal"] * .9 for g in gpus)
        session = {}
        path = profile_path(key, self.root).with_name("gpu-memory-session.json")
        if path.exists():
            session = json.loads(path.read_text())
            pid = int(session.get("pid", 0))
            if pid > 0 and session.get("status") not in {"exited", "starting"}:
                current = host_call("snapshot", "--pid", str(pid), "--region", profile.get("region", ""))
                if current.get("startTicks") != session.get("startTicks"):
                    session = {**session, "status": "process_exited", "protectionVerified": False}
                else:
                    session = {**session, **current}
        # The wrapper PID alone proves only its scope. Match the actual selected
        # executable and its cgroup using the existing host process evidence.
        games = []
        host = self.post.host
        if executable and host:
            for process in host.game_process_modules(Path(executable).name):
                pid = int(process.get("processId", process.get("pid", 0)))
                if pid and any(str(m).startswith(str(game.install_path) + "/") for m in process.get("modules", [])):
                    sample = host_call("snapshot", "--pid", str(pid), "--region", profile.get("region", ""))
                    sample["vkd3dLoaded"] = any(Path(m).name.lower() in {"d3d12.dll", "d3d12core.dll"} for m in process.get("modules", []))
                    expected = session.get("cgroup", "")
                    sample["scopeMatches"] = bool(expected and (sample["cgroup"] == expected or sample["cgroup"].startswith(expected + "/")))
                    games.append(sample)
        ready = bool(system["readyRegions"] and system["delegated"] and system["systemBooster"]["active"] == "active"
                     and system["userBooster"]["active"] == "active")
        effective = bool(games) and all(g["protectionVerified"] for g in games)
        observations = [gpu_memory_host.protection_observation(g, profile.get("region", "")) for g in games]
        runtime_state = ("protected" if effective else "unprotected" if observations and
                         all(v == "unprotected" for v in observations) else "unverified" if games else "not_running")
        pressure_effective = any(g["vkd3dLoaded"] and g["vkd3dNoUploadHvv"] for g in games)
        configured = bool(profile["priority"] or profile["pressure"] != "default")
        failed = bool(configured and session.get("status") == "exited" and session.get("exitCode", 0) != 0
                      and session.get("createdAt", "") >= profile.get("configuredAt", ""))
        state = "error" if failed else "effective" if profile["priority"] and effective else "pressure_applied" if configured and pressure_effective else "configured" if configured else "off"
        return {"success": True, "profile": profile, "system": system, "tool": tool,
                "detectedApi": api, "executable": executable, "ready": ready,
                "pressureSupported": bool(rebar and tool.get("noUploadHvv")), "dedicatedRebar": rebar,
                "state": state, "session": session, "gameProcesses": games,
                "runtimeProtectionVerified": effective, "failedLaunch": failed,
                "runtimeState": runtime_state,
                "protectionOrigin": ("managed_scope" if effective and all(g["scopeMatches"] for g in games)
                                     else "independent" if effective else "unverified"),
                "observedAt": datetime.now(UTC).isoformat(),
                "runtimePressureVerified": pressure_effective,
                "scopeMismatch": bool(profile["priority"] and games and any(not g["scopeMatches"] for g in games)),
                "launchSupported": self.supported_launcher(game),
                "measurements": self.measurements(game),
                "measuredImprovement": False,
                "missingMetrics": ["Per-process Vulkan budget (not exposed through /proc)",
                                   "A matched off/on scene comparison is required"]}

    def apply(self, game, values):
        # A stopped game avoids silently moving existing GPU allocations.
        if self.post.optiscaler._process_detector(game.install_path):
            raise ValueError("Close the game before changing GPU-memory launch settings")
        before = self.status(game)
        priority = values.get("priority") is True
        pressure = str(values.get("pressure", "default"))
        api = str(values.get("api", "auto"))
        region = str(values.get("region", ""))
        if api not in {"auto", "d3d12", "d3d11", "vulkan", "opengl"} or pressure not in {"default", "upload-system-memory"}:
            raise ValueError("Unsupported GPU-memory profile")
        if priority and (not before["ready"] or region not in before["system"]["readyRegions"]):
            raise ValueError("Prepare the host and select an exposed GPU memory region first")
        effective_api = before["detectedApi"] if api == "auto" else api
        if pressure != "default" and (not before["pressureSupported"] or effective_api != "d3d12"):
            raise ValueError("UPLOAD system-memory profile requires verified Proton/vkd3d, D3D12 and discrete ReBAR GPU")
        if not self.supported_launcher(game) and (priority or pressure != "default"):
            raise ValueError("The launcher Flatpak cannot access the host systemd scope. Use a native launcher or independent Plasma/Gamescope protection; no fake activation was saved")
        if game.launcher.value != "Steam" and pressure != "default":
            raise ValueError("The actual Proton/vkd3d launch version is not established for this launcher; its default allocator is preserved")
        profile = {"priority": priority, "pressure": pressure, "api": api, "effectiveApi": effective_api,
                   "region": region, "dedicatedRebar": before["dedicatedRebar"],
                   "toolHash": before["tool"].get("hash", ""), "toolVersion": before["tool"].get("version", ""),
                   "toolHash32": before["tool"].get("hash32", ""),
                   "targetExecutable": before.get("executable", ""),
                   "configuredAt": datetime.now(UTC).isoformat()}
        path = profile_path(game_key(game), self.root)
        previous = path.read_text() if path.exists() else None
        _atomic_write(path, json.dumps(profile, indent=2) + "\n")
        try:
            if game.launcher.value in {"Heroic", "Lutris"} and self.apply_launcher:
                self.apply_launcher(game)
        except Exception:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
            raise
        return self.status(game)

    def setup(self, game):
        host_call("setup", privileged=True, timeout=240)
        return self.status(game)

    def restore_system(self, game):
        if self.status(game)["gameProcesses"]:
            raise ValueError("Close the protected game before restoring system setup")
        host_call("restore-system", privileged=True, timeout=60)
        return self.status(game)


def sampled_hitches(path):
    """MangoHud sampled frametime spikes, explicitly not a count of all frames."""
    import csv
    from statistics import median
    from .performance_analysis import MangoHudLogParser
    try:
        if path.stat().st_size > 128 * 1024 * 1024:
            return None
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        index, delimiter = MangoHudLogParser._header(lines)
        if index is None:
            return None
        reader = csv.DictReader(lines[index:], delimiter=delimiter)
        heading = MangoHudLogParser._column(reader.fieldnames or (), ("frametime", "frametimems", "frametime_ms"))
        if not heading:
            return None
        values = []
        for row in reader:
            try:
                number = float(row[heading])
                if number > 0:
                    values.append(number)
            except (ValueError, TypeError):
                pass
        threshold = max(50, median(values) * 2) if values else 50
        return {"samples": len(values), "spikes": sum(v > threshold for v in values), "thresholdMs": threshold}
    except (OSError, ValueError):
        return None
