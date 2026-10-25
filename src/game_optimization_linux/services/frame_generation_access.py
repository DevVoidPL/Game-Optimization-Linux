"""Owned, narrow launcher Flatpak grants, preserving independent overrides."""
import json
from pathlib import Path
import re

from .host_bootstrap import host_home_directory
from .mangohud import _atomic_write

LAUNCHERS = {"Steam": "com.valvesoftware.Steam", "Heroic": "com.heroicgameslauncher.hgl", "Lutris": "net.lutris.Lutris"}


def prepare(game, service, backend="off", *, restore=False):
    if game.launcher_variant != "flatpak": return
    app_id = LAUNCHERS[game.launcher.value]
    config = service.root / game.profile_key / "frame-generation"
    record_path = service.root / "frame-generation-launcher-access.json"
    override = host_home_directory() / ".local/share/flatpak/overrides" / app_id
    records = json.loads(record_path.read_text()) if record_path.exists() else {}
    record = records.get(app_id, {"added": [], "games": {}})
    if restore:
        record["games"].pop(game.profile_key, None)
        needed = {path for paths in record["games"].values() for path in paths}
        text = override.read_text() if override.exists() else ""
        # Remove only exact grants this feature introduced. Other permissions survive.
        owned = set(record["added"]) - needed
        def remove(match):
            values = [v for v in match.group(2).split(";") if v and v not in owned]
            return match.group(1) + ";".join(values) + (";" if values else "")
        text = re.sub(r"(?m)^(filesystems=)([^\r\n]*)", remove, text)
        if record.get("busOwned") and not record["games"]:
            previous = record.get("busPrevious")
            replacement = "org.freedesktop.Flatpak=" + previous if previous else ""
            text = re.sub(r"(?m)^org\.freedesktop\.Flatpak=talk$", lambda _: replacement, text)
            record["busOwned"] = False
        _atomic_write(override, text)
        record["added"] = [p for p in record["added"] if p not in owned]
        records[app_id] = record
        _atomic_write(record_path, json.dumps(records, indent=2))
        return
    config.mkdir(parents=True, exist_ok=True)
    paths = [str(service.artifacts.directory(backend)) + ":ro", str(config)]
    if game.launcher.value == "Steam":
        base = host_home_directory() / ".local/share/game-optimization-linux"
        (base / "run-env").mkdir(parents=True, exist_ok=True)
        paths += [str(base / "bin") + ":ro", str(base / "run-env")]
    text = override.read_text() if override.exists() else ""
    current = re.search(r"(?m)^filesystems=([^\r\n]*)", text)
    existing = set(current.group(1).split(";")) if current else set()
    added = [p for p in paths if p not in existing]
    bus = re.search(r"(?m)^org\.freedesktop\.Flatpak=([^\r\n]+)$", text)
    bridge = game.launcher.value == "Steam" and (not bus or bus.group(1) not in {"talk", "own"})
    result = service.host.frame_generation_grant(app_id, paths, bridge=bridge)
    if result is None or result.returncode != 0:
        raise ValueError("Launcher Flatpak access setup failed; see the host permission error")
    # CLI is authoritative; do not claim ownership if a custom installation stores overrides elsewhere.
    updated = override.read_text() if override.exists() else ""
    field = re.search(r"(?m)^filesystems=([^\r\n]*)", updated)
    if not field or not set(paths).issubset(set(field.group(1).split(";"))):
        raise ValueError("Launcher override location could not be verified; no automatic restore ownership recorded")
    record["added"] = sorted(set(record["added"]) | set(added))
    if bridge and not record.get("busOwned"):
        record.update(busOwned=True, busPrevious=bus.group(1) if bus else None)
    record["games"][game.profile_key] = sorted(set(record["games"].get(game.profile_key, [])) | set(paths))
    records[app_id] = record
    _atomic_write(record_path, json.dumps(records, indent=2))
