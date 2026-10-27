"""Local, evidence-based fingerprint of one game run (read-only).

Everything here reads local files only. A value that is not proven by local
evidence stays ``Unknown``; nothing is guessed from a game's name or genre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import struct
from typing import Any, Iterable, Mapping

UNKNOWN = "Unknown"

# DLL imported by the executable -> rendering API it implies.
GRAPHICS_IMPORTS = {
    "d3d12.dll": "Direct3D 12",
    "d3d11.dll": "Direct3D 11",
    "d3d10.dll": "Direct3D 10",
    "d3d10_1.dll": "Direct3D 10",
    "d3d9.dll": "Direct3D 9",
    "vulkan-1.dll": "Vulkan",
    "opengl32.dll": "OpenGL",
}
# Engine runtime modules that are imported by the main executable.
ENGINE_IMPORTS = {"unityplayer.dll": "Unity"}
STEAM_RUNTIMES = {
    "1070560": "Steam Linux Runtime 1.0 (scout)",
    "1391110": "Steam Linux Runtime 2.0 (soldier)",
    "1628350": "Steam Linux Runtime 3.0 (sniper)",
}
# Per-game GameOpti files whose content defines the launch configuration.
CONFIG_FILES = (
    "optimization.json", "mangohud.json", "MangoHud.conf", "proton-tweaks.json",
    "gpu-memory.json", "optiscaler.json", "custom-launch.json",
    "post-processing.json", "frame-generation.json",
)
_PARTIAL_BYTES = 1024 * 1024
_MAX_PE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Evidence:
    value: str
    confidence: float
    source: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": round(max(0.0, min(1.0, self.confidence)), 2),
                "source": self.source, "evidence": list(self.evidence)}

    @classmethod
    def unknown(cls, source: str, *evidence: str) -> "Evidence":
        return cls(UNKNOWN, 0.0, source, tuple(evidence))


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    for virtual, virtual_size, raw, raw_size in sections:
        if virtual <= rva < virtual + max(virtual_size, raw_size):
            return raw + rva - virtual
    return None


def _ascii_at(data: bytes, offset: int | None) -> str:
    if offset is None or not 0 <= offset < len(data):
        return ""
    end = data.find(b"\0", offset, offset + 260)
    return data[offset:end].decode("ascii", "replace") if end > offset else ""


def pe_imports(path: Path) -> tuple[str, ...] | None:
    """Lower-case DLL names in the import and delay-import tables, or None
    when the file is not a readable PE image."""

    try:
        size = path.stat().st_size
        if size < 64 or size > _MAX_PE_BYTES:
            return None
        with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return _pe_imports(data)
    except (OSError, ValueError):
        return None


def _pe_imports(data: Any) -> tuple[str, ...] | None:
    if data[:2] != b"MZ":
        return None
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return None
        sections_count = struct.unpack_from("<H", data, pe + 6)[0]
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        directories = optional + (112 if magic == 0x20B else 96)
        sections = []
        table = optional + optional_size
        for index in range(min(sections_count, 96)):
            entry = table + index * 40
            virtual_size, virtual, raw_size, raw = struct.unpack_from("<IIII", data, entry + 8)
            sections.append((virtual, virtual_size, raw, raw_size))
        names: list[str] = []
        for directory, stride, name_field in ((1, 20, 12), (13, 32, 4)):
            rva, length = struct.unpack_from("<II", data, directories + directory * 8)
            offset = _rva_to_offset(rva, sections) if rva and length else None
            for _ in range(4096 if offset is not None else 0):
                if offset + stride > len(data):
                    break
                descriptor = data[offset:offset + stride]
                if not any(descriptor):
                    break
                name_rva = struct.unpack_from("<I", descriptor, name_field)[0]
                name = _ascii_at(data, _rva_to_offset(name_rva, sections)).casefold()
                if name and name not in names:
                    names.append(name)
                offset += stride
        return tuple(names)
    except struct.error:
        return None


def executable_identity(path: Path | None, root: Path | None) -> dict[str, Any]:
    """Size, mtime and a hash of the first and last MiB: detects a changed
    game build without hashing a multi-GB executable on every status read."""

    if path is None:
        return {"available": False, "reason": "executable not resolved"}
    try:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            digest.update(stream.read(_PARTIAL_BYTES))
            if stat.st_size > 2 * _PARTIAL_BYTES:
                stream.seek(stat.st_size - _PARTIAL_BYTES)
            digest.update(stream.read(_PARTIAL_BYTES))
    except OSError as error:
        return {"available": False, "reason": str(error)}
    relative = path.name
    if root is not None:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            pass
    return {"available": True, "path": relative, "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns, "partialSha256": digest.hexdigest(),
            "hashScope": "first and last MiB"}


def graphics_api(imports: Iterable[str] | None, analyzer_api: Mapping[str, Any] | None) -> Evidence:
    active = dict(analyzer_api or {})
    if str(active.get("value") or UNKNOWN) != UNKNOWN and float(active.get("confidence") or 0) >= 0.8:
        return Evidence(str(active["value"]), float(active["confidence"]), str(active.get("source") or "game configuration"),
                        tuple(str(item.get("detail", item)) if isinstance(item, Mapping) else str(item)
                              for item in active.get("evidence", ()) or ()))
    if imports is None:
        return Evidence.unknown("executable imports unavailable")
    found = sorted({GRAPHICS_IMPORTS[name] for name in imports if name in GRAPHICS_IMPORTS})
    evidence = tuple(f"imports {name}" for name in imports if name in GRAPHICS_IMPORTS)
    if len(found) == 1:
        # The engine may still load another renderer at runtime.
        return Evidence(found[0], 0.7, "executable import table", evidence)
    if found:
        return Evidence(UNKNOWN, 0.2, "several graphics APIs imported", evidence)
    return Evidence.unknown("no graphics API in the import table (it may be loaded at runtime)")


def engine(imports: Iterable[str] | None, analyzer_engine: Mapping[str, Any] | None) -> Evidence:
    detected = dict(analyzer_engine or {})
    value = str(detected.get("value") or UNKNOWN)
    if value != UNKNOWN and float(detected.get("confidence") or 0) >= 0.55:
        return Evidence(value, float(detected["confidence"]), str(detected.get("source") or "filesystem signatures"),
                        tuple(str(item.get("detail", item)) if isinstance(item, Mapping) else str(item)
                              for item in detected.get("evidence", ()) or ()))
    for name in imports or ():
        if name in ENGINE_IMPORTS:
            return Evidence(ENGINE_IMPORTS[name], 0.8, "executable import table", (f"imports {name}",))
    return Evidence.unknown("no reliable engine signature")


def proton_runtime(runner_script: Path | None, selection: str, native: bool) -> dict[str, Any]:
    if native:
        return {"kind": "native", "name": "Native Linux", "version": "", "steamRuntime": UNKNOWN}
    if runner_script is None:
        return {"kind": "unknown" if selection != "not_proton" else "wine", "name": UNKNOWN, "version": "",
                "steamRuntime": UNKNOWN, "reason": selection}
    directory = runner_script.parent
    version = ""
    try:
        text = (directory / "version").read_text(encoding="utf-8", errors="replace").split()
        version = text[-1] if text else ""
    except OSError:
        pass
    runtime = UNKNOWN
    try:
        manifest = (directory / "toolmanifest.vdf").read_text(encoding="utf-8", errors="replace")
        match = re.search(r'"require_tool_appid"\s+"(\d+)"', manifest)
        if match:
            runtime = STEAM_RUNTIMES.get(match.group(1), f"Steam runtime AppID {match.group(1)}")
    except OSError:
        pass
    return {"kind": "proton", "name": directory.name, "version": version or directory.name,
            "steamRuntime": runtime}


def config_snapshot(root: Path, key: str) -> dict[str, Any]:
    """SHA-256 of every per-game GameOpti file that shapes the launch."""

    result: dict[str, Any] = {}
    directory = Path(root) / key
    for name in CONFIG_FILES:
        path = directory / name
        try:
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None
        except OSError:
            result[name] = None
    encoded = json.dumps(result, sort_keys=True).encode()
    return {"files": result, "revision": hashlib.sha256(encoded).hexdigest()[:16]}


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def hardware(system_info: Mapping[str, Any]) -> dict[str, Any]:
    info = dict(system_info or {})
    capabilities = info.get("capabilities") if isinstance(info.get("capabilities"), Mapping) else {}
    return {
        "cpu": str(info.get("cpuModel") or info.get("cpu") or info.get("cpu_model") or UNKNOWN),
        "gpu": str(info.get("gpuModel") or info.get("gpu") or info.get("gpu_model") or UNKNOWN),
        "driver": str(info.get("gpuDriver") or info.get("gpu_driver") or UNKNOWN),
        "vramGb": _float(info.get("vramGb") or info.get("vram_gb") or info.get("gpuVramGb")),
        "ramGb": _float(info.get("ramGb") or info.get("ram_gb") or info.get("memoryGb")),
        "capabilities": {str(k): v for k, v in capabilities.items() if isinstance(v, bool)},
    }


@dataclass(frozen=True, slots=True)
class RunFingerprint:
    key: str
    launcher: str
    variant: str
    executable: Mapping[str, Any]
    architecture: str
    engine: Evidence
    api: Evidence
    runtime: Mapping[str, Any]
    hardware: Mapping[str, Any]
    display: Mapping[str, Any]
    active: Mapping[str, Any]
    config: Mapping[str, Any]
    build: str
    category: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "launcher": self.launcher, "variant": self.variant,
                "executable": dict(self.executable), "architecture": self.architecture,
                "engine": self.engine.to_dict(), "api": self.api.to_dict(),
                "runtime": dict(self.runtime), "hardware": dict(self.hardware),
                "display": dict(self.display), "active": dict(self.active),
                "config": dict(self.config), "build": self.build, "category": dict(self.category)}

    def scope(self) -> dict[str, Any]:
        """The values local evidence is valid for (see ``store.scope_match``)."""

        hardware = self.hardware
        return {
            "key": self.key,
            "build": self.build or (self.executable.get("partialSha256") or UNKNOWN),
            "gpu": hardware.get("gpu", UNKNOWN), "cpu": hardware.get("cpu", UNKNOWN),
            "vramGb": hardware.get("vramGb"), "driver": hardware.get("driver", UNKNOWN),
            "runtime": f"{self.runtime.get('name', UNKNOWN)} {self.runtime.get('version', '')}".strip(),
            "resolution": self.display.get("resolution", UNKNOWN),
            "api": self.api.value,
        }


__all__ = [
    "CONFIG_FILES", "Evidence", "GRAPHICS_IMPORTS", "RunFingerprint", "UNKNOWN",
    "config_snapshot", "engine", "executable_identity", "graphics_api", "hardware",
    "pe_imports", "proton_runtime",
]
