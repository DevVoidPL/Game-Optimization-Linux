"""Managed per-game post-processing, independent of upscaler file ownership."""
from __future__ import annotations

from configparser import ConfigParser
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
import io
import json
import logging
import math
import os
from pathlib import Path
import shutil
import struct
from uuid import uuid4

from ..config import CACHE_DIR, GAMES_CONFIG_DIR, STATE_DIR
from .mangohud import _atomic_write
from .optiscaler import OptiScalerError, anti_cheat_matches
from .post_processing_artifacts import PostProcessingArtifacts, RESHADE_VERSION, VKBASALT_VERSION
from .post_processing_catalog import EFFECTS, PRESETS, TOGGLE_KEYS, VK_PARAMETER_NAMES

logger = logging.getLogger(__name__)
MANAGED = ".gameopti-post-processing"
LAYER = "VK_LAYER_VKBASALT_post_processing"
LOADER_FILES = ("dxgi.dll", "d3d9.dll", "d3d10.dll", "d3d11.dll", "d3d12.dll",
                "opengl32.dll", "version.dll", "winmm.dll", "dinput8.dll", "dsound.dll", "ReShade.ini")


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def inspect_binary(path: Path) -> dict:
    """Read ELF/PE headers and PE imports; do not infer bitness from a filename."""
    info = path.stat()
    return dict(_inspect_binary(path, info.st_mtime_ns, info.st_size))


@lru_cache(maxsize=32)
def _inspect_binary(path: Path, _mtime: int, _size: int) -> dict:
    data = path.read_bytes()
    if data[:4] == b"\x7fELF" and len(data) > 20:
        if data[5] != 1 or struct.unpack_from("<H", data, 18)[0] not in {3, 62}:
            raise ValueError("Only x86 and x86-64 ELF games are supported")
        return {"format": "ELF", "bits": {1: 32, 2: 64}.get(data[4], 0),
                "api": "unknown", "imports": []}
    if data[:2] != b"MZ" or len(data) < 64:
        raise ValueError("Select a supported ELF or Windows PE executable")
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if pe + 26 >= len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Invalid PE executable")
    machine, sections = struct.unpack_from("<HH", data, pe + 4)
    bits = {0x14c: 32, 0x8664: 64}.get(machine, 0)
    if not bits:
        raise ValueError("Only x86 and x86-64 games are supported")
    optional = pe + 24
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    table = optional + opt_size
    def offset(rva):
        for i in range(sections):
            row = table + i * 40
            virtual_size, address, size, raw = struct.unpack_from("<IIII", data, row + 8)
            if address <= rva < address + max(virtual_size, size):
                return raw + rva - address
        return 0
    imports = []
    try:
        directory = optional + (112 if bits == 64 else 96)
        rva = struct.unpack_from("<I", data, directory + 8)[0]
        row = offset(rva)
        for _ in range(512):
            if not row or not any(data[row:row + 20]):
                break
            name = offset(struct.unpack_from("<I", data, row + 12)[0])
            if name:
                imports.append(data[name:name + 256].split(b"\0")[0].decode("ascii", "replace").lower())
            row += 20
    except (struct.error, IndexError):
        pass
    api = next((api for dll, api in (("d3d12.dll", "d3d12"), ("d3d11.dll", "d3d11"),
               ("d3d10.dll", "d3d10"), ("d3d9.dll", "d3d9"), ("opengl32.dll", "opengl"),
               ("vulkan-1.dll", "vulkan"), ("dxgi.dll", "d3d11")) if dll in imports), "unknown")
    return {"format": "PE", "bits": bits, "api": api, "imports": imports}


def profile_path(key: str, root: Path = GAMES_CONFIG_DIR) -> Path:
    from ..models.mangohud import validate_game_key
    return Path(root) / validate_game_key(key) / "post-processing.json"


def load_profile(key: str, root: Path = GAMES_CONFIG_DIR) -> dict:
    path = profile_path(key, root)
    if not path.is_file():
        return dict(backend="off", effects=[], parameters={}, toggleKey="Home", api="auto")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("backend") not in {"off", "vkbasalt", "reshade"}:
        raise ValueError("Invalid post-processing profile")
    return value


