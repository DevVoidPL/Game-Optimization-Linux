"""Targeted frame-generation isolation that mirrors the Vulkan loader.

Semantics follow KhronosGroup/Vulkan-Loader HEAD 3172c4859f61 and were verified
against the real loaders on 26.10.2026: host 1.4.363 (also imported into Steam
Linux Runtime 4) matched every probe case. The Freedesktop 25.08 runtime loader
1.4.321 (launcher Flatpaks) differs: "**" matches nothing and VK_INSTANCE_LAYERS
has no substring skip. ``legacy=True`` models that loader; plans are made safe
for both. Original sources:
``loader_implicit_layer_is_enabled``, ``loader_add_implicit_layers`` and
``loader_add_environment_layers`` in loader.c, filter parsing in
loader_environment.c. Finding a manifest or library is not proof that a layer
is loaded, and a loaded layer is not proof that it generates frames.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .host_bootstrap import host_home_directory

# Verified upstream names: lsfg-vk v0.9.0/v1.0.0 and 2.0.0, MAKO render-v4.6.0,
# afmf-linux 1.3.1. MAKO spatial scaling (VK_LAYER_MAKO_spatial_scaling) is an
# upscaler, not a frame generator, and is deliberately absent.
GENERATOR_LAYERS = {
    "VK_LAYER_LSFGVK_frame_generation": "lsfg",
    "VK_LAYER_LSFGVK_frame_generation_x86": "lsfg",
    "VK_LAYER_LS_frame_generation": "lsfg",
    "VK_LAYER_MAKO_render": "mako",
    "VK_LAYER_AFMF": "afmf",
}
BACKEND_NAMES = {"lsfg": "lsfg-vk", "mako": "MAKO Renderer", "afmf": "afmf-linux"}
# Disable gates GameOpti may set; any other gate falls back to an exact filter.
GATE_KEYS = frozenset({"DISABLE_LSFG", "DISABLE_LSFGVK", "DISABLE_MAKO", "DISABLE_AFMF"})
# Layers GameOpti itself activates for a selected backend.
MANAGED_LAYERS = {
    "lsfg": {"name": "VK_LAYER_LSFGVK_frame_generation", "enable": None, "disable": "DISABLE_LSFGVK"},
    "mako": {"name": "VK_LAYER_MAKO_render", "enable": ["ENABLE_MAKO", "1"], "disable": "DISABLE_MAKO"},
    "afmf": {"name": "VK_LAYER_AFMF", "enable": ["AFMF_ENABLE", "1"], "disable": "DISABLE_AFMF"},
}
MAX_FILTERS = 16  # MAX_ADDITIONAL_FILTERS in loader_common.h
MANIFEST_LIMIT = 65536
CACHE_SECONDS = 10.0
_cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}


def backend_for_layer(name: object) -> str:
    return GENERATOR_LAYERS.get(str(name), "")


def clear_discovery_cache() -> None:
    _cache.clear()


# ---- loader filter semantics -------------------------------------------------

def _filter(token: str, legacy: bool = False) -> tuple[str, str]:
    text = token.lower()  # parse_*_filter_environment_var lowercases the value
    if legacy and text == "**":
        return "none", text
    if text.startswith("~") or text in {"*", "**"}:
        return "special", text
    begin, end = text.startswith("*"), text.endswith("*")
    if begin and end:
        return "substring", text[1:-1]
    if begin:
        return "suffix", text[1:]
    if end:
        return "prefix", text[:-1]
    return "full", text  # a '*' in the middle is a literal character


def parse_filters(value: object, *, disable: bool = False, legacy: bool = False) -> dict[str, Any]:
    """Split on ',' like strtok (empty tokens skipped, no trimming)."""
    result: dict[str, Any] = {"all": False, "implicit": False, "explicit": False, "filters": []}
    for token in (t for t in str(value or "").split(",") if t):
        kind, text = _filter(token, legacy)
        if disable and kind == "special":
            if text in {"~all~", "*", "**"}:
                result["all"] = True
            elif text == "~implicit~":
                result["implicit"] = True
            elif text == "~explicit~":
                result["explicit"] = True
            continue
        result["filters"].append((kind, text, token))
        if len(result["filters"]) >= MAX_FILTERS:
            break  # the loader ignores later entries
    return result


def matching_tokens(name: str, parsed: Mapping[str, Any]) -> list[str]:
    lower = name.lower()
    hits = []
    for kind, text, token in parsed["filters"]:
        if len(text) > len(lower):
            continue
        if kind == "none":
            hit = False
        elif kind == "special":
            hit = text in {"~all~", "*", "**"}  # ~implicit~/~explicit~ match nothing here
        elif kind == "substring":
            hit = text in lower
        elif kind == "suffix":
            hit = lower.endswith(text)
        elif kind == "prefix":
            hit = lower.startswith(text)
        else:
            hit = lower == text
        if hit:
            hits.append(token)
    return hits


def load_decision(layer: Mapping[str, Any], env: Mapping[str, str], *, legacy: bool | None = None) -> str:
    """Activation source if the loader would insert this layer, otherwise ''.

    ``legacy=None`` returns a source if either loader version would insert it.
    """
    if legacy is None:
        return load_decision(layer, env, legacy=False) or load_decision(layer, env, legacy=True)
    name = str(layer["name"])
    raw = env.get("VK_INSTANCE_LAYERS")
    if name in [t for t in str(raw or "").split(":") if t]:
        return "instance_layers"  # bypasses disable gates and filters
    enabled_by_filter = bool(matching_tokens(name, parse_filters(env.get("VK_LOADER_LAYERS_ENABLE"), legacy=legacy)))
    if layer.get("kind") == "explicit":
        return "enable_filter" if enabled_by_filter else ""
    if raw and name in str(raw) and not legacy:
        return ""  # strstr(): the implicit pass skips it; no exact token loads it
    disable = parse_filters(env.get("VK_LOADER_LAYERS_DISABLE"), disable=True, legacy=legacy)
    allowed = bool(matching_tokens(name, parse_filters(env.get("VK_LOADER_LAYERS_ALLOW"), legacy=legacy)))
    forced_off = (disable["all"] or disable["implicit"] or bool(matching_tokens(name, disable))) and not allowed
    gate = layer.get("enable")
    source = "automatic_load" if not gate else "inherited_enable" if env.get(gate[0]) == gate[1] else ""
    if enabled_by_filter:
        source = source or "enable_filter"
    elif source and forced_off:
        return ""
    if layer.get("disable") and layer["disable"] in env:
        return ""  # presence check: any value, even empty, disables
    return source


# ---- discovery -----------------------------------------------------------------

def _split(value: object) -> list[str]:
    return [p for p in str(value or "").split(":") if p]


def search_directories(env: Mapping[str, str], kind: str = "implicit") -> list[str]:
    """read_data_files_in_search_paths order for Linux."""
    override, additional, relative = (
        ("VK_IMPLICIT_LAYER_PATH", "VK_ADD_IMPLICIT_LAYER_PATH", "vulkan/implicit_layer.d") if kind == "implicit"
        else ("VK_LAYER_PATH", "VK_ADD_LAYER_PATH", "vulkan/explicit_layer.d"))
    if env.get(override):
        return list(dict.fromkeys(_split(env[override])))  # additional paths are ignored
    home = str(env.get("HOME") or "").strip() or str(host_home_directory())
    # A Flatpak sandbox environment (the GUI preview) is never the game's view.
    sandboxed = bool(str(env.get("FLATPAK_ID", "")).strip())
    def xdg(key: str) -> str:
        return "" if sandboxed else str(env.get(key) or "")
    bases = [xdg("XDG_CONFIG_HOME") or home + "/.config", *_split(xdg("XDG_CONFIG_DIRS") or "/etc/xdg"), "/etc",
             xdg("XDG_DATA_HOME") or home + "/.local/share",
             *_split(xdg("XDG_DATA_DIRS") or "/usr/local/share:/usr/share")]
    return list(dict.fromkeys([*_split(env.get(additional)), *(b.rstrip("/") + "/" + relative for b in bases)]))


def _first(value: object) -> list[str] | None:
    # The loader reads only the first child of enable/disable_environment.
    if isinstance(value, dict) and value:
        key, item = next(iter(value.items()))
        return [str(key), str(item)]
    return None


def parse_manifest(path: str, text: str, kind: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("layers") if isinstance(data.get("layers"), list) else [data.get("layer")]
    result = []
    for layer in items:
        if not isinstance(layer, dict) or not backend_for_layer(layer.get("name")):
            continue
        disable = _first(layer.get("disable_environment"))
        library = str(layer.get("library_path", ""))
        candidate = (library if library.startswith("/") else
                     os.path.normpath(os.path.join(os.path.dirname(path), library)) if "/" in library else "")
        result.append({"name": str(layer["name"]), "backend": backend_for_layer(layer["name"]),
                       "manifest": path, "kind": kind, "library": candidate or library,
                       "libraryPresent": None, "arch": str(layer.get("library_arch", "")),
                       "enable": _first(layer.get("enable_environment")),
                       "disable": disable[0] if disable else None})
    return result


def _read_local(directories: Sequence[str]) -> list[tuple[str, str]]:
    records = []
    for directory in directories:
        try:
            paths = sorted(Path(directory).glob("*.json"))
        except OSError:
            continue
        for path in paths:
            try:
                if path.is_file() and path.stat().st_size <= MANIFEST_LIMIT:
                    records.append((str(path), path.read_text(errors="replace")))
            except OSError:
                continue
    return records


def _host_client():
    # Inside GameOpti's Flatpak, host /usr and /etc are not visible directly.
    if not str(os.environ.get("FLATPAK_ID", "")).strip():
        return None
    try:
        from .host_service import HostServiceClient
        return HostServiceClient()
    except Exception:  # noqa: BLE001 - discovery must never block a launch
        return None


def discover_layers(environment: Mapping[str, str], directories: Sequence[Path | str] | None = None) -> list[dict]:
    """Generator manifests the game's loader can see; detection only."""
    env = {str(k): str(v) for k, v in environment.items()}
    if directories is not None:
        groups = [("explicit" if Path(d).name == "explicit_layer.d" else "implicit", [str(d)]) for d in directories]
    else:
        groups = [("implicit", search_directories(env, "implicit"))]
        if env.get("VK_INSTANCE_LAYERS") or env.get("VK_LOADER_LAYERS_ENABLE"):
            groups.append(("explicit", search_directories(env, "explicit")))
    key = tuple((kind, tuple(paths)) for kind, paths in groups)
    cached = _cache.get(key)
    if directories is None and cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return [dict(item) for item in cached[1]]
    host = _host_client() if directories is None else None
    layers: list[dict[str, Any]] = []
    seen = set()
    for kind, paths in groups:
        records = None
        if host is not None:
            try:
                records = host.vulkan_layer_manifests(paths)
            except Exception:  # noqa: BLE001 - fall back to the visible paths
                records = None
        for path, text in (records if records is not None else _read_local(paths)):
            for layer in parse_manifest(path, text, kind):
                token = (layer["manifest"], layer["name"])
                if token not in seen:
                    seen.add(token)
                    layers.append(layer)
    files = [layer["library"] for layer in layers if layer["library"].startswith("/")]
    existing: set[str] = set()
    if files:
        try:
            existing = set(host.existing_files(files)) if host is not None else {f for f in files if Path(f).is_file()}
        except Exception:  # noqa: BLE001
            existing = {f for f in files if Path(f).is_file()}
    for layer in layers:
        # A bare name is resolved by the dynamic linker; presence is not verified.
        layer["libraryPresent"] = layer["library"] in existing if layer["library"].startswith("/") else None
    if directories is None:
        _cache[key] = (time.monotonic(), [dict(item) for item in layers])
    return layers


