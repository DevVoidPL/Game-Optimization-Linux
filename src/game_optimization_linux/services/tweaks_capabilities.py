"""Read-only evidence from the selected runner, never from the distro name."""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Mapping

from game_optimization_linux.models import Launcher
from game_optimization_linux.models.proton_tweaks import PROTON_TWEAK_REGISTRY
from game_optimization_linux.providers.keyvalues import load_keyvalues
from game_optimization_linux.providers.steam import SteamGameProvider
from .launcher_integration import locate_config, LauncherSyncError


def _mapping(node, name):
    if not isinstance(node, dict):
        return {}
    for key, value in node.items():
        if key.casefold() == name.casefold() and isinstance(value, dict):
            return value
        found = _mapping(value, name)
        if found:
            return found
    return {}


def selected_runner(game, home: Path | None = None) -> tuple[Path | None, str]:
    home = home or Path.home()
    try:
        if game.launcher != Launcher.STEAM:
            config = locate_config(game, home)
            doc = config.load()
            if config.proton_runner(doc) is False:
                return None, "not_proton"
            if game.launcher == Launcher.HEROIC:
                version = config._section(doc).get("wineVersion", {})
                candidate = Path(str(version.get("bin", ""))).expanduser()
                # Heroic stores either the Proton script or a wine executable.
                if candidate.name == "proton" and candidate.is_file():
                    return candidate, "selected"
                for parent in list(candidate.parents)[:4]:
                    if (parent / "proton").is_file():
                        return parent / "proton", "selected"
            else:
                wine = doc.get("wine", {})
                version = str(wine.get("version", ""))
                custom = Path(str(wine.get("custom_wine_path", ""))).expanduser()
                candidates = [custom, home / ".local/share/lutris/runners/wine" / version / "bin/wine",
                              home / ".var/app/net.lutris.Lutris/data/lutris/runners/wine" / version / "bin/wine"]
                for candidate in candidates:
                    for parent in list(candidate.parents)[:3]:
                        if (parent / "proton").is_file():
                            return parent / "proton", "selected"
            return None, "unknown"
        roots = SteamGameProvider.standard_roots(home)
        roots = [p for p in roots if (".var/app/" in str(p)) == (game.launcher_variant == "flatpak")]
        for root in roots:
            path = root / "config/config.vdf"
            if not path.is_file():
                continue
            mapping = _mapping(load_keyvalues(path), "CompatToolMapping")
            selected = mapping.get(str(game.steam_app_id), mapping.get("0", {}))
            name = str(selected.get("name", "")) if isinstance(selected, dict) else ""
            if not name:
                continue  # Steam's implicit selection is not evidence of a version.
            libraries = [root]
            if game.library_path:
                libraries.append(Path(game.library_path))
            lf = root / "steamapps/libraryfolders.vdf"
            if lf.is_file():
                folders = _mapping(load_keyvalues(lf), "libraryfolders")
                libraries.extend(Path(v["path"]) for v in folders.values() if isinstance(v, dict) and v.get("path"))
            candidates = [root / "compatibilitytools.d" / name]
            for base in (Path("/usr/share/steam/compatibilitytools.d"), Path("/run/host/usr/share/steam/compatibilitytools.d")):
                candidates.extend(base.glob("*"))
            for library in libraries:
                candidates.extend((library / "steamapps/common").glob("Proton*"))
            for candidate in candidates:
                manifest = candidate / "compatibilitytool.vdf"
                matches = candidate.name == name
                if manifest.is_file():
                    matches = matches or name in _mapping(load_keyvalues(manifest), "compat_tools")
                if matches and (candidate / "proton").is_file():
                    return candidate / "proton", "selected"
            return None, "unknown"
    except (OSError, ValueError, LauncherSyncError):
        pass
    return None, "unknown"


def log_directory(game, key: str, home: Path | None = None) -> Path:
    home = home or Path.home()
    app = {Launcher.STEAM: "com.valvesoftware.Steam", Launcher.HEROIC: "com.heroicgameslauncher.hgl", Launcher.LUTRIS: "net.lutris.Lutris"}.get(game.launcher)
    base = home / ".var/app" / app / "data" if app and game.launcher_variant == "flatpak" else home / ".local/share"
    return base / "game-optimization-linux/proton-logs" / key


def _files(root: Path, pattern: str):
    for base in (root / "files", root / "dist"):
        yield from base.glob(pattern)


