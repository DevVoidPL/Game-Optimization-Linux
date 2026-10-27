"""Per-game frame generation: profiles, prerequisites and launch contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import re
import struct

from ..config import GAMES_CONFIG_DIR, STATE_DIR
from ..models.mangohud import validate_game_key
from .frame_generation_artifacts import FrameGenerationArtifacts, LAYER_NAMES, VERSIONS, file_hash
from .host_bootstrap import host_home_directory
from .frame_generation_isolation import GATE_KEYS, backend_for_layer, discover_layers, isolation_plan, selected_blocker
from .mangohud import _atomic_write
from .post_processing import inspect_binary

DEFAULTS = {
    "lsfg": {"multiplier": 2, "flow_scale": 1.0, "performance_mode": False, "allow_fp16": True,
             "pacing": "vsync", "preserve_swapchain_image_count": False},
    "mako": {"multiplier": 2, "flow_scale": 0.8, "performance_mode": False, "allow_fp16": True,
             "adaptive": False, "target_fps": 120, "adaptive_max_multiplier": 3,
             "adaptive_auto_base_fps_cap": False, "base_fps_cap": 0,
             "adaptive_stable_cadence": True, "swapchain_image_count_compatibility": False},
    "afmf": {"performance_mode": "auto", "search_mode": "auto", "fast_motion_response": "blend",
             "pacing": True, "governor": True, "min_fps": 30, "hud_detect": True},
}
# Keys a frame-generation plan may set. Disable gates are added only for a
# concrete activation (see frame_generation_isolation); MAKO spatial scaling
# is an upscaler and has no entry here.
ALLOWED_ENV = {*GATE_KEYS, "ENABLE_MAKO", "AFMF_ENABLE", "LSFGVK_CONFIG", "LSFGVK_PROFILE", "MAKO_CONFIG",
               "MAKO_PROFILE", "MAKO_LAUNCH_CONFIG", "VK_ADD_IMPLICIT_LAYER_PATH", "VK_INSTANCE_LAYERS",
               "VK_LOADER_LAYERS_DISABLE", "VK_LOADER_LAYERS_ENABLE", "VK_LOADER_LAYERS_ALLOW", "AFMF_LOG",
               "AFMF_PERFORMANCE_MODE", "AFMF_SEARCH_MODE", "AFMF_FAST_MOTION_RESPONSE", "AFMF_PACING",
               "AFMF_GOVERNOR", "AFMF_MIN_FPS", "AFMF_HUD_DETECT", "AFMF_GAMESCOPE", "LSFGVK_ENV", "MAKO_ENV"}


@dataclass(frozen=True)
class FrameGenerationLaunch:
    """Resolved per-game contract: environment, wrapper and the reason for each guard."""
    environment: dict
    prefix: list
    backend: str = ""  # "" = no GameOpti frame-generation profile for this game
    entries: tuple = ()
    warnings: tuple = ()
    detected: tuple = ()

    def report(self) -> dict:
        return {"backend": self.backend, "entries": [dict(x) for x in self.entries],
                "warnings": list(self.warnings), "detected": [dict(x) for x in self.detected]}


def profile_path(key, root=GAMES_CONFIG_DIR):
    return Path(root) / validate_game_key(key) / "frame-generation.json"


def load_profile(key, root=GAMES_CONFIG_DIR):
    path = profile_path(key, root)
    value = json.loads(path.read_text()) if path.is_file() else {"backend": "off", "settings": {}}
    if value.get("backend") not in {"off", *DEFAULTS}: raise ValueError("Invalid frame-generation backend")
    return {**value, "settings": {k: {**v, **value.get("settings", {}).get(k, {})} for k, v in DEFAULTS.items()}}


def checked_settings(backend, values):
    result = {**DEFAULTS[backend]}
    if not isinstance(values, dict): raise ValueError("Invalid frame-generation settings")
    for name, value in values.items():
        if name not in result: raise ValueError(f"Unknown {backend} setting: {name}")
        default = result[name]
        if type(default) is bool:
            if type(value) is not bool: raise ValueError(f"Invalid boolean: {name}")
        elif type(default) in (int, float):
            if type(value) not in (int, float) or not math.isfinite(value): raise ValueError(f"Invalid number: {name}")
            bounds = {"multiplier": (2, 5 if backend == "mako" else 20), "flow_scale": (0.25, 1),
                      "target_fps": (10, 1000), "adaptive_max_multiplier": (2, 5),
                      "base_fps_cap": (0, 1000), "min_fps": (0, 240)}
            low, high = bounds[name]
            if not low <= value <= high or (type(default) is int and int(value) != value):
                raise ValueError(f"Out of range: {name}")
            value = type(default)(value)
        else:
            choices = {"performance_mode": {"auto", "quality", "performance"},
                       "search_mode": {"auto", "standard", "high"}, "fast_motion_response": {"blend", "repeat"},
                       "pacing": {"vsync"}}
            if value not in choices[name]: raise ValueError(f"Invalid choice: {name}")
        result[name] = value
    return result


def render_config(backend, key, executable, dll, settings, log_path):
    # JSON strings and booleans are valid TOML basic strings and booleans.
    lines = ["version = 2", "[global]", "dll = " + json.dumps(dll),
             "allow_fp16 = " + json.dumps(settings["allow_fp16"])]
    if backend == "lsfg":
        lines += ['log_level = "info"', "log_file = " + json.dumps(str(log_path))]
    lines += ["[[profile]]", "name = " + json.dumps("gameopti-" + key),
              "active_in = [" + json.dumps(Path(executable).name) + "]"]
    if backend == "mako":
        lines += ["frame_generation_provisioned = true", "frame_generation_enabled = true",
                  "scaling_enabled = false", 'pacing = "none"']
    for name, value in settings.items():
        if name != "allow_fp16": lines.append(name + " = " + json.dumps(value))
    return "\n".join(lines) + "\n"


def session_evidence(key, state, root=STATE_DIR):
    """Only attribute a runner log to the exact saved configuration/session."""
    result = {"sessionAt": "", "log": "", "errors": [], "generationVerified": False}
    report = Path(root) / "launch-reports" / (validate_game_key(key) + ".json")
    try:
        payload = json.loads(report.read_text())
        if (payload.get("frameGenerationBackend") != state["backend"] or not state.get("configuredAt")
                or payload.get("frameGenerationConfiguredAt") != state["configuredAt"]
                or payload.get("status") in {"plan_verified", "preflight_failed"}): return result
        path = Path(payload.get("frameGenerationLog", ""))
        if not path.is_absolute() or not path.is_file() or path.is_symlink(): return result
        text = path.read_bytes()[-2 * 1024 * 1024:].decode("utf-8", "replace")
        result.update(sessionAt=payload.get("timestamp", ""), log=str(path))
        lines = [line for line in text.splitlines() if any(marker in line.lower() for marker in (
            "[afmf", "(lsfg-vk)", "[mako", "(mako)", "mako renderer:", "mako-launch:"))]
        result["errors"] = [line for line in lines if any(marker in line.lower() for marker in (
            "interpolation unavailable", "[error]", "[afmf error]", "failed to", "unable to", "vulkan error",
            "cannot be enabled", "not installed"))][-12:]
        # AFMF also counts repeated frames as generated when shaders are unavailable.
        # Require both initialized interpolation and nonzero submitted generated frames.
        result["generationVerified"] = bool(state["backend"] == "afmf" and not result["errors"]
            and "interpolation ready:" in text and re.search(r"destroyed after \d+ presents: [1-9]\d* generated", text))
    except (OSError, ValueError, KeyError): pass
    return result


def launch_settings(key, root=GAMES_CONFIG_DIR, *, inherited=None, gamescope=False):
    """Compatibility wrapper: (environment, wrapper prefix)."""
    resolved = resolve_launch(key, root, inherited=inherited, gamescope=gamescope)
    return resolved.environment, resolved.prefix


def resolve_launch(key, root=GAMES_CONFIG_DIR, *, inherited=None, gamescope=False) -> FrameGenerationLaunch:
    """Read only. Invalid selected integration blocks launch instead of falling back.

    Off never activates a generator. A guard is added only for a discovered
    generator with a concrete request in ``inherited``; each guard has a reason.
    """
    if not profile_path(key, root).exists(): return FrameGenerationLaunch({}, [])
    state = load_profile(key, root)
    backend = state["backend"]
    inherited = {str(k): str(v) for k, v in (inherited or {}).items()}
    isolation = isolation_plan(inherited, discover_layers(inherited), selected=backend)
    environment = dict(isolation["environment"])
    def result(prefix):
        return FrameGenerationLaunch(environment, prefix, backend, tuple(isolation["entries"]),
                                     tuple(isolation["warnings"]), tuple(isolation["detected"]))
    if backend == "off":
        return result([])
    if isolation["warnings"]:
        # Stacking two generators is a documented conflict for a selected backend.
        raise ValueError("\n".join(isolation["warnings"]))
    components = FrameGenerationArtifacts(state.get("componentRoot"))
    executable = Path(state.get("executable", ""))
    if state.get("executable") and (not executable.is_file() or (backend == "afmf" and inspect_binary(executable)["bits"] != 64)):
        raise ValueError("Selected executable is missing or incompatible with the saved frame generator")
    # Recheck mutable sibling integrations at launch, not only at Apply.
    opti_path = Path(root) / key / "optiscaler.json"
    opti = json.loads(opti_path.read_text()) if opti_path.is_file() else {}
    from .post_processing import load_profile as post_profile
    pp = post_profile(key, root)
    if backend == "afmf" and opti.get("installation_state") in {"installed", "modified", "missing"}:
        raise ValueError("afmf-linux upstream does not support OptiScaler injectors. Preserve OptiScaler and choose lsfg-vk/MAKO, or restore it explicitly on its own page")
    if backend == "mako":
        optimization_path = Path(root) / key / "optimization.json"
        optimization = json.loads(optimization_path.read_text()) if optimization_path.is_file() else {}
        if optimization.get("gamescope_hdr") or str((inherited or {}).get("DXVK_HDR", "")) == "1":
            raise ValueError("This MAKO integration uses its supported SDR launcher. Disable HDR explicitly or select another generator; GameOpti will not lower it silently")
        if pp.get("backend") == "reshade":
            raise ValueError("MAKO + ReShade loader coexistence has not been verified; select Off on one page and Apply")
        hud_path = Path(root) / key / "mangohud.json"
        hud = json.loads(hud_path.read_text()) if hud_path.is_file() else {}
        if hud.get("enabled") and state.get("launcherFlatpak"):
            raise ValueError("Standalone MAKO's host MangoHud integration is unavailable inside a launcher Flatpak. Disable the in-game HUD explicitly or select another generator")
        if pp.get("backend") == "vkbasalt" and pp.get("effects") and hud.get("enabled"):
            raise ValueError("MAKO supports vkBasalt or MangoHud in the game, not both. Disable one in its page, or select lsfg-vk")
        if hud.get("enabled") and (host_home_directory() / ".config/mako-render/mangohud.conf").is_file():
            raise ValueError("MAKO's launcher would select its independent MangoHud configuration. Preserve it and select another generator, or disable the in-game HUD explicitly")
    if str((inherited or {}).get("PROTON_USE_WINED3D", "")) == "1":
        raise ValueError("Selected frame generator requires Vulkan; PROTON_USE_WINED3D=1 selects OpenGL")
    status = components.status(backend)
    if not status["installed"]: raise ValueError(status.get("error", "Selected frame generator is not installed"))
    directory = components.directory(backend)
    config = profile_path(key, root).parent / "frame-generation" / (backend + ".toml")
    if backend in {"lsfg", "mako"}:
        dll = Path(state.get("dlls", {}).get(backend, ""))
        receipt = state.get("validation", {}).get(backend, {})
        if not dll.is_file() or file_hash(dll) != receipt.get("sha256") or receipt.get("componentVersion") != status["version"]:
            raise ValueError("Lossless Scaling files changed or were not validated for this backend")
        if not config.is_file(): raise ValueError("Per-game frame-generation configuration is missing")
    if backend == "lsfg":
        environment.pop("DISABLE_LSFGVK", None)
        environment.update(LSFGVK_CONFIG=str(config),
                           VK_ADD_IMPLICIT_LAYER_PATH=str(directory / "share/vulkan/implicit_layer.d"))
        prefix = ["env", "-u", "DISABLE_LSFGVK", "-u", "LSFGVK_ENV", "-u", "LSFGVK_PROFILE"]
    elif backend == "mako":
        environment.pop("DISABLE_MAKO", None)
        environment.update(ENABLE_MAKO="1", MAKO_CONFIG=str(config),
                           MAKO_LAUNCH_CONFIG=str(config.with_suffix(".launcher.conf")))
        prefix = ["env", "-u", "DISABLE_MAKO", "-u", "MAKO_ENV", "-u", "MAKO_PROFILE",
                  "-u", "MAKO_PROFILE_FALLBACK", "-u", "MAKO_FOLLOW_CURRENT_PROFILE", str(directory / "bin/mako-launch")]
    else:
        settings = checked_settings(backend, state["settings"][backend])
        environment.pop("DISABLE_AFMF", None)
        environment.update(AFMF_ENABLE="1", AFMF_LOG="2",
                           VK_ADD_IMPLICIT_LAYER_PATH=str(directory / "share/vulkan/implicit_layer.d"))
        for name, value in settings.items():
            environment["AFMF_" + name.upper()] = str(int(value)) if type(value) is bool else str(value)
        if gamescope: environment["AFMF_GAMESCOPE"] = "1"
        prefix = ["env", "-u", "DISABLE_AFMF"]
    blocker = selected_blocker(backend, {**inherited, **environment})
    if blocker:
        raise ValueError(blocker)
    return result(prefix)


class FrameGenerationService:
    def __init__(self, post_processing, artifacts=None):
        self.post = post_processing
        self.root = post_processing.root
        self.host = post_processing.host
        self.artifacts = artifacts or FrameGenerationArtifacts()
        self.launcher_status = None

    def _save(self, key, state):
        _atomic_write(profile_path(key, self.root), json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    def discover_dlls(self):
        from ..providers.keyvalues import load_keyvalues
        home = host_home_directory()
        roots = [home / ".local/share/Steam", home / ".steam/steam", home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
        libraries = set(roots)
        for root in roots:
            try:
                folders = load_keyvalues(root / "steamapps/libraryfolders.vdf").get("libraryfolders", {})
                for item in folders.values():
                    if isinstance(item, dict) and item.get("path"): libraries.add(Path(item["path"]))
            except (OSError, ValueError): pass
        return {b: sorted({str(lib / "steamapps/common/Lossless Scaling" / name) for lib in libraries
                          if (lib / "steamapps/common/Lossless Scaling" / name).is_file()})
                for b, name in (("lsfg", "lsfg-vk.dll"), ("mako", "Lossless.dll"))}

    def external_installations(self):
        if self.host and hasattr(self.host, "frame_generation_external_layers"):
            return self.host.frame_generation_external_layers()
        home = host_home_directory()
        roots = [home / ".local/share/vulkan/implicit_layer.d", Path("/usr/share/vulkan/implicit_layer.d"),
                 Path("/etc/vulkan/implicit_layer.d")]
        result = []
        for root in roots:
            for path in root.glob("*.json"):
                try:
                    layer = json.loads(path.read_text()).get("layer", {})
                    if backend_for_layer(layer.get("name", "")):
                        result.append({"path": str(path), "name": layer.get("name"), "disable": layer.get("disable_environment", {})})
                except (OSError, ValueError): pass
        return result

    def validate_dll(self, game, backend, path):
        if backend not in {"lsfg", "mako"}: raise ValueError("This backend does not use Lossless Scaling")
        self.post._safe(game, self.post.executable(game))
        path = Path(path).expanduser().resolve(strict=True)
        expected = "lsfg-vk.dll" if backend == "lsfg" else "Lossless.dll"
        if path.name.casefold() != expected.casefold() or inspect_binary(path)["format"] != "PE":
            raise ValueError(f"This backend requires {expected}; the other Lossless Scaling branch is not interchangeable")
        component = self.artifacts.status(backend)
        if not component["installed"]: raise ValueError("Install the selected component before validating its proprietary models")
        binary = self.artifacts.directory(backend) / "bin" / ("lsfg-vk-cli" if backend == "lsfg" else "mako-cli")
        arguments = (["benchmark", "-d", str(path), "-w", "128", "-h", "128", "-t", "1"] if backend == "lsfg"
                     else ["inspect-dll", "--dll", str(path), "--lsfg"])
        result = self.host.frame_generation_command(binary, arguments)
        if result is None or result.returncode != 0:
            raise ValueError("Lossless Scaling model validation failed: " + (result.stderr[-1800:] if result else "host bridge unavailable"))
        data = path.read_bytes()
        version = "unknown"
        offset = data.find(b"\xbd\x04\xef\xfe")
        if offset >= 0 and offset + 16 < len(data) and struct.unpack_from("<I", data, offset + 4)[0] == 0x10000:
            major, minor = struct.unpack_from("<II", data, offset + 8)
            version = f"{major >> 16}.{major & 65535}.{minor >> 16}.{minor & 65535}"
        state = load_profile(game.profile_key, self.root)
        state.setdefault("dlls", {})[backend] = str(path)
        state.setdefault("validation", {})[backend] = {"sha256": file_hash(path), "componentVersion": component["version"],
            "fileVersion": version, "validatedAt": datetime.now(UTC).isoformat(), "output": result.stdout[-1800:]}
        self._save(game.profile_key, state)
        return self.status(game)

    def compatibility(self, game, backend, executable, binary):
        errors = []
        if backend == "off": return errors
        if backend == "afmf" and binary.get("bits") != 64: errors.append("afmf-linux 1.3.1 has no published 32-bit layer")
        if binary.get("api") == "opengl": errors.append("This integration requires Vulkan, DXVK or VKD3D-Proton; OpenGL is not enabled automatically")
        from .post_processing import load_profile as post_profile
        pp = post_profile(game.profile_key, self.root)
        opti = self.post.optiscaler.profile_repository.load(game.profile_key)
        if backend == "afmf" and opti.installation_state in {"installed", "modified", "missing"}:
            errors.append("afmf-linux upstream does not support OptiScaler injectors. Preserve OptiScaler and choose lsfg-vk/MAKO, or restore it explicitly on its own page")
        if backend == "mako":
            optimization_path = Path(self.root) / game.profile_key / "optimization.json"
            optimization = json.loads(optimization_path.read_text()) if optimization_path.is_file() else {}
            if optimization.get("gamescope_hdr"):
                errors.append("This MAKO integration uses its supported SDR launcher. Disable HDR explicitly or select another generator; GameOpti will not lower it silently")
            # Official mako-launch supports either its ordered vkBasalt chain or host MangoHud, not both.
            from .mangohud import MangoHudProfileRepository
            try: hud = MangoHudProfileRepository(root=self.root).load(game.profile_key)
            except (OSError, ValueError): hud = None
            if hud and hud.enabled and game.launcher_variant == "flatpak":
                errors.append("Standalone MAKO's host MangoHud integration is unavailable inside a launcher Flatpak. Disable the in-game HUD explicitly or select another generator")
            if hud and hud.enabled and (host_home_directory() / ".config/mako-render/mangohud.conf").is_file():
                errors.append("MAKO's launcher would select its independent MangoHud configuration. Preserve it and select another generator, or disable the in-game HUD explicitly")
            if pp.get("backend") == "reshade": errors.append("MAKO + ReShade loader coexistence has not been verified; select Off on one page and Apply")
            if pp.get("backend") == "vkbasalt" and pp.get("effects") and hud and getattr(hud, "enabled", False):
                errors.append("MAKO supports vkBasalt or MangoHud in the game, not both. Disable one in its page, or select lsfg-vk")
        return errors

    def apply(self, game, values):
        key = game.profile_key
        state = load_profile(key, self.root)
        backend = values.get("backend", "off")
        if backend not in {"off", *DEFAULTS}: raise ValueError("Unknown frame generator")
        settings = {b: checked_settings(b, values.get("settings", {}).get(b, state["settings"][b])) for b in DEFAULTS}
        if backend == "off":
            # Disabling must remain possible after a component/executable disappears.
            if self.post.optiscaler._process_detector(game.install_path): raise ValueError("The game is currently running")
            state.update(backend="off", settings=settings, configuredAt=datetime.now(UTC).isoformat())
            self._save(key, state)
            return self.status(game)
        executable = self.post.executable(game)
        self.post._safe(game, executable)
        errors = self.compatibility(game, backend, executable, inspect_binary(executable))
        api = values.get("api", state.get("api", "auto"))
        if api not in {"auto", "vulkan", "dxvk", "vkd3d"}: raise ValueError("Invalid Vulkan launch path")
        if backend != "off":
            if inspect_binary(executable).get("api") == "unknown" and api == "auto":
                errors.append("Graphics API is inconclusive; confirm Vulkan, DXVK or VKD3D-Proton explicitly")
            caps = self.host.frame_generation_capabilities() if self.host else {}
            if not caps.get("available") or not caps.get("compute") or not caps.get("storageImages"):
                errors.append("Vulkan compute/storage-image capabilities were not confirmed on the host")
            minimum = (1, 1) if backend == "afmf" else (1, 2)
            versions = [tuple(map(int, match.groups())) for version in caps.get("apiVersions", [])
                        if (match := re.match(r"(\d+)\.(\d+)", version))]
            if not any(version >= minimum for version in versions):
                errors.append("Required Vulkan API version was not confirmed for the selected backend")
            opti = self.post.optiscaler.profile_repository.load(key)
            if opti.installation_state == "installed" and not values.get("internalFgOff", False):
                errors.append("Confirm internal/in-game frame generation is off before selecting an external generator; the upscaler is preserved")
            component = self.artifacts.status(backend)
            if not component["installed"]: errors.append(component.get("error", "Download and install the selected backend first"))
            elif not self.environment_check(game, backend).get("available"):
                errors.append("Component/dependencies are not available inside the selected launch environment; see diagnostics")
        if errors: raise ValueError("\n".join(errors))
        state.update(backend=backend, settings=settings, componentRoot=str(self.artifacts.root),
                     executable=str(executable), configuredAt=datetime.now(UTC).isoformat(), api=api,
                     internalFgOff=bool(values.get("internalFgOff", False)), launcherFlatpak=game.launcher_variant == "flatpak")
        directory = profile_path(key, self.root).parent / "frame-generation"
        if backend in {"lsfg", "mako"}:
            dll = state.get("dlls", {}).get(backend, "")
            receipt = state.get("validation", {}).get(backend, {})
            if not dll or not Path(dll).is_file() or file_hash(dll) != receipt.get("sha256") or receipt.get("componentVersion") != self.artifacts.status(backend)["version"]:
                raise ValueError("Discover/select and validate Lossless Scaling for this backend first")
            config = directory / (backend + ".toml")
            _atomic_write(config, render_config(backend, key, executable, dll, settings[backend], directory / "lsfg.log"))
            if backend == "mako":
                _atomic_write(config.with_suffix(".launcher.conf"), "version=1\nenable_zink=0\nforce_alsa_audio=0\ndisable_steam_overlay=1\ndisable_hdr_exposure=1\n")
        self._save(key, state)
        return self.status(game)

    def environment_check(self, game, backend):
        directory = self.artifacts.directory(backend)
        bits = inspect_binary(self.post.executable(game))["bits"]
        paths = ([directory / ("lib/liblsfg-vk-layer.x86.so" if bits == 32 else "lib/liblsfg-vk-layer.so")] if backend == "lsfg"
                 else [directory / ("lib32/libmako-render.so" if bits == 32 else "lib/libmako-render.so"), directory / "bin/mako-launch"]
                 if backend == "mako" else [directory / "libafmf-linux.so"])
        app_id = {"Steam": "com.valvesoftware.Steam", "Heroic": "com.heroicgameslauncher.hgl",
                  "Lutris": "net.lutris.Lutris"}.get(game.launcher.value, "") if game.launcher_variant == "flatpak" else ""
        if app_id: paths.append(profile_path(game.profile_key, self.root).parent / "frame-generation")
        return self.host.frame_generation_environment(app_id, [str(p) for p in paths]) if self.host else {"available": False}

    def status(self, game):
        state = load_profile(game.profile_key, self.root)
        errors, binary, executable = [], {}, None
        try:
            executable = self.post.executable(game)
            binary = inspect_binary(executable)
        except (OSError, ValueError, RuntimeError) as error: errors.append(str(error))
        backend = state["backend"]
        if executable: errors += self.compatibility(game, backend, executable, binary)
        processes = self.host.game_process_modules(executable.name) if executable and self.host else ()
        # Match the full installation path, not merely a common/truncated exe name.
        def canonical(value):
            return Path(str(value).removesuffix(" (deleted)").removeprefix("/run/host")).resolve()
        processes = [p for p in processes if any(canonical(m) == executable.resolve() for m in p.get("modules", []))]
        modules = {"lsfg": "liblsfg-vk-layer", "mako": "libmako-render.so", "afmf": "libafmf-linux.so"}
        directory = self.artifacts.directory(backend).resolve() if backend in DEFAULTS else None
        loaded = [p["processId"] for p in processes if directory and any(modules[backend] in m and canonical(m).is_relative_to(directory) for m in p.get("modules", []))]
        foreign = sorted({m for p in processes for m in p.get("modules", []) if any(name in m for name in (*modules.values(), "liblsfg-vk.so"))
                          and (not directory or not canonical(m).is_relative_to(directory))})
        components = {b: self.artifacts.status(b) for b in DEFAULTS}
        opti = self.post.optiscaler.profile_repository.load(game.profile_key)
        from ..providers.steam import steam_update_activity
        update = steam_update_activity(game)
        for b, component in components.items():
            if component["installed"]: component["environmentCheck"] = self.environment_check(game, b)
            component["blockers"] = self.compatibility(game, b, executable, binary) if executable else errors
            component["optiScalerConflict"] = b == "afmf" and opti.installation_state in {"installed", "modified", "missing"}
            dll = Path(state.get("dlls", {}).get(b, ""))
            receipt = state.get("validation", {}).get(b, {})
            try:
                component["modelsValidated"] = bool(b == "afmf" or (component["installed"] and dll.is_file()
                    and receipt.get("componentVersion") == component["version"] and file_hash(dll) == receipt.get("sha256")))
            except OSError: component["modelsValidated"] = False
        runner = {}
        if game.launcher.value == "Steam":
            from .runner_integration import RunnerIntegration
            home = host_home_directory()
            configs = [p for suffix in (".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam")
                       for p in (home / suffix / "userdata").glob("*/config/localconfig.vdf")]
            runner = RunnerIntegration(steam_config_paths=configs).steam_launch_option_status(game).to_dict()
        elif game.launcher.value in {"Heroic", "Lutris"}:
            info = self.launcher_status(game) if self.launcher_status else {}
            runner = {"configured": info.get("state") == "applied", "note": info.get("message", "")}
        from .optiscaler import anti_cheat_matches
        evidence = session_evidence(game.profile_key, state)
        if foreign:
            evidence["errors"].append("Conflicting or independently loaded generator modules: " + ", ".join(foreign))
            evidence["generationVerified"] = False
        access_path = self.root / "frame-generation-launcher-access.json"
        accesses = json.loads(access_path.read_text()) if access_path.exists() else {}
        own_access = any(game.profile_key in entry.get("games", {}) for entry in accesses.values())
        return {**state, "success": True, "defaults": DEFAULTS, "components": components, "binary": binary,
                "executable": str(executable or ""), "dllCandidates": self.discover_dlls(),
                "externalInstallations": self.external_installations(), "blockers": errors,
                "runtimeState": "error" if evidence["errors"] else "loaded" if loaded else "unverified", "runtimePids": loaded,
                "generationVerified": evidence["generationVerified"], "runtimeEvidence": evidence,
                "realFps": None, "outputFps": None,
                "runner": runner, "antiCheat": anti_cheat_matches(game.install_path),
                "steamUpdate": update, "optiScalerPresent": opti.installation_state in {"installed", "modified", "missing"},
                "profileDirectory": str(profile_path(game.profile_key, self.root).parent / "frame-generation"),
                "runnerBackup": own_access or profile_path(game.profile_key, self.root).with_name("frame-generation-runner-backup.json").is_file(),
                "launcherFlatpak": game.launcher_variant == "flatpak",
                "environment": "launcher-flatpak" if game.launcher_variant == "flatpak" else "host / Steam Linux Runtime",
                "capabilities": self.host.frame_generation_capabilities() if self.host else {},
                "configured": profile_path(game.profile_key, self.root).exists()}