# ---- planning ------------------------------------------------------------------

def isolation_plan(environment: Mapping[str, str], layers: Sequence[Mapping[str, Any]], *,
                   selected: str = "off", target: str = "game", protect: str = "") -> dict[str, Any]:
    """Minimal changes so that no non-selected generator loads in ``target``.

    Absent layers and opt-in layers without a concrete request add nothing.
    Unrelated layers, filters, paths and preloads are never removed. ``protect``
    names a backend selected for the game: its layers may only be gated (the
    game's wrapper unsets the gate), never filtered by name.
    """
    env = {str(k): str(v) for k, v in environment.items()}
    changes: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    candidates = [dict(l) for l in layers if l.get("backend") and l["backend"] != selected
                  and l.get("libraryPresent") is not False]
    requested = [{"name": l["name"], "backend": l["backend"], "manifest": l.get("manifest", ""), "source": s}
                 for l in candidates if (s := load_decision(l, env))]
    unresolved: set[str] = set()

    def record(key: str, value: str, layer: Mapping[str, Any], reason: str, detail: str) -> None:
        env[key] = value
        changes[key] = value
        entries.append({"target": target, "key": key, "value": value, "layer": layer["name"],
                        "backend": layer["backend"], "backendName": BACKEND_NAMES[layer["backend"]],
                        "reason": reason, "detail": detail, "manifest": layer.get("manifest", "")})

    def remove_exact(key: str, tokens: list[str], name: str, sep: str) -> str:
        return sep.join(t for t in str(env.get(key, "")).split(sep) if t and t not in tokens)

    for _ in range(4):
        progressed = False
        for layer in candidates:
            name = layer["name"]
            source = load_decision(layer, env)
            if not source or name in unresolved:
                continue
            progressed = True
            if source == "instance_layers":
                record("VK_INSTANCE_LAYERS", remove_exact("VK_INSTANCE_LAYERS", [name], name, ":"),
                       layer, "instance_layers", name)
                continue
            gate = layer.get("disable")
            if layer.get("kind", "implicit") == "implicit" and gate in GATE_KEYS:
                detail = ("=".join(layer["enable"]) if source == "inherited_enable" else
                          ",".join(matching_tokens(name, parse_filters(env.get("VK_LOADER_LAYERS_ENABLE"))))
                          if source == "enable_filter" else "")
                record(gate, "1", layer, source, detail)
                continue
            problem = ""
            if protect and layer["backend"] == protect:
                problem = f"{name} has no usable disable variable; it cannot be filtered without blocking the game's selected generator"
            forced = matching_tokens(name, parse_filters(env.get("VK_LOADER_LAYERS_ENABLE")))
            if not problem and forced:
                if all(t.lower() == name.lower() for t in forced):
                    record("VK_LOADER_LAYERS_ENABLE", remove_exact("VK_LOADER_LAYERS_ENABLE", forced, name, ","),
                           layer, "enable_filter_removed", ",".join(forced))
                    continue
                problem = f"VK_LOADER_LAYERS_ENABLE entry {','.join(forced)} force-enables {name} together with unrelated layers"
            if not problem and layer.get("kind", "implicit") == "implicit":
                allowed = matching_tokens(name, parse_filters(env.get("VK_LOADER_LAYERS_ALLOW")))
                if allowed and not all(t.lower() == name.lower() for t in allowed):
                    problem = f"VK_LOADER_LAYERS_ALLOW entry {','.join(allowed)} would exempt {name} from the disable filter together with unrelated layers"
                elif allowed:
                    record("VK_LOADER_LAYERS_ALLOW", remove_exact("VK_LOADER_LAYERS_ALLOW", allowed, name, ","),
                           layer, "allow_removed", ",".join(allowed))
                elif len(parse_filters(env.get("VK_LOADER_LAYERS_DISABLE"), disable=True)["filters"]) >= MAX_FILTERS:
                    problem = f"VK_LOADER_LAYERS_DISABLE already holds {MAX_FILTERS} name filters; the loader would ignore an entry for {name}"
                else:
                    current = [t for t in str(env.get("VK_LOADER_LAYERS_DISABLE", "")).split(",") if t]
                    record("VK_LOADER_LAYERS_DISABLE", ",".join([*current, name]), layer, "disable_filter", name)
                if not problem:
                    continue
            unresolved.add(name)
            warnings.append(f"{'Gamescope compositor' if target == 'compositor' else 'Game'}: {problem}; "
                            "GameOpti did not change it. Resolve it explicitly to block this generator")
        if not progressed:
            break
    detected = [{"name": l["name"], "backend": l["backend"], "backendName": BACKEND_NAMES[l["backend"]],
                 "manifest": l.get("manifest", ""), "kind": l.get("kind", "implicit"), "libraryPresent": l.get("libraryPresent")}
                for l in layers if l.get("backend")]
    return {"environment": changes, "entries": entries, "warnings": warnings,
            "requested": requested, "detected": detected}