def launch_environment(key: str, root: Path = GAMES_CONFIG_DIR) -> dict[str, str]:
    """One activation owner; no global config or inherited env is changed on disk."""
    state = load_profile(key, root)
    if state["backend"] == "off" or state.get("phase") != "installed":
        return {}
    base = Path(state["directory"])
    module = base / state["module"]
    if base.is_symlink() or (base / MANAGED).is_symlink():
        raise ValueError("Managed post-processing directory was replaced by a symlink")
    if not module.is_file() or module.is_symlink() or digest(module) != state["moduleHash"]:
        raise ValueError("Post-processing module is missing or modified")
    for name, expected in state.get("ownedHashes", {}).items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid managed file path")
        if relative.suffix.lower() in {".fx", ".fxh", ".png", ".json"}:
            path = base / relative
            if not path.is_file() or path.is_symlink() or digest(path) != expected:
                raise ValueError(f"Managed shader dependency is missing or modified: {path}")
    if state["backend"] == "reshade":
        return {"WINEDLLOVERRIDES": f"{module.stem}=n,b", "ENABLE_VKBASALT": "0", "DISABLE_VKBASALT": "1"}
    if not state.get("effects"):
        # vkBasalt interprets an empty effects option as its default CAS.
        # A neutral profile must not load the layer at all.
        return {"ENABLE_VKBASALT": "0", "DISABLE_VKBASALT": "1"}
    managed = base / MANAGED
    return {"ENABLE_VKBASALT": "1", "DISABLE_VKBASALT": "0",
            "VKBASALT_CONFIG_FILE": str(managed / "vkBasalt.conf"),
            "VKBASALT_LOG_LEVEL": "debug", "VKBASALT_LOG_FILE": str(managed / "vkBasalt.log"),
            "VK_ADD_LAYER_PATH": str(managed / "layer"), "VK_INSTANCE_LAYERS": LAYER}


def optional_launch_environment(key: str, root: Path = GAMES_CONFIG_DIR) -> dict[str, str]:
    try:
        return launch_environment(key, root)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Post-processing ignored for %s: %s", key, error)
        return {}