def runner_capabilities(game, home: Path | None = None) -> dict:
    script, selection = selected_runner(game, home)
    text = ""
    if script:
        try:
            text = script.read_text(errors="replace")[:2_000_000]
        except OSError:
            pass
    support = {}
    for definition in PROTON_TWEAK_REGISTRY:
        state = "unknown"
        if selection == "not_proton":
            state = "unsupported"
        elif text:
            state = "present" if definition.environment_key in text else "unsupported"
        support[definition.id] = state
    components = {}
    if script:
        root = script.parent
        components["wayland"] = any(_files(root, "lib*/wine/*/winewayland.drv*"))
        manifests = []
        for path in _files(root, "share/low_latency_layer/implicit_layer.d/*.json"):
            try:
                layer = json.loads(path.read_text()).get("layer", {})
                if (layer.get("enable_environment", {}).get("LOW_LATENCY_LAYER") == "1"
                        and layer.get("disable_environment", {}).get("DISABLE_LOW_LATENCY_LAYER") == "1"
                        and Path(str(layer.get("library_path", ""))).name == "libVkLayer_KORTHOS_LowLatency.so"):
                    manifests.append(path)
            except (OSError, ValueError, AttributeError):
                continue
        libraries = list(_files(root, "lib*/**/libVkLayer_KORTHOS_LowLatency.so"))
        # ELF class records architectures actually installed, not just a JSON.
        classes = set()
        for library in libraries:
            try:
                with library.open("rb") as stream:
                    header = stream.read(5)
                if header[:4] == b"\x7fELF":
                    classes.add({1: "32", 2: "64"}.get(header[4], "unknown"))
            except OSError:
                pass
        components["lowLatencyArchitectures"] = sorted(classes)
        if support["wayland"] == "present" and not components["wayland"]:
            support["wayland"] = "unsupported"
        if not manifests or not classes:
            support["low_latency"] = support["latency_reflex"] = "unsupported"
        else:
            support["latency_reflex"] = "unknown"  # separate layer setting, not a Proton script flag
            for library in libraries:
                try:
                    if b"LOW_LATENCY_LAYER_REFLEX" in library.read_bytes():
                        support["latency_reflex"] = "present"
                        break
                except OSError:
                    pass
        # HDR is implemented by DXGI, not necessarily in the Proton script.
        for dll in _files(root, "lib*/wine/dxvk/*/dxgi.dll"):
            try:
                if b"DXVK_NO_HDR" in dll.read_bytes():
                    support["disable_hdr"] = "present"
                    break
            except OSError:
                pass
    architecture = "unknown"
    try:
        import struct
        with Path(game.executable_path).open("rb") as stream:
            header = stream.read(64)
            if header[:2] == b"MZ" and len(header) == 64:
                stream.seek(struct.unpack_from("<I", header, 60)[0])
                pe = stream.read(6)
                if pe[:4] == b"PE\0\0":
                    architecture = {0x14c: "32", 0x8664: "64", 0xaa64: "arm64"}.get(struct.unpack_from("<H", pe, 4)[0], "unknown")
    except (OSError, ValueError):
        pass
    components["gameArchitecture"] = architecture
    if architecture != "unknown" and components.get("lowLatencyArchitectures") and architecture not in components["lowLatencyArchitectures"]:
        # A 32-bit PE may run in a 64-bit Wine WoW64 Unix process. Without
        # the actual launch selection, do not report a false architecture failure.
        status = "unknown" if architecture == "32" and "64" in components["lowLatencyArchitectures"] and "PROTON_USE_WOW64" in text else "unsupported"
        support["low_latency"] = support["latency_reflex"] = status
    return {"selection": selection, "runner": str(script.parent.name) if script else "",
            "runnerPath": str(script or ""), "support": support, "components": components,
            "boundary": "flatpak" if game.launcher_variant == "flatpak" else "host",
            "waylandSession": bool(os.environ.get("WAYLAND_DISPLAY"))}


def merge_tweak_environment(inherited: Mapping[str, str], overrides: Mapping[str, str]) -> dict[str, str]:
    result = dict(overrides)
    # Keep the legacy, explicitly opted-in DXIL token without discarding other
    # tokens. Default contributes nothing, so foreign configuration survives.
    for key in ("DXIL_SPIRV_CONFIG",):
        if key in result:
            import re
            values = re.split(r"[,;]", str(inherited.get(key, ""))) + re.split(r"[,;]", result[key])
            result[key] = ",".join(dict.fromkeys(v.strip() for v in values if v.strip()))
    return result


def conflicts(states: Mapping[str, str], inherited: Mapping[str, str], *, gamescope=False, frame_generation=False) -> list[str]:
    enabled = lambda key: states.get(key) == "enabled"
    messages = []
    if enabled("wayland") and gamescope:
        messages.append("wayland_gamescope")
    if enabled("latency_reflex") and not enabled("low_latency"):
        messages.append("reflex_requires_layer")
    if enabled("low_latency"):
        if enabled("use_wined3d"):
            messages.append("layer_requires_vulkan")
        if enabled("latency_reflex") and enabled("disable_nvapi"):
            messages.append("reflex_requires_nvapi")
        if inherited.get("DISABLE_LOW_LATENCY_LAYER"):
            messages.append("external_layer_veto")
        if inherited.get("DXVK_NVAPI_VKREFLEX") == "1" or frame_generation:
            messages.append("latency_stack")
    return messages