def suppression_environment(inherited: Mapping[str, str], layers: Sequence[Mapping[str, Any]], *,
                            selected: str = "off") -> dict[str, str]:
    """Compatibility wrapper returning only the environment changes."""
    return isolation_plan(inherited, layers, selected=selected)["environment"]


def selected_blocker(backend: str, environment: Mapping[str, str]) -> str:
    """Reason the loader would not insert the selected managed layer, or ''."""
    if environment.get("VK_IMPLICIT_LAYER_PATH"):
        return ("Inherited VK_IMPLICIT_LAYER_PATH replaces the Vulkan loader search path, so the selected "
                "generator's layer directory would be ignored; remove that per-game override explicitly")
    layer = {**MANAGED_LAYERS[backend], "kind": "implicit", "backend": backend}
    probe = {k: str(v) for k, v in environment.items() if k != layer["disable"]}  # the wrapper unsets it
    if load_decision(layer, probe, legacy=False) and load_decision(layer, probe, legacy=True):
        return ""
    raw = str(probe.get("VK_INSTANCE_LAYERS", ""))
    if raw and layer["name"] in raw:
        return (f"A VK_INSTANCE_LAYERS entry containing {layer['name']} makes the Vulkan loader skip the selected "
                "implicit layer; remove that entry explicitly")
    return "Existing VK_LOADER_LAYERS_DISABLE blocks the selected frame generator; remove that per-game conflict explicitly"