class PostProcessingService:
    def __init__(self, optiscaler, host=None, artifacts=None):
        self.optiscaler = optiscaler
        self.host = host
        self.root = optiscaler.profile_repository.root
        self.artifacts = artifacts or PostProcessingArtifacts(CACHE_DIR / "post-processing", host)

    def _save(self, key, value):
        _atomic_write(profile_path(key, self.root), json.dumps(value, indent=2, sort_keys=True) + "\n")

    def _journal(self, key):
        return profile_path(key, self.root).with_name("post-processing-transaction.json")

    def executable(self, game, selected=""):
        stored = self.optiscaler.profile_repository.load(game.profile_key)
        requested = selected or stored.executable or game.executable_path
        candidate, _confidence = self.optiscaler._selected_candidate(
            game, self.optiscaler.executable_resolver, requested)
        root = self.optiscaler._canonical_game_root(game)
        return self.optiscaler._target(root, candidate.relative_path)

    def _safe(self, game, executable):
        from ..providers.steam import steam_update_activity
        if steam_update_activity(game)["blocked"]:
            raise ValueError("A Steam update is currently active")
        if self.optiscaler._process_detector(game.install_path):
            raise ValueError("The game is currently running")
        if self.host and any(any(str(m).startswith(str(game.install_path) + "/")
                                  for m in p.get("modules", []))
                             for p in self.host.game_process_modules(executable.name)):
            raise ValueError("The game is currently running")

    def _transaction(self, game, steps, after, before):
        """Journal renames before execution, undo every completed step on failure."""
        self._validate_steps(game, steps)
        key = game.profile_key
        journal = dict(root=str(game.install_path.resolve()), steps=steps, done=0,
                       before=before, after=after)
        _atomic_write(self._journal(key), json.dumps(journal))
        try:
            for i, step in enumerate(steps):
                journal["done"] = i + 1
                _atomic_write(self._journal(key), json.dumps(journal))
                self._move(step["from"], step["to"])
            self._save(key, after)
            self._journal(key).unlink()
        except BaseException:
            self.recover(game)
            raise

    @staticmethod
    def _move(source, target):
        source, target = Path(source), Path(target)
        if target.exists() or target.is_symlink():
            raise ValueError(f"File conflict: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)

    def _validate_steps(self, game, steps):
        root = game.install_path.resolve()
        history = (self.optiscaler.data_root / game.profile_key / "post-processing").resolve()
        for step in steps:
            for name in ("from", "to"):
                path = Path(step[name]).resolve()
                if not (path.is_relative_to(root) or path.is_relative_to(history)):
                    raise ValueError("Invalid post-processing transaction path; game directory changed")

    def recover(self, game):
        path = self._journal(game.profile_key)
        if not path.is_file():
            return
        journal = json.loads(path.read_text())
        root = game.install_path.resolve()
        if str(root) != journal["root"]:
            raise ValueError("Game directory changed; review the interrupted post-processing transaction")
        self._validate_steps(game, journal["steps"])
        for i in range(journal["done"] - 1, -1, -1):
            step = journal["steps"][i]
            moved, original = Path(step["to"]), Path(step["from"])
            if moved.exists() and not original.exists():
                self._move(moved, original)
            journal["done"] = i
            _atomic_write(path, json.dumps(journal))
        self._save(game.profile_key, journal["before"])
        path.unlink()

    def settings(self, values, previous=None):
        allowed = {"backend", "effects", "parameters", "toggleKey", "api", "preset", "executable"}
        settings = {**(previous or {}), **{k: v for k, v in dict(values).items() if k in allowed}}
        backend = str(settings.get("backend", "off"))
        if backend not in {"off", "vkbasalt", "reshade"}:
            raise ValueError("Unknown post-processing backend")
        if settings.get("preset") in PRESETS:
            settings.update(PRESETS[settings["preset"]])
        effects = list(settings.get("effects", []))
        if len(effects) != len(set(effects)) or len(effects) > 12:
            raise ValueError("Invalid effect order")
        if any(e not in EFFECTS or (backend != "off" and backend not in EFFECTS[e]["backends"]) for e in effects):
            raise ValueError("Effect is not supported by this backend")
        parameters = {}
        for effect in EFFECTS.values():
            for spec in effect["parameters"]:
                value = float(settings.get("parameters", {}).get(spec["key"], spec["default"]))
                if not math.isfinite(value) or not spec["minimum"] <= value <= spec["maximum"]:
                    raise ValueError(f"Invalid effect parameter: {spec['key']}")
                if spec.get("integer"):
                    value = int(round(value))
                parameters[spec["key"]] = value
        toggle = str(settings.get("toggleKey", "Home"))
        if toggle not in TOGGLE_KEYS:
            raise ValueError("Unsupported effect-toggle key")
        api = str(settings.get("api", "auto"))
        if api not in {"auto", "vulkan", "d3d9", "d3d10", "d3d11", "d3d12", "opengl"}:
            raise ValueError("Unsupported graphics API")
        return {**settings, "backend": backend, "effects": effects, "parameters": parameters,
                "api": api, "toggleKey": toggle, "preset": "custom"}

    def apply(self, game, values, progress=None):
        emit = progress or (lambda *_args: None)
        previous = load_profile(game.profile_key, self.root)
        executable = self.executable(game, values.get("executable", previous.get("executable", "")))
        self._safe(game, executable)
        self.recover(game)
        previous = load_profile(game.profile_key, self.root)
        settings = self.settings(values, previous)
        backend = settings["backend"]
        if backend == "off":
            return self.remove(game)
        if previous["backend"] != "off" and previous["backend"] != backend:
            raise ValueError("Turn off the current post-processing backend before switching")
        if previous.get("phase") == "installed":
            if executable.parent != Path(previous["directory"]) or settings["api"] != previous["api"]:
                raise ValueError("Turn Off before changing the executable or graphics path")
            self._configure(game, settings)
            return self.status(game)
        binary = inspect_binary(executable)
        if binary["bits"] not in {32, 64}:
            raise ValueError("Unsupported executable architecture")
        api = binary["api"] if settings["api"] == "auto" else settings["api"]
        if backend == "reshade" and (binary["format"] != "PE" or api not in {"d3d9", "d3d10", "d3d11", "d3d12", "opengl"}):
            raise ValueError("ReShade requires a Windows PE game with D3D9/10/11/12 or OpenGL selected")
        if backend == "vkbasalt" and api == "unknown":
            raise ValueError("Select the actual Vulkan/DXVK/VKD3D graphics path before installing vkBasalt")
        if backend == "vkbasalt" and binary["format"] == "ELF" and api != "vulkan":
            raise ValueError("Select Vulkan for a native Vulkan game; native OpenGL is not supported by vkBasalt")
        if backend == "vkbasalt" and api == "opengl":
            raise ValueError("vkBasalt needs Vulkan, DXVK or VKD3D; Wine OpenGL/wined3d is unsupported")
        base = executable.parent
        if anti_cheat_matches(game.install_path):
            logger.warning("Anti-cheat files detected for %s; post-processing is for offline/single-player use", game.profile_key)
        module_name = ("d3d9.dll" if api == "d3d9" else "opengl32.dll" if api == "opengl" else "dxgi.dll")
        module = base / module_name
        if backend == "reshade":
            opti = self.optiscaler.profile_repository.load(game.profile_key)
            if opti.enabled and opti.installation_state == "installed":
                if not (base / opti.injection_dll).is_file():
                    raise ValueError(f"Upscaler profile requires verification; recorded loader is missing: {base / opti.injection_dll}")
                raise ValueError(f"Loader conflict: {opti.backend} owns {opti.injection_dll}. Use vkBasalt or restore/remove the upscaler through its own page first")
            for name in LOADER_FILES:
                if (base / name).exists() or (base / name).is_symlink():
                    raise ValueError(f"Existing file is preserved; installation blocked: {base / name}")
        if (base / MANAGED).exists() or (base / MANAGED).is_symlink():
            raise ValueError(f"Unmanaged directory is preserved: {base / MANAGED}")
        stage = base / (".gameopti-post-stage-" + uuid4().hex)
        stage.mkdir()
        payload = stage / MANAGED
        payload.mkdir()
        try:
            emit("Downloading verified post-processing components", 0.1)
            self.artifacts.shaders(payload)
            if backend == "reshade":
                self.artifacts.reshade(binary["bits"], stage / module_name)
            else:
                module_name = f"{MANAGED}/libvkbasalt.so"
                self.artifacts.vkbasalt(binary["bits"], payload / "libvkbasalt.so")
                layer = {"file_format_version": "1.0.0", "layer": {
                    "name": LAYER, "type": "GLOBAL", "library_path": str(base / module_name),
                    "api_version": "1.3.223", "implementation_version": "1", "description": "vkBasalt per-game",
                    "functions": {"vkGetInstanceProcAddr": "vkBasalt_GetInstanceProcAddr",
                                  "vkGetDeviceProcAddr": "vkBasalt_GetDeviceProcAddr"}}}
                (payload / "layer").mkdir()
                _atomic_write(payload / "layer/vkBasalt.json", json.dumps(layer))
                if self.host:
                    launcher_id = {"Heroic": "com.heroicgameslauncher.hgl", "Lutris": "net.lutris.Lutris",
                                   "Steam": "com.valvesoftware.Steam"}.get(game.launcher.value, "")
                    runtime = self._steam_runtime(game)
                    settings["environmentCheck"] = self.host.post_processing_probe(
                        payload / "libvkbasalt.so",
                        launcher_id=launcher_id if game.launcher_variant == "flatpak" else "",
                        runtime=runtime if game.launcher_variant != "flatpak" else "")
                    settings["environmentCheck"]["steamRuntimeUnverified"] = (game.launcher.value == "Steam" and not runtime)
                    if not settings["environmentCheck"]["dependenciesAvailable"]:
                        raise ValueError("vkBasalt dependencies or files are unavailable in the launcher environment: "
                                         + settings["environmentCheck"]["detail"])
            settings.update(phase="installed", directory=str(base), module=module_name,
                            moduleHash=digest(stage / module_name), binary=binary, effectiveApi=api,
                            executable=str(executable.relative_to(game.install_path.resolve())),
                            installedAt=datetime.now(UTC).isoformat(),
                            version=RESHADE_VERSION if backend == "reshade" else VKBASALT_VERSION)
            self._render(settings, stage, base)
            steps = [{"from": str(payload), "to": str(base / MANAGED)}]
            if backend == "reshade":
                steps.extend({"from": str(stage / name), "to": str(base / name)}
                             for name in (module_name, "ReShade.ini"))
            settings["ownedHashes"] = {str(p.relative_to(stage)): digest(p) for p in stage.rglob("*") if p.is_file()}
            self._transaction(game, steps, settings, previous)
        finally:
            # Only this newly-created staging directory; never a game root.
            if not self._journal(game.profile_key).exists():
                shutil.rmtree(stage, ignore_errors=True)
        emit("Completed", 1.0)
        return self.status(game)

    def _steam_runtime(self, game):
        """Use the last actual Steam command; never guess a runtime from host libraries."""
        if game.launcher.value != "Steam" or not game.steam_app_id:
            return ""
        try:
            report = json.loads((STATE_DIR / "launch-reports" / f"{game.steam_app_id}.json").read_text())
            return next((arg for arg in report.get("steamCommand", [])
                         if isinstance(arg, str) and Path(arg).name == "_v2-entry-point"), "")
        except (OSError, ValueError):
            return ""

    def _render(self, state, write_base, final_base):
        managed = write_base / MANAGED
        final = final_base / MANAGED
        effects = state["effects"]
        values = state["parameters"]
        if state["backend"] == "vkbasalt":
            lines = ["# GameOpti per-game; no global configuration is modified",
                     "effects = " + ":".join(effects), "toggleKey = " + state["toggleKey"],
                     "enableOnLaunch = True", "depthCapture = off", "smaaEdgeDetection = color",
                     f'reshadeIncludePath = "{final / "Shaders/SweetFX"}"',
                     f'reshadeTexturePath = "{final / "Textures/SweetFX"}"']
            for effect in effects:
                spec = EFFECTS[effect]
                if effect == "lut":
                    if not state.get("lutPath"):
                        raise ValueError("Select a LUT file before enabling LUT")
                    lines.append(f'lutFile = "{state["lutPath"]}"')
                if effect not in {"fxaa", "smaa"} and spec.get("shader"):
                    lines.append(f'{effect} = "{final / "Shaders/SweetFX" / spec["shader"]}"')
                for param in spec["parameters"]:
                    key = param["key"]
                    lines.append(f"{VK_PARAMETER_NAMES.get(key, key)} = {values[key]:g}")
            _atomic_write(managed / "vkBasalt.conf", "\n".join(lines) + "\n")
        else:
            preset = ConfigParser(interpolation=None)
            preset.optionxform = str
            preset["DEFAULT"] = {}
            techniques = [EFFECTS[e]["technique"] + "@" + EFFECTS[e]["shader"] for e in effects]
            # ReShade presets use global (unsectioned) technique/order entries.
            header = "Techniques=" + ",".join(techniques) + "\nTechniqueSorting=" + ",".join(techniques) + "\n"
            for e in effects:
                spec = EFFECTS[e]
                preset[spec["shader"]] = {p["key"]: f'{values[p["key"]]:g}' for p in spec["parameters"]}
                if e == "smaa":
                    preset[spec["shader"]]["EdgeDetectionType"] = "1"
                    preset[spec["shader"]]["PredicationEnabled"] = "0"
            out = io.StringIO(); preset.write(out)
            _atomic_write(managed / "Preset.ini", header + out.getvalue())
            # Relative Windows paths work with every Wine drive mapping.
            _atomic_write(write_base / "ReShade.ini",
                          f"[GENERAL]\nEffectSearchPaths=.\\{MANAGED}\\Shaders\\SweetFX\n"
                          f"TextureSearchPaths=.\\{MANAGED}\\Textures\\SweetFX\n"
                          f"PresetPath=.\\{MANAGED}\\Preset.ini\nPerformanceMode=0\n"
                          f"[INPUT]\nKeyOverlay=36,0,0,0\nKeyEffects={TOGGLE_KEYS[state['toggleKey']]},1,0,0\nGamepadNavigation=1\n")

    def _configure(self, game, state):
        launch_environment(game.profile_key, self.root)
        base = Path(state["directory"])
        # Preserve every user-edited preset/config before applying new settings.
        backup = self.optiscaler.data_root / game.profile_key / "post-processing" / uuid4().hex
        backup.mkdir(parents=True)
        paths = [base / MANAGED / ("vkBasalt.conf" if state["backend"] == "vkbasalt" else "Preset.ini")]
        if state["backend"] == "reshade": paths.append(base / "ReShade.ini")
        before = load_profile(game.profile_key, self.root)
        for path in paths:
            if path.is_symlink(): raise ValueError(f"Configuration symlink is not managed: {path}")
            if path.is_file(): shutil.copy2(path, backup / path.name)
        try:
            self._render(state, base, base)
            state["configuredAt"] = datetime.now(UTC).isoformat()
            state.pop("runtimeEvidence", None)
            self._save(game.profile_key, state)
        except BaseException:
            for path in paths:
                if (backup / path.name).is_file(): self.optiscaler._copy_atomic(backup / path.name, path)
            self._save(game.profile_key, before)
            raise

    def remove(self, game):
        state = load_profile(game.profile_key, self.root)
        executable = self.executable(game, state.get("executable", ""))
        self._safe(game, executable)
        self.recover(game)
        state = load_profile(game.profile_key, self.root)
        if state["backend"] == "off": return self.status(game)
        base = Path(state["directory"])
        module = base / state["module"]
        if module.is_symlink() or (module.exists() and digest(module) != state["moduleHash"]):
            raise ValueError(f"Modified module is preserved; removal blocked: {module}")
        # Same-filesystem staging for renames. History is inside the game so
        # external libraries/mounts do not turn an atomic rename into a copy.
        history = base / (".gameopti-post-preserved-" + uuid4().hex)
        steps = []
        names = [MANAGED] + ([state["module"], "ReShade.ini"] if state["backend"] == "reshade" else [])
        for name in names:
            p = base / name
            if p.is_symlink(): raise ValueError(f"Managed path was replaced with a symlink: {p}")
            if p.exists(): steps.append({"from": str(p), "to": str(history / name)})
        after = {**state, "backend": "off", "phase": "configured", "preservedDirectory": str(history)}
        self._transaction(game, steps, after, state)
        return self.status(game)

    def import_preset(self, game, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError("Select a ReShade INI preset smaller than 1 MiB")
        text = path.read_text(encoding="utf-8-sig")
        parser = ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read_string("[ROOT]\n" + text)
        techniques = parser.get("ROOT", "Techniques", fallback="").split(",")
        effects, parameters = [], {}
        for technique in techniques:
            name = technique.strip().split("@")[0]
            if not name: continue
            match = next((key for key, item in EFFECTS.items() if item.get("technique") == name), None)
            if match is None: raise ValueError(f"Preset needs an unsupported shader: {name}")
            effects.append(match)
            spec = EFFECTS[match]
            for p in spec["parameters"]:
                if parser.has_option(spec["shader"], p["key"]):
                    parameters[p["key"]] = parser.getfloat(spec["shader"], p["key"])
        # Import settings only: never execute downloads, add-ons or paths in INI.
        return self.apply(game, {"effects": effects, "parameters": parameters})

    def import_lut(self, game, path):
        path = Path(path)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Select a PNG or CUBE LUT smaller than 32 MiB")
        data = path.read_bytes()
        suffix = path.suffix.lower()
        if suffix == ".png":
            if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
                raise ValueError("Invalid PNG LUT")
            width, height = struct.unpack_from(">II", data, 16)
            if width != height * height or height < 2 or height > 64:
                raise ValueError("PNG LUT width must equal height squared (2-64)")
        elif suffix == ".cube":
            text = data.decode("utf-8-sig")
            rows = [r.split("#", 1)[0].strip() for r in text.splitlines()]
            size_rows = [r.split() for r in rows if r.startswith("LUT_3D_SIZE ")]
            if len(size_rows) != 1 or len(size_rows[0]) != 2:
                raise ValueError("Only 3D CUBE LUTs are supported")
            size = int(size_rows[0][1])
            samples = [r.split() for r in rows if r and r[0] in "0123456789+-." ]
            if not 2 <= size <= 64 or len(samples) != size ** 3 or any(
                    len(r) != 3 or any(not math.isfinite(float(v)) for v in r) for r in samples):
                raise ValueError("Invalid CUBE LUT size or colour data")
        else:
            raise ValueError("Only PNG and CUBE LUTs are supported")
        state = load_profile(game.profile_key, self.root)
        if state.get("phase") != "installed" or state["backend"] != "vkbasalt":
            raise ValueError("Install vkBasalt before importing a LUT")
        self._safe(game, self.executable(game, state.get("executable", "")))
        target = Path(state["directory"]) / MANAGED / ("lut-" + sha256(data).hexdigest()[:12] + suffix)
        if not target.exists(): self.optiscaler._copy_atomic(path, target)
        self._configure(game, {**state, "lutPath": str(target)})
        return self.status(game)

    def status(self, game):
        state = load_profile(game.profile_key, self.root)
        result = {**state, "success": True, "configured": state["backend"] != "off",
                  "installed": False, "runtimeLoaded": False, "effectsVerified": False,
                  "statusState": "off", "catalogue": [{"id": k, **v} for k, v in EFFECTS.items()],
                  "antiCheat": anti_cheat_matches(game.install_path), "conflicts": []}
        result["diagnostics"] = {"gameId": game.id, "appId": game.steam_app_id,
                                 "configuredAt": state.get("configuredAt", state.get("installedAt", ""))}
        session_log = ""
        if game.launcher.value == "Steam":
            from .runner_integration import RunnerIntegration
            roots = [game.library_path] if game.library_path else []
            paths = [p for root in roots for p in (Path(root) / "userdata").glob("*/config/localconfig.vdf")]
            result["steamRunner"] = RunnerIntegration(steam_config_paths=paths).steam_launch_option_status(game).to_dict()
            report = STATE_DIR / "launch-reports" / f"{game.steam_app_id}.json"
            if report.is_file():
                try:
                    payload = json.loads(report.read_text())
                    if payload.get("status") != "plan_verified":
                        session_log = str(payload.get("postProcessingLog", ""))
                    result["diagnostics"].update(sessionAt=payload.get("timestamp", ""),
                        sessionCommand=payload.get("steamCommandShell", ""), reportPath=str(report),
                        sessionEnvironment=payload.get("environmentOverridesShell", ""))
                except (OSError, ValueError):
                    pass
        try:
            executable = self.executable(game, state.get("executable", ""))
            result["executable"] = str(executable.relative_to(game.install_path.resolve()))
            result["detected"] = inspect_binary(executable)
            result["apiDetectionInconclusive"] = result["detected"]["api"] == "unknown"
            base = executable.parent
            result["conflicts"] = [str(base / name) for name in LOADER_FILES
                                   if (base / name).exists() and state["backend"] != "reshade"]
            opti = self.optiscaler.profile_repository.load(game.profile_key)
            if opti.enabled and opti.installation_state == "installed":
                result["upscalerLoaderMissing"] = not (base / opti.injection_dll).is_file()
                result["upscalerLoaderPath"] = str(base / opti.injection_dll)
            result["loaderConflicts"] = [{"path": path,
                "owner": opti.backend if opti.installation_state == "installed" and Path(path).name == opti.injection_dll else "existing_file"}
                for path in result["conflicts"]]
        except (ValueError, OSError, OptiScalerError) as error:
            result["compatibilityError"] = str(error)
        if self._journal(game.profile_key).exists():
            return {**result, "statusState": "error", "error": "Interrupted transaction; turn off to recover"}
        if state["backend"] == "off": return result
        try:
            result["environment"] = launch_environment(game.profile_key, self.root)
            result["installed"] = True
            result["statusState"] = "installed"
            base = Path(state["directory"])
            log = base / (MANAGED + "/vkBasalt.log" if state["backend"] == "vkbasalt" else "ReShade.log")
            if state["backend"] == "vkbasalt" and session_log:
                candidate = Path(session_log)
                if (candidate.parent == base / MANAGED and candidate.name.startswith("vkBasalt-session-")
                        and candidate.suffix == ".log" and not candidate.is_symlink()):
                    log = candidate
            result["logPath"] = str(log)
            config = base / (MANAGED + "/vkBasalt.conf" if state["backend"] == "vkbasalt" else "ReShade.ini")
            result["diagnostics"]["configPath"] = str(config)
            result["diagnostics"]["effects"] = state.get("effects", [])
            if not config.is_file():
                raise ValueError(f"Post-processing configuration is missing: {config}")
            if state["backend"] == "vkbasalt":
                config_values = dict(line.split("=", 1) for line in config.read_text().splitlines()
                                     if "=" in line and not line.lstrip().startswith("#"))
                config_values = {k.strip(): v.strip().strip('"') for k,v in config_values.items()}
                result["diagnostics"]["configEffects"] = config_values.get("effects", "")
                mismatches = []
                if config_values.get("effects", "") != ":".join(state.get("effects", [])):
                    mismatches.append("effects")
                for effect in state.get("effects", []):
                    for spec in EFFECTS[effect]["parameters"]:
                        key = spec["key"]
                        try:
                            if not math.isclose(float(config_values[VK_PARAMETER_NAMES.get(key, key)]),
                                                state["parameters"][key], rel_tol=1e-5, abs_tol=1e-6):
                                mismatches.append(key)
                        except (ValueError, KeyError): mismatches.append(key)
                result["diagnostics"]["configMatches"] = not mismatches
                if mismatches:
                    raise ValueError("Configuration differs from saved settings: " + ", ".join(mismatches))
            threshold = datetime.fromisoformat(state.get("configuredAt", state["installedAt"])).timestamp()
            result["diagnostics"]["evidenceMissing"] = "neutral" if not state.get("effects") else "fresh_game_log"
            if log.is_file() and log.stat().st_mtime >= threshold:
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 256 * 1024))
                    text = stream.read().decode("utf-8", errors="replace")
                result["runtimeLoaded"] = ("wrote CommandBuffers" in text if state["backend"] == "vkbasalt"
                                           else "Initializing crosire's ReShade" in text)
                result["runtimeErrors"] = [line for line in text.splitlines()
                    if ("vkBasalt err:" in line if state["backend"] == "vkbasalt" else "error" in line.lower())][-12:]
                if result["runtimeLoaded"]: result["statusState"] = "runtime_loaded"
                if result["runtimeLoaded"]:
                    result["diagnostics"].update(evidenceMissing="", logModifiedAt=datetime.fromtimestamp(log.stat().st_mtime, UTC).isoformat())
                if result["runtimeErrors"]: result["statusState"] = "error"
            # Wine launchers and their helpers inherit the same filename, and
            # upstream opens it with truncation. Inspect the selected game's
            # module maps as independent evidence instead of trusting the last writer.
            if self.host and hasattr(self.host, "game_process_modules"):
                module_path = str(base / state["module"])
                processes = self.host.game_process_modules(executable.name)
                matches = [p for p in processes if module_path in p.get("modules", [])]
                if matches:
                    result["runtimeLoaded"] = True
                    result["runtimeEvidence"] = {
                        "method": "game_process_module", "module": module_path,
                        "processId": matches[0]["processId"],
                        "configuredAt": result["diagnostics"]["configuredAt"],
                        "sessionAt": result["diagnostics"].get("sessionAt", ""),
                        "observedAt": datetime.now(UTC).isoformat()}
                    result["diagnostics"]["evidenceMissing"] = ""
                    if not result.get("runtimeErrors"): result["statusState"] = "runtime_loaded"
            # Effects verified is deliberately never inferred from module loading.
        except (ValueError, OSError) as error:
            result.update(statusState="error", error=str(error))
        return result
