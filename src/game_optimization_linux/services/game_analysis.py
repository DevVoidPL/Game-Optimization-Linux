from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import struct
from typing import Any, Mapping, Sequence

from game_optimization_linux.models import (
    DetectedValue,
    DetectionEvidence,
    Game,
    GameFingerprint,
    SystemSnapshot,
)

from .displays import DisplayProfile
from .game_executable import GameExecutableResolver


@dataclass(frozen=True, slots=True)
class _EngineMatch:
    name: str
    confidence: float
    evidence: tuple[DetectionEvidence, ...]
    version: str = ""


class GameAnalyzer:
    def __init__(
        self,
        executable_resolver: GameExecutableResolver,
        *,
        maximum_files: int = 80000,
    ) -> None:
        self._executable_resolver = executable_resolver
        self._maximum_files = max(100, int(maximum_files))

    def analyze(
        self,
        game: Game,
        *,
        system_info: Mapping[str, Any] | None = None,
        display: DisplayProfile | None = None,
        category: str = "unknown",
        manual_category_override: bool = False,
        selected_executable: str = "",
        runtime_hint: DetectedValue | None = None,
    ) -> GameFingerprint:
        root = game.install_path.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Game root is not a directory")
        selected = selected_executable or game.executable_path
        resolution = self._executable_resolver.resolve(game, selected)
        executable = resolution.selected
        executable_path = root / executable.relative_path if executable else None
        files = self._scan(root)
        folded = {relative.casefold(): path for relative, path in files}
        engine = self._detect_engine(root, files, folded)
        graphics_api, available_graphics_apis = self._detect_graphics_apis(
            engine.name, files, executable.relative_path if executable else ""
        )
        runtime = runtime_hint or self._runtime(executable)
        architecture = self._architecture(executable_path)
        config_locations = self._config_locations(root, engine.name, files)
        launcher = self._launcher(files, executable.relative_path if executable else "")
        category_value = str(category or "unknown").strip().casefold()
        category_source = "manual override" if manual_category_override else "saved profile"
        if category_value == "unknown":
            category_source = "not determined"
        snapshot = self._system_snapshot(system_info or {}, display)
        return GameFingerprint(
            game_id=game.id,
            app_id=str(game.steam_app_id or game.id),
            title=game.name,
            provider=game.source,
            game_root=os.fspath(root),
            main_executable=(
                os.fspath(executable_path) if executable_path is not None else ""
            ),
            executable_directory=(
                os.fspath(executable_path.parent) if executable_path is not None else ""
            ),
            runtime=runtime,
            architecture=architecture,
            engine=DetectedValue(
                engine.name,
                engine.confidence,
                "filesystem signatures" if engine.evidence else "no reliable signature",
                engine.evidence,
            ),
            engine_version=engine.version,
            graphics_api=graphics_api,
            available_graphics_apis=available_graphics_apis,
            category=DetectedValue(
                category_value,
                1.0 if manual_category_override else 0.55 if category_value != "unknown" else 0.0,
                category_source,
            ),
            manual_category_override=manual_category_override,
            config_locations=config_locations,
            launcher=launcher,
            system=snapshot,
        )

    def _scan(self, root: Path) -> tuple[tuple[str, Path], ...]:
        results: list[tuple[str, Path]] = []
        seen = 0
        for directory, directories, files in os.walk(root, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not (Path(directory) / name).is_symlink()
            ]
            for name in files:
                path = Path(directory) / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    relative = path.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                results.append((relative, path))
                seen += 1
                if seen >= self._maximum_files:
                    return tuple(results)
        return tuple(results)

    def _detect_engine(
        self,
        root: Path,
        files: Sequence[tuple[str, Path]],
        folded: Mapping[str, Path],
    ) -> _EngineMatch:
        matches = [
            self._unreal(root, files, folded),
            self._unity(files, folded),
            self._redengine(files, folded),
        ]
        matches = sorted(matches, key=lambda item: item.confidence, reverse=True)
        first = matches[0]
        second = matches[1]
        if first.confidence < 0.55:
            return _EngineMatch("Unknown", 0.0, ())
        if second.confidence >= 0.55 and first.confidence - second.confidence < 0.18:
            evidence = (*first.evidence, *second.evidence)
            return _EngineMatch("Unknown", 0.25, evidence)
        return first

    @staticmethod
    def _unreal(
        root: Path,
        files: Sequence[tuple[str, Path]],
        folded: Mapping[str, Path],
    ) -> _EngineMatch:
        evidence: list[DetectionEvidence] = []
        directories = {part.casefold() for relative, _path in files for part in Path(relative).parts}
        engine_directory = next(
            (
                path for path in root.iterdir()
                if path.name.casefold() == "engine" and path.is_dir() and not path.is_symlink()
            ),
            None,
        )
        if "engine" in directories or engine_directory is not None:
            evidence.append(DetectionEvidence("directory", "Engine/ directory", 0.20))
        try:
            project_directories = tuple(
                path for path in root.iterdir()
                if path.is_dir() and not path.is_symlink()
                and path.name.casefold() not in {"engine", "__installer", "redist"}
            )[:64]
        except OSError:
            project_directories = ()
        for project in project_directories:
            try:
                content = next(
                    (path for path in project.iterdir() if path.name.casefold() == "content" and path.is_dir()),
                    None,
                )
                binaries = next(
                    (path for path in project.iterdir() if path.name.casefold() == "binaries" and path.is_dir()),
                    None,
                )
                paks = next(
                    (path for path in content.iterdir() if path.name.casefold() == "paks" and path.is_dir()),
                    None,
                ) if content else None
                win64 = next(
                    (path for path in binaries.iterdir() if path.name.casefold() in {"win64", "wingdk"} and path.is_dir()),
                    None,
                ) if binaries else None
                packages = tuple(
                    path for path in paks.iterdir()
                    if path.is_file() and path.suffix.casefold() in {".pak", ".ucas", ".utoc"}
                )[:4] if paks else ()
                executables = tuple(
                    path for path in win64.iterdir()
                    if path.is_file() and path.suffix.casefold() == ".exe"
                )[:4] if win64 else ()
            except OSError:
                continue
            if packages and executables:
                project_name = project.relative_to(root).as_posix()
                evidence.append(DetectionEvidence(
                    project_name,
                    "Project Content/Paks and Binaries/Win64 hierarchy",
                    0.35,
                ))
                package = packages[0].relative_to(root).as_posix()
                evidence.append(DetectionEvidence(
                    package,
                    f"Unreal package ({packages[0].suffix.casefold()})",
                    0.25,
                ))
                executable = executables[0].relative_to(root).as_posix()
                evidence.append(DetectionEvidence(
                    executable,
                    "Project Win64 executable",
                    0.16,
                ))
                break
        pak = next(
            (relative for relative, _path in files if "/content/paks/" in f"/{relative.casefold()}/" and relative.casefold().endswith(".pak")),
            "",
        )
        if pak:
            evidence.append(DetectionEvidence(pak, "Content/Paks package", 0.25))
        shipping = next(
            (relative for relative, _path in files if relative.casefold().endswith("-win64-shipping.exe")),
            "",
        )
        if shipping:
            evidence.append(DetectionEvidence(shipping, "Unreal Shipping executable", 0.30))
        config = next(
            (relative for relative, _path in files if relative.casefold().endswith(("config/defaultengine.ini", "config/defaultgame.ini"))),
            "",
        )
        if config:
            evidence.append(DetectionEvidence(config, "Unreal configuration hierarchy", 0.20))
        version = ""
        build = next(
            ((relative, path) for relative, path in files if relative.casefold().endswith("engine/build/build.version")),
            None,
        )
        if build:
            try:
                payload = json.loads(build[1].read_text(encoding="utf-8"))
                major = int(payload.get("MajorVersion") or 0)
                minor = int(payload.get("MinorVersion") or 0)
                if major in {4, 5}:
                    version = f"{major}.{minor}"
                    evidence.append(DetectionEvidence(build[0], f"Engine build {version}", 0.12))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        confidence = min(0.99, sum(item.weight for item in evidence))
        return _EngineMatch("Unreal Engine", confidence, tuple(evidence), version)

    @staticmethod
    def _unity(
        files: Sequence[tuple[str, Path]], folded: Mapping[str, Path]
    ) -> _EngineMatch:
        del folded
        evidence: list[DetectionEvidence] = []
        unity_player = next(
            (relative for relative, _path in files if Path(relative).name.casefold() == "unityplayer.dll"),
            "",
        )
        if unity_player:
            evidence.append(DetectionEvidence(unity_player, "UnityPlayer.dll", 0.32))
        managers = next(
            (relative for relative, _path in files if Path(relative).name.casefold() == "globalgamemanagers" and "_data/" in relative.casefold()),
            "",
        )
        if managers:
            evidence.append(DetectionEvidence(managers, "Unity globalgamemanagers", 0.32))
        assembly = next(
            (relative for relative, _path in files if Path(relative).name.casefold() == "gameassembly.dll"),
            "",
        )
        if assembly:
            evidence.append(DetectionEvidence(assembly, "Unity IL2CPP GameAssembly", 0.20))
        resources = next(
            (relative for relative, _path in files if relative.casefold().endswith("_data/resources.assets")),
            "",
        )
        if resources:
            evidence.append(DetectionEvidence(resources, "Unity resources asset", 0.15))
        return _EngineMatch(
            "Unity", min(0.99, sum(item.weight for item in evidence)), tuple(evidence)
        )

    @staticmethod
    def _redengine(
        files: Sequence[tuple[str, Path]], folded: Mapping[str, Path]
    ) -> _EngineMatch:
        del folded
        lower = [relative.casefold() for relative, _path in files]
        evidence: list[DetectionEvidence] = []
        r6 = next((value for value in lower if value.startswith("r6/")), "")
        archive = next((value for value in lower if value.startswith("archive/pc/content/")), "")
        bin_x64 = next((value for value in lower if value.startswith("bin/x64/") and value.endswith(".exe")), "")
        content = next((value for value in lower if value.startswith("content/content") or "/content/content" in value), "")
        prelauncher = next((value for value in lower if Path(value).name == "redprelauncher.exe"), "")
        for value, detail, weight in (
            (r6, "REDengine r6 data", 0.28),
            (archive, "REDengine archive/pc/content data", 0.28),
            (bin_x64, "REDengine bin/x64 executable", 0.22),
            (content, "REDengine content package hierarchy", 0.22),
            (prelauncher, "REDlauncher prelauncher", 0.12),
        ):
            if value:
                evidence.append(DetectionEvidence(value, detail, weight))
        confidence = min(0.98, sum(item.weight for item in evidence))
        version = "4" if r6 and archive else "3" if content and bin_x64 else ""
        return _EngineMatch("REDengine", confidence, tuple(evidence), version)

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            if path.stat().st_size > 1024 * 1024:
                return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _detect_graphics_apis(
        self,
        engine: str,
        files: Sequence[tuple[str, Path]],
        selected_executable: str,
    ) -> tuple[DetectedValue, tuple[DetectedValue, ...]]:
        detected: dict[str, DetectedValue] = {}
        active: DetectedValue | None = None
        for relative, path in files:
            folded = relative.casefold()
            if engine == "Unreal Engine" and folded.endswith("defaultengine.ini"):
                text = self._read_text(path)
                match = re.search(
                    r"(?im)^\s*DefaultGraphicsRHI\s*=\s*DefaultGraphicsRHI_(DX11|DX12|Vulkan)\s*$",
                    text,
                )
                if match:
                    value = {"DX11": "Direct3D 11", "DX12": "Direct3D 12", "VULKAN": "Vulkan"}[match.group(1).upper()]
                    evidence = DetectionEvidence(relative, match.group(0).strip(), 0.92)
                    active = DetectedValue(value, 0.92, "active engine configuration", (evidence,))
                    detected[value] = active
            if engine == "Unity" and Path(folded).name == "boot.config":
                text = self._read_text(path)
                for key, value in (("force-d3d12", "Direct3D 12"), ("force-d3d11", "Direct3D 11"), ("force-vulkan", "Vulkan")):
                    if re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*1\s*$", text):
                        evidence = DetectionEvidence(relative, f"{key}=1", 0.90)
                        active = DetectedValue(value, 0.90, "Unity boot configuration", (evidence,))
                        detected[value] = active

        selected_stem = Path(selected_executable).stem.casefold()
        matching_executables = [
            relative.casefold()
            for relative, _path in files
            if relative.casefold().endswith(".exe")
            and (not selected_stem or Path(relative).stem.casefold() == selected_stem)
        ]
        has_renderer_specific_dx12 = any(
            "dx12" in part or "d3d12" in part
            for relative in matching_executables
            for part in Path(relative).parts
        )
        for relative, _path in files:
            folded = relative.casefold()
            if not folded.endswith(".exe"):
                continue
            if selected_stem and Path(folded).stem != selected_stem:
                continue
            parts = set(Path(folded).parts)
            value = ""
            if any("dx12" in part or "d3d12" in part for part in parts):
                value = "Direct3D 12"
            elif any("dx11" in part or "d3d11" in part for part in parts):
                value = "Direct3D 11"
            elif (
                engine == "REDengine"
                and "x64" in parts
                and has_renderer_specific_dx12
            ):
                value = "Direct3D 11"
            if not value:
                continue
            evidence = DetectionEvidence(
                relative,
                f"renderer-specific executable path for {value}",
                0.84,
            )
            candidate = DetectedValue(
                value, 0.84, "renderer-specific executable path", (evidence,)
            )
            previous = detected.get(value)
            if previous is None or candidate.confidence > previous.confidence:
                detected[value] = candidate
            if relative.casefold() == selected_executable.casefold():
                active = DetectedValue(
                    value,
                    0.94,
                    "selected renderer-specific executable",
                    (evidence,),
                )
                detected[value] = active

        return (
            active or DetectedValue("Unknown", 0.0, "no reliable active API setting"),
            tuple(sorted(detected.values(), key=lambda item: item.value)),
        )

    @staticmethod
    def _runtime(executable: Any) -> DetectedValue:
        if executable is None:
            return DetectedValue("Unknown", 0.0, "executable not resolved")
        evidence = DetectionEvidence(
            executable.relative_path,
            "PE executable" if executable.wine else "native executable",
            0.9,
        )
        if executable.wine:
            return DetectedValue(
                "Windows game using Steam compatibility layer",
                0.85,
                "resolved PE executable",
                (evidence,),
            )
        return DetectedValue(
            "Native Linux", 0.95, "resolved native executable", (evidence,)
        )

    @staticmethod
    def _architecture(path: Path | None) -> DetectedValue:
        if path is None:
            return DetectedValue("Unknown", 0.0, "executable not resolved")
        try:
            with path.open("rb") as stream:
                header = stream.read(64)
                if header.startswith(b"\x7fELF") and len(header) >= 5:
                    value = "64-bit" if header[4] == 2 else "32-bit" if header[4] == 1 else "Unknown"
                    return DetectedValue(value, 1.0 if value != "Unknown" else 0.0, "ELF header")
                if header.startswith(b"MZ") and len(header) >= 64:
                    offset = struct.unpack_from("<I", header, 0x3C)[0]
                    stream.seek(offset)
                    pe = stream.read(26)
                    if pe.startswith(b"PE\0\0") and len(pe) >= 26:
                        magic = struct.unpack_from("<H", pe, 24)[0]
                        value = "64-bit" if magic == 0x20B else "32-bit" if magic == 0x10B else "Unknown"
                        return DetectedValue(value, 1.0 if value != "Unknown" else 0.0, "PE header")
        except OSError:
            pass
        return DetectedValue("Unknown", 0.0, "executable header unavailable")

    @staticmethod
    def _config_locations(
        root: Path, engine: str, files: Sequence[tuple[str, Path]]
    ) -> tuple[str, ...]:
        suffixes = {
            "Unreal Engine": (".ini",),
            "Unity": ("boot.config",),
            "REDengine": (".ini", ".json", ".xml"),
        }.get(engine, ())
        locations: list[str] = []
        for relative, path in files:
            folded = relative.casefold()
            if not suffixes or not folded.endswith(suffixes):
                continue
            if (
                folded.startswith("config/")
                or "/config/" in folded
                or folded.startswith("r6/config/")
                or folded.startswith("engine/config/")
                or folded.endswith("boot.config")
            ):
                parent = os.fspath(path.parent)
                if parent not in locations:
                    locations.append(parent)
        return tuple(locations[:20])

    @staticmethod
    def _launcher(
        files: Sequence[tuple[str, Path]], selected_relative: str
    ) -> str:
        selected = selected_relative.casefold()
        if "launcher" in Path(selected).name.casefold():
            return selected_relative
        for relative, _path in files:
            name = Path(relative).name.casefold()
            if name.endswith(".exe") and ("prelauncher" in name or name.endswith("launcher.exe")):
                return relative
        return ""

    @staticmethod
    def _system_snapshot(
        system_info: Mapping[str, Any], display: DisplayProfile | None
    ) -> SystemSnapshot:
        def optional_float(*keys: str) -> float | None:
            for key in keys:
                value = system_info.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    return float(value)
            return None

        return SystemSnapshot(
            cpu=str(
                system_info.get("cpu")
                or system_info.get("cpuModel")
                or system_info.get("cpu_model")
                or "Unknown"
            ),
            gpu=str(
                system_info.get("gpu")
                or system_info.get("gpuModel")
                or system_info.get("gpu_model")
                or "Unknown"
            ),
            vram_gb=optional_float("vram_gb", "vramGb"),
            ram_gb=optional_float("ram_gb", "ramGb"),
            display_name=display.name if display else "",
            resolution_width=display.width if display else None,
            resolution_height=display.height if display else None,
            refresh_rate=display.refresh_rate if display else None,
        )


__all__ = ["GameAnalyzer"]
